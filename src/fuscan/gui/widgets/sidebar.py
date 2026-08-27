"""侧边栏导航：Logo 区 + 顶部主导航 + 底部辅助导航 + 暗色开关。

对照 QML 版 :file:`Sidebar.qml` 等价迁移：

- 顶部：文件扫描（home）/ 文件监控（monitor）
- 弹性撑开后底部：设置（settings）/ 关于（about）
- 每个 NavItem 左侧 3px 选中指示条 + 图标/文字染色随主题与选中态切换
- 暗色模式开关点击后发 :attr:`darkToggled`，由主窗口统一切换全局 QSS
"""

# pyrefly: ignore-errors
# PySide2 官方 .pyi 存根存在系统性重载合并缺陷（pyrefly 将多个 @typing.overload 合并为错误签名），
# 对 Widgets API 大量误报 missing-argument/bad-argument-count/bad-argument-type 与 Signal.emit/connect
# 的 missing-attribute（emit/connect 由元类动态提供、存根缺失）。与 controllers 内定点 ignore 同根因；
# 本文件为纯 GUI 布局代码，改用文件级压制降低噪音，待上游修复/支持带码压制后移除本指令。

from __future__ import annotations

from PySide2.QtCore import QEvent, Qt, Signal
from PySide2.QtGui import QPaintEvent
from PySide2.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fuscan.gui.widgets.icons import tinted_svg_icon
from fuscan.gui.widgets.qss import palette_tokens

__all__ = ["SidebarWidget"]

# 侧栏固定宽度；折叠时宽度置 0 隐藏
SIDEBAR_WIDTH = 200

# 页面 id → (qrc 图标, 显示文本)；顺序即导航顺序
_TOP_NAV: tuple[tuple[str, str, str], ...] = (
    ("home", ":/icons/home.svg", "文件扫描"),
    ("monitor", ":/icons/search.svg", "文件监控"),
)
_BOTTOM_NAV: tuple[tuple[str, str, str], ...] = (
    ("settings", ":/icons/settings.svg", "设置"),
    ("about", ":/icons/info.svg", "关于"),
)


class _NavItem(QFrame):
    """单条导航项：左指示条 + SVG 图标 + 文本。

    选中/hover 态通过重设 ``_foreground``/``_background`` 色值并 repolish 实现，
    色值来自 :func:`palette_tokens`，避免 QSS 动态 property 方案的繁琐刷新。
    """

    clicked = Signal(str)

    def __init__(self, page_id: str, icon: str, label: str, dark: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page_id = page_id
        self._icon_source = icon
        self._label = label
        self._dark = dark
        self._selected = False
        self._hovered = False
        self.setFixedHeight(40)
        self.setCursor(Qt.PointingHandCursor)

        # 左侧 3px 选中指示条 + 右侧内容行
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 12, 0)
        row.setSpacing(12)
        self._indicator = QFrame()
        self._indicator.setFixedSize(3, 22)
        self._indicator.setStyleSheet("border: none; border-radius: 2px;")
        row.addWidget(self._indicator)
        row.addSpacing(15)
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(20, 20)
        self._icon_label.setAlignment(Qt.AlignCenter)
        row.addWidget(self._icon_label)
        self._text_label = QLabel(label)
        row.addWidget(self._text_label)
        row.addStretch()

        self._apply_state()

    # ----------------------------- 状态 -----------------------------

    @property
    def page_id(self) -> str:
        """对应页面 id。"""
        return self._page_id

    def set_selected(self, selected: bool) -> None:
        """设置选中态并刷新配色与图标染色。"""
        if self._selected != selected:
            self._selected = selected
            self._apply_state()

    def set_dark(self, dark: bool) -> None:
        """主题切换时更新底色假设并刷新配色。"""
        if self._dark != dark:
            self._dark = dark
            self._apply_state()

    # ----------------------------- 绘制事件 -----------------------------

    def enterEvent(self, event: QEvent) -> None:
        """悬停高亮。"""
        super().enterEvent(event)
        self._hovered = True
        self._apply_state()

    def leaveEvent(self, event: QEvent) -> None:
        """取消悬停。"""
        super().leaveEvent(event)
        self._hovered = False
        self._apply_state()

    def mousePressEvent(self, event: QEvent) -> None:
        """点击发出导航信号（仅左键）。"""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._page_id)
        super().mousePressEvent(event)

    # ----------------------------- 私有 -----------------------------

    def _apply_state(self) -> None:
        """按当前选中/hover/主题态计算配色并应用到子控件。"""
        t = palette_tokens(self._dark)
        fg = t["primary"] if self._selected else t["text_secondary"]
        bg = t["bg_selected"] if self._selected else (t["bg_hover"] if self._hovered else "transparent")
        self.setStyleSheet(f"QFrame {{ background-color: {bg}; border: none; }}")
        self._indicator.setStyleSheet(
            f"border: none; border-radius: 2px; background-color: {fg};"
            if self._selected
            else "border: none; border-radius: 2px; background-color: transparent;"
        )
        self._text_label.setStyleSheet(f"color: {fg}; font-size: 13px; background: transparent;")
        icon = tinted_svg_icon(self._icon_source, fg, 16)
        self._icon_label.setPixmap(icon.pixmap(16, 16))


