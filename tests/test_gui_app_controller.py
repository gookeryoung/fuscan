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

    from fuscan.gui.qml import AppController
    from fuscan.gui.qml.controllers.about_controller import AboutController
    from fuscan.gui.qml.controllers.config_controller import ConfigController
    from fuscan.gui.qml.controllers.rules_controller import RulesController
    from fuscan.gui.qml.controllers.workspace_controller import WorkspaceController
    from fuscan.gui.qml.theme import ThemeController

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
        """register_to 应将 5 个 controller 全部注册到 QML context。"""
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
            "AboutController",
        }
        assert registered["Theme"] is controller.theme
        assert registered["ConfigController"] is controller.config
        assert registered["RulesController"] is controller.rules
        assert registered["WorkspaceController"] is controller.workspace
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

    def test_getattr_unknown_attribute_raises(self) -> None:
        """访问不存在的属性应抛 AttributeError。"""
        import fuscan.gui as gui_pkg

        with pytest.raises(AttributeError, match="nonexistent_attribute"):
            _ = gui_pkg.nonexistent_attribute  # type: ignore[attr-defined]
