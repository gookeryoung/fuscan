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
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]

from fuscan import config as config_module
from fuscan.gui.controllers._persistence import (
    PERSIST_FILENAME,
    TASK_OVERRIDE_KEYS,
    clamp_task_override_int,
    coerce_int,
    coerce_str,
    coerce_str_tuple,
    deserialize_task_overrides,
    load_persisted_workspaces,
    save_persisted_workspaces,
    serialize_workspaces,
)
from fuscan.gui.controllers.scan_controller import ScanController
from fuscan.gui.models.workspace_model import (
    ACTIVE_STATUS_TEXTS,
    STR_STATUS_DONE,
    STR_STATUS_PAUSED,
    STR_STATUS_READY,
    STR_STATUS_SCANNING,
    WorkspaceItem,
    WorkspaceListModel,
)
from fuscan.gui.scan_mode import SCAN_MODE_DEFAULT_INDEX, SCAN_MODE_STR_TO_INDEX

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fuscan.gui.controllers.config_controller import ConfigController
    from fuscan.gui.controllers.rules_controller import RulesController
    from fuscan.rules.model import RuleSet

__all__ = ["WorkspaceController"]

logger = logging.getLogger(__name__)


def _load_workspace_ruleset(rules_paths: Sequence[str], use_builtin: bool) -> RuleSet | None:
    """根据工作区的规则路径与内置规则开关加载 :class:`RuleSet`。

    :param rules_paths: 工作区专属规则文件路径列表
    :param use_builtin: 是否启用内置规则
    :return: 合并后的 :class:`RuleSet`；规则加载失败返回 ``None``
    """
    from fuscan.config import load_with_builtin
    from fuscan.rules import RuleError, load_ruleset, merge_multiple_rulesets

    paths = [Path(p) for p in rules_paths if Path(p).exists()]
    try:
        if use_builtin:
            return load_with_builtin(paths)
        if paths:
            rulesets = [load_ruleset(p) for p in paths]
            return merge_multiple_rulesets(*rulesets)
        return None
    except RuleError as exc:
        logger.warning("工作区规则集加载失败: %s", exc)
        return None


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
    # 当前扫描中（含暂停态）工作区变化：HomePage 据此切换扫描进度面板
    activeScanChanged = Signal()

    def __init__(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_controller = config_controller
        self._rules_controller = rules_controller
        # 反向注入引用：RulesController 绑定工作区时需要查询 WorkspaceItem
        self._rules_controller.set_workspace_controller(self)
        self._model = WorkspaceListModel(self)
        self._scan_controllers: dict[str, ScanController] = {}
        self._current_workspace_id: str = ""
        # iter-128：延迟加载——已恢复结果的工作区集合 + 正在恢复中的工作区集合
        self._restored_workspaces: set[str] = set()
        self._restoring_workspaces: set[str] = set()
        self._restore_workers: dict[str, object] = {}  # ws_id → ResultRestoreWorker
        # 当前扫描中（含暂停态）工作区 ID；空串表示无扫描任务进行
        self._active_scan_workspace_id: str = ""
        # iter-115：扫描历史归档存储，扫描结束时自动归档
        from fuscan.history import HistoryStore

        self._history_store: HistoryStore = HistoryStore()
        # 恢复持久化的工作区
        self._load_persisted()

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
        """切换当前工作区 ID。

        iter-128：切换时触发延迟加载——若该工作区有缓存结果且尚未恢复，
        在后台启动 ResultRestoreWorker 异步加载，避免启动时全量阻塞。
        """
        if ws_id != self._current_workspace_id:
            self._current_workspace_id = ws_id
            self.currentWorkspaceChanged.emit()  # pyrefly: ignore [missing-attribute]
            # 延迟加载：切换到的工作区若有缓存且未恢复，后台异步加载
            self._try_load_cached_results(ws_id)

    @Property(ScanController, notify=currentWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def currentScanController(self) -> ScanController:
        """当前工作区对应的 :class:`ScanController` 实例。

        未选中工作区时返回一个默认实例（避免 QML 绑定 null 报错）。
        """
        if self._current_workspace_id and self._current_workspace_id in self._scan_controllers:
            return self._scan_controllers[self._current_workspace_id]
        # 兜底：返回一个临时实例（仅当未选中工作区时）
        # iter-107：fallback 实例使用全局 RulesController 的全局 ruleset 作为占位
        # 该实例不会启动扫描（hasCurrentWorkspace=False 时 QML 不显示扫描入口）
        if not hasattr(self, "_fallback_controller"):
            self._fallback_controller = ScanController(self._config_controller, self._rules_controller, self)
            global_paths_str = [str(p) for p in self._rules_controller.rules_paths]
            global_ruleset = _load_workspace_ruleset(
                global_paths_str,
                self._rules_controller.use_builtin,
            )
            self._fallback_controller.setWorkspaceRuleset(
                global_ruleset,
                global_paths_str,
                self._rules_controller.use_builtin,
            )
        return self._fallback_controller

    @Property(bool, notify=currentWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def hasCurrentWorkspace(self) -> bool:
        """是否有当前选中工作区。"""
        return bool(self._current_workspace_id) and self._current_workspace_id in self._scan_controllers

    @Property(str, notify=activeScanChanged)  # pyrefly: ignore [not-callable]
    def activeScanWorkspaceId(self) -> str:
        """当前扫描中（含暂停态）工作区 ID；空串表示无扫描任务进行。

        HomePage 据此决定显示扫描进度面板还是工作区列表：扫描进行/暂停期间
        隐藏其他工作区，扫描结束（完成/取消/失败）后清空，恢复显示所有工作区。
        """
        return self._active_scan_workspace_id

    @Property(bool, notify=activeScanChanged)  # pyrefly: ignore [not-callable]
    def hasActiveScan(self) -> bool:
        """是否存在扫描中（含暂停态）的工作区。"""
        return bool(self._active_scan_workspace_id) and self._active_scan_workspace_id in self._scan_controllers

    @Property(ScanController, notify=activeScanChanged)  # pyrefly: ignore [not-callable]
    def activeScanController(self) -> ScanController:
        """当前扫描中工作区的 :class:`ScanController` 实例。

        无扫描任务时返回 :attr:`currentScanController` 的兜底实例，
        避免 QML 绑定 null 报错（与 :attr:`currentScanController` 同策略）。
        """
        if self._active_scan_workspace_id and self._active_scan_workspace_id in self._scan_controllers:
            return self._scan_controllers[self._active_scan_workspace_id]
        # 兜底：复用 currentScanController 的 fallback 实例（已注入全局 ruleset）
        return self.currentScanController

    @Property(str, notify=activeScanChanged)  # pyrefly: ignore [not-callable]
    def activeScanWorkspaceName(self) -> str:
        """当前扫描中工作区名称（供 ScanProgressCard 展示）。"""
        item = self._model.get_workspace(self._active_scan_workspace_id)
        return item.name if item is not None else ""

    @Property(str, notify=activeScanChanged)  # pyrefly: ignore [not-callable]
    def activeScanModeText(self) -> str:
        """当前扫描中工作区的扫描模式文本。"""
        item = self._model.get_workspace(self._active_scan_workspace_id)
        return item.mode_text if item is not None else ""

    @Property(str, notify=activeScanChanged)  # pyrefly: ignore [not-callable]
    def activeScanTarget(self) -> str:
        """当前扫描中工作区的目标路径。"""
        item = self._model.get_workspace(self._active_scan_workspace_id)
        return item.target if item is not None else ""

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

        self._create_workspace(ws_id, name, mode_str, target, rules_paths, use_builtin)
        self._persist()
        self.workspaceListChanged.emit()  # pyrefly: ignore [missing-attribute]
        return ws_id

    def _create_workspace(
        self,
        ws_id: str,
        name: str,
        mode_str: str,
        target: str,
        rules_paths: Sequence[str],
        use_builtin: bool,
        status_text: str = STR_STATUS_READY,
        matched_count: int = 0,
        passed_count: int = 0,
        skipped_count: int = 0,
        error_count: int = 0,
        last_summary: str = "",
        collected_count: int = 0,
        task_overrides: dict[str, object] | None = None,
    ) -> None:
        """创建工作区内部实现（构造 item + ScanController + 连接信号）。

        供 :meth:`addWorkspace`（新建）与 :meth:`_load_persisted`（恢复）共用。
        持久化恢复时传入 ``status_text``/计数字段，使重启后仍能展示上次扫描状态。
        """
        item = WorkspaceItem(
            workspace_id=ws_id,
            name=name,
            mode_str=mode_str,
            target=target,
            rules_paths=tuple(rules_paths),
            use_builtin=use_builtin,
            status_text=status_text,
            matched_count=matched_count,
            passed_count=passed_count,
            skipped_count=skipped_count,
            error_count=error_count,
            last_summary=last_summary,
            collected_count=collected_count,
            task_overrides=dict(task_overrides) if task_overrides else {},
        )
        self._model.add_workspace(item)

        # iter-127：ScanController 创建/初始化失败时回滚 model.add_workspace，
        # 避免残留无 ScanController 的无效工作区（_load_persisted 单条容错）
        try:
            # 为该工作区构造独立的 ScanController
            scan_controller = ScanController(self._config_controller, self._rules_controller, self)
            # 按工作区参数初始化 ScanController
            mode_index = SCAN_MODE_STR_TO_INDEX.get(mode_str, SCAN_MODE_DEFAULT_INDEX)
            scan_controller.setScanModeIndex(mode_index)
            if mode_str == "drive" and target:
                scan_controller.setSelectedDrive(target)
            elif mode_str == "folder" and target:
                scan_controller.setFolderRoot(target)
            # 同步任务级配置覆盖到 ScanController（iter-104）
            for key, value in item.task_overrides.items():
                scan_controller.setTaskOverride(key, value)
            # 注入工作区专属 ruleset（iter-107 规则与工作区绑定）
            # ScanController 不再依赖全局 RulesController.ruleset，而是持工作区专属副本
            workspace_ruleset = _load_workspace_ruleset(item.rules_paths, item.use_builtin)
            scan_controller.setWorkspaceRuleset(workspace_ruleset, tuple(item.rules_paths), item.use_builtin)
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
        except Exception:
            # 回滚已添加到 model 的工作区，避免残留无效项
            self._model.remove_workspace(ws_id)
            raise
        self._scan_controllers[ws_id] = scan_controller

    @Slot(str)  # pyrefly: ignore [not-callable]
    def removeWorkspace(self, ws_id: str) -> None:
        """按 ID 移除工作区（同时清理对应 ScanController 与扫描历史）。"""
        if not self._model.remove_workspace(ws_id):
            return
        controller = self._scan_controllers.pop(ws_id, None)
        if controller is not None:
            controller.cleanup()
            controller.deleteLater()
        if self._current_workspace_id == ws_id:
            self._current_workspace_id = ""
            self.currentWorkspaceChanged.emit()  # pyrefly: ignore [missing-attribute]
        # 若删除的是当前扫描中的工作区，清空 active 状态
        if self._active_scan_workspace_id == ws_id:
            self._active_scan_workspace_id = ""
            self.activeScanChanged.emit()  # pyrefly: ignore [missing-attribute]
        # iter-115：清理该工作区的扫描历史
        self._history_store.clear_workspace(ws_id)
        # iter-123：清理该工作区的缓存扫描结果
        self._delete_cached_results(ws_id)
        self._persist()
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
    def startIncrementalScan(self, ws_id: str) -> None:
        """启动指定工作区的增量扫描（iter-124）。

        委托给对应工作区的 :class:`ScanController`，加载上次 manifest 与
        ScanReport 后启用增量模式。无上次结果时回退到全量扫描。
        """
        controller = self._scan_controllers.get(ws_id)
        if controller is None:
            logger.warning("工作区 %s 不存在", ws_id)
            return
        controller.startIncrementalScan(ws_id)

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

    @Slot(str, str, str)  # pyrefly: ignore [not-callable]
    def updateWorkspaceTarget(self, ws_id: str, mode_str: str, target: str) -> None:
        """更新工作区扫描目标（iter-104 任务切换扫描目标）。

        :param ws_id: 工作区 ID
        :param mode_str: 新的扫描模式（``"full"``/``"drive"``/``"folder"``）
        :param target: 新的目标（盘符或文件夹路径，全盘模式忽略）

        更新 :class:`WorkspaceItem` 的 mode_str/target 字段并同步到对应
        :class:`ScanController`；仅当工作区处于 ``就绪``/``已完成`` 状态时
        允许修改，扫描中/暂停中拒绝修改以避免破坏运行时状态。
        """
        item = self._model.get_workspace(ws_id)
        if item is None:
            logger.warning("工作区 %s 不存在，无法更新目标", ws_id)
            return
        # 扫描中/暂停中拒绝修改
        if item.status_text in ACTIVE_STATUS_TEXTS:
            logger.warning("工作区 %s 处于 %s 状态，拒绝修改目标", ws_id, item.status_text)
            return
        # 规范化参数
        if mode_str not in SCAN_MODE_STR_TO_INDEX:
            logger.warning("无效的扫描模式: %s", mode_str)
            return
        # 全盘模式 target 强制为空
        if mode_str == "full":
            target = ""
        # 同步到 model
        self._model.update_workspace(ws_id, mode_str=mode_str, target=target)
        # 同步到 ScanController
        controller = self._scan_controllers.get(ws_id)
        if controller is not None:
            mode_index = SCAN_MODE_STR_TO_INDEX[mode_str]
            controller.setScanModeIndex(mode_index)
            if mode_str == "drive" and target:
                controller.setSelectedDrive(target)
            elif mode_str == "folder" and target:
                controller.setFolderRoot(target)
        self._persist()
        self.workspaceListChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot(str, list, bool)  # pyrefly: ignore [not-callable]
    def updateWorkspaceRules(self, ws_id: str, rules_paths: list[str], use_builtin: bool) -> None:
        """更新工作区规则配置（iter-107 规则与工作区绑定）。

        :param ws_id: 工作区 ID
        :param rules_paths: 新的规则文件路径列表
        :param use_builtin: 是否启用内置规则

        由 :class:`RulesController` 在绑定模式下编辑规则后调用。更新
        :class:`WorkspaceItem` 的 rules_paths/use_builtin 字段，重新加载工作区
        专属 :class:`RuleSet` 并推送给对应 :class:`ScanController`，最后持久化。
        扫描中/暂停中拒绝修改以避免破坏运行时状态。
        """
        item = self._model.get_workspace(ws_id)
        if item is None:
            logger.warning("工作区 %s 不存在，无法更新规则", ws_id)
            return
        if item.status_text in ACTIVE_STATUS_TEXTS:
            logger.warning("工作区 %s 处于 %s 状态，拒绝修改规则", ws_id, item.status_text)
            return
        # 规范化为 tuple[str, ...]
        normalized_paths = tuple(str(p) for p in rules_paths)
        # 同步到 model（触发 rulesText/rulesTags role 刷新）
        self._model.update_workspace(
            ws_id,
            rules_paths=normalized_paths,
            use_builtin=bool(use_builtin),
        )
        # 重新加载工作区专属 ruleset 并注入 ScanController
        workspace_ruleset = _load_workspace_ruleset(normalized_paths, use_builtin)
        controller = self._scan_controllers.get(ws_id)
        if controller is not None:
            controller.setWorkspaceRuleset(workspace_ruleset, normalized_paths, bool(use_builtin))
        self._persist()
        self.workspaceListChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def bindRulesController(self, ws_id: str) -> bool:
        """将全局 :class:`RulesController` 绑定到指定工作区（iter-107）。

        :param ws_id: 工作区 ID
        :return: 是否绑定成功

        HomePage 在「定义规则」按钮触发时调用，使 RulesController 进入工作区绑定模式，
        后续 RulesPage 编辑仅作用于该工作区，不影响全局配置与其他工作区。
        """
        if not ws_id:
            return False
        return self._rules_controller.bindWorkspace(ws_id)

    @Slot()  # pyrefly: ignore [not-callable]
    def unbindRulesController(self) -> None:
        """解除 :class:`RulesController` 的工作区绑定（iter-107）。

        RulesPage 返回时调用，恢复 RulesController 全局模式。
        """
        self._rules_controller.unbindWorkspace()

    def get_workspace(self, ws_id: str) -> WorkspaceItem | None:
        """按 ID 取 :class:`WorkspaceItem`（供 RulesController 查询绑定工作区）。

        :param ws_id: 工作区 ID
        :return: 工作区数据；不存在返回 ``None``
        """
        return self._model.get_workspace(ws_id)

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def taskOverridesJson(self, ws_id: str) -> str:
        """返回指定工作区的任务级配置覆盖 JSON 字符串（iter-104）。

        :param ws_id: 工作区 ID
        :return: JSON 字符串（如 ``{"scan_archives": false, "max_workers": 8}``）；
            工作区不存在或序列化失败返回 ``"{}"``
        """
        item = self._model.get_workspace(ws_id)
        if item is None:
            return "{}"
        try:
            return json.dumps(item.task_overrides, ensure_ascii=False)
        except (TypeError, ValueError):
            # iter-105：容错防御，避免非 JSON 可序列化对象冒泡到 QML
            logger.warning("工作区 %s 的 task_overrides 序列化失败", ws_id, exc_info=True)
            return "{}"

    @Slot(str, str, str)  # pyrefly: ignore [not-callable]
    def setTaskOverride(self, ws_id: str, key: str, value_json: str) -> None:
        """设置任务级配置覆盖（iter-104）。

        :param ws_id: 工作区 ID
        :param key: Config 字段名（如 ``"scan_archives"``/``"max_workers"``）
        :param value_json: 值的 JSON 字符串（如 ``"false"``/``"8"``/``"["a","b"]"``）

        支持 5 个字段：``scan_archives``/``max_workers``/``max_file_size``/
        ``max_depth``/``ignore_dirs``。其他字段忽略。
        int 字段会做范围钳制（与全局 :class:`ConfigController` 一致），
        越界值拒绝并 warning。修改后持久化并同步到对应 ScanController。
        """
        item = self._model.get_workspace(ws_id)
        if item is None:
            logger.warning("工作区 %s 不存在，无法设置任务级配置", ws_id)
            return
        # 白名单校验（iter-105：统一用 TASK_OVERRIDE_KEYS，避免重复定义）
        if key not in TASK_OVERRIDE_KEYS:
            logger.warning("不允许覆盖字段: %s", key)
            return
        try:
            value = json.loads(value_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("任务级配置值 JSON 解析失败: %s", value_json)
            return
        # 类型校验
        expected_type = TASK_OVERRIDE_KEYS[key]
        if key == "ignore_dirs":
            # JSON 反序列化为 list，校验后转 tuple
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                logger.warning("ignore_dirs 应为 list[str]")
                return
            value = tuple(value)
        elif not isinstance(value, expected_type):
            logger.warning("%s 应为 %s，得到 %s", key, expected_type.__name__, type(value).__name__)
            return
        # iter-105：int 字段范围钳制（max_workers 1-16，max_file_size 1-500MB）
        if isinstance(value, int) and not isinstance(value, bool):
            clamped = clamp_task_override_int(key, value)
            if clamped is None:
                logger.warning("%s=%s 越界，拒绝任务级覆盖", key, value)
                return
            value = clamped
        # 更新 WorkspaceItem.task_overrides（replace 重建 frozen dataclass）
        new_overrides = dict(item.task_overrides)
        new_overrides[key] = value
        self._model.update_workspace(ws_id, task_overrides=new_overrides)
        # 同步到 ScanController
        controller = self._scan_controllers.get(ws_id)
        if controller is not None:
            controller.setTaskOverride(key, value)
        self._persist()

    @Slot(str, str)  # pyrefly: ignore [not-callable]
    def clearTaskOverride(self, ws_id: str, key: str) -> None:
        """清除任务级配置覆盖的指定字段（iter-127）。

        用于"留空使用全局"语义：当任务级配置值与全局值相同时，
        删除该字段的覆盖，使后续全局配置变更能自动生效。

        :param ws_id: 工作区 ID
        :param key: Config 字段名（如 ``"scan_archives"``/``"max_workers"``）
        """
        item = self._model.get_workspace(ws_id)
        if item is None:
            logger.warning("工作区 %s 不存在，无法清除任务级配置", ws_id)
            return
        if key not in TASK_OVERRIDE_KEYS:
            logger.warning("不允许清除字段: %s", key)
            return
        if key not in item.task_overrides:
            return  # 无覆盖，无需清除
        new_overrides = dict(item.task_overrides)
        new_overrides.pop(key, None)
        self._model.update_workspace(ws_id, task_overrides=new_overrides)
        # 同步到 ScanController：用 ConfigController 全局值回填
        controller = self._scan_controllers.get(ws_id)
        if controller is not None:
            global_value = self._config_controller.get_config_value(key)
            if global_value is not None:
                controller.setTaskOverride(key, global_value)
        self._persist()

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def workspaceExists(self, ws_id: str) -> bool:
        """检查工作区 ID 是否存在。"""
        return self._model.get_workspace(ws_id) is not None

    @Slot(result=bool)  # pyrefly: ignore [not-callable]
    def clearAllWorkspaces(self) -> bool:
        """清空所有工作区（iter-108 快速移除全部任务）。

        :return: 是否成功清空。扫描中/暂停中拒绝清空返回 False，
            避免破坏运行时 worker 状态；空列表视为已成功无需操作返回 True。

        清理所有 :class:`ScanController` 资源、清空 model、重置当前/活动工作区 ID，
        并持久化空列表到 ``workspaces.json``。绑定中的 :class:`RulesController`
        若指向被清空的工作区，自动解绑恢复全局模式。
        """
        # 扫描中/暂停中拒绝清空
        if self._active_scan_workspace_id:
            logger.warning(
                "工作区 %s 正在扫描，拒绝清空",
                self._active_scan_workspace_id,
            )
            return False
        if self._model.rowCount() == 0:
            # 空列表：仍清理可能残留的状态，并视为成功
            if self._current_workspace_id:
                self._current_workspace_id = ""
                self.currentWorkspaceChanged.emit()  # pyrefly: ignore [missing-attribute]
            return True
        # 清理所有 ScanController
        for controller in self._scan_controllers.values():
            controller.cleanup()
            controller.deleteLater()
        self._scan_controllers.clear()
        # iter-123：清理所有工作区的缓存扫描结果
        for ws_item in list(self._model.all_workspaces()):
            self._delete_cached_results(ws_item.workspace_id)
        self._model.clear()
        # 重置当前/活动工作区 ID
        if self._current_workspace_id:
            self._current_workspace_id = ""
            self.currentWorkspaceChanged.emit()  # pyrefly: ignore [missing-attribute]
        # active_scan_workspace_id 已在入口校验非空，此处清空兜底
        if self._active_scan_workspace_id:
            self._active_scan_workspace_id = ""
            self.activeScanChanged.emit()  # pyrefly: ignore [missing-attribute]
        # 若 RulesController 绑定到已清空的工作区，自动解绑
        if self._rules_controller.isBound:
            self._rules_controller.unbindWorkspace()
        # 持久化空列表
        self._persist()
        self.workspaceListChanged.emit()  # pyrefly: ignore [missing-attribute]
        return True

    # ----------------------------- 内部方法 -----------------------------

    def _sync_workspace_state(self, ws_id: str) -> None:
        """从 ScanController 同步状态到 WorkspaceListModel。

        在 ScanController 的 scanStateChanged/progressChanged/statusChanged
        信号触发时调用，将状态/计数/摘要写回对应 WorkspaceItem。
        同时维护 :attr:`_active_scan_workspace_id`：扫描中（含暂停态）的工作区
        被标记为 active，扫描结束（完成/取消/失败/就绪）后清空，触发
        :signal:`activeScanChanged` 通知 HomePage 切换视图。
        """
        controller = self._scan_controllers.get(ws_id)
        if controller is None:
            return

        scan_state = controller.scanState
        # scanning 态包含暂停（isPaused=True），仍视为 active
        is_active = scan_state == "scanning"
        if scan_state == "scanning":
            status_text = STR_STATUS_SCANNING if not controller.isPaused else STR_STATUS_PAUSED
        elif scan_state == "results":
            # 直接使用 ScanController.statusText：正常完成=STR_STATUS_DONE，取消=STR_STATUS_CANCELLED
            status_text = controller.statusText or STR_STATUS_DONE
        else:
            status_text = controller.statusText or STR_STATUS_READY

        self._model.update_workspace(
            ws_id,
            status_text=status_text,
            matched_count=controller.matchedCount,
            passed_count=controller.passedCount,
            skipped_count=controller.skippedCount,
            error_count=controller.errorCount,
            last_summary=controller.statusSummary,
            collected_count=controller.walkClassified,
        )

        # 同步 active scan 工作区 ID
        if is_active:
            if self._active_scan_workspace_id != ws_id:
                self._active_scan_workspace_id = ws_id
                self.activeScanChanged.emit()  # pyrefly: ignore [missing-attribute]
        elif self._active_scan_workspace_id == ws_id:
            self._active_scan_workspace_id = ""
            self.activeScanChanged.emit()  # pyrefly: ignore [missing-attribute]
            # 扫描结束（scanning → 非 scanning）：持久化状态，重启后仍能展示
            self._persist()
            # iter-115：扫描结束自动归档到历史存储
            self._archive_scan_history(ws_id, controller)
            # iter-123：扫描结果持久化到 ~/.fuscan/results/<ws_id>.json，
            # 重启后通过 restoreFromReport 恢复，避免用户被迫重新扫描
            self._save_cached_results(ws_id, controller)

    def cleanup(self) -> None:
        """窗口关闭时快速取消所有 ScanController 的 worker。

        iter-127：改用 ``quick_cancel()`` 替代 ``cleanup()``——仅设置 cancel 标志，
        不 ``wait()`` / ``cache.close()`` / ``deleteLater()``，进程退出时由 OS
        回收线程与文件句柄。10 万结果场景下避免主线程阻塞（原 ``cleanup()``
        每 controller 最多 5s 累计等待 + SQLite 刷盘）。

        iter-124：关闭时不再 emit ``currentWorkspaceChanged``/``activeScanChanged``
        信号，避免 QML 在组件销毁过程中重新求值 ``currentScanController`` binding
        访问到已被 ``deleteLater`` 的对象（Terminal#4-17 null 错误根因）。
        """
        for controller in self._scan_controllers.values():
            controller.quick_cancel()
        self._scan_controllers.clear()
        # fallback 仅快速取消，不 wait/close/deleteLater（进程退出由 OS 回收）
        if hasattr(self, "_fallback_controller"):
            self._fallback_controller.quick_cancel()
        # 不清空 _current_workspace_id / _active_scan_workspace_id，不 emit 信号：
        # 应用退出阶段 QML 组件正在销毁，重新求值 binding 会触发 null TypeError。
        # 状态清空无意义（进程即将退出），保留原值让 QML binding 求值稳定。

    # ----------------------------- 持久化 -----------------------------

    @property
    def _persist_file(self) -> Path:
        """持久化文件路径（运行时计算，跟随 ``CONFIG_DIR`` monkeypatch）。"""
        return config_module.CONFIG_DIR / PERSIST_FILENAME

    def _persist(self) -> None:
        """将所有工作区序列化到 ``~/.fuscan/workspaces.json``。

        持久化「定义字段」与「上次扫描状态」（status_text/counts/summary），
        使重启后仍能展示上次扫描结果状态；ScanController 的运行时态
        （scanState/worker/result_model）不持久化，重启后重置为 setup。
        """
        payload = serialize_workspaces(self._model.all_workspaces())
        save_persisted_workspaces(self._persist_file, payload, config_module.CONFIG_DIR)

    def _load_persisted(self) -> None:
        """从 ``~/.fuscan/workspaces.json`` 恢复工作区列表。

        文件不存在/解析失败时静默跳过（首次启动或文件损坏）。
        """
        workspaces = load_persisted_workspaces(self._persist_file)
        for ws in workspaces:
            # iter-113：dict 反序列化返回 object，通过 coerce_* 辅助函数做类型守卫
            ws_id = coerce_str(ws.get("id", ""))
            if not ws_id or self._model.get_workspace(ws_id) is not None:
                continue
            try:
                self._create_workspace(
                    ws_id=ws_id,
                    name=coerce_str(ws.get("name", "任务")),
                    mode_str=coerce_str(ws.get("mode", "folder")),
                    target=coerce_str(ws.get("target", "")),
                    rules_paths=coerce_str_tuple(ws.get("rules_paths", [])),
                    use_builtin=bool(ws.get("use_builtin", True)),
                    # 恢复上次扫描状态（iter-102 起持久化）
                    status_text=coerce_str(ws.get("status_text", STR_STATUS_READY)),
                    matched_count=coerce_int(ws.get("matched_count", 0)),
                    passed_count=coerce_int(ws.get("passed_count", 0)),
                    skipped_count=coerce_int(ws.get("skipped_count", 0)),
                    error_count=coerce_int(ws.get("error_count", 0)),
                    last_summary=coerce_str(ws.get("last_summary", "")),
                    # 恢复收集到的符合文件类型文件数（iter-105 起持久化）
                    collected_count=coerce_int(ws.get("collected_count", 0)),
                    # 恢复任务级配置覆盖（iter-104 起持久化）
                    task_overrides=deserialize_task_overrides(ws.get("task_overrides", {})),
                )
                # iter-128：不再在启动时同步加载所有工作区的缓存结果，
                # 改为延迟加载——setCurrentWorkspaceId 时按需后台异步恢复。
                # 工作区列表的 status_text/matched_count 等已从 workspaces.json 恢复，
                # 用户看到正确的状态摘要，完整结果在切换到该工作区时才加载。
            except Exception as exc:  # 持久化恢复容错：单条失败不阻塞其余
                logger.warning("工作区 %s 恢复失败: %s", ws_id, exc)
        if self._model.rowCount() > 0:
            self.workspaceListChanged.emit()  # pyrefly: ignore [missing-attribute]
            # iter-128：启动时仅后台加载第一个工作区的结果（QML 默认选中第一个）
            first_ws_id = self._model.all_workspaces()[0].workspace_id
            self._try_load_cached_results(first_ws_id)

    # ----------------------------- 扫描结果缓存（iter-123） -----------------------------

    @property
    def _cached_results_dir(self) -> Path:
        """扫描结果缓存目录：``~/.fuscan/results/``。"""
        return config_module.CONFIG_DIR / "results"

    def _cached_results_path(self, ws_id: str) -> Path:
        """指定工作区的缓存结果文件路径。"""
        return self._cached_results_dir / f"{ws_id}.json"

    def _save_cached_results(self, ws_id: str, controller: ScanController) -> None:
        """将 ScanController 的 ``_last_report`` 持久化到 ``~/.fuscan/results/<ws_id>.json``。

        扫描结束（含取消）后调用，重启后通过 :meth:`_load_cached_results` 恢复。
        持久化失败仅记录日志，不影响主流程。

        iter-127：改用 ``to_json_bytes()`` + ``write_bytes()``，orjson 直接输出
        UTF-8 bytes，跳过 ``.decode()`` + ``.encode()`` 往返，10 万命中结果
        序列化速度提升 5-10x。
        """
        report = controller._last_report  # 同包私有访问
        if report is None:
            return
        try:
            self._cached_results_dir.mkdir(parents=True, exist_ok=True)
            self._cached_results_path(ws_id).write_bytes(report.to_json_bytes())
            logger.debug("工作区 %s 扫描结果已缓存（%d 条命中）", ws_id, len(report.hits))
        except (OSError, ValueError) as exc:
            logger.warning("工作区 %s 扫描结果缓存失败: %s", ws_id, exc)

    def _try_load_cached_results(self, ws_id: str) -> None:
        """后台异步加载工作区缓存结果（iter-128）。

        若该工作区已恢复或正在恢复中则跳过（幂等）。否则启动
        :class:`ResultRestoreWorker` 在后台线程读取 + 反序列化，
        完成后通过 ``_on_restore_done`` 信号回到主线程恢复结果。

        启动时对第一个工作区调用、``setCurrentWorkspaceId`` 时对目标工作区调用。
        """
        if not ws_id or ws_id in self._restored_workspaces or ws_id in self._restoring_workspaces:
            return
        cache_file = self._cached_results_path(ws_id)
        if not cache_file.exists():
            return
        controller = self._scan_controllers.get(ws_id)
        if controller is None:
            return
        # 标记恢复中，启动后台线程
        self._restoring_workspaces.add(ws_id)
        controller._set_restoring(True)
        from fuscan.workers.restore_worker import ResultRestoreWorker

        worker = ResultRestoreWorker(ws_id, cache_file)
        worker.restore_done.connect(self._on_restore_done)  # pyrefly: ignore [missing-attribute]
        worker.restore_failed.connect(self._on_restore_failed)  # pyrefly: ignore [missing-attribute]
        worker.finished.connect(lambda wid=ws_id: self._cleanup_restore_worker(wid))
        self._restore_workers[ws_id] = worker
        worker.start()

    def _on_restore_done(self, ws_id: str, report: object) -> None:
        """后台恢复完成：在主线程恢复结果到 ScanController。"""
        from fuscan.scanner import ScanReport

        controller = self._scan_controllers.get(ws_id)
        if controller is not None and isinstance(report, ScanReport):
            controller.restoreFromReport(report)
            controller._set_restoring(False)
            logger.debug("工作区 %s 扫描结果后台恢复完成（%d 条命中）", ws_id, len(report.hits))
        self._restoring_workspaces.discard(ws_id)
        self._restored_workspaces.add(ws_id)

    def _on_restore_failed(self, ws_id: str, error_msg: str) -> None:
        """后台恢复失败：记录日志，清除恢复态。"""
        controller = self._scan_controllers.get(ws_id)
        if controller is not None:
            controller._set_restoring(False)
        self._restoring_workspaces.discard(ws_id)
        logger.warning("工作区 %s 扫描结果后台恢复失败: %s", ws_id, error_msg)

    def _cleanup_restore_worker(self, ws_id: str) -> None:
        """清理已完成的 ResultRestoreWorker（避免 QObject 泄漏）。"""
        worker = self._restore_workers.pop(ws_id, None)
        if worker is not None:
            worker.deleteLater()  # pyrefly: ignore [missing-attribute]

    def _delete_cached_results(self, ws_id: str) -> None:
        """删除指定工作区的缓存扫描结果文件。"""
        cache_file = self._cached_results_path(ws_id)
        try:
            if cache_file.exists():
                cache_file.unlink()
                logger.debug("工作区 %s 缓存结果已删除", ws_id)
        except OSError as exc:
            logger.warning("工作区 %s 缓存结果删除失败: %s", ws_id, exc)

    # ----------------------------- 扫描历史（iter-115） -----------------------------

    def _archive_scan_history(self, ws_id: str, controller: ScanController) -> None:
        """扫描结束时从 ScanController 提取报告并归档到 :class:`HistoryStore`。

        :param ws_id: 工作区 ID
        :param controller: 该工作区的 :class:`ScanController` 实例
        """
        ws_item = self._model.get_workspace(ws_id)
        if ws_item is None:
            return
        try:
            entry = controller.build_history_entry(ws_id, ws_item.name)
            if entry is not None:
                self._history_store.add(entry)
        except Exception as exc:  # 归档失败不影响主流程
            logger.warning("工作区 %s 扫描历史归档失败: %s", ws_id, exc)

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def workspaceHistoryJson(self, ws_id: str) -> str:
        """返回指定工作区的历史记录 JSON 字符串（供 QML 解析展示）。

        :param ws_id: 工作区 ID
        :return: JSON 数组字符串，每个元素为历史条目 dict（按时间倒序）；
            空历史返回 ``"[]"``
        """
        import json as _json

        entries = self._history_store.workspace_history(ws_id)
        payload = [
            {
                "scan_id": e.scan_id,
                "workspace_name": e.workspace_name,
                "started_at": e.started_at,
                "finished_at": e.finished_at,
                "status": e.status,
                "total_files": e.total_files,
                "scanned_files": e.scanned_files,
                "matched_files": e.matched_files,
                "skipped_files": e.skipped_files,
                "error_count": e.error_count,
                "duration_seconds": round(e.duration_seconds, 2),
                "rule_names": list(e.rule_names),
                "summary": e.summary,
            }
            for e in entries
        ]
        return _json.dumps(payload, ensure_ascii=False)

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def compareWithPreviousScan(self, ws_id: str) -> str:
        """对比指定工作区最近一次扫描与上上次扫描，返回对比结果 JSON。

        :param ws_id: 工作区 ID
        :return: JSON 对象字符串，包含 ``current``/``previous``/``summary``/
            ``new_hits``/``resolved_hits``/``persistent_hits``/``matched_delta``/
            ``trend`` 字段；无历史返回 ``"{}"``
        """
        import json as _json

        from fuscan.history import compare_scans

        entries = self._history_store.workspace_history(ws_id, limit=2)
        if not entries:
            return "{}"
        current = entries[0]
        previous = entries[1] if len(entries) >= 2 else None
        comparison = compare_scans(current, previous)
        payload = {
            "current": {
                "scan_id": comparison.current.scan_id,
                "finished_at": comparison.current.finished_at,
                "matched_files": comparison.current.matched_files,
                "status": comparison.current.status,
            },
            "previous": (
                {
                    "scan_id": comparison.previous.scan_id,
                    "finished_at": comparison.previous.finished_at,
                    "matched_files": comparison.previous.matched_files,
                    "status": comparison.previous.status,
                }
                if comparison.previous is not None
                else None
            ),
            "summary": comparison.summary(),
            "trend": comparison.trend,
            "matched_delta": comparison.matched_delta,
            "new_hits_count": len(comparison.new_hits),
            "resolved_hits_count": len(comparison.resolved_hits),
            "persistent_hits_count": len(comparison.persistent_hits),
            "new_hits": list(comparison.new_hits[:50]),  # 限制返回数量避免过大
            "resolved_hits": list(comparison.resolved_hits[:50]),
            "new_rules": list(comparison.new_rules),
            "dropped_rules": list(comparison.dropped_rules),
        }
        return _json.dumps(payload, ensure_ascii=False)

    @Slot(str, result=int)  # pyrefly: ignore [not-callable]
    def clearWorkspaceHistory(self, ws_id: str) -> int:
        """清空指定工作区的扫描历史，返回被清除的条目数。"""
        return self._history_store.clear_workspace(ws_id)
