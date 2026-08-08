"""缓存模块共享数据结构与纯工具函数。

从 :mod:`fuscan.cache.store` 抽离的模块级常量、数据类与无状态工具函数，
供 :class:`CacheStore` 与各职责子模块（``_queries``/``_writes``/``_cleanup``）复用。

公共 API：

- :class:`CacheStats`：缓存统计快照（不可变）
- :class:`BatchWriteItem`：批量写入项数据类
- :func:`default_cache_path`：默认缓存路径 ``~/.fuscan/cache.db``
- :data:`HIT_CACHE_MAX`：进程内 LRU 命中缓存容量上限

.. note::

    时间工具函数（``now_iso`` / ``iso_days_ago``）已迁移到
    :mod:`fuscan.utils.time` 通用模块，供 cache 与 history 等子包共享。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fuscan.scanner.result import RuleHit

__all__ = [
    "EXTRACT_CACHE_MAX",
    "HIT_CACHE_MAX",
    "BatchWriteItem",
    "CacheStats",
    "default_cache_path",
]

# 进程内 LRU 命中缓存容量上限（条目数）。
# 每条平均 ~1KB（含 rule_hash 元组与 RuleHit），4096 条约占 4MB 内存。
HIT_CACHE_MAX: int = 4096

# 提取内容内存 LRU 缓存容量上限（条目数）。
# 提取后的纯文本内容较大（docx/pptx 平均 20KB），512 条约占 10MB 内存。
# node_modules 重复依赖场景下，同一 file_hash 的内容会被查询多次，
# 内存 LRU 使二次及后续查询完全命中内存，跳过 SQLite 查询。
EXTRACT_CACHE_MAX: int = 512


def default_cache_path() -> Path:
    """返回默认缓存路径：``~/.fuscan/cache.db``。"""
    return Path.home() / ".fuscan" / "cache.db"


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
