"""``WorkspaceController`` 与 ``WorkspaceListModel`` 单元测试。

覆盖：

- ``WorkspaceListModel``：增删改查、roleNames、data() 按 role 返回、clear、items 只读视图
- ``WorkspaceItem``：mode_text/rules_text 派生属性、frozen 不可变
- ``WorkspaceController``：构造初始状态、addWorkspace/removeWorkspace、
  startScan/togglePause/cancelScan 委托 ScanController、exportResults、
  setCurrentWorkspaceId、currentScanController 兜底、cleanup 资源释放、
  _sync_workspace_state 信号回写
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from PySide2.QtCore import Qt

    from fuscan.config import Config  # noqa: F401
    from fuscan.gui.controllers.config_controller import ConfigController
    from fuscan.gui.controllers.rules_controller import RulesController
    from fuscan.gui.controllers.scan_controller import ScanController
    from fuscan.gui.controllers.workspace_controller import WorkspaceController
    from fuscan.gui.models.workspace_model import (
        WorkspaceItem,
        WorkspaceListModel,
    )

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过工作区控制器测试", allow_module_level=True)


# ============================= fixtures =============================


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
def rules_controller(config_controller: ConfigController) -> RulesController:
    return RulesController(config_controller)


@pytest.fixture()
def controller(
    config_controller: ConfigController,
    rules_controller: RulesController,
) -> WorkspaceController:
    return WorkspaceController(config_controller, rules_controller)


# ============================= WorkspaceItem =============================


class TestWorkspaceItem:
    """``WorkspaceItem`` 数据类的派生属性与不可变性。"""

    def test_default_values(self) -> None:
        """默认构造应有合理初值。"""
        item = WorkspaceItem(workspace_id="ws-1", name="任务 1")
        assert item.mode_str == "folder"
        assert item.target == ""
        assert item.rules_paths == ()
        assert item.use_builtin is True
        assert item.status_text == "就绪"
        assert item.matched_count == 0

    def test_mode_text_full(self) -> None:
        """full 模式应显示「全盘扫描」。"""
        item = WorkspaceItem(workspace_id="ws-1", name="t", mode_str="full")
        assert item.mode_text == "全盘扫描"

    def test_mode_text_drive(self) -> None:
        item = WorkspaceItem(workspace_id="ws-1", name="t", mode_str="drive")
        assert item.mode_text == "盘符扫描"

    def test_mode_text_folder(self) -> None:
        item = WorkspaceItem(workspace_id="ws-1", name="t", mode_str="folder")
        assert item.mode_text == "文件夹扫描"

    def test_mode_text_unknown_fallback(self) -> None:
        """未知模式字符串应回退为原值。"""
        item = WorkspaceItem(workspace_id="ws-1", name="t", mode_str="custom")
        assert item.mode_text == "custom"

    def test_rules_text_builtin_only(self) -> None:
        """仅启用内置规则时显示「内置规则」。"""
        item = WorkspaceItem(workspace_id="ws-1", name="t", use_builtin=True)
        assert item.rules_text == "内置规则"

    def test_rules_text_builtin_with_files(self) -> None:
        """内置 + 规则文件。"""
        item = WorkspaceItem(
            workspace_id="ws-1",
            name="t",
            rules_paths=("a.yaml", "b.yaml"),
            use_builtin=True,
        )
        assert item.rules_text == "内置 + 2 文件"

    def test_rules_text_files_only(self) -> None:
        """仅规则文件（无内置）。"""
        item = WorkspaceItem(
            workspace_id="ws-1",
            name="t",
            rules_paths=("a.yaml",),
            use_builtin=False,
        )
        assert item.rules_text == "1 文件"

    def test_rules_text_no_rules(self) -> None:
        """无规则。"""
        item = WorkspaceItem(workspace_id="ws-1", name="t", use_builtin=False)
        assert item.rules_text == "未配置规则"

    def test_rules_tags_builtin_only(self) -> None:
        """仅启用内置规则时返回单个内置 TAG。"""
        item = WorkspaceItem(workspace_id="ws-1", name="t", use_builtin=True)
        tags = item.rules_tags
        assert len(tags) == 1
        assert tags[0] == {"name": "内置", "is_builtin": True}

    def test_rules_tags_builtin_with_files(self) -> None:
        """内置 + 规则文件混合：内置在前，用户文件在后。"""
        item = WorkspaceItem(
            workspace_id="ws-1",
            name="t",
            rules_paths=("a.yaml", "b.json"),
            use_builtin=True,
        )
        tags = item.rules_tags
        assert len(tags) == 3
        assert tags[0] == {"name": "内置", "is_builtin": True}
        assert tags[1] == {"name": "a.yaml", "is_builtin": False}
        assert tags[2] == {"name": "b.json", "is_builtin": False}

    def test_rules_tags_files_only(self) -> None:
        """仅规则文件（无内置）：返回用户文件 TAG 列表。"""
        item = WorkspaceItem(
            workspace_id="ws-1",
            name="t",
            rules_paths=("rules.yaml",),
            use_builtin=False,
        )
        tags = item.rules_tags
        assert len(tags) == 1
        assert tags[0] == {"name": "rules.yaml", "is_builtin": False}

    def test_rules_tags_no_rules(self) -> None:
        """无规则：返回空列表。"""
        item = WorkspaceItem(workspace_id="ws-1", name="t", use_builtin=False)
        assert item.rules_tags == []

    def test_frozen_immutable(self) -> None:
        """frozen dataclass 应禁止字段赋值。"""
        item = WorkspaceItem(workspace_id="ws-1", name="t")
        # FrozenInstanceError 继承自 AttributeError
        with pytest.raises(AttributeError):
            item.name = "changed"  # type: ignore[misc]


# ============================= WorkspaceListModel =============================


def _make_item(
    ws_id: str = "ws-1",
    name: str = "任务 1",
    mode_str: str = "folder",
    target: str = "",
    rules_paths: tuple[str, ...] = (),
    use_builtin: bool = True,
    status_text: str = "就绪",
    matched_count: int = 0,
    passed_count: int = 0,
    skipped_count: int = 0,
    error_count: int = 0,
    last_summary: str = "",
) -> WorkspaceItem:
    """构造测试用 WorkspaceItem。"""
    return WorkspaceItem(
        workspace_id=ws_id,
        name=name,
        mode_str=mode_str,
        target=target,
        rules_paths=rules_paths,
        use_builtin=use_builtin,
        status_text=status_text,
        matched_count=matched_count,
        passed_count=passed_count,
        skipped_count=skipped_count,
        error_count=error_count,
        last_summary=last_summary,
    )


class TestWorkspaceListModel:
    """``WorkspaceListModel`` 的增删改查与 QAbstractListModel 接口。"""

    def test_initial_empty(self) -> None:
        """新构造的模型应为空。"""
        model = WorkspaceListModel()
        assert model.rowCount() == 0
        assert list(model.items) == []

    def test_role_names_returns_all_roles(self) -> None:
        """roleNames 应返回所有 12 个 role。"""
        model = WorkspaceListModel()
        roles = model.roleNames()
        assert len(roles) == 13
        assert roles[Qt.UserRole + 1] == b"workspaceId"
        assert roles[Qt.UserRole + 2] == b"name"
        assert roles[Qt.UserRole + 3] == b"modeText"
        assert roles[Qt.UserRole + 12] == b"index"
        assert roles[Qt.UserRole + 13] == b"rulesTags"

    def test_row_count_with_parent_index(self) -> None:
        """传有效 parent 时 rowCount 应为 0（扁平列表）。"""
        model = WorkspaceListModel()
        model.add_workspace(_make_item())
        # 构造一个「有效」的 parent（实际扁平列表不应有子项）
        parent = model.index(0, 0)
        # parent 为顶层 index 时也算有效，但 rowCount 应返回 0
        assert model.rowCount(parent) == 0

    def test_add_workspace_returns_row(self) -> None:
        """add_workspace 返回新增行号。"""
        model = WorkspaceListModel()
        row0 = model.add_workspace(_make_item("ws-1"))
        row1 = model.add_workspace(_make_item("ws-2"))
        assert row0 == 0
        assert row1 == 1
        assert model.rowCount() == 2

    def test_add_workspace_appends_to_items(self) -> None:
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1", "任务 1"))
        model.add_workspace(_make_item("ws-2", "任务 2"))
        items = list(model.items)
        assert len(items) == 2
        assert items[0].workspace_id == "ws-1"
        assert items[1].workspace_id == "ws-2"

    def test_data_returns_field_by_role(self) -> None:
        """data() 按 role 返回对应字段值。"""
        model = WorkspaceListModel()
        item = WorkspaceItem(
            workspace_id="ws-1",
            name="任务 1",
            mode_str="full",
            target="C:\\",
            rules_paths=("a.yaml",),
            use_builtin=True,
            status_text="扫描中",
            matched_count=5,
            passed_count=10,
            skipped_count=2,
            error_count=1,
            last_summary="用时 1.2s",
        )
        model.add_workspace(item)
        idx = model.index(0, 0)

        assert model.data(idx, Qt.UserRole + 1) == "ws-1"
        assert model.data(idx, Qt.UserRole + 2) == "任务 1"
        assert model.data(idx, Qt.UserRole + 3) == "全盘扫描"
        assert model.data(idx, Qt.UserRole + 4) == "C:\\"
        assert model.data(idx, Qt.UserRole + 5) == "内置 + 1 文件"
        assert model.data(idx, Qt.UserRole + 6) == "扫描中"
        assert model.data(idx, Qt.UserRole + 7) == 5
        assert model.data(idx, Qt.UserRole + 8) == 10
        assert model.data(idx, Qt.UserRole + 9) == 2
        assert model.data(idx, Qt.UserRole + 10) == 1
        assert model.data(idx, Qt.UserRole + 11) == "用时 1.2s"
        assert model.data(idx, Qt.UserRole + 12) == 0

    def test_data_invalid_index_returns_empty(self) -> None:
        """无效 index 时 data() 返回空字符串。"""
        model = WorkspaceListModel()
        model.add_workspace(_make_item())
        # 越界 index
        invalid = model.index(99, 0)
        assert model.data(invalid, Qt.UserRole + 1) == ""
        # 未识别 role
        idx = model.index(0, 0)
        assert model.data(idx, Qt.UserRole + 999) == ""

    def test_get_workspace_by_id(self) -> None:
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1", "任务 1"))
        model.add_workspace(_make_item("ws-2", "任务 2"))
        found = model.get_workspace("ws-2")
        assert found is not None
        assert found.name == "任务 2"

    def test_get_workspace_not_found_returns_none(self) -> None:
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1"))
        assert model.get_workspace("ws-missing") is None

    def test_get_by_index(self) -> None:
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1", "任务 1"))
        model.add_workspace(_make_item("ws-2", "任务 2"))
        item = model.get_by_index(1)
        assert item is not None
        assert item.workspace_id == "ws-2"

    def test_get_by_index_out_of_range(self) -> None:
        model = WorkspaceListModel()
        assert model.get_by_index(-1) is None
        assert model.get_by_index(0) is None

    def test_remove_workspace_success(self) -> None:
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1"))
        model.add_workspace(_make_item("ws-2"))
        assert model.remove_workspace("ws-1") is True
        assert model.rowCount() == 1
        assert model.get_workspace("ws-1") is None
        assert model.get_workspace("ws-2") is not None

    def test_remove_workspace_not_found(self) -> None:
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1"))
        assert model.remove_workspace("ws-missing") is False
        assert model.rowCount() == 1

    def test_update_workspace_success(self) -> None:
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1", status_text="就绪"))
        assert model.update_workspace("ws-1", status_text="扫描中", matched_count=3) is True
        item = model.get_workspace("ws-1")
        assert item is not None
        assert item.status_text == "扫描中"
        assert item.matched_count == 3

    def test_update_workspace_not_found(self) -> None:
        model = WorkspaceListModel()
        assert model.update_workspace("ws-missing", status_text="x") is False

    def test_clear(self) -> None:
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1"))
        model.add_workspace(_make_item("ws-2"))
        model.clear()
        assert model.rowCount() == 0
        assert list(model.items) == []

    def test_items_property_is_tuple(self) -> None:
        """items 应返回 tuple（只读视图，防止外部修改内部列表）。"""
        model = WorkspaceListModel()
        model.add_workspace(_make_item())
        items = model.items
        assert isinstance(items, tuple)


# ============================= WorkspaceController =============================


class TestControllerInitialState:
    """``WorkspaceController`` 构造初始状态。"""

    def test_initial_count_zero(self, controller: WorkspaceController) -> None:
        assert controller.workspaceCount == 0

    def test_initial_current_workspace_empty(self, controller: WorkspaceController) -> None:
        assert controller.currentWorkspaceId == ""
        assert controller.hasCurrentWorkspace is False

    def test_workspace_model_exposed(self, controller: WorkspaceController) -> None:
        assert isinstance(controller.workspaceModel, WorkspaceListModel)

    def test_current_scan_controller_fallback(self, controller: WorkspaceController) -> None:
        """未选中工作区时 currentScanController 应返回兜底 ScanController。"""
        sc = controller.currentScanController
        assert isinstance(sc, ScanController)

    def test_children_parented_to_controller(
        self,
        controller: WorkspaceController,
    ) -> None:
        """model 应以 controller 为 parent。"""
        assert controller.workspaceModel.parent() is controller


class TestAddWorkspace:
    """``addWorkspace`` 槽。"""

    def test_returns_ws_id_with_prefix(self, controller: WorkspaceController) -> None:
        ws_id = controller.addWorkspace("任务 1", "folder", "/tmp", "[]", True)
        assert ws_id.startswith("ws-")
        assert len(ws_id) == len("ws-") + 8  # token_hex(4) → 8 字符

    def test_increments_count(self, controller: WorkspaceController) -> None:
        controller.addWorkspace("任务 1", "folder", "/tmp", "[]", True)
        controller.addWorkspace("任务 2", "folder", "/tmp", "[]", True)
        assert controller.workspaceCount == 2

    def test_empty_name_auto_generated(self, controller: WorkspaceController) -> None:
        """空名称应自动生成「任务 N」。"""
        ws_id = controller.addWorkspace("", "folder", "/tmp", "[]", True)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.name == "任务 1"

    def test_empty_name_auto_increment(self, controller: WorkspaceController) -> None:
        controller.addWorkspace("", "folder", "/tmp", "[]", True)
        ws_id2 = controller.addWorkspace("", "folder", "/tmp", "[]", True)
        item = controller.workspaceModel.get_workspace(ws_id2)
        assert item is not None
        assert item.name == "任务 2"

    def test_parses_rules_paths_json(self, controller: WorkspaceController) -> None:
        """rules_paths_json 应解析为 tuple。"""
        rules_json = json.dumps(["a.yaml", "b.yaml"])
        ws_id = controller.addWorkspace("t", "folder", "/tmp", rules_json, True)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.rules_paths == ("a.yaml", "b.yaml")
        assert item.use_builtin is True

    def test_invalid_rules_paths_json_falls_back_to_empty(
        self,
        controller: WorkspaceController,
    ) -> None:
        """无效 JSON 应回退为空 tuple。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "not-json", True)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.rules_paths == ()

    def test_empty_rules_paths_json(self, controller: WorkspaceController) -> None:
        """空字符串应解析为空 tuple。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "", True)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.rules_paths == ()

    def test_creates_independent_scan_controller(self, controller: WorkspaceController) -> None:
        """每个工作区应有独立的 ScanController。"""
        ws_id1 = controller.addWorkspace("t1", "folder", "/tmp", "[]", True)
        ws_id2 = controller.addWorkspace("t2", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id1)
        sc1 = controller.currentScanController
        controller.setCurrentWorkspaceId(ws_id2)
        sc2 = controller.currentScanController
        assert sc1 is not sc2

    def test_full_mode_initializes_scan_mode(
        self,
        controller: WorkspaceController,
    ) -> None:
        """full 模式应设置 ScanController.scanModeIndex=0。"""
        ws_id = controller.addWorkspace("t", "full", "", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        assert sc.scanModeIndex == 0

    def test_drive_mode_sets_selected_drive(self, controller: WorkspaceController) -> None:
        """drive 模式应同步设置 selectedDrive。"""
        ws_id = controller.addWorkspace("t", "drive", "C:\\", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        assert sc.scanModeIndex == 1
        assert sc.selectedDrive == "C:\\"

    def test_folder_mode_sets_folder_root(self, controller: WorkspaceController) -> None:
        """folder 模式应同步设置 folderRoot。"""
        ws_id = controller.addWorkspace("t", "folder", "/custom/path", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        assert sc.scanModeIndex == 2
        assert sc.folderRoot == "/custom/path"

    def test_unknown_mode_defaults_to_folder(
        self,
        controller: WorkspaceController,
    ) -> None:
        """未知模式字符串应回退为 folder（索引 2）。"""
        ws_id = controller.addWorkspace("t", "custom", "/x", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        assert sc.scanModeIndex == 2

    def test_workspace_list_changed_emitted(
        self,
        controller: WorkspaceController,
    ) -> None:
        """addWorkspace 应 emit workspaceListChanged。"""
        emitted: list[None] = []
        controller.workspaceListChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        assert len(emitted) == 1


class TestRemoveWorkspace:
    """``removeWorkspace`` 槽。"""

    def test_removes_from_model(self, controller: WorkspaceController) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.removeWorkspace(ws_id)
        assert controller.workspaceCount == 0
        assert controller.workspaceModel.get_workspace(ws_id) is None

    def test_emits_workspace_list_changed(
        self,
        controller: WorkspaceController,
    ) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        emitted: list[None] = []
        controller.workspaceListChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.removeWorkspace(ws_id)
        assert len(emitted) == 1

    def test_clears_current_workspace_id_if_matches(
        self,
        controller: WorkspaceController,
    ) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        assert controller.hasCurrentWorkspace is True
        controller.removeWorkspace(ws_id)
        assert controller.currentWorkspaceId == ""
        assert controller.hasCurrentWorkspace is False

    def test_keeps_current_workspace_id_if_different(
        self,
        controller: WorkspaceController,
    ) -> None:
        ws_id1 = controller.addWorkspace("t1", "folder", "/tmp", "[]", True)
        ws_id2 = controller.addWorkspace("t2", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id1)
        controller.removeWorkspace(ws_id2)
        assert controller.currentWorkspaceId == ws_id1

    def test_remove_nonexistent_noop(self, controller: WorkspaceController) -> None:
        """移除不存在的工作区应静默忽略。"""
        emitted: list[None] = []
        controller.workspaceListChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.removeWorkspace("ws-nonexistent")
        assert len(emitted) == 0
        assert controller.workspaceCount == 0

    def test_cleanup_called_on_scan_controller(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """removeWorkspace 应调用对应 ScanController.cleanup。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        called = False

        def fake_cleanup() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(sc, "cleanup", fake_cleanup)
        controller.removeWorkspace(ws_id)
        assert called is True


