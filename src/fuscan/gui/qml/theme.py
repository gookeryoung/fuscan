"""主题控制器：将设计令牌暴露给 QML 双向绑定。

按 rule-12-pyside-dev.md 要求，所有色值/字号/圆角通过 :class:`ThemeController`
暴露为 ``@Property``，QML 直接绑定（如 ``color: Theme.colorPrimary``），
禁止 QML 侧硬编码色值。暗色模式由 ``isDark`` 双向驱动，切换时仅 emit
``themeChanged``，QML 绑定自动刷新。

色值定义沿用 GitHub Desktop 风格（浅色）+ Tokyo Night 风格（深色），
按钮三级层级差异化设计（L1 主操作/L2 次要/L3 辅助）。

所有 ``@Property`` 共用 :attr:`themeChanged` 作为 NOTIFY 信号：色值/字号/圆角
本身为常量不变，但 QML 绑定要求属性必须声明 NOTIFY 才能在绑定表达式中使用
（否则报 ``depends on non-NOTIFYable properties`` 警告，且暗色模式切换时
``Theme.isDark ? colorA : colorB`` 三元不会重新求值）。``setDark`` 切换时
emit ``themeChanged``，所有绑定表达式重新求值，实现暗色模式无缝切换。
"""

from __future__ import annotations

try:
    from PySide2.QtCore import Property, QObject, Signal
    from PySide2.QtGui import QColor
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QColor  # pyrefly: ignore [missing-import]

__all__ = ["ThemeController"]

# 速度档次色（T1-T5，与 base.SpeedTier.color 一致）
_SPEED_TIER_COLORS: tuple[str, ...] = ("#28A745", "#17A2B8", "#FFC107", "#FD7E14", "#DC3545")


