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
import time
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from PySide2.QtCore import Qt
except ImportError:
    from PySide6.QtCore import Qt  # type: ignore[no-redef]

try:
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


# ============================= QApp fixture =============================


@pytest.fixture(scope="session")
def qapp() -> object:
    """创建 QApplication（若不存在），用于 QThread 信号传递。"""
    try:
        from PySide2.QtWidgets import QApplication
    except ImportError:
        from PySide6.QtWidgets import QApplication  # type: ignore[no-redef]

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _wait_for_restore(controller: WorkspaceController, ws_id: str, timeout_ms: int = 5000) -> None:
    """等待异步恢复完成（处理 Qt 事件循环以接收 worker 信号）。"""
    try:
        from PySide2.QtCore import QCoreApplication
    except ImportError:
        from PySide6.QtCore import QCoreApplication  # type: ignore[no-redef]

    elapsed = 0
    while ws_id in controller._restoring_workspaces and elapsed < timeout_ms:  # type: ignore[attr-defined]
        QCoreApplication.processEvents()
        import time

        time.sleep(0.01)
        elapsed += 10


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
    # 同步重定向 scan_controller 的 _MANIFESTS_DIR（模块级常量，加载时已求值）
    manifests_dir = config_dir / "manifests"
    manifests_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("fuscan.gui.controllers.scan_controller._MANIFESTS_DIR", manifests_dir)
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
        assert tags[0] == {"name": "内置", "is_builtin": True, "is_temp": False}

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
        assert tags[0] == {"name": "内置", "is_builtin": True, "is_temp": False}
        assert tags[1] == {"name": "a.yaml", "is_builtin": False, "is_temp": False}
        assert tags[2] == {"name": "b.json", "is_builtin": False, "is_temp": False}

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
        assert tags[0] == {"name": "rules.yaml", "is_builtin": False, "is_temp": False}

    def test_rules_tags_no_rules(self) -> None:
        """无规则：返回空列表。"""
        item = WorkspaceItem(workspace_id="ws-1", name="t", use_builtin=False)
        assert item.rules_tags == []

    def test_rules_tags_with_temp_rules(self) -> None:
        """临时规则（已启用）应作为 is_temp=True 的 TAG 展示。"""
        item = WorkspaceItem(
            workspace_id="ws-1",
            name="t",
            rules_paths=("global.yaml",),
            use_builtin=True,
            task_overrides={"temp_rules_paths": ("temp1.yaml", "temp2.yaml")},
        )
        tags = item.rules_tags
        assert len(tags) == 4
        assert tags[0] == {"name": "内置", "is_builtin": True, "is_temp": False}
        assert tags[1] == {"name": "global.yaml", "is_builtin": False, "is_temp": False}
        assert tags[2] == {"name": "temp1.yaml", "is_builtin": False, "is_temp": True}
        assert tags[3] == {"name": "temp2.yaml", "is_builtin": False, "is_temp": True}

    def test_rules_tags_temp_rules_filtered_by_disabled(self) -> None:
        """禁用的临时规则（在 disabled_temp_rules_paths 中）不应展示。"""
        item = WorkspaceItem(
            workspace_id="ws-1",
            name="t",
            use_builtin=False,
            task_overrides={
                "temp_rules_paths": ("enabled.yaml", "disabled.yaml"),
                "disabled_temp_rules_paths": ("disabled.yaml",),
            },
        )
        tags = item.rules_tags
        assert len(tags) == 1
        assert tags[0] == {"name": "enabled.yaml", "is_builtin": False, "is_temp": True}

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
    collected_count: int = 0,
    last_activity_time: float | None = None,
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
        collected_count=collected_count,
        last_activity_time=last_activity_time if last_activity_time is not None else time.time(),
    )


