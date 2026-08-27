"""``AppController`` 与 ``fuscan.gui`` 包入口单元测试。

覆盖：

- ``AppController`` 构造时聚合 7 个 controller（theme/config/rules/whitelist/workspace/about/file_monitor）
- ``cleanup`` 调用 WorkspaceController.cleanup（资源释放）
- ``fuscan.gui.__init__`` 的 ``__getattr__`` 惰性导入 AppController 等类
- 错误属性名抛 ``AttributeError``
"""

from __future__ import annotations

import os

import pytest
from PySide2.QtGui import QGuiApplication

from fuscan.app import _apply_global_font
from fuscan.config import Config
from fuscan.gui import AppController
from fuscan.gui.controllers.about_controller import AboutController
from fuscan.gui.controllers.config_controller import ConfigController
from fuscan.gui.controllers.file_monitor_controller import FileMonitorController
from fuscan.gui.controllers.rules_controller import RulesController
from fuscan.gui.controllers.workspace_controller import WorkspaceController
from fuscan.gui.theme import ThemeController

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui


@pytest.fixture()
def controller() -> AppController:
    """构造 AppController 实例。"""
    return AppController()


class TestConstruction:
    """``AppController`` 构造时聚合 5 个 controller。"""

    def test_theme_property(self, controller: AppController) -> None:
        assert isinstance(controller.theme, ThemeController)

    def test_config_property(self, controller: AppController) -> None:
        assert isinstance(controller.config, ConfigController)

    def test_rules_property(self, controller: AppController) -> None:
        assert isinstance(controller.rules, RulesController)

    def test_workspace_property(self, controller: AppController) -> None:
        assert isinstance(controller.workspace, WorkspaceController)

    def test_about_property(self, controller: AppController) -> None:
        assert isinstance(controller.about, AboutController)

    def test_file_monitor_property(self, controller: AppController) -> None:
        assert isinstance(controller.file_monitor, FileMonitorController)

    def test_children_parented_to_controller(self, controller: AppController) -> None:
        """子 controller 的 parent 应为 AppController，确保生命周期管理。"""
        assert controller.theme.parent() is controller
        assert controller.config.parent() is controller
        assert controller.rules.parent() is controller
        assert controller.workspace.parent() is controller
        assert controller.about.parent() is controller
        assert controller.file_monitor.parent() is controller


