"""``ThemeController`` 单元测试。

验证主题令牌（色彩/排版/间距/圆角/按钮层级/速度档次色）的默认值与
暗色模式切换行为。QML 通过 ``@Property`` 直接绑定，本测试确保令牌值
与 rule-12-pyside-dev.md 中 GitHub Desktop + Tokyo Night 风格定义一致。

测试不依赖 QML 引擎，仅构造 ``QObject`` 子类验证 ``@Property`` 返回值。
"""

from __future__ import annotations

import os

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    try:
        from PySide2.QtGui import QColor
    except ImportError:  # pragma: no cover
        from PySide6.QtGui import QColor  # pyrefly: ignore [missing-import]

    from fuscan.gui.qml.theme import ThemeController

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过主题测试", allow_module_level=True)


@pytest.fixture()
def theme() -> ThemeController:
    """每个测试独立 ThemeController 实例。"""
    return ThemeController()


class TestDarkMode:
    def test_default_is_light(self, theme: ThemeController) -> None:
        """默认应为浅色模式。"""
        assert theme.isDark is False
        assert theme.isLight is True

    def test_set_dark_to_true_emits_signal(self, theme: ThemeController) -> None:
        """切换到暗色模式应 emit themeChanged。"""
        emitted: list[None] = []
        theme.themeChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        theme.setDark(True)
        assert theme.isDark is True
        assert theme.isLight is False
        assert len(emitted) == 1

    def test_set_dark_noop_when_same(self, theme: ThemeController) -> None:
        """重复设置相同值不应 emit 信号。"""
        emitted: list[None] = []
        theme.themeChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        theme.setDark(False)
        assert len(emitted) == 0

    def test_toggle_dark_back_to_light(self, theme: ThemeController) -> None:
        """暗色 → 浅色切换。"""
        theme.setDark(True)
        theme.setDark(False)
        assert theme.isDark is False
        assert theme.isLight is True


class TestColorTokens:
    def test_primary_color(self, theme: ThemeController) -> None:
        assert theme.colorPrimary == QColor("#0366D6")

    def test_primary_dark_color(self, theme: ThemeController) -> None:
        assert theme.colorPrimaryDark == QColor("#0245A6")

    def test_danger_color(self, theme: ThemeController) -> None:
        assert theme.colorDanger == QColor("#D73A49")

    def test_warning_color(self, theme: ThemeController) -> None:
        assert theme.colorWarning == QColor("#F0883E")

    def test_success_color(self, theme: ThemeController) -> None:
        assert theme.colorSuccess == QColor("#28A745")

    def test_text_primary_color(self, theme: ThemeController) -> None:
        assert theme.colorTextPrimary == QColor("#24292E")

    def test_text_secondary_color(self, theme: ThemeController) -> None:
        assert theme.colorTextSecondary == QColor("#586069")

    def test_text_on_primary_color(self, theme: ThemeController) -> None:
        assert theme.colorTextOnPrimary == QColor("#FFFFFF")

    def test_bg_app_color(self, theme: ThemeController) -> None:
        assert theme.colorBgApp == QColor("#F5F6F8")

    def test_bg_card_color(self, theme: ThemeController) -> None:
        assert theme.colorBgCard == QColor("#FFFFFF")

    def test_bg_hover_color(self, theme: ThemeController) -> None:
        assert theme.colorBgHover == QColor("#F6F8FA")

    def test_bg_selected_color(self, theme: ThemeController) -> None:
        assert theme.colorBgSelected == QColor("#EDF3FF")

    def test_border_color(self, theme: ThemeController) -> None:
        assert theme.colorBorder == QColor("#E1E4E8")

    def test_border_muted_color(self, theme: ThemeController) -> None:
        assert theme.colorBorderMuted == QColor("#D0D7DE")


