"""工作区列表模型（QAbstractListModel）。

按 rule-12-pyside-dev.md，工作区列表用 Model 暴露给 QML ``ListView`` 绑定，
禁止 QML 侧 ``ListModel`` 动态 append。

每个 :class:`WorkspaceItem` 描述一个独立的扫描任务，包含名称、扫描模式、
目标路径、规则文件列表与最近一次扫描结果摘要。所有字段在 Python 端维护，
QML 通过 role 读取展示字段，通过 :class:`WorkspaceController` 槽修改状态。

公共 API：

- :class:`WorkspaceItem`：工作区数据类（frozen dataclass）
- :class:`WorkspaceListModel`：``QAbstractListModel`` 子类
- :meth:`WorkspaceListModel.add_workspace`：追加工作区
- :meth:`WorkspaceListModel.remove_workspace`：按 ID 移除工作区
- :meth:`WorkspaceListModel.update_workspace`：按 ID 更新字段
- :meth:`WorkspaceListModel.get_workspace`：按 ID 取工作区
- :meth:`WorkspaceListModel.get_by_index`：按行号取工作区
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt  # pyrefly: ignore [missing-import]

from fuscan.gui.scan_mode import scan_mode_text

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "STR_STATUS_CANCELLED",
    "STR_STATUS_DONE",
    "STR_STATUS_FAILED",
    "STR_STATUS_PAUSED",
    "STR_STATUS_READY",
    "STR_STATUS_SCANNING",
    "WorkspaceItem",
    "WorkspaceListModel",
]

# 工作区状态展示文本（跨 controller/QML 共享，QML 侧用字符串字面量与此处对齐）。
# 历史上这些字符串散落在 workspace_controller/scan_controller/WorkspaceCard.qml 等
# 多处硬编码，本处集中为唯一来源，便于检索与重命名。
STR_STATUS_READY: str = "就绪"
STR_STATUS_SCANNING: str = "扫描中"
STR_STATUS_PAUSED: str = "已暂停"
STR_STATUS_DONE: str = "已完成"
STR_STATUS_CANCELLED: str = "已完成[用户取消]"
STR_STATUS_FAILED: str = "失败"

# 扫描中或已暂停的状态集合（用于"拒绝修改目标/清空工作区"等守卫判断）
ACTIVE_STATUS_TEXTS: frozenset[str] = frozenset({STR_STATUS_SCANNING, STR_STATUS_PAUSED})

# QML role 名称（与 HomePage.qml delegate 中 model.* 一致）
_ROLE_WORKSPACE_ID = b"workspaceId"
_ROLE_NAME = b"name"
_ROLE_MODE_TEXT = b"modeText"
_ROLE_TARGET = b"target"
_ROLE_RULES_TEXT = b"rulesText"
_ROLE_STATUS_TEXT = b"statusText"
_ROLE_MATCHED_COUNT = b"matchedCount"
_ROLE_PASSED_COUNT = b"passedCount"
_ROLE_SKIPPED_COUNT = b"skippedCount"
_ROLE_ERROR_COUNT = b"errorCount"
_ROLE_LAST_SUMMARY = b"lastSummary"
_ROLE_INDEX = b"index"
_ROLE_RULES_TAGS = b"rulesTags"
_ROLE_COLLECTED_COUNT = b"collectedCount"

_ROLES: dict[int, bytes] = {
    Qt.UserRole + 1: _ROLE_WORKSPACE_ID,
    Qt.UserRole + 2: _ROLE_NAME,
    Qt.UserRole + 3: _ROLE_MODE_TEXT,
    Qt.UserRole + 4: _ROLE_TARGET,
    Qt.UserRole + 5: _ROLE_RULES_TEXT,
    Qt.UserRole + 6: _ROLE_STATUS_TEXT,
    Qt.UserRole + 7: _ROLE_MATCHED_COUNT,
    Qt.UserRole + 8: _ROLE_PASSED_COUNT,
    Qt.UserRole + 9: _ROLE_SKIPPED_COUNT,
    Qt.UserRole + 10: _ROLE_ERROR_COUNT,
    Qt.UserRole + 11: _ROLE_LAST_SUMMARY,
    Qt.UserRole + 12: _ROLE_INDEX,
    Qt.UserRole + 13: _ROLE_RULES_TAGS,
    Qt.UserRole + 14: _ROLE_COLLECTED_COUNT,
}

# 字段名 → 关联 role 列表（含派生属性依赖）
# update_workspace 按字段对比仅 emit 实际变化的 role，
# 避免扫描进度回调（0.3s 节流）时全量 14 个 role 刷新导致 QML 重新评估所有绑定
_FIELD_TO_ROLES: dict[str, list[int]] = {
    "workspace_id": [Qt.UserRole + 1],
    "name": [Qt.UserRole + 2],
    "mode_str": [Qt.UserRole + 3],  # mode_text 派生
    "target": [Qt.UserRole + 4],
    "rules_paths": [Qt.UserRole + 5, Qt.UserRole + 13],  # rules_text/rules_tags 派生
    "use_builtin": [Qt.UserRole + 5, Qt.UserRole + 13],
    "status_text": [Qt.UserRole + 6],
    "matched_count": [Qt.UserRole + 7],
    "passed_count": [Qt.UserRole + 8],
    "skipped_count": [Qt.UserRole + 9],
    "error_count": [Qt.UserRole + 10],
    "last_summary": [Qt.UserRole + 11],
    "collected_count": [Qt.UserRole + 14],
    # task_overrides 不通过 role 暴露给 QML，无需 emit
}


@dataclass(frozen=True)
class WorkspaceItem:
    """工作区数据类（不可变，修改通过 :func:`dataclasses.replace` 重建）。

    :param workspace_id: 唯一标识（``"ws-<8位hex>"`` 格式）
    :param name: 工作区名称（用户输入或自动生成）
    :param mode_str: 扫描模式字符串（``"drive"``/``"folder"``）
    :param target: 扫描目标（盘符模式为盘符如 ``"C:\\"``，文件夹模式为路径）
    :param rules_paths: 规则文件路径列表（空列表表示仅用内置规则）
    :param use_builtin: 是否启用内置规则
    :param status_text: 状态文本（``"就绪"``/``"扫描中"``/``"已完成"``/``"已取消"``/``"失败"``）
    :param matched_count: 命中文件数
    :param passed_count: 已通过文件数
    :param skipped_count: 跳过文件数
    :param error_count: 错误文件数
    :param last_summary: 最近一次扫描摘要（含速度等）
    :param collected_count: walk 阶段收集到的符合文件类型的文件数
    :param task_overrides: 任务级配置覆盖

        ``dict[str, object]``，键为 :class:`fuscan.config.Config` 字段名，
        值为该任务专属的覆盖值。支持的字段：

        - ``"scan_archives"``: bool
        - ``"max_workers"``: int
        - ``"max_file_size"``: int（字节）
        - ``"max_depth"``: int
        - ``"ignore_dirs"``: tuple[str, ...]
        - ``"rules_paths"``: tuple[str, ...]（任务级规则文件覆盖）
        - ``"use_builtin"``: bool（任务级内置规则开关覆盖）

        未在 dict 中的字段使用全局 :class:`Config` 默认值。
    :param last_activity_time: 最近活动时间戳（``time.time()``），用于列表排序。
        新建或启动扫描时更新为当前时间，列表按此字段倒序排列（最新活动在最上方）。
    """

    workspace_id: str
    name: str
    mode_str: str = "folder"
    target: str = ""
    rules_paths: tuple[str, ...] = field(default_factory=tuple)
    use_builtin: bool = True
    status_text: str = STR_STATUS_READY
    matched_count: int = 0
    passed_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    last_summary: str = ""
    collected_count: int = 0
    task_overrides: dict[str, object] = field(default_factory=dict)
    last_activity_time: float = field(default_factory=time.time)

    @property
    def mode_text(self) -> str:
        """扫描模式中文文本。"""
        return scan_mode_text(self.mode_str)

    @property
    def rules_text(self) -> str:
        """规则摘要文本（如 ``"内置 + 2 文件"`` 或 ``"3 文件"``）。"""
        files_count = len(self.rules_paths)
        if self.use_builtin and files_count > 0:
            return f"内置 + {files_count} 文件"
        if self.use_builtin:
            return "内置规则"
        if files_count > 0:
            return f"{files_count} 文件"
        return "未配置规则"

    @property
    def rules_tags(self) -> list[dict[str, object]]:
        """规则标签列表（供 QML TAG 标签展示）。

        每项：``{"name": 规则名, "is_builtin": bool}``

        - 内置规则：``is_builtin=True``，name 为 ``"内置"``
        - 用户规则文件：``is_builtin=False``，name 为文件名（含扩展名）
        - 未配置任何规则时返回空列表
        """
        tags: list[dict[str, object]] = []
        if self.use_builtin:
            tags.append({"name": "内置", "is_builtin": True})
        for path in self.rules_paths:
            tags.append({"name": Path(path).name, "is_builtin": False})
        return tags


class WorkspaceListModel(QAbstractListModel):  # pyrefly: ignore [invalid-inheritance]
    """工作区列表模型。

    存储 :class:`WorkspaceItem` 列表，按 role 返回展示字段。
    所有修改操作（add/remove/update）均 emit 对应 ``QAbstractItemModel`` 信号，
    QML ``ListView`` 自动刷新。
    """

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._items: list[WorkspaceItem] = []

    def rowCount(self, parent: QModelIndex = None) -> int:  # type: ignore[assignment]
        """返回工作区数量。"""
        if parent is None:
            parent = QModelIndex()
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self) -> dict[int, bytes]:
        """返回 role 名称映射。"""
        return _ROLES

    # role 偏移量 → WorkspaceItem 属性名映射（避免 data() 中长串 if-elif）
    _ROLE_TO_ATTR: dict[int, str] = {
        Qt.UserRole + 1: "workspace_id",
        Qt.UserRole + 2: "name",
        Qt.UserRole + 3: "mode_text",
        Qt.UserRole + 4: "target",
        Qt.UserRole + 5: "rules_text",
        Qt.UserRole + 6: "status_text",
        Qt.UserRole + 7: "matched_count",
        Qt.UserRole + 8: "passed_count",
        Qt.UserRole + 9: "skipped_count",
        Qt.UserRole + 10: "error_count",
        Qt.UserRole + 11: "last_summary",
        Qt.UserRole + 13: "rules_tags",
        Qt.UserRole + 14: "collected_count",
    }

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:
        """按 role 返回对应字段值。"""
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return ""
        item = self._items[index.row()]
        attr = self._ROLE_TO_ATTR.get(role)
        if attr is not None:
            return getattr(item, attr)
        if role == Qt.UserRole + 12:
            return index.row()
        return ""

    # ----------------------------- 公共 API -----------------------------

    def add_workspace(self, item: WorkspaceItem) -> int:
        """插入工作区到列表顶部（最近活动在最上方），返回新行号。

        新工作区插入到列表顶部（row 0），符合「最新任务在上面」
        的交互预期。``last_activity_time`` 默认为构造时的 ``time.time()``，
        新建工作区自然排在最上方。

        :param item: 工作区数据
        :return: 新增行号（0 = 顶部）
        """
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._items.insert(0, item)
        self.endInsertRows()
        return 0

    def remove_workspace(self, workspace_id: str) -> bool:
        """按 ID 移除工作区。

        :param workspace_id: 工作区 ID
        :return: 是否成功移除
        """
        for idx, item in enumerate(self._items):
            if item.workspace_id == workspace_id:
                self.beginRemoveRows(QModelIndex(), idx, idx)
                self._items.pop(idx)
                self.endRemoveRows()
                return True
        return False

    def move_to_top(self, workspace_id: str) -> bool:
        """将指定工作区移到列表顶部（row 0）。

        增量扫描或重新扫描时调用，使最近活动的工作区排在最上方。
        更新 ``last_activity_time`` 为当前时间。

        :param workspace_id: 工作区 ID
        :return: 是否成功移动（已在顶部或不存在返回 False）
        """
        for idx, item in enumerate(self._items):
            if item.workspace_id == workspace_id:
                if idx == 0:
                    # 已在顶部，仅更新时间
                    new_item = replace(item, last_activity_time=time.time())
                    self._items[0] = new_item
                    self.dataChanged.emit(self.index(0), self.index(0))
                    return False
                # 移除当前位置，插入到顶部
                self.beginRemoveRows(QModelIndex(), idx, idx)
                self._items.pop(idx)
                self.endRemoveRows()
                new_item = replace(item, last_activity_time=time.time())
                self.beginInsertRows(QModelIndex(), 0, 0)
                self._items.insert(0, new_item)
                self.endInsertRows()
                return True
        return False

    def update_workspace(self, workspace_id: str, **changes: Any) -> bool:
        """按 ID 更新工作区字段（基于 :func:`dataclasses.replace`）。

        :param workspace_id: 工作区 ID
        :param changes: 要更新的字段关键字参数
        :return: 是否成功更新

        按字段对比新旧 item，仅 emit 实际变化字段对应的 role，
        避免扫描进度回调（0.3s 节流）时全量 14 个 role 刷新导致 QML 重新评估
        所有绑定（statusText/matchedCount/collectedCount 等）。
        ``task_overrides`` 不通过 role 暴露，变化时不 emit 信号。
        """
        for idx, item in enumerate(self._items):
            if item.workspace_id == workspace_id:
                new_item = replace(item, **changes)
                self._items[idx] = new_item
                # 计算变化的 role（去重保序）
                changed_roles = self._compute_changed_roles(item, new_item)
                if changed_roles:
                    model_index = self.index(idx, 0)
                    self.dataChanged.emit(model_index, model_index, changed_roles)
                return True
        return False

    @staticmethod
    def _compute_changed_roles(old: WorkspaceItem, new: WorkspaceItem) -> list[int]:
        """对比新旧 item，返回变化的 role 列表（去重保序）。

        遍历 :data:`_FIELD_TO_ROLES` 中所有字段，对比 ``getattr`` 值；
        派生属性（``rules_text``/``rules_tags``/``mode_text``）通过 property
        计算自动纳入对比。
        """
        changed: list[int] = []
        seen: set[int] = set()
        for field_name, roles in _FIELD_TO_ROLES.items():
            if not roles:
                continue
            if getattr(old, field_name) != getattr(new, field_name):
                for role in roles:
                    if role not in seen:
                        seen.add(role)
                        changed.append(role)
        return changed

    def get_workspace(self, workspace_id: str) -> WorkspaceItem | None:
        """按 ID 取工作区，未找到返回 None。"""
        for item in self._items:
            if item.workspace_id == workspace_id:
                return item
        return None

    def get_by_index(self, row: int) -> WorkspaceItem | None:
        """按行号取工作区，越界返回 None。"""
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def all_workspaces(self) -> list[WorkspaceItem]:
        """返回所有工作区（持久化用，按插入顺序）。"""
        return list(self._items)

    def clear(self) -> None:
        """清空所有工作区。"""
        self.beginResetModel()
        self._items.clear()
        self.endResetModel()

    @property
    def items(self) -> Sequence[WorkspaceItem]:
        """原始工作区列表（只读）。"""
        return tuple(self._items)
