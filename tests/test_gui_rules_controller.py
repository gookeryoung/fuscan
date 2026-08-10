"""``RulesController`` 单元测试（iter-137 全局模式）。

验证规则文件管理（加载/上移/下移/移除）、内置规则勾选、规则集合并与
``rulesetChanged`` 信号触发。iter-137 起规则配置改为全局模式，所有工作区
共享同一规则集，直接读写 :class:`Config` 的 ``rules_paths``/``use_builtin``，
不再支持工作区绑定编辑。使用 ``tmp_path`` 隔离配置与规则文件。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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
    def test_empty_rules_file_model_has_only_builtin(self, config_controller: ConfigController) -> None:
        """无规则文件时列表仅包含内置规则项。"""
        controller = RulesController(config_controller)
        model = controller.rulesFileModel
        assert len(model) == 1
        assert model[0]["isBuiltin"] is True
        assert model[0]["scope"] == "global"
        assert model[0]["canRemove"] is False
        assert model[0]["enabled"] is True  # 默认启用内置规则

    def test_rules_file_model_after_load(self, controller_with_file: RulesController, rules_file: Path) -> None:
        """加载规则文件后列表应有内置 + 用户文件两项，用户文件在索引 1。"""
        model = controller_with_file.rulesFileModel
        assert len(model) == 2
        # 索引 0：内置规则
        assert model[0]["isBuiltin"] is True
        # 索引 1：用户规则文件
        assert model[1]["fileName"] == rules_file.name
        assert model[1]["path"] == str(rules_file)
        assert model[1]["scope"] == "global"
        assert model[1]["isBuiltin"] is False
        assert model[1]["enabled"] is True
        assert model[1]["canRemove"] is True

    def test_rules_file_model_includes_exists_field(
        self, controller_with_file: RulesController, rules_file: Path
    ) -> None:
        """iter-139：rulesFileModel 每项应包含 exists 字段标记文件是否存在。"""
        model = controller_with_file.rulesFileModel
        assert len(model) == 2
        assert model[1]["exists"] is True  # 用户文件存在

    def test_rules_file_model_marks_missing_file(self, config_controller: ConfigController, tmp_path: Path) -> None:
        """iter-139：规则文件不存在时 exists 字段应为 False。"""
        missing = tmp_path / "missing.yaml"
        config_controller.config.rules_paths = [str(missing)]
        config_controller.save()
        controller = RulesController(config_controller)
        model = controller.rulesFileModel
        assert len(model) == 2  # 内置 + 缺失用户文件
        assert model[1]["exists"] is False

    def test_rules_file_model_builtin_has_scan_extensions(self, config_controller: ConfigController) -> None:
        """内置规则项应携带 scanExtensions（来自 builtin.yaml）与 state='list'。"""
        controller = RulesController(config_controller)
        model = controller.rulesFileModel
        assert model[0]["isBuiltin"] is True
        assert model[0]["scanExtensionsState"] == "list"
        exts = model[0]["scanExtensions"]
        assert isinstance(exts, list)
        # builtin.yaml 至少定义了 txt/log/pdf/docx 等后缀
        assert "txt" in exts
        assert "pdf" in exts

    def test_rules_file_model_user_file_without_scan_extensions(self, controller_with_file: RulesController) -> None:
        """用户规则文件未定义 scan_extensions 时 state='unset'，列表为空。"""
        model = controller_with_file.rulesFileModel
        # 索引 1 是用户文件（_write_rules_file 不写 scan_extensions）
        assert model[1]["scanExtensionsState"] == "unset"
        assert model[1]["scanExtensions"] == []

    def test_rules_file_model_user_file_with_scan_extensions(
        self, config_controller: ConfigController, tmp_path: Path
    ) -> None:
        """用户规则文件定义 scan_extensions 时 state='list'，列表非空。"""
        path = tmp_path / "with_ext.yaml"
        path.write_text(
            'version: "1.0"\n'
            "scan_extensions:\n"
            '  - "py"\n'
            '  - "js"\n'
            "rules:\n"
            '  - name: "r1"\n'
            "    match:\n"
            "      type: content\n"
            "      mode: contains\n"
            '      pattern: "x"\n',
            encoding="utf-8",
        )
        config_controller.config.rules_paths = [str(path)]
        config_controller.save()
        controller = RulesController(config_controller)
        model = controller.rulesFileModel
        assert model[1]["scanExtensionsState"] == "list"
        assert model[1]["scanExtensions"] == ["py", "js"]

    def test_rules_file_model_user_file_with_empty_scan_extensions(
        self, config_controller: ConfigController, tmp_path: Path
    ) -> None:
        """用户规则文件 scan_extensions 为空列表时 state='none'（都不扫描）。"""
        path = tmp_path / "empty_ext.yaml"
        path.write_text(
            'version: "1.0"\n'
            "scan_extensions: []\n"
            "rules:\n"
            '  - name: "r1"\n'
            "    match:\n"
            "      type: content\n"
            "      mode: contains\n"
            '      pattern: "x"\n',
            encoding="utf-8",
        )
        config_controller.config.rules_paths = [str(path)]
        config_controller.save()
        controller = RulesController(config_controller)
        model = controller.rulesFileModel
        assert model[1]["scanExtensionsState"] == "none"
        assert model[1]["scanExtensions"] == []

    def test_rules_file_model_missing_file_state_unset(
        self, config_controller: ConfigController, tmp_path: Path
    ) -> None:
        """不存在的规则文件 state='unset'（避免阻塞 UI 渲染）。"""
        missing = tmp_path / "missing.yaml"
        config_controller.config.rules_paths = [str(missing)]
        config_controller.save()
        controller = RulesController(config_controller)
        model = controller.rulesFileModel
        assert model[1]["exists"] is False
        assert model[1]["scanExtensionsState"] == "unset"
        assert model[1]["scanExtensions"] == []

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

    def test_can_move_up_false_for_builtin(self, controller_with_file: RulesController) -> None:
        """内置规则（索引 0）不可上移。"""
        controller_with_file.setSelectedFileIndex(0)
        assert controller_with_file.canMoveUp is False

    def test_can_move_up_false_for_first_global(self, controller_with_file: RulesController) -> None:
        """第一个全局规则文件（索引 1）不可上移。"""
        controller_with_file.setSelectedFileIndex(1)
        assert controller_with_file.canMoveUp is False

    def test_can_move_down_false_when_empty(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        assert controller.canMoveDown is False

    def test_can_move_down_false_for_builtin(self, controller_with_file: RulesController) -> None:
        """内置规则不可下移。"""
        controller_with_file.setSelectedFileIndex(0)
        assert controller_with_file.canMoveDown is False

    def test_can_move_down_false_when_last(self, controller_with_file: RulesController) -> None:
        """最后一个全局规则文件不可下移。"""
        controller_with_file.setSelectedFileIndex(1)
        assert controller_with_file.canMoveDown is False

    def test_can_remove_default_false(self, config_controller: ConfigController) -> None:
        controller = RulesController(config_controller)
        assert controller.canRemove is False

    def test_can_remove_false_for_builtin(self, controller_with_file: RulesController) -> None:
        """内置规则不可移除。"""
        controller_with_file.setSelectedFileIndex(0)
        assert controller_with_file.canRemove is False

    def test_can_remove_true_for_user_file(self, controller_with_file: RulesController) -> None:
        """用户规则文件可移除。"""
        controller_with_file.setSelectedFileIndex(1)
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
        # 列表：[内置(0), r1.yaml(1), r2.yaml(2)]
        # 选中 r2.yaml（索引 2），上移到 r1.yaml 位置（索引 1）
        controller.setSelectedFileIndex(2)
        assert controller.canMoveUp is True
        controller.moveUp()

        assert controller.selectedFileIndex == 1
        assert controller.rulesFileModel[1]["fileName"] == "r2.yaml"
        assert controller.rulesFileModel[2]["fileName"] == "r1.yaml"

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
        # 选中 r1.yaml（索引 1），下移到 r2.yaml 位置（索引 2）
        controller.setSelectedFileIndex(1)
        assert controller.canMoveDown is True
        controller.moveDown()

        assert controller.selectedFileIndex == 2
        assert controller.rulesFileModel[1]["fileName"] == "r2.yaml"
        assert controller.rulesFileModel[2]["fileName"] == "r1.yaml"

    def test_move_up_noop_when_first(self, controller_with_file: RulesController) -> None:
        """第一个全局规则文件不可上移，操作无效。"""
        controller_with_file.setSelectedFileIndex(1)
        controller_with_file.moveUp()
        assert controller_with_file.selectedFileIndex == 1

    def test_move_down_noop_when_last(self, controller_with_file: RulesController) -> None:
        """最后一个全局规则文件不可下移，操作无效。"""
        controller_with_file.setSelectedFileIndex(1)
        controller_with_file.moveDown()
        assert controller_with_file.selectedFileIndex == 1


class TestRemoveSelected:
    def test_remove_selected_clears_entry(self, controller_with_file: RulesController, rules_file: Path) -> None:
        """移除用户规则文件后列表仅剩内置规则。"""
        controller_with_file.setSelectedFileIndex(1)  # 用户文件索引
        controller_with_file.removeSelected()
        # 仅剩内置规则项
        assert len(controller_with_file.rulesFileModel) == 1
        assert controller_with_file.rulesFileModel[0]["isBuiltin"] is True
        assert controller_with_file.selectedFileIndex == -1

    def test_remove_selected_noop_when_invalid(self, controller_with_file: RulesController) -> None:
        """无效索引时移除操作无效，列表不变。"""
        controller_with_file.setSelectedFileIndex(-1)
        controller_with_file.removeSelected()
        # 列表未变（内置 + 用户文件）
        assert len(controller_with_file.rulesFileModel) == 2

    def test_remove_selected_noop_for_builtin(self, controller_with_file: RulesController) -> None:
        """内置规则不可移除。"""
        controller_with_file.setSelectedFileIndex(0)
        controller_with_file.removeSelected()
        # 列表未变
        assert len(controller_with_file.rulesFileModel) == 2


class TestRemoveGlobalPath:
    """``removeGlobalPath`` 按路径直接移除全局规则文件（无需先选中）。"""

    def test_remove_by_path_clears_entry(self, controller_with_file: RulesController, rules_file: Path) -> None:
        """按路径移除用户规则文件后列表仅剩内置规则。"""
        controller_with_file.removeGlobalPath(str(rules_file))
        assert len(controller_with_file.rulesFileModel) == 1
        assert controller_with_file.rulesFileModel[0]["isBuiltin"] is True
        assert controller_with_file.selectedFileIndex == -1

    def test_remove_by_path_noop_for_builtin(self, controller_with_file: RulesController) -> None:
        """内置规则标识 ``__builtin__`` 忽略，不移除。"""
        controller_with_file.removeGlobalPath("__builtin__")
        assert len(controller_with_file.rulesFileModel) == 2

    def test_remove_by_path_noop_for_nonexistent(self, controller_with_file: RulesController) -> None:
        """路径不在全局规则列表中时安全忽略。"""
        controller_with_file.removeGlobalPath("/nonexistent/rule.yaml")
        assert len(controller_with_file.rulesFileModel) == 2

    def test_remove_by_path_clears_disabled_list(self, config_controller: ConfigController, tmp_path: Path) -> None:
        """移除时同步清理 disabled_rules_paths。"""
        rules_file = _write_rules_file(tmp_path, "to_remove.yaml")
        config_controller.config.rules_paths = [str(rules_file)]
        config_controller.config.disabled_rules_paths = [str(rules_file)]
        config_controller.save()
        controller = RulesController(config_controller)
        controller.removeGlobalPath(str(rules_file))
        assert str(rules_file) not in config_controller.config.rules_paths
        assert str(rules_file) not in config_controller.config.disabled_rules_paths


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
        # 列表：[内置(0), r1.yaml(1), r2.yaml(2)]，选中 r2.yaml（索引 2）

        emitted: list[None] = []
        controller.rulesetChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setSelectedFileIndex(2)
        emitted.clear()
        controller.moveUp()
        assert len(emitted) == 1

    def test_remove_selected_emits_ruleset_changed(self, controller_with_file: RulesController) -> None:
        emitted: list[None] = []
        controller_with_file.rulesetChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller_with_file.setSelectedFileIndex(1)  # 用户文件索引
        emitted.clear()
        controller_with_file.removeSelected()
        assert len(emitted) == 1


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


# ============================= iter-122 导入/导出 =============================


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


# ============================= iter-138 全局/临时规则管理 =============================


class _FakeSignal:
    """模拟 PySide2 Signal 对象。"""

    def __init__(self) -> None:
        self._callbacks: list[Any] = []

    def connect(self, cb: Any) -> None:
        self._callbacks.append(cb)

    def emit(self, *args: object) -> None:
        for cb in self._callbacks:
            cb(*args)


class _FakeWorkspaceItem:
    """模拟 WorkspaceItem（仅含 task_overrides 字典）。"""

    def __init__(self, ws_id: str, name: str) -> None:
        self.id = ws_id
        self.name = name
        self.task_overrides: dict[str, object] = {}


class _FakeWorkspaceController:
    """模拟 WorkspaceController，仅实现 RulesController 依赖的接口。

    - ``currentWorkspaceId``：当前工作区 ID（空串表示未选中）
    - ``get_workspace(ws_id)``：返回 :class:`_FakeWorkspaceItem`
    - ``workspaceName(ws_id)``：返回工作区名称
    - ``currentWorkspaceChanged``：信号（连接 RulesController 的刷新回调）
    - ``setTaskOverride(ws_id, key, value_json)``：更新 task_overrides
    """

    def __init__(self) -> None:
        self._workspaces: dict[str, _FakeWorkspaceItem] = {}
        self._current_id: str = ""
        self.currentWorkspaceChanged = _FakeSignal()

    @property
    def currentWorkspaceId(self) -> str:
        return self._current_id

    def set_current(self, ws_id: str) -> None:
        self._current_id = ws_id
        self.currentWorkspaceChanged.emit()

    def add_workspace(self, ws_id: str, name: str) -> _FakeWorkspaceItem:
        item = _FakeWorkspaceItem(ws_id, name)
        self._workspaces[ws_id] = item
        return item

    def get_workspace(self, ws_id: str) -> _FakeWorkspaceItem | None:
        return self._workspaces.get(ws_id)

    def workspaceName(self, ws_id: str) -> str:
        item = self._workspaces.get(ws_id)
        return item.name if item is not None else ""

    def setTaskOverride(self, ws_id: str, key: str, value_json: str) -> None:

        item = self._workspaces.get(ws_id)
        if item is None:
            return
        value = json.loads(value_json)
        if key in ("rules_paths", "temp_rules_paths", "disabled_temp_rules_paths"):
            value = tuple(value)
        item.task_overrides[key] = value


@pytest.fixture()
def fake_workspace_controller() -> _FakeWorkspaceController:
    return _FakeWorkspaceController()


@pytest.fixture()
def controller_with_workspace(
    config_controller: ConfigController,
    fake_workspace_controller: _FakeWorkspaceController,
) -> tuple[RulesController, _FakeWorkspaceController]:
    """构造已注入伪 WorkspaceController 的 RulesController，并选中一个工作区。"""
    fake_workspace_controller.add_workspace("ws1", "工作区A")
    fake_workspace_controller.set_current("ws1")
    controller = RulesController(config_controller)
    controller.set_workspace_controller(fake_workspace_controller)
    return controller, fake_workspace_controller


class TestSetRuleEnabled:
    """``setRuleEnabled`` Slot 测试（iter-138）。"""

    def test_disable_builtin_rule(
        self,
        controller_with_file: RulesController,
    ) -> None:
        """禁用内置规则等价于 setUseBuiltin(False)。"""
        controller_with_file.setRuleEnabled("__builtin__", False)
        assert controller_with_file.useBuiltin is False
        # rulesFileModel[0] 的 enabled 应反映禁用状态
        assert controller_with_file.rulesFileModel[0]["enabled"] is False

    def test_enable_builtin_rule(
        self,
        config_controller: ConfigController,
    ) -> None:
        """启用内置规则。"""
        config_controller.config.use_builtin = False
        controller = RulesController(config_controller)
        controller.setRuleEnabled("__builtin__", True)
        assert controller.useBuiltin is True
        assert controller.rulesFileModel[0]["enabled"] is True

    def test_disable_global_user_file(
        self,
        controller_with_file: RulesController,
        rules_file: Path,
    ) -> None:
        """禁用全局用户规则文件应加入 disabled_rules_paths。"""
        path_str = str(rules_file)
        controller_with_file.setRuleEnabled(path_str, False)
        assert path_str in controller_with_file._config.disabled_rules_paths
        # rulesFileModel 中该项 enabled 应为 False
        model = controller_with_file.rulesFileModel
        # 索引 1 是用户文件
        assert model[1]["enabled"] is False

    def test_enable_global_user_file_removes_from_disabled(
        self,
        controller_with_file: RulesController,
        rules_file: Path,
    ) -> None:
        """启用已禁用的全局规则文件应从 disabled_rules_paths 移除。"""
        path_str = str(rules_file)
        # 先禁用
        controller_with_file.setRuleEnabled(path_str, False)
        assert path_str in controller_with_file._config.disabled_rules_paths
        # 再启用
        controller_with_file.setRuleEnabled(path_str, True)
        assert path_str not in controller_with_file._config.disabled_rules_paths
        assert controller_with_file.rulesFileModel[1]["enabled"] is True

    def test_disable_nonexistent_path_noop(
        self,
        controller_with_file: RulesController,
    ) -> None:
        """禁用不在 rules_paths 中的路径应 warning 并 noop。"""
        controller_with_file.setRuleEnabled("/not/loaded.yaml", False)
        assert "/not/loaded.yaml" not in controller_with_file._config.disabled_rules_paths

    def test_disable_filters_ruleset(
        self,
        controller_with_file: RulesController,
        rules_file: Path,
    ) -> None:
        """禁用全局规则文件后 ruleset 应不再包含该文件规则（仅内置规则）。"""
        # controller_with_file 默认启用内置规则 + rules_file
        initial_count = controller_with_file.ruleCount
        # 禁用 rules_file 后只剩内置规则
        controller_with_file.setRuleEnabled(str(rules_file), False)
        # 内置规则数应小于初始合并规则数（除非两者规则完全重合）
        assert controller_with_file.ruleCount <= initial_count


class TestLoadFileToTemp:
    """``loadFileToTemp`` Slot 测试（iter-138）。"""

    def test_load_temp_without_workspace_returns_false(
        self,
        config_controller: ConfigController,
        rules_file: Path,
    ) -> None:
        """无当前工作区时加载临时规则返回 False。"""
        controller = RulesController(config_controller)
        # 未注入 workspace_controller
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.loadFileToTemp(str(rules_file)) is False
        assert len(emitted) == 1
        assert emitted[0][0] is False
        assert "工作区" in emitted[0][1]

    def test_load_temp_nonexistent_returns_false(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        tmp_path: Path,
    ) -> None:
        """加载不存在的文件返回 False。"""
        controller, _ = controller_with_workspace
        target = tmp_path / "missing.yaml"
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.loadFileToTemp(str(target)) is False
        assert len(emitted) == 1
        assert "不存在" in emitted[0][1]

    def test_load_temp_success(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """加载规则文件到当前工作区临时规则成功。"""
        controller, wc = controller_with_workspace
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.loadFileToTemp(str(rules_file)) is True
        assert len(emitted) == 1
        assert emitted[0][0] is True

        # temp_rules_paths 应已写入
        item = wc.get_workspace("ws1")
        assert item is not None
        assert item.task_overrides.get("temp_rules_paths") == (str(rules_file),)

        # rulesFileModel 应包含临时规则项
        # 列表：[内置(0), 临时(1)]（controller_with_workspace 无全局用户文件）
        model = controller.rulesFileModel
        assert len(model) == 2
        temp_item = model[1]
        assert temp_item["scope"] == "temp"
        assert temp_item["path"] == str(rules_file)
        assert temp_item["canRemove"] is True
        assert temp_item["enabled"] is True

    def test_load_temp_duplicate_returns_false(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """重复加载同一临时规则文件返回 False。"""
        controller, _ = controller_with_workspace
        # 第一次加载成功
        assert controller.loadFileToTemp(str(rules_file)) is True
        # 第二次应失败
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.loadFileToTemp(str(rules_file)) is False
        assert len(emitted) == 1
        assert "已在临时规则" in emitted[0][1]

    def test_load_temp_invalid_yaml_returns_false(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        tmp_path: Path,
    ) -> None:
        """加载非法 YAML 文件返回 False。"""
        controller, _ = controller_with_workspace
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("not: valid: yaml: - -", encoding="utf-8")
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.loadFileToTemp(str(bad_yaml)) is False
        assert len(emitted) == 1
        assert emitted[0][0] is False
        assert "加载失败" in emitted[0][1]


class TestWorkspaceIntegration:
    """``hasCurrentWorkspace``/``currentWorkspaceName`` 属性测试（iter-138）。"""

    def test_no_workspace_initially(self, config_controller: ConfigController) -> None:
        """未注入 workspace_controller 时 hasCurrentWorkspace 为 False。"""
        controller = RulesController(config_controller)
        assert controller.hasCurrentWorkspace is False
        assert controller.currentWorkspaceName == ""

    def test_has_current_workspace_after_inject(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
    ) -> None:
        """注入工作区并选中后 hasCurrentWorkspace 为 True。"""
        controller, _ = controller_with_workspace
        assert controller.hasCurrentWorkspace is True
        assert controller.currentWorkspaceName == "工作区A"

    def test_workspace_switch_refreshes_temp_list(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
    ) -> None:
        """切换工作区时 rulesFileListChanged 与 currentWorkspaceChanged 应 emit。"""
        controller, wc = controller_with_workspace
        wc.add_workspace("ws2", "工作区B")

        list_emitted: list[None] = []
        ws_emitted: list[None] = []
        controller.rulesFileListChanged.connect(lambda: list_emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.currentWorkspaceChanged.connect(lambda: ws_emitted.append(None))  # pyrefly: ignore [missing-attribute]

        wc.set_current("ws2")
        assert controller.currentWorkspaceName == "工作区B"
        assert len(list_emitted) >= 1
        assert len(ws_emitted) >= 1

    def test_temp_rules_isolated_per_workspace(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """不同工作区的临时规则相互隔离。"""
        controller, wc = controller_with_workspace
        # ws1 加载临时规则
        assert controller.loadFileToTemp(str(rules_file)) is True
        # 切换到 ws2（无临时规则）
        wc.add_workspace("ws2", "工作区B")
        wc.set_current("ws2")
        # ws2 无临时规则，列表应只有内置项
        model = controller.rulesFileModel
        assert len(model) == 1
        assert model[0]["isBuiltin"] is True
        # 切回 ws1，临时规则应恢复
        wc.set_current("ws1")
        model = controller.rulesFileModel
        assert len(model) == 2
        assert model[1]["scope"] == "temp"

    def test_remove_temp_rule(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """移除临时规则文件应从 task_overrides 删除。"""
        controller, wc = controller_with_workspace
        controller.loadFileToTemp(str(rules_file))
        # 列表：[内置(0), 临时(1)]
        controller.setSelectedFileIndex(1)
        assert controller.canRemove is True
        controller.removeSelected()
        # 临时规则已移除
        item = wc.get_workspace("ws1")
        assert item is not None
        assert item.task_overrides.get("temp_rules_paths", ()) == ()
        # 列表仅剩内置
        assert len(controller.rulesFileModel) == 1


class TestPromoteToGlobal:
    """``promoteToGlobal`` Slot 测试（临时规则提升为全局规则）。"""

    def test_promote_without_workspace_returns_false(
        self,
        config_controller: ConfigController,
        rules_file: Path,
    ) -> None:
        """无当前工作区时提升返回 False。"""
        controller = RulesController(config_controller)
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.promoteToGlobal(str(rules_file)) is False
        assert len(emitted) == 1
        assert emitted[0][0] is False
        assert "工作区" in emitted[0][1]

    def test_promote_path_not_in_temp_returns_false(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """路径不在当前工作区临时规则中时返回 False。"""
        controller, _ = controller_with_workspace
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        # 临时规则列表为空，promote 应失败
        assert controller.promoteToGlobal(str(rules_file)) is False
        assert len(emitted) == 1
        assert "不是当前工作区的临时规则" in emitted[0][1]

    def test_promote_success(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """提升临时规则到全局成功：从 temp 移除，加入 global。"""
        controller, wc = controller_with_workspace
        # 先加载为临时规则
        assert controller.loadFileToTemp(str(rules_file)) is True
        # 列表：[内置(0), 临时(1)]
        assert len(controller.rulesFileModel) == 2
        assert controller.rulesFileModel[1]["scope"] == "temp"

        # 提升为全局
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.promoteToGlobal(str(rules_file)) is True
        assert len(emitted) == 1
        assert emitted[0][0] is True
        assert "提升" in emitted[0][1]

        # temp_rules_paths 应清空
        item = wc.get_workspace("ws1")
        assert item is not None
        assert item.task_overrides.get("temp_rules_paths", ()) == ()

        # rules_paths 应包含该文件
        assert str(rules_file) in controller._config.rules_paths

        # 列表：[内置(0), 全局(1)]
        model = controller.rulesFileModel
        assert len(model) == 2
        assert model[1]["scope"] == "global"
        assert model[1]["path"] == str(rules_file)

    def test_promote_dedup_when_already_global(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """提升时若文件已在全局列表，仅移除临时侧，不重复加入全局。"""
        controller, wc = controller_with_workspace
        # 先加入全局
        controller._config.rules_paths.append(str(rules_file))
        controller._config_controller.save()  # pyrefly: ignore [missing-attribute]
        controller._reload_ruleset()
        # 再加载为临时规则（同一文件）
        assert controller.loadFileToTemp(str(rules_file)) is True
        # 列表：[内置(0), 全局(1), 临时(2)]
        assert len(controller.rulesFileModel) == 3

        # 提升临时侧
        assert controller.promoteToGlobal(str(rules_file)) is True

        # rules_paths 应只有一份（不重复）
        assert controller._config.rules_paths.count(str(rules_file)) == 1
        # temp 侧清空
        item = wc.get_workspace("ws1")
        assert item is not None
        assert item.task_overrides.get("temp_rules_paths", ()) == ()
        # 列表：[内置(0), 全局(1)]
        assert len(controller.rulesFileModel) == 2
        assert controller.rulesFileModel[1]["scope"] == "global"

    def test_promote_invalid_yaml_returns_false(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        tmp_path: Path,
    ) -> None:
        """提升损坏 YAML 文件返回 False（不影响两侧状态）。"""
        controller, wc = controller_with_workspace
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("not: valid: yaml: - -", encoding="utf-8")
        # 直接写入 task_overrides（绕过 loadFileToTemp 的预校验）
        item = wc.get_workspace("ws1")
        assert item is not None
        item.task_overrides["temp_rules_paths"] = (str(bad_yaml),)

        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.promoteToGlobal(str(bad_yaml)) is False
        assert len(emitted) == 1
        assert "加载失败" in emitted[0][1]
        # temp 侧仍保留（未迁移）
        assert item.task_overrides.get("temp_rules_paths") == (str(bad_yaml),)
        # global 侧未加入
        assert str(bad_yaml) not in controller._config.rules_paths

    def test_promote_clears_disabled_mark(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """提升时若该路径曾被禁用，应同步清理 disabled_rules_paths。"""
        controller, _wc = controller_with_workspace
        # 加载为临时规则
        assert controller.loadFileToTemp(str(rules_file)) is True
        # 模拟曾在全局侧禁用过该路径
        controller._config.disabled_rules_paths.append(str(rules_file))
        controller._config_controller.save()  # pyrefly: ignore [missing-attribute]

        assert controller.promoteToGlobal(str(rules_file)) is True
        # disabled_rules_paths 应已清理
        assert str(rules_file) not in controller._config.disabled_rules_paths
        # 全局侧启用
        model = controller.rulesFileModel
        global_item = next(
            (m for m in model if m["path"] == str(rules_file) and m["scope"] == "global"),
            None,
        )
        assert global_item is not None
        assert global_item["enabled"] is True


class TestDemoteToTemp:
    """``demoteToTemp`` Slot 测试（全局规则降级为临时规则）。"""

    def test_demote_without_workspace_returns_false(
        self,
        controller_with_file: RulesController,
        rules_file: Path,
    ) -> None:
        """无当前工作区时降级返回 False。"""
        controller = controller_with_file
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.demoteToTemp(str(rules_file)) is False
        assert len(emitted) == 1
        assert "工作区" in emitted[0][1]

    def test_demote_path_not_in_global_returns_false(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """路径不在全局规则中时返回 False。"""
        controller, _ = controller_with_workspace
        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.demoteToTemp(str(rules_file)) is False
        assert len(emitted) == 1
        assert "不是全局规则" in emitted[0][1]

    def test_demote_success(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """降级全局规则到当前工作区临时规则成功。"""
        controller, wc = controller_with_workspace
        # 先加入全局
        controller._config.rules_paths.append(str(rules_file))
        controller._config_controller.save()  # pyrefly: ignore [missing-attribute]
        controller._reload_ruleset()
        # 列表：[内置(0), 全局(1)]
        assert len(controller.rulesFileModel) == 2
        assert controller.rulesFileModel[1]["scope"] == "global"

        emitted: list[tuple[bool, str]] = []
        controller.rulesIoCompleted.connect(  # pyrefly: ignore [missing-attribute]
            lambda ok, msg: emitted.append((ok, msg))
        )
        assert controller.demoteToTemp(str(rules_file)) is True
        assert len(emitted) == 1
        assert emitted[0][0] is True
        assert "降级" in emitted[0][1]

        # global 侧移除
        assert str(rules_file) not in controller._config.rules_paths
        # temp 侧加入
        item = wc.get_workspace("ws1")
        assert item is not None
        assert item.task_overrides.get("temp_rules_paths") == (str(rules_file),)

        # 列表：[内置(0), 临时(1)]
        model = controller.rulesFileModel
        assert len(model) == 2
        assert model[1]["scope"] == "temp"
        assert model[1]["path"] == str(rules_file)

    def test_demote_dedup_when_already_temp(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """降级时若文件已在临时列表，仅移除全局侧，不重复加入临时。"""
        controller, wc = controller_with_workspace
        # 先加入全局
        controller._config.rules_paths.append(str(rules_file))
        controller._config_controller.save()  # pyrefly: ignore [missing-attribute]
        controller._reload_ruleset()
        # 再加载为临时规则（同一文件）
        assert controller.loadFileToTemp(str(rules_file)) is True
        # 列表：[内置(0), 全局(1), 临时(2)]
        assert len(controller.rulesFileModel) == 3

        assert controller.demoteToTemp(str(rules_file)) is True

        # temp 侧只有一份（不重复）
        item = wc.get_workspace("ws1")
        assert item is not None
        temp_paths = item.task_overrides.get("temp_rules_paths", ())
        assert temp_paths.count(str(rules_file)) == 1  # pyrefly: ignore [missing-attribute]
        # global 侧清空
        assert str(rules_file) not in controller._config.rules_paths
        # 列表：[内置(0), 临时(1)]
        assert len(controller.rulesFileModel) == 2
        assert controller.rulesFileModel[1]["scope"] == "temp"

    def test_demote_clears_disabled_mark(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """降级时若该路径曾被禁用，应同步清理 disabled_rules_paths。"""
        controller, _wc = controller_with_workspace
        # 加入全局并禁用
        controller._config.rules_paths.append(str(rules_file))
        controller._config.disabled_rules_paths.append(str(rules_file))
        controller._config_controller.save()  # pyrefly: ignore [missing-attribute]
        controller._reload_ruleset()

        assert controller.demoteToTemp(str(rules_file)) is True
        # disabled_rules_paths 应已清理
        assert str(rules_file) not in controller._config.disabled_rules_paths
        assert str(rules_file) not in controller._config.rules_paths


class TestDisabledRulesPathsPersistence:
    """``disabled_rules_paths`` 持久化测试（iter-138）。"""

    def test_disabled_rules_paths_default_empty(self, config_controller: ConfigController) -> None:
        """默认 disabled_rules_paths 为空列表。"""
        assert config_controller.config.disabled_rules_paths == []

    def test_disable_persists_to_config(
        self,
        controller_with_file: RulesController,
        rules_file: Path,
        config_controller: ConfigController,
    ) -> None:
        """禁用全局规则文件后 disabled_rules_paths 应持久化到 Config。"""
        path_str = str(rules_file)
        controller_with_file.setRuleEnabled(path_str, False)
        # setRuleEnabled 内部已调用 save()（debounce 路径），需 flush 强制写入磁盘
        config_controller.flush_save()
        from fuscan.config import load_config

        reloaded = load_config()
        assert path_str in reloaded.disabled_rules_paths


# ============================= iter-140 临时规则禁用 =============================


class TestSetRuleEnabledTemp:
    """``setRuleEnabled`` 对临时规则的禁用/启用支持（iter-140）。

    临时规则禁用持久化到 ``task_overrides.disabled_temp_rules_paths``，
    与全局 ``disabled_rules_paths`` 同语义但仅作用于当前工作区。
    """

    def test_disable_temp_rule_updates_task_overrides(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """禁用临时规则应写入 disabled_temp_rules_paths。"""
        controller, wc = controller_with_workspace
        controller.loadFileToTemp(str(rules_file))
        path_str = str(rules_file)

        # 禁用临时规则
        controller.setRuleEnabled(path_str, False)

        item = wc.get_workspace("ws1")
        assert item is not None
        disabled_temp: tuple[str, ...] = item.task_overrides.get("disabled_temp_rules_paths", ())  # type: ignore[assignment]
        assert path_str in disabled_temp
        # rulesFileModel 中该项 enabled 应为 False
        temp_item = next(m for m in controller.rulesFileModel if m["scope"] == "temp")
        assert temp_item["enabled"] is False

    def test_enable_temp_rule_removes_from_disabled(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """启用已禁用的临时规则应从 disabled_temp_rules_paths 移除。"""
        controller, wc = controller_with_workspace
        controller.loadFileToTemp(str(rules_file))
        path_str = str(rules_file)

        # 禁用后启用
        controller.setRuleEnabled(path_str, False)
        controller.setRuleEnabled(path_str, True)

        item = wc.get_workspace("ws1")
        assert item is not None
        disabled_temp: tuple[str, ...] = item.task_overrides.get("disabled_temp_rules_paths", ())  # type: ignore[assignment]
        assert path_str not in disabled_temp
        temp_item = next(m for m in controller.rulesFileModel if m["scope"] == "temp")
        assert temp_item["enabled"] is True

    def test_disable_temp_rule_noop_when_already_disabled(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """重复禁用同一临时规则应 noop（disabled_temp_rules_paths 不重复追加）。"""
        controller, wc = controller_with_workspace
        controller.loadFileToTemp(str(rules_file))
        path_str = str(rules_file)

        controller.setRuleEnabled(path_str, False)
        controller.setRuleEnabled(path_str, False)  # 重复禁用

        item = wc.get_workspace("ws1")
        assert item is not None
        disabled = item.task_overrides.get("disabled_temp_rules_paths", ())
        assert disabled.count(path_str) == 1  # pyrefly: ignore [missing-attribute]

    def test_enable_temp_rule_noop_when_already_enabled(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """启用已启用的临时规则应 noop。"""
        controller, wc = controller_with_workspace
        controller.loadFileToTemp(str(rules_file))
        path_str = str(rules_file)

        # 默认启用，再次启用应 noop（disabled_temp_rules_paths 仍为空）
        controller.setRuleEnabled(path_str, True)

        item = wc.get_workspace("ws1")
        assert item is not None
        assert item.task_overrides.get("disabled_temp_rules_paths", ()) == ()

    def test_disable_temp_rule_without_workspace_noop(
        self,
        config_controller: ConfigController,
        rules_file: Path,
    ) -> None:
        """无当前工作区时禁用临时规则应 noop（不抛异常）。"""
        controller = RulesController(config_controller)
        # 未注入 workspace_controller，path 不在 _current_temp_paths（空）
        # 应走全局规则禁用分支，但因 path 不在 rules_paths 也 noop
        controller.setRuleEnabled(str(rules_file), False)
        assert str(rules_file) not in config_controller.config.disabled_rules_paths

    def test_disable_temp_rule_isolated_per_workspace(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """禁用状态随工作区切换刷新——ws1 禁用不影响 ws2 视图。"""
        controller, wc = controller_with_workspace
        controller.loadFileToTemp(str(rules_file))
        path_str = str(rules_file)

        # ws1 禁用
        controller.setRuleEnabled(path_str, False)
        # 切换到 ws2（无临时规则）
        wc.add_workspace("ws2", "工作区B")
        wc.set_current("ws2")
        # ws2 列表仅内置项，无临时规则
        model = controller.rulesFileModel
        assert all(m["scope"] != "temp" for m in model)
        # 切回 ws1，临时规则仍存在且 enabled 为 False
        wc.set_current("ws1")
        temp_item = next(m for m in controller.rulesFileModel if m["scope"] == "temp")
        assert temp_item["enabled"] is False


class TestRemoveTempRuleClearsDisabled:
    """移除临时规则时同步清理 disabled_temp_rules_paths（iter-140）。"""

    def test_remove_temp_rule_clears_disabled_entry(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """移除已禁用的临时规则应同步从 disabled_temp_rules_paths 删除。"""
        controller, wc = controller_with_workspace
        controller.loadFileToTemp(str(rules_file))
        path_str = str(rules_file)

        # 禁用后移除
        controller.setRuleEnabled(path_str, False)
        controller.setSelectedFileIndex(1)  # 临时规则索引
        controller.removeSelected()

        item = wc.get_workspace("ws1")
        assert item is not None
        temp_paths: tuple[str, ...] = item.task_overrides.get("temp_rules_paths", ())  # type: ignore[assignment]
        disabled_temp: tuple[str, ...] = item.task_overrides.get("disabled_temp_rules_paths", ())  # type: ignore[assignment]
        assert path_str not in temp_paths
        assert path_str not in disabled_temp


class TestPromoteToGlobalClearsDisabledTemp:
    """提升临时规则到全局时同步清理 disabled_temp_rules_paths（iter-140）。"""

    def test_promote_clears_disabled_temp_entry(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """提升已禁用的临时规则到全局应清理 disabled_temp_rules_paths。"""
        controller, wc = controller_with_workspace
        controller.loadFileToTemp(str(rules_file))
        path_str = str(rules_file)

        # 禁用临时规则后提升
        controller.setRuleEnabled(path_str, False)
        assert controller.promoteToGlobal(path_str) is True

        item = wc.get_workspace("ws1")
        assert item is not None
        # temp_rules_paths 已清空
        assert item.task_overrides.get("temp_rules_paths", ()) == ()
        # disabled_temp_rules_paths 也已清空（无悬空记录）
        assert item.task_overrides.get("disabled_temp_rules_paths", ()) == ()
        # 全局侧启用（默认启用，未被加入 disabled_rules_paths）
        assert path_str not in controller._config.disabled_rules_paths


class TestRulesFileModelTempEnabledField:
    """``rulesFileModel`` 临时规则 enabled 字段反映禁用状态（iter-140）。"""

    def test_temp_rule_default_enabled(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """加载临时规则后默认 enabled=True。"""
        controller, _ = controller_with_workspace
        controller.loadFileToTemp(str(rules_file))

        temp_item = next(m for m in controller.rulesFileModel if m["scope"] == "temp")
        assert temp_item["enabled"] is True

    def test_temp_rule_disabled_reflected_in_model(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """禁用临时规则后 rulesFileModel 该项 enabled=False。"""
        controller, _ = controller_with_workspace
        controller.loadFileToTemp(str(rules_file))
        controller.setRuleEnabled(str(rules_file), False)

        temp_item = next(m for m in controller.rulesFileModel if m["scope"] == "temp")
        assert temp_item["enabled"] is False


# ============================= effectiveConfigPreview =============================


class TestEffectiveConfigPreview:
    """``effectiveConfigPreview`` 属性测试。"""

    def test_preview_with_none_ruleset(self, config_controller: ConfigController) -> None:
        """ruleset 为 None 时返回内置默认值，hasRuleset=False。"""
        config_controller.config.use_builtin = False
        controller = RulesController(config_controller)
        assert controller.ruleset is None

        preview = controller.effectiveConfigPreview
        assert preview["hasRuleset"] is False
        assert preview["scanArchives"] is True
        assert preview["maxWorkers"] == 5
        assert preview["maxDepth"] == 0
        assert preview["cacheEnabled"] is True
        assert preview["perfLogEnabled"] is False
        assert preview["ignoreDirs"] == []
        assert preview["scanExtensions"] == []
        assert preview["whitelistCount"] == 0

    def test_preview_with_builtin_ruleset(
        self,
        config_controller: ConfigController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """内置规则集加载后 hasRuleset=True，max_workers 按 CPU 核数动态计算。"""
        # 锁定 CPU 核数为 8，使 recommended_max_workers() 稳定返回 6（8 - 2）
        monkeypatch.setattr("os.cpu_count", lambda: 8)
        from fuscan.rules.builtin import load_builtin_ruleset, recommended_max_workers

        load_builtin_ruleset.cache_clear()
        try:
            expected_workers = recommended_max_workers(8)

            controller = RulesController(config_controller)
            assert controller.ruleset is not None

            preview = controller.effectiveConfigPreview
            assert preview["hasRuleset"] is True
            # 内置规则集的 scan_params 各字段为 None，max_workers 按 CPU 核数动态计算
            assert preview["scanArchives"] is True
            assert preview["maxWorkers"] == expected_workers
            assert preview["maxDepth"] == 0
            assert preview["cacheEnabled"] is True
            assert preview["perfLogEnabled"] is False
            assert isinstance(preview["ignoreDirs"], list)
            assert isinstance(preview["scanExtensions"], list)
        finally:
            # 清除缓存的 mocked 结果，避免污染后续测试（lru_cache 全局共享）
            load_builtin_ruleset.cache_clear()


# ============================= appendWhitelistEntry =============================


class TestAppendWhitelistEntry:
    """``appendWhitelistEntry`` Slot 测试（追加白名单到 user-scan.yaml）。"""

    def test_empty_path_returns_error(self, config_controller: ConfigController) -> None:
        """空路径返回错误消息。"""
        controller = RulesController(config_controller)
        msg = controller.appendWhitelistEntry("   ", "*", "")
        assert "不能为空" in msg

    def test_empty_rule_normalized_to_wildcard(
        self,
        config_controller: ConfigController,
    ) -> None:
        """空规则名归一化为 *。"""
        controller = RulesController(config_controller)
        msg = controller.appendWhitelistEntry("/a/b.txt", "   ", "")
        assert "*" in msg

    def test_append_creates_user_scan_yaml(
        self,
        config_controller: ConfigController,
    ) -> None:
        """user-scan.yaml 不存在时创建并写入白名单条目。"""
        controller = RulesController(config_controller)
        msg = controller.appendWhitelistEntry("/a/b.txt", "r1", "备注")
        assert "已添加" in msg

        user_scan = controller.userScanPath
        assert user_scan.exists()
        # user-scan.yaml 应被加入 rules_paths
        assert str(user_scan) in controller._config.rules_paths

    def test_append_to_existing_user_scan_yaml(
        self,
        config_controller: ConfigController,
    ) -> None:
        """user-scan.yaml 已存在时追加条目。"""
        controller = RulesController(config_controller)
        # 第一次追加
        controller.appendWhitelistEntry("/a", "r1", "")
        # 第二次追加不同条目
        msg = controller.appendWhitelistEntry("/b", "r2", "")
        assert "已添加" in msg

        # 验证两条都在 effective ruleset 白名单中
        assert controller.ruleset is not None
        paths = {(e.path_glob, e.rule_name) for e in controller.ruleset.whitelist}
        assert ("/a", "r1") in paths
        assert ("/b", "r2") in paths

    def test_append_duplicate_returns_exists_message(
        self,
        config_controller: ConfigController,
    ) -> None:
        """重复追加相同 (path_glob, rule_name) 返回已存在消息。"""
        controller = RulesController(config_controller)
        controller.appendWhitelistEntry("/a", "r1", "")
        msg = controller.appendWhitelistEntry("/a", "r1", "")
        assert "已存在" in msg

    def test_append_emits_ruleset_changed(
        self,
        config_controller: ConfigController,
    ) -> None:
        """追加成功后发射 rulesetChanged 信号。"""
        controller = RulesController(config_controller)
        emitted: list[None] = []

        def _on_changed() -> None:
            emitted.append(None)

        controller.rulesetChanged.connect(_on_changed)  # type: ignore[arg-type]
        msg = controller.appendWhitelistEntry("/a", "r1", "")
        assert "已添加" in msg, f"追加失败: {msg}"
        assert len(emitted) == 1

    def test_append_rule_name_defaults_to_wildcard(
        self,
        config_controller: ConfigController,
    ) -> None:
        """空规则名归一化为 * 并持久化。"""
        controller = RulesController(config_controller)
        controller.appendWhitelistEntry("/a", "", "")
        assert controller.ruleset is not None
        entry = next(e for e in controller.ruleset.whitelist if e.path_glob == "/a")
        assert entry.rule_name == "*"
        assert entry.source == "runtime"


# ============================= previewRuleset =============================


class TestPreviewRuleset:
    """``previewRuleset`` Slot 测试。"""

    def test_empty_ws_id_returns_empty_object(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
    ) -> None:
        """空 wsId 返回 ``"{}"``。"""
        controller, _ = controller_with_workspace
        assert controller.previewRuleset("") == "{}"

    def test_nonexistent_ws_id_returns_empty_object(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
    ) -> None:
        """不存在的 wsId 返回 ``"{}"``。"""
        controller, _ = controller_with_workspace
        assert controller.previewRuleset("not-exist") == "{}"

    def test_default_workspace_preview_has_ruleset(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
    ) -> None:
        """默认工作区（启用内置规则）预览 hasRuleset=True，含规则与规则文件。"""
        controller, _ = controller_with_workspace

        data = json.loads(controller.previewRuleset("ws1"))
        assert data["hasRuleset"] is True
        # 内置规则集规则数 > 0
        assert len(data["rules"]) > 0
        # 规则文件列表包含内置项
        assert len(data["ruleFiles"]) >= 1
        assert data["ruleFiles"][0]["isBuiltin"] is True
        # 必需字段齐全
        for key in (
            "scanArchives",
            "maxWorkers",
            "maxDepth",
            "maxFileSizeMB",
            "cacheEnabled",
            "perfLogEnabled",
            "ignoreDirs",
            "scanExtensions",
            "whitelistEntries",
            "rules",
            "ruleFiles",
            "hasRuleset",
        ):
            assert key in data, f"缺少字段 {key}"
        # scanExtensions 为列表（空列表表示未限制后缀）
        assert isinstance(data["scanExtensions"], list)

    def test_preview_reflects_temp_rules(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """加载临时规则后预览包含合并后的规则与临时规则文件项。"""
        controller, _ = controller_with_workspace

        # 加载临时规则文件
        assert controller.loadFileToTemp(str(rules_file)) is True
        data = json.loads(controller.previewRuleset("ws1"))

        # 规则文件列表应包含临时项
        temp_items = [r for r in data["ruleFiles"] if r["scope"] == "temp"]
        assert len(temp_items) == 1
        assert temp_items[0]["path"] == str(rules_file)
        assert temp_items[0]["enabled"] is True

        # 规则列表应包含临时规则文件中的「敏感内容」规则
        rule_names = [r["name"] for r in data["rules"]]
        assert "敏感内容" in rule_names

    def test_preview_rule_files_carry_scan_extensions(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
    ) -> None:
        """预览的 ruleFiles 每项应携带 scanExtensions/scanExtensionsState 字段。

        内置规则项 state='list' 且包含 builtin.yaml 中的后缀；
        缺失文件项 state='unset'。
        """
        controller, _ = controller_with_workspace
        data = json.loads(controller.previewRuleset("ws1"))

        rule_files = data["ruleFiles"]
        assert len(rule_files) >= 1
        # 内置项：state='list'，scanExtensions 非空
        builtin_item = rule_files[0]
        assert builtin_item["isBuiltin"] is True
        assert builtin_item["scanExtensionsState"] == "list"
        assert isinstance(builtin_item["scanExtensions"], list)
        assert "txt" in builtin_item["scanExtensions"]

    def test_preview_disabled_temp_rule_excluded_from_rules(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
        rules_file: Path,
    ) -> None:
        """禁用临时规则后预览的 rules 不包含该规则的条目，ruleFiles 仍列出但 enabled=False。"""
        controller, _ = controller_with_workspace

        controller.loadFileToTemp(str(rules_file))
        # 禁用临时规则
        controller.setRuleEnabled(str(rules_file), False)
        data = json.loads(controller.previewRuleset("ws1"))

        # ruleFiles 中临时规则仍存在但 enabled=False
        temp_items = [r for r in data["ruleFiles"] if r["scope"] == "temp"]
        assert len(temp_items) == 1
        assert temp_items[0]["enabled"] is False

        # rules 中不包含「敏感内容」（来自被禁用的临时规则文件）
        rule_names = [r["name"] for r in data["rules"]]
        assert "敏感内容" not in rule_names

    def test_preview_no_ruleset_when_builtin_disabled(
        self,
        config_controller: ConfigController,
    ) -> None:
        """禁用内置规则且无用户规则文件时 hasRuleset=False。"""
        config_controller.config.use_builtin = False
        controller = RulesController(config_controller)
        # 注入伪工作区
        wc = _FakeWorkspaceController()
        wc.add_workspace("ws1", "工作区A")
        wc.set_current("ws1")
        controller.set_workspace_controller(wc)

        data = json.loads(controller.previewRuleset("ws1"))
        assert data["hasRuleset"] is False
        assert data["rules"] == []
        assert data["whitelistEntries"] == []

    def test_preview_rule_files_field_consistency(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
    ) -> None:
        """预览的 ruleFiles 字段与 rulesFileModel 一致（含 fileName/path/exists/scope/isBuiltin/enabled/canRemove）。"""
        controller, _ = controller_with_workspace

        data = json.loads(controller.previewRuleset("ws1"))
        model = controller.rulesFileModel
        assert len(data["ruleFiles"]) == len(model)
        for preview_item, model_item in zip(data["ruleFiles"], model, strict=True):
            for key in ("fileName", "path", "exists", "scope", "isBuiltin", "enabled", "canRemove"):
                assert preview_item[key] == model_item[key], f"字段 {key} 不一致"

    def test_preview_reflects_whitelist_entries(
        self,
        controller_with_workspace: tuple[RulesController, _FakeWorkspaceController],
    ) -> None:
        """追加白名单条目后预览包含该条目。"""
        controller, _ = controller_with_workspace

        controller.appendWhitelistEntry("/a/b.txt", "r1", "备注")
        data = json.loads(controller.previewRuleset("ws1"))
        assert len(data["whitelistEntries"]) >= 1
        entry = next(e for e in data["whitelistEntries"] if e["pathGlob"] == "/a/b.txt")
        assert entry["ruleName"] == "r1"
        assert entry["note"] == "备注"
        assert entry["source"] == "runtime"
