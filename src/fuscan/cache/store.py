"""SQLite 持久化扫描结果缓存。

公共 API：

- :class:`CacheStore`：线程安全的 SQLite 缓存，封装规则登记、结果查询、清理等操作
- :class:`CacheStats`：缓存统计快照（不可变，从 :mod:`fuscan.cache._helpers` 复用）
- :class:`BatchWriteItem`：批量写入项（从 :mod:`fuscan.cache._helpers` 复用）
- :func:`default_cache_path`：默认缓存路径（从 :mod:`fuscan.cache._helpers` 复用）

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
- **提取内容 LRU 缓存**（iter-118）：``get_extracted_content`` 结果在内存中
  再缓存一份；node_modules 重复依赖等场景下，同一 ``file_hash`` 的内容查询
  二次及后续完全命中内存，跳过 SQLite 查询。``put_extracted_content`` 写入后
  主动填充 LRU

模块结构（iter-108 拆分）：

- :mod:`fuscan.cache._helpers`：数据类与无状态工具函数（CacheStats/BatchWriteItem/时间工具）
- :mod:`fuscan.cache._queries`：只读查询子流程（命中缓存、路径预筛、提取内容）
- :mod:`fuscan.cache._writes`：写入子流程（规则登记、结果写入、批量写入）
- :mod:`fuscan.cache._cleanup`：清理与统计子流程
- 本模块：:class:`CacheStore` 主类，管理连接生命周期与内存 LRU，公共方法
  委托到对应子模块
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import weakref
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Collection, Mapping

from fuscan.cache._cleanup import prune_orphan_rules, prune_stale_files, stats
from fuscan.cache._helpers import (
    EXTRACT_CACHE_MAX,
    HIT_CACHE_MAX,
    BatchWriteItem,
    CacheStats,
    default_cache_path,
)
from fuscan.cache._queries import get_cached_hits, get_extracted_content, get_rule_hashes, lookup_file_hash
from fuscan.cache._writes import (
    batch_put_results,
    put_extracted_content,
    put_result,
    register_file,
    register_path,
    register_ruleset,
)
from fuscan.cache.schema import migrate
from fuscan.rules.model import RuleSet

if TYPE_CHECKING:
    from fuscan.scanner.result import RuleHit

__all__ = ["BatchWriteItem", "CacheStats", "CacheStore", "default_cache_path"]

logger = logging.getLogger(__name__)

# iter-110：PRAGMA 调优常量。集中在模块级便于调整与诊断，所有连接（读/写）
# 共用同一组参数，避免行为不一致。
# - mmap_size=256MB：内存映射 I/O，大缓存库（>10MB）读路径减少 syscall 与
#   用户态/内核态数据拷贝；64 位系统地址空间充裕，256MB 上限足够覆盖
#   fuscan 典型工作集（100MB 量级）
# - cache_size=64MB（负值表示 KiB）：SQLite 页缓存，避免反复读同一页
# - temp_store=MEMORY：临时 B-tree 与排序在内存中完成
# - wal_autocheckpoint=10000：WAL 累积 10000 页（约 40MB）才 checkpoint，
#   默认 1000 页过于激进，扫描期间频繁 checkpoint 导致 fsync 卡顿
_PRAGMA_MMAP_SIZE: int = 256 * 1024 * 1024
_PRAGMA_CACHE_SIZE_KIB: int = -65536  # 负值表示 KiB，65536 KiB = 64 MiB
_PRAGMA_WAL_AUTOCHECKPOINT: int = 10000


def _apply_pragmas(conn: sqlite3.Connection, read_only: bool) -> None:
    """对连接应用 PRAGMA 调优（iter-110）。

    :param conn: 待配置的 SQLite 连接
    :param read_only: ``True`` 表示只读连接（应用 ``query_only=ON`` 防误写）；
        ``False`` 表示主写连接（额外设置 ``wal_autocheckpoint``）

    PRAGMA 失败（如低版本 SQLite 不支持）记 WARNING 不抛异常，保持向前兼容。
    """
    # 基础 PRAGMA（读/写连接共用）
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    # 安全设置 mmap_size：低版本或某些平台可能不支持，失败时降级到默认 0
    try:
        conn.execute(f"PRAGMA mmap_size = {_PRAGMA_MMAP_SIZE}")
    except sqlite3.DatabaseError:
        logger.warning("PRAGMA mmap_size 不支持，降级到默认", exc_info=True)
    try:
        conn.execute(f"PRAGMA cache_size = {_PRAGMA_CACHE_SIZE_KIB}")
    except sqlite3.DatabaseError:
        logger.warning("PRAGMA cache_size 不支持，降级到默认", exc_info=True)
    if read_only:
        # 只读保护：防止读连接误写，违反 query_only 会抛 sqlite3.OperationalError
        conn.execute("PRAGMA query_only = ON")
    else:
        # 仅主写连接设置 wal_autocheckpoint：控制 WAL 文件增长节奏
        try:
            conn.execute(f"PRAGMA wal_autocheckpoint = {_PRAGMA_WAL_AUTOCHECKPOINT}")
        except sqlite3.DatabaseError:
            logger.warning("PRAGMA wal_autocheckpoint 不支持，降级到默认", exc_info=True)


class _ConnRef:
    """``sqlite3.Connection`` 的弱引用包装（iter-147）。

    ``sqlite3.Connection`` 是 C 扩展类型，未设 ``tp_weaklistoffset``，
    不支持 ``weakref.ref``。用本包装类间接实现弱引用跟踪：

    - ``_read_local`` 持有强引用 ``_ConnRef``（线程本地）
    - ``_read_conns: WeakSet[_ConnRef]`` 持有弱引用

    worker 线程正常退出时 ``threading.local`` 数据 slot 被清理，
    ``_ConnRef`` 失去强引用被 GC，``WeakSet`` 自动移除条目，
    连接对象随之 GC 释放 OS 句柄。daemon worker 被 OS 强杀时
    ``threading.local`` 不会清理，依赖 :meth:`CacheStore.close`
    主动关闭残留连接（由 FIX-2 统一 cleanup 路径保证 ``close`` 被调用）。
    """

    __slots__ = ("__weakref__", "conn")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn


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

    本类仅负责连接生命周期与内存 LRU 缓存管理；具体 SQL 操作委托到
    ``_queries``/``_writes``/``_cleanup`` 子模块，保持职责单一。
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
        # 提取内容 LRU 缓存（iter-118）：file_hash -> content
        # get_extracted_content 命中时跳过 SQLite 查询；put_extracted_content
        # 写入后主动填充。node_modules 重复依赖场景下显著减少 SQLite 查询次数
        self._extract_cache: OrderedDict[str, str] = OrderedDict()
        # 线程本地只读连接：每线程一个，WAL 模式下读完全并行
        self._read_local: threading.local = threading.local()
        # iter-147：读连接弱引用集合（close 时统一关闭，用 _lru_lock 保护）。
        # 用 WeakSet[_ConnRef] 替代原 list[sqlite3.Connection]：worker 线程正常
        # 退出后 threading.local 数据 slot 被清理，_ConnRef 失去强引用被 GC，
        # WeakSet 自动移除条目，避免原 list 强引用导致连接永不释放、list 膨胀。
        # daemon worker 被 OS 强杀时依赖 close() 主动关闭（FIX-2 保证）。
        self._read_conns: weakref.WeakSet[_ConnRef] = weakref.WeakSet()
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
        """返回当前线程的只读连接（惰性创建并复用）。

        每个线程首次调用时创建独立连接，配置 WAL + ``query_only = ON``
        防止误写。WAL 模式下读不阻塞写，读连接可完全并行执行查询。

        iter-147 修复：原实现每次调用都创建新连接并覆盖 ``_read_local.conn``，
        导致 ``_read_conns`` 列表无限膨胀（每次扫描每文件查询都新增连接）。
        现改为先检查 ``_read_local.ref`` 是否已存在，有则复用，避免重复创建。
        连接经 :class:`_ConnRef` 包装后登记到 ``_read_conns`` 弱引用集合，
        worker 线程退出后自动 GC 释放，``close`` 时统一关闭残留连接。
        """
        ref = getattr(self._read_local, "ref", None)
        if ref is not None and ref.conn is not None:
            return ref.conn
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # 自动提交，WAL 下每次查询读最新快照
        )
        conn.row_factory = sqlite3.Row
        _apply_pragmas(conn, read_only=True)
        wrapper = _ConnRef(conn)
        self._read_local.ref = wrapper
        with self._lru_lock:
            self._read_conns.add(wrapper)
        return conn

    def _init_db(self) -> None:
        """初始化数据库：启用 WAL、外键，迁移 schema。"""
        with self._lock:
            _apply_pragmas(self._conn, read_only=False)
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
        while len(self._hit_cache) > HIT_CACHE_MAX:
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
        while len(self._path_cache) > HIT_CACHE_MAX:
            self._path_cache.popitem(last=False)

    def _extract_cache_get(self, file_hash: str) -> str | None:
        """查询提取内容 LRU 缓存（已持 ``_lru_lock``）。

        命中时移动到队尾（LRU 语义），返回内容字符串；未命中返回 None。
        注意：``None`` 与空字符串语义不同——``None`` 表示未缓存（需走 SQLite），
        空字符串表示已缓存但提取结果为空（不应写入 LRU，由 ``put_extracted_content`` 保证）。
        """
        content = self._extract_cache.get(file_hash)
        if content is not None:
            self._extract_cache.move_to_end(file_hash)
        return content

    def _extract_cache_put(self, file_hash: str, content: str) -> None:
        """写入提取内容 LRU 缓存（已持 ``_lru_lock``）。

        超容量时弹出最旧条目。空内容不写入（与 ``put_extracted_content`` 一致）。
        """
        if not content:
            return
        self._extract_cache[file_hash] = content
        self._extract_cache.move_to_end(file_hash)
        while len(self._extract_cache) > EXTRACT_CACHE_MAX:
            self._extract_cache.popitem(last=False)

    def _extract_cache_invalidate(self, file_hash: str) -> None:
        """失效指定 ``file_hash`` 的提取内容内存缓存条目（已持锁）。"""
        self._extract_cache.pop(file_hash, None)

    def hit_cache_size(self) -> int:
        """返回进程内 LRU 命中缓存当前条目数（诊断用）。"""
        with self._lru_lock:
            return len(self._hit_cache)

    def path_cache_size(self) -> int:
        """返回路径预筛 LRU 缓存当前条目数（诊断用，iter-73）。"""
        with self._lru_lock:
            return len(self._path_cache)

    def extract_cache_size(self) -> int:
        """返回提取内容 LRU 缓存当前条目数（诊断用，iter-118）。"""
        with self._lru_lock:
            return len(self._extract_cache)

    # ------------------------------------------------------------------ 规则登记

    def register_ruleset(
        self,
        ruleset: RuleSet,
        source_files: Mapping[Path, str] | None = None,
    ) -> dict[str, str]:
        """登记规则集到缓存（委托 :func:`fuscan.cache._writes.register_ruleset`）。"""
        return register_ruleset(self, ruleset, source_files)  # pyrefly: ignore [bad-argument-type]

    def get_rule_hashes(self) -> dict[str, str]:
        """查询当前已登记的 ``rule_name -> rule_hash`` 映射（委托 :func:`fuscan.cache._queries.get_rule_hashes`）。"""
        return get_rule_hashes(self)  # pyrefly: ignore [bad-argument-type]

    # ------------------------------------------------------------------ 结果缓存

    def get_cached_hits(
        self,
        file_hash: str,
        rule_hashes: Collection[str],
    ) -> dict[str, RuleHit | None]:
        """批量查询缓存结果（委托 :func:`fuscan.cache._queries.get_cached_hits`）。"""
        return get_cached_hits(self, file_hash, rule_hashes)  # pyrefly: ignore [bad-argument-type]

    def put_result(
        self,
        file_hash: str,
        rule_hash: str,
        hit: RuleHit | None,
    ) -> None:
        """写入单条缓存结果（委托 :func:`fuscan.cache._writes.put_result`）。"""
        put_result(self, file_hash, rule_hash, hit)  # pyrefly: ignore [bad-argument-type]

    def register_file(self, file_hash: str, size: int) -> None:
        """登记/更新 ``scanned_files``（委托 :func:`fuscan.cache._writes.register_file`）。"""
        register_file(self, file_hash, size)  # pyrefly: ignore [bad-argument-type]

    def register_path(self, file_hash: str, path: Path, mtime: float) -> None:
        """登记/更新 ``file_paths``（委托 :func:`fuscan.cache._writes.register_path`）。"""
        register_path(self, file_hash, path, mtime)  # pyrefly: ignore [bad-argument-type]

    def batch_put_results(self, items: list[BatchWriteItem]) -> None:
        """批量写入扫描结果与文件元数据（委托 :func:`fuscan.cache._writes.batch_put_results`）。"""
        batch_put_results(self, items)  # pyrefly: ignore [bad-argument-type]

    def lookup_file_hash(
        self,
        path: Path,
        mtime: float,
        size: int,
    ) -> str | None:
        """按 ``(path, mtime, size)`` 查询已登记的 ``file_hash``（委托 :func:`fuscan.cache._queries.lookup_file_hash`）。"""
        return lookup_file_hash(self, path, mtime, size)  # pyrefly: ignore [bad-argument-type]

    # ------------------------------------------------------------------ 提取内容缓存

    def get_extracted_content(self, file_hash: str) -> str | None:
        """查询提取器结果缓存（委托 :func:`fuscan.cache._queries.get_extracted_content`）。"""
        return get_extracted_content(self, file_hash)  # pyrefly: ignore [bad-argument-type]

    def put_extracted_content(self, file_hash: str, content: str, extension: str) -> None:
        """写入提取器结果缓存（委托 :func:`fuscan.cache._writes.put_extracted_content`）。"""
        put_extracted_content(self, file_hash, content, extension)  # pyrefly: ignore [bad-argument-type]

    # ------------------------------------------------------------------ 清理与统计

    def prune_orphan_rules(self, active_rule_hashes: Collection[str]) -> int:
        """清理不在当前规则集中的旧规则（委托 :func:`fuscan.cache._cleanup.prune_orphan_rules`）。"""
        return prune_orphan_rules(self, active_rule_hashes)  # pyrefly: ignore [bad-argument-type]

    def prune_stale_files(self, max_age_days: int = 30) -> int:
        """清理过期文件缓存（委托 :func:`fuscan.cache._cleanup.prune_stale_files`）。"""
        return prune_stale_files(self, max_age_days)  # pyrefly: ignore [bad-argument-type]

    def stats(self) -> CacheStats:
        """返回缓存统计快照（委托 :func:`fuscan.cache._cleanup.stats`）。"""
        return stats(self)  # pyrefly: ignore [bad-argument-type]

    # ------------------------------------------------------------------ 资源管理

    def close(self) -> None:
        """关闭数据库连接。重复调用安全（幂等）。

        关闭主写连接与所有线程本地读连接。iter-147 改为遍历 :class:`_ConnRef`
        弱引用集合，关闭仍存在的 ``ref.conn``；正常路径下 worker 退出后
        ``_ConnRef`` 已被 GC，此处仅关闭残留连接。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            with self._lru_lock:
                self._hit_cache.clear()
                self._path_cache.clear()
                self._extract_cache.clear()
                refs = list(self._read_conns)
                self._read_conns.clear()
            # 关闭所有仍存在的读连接（_ConnRef 可能已被 GC，跳过 None）
            closed_count = 0
            for ref in refs:
                conn = ref.conn
                if conn is None:
                    continue
                try:
                    conn.close()
                    closed_count += 1
                except sqlite3.Error:
                    logger.warning("关闭读连接失败", exc_info=True)
            if closed_count:
                logger.debug("已关闭 %d 个读连接", closed_count)
            self._conn.close()

    def __enter__(self) -> CacheStore:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
