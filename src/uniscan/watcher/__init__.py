"""文件监控子包。

提供：

- :class:`FileMonitor`：基于 watchdog 的目录监控
- :class:`IncrementalScanner`：增量扫描器（跳过未变化文件）
- :func:`default_ignore_dirs`：平台默认忽略目录
- :class:`TrayApp`：托盘驻守应用（P4-1 实现）
"""

from __future__ import annotations

from uniscan.watcher.ignore_dirs import default_ignore_dirs
from uniscan.watcher.incremental import IncrementalScanner
from uniscan.watcher.monitor import FileEvent, FileEventType, FileMonitor, MonitorConfig

__all__ = [
    "FileEvent",
    "FileEventType",
    "FileMonitor",
    "IncrementalScanner",
    "MonitorConfig",
    "TrayApp",
    "default_ignore_dirs",
]


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """惰性导入 TrayApp，避免无 GUI 环境下 import 失败。"""
    if name == "TrayApp":
        from uniscan.watcher.tray import TrayApp

        return TrayApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
