"""``ThemeController`` 单元测试。

验证主题令牌（色彩/排版/间距/圆角/按钮层级/速度档次色）的默认值与
暗色模式切换行为。QML 通过 ``@Property`` 直接绑定，本测试确保令牌值
与 rule-12-pyside-dev.md 中 GitHub Desktop + Tokyo Night 风格定义一致。

测试不依赖 QML 引擎，仅构造 ``QObject`` 子类验证 ``@Property`` 返回值。
"""

from __future__ import annotations

import os

import pytest
from PySide2.QtGui import QColor

from fuscan.gui.theme import ThemeController, detect_font_families

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui


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


class TestDynamicDarkSwitch:
    """通用色值属性在 isDark 切换时应动态返回对应深浅色值。

    QML 中存在大量 ``theme.isDark ? theme.colorX : theme.colorX`` 三元（两侧
    同名属性），只有当 ``colorX`` 本身根据 ``isDark`` 动态切换时三元才生效。
    """

    def test_primary_switches_in_dark(self, theme: ThemeController) -> None:
        theme.setDark(True)
        assert theme.colorPrimary == QColor("#7AA2F7")

    def test_text_primary_switches_in_dark(self, theme: ThemeController) -> None:
        theme.setDark(True)
        assert theme.colorTextPrimary == QColor("#E0E0EF")

    def test_text_secondary_switches_in_dark(self, theme: ThemeController) -> None:
        theme.setDark(True)
        assert theme.colorTextSecondary == QColor("#A0A0B0")

    def test_bg_app_switches_in_dark(self, theme: ThemeController) -> None:
        theme.setDark(True)
        assert theme.colorBgApp == QColor("#1A1B26")

    def test_bg_card_switches_in_dark(self, theme: ThemeController) -> None:
        theme.setDark(True)
        assert theme.colorBgCard == QColor("#1E1F2A")

    def test_bg_hover_switches_in_dark(self, theme: ThemeController) -> None:
        theme.setDark(True)
        assert theme.colorBgHover == QColor("#2A2B3A")

    def test_bg_selected_switches_in_dark(self, theme: ThemeController) -> None:
        theme.setDark(True)
        assert theme.colorBgSelected == QColor("#2A2B3A")

    def test_border_switches_in_dark(self, theme: ThemeController) -> None:
        theme.setDark(True)
        assert theme.colorBorder == QColor("#2E2F3A")

    def test_tokens_revert_after_toggle_back(self, theme: ThemeController) -> None:
        """暗色 → 浅色切换后通用属性应回到浅色值。"""
        theme.setDark(True)
        theme.setDark(False)
        assert theme.colorPrimary == QColor("#0366D6")
        assert theme.colorTextPrimary == QColor("#24292E")
        assert theme.colorBgApp == QColor("#F5F6F8")
        assert theme.colorBgCard == QColor("#FFFFFF")
        assert theme.colorBorder == QColor("#E1E4E8")


class TestTypographyTokens:
    def test_font_size_caption(self, theme: ThemeController) -> None:
        assert theme.fontSizeCaption == 12

    def test_font_size_small(self, theme: ThemeController) -> None:
        assert theme.fontSizeSmall == 13

    def test_font_size_body(self, theme: ThemeController) -> None:
        assert theme.fontSizeBody == 14

    def test_font_size_heading(self, theme: ThemeController) -> None:
        assert theme.fontSizeHeading == 16

    def test_font_size_title(self, theme: ThemeController) -> None:
        assert theme.fontSizeTitle == 18

    def test_font_size_page_title(self, theme: ThemeController) -> None:
        assert theme.fontSizePageTitle == 22

    def test_font_size_min_default_12(self, theme: ThemeController) -> None:
        """默认最小字号下限为 12。"""
        assert theme.fontSizeMin == 12

    def test_font_size_caption_respects_min(self, theme: ThemeController) -> None:
        """caption 字号不应低于用户设定的最小字号。"""
        # 默认 base=14，min=12，caption=12（14-2=12，被 min 钳制为 12）
        assert theme.fontSizeCaption == 12
        # 设置 min_size=14，caption 应被钳制为 14
        theme.setFontConfig("", 14, False, 14)
        assert theme.fontSizeCaption == 14
        # 设置 base=20，min=10，caption=18（20-2=18，高于 min 不被钳制）
        theme.setFontConfig("", 20, False, 10)
        assert theme.fontSizeCaption == 18

    def test_font_size_small_respects_min(self, theme: ThemeController) -> None:
        """small 字号不应低于用户设定的最小字号。"""
        # 默认 base=14，min=12，small=13（14-1=13，高于 min 不被钳制）
        assert theme.fontSizeSmall == 13
        # 设置 min_size=14，small 应被钳制为 14
        theme.setFontConfig("", 14, False, 14)
        assert theme.fontSizeSmall == 14

    def test_set_font_config_propagates_min_size(self, theme: ThemeController) -> None:
        """setFontConfig 应同步最小字号并 emit themeChanged。"""
        emitted: list[None] = []
        theme.themeChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        theme.setFontConfig("", 14, False, 15)
        assert theme.fontSizeMin == 15
        assert len(emitted) == 1

    def test_set_font_config_noop_when_same(self, theme: ThemeController) -> None:
        """相同配置不应 emit 信号。"""
        theme.setFontConfig("", 14, False, 12)
        emitted: list[None] = []
        theme.themeChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        theme.setFontConfig("", 14, False, 12)
        assert len(emitted) == 0

    def test_font_family_returns_non_empty_string(self, theme: ThemeController) -> None:
        """fontFamily 应返回非空字符串（首个可用字体名）。"""
        family = theme.fontFamily
        assert isinstance(family, str)
        assert len(family) > 0

    def test_font_family_matches_detect_font_families_first(self, theme: ThemeController) -> None:
        """fontFamily 应与 detect_font_families() 首个字体一致。"""
        assert theme.fontFamily == detect_font_families()[0]


