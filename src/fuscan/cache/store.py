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
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Collection, Mapping

from fuscan.cache._cleanup import prune_orphan_rules, prune_stale_files, stats
from fuscan.cache._helpers import (
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
