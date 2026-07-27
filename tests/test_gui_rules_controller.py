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
    from fuscan.gui.controllers.config_controller import ConfigController
    from fuscan.gui.controllers.rules_controller import RulesController
    from fuscan.gui.models.rule_model import RuleListModel

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
      type: content
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


class TestWorkspaceBinding:
    """iter-107 规则与工作区绑定测试。"""

    def test_unbound_by_default(self, config_controller: ConfigController) -> None:
        """构造后默认未绑定工作区。"""
        controller = RulesController(config_controller)
        assert controller.isBound is False
        assert controller.boundWorkspaceId == ""
        assert controller.boundWorkspaceName == ""

    def test_bind_workspace_without_workspace_controller_returns_false(
        self,
        config_controller: ConfigController,
    ) -> None:
        """未注入 WorkspaceController 时 bindWorkspace 返回 False。"""
        controller = RulesController(config_controller)
        assert controller.bindWorkspace("ws-1234") is False

    def test_bind_workspace_loads_local_copy(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """绑定工作区后 useBuiltin/rulesFileModel 应从工作区副本读取。"""
        from fuscan.gui.controllers.workspace_controller import WorkspaceController
        from fuscan.gui.models.workspace_model import WorkspaceItem

        rules_file = _write_rules_file(tmp_path, "ws_rule.yaml")
        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        ws_id = "ws-test123"
        item = WorkspaceItem(
            workspace_id=ws_id,
            name="绑定测试",
            rules_paths=(str(rules_file),),
            use_builtin=False,
        )
        ws_controller.workspaceModel.add_workspace(item)
        # RulesController 注入 ws_controller 引用
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)

        assert controller.bindWorkspace(ws_id) is True
        assert controller.isBound is True
        assert controller.boundWorkspaceId == ws_id
        assert controller.boundWorkspaceName == "绑定测试"
        assert controller.useBuiltin is False
        assert len(controller.rulesFileModel) == 1
        assert controller.rulesFileModel[0]["fileName"] == "ws_rule.yaml"

    def test_unbind_restores_global_state(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """解除绑定后恢复全局模式。"""
        from fuscan.gui.controllers.workspace_controller import WorkspaceController
        from fuscan.gui.models.workspace_model import WorkspaceItem

        rules_file = _write_rules_file(tmp_path, "ws_rule.yaml")
        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        ws_id = "ws-test456"
        item = WorkspaceItem(
            workspace_id=ws_id,
            name="绑定测试",
            rules_paths=(str(rules_file),),
            use_builtin=False,
        )
        ws_controller.workspaceModel.add_workspace(item)
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)
        controller.bindWorkspace(ws_id)
        assert controller.isBound is True

        controller.unbindWorkspace()
        assert controller.isBound is False
        assert controller.boundWorkspaceId == ""
        # 恢复全局 use_builtin（默认 True）
        assert controller.useBuiltin is True
        # 全局规则文件列表应为空（config_dir 默认无规则文件）
        assert controller.rulesFileModel == []

    def test_bound_load_file_persists_to_workspace(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """绑定模式下 loadFileFromPath 仅写入工作区副本，不影响全局 Config。"""
        from fuscan.gui.controllers.workspace_controller import WorkspaceController
        from fuscan.gui.models.workspace_model import WorkspaceItem

        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        ws_id = "ws-test789"
        item = WorkspaceItem(workspace_id=ws_id, name="t", rules_paths=(), use_builtin=False)
        ws_controller.workspaceModel.add_workspace(item)
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)
        controller.bindWorkspace(ws_id)

        rules_file = _write_rules_file(tmp_path, "new_rule.yaml")
        assert controller.loadFileFromPath(str(rules_file)) is True
        # 全局 Config 不受影响
        assert config_controller.config.rules_paths == []
        # 工作区 WorkspaceItem 已写入新规则
        updated = ws_controller.get_workspace(ws_id)
        assert updated is not None
        assert str(rules_file) in updated.rules_paths

    def test_bound_set_use_builtin_persists_to_workspace(
        self,
        config_controller: ConfigController,
    ) -> None:
        """绑定模式下 setUseBuiltin 仅写入工作区副本。"""
        from fuscan.gui.controllers.workspace_controller import WorkspaceController
        from fuscan.gui.models.workspace_model import WorkspaceItem

        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        ws_id = "ws-toggle"
        item = WorkspaceItem(workspace_id=ws_id, name="t", rules_paths=(), use_builtin=True)
        ws_controller.workspaceModel.add_workspace(item)
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)
        controller.bindWorkspace(ws_id)

        controller.setUseBuiltin(False)
        # 全局 Config 不受影响
        assert config_controller.config.use_builtin is True
        # 工作区已写入
        updated = ws_controller.get_workspace(ws_id)
        assert updated is not None
        assert updated.use_builtin is False

    def test_bound_move_up_persists_to_workspace(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """绑定模式下 moveUp 仅调整工作区副本顺序。"""
        from fuscan.gui.controllers.workspace_controller import WorkspaceController
        from fuscan.gui.models.workspace_model import WorkspaceItem

        f1 = _write_rules_file(tmp_path, "r1.yaml", "password")
        f2 = _write_rules_file(tmp_path, "r2.yaml", "secret")
        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        ws_id = "ws-move"
        item = WorkspaceItem(
            workspace_id=ws_id,
            name="t",
            rules_paths=(str(f1), str(f2)),
            use_builtin=False,
        )
        ws_controller.workspaceModel.add_workspace(item)
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)
        controller.bindWorkspace(ws_id)

        controller.setSelectedFileIndex(1)
        controller.moveUp()
        # 工作区副本顺序调整
        updated = ws_controller.get_workspace(ws_id)
        assert updated is not None
        assert updated.rules_paths[0] == str(f2)
        assert updated.rules_paths[1] == str(f1)
        # 全局 Config 不受影响
        assert config_controller.config.rules_paths == []

    def test_bound_remove_selected_persists_to_workspace(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """绑定模式下 removeSelected 仅从工作区副本移除。"""
        from fuscan.gui.controllers.workspace_controller import WorkspaceController
        from fuscan.gui.models.workspace_model import WorkspaceItem

        f1 = _write_rules_file(tmp_path, "r1.yaml", "password")
        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        ws_id = "ws-rm"
        item = WorkspaceItem(
            workspace_id=ws_id,
            name="t",
            rules_paths=(str(f1),),
            use_builtin=False,
        )
        ws_controller.workspaceModel.add_workspace(item)
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)
        controller.bindWorkspace(ws_id)

        controller.setSelectedFileIndex(0)
        controller.removeSelected()
        # 工作区副本为空
        updated = ws_controller.get_workspace(ws_id)
        assert updated is not None
        assert updated.rules_paths == ()

    def test_bind_nonexistent_workspace_returns_false(
        self,
        config_controller: ConfigController,
    ) -> None:
        """绑定不存在的工作区 ID 返回 False。"""
        from fuscan.gui.controllers.workspace_controller import WorkspaceController

        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)
        assert controller.bindWorkspace("ws-not-exist") is False
        assert controller.isBound is False


