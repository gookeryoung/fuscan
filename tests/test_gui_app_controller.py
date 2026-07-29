"""``AppController`` 与 ``fuscan.gui`` 包入口单元测试。

覆盖：

- ``AppController`` 构造时聚合 5 个 controller（theme/config/rules/workspace/about）
- ``register_to`` 将 controller 注册到 QQmlContext
- ``cleanup`` 调用 WorkspaceController.cleanup（资源释放）
- ``fuscan.gui.__init__`` 的 ``__getattr__`` 惰性导入 launch / AppController
- 错误属性名抛 ``AttributeError``
"""

from __future__ import annotations

import os

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from PySide2.QtCore import QObject
    from PySide2.QtGui import QGuiApplication

    from fuscan.config import Config
    from fuscan.gui import AppController
    from fuscan.gui.app import _apply_global_font
    from fuscan.gui.controllers.about_controller import AboutController
    from fuscan.gui.controllers.config_controller import ConfigController
    from fuscan.gui.controllers.rules_controller import RulesController
    from fuscan.gui.controllers.workspace_controller import WorkspaceController
    from fuscan.gui.theme import ThemeController

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过 AppController 测试", allow_module_level=True)


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

    def test_children_parented_to_controller(self, controller: AppController) -> None:
        """子 controller 的 parent 应为 AppController，确保生命周期管理。"""
        assert controller.theme.parent() is controller
        assert controller.config.parent() is controller
        assert controller.rules.parent() is controller
        assert controller.workspace.parent() is controller
        assert controller.about.parent() is controller


class TestRegisterTo:
    """``register_to`` 将 controller 注册到 QQmlContext。"""

    def test_register_to_sets_all_context_properties(
        self,
        controller: AppController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """register_to 应将 6 个 controller 全部注册到 QML context。"""
        registered: dict[str, QObject] = {}

        def fake_set_context_property(name: str, obj: QObject) -> None:
            registered[name] = obj

        # QQmlApplicationEngine.rootContext() 返回的 QQmlContext 在测试环境
        # 难以构造，用 duck typing 传入带 setContextProperty 的 stub。
        # 不创建真实 QQmlApplicationEngine —— Windows 上 PySide2 + QML 会触发
        # STATUS_STACK_BUFFER_OVERRUN 崩溃（参考 iter-95 文档已知问题）。
        class FakeContext:
            def setContextProperty(self, name: str, obj: QObject) -> None:
                fake_set_context_property(name, obj)

        controller.register_to(FakeContext())

        assert set(registered.keys()) == {
            "Theme",
            "ConfigController",
            "RulesController",
            "WorkspaceController",
            "WhitelistController",
            "AboutController",
        }
        assert registered["Theme"] is controller.theme
        assert registered["ConfigController"] is controller.config
        assert registered["RulesController"] is controller.rules
        assert registered["WorkspaceController"] is controller.workspace
        assert registered["WhitelistController"] is controller.whitelist
        assert registered["AboutController"] is controller.about


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

    def test_getattr_launch(self) -> None:
        """``from fuscan.gui import launch`` 应惰性导入 launch 函数。"""
        # 强制重新导入以触发 __getattr__
        import fuscan.gui as gui_pkg

        launch = gui_pkg.launch
        assert callable(launch)
        assert launch.__name__ == "launch"

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


class TestGuiMainModule:
    """``fuscan.gui.__main__`` 模块入口测试。

    覆盖 ``python -m fuscan.gui`` 入口的 import 段（PySide2/PySide6 探测）
    与 ``launch`` 引用，不实际启动 GUI（避免阻塞测试进程）。
    """

    def test_main_module_imports_launch(self) -> None:
        """``fuscan.gui.__main__`` 模块应能成功导入并暴露 ``launch`` 引用。"""
        import fuscan.gui.__main__ as main_mod

        # launch 应为模块级可调用对象
        assert callable(main_mod.launch)
        assert main_mod.launch.__name__ == "launch"

    def test_main_module_pyside_detection(self) -> None:
        """模块加载时应成功探测到 PySide2 或 PySide6 之一。"""
        # 间接验证：若 PySide 探测失败，模块 import 时即抛 ImportError，
        # test_main_module_imports_launch 无法通过。这里再断言 PySide2 可用。
        import PySide2

        assert PySide2 is not None


class TestApplyGlobalFont:
    """``_apply_global_font`` 测试：用户配置覆盖与平台默认回退。

    覆盖 :func:`fuscan.gui.app._apply_global_font` 的两个分支：
    ``font_family`` 非空时用指定字体，为空时回退到平台默认字体族列表。
    本测试不标记 ``gui_qml``，CI 上仍会运行（不同于 ``launch`` smoke 测试）。
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
        with patch("fuscan.config.load_config", return_value=cfg), patch(
            "fuscan.gui.theme.detect_font_families", return_value=("DefaultFont",)
        ):
            _apply_global_font(app)
        font = app.font()
        assert font.pixelSize() == 14
        # family 应被设置（具体名取决于 QFont.setFamilies 选中的首个可用字体）
        assert font.family()