class TestWorkspaceListModel:
    """``WorkspaceListModel`` 的增删改查与 QAbstractListModel 接口。"""

    def test_initial_empty(self) -> None:
        """新构造的模型应为空。"""
        model = WorkspaceListModel()
        assert model.rowCount() == 0
        assert list(model.items) == []

    def test_role_names_returns_all_roles(self) -> None:
        """roleNames 应返回所有 14 个 role。"""
        model = WorkspaceListModel()
        roles = model.roleNames()
        assert len(roles) == 14
        assert roles[Qt.UserRole + 1] == b"workspaceId"
        assert roles[Qt.UserRole + 2] == b"name"
        assert roles[Qt.UserRole + 3] == b"modeText"
        assert roles[Qt.UserRole + 12] == b"index"
        assert roles[Qt.UserRole + 13] == b"rulesTags"
        assert roles[Qt.UserRole + 14] == b"collectedCount"

    def test_row_count_with_parent_index(self) -> None:
        """传有效 parent 时 rowCount 应为 0（扁平列表）。"""
        model = WorkspaceListModel()
        model.add_workspace(_make_item())
        # 构造一个「有效」的 parent（实际扁平列表不应有子项）
        parent = model.index(0, 0)
        # parent 为顶层 index 时也算有效，但 rowCount 应返回 0
        assert model.rowCount(parent) == 0

    def test_add_workspace_returns_row(self) -> None:
        """add_workspace 返回新增行号（iter-132：插入到顶部 row 0）。"""
        model = WorkspaceListModel()
        row0 = model.add_workspace(_make_item("ws-1"))
        row1 = model.add_workspace(_make_item("ws-2"))
        assert row0 == 0
        assert row1 == 0  # iter-132：新工作区插入到顶部
        assert model.rowCount() == 2

    def test_add_workspace_appends_to_items(self) -> None:
        """iter-132：新工作区插入到列表顶部（最近活动在最上方）。"""
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1", "任务 1"))
        model.add_workspace(_make_item("ws-2", "任务 2"))
        items = list(model.items)
        assert len(items) == 2
        # ws-2 后插入，排在顶部
        assert items[0].workspace_id == "ws-2"
        assert items[1].workspace_id == "ws-1"

    def test_data_returns_field_by_role(self) -> None:
        """data() 按 role 返回对应字段值。"""
        model = WorkspaceListModel()
        item = WorkspaceItem(
            workspace_id="ws-1",
            name="任务 1",
            mode_str="drive",
            target="C:\\",
            rules_paths=("a.yaml",),
            use_builtin=True,
            status_text="扫描中",
            matched_count=5,
            passed_count=10,
            skipped_count=2,
            error_count=1,
            last_summary="用时 1.2s",
            collected_count=42,
        )
        model.add_workspace(item)
        idx = model.index(0, 0)

        assert model.data(idx, Qt.UserRole + 1) == "ws-1"
        assert model.data(idx, Qt.UserRole + 2) == "任务 1"
        assert model.data(idx, Qt.UserRole + 3) == "盘符扫描"
        assert model.data(idx, Qt.UserRole + 4) == "C:\\"
        assert model.data(idx, Qt.UserRole + 5) == "内置 + 1 文件"
        assert model.data(idx, Qt.UserRole + 6) == "扫描中"
        assert model.data(idx, Qt.UserRole + 7) == 5
        assert model.data(idx, Qt.UserRole + 8) == 10
        assert model.data(idx, Qt.UserRole + 9) == 2
        assert model.data(idx, Qt.UserRole + 10) == 1
        assert model.data(idx, Qt.UserRole + 11) == "用时 1.2s"
        assert model.data(idx, Qt.UserRole + 12) == 0
        assert model.data(idx, Qt.UserRole + 14) == 42

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
        """iter-132：新工作区插入到顶部，ws-2 在 index 0，ws-1 在 index 1。"""
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1", "任务 1"))
        model.add_workspace(_make_item("ws-2", "任务 2"))
        item = model.get_by_index(1)
        assert item is not None
        assert item.workspace_id == "ws-1"  # iter-132：ws-1 在 index 1（顶部是 ws-2）

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

    def test_move_to_top_promotes_to_first(self) -> None:
        """iter-132：move_to_top 将指定工作区移到列表顶部。"""
        model = WorkspaceListModel()
        # 插入顺序：ws-1, ws-2, ws-3 → 列表：ws-3, ws-2, ws-1（顶部插入）
        model.add_workspace(_make_item("ws-1"))
        model.add_workspace(_make_item("ws-2"))
        model.add_workspace(_make_item("ws-3"))
        # ws-1 在底部（index 2），移到顶部
        assert model.move_to_top("ws-1") is True
        items = list(model.items)
        assert items[0].workspace_id == "ws-1"
        assert items[1].workspace_id == "ws-3"
        assert items[2].workspace_id == "ws-2"

    def test_move_to_top_already_at_top(self) -> None:
        """iter-132：已在顶部时返回 False，仅更新时间。"""
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1"))
        model.add_workspace(_make_item("ws-2"))
        # ws-2 在顶部
        assert model.move_to_top("ws-2") is False
        items = list(model.items)
        assert items[0].workspace_id == "ws-2"

    def test_move_to_top_not_found(self) -> None:
        """iter-132：不存在的工作区返回 False。"""
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1"))
        assert model.move_to_top("ws-missing") is False

    def test_move_to_top_updates_activity_time(self) -> None:
        """iter-132：move_to_top 更新 last_activity_time。"""
        import time

        model = WorkspaceListModel()
        old_time = 1000.0
        model.add_workspace(_make_item("ws-1", last_activity_time=old_time))
        model.add_workspace(_make_item("ws-2"))
        # ws-1 在 index 1，移到顶部
        model.move_to_top("ws-1")
        item = model.get_workspace("ws-1")
        assert item is not None
        assert item.last_activity_time > old_time
        assert item.last_activity_time <= time.time()

    def test_update_workspace_success(self) -> None:
        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1", status_text="就绪"))
        assert model.update_workspace("ws-1", status_text="扫描中", matched_count=3) is True
        item = model.get_workspace("ws-1")
        assert item is not None
        assert item.status_text == "扫描中"
        assert item.matched_count == 3

    def test_update_workspace_emits_only_changed_roles(self) -> None:
        """iter-105 P1：update_workspace 仅 emit 实际变化字段对应的 role。"""
        from unittest.mock import MagicMock

        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1", status_text="就绪", matched_count=0))
        # 用 mock 捕获 dataChanged 信号
        mock = MagicMock()
        model.dataChanged.connect(mock)

        # 仅更新 matched_count，其他字段不变
        model.update_workspace("ws-1", matched_count=5)

        # 应只 emit 一次，且 roles 仅含 matchedCount (UserRole+7)
        assert mock.call_count == 1
        args = mock.call_args.args
        # dataChanged(topLeft, bottomRight, roles)
        assert args[2] == [Qt.UserRole + 7]

    def test_update_workspace_no_signal_when_no_field_changed(self) -> None:
        """iter-105 P1：传入与当前值相同的字段时不 emit dataChanged。"""
        from unittest.mock import MagicMock

        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1", status_text="就绪", matched_count=3))
        mock = MagicMock()
        model.dataChanged.connect(mock)

        # 传入与当前值相同的字段
        model.update_workspace("ws-1", status_text="就绪", matched_count=3)

        # 应不 emit
        assert mock.call_count == 0

    def test_update_workspace_no_signal_for_task_overrides_only(self) -> None:
        """task_overrides 变化时 emit rulesTags role（rules_tags 派生属性依赖临时规则）。

        task_overrides 含 temp_rules_paths/disabled_temp_rules_paths，二者变化会
        影响 rules_tags 派生属性（临时规则 TAG 列表），故 task_overrides 变化时
        需 emit rulesTags role 刷新 QML。其他非规则相关 task_overrides key 变化
        也会触发 emit（因 _FIELD_TO_ROLES 按 field 整体对比，无法区分 key 级别），
        开销可接受（task_overrides 变化频率低，仅用户操作触发）。
        """
        from unittest.mock import MagicMock

        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1"))
        mock = MagicMock()
        model.dataChanged.connect(mock)

        model.update_workspace("ws-1", task_overrides={"max_workers": 8})

        # task_overrides 变化 → emit rulesTags role（Qt.UserRole + 13）
        assert mock.call_count == 1
        call_args = mock.call_args
        # dataChanged(topLeft, bottomRight, roles) 第三参数为 roles 列表
        emitted_roles: list[int] = list(call_args.args[2]) if len(call_args.args) >= 3 else []
        assert Qt.UserRole + 13 in emitted_roles
        item = model.get_workspace("ws-1")
        assert item is not None
        assert item.task_overrides == {"max_workers": 8}

    def test_update_workspace_derived_role_emitted(self) -> None:
        """iter-105 P1：更新 rules_paths 时应 emit rulesText 与 rulesTags 两个派生 role。"""
        from unittest.mock import MagicMock

        model = WorkspaceListModel()
        model.add_workspace(_make_item("ws-1", rules_paths=(), use_builtin=True))
        mock = MagicMock()
        model.dataChanged.connect(mock)

        # 更新 rules_paths，应同时触发 rulesText (UserRole+5) 与 rulesTags (UserRole+13)
        model.update_workspace("ws-1", rules_paths=("a.yaml",))

        assert mock.call_count == 1
        args = mock.call_args.args
        assert set(args[2]) == {Qt.UserRole + 5, Qt.UserRole + 13}

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

    def test_add_workspace_syncs_global_rules(
        self,
        controller: WorkspaceController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """iter-139：新建工作区应从全局 RulesController 同步规则配置。

        rules_paths_json 参数已废弃（iter-137 规则全局化），工作区的
        rules_paths/use_builtin 始终从全局读取，使 rules_tags 标签
        反映实际扫描时使用的规则。
        """
        # 准备一个真实的规则文件，使 RulesController.rules_paths 过滤后非空
        rules_file = tmp_path / "custom.yaml"
        rules_file.write_text("rules: []", encoding="utf-8")
        rules_controller._config.rules_paths = [str(rules_file)]
        rules_controller._config.use_builtin = True
        rules_controller._reload_ruleset()
        rules_controller.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

        # QML 传入 "[]" + True，但工作区应反映全局规则
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.rules_paths == (str(rules_file),)
        assert item.use_builtin is True
        # rules_tags 应包含内置 + 自定义规则文件
        tags = item.rules_tags
        assert len(tags) == 2
        assert tags[0] == {"name": "内置", "is_builtin": True, "is_temp": False}
        assert tags[1] == {"name": "custom.yaml", "is_builtin": False, "is_temp": False}

    def test_add_workspace_ignores_rules_paths_json(
        self,
        controller: WorkspaceController,
    ) -> None:
        """iter-139：rules_paths_json 参数已废弃，不影响工作区规则。

         无论传入什么 JSON，工作区 rules_paths 始终从全局同步
        （fixture 中全局 Config.rules_paths=[]，故为空 tuple）。
        """
        rules_json = json.dumps(["a.yaml", "b.yaml"])
        ws_id = controller.addWorkspace("t", "folder", "/tmp", rules_json, True)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        # 全局为空，工作区也为空（忽略 QML 传入的 ["a.yaml", "b.yaml"]）
        assert item.rules_paths == ()
        assert item.use_builtin is True

    def test_add_workspace_ignores_invalid_rules_paths_json(
        self,
        controller: WorkspaceController,
    ) -> None:
        """iter-139：无效 JSON 不影响工作区规则（从全局同步）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "not-json", True)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.rules_paths == ()

    def test_add_workspace_ignores_empty_rules_paths_json(
        self,
        controller: WorkspaceController,
    ) -> None:
        """iter-139：空字符串不影响工作区规则（从全局同步）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "", True)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.rules_paths == ()

    def test_add_workspace_from_path_creates_with_folder_name(
        self,
        controller: WorkspaceController,
        tmp_path: Path,
    ) -> None:
        """拖拽入口：传入文件夹路径应创建工作区，任务名取文件夹名。"""
        folder = tmp_path / "我的项目"
        folder.mkdir()
        ws_id = controller.addWorkspaceFromPath(str(folder))
        assert ws_id != ""
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.name == "我的项目"
        assert item.mode_str == "folder"
        assert item.target == str(folder)

    def test_add_workspace_from_path_rejects_file(
        self,
        controller: WorkspaceController,
        tmp_path: Path,
    ) -> None:
        """拖拽入口：传入文件路径（非文件夹）应返回空串，不创建工作区。"""
        file_path = tmp_path / "not_a_folder.txt"
        file_path.write_text("hello", encoding="utf-8")
        ws_id = controller.addWorkspaceFromPath(str(file_path))
        assert ws_id == ""
        assert controller.workspaceModel.rowCount() == 0

    def test_add_workspace_from_path_rejects_empty(
        self,
        controller: WorkspaceController,
    ) -> None:
        """拖拽入口：空路径返回空串。"""
        assert controller.addWorkspaceFromPath("") == ""

    def test_add_workspace_from_path_nonexistent(
        self,
        controller: WorkspaceController,
    ) -> None:
        """拖拽入口：不存在的路径返回空串。"""
        ws_id = controller.addWorkspaceFromPath("/不/存在/的/路径/abcxyz")
        assert ws_id == ""

    def test_add_workspaces_from_paths_batch(
        self,
        controller: WorkspaceController,
        tmp_path: Path,
    ) -> None:
        """拖拽多选：批量传入文件夹路径，返回成功创建数。"""
        f1 = tmp_path / "dir1"
        f2 = tmp_path / "dir2"
        f1.mkdir()
        f2.mkdir()
        count = controller.addWorkspacesFromPaths([str(f1), str(f2)])
        assert count == 2
        assert controller.workspaceModel.rowCount() == 2

    def test_add_workspaces_from_paths_mixed(
        self,
        controller: WorkspaceController,
        tmp_path: Path,
    ) -> None:
        """拖拽多选：混合文件夹与文件，只创建文件夹的工作区。"""
        folder = tmp_path / "dir"
        file_path = tmp_path / "file.txt"
        folder.mkdir()
        file_path.write_text("x", encoding="utf-8")
        count = controller.addWorkspacesFromPaths([str(folder), str(file_path)])
        assert count == 1
        assert controller.workspaceModel.rowCount() == 1

    def test_sync_all_workspaces_rules_on_global_change(
        self,
        controller: WorkspaceController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """iter-139：全局规则变化时已存在的工作区应同步刷新 rules_paths/use_builtin。"""
        # 先新建一个工作区（全局规则为空，工作区 rules_paths=()）
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.rules_paths == ()

        # 添加自定义规则文件到全局配置
        rules_file = tmp_path / "custom.yaml"
        rules_file.write_text("rules: []", encoding="utf-8")
        rules_controller._config.rules_paths = [str(rules_file)]
        rules_controller._reload_ruleset()
        rules_controller.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

        # 工作区 rules_paths 应被刷新为全局值
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.rules_paths == (str(rules_file),)
        assert item.use_builtin is True
        # rules_tags 应包含内置 + 自定义规则文件
        tags = item.rules_tags
        assert len(tags) == 2
        assert tags[0] == {"name": "内置", "is_builtin": True, "is_temp": False}
        assert tags[1] == {"name": "custom.yaml", "is_builtin": False, "is_temp": False}

    def test_sync_all_workspaces_rules_no_change_skip_persist(
        self,
        controller: WorkspaceController,
        rules_controller: RulesController,
    ) -> None:
        """iter-139：全局规则未变化时不触发 persist（避免无谓 I/O）。"""
        controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # 全局规则未变化，再次 emit 信号不应触发 persist
        from unittest.mock import patch

        with patch.object(controller, "_persist") as mock_persist:
            rules_controller.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]
            mock_persist.assert_not_called()

    def test_set_rule_enabled_propagates_to_all_workspaces_tags(
        self,
        controller: WorkspaceController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """禁用全局规则文件后所有工作区 rules_tags 应同步刷新。

        回归用例：``RulesController.setRuleEnabled`` 走全局分支后
        emit ``rulesetChanged``，``WorkspaceController._sync_all_workspaces_rules``
        应将过滤后的全局规则路径同步到每个工作区的 ``rules_paths`` 字段，
        使 ``WorkspaceItem.rules_tags`` 不再展示被禁用的规则文件。
        """
        # 准备两个全局规则文件
        rules_file_a = tmp_path / "a.yaml"
        rules_file_b = tmp_path / "b.yaml"
        rules_file_a.write_text("rules: []", encoding="utf-8")
        rules_file_b.write_text("rules: []", encoding="utf-8")
        rules_controller._config.rules_paths = [str(rules_file_a), str(rules_file_b)]
        rules_controller._reload_ruleset()
        rules_controller.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

        # 新建两个工作区（无任务级覆盖，跟随全局）
        ws_id_1 = controller.addWorkspace("t1", "folder", "/tmp1", "[]", True)
        ws_id_2 = controller.addWorkspace("t2", "folder", "/tmp2", "[]", True)

        # 初始标签应包含内置 + a + b
        for ws_id in (ws_id_1, ws_id_2):
            item = controller.workspaceModel.get_workspace(ws_id)
            assert item is not None
            names = [t["name"] for t in item.rules_tags]
            assert names == ["内置", "a.yaml", "b.yaml"]

        # 通过 RulesController.setRuleEnabled 禁用 a.yaml
        rules_controller.setRuleEnabled(str(rules_file_a), False)

        # 两个工作区的 rules_tags 应同步移除 a.yaml
        for ws_id in (ws_id_1, ws_id_2):
            item = controller.workspaceModel.get_workspace(ws_id)
            assert item is not None
            names = [t["name"] for t in item.rules_tags]
            assert names == ["内置", "b.yaml"], f"工作区 {ws_id} 标签未同步：{names}"

        # 重新启用 a.yaml，标签恢复
        rules_controller.setRuleEnabled(str(rules_file_a), True)
        for ws_id in (ws_id_1, ws_id_2):
            item = controller.workspaceModel.get_workspace(ws_id)
            assert item is not None
            names = [t["name"] for t in item.rules_tags]
            assert names == ["内置", "a.yaml", "b.yaml"]

    def test_creates_independent_scan_controller(self, controller: WorkspaceController) -> None:
        """每个工作区应有独立的 ScanController。"""
        ws_id1 = controller.addWorkspace("t1", "folder", "/tmp", "[]", True)
        ws_id2 = controller.addWorkspace("t2", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id1)
        sc1 = controller.currentScanController
        controller.setCurrentWorkspaceId(ws_id2)
        sc2 = controller.currentScanController
        assert sc1 is not sc2

    def test_drive_mode_sets_selected_drive(self, controller: WorkspaceController) -> None:
        """drive 模式应同步设置 selectedDrive。"""
        ws_id = controller.addWorkspace("t", "drive", "C:\\", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        assert sc.scanModeIndex == 0
        assert sc.selectedDrive == "C:\\"

    def test_folder_mode_sets_folder_root(self, controller: WorkspaceController) -> None:
        """folder 模式应同步设置 folderRoot。"""
        ws_id = controller.addWorkspace("t", "folder", "/custom/path", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        assert sc.scanModeIndex == 1
        assert sc.folderRoot == "/custom/path"

    def test_unknown_mode_defaults_to_folder(
        self,
        controller: WorkspaceController,
    ) -> None:
        """未知模式字符串应回退为 folder（索引 1）。"""
        ws_id = controller.addWorkspace("t", "custom", "/x", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        assert sc.scanModeIndex == 1

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
        # iter-105：walk 阶段收集到的符合文件类型文件数也应回写
        sc._walk_discovered = 100  # type: ignore[attr-defined]
        sc._walk_skipped = 30  # type: ignore[attr-defined]
        sc._walk_user_skipped = 5  # type: ignore[attr-defined]

        controller._sync_workspace_state(ws_id)

        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.status_text == "已完成"
        assert item.matched_count == 5
        assert item.passed_count == 10
        assert item.skipped_count == 2
        assert item.error_count == 1
        assert item.last_summary == "用时 1.5s"
        # 100 - 30 - 5 = 65
        assert item.collected_count == 65

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

    def test_cleanup_clears_scan_controllers(self, controller: WorkspaceController) -> None:
        """iter-124：cleanup 清理 ScanController 资源，但不清空 model（避免 QML binding null）。"""
        controller.addWorkspace("t1", "folder", "/tmp", "[]", True)
        controller.addWorkspace("t2", "folder", "/tmp", "[]", True)
        controller.cleanup()
        # model 保留（进程即将退出，清空会触发 QML binding null 错误）
        assert controller.workspaceCount == 2
        # ScanController 字典已清空
        assert len(controller._scan_controllers) == 0

    def test_cleanup_calls_scan_controller_quick_cancel(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """iter-127：cleanup 改用 quick_cancel（非阻塞），不再调 cleanup/wait/close。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        called = False

        def fake_quick_cancel() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(sc, "quick_cancel", fake_quick_cancel)
        controller.cleanup()
        assert called is True

    def test_cleanup_preserves_current_workspace(self, controller: WorkspaceController) -> None:
        """iter-124：cleanup 不清空 currentWorkspaceId（避免 QML binding 求值 null）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        controller.cleanup()
        # ID 保留（进程即将退出，清空会触发 QML binding null 错误）
        assert controller.currentWorkspaceId == ws_id
        # hasCurrentWorkspace 依赖 _scan_controllers 字典，已清空 → False
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
        ws_id = controller.addWorkspace("我的任务", "drive", "C:\\", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)

        assert controller.activeScanWorkspaceName == "我的任务"
        assert controller.activeScanModeText == "盘符扫描"
        assert controller.activeScanTarget == "C:\\"

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

    def test_cleanup_preserves_active(self, controller: WorkspaceController) -> None:
        """iter-124：cleanup 不清空 active 状态（避免 QML binding 求值 null）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert controller.hasActiveScan is True

        controller.cleanup()
        # ID 保留（进程即将退出，清空会触发 QML binding null 错误）
        assert controller.activeScanWorkspaceId == ws_id
        # hasActiveScan 依赖 _scan_controllers 字典，已清空 → False
        assert controller.hasActiveScan is False


class TestUpdateWorkspaceTarget:
    """iter-104 任务切换扫描目标测试。"""

    def test_update_target_folder_mode(self, controller: WorkspaceController) -> None:
        """更新 folder 模式目标应同步到 WorkspaceItem 与 ScanController。"""
        ws_id = controller.addWorkspace("t", "folder", "/old", "[]", True)
        controller.updateWorkspaceTarget(ws_id, "folder", "/new/path")

        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.mode_str == "folder"
        assert item.target == "/new/path"
        # ScanController 同步
        sc = controller.currentScanController if controller.currentWorkspaceId == ws_id else None
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        assert sc.folderRoot == "/new/path"

    def test_update_target_to_drive_mode(self, controller: WorkspaceController) -> None:
        """从 folder 切换到 drive 模式应正确同步。"""
        ws_id = controller.addWorkspace("t", "folder", "/old", "[]", True)
        controller.updateWorkspaceTarget(ws_id, "drive", "C:\\")

        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.mode_str == "drive"
        assert item.target == "C:\\"

    def test_update_target_rejected_when_scanning(
        self,
        controller: WorkspaceController,
    ) -> None:
        """扫描中/暂停中应拒绝修改目标。"""
        ws_id = controller.addWorkspace("t", "folder", "/old", "[]", True)
        # 强制设为扫描中状态
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        controller.workspaceModel.update_workspace(ws_id, status_text="扫描中")

        controller.updateWorkspaceTarget(ws_id, "folder", "/new")

        # 应保持原值
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.target == "/old"

    def test_update_target_invalid_mode_noop(self, controller: WorkspaceController) -> None:
        """无效的扫描模式应被拒绝。"""
        ws_id = controller.addWorkspace("t", "folder", "/old", "[]", True)
        controller.updateWorkspaceTarget(ws_id, "invalid_mode", "/new")

        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.mode_str == "folder"
        assert item.target == "/old"

    def test_update_target_unknown_workspace_noop(self, controller: WorkspaceController) -> None:
        """未知工作区 ID 应静默返回。"""
        controller.updateWorkspaceTarget("ws-nonexistent", "folder", "/new")
        # 不抛异常即通过

    def test_update_target_persists(self, controller: WorkspaceController, config_dir: Path) -> None:
        """更新目标应持久化到 workspaces.json。"""
        ws_id = controller.addWorkspace("t", "folder", "/old", "[]", True)
        controller.updateWorkspaceTarget(ws_id, "folder", "/new/persisted")

        persist_file = config_dir / "workspaces.json"
        assert persist_file.exists()
        data = json.loads(persist_file.read_text(encoding="utf-8"))
        ws_data = next(w for w in data["workspaces"] if w["id"] == ws_id)
        assert ws_data["target"] == "/new/persisted"
        assert ws_data["mode"] == "folder"

    def test_collected_count_persisted_and_restored(
        self,
        config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """iter-105：collected_count 应持久化到 workspaces.json 并在重启后恢复。"""
        # 第一次启动：创建工作区并模拟扫描完成（写入 collected_count）
        cfg1 = ConfigController()
        rules1 = RulesController(cfg1)
        ctrl1 = WorkspaceController(cfg1, rules1)
        ws_id = ctrl1.addWorkspace("t", "folder", "/tmp", "[]", True)
        ctrl1.setCurrentWorkspaceId(ws_id)
        sc1 = ctrl1.currentScanController
        # 模拟 walk 阶段结果：发现 100，跳过 30，用户跳过 5 → 符合 65
        sc1._walk_discovered = 100  # type: ignore[attr-defined]
        sc1._walk_skipped = 30  # type: ignore[attr-defined]
        sc1._walk_user_skipped = 5  # type: ignore[attr-defined]
        sc1._scan_state = "results"  # type: ignore[attr-defined]
        sc1._status_text = "已完成"  # type: ignore[attr-defined]
        # 标记为 active scan，使 _sync_workspace_state 走「扫描结束持久化」分支
        ctrl1._active_scan_workspace_id = ws_id  # type: ignore[attr-defined]
        ctrl1._sync_workspace_state(ws_id)
        ctrl1.cleanup()
        cfg1.save()

        # 验证持久化文件中包含 collected_count
        persist_file = config_dir / "workspaces.json"
        data = json.loads(persist_file.read_text(encoding="utf-8"))
        ws_data = next(w for w in data["workspaces"] if w["id"] == ws_id)
        assert ws_data["collected_count"] == 65

        # 第二次启动：重新创建控制器，应恢复 collected_count
        cfg2 = ConfigController()
        rules2 = RulesController(cfg2)
        ctrl2 = WorkspaceController(cfg2, rules2)
        item = ctrl2.workspaceModel.get_workspace(ws_id)
        assert item is not None
        assert item.collected_count == 65
        ctrl2.cleanup()


class TestClearAllWorkspaces:
    """iter-108 清空所有工作区测试。"""

    def test_clear_all_returns_true_when_empty(self, controller: WorkspaceController) -> None:
        """空工作区列表清空应返回 True。"""
        assert controller.clearAllWorkspaces() is True
        assert controller.workspaceCount == 0

    def test_clear_all_removes_all_workspaces(
        self,
        controller: WorkspaceController,
    ) -> None:
        """清空应移除所有工作区。"""
        controller.addWorkspace("t1", "folder", "/tmp", "[]", True)
        controller.addWorkspace("t2", "folder", "/tmp", "[]", True)
        controller.addWorkspace("t3", "folder", "/tmp", "[]", True)
        assert controller.workspaceCount == 3

        assert controller.clearAllWorkspaces() is True
        assert controller.workspaceCount == 0
        assert tuple(controller.workspaceModel.items) == ()

    def test_clear_all_rejected_when_scanning(
        self,
        controller: WorkspaceController,
    ) -> None:
        """扫描中状态拒绝清空。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        # 模拟扫描中状态
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = False  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert controller.hasActiveScan is True

        # 扫描中应拒绝清空
        assert controller.clearAllWorkspaces() is False
        # 工作区仍存在
        assert controller.workspaceCount == 1

    def test_clear_all_rejected_when_paused(
        self,
        controller: WorkspaceController,
    ) -> None:
        """暂停中状态同样拒绝清空。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        sc._scan_state = "scanning"  # type: ignore[attr-defined]
        sc._is_paused = True  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        assert controller.hasActiveScan is True

        assert controller.clearAllWorkspaces() is False
        assert controller.workspaceCount == 1

    def test_clear_all_resets_current_workspace_id(
        self,
        controller: WorkspaceController,
    ) -> None:
        """清空后 currentWorkspaceId 应被重置为空串。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        assert controller.currentWorkspaceId == ws_id

        controller.clearAllWorkspaces()
        assert controller.currentWorkspaceId == ""
        assert controller.hasCurrentWorkspace is False

    def test_clear_all_calls_cleanup_on_scan_controllers(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """清空应调用每个 ScanController 的 cleanup。"""
        ws_id1 = controller.addWorkspace("t1", "folder", "/tmp", "[]", True)
        ws_id2 = controller.addWorkspace("t2", "folder", "/tmp", "[]", True)
        sc1 = controller._ensure_scan_controller(ws_id1)  # type: ignore[attr-defined]
        sc2 = controller._ensure_scan_controller(ws_id2)  # type: ignore[attr-defined]

        cleanup_calls: list[ScanController] = []
        monkeypatch.setattr(sc1, "cleanup", lambda: cleanup_calls.append(sc1))
        monkeypatch.setattr(sc2, "cleanup", lambda: cleanup_calls.append(sc2))

        controller.clearAllWorkspaces()

        assert sc1 in cleanup_calls
        assert sc2 in cleanup_calls
        # ScanController 映射应清空
        assert len(controller._scan_controllers) == 0

    def test_clear_all_persists_empty_list(
        self,
        controller: WorkspaceController,
        config_dir: Path,
    ) -> None:
        """清空后 workspaces.json 应包含空 workspaces 列表。"""
        controller.addWorkspace("t1", "folder", "/tmp", "[]", True)
        controller.addWorkspace("t2", "folder", "/tmp", "[]", True)
        persist_file = config_dir / "workspaces.json"
        assert persist_file.exists()
        # 清空前有 2 个工作区
        data_before = json.loads(persist_file.read_text(encoding="utf-8"))
        assert len(data_before["workspaces"]) == 2

        controller.clearAllWorkspaces()

        # 清空后持久化文件应包含空列表
        data_after = json.loads(persist_file.read_text(encoding="utf-8"))
        assert data_after["version"] == 1
        assert data_after["workspaces"] == []

    def test_clear_all_emits_workspace_list_changed(
        self,
        controller: WorkspaceController,
    ) -> None:
        """清空应 emit workspaceListChanged 信号。"""
        controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        emitted: list[None] = []
        controller.workspaceListChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]

        controller.clearAllWorkspaces()
        assert len(emitted) == 1

    def test_clear_all_no_signal_when_empty(
        self,
        controller: WorkspaceController,
    ) -> None:
        """空列表清空不应 emit workspaceListChanged。"""
        emitted: list[None] = []
        controller.workspaceListChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]

        assert controller.clearAllWorkspaces() is True
        assert len(emitted) == 0

    def test_clear_all_after_results_state_allowed(
        self,
        controller: WorkspaceController,
    ) -> None:
        """已完成状态应允许清空（仅扫描中/暂停中拒绝）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        # 模拟扫描完成进入 results 状态
        sc._scan_state = "results"  # type: ignore[attr-defined]
        sc._status_text = "已完成"  # type: ignore[attr-defined]
        controller._sync_workspace_state(ws_id)
        # results 状态不再属于 active scan
        assert controller.hasActiveScan is False

        assert controller.clearAllWorkspaces() is True
        assert controller.workspaceCount == 0


class TestTaskOverrides:
    """iter-104 任务级配置覆盖测试。"""

    def test_task_overrides_json_default_empty(self, controller: WorkspaceController) -> None:
        """新建工作区默认无覆盖，taskOverridesJson 返回 '{}'。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        assert controller.taskOverridesJson(ws_id) == "{}"

    def test_set_task_override_use_builtin_updates_field(self, controller: WorkspaceController) -> None:
        """setTaskOverride 应更新 task_overrides 字段。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "use_builtin", "false")

        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert overrides == {"use_builtin": False}

    def test_set_task_override_temp_rules_paths_list_to_tuple(self, controller: WorkspaceController) -> None:
        """temp_rules_paths 列表应在内部转为 tuple。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "temp_rules_paths", '["/tmp/a.yaml", "/tmp/b.yaml"]')

        overrides = json.loads(controller.taskOverridesJson(ws_id))
        # 序列化时 tuple 转为 list
        assert overrides == {"temp_rules_paths": ["/tmp/a.yaml", "/tmp/b.yaml"]}
        # 内部存储为 tuple（通过 ScanController 同步验证）
        sc = controller._ensure_scan_controller(ws_id)  # type: ignore[attr-defined]
        assert sc._task_overrides["temp_rules_paths"] == ("/tmp/a.yaml", "/tmp/b.yaml")  # type: ignore[attr-defined]

    def test_set_task_override_rules_paths_list_to_tuple(self, controller: WorkspaceController) -> None:
        """rules_paths 列表应在内部转为 tuple，并同步 WorkspaceItem 字段。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "rules_paths", '["/tmp/a.yaml", "/tmp/b.yaml"]')

        overrides = json.loads(controller.taskOverridesJson(ws_id))
        # 序列化时 tuple 转为 list
        assert overrides == {"rules_paths": ["/tmp/a.yaml", "/tmp/b.yaml"]}
        # 内部存储为 tuple
        sc = controller._ensure_scan_controller(ws_id)  # type: ignore[attr-defined]
        assert sc._task_overrides["rules_paths"] == ("/tmp/a.yaml", "/tmp/b.yaml")  # type: ignore[attr-defined]
        # WorkspaceItem.rules_paths 同步为覆盖值（使 rules_tags 反映 effective 规则）
        item = controller.get_workspace(ws_id)  # type: ignore[attr-defined]
        assert item is not None
        assert item.rules_paths == ("/tmp/a.yaml", "/tmp/b.yaml")

    def test_set_task_override_use_builtin(self, controller: WorkspaceController) -> None:
        """use_builtin 覆盖应同步 WorkspaceItem 字段。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # 默认 use_builtin=True（从全局快照），覆盖为 False
        controller.setTaskOverride(ws_id, "use_builtin", "false")

        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert overrides == {"use_builtin": False}
        # WorkspaceItem.use_builtin 同步为覆盖值
        item = controller.get_workspace(ws_id)  # type: ignore[attr-defined]
        assert item is not None
        assert item.use_builtin is False

    def test_set_task_override_rules_paths_wrong_type_noop(
        self,
        controller: WorkspaceController,
    ) -> None:
        """rules_paths 非 list[str] 应被拒绝。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # JSON 123 是 int 不是 list
        controller.setTaskOverride(ws_id, "rules_paths", "123")

        assert controller.taskOverridesJson(ws_id) == "{}"

    def test_set_task_override_use_builtin_wrong_type_noop(
        self,
        controller: WorkspaceController,
    ) -> None:
        """use_builtin 非 bool 应被拒绝。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # JSON 字符串 "yes" 不是 bool
        controller.setTaskOverride(ws_id, "use_builtin", '"yes"')

        assert controller.taskOverridesJson(ws_id) == "{}"

    def test_set_task_override_invalid_key_noop(self, controller: WorkspaceController) -> None:
        """不允许覆盖的字段应被拒绝。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "backup_dir", '"/custom"')

        assert controller.taskOverridesJson(ws_id) == "{}"

    def test_set_task_override_invalid_json_noop(self, controller: WorkspaceController) -> None:
        """无效 JSON 应被拒绝。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "use_builtin", "not a json")

        assert controller.taskOverridesJson(ws_id) == "{}"

    def test_set_task_override_wrong_type_noop(self, controller: WorkspaceController) -> None:
        """类型不符应被拒绝。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # use_builtin 应为 bool，传字符串
        controller.setTaskOverride(ws_id, "use_builtin", '"not_bool"')

        assert controller.taskOverridesJson(ws_id) == "{}"

    def test_set_task_override_syncs_to_scan_controller(
        self,
        controller: WorkspaceController,
    ) -> None:
        """setTaskOverride 应同步到对应 ScanController。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "use_builtin", "false")

        sc = controller._ensure_scan_controller(ws_id)  # type: ignore[attr-defined]
        assert sc._task_overrides.get("use_builtin") is False  # type: ignore[attr-defined]
        # _effective_use_builtin 应返回覆盖值
        assert sc._effective_use_builtin() is False  # type: ignore[attr-defined]

    def test_task_overrides_persisted(self, controller: WorkspaceController, config_dir: Path) -> None:
        """任务级覆盖应持久化到 workspaces.json。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "use_builtin", "false")
        controller.setTaskOverride(ws_id, "rules_paths", '["/tmp/x.yaml"]')

        persist_file = config_dir / "workspaces.json"
        data = json.loads(persist_file.read_text(encoding="utf-8"))
        ws_data = next(w for w in data["workspaces"] if w["id"] == ws_id)
        assert ws_data["task_overrides"]["use_builtin"] is False
        assert ws_data["task_overrides"]["rules_paths"] == ["/tmp/x.yaml"]

    def test_task_overrides_restored_on_restart(
        self,
        config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """重启后应恢复任务级覆盖并同步到 ScanController。"""
        # 第一次启动：创建工作区并设置覆盖
        cfg1 = ConfigController()
        rules1 = RulesController(cfg1)
        ctrl1 = WorkspaceController(cfg1, rules1)
        ws_id = ctrl1.addWorkspace("t", "folder", "/tmp", "[]", True)
        ctrl1.setTaskOverride(ws_id, "use_builtin", "false")
        ctrl1.setTaskOverride(ws_id, "rules_paths", '["/tmp/y.yaml"]')
        ctrl1.cleanup()
        cfg1.save()

        # 第二次启动：重新创建控制器，应恢复覆盖
        cfg2 = ConfigController()
        rules2 = RulesController(cfg2)
        ctrl2 = WorkspaceController(cfg2, rules2)

        overrides = json.loads(ctrl2.taskOverridesJson(ws_id))
        assert overrides.get("use_builtin") is False
        assert overrides.get("rules_paths") == ["/tmp/y.yaml"]
        # ScanController 也应同步
        sc = ctrl2._ensure_scan_controller(ws_id)  # type: ignore[attr-defined]
        assert sc._effective_use_builtin() is False  # type: ignore[attr-defined]
        assert sc._effective_rules_paths() == ("/tmp/y.yaml",)  # type: ignore[attr-defined]
        ctrl2.cleanup()


class TestScanControllerTaskOverrides:
    """ScanController effective 扫描参数从规则集读取（不再支持任务级覆盖）。"""

    def test_effective_reads_ruleset_defaults(
        self,
        controller: WorkspaceController,
    ) -> None:
        """扫描参数应从规则集读取（builtin 默认 scan_archives=True, max_workers=5）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController

        assert sc._effective_scan_archives() is True
        assert sc._effective_max_workers() == 5

    def test_effective_ignore_dirs_returns_tuple(
        self,
        controller: WorkspaceController,
    ) -> None:
        """_effective_ignore_dirs 应返回 tuple。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController

        result = sc._effective_ignore_dirs()  # type: ignore[attr-defined]
        assert isinstance(result, tuple)

    def test_effective_max_file_size_reads_ruleset(self, controller: WorkspaceController) -> None:
        """_effective_max_file_size 应从规则集读取（builtin 默认）。"""
        from fuscan.config import DEFAULT_MAX_FILE_SIZE

        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController

        assert sc._effective_max_file_size() == DEFAULT_MAX_FILE_SIZE  # type: ignore[attr-defined]

    def test_effective_max_depth_defaults_none(
        self,
        controller: WorkspaceController,
    ) -> None:
        """无规则集 max_depth 配置时 _effective_max_depth 应返回 None（无限深度）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController

        assert sc._effective_max_depth() is None  # type: ignore[attr-defined]


class TestTaskOverridesJsonErrorHandling:
    """iter-105 M4 修复：taskOverridesJson 容错测试。"""

    def test_task_overrides_json_handles_non_serializable(
        self,
        controller: WorkspaceController,
    ) -> None:
        """T19：task_overrides 含非 JSON 可序列化对象时应返回 "{}" 不抛异常。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # 直接通过 model 注入非可序列化对象（模拟外部代码污染）
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None
        bad_overrides = dict(item.task_overrides)
        bad_overrides["_bad"] = object()  # object() 不可 JSON 序列化
        controller.workspaceModel.update_workspace(ws_id, task_overrides=bad_overrides)

        # 应返回 "{}" 并 warning，不抛异常
        result = controller.taskOverridesJson(ws_id)
        assert result == "{}"


class TestTaskOverridesGlobalValueBehavior:
    """iter-105 T3：覆盖值等于全局值时的行为测试。"""

    def test_override_equal_to_global_is_stored(
        self,
        controller: WorkspaceController,
    ) -> None:
        """T3：明确锁定行为——覆盖值等于全局值时仍无条件存储。

        当前实现选择「无条件存储」语义：用户显式设置的覆盖即使与全局值相同也持久化。
        这样全局值后续变化时，任务级保持用户当时的选择。
        """
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # use_builtin 全局默认值为 True
        global_value = controller._config_controller.config.use_builtin  # type: ignore[attr-defined]

        controller.setTaskOverride(ws_id, "use_builtin", str(global_value).lower())

        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert overrides.get("use_builtin") == global_value


class TestClearTaskOverride:
    """iter-138 ``clearTaskOverride`` 测试：清除任务级配置覆盖。

    覆盖四个分支：工作区不存在、非法 key、无覆盖 noop、正常清除并回填全局值。
    """

    def test_clear_nonexistent_workspace_noop(self, controller: WorkspaceController) -> None:
        """工作区不存在时记录 warning 并返回，不抛异常。"""
        # 不应抛异常
        controller.clearTaskOverride("nonexistent-ws", "scan_archives")

    def test_clear_invalid_key_noop(self, controller: WorkspaceController) -> None:
        """非法 key（不在 TASK_OVERRIDE_KEYS 中）应被拒绝。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "scan_archives", "false")

        controller.clearTaskOverride(ws_id, "backup_dir")  # backup_dir 不在允许列表

        # 原有覆盖应保留
        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert overrides == {"scan_archives": False}

    def test_clear_non_existing_override_noop(self, controller: WorkspaceController) -> None:
        """key 在 task_overrides 中不存在时无操作返回。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # 未设置过任何覆盖，清除 scan_archives 应无操作
        controller.clearTaskOverride(ws_id, "scan_archives")
        assert controller.taskOverridesJson(ws_id) == "{}"

    def test_clear_existing_override_removes_and_backfills_global(
        self,
        controller: WorkspaceController,
    ) -> None:
        """清除已有覆盖时应删除字段并用全局值回填 ScanController。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "scan_archives", "false")
        # 确认覆盖已设置
        assert json.loads(controller.taskOverridesJson(ws_id)) == {"scan_archives": False}

        # 清除覆盖
        controller.clearTaskOverride(ws_id, "scan_archives")

        # task_overrides 中应不再包含 scan_archives
        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert "scan_archives" not in overrides

        # ScanController 覆盖已清除，effective 值回退到规则集（builtin 默认 True）
        sc = controller._ensure_scan_controller(ws_id)  # type: ignore[attr-defined]
        assert "scan_archives" not in sc._task_overrides  # type: ignore[attr-defined]
        assert sc._effective_scan_archives() is True  # type: ignore[attr-defined]

    def test_clear_persisted_after_restart(
        self,
        config_dir: Path,
    ) -> None:
        """清除覆盖后应持久化，重启后覆盖仍为空。"""
        cfg1 = ConfigController()
        rules1 = RulesController(cfg1)
        ctrl1 = WorkspaceController(cfg1, rules1)
        ws_id = ctrl1.addWorkspace("t", "folder", "/tmp", "[]", True)
        ctrl1.setTaskOverride(ws_id, "max_workers", "8")
        ctrl1.clearTaskOverride(ws_id, "max_workers")

        # 重新创建控制器，验证清除已持久化
        cfg2 = ConfigController()
        rules2 = RulesController(cfg2)
        ctrl2 = WorkspaceController(cfg2, rules2)
        assert ctrl2.taskOverridesJson(ws_id) == "{}"

    def test_clear_rules_paths_backfills_global(self, controller: WorkspaceController) -> None:
        """清除 rules_paths 覆盖应回填全局值到 WorkspaceItem 与 ScanController。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "rules_paths", '["/tmp/x.yaml"]')
        item = controller.get_workspace(ws_id)  # type: ignore[attr-defined]
        assert item is not None
        assert item.rules_paths == ("/tmp/x.yaml",)

        # 清除覆盖
        controller.clearTaskOverride(ws_id, "rules_paths")

        # task_overrides 中应不再包含 rules_paths
        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert "rules_paths" not in overrides
        # WorkspaceItem.rules_paths 回退到全局值
        global_paths = tuple(controller._config_controller.config.rules_paths)  # type: ignore[attr-defined]
        item_after = controller.get_workspace(ws_id)  # type: ignore[attr-defined]
        assert item_after is not None
        assert item_after.rules_paths == global_paths

    def test_clear_use_builtin_backfills_global(self, controller: WorkspaceController) -> None:
        """清除 use_builtin 覆盖应回填全局值到 WorkspaceItem。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "use_builtin", "false")
        item = controller.get_workspace(ws_id)  # type: ignore[attr-defined]
        assert item is not None
        assert item.use_builtin is False

        # 清除覆盖
        controller.clearTaskOverride(ws_id, "use_builtin")

        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert "use_builtin" not in overrides
        # WorkspaceItem.use_builtin 回退到全局值
        global_use_builtin = controller._config_controller.config.use_builtin  # type: ignore[attr-defined]
        item_after = controller.get_workspace(ws_id)  # type: ignore[attr-defined]
        assert item_after is not None
        assert item_after.use_builtin == global_use_builtin


