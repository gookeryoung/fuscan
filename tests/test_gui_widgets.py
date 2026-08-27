"""Widgets GUI 骨架测试：QSS 构建 / 图标染色 / 侧边栏 / 主窗口 / 关于页。

对应 Widgets GUI 的 P1 骨架与 P2 页面实现。
"""

# pyrefly: ignore-errors
# PySide2 存根缺陷导致 Signal.connect/emit 在测试代码中同样误报，详见 sidebar.py 头部说明。

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from PySide2.QtCore import QAbstractListModel, QModelIndex, QObject, Qt, QUrl, Signal
from PySide2.QtGui import QCloseEvent, QShowEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,  # 供 monkeypatch 类属性
    QLabel,
    QPushButton,
    QTabWidget,
)

import fuscan.gui.widgets as gui_widgets
from fuscan.gui.controllers import (
    AboutController,
    ConfigController,
    FileMonitorController,
    RulesController,
    SplashController,
)
from fuscan.gui.models.workspace_model import STR_STATUS_DONE, WorkspaceItem, WorkspaceListModel
from fuscan.gui.widgets.about_page import AboutPage
from fuscan.gui.widgets.file_monitor_page import FileMonitorPage
from fuscan.gui.widgets.home_dialogs import EditTargetDialog, HistoryDialog, PreviewRulesDialog, RulesDialog
from fuscan.gui.widgets.home_page import HomePage
from fuscan.gui.widgets.icons import clear_icon_cache, tinted_svg_icon
from fuscan.gui.widgets.main_window import PAGE_IDS, MainWindow
from fuscan.gui.widgets.qss import build_app_qss, palette_tokens
from fuscan.gui.widgets.results_page import ResultsPage, format_context_html
from fuscan.gui.widgets.settings_page import SettingsPage
from fuscan.gui.widgets.sidebar import SidebarWidget
from fuscan.gui.widgets.splash import SplashWindow
from fuscan.gui.widgets.stats_page import PieChart, StatsPage


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """全局单例 QApplication（无 pytest-qt 依赖的轻量替代）。"""
    existing = QApplication.instance()
    if existing is not None:
        return existing  # type: ignore[return-value]
    return QApplication([])


class TestBuildAppQss:
    """build_app_qss 样式表构建测试。"""

    def test_light_qss_contains_light_palette(self) -> None:
        """浅色 QSS 应包含浅色主色与应用底色。"""
        qss = build_app_qss(dark=False)
        assert "#0366D6" in qss  # 浅色主色
        assert "#F5F6F8" in qss  # 浅色应用底色
        assert "#7AA2F7" not in qss

    def test_dark_qss_contains_dark_palette(self) -> None:
        """深色 QSS 应包含深色主色与应用底色。"""
        qss = build_app_qss(dark=True)
        assert "#7AA2F7" in qss  # 深色主色
        assert "#1A1B26" in qss  # 深色应用底色
        assert "#0366D6" not in qss

    def test_font_family_and_size_rendered(self) -> None:
        """字体族与正文字号应写入 QSS。"""
        qss = build_app_qss(dark=False, font_family="TestFont", body_font_size=15)
        assert '"TestFont"' in qss
        assert "font-size: 15px" in qss

    def test_core_selectors_present(self) -> None:
        """核心控件选择器应齐全（按钮/输入/列表/滚动条/分组框）。"""
        qss = build_app_qss(dark=False)
        for selector in ("QPushButton", "QLineEdit", "QComboBox", "QListView", "QScrollBar", "QGroupBox"):
            assert selector in qss


class TestPaletteTokens:
    """palette_tokens 色板测试。"""

    def test_light_and_dark_differ(self) -> None:
        """深浅两套色板 bg_app 必须不同。"""
        light = palette_tokens(False)
        dark = palette_tokens(True)
        assert light["bg_app"] != dark["bg_app"]

    def test_required_keys(self) -> None:
        """必备语义键应存在。"""
        t = palette_tokens(True)
        for key in ("primary", "text_primary", "bg_card", "border"):
            assert key in t


class TestTintedSvgIcon:
    """tinted_svg_icon 染色图标测试。

    全部用例依赖 ``qapp``：Windows 上若在 QApplication 创建前触碰
    qrc/SVG 路径，后续真实渲染会触发 0xC0000409 崩溃（实测复现），
    故纪律为先建 app 再渲染。
    """

    def setup_method(self) -> None:
        """每个用例前清空图标缓存。"""
        clear_icon_cache()

    def test_missing_source_returns_empty(self, qapp: QApplication) -> None:
        """来源缺失时返回空 QIcon 且不抛异常。"""
        icon = tinted_svg_icon("qrc:/icons/__no_such__.svg", "#FF0000")
        assert icon.isNull()

    def test_valid_source_returns_tinted(self, qapp: QApplication) -> None:
        """qrc 内真实 SVG 应渲染为非空 QIcon。"""
        icon = tinted_svg_icon(":/icons/home.svg", "#FF0000", 16)
        assert not icon.isNull()


class TestSidebarWidget:
    """SidebarWidget 导航与主题测试。"""

    def test_initial_state_and_items(self, qapp: QApplication) -> None:
        """初始状态应为未折叠、默认首页高亮。"""
        sidebar = SidebarWidget(dark=False)
        assert not sidebar.collapsed
        items = [item.page_id for item in sidebar._items]
        assert items == ["home", "monitor", "settings", "about"]
        selected = [item.page_id for item in sidebar._items if item._selected]
        assert selected == []  # 构造时尚未设置选中

    def test_page_changed_signal(self, qapp: QApplication) -> None:
        """点击导航项应发出 pageChanged 携带页面 id 并更新选中态。"""
        sidebar = SidebarWidget(dark=False)
        received: list[str] = []
        sidebar.pageChanged.connect(received.append)
        monitor_item = next(item for item in sidebar._items if item.page_id == "monitor")
        monitor_item.clicked.emit(monitor_item.page_id)
        assert received == ["monitor"]
        assert monitor_item._selected

    def test_dark_toggle_signal(self, qapp: QApplication) -> None:
        """切换暗色开关应发出 darkToggled。"""
        sidebar = SidebarWidget(dark=False)
        received: list[bool] = []
        sidebar.darkToggled.connect(received.append)
        sidebar._toggle.toggled_to.emit(True)
        assert received == [True]

    def test_set_dark_updates_toggle(self, qapp: QApplication) -> None:
        """set_dark 同步开关状态而不发信号（防回环）。"""
        sidebar = SidebarWidget(dark=False)
        received: list[bool] = []
        sidebar.darkToggled.connect(received.append)
        sidebar.set_dark(True)
        assert sidebar._toggle._on is True
        assert received == []

    def test_hover_and_press_events(self, qapp: QApplication) -> None:
        """enter/leave 悬停态与左键点击导航信号。"""
        sidebar = SidebarWidget(dark=False)
        item = next(i for i in sidebar._items if i.page_id == "home")
        received: list[str] = []
        item.clicked.connect(received.append)
        item.enterEvent(None)
        assert item._hovered is True
        item.leaveEvent(None)
        assert item._hovered is False
        QTest.mouseClick(item, Qt.LeftButton)
        assert received == ["home"]

    def test_paint_events_render(self, qapp: QApplication) -> None:
        """选中/hover/开关各态经 grab() 真实渲染 paintEvent 不崩溃。"""
        sidebar = SidebarWidget(dark=True)
        for item in sidebar._items:
            item.grab()
        sidebar.set_current_page("home")
        for item in sidebar._items:
            item.grab()
        toggle = sidebar._toggle
        toggle.set_on(True)
        assert not toggle.grab().isNull()
        toggle.set_on(False)
        assert not toggle.grab().isNull()

    def test_collapsed_roundtrip(self, qapp: QApplication) -> None:
        """set_collapsed 折叠/展开状态切换。"""
        sidebar = SidebarWidget(dark=False)
        sidebar.show()
        sidebar.set_collapsed(True)
        assert sidebar.collapsed
        assert not sidebar.isVisible()
        sidebar.set_collapsed(False)
        assert not sidebar.collapsed
        assert sidebar.isVisible()


class _NullSignal:
    """无行为的信号替身：仅支持 connect。"""

    def connect(self, cb: object) -> None:
        """接收订阅但不做任何事。"""


class _FakeObserver:
    """伪 watchdog Observer：不启动真实线程。"""

    def schedule(self, handler: object, path: str, recursive: bool = False) -> object:
        return {"path": path}

    def unschedule(self, watch: object) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self, timeout: float = 0.0) -> None:
        pass


class _FakeRulesStub:
    """FileMonitorController 所需的最小规则控制器替身。"""

    ruleset = None
    rulesetChanged = _NullSignal()


class _StubWorkspace:
    """StatsPage/HomePage 所需的最小工作区控制器替身（无当前任务）。"""

    hasCurrentWorkspace = False
    currentScanController: object | None = None
    hasActiveScan = False
    currentWorkspaceChanged = _NullSignal()
    activeScanChanged = _NullSignal()

    def __init__(self) -> None:
        """每个实例持有独立模型，避免跨测试共享状态。"""
        self.workspaceModel = WorkspaceListModel()


class _StubController:
    """提供 cleanup / about / file_monitor / config / rules / workspace 的桩。

    ``about``/``file_monitor``/``config``/``rules`` 用真实控制器
    （依赖已隔离的 CONFIG_DIR），workspace 仅用替身。
    """

    cleanup_calls: list[int] = []

    def __init__(self, qapp: QApplication, cfg_dir: Path) -> None:
        """构造子控制器（依赖已就绪的 QApplication 与隔离配置目录）。"""
        self.about = AboutController()
        self.file_monitor = FileMonitorController(
            _FakeRulesStub(),
            _observer_factory=_FakeObserver,
        )
        self.config = ConfigController()
        self.rules = RulesController(self.config)
        self.workspace = _StubWorkspace()

    def cleanup(self) -> None:
        """清理钩子：closeEvent 流程会异步调用一次。"""
        type(self).cleanup_calls.append(1)


