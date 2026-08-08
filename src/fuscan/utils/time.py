"""通用 ISO 8601 时间字符串生成工具。

提供项目内跨模块复用的时间字符串生成函数，供缓存模块（:mod:`fuscan.cache`）、
历史归档模块（:mod:`fuscan.history`）、性能记录与 GUI 展示等场景共享，
避免重复实现。

公共 API：

- :func:`now_iso`：当前 UTC 时间的 ISO 8601 字符串（含时区后缀 ``Z``）
- :func:`iso_days_ago`：N 天前的 UTC ISO 时间字符串
- :func:`now_iso_local`：当前本地时间的 ISO 8601 字符串（秒精度，不含时区后缀）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

__all__ = ["iso_days_ago", "now_iso", "now_iso_local"]


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


def now_iso_local() -> str:
    """当前本地时间的 ISO 8601 字符串（秒精度，不含时区后缀）。

    格式：``YYYY-MM-DDTHH:MM:SS``。用于面向用户展示的时间戳（如 GUI 显示、
    性能记录），与 :func:`now_iso` 的 UTC+Z 风格互补——后者用于跨时区
    一致的内部存储与比较。
    """
    return datetime.now().isoformat(timespec="seconds")
