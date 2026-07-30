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
from fuscan.gui.controllers._history_view import (
    build_scan_comparison_json as _build_scan_comparison_json,
)
from fuscan.gui.controllers._history_view import (
    build_workspace_history_json as _build_workspace_history_json,
)
from fuscan.gui.controllers._persistence import (
    PERSIST_FILENAME,
    TASK_OVERRIDE_KEYS,
    clamp_task_override_int,
    coerce_float,
    coerce_int,
    coerce_str,
    coerce_str_tuple,
    deserialize_task_overrides,
    load_persisted_workspaces,
    save_persisted_workspaces,
    serialize_workspaces,
)
from fuscan.gui.controllers._restore import (
    delete_cached_results as _delete_cached_results_fn,
)
from fuscan.gui.controllers._restore import (
    save_cached_results as _save_cached_results_fn,
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
    from fuscan.gui.controllers.whitelist_controller import WhitelistController
    from fuscan.history import HistoryStore

__all__ = ["WorkspaceController"]

logger = logging.getLogger(__name__)


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
        whitelist_controller: WhitelistController | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_controller = config_controller
        self._rules_controller = rules_controller
        # iter-133：误报白名单控制器——共享实例注入到所有 ScanController，
        # 使白名单变更对所有工作区生效；为 None 时（独立测试）ScanController
        # 内部回退到自建 WhitelistStore，保持向后兼容。
        self._whitelist_controller: WhitelistController | None = whitelist_controller
        # iter-137：规则配置全局化——RulesController 不再绑定工作区，
        # 全局 rulesetChanged 时清除所有工作区 manifest，使下次增量扫描回退全量，
        # 确保新规则被实际执行（manifest 指纹只记录 mtime+size，不感知规则变化）
        self._rules_controller.rulesetChanged.connect(self._invalidate_all_manifests)  # pyrefly: ignore [missing-attribute]
        # iter-139：全局规则变化时同步刷新所有工作区的 rules_paths/use_builtin，
        # 使 WorkspaceCard 的 rules_tags 标签反映当前全局规则配置。
        # iter-137 规则全局化后，WorkspaceItem.rules_paths/use_builtin 字段不再
        # 决定扫描时使用的规则（ScanController 直接读全局 RulesController），
        # 但 rules_tags 派生属性仍依赖这些字段，需同步以保持 UI 一致。
        self._rules_controller.rulesetChanged.connect(self._sync_all_workspaces_rules)  # pyrefly: ignore [missing-attribute]
        self._rules_controller.rulesFileListChanged.connect(self._sync_all_workspaces_rules)  # pyrefly: ignore [missing-attribute]
        self._rules_controller.useBuiltinChanged.connect(self._sync_all_workspaces_rules)  # pyrefly: ignore [missing-attribute]
        self._model = WorkspaceListModel(self)
        self._scan_controllers: dict[str, ScanController] = {}
        self._current_workspace_id: str = ""
        # iter-128：延迟加载——已恢复结果的工作区集合 + 正在恢复中的工作区集合
        self._restored_workspaces: set[str] = set()
        self._restoring_workspaces: set[str] = set()
        self._restore_workers: dict[str, object] = {}  # ws_id → ResultRestoreWorker
        # 当前扫描中（含暂停态）工作区 ID；空串表示无扫描任务进行
        self._active_scan_workspace_id: str = ""
        # iter-133：全局共享 SkipStore——所有 ScanController 复用同一实例，
        # 避免每个工作区独立读 ~/.fuscan/skips.json 造成的 N 次重复 I/O。
        from fuscan.processing.skip_store import SkipStore

        self._shared_skip_store: SkipStore = SkipStore()
        # iter-133：HistoryStore 延迟初始化——首次访问时构造，
        # 避免启动时读 ~/.fuscan/history.json 阻塞主线程。
        self._history_store_instance: HistoryStore | None = None
        # iter-133：cleanup 标志——cleanup() 后置 True，hasCurrentWorkspace
        # 据此返回 False（进程退出阶段 QML 不应再访问 currentScanController）
        self._cleaned_up: bool = False
        # 恢复持久化的工作区
        self._load_persisted()

    @property
    def _history_store(self) -> HistoryStore:
        """延迟构造并返回 :class:`HistoryStore` 实例（兼容原属性访问）。

        首次访问时读取 ``~/.fuscan/history.json`` 构造实例并缓存，
        后续访问直接返回缓存实例。避免启动时同步读 history.json 阻塞主线程。

        .. note::
            内部存储用 :attr:`_history_store_instance`，本 property 提供
            ``self._history_store`` 透明访问，保持对历史代码与测试的兼容。
        """
        if self._history_store_instance is None:
            from fuscan.history import HistoryStore

            self._history_store_instance = HistoryStore()
        return self._history_store_instance

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

        iter-133：ScanController 延迟创建——首次访问时通过
        :meth:`_ensure_scan_controller` 构造，避免启动时为所有工作区预创建。
        """
        if self._current_workspace_id:
            controller = self._ensure_scan_controller(self._current_workspace_id)
            if controller is not None:
                return controller
        # 兜底：返回一个临时实例（仅当未选中工作区或工作区不存在时）
        # iter-137：fallback 实例直接复用全局 RulesController.ruleset（启动时占位），
        # 该实例不会启动扫描（hasCurrentWorkspace=False 时 QML 不显示扫描入口）
        if not hasattr(self, "_fallback_controller"):
            self._fallback_controller = ScanController(
                self._config_controller,
                self._rules_controller,
                self,
                skip_store=self._shared_skip_store,
                whitelist_controller=self._whitelist_controller,
            )
        return self._fallback_controller

    @Property(bool, notify=currentWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def hasCurrentWorkspace(self) -> bool:
        """是否有当前选中工作区。

        iter-133：基于 :class:`WorkspaceListModel` 判断工作区是否存在，
        不依赖 ScanController 是否已创建（延迟创建场景下未访问过的工作区
        也应视为「存在」，QML 据此显示工作区详情）。
        cleanup 后返回 False（进程退出阶段 QML 不应再访问工作区）。
        """
        if self._cleaned_up or not self._current_workspace_id:
            return False
        return self._model.get_workspace(self._current_workspace_id) is not None

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
        rules_paths_json: str,  # noqa: ARG002 废弃参数，保留以兼容 QML 调用签名
        use_builtin: bool,  # noqa: ARG002 废弃参数，保留以兼容 QML 调用签名
    ) -> str:
        """新建工作区。

        :param name: 工作区名称（空串时自动生成）
        :param mode_str: 扫描模式字符串（``"full"``/``"drive"``/``"folder"``）
        :param target: 扫描目标（盘符或文件夹路径，全盘模式忽略）
        :param rules_paths_json: 已废弃（iter-137 规则全局化），保留向后兼容
        :param use_builtin: 已废弃（iter-137 规则全局化），保留向后兼容
        :return: 新工作区 ID（``"ws-<8位hex>"`` 格式）

        iter-137：规则配置全局化——所有工作区共享同一规则集，扫描时直接读
        全局 :class:`RulesController`。iter-139：新工作区的 ``rules_paths``/
        ``use_builtin`` 字段从全局 :class:`RulesController` 同步，使
        :attr:`WorkspaceItem.rules_tags` 标签反映实际扫描时使用的规则。
        ``rules_paths_json`` 与 ``use_builtin`` 参数保留仅为向后兼容，
        实际值从全局读取。
        """
        ws_id = f"ws-{secrets.token_hex(4)}"
        # iter-139：从全局 RulesController 读取规则配置快照
        global_paths, global_use_builtin = self._read_global_rules_snapshot()

        if not name:
            name = f"任务 {self._model.rowCount() + 1}"

        self._create_workspace(ws_id, name, mode_str, target, global_paths, global_use_builtin)
        # iter-133：用户新建工作区后立即创建 ScanController（用户马上要操作），
        # 与 _load_persisted（启动恢复）的延迟创建策略区分
        self._ensure_scan_controller(ws_id)
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
        last_activity_time: float | None = None,
    ) -> None:
        """创建工作区内部实现（仅构造 item，ScanController 延迟创建）。

        供 :meth:`addWorkspace`（新建）与 :meth:`_load_persisted`（恢复）共用。
        持久化恢复时传入 ``status_text``/计数字段，使重启后仍能展示上次扫描状态。

        iter-133：ScanController 改为延迟创建——本方法仅创建 WorkspaceItem
        并加入 model，ScanController 在首次访问（:meth:`_ensure_scan_controller`）
        时才构造。避免启动时为 N 个工作区各创建一个 ScanController + 加载规则集
        造成的主线程阻塞。

        :param last_activity_time: 最近活动时间戳；None 表示使用当前时间（新建场景）
        """
        import time as _time

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
            last_activity_time=last_activity_time if last_activity_time is not None else _time.time(),
        )
        self._model.add_workspace(item)

    def _ensure_scan_controller(self, ws_id: str) -> ScanController | None:
        """延迟创建并返回指定工作区的 :class:`ScanController` 实例。

        iter-133：ScanController 延迟创建——首次访问时才构造 ScanController，
        注入工作区参数（扫描模式/目标/任务覆盖/规则集）并连接状态信号。
        后续访问直接返回缓存的实例。

        :param ws_id: 工作区 ID
        :return: ScanController 实例；若工作区不存在返回 ``None``
        """
        # 已创建：直接返回
        existing = self._scan_controllers.get(ws_id)
        if existing is not None:
            return existing
        # 工作区不存在：返回 None
        item = self._model.get_workspace(ws_id)
        if item is None:
            return None
        # 首次访问：构造 ScanController + 注入参数 + 连接信号
        # iter-133：注入共享 SkipStore，避免每个工作区独立读 skips.json
        # iter-133：注入共享 WhitelistController，使误报白名单对所有工作区生效
        scan_controller = ScanController(
            self._config_controller,
            self._rules_controller,
            self,
            skip_store=self._shared_skip_store,
            whitelist_controller=self._whitelist_controller,
        )
        try:
            mode_index = SCAN_MODE_STR_TO_INDEX.get(item.mode_str, SCAN_MODE_DEFAULT_INDEX)
            scan_controller.setScanModeIndex(mode_index)
            if item.mode_str == "drive" and item.target:
                scan_controller.setSelectedDrive(item.target)
            elif item.mode_str == "folder" and item.target:
                scan_controller.setFolderRoot(item.target)
            # 同步任务级配置覆盖到 ScanController（iter-104）
            for key, value in item.task_overrides.items():
                scan_controller.setTaskOverride(key, value)
            # iter-137：规则配置全局化——ScanController 启动时从全局 RulesController
            # 取 ruleset 占位，startScan 时再次取最新（保证规则变更立即生效），
            # 不再注入工作区专属 ruleset
            # 连接状态变化信号以回写工作区
            scan_controller.scanStateChanged.connect(  # pyrefly: ignore [missing-attribute]
                lambda wid=ws_id: self._sync_workspace_state(wid)
            )
            scan_controller.progressChanged.connect(  # pyrefly: ignore [missing-attribute]
                lambda wid=ws_id: self._sync_workspace_state(wid)
            )
            scan_controller.statusChanged.connect(  # pyrefly: ignore [missing-attribute]
                lambda wid=ws_id: self._sync_workspace_state(wid)
            )
        except Exception:
            # 初始化失败：cleanup 并抛出
            scan_controller.cleanup()
            scan_controller.deleteLater()
            raise
        self._scan_controllers[ws_id] = scan_controller
        return scan_controller

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
        """启动指定工作区的扫描。

        iter-132：启动扫描时将工作区移到列表顶部（最近活动在最上方）。
        iter-134：显式设置 ``ScanController._pending_ws_id``，即使是「启动扫描」
            （全量）也持久化 manifest，使重启后下一次增量扫描可直接生效。
        """
        controller = self._ensure_scan_controller(ws_id)
        if controller is None:
            logger.warning("工作区 %s 不存在", ws_id)
            return
        self._model.move_to_top(ws_id)
        self._persist()
        # iter-134：全量扫描同样持久化 manifest，保证下次增量扫描有基线可比对
        controller._pending_ws_id = ws_id  # 同包私有访问
        controller.startScan()

    @Slot(str)  # pyrefly: ignore [not-callable]
    def startIncrementalScan(self, ws_id: str) -> None:
        """启动指定工作区的增量扫描（iter-124）。

        委托给对应工作区的 :class:`ScanController`，加载上次 manifest 与
        ScanReport 后启用增量模式。无上次结果时回退到全量扫描。
        iter-132：增量扫描同样将工作区移到列表顶部。
        """
        controller = self._ensure_scan_controller(ws_id)
        if controller is None:
            logger.warning("工作区 %s 不存在", ws_id)
            return
        self._model.move_to_top(ws_id)
        self._persist()
        controller.startIncrementalScan(ws_id)

    @Slot(str)  # pyrefly: ignore [not-callable]
    def togglePause(self, ws_id: str) -> None:
        """暂停/继续指定工作区的扫描。"""
        controller = self._ensure_scan_controller(ws_id)
        if controller is None:
            return
        controller.togglePause()

    @Slot(str)  # pyrefly: ignore [not-callable]
    def cancelScan(self, ws_id: str) -> None:
        """取消指定工作区的扫描。"""
        controller = self._ensure_scan_controller(ws_id)
        if controller is None:
            return
        controller.cancelScan()

    @Slot(str, str, str)  # pyrefly: ignore [not-callable]
    def exportResults(self, ws_id: str, fmt: str, path_str: str) -> None:
        """导出指定工作区的扫描结果。

        :param ws_id: 工作区 ID
        :param fmt: ``"pdf"``/``"csv"``/``"json"``/``"sarif"``/``"text"``
        :param path_str: 导出文件绝对路径（由 QML FileDialog 选定）
        """
        controller = self._ensure_scan_controller(ws_id)
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
        controller = self._ensure_scan_controller(ws_id)
        if controller is not None:
            mode_index = SCAN_MODE_STR_TO_INDEX[mode_str]
            controller.setScanModeIndex(mode_index)
            if mode_str == "drive" and target:
                controller.setSelectedDrive(target)
            elif mode_str == "folder" and target:
                controller.setFolderRoot(target)
        self._persist()
        self.workspaceListChanged.emit()  # pyrefly: ignore [missing-attribute]

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
        controller = self._ensure_scan_controller(ws_id)
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
        controller = self._ensure_scan_controller(ws_id)
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
        并持久化空列表到 ``workspaces.json``。
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
        if self._active_scan_workspace_id:  # pragma: no cover - 入口已拒绝，防御性兜底
            self._active_scan_workspace_id = ""
            self.activeScanChanged.emit()  # pyrefly: ignore [missing-attribute]
        # iter-137：规则配置全局化——不再有工作区绑定，无需解绑
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
        controller = self._ensure_scan_controller(ws_id)
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

        iter-132：``quick_cancel`` 内部已改为 cancel + wait(500) + terminate，
        确保 QThread 退出，避免进程退出后后台残留。同时取消 ResultRestoreWorker
        和 ResultListModel 的 FilterWorker。
        """
        for controller in self._scan_controllers.values():
            controller.quick_cancel()
        self._scan_controllers.clear()
        # iter-133：标记已清理，hasCurrentWorkspace 据此返回 False
        self._cleaned_up = True
        # fallback 仅快速取消
        if hasattr(self, "_fallback_controller"):
            self._fallback_controller.quick_cancel()
        # iter-132：取消未完成的 ResultRestoreWorker
        from typing import cast

        try:
            from PySide2.QtCore import QThread
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QThread  # pyrefly: ignore [missing-import]

        for worker in list(self._restore_workers.values()):
            qt_worker = cast(QThread, worker)
            if qt_worker.isRunning():
                qt_worker.quit()
                qt_worker.wait(500)  # pyrefly: ignore [missing-argument]
                if qt_worker.isRunning():
                    qt_worker.terminate()
        self._restore_workers.clear()
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
        iter-132：按 ``last_activity_time`` 倒序加载，使最近活动的工作区排在顶部。
        """
        workspaces = load_persisted_workspaces(self._persist_file)
        # iter-132：按 last_activity_time 倒序排列，最新活动的排在最上方
        workspaces.sort(
            key=lambda ws: float(ws.get("last_activity_time", 0.0)),
            reverse=True,
        )
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
                    # 恢复最近活动时间（iter-132 起持久化，用于列表排序）
                    last_activity_time=coerce_float(ws.get("last_activity_time", 0.0)),
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
        # iter-137：将旧版本工作区级 rules_paths 迁移到全局规则配置
        self._migrate_workspace_rules_to_global(workspaces)
        # iter-139：迁移完成后同步刷新所有工作区的 rules_paths/use_builtin
        # 为全局值，使 WorkspaceCard 标签反映当前全局规则（而非持久化旧值）
        self._sync_all_workspaces_rules()

    # ----------------------------- iter-137 规则全局化迁移 -----------------------------

    def _migrate_workspace_rules_to_global(self, workspaces: list[dict[str, object]]) -> None:
        """将旧版本工作区级 ``rules_paths``/``use_builtin`` 合并到全局 :class:`Config`。

        iter-137 之前每个工作区有独立的 ``rules_paths``，重构后所有工作区共享
        全局规则集。启动恢复时遍历所有工作区，将不重复的规则文件路径合并到
        ``Config.rules_paths``；``use_builtin`` 取 OR（任一工作区启用则全局启用）。
        合并后调用 :meth:`ConfigController.save` 持久化。

        :param workspaces: 从 ``workspaces.json`` 加载的原始 dict 列表
        """
        if not workspaces:
            return
        config = self._config_controller.config
        global_paths: list[str] = list(config.rules_paths)
        global_use_builtin = bool(config.use_builtin)
        changed = False
        for ws in workspaces:
            ws_paths = coerce_str_tuple(ws.get("rules_paths", []))
            for p in ws_paths:
                if p and p not in global_paths:
                    global_paths.append(p)
                    changed = True
            # 任一工作区启用内置规则则全局启用
            if bool(ws.get("use_builtin", False)) and not global_use_builtin:
                global_use_builtin = True
                changed = True
        if changed:
            config.rules_paths = global_paths
            config.use_builtin = global_use_builtin
            self._config_controller.save()
            logger.info(
                "已迁移工作区级规则到全局配置：rules_paths=%d 条，use_builtin=%s",
                len(global_paths),
                global_use_builtin,
            )

    def _invalidate_all_manifests(self) -> None:
        """全局规则变更时清除所有已创建 ScanController 的 manifest（iter-137）。

        全局 :class:`RulesController` 的 ``rulesetChanged`` 信号触发本方法，
        遍历所有已创建的 :class:`ScanController` 调用 ``invalidate_manifest``，
        使下次增量扫描回退为全量，确保新规则被实际执行。
        """
        for ws_id, controller in self._scan_controllers.items():
            controller.invalidate_manifest(ws_id)

    def _read_global_rules_snapshot(self) -> tuple[tuple[str, ...], bool]:
        """读取当前全局 :class:`RulesController` 的规则配置快照。

        :return: ``(rules_paths, use_builtin)``，``rules_paths`` 为
            ``tuple[str, ...]``，仅包含实际存在的规则文件路径（与
            :attr:`RulesController.rules_paths` 一致，扫描时实际加载的文件）。
        """
        paths = tuple(str(p) for p in self._rules_controller.rules_paths)
        use_builtin = self._rules_controller.use_builtin
        return paths, use_builtin

    def _sync_all_workspaces_rules(self) -> None:
        """全局规则变化时同步刷新所有工作区的 ``rules_paths``/``use_builtin`` 字段。

        iter-139：iter-137 规则全局化后，:attr:`WorkspaceItem.rules_paths`/
        ``use_builtin`` 字段不再决定扫描时使用的规则（ScanController 直接读
        全局 :class:`RulesController`），但 :attr:`WorkspaceItem.rules_tags`
        派生属性仍依赖这些字段。本方法在全局规则变化时将所有工作区的
        ``rules_paths``/``use_builtin`` 同步为全局值，使 QML ``WorkspaceCard``
        的规则标签反映当前全局规则配置。
        """
        global_paths, global_use_builtin = self._read_global_rules_snapshot()
        changed = False
        for item in self._model.all_workspaces():
            if item.rules_paths != global_paths or item.use_builtin != global_use_builtin:
                self._model.update_workspace(
                    item.workspace_id,
                    rules_paths=global_paths,
                    use_builtin=global_use_builtin,
                )
                changed = True
        if changed:
            self._persist()

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
        持久化失败仅记录日志，不影响主流程。委托 :func:`._restore.save_cached_results`。

        iter-135：本次结果无命中但缓存文件中已有非空结果时不覆盖，避免增量扫描
        回退全量后空结果覆盖之前的完整结果，导致重启后无法恢复且后续增量扫描
        因 ``prev_report.hits`` 为空而无法合并旧命中（恶性循环）。
        """
        _save_cached_results_fn(
            report=controller._last_report,  # 同包私有访问
            cache_file=self._cached_results_path(ws_id),
            cached_results_dir=self._cached_results_dir,
        )

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
        controller = self._ensure_scan_controller(ws_id)
        if controller is None:
            return
        # 标记恢复中，启动后台线程
        self._restoring_workspaces.add(ws_id)
        controller._set_restoring(True)
        from fuscan.gui.workers.restore_worker import ResultRestoreWorker

        worker = ResultRestoreWorker(ws_id, cache_file)
        worker.restore_done.connect(self._on_restore_done)  # pyrefly: ignore [missing-attribute]
        worker.restore_failed.connect(self._on_restore_failed)  # pyrefly: ignore [missing-attribute]
        worker.finished.connect(lambda wid=ws_id: self._cleanup_restore_worker(wid))
        self._restore_workers[ws_id] = worker
        worker.start()

    def _on_restore_done(self, ws_id: str, report: object) -> None:
        """后台恢复完成：在主线程恢复结果到 ScanController。"""
        from fuscan.scanner import ScanReport

        controller = self._ensure_scan_controller(ws_id)
        if controller is not None and isinstance(report, ScanReport):
            controller.restoreFromReport(report)
            controller._set_restoring(False)
            logger.debug("工作区 %s 扫描结果后台恢复完成（%d 条命中）", ws_id, len(report.hits))
        self._restoring_workspaces.discard(ws_id)
        self._restored_workspaces.add(ws_id)

    def _on_restore_failed(self, ws_id: str, error_msg: str) -> None:
        """后台恢复失败：记录日志，清除恢复态。"""
        controller = self._ensure_scan_controller(ws_id)
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
        """删除指定工作区的缓存扫描结果文件。委托 :func:`._restore.delete_cached_results`。"""
        _delete_cached_results_fn(self._cached_results_path(ws_id))

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

        委托 :func:`._history_view.build_workspace_history_json`。

        :param ws_id: 工作区 ID
        :return: JSON 数组字符串，每个元素为历史条目 dict（按时间倒序）；
            空历史返回 ``"[]"``
        """
        return _build_workspace_history_json(self._history_store.workspace_history(ws_id))

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def compareWithPreviousScan(self, ws_id: str) -> str:
        """对比指定工作区最近一次扫描与上上次扫描，返回对比结果 JSON。

        委托 :func:`._history_view.build_scan_comparison_json`。

        :param ws_id: 工作区 ID
        :return: JSON 对象字符串，包含 ``current``/``previous``/``summary``/
            ``new_hits``/``resolved_hits``/``persistent_hits``/``matched_delta``/
            ``trend`` 字段；无历史返回 ``"{}"``
        """
        return _build_scan_comparison_json(self._history_store.workspace_history(ws_id, limit=2))

    @Slot(str, result=int)  # pyrefly: ignore [not-callable]
    def clearWorkspaceHistory(self, ws_id: str) -> int:
        """清空指定工作区的扫描历史，返回被清除的条目数。"""
        return self._history_store.clear_workspace(ws_id)
