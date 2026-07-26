"""缓存清理与统计：从 :class:`CacheStore` 抽离的维护性子流程。

包含孤立规则清理、过期文件清理、缓存统计快照。所有函数接收
:class:`CacheStore` 实例作为首参，通过其主写连接 + ``RLock`` 串行化。

模块化拆分原因：原 ``cache/store.py`` 单文件 886 行，清理与统计逻辑
与查询/写入混杂。本模块专责维护路径，便于独立调整清理策略与统计口径。

公共 API：

- :func:`prune_orphan_rules`：清理不在当前规则集中的旧规则
- :func:`prune_stale_files`：清理过期文件缓存
- :func:`stats`：返回缓存统计快照
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Collection

from fuscan.cache._helpers import CacheStats, iso_days_ago
from fuscan.cache.schema import CURRENT_VERSION

if TYPE_CHECKING:
    from fuscan.cache.store import CacheStore

__all__ = ["prune_orphan_rules", "prune_stale_files", "stats"]

logger = logging.getLogger(__name__)


def prune_orphan_rules(store: CacheStore, active_rule_hashes: Collection[str]) -> int:
    """清理不在当前规则集中的旧规则及其缓存。

    清理后失效全部进程内 LRU 条目（规则哈希集合已变）。

    :param store: 所属 CacheStore 实例
    :param active_rule_hashes: 当前活跃的规则哈希集合
    :return: 删除的规则数（``rules`` 表行数）
    """
    with store._lock:
        cur = store._conn.execute("SELECT COUNT(*) FROM rules").fetchone()
        before = cur[0] if cur else 0
        if active_rule_hashes:
            placeholders = ",".join("?" for _ in active_rule_hashes)
            store._conn.execute(
                f"DELETE FROM rules WHERE rule_hash NOT IN ({placeholders})",
                tuple(active_rule_hashes),
            )
        else:
            store._conn.execute("DELETE FROM rules")
        cur = store._conn.execute("SELECT COUNT(*) FROM rules").fetchone()
        after = cur[0] if cur else 0
        deleted = before - after
        if deleted > 0:
            logger.info("清理孤立规则: %d 条", deleted)
            # 规则集合变化：全部 LRU 条目可能引用了已删除规则，整体失效
            with store._lru_lock:
                store._hit_cache.clear()
                store._path_cache.clear()
        return deleted


def prune_stale_files(store: CacheStore, max_age_days: int = 30) -> int:
    """清理 ``last_scanned_at`` 早于 ``max_age_days`` 天的文件缓存。

    清理后失效全部进程内 LRU 条目。

    :param store: 所属 CacheStore 实例
    :param max_age_days: 最大保留天数
    :return: 删除的文件数（``scanned_files`` 表行数）
    """
    if max_age_days < 0:
        raise ValueError("max_age_days 不能为负数")
    with store._lock:
        cur = store._conn.execute("SELECT COUNT(*) FROM scanned_files").fetchone()
        before = cur[0] if cur else 0
        store._conn.execute(
            "DELETE FROM scanned_files WHERE last_scanned_at < ?",
            (iso_days_ago(max_age_days),),
        )
        cur = store._conn.execute("SELECT COUNT(*) FROM scanned_files").fetchone()
        after = cur[0] if cur else 0
        deleted = before - after
        if deleted > 0:
            logger.info("清理过期文件缓存: %d 条（>=%d 天）", deleted, max_age_days)
            with store._lru_lock:
                store._hit_cache.clear()
                store._path_cache.clear()
        return deleted


def stats(store: CacheStore) -> CacheStats:
    """返回缓存统计快照。

    诊断方法，不在扫描热路径上，使用主连接持锁以保证与写入的一致性。

    :param store: 所属 CacheStore 实例
    :return: 缓存统计快照
    """
    with store._lock:
        rule_files = _count(store, "rule_files")
        rules = _count(store, "rules")
        scanned_files = _count(store, "scanned_files")
        file_paths = _count(store, "file_paths")
        scan_results = _count(store, "scan_results")
        extracted_contents = _count(store, "extracted_contents")
        db_bytes = store._db_path.stat().st_size if store._db_path.exists() else 0
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


def _count(store: CacheStore, table: str) -> int:
    """统计表行数（已持 ``_lock``）。

    :param store: 所属 CacheStore 实例
    :param table: 表名（来自代码常量，非用户输入，无 SQL 注入风险）
    :return: 表行数
    """
    # table 名来自代码常量，非用户输入，无 SQL 注入风险
    cur = store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return cur[0] if cur else 0