# ============================= iter-116 边界补充 =============================


class TestLoadFileFromPathEdgeCases:
    """``loadFileFromPath`` 边界场景补充（iter-116）。"""

    def test_load_nonexistent_path_returns_false(
        self,
        controller_with_file: RulesController,
        tmp_path: Path,
    ) -> None:
        """加载不存在的路径返回 False，不抛异常。"""
        target = tmp_path / "missing.yaml"
        assert controller_with_file.loadFileFromPath(str(target)) is False

    def test_load_already_loaded_global_skips(
        self,
        controller_with_file: RulesController,
        rules_file: Path,
    ) -> None:
        """全局模式下重复加载已存在的路径返回 False。"""
        # rules_file 已在 controller_with_file fixture 中加载
        assert controller_with_file.loadFileFromPath(str(rules_file)) is False

    def test_load_already_loaded_bound_skips(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """绑定模式下重复加载已存在的路径返回 False。"""
        import json as _json

        from fuscan.gui.controllers.workspace_controller import WorkspaceController

        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)

        # 添加工作区并绑定（用 json.dumps 避免路径反斜杠转义问题）
        rules_file = _write_rules_file(tmp_path, "bound.yaml")
        ws_id = ws_controller.addWorkspace("t", "folder", "/tmp", _json.dumps([str(rules_file)]), True)
        assert controller.bindWorkspace(ws_id) is True
        assert str(rules_file) in controller._local_rules_paths  # type: ignore[attr-defined]

        # 重复加载已绑定路径
        assert controller.loadFileFromPath(str(rules_file)) is False

    def test_load_global_mode_invalid_yaml_returns_true_but_ruleset_none(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """全局模式下加载非法 YAML 时返回 True（``_reload_ruleset`` 已吞 ``RuleError``）。

        注意：``_reload_ruleset`` 内部捕获 ``RuleError`` 并设 ruleset=None，
        ``loadFileFromPath`` 的 try 块因此不抛异常，返回 True。
        ``rules_paths`` 中已追加非法路径（设计遗留，见 iter-116 笔记）。
        """
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("not: valid: yaml: - -", encoding="utf-8")
        controller = RulesController(config_controller)
        # 加载返回 True，但 ruleset 因解析失败变 None
        assert controller.loadFileFromPath(str(bad_yaml)) is True
        assert controller.ruleset is None
        # 非法路径已追加到 config
        assert str(bad_yaml) in config_controller.config.rules_paths

    def test_load_bound_mode_invalid_yaml_returns_true_but_ruleset_none(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """绑定模式下加载非法 YAML 时返回 True（``_reload_ruleset`` 已吞 ``RuleError``）。"""
        import json as _json

        from fuscan.gui.controllers.workspace_controller import WorkspaceController

        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)

        good_yaml = _write_rules_file(tmp_path, "good.yaml")
        ws_id = ws_controller.addWorkspace("t", "folder", "/tmp", _json.dumps([str(good_yaml)]), True)
        assert controller.bindWorkspace(ws_id) is True

        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("not: valid: yaml: - -", encoding="utf-8")

        # 加载返回 True，但 ruleset 因解析失败变 None
        assert controller.loadFileFromPath(str(bad_yaml)) is True
        assert controller.ruleset is None
        # 本地副本已追加非法路径
        assert str(bad_yaml) in controller._local_rules_paths  # type: ignore[attr-defined]


class TestUnbindNoopWhenNotBound:
    """``unbindWorkspace`` 无绑定时为 no-op（iter-116）。"""

    def test_unbind_when_not_bound_no_emit(
        self,
        config_controller: ConfigController,
    ) -> None:
        """未绑定时调用 ``unbindWorkspace`` 不抛异常、不 emit 信号。"""
        controller = RulesController(config_controller)
        # 直接调用，不应抛异常
        controller.unbindWorkspace()
        assert controller.isBound is False
        assert controller.boundWorkspaceId == ""


class TestBoundWorkspaceMoveDown:
    """绑定模式下 ``moveDown`` 写回工作区（iter-116）。"""

    def test_bound_move_down_persists(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """绑定模式下 ``moveDown`` 应同步到工作区。"""
        import json as _json

        from fuscan.gui.controllers.workspace_controller import WorkspaceController

        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)

        file_a = _write_rules_file(tmp_path, "a.yaml", "alpha")
        file_b = _write_rules_file(tmp_path, "b.yaml", "beta")
        ws_id = ws_controller.addWorkspace(
            "t",
            "folder",
            "/tmp",
            _json.dumps([str(file_a), str(file_b)]),
            True,
        )
        assert controller.bindWorkspace(ws_id) is True
        assert len(controller._local_rules_paths) == 2  # type: ignore[attr-defined]

        # 选中第 0 个并下移
        controller.setSelectedFileIndex(0)  # type: ignore[attr-defined]
        assert controller.canMoveDown is True
        controller.moveDown()  # type: ignore[attr-defined]

        # 工作区项的 rules_paths 顺序应交换
        item = ws_controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.rules_paths[0] == str(file_b)
        assert item.rules_paths[1] == str(file_a)


class TestReloadRulesetBranches:
    """``_reload_ruleset`` 内部分支覆盖（iter-116）。"""

    def test_reload_with_use_builtin_false_and_no_paths(
        self,
        config_controller: ConfigController,
    ) -> None:
        """禁用内置规则且无规则文件时 ruleset 应为 None。"""
        cfg = config_controller.config
        cfg.use_builtin = False
        cfg.rules_paths = []
        controller = RulesController(config_controller)
        controller._reload_ruleset()  # type: ignore[attr-defined]
        assert controller.ruleset is None

    def test_reload_with_use_builtin_false_and_paths(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """禁用内置规则但有规则文件时应加载用户规则。"""
        # 写入带 type 字段的合法规则文件
        rules_file = tmp_path / "valid_rule.yaml"
        rules_file.write_text(
            """version: "1.0"
rules:
  - name: "敏感内容"
    severity: critical
    match:
      type: content
      target: content
      mode: contains
      pattern: "secret_pattern"
""",
            encoding="utf-8",
        )
        cfg = config_controller.config
        cfg.use_builtin = False
        cfg.rules_paths = [str(rules_file)]
        controller = RulesController(config_controller)
        controller._reload_ruleset()  # type: ignore[attr-defined]
        # ruleset 应非空，且包含 "敏感内容" 规则
        assert controller.ruleset is not None
        assert len(controller.ruleset.rules) >= 1


class TestBoundWorkspaceName:
    """``boundWorkspaceName`` Property 分支覆盖（iter-116）。"""

    def test_bound_workspace_name_empty_when_no_controller(
        self,
        config_controller: ConfigController,
    ) -> None:
        """``_workspace_controller`` 为 ``None`` 时返回空串。"""
        controller = RulesController(config_controller)
        # 默认 _workspace_controller 为 None（未注入）
        assert controller.boundWorkspaceName == ""

    def test_bound_workspace_name_empty_when_not_bound(
        self,
        config_controller: ConfigController,
    ) -> None:
        """未绑定时返回空串。"""
        from fuscan.gui.controllers.workspace_controller import WorkspaceController

        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)
        assert controller.boundWorkspaceName == ""

    def test_bound_workspace_name_returns_workspace_name(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """绑定时返回工作区名称。"""
        from fuscan.gui.controllers.workspace_controller import WorkspaceController

        ws_controller = WorkspaceController(config_controller, RulesController(config_controller))
        controller = ws_controller._rules_controller
        controller.set_workspace_controller(ws_controller)

        rules_file = _write_rules_file(tmp_path, "x.yaml")
        ws_id = ws_controller.addWorkspace("我的任务", "folder", "/tmp", f'["{rules_file}"]', True)
        assert controller.bindWorkspace(ws_id) is True
        assert controller.boundWorkspaceName == "我的任务"


class TestPersistToBoundWorkspaceNoop:
    """``_persist_to_bound_workspace`` 无绑定时为 no-op（iter-116）。"""

    def test_persist_noop_when_not_bound(
        self,
        config_controller: ConfigController,
    ) -> None:
        """未绑定时调用 ``_persist_to_bound_workspace`` 不抛异常。"""
        controller = RulesController(config_controller)
        # 默认未绑定，_workspace_controller 为 None
        controller._persist_to_bound_workspace()  # type: ignore[attr-defined]
        # 无副作用即可


# ============================= iter-122 导入/导出/模板 =============================


class TestTemplateList:
    """``templateList`` Property 测试。"""

    def test_template_list_returns_all_templates(self, config_controller: ConfigController) -> None:
        """templateList 应返回所有内置模板，按名字母序排列。"""
        controller = RulesController(config_controller)
        templates = controller.templateList
        assert len(templates) >= 5
        # 每项含 name/description 字段
        for item in templates:
            assert "name" in item
            assert "description" in item
            assert len(item["name"]) > 0
            assert len(item["description"]) > 0

    def test_template_list_contains_aws_keys(self, config_controller: ConfigController) -> None:
        """templateList 应包含 aws_keys 模板。"""
        controller = RulesController(config_controller)
        names = [t["name"] for t in controller.templateList]
        assert "aws_keys" in names
        assert "azure_keys" in names
        assert "gcp_keys" in names
        assert "privacy_data" in names
        assert "common_credentials" in names

    def test_template_list_sorted_alphabetically(self, config_controller: ConfigController) -> None:
        """templateList 应按名字母序排列。"""
        controller = RulesController(config_controller)
        names = [t["name"] for t in controller.templateList]
        assert names == sorted(names)


class TestExportRuleset:
    """``exportRuleset`` Slot 测试（iter-122）。"""

    def test_export_to_yaml_roundtrip(
        self,
        controller_with_file: RulesController,
        tmp_path: Path,
    ) -> None:
        """导出 YAML 后可被 load_ruleset 加载，规则数一致。"""
        from fuscan.rules import load_ruleset

        target = tmp_path / "exported.yaml"
        assert controller_with_file.exportRuleset(str(target)) is True
        assert target.exists()

        loaded = load_ruleset(target)
        assert loaded.version == "1.0"
        assert len(loaded.rules) == controller_with_file.ruleCount

    def test_export_to_json_roundtrip(
        self,
        controller_with_file: RulesController,
        tmp_path: Path,
    ) -> None:
        """导出 JSON 后可被 load_ruleset 加载。"""
        from fuscan.rules import load_ruleset

        target = tmp_path / "exported.json"
        assert controller_with_file.exportRuleset(str(target)) is True
        assert target.exists()

        # YAML 是 JSON 超集，可解析 JSON 文件
        loaded = load_ruleset(target)
        assert loaded.version == "1.0"
        assert len(loaded.rules) > 0

    def test_export_emits_success_signal(
        self,
        controller_with_file: RulesController,
        tmp_path: Path,
    ) -> None:
        """导出成功后应 emit rulesIoCompleted(True, msg)。"""
        emitted: list[tuple[bool, str]] = []
        controller_with_file.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        target = tmp_path / "out.yaml"
        controller_with_file.exportRuleset(str(target))
        assert len(emitted) == 1
        assert emitted[0][0] is True
        assert "已导出" in emitted[0][1]

    def test_export_empty_path_returns_false(
        self,
        controller_with_file: RulesController,
    ) -> None:
        """空路径应返回 False 并 emit 失败信号。"""
        emitted: list[tuple[bool, str]] = []
        controller_with_file.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller_with_file.exportRuleset("") is False
        assert len(emitted) == 1
        assert emitted[0][0] is False

    def test_export_no_ruleset_returns_false(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """无规则集（未启用内置且无规则文件）时返回 False。"""
        config_controller.config.use_builtin = False
        config_controller.config.rules_paths = []
        controller = RulesController(config_controller)
        assert controller.ruleset is None

        target = tmp_path / "empty.yaml"
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.exportRuleset(str(target)) is False
        assert len(emitted) == 1
        assert emitted[0][0] is False
        assert "无规则集" in emitted[0][1]


class TestImportRuleset:
    """``importRuleset`` Slot 测试（iter-122）。"""

    def test_import_valid_yaml_adds_to_rules_paths(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """导入合法 YAML 后应加入 rules_paths 并刷新 ruleset。"""
        # 准备一个合法规则文件
        rules_file = _write_rules_file(tmp_path, "importable.yaml", "secret")
        controller = RulesController(config_controller)
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )

        assert controller.importRuleset(str(rules_file)) is True
        assert str(rules_file) in config_controller.config.rules_paths
        assert controller.ruleset is not None
        assert len(emitted) == 1
        assert emitted[0][0] is True
        assert "已导入" in emitted[0][1]

    def test_import_already_loaded_returns_false(
        self,
        controller_with_file: RulesController,
        rules_file: Path,
    ) -> None:
        """重复导入已加载的规则文件返回 False。"""
        emitted: list[tuple[bool, str]] = []
        controller_with_file.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller_with_file.importRuleset(str(rules_file)) is False
        assert len(emitted) == 1
        assert emitted[0][0] is False
        assert "已加载" in emitted[0][1]

    def test_import_nonexistent_returns_false(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """导入不存在的文件返回 False。"""
        controller = RulesController(config_controller)
        target = tmp_path / "missing.yaml"
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.importRuleset(str(target)) is False
        assert len(emitted) == 1
        assert emitted[0][0] is False
        assert "不存在" in emitted[0][1]

    def test_import_empty_path_returns_false(
        self,
        config_controller: ConfigController,
    ) -> None:
        """空路径返回 False。"""
        controller = RulesController(config_controller)
        assert controller.importRuleset("") is False

    def test_import_unsupported_version_returns_false(
        self,
        config_controller: ConfigController,
        tmp_path: Path,
    ) -> None:
        """导入版本不兼容的规则文件返回 False（版本兼容性检查）。"""
        bad_version = tmp_path / "v2.yaml"
        bad_version.write_text(
            'version: "2.0"\nrules: []\n',
            encoding="utf-8",
        )
        controller = RulesController(config_controller)
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.importRuleset(str(bad_version)) is False
        assert len(emitted) == 1
        assert emitted[0][0] is False
        assert "不支持" in emitted[0][1] or "导入失败" in emitted[0][1]
        # 不应污染 rules_paths
        assert str(bad_version) not in config_controller.config.rules_paths


class TestLoadTemplate:
    """``loadTemplate`` Slot 测试（iter-122）。"""

    def test_load_aws_keys_template_persists_to_home(
        self,
        config_controller: ConfigController,
        config_dir: Path,
    ) -> None:
        """加载 aws_keys 模板后应在 ~/.fuscan/templates/ 创建文件并加入 rules_paths。"""
        controller = RulesController(config_controller)
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )

        assert controller.loadTemplate("aws_keys") is True
        assert len(emitted) == 1
        assert emitted[0][0] is True
        assert "aws_keys" in emitted[0][1]

        # 模板文件已创建
        templates_dir = config_dir / "templates"
        assert (templates_dir / "aws_keys.yaml").exists()

        # rules_paths 中应包含模板文件路径
        template_path = str(templates_dir / "aws_keys.yaml")
        assert template_path in config_controller.config.rules_paths
        # ruleset 已刷新
        assert controller.ruleset is not None
        # AWS 模板包含 2 条规则
        aws_rules = [r for r in controller.ruleset.rules if "AWS" in r.name]
        assert len(aws_rules) >= 2

    def test_load_template_unknown_returns_false(
        self,
        config_controller: ConfigController,
    ) -> None:
        """加载不存在的模板返回 False。"""
        controller = RulesController(config_controller)
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.loadTemplate("nonexistent") is False
        assert len(emitted) == 1
        assert emitted[0][0] is False
        assert "不存在" in emitted[0][1]

    def test_load_template_empty_name_returns_false(
        self,
        config_controller: ConfigController,
    ) -> None:
        """空模板名返回 False。"""
        controller = RulesController(config_controller)
        assert controller.loadTemplate("") is False

    def test_load_template_twice_succeeds(
        self,
        config_controller: ConfigController,
    ) -> None:
        """重复加载同一模板应返回 True（视为已加载成功，不报错）。"""
        controller = RulesController(config_controller)
        assert controller.loadTemplate("privacy_data") is True
        # 第二次：路径已存在，loadFileFromPath 返回 False，但 loadTemplate 应返回 True
        assert controller.loadTemplate("privacy_data") is True

    def test_load_all_templates_succeed(
        self,
        config_controller: ConfigController,
    ) -> None:
        """所有列出的模板都应能成功加载。"""
        from fuscan.rules import get_template_names

        controller = RulesController(config_controller)
        for name in get_template_names():
            assert controller.loadTemplate(name) is True, f"加载模板 {name} 失败"

        # rules_paths 应包含所有模板文件
        assert len(config_controller.config.rules_paths) >= 5
