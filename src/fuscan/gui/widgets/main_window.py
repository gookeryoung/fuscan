"""主窗口：原生标题栏 + 侧边栏导航 + 六页 QStackedWidget。

- 布局：HBox（侧边栏 | 内容栈），内容栈 6 页常驻
- 快捷键：Ctrl+1..6 切页 / Ctrl+B 折叠侧边栏 / Ctrl+R 重扫当前工作区 /
  Esc 返回首页
- 暗色切换：侧边栏开关驱动 :meth:`set_dark`，整表替换全局 QSS 并刷新全部子页面
- 关闭窗口：显示保存进度对话框后异步调用 ``controller.cleanup()``，复刻
  原 exitPopup 渐进退出模式
"""

# pyrefly: ignore-errors
# PySide2 存根重载合并缺陷导致 QWidget/QLayout/QTimer 调用与 Signal.connect 误报，详见 sidebar.py 头部说明。

from __future__ import annotations

from typing import Any

from PySide2.QtCore import QTimer
from PySide2.QtGui import QCloseEvent, QKeySequence
from PySide2.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QProgressDialog,
    QShortcut,
    QStackedWidget,
    QWidget,
)

from fuscan.gui.widgets.about_page import AboutPage
from fuscan.gui.widgets.file_monitor_page import FileMonitorPage
from fuscan.gui.widgets.home_page import HomePage
from fuscan.gui.widgets.qss import build_app_qss
from fuscan.gui.widgets.results_page import ResultsPage
from fuscan.gui.widgets.settings_page import SettingsPage
from fuscan.gui.widgets.sidebar import SidebarWidget
from fuscan.gui.widgets.stats_page import StatsPage

__all__ = ["PAGE_IDS", "MainWindow"]

# 页面 id 与堆栈索引一一对应（顺序与侧边栏/快捷键约定一致）
PAGE_IDS: tuple[str, ...] = ("home", "monitor", "results", "stats", "settings", "about")


class MainWindow(QMainWindow):
    """fuscan 主窗口（Widgets 版）。"""

    def __init__(self, controller: Any, parent: QWidget | None = None) -> None:
        """初始化主窗口。

        :param controller: :class:`~fuscan.gui.controllers.app_controller.AppController`
            主控制器（业务层不迁移，Widgets 直接持引用调用）
        :param parent: 父部件
        """
        super().__init__(parent)
        self._controller = controller
        self._dark = False
        self.setWindowTitle("fuscan")
        self.resize(1080, 680)
        self.setMinimumSize(880, 560)
        self._build_ui()
        self._build_shortcuts()

    # ----------------------------- 构建 -----------------------------

    def _build_ui(self) -> None:
        """组装侧边栏 + 内容栈。"""
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.sidebar = SidebarWidget(dark=self._dark)
        self.sidebar.pageChanged.connect(self.switch_page)
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        for page_id in PAGE_IDS:
            self.stack.addWidget(self._make_page(page_id))
        layout.addWidget(self.stack, stretch=1)
        self.setCentralWidget(central)
        # 工作区卡片「查看结果/统计」→ 跳页（当前工作区已由 HomePage 先行切换）
        home = self.stack.widget(PAGE_IDS.index("home"))
        if isinstance(home, HomePage):
            home.viewResultsRequested.connect(lambda _ws_id: self.switch_page("results"))
            home.viewStatsRequested.connect(lambda _ws_id: self.switch_page("stats"))
        # 结果页「返回」→ 回文件扫描页
        results = self.stack.widget(PAGE_IDS.index("results"))
        if isinstance(results, ResultsPage):
            results.backRequested.connect(lambda: self.switch_page("home"))
        # 默认首页
        self.sidebar.set_current_page("home")

    def _make_page(self, page_id: str) -> QWidget:
        """构建指定页面（六页均已迁移为正式 Widgets 实现）。"""
        if page_id == "home":
            return HomePage(self._controller)
        if page_id == "results":
            return ResultsPage(self._controller)
        if page_id == "about":
            return AboutPage(self._controller)
        if page_id == "monitor":
            return FileMonitorPage(self._controller)
        if page_id == "stats":
            return StatsPage(self._controller)
        return SettingsPage(self._controller)

    def _build_shortcuts(self) -> None:
        """注册全局快捷键：Ctrl+1..6 切页 / Ctrl+B 折叠 / Ctrl+R 重扫 / Esc 回首页。"""
        page_keys = {
            "Ctrl+1": "home",
            "Ctrl+2": "monitor",
            "Ctrl+3": "results",
            "Ctrl+4": "stats",
            "Ctrl+5": "settings",
            "Ctrl+6": "about",
        }
        for seq, page_id in page_keys.items():
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.activated.connect(lambda pid=page_id: self.sidebar.set_current_page(pid))
        collapse_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        collapse_shortcut.activated.connect(lambda: self.sidebar.set_collapsed(not self.sidebar.collapsed))
        rescan_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        rescan_shortcut.activated.connect(self._rescan_current)
        esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        esc_shortcut.activated.connect(lambda: self.sidebar.set_current_page("home"))

    def _rescan_current(self) -> None:
        """Ctrl+R：重扫当前工作区（未选中时忽略）。"""
        wc = self._controller.workspace
        ws_id = str(wc.currentWorkspaceId)
        if ws_id:
            wc.startScan(ws_id)

    # ----------------------------- 页面切换与主题 -----------------------------

    def switch_page(self, page_id: str) -> None:
        """切换到指定页面（未知 id 归位首页）。"""
        if page_id not in PAGE_IDS:
            page_id = "home"
        self.stack.setCurrentIndex(PAGE_IDS.index(page_id))

    def set_dark(self, dark: bool) -> None:
        """整表替换全局 QSS 并刷新侧边栏配色。

        :param dark: 是否启用深色主题
        """
        self._dark = dark
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_app_qss(dark))
        self.sidebar.blockSignals(True)
        self.sidebar.set_dark(dark)
        self.sidebar.blockSignals(False)
        # 主题切换时刷新子页面语义色（逐页探测 set_dark）
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            if hasattr(page, "set_dark"):
                page.set_dark(dark)

    @property
    def dark_mode(self) -> bool:
        """当前是否为深色主题。"""
        return self._dark

    # ----------------------------- 关闭流程 -----------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """拦截关闭：先渲染保存进度对话框，再异步清理资源退出。

        退出确认弹窗确认后延时 50ms 再执行清理退出。
        """
        event.ignore()
        dialog = QProgressDialog("正在清理扫描线程与缓存资源", "", 0, 0, self)
        dialog.setWindowTitle("fuscan")
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.show()

        def _quit() -> None:
            self._controller.cleanup()
            app = QApplication.instance()
            if app is not None:
                app.quit()

        QTimer.singleShot(50, _quit)