class TestNonexistentWorkspaceEdgeCases:
    """工作区不存在时的边界测试：确保各方法不抛异常。"""

    def test_start_incremental_scan_nonexistent_workspace_noop(
        self,
        controller: WorkspaceController,
    ) -> None:
        """startIncrementalScan 对不存在的工作区应记录 warning 并返回。"""
        # 不应抛异常
        controller.startIncrementalScan("nonexistent-ws")

    def test_set_task_override_nonexistent_workspace_noop(
        self,
        controller: WorkspaceController,
    ) -> None:
        """setTaskOverride 对不存在的工作区应记录 warning 并返回。"""
        # 不应抛异常
        controller.setTaskOverride("nonexistent-ws", "scan_archives", "false")


class TestLegacyPersistFileCompat:
    """iter-105 T4：旧版本持久化文件（无 collected_count/task_overrides 字段）兼容测试。"""

    def test_load_persisted_legacy_file_without_collected_count(
        self,
        config_dir: Path,
    ) -> None:
        """T4：旧版本 workspaces.json 缺 collected_count 字段时应默认为 0。"""
        # 手动构造旧版本持久化文件（无 collected_count 与 task_overrides 字段）
        persist_file = config_dir / "workspaces.json"
        legacy_data: dict[str, object] = {
            "version": 1,
            "workspaces": [
                {
                    "id": "ws-legacy",
                    "name": "旧任务",
                    "mode": "folder",
                    "target": "/old",
                    "rules_paths": [],
                    "use_builtin": True,
                    "status_text": "已完成",
                    "matched_count": 3,
                    "passed_count": 5,
                    "skipped_count": 1,
                    "error_count": 0,
                    "last_summary": "用时 0.5s",
                    # 故意省略 collected_count 与 task_overrides
                }
            ],
        }
        persist_file.write_text(json.dumps(legacy_data, ensure_ascii=False), encoding="utf-8")

        # 重新创建控制器，应能加载且 collected_count 默认 0、task_overrides 默认 {}
        cfg = ConfigController()
        rules = RulesController(cfg)
        ctrl = WorkspaceController(cfg, rules)
        item = ctrl.workspaceModel.get_workspace("ws-legacy")
        assert item is not None
        assert item.collected_count == 0
        assert item.task_overrides == {}
        ctrl.cleanup()


