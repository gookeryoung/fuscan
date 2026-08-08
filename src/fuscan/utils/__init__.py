"""通用工具子包。

集中托管跨模块复用的无状态工具函数，避免各子模块重复实现。

公共 API：

- :func:`now_iso` / :func:`iso_days_ago`：UTC ISO 8601 时间字符串生成
"""

from __future__ import annotations

from fuscan.utils.time import iso_days_ago, now_iso

__all__ = ["iso_days_ago", "now_iso"]
