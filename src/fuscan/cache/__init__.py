"""扫描结果缓存子包。

公共 API：

- :class:`CacheStore`：线程安全的 SQLite 缓存
- :class:`CacheStats`：缓存统计快照
- :func:`compute_rule_hash` / :func:`compute_file_hash` / :func:`hash_bytes`：哈希工具
- :func:`compute_source_files`：规则来源文件哈希映射计算
- :func:`serialize_rule` / :func:`serialize_match`：规则稳定序列化
- :func:`default_cache_path`：默认缓存路径 ``~/.fuscan/cache.db``
- :data:`CURRENT_VERSION`：当前 schema 版本
"""

from __future__ import annotations

from fuscan.cache.hashes import (
    compute_file_hash,
    compute_rule_hash,
    hash_bytes,
    serialize_match,
    serialize_rule,
)
from fuscan.cache.schema import CURRENT_VERSION
from fuscan.cache.sources import compute_source_files
from fuscan.cache.store import CacheStats, CacheStore, default_cache_path

__all__ = [
    "CURRENT_VERSION",
    "CacheStats",
    "CacheStore",
    "compute_file_hash",
    "compute_rule_hash",
    "compute_source_files",
    "default_cache_path",
    "hash_bytes",
    "serialize_match",
    "serialize_rule",
]