class TestCurrentWorkspace:
    """``setCurrentWorkspaceId`` / ``currentScanController``。"""

    def test_set_current_workspace_emits_signal(
        self,
        controller: WorkspaceController,
    ) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        emitted: list[None] = []
        controller.currentWorkspaceChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setCurrentWorkspaceId(ws_id)
        assert len(emitted) == 1
        assert controller.currentWorkspaceId == ws_id
        assert controller.hasCurrentWorkspace is True

    def test_set_same_current_workspace_noop(
        self,
        controller: WorkspaceController,
    ) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        emitted: list[None] = []
        controller.currentWorkspaceChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setCurrentWorkspaceId(ws_id)
        assert len(emitted) == 0

    def test_current_scan_controller_returns_associated(
        self,
        controller: WorkspaceController,
    ) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        assert isinstance(sc, ScanController)

    def test_current_scan_controller_fallback_when_not_set(
        self,
        controller: WorkspaceController,
    ) -> None:
        """无当前工作区时应返回 fallback ScanController（同一实例）。"""
        sc1 = controller.currentScanController
        sc2 = controller.currentScanController
        assert sc1 is sc2  # 同一兜底实例


class TestScanControlDelegation:
    """``startScan`` / ``togglePause`` / ``cancelScan`` 委托 ScanController。"""

    def test_start_scan_calls_scan_controller(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        called = False

        def fake_start() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(sc, "startScan", fake_start)
        controller.startScan(ws_id)
        assert called is True

    def test_start_scan_nonexistent_noop(
        self,
        controller: WorkspaceController,
    ) -> None:
        """不存在的工作区 ID 应静默忽略（仅记录 warning）。"""
        controller.startScan("ws-nonexistent")  # 不抛异常即可

    def test_toggle_pause_calls_scan_controller(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        called = False

        def fake_toggle() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(sc, "togglePause", fake_toggle)
        controller.togglePause(ws_id)
        assert called is True

    def test_toggle_pause_nonexistent_noop(self, controller: WorkspaceController) -> None:
        controller.togglePause("ws-nonexistent")

    def test_cancel_scan_calls_scan_controller(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        called = False

        def fake_cancel() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(sc, "cancelScan", fake_cancel)
        controller.cancelScan(ws_id)
        assert called is True

    def test_cancel_scan_nonexistent_noop(self, controller: WorkspaceController) -> None:
        controller.cancelScan("ws-nonexistent")


class TestExportResults:
    """``exportResults`` 委托 ScanController。"""

    def test_export_results_calls_scan_controller(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        captured: dict[str, object] = {}

        def fake_export(fmt: str, path_str: str) -> None:
            captured["fmt"] = fmt
            captured["path"] = path_str

        monkeypatch.setattr(sc, "exportResults", fake_export)
        export_path = str(tmp_path / "out.csv")
        controller.exportResults(ws_id, "csv", export_path)
        assert captured == {"fmt": "csv", "path": export_path}

    def test_export_results_nonexistent_noop(
        self,
        controller: WorkspaceController,
        tmp_path: Path,
    ) -> None:
        controller.exportResults("ws-nonexistent", "csv", str(tmp_path / "x.csv"))


class TestWorkspaceExists:
    """``workspaceExists`` 槽。"""

    def test_exists_true(self, controller: WorkspaceController) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        assert controller.workspaceExists(ws_id) is True

    def test_exists_false(self, controller: WorkspaceController) -> None:
        assert controller.workspaceExists("ws-nonexistent") is False


class TestSyncWorkspaceState:
    """``_sync_workspace_state`` 信号回写。"""

    def test_sync_updates_status_text_and_counts(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ScanController 状态变化应回写到 WorkspaceItem。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)

        # 取出关联的 ScanController
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController

        # 用 monkeypatch 修改 ScanController 的属性读取
        # ScanController 内部状态用私有字段，通过 Property 读取
        # 直接修改私有字段以模拟扫描完成
        sc._scan_state = "results"  # type: ignore[attr-defined]
        sc._matched_count = 5  # type: ignore[attr-defined]
        sc._passed_count = 10  # type: ignore[attr-defined]
        sc._skipped_count = 2  # type: ignore[attr-defined]
        sc._error_count = 1  # type: ignore[attr-defined]
        sc._status_summary = "用时 1.5s"  # type: ignore[attr-defined]
        sc._status_text = "已完成"  # type: ignore[attr-defined]

        controller._sync_workspace_state(ws_id)

        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.status_text == "已完成"
        assert item.matched_count == 5
        assert item.passed_count == 10
        assert item.skipped_count == 2
        assert item.error_count == 1
        assert item.last_summary == "用时 1.5s"

    def test_sync_scanning_state(self, controller: WorkspaceController) -> None:
        """scanning 状态应回写为「扫描中」。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.status_text == "扫描中"

    def test_sync_paused_state(self, controller: WorkspaceController) -> None:
        """scanning + isPaused=True 应回写为「已暂停」。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = True  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.status_text == "已暂停"

    def test_sync_nonexistent_noop(self, controller: WorkspaceController) -> None:
        """不存在的工作区 ID 应静默忽略。"""
        controller._sync_workspace_state("ws-nonexistent")  # 不抛异常即可


class TestCleanup:
    """``cleanup`` 资源释放。"""

    def test_cleanup_clears_model(self, controller: WorkspaceController) -> None:
        controller.addWorkspace("t1", "folder", "/tmp", "[]", True)
        controller.addWorkspace("t2", "folder", "/tmp", "[]", True)
        controller.cleanup()
        assert controller.workspaceCount == 0

    def test_cleanup_calls_scan_controller_cleanup(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        called = False

        def fake_cleanup() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(sc, "cleanup", fake_cleanup)
        controller.cleanup()
        assert called is True

    def test_cleanup_clears_current_workspace(self, controller: WorkspaceController) -> None:
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        controller.cleanup()
        assert controller.currentWorkspaceId == ""
        assert controller.hasCurrentWorkspace is False

    def test_cleanup_no_workspaces_noop(self, controller: WorkspaceController) -> None:
        """无工作区时 cleanup 不应抛异常。"""
        controller.cleanup()


class TestActiveScan:
    """``activeScanWorkspaceId`` / ``hasActiveScan`` / ``activeScanController`` 等。

    验证扫描中（含暂停态）工作区被标记为 active，扫描结束后清空，
    HomePage 据此切换扫描进度面板与工作区列表视图。
    """

    def test_initial_no_active_scan(self, controller: WorkspaceController) -> None:
        """构造初始应无扫描任务进行。"""
        assert controller.activeScanWorkspaceId == ""
        assert controller.hasActiveScan is False

    def test_active_scan_set_when_scanning(
        self,
        controller: WorkspaceController,
    ) -> None:
        """工作区进入 scanning 态应被标记为 active。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]

        controller._sync_workspace_state(ws_id)

        assert controller.activeScanWorkspaceId == ws_id
        assert controller.hasActiveScan is True

    def test_paused_keeps_active(self, controller: WorkspaceController) -> None:
        """暂停态（scanning + isPaused=True）应保留 active。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert controller.hasActiveScan is True

        # 切到暂停
        sc._is_paused = True  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert controller.hasActiveScan is True
        assert controller.activeScanWorkspaceId == ws_id

    def test_results_clears_active(self, controller: WorkspaceController) -> None:
        """扫描完成（results 态）应清空 active。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        # 进入扫描
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert controller.hasActiveScan is True
        # 完成
        sc._scan_state = "results"  # type: ignore[attr-defined]
        sc._status_text = "已完成"  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert controller.hasActiveScan is False
        assert controller.activeScanWorkspaceId == ""

    def test_setup_clears_active(self, controller: WorkspaceController) -> None:
        """扫描取消/失败回 setup 态应清空 active。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        # 取消回 setup
        sc._scan_state = "setup"  # type: ignore[attr-defined]
        sc._status_text = "已取消"  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert controller.hasActiveScan is False

    def test_active_scan_controller_returns_associated(
        self,
        controller: WorkspaceController,
    ) -> None:
        """activeScanController 应返回扫描中工作区的 ScanController。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)

        active_sc = controller.activeScanController
        assert active_sc is sc

    def test_active_scan_controller_fallback_when_no_active(
        self,
        controller: WorkspaceController,
    ) -> None:
        """无扫描任务时 activeScanController 应返回兜底实例。"""
        sc = controller.activeScanController
        assert isinstance(sc, ScanController)

    def test_active_scan_workspace_metadata(
        self,
        controller: WorkspaceController,
    ) -> None:
        """activeScanWorkspaceName/ModeText/Target 应返回工作区元数据。"""
        ws_id = controller.addWorkspace("我的任务", "full", "", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)

        assert controller.activeScanWorkspaceName == "我的任务"
        assert controller.activeScanModeText == "全盘扫描"
        assert controller.activeScanTarget == ""

    def test_active_scan_metadata_empty_when_no_active(
        self,
        controller: WorkspaceController,
    ) -> None:
        """无扫描任务时元数据 Property 应返回空串。"""
        assert controller.activeScanWorkspaceName == ""
        assert controller.activeScanModeText == ""
        assert controller.activeScanTarget == ""

    def test_active_scan_changed_emitted_on_enter(
        self,
        controller: WorkspaceController,
    ) -> None:
        """进入扫描态应 emit activeScanChanged。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        emitted: list[None] = []
        controller.activeScanChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]

        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert len(emitted) == 1

    def test_active_scan_changed_emitted_on_exit(
        self,
        controller: WorkspaceController,
    ) -> None:
        """离开扫描态应 emit activeScanChanged。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        # 先进入扫描
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)

        emitted: list[None] = []
        controller.activeScanChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        # 完成
        sc._scan_state = "results"  # type: ignore[attr-defined]
        sc._status_text = "已完成"  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert len(emitted) == 1

    def test_active_scan_changed_not_emitted_when_no_change(
        self,
        controller: WorkspaceController,
    ) -> None:
        """状态不变时不应 emit activeScanChanged。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)

        emitted: list[None] = []
        controller.activeScanChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        # 仍是 scanning 态，仅进度变化
        sc._progress_scanned = 100  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert len(emitted) == 0

    def test_remove_active_workspace_clears_active(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """删除扫描中的工作区应清空 active 状态。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert controller.hasActiveScan is True

        # mock cleanup 避免触发 worker 清理
        monkeypatch.setattr(sc, "cleanup", lambda: None)
        controller.removeWorkspace(ws_id)
        assert controller.hasActiveScan is False
        assert controller.activeScanWorkspaceId == ""

    def test_cleanup_clears_active(self, controller: WorkspaceController) -> None:
        """cleanup 应清空 active 状态。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert controller.hasActiveScan is True

        controller.cleanup()
        assert controller.hasActiveScan is False
        assert controller.activeScanWorkspaceId == ""
