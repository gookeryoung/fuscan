"""缓存模块共享数据结构与纯工具函数。

从 :mod:`fuscan.cache.store` 抽离的模块级常量、数据类与无状态工具函数，
供 :class:`CacheStore` 与各职责子模块（``_queries``/``_writes``/``_cleanup``）复用。

公共 API：

- :class:`CacheStats`：缓存统计快照（不可变）
- :class:`BatchWriteItem`：批量写入项数据类
- :func:`default_cache_path`：默认缓存路径 ``~/.fuscan/cache.db``
- :func:`now_iso`：当前 UTC 时间的 ISO 8601 字符串
- :func:`iso_days_ago`：N 天前的 UTC ISO 时间字符串
- :data:`HIT_CACHE_MAX`：进程内 LRU 命中缓存容量上限
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fuscan.scanner.result import RuleHit

__all__ = [
    "HIT_CACHE_MAX",
    "BatchWriteItem",
    "CacheStats",
    "default_cache_path",
    "iso_days_ago",
    "now_iso",
]

# 进程内 LRU 命中缓存容量上限（条目数）。
# 每条平均 ~1KB（含 rule_hash 元组与 RuleHit），4096 条约占 4MB 内存。
HIT_CACHE_MAX: int = 4096


def default_cache_path() -> Path:
    """返回默认缓存路径：``~/.fuscan/cache.db``。"""
    return Path.home() / ".fuscan" / "cache.db"


def now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串（含时区后缀 ``Z``）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_days_ago(days: int) -> str:
    """返回 ``days`` 天前的 UTC ISO 时间字符串。"""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")


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
