"""Widgets GUI 骨架测试：QSS 构建 / 图标染色 / 侧边栏 / 主窗口 / 关于页。

对应 QML → Widgets 迁移 P1 骨架与 P2 页面迁移（iter-qml-widgets）。
"""

# pyrefly: ignore-errors
# PySide2 存根缺陷导致 Signal.connect/emit 在测试代码中同样误报，详见 sidebar.py 头部说明。

from __future__ import annotations

from pathlib import Path

import pytest
from PySide2.QtCore import Qt, QUrl
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication

import fuscan.gui.widgets as gui_widgets
from fuscan.gui.controllers import AboutController, FileMonitorController, SplashController
from fuscan.gui.widgets.about_page import AboutPage
from fuscan.gui.widgets.file_monitor_page import FileMonitorPage
from fuscan.gui.widgets.icons import clear_icon_cache, tinted_svg_icon
from fuscan.gui.widgets.main_window import PAGE_IDS, MainWindow
from fuscan.gui.widgets.qss import build_app_qss, palette_tokens
from fuscan.gui.widgets.sidebar import SidebarWidget
from fuscan.gui.widgets.splash import SplashWindow


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


class _StubController:
    """提供 cleanup / about / file_monitor 的最小桩（页面用真实控制器）。"""

    cleanup_calls: list[int] = []

    def __init__(self, qapp: QApplication, cfg_dir: Path) -> None:
        """构造子控制器（依赖已就绪的 QApplication 与隔离配置目录）。"""
        self.about = AboutController()
        self.file_monitor = FileMonitorController(
            _FakeRulesStub(),
            _observer_factory=_FakeObserver,
        )

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


class TestLazyFacade:
    """widgets 包惰性导出门面测试。"""

    def test_lazy_exports_resolve(self) -> None:
        """MainWindow/SplashWindow/AboutPage/FileMonitorPage 惰性解析为类。"""
        assert gui_widgets.MainWindow.__name__ == "MainWindow"
        assert gui_widgets.SplashWindow.__name__ == "SplashWindow"
        assert gui_widgets.AboutPage.__name__ == "AboutPage"
        assert gui_widgets.FileMonitorPage.__name__ == "FileMonitorPage"

    def test_unknown_attribute_raises(self) -> None:
        """未知属性抛 AttributeError。"""
        with pytest.raises(AttributeError):
            _ = gui_widgets.__no_such_export__  # 故意访问不存在的属性
