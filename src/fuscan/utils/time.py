"""通用 UTC ISO 8601 时间字符串生成工具。

提供项目内跨模块复用的时间字符串生成函数，供缓存模块（:mod:`fuscan.cache`）
与历史归档模块（:mod:`fuscan.history`）共享，避免重复实现。

公共 API：

- :func:`now_iso`：当前 UTC 时间的 ISO 8601 字符串（含时区后缀 ``Z``）
- :func:`iso_days_ago`：N 天前的 UTC ISO 时间字符串
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

__all__ = ["iso_days_ago", "now_iso"]


def now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串（含时区后缀 ``Z``）。

    格式：``YYYY-MM-DDTHH:MM:SSZ``（秒精度，``Z`` 表示 UTC）。
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_days_ago(days: int) -> str:
    """返回 ``days`` 天前的 UTC ISO 时间字符串。

    :param days: 天数（非负）
    :return: ISO 8601 字符串（含时区后缀 ``Z``）
    """
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