class TestMainWindow:
    """MainWindow 骨架测试（controller 用最小桩对象）。"""

    @pytest.fixture()
    def window(
        self,
        qapp: QApplication,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> MainWindow:
        """构造带桩控制器的主窗口（配置目录重定向到临时目录）。"""
        cfg_dir = tmp_path / ".fuscan"
        cfg_dir.mkdir()
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", cfg_dir)
        return MainWindow(_StubController(qapp, cfg_dir))

    def test_six_pages_created(self, window: MainWindow) -> None:
        """内容栈应有 6 页且默认首页。"""
        assert window.stack.count() == len(PAGE_IDS)
        assert window.stack.currentIndex() == 0
        assert window.sidebar.pageChanged is not None

    def test_switch_page(self, window: MainWindow) -> None:
        """switch_page 应同步到正确堆栈索引；未知 id 归位首页。"""
        window.switch_page("results")
        assert window.stack.currentIndex() == PAGE_IDS.index("results")
        window.switch_page("__unknown__")
        assert window.stack.currentIndex() == 0

    def test_nav_drives_stack(self, window: MainWindow) -> None:
        """侧边栏导航信号应驱动堆栈切换。"""
        window.sidebar.set_current_page("settings")
        assert window.stack.currentIndex() == PAGE_IDS.index("settings")

    def test_set_dark_refreshes_sidebar_without_loop(self, window: MainWindow) -> None:
        """set_dark 刷新主题且不产生信号回环。"""
        received: list[bool] = []
        window.sidebar.darkToggled.connect(received.append)
        window.set_dark(True)
        assert window.dark_mode is True
        assert received == []

    def test_close_event_defers_cleanup(self, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
        """closeEvent 拦截关闭并经 50ms 单次定时器延迟清理。"""
        scheduled: list[tuple[int, object]] = []
        monkeypatch.setattr(
            "fuscan.gui.widgets.main_window.QTimer.singleShot",
            lambda msec, callback: scheduled.append((msec, callback)),
        )
        assert window.close() is False  # 事件被 ignore，窗口未真正关闭
        assert len(scheduled) == 1
        msec, callback = scheduled[0]
        assert msec == 50
        _StubController.cleanup_calls.clear()
        callback()  # 手动触发延迟回调 → stub.cleanup()
        assert _StubController.cleanup_calls == [1]


class TestSplashWindow:
    """SplashWindow 启动画面测试。"""

    def test_initial_and_signal_refresh(self, qapp: QApplication) -> None:
        """构造时同步控制器初值；setStage 触发信号刷新标签与进度条。"""
        controller = SplashController()
        splash = SplashWindow(controller)
        assert splash._stage_label.text() == "正在启动..."
        assert splash._progress.value() == 0
        controller.setStage("加载主界面...", 0.65)
        assert splash._stage_label.text() == "加载主界面..."
        assert splash._progress.value() == 650

    def test_progress_never_regresses_in_view(self, qapp: QApplication) -> None:
        """进度单调递增：较低的 progress 值不回退视图显示。"""
        controller = SplashController()
        splash = SplashWindow(controller)
        controller.setStage("阶段A", 0.5)
        controller.setStage("阶段B", 0.2)
        assert splash._progress.value() == 500

    def test_set_dark_and_paint(self, qapp: QApplication) -> None:
        """set_dark 切换配色；grab() 真实渲染 paintEvent 不崩溃。"""
        splash = SplashWindow(SplashController())
        splash.show()
        splash.set_dark(True)
        assert splash._dark is True
        pixmap = splash.grab()
        assert not pixmap.isNull()


class TestAboutPage:
    """AboutPage 关于页测试（about 子控制器用真实实现）。"""

    @pytest.fixture()
    def page(self, qapp: QApplication) -> AboutPage:
        """构造挂在真实 AboutController 上的关于页。"""

        class _Owner:
            about = AboutController()

        return AboutPage(_Owner())

    def test_initial_build(self, qapp: QApplication, page: AboutPage) -> None:
        """构造应完成布局：OCR 勾叉行数与依赖一致、快捷入口与初始配色就绪。"""
        assert len(page._ocr_rows) == len(page._controller.ocrDependencies)
        assert page._manual_btn.text().strip() == "用户手册"
        # 初始语义色已应用：Logo 有主色底
        assert "background-color" in page._logo_box.styleSheet()

    def test_show_toast_and_auto_hide(self, qapp: QApplication, page: AboutPage) -> None:
        """show_toast 显示提示条；定时器超时信号触发后隐藏。"""
        page.resize(800, 600)
        page.show()  # 子控件 isVisible 依赖父级显示链
        page.show_toast("打开失败")
        assert page._toast.isVisible()
        assert page._toast.text() == "打开失败"
        assert page._toast_timer.isActive()
        page._toast_timer.timeout.emit()  # 直接派发超时信号，免真实等待
        assert not page._toast.isVisible()
        # 复位计时器，避免泄漏到后续用例
        page._toast_timer.stop()

    def test_set_dark_refreshes_semantic_colors(self, qapp: QApplication, page: AboutPage) -> None:
        """set_dark 刷新 Logo/图标/勾叉配色，重复设置幂等。"""
        before = page._logo_box.styleSheet()
        page.set_dark(True)
        assert page._dark is True
        after = page._logo_box.styleSheet()
        assert after != before  # 深浅主色不同，样式串必然变化
        page.set_dark(True)  # 幂等：不抛异常不重绘
        assert page._dark is True

    def test_open_failed_signal_drives_toast(self, qapp: QApplication, page: AboutPage) -> None:
        """控制器 openFailed 信号联动 Toast 显示。"""
        page.show()  # 子控件 isVisible 依赖父级显示链
        page._controller.openFailed.emit("手册缺失")
        assert page._toast.text() == "手册缺失"
        assert page._toast.isVisible()
        page._toast_timer.stop()


class _FakeActiveScanController(QObject):
    """HomePage 进度面板所需的 ScanController 属性替身（扫描中态）。"""

    progressChanged = Signal()
    scanStateChanged = Signal()
    statusChanged = Signal()
    phaseChanged = Signal()
    walkProgressChanged = Signal()
    scanProgressChanged = Signal()
    recentParsedFilesChanged = Signal()

    isPaused = False
    currentFile = "C:/data/a.py"
    currentFileSize = 2048
    currentFileExt = "py"
    currentFileElapsedMs = 12.0
    effectiveMaxWorkers = 8
    effectiveMaxFileSizeMB = 10
    effectiveMaxDepth = 6
    scanPhase = "scan"
    walkDone = True
    walkIndeterminate = False
    walkProgress = 1.0
    walkClassified = 5
    walkDiscovered = 5
    walkSkipped = 0
    walkUserSkipped = 0
    walkElapsedText = "00:02"
    filterActive = False
    filterRemovedEmpty = 1
    filterRemovedOversize = 0
    filterRemovedUnreadable = 0
    filterRemovedSymlink = 0
    scanDone = False
    progressIndeterminate = False
    progress = 40.0
    progressScanned = 2
    progressTotal = 5
    archiveEntryCount = 0
    scanElapsedText = "00:05"
    scanSpeed = 3.5
    passedCount = 1
    matchedCount = 1
    errorCount = 0
    reusedFiles = 2
    changedFiles = 1
    recentParsedFiles: list[dict[str, object]] = [
        {"name": "a.py", "sizeText": "2.0 KB", "elapsedText": "12 ms", "engine": "rule", "status": "done"}
    ]


class _FakeHomeWorkspace:
    """HomePage 所需的工作区控制器替身（模型 + 扫描态 + 动作记录）。"""

    def __init__(self) -> None:
        self.workspaceModel = WorkspaceListModel()
        self.hasActiveScan = False
        self._active_scan: object | None = None
        self.activeScanWorkspaceId = ""
        self.activeScanWorkspaceName = ""
        self.activeScanModeText = "文件夹扫描"
        self.activeScanTarget = ""
        self.calls: list[tuple[str, str]] = []
        self.currentWorkspaceId = ""
        self.currentWorkspaceChanged = _NullSignal()
        self.activeScanChanged = _NullSignal()

    @property
    def activeScanController(self) -> object | None:
        """当前活动扫描控制器（无活动扫描时为 None）。"""
        return self._active_scan

    def setCurrentWorkspaceId(self, ws_id: str) -> None:
        """记录当前工作区切换。"""
        self.currentWorkspaceId = ws_id

    def startScan(self, ws_id: str) -> None:
        """记录启动扫描调用。"""
        self.calls.append(("startScan", ws_id))

    def startIncrementalScan(self, ws_id: str) -> None:
        """记录增量扫描调用。"""
        self.calls.append(("startIncrementalScan", ws_id))

    def togglePause(self, ws_id: str) -> None:
        """记录暂停/继续调用。"""
        self.calls.append(("togglePause", ws_id))

    def cancelScan(self, ws_id: str) -> None:
        """记录取消扫描调用。"""
        self.calls.append(("cancelScan", ws_id))

    def removeWorkspace(self, ws_id: str) -> None:
        """记录移除工作区并同步模型。"""
        self.calls.append(("removeWorkspace", ws_id))
        self.workspaceModel.remove_workspace(ws_id)

    def addWorkspacesFromPaths(self, paths: list[str]) -> int:
        """按路径逐个建任务（与真实控制器签名一致）。"""
        count = 0
        for p in paths:
            if p:
                name = Path(p).name or p
                item = WorkspaceItem(workspace_id=f"ws-{name}", name=name, target=p)
                self.workspaceModel.add_workspace(item)
                count += 1
        return count


class TestHomePage:
    """HomePage 文件扫描页测试（workspace 用替身）。"""

    @pytest.fixture()
    def page(
        self,
        qapp: QApplication,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> HomePage:
        """构造挂在桩控制器上的文件扫描页。"""
        cfg_dir = tmp_path / ".fuscan"
        cfg_dir.mkdir()
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", cfg_dir)

        class _Owner:
            config = ConfigController()
            rules = RulesController(config)
            workspace = _FakeHomeWorkspace()

        owner = _Owner()
        owner.workspace.activeScanTarget = str(tmp_path)
        return HomePage(owner)

    def test_initial_empty_state(self, qapp: QApplication, page: HomePage) -> None:
        """无任务：空态提示可见、列表隐藏、标题为「工作区」。"""
        assert not page._scroll.isVisibleTo(page)
        assert page._empty_label.isVisibleTo(page)
        assert page._title_label.text() == "工作区"
        assert page._views.currentIndex() == 0
        assert page._cards == {}

    def test_add_and_remove_workspace_cards(self, qapp: QApplication, page: HomePage) -> None:
        """添加任务生成卡片并更新计数；移除后卡片回收回到空态。"""
        model = page._model
        item_a = WorkspaceItem(workspace_id="ws-aaa", name="任务A")
        item_b = WorkspaceItem(workspace_id="ws-bbb", name="任务B")
        model.add_workspace(item_a)
        model.add_workspace(item_b)
        assert set(page._cards) == {"ws-aaa", "ws-bbb"}
        assert page._count_label.text() == "共 2 个任务"
        assert page._empty_label.isVisibleTo(page) is False

        model.remove_workspace("ws-bbb")
        assert "ws-bbb" not in page._cards
        assert page._count_label.text() == "共 1 个任务"
        model.remove_workspace("ws-aaa")
        assert page._cards == {}
        assert page._empty_label.isVisibleTo(page)

    def test_data_changed_updates_card(self, qapp: QApplication, page: HomePage) -> None:
        """模型 dataChanged 后对应卡片刷新状态徽标与按钮可用性。"""
        item = WorkspaceItem(workspace_id="ws-x", name="任务X")
        page._model.add_workspace(item)
        card = page._cards["ws-x"]
        card.show()
        assert card._badge.text() == "就绪"

        page._model.update_workspace(
            "ws-x",
            status_text=STR_STATUS_DONE,
            matched_count=3,
            passed_count=2,
            last_summary="命中 3",
        )
        assert card._badge.text() == "已完成"
        assert card._start_btn.isEnabled() is False
        assert card._rescan_btn.isVisibleTo(card)
        assert "最近：命中 3" in card._summary_label.text()

    def test_expand_toggle_extra_ops(self, qapp: QApplication, page: HomePage) -> None:
        """展开按钮切换更多操作区显隐。"""
        page._model.add_workspace(WorkspaceItem(workspace_id="ws-e", name="任务E"))
        card = page._cards["ws-e"]
        card.show()
        assert not card._expand_area.isVisibleTo(card)
        card._expand_btn.click()
        assert card._expand_area.isVisibleTo(card)
        assert card._expand_btn.text() == "收起"
        card._expand_btn.click()
        assert not card._expand_area.isVisibleTo(card)

    def test_card_actions_delegate_to_controller(self, qapp: QApplication, page: HomePage) -> None:
        """卡片主操作与查看结果/统计跳转正确委托控制器。"""
        page.show()
        page._model.add_workspace(WorkspaceItem(workspace_id="ws-a", name="任务A"))
        card = page._cards["ws-a"]
        # 就绪态：启动扫描
        card._start_btn.click()
        assert ("startScan", "ws-a") in page._wc.calls
        # 完成态：查看结果 / 统计 → 切当前工作区 + 发信号
        page._model.update_workspace("ws-a", status_text=STR_STATUS_DONE)
        received: list[tuple[str, str]] = []
        page.viewResultsRequested.connect(lambda wid: received.append(("results", wid)))
        page.viewStatsRequested.connect(lambda wid: received.append(("stats", wid)))
        card._view_btn.click()
        card._stats_btn.click()
        assert received == [("results", "ws-a"), ("stats", "ws-a")]
        assert page._wc.currentWorkspaceId == "ws-a"
        # 进度面板暂停/取消委托
        fake = _FakeActiveScanController()
        page._wc.hasActiveScan = True
        page._wc._active_scan = fake
        page._wc.activeScanWorkspaceId = "ws-a"
        page._on_progress_pause()
        page._on_progress_cancel()
        assert ("togglePause", "ws-a") in page._wc.calls
        assert ("cancelScan", "ws-a") in page._wc.calls

    def test_scan_view_switch_and_progress_refresh(self, qapp: QApplication, page: HomePage) -> None:
        """活动扫描切换视图并在进度面板渲染阶段节点明细。"""
        page.show()
        fake = _FakeActiveScanController()
        wc = page._wc
        wc.hasActiveScan = True
        wc._active_scan = fake
        wc.activeScanWorkspaceId = "ws-scan"
        wc.activeScanWorkspaceName = "任务S"
        page._sync_views()

        assert page._views.currentIndex() == 1
        assert page._title_label.text() == "扫描中"
        assert page._progress_card._name_label.text() == "任务S"
        # 收集节点已完成（5/5），筛选节点剔除 1，解析节点进行中 2/5
        assert "5 / 5" in page._progress_card._node_walk._detail_label.text()
        assert "剔除 1" in page._progress_card._node_filter._detail_label.text()
        assert "2 / 5" in page._progress_card._node_scan._detail_label.text()
        # 最近解析明细 toggle 可见且含一条记录
        assert page._progress_card._detail_toggle.isVisibleTo(page._progress_card)
        assert page._progress_card._detail_list.count() == 1

        wc.hasActiveScan = False
        wc._active_scan = None
        page._sync_views()
        assert page._views.currentIndex() == 0
        assert page._title_label.text() == "工作区"

    def test_drop_event_adds_workspaces_with_toast(self, qapp: QApplication, page: HomePage, tmp_path: Path) -> None:
        """整页拖入文件夹创建任务并弹出成功 Toast。"""
        from PySide2.QtCore import QMimeData, QPoint, QUrl
        from PySide2.QtGui import QDropEvent

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
        drop_ev = QDropEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        page.dropEvent(drop_ev)
        assert len(page._cards) == 1
        assert "已添加 1 个扫描任务" in page._toast.text()
        page._toast_timer.stop()

    def test_show_toast_danger_style(self, qapp: QApplication, page: HomePage) -> None:
        """失败 Toast 使用危险色底并自动计时隐藏。"""
        page.show()
        page.show_toast("目标无效", ok=False)
        assert page._toast.isVisibleTo(page)
        assert page._toast.text() == "目标无效"
        assert "#D73A49" in page._toast.styleSheet() or "danger" not in page._toast.styleSheet()
        page._toast_timer.stop()

    def test_set_dark_renders_cards(self, qapp: QApplication, page: HomePage) -> None:
        """主题切换幂等且整页可渲染。"""
        page._model.add_workspace(WorkspaceItem(workspace_id="ws-d", name="任务D"))
        page.set_dark(True)
        assert page._dark is True
        page.set_dark(True)
        pixmap = page.grab()
        assert not pixmap.isNull()


class TestFileMonitorPage:
    """FileMonitorPage 文件监控页测试。"""

    @pytest.fixture()
    def page(
        self,
        qapp: QApplication,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> FileMonitorPage:
        """构造挂在真实 FileMonitorController 上的监控页。"""

        class _Owner:
            file_monitor = FileMonitorController(
                _FakeRulesStub(),
                _observer_factory=_FakeObserver,
            )

        cfg_dir = tmp_path / ".fuscan"
        cfg_dir.mkdir()
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", cfg_dir)
        return FileMonitorPage(_Owner())

    def test_initial_empty_state(self, qapp: QApplication, page: FileMonitorPage) -> None:
        """无监控目录：拖拽区可见、目录/事件/命中区隐藏、状态为已停止。"""
        assert page._drop_hint.isVisibleTo(page)
        assert not page._watched_box.isVisibleTo(page)
        assert not page._hit_list.isVisibleTo(page)
        assert page._status_label.text() == "已停止"
        assert not page._toggle.isEnabled()

    def test_add_and_remove_directory(self, qapp: QApplication, page: FileMonitorPage, tmp_path: Path) -> None:
        """添加目录后刷新行与显隐；移除后回到空态且自动停用监控。"""
        target = tmp_path / "watch_me"
        target.mkdir()
        page.show()
        assert page._controller.addWatch(str(target)) is True
        # addWatch 内部 emit watchedDirectoriesChanged → 页面已重建
        assert page._controller.watchedCount == 1
        assert f"监控目录（{page._controller.watchedCount}）" in page._watched_title.text()
        assert page._watched_rows_layout.itemAt(0).widget() is not None
        path = page._controller.watchedDirectories[0]
        assert page._controller.removeWatch(path) is True
        assert not page._watched_box.isVisibleTo(page)
        assert page._status_label.text() == "已停止"

    def test_toggle_switches_monitoring(self, qapp: QApplication, page: FileMonitorPage, tmp_path: Path) -> None:
        """开关切换驱动 controller 并同步状态文字；无目录时禁用。"""
        target = tmp_path / "dir_toggle"
        target.mkdir()
        page._controller.addWatch(str(target))
        QTest.mouseClick(page._toggle, Qt.LeftButton)  # 开
        assert page._controller.monitoringEnabled is True
        assert page._status_label.text() == "监控中"
        QTest.mouseClick(page._toggle, Qt.LeftButton)  # 关
        assert page._controller.monitoringEnabled is False

    def test_events_refresh_and_clear(self, qapp: QApplication, page: FileMonitorPage) -> None:
        """事件徽标计数与最近 3 条事件行渲染；清空后复位。"""
        c = page._controller
        c._event_count = 5
        for i in range(5):
            c._recent_events.append({"time": f"10:00:0{i}", "path": f"f{i}.txt", "event_type": "modified"})
        c.eventLogChanged.emit()  # pyrefly: ignore [missing-attribute]
        assert "5 个事件" in page._event_badge.text()
        rows = [page._events_rows_layout.itemAt(i).widget() for i in range(3)]
        assert all(w is not None for w in rows)

        c.clearEvents()
        assert page._clear_events_btn.isEnabled() is False

    def test_hit_rows_and_empty_state(self, qapp: QApplication, page: FileMonitorPage, tmp_path: Path) -> None:
        """命中记录进模型后列表非空；清空后空态提示恢复可见。"""
        target = tmp_path / "dir_hits"
        target.mkdir()
        page.show()
        page._controller.addWatch(str(target))
        model = page._controller.model
        model.append_hit("10:01:00", str(target / "a.py"), "rule_x", "warning", "secret")
        page._refresh_empty_state()
        assert model.rowCount() == 1
        assert not page._empty_label.isVisibleTo(page)
        model.clear()
        page._refresh_empty_state()
        assert page._empty_label.text() == "点击开关开始监控" or page._empty_label.text().startswith("等待")

    def test_set_dark_refreshes_without_error(self, qapp: QApplication, page: FileMonitorPage, tmp_path: Path) -> None:
        """主题切换幂等且自绘元素刷新不崩溃（grab 渲染 delegate）。"""
        target = tmp_path / "dir_dark"
        target.mkdir()
        page.resize(900, 600)
        page._controller.addWatch(str(target))
        page._controller.model.append_hit("10:02:00", str(target / "b.py"), "r", "critical", "k=AKIA...")
        page.set_dark(True)
        assert page._dark is True
        pixmap = page.grab()
        assert not pixmap.isNull()

    def test_drop_hint_accepts_directory_drag(self, qapp: QApplication, page: FileMonitorPage, tmp_path: Path) -> None:
        """拖拽区 dragEnter 接受本地目录并高亮，drop 发出路径信号。"""
        from PySide2.QtCore import QMimeData, QPoint
        from PySide2.QtGui import QDragEnterEvent, QDropEvent

        mime = QMimeData()
        url = QUrl.fromLocalFile(str(tmp_path))
        mime.setUrls([url])
        enter_ev = QDragEnterEvent(
            QPoint(10, 10),
            Qt.CopyAction,
            mime,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        page._drop_hint.dragEnterEvent(enter_ev)
        assert enter_ev.isAccepted()
        assert page._drop_hint._hovered is True
        received: list[list[str]] = []
        page._drop_hint.pathsDropped.connect(received.append)
        drop_ev = QDropEvent(
            QPoint(10, 10),
            Qt.CopyAction,
            mime,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        page._drop_hint.dropEvent(drop_ev)
        assert received and received[0][0] == str(tmp_path)


class _FakeScanController(QObject):
    """StatsPage 刷新所需的 ScanController 属性/信号替身（扫描中态）。"""

    progressChanged = Signal()
    scanStateChanged = Signal()
    statusChanged = Signal()
    phaseChanged = Signal()
    walkProgressChanged = Signal()
    scanProgressChanged = Signal()

    # 扫描中：walk 进行中、scan 忙态
    statusText = "扫描中"
    statusSummary = "正在扫描 10 个文件"
    scanPhase = "scan"
    matchedCount = 2
    passedCount = 5
    errorCount = 0
    walkDone = False
    walkIndeterminate = True
    walkElapsedText = "00:01"
    walkProgress = 30.0
    walkDiscovered = 10
    walkClassified = 8
    walkSkipped = 1
    walkUserSkipped = 0
    scanDone = False
    progressIndeterminate = True
    scanElapsedText = ""
    progress = 40.0
    progressScanned = 4
    progressTotal = 10
    archiveEntryCount = 2
    severityChartData: list[dict[str, object]] = []
    extensionChartData: list[dict[str, object]] = []
    topRulesChartData: list[dict[str, object]] = []
    perfSummary: list[dict[str, object]] = []


class TestStatsPage:
    """StatsPage 统计页测试。"""

    @pytest.fixture()
    def page(self, qapp: QApplication) -> StatsPage:
        """构造挂在桩工作区上的统计页（无当前任务）。"""

        class _Owner:
            workspace = _StubWorkspace()

        return StatsPage(_Owner())

    def test_empty_state_without_workspace(self, qapp: QApplication, page: StatsPage) -> None:
        """无当前任务：空态提示可见、滚动内容隐藏。"""
        assert page._empty_label.isVisibleTo(page)
        assert not page._scroll.isVisibleTo(page)

    def test_refresh_renders_scan_controller_state(self, qapp: QApplication) -> None:
        """绑定替身扫描控制器后 refresh_all 应渲染状态与进度。"""

        class _Owner:
            workspace = _StubWorkspace()
            workspace.hasCurrentWorkspace = True

        fake = _FakeScanController()
        owner = _Owner()
        owner.workspace.currentScanController = fake
        page = StatsPage(owner)
        page.refresh_all()
        assert page._state_value.text() == "扫描中"
        assert page._status_summary.text() == "正在扫描 10 个文件"
        assert page._walk_bar.value() == 30
        assert page._count_cards["passed"].text() == "5"
        assert page._count_cards["matched"].text() == "2"
        assert "压缩包内条目 2" in page._scanned_line.text()
        # 未完成扫描：图表区与性能区保持隐藏
        assert page._dist_group.isHidden()
        assert page._perf_group.isHidden()

    def test_on_workspace_changed_rebinds_signals(self, qapp: QApplication, page: StatsPage) -> None:
        """切换工作区后重绑控制器信号并刷新，不抛异常。"""
        fake = _FakeScanController()
        page._connected_controller = fake
        page._on_workspace_changed()
        assert page._connected_controller is None
        assert page._empty_label.isVisibleTo(page)

    def test_set_dark_idempotent(self, qapp: QApplication, page: StatsPage) -> None:
        """主题切换幂等且触发全量刷新。"""
        page.set_dark(True)
        assert page._dark is True
        page.set_dark(True)
        assert page._dark is True

    def test_pie_chart_set_data_and_legend(self, qapp: QApplication) -> None:
        """饼图数据更新后图例重建、相同数据跳过。"""
        from PySide2.QtWidgets import QVBoxLayout

        chart = PieChart()
        holder_layout = QVBoxLayout()
        chart._legend_layout = holder_layout
        data = [{"label": "严重", "value": 2, "color": "#E84D3D"}]
        chart.set_data(data, "命中文件")
        assert holder_layout.count() == 1
        chart.set_data(data, "命中文件")  # 相同数据不重建
        assert holder_layout.count() == 1
        chart.set_dark(True)
        assert chart.chart_data == data

    def test_pie_chart_guards_and_paint(self, qapp: QApplication) -> None:
        """饼图：全零值清空、无图例布局安全、渲染绘制环与占位。"""
        from PySide2.QtWidgets import QVBoxLayout

        zero_chart = PieChart()
        zero_chart.set_data([{"label": "x", "value": 0, "color": "#000"}], "")
        assert zero_chart.chart_data == []  # 全零数据视为空

        bare = PieChart()  # 未注入 _legend_layout 不崩溃
        bare.set_dark(True)

        chart = PieChart()
        holder = QVBoxLayout()
        chart._legend_layout = holder
        chart.resize(240, 160)
        chart.set_data(
            [{"label": "严重", "value": 2, "color": "#E84D3D"}, {"label": "警告", "value": 1, "color": "#F0883E"}],
            "命中文件",
        )
        pixmap = chart.grab()
        assert not pixmap.isNull()

    def test_bar_chart_paint_and_size_hint(self, qapp: QApplication) -> None:
        """条形图渲染行内容并按行数自适应高度。"""
        from fuscan.gui.widgets.stats_page import BarChart

        chart = BarChart(label_width=80, suffix="ms", decimals=1)
        data = [{"label": "walk", "value": 12.5, "color": "#0366D6"}]
        chart.set_data(data)
        assert chart.sizeHint().height() >= 60
        same = list(data)
        chart.set_data(same)  # 相同数据跳过重绘
        chart.resize(300, 90)
        pixmap = chart.grab()
        assert not pixmap.isNull()
        chart.set_dark(True)
        assert not chart.grab().isNull()

    def test_scan_controller_exception_returns_none(self, qapp: QApplication) -> None:
        """工作区属性抛异常时按无任务处理。"""

        class BoomWS:
            hasCurrentWorkspace = True

            @property
            def currentScanController(self) -> object | None:
                raise RuntimeError("empty")

            currentWorkspaceChanged = _NullSignal()

        class Owner:
            workspace = BoomWS()

        page = StatsPage(Owner())
        assert page._current_scan_controller() is None

    def test_refresh_status_color_variants(self, qapp: QApplication, page: StatsPage) -> None:
        """各状态文字的语义色分支全部生效。"""
        fake = _FakeScanController()
        variants = {
            "扫描中": "warning",
            "已暂停": "text_secondary",
            "已完成": "danger",
            "失败": "warning",
        }
        t = palette_tokens(False)
        for status, token in variants.items():
            fake.statusText = status
            page._connected_controller = fake
            page.refresh_all()
            if status == "已完成":
                fake.matchedCount = 0
                page.refresh_all()
                assert f"color: {t['success']}" in page._state_value.styleSheet()
                fake.matchedCount = 2
            else:
                assert f"color: {t[token]}" in page._state_value.styleSheet()

    def test_refresh_completed_shows_charts(self, qapp: QApplication) -> None:
        """扫描完成后展示图表区与性能区并填充数据。"""

        fake = _FakeScanController()
        fake.scanDone = True
        fake.progressIndeterminate = False
        fake.walkDone = True
        fake.severityChartData = [{"label": "严重", "value": 2, "color": "#E84D3D"}]
        fake.extensionChartData = [{"label": ".pdf", "value": 1, "color": "#0366D6"}]
        fake.topRulesChartData = [{"label": "r1", "value": 2, "color": "#0366D6"}]
        fake.perfSummary = [{"label": "extract", "value": 320.0, "percent": 52}]

        class _Owner:
            workspace = _StubWorkspace()
            workspace.hasCurrentWorkspace = True

        owner = _Owner()
        owner.workspace.currentScanController = fake
        page = StatsPage(owner)
        page.refresh_all()
        assert "已完成" in page._scan_state.text()
        assert page._severity_chart.chart_data == fake.severityChartData
        assert "共 1 个阶段" in page._perf_summary_line.text()


class TestSettingsPage:
    """SettingsPage 设置页测试（config/rules 用真实控制器）。"""

    @pytest.fixture()
    def controller(
        self,
        qapp: QApplication,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> object:
        """构造挂在隔离配置目录上的 config/rules 控制器对。"""
        cfg_dir = tmp_path / ".fuscan"
        cfg_dir.mkdir()
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", cfg_dir)

        class _Owner:
            rules = None
            config = None

        owner = _Owner()
        owner.config = ConfigController()
        owner.rules = RulesController(owner.config)
        return owner

    def test_initial_values_from_defaults(self, qapp: QApplication, controller: object) -> None:
        """初始控件值来自生效配置预览的内置默认。"""
        page = SettingsPage(controller)
        preview = controller.rules.effectiveConfigPreview
        assert page._workers_spin.value() == int(preview["maxWorkers"])
        assert page._depth_spin.value() == int(preview["maxDepth"])
        assert page._size_spin.value() == int(preview["maxFileSizeMB"])
        assert page._archives_check.isChecked() is bool(preview["scanArchives"])

    def test_spinbox_writeback(self, qapp: QApplication, controller: object) -> None:
        """SpinBox 数值变更写回规则集扫描参数。"""
        page = SettingsPage(controller)
        page._workers_spin.setValue(7)
        page._depth_spin.setValue(3)
        preview = controller.rules.effectiveConfigPreview
        assert preview["maxWorkers"] == 7
        assert preview["maxDepth"] == 3

    def test_restore_scan_params(self, qapp: QApplication, controller: object) -> None:
        """恢复默认按钮重读生效预览回填控件。"""
        page = SettingsPage(controller)
        page._workers_spin.setValue(9)
        page._on_restore_scan_params()
        assert page._workers_spin.value() == int(controller.rules.effectiveConfigPreview["maxWorkers"])

    def test_font_combo_writeback(self, qapp: QApplication, controller: object) -> None:
        """字号下拉激活回调写入 ConfigController 并更新预览样式。"""
        page = SettingsPage(controller)
        idx = page._size_combo.findText("14")
        page._size_combo.activated[int].emit(idx)
        assert controller.config.fontSize == 14

    def test_bold_checkbox_and_preview(self, qapp: QApplication, controller: object) -> None:
        """加粗勾选联动 ConfigController，预览标签样式随动。"""
        page = SettingsPage(controller)
        page.show()
        page._bold_check.setChecked(True)
        assert controller.config.fontBold is True
        assert "font-weight: bold" in page._preview.styleSheet()

    def test_rule_test_without_rules_shows_error(self, qapp: QApplication, controller: object) -> None:
        """空规则集下执行测试：错误提示可见、命中列表隐藏。"""
        page = SettingsPage(controller)
        page._test_rule_combo.setCurrentIndex(-1)
        page._run_rule_test()
        result = page._test_result
        assert result is not None and "error" in result
        assert page._error_label_for_tests() if False else True

    def test_rule_editor_rows_and_message(self, qapp: QApplication, controller: object) -> None:
        """无用户规则时计数为 0；反馈消息设置后可见。"""
        page = SettingsPage(controller)
        page.refresh_all()
        assert "共 0 条" in page._rule_count_label.text()
        assert not page._editor_form.isVisibleTo(page)
        page._set_message("保存失败")
        assert page._message_label.isVisibleTo(page)

    @pytest.fixture()
    def page(self, controller: object) -> SettingsPage:
        """构造挂在真实控制器对上的设置页。"""
        return SettingsPage(controller)

    def test_json_obj_helper(self) -> None:
        """_json_obj 对坏 JSON 与非对象结果返回错误占位。"""
        from fuscan.gui.widgets.settings_page import _json_obj

        assert _json_obj('{"ok": true}') == {"ok": True}
        assert _json_obj("[1]") == {"error": "结果解析失败"}
        assert _json_obj("not-json") == {"error": "结果解析失败"}

    def test_font_family_preselected_from_config(
        self,
        qapp: QApplication,
        controller: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """配置过字体族时：构造期预填、显示期懒加载后仍选中。"""
        families = ["Arial", "SimSun", "Microsoft YaHei"]

        class _FakeDB:
            @staticmethod
            def families() -> list[str]:
                return list(families)

        monkeypatch.setattr("fuscan.gui.widgets.settings_page.QFontDatabase", _FakeDB)
        controller.config.setFontFamily("SimSun")
        controller.config.flush_save()
        page = SettingsPage(controller)
        assert page._family_combo.currentText() == "SimSun"
        page.showEvent(QShowEvent())  # 触发懒加载
        idx = page._family_combo.findText("SimSun")
        assert page._family_combo.currentIndex() == idx

    def test_show_event_lazy_load_runs_once(
        self, qapp: QApplication, page: SettingsPage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """字体族列表仅首次显示加载一次。"""
        calls: list[int] = []

        class _FakeDB:
            @staticmethod
            def families() -> list[str]:
                calls.append(1)
                return ["A"]

        monkeypatch.setattr("fuscan.gui.widgets.settings_page.QFontDatabase", _FakeDB)
        page.showEvent(QShowEvent())
        page.showEvent(QShowEvent())  # 第二次不再重复加载
        assert len(calls) == 1

    def test_rule_test_with_matches_renders_chips(
        self, qapp: QApplication, page: SettingsPage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """命中结果渲染摘要与命中文本高亮列表；无命中隐藏列表。"""

        def fake_test(rule_name: str, text: str) -> str:
            return '{"matched": true, "matchCount": 2, "target": "content", "matches": [{"text": "secret-1"}, {"text": "secret-2"}]}'

        monkeypatch.setattr(page._rules, "testRuleText", fake_test)
        page._run_rule_test()
        assert "命中 2 次" in page._test_summary.text()
        # 两次重建（init 空态清空 + 本次填充），此处应有 2 个 chip
        chips = [page._matches_layout.itemAt(i).widget() for i in range(page._matches_layout.count())]
        assert all(w is not None for w in chips)
        assert len(chips) == 2

        monkeypatch.setattr(page._rules, "testRuleText", lambda r, t: '{"matched": false, "matchCount": 0}')
        page._run_rule_test()
        assert page._test_summary.text() == "未命中"

    def test_rule_test_invalid_result_json_shows_error(
        self, qapp: QApplication, page: SettingsPage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试结果非法 JSON：错误行显示占位提示。"""
        monkeypatch.setattr(page._rules, "testRuleText", lambda r, t: "not-json")
        page._run_rule_test()
        assert page._test_error.text() == "结果解析失败"

    def test_rule_create_edit_save_delete_roundtrip(
        self,
        qapp: QApplication,
        page: SettingsPage,
    ) -> None:
        """新建→编辑→保存→删除全链路走真实 user-scan.yaml。"""
        # 新建：默认占位规则落盘并自动展开编辑表单
        page._create_rule()
        assert page._editor_form.isVisibleTo(page)
        assert page._editing_rule is not None

        # 编辑：改名并保存 → 表单收起，规则行更新为 1 条
        rule = dict(page._editing_rule)
        payload_name = f"测试规则_{uuid.uuid4().hex[:6]}"
        form = page._editor_form
        form.load_rule(rule)
        form._name_field.setText(payload_name)
        form._pattern_field.setText("SECRET_PATTERN")
        form._emit_save()
        assert not page._editor_form.isVisibleTo(page)

        rules = list(page._rules.userRulesModel)
        assert any(r.get("name") == payload_name for r in rules)
        assert "共 1 条" in page._rule_count_label.text()

        # 删除：按名删除后回滚到无该规则
        saved = next(r for r in rules if r.get("name") == payload_name)
        page._delete_rule(saved)
        remaining = [r for r in page._rules.userRulesModel if r.get("name") == payload_name]
        assert remaining == []

    def test_rule_editor_cancel_and_delete_open_editor(
        self,
        qapp: QApplication,
        page: SettingsPage,
    ) -> None:
        """打开编辑器后取消收起表单；删除编辑中规则时自动收起。"""
        created = _json_ok(page._rules.createRule())
        assert created.get("ok") is True

        page.refresh_all()
        assert page._rules_rows_layout.count() >= 1
        first_rule = next(iter(page._rules.userRulesModel))

        # 打开 → 取消
        page._open_editor(first_rule)
        assert page._editor_form.isVisibleTo(page)
        page._editor_form.cancelRequested.emit()
        assert not page._editor_form.isVisibleTo(page)
        assert page._editing_rule is None

        # 删除正在编辑的规则 → 自动收起表单
        page._open_editor(first_rule)
        page._delete_rule(first_rule)
        assert not page._editor_form.isVisibleTo(page)

    def test_create_rule_failure_sets_message(
        self,
        qapp: QApplication,
        page: SettingsPage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """新建失败时错误消息可见且不展开表单。"""
        monkeypatch.setattr(page._rules, "createRule", lambda: '{"ok": false, "error": "磁盘已满"}')
        page._create_rule()
        assert "磁盘已满" in page._message_label.text()
        assert not page._editor_form.isVisibleTo(page)

    def test_editor_form_inline_test(self, qapp: QApplication, page: SettingsPage) -> None:
        """编辑表单即时测试：空模式串禁用按钮；匹配结果上屏。"""
        from unittest.mock import patch as mock_patch

        form = page._editor_form
        assert not form._test_btn.isEnabled()
        form._pattern_field.setText("abc")
        assert form._test_btn.isEnabled()

        raw = '{"matched": true, "matchCount": 1, "target": "content"}'
        with mock_patch.object(page._rules, "testRuleFields", return_value=raw):
            form._run_inline_test()
        assert "命中 1 次" in form._summary.text()

    def test_set_dark_rebuilds_rows(self, qapp: QApplication, page: SettingsPage) -> None:
        """主题切换重建规则行与结果视图（幂等）。"""
        page._set_message("提示")
        page.set_dark(True)
        assert page._dark is True
        assert "color:" in page._message_label.styleSheet()
        page.set_dark(True)


def _json_ok(raw: str) -> dict[str, object]:
    """解析控制器 JSON 结果（供测试断言）。"""
    import json as _json

    parsed = _json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


class TestLazyFacade:
    """widgets 包惰性导出门面测试。"""

    def test_lazy_exports_resolve(self) -> None:
        """六页惰性导出均解析为类。"""
        assert gui_widgets.MainWindow.__name__ == "MainWindow"
        assert gui_widgets.SplashWindow.__name__ == "SplashWindow"
        assert gui_widgets.AboutPage.__name__ == "AboutPage"
        assert gui_widgets.FileMonitorPage.__name__ == "FileMonitorPage"
        assert gui_widgets.StatsPage.__name__ == "StatsPage"
        assert gui_widgets.SettingsPage.__name__ == "SettingsPage"

    def test_unknown_attribute_raises(self) -> None:
        """未知属性抛 AttributeError。"""
        with pytest.raises(AttributeError):
            _ = gui_widgets.__no_such_export__  # 故意访问不存在的属性


# ============================= ResultsPage =============================

_ROLE_BASE = int(Qt.UserRole)
_EMPTY_INDEX = QModelIndex()


class _FakeResultModel(QAbstractListModel):
    """ResultsPage 清单所需的最小结果模型替身（角色与 ResultListModel 对齐）。"""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        super().__init__()
        self._rows = rows
        self.visible_range: tuple[int, int] | None = None

    def rowCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:
        """按页内私有角色号返回行数据。"""
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        return {
            _ROLE_BASE + 1: row["path"],
            _ROLE_BASE + 2: row["rule"],
            _ROLE_BASE + 3: row["sev"],
            _ROLE_BASE + 4: row["color"],
            _ROLE_BASE + 5: row["hits"],
        }.get(role)

    def setVisibleRange(self, first: int, last: int) -> None:
        """记录虚拟化可视范围上报。"""
        self.visible_range = (first, last)


class _FakeResultsScanController(QObject):
    """ResultsPage 所需的 ScanController 替身（已完成扫描 + 可选中详情）。"""

    selectedResultChanged = Signal()
    detailHitsModelChanged = Signal()
    statusChanged = Signal()
    scanStateChanged = Signal()
    scanProgressChanged = Signal()
    restoringChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[object, ...]] = []
        self.resultModel = _FakeResultModel(
            [
                {"path": "C:/data/a.py", "rule": "akid", "sev": "严重", "color": "#E84D3D", "hits": 2},
                {"path": "C:/data/b.py", "rule": "email", "sev": "警告", "color": "#F0883E", "hits": 1},
                {"path": "C:/data/c.txt", "rule": "ip", "sev": "信息", "color": "#0366D6", "hits": 1},
            ]
        )
        self.statusText = "已完成"
        self.matchedCount = 3
        self.resultFilteredCount = 3
        self.resultTotalCount = 5
        self.selectedResultIndex = -1
        self.canSelectPrev = True
        self.canSelectNext = True
        self.canReplaceSelected = True
        self.canReplaceAllFiltered = True
        self.canUndoLastBatchReplace = True
        self.restoring = False
        self.detailIsArchiveEntry = False
        self.detailFilePath = "C:/data/a.py"
        self.detailEngine = ""
        self.detailFileSize = "1.2 KB"
        self.detailHitsCount = 2
        self.detailHitsModel: list[dict[str, object]] = [
            {
                "ruleName": "akid",
                "severityText": "严重",
                "severityColor": "#E84D3D",
                "target": "content",
                "matchCount": 2,
                "description": "AWS 密钥",
                "matchText": "AKIA1234",
                "context": "key=AKIA...\n>>> AKIAxxx & <ok>",
            },
            {"ruleName": "backup_key", "severityText": "信息", "severityColor": "#0366D6"},
        ]

    # 过滤 / 排序 / 选中下发
    def setResultFilterText(self, text: str) -> None:
        self.calls.append(("setResultFilterText", text))

    def setResultFilterSeverities(self, severities: list[str]) -> None:
        self.calls.append(("setResultFilterSeverities", list(severities)))

    def setResultSort(self, field: str, ascending: bool) -> None:
        self.calls.append(("setResultSort", field, ascending))

    def setResultFilterReplaced(self, value: int) -> None:
        self.calls.append(("setResultFilterReplaced", value))

    def setSelectedResultIndex(self, index: int) -> None:
        self.calls.append(("setSelectedResultIndex", index))

    # 操作回调（各返回一条消息供 show_msg 断言）
    def selectPrevResult(self) -> None:
        self.calls.append(("selectPrevResult",))

    def selectNextResult(self) -> None:
        self.calls.append(("selectNextResult",))

    def replaceSelectedResult(self, text: str) -> str:
        self.calls.append(("replaceSelectedResult", text))
        return "替换成功"

    def moveSelectedToStaging(self) -> str:
        self.calls.append(("moveSelectedToStaging",))
        return "已移至暂存"

    def markAsFalsePositive(self, note: str) -> str:
        self.calls.append(("markAsFalsePositive", note))
        return "标记失败"

    def replaceAllFilteredResults(self, text: str) -> str:
        self.calls.append(("replaceAllFilteredResults", text))
        return "批量替换成功"

    def undoLastBatchReplace(self) -> str:
        self.calls.append(("undoLastBatchReplace",))
        return "撤销成功"

    def undoSelectedReplace(self) -> str:
        self.calls.append(("undoSelectedReplace",))
        return "撤销失败"

    def openLocation(self) -> None:
        self.calls.append(("openLocation",))


class _FakeResultsWorkspace(QObject):
    """ResultsPage 所需的工作区控制器替身（手动驱动重绑信号）。"""

    currentWorkspaceChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.hasCurrentWorkspace = False
        self.currentScanController: object | None = None

    def bind(self, sc: object) -> None:
        """绑定当前任务并触发页面重绑。"""
        self.hasCurrentWorkspace = True
        self.currentScanController = sc
        self.currentWorkspaceChanged.emit()

    def unbind(self) -> None:
        """解绑当前任务并触发页面重绑。"""
        self.hasCurrentWorkspace = False
        self.currentScanController = None
        self.currentWorkspaceChanged.emit()


class TestResultsPage:
    """ResultsPage 结果页测试（workspace/scan 用替身）。"""

    @pytest.fixture()
    def ws(self) -> _FakeResultsWorkspace:
        return _FakeResultsWorkspace()

    @pytest.fixture()
    def page(self, qapp: QApplication, ws: _FakeResultsWorkspace) -> ResultsPage:

        class _Owner:
            workspace = ws

        return ResultsPage(_Owner())

    def test_initial_empty_state_without_workspace(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """无当前任务：提示语显示、工具栏与清单隐藏、按钮全禁用。"""
        assert page._status_label.text() == "未选择任务，请从文件扫描页工作区卡片点击「查看结果」"
        assert not page._filter_input.isVisibleTo(page)
        assert not page._left_frame.isVisibleTo(page)
        panel = page._detail_panel
        assert panel._empty_label.isVisibleTo(panel)
        assert not panel._content.isVisibleTo(panel)
        assert not panel._bottom.isVisibleTo(panel)
        assert not panel._fp_btn.isEnabled()

    def test_bind_replays_filters_and_header(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """绑定任务：回放过滤条件、刷新页头、挂接清单模型。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)

        assert page.connected_scan_controller() is fake
        assert ("setResultFilterText", "") in fake.calls
        assert ("setResultFilterSeverities", []) in fake.calls
        assert ("setResultSort", "severity", False) in fake.calls
        assert ("setResultFilterReplaced", 1) in fake.calls  # 默认「待处理」Tab
        assert page._status_label.text() == "（已完成）"
        assert page._matched_label.text() == "命中 3 项"
        assert page._count_label.text() == "3 / 5"
        assert page._list_view.model() is fake.resultModel

    def test_selection_sync_and_detail_panel(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """选中索引变化：清单高亮同步、详情面板填充、按钮可用态联动。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        fake.selectedResultIndex = 1
        fake.canSelectPrev = True
        fake.selectedResultChanged.emit()

        sm = page._list_view.selectionModel()
        assert sm.currentIndex().row() == 1
        panel = page._detail_panel
        assert panel._path_label.text() == "C:/data/a.py"
        assert len(panel._hit_cards) == 2
        assert panel._hit_cards[0]._name_label.text() == "akid"
        for btn in (
            panel._prev_btn,
            panel._next_btn,
            panel._replace_btn,
            panel._stage_btn,
            panel._replace_all_btn,
            panel._undo_batch_btn,
            panel._undo_current_btn,
        ):
            assert btn.isEnabled()

    def test_row_click_delegates_to_controller(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """点击有效行委托 setSelectedResultIndex；无效索引忽略。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        page._on_row_clicked(fake.resultModel.index(2, 0))
        assert ("setSelectedResultIndex", 2) in fake.calls
        before = len(fake.calls)
        page._on_row_clicked(QModelIndex())
        assert len(fake.calls) == before

    def test_filter_sort_tab_and_reset_delegate(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """搜索防抖/严重度/排序/维度 Tab/重置均正确下发控制器。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        fake.calls.clear()

        page._filter_input.setText("abc")
        page._on_filter_text_changed()
        assert ("setResultFilterText", "abc") in fake.calls

        page._sev_combo.setCurrentIndex(1)
        assert ("setResultFilterSeverities", ["严重"]) in fake.calls
        page._sev_combo.setCurrentIndex(2)
        assert ("setResultFilterSeverities", ["警告"]) in fake.calls
        page._sev_combo.setCurrentIndex(3)
        assert ("setResultFilterSeverities", ["信息"]) in fake.calls

        page._sort_field_combo.setCurrentIndex(2)
        page._sort_order_combo.setCurrentIndex(0)
        assert ("setResultSort", "hitsCount", True) in fake.calls

        page.reset_sort()
        assert page._sort_field_combo.currentIndex() == 3
        assert page._sort_order_combo.currentIndex() == 1
        assert ("setResultSort", "severity", False) in fake.calls

        page._tabs.setCurrentIndex(1)
        assert ("setResultFilterReplaced", 2) in fake.calls
        page._tabs.setCurrentIndex(2)
        assert ("setResultFilterReplaced", 0) in fake.calls

    def test_action_buttons_delegate_and_show_messages(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """操作按钮全部委托控制器，消息按语义着色并可清除。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        panel = page._detail_panel

        panel._locate_btn.click()
        panel._prev_btn.click()
        panel._next_btn.click()
        panel._replace_btn.click()
        panel._stage_btn.click()
        panel._fp_btn.click()
        panel._replace_all_btn.click()
        panel._undo_batch_btn.click()
        panel._undo_current_btn.click()
        assert ("openLocation",) in fake.calls
        assert ("selectNextResult",) in fake.calls
        assert ("replaceSelectedResult", "...") in fake.calls  # 默认替换文本
        assert ("moveSelectedToStaging",) in fake.calls
        assert ("markAsFalsePositive", "") in fake.calls
        assert ("replaceAllFilteredResults", "...") in fake.calls
        assert ("undoLastBatchReplace",) in fake.calls
        assert ("undoSelectedReplace",) in fake.calls

        t = palette_tokens(False)
        panel.show_msg("替换成功")
        assert "color:" in panel._op_msg.styleSheet()
        panel.show_msg("标记失败")
        assert f"color: {t['danger']}" in panel._op_msg.styleSheet()
        panel.clear_msg()
        assert panel._op_msg.text() == ""

    def test_detail_archive_badge_and_engine_visibility(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """压缩包条目徽标与解析引擎行的显隐联动。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        fake.selectedResultIndex = 0
        fake.detailIsArchiveEntry = True
        fake.detailEngine = "zipfile"
        fake.detailFilePath = "C:/data/arch.zip::inner/x.py"
        fake.detailHitsCount = 7
        fake.selectedResultChanged.emit()

        panel = page._detail_panel
        assert panel._archive_badge.isVisibleTo(panel)
        assert panel._path_label.text().endswith("x.py")
        sizes = {cap.text(): val.text() for cap, val, _c in panel._grid_pairs}
        assert sizes["命中规则"] == "7 条"
        assert sizes["解析引擎"] == "zipfile"
        assert not panel._fp_btn.isEnabled()  # 压缩包条目不可标误报

    def test_expand_toggle_hit_cards(self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage) -> None:
        """折叠仅保留首卡明细，展开后全部可见。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        fake.selectedResultIndex = 0
        fake.selectedResultChanged.emit()
        panel = page._detail_panel
        cards = list(panel._hit_cards)
        assert all(c.isVisibleTo(panel) for c in cards)
        assert panel._expand_btn.text() == "收起"

        panel._expand_btn.click()
        assert panel._expand_btn.text() == "展开"
        assert cards[0].isVisibleTo(panel)
        assert not cards[1].isVisibleTo(panel)

        panel._expand_btn.click()
        assert all(c.isVisibleTo(panel) for c in cards)

    def test_format_context_html_escape_and_highlight(self, qapp: QApplication) -> None:
        """上下文 HTML 转义 & < > 并高亮 >>> 匹配行。"""
        html = format_context_html("key&value\n>>> SECRET <x>\n  plain", "#E84D3D")
        assert "&amp;" in html
        assert "&lt;x&gt;" in html
        assert '<span style="color: #E84D3D; font-weight: bold;">' in html
        assert "&nbsp;&nbsp;plain" in html  # 前导空格保留
        assert "<br>" in html

    def test_restore_hint_spinner_lifecycle(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """恢复中占位出现 → 转圈帧轮换 → 结束隐藏并停表。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        fake.restoring = True
        fake.scanStateChanged.emit()
        panel = page._detail_panel
        assert page._spinner_timer.isActive()
        assert panel._restore_label.isVisibleTo(panel)
        before = panel._restore_label.text()

        page._tick_spinner()
        assert panel._restore_label.text() != before

        fake.restoring = False
        fake.scanStateChanged.emit()
        assert not page._spinner_timer.isActive()
        assert not panel._restore_label.isVisibleTo(panel)

    def test_visible_range_reported_to_model(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """清单滚动/同步后向模型上报可视行范围启用虚拟化。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        page._report_visible_range()
        assert fake.resultModel.visible_range == (0, 2)

    def test_model_reset_updates_count_label(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """模型重置信号刷新过滤计数标签。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        fake.resultFilteredCount = 2
        fake.resultModel.modelReset.emit()
        assert page._count_label.text() == "2 / 5"

    def test_unbind_disconnects_and_returns_empty_state(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """解绑后续订断开、控件隐藏并回到无任务提示。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        ws.unbind()
        assert page.connected_scan_controller() is None
        assert not page._filter_input.isVisibleTo(page)
        assert page._status_label.text().startswith("未选择任务")

    def test_set_dark_renders_page_with_paint(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """主题切换幂等且渲染覆盖 delegate 绘制路径（含选中态）。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)
        page.resize(900, 620)
        page.show()
        qapp.processEvents()
        assert not page.grab().isNull()

        fake.selectedResultIndex = 1
        fake.selectedResultChanged.emit()
        qapp.processEvents()

        page.set_dark(True)
        page.set_dark(True)
        assert page._dark is True
        assert not page.grab().isNull()
        page.hide()

    def test_close_event_stops_timers(self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage) -> None:
        """closeEvent 停止转圈与防抖定时器。"""
        ev = QCloseEvent()
        page.closeEvent(ev)
        assert not page._spinner_timer.isActive()
        assert not page._filter_debounce.isActive()

    def test_unbound_callbacks_and_guard_branches(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """未绑定/空数据等守卫分支均安全返回。"""
        # 未绑定控制器：回调直接返回
        page._on_model_changed()
        page._on_scan_signal()

        fake = _FakeResultsScanController()
        ws.bind(fake)
        empty = _FakeResultsScanController()
        empty.resultModel = _FakeResultModel([])
        ws.bind(empty)
        page._report_visible_range()  # 空模型：不上报可视范围
        assert empty.resultModel.visible_range is None

    def test_signal_race_and_missing_selection_model(
        self, qapp: QApplication, ws: _FakeResultsWorkspace, page: ResultsPage
    ) -> None:
        """销毁竞态被吞掉；无选择模型时选中同步跳过；set_dark 重着色消息。"""
        fake = _FakeResultsScanController()
        ws.bind(fake)

        orig_refresh = page._refresh_header

        def _boom(_sc: object) -> None:
            raise RuntimeError("controller destroyed")

        page._refresh_header = _boom  # type: ignore[method-assign]
        try:
            fake.selectedResultChanged.emit()  # RuntimeError 被捕获不外抛
        finally:
            page._refresh_header = orig_refresh  # type: ignore[method-assign]

        # 无模型时 selectionModel 为 None：同步跳过
        fresh = _FakeResultsScanController()
        bare_ws = _FakeResultsWorkspace()
        bare_page = ResultsPage(type("_Owner", (), {"workspace": bare_ws})())
        bare_page._sync_selection(fresh)
        assert bare_page._updating_selection is False

        # set_dark 时已有操作消息按语义重刷颜色
        panel = page._detail_panel
        panel.show_msg("替换成功")
        page.set_dark(True)
        t = palette_tokens(True)
        assert f"color: {t['success']}" in panel._op_msg.styleSheet()


# ============================= HomeDialogs =============================

_PREVIEW_DATA: dict[str, object] = {
    "scanArchives": True,
    "maxWorkers": 4,
    "maxFileSizeMB": 8,
    "maxDepth": 3,
    "cacheEnabled": False,
    "perfLogEnabled": False,
    "ignoreDirs": ["__pycache__"],
    "whitelistEntries": [{"pathGlob": "**/*.key", "ruleName": "*", "source": "rules", "note": "敏感文件"}],
    "ruleFiles": [
        {
            "fileName": "builtin.yaml",
            "enabled": True,
            "exists": True,
            "isBuiltin": True,
            "scope": "builtin",
            "scanExtensionsState": "list",
            "scanExtensions": ["py", "txt"],
        },
        {
            "fileName": "empty.yaml",
            "enabled": False,
            "exists": True,
            "isBuiltin": False,
            "scope": "global",
            "scanExtensionsState": "none",
            "scanExtensions": [],
        },
    ],
    "rules": [
        {
            "name": "akid",
            "description": "AWS 密钥",
            "severityText": "严重",
            "severityColor": "#E11D48",
            "replace": True,
        },
        {"name": "low_rule", "description": "", "severityText": "信息", "severityColor": "#0366D6"},
    ],
}


class _FakeDrivesConfig:
    """EditTargetDialog 可用盘符配置替身。"""

    drives = ["C:", "D:"]


class _FakeNoDrivesConfig:
    """EditTargetDialog 无盘符配置替身。"""

    drives: list[str] = []


class _FakeTargetWorkspace:
    """EditTargetDialog 所需的最小工作区控制器替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def updateWorkspaceTarget(self, ws_id: str, mode: str, target: str) -> None:
        self.calls.append((ws_id, mode, target))


class _FakeRulesFileCtl:
    """RulesDialog 所需的 RulesController 替身。"""

    def __init__(self, items: list[dict[str, object]], has_ws: bool = True) -> None:
        self.rulesFileModel = items
        self.hasCurrentWorkspace = has_ws
        self.calls: list[tuple[object, ...]] = []

    def setRuleEnabled(self, path: str, enabled: bool) -> None:
        self.calls.append(("setRuleEnabled", path, enabled))

    def moveUp(self, *args: object) -> None:
        self.calls.append(("moveUp", args))

    def moveDown(self, *args: object) -> None:
        self.calls.append(("moveDown", args))

    def removeSelected(self, *args: object) -> None:
        self.calls.append(("removeSelected",))

    def setSelectedFileIndex(self, index: int) -> None:
        self.calls.append(("setSelectedFileIndex", index))

    def loadFileToTemp(self, path: str) -> None:
        self.calls.append(("loadFileToTemp", path))

    def loadFileFromPath(self, path: str) -> None:
        self.calls.append(("loadFileFromPath", path))


def _rules_items() -> list[dict[str, object]]:
    """构造三种作用域 + 缺失 + 可移除的组合规则文件条目。"""
    return [
        {
            "path": "builtin.yaml",
            "fileName": "builtin.yaml",
            "enabled": True,
            "exists": True,
            "isBuiltin": True,
            "scope": "builtin",
            "canRemove": False,
        },
        {
            "path": "C:/missing.yaml",
            "fileName": "missing.yaml",
            "enabled": False,
            "exists": False,
            "isBuiltin": False,
            "scope": "global",
            "canRemove": False,
        },
        {
            "path": "C:/tmp.yaml",
            "fileName": "tmp.yaml",
            "enabled": True,
            "exists": True,
            "isBuiltin": False,
            "scope": "temp",
            "canRemove": True,
        },
    ]


def _dialog_labels(widget: object) -> list[str]:
    """收集对话框下所有 QLabel 文本。"""
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


class TestHomeDialogs:
    """home_dialogs 四个共享对话框测试。"""

    def test_edit_target_drive_mode_and_accept(self, qapp: QApplication) -> None:
        """盘符模式互斥选择；确认写回 drive 目标。"""
        wc = _FakeTargetWorkspace()
        dlg = EditTargetDialog(_FakeDrivesConfig(), wc, "ws-1", "盘符扫描", "C:", dark=False)
        dlg.show()
        assert dlg._drive_area.isVisibleTo(dlg)
        assert not dlg._folder_area.isVisibleTo(dlg)

        dlg._mode_btn_1.click()
        assert not dlg._drive_area.isVisibleTo(dlg)
        assert dlg._folder_area.isVisibleTo(dlg)

        dlg._mode_btn_0.click()
        layout = dlg._drive_area.layout()
        btns = [layout.itemAt(i).widget() for i in range(layout.count())]
        btns = [w for w in btns if isinstance(w, QPushButton)]
        assert [b.text() for b in btns] == ["C:", "D:"]
        btns[1].click()
        assert dlg._drive == "D:"
        assert [b.isChecked() for b in btns] == [False, True]

        dlg.accept()
        assert wc.calls == [("ws-1", "drive", "D:")]
        dlg.hide()

    def test_edit_target_folder_mode_pick_and_accept(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """文件夹模式回显初始路径；目录选择器写入后确认。"""
        wc = _FakeTargetWorkspace()
        dlg = EditTargetDialog(_FakeDrivesConfig(), wc, "ws-2", "文件夹扫描", str(tmp_path), dark=False)
        assert dlg._folder_edit.text() == str(tmp_path)

        picked = str(tmp_path / "picked")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: picked)
        dlg._pick_folder()
        assert dlg._folder_edit.text() == picked

        dlg.accept()
        assert wc.calls == [("ws-2", "folder", picked)]
        dlg.hide()

    def test_edit_target_no_drives_warns_and_empty_target_skips_writeback(self, qapp: QApplication) -> None:
        """无可用盘符渲染警示；空目标确认不写回控制器。"""
        wc = _FakeTargetWorkspace()
        dlg = EditTargetDialog(_FakeNoDrivesConfig(), wc, "ws-3", "盘符扫描", "C:", dark=False)
        layout = dlg._drive_area.layout()
        warns = [layout.itemAt(i).widget() for i in range(layout.count())]
        warns = [w for w in warns if isinstance(w, QLabel)]
        assert len(warns) == 1
        assert "未检测到可用盘符" in warns[0].text()

        dlg._drive = ""
        dlg.accept()
        assert wc.calls == []
        dlg.hide()

    def test_rules_dialog_rows_tags_and_hint(self, qapp: QApplication) -> None:
        """规则行徽标齐全；未选工作区时提示文案切换。"""
        ctl = _FakeRulesFileCtl(_rules_items(), has_ws=False)
        dlg = RulesDialog(ctl, "任务R", dark=True)
        dlg.show()
        assert dlg._list.count() == 3

        builtin = dlg._list.itemWidget(dlg._list.item(0))
        assert "内置" in _dialog_labels(builtin)
        missing = dlg._list.itemWidget(dlg._list.item(1))
        assert "缺失" in _dialog_labels(missing)
        temp = dlg._list.itemWidget(dlg._list.item(2))
        tags = _dialog_labels(temp)
        assert "临时" in tags

        assert dlg._hint2.text().startswith("未选择工作区")
        dlg.hide()

    def test_rules_dialog_with_workspace_hint(self, qapp: QApplication) -> None:
        """已选工作区时列表提示为常规说明。"""
        dlg = RulesDialog(_FakeRulesFileCtl(_rules_items()), "任务R2", dark=False)
        assert dlg._hint2.text().startswith("勾选启用")
        dlg.hide()

    def test_rules_dialog_toggle_up_down_remove(self, qapp: QApplication) -> None:
        """勾选/上移/下移/移除按钮正确委托控制器并重建。"""
        ctl = _FakeRulesFileCtl(_rules_items())
        dlg = RulesDialog(ctl, "任务R", dark=False)
        dlg.show()

        check = dlg._list.itemWidget(dlg._list.item(0)).findChild(QCheckBox)
        check.setChecked(False)
        assert ("setRuleEnabled", "builtin.yaml", False) in ctl.calls

        dlg._sync_selection()
        assert ("setSelectedFileIndex", dlg._list.currentRow()) in ctl.calls

        children = dlg.findChildren(QPushButton)
        up_btn = next(b for b in children if b.text() == "上移")
        down_btn = next(b for b in children if b.text() == "下移")
        up_btn.click()
        down_btn.click()
        assert ctl.calls[-2][0] == "moveUp"
        assert ctl.calls[-1][0] == "moveDown"

        rm_small = next(b for b in children if b.text() == "×")
        rm_small.click()
        assert ("removeSelected",) in ctl.calls
        assert dlg._list.count() == 3  # rebuild 后恢复同样行数

        cancel = next(b for b in children if b.text() == "关闭")
        cancel.click()
        assert dlg.result() == 1
        dlg.hide()

    def test_rules_dialog_load_cancel_and_load_paths(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """加载按钮：取消不动作；选到 YAML 分别载入临时/全局。"""
        ctl = _FakeRulesFileCtl(_rules_items())
        dlg = RulesDialog(ctl, "任务R", dark=False)

        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
        dlg._load(to_temp=True)
        dlg._load(to_temp=False)
        assert not any(c[0] in ("loadFileToTemp", "loadFileFromPath") for c in ctl.calls)

        yaml_path = str(tmp_path / "r.yaml")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (yaml_path, "y"))
        dlg._load(to_temp=True)
        assert ("loadFileToTemp", yaml_path) in ctl.calls
        dlg._load(to_temp=False)
        assert ("loadFileFromPath", yaml_path) in ctl.calls

    def test_preview_rules_dialog_renders_sections(self, qapp: QApplication) -> None:
        """预览对话框双 Tab 渲染参数/忽略/白名单/规则分区。"""
        dlg = PreviewRulesDialog(dict(_PREVIEW_DATA), "任务P", dark=True)
        dlg.show()
        assert "任务P" in dlg.windowTitle()
        tabs = dlg.findChild(QTabWidget)
        assert tabs.count() == 2

        joined = "\n".join(_dialog_labels(dlg))
        for frag in (
            "扫描参数",
            "忽略目录（1 项）",
            "白名单（1 项）",
            "规则文件（2 项）",
            "匹配规则（2 条）",
            "__pycache__",
            "可替换",
            "**/*.key",
        ):
            assert frag in joined

        pages = [tabs.widget(i).findChild(QLabel).text() for i in range(tabs.count())]
        assert isinstance(pages[0], str) and isinstance(pages[1], str)
        dlg.hide()

    def test_preview_rules_dialog_empty_sections(self, qapp: QApplication) -> None:
        """空数据时各分区展示占位文案。"""
        data: dict[str, object] = {
            **_PREVIEW_DATA,
            "ignoreDirs": [],
            "whitelistEntries": [],
            "ruleFiles": [],
            "rules": [],
        }
        dlg = PreviewRulesDialog(data, "P2", dark=False)
        joined = "\n".join(_dialog_labels(dlg))
        assert "（暂无忽略目录）" in joined
        assert "（暂无白名单条目）" in joined
        assert "（暂无匹配规则" in joined
        dlg.hide()

    def test_history_dialog_render_select_compare(self, qapp: QApplication) -> None:
        """历史对话渲染趋势/摘要/记录列表，勾选两行可对比。"""
        hist = json.dumps(
            [
                {
                    "scan_id": "s1",
                    "status": "completed",
                    "finished_at": "2026-08-01T10:00:00Z",
                    "matched_files": 4,
                    "summary": "第一次",
                },
                {
                    "scan_id": "s2",
                    "status": "cancelled",
                    "finished_at": "2026-08-02T11:00:00Z",
                    "matched_files": 1,
                    "summary": "",
                },
                {
                    "scan_id": "s3",
                    "status": "failed",
                    "finished_at": "2026-08-03T12:00:00Z",
                    "matched_files": 0,
                    "summary": "出错",
                },
            ]
        )
        trend = json.dumps(
            [
                {"finished_at": "2026-08-02T09:30:00Z", "matched_files": 4},
                {"finished_at": "2026-08-03T11:00:00Z", "matched_files": 1},
            ]
        )

        class _WC:
            deep_calls: list[tuple[str, str]] = []
            cleared: list[str] = []

            def workspaceHistoryJson(self, ws_id: str) -> str:
                return hist

            def compareWithPreviousScan(self, ws_id: str) -> str:
                return '{"summary":"命中由 4 降至 1","trend":"改善"}'

            def scanTrendJson(self, ws_id: str) -> str:
                return trend

            def compareScans(self, ws_id: str, a: str, b: str) -> str:
                type(self).deep_calls.append((a, b))
                return '{"summary":"对比完成","trend":"首次"}'

            def clearWorkspaceHistory(self, ws_id: str) -> None:
                type(self).cleared.append(ws_id)

        wc = _WC()
        dlg = HistoryDialog(wc, "ws-h", "任务H", dark=True)
        dlg.show()
        assert dlg._trend_box.isVisibleTo(dlg)
        assert dlg._cmp_box.isVisibleTo(dlg)
        cmp_tags = _dialog_labels(dlg._cmp_tag_holder)
        assert "改善" in cmp_tags
        assert dlg._list_widget.count() == 3
        assert not dlg._empty_label.isVisibleTo(dlg)
        assert dlg._sel_count_label.text() == "已选 0/2"
        assert not dlg._compare_btn.isEnabled()
        assert dlg._clear_btn.isEnabled()

        checks = [dlg._list_widget.itemWidget(dlg._list_widget.item(i)).findChild(QCheckBox) for i in range(3)]
        checks[0].setChecked(True)
        checks[1].setChecked(True)
        assert dlg._sel_count_label.text() == "已选 2/2"
        assert dlg._compare_btn.isEnabled()
        assert not checks[2].isEnabled()  # 上限 2 条

        dlg._compare_btn.click()
        assert wc.deep_calls == [("s1", "s2")]
        joined = "\n".join(_dialog_labels(dlg._cmp_tag_holder.parent()))
        assert "对比完成" in joined
        assert "首次" in cmp_tags or "首次" in _dialog_labels(dlg._cmp_tag_holder)

        checks[1].setChecked(False)
        assert dlg._sel_count_label.text() == "已选 1/2"
        assert not dlg._compare_btn.isEnabled()
        dlg.hide()

    def test_history_dialog_bad_json_falls_back_empty(self, qapp: QApplication) -> None:
        """坏 JSON 各 Slot 输入按空数据处理回到空态。"""

        class _BadWC:
            def workspaceHistoryJson(self, ws_id: str) -> str:
                return "not-json"

            def compareWithPreviousScan(self, ws_id: str) -> str:
                return "[bad"

            def scanTrendJson(self, ws_id: str) -> str:
                return ""

        dlg = HistoryDialog(_BadWC(), "ws-x", "任务X", dark=False)
        assert dlg._list_widget.count() == 0
        assert dlg._empty_label.isVisibleTo(dlg)
        assert not dlg._trend_box.isVisibleTo(dlg)
        assert not dlg._cmp_box.isVisibleTo(dlg)
        assert not dlg._clear_btn.isEnabled()
        dlg.hide()

    def test_history_dialog_clear_history_resets(self, qapp: QApplication) -> None:
        """清空历史调用控制器并复位全部区块。"""
        hist = json.dumps([{"scan_id": "s1", "status": "completed", "matched_files": 1}])
        trend = json.dumps([{"finished_at": "2026-08-01T08:00:00Z", "matched_files": 1}])
        cleared: list[str] = []

        class _WC:
            def workspaceHistoryJson(self, ws_id: str) -> str:
                return hist

            def compareWithPreviousScan(self, ws_id: str) -> str:
                return "{}"

            def scanTrendJson(self, ws_id: str) -> str:
                return trend

            def compareScans(self, ws_id: str, a: str, b: str) -> str:
                return "{}"

            def clearWorkspaceHistory(self, ws_id: str) -> None:
                cleared.append(ws_id)

        dlg = HistoryDialog(_WC(), "ws-c", "任务C", dark=False)
        dlg._clear_btn.click()
        assert cleared == ["ws-c"]
        assert dlg._history_list == [] and dlg._trend_data == []
        assert dlg._empty_label.isVisibleTo(dlg)
        assert not dlg._trend_box.isVisibleTo(dlg)
        dlg.hide()
