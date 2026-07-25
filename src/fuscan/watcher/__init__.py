"""文件监控子包。

提供：

- :class:`FileMonitor`：基于 watchdog 的目录监控
- :class:`IncrementalScanner`：增量扫描器（跳过未变化文件）
- :func:`default_ignore_dirs`：平台默认忽略目录

托盘驻守应用（TrayApp）已在 QML 迁移中移除，后续单独设计 GUI 集成方案。
"""

from __future__ import annotations

from fuscan.watcher.ignore_dirs import default_ignore_dirs
from fuscan.watcher.incremental import IncrementalScanner
from fuscan.watcher.monitor import FileEvent, FileEventType, FileMonitor, MonitorConfig

__all__ = [
    "FileEvent",
    "FileEventType",
    "FileMonitor",
    "IncrementalScanner",
    "MonitorConfig",
    "default_ignore_dirs",
]
