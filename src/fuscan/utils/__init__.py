"""通用工具子包。

集中托管跨模块复用的无状态工具函数，避免各子模块重复实现。

公共 API：

- :func:`now_iso` / :func:`iso_days_ago`：UTC ISO 8601 时间字符串生成
- :func:`atomic_write_text` / :func:`atomic_write_bytes`：原子写入文件
"""

from __future__ import annotations

from fuscan.utils.io import atomic_write_bytes, atomic_write_text
from fuscan.utils.time import iso_days_ago, now_iso

__all__ = ["atomic_write_bytes", "atomic_write_text", "iso_days_ago", "now_iso"]
