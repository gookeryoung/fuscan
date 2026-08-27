"""QtWidgets GUI 子包（QML 已全面迁移至本层）。

按职责拆分：

- :mod:`fuscan.gui.widgets.qss`：全局样式表构建（色板唯一来源）
- :mod:`fuscan.gui.widgets.icons`：SVG 染色图标
- :mod:`fuscan.gui.widgets.sidebar`：侧边栏导航
- :mod:`fuscan.gui.widgets.splash`：启动画面
- :mod:`fuscan.gui.widgets.main_window`：主窗口骨架

公共 API（惰性导出，避免无 GUI 环境下 import 失败）：

- :class:`MainWindow`：主窗口
- :class:`SplashWindow`：启动画面
- :class:`AboutPage`：关于页
- :class:`FileMonitorPage`：文件监控页
- :class:`StatsPage`：统计页
- :class:`SettingsPage`：设置页
"""

from __future__ import annotations


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """惰性导入 Widgets 顶层组件。"""
    if name == "MainWindow":
        from fuscan.gui.widgets.main_window import MainWindow

        return MainWindow
    if name == "SplashWindow":
        from fuscan.gui.widgets.splash import SplashWindow

        return SplashWindow
    if name == "AboutPage":
        from fuscan.gui.widgets.about_page import AboutPage

        return AboutPage
    if name == "FileMonitorPage":
        from fuscan.gui.widgets.file_monitor_page import FileMonitorPage

        return FileMonitorPage
    if name == "StatsPage":
        from fuscan.gui.widgets.stats_page import StatsPage

        return StatsPage
    if name == "SettingsPage":
        from fuscan.gui.widgets.settings_page import SettingsPage

        return SettingsPage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
