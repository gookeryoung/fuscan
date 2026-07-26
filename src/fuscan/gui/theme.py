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

import sys
from pathlib import Path

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
    from PySide2.QtGui import QColor
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QColor  # pyrefly: ignore [missing-import]

__all__ = ["ThemeController", "detect_font_families"]

# 速度档次色（T1-T5，与 base.SpeedTier.color 一致）
_SPEED_TIER_COLORS: tuple[str, ...] = ("#28A745", "#17A2B8", "#FFC107", "#FD7E14", "#DC3545")


def detect_font_families() -> tuple[str, ...]:
    """按平台返回优先级字体族列表，供 ``QFont.setFamilies()`` 回退使用。

    跨平台最佳实践（Qt 5.13+ ``setFamilies`` 支持自动回退到首个可用字体）：

    - **Windows**：``Microsoft YaHei UI``（Win10+ UI 字体，专为界面渲染优化）→
      ``Microsoft YaHei``（Win7 兜底）→ ``Segoe UI``（英文/数字）→ ``Arial``
    - **macOS**：``PingFang SC``（苹方，macOS 默认中文）→
      ``.AppleSystemUIFont``（系统字体）→ ``Helvetica Neue``
    - **Linux**：``Noto Sans CJK SC``（Google 思源黑体）→
      ``Source Han Sans SC``（Adobe 思源黑体）→ ``Roboto`` → ``DejaVu Sans``

    :return: 字体族优先级列表（首个可用者被采用）
    """
    if sys.platform == "win32":
        return ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial")
    if sys.platform == "darwin":
        return ("PingFang SC", ".AppleSystemUIFont", "Helvetica Neue", "Arial")
    return ("Noto Sans CJK SC", "Source Han Sans SC", "Roboto", "DejaVu Sans")


class ThemeController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """主题令牌控制器：暴露色值/字号/圆角/按钮层级给 QML。

    所有 ``@Property`` 只读，仅 :attr:`isDark` 可通过 :meth:`setDark` 双向切换。
    QML 通过 ``Theme.isDark ? colorA : colorB`` 三元表达式切换深浅色。
    """

    themeChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dark: bool = False
        # 字体配置（由 ConfigController.setFontConfig 注入，默认 14px）
        self._font_family: str | None = None
        self._font_size: int = 14
        self._font_bold: bool = False

    # ----------------------------- 暗色模式 -----------------------------

    @Property(bool, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def isDark(self) -> bool:
        """当前是否为暗色模式。"""
        return self._dark

    @Property(bool, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def isLight(self) -> bool:
        """当前是否为浅色模式（``not isDark`` 的便捷别名）。"""
        return not self._dark

    @Slot(bool)  # pyrefly: ignore [not-callable]
    def setDark(self, value: bool) -> None:
        """切换暗色模式（QML 通过 ``Theme.setDark(...)`` 调用）。"""
        if self._dark != value:
            self._dark = value
            self.themeChanged.emit()  # pyrefly: ignore [missing-attribute]

    # ----------------------------- 字体配置 -----------------------------

    @Slot(str, int, bool)  # pyrefly: ignore [not-callable]
    def setFontConfig(self, family: str, size: int, bold: bool) -> None:
        """注入用户字体配置（由 :class:`ConfigController` 启动时调用）。

        :param family: 字体族名（空串表示使用平台默认）
        :param size: 基准字号（默认 14，ThemeController 基于 base 计算其他字号）
        :param bold: 是否加粗
        """
        new_family = family if family else None
        size = max(8, min(32, size))  # 钳制到合理范围，避免极端值
        if self._font_family != new_family or self._font_size != size or self._font_bold != bold:
            self._font_family = new_family
            self._font_size = size
            self._font_bold = bold
            self.themeChanged.emit()  # pyrefly: ignore [missing-attribute]

    # ----------------------------- 色彩令牌（浅色） -----------------------------

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorPrimary(self) -> QColor:
        """主色（浅色蓝 / 暗色蓝紫，根据 isDark 动态切换）。"""
        return QColor("#7AA2F7") if self._dark else QColor("#0366D6")

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
        """主文本色（根据 isDark 动态切换）。"""
        return QColor("#E0E0EF") if self._dark else QColor("#24292E")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorTextSecondary(self) -> QColor:
        """次要文本色（根据 isDark 动态切换）。"""
        return QColor("#A0A0B0") if self._dark else QColor("#586069")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorTextOnPrimary(self) -> QColor:
        """主色背景上的文本色（白）。"""
        return QColor("#FFFFFF")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgApp(self) -> QColor:
        """应用背景色（根据 isDark 动态切换）。"""
        return QColor("#1A1B26") if self._dark else QColor("#F5F6F8")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgCard(self) -> QColor:
        """卡片背景色（根据 isDark 动态切换）。"""
        return QColor("#1E1F2A") if self._dark else QColor("#FFFFFF")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgHover(self) -> QColor:
        """hover 背景色（根据 isDark 动态切换）。"""
        return QColor("#2A2B3A") if self._dark else QColor("#F6F8FA")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBgSelected(self) -> QColor:
        """选中态背景色（根据 isDark 动态切换）。"""
        return QColor("#2A2B3A") if self._dark else QColor("#EDF3FF")

    @Property(QColor, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def colorBorder(self) -> QColor:
        """边框色（根据 isDark 动态切换）。"""
        return QColor("#2E2F3A") if self._dark else QColor("#E1E4E8")

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
        """caption 字号（base - 2，默认 12px）。"""
        return self._font_size - 2

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizeSmall(self) -> int:
        """小字号（base - 1，默认 13px）。"""
        return self._font_size - 1

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizeBody(self) -> int:
        """正文字号（base，默认 14px）。"""
        return self._font_size

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizeHeading(self) -> int:
        """标题字号（base + 2，默认 16px）。"""
        return self._font_size + 2

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizeTitle(self) -> int:
        """大标题字号（base + 4，默认 18px）。"""
        return self._font_size + 4

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizePageTitle(self) -> int:
        """页面大标题字号（base + 8，默认 22px）。"""
        return self._font_size + 8

    @Property(str, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontFamily(self) -> str:
        """主字体族（用户配置优先，否则返回平台默认字体族首个可用字体名）。

        全局字体回退由 ``app.py`` 的 ``QGuiApplication.setFont()`` +
        ``QFont.setFamilies()`` 处理，QML 控件默认继承，无需每个控件单独设置。
        仅在需要显式覆盖时绑定此令牌。
        """
        if self._font_family:
            return self._font_family
        return detect_font_families()[0]

    @Property(bool, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontBold(self) -> bool:
        """是否全局加粗（QML 通过 ``font.bold: theme.fontBold`` 绑定）。"""
        return self._font_bold

    @Property(int, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def fontSizeBase(self) -> int:
        """基准字号（用户可配置，默认 14，其他字号基于此计算）。"""
        return self._font_size

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

    @Property(str, notify=themeChanged)  # pyrefly: ignore [not-callable]
    def iconsDir(self) -> str:
        """图标目录绝对路径（供 QML ``Image { source: "file:///" + theme.iconsDir + "/xxx.svg" }``）。"""
        return str(Path(__file__).parent.parent / "assets" / "icons")

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
