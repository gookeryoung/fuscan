"""工作区控制器：管理多个扫描任务（工作区）。

工作区是 fuscan GUI 的核心组织单元：每个工作区代表一个独立的扫描任务，
包含名称、扫描模式、目标、规则配置与最近一次扫描结果摘要。所有工作区
通过 :class:`WorkspaceListModel` 暴露给 QML ``ListView`` 绑定，
QML 通过本控制器的 ``@Slot`` 修改状态。

公共 API：

- :class:`WorkspaceController`：``QObject`` 子类，管理工作区列表与 ScanController 实例
- :meth:`WorkspaceController.add_workspace`：新建工作区，返回 workspace_id
- :meth:`WorkspaceController.remove_workspace`：按 ID 移除工作区
- :meth:`WorkspaceController.start_scan`：启动指定工作区的扫描
- :meth:`WorkspaceController.toggle_pause` / :meth:`cancel_scan`：暂停/取消扫描
- :meth:`WorkspaceController.export_results`：导出指定工作区的扫描结果
- :meth:`WorkspaceController.set_current_workspace_id`：切换当前工作区（详情页用）
- :meth:`WorkspaceController.cleanup`：窗口关闭时统一清理所有 ScanController
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]

from fuscan.gui.qml.controllers.scan_controller import ScanController
from fuscan.gui.qml.models.workspace_model import WorkspaceItem, WorkspaceListModel

if TYPE_CHECKING:
    from fuscan.gui.qml.controllers.config_controller import ConfigController
    from fuscan.gui.qml.controllers.rules_controller import RulesController

__all__ = ["WorkspaceController"]

logger = logging.getLogger(__name__)

# 扫描模式字符串 ↔ 索引（与 scan_controller._SCAN_MODE_INDEX_TO_STR 一致）
_MODE_STR_TO_INDEX: dict[str, int] = {"full": 0, "drive": 1, "folder": 2}


class WorkspaceController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """工作区控制器。

    持有 :class:`WorkspaceListModel` 与 ``dict[str, ScanController]`` 映射，
    每个工作区有独立的 :class:`ScanController` 实例（独立状态/worker/结果模型）。
    共享全局 :class:`ConfigController` 与 :class:`RulesController`。

    :param config_controller: 配置控制器（共享）
    :param rules_controller: 规则控制器（共享）
    :param parent: 父 QObject
    """

    workspaceListChanged = Signal()
    currentWorkspaceChanged = Signal()

    def __init__(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_controller = config_controller
        self._rules_controller = rules_controller
        self._model = WorkspaceListModel(self)
        self._scan_controllers: dict[str, ScanController] = {}
        self._current_workspace_id: str = ""

    # ----------------------------- QML 属性 -----------------------------

    @Property(QObject, notify=workspaceListChanged)  # pyrefly: ignore [not-callable]
    def workspaceModel(self) -> WorkspaceListModel:
        """工作区列表模型。

        用 ``QObject`` 作为 Property 类型，避免 PySide2 元类型系统对
        ``QAbstractListModel*`` 未注册导致的 ``QMetaObjectBuilder`` 警告。
        QML ``ListView.model`` 接受任何 ``QAbstractItemModel*``，绑定不受影响。
        """
        return self._model

    @Property(int, notify=workspaceListChanged)  # pyrefly: ignore [not-callable]
    def workspaceCount(self) -> int:
        """工作区总数。"""
        return self._model.rowCount()

    @Property(str, notify=currentWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def currentWorkspaceId(self) -> str:
        """当前选中工作区 ID（空串表示未选中）。"""
        return self._current_workspace_id

    @Slot(str)  # pyrefly: ignore [not-callable]
    def setCurrentWorkspaceId(self, ws_id: str) -> None:
        """切换当前工作区 ID。"""
        if ws_id != self._current_workspace_id:
            self._current_workspace_id = ws_id
            self.currentWorkspaceChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property(ScanController, notify=currentWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def currentScanController(self) -> ScanController:
        """当前工作区对应的 :class:`ScanController` 实例。

        未选中工作区时返回一个默认实例（避免 QML 绑定 null 报错）。
        """
        if self._current_workspace_id and self._current_workspace_id in self._scan_controllers:
            return self._scan_controllers[self._current_workspace_id]
        # 兜底：返回一个临时实例（仅当未选中工作区时）
        if not hasattr(self, "_fallback_controller"):
            self._fallback_controller = ScanController(self._config_controller, self._rules_controller, self)
        return self._fallback_controller

    @Property(bool, notify=currentWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def hasCurrentWorkspace(self) -> bool:
        """是否有当前选中工作区。"""
        return bool(self._current_workspace_id) and self._current_workspace_id in self._scan_controllers

    # ----------------------------- QML 调用槽 -----------------------------

    @Slot(str, str, str, str, bool, result=str)  # pyrefly: ignore [not-callable]
    def addWorkspace(
        self,
        name: str,
        mode_str: str,
        target: str,
        rules_paths_json: str,
        use_builtin: bool,
    ) -> str:
        """新建工作区。

        :param name: 工作区名称（空串时自动生成）
        :param mode_str: 扫描模式字符串（``"full"``/``"drive"``/``"folder"``）
        :param target: 扫描目标（盘符或文件夹路径，全盘模式忽略）
        :param rules_paths_json: 规则文件路径列表的 JSON 字符串（如 ``"[]"``）
        :param use_builtin: 是否启用内置规则
        :return: 新工作区 ID（``"ws-<8位hex>"`` 格式）
        """
        ws_id = f"ws-{secrets.token_hex(4)}"
        try:
            rules_paths = tuple(json.loads(rules_paths_json) if rules_paths_json else ())
        except (json.JSONDecodeError, TypeError):
            logger.warning("rules_paths_json 解析失败: %s", rules_paths_json)
            rules_paths = ()

        if not name:
            name = f"任务 {self._model.rowCount() + 1}"

        item = WorkspaceItem(
            workspace_id=ws_id,
            name=name,
            mode_str=mode_str,
            target=target,
            rules_paths=rules_paths,
            use_builtin=use_builtin,
        )
        self._model.add_workspace(item)

        # 为该工作区构造独立的 ScanController
        scan_controller = ScanController(self._config_controller, self._rules_controller, self)
        # 按工作区参数初始化 ScanController
        mode_index = _MODE_STR_TO_INDEX.get(mode_str, 2)
        scan_controller.setScanModeIndex(mode_index)
        if mode_str == "drive" and target:
            scan_controller.setSelectedDrive(target)
        elif mode_str == "folder" and target:
            scan_controller.setFolderRoot(target)
        # 连接状态变化信号以回写工作区
        scan_controller.scanStateChanged.connect(  # pyrefly: ignore [missing-attribute]
            lambda ws_id=ws_id: self._sync_workspace_state(ws_id)
        )
        scan_controller.progressChanged.connect(  # pyrefly: ignore [missing-attribute]
            lambda ws_id=ws_id: self._sync_workspace_state(ws_id)
        )
        scan_controller.statusChanged.connect(  # pyrefly: ignore [missing-attribute]
            lambda ws_id=ws_id: self._sync_workspace_state(ws_id)
        )
        self._scan_controllers[ws_id] = scan_controller

        self.workspaceListChanged.emit()  # pyrefly: ignore [missing-attribute]
        return ws_id

    @Slot(str)  # pyrefly: ignore [not-callable]
    def removeWorkspace(self, ws_id: str) -> None:
        """按 ID 移除工作区（同时清理对应 ScanController）。"""
        if not self._model.remove_workspace(ws_id):
            return
        controller = self._scan_controllers.pop(ws_id, None)
        if controller is not None:
            controller.cleanup()
            controller.deleteLater()
        if self._current_workspace_id == ws_id:
            self._current_workspace_id = ""
            self.currentWorkspaceChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.workspaceListChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot(str)  # pyrefly: ignore [not-callable]
    def startScan(self, ws_id: str) -> None:
        """启动指定工作区的扫描。"""
        controller = self._scan_controllers.get(ws_id)
        if controller is None:
            logger.warning("工作区 %s 不存在", ws_id)
            return
        controller.startScan()

    @Slot(str)  # pyrefly: ignore [not-callable]
    def togglePause(self, ws_id: str) -> None:
        """暂停/继续指定工作区的扫描。"""
        controller = self._scan_controllers.get(ws_id)
        if controller is None:
            return
        controller.togglePause()

    @Slot(str)  # pyrefly: ignore [not-callable]
    def cancelScan(self, ws_id: str) -> None:
        """取消指定工作区的扫描。"""
        controller = self._scan_controllers.get(ws_id)
        if controller is None:
            return
        controller.cancelScan()

    @Slot(str, str, str)  # pyrefly: ignore [not-callable]
    def exportResults(self, ws_id: str, fmt: str, path_str: str) -> None:
        """导出指定工作区的扫描结果。

        :param ws_id: 工作区 ID
        :param fmt: ``"csv"`` 或 ``"json"``
        :param path_str: 导出文件绝对路径（由 QML FileDialog 选定）
        """
        controller = self._scan_controllers.get(ws_id)
        if controller is None:
            return
        controller.exportResults(fmt, path_str)

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def workspaceExists(self, ws_id: str) -> bool:
        """检查工作区 ID 是否存在。"""
        return self._model.get_workspace(ws_id) is not None

    # ----------------------------- 内部方法 -----------------------------

    def _sync_workspace_state(self, ws_id: str) -> None:
        """从 ScanController 同步状态到 WorkspaceListModel。

        在 ScanController 的 scanStateChanged/progressChanged/statusChanged
        信号触发时调用，将状态/计数/摘要写回对应 WorkspaceItem。
        """
        controller = self._scan_controllers.get(ws_id)
        if controller is None:
            return
        # 跳过 fallback controller（未关联工作区）
        if ws_id == getattr(self, "_fallback_ws_id", ""):  # pragma: no cover
            return

        scan_state = controller.scanState
        if scan_state == "scanning":
            status_text = "扫描中" if not controller.isPaused else "已暂停"
        elif scan_state == "results":
            status_text = "已完成"
        else:
            status_text = controller.statusText or "就绪"

        self._model.update_workspace(
            ws_id,
            status_text=status_text,
            matched_count=controller.matchedCount,
            passed_count=controller.passedCount,
            skipped_count=controller.skippedCount,
            error_count=controller.errorCount,
            last_summary=controller.statusSummary,
        )

    def cleanup(self) -> None:
        """窗口关闭时清理所有 ScanController 资源。"""
        for controller in self._scan_controllers.values():
            controller.cleanup()
            controller.deleteLater()
        self._scan_controllers.clear()
        if hasattr(self, "_fallback_controller"):
            self._fallback_controller.cleanup()
            self._fallback_controller.deleteLater()
            del self._fallback_controller
        self._model.clear()
        # 清理当前工作区 ID，使状态与空模型一致
        if self._current_workspace_id:
            self._current_workspace_id = ""
            self.currentWorkspaceChanged.emit()  # pyrefly: ignore [missing-attribute]