class TestCleanup:
    """``cleanup`` 调用 WorkspaceController.cleanup。"""

    def test_cleanup_delegates_to_workspace(
        self,
        controller: AppController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cleanup 应委托给 WorkspaceController.cleanup。"""
        called = False

        def fake_cleanup() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(controller.workspace, "cleanup", fake_cleanup)
        controller.cleanup()
        assert called is True


class TestGuiPackageGetattr:
    """``fuscan.gui`` 包入口的 ``__getattr__`` 惰性导入。"""

    def test_getattr_app_controller(self) -> None:
        """``from fuscan.gui import AppController`` 应惰性导入类。"""
        import fuscan.gui as gui_pkg

        cls = gui_pkg.AppController
        assert cls is AppController

    def test_getattr_scan_controller(self) -> None:
        """``from fuscan.gui import ScanController`` 应惰性导入类。"""
        import fuscan.gui as gui_pkg
        from fuscan.gui.controllers.scan_controller import ScanController as Impl

        cls = gui_pkg.ScanController
        assert cls is Impl

    def test_getattr_config_controller(self) -> None:
        """``from fuscan.gui import ConfigController`` 应惰性导入类。"""
        import fuscan.gui as gui_pkg

        cls = gui_pkg.ConfigController
        assert cls is ConfigController

    def test_getattr_rules_controller(self) -> None:
        """``from fuscan.gui import RulesController`` 应惰性导入类。"""
        import fuscan.gui as gui_pkg

        cls = gui_pkg.RulesController
        assert cls is RulesController

    def test_getattr_about_controller(self) -> None:
        """``from fuscan.gui import AboutController`` 应惰性导入类。"""
        import fuscan.gui as gui_pkg

        cls = gui_pkg.AboutController
        assert cls is AboutController

    def test_getattr_theme_controller(self) -> None:
        """``from fuscan.gui import ThemeController`` 应惰性导入类。"""
        import fuscan.gui as gui_pkg

        cls = gui_pkg.ThemeController
        assert cls is ThemeController

    def test_getattr_result_list_model(self) -> None:
        """``from fuscan.gui import ResultListModel`` 应惰性导入类。"""
        import fuscan.gui as gui_pkg
        from fuscan.gui.models.result_model import ResultListModel as Impl

        cls = gui_pkg.ResultListModel
        assert cls is Impl

    def test_getattr_rule_list_model(self) -> None:
        """``from fuscan.gui import RuleListModel`` 应惰性导入类。"""
        import fuscan.gui as gui_pkg
        from fuscan.gui.models.rule_model import RuleListModel as Impl

        cls = gui_pkg.RuleListModel
        assert cls is Impl

    def test_getattr_extractor_list_model(self) -> None:
        """``from fuscan.gui import ExtractorListModel`` 应惰性导入类。"""
        import fuscan.gui as gui_pkg
        from fuscan.gui.models.extractor_model import ExtractorListModel as Impl

        cls = gui_pkg.ExtractorListModel
        assert cls is Impl

    def test_getattr_unknown_attribute_raises(self) -> None:
        """访问不存在的属性应抛 AttributeError。"""
        import fuscan.gui as gui_pkg

        with pytest.raises(AttributeError, match="nonexistent_attribute"):
            _ = gui_pkg.nonexistent_attribute  # type: ignore[attr-defined]

    def test_controllers_getattr_unknown_attribute_raises(self) -> None:
        """``fuscan.gui.controllers`` 模块访问不存在的属性应抛 AttributeError。"""
        import fuscan.gui.controllers as ctrl_pkg

        with pytest.raises(AttributeError, match="nonexistent_controller"):
            _ = ctrl_pkg.nonexistent_controller  # type: ignore[attr-defined]


class TestApplyGlobalFont:
    """``_apply_global_font`` 测试：用户配置覆盖与平台默认回退。

    覆盖 :func:`fuscan.app._apply_global_font` 的两个分支：
    ``font_family`` 非空时用指定字体，为空时回退到平台默认字体族列表。
    本测试不标记 ``gui_qml``，CI 上仍会运行（不同于 ``main`` smoke 测试）。
    """

    def test_user_font_family_applied(self) -> None:
        """用户配置 font_family 时使用指定字体族。"""
        from unittest.mock import patch

        app = QGuiApplication.instance() or QGuiApplication(["fuscan"])
        cfg = Config(font_family="Microsoft YaHei UI", font_size=16, font_bold=True)
        with patch("fuscan.config.load_config", return_value=cfg):
            _apply_global_font(app)
        font = app.font()
        assert font.family() == "Microsoft YaHei UI"
        assert font.pixelSize() == 16
        assert font.bold() is True

    def test_default_families_when_no_user_font(self) -> None:
        """font_family 为 None 时回退到平台默认字体族列表。"""
        from unittest.mock import patch

        app = QGuiApplication.instance() or QGuiApplication(["fuscan"])
        cfg = Config(font_family=None, font_size=14, font_bold=False)
        with (
            patch("fuscan.config.load_config", return_value=cfg),
            patch("fuscan.app.detect_font_families", return_value=("DefaultFont",)),
        ):
            _apply_global_font(app)
        font = app.font()
        assert font.pixelSize() == 14
        # family 应被设置（具体名取决于 QFont.setFamilies 选中的首个可用字体）
        assert font.family()


class TestApplyFontConfigToTheme:
    """``AppController._apply_font_config_to_theme`` 字体同步测试。"""

    def test_apply_font_config_sets_theme_font(self, controller: AppController) -> None:
        """应将 ConfigController 的字体配置同步到 ThemeController 与 QGuiApplication。"""
        controller._apply_font_config_to_theme()  # type: ignore[attr-defined]
        # ThemeController 应已收到字体配置（fontSizeMin 为最小字号约束）
        assert controller.theme.fontSizeMin > 0

    def test_apply_font_config_updates_app_font(self, controller: AppController) -> None:
        """QGuiApplication 存在时应同步全局字体。"""
        app = QGuiApplication.instance() or QGuiApplication(["fuscan"])
        controller._apply_font_config_to_theme()  # type: ignore[attr-defined]
        font = app.font()
        assert font.pixelSize() > 0
