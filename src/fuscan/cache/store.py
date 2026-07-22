"""SQLite 持久化扫描结果缓存。

公共 API：

- :class:`CacheStore`：线程安全的 SQLite 缓存，封装规则登记、结果查询、清理等操作
- :class:`CacheStats`：缓存统计快照（不可变）

设计要点：

- **读写连接分离**（iter-68）：写操作经主连接 + ``RLock`` 串行化；
  读操作使用线程本地只读连接，WAL 模式下完全并行，消除锁竞争
- WAL 模式：读不阻塞写，提升并发扫描吞吐
- 缓存键为 ``(file_hash, rule_hash)``：路径无关，规则变更感知
- ``scanned_files`` 表以内容哈希为主键，``file_paths`` 表登记多个路径引用
- **进程内 LRU 命中缓存**：``get_cached_hits`` 结果在内存中再缓存一份，
  热点文件（如 node_modules 中重复依赖）查询次数大幅降低；``put_result``
  / ``register_file`` 等写入操作自动 invalidate 对应 ``file_hash`` 条目
- **路径预筛 LRU 缓存**（iter-73）：``lookup_file_hash`` 按 ``(path, mtime, size)``
  查询 ``file_paths`` 索引，结果在内存中再缓存一份；``register_path`` /
  ``batch_put_results`` 写入后主动填充对应条目，使热缓存二次扫描完全命中内存，
  消除 SQLite 查询开销。文件 ``mtime`` 变化时 LRU 键自然不同，自动失效
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Collection, Mapping

from fuscan.cache.hashes import compute_rule_hash, hash_bytes, serialize_rule
from fuscan.cache.schema import CURRENT_VERSION, migrate
from fuscan.rules.model import Rule, RuleSet, Severity

if TYPE_CHECKING:
    # 仅类型注解使用，运行时不导入以避免与 scanner.scanner 形成循环
    from fuscan.scanner.result import RuleHit

__all__ = ["BatchWriteItem", "CacheStats", "CacheStore", "default_cache_path"]

logger = logging.getLogger(__name__)

# 进程内 LRU 命中缓存容量上限（条目数）。
# 每条平均 ~1KB（含 rule_hash 元组与 RuleHit），4096 条约占 4MB 内存。
_HIT_CACHE_MAX: int = 4096


def default_cache_path() -> Path:
    """返回默认缓存路径：``~/.fuscan/cache.db``。"""
    return Path.home() / ".fuscan" / "cache.db"


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串（含时区后缀 ``Z``）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class CacheStats:
    """缓存统计快照（不可变）。"""

    rule_files: int = 0
    rules: int = 0
    scanned_files: int = 0
    file_paths: int = 0
    scan_results: int = 0
    extracted_contents: int = 0
    db_bytes: int = 0
    schema_version: int = 0


@dataclass(frozen=True)
class BatchWriteItem:
    """单次批量写入项：包含文件元数据与该文件所有规则的缓存结果。

    用于 :meth:`CacheStore.batch_put_results` 批量写入，避免逐条
    :meth:`CacheStore.put_result` + :meth:`CacheStore.register_file`
    + :meth:`CacheStore.register_path` 触发多次 commit/fsync。
    预筛命中场景下 ``hits`` 可为空元组，仅刷新文件元数据。
    """

    file_hash: str
    size: int
    path: Path
    mtime: float
    hits: tuple[tuple[str, RuleHit | None], ...]


class CacheStore:
    """线程安全的 SQLite 扫描结果缓存。

    使用方式：

    1. 构造时打开/创建数据库，自动迁移 schema
    2. ``register_ruleset()`` 登记当前规则集与来源文件
    3. 扫描每个文件时：
       - 算 ``file_hash``
       - ``get_cached_hits()`` 批量查询
       - 命中的规则直接复用 ``RuleHit``
       - 未命中的规则扫描后调 ``put_result()`` 写入
       - ``register_file()`` / ``register_path()`` 更新元数据
    4. 可选 ``prune_orphan_rules()`` / ``prune_stale_files()`` 清理
    5. ``close()`` 释放连接

    所有公共方法线程安全。写操作经 ``RLock`` 串行化，读操作使用线程本地
    只读连接并行执行（iter-68 起读写分离）。
    """

    def __init__(self, db_path: Path) -> None:
        """打开或创建缓存数据库。

        :param db_path: SQLite 文件路径；父目录自动创建
        """
        self._db_path: Path = db_path
        self._lock: threading.RLock = threading.RLock()
        # LRU 细粒度锁：读操作的 LRU 访问不阻塞 DB 读，也不被写操作的 _lock 阻塞
        # 锁顺序约定：_lock → _lru_lock（写操作先持 _lock 再持 _lru_lock），避免死锁
        self._lru_lock: threading.Lock = threading.Lock()
        self._closed: bool = False
        # 进程内 LRU 命中缓存：file_hash -> (rule_hashes_tuple, result_dict)
        # 用 OrderedDict 实现 LRU 语义：访问时 move_to_end，超容量时 popitem(last=False)
        self._hit_cache: OrderedDict[str, tuple[tuple[str, ...], dict[str, RuleHit | None]]] = OrderedDict()
        # 路径预筛 LRU 缓存（iter-73）：(path_str, mtime, size) -> file_hash
        # lookup_file_hash 命中时跳过 SQLite 查询；register_path / batch_put_results
        # 写入后主动填充，使热缓存二次扫描完全命中内存。文件 mtime 变化时键不同，自动失效
        self._path_cache: OrderedDict[tuple[str, float, int], str] = OrderedDict()
        # 线程本地只读连接：每线程一个，WAL 模式下读完全并行
        self._read_local: threading.local = threading.local()
        # 已创建的读连接列表（close 时统一关闭，用 _lru_lock 保护追加）
        self._read_conns: list[sqlite3.Connection] = []
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False 允许跨线程使用连接，所有访问经 RLock 序列化
        self._conn: sqlite3.Connection = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,  # 自动提交模式，事务显式管理
        )
        try:
            self._conn.row_factory = sqlite3.Row
            self._init_db()
        except Exception:
            # _init_db 失败（如磁盘满、schema 损坏）时关闭连接，避免泄漏
            self._conn.close()
            raise

    def _get_read_conn(self) -> sqlite3.Connection:
        """返回当前线程的只读连接（惰性创建）。

        每个线程首次调用时创建独立连接，配置 WAL + ``query_only = ON``
        防止误写。WAL 模式下读不阻塞写，读连接可完全并行执行查询。

        连接创建后登记到 ``_read_conns`` 列表，``close`` 时统一关闭。
        """
        conn = getattr(self._read_local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # 自动提交，WAL 下每次查询读最新快照
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = NORMAL")
        # 只读保护：防止读连接误写，违反 query_only 会抛 sqlite3.OperationalError
        conn.execute("PRAGMA query_only = ON")
        self._read_local.conn = conn
        with self._lru_lock:
            self._read_conns.append(conn)
        return conn

    def _init_db(self) -> None:
        """初始化数据库：启用 WAL、外键，迁移 schema。"""
        with self._lock:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            version = migrate(self._conn)
            logger.debug("缓存数据库已就绪: %s, schema_version=%d", self._db_path, version)

    @property
    def db_path(self) -> Path:
        """缓存数据库文件路径。"""
        return self._db_path

    @property
    def schema_version(self) -> int:
        """当前 schema 版本号。"""
        row = self._get_read_conn().execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ 内存 LRU

    def _hit_cache_get(
        self,
        file_hash: str,
        rule_hashes: Collection[str],
    ) -> dict[str, RuleHit | None] | None:
        """查询进程内 LRU 命中缓存（已持锁）。

        命中条件：``file_hash`` 与 ``rule_hashes`` 集合完全一致（顺序无关）。
        命中时移动到队尾（LRU），返回缓存的字典；未命中返回 None。
        """
        key = (file_hash, tuple(sorted(rule_hashes)))
        cached = self._hit_cache.get(file_hash)
        if cached is None:
            return None
        cached_rule_keys, cached_dict = cached
        if cached_rule_keys != key[1]:
            # rule_hashes 集合变化：视为未命中（如新增了规则）
            return None
        # LRU：移到队尾
        self._hit_cache.move_to_end(file_hash)
        return dict(cached_dict)  # 返回副本，避免外部修改污染缓存

    def _hit_cache_put(
        self,
        file_hash: str,
        rule_hashes: Collection[str],
        result: dict[str, RuleHit | None],
    ) -> None:
        """写入进程内 LRU 命中缓存（已持锁）。

        超容量时弹出最旧条目。
        """
        self._hit_cache[file_hash] = (tuple(sorted(rule_hashes)), dict(result))
        self._hit_cache.move_to_end(file_hash)
        while len(self._hit_cache) > _HIT_CACHE_MAX:
            self._hit_cache.popitem(last=False)

    def _hit_cache_invalidate(self, file_hash: str) -> None:
        """失效指定 ``file_hash`` 的内存缓存条目（已持锁）。

        ``put_result`` / ``register_file`` / ``register_path`` 写入后调用，
        确保下次查询走 SQLite 取最新数据。
        """
        self._hit_cache.pop(file_hash, None)

    def _path_cache_get(self, path: str, mtime: float, size: int) -> str | None:
        """查询路径预筛 LRU 缓存（已持 ``_lru_lock``）。

        命中时移动到队尾（LRU 语义），返回 ``file_hash``；未命中返回 None。
        """
        key = (path, mtime, size)
        file_hash = self._path_cache.get(key)
        if file_hash is not None:
            self._path_cache.move_to_end(key)
        return file_hash

    def _path_cache_put(self, path: str, mtime: float, size: int, file_hash: str) -> None:
        """写入路径预筛 LRU 缓存（已持 ``_lru_lock``）。

        超容量时弹出最旧条目。
        """
        key = (path, mtime, size)
        self._path_cache[key] = file_hash
        self._path_cache.move_to_end(key)
        while len(self._path_cache) > _HIT_CACHE_MAX:
            self._path_cache.popitem(last=False)

    def hit_cache_size(self) -> int:
        """返回进程内 LRU 命中缓存当前条目数（诊断用）。"""
        with self._lru_lock:
            return len(self._hit_cache)

    def path_cache_size(self) -> int:
        """返回路径预筛 LRU 缓存当前条目数（诊断用，iter-73）。"""
        with self._lru_lock:
            return len(self._path_cache)

    # ------------------------------------------------------------------ 规则登记

    def register_ruleset(
        self,
        ruleset: RuleSet,
        source_files: Mapping[Path, str] | None = None,
    ) -> dict[str, str]:
        """登记规则集到缓存：算规则哈希，写入 ``rules``/``rule_files``/``rule_file_members``。

        相同规则的哈希跨文件去重，``rule_file_members`` 维护多对多关系。
        旧的 ``rule_file_members`` 关系在重新登记时被该文件的当前规则集替换。

        :param ruleset: 规则集
        :param source_files: 规则文件路径 → 文件 SHA-256 映射；
            为空时按"匿名来源"登记（``__inline__`` 虚拟文件）
        :return: ``rule_name -> rule_hash`` 映射，供 Scanner 复用
        """
        with self._lock:
            now = _now_iso()
            sources: dict[Path, str] = dict(source_files) if source_files else {}
            # 默认虚拟来源，避免无 source_files 时规则无处归属
            if not sources:
                sources = {Path("__inline__"): hash_bytes(b"")}

            # 收集 (rule_name -> rule_hash)，重名规则以最后一条为准
            rule_hashes: dict[str, str] = {}
            for rule in ruleset.rules:
                rhash = compute_rule_hash(rule)
                rule_hashes[rule.name] = rhash
                self._upsert_rule(rule, rhash)

            # 登记规则文件与成员关系
            for file_path, file_hash in sources.items():
                path_str = str(file_path)
                try:
                    mtime = file_path.stat().st_mtime if file_path.exists() else 0.0
                except OSError:
                    mtime = 0.0
                self._conn.execute(
                    "INSERT INTO rule_files (file_path, file_hash, mtime, loaded_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(file_path) DO UPDATE SET "
                    "  file_hash = excluded.file_hash, "
                    "  mtime = excluded.mtime, "
                    "  loaded_at = excluded.loaded_at",
                    (path_str, file_hash, mtime, now),
                )
                # 替换该文件下的成员关系（先删后插）
                self._conn.execute(
                    "DELETE FROM rule_file_members WHERE file_path = ?",
                    (path_str,),
                )
                for rule in ruleset.rules:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO rule_file_members (file_path, rule_hash) VALUES (?, ?)",
                        (path_str, rule_hashes[rule.name]),
                    )
            return rule_hashes

    def _upsert_rule(self, rule: Rule, rule_hash: str) -> None:
        """写入或更新单条规则（按 rule_hash 去重）。"""
        serialized = serialize_rule(rule)
        self._conn.execute(
            "INSERT INTO rules (rule_hash, rule_name, severity, description, serialized) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(rule_hash) DO UPDATE SET "
            "  rule_name = excluded.rule_name, "
            "  severity = excluded.severity, "
            "  description = excluded.description, "
            "  serialized = excluded.serialized",
            (rule_hash, rule.name, rule.severity.value, rule.description, serialized),
        )

    def get_rule_hashes(self) -> dict[str, str]:
        """查询当前已登记的 ``rule_name -> rule_hash`` 映射。

        重名规则以最后登记的为准（与 ``register_ruleset`` 行为一致）。
        """
        rows = self._get_read_conn().execute("SELECT rule_name, rule_hash FROM rules").fetchall()
        return {row["rule_name"]: row["rule_hash"] for row in rows}

    # ------------------------------------------------------------------ 结果缓存

    def get_cached_hits(
        self,
        file_hash: str,
        rule_hashes: Collection[str],
    ) -> dict[str, RuleHit | None]:
        """批量查询缓存结果。

        优先命中进程内 LRU 缓存；未命中走 SQLite 查询，结果写回 LRU。

        线程安全：LRU 访问经 ``_lru_lock`` 保护，SQLite 查询使用线程本地
        只读连接并行执行（iter-68）。

        :param file_hash: 被扫描文件内容哈希
        :param rule_hashes: 待查询的规则哈希集合
        :return: ``rule_hash -> RuleHit | None`` 映射；
            值为 ``RuleHit`` 表示该规则命中且已缓存；
            值为 ``None`` 表示该规则未命中且已缓存（避免重复扫描未命中）；
            不在返回字典中的 ``rule_hash`` 表示未缓存，需扫描。
        """
        if not rule_hashes:
            return {}
        # 先查进程内 LRU（细粒度锁，不阻塞 DB 读）
        with self._lru_lock:
            cached = self._hit_cache_get(file_hash, rule_hashes)
        if cached is not None:
            return cached

        placeholders = ",".join("?" for _ in rule_hashes)
        params: tuple[Any, ...] = (file_hash, *rule_hashes)
        rows = (
            self._get_read_conn()
            .execute(
                f"SELECT rule_hash, matched, severity, detail, match_text, "
                f"       match_texts, match_description, match_count, target "
                f"FROM scan_results WHERE file_hash = ? AND rule_hash IN ({placeholders})",
                params,
            )
            .fetchall()
        )
        # 延迟导入打破循环：cache.store → scanner.result → scanner.__init__ → scanner.scanner → cache.store
        from fuscan.scanner.result import RuleHit

        result: dict[str, RuleHit | None] = {}
        for row in rows:
            if row["matched"]:
                severity = Severity(row["severity"]) if row["severity"] else Severity.INFO
                # match_texts 以 JSON 数组形式存储；NULL 或空数组视为空元组
                raw_texts = row["match_texts"]
                if raw_texts:
                    try:
                        texts_list = json.loads(raw_texts)
                        match_texts = tuple(str(t) for t in texts_list) if isinstance(texts_list, list) else ()
                    except (TypeError, ValueError):
                        logger.warning("match_texts 反序列化失败，回退到空元组: %r", raw_texts)
                        match_texts = ()
                else:
                    match_texts = ()
                result[row["rule_hash"]] = RuleHit(
                    rule_name="",  # 调用方按 rule_hash 反查 name，避免冗余存储
                    severity=severity,
                    detail=row["detail"] or "",
                    match_text=row["match_text"] or "",
                    match_count=row["match_count"],
                    target=row["target"] or "",
                    match_texts=match_texts,
                    match_description=row["match_description"] or "",
                )
            else:
                result[row["rule_hash"]] = None
        # 写回 LRU（细粒度锁）
        with self._lru_lock:
            self._hit_cache_put(file_hash, rule_hashes, result)
        return result

    def put_result(
        self,
        file_hash: str,
        rule_hash: str,
        hit: RuleHit | None,
    ) -> None:
        """写入单条缓存结果。

        仅写入 ``scan_results``；文件元数据（``scanned_files``/``file_paths``）请由调用方
        通过 :meth:`register_file` 与 :meth:`register_path` 单独登记，避免单次调用承担过多职责。

        写入后失效对应 ``file_hash`` 的进程内 LRU 条目，下次查询走 SQLite 取最新数据。

        :param file_hash: 文件内容哈希
        :param rule_hash: 规则哈希
        :param hit: ``RuleHit`` 表示命中；``None`` 表示该规则对该文件未命中（也缓存，避免重复扫描）
        """
        now = _now_iso()
        with self._lock:
            # 确保 scanned_files 中存在该 file_hash，避免外键约束失败；
            # size 未知时用 0 占位，调用方可通过 register_file() 更新真实 size。
            self._conn.execute(
                "INSERT OR IGNORE INTO scanned_files "
                "(file_hash, size, first_seen_at, last_scanned_at) VALUES (?, 0, ?, ?)",
                (file_hash, now, now),
            )
            if hit is None:
                self._conn.execute(
                    "INSERT INTO scan_results "
                    "(file_hash, rule_hash, matched, severity, detail, match_text, "
                    " match_texts, match_description, match_count, target, cached_at) "
                    "VALUES (?, ?, 0, NULL, NULL, NULL, NULL, '', 0, '', ?) "
                    "ON CONFLICT(file_hash, rule_hash) DO UPDATE SET "
                    "  matched = 0, severity = NULL, detail = NULL, match_text = NULL, "
                    "  match_texts = NULL, match_description = '', "
                    "  match_count = 0, target = '', cached_at = excluded.cached_at",
                    (file_hash, rule_hash, now),
                )
            else:
                # match_texts 以 JSON 数组形式序列化，便于跨行解析且保持顺序
                texts_json = json.dumps(list(hit.match_texts), ensure_ascii=False) if hit.match_texts else None
                self._conn.execute(
                    "INSERT INTO scan_results "
                    "(file_hash, rule_hash, matched, severity, detail, match_text, "
                    " match_texts, match_description, match_count, target, cached_at) "
                    "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(file_hash, rule_hash) DO UPDATE SET "
                    "  matched = 1, severity = excluded.severity, detail = excluded.detail, "
                    "  match_text = excluded.match_text, match_texts = excluded.match_texts, "
                    "  match_description = excluded.match_description, "
                    "  match_count = excluded.match_count, target = excluded.target, "
                    "  cached_at = excluded.cached_at",
                    (
                        file_hash,
                        rule_hash,
                        hit.severity.value,
                        hit.detail,
                        hit.match_text,
                        texts_json,
                        hit.match_description,
                        hit.match_count,
                        hit.target,
                        now,
                    ),
                )
            # 失效 LRU 条目：调用方下次查询时会从 SQLite 取最新数据并回填 LRU
            with self._lru_lock:
                self._hit_cache_invalidate(file_hash)

    def _register_file_locked(self, file_hash: str, size: int, now: str) -> None:
        """登记文件哈希到 ``scanned_files``（已持锁）。

        ``put_result`` 会用 ``size=0`` 占位插入以满足外键约束；
        本方法用真实 size 覆盖占位值（仅在 size > 0 时更新，避免覆盖已登记的真实值）。
        """
        self._conn.execute(
            "INSERT INTO scanned_files (file_hash, size, first_seen_at, last_scanned_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(file_hash) DO UPDATE SET "
            "  size = CASE WHEN excluded.size > 0 THEN excluded.size ELSE scanned_files.size END, "
            "  last_scanned_at = excluded.last_scanned_at",
            (file_hash, size, now, now),
        )

    def _register_path_locked(self, file_hash: str, path: Path, mtime: float, now: str) -> None:
        """登记文件路径到 ``file_paths``（已持锁）。"""
        self._conn.execute(
            "INSERT INTO file_paths (file_hash, path, mtime, last_seen_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(file_hash, path) DO UPDATE SET "
            "  mtime = excluded.mtime, last_seen_at = excluded.last_seen_at",
            (file_hash, str(path), mtime, now),
        )

    def register_file(self, file_hash: str, size: int) -> None:
        """登记/更新 ``scanned_files`` 的 ``last_scanned_at``。

        写入后失效对应 ``file_hash`` 的进程内 LRU 条目。
        """
        with self._lock:
            now = _now_iso()
            self._register_file_locked(file_hash, size, now)
            with self._lru_lock:
                self._hit_cache_invalidate(file_hash)

    def register_path(self, file_hash: str, path: Path, mtime: float) -> None:
        """登记/更新 ``file_paths``。

        写入后失效对应 ``file_hash`` 的进程内 LRU 条目。
        """
        with self._lock:
            now = _now_iso()
            self._register_path_locked(file_hash, path, mtime, now)
            with self._lru_lock:
                self._hit_cache_invalidate(file_hash)

    def batch_put_results(self, items: list[BatchWriteItem]) -> None:
        """批量写入扫描结果与文件元数据，单次事务提交。

        适用于扫描器累积一批后 flush 的场景。相比逐条
        :meth:`put_result` + :meth:`register_file` + :meth:`register_path`，
        显著减少 commit/fsync 次数，提升冷缓存场景吞吐。

        - ``items[i].hits`` 为非空时，等价于对该 ``file_hash`` 调用多次
          :meth:`put_result`（含命中与未命中两种 RuleHit）
        - ``items[i].hits`` 为空元组时，仅刷新 ``scanned_files`` 与 ``file_paths``
          元数据（预筛命中场景，无新结果需写入）
        - ``scanned_files`` 用 :meth:`_register_file_locked` 同款 UPSERT 语义
          （``size > 0`` 才覆盖占位值）
        - 异常时整批 ROLLBACK，已写入数据不受影响

        COMMIT 成功后统一失效涉及到的 ``file_hash`` 的进程内 LRU 条目。

        :param items: 批量写入项列表；空列表直接返回
        """
        if not items:
            return
        now = _now_iso()
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                # 1. scanned_files（executemany 比 循环 execute 快）
                self._conn.executemany(
                    "INSERT INTO scanned_files (file_hash, size, first_seen_at, last_scanned_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(file_hash) DO UPDATE SET "
                    "  size = CASE WHEN excluded.size > 0 THEN excluded.size ELSE scanned_files.size END, "
                    "  last_scanned_at = excluded.last_scanned_at",
                    [(item.file_hash, item.size, now, now) for item in items],
                )
                # 2. file_paths
                self._conn.executemany(
                    "INSERT INTO file_paths (file_hash, path, mtime, last_seen_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(file_hash, path) DO UPDATE SET "
                    "  mtime = excluded.mtime, last_seen_at = excluded.last_seen_at",
                    [(item.file_hash, str(item.path), item.mtime, now) for item in items],
                )
                # 3. scan_results（扁平化为行列表后一次性 executemany）
                result_rows: list[tuple[Any, ...]] = []
                for item in items:
                    for rule_hash, hit in item.hits:
                        if hit is None:
                            result_rows.append((item.file_hash, rule_hash, 0, None, None, None, None, "", 0, "", now))
                        else:
                            texts_json = (
                                json.dumps(list(hit.match_texts), ensure_ascii=False) if hit.match_texts else None
                            )
                            result_rows.append(
                                (
                                    item.file_hash,
                                    rule_hash,
                                    1,
                                    hit.severity.value,
                                    hit.detail,
                                    hit.match_text,
                                    texts_json,
                                    hit.match_description,
                                    hit.match_count,
                                    hit.target,
                                    now,
                                )
                            )
                if result_rows:
                    self._conn.executemany(
                        "INSERT INTO scan_results "
                        "(file_hash, rule_hash, matched, severity, detail, match_text, "
                        " match_texts, match_description, match_count, target, cached_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(file_hash, rule_hash) DO UPDATE SET "
                        "  matched = excluded.matched, severity = excluded.severity, "
                        "  detail = excluded.detail, match_text = excluded.match_text, "
                        "  match_texts = excluded.match_texts, "
                        "  match_description = excluded.match_description, "
                        "  match_count = excluded.match_count, target = excluded.target, "
                        "  cached_at = excluded.cached_at",
                        result_rows,
                    )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    # ROLLBACK 失败不应掩盖原始异常，仅记录警告
                    logger.warning("ROLLBACK 失败", exc_info=True)
                raise
            # 仅 COMMIT 成功后更新内存缓存
            with self._lru_lock:
                for item in items:
                    # 主动填充 _hit_cache（iter-73）：从 item.hits 构造 result dict，
                    # 使下次 get_cached_hits 命中内存跳过 SQLite。item.hits 完整时
                    # （如冷缓存首次扫描所有规则）LRU 命中；不完整时（混合路径部分
                    # 规则已缓存）_hit_cache_get 检测 rule_hashes 集合不匹配，走
                    # SQLite 回填，安全降级
                    if item.hits:
                        rule_hashes = [rh for rh, _ in item.hits]
                        result_dict: dict[str, RuleHit | None] = dict(item.hits)
                        self._hit_cache_put(item.file_hash, rule_hashes, result_dict)
                    # item.hits 为空（预筛命中，仅刷新元数据）：scan_results 未变，
                    # 保留 LRU 中已有条目，避免下次查询走 SQLite
                    # 主动填充路径预筛 LRU：使下次 lookup_file_hash 命中内存
                    self._path_cache_put(str(item.path), item.mtime, item.size, item.file_hash)

    def lookup_file_hash(
        self,
        path: Path,
        mtime: float,
        size: int,
    ) -> str | None:
        """按 ``(path, mtime, size)`` 查询已登记的 ``file_hash``。

        用于缓存模式预筛：文件 mtime 与 size 未变时，
        可直接复用已登记的 ``file_hash``，跳过 ``read_bytes`` 与哈希计算。

        线程安全：使用线程本地只读连接，无锁并行（iter-68）。
        查询优化：JOIN 替代 IN 子查询 + 复合索引（iter-70），消除全表扫描。
        内存缓存（iter-73）：先查进程内 LRU，命中跳过 SQLite 查询；
        ``register_path`` / ``batch_put_results`` 写入后主动填充 LRU，
        使热缓存二次扫描完全命中内存。

        安全性说明：mtime 可被人为修改，本方法仅作为性能优化；
        对安全性敏感场景，调用方可关闭此预筛（始终走哈希校验）。

        :param path: 文件路径
        :param mtime: 当前文件 mtime（秒，浮点）
        :param size: 当前文件大小（字节）
        :return: 命中时返回 ``file_hash``（64 字符 hex）；未命中返回 None
        """
        path_str = str(path)
        # 先查进程内 LRU（iter-73）：热缓存二次扫描时 100% 命中，跳过 SQLite
        with self._lru_lock:
            cached = self._path_cache_get(path_str, mtime, size)
        if cached is not None:
            return cached
        # JOIN 形式：先用 idx_paths_path_mtime 按 (path, mtime) 定位，
        # 再用 scanned_files 主键 (file_hash) JOIN 验证 size，全程索引扫描
        row = (
            self._get_read_conn()
            .execute(
                "SELECT fp.file_hash FROM file_paths fp "
                "JOIN scanned_files sf ON fp.file_hash = sf.file_hash "
                "WHERE fp.path = ? AND fp.mtime = ? AND sf.size = ?",
                (path_str, mtime, size),
            )
            .fetchone()
        )
        if row is None:
            # 未登记路径不缓存（None 不写入 LRU），避免污染缓存
            return None
        file_hash = row["file_hash"]
        # 命中 SQLite 后回填 LRU，下次同一 (path, mtime, size) 直接命中内存
        with self._lru_lock:
            self._path_cache_put(path_str, mtime, size, file_hash)
        return file_hash

    # ------------------------------------------------------------------ 提取内容缓存

    def get_extracted_content(self, file_hash: str) -> str | None:
        """查询提取器结果缓存。

        用于缓存模式：同内容不同路径（如 node_modules 重复依赖）的文件，
        通过 ``file_hash`` 复用提取结果，跳过 ``extract_content_from_bytes``。

        线程安全：使用线程本地只读连接，无锁并行（iter-68）。

        :param file_hash: 文件内容哈希
        :return: 命中时返回提取后的纯文本；未命中返回 None
        """
        row = (
            self._get_read_conn()
            .execute(
                "SELECT content FROM extracted_contents WHERE file_hash = ?",
                (file_hash,),
            )
            .fetchone()
        )
        return row["content"] if row else None

    def put_extracted_content(self, file_hash: str, content: str, extension: str) -> None:
        """写入提取器结果缓存。

        仅缓存非空内容；空内容（如提取失败回退到空字符串）不缓存，避免哨兵值污染。
        ``scanned_files`` 中须已存在该 ``file_hash``（外键约束），
        调用方通常先 :meth:`register_file` 再调本方法。

        :param file_hash: 文件内容哈希
        :param content: 提取后的纯文本内容
        :param extension: 文件扩展名（用于诊断与未来按格式清理）
        """
        if not content:
            return
        now = _now_iso()
        with self._lock:
            # 确保 scanned_files 存在该 file_hash，避免外键约束失败
            self._conn.execute(
                "INSERT OR IGNORE INTO scanned_files "
                "(file_hash, size, first_seen_at, last_scanned_at) VALUES (?, 0, ?, ?)",
                (file_hash, now, now),
            )
            self._conn.execute(
                "INSERT INTO extracted_contents (file_hash, content, extension, cached_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(file_hash) DO UPDATE SET "
                "  content = excluded.content, extension = excluded.extension, "
                "  cached_at = excluded.cached_at",
                (file_hash, content, extension, now),
            )

    # ------------------------------------------------------------------ 清理与统计

    def prune_orphan_rules(self, active_rule_hashes: Collection[str]) -> int:
        """清理不在当前规则集中的旧规则及其缓存。

        清理后失效全部进程内 LRU 条目（规则哈希集合已变）。

        :param active_rule_hashes: 当前活跃的规则哈希集合
        :return: 删除的规则数（``rules`` 表行数）
        """
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM rules").fetchone()
            before = cur[0] if cur else 0
            if active_rule_hashes:
                placeholders = ",".join("?" for _ in active_rule_hashes)
                self._conn.execute(
                    f"DELETE FROM rules WHERE rule_hash NOT IN ({placeholders})",
                    tuple(active_rule_hashes),
                )
            else:
                self._conn.execute("DELETE FROM rules")
            cur = self._conn.execute("SELECT COUNT(*) FROM rules").fetchone()
            after = cur[0] if cur else 0
            deleted = before - after
            if deleted > 0:
                logger.info("清理孤立规则: %d 条", deleted)
                # 规则集合变化：全部 LRU 条目可能引用了已删除规则，整体失效
                with self._lru_lock:
                    self._hit_cache.clear()
                    self._path_cache.clear()
            return deleted

    def prune_stale_files(self, max_age_days: int = 30) -> int:
        """清理 ``last_scanned_at`` 早于 ``max_age_days`` 天的文件缓存。

        清理后失效全部进程内 LRU 条目。

        :param max_age_days: 最大保留天数
        :return: 删除的文件数（``scanned_files`` 表行数）
        """
        if max_age_days < 0:
            raise ValueError("max_age_days 不能为负数")
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM scanned_files").fetchone()
            before = cur[0] if cur else 0
            self._conn.execute(
                "DELETE FROM scanned_files WHERE last_scanned_at < ?",
                (_iso_days_ago(max_age_days),),
            )
            cur = self._conn.execute("SELECT COUNT(*) FROM scanned_files").fetchone()
            after = cur[0] if cur else 0
            deleted = before - after
            if deleted > 0:
                logger.info("清理过期文件缓存: %d 条（>=%d 天）", deleted, max_age_days)
                with self._lru_lock:
                    self._hit_cache.clear()
                    self._path_cache.clear()
            return deleted

    def stats(self) -> CacheStats:
        """返回缓存统计快照。

        诊断方法，不在扫描热路径上，使用主连接持锁以保证与写入的一致性。
        """
        with self._lock:
            rule_files = self._count("rule_files")
            rules = self._count("rules")
            scanned_files = self._count("scanned_files")
            file_paths = self._count("file_paths")
            scan_results = self._count("scan_results")
            extracted_contents = self._count("extracted_contents")
            db_bytes = self._db_path.stat().st_size if self._db_path.exists() else 0
            return CacheStats(
                rule_files=rule_files,
                rules=rules,
                scanned_files=scanned_files,
                file_paths=file_paths,
                scan_results=scan_results,
                extracted_contents=extracted_contents,
                db_bytes=db_bytes,
                schema_version=CURRENT_VERSION,
            )

    def _count(self, table: str) -> int:
        """统计表行数（已持 ``_lock``）。"""
        # table 名来自代码常量，非用户输入，无 SQL 注入风险
        cur = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return cur[0] if cur else 0

    # ------------------------------------------------------------------ 资源管理

    def close(self) -> None:
        """关闭数据库连接。重复调用安全（幂等）。

        关闭主写连接与所有线程本地读连接。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            with self._lru_lock:
                self._hit_cache.clear()
                self._path_cache.clear()
                read_conns = list(self._read_conns)
                self._read_conns.clear()
            # 关闭所有读连接
            for conn in read_conns:
                try:
                    conn.close()
                except sqlite3.Error:
                    logger.warning("关闭读连接失败", exc_info=True)
            self._conn.close()

    def __enter__(self) -> CacheStore:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def _iso_days_ago(days: int) -> str:
    """返回 ``days`` 天前的 UTC ISO 时间字符串。"""
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