class SidebarWidget(QWidget):
    """侧边栏容器。页面切换统一经 :attr:`pageChanged` 与暗色开关 :attr:`darkToggled`。"""

    pageChanged = Signal(str)
    darkToggled = Signal(bool)

    def __init__(self, dark: bool = False, parent: QWidget | None = None) -> None:
        """初始化侧边栏并装配 Logo、导航项与暗色开关。

        :param dark: 初始是否为深色主题
        :param parent: 父部件
        """
        super().__init__(parent)
        self._dark = dark
        self._collapsed = False
        self._items: list[_NavItem] = []
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._build_ui()
        self._refresh_theme()

    # ----------------------------- 构建 -----------------------------

    def _build_ui(self) -> None:
        """构建侧边栏布局。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 16)
        root.setSpacing(0)

        # Logo 区
        logo_row_widget = QWidget()
        logo_row = QHBoxLayout(logo_row_widget)
        logo_row.setContentsMargins(20, 18, 16, 18)
        logo_row.setSpacing(10)
        logo_box = QLabel("F", alignment=Qt.AlignCenter)
        logo_box.setFixedSize(28, 28)
        logo_row.addWidget(logo_box)
        title_label = QLabel("fuscan")
        logo_row.addWidget(title_label)
        logo_row.addStretch()
        root.addWidget(logo_row_widget)
        self._logo_box = logo_box
        self._title_label = title_label

        # 顶部主导航
        for nav in _TOP_NAV:
            root.addWidget(self._make_item(*nav))

        root.addStretch()

        # 底部辅助导航
        for nav in _BOTTOM_NAV:
            root.addWidget(self._make_item(*nav))

        # 暗色模式开关
        root.addSpacing(8)
        root.addWidget(self._build_dark_switch())

    def _make_item(self, page_id: str, icon: str, label: str) -> _NavItem:
        """创建一条导航项并连接信号。"""
        item = _NavItem(page_id, icon, label, self._dark)
        item.clicked.connect(self.set_current_page)
        self._items.append(item)
        return item

    def _build_dark_switch(self) -> QFrame:
        """构建暗色模式开关行（图标 + 文本 + 滑块开关）。"""
        box = QFrame()
        box.setObjectName("darkSwitch")
        box.setFixedHeight(36)
        row = QHBoxLayout(box)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(8)
        moon = QLabel()
        moon.setFixedSize(14, 14)
        moon.setAlignment(Qt.AlignCenter)
        self._moon_icon = moon
        text = QLabel("暗色模式")
        self._switch_text = text
        self._toggle = _ToggleSwitch(self._dark)
        self._toggle.toggled_to.connect(self.darkToggled)
        row.addWidget(moon)
        row.addWidget(text)
        row.addStretch()
        row.addWidget(self._toggle)
        return box

    # ----------------------------- 公共 API -----------------------------

    def set_current_page(self, page_id: str) -> None:
        """程序化或用户触发地切换当前页并广播信号。"""
        for item in self._items:
            item.set_selected(item.page_id == page_id)
        self.pageChanged.emit(page_id)

    def set_dark(self, dark: bool) -> None:
        """外部（主窗口）驱动主题切换，同步侧边栏配色与开关状态。"""
        self._dark = dark
        for item in self._items:
            item.set_dark(dark)
        self._toggle.set_on(dark)
        self._refresh_theme()

    @property
    def collapsed(self) -> bool:
        """是否处于折叠状态。"""
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        """折叠/展开侧边栏（Ctrl+B）。折叠时整栏隐藏。"""
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        self.setVisible(not collapsed)

    # ----------------------------- 私有 -----------------------------

    def _refresh_theme(self) -> None:
        """按当前主题刷新背景与 Logo/文本/月亮图标配色。"""
        t = palette_tokens(self._dark)
        self.setStyleSheet(
            f"SidebarWidget {{ background-color: {t['bg_sidebar']}; border-right: 1px solid {t['border']}; }}"
        )
        # NavItem 子控件颜色由其 _apply_state 内联样式自行刷新
        self._logo_box.setStyleSheet(
            f"background-color: {t['primary']}; color: {t['text_on_primary']};"
            " border-radius: 6px; font-weight: bold; font-size: 14px;"
        )
        self._title_label.setStyleSheet(f"color: {t['text_primary']}; font-size: 15px; font-weight: bold;")
        self._moon_icon.setPixmap(tinted_svg_icon(":/icons/moon.svg", t["text_secondary"], 14).pixmap(14, 14))
        self._switch_text.setStyleSheet(f"color: {t['text_secondary']}; font-size: 12px;")


class _ToggleSwitch(QWidget):
    """最小化滑块开关（对应 QML 版自定义 Rectangle 开关）。

    点击切换状态并发 :attr:`toggled_to(bool)`。
    """

    toggled_to = Signal(bool)

    def __init__(self, initial: bool, parent: QWidget | None = None) -> None:
        """初始化开关。

        :param initial: 初始状态（True 为开/深色）
        :param parent: 父部件
        """
        super().__init__(parent)
        self._on = initial
        self.setFixedSize(36, 20)
        self.setCursor(Qt.PointingHandCursor)

    def set_on(self, value: bool) -> None:
        """外部同步开关状态（不发信号，避免回环）。"""
        self._on = value
        self.update()

    def mousePressEvent(self, event: QEvent) -> None:
        """点击切换并发出信号。"""
        if event.button() == Qt.LeftButton:
            self._on = not self._on
            self.update()
            self.toggled_to.emit(self._on)
        super().mousePressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:
        """自绘滑轨与圆形滑块（色值取自当前主题色板）。"""
        from PySide2.QtGui import QColor, QPainter

        t = palette_tokens(self._dark_hint())
        track = QColor(t["primary"]) if self._on else QColor(t["border_muted"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(self.rect(), 10, 10)
        knob_x = 18.0 if self._on else 2.0
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(int(knob_x), 2, 16, 16)
        painter.end()

    def _dark_hint(self) -> bool:
        """推断深色模式供滑块配色：对齐父级 SidebarWidget 的状态。"""
        parent = self.parent()
        return bool(getattr(parent, "_dark", False)) if parent is not None else False
