"""``RulesController`` 单元测试。

验证规则文件管理（加载/上移/下移/移除）、内置规则勾选、规则集合并与
``rulesetChanged`` 信号触发。使用 ``tmp_path`` 隔离配置与规则文件。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from fuscan.config import Config  # noqa: F401
    from fuscan.gui.qml.config_controller import ConfigController
    from fuscan.gui.qml.rule_model import RuleListModel
    from fuscan.gui.qml.rules_controller import RulesController

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过规则控制器测试", allow_module_level=True)


def _write_rules_file(tmp_path: Path, name: str, pattern: str = "password") -> Path:
    """写入一个简单的规则文件。"""
    path = tmp_path / name
    path.write_text(
        f"""version: "1.0"
rules:
  - name: "敏感内容"
    severity: critical
    match:
      target: content
      mode: contains
      pattern: "{pattern}"
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将 ~/.fuscan 重定向到 tmp_path，避免污染用户配置。"""
    fake_home = tmp_path / "fuscan_home"
    fake_home.mkdir()
    config_dir = fake_home / ".fuscan"
    config_dir.mkdir()
    monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_dir / "config.yaml")
    return config_dir


@pytest.fixture()
def config_controller(config_dir: Path) -> ConfigController:
    return ConfigController()


@pytest.fixture()
def rules_file(tmp_path: Path) -> Path:
    return _write_rules_file(tmp_path, "custom.yaml")


@pytest.fixture()
def controller_with_file(config_controller: ConfigController, rules_file: Path) -> RulesController:
    """构造一个已加载自定义规则文件的 RulesController。"""
    config_controller.config.rules_paths = [str(rules_file)]
    config_controller.save()
    # 重新构造 RulesController 以加载规则
    return RulesController(config_controller)


class TestConstruction:
    def test_default_no_ruleset(self, config_controller: ConfigController) -> None:
        """无规则文件且禁用内置规则时 ruleset 为 None。"""
        config_controller.config.use_builtin = False
        controller = RulesController(config_controller)
        assert controller.ruleset is None
        assert controller.ruleCount == 0

    def test_default_use_builtin_loads_builtin_ruleset(self, config_controller: ConfigController) -> None:
        """默认启用内置规则时应加载内置规则集。"""
        controller = RulesController(config_controller)
        assert controller.ruleset is not None
        assert controller.ruleCount > 0

    def test_rule_model_exposed(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        assert isinstance(controller.ruleModel, RuleListModel)


class TestRulesFileList:
    def test_empty_rules_file_model(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        assert controller.rulesFileModel == []

    def test_rules_file_model_after_load(self, controller_with_file: RulesController, rules_file: Path) -> None:
        model = controller_with_file.rulesFileModel
        assert len(model) == 1
        assert model[0]["fileName"] == rules_file.name
        assert model[0]["path"] == str(rules_file)

    def test_selected_file_index_default_negative(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        assert controller.selectedFileIndex == -1

    def test_set_selected_file_index_emits_signal(self, controller_with_file: RulesController) -> None:
        emitted: list[None] = []
        controller_with_file.selectionChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller_with_file.setSelectedFileIndex(0)
        assert controller_with_file.selectedFileIndex == 0
        assert len(emitted) == 1

    def test_set_selected_file_index_noop_when_same(self, controller_with_file: RulesController) -> None:
        controller_with_file.setSelectedFileIndex(0)
        emitted: list[None] = []
        controller_with_file.selectionChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller_with_file.setSelectedFileIndex(0)
        assert len(emitted) == 0


class TestCanMove:
    def test_can_move_up_default_false(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        assert controller.canMoveUp is False

    def test_can_move_up_false_when_first(self, controller_with_file: RulesController) -> None:
        controller_with_file.setSelectedFileIndex(0)
        assert controller_with_file.canMoveUp is False

    def test_can_move_down_false_when_empty(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        assert controller.canMoveDown is False

    def test_can_move_down_false_when_last(self, controller_with_file: RulesController) -> None:
        controller_with_file.setSelectedFileIndex(0)
        assert controller_with_file.canMoveDown is False

    def test_can_remove_default_false(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        assert controller.canRemove is False

    def test_can_remove_true_when_valid_index(self, controller_with_file: RulesController) -> None:
        controller_with_file.setSelectedFileIndex(0)
        assert controller_with_file.canRemove is True


class TestMoveOperations:
    def test_move_up_swaps_order(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        f1 = _write_rules_file(tmp_path, "r1.yaml", "password")
        f2 = _write_rules_file(tmp_path, "r2.yaml", "secret")
        config_controller.config.rules_paths = [str(f1), str(f2)]
        config_controller.save()
        controller = RulesController(config_controller)

        controller.setSelectedFileIndex(1)
        assert controller.canMoveUp is True
        controller.moveUp()

        assert controller.selectedFileIndex == 0
        assert controller.rulesFileModel[0]["fileName"] == "r2.yaml"

    def test_move_down_swaps_order(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        f1 = _write_rules_file(tmp_path, "r1.yaml", "password")
        f2 = _write_rules_file(tmp_path, "r2.yaml", "secret")
        config_controller.config.rules_paths = [str(f1), str(f2)]
        config_controller.save()
        controller = RulesController(config_controller)

        controller.setSelectedFileIndex(0)
        assert controller.canMoveDown is True
        controller.moveDown()

        assert controller.selectedFileIndex == 1
        assert controller.rulesFileModel[0]["fileName"] == "r2.yaml"

    def test_move_up_noop_when_first(self, controller_with_file: RulesController) -> None:
        controller_with_file.setSelectedFileIndex(0)
        controller_with_file.moveUp()
        assert controller_with_file.selectedFileIndex == 0

    def test_move_down_noop_when_last(self, controller_with_file: RulesController) -> None:
        controller_with_file.setSelectedFileIndex(0)
        controller_with_file.moveDown()
        assert controller_with_file.selectedFileIndex == 0


class TestRemoveSelected:
    def test_remove_selected_clears_entry(self, controller_with_file: RulesController, rules_file: Path) -> None:
        controller_with_file.setSelectedFileIndex(0)
        controller_with_file.removeSelected()
        assert controller_with_file.rulesFileModel == []
        assert controller_with_file.selectedFileIndex == -1

    def test_remove_selected_noop_when_invalid(self, controller_with_file: RulesController) -> None:
        controller_with_file.setSelectedFileIndex(-1)
        controller_with_file.removeSelected()
        # 列表未变
        assert len(controller_with_file.rulesFileModel) == 1


class TestUseBuiltin:
    def test_use_builtin_default_true(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        assert controller.useBuiltin is True

    def test_set_use_builtin_false_emits_signal(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        emitted: list[None] = []
        controller.useBuiltinChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setUseBuiltin(False)
        assert controller.useBuiltin is False
        assert len(emitted) == 1

    def test_set_use_builtin_noop_when_same(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        emitted: list[None] = []
        controller.useBuiltinChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setUseBuiltin(True)
        assert len(emitted) == 0

    def test_set_use_builtin_triggers_ruleset_reload(self, config_controller: ConfigController) -> None:
        """关闭内置规则后 ruleset 应变为 None（无自定义规则时）。"""
        controller = RulesController(config_controller)
        assert controller.ruleset is not None
        controller.setUseBuiltin(False)
        assert controller.ruleset is None
        assert controller.ruleCount == 0


class TestRulesetChangedSignal:
    def test_move_up_emits_ruleset_changed(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        f1 = _write_rules_file(tmp_path, "r1.yaml", "password")
        f2 = _write_rules_file(tmp_path, "r2.yaml", "secret")
        config_controller.config.rules_paths = [str(f1), str(f2)]
        config_controller.save()
        controller = RulesController(config_controller)

        emitted: list[None] = []
        controller.rulesetChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setSelectedFileIndex(1)
        emitted.clear()
        controller.moveUp()
        assert len(emitted) == 1

    def test_remove_selected_emits_ruleset_changed(self, controller_with_file: RulesController) -> None:
        emitted: list[None] = []
        controller_with_file.rulesetChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller_with_file.setSelectedFileIndex(0)
        emitted.clear()
        controller_with_file.removeSelected()
        assert len(emitted) == 1