class TestDetectFontFamilies:
    """``detect_font_families`` 跨平台字体族检测。"""

    def test_returns_non_empty_tuple(self) -> None:
        families = detect_font_families()
        assert isinstance(families, tuple)
        assert len(families) >= 3  # 至少 3 个回退字体

    def test_windows_families(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "win32")
        families = detect_font_families()
        assert "Microsoft YaHei UI" in families
        assert "Segoe UI" in families
        assert families[0] == "Microsoft YaHei UI"

    def test_macos_families(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "darwin")
        families = detect_font_families()
        assert "PingFang SC" in families
        assert "Helvetica Neue" in families
        assert families[0] == "PingFang SC"

    def test_linux_families(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        families = detect_font_families()
        assert "Noto Sans CJK SC" in families
        assert "Roboto" in families
        assert families[0] == "Noto Sans CJK SC"

    def test_all_families_are_strings(self) -> None:
        families = detect_font_families()
        for f in families:
            assert isinstance(f, str)
            assert len(f) > 0


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


class TestRawDarkGetters:
    """暗色模式原始色值属性（``colorXxxDark`` getter）。

    这些 getter 需独立于 ``isDark`` 切换直接验证返回值，确保浅色模式下
    显式读取暗色令牌也得到正确结果。
    """

    def test_bg_hover_dark_raw(self, theme: ThemeController) -> None:
        assert theme.colorBgHoverDark == QColor("#2A2B3A")

    def test_bg_selected_dark_raw(self, theme: ThemeController) -> None:
        assert theme.colorBgSelectedDark == QColor("#2A2B3A")

    def test_border_dark_raw(self, theme: ThemeController) -> None:
        assert theme.colorBorderDark == QColor("#2E2F3A")

    def test_text_secondary_dark_raw(self, theme: ThemeController) -> None:
        assert theme.colorTextSecondaryDark == QColor("#A0A0B0")


class TestFontConfigGetters:
    """字体配置相关 getter（``fontFamily`` 用户覆盖分支、``fontBold``、``fontSizeBase``）。"""

    def test_font_family_returns_user_configured(self, theme: ThemeController) -> None:
        """setFontConfig 设置非空字体族后，fontFamily 应返回用户配置值。"""
        theme.setFontConfig("Custom Font", 14, False, 12)
        assert theme.fontFamily == "Custom Font"

    def test_font_bold_default_false(self, theme: ThemeController) -> None:
        """默认非加粗。"""
        assert theme.fontBold is False

    def test_font_bold_reflects_config(self, theme: ThemeController) -> None:
        """setFontConfig 设置加粗后 fontBold 应为 True。"""
        theme.setFontConfig("", 14, True, 12)
        assert theme.fontBold is True

    def test_font_size_base_default_14(self, theme: ThemeController) -> None:
        """默认基准字号 14。"""
        assert theme.fontSizeBase == 14

    def test_font_size_base_reflects_config(self, theme: ThemeController) -> None:
        """setFontConfig 设置字号后 fontSizeBase 应同步。"""
        theme.setFontConfig("", 18, False, 12)
        assert theme.fontSizeBase == 18


class TestIconPathGetters:
    """图标路径 getter（``iconsPrefix`` qrc 前缀）。"""

    def test_icons_prefix_is_qrc(self, theme: ThemeController) -> None:
        """iconsPrefix 应返回 qrc 资源前缀。"""
        assert theme.iconsPrefix == "qrc:/icons/"