class TestDarkColorTokens:
    def test_sidebar_dark_color(self, theme: ThemeController) -> None:
        assert theme.colorSidebarDark == QColor("#16161E")

    def test_bg_app_dark_color(self, theme: ThemeController) -> None:
        assert theme.colorBgAppDark == QColor("#1A1B26")

    def test_bg_card_dark_color(self, theme: ThemeController) -> None:
        assert theme.colorBgCardDark == QColor("#1E1F2A")

    def test_primary_dark_mode_color(self, theme: ThemeController) -> None:
        assert theme.colorPrimaryDarkMode == QColor("#7AA2F7")

    def test_text_primary_dark_color(self, theme: ThemeController) -> None:
        assert theme.colorTextPrimaryDark == QColor("#E0E0EF")

    def test_text_secondary_dark_color(self, theme: ThemeController) -> None:
        assert theme.colorTextSecondaryDark == QColor("#A0A0B0")


class TestTypographyTokens:
    def test_font_size_caption(self, theme: ThemeController) -> None:
        assert theme.fontSizeCaption == 11

    def test_font_size_small(self, theme: ThemeController) -> None:
        assert theme.fontSizeSmall == 12

    def test_font_size_body(self, theme: ThemeController) -> None:
        assert theme.fontSizeBody == 13

    def test_font_size_heading(self, theme: ThemeController) -> None:
        assert theme.fontSizeHeading == 15

    def test_font_size_title(self, theme: ThemeController) -> None:
        assert theme.fontSizeTitle == 18

    def test_font_size_page_title(self, theme: ThemeController) -> None:
        assert theme.fontSizePageTitle == 22


class TestSpacingTokens:
    def test_spacing_xs(self, theme: ThemeController) -> None:
        assert theme.spacingXs == 4

    def test_spacing_sm(self, theme: ThemeController) -> None:
        assert theme.spacingSm == 8

    def test_spacing_md(self, theme: ThemeController) -> None:
        assert theme.spacingMd == 16

    def test_spacing_lg(self, theme: ThemeController) -> None:
        assert theme.spacingLg == 24

    def test_spacing_xl(self, theme: ThemeController) -> None:
        assert theme.spacingXl == 32


class TestRadiusAndSize:
    def test_radius_sm(self, theme: ThemeController) -> None:
        assert theme.radiusSm == 4

    def test_radius_md(self, theme: ThemeController) -> None:
        assert theme.radiusMd == 6

    def test_radius_lg(self, theme: ThemeController) -> None:
        assert theme.radiusLg == 8

    def test_sidebar_width(self, theme: ThemeController) -> None:
        assert theme.sidebarWidth == 200


class TestButtonHierarchy:
    def test_btn_height_primary(self, theme: ThemeController) -> None:
        assert theme.btnHeightPrimary == 48

    def test_btn_height_secondary(self, theme: ThemeController) -> None:
        assert theme.btnHeightSecondary == 40

    def test_btn_height_ghost(self, theme: ThemeController) -> None:
        assert theme.btnHeightGhost == 32

    def test_btn_radius_primary(self, theme: ThemeController) -> None:
        assert theme.btnRadiusPrimary == theme.radiusLg

    def test_btn_radius_secondary(self, theme: ThemeController) -> None:
        assert theme.btnRadiusSecondary == theme.radiusMd

    def test_btn_radius_ghost(self, theme: ThemeController) -> None:
        assert theme.btnRadiusGhost == theme.radiusSm


class TestSpeedTierColors:
    def test_speed_tier_colors_list(self, theme: ThemeController) -> None:
        """5 档色值列表（T1-T5）。"""
        colors = theme.speedTierColors
        assert colors == ["#28A745", "#17A2B8", "#FFC107", "#FD7E14", "#DC3545"]

    def test_speed_tier_individual_colors(self, theme: ThemeController) -> None:
        assert theme.speedTierT1 == QColor("#28A745")
        assert theme.speedTierT2 == QColor("#17A2B8")
        assert theme.speedTierT3 == QColor("#FFC107")
        assert theme.speedTierT4 == QColor("#FD7E14")
        assert theme.speedTierT5 == QColor("#DC3545")