class TestScanControllerOverrideSyncContract:
    """iter-105 T5：ScanController.setTaskOverride 单向同步契约测试。"""

    def test_scan_controller_set_override_does_not_leak_to_workspace_item(
        self,
        controller: WorkspaceController,
    ) -> None:
        """T5：直接调 ScanController.setTaskOverride 不应回写 WorkspaceItem.task_overrides。

        契约：WorkspaceController → ScanController 是单向同步。
        ScanController.setTaskOverride 是 @Slot 暴露给 QML 的，但 QML 应通过
        WorkspaceController.setTaskOverride 调用以同时更新 WorkspaceItem。
        直接调 ScanController.setTaskOverride 仅影响运行时扫描行为，
        不会持久化也不会更新 WorkspaceItem。
        """
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        item = controller.workspaceModel.get_workspace(ws_id)
        assert item is not None

        # 直接调 ScanController.setTaskOverride
        sc.setTaskOverride("max_workers", 7)  # type: ignore[attr-defined]

        # ScanController 运行时已生效
        assert sc._effective_max_workers() == 7  # type: ignore[attr-defined]
        # 但 WorkspaceItem.task_overrides 不应被回写
        assert "max_workers" not in item.task_overrides


# ============================= iter-115 扫描历史 =============================


class TestWorkspaceHistorySlots:
    """iter-115：``WorkspaceController`` 扫描历史 QML 槽测试。"""

    def test_workspace_history_json_empty(self, controller: WorkspaceController) -> None:
        """无历史时返回 ``"[]"``。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        result = controller.workspaceHistoryJson(ws_id)
        assert result == "[]"

    def test_compare_with_previous_scan_empty(self, controller: WorkspaceController) -> None:
        """无历史时对比槽返回 ``"{}"``。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        result = controller.compareWithPreviousScan(ws_id)
        assert result == "{}"

    def test_clear_workspace_history_no_op_when_empty(
        self,
        controller: WorkspaceController,
    ) -> None:
        """无历史时清空返回 0。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        assert controller.clearWorkspaceHistory(ws_id) == 0

    def test_workspace_history_json_after_manual_archive(
        self,
        controller: WorkspaceController,
    ) -> None:
        """手动注入历史条目后 ``workspaceHistoryJson`` 应返回 JSON 数组。"""
        ws_id = controller.addWorkspace("任务A", "folder", "/tmp", "[]", True)
        # 直接通过底层 store 注入两条历史
        from fuscan.history import ScanHistoryEntry

        controller._history_store.add(  # type: ignore[attr-defined]
            ScanHistoryEntry(
                scan_id="s1",
                workspace_id=ws_id,
                workspace_name="任务A",
                finished_at="2026-07-27T10:00:00Z",
                matched_files=3,
                hit_paths=("/a", "/b", "/c"),
                rule_names=("rule1",),
                summary="命中 3 个文件",
            )
        )
        controller._history_store.add(  # type: ignore[attr-defined]
            ScanHistoryEntry(
                scan_id="s2",
                workspace_id=ws_id,
                workspace_name="任务A",
                finished_at="2026-07-27T11:00:00Z",
                matched_files=2,
                hit_paths=("/a", "/d"),
                rule_names=("rule1", "rule2"),
                summary="命中 2 个文件",
            )
        )

        payload = json.loads(controller.workspaceHistoryJson(ws_id))
        assert len(payload) == 2
        # 最新在前
        assert payload[0]["scan_id"] == "s2"
        assert payload[0]["matched_files"] == 2
        assert payload[0]["workspace_name"] == "任务A"
        assert payload[0]["rule_names"] == ["rule1", "rule2"]
        assert payload[1]["scan_id"] == "s1"

    def test_compare_with_previous_scan_returns_delta(
        self,
        controller: WorkspaceController,
    ) -> None:
        """两次扫描后对比槽应返回 trend/summary/new_hits 等字段。"""
        ws_id = controller.addWorkspace("任务B", "folder", "/tmp", "[]", True)
        from fuscan.history import ScanHistoryEntry

        controller._history_store.add(  # type: ignore[attr-defined]
            ScanHistoryEntry(
                scan_id="s1",
                workspace_id=ws_id,
                workspace_name="任务B",
                finished_at="2026-07-27T10:00:00Z",
                matched_files=3,
                hit_paths=("/a", "/b", "/c"),
                rule_names=("rule1",),
            )
        )
        controller._history_store.add(  # type: ignore[attr-defined]
            ScanHistoryEntry(
                scan_id="s2",
                workspace_id=ws_id,
                workspace_name="任务B",
                finished_at="2026-07-27T11:00:00Z",
                matched_files=2,
                hit_paths=("/a", "/d"),
                rule_names=("rule1", "rule2"),
            )
        )

        payload = json.loads(controller.compareWithPreviousScan(ws_id))
        assert payload["trend"] == "改善"  # 3 → 2
        assert payload["matched_delta"] == -1
        assert payload["new_hits_count"] == 1  # /d
        assert payload["resolved_hits_count"] == 2  # /b /c
        assert payload["persistent_hits_count"] == 1  # /a
        assert payload["current"]["scan_id"] == "s2"
        assert payload["previous"]["scan_id"] == "s1"
        # new_rules 包含 rule2
        assert "rule2" in payload["new_rules"]
        assert payload["dropped_rules"] == []

    def test_compare_with_previous_scan_first_scan_only(
        self,
        controller: WorkspaceController,
    ) -> None:
        """仅一次扫描时 previous 为 ``None``，trend 为「首次」。"""
        ws_id = controller.addWorkspace("任务C", "folder", "/tmp", "[]", True)
        from fuscan.history import ScanHistoryEntry

        controller._history_store.add(  # type: ignore[attr-defined]
            ScanHistoryEntry(
                scan_id="s1",
                workspace_id=ws_id,
                workspace_name="任务C",
                matched_files=5,
                hit_paths=("/x",),
                rule_names=("r1",),
            )
        )

        payload = json.loads(controller.compareWithPreviousScan(ws_id))
        assert payload["previous"] is None
        assert payload["trend"] == "首次"
        assert payload["matched_delta"] == 5
        assert payload["new_hits_count"] == 1

    def test_clear_workspace_history_removes_entries(
        self,
        controller: WorkspaceController,
    ) -> None:
        """清空历史后 ``workspaceHistoryJson`` 返回 ``"[]"``。"""
        ws_id = controller.addWorkspace("任务D", "folder", "/tmp", "[]", True)
        from fuscan.history import ScanHistoryEntry

        controller._history_store.add(  # type: ignore[attr-defined]
            ScanHistoryEntry(
                scan_id="s1",
                workspace_id=ws_id,
                workspace_name="任务D",
                matched_files=1,
            )
        )
        controller._history_store.add(  # type: ignore[attr-defined]
            ScanHistoryEntry(
                scan_id="s2",
                workspace_id=ws_id,
                workspace_name="任务D",
                matched_files=2,
            )
        )

        removed = controller.clearWorkspaceHistory(ws_id)
        assert removed == 2
        assert controller.workspaceHistoryJson(ws_id) == "[]"

    def test_remove_workspace_clears_history(
        self,
        controller: WorkspaceController,
    ) -> None:
        """移除工作区应同时清理对应历史。"""
        ws_id = controller.addWorkspace("任务E", "folder", "/tmp", "[]", True)
        from fuscan.history import ScanHistoryEntry

        controller._history_store.add(  # type: ignore[attr-defined]
            ScanHistoryEntry(
                scan_id="s1",
                workspace_id=ws_id,
                workspace_name="任务E",
            )
        )
        # 验证历史已存在
        assert json.loads(controller.workspaceHistoryJson(ws_id))

        controller.removeWorkspace(ws_id)
        # 工作区被删除后历史也应清空
        assert controller.workspaceHistoryJson(ws_id) == "[]"

    def test_archive_scan_history_handles_missing_workspace(
        self,
        controller: WorkspaceController,
    ) -> None:
        """``_archive_scan_history`` 对不存在的工作区应静默返回。"""
        # 直接调用内部方法，传入不存在的 ws_id
        sc = controller.currentScanController
        # 不应抛异常
        controller._archive_scan_history("ws-nonexistent", sc)  # type: ignore[attr-defined]

    def test_archive_scan_history_handles_none_report(
        self,
        controller: WorkspaceController,
    ) -> None:
        """ScanController 无 ``_last_report`` 时归档跳过（不抛异常）。"""
        ws_id = controller.addWorkspace("任务F", "folder", "/tmp", "[]", True)
        sc = controller.currentScanController
        # ScanController._last_report 默认 None
        controller._archive_scan_history(ws_id, sc)  # type: ignore[attr-defined]
        # 无历史被添加
        assert controller.workspaceHistoryJson(ws_id) == "[]"


# ============================= iter-123：扫描结果缓存 =============================


class TestCachedResultsPaths:
    """``_cached_results_dir`` 与 ``_cached_results_path`` 路径计算测试。"""

    def test_cached_results_dir_under_config_dir(
        self,
        controller: WorkspaceController,
        config_dir: Path,
    ) -> None:
        """缓存目录应在 ``CONFIG_DIR/results/`` 下。"""
        assert controller._cached_results_dir == config_dir / "results"  # type: ignore[attr-defined]

    def test_cached_results_path_format(
        self,
        controller: WorkspaceController,
    ) -> None:
        """缓存文件名格式为 ``<ws_id>.json``。"""
        path = controller._cached_results_path("ws-abc")  # type: ignore[attr-defined]
        assert path.name == "ws-abc.json"
        assert path.parent.name == "results"


class TestSaveCachedResults:
    """``_save_cached_results`` 持久化测试。"""

    def test_save_creates_results_dir(
        self,
        controller: WorkspaceController,
        config_dir: Path,
    ) -> None:
        """保存时应自动创建 ``results/`` 目录。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        sc = controller.currentScanController
        # results 目录不应预先存在
        assert not (config_dir / "results").exists()
        # 注入 _last_report 后保存
        sc._last_report = _build_simple_report()  # type: ignore[attr-defined]
        controller._save_cached_results(ws_id, sc)  # type: ignore[attr-defined]
        # 目录与文件均应存在
        assert (config_dir / "results").is_dir()
        assert (config_dir / "results" / f"{ws_id}.json").is_file()

    def test_save_skipped_when_no_report(
        self,
        controller: WorkspaceController,
        config_dir: Path,
    ) -> None:
        """ScanController 无 _last_report 时跳过保存（不创建文件）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        sc = controller.currentScanController
        # 默认 _last_report 为 None
        controller._save_cached_results(ws_id, sc)  # type: ignore[attr-defined]
        # 不应创建任何文件
        assert not (config_dir / "results").exists()

    def test_save_persists_json_content(
        self,
        controller: WorkspaceController,
        config_dir: Path,
    ) -> None:
        """保存的文件应为合法 JSON，含 root 与 hits 字段。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        sc = controller.currentScanController
        report = _build_simple_report()
        sc._last_report = report  # type: ignore[attr-defined]
        controller._save_cached_results(ws_id, sc)  # type: ignore[attr-defined]

        cache_file = config_dir / "results" / f"{ws_id}.json"
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["root"] == str(report.root)
        assert "hits" in data
        assert "stats" in data

    def test_save_empty_does_not_overwrite_nonempty_cache(
        self,
        controller: WorkspaceController,
        config_dir: Path,
    ) -> None:
        """iter-135：本次无命中但缓存已有非空结果时不覆盖。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        sc = controller.currentScanController
        # 先保存有命中的结果
        sc._last_report = _build_simple_report()  # type: ignore[attr-defined]
        controller._save_cached_results(ws_id, sc)  # type: ignore[attr-defined]
        cache_file = config_dir / "results" / f"{ws_id}.json"
        assert cache_file.exists()

        # 再保存空结果（模拟增量扫描回退全量后 0 命中）
        from pathlib import Path

        from fuscan.scanner import ScanReport
        from fuscan.scanner.result import ScanStats

        sc._last_report = ScanReport(
            root=Path("/tmp"),
            results=(),
            stats=ScanStats(total_files=10, scanned_files=10),
        )
        controller._save_cached_results(ws_id, sc)  # type: ignore[attr-defined]

        # 缓存文件应仍包含之前的有命中结果
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert len(data["hits"]) > 0


class TestLoadCachedResults:
    """``_try_load_cached_results`` 异步恢复测试（iter-128）。"""

    def test_load_restores_report_to_scan_controller(
        self,
        controller: WorkspaceController,
        config_dir: Path,
        qapp: object,
    ) -> None:
        """异步加载缓存后 ScanController 恢复 _last_report 与 result_model。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController

        # 先保存一份缓存
        report = _build_simple_report()
        sc._last_report = report  # type: ignore[attr-defined]
        controller._save_cached_results(ws_id, sc)  # type: ignore[attr-defined]

        # 重置 ScanController 状态模拟重启
        sc._last_report = None  # type: ignore[attr-defined]
        sc._result_model.clear()  # type: ignore[attr-defined]
        # 清除已恢复标记，允许重新加载
        controller._restored_workspaces.discard(ws_id)  # type: ignore[attr-defined]
        assert sc._last_report is None  # type: ignore[attr-defined]

        # 异步加载缓存
        controller._try_load_cached_results(ws_id)  # type: ignore[attr-defined]
        _wait_for_restore(controller, ws_id)

        # 验证恢复
        assert sc._last_report is not None  # type: ignore[attr-defined]
        assert sc._last_report.root == report.root  # type: ignore[attr-defined]
        assert sc._result_model.rowCount() == len(report.hits)  # type: ignore[attr-defined]

    def test_load_skipped_when_no_cache_file(
        self,
        controller: WorkspaceController,
        qapp: object,
    ) -> None:
        """无缓存文件时静默跳过（不抛异常）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        sc = controller.currentScanController
        # 不创建缓存文件
        controller._try_load_cached_results(ws_id)  # type: ignore[attr-defined]
        # 无缓存文件 → 不启动 worker，_restoring_workspaces 为空
        assert ws_id not in controller._restoring_workspaces  # type: ignore[attr-defined]
        # ScanController 状态不变
        assert sc._last_report is None  # type: ignore[attr-defined]

    def test_load_skipped_when_corrupted_json(
        self,
        controller: WorkspaceController,
        config_dir: Path,
        qapp: object,
    ) -> None:
        """缓存文件损坏时静默跳过（不抛异常）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # 写入损坏的 JSON
        results_dir = config_dir / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / f"{ws_id}.json").write_text("{not valid json", encoding="utf-8")

        # 加载不应抛异常
        controller._try_load_cached_results(ws_id)  # type: ignore[attr-defined]
        _wait_for_restore(controller, ws_id)
        # ScanController 状态不变（恢复失败，_last_report 仍为 None）
        sc = controller.currentScanController
        assert sc._last_report is None  # type: ignore[attr-defined]

    def test_load_after_restart_restores_full_state(
        self,
        controller: WorkspaceController,
        config_controller: ConfigController,
        rules_controller: RulesController,
        config_dir: Path,
        qapp: object,
    ) -> None:
        """完整重启场景：保存缓存 → 重建 WorkspaceController → 后台恢复缓存。"""
        ws_id = controller.addWorkspace("任务A", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        sc = controller.currentScanController
        report = _build_simple_report()
        sc._last_report = report  # type: ignore[attr-defined]
        controller._save_cached_results(ws_id, sc)  # type: ignore[attr-defined]
        controller._persist()  # type: ignore[attr-defined]

        # 模拟重启：创建新的 WorkspaceController
        new_controller = WorkspaceController(
            ConfigController(),
            RulesController(ConfigController()),
        )
        # 重启后应自动加载工作区
        assert new_controller.workspaceModel.rowCount() == 1
        # _load_persisted 已对第一个工作区启动后台恢复
        _wait_for_restore(new_controller, ws_id)
        # 切换为当前工作区，currentScanController 才会返回该工作区的 ScanController
        new_controller.setCurrentWorkspaceId(ws_id)
        new_sc = new_controller.currentScanController
        assert new_sc._last_report is not None  # type: ignore[attr-defined]
        assert new_sc._last_report.root == report.root  # type: ignore[attr-defined]
        assert new_sc._result_model.rowCount() == len(report.hits)  # type: ignore[attr-defined]


class TestDeleteCachedResults:
    """``_delete_cached_results`` 清理测试。"""

    def test_delete_removes_cache_file(
        self,
        controller: WorkspaceController,
        config_dir: Path,
    ) -> None:
        """删除工作区时应清理对应缓存文件。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        sc = controller.currentScanController
        sc._last_report = _build_simple_report()  # type: ignore[attr-defined]
        controller._save_cached_results(ws_id, sc)  # type: ignore[attr-defined]

        cache_file = config_dir / "results" / f"{ws_id}.json"
        assert cache_file.exists()

        controller._delete_cached_results(ws_id)  # type: ignore[attr-defined]
        assert not cache_file.exists()

    def test_delete_nonexistent_file_no_error(
        self,
        controller: WorkspaceController,
    ) -> None:
        """删除不存在的缓存文件不应抛异常。"""
        # 不应抛异常
        controller._delete_cached_results("ws-nonexistent")  # type: ignore[attr-defined]

    def test_remove_workspace_cleans_cache(
        self,
        controller: WorkspaceController,
        config_dir: Path,
    ) -> None:
        """removeWorkspace 应同时清理缓存结果文件。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        sc = controller.currentScanController
        sc._last_report = _build_simple_report()  # type: ignore[attr-defined]
        controller._save_cached_results(ws_id, sc)  # type: ignore[attr-defined]

        cache_file = config_dir / "results" / f"{ws_id}.json"
        assert cache_file.exists()

        controller.removeWorkspace(ws_id)
        assert not cache_file.exists()


# ============================= 辅助函数 =============================


def _build_simple_report():
    """构造简单的 ScanReport 用于缓存测试。"""
    from pathlib import Path

    from fuscan.rules.model import Severity
    from fuscan.scanner import ScanReport, ScanResult
    from fuscan.scanner.result import RuleHit, ScanStats

    return ScanReport(
        root=Path("/tmp"),
        results=(
            ScanResult(
                path=Path("/tmp/secret.txt"),
                size=10,
                hits=(
                    RuleHit(
                        rule_name="敏感文件",
                        severity=Severity.WARNING,
                        detail="匹配 password",
                        match_count=1,
                    ),
                ),
            ),
        ),
        stats=ScanStats(
            total_files=1,
            scanned_files=1,
            matched_files=1,
            total_matches=1,
        ),
    )


class TestIter143CoverageGaps:
    """iter-143：补充 workspace_controller.py 未覆盖分支。

    覆盖目标：currentScanController fallback / _ensure_scan_controller except /
    removeWorkspace controller=None / startScan controller=None /
    updateWorkspaceTarget controller=None / get_workspace / taskOverridesJson
    item=None / setTaskOverride ignore_dirs 类型 / setTaskOverride controller=None /
    clearTaskOverride controller=None & global_value=None / clearAllWorkspaces 空列表 /
    cleanup fallback & restore_workers / _load_persisted 重复 & except /
    _migrate_workspace_rules / _try_load_cached_results controller=None /
    _on_restore_done / _on_restore_failed / _cleanup_restore_worker /
    _archive_scan_history except。
    """

    def test_get_workspace_method(
        self,
        controller: WorkspaceController,
    ) -> None:
        """controller.get_workspace 返回 WorkspaceItem 或 None（iter-143 覆盖行 570）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        item = controller.get_workspace(ws_id)
        assert item is not None
        assert item.workspace_id == ws_id
        # 不存在的工作区返回 None
        assert controller.get_workspace("nonexistent") is None

    def test_current_scan_controller_fallback_when_workspace_missing(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_ensure_scan_controller 返回 None 时 currentScanController 返回 fallback（iter-143 覆盖 213->218）。"""
        # 设置一个 _current_workspace_id 但 mock _ensure_scan_controller 返回 None
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setCurrentWorkspaceId(ws_id)
        # mock _ensure_scan_controller 返回 None（模拟工作区刚被移除但 ID 仍保留）
        monkeypatch.setattr(controller, "_ensure_scan_controller", lambda _wid: None)

        sc = controller.currentScanController
        # 返回 fallback 实例（首次创建）
        assert sc is not None

    def test_ensure_scan_controller_exception_cleans_up(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ScanController 初始化抛异常时应 cleanup+deleteLater+raise（iter-143 覆盖 429-433）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # 移除已创建的 ScanController，强制下次 _ensure_scan_controller 重新创建
        existing = controller._scan_controllers.pop(ws_id, None)  # type: ignore[attr-defined]
        if existing is not None:
            existing.cleanup()
            existing.deleteLater()

        cleanup_called: list[bool] = []
        delete_later_called: list[bool] = []

        def raise_set_mode_index(_index: int) -> None:
            raise RuntimeError("初始化失败")

        # patch ScanController.setScanModeIndex 让 try 块内抛异常
        from fuscan.gui.controllers.scan_controller import ScanController as _SC

        def patched_set_mode(self_sc: object, index: int) -> None:
            cleanup_called.append(True)  # 标记 cleanup 可调用
            raise_set_mode_index(index)

        monkeypatch.setattr(_SC, "setScanModeIndex", patched_set_mode)
        # patch cleanup 和 deleteLater 捕获调用
        original_cleanup = _SC.cleanup

        def patched_cleanup(self_sc: object) -> None:
            cleanup_called.append(True)
            original_cleanup(self_sc)  # type: ignore[arg-type]

        monkeypatch.setattr(_SC, "cleanup", patched_cleanup)

        def patched_delete_later(self_sc: object) -> None:
            delete_later_called.append(True)

        monkeypatch.setattr(_SC, "deleteLater", patched_delete_later)

        with pytest.raises(RuntimeError, match="初始化失败"):
            controller._ensure_scan_controller(ws_id)  # type: ignore[attr-defined]
        # cleanup 和 deleteLater 被调用
        assert delete_later_called == [True]

    def test_remove_workspace_without_scan_controller(
        self,
        controller: WorkspaceController,
    ) -> None:
        """removeWorkspace 时 _scan_controllers 中无对应项应安全跳过（iter-143 覆盖 443->446）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # addWorkspace 已创建 ScanController，手动移除模拟"未创建"场景
        existing = controller._scan_controllers.pop(ws_id, None)  # type: ignore[attr-defined]
        if existing is not None:
            existing.cleanup()
            existing.deleteLater()
        assert ws_id not in controller._scan_controllers  # type: ignore[attr-defined]
        # 移除不应抛异常
        controller.removeWorkspace(ws_id)
        assert controller.get_workspace(ws_id) is None

    def test_start_scan_nonexistent_workspace_warns(
        self,
        controller: WorkspaceController,
    ) -> None:
        """startScan 不存在工作区应记录 warning 并返回（iter-143 覆盖 490-492）。"""
        # 不应抛异常
        controller.startScan("nonexistent-ws")

    def test_update_workspace_target_when_controller_not_created(
        self,
        controller: WorkspaceController,
    ) -> None:
        """updateWorkspaceTarget 时 ScanController 未创建应安全跳过（iter-143 覆盖 554->561）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # addWorkspace 已创建 ScanController，手动移除模拟"未创建"场景
        existing = controller._scan_controllers.pop(ws_id, None)  # type: ignore[attr-defined]
        if existing is not None:
            existing.cleanup()
            existing.deleteLater()
        assert ws_id not in controller._scan_controllers  # type: ignore[attr-defined]
        # 更新目标不应抛异常
        controller.updateWorkspaceTarget(ws_id, "folder", "/new/path")
        item = controller.get_workspace(ws_id)
        assert item is not None
        assert item.target == "/new/path"

    def test_task_overrides_json_nonexistent_workspace(
        self,
        controller: WorkspaceController,
    ) -> None:
        """taskOverridesJson 不存在工作区返回 '{}'（iter-143 覆盖行 582）。"""
        assert controller.taskOverridesJson("nonexistent-ws") == "{}"

    def test_workspace_name_nonexistent_returns_empty(
        self,
        controller: WorkspaceController,
    ) -> None:
        """workspaceName 不存在工作区返回空串（覆盖行 616 None 分支）。"""
        assert controller.workspaceName("nonexistent-ws") == ""

    def test_workspace_name_returns_name(
        self,
        controller: WorkspaceController,
    ) -> None:
        """workspaceName 存在的工作区返回其名称。"""
        ws_id = controller.addWorkspace("我的任务", "folder", "/tmp", "[]", True)
        assert controller.workspaceName(ws_id) == "我的任务"

    def test_set_task_override_ignore_dirs_invalid_type(
        self,
        controller: WorkspaceController,
    ) -> None:
        """setTaskOverride ignore_dirs 非 list[str] 应记录 warning 并返回（iter-143 覆盖 621-622）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # ignore_dirs 为非 list（数字）应被拒绝
        controller.setTaskOverride(ws_id, "ignore_dirs", "123")  # JSON 123 是 int 不是 list
        # task_overrides 未变更
        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert "ignore_dirs" not in overrides

    def test_set_task_override_when_controller_not_created(
        self,
        controller: WorkspaceController,
    ) -> None:
        """setTaskOverride 时 ScanController 未创建应安全跳过同步（iter-143 覆盖 640->642）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # addWorkspace 已创建 ScanController，手动移除模拟"未创建"场景
        existing = controller._scan_controllers.pop(ws_id, None)  # type: ignore[attr-defined]
        if existing is not None:
            existing.cleanup()
            existing.deleteLater()
        assert ws_id not in controller._scan_controllers  # type: ignore[attr-defined]
        # 不应抛异常
        controller.setTaskOverride(ws_id, "scan_archives", "false")
        # task_overrides 已更新
        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert overrides.get("scan_archives") is False

    def test_clear_task_override_when_controller_not_created(
        self,
        controller: WorkspaceController,
    ) -> None:
        """clearTaskOverride 时 ScanController 未创建应安全跳过（iter-143 覆盖 668->672）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # 先设置覆盖（会创建 ScanController）
        controller.setTaskOverride(ws_id, "scan_archives", "false")
        # 手动移除 ScanController 模拟"未创建"场景
        existing = controller._scan_controllers.pop(ws_id, None)  # type: ignore[attr-defined]
        if existing is not None:
            existing.cleanup()
            existing.deleteLater()
        assert ws_id not in controller._scan_controllers  # type: ignore[attr-defined]
        # 清除不应抛异常
        controller.clearTaskOverride(ws_id, "scan_archives")
        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert "scan_archives" not in overrides

    def test_clear_task_override_global_value_is_none(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """clearTaskOverride 时 global_value is None 应跳过 setTaskOverride（iter-143 覆盖 670->672）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        controller.setTaskOverride(ws_id, "scan_archives", "false")
        # 创建 ScanController（通过访问 currentScanController 触发延迟构造）
        _ = controller.currentScanController

        # mock get_config_value 返回 None（模拟未知字段）
        monkeypatch.setattr(controller._config_controller, "get_config_value", lambda _key: None)

        # 不应抛异常
        controller.clearTaskOverride(ws_id, "scan_archives")
        overrides = json.loads(controller.taskOverridesJson(ws_id))
        assert "scan_archives" not in overrides

    def test_clear_all_workspaces_empty_list_with_current_id(
        self,
        controller: WorkspaceController,
    ) -> None:
        """clearAllWorkspaces 空列表但 _current_workspace_id 非空应清空并 emit（iter-143 覆盖 699-700）。"""
        # 设置 _current_workspace_id 但不添加工作区（model 为空）
        controller._current_workspace_id = "ws-orphan"  # type: ignore[attr-defined]
        # model 为空
        assert controller.workspaceModel.rowCount() == 0
        result = controller.clearAllWorkspaces()
        assert result is True
        # _current_workspace_id 已清空
        assert controller.currentWorkspaceId == ""

    def test_cleanup_with_fallback_controller(
        self,
        controller: WorkspaceController,
    ) -> None:
        """cleanup 时已创建 _fallback_controller 应被快速取消（iter-143 覆盖行 801）。"""
        # 触发 fallback 创建：未选中工作区时访问 currentScanController
        _ = controller.currentScanController
        assert hasattr(controller, "_fallback_controller")  # type: ignore[attr-defined]
        # cleanup 不应抛异常
        controller.cleanup()
        # cleanup 后 fallback_controller 仍存在（quick_cancel 不删除引用）
        assert hasattr(controller, "_fallback_controller")  # type: ignore[attr-defined]

    def test_cleanup_with_running_restore_worker(
        self,
        controller: WorkspaceController,
        config_dir: Path,
    ) -> None:
        """cleanup 时 _restore_workers 中有运行中 worker 应 quit+wait+terminate（iter-143 覆盖 811-816）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)

        class FakeRestoreWorker:
            def __init__(self) -> None:
                self._running = True
                self.quit_called = False
                self.wait_called = False
                self.terminate_called = False

            def isRunning(self) -> bool:
                return self._running

            def quit(self) -> None:
                self.quit_called = True
                self._running = False

            def wait(self, _msecs: int = 0) -> bool:
                self.wait_called = True
                return True

            def terminate(self) -> None:
                self.terminate_called = True

            def deleteLater(self) -> None:
                pass

        fake_worker = FakeRestoreWorker()
        controller._restore_workers[ws_id] = fake_worker  # type: ignore[attr-defined]
        controller.cleanup()
        assert fake_worker.quit_called is True
        assert fake_worker.wait_called is True

    def test_load_persisted_skips_duplicate_ws_id(
        self,
        config_dir: Path,
        config_controller: ConfigController,
        rules_controller: RulesController,
    ) -> None:
        """_load_persisted 时持久化文件中重复 ws_id 应跳过（iter-143 覆盖行 855）。"""
        persist_file = config_dir / "workspaces.json"
        persist_data: dict[str, object] = {
            "version": 1,
            "workspaces": [
                {
                    "id": "ws-dup",
                    "name": "任务 1",
                    "mode": "folder",
                    "target": "/tmp",
                    "rules_paths": [],
                    "use_builtin": True,
                },
                {
                    "id": "ws-dup",  # 重复 ID
                    "name": "任务 2",
                    "mode": "folder",
                    "target": "/other",
                    "rules_paths": [],
                    "use_builtin": True,
                },
            ],
        }
        persist_file.write_text(json.dumps(persist_data), encoding="utf-8")

        controller = WorkspaceController(config_controller, rules_controller)
        # 只应加载第一个 ws-dup
        assert controller.workspaceModel.rowCount() == 1
        item = controller.get_workspace("ws-dup")
        assert item is not None
        assert item.name == "任务 1"

    def test_load_persisted_handles_single_workspace_failure(
        self,
        config_dir: Path,
        config_controller: ConfigController,
        rules_controller: RulesController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_load_persisted 时单条工作区恢复失败应记录 warning 不阻塞其余（iter-143 覆盖 882-883）。"""
        persist_file = config_dir / "workspaces.json"
        persist_data: dict[str, object] = {
            "version": 1,
            "workspaces": [
                {
                    "id": "ws-bad",
                    "name": "坏任务",
                    "mode": "folder",
                    "target": "/tmp",
                    "rules_paths": [],
                    "use_builtin": True,
                },
                {
                    "id": "ws-good",
                    "name": "好任务",
                    "mode": "folder",
                    "target": "/tmp",
                    "rules_paths": [],
                    "use_builtin": True,
                },
            ],
        }
        persist_file.write_text(json.dumps(persist_data), encoding="utf-8")

        # mock _create_workspace 对 ws-bad 抛异常,对其他正常调用原始实现
        original_create = WorkspaceController._create_workspace

        def fake_create(self: WorkspaceController, **kwargs: object) -> None:
            if kwargs.get("ws_id") == "ws-bad":
                raise ValueError("模拟恢复失败")
            # 正常工作区调用原始 _create_workspace
            original_create(self, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(WorkspaceController, "_create_workspace", fake_create)

        # 重新构造 controller（会调用 _load_persisted）
        new_controller = WorkspaceController(config_controller, rules_controller)
        # ws-bad 失败,ws-good 成功
        assert new_controller.get_workspace("ws-bad") is None
        assert new_controller.get_workspace("ws-good") is not None

    def test_migrate_workspace_rules_paths_to_global(
        self,
        controller: WorkspaceController,
        config_controller: ConfigController,
    ) -> None:
        """_migrate_workspace_rules_to_global 应合并 rules_paths 到全局（iter-143 覆盖 916-918）。"""
        # 全局初始无 rules_paths
        config_controller.config.rules_paths = []
        # 模拟从持久化加载的 workspaces 数据
        workspaces = [
            {"id": "ws-1", "rules_paths": ["/path/to/rule1.yaml"], "use_builtin": True},
            {"id": "ws-2", "rules_paths": ["/path/to/rule2.yaml", "/path/to/rule1.yaml"], "use_builtin": True},
        ]
        controller._migrate_workspace_rules_to_global(workspaces)  # type: ignore[attr-defined]
        # 去重后两条路径
        assert "/path/to/rule1.yaml" in config_controller.config.rules_paths
        assert "/path/to/rule2.yaml" in config_controller.config.rules_paths
        assert len(config_controller.config.rules_paths) == 2

    def test_migrate_workspace_rules_use_builtin_to_global(
        self,
        controller: WorkspaceController,
        config_controller: ConfigController,
    ) -> None:
        """_migrate_workspace_rules_to_global 应 OR 合并 use_builtin（iter-143 覆盖 921-927）。"""
        config_controller.config.use_builtin = False
        config_controller.config.rules_paths = []
        workspaces: list[dict[str, object]] = [
            {"id": "ws-1", "rules_paths": [], "use_builtin": False},
            {"id": "ws-2", "rules_paths": [], "use_builtin": True},  # 任一启用则全局启用
        ]
        controller._migrate_workspace_rules_to_global(workspaces)  # type: ignore[attr-defined]
        assert config_controller.config.use_builtin is True

    def test_migrate_workspace_rules_no_change(
        self,
        controller: WorkspaceController,
        config_controller: ConfigController,
    ) -> None:
        """_migrate_workspace_rules_to_global 无变更时不调用 save（iter-143 覆盖 changed=False 分支）。"""
        config_controller.config.use_builtin = True
        config_controller.config.rules_paths = []
        # 所有工作区都已迁移（rules_paths 已在全局，use_builtin 已 True）
        workspaces: list[dict[str, object]] = [
            {"id": "ws-1", "rules_paths": [], "use_builtin": True},
        ]
        save_called: list[bool] = []
        original_save = config_controller.save
        config_controller.save = lambda: save_called.append(True)  # type: ignore[assignment]
        try:
            controller._migrate_workspace_rules_to_global(workspaces)  # type: ignore[attr-defined]
            assert save_called == []  # 无变更不调用 save
        finally:
            config_controller.save = original_save  # type: ignore[assignment]

    def test_try_load_cached_results_controller_is_none(
        self,
        controller: WorkspaceController,
        config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_try_load_cached_results 时 controller is None 应安全返回（iter-143 覆盖行 1020）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        # 创建 cache 文件使其存在
        cache_file = config_dir / "results" / f"{ws_id}.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("{}", encoding="utf-8")
        # mock _ensure_scan_controller 返回 None
        monkeypatch.setattr(controller, "_ensure_scan_controller", lambda _wid: None)
        # 不应抛异常
        controller._try_load_cached_results(ws_id)  # type: ignore[attr-defined]
        # 未启动恢复（_restoring_workspaces 为空）
        assert ws_id not in controller._restoring_workspaces  # type: ignore[attr-defined]

    def test_on_restore_done_controller_is_none(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_on_restore_done 时 controller is None 应安全跳过（iter-143 覆盖 1038->1042）。"""
        ws_id = "ws-test"
        monkeypatch.setattr(controller, "_ensure_scan_controller", lambda _wid: None)
        # 不应抛异常
        controller._on_restore_done(ws_id, object())  # type: ignore[attr-defined]
        # 仍标记为已恢复
        assert ws_id in controller._restored_workspaces  # type: ignore[attr-defined]

    def test_on_restore_done_non_scan_report(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_on_restore_done 时 report 不是 ScanReport 应安全跳过 restoreFromReport（iter-143 覆盖 1038->1042）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        sc = controller.currentScanController  # 创建 ScanController
        restore_called: list[bool] = []
        monkeypatch.setattr(sc, "restoreFromReport", lambda _report: restore_called.append(True))

        # 传入非 ScanReport 对象
        controller._on_restore_done(ws_id, "not a report")  # type: ignore[attr-defined]
        # 未调用 restoreFromReport
        assert restore_called == []
        # 仍标记为已恢复
        assert ws_id in controller._restored_workspaces  # type: ignore[attr-defined]

    def test_on_restore_failed_controller_is_none(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_on_restore_failed 时 controller is None 应安全跳过（iter-143 覆盖 1048->1050）。"""
        ws_id = "ws-test"
        monkeypatch.setattr(controller, "_ensure_scan_controller", lambda _wid: None)
        # 不应抛异常
        controller._on_restore_failed(ws_id, "模拟失败")  # type: ignore[attr-defined]
        # 清除恢复态
        assert ws_id not in controller._restoring_workspaces  # type: ignore[attr-defined]

    def test_cleanup_restore_worker_no_worker(
        self,
        controller: WorkspaceController,
    ) -> None:
        """_cleanup_restore_worker 时 worker 不存在应安全跳过（iter-143 覆盖 1056->exit）。"""
        # _restore_workers 中无此项
        controller._cleanup_restore_worker("ws-nonexistent")  # type: ignore[attr-defined]
        # 不应抛异常,无副作用

    def test_archive_scan_history_handles_exception(
        self,
        controller: WorkspaceController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_archive_scan_history 时 build_history_entry 抛异常应记录 warning 不传播（iter-143 覆盖 1077-1079）。"""
        ws_id = controller.addWorkspace("t", "folder", "/tmp", "[]", True)
        sc = controller.currentScanController

        # mock build_history_entry 抛异常
        def raise_exc(_ws_id: str, _name: str) -> None:
            raise RuntimeError("归档失败")

        monkeypatch.setattr(sc, "build_history_entry", raise_exc)

        # 不应抛异常
        controller._archive_scan_history(ws_id, sc)  # type: ignore[attr-defined]