class ThemeController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """主题令牌控制器：暴露色值/字号/圆角/按钮层级给 QML。

    所有 ``@Property`` 只读，仅 :attr:`isDark` 可通过 :meth:`setDark` 双向切换。
    QML 通过 ``Theme.isDark ? colorA : colorB`` 三元表达式切换深浅色。
    """

    themeChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dark: bool = False

    # ----------------------------- 暗色模式 -----------------------------

    @Property(bool, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def isDark(self) -> bool:
        """当前是否为暗色模式。"""
        return self._dark

    @Property(bool, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def isLight(self) -> bool:
        """当前是否为浅色模式（``not isDark`` 的便捷别名）。"""
        return not self._dark

    def setDark(self, value: bool) -> None:
        """切换暗色模式（QML 通过 ``Theme.setDark(...)`` 调用）。"""
        if self._dark != value:
            self._dark = value
            self.themeChanged.emit()  # pyrefly: ignore [missing-attribute]

    # ----------------------------- 色彩令牌（浅色） -----------------------------

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorPrimary(self) -> QColor:
        """主色（蓝）。"""
        return QColor("#0366D6")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorPrimaryDark(self) -> QColor:
        """主色按下态。"""
        return QColor("#0245A6")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorDanger(self) -> QColor:
        """危险色（红）。"""
        return QColor("#D73A49")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorWarning(self) -> QColor:
        """警告色（橙）。"""
        return QColor("#F0883E")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorSuccess(self) -> QColor:
        """成功色（绿）。"""
        return QColor("#28A745")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorTextPrimary(self) -> QColor:
        """主文本色。"""
        return QColor("#24292E")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorTextSecondary(self) -> QColor:
        """次要文本色。"""
        return QColor("#586069")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorTextOnPrimary(self) -> QColor:
        """主色背景上的文本色（白）。"""
        return QColor("#FFFFFF")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgApp(self) -> QColor:
        """应用背景色（中性灰）。"""
        return QColor("#F5F6F8")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgCard(self) -> QColor:
        """卡片背景色（白）。"""
        return QColor("#FFFFFF")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgHover(self) -> QColor:
        """hover 背景色。"""
        return QColor("#F6F8FA")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgSelected(self) -> QColor:
        """选中态背景色。"""
        return QColor("#EDF3FF")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBorder(self) -> QColor:
        """边框色。"""
        return QColor("#E1E4E8")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBorderMuted(self) -> QColor:
        """弱化边框色。"""
        return QColor("#D0D7DE")

    # ----------------------------- 色彩令牌（深色专属） -----------------------------

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorSidebarDark(self) -> QColor:
        """暗色模式侧栏背景色。"""
        return QColor("#16161E")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgAppDark(self) -> QColor:
        """暗色模式应用背景色。"""
        return QColor("#1A1B26")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgCardDark(self) -> QColor:
        """暗色模式卡片背景色。"""
        return QColor("#1E1F2A")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgHoverDark(self) -> QColor:
        """暗色模式 hover 背景色。"""
        return QColor("#2A2B3A")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgSelectedDark(self) -> QColor:
        """暗色模式选中态背景色。"""
        return QColor("#2A2B3A")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBorderDark(self) -> QColor:
        """暗色模式边框色。"""
        return QColor("#2E2F3A")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorPrimaryDarkMode(self) -> QColor:
        """暗色模式主色（蓝紫，Tokyo Night 风格）。"""
        return QColor("#7AA2F7")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorTextPrimaryDark(self) -> QColor:
        """暗色模式主文本色。"""
        return QColor("#E0E0EF")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorTextSecondaryDark(self) -> QColor:
        """暗色模式次要文本色。"""
        return QColor("#A0A0B0")

    # ----------------------------- 排版令牌 -----------------------------

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizeCaption(self) -> int:
        """caption 字号（11px）。"""
        return 11

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizeSmall(self) -> int:
        """小字号（12px）。"""
        return 12

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizeBody(self) -> int:
        """正文字号（13px）。"""
        return 13

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizeHeading(self) -> int:
        """标题字号（15px）。"""
        return 15

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizeTitle(self) -> int:
        """大标题字号（18px）。"""
        return 18

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizePageTitle(self) -> int:
        """页面大标题字号（22px）。"""
        return 22

    # ----------------------------- 间距令牌（8px 基准网格） -----------------------------

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def spacingXs(self) -> int:
        """超小间距（4px）。"""
        return 4

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def spacingSm(self) -> int:
        """小间距（8px）。"""
        return 8

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def spacingMd(self) -> int:
        """中间距（16px）。"""
        return 16

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def spacingLg(self) -> int:
        """大间距（24px）。"""
        return 24

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def spacingXl(self) -> int:
        """超大间距（32px）。"""
        return 32

    # ----------------------------- 圆角与尺寸 -----------------------------

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def radiusSm(self) -> int:
        """小圆角（4px）。"""
        return 4

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def radiusMd(self) -> int:
        """中圆角（6px）。"""
        return 6

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def radiusLg(self) -> int:
        """大圆角（8px）。"""
        return 8

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def sidebarWidth(self) -> int:
        """侧栏宽度（200px）。"""
        return 200

    # ----------------------------- 按钮三级层级 -----------------------------

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def btnHeightPrimary(self) -> int:
        """L1 主操作按钮高度（48px）。"""
        return 48

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def btnHeightSecondary(self) -> int:
        """L2 次要按钮高度（40px）。"""
        return 40

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def btnHeightGhost(self) -> int:
        """L3 辅助按钮高度（32px）。"""
        return 32

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def btnRadiusPrimary(self) -> int:
        """L1 主操作按钮圆角。"""
        return self.radiusLg

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def btnRadiusSecondary(self) -> int:
        """L2 次要按钮圆角。"""
        return self.radiusMd

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def btnRadiusGhost(self) -> int:
        """L3 辅助按钮圆角。"""
        return self.radiusSm

    # ----------------------------- 速度档次色 -----------------------------

    @Property("QVariantList", notify=themeChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def speedTierColors(self) -> list[str]:
        """速度档次色列表（T1-T5 对应 #28A745/#17A2B8/#FFC107/#FD7E14/#DC3545）。"""
        return list(_SPEED_TIER_COLORS)

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def speedTierT1(self) -> QColor:
        """T1 极速色（绿）。"""
        return QColor(_SPEED_TIER_COLORS[0])

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def speedTierT2(self) -> QColor:
        """T2 快速色（青）。"""
        return QColor(_SPEED_TIER_COLORS[1])

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def speedTierT3(self) -> QColor:
        """T3 中速色（琥珀）。"""
        return QColor(_SPEED_TIER_COLORS[2])

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def speedTierT4(self) -> QColor:
        """T4 慢速色（橙）。"""
        return QColor(_SPEED_TIER_COLORS[3])

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def speedTierT5(self) -> QColor:
        """T5 极慢色（红）。"""
        return QColor(_SPEED_TIER_COLORS[4])
