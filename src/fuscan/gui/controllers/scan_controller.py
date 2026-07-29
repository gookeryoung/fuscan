"""扫描工作流控制器：QML ↔ ScanWorker/FileStatsWorker 桥接。

状态机三态：``setup`` → ``scanning`` → ``results``（取消/失败回 ``setup``）。
所有耗时操作走 ``QThread`` Worker，QML 主线程仅渲染。

公共 API：

- :class:`ScanController`：``QObject`` 子类，``@Property``/``@Slot`` 暴露给 QML
- :meth:`ScanController.start_scan`：开始扫描（启动 stats worker → scan worker 串行）
- :meth:`ScanController.toggle_pause`：暂停/继续扫描
- :meth:`ScanController.cancel_scan`：取消扫描
- :meth:`ScanController.export_results`：导出 CSV/JSON（路径由 QML FileDialog 传入）
- :meth:`ScanController.open_location` / :meth:`copy_path`：选中结果文件操作
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]

from fuscan.config import CONFIG_DIR, Config
from fuscan.gui.controllers._result_detail import (
    build_detail_hits_model,
    can_replace_result,
    move_to_staging,
    replace_selected,
)
from fuscan.gui.controllers._scan_roots import build_scan_roots, can_build_roots
from fuscan.gui.controllers._task_overrides import (
    effective_ignore_dirs,
    effective_max_depth,
    effective_max_file_size,
    effective_max_workers,
    effective_scan_archives,
)
from fuscan.gui.explorer import open_path_in_explorer
from fuscan.gui.models.result_model import ResultListModel
from fuscan.gui.models.workspace_model import (
    STR_STATUS_CANCELLED,
    STR_STATUS_DONE,
    STR_STATUS_PAUSED,
    STR_STATUS_READY,
)
from fuscan.gui.scan_mode import (
    SCAN_MODE_DEFAULT_INDEX,
    SCAN_MODE_STR_TO_INDEX,
    scan_mode_index_to_str,
)
from fuscan.replacer import ReplaceStatus
from fuscan.rules.model import Severity
from fuscan.scanner import ScanReport
from fuscan.scanner.export import export_report
from fuscan.scanner.result import IncrementalManifest, ProgressInfo, ScanResult, WalkResult, format_size
from fuscan.skip_store import SkipStore
from fuscan.workers import FileStatsWorker, ScanWorker

if TYPE_CHECKING:
    from fuscan.cache import CacheStore
    from fuscan.gui.controllers.config_controller import ConfigController
    from fuscan.gui.controllers.rules_controller import RulesController
    from fuscan.gui.controllers.whitelist_controller import WhitelistController
    from fuscan.history.model import ScanHistoryEntry
    from fuscan.rules.model import RuleSet

__all__ = ["ScanController"]

logger = logging.getLogger(__name__)


def _new_whitelist_controller() -> WhitelistController:
    """构造独立的 :class:`WhitelistController` 实例（向后兼容回退）。

    延迟导入避免 controllers 包顶层循环依赖（whitelist_controller 导入 rules.whitelist，
    与 scan_controller 无循环）。
    """
    from fuscan.gui.controllers.whitelist_controller import WhitelistController

    return WhitelistController()


# 扫描状态机字符串（与 QML 侧 ContentArea.qml 的 case 分支对齐）
STATE_SETUP: str = "setup"
STATE_SCANNING: str = "scanning"
STATE_RESULTS: str = "results"

# 扫描阶段字符串（QML 侧 StatsPage.qml 按 phase 切换展示）
PHASE_SETUP: str = "setup"
PHASE_WALK: str = "walk"
PHASE_SCAN: str = "scan"
PHASE_ARCHIVE: str = "archive"
PHASE_DONE: str = "done"

# iter-124：增量扫描清单持久化目录（与 results 目录并行，存放 <ws_id>.json）
_MANIFESTS_DIR: Path = CONFIG_DIR / "manifests"


class ScanController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """扫描工作流控制器。

    持有 :class:`FileStatsWorker` / :class:`ScanWorker` 引用、扫描状态、
    进度信息与结果模型，通过 ``@Property`` 暴露给 QML 绑定。

    :param config_controller: 配置控制器（提供 Config 与 extractor 勾选）
    :param rules_controller: 规则控制器（提供 RuleSet）
    :param parent: 父 QObject
    """

    # ----------------------------- 信号 -----------------------------

    scanStateChanged = Signal()
    progressChanged = Signal()
    statusChanged = Signal()
    selectedResultChanged = Signal()
    drivesChanged = Signal()
    scanModeChanged = Signal()
    folderRootChanged = Signal()
    rulesCountChanged = Signal()
    selectedDriveChanged = Signal()
    # canStartScan 的独立 NOTIFY 信号：仅触发 canStartScan 重算，
    # 不触发 QML 侧 Connections.onScanStateChanged（避免 StackView 误重建）
    canStartScanChanged = Signal()
    # iter-128：后台恢复扫描结果时的加载态信号
    restoringChanged = Signal()

    def __init__(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        parent: QObject | None = None,
        skip_store: SkipStore | None = None,
        whitelist_controller: WhitelistController | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_controller = config_controller
        self._rules_controller = rules_controller
        self._config: Config = config_controller.config
        self._ruleset: RuleSet | None = None
        self._last_report: ScanReport | None = None
        self._worker: ScanWorker | None = None
        self._stats_worker: FileStatsWorker | None = None
        self._cache: CacheStore | None = None
        # iter-133：SkipStore 共享实例——由 WorkspaceController 注入全局共享
        # SkipStore，避免 N 个工作区各自读 ~/.fuscan/skips.json 造成的重复 I/O。
        # 独立构造（无 skip_store 参数）时回退到自建实例，保持向后兼容。
        self._skip_store: SkipStore = skip_store if skip_store is not None else SkipStore()
        # iter-133：WhitelistController 共享实例——由 WorkspaceController 注入。
        # 为 None 时（独立测试）回退到自建实例，保持向后兼容。
        self._whitelist_controller: WhitelistController = (
            whitelist_controller if whitelist_controller is not None else _new_whitelist_controller()
        )
        self._result_model: ResultListModel = ResultListModel(self)
        # 任务级配置覆盖（iter-104）：键为 Config 字段名，值为该任务专属覆盖值
        # 通过 _effective_<field>() 方法优先读取覆盖值，回退到全局 Config
        self._task_overrides: dict[str, object] = {}
        # iter-137：规则配置全局化——本控制器不再持有工作区专属 ruleset 副本，
        # 启动时从全局 RulesController.ruleset 取占位，startScan 时再取最新
        # （保证规则变更立即生效）。缓存上下文构建时直接读取全局 rules_paths/use_builtin
        # iter-113：最近一次批量替换的 (源文件路径, 备份文件路径) 配对元组，供 undoLastBatchReplace 撤销。
        # 初始为空元组表示无可撤销记录；每次批量替换后由 replaceAllFilteredResults 更新。
        # 存储 (src, backup) 配对而非仅 backup_path，因为 backup_path 与 src 不在同一目录，
        # 直接 with_suffix('') 会得到备份区下的路径而非源文件路径。
        self._last_batch_backup_paths: tuple[tuple[Path, Path], ...] = ()
        # iter-124：增量扫描上下文（由 startIncrementalScan 设置，_on_stats_finished/
        # _on_scan_finished 读取）。_pending_manifest 由 stats worker 完成后填入，
        # _pending_prev_report 传给 ScanWorker 供 Scanner 合并未变更文件命中结果，
        # _pending_ws_id 标识当前工作区用于 manifest 持久化（空串表示全量扫描不持久化）。
        self._pending_manifest: IncrementalManifest | None = None
        self._pending_prev_report: ScanReport | None = None
        self._pending_ws_id: str = ""
        # iter-135：标记增量扫描回退为全量扫描，_on_scan_finished 据此在本次
        # 无命中时合并 _pending_prev_report 中的旧 hits，避免回退全量 0 命中
        # 导致用户丢失之前的结果。
        self._fallback_from_incremental: bool = False

        # 扫描状态
        self._scan_state: str = STATE_SETUP  # setup / scanning / results
        self._cancelling: bool = False
        self._is_paused: bool = False
        # iter-128：后台恢复扫描结果的加载态
        self._restoring: bool = False

        # 进度信息
        self._progress_scanned: int = 0
        self._progress_total: int = 0
        self._progress_indeterminate: bool = False
        self._current_file: str = ""
        self._status_summary: str = STR_STATUS_READY
        self._status_text: str = STR_STATUS_READY
        self._passed_count: int = 0
        self._matched_count: int = 0
        self._skipped_count: int = 0
        self._error_count: int = 0
        # iter-137：压缩包内条目数（含在 scanned 中，单独暴露供 UI 注明）
        self._archive_entry_count: int = 0
        # 阶段独立进度（iter-105 双进度条）：
        # walk 阶段：discovered 持续增长，skipped/user_skipped 反映白名单与用户标记跳过
        # scan 阶段：scanned/total 反映解析进度，与上方 progressScanned/progressTotal 同步
        self._scan_phase: str = PHASE_SETUP  # setup / walk / scan / archive / done
        self._walk_discovered: int = 0
        self._walk_skipped: int = 0
        self._walk_user_skipped: int = 0
        self._walk_indeterminate: bool = False
        self._walk_done: bool = False
        self._scan_done: bool = False

        # 扫描目标
        self._scan_mode_index: int = SCAN_MODE_STR_TO_INDEX.get(self._config.scan_mode, SCAN_MODE_DEFAULT_INDEX)
        self._selected_drive: str = self._config.last_drive or ""
        self._folder_root: str = ""
        if self._config_controller.scanPaths:
            self._folder_root = self._config_controller.scanPaths[0]

        # 选中结果
        self._selected_result_index: int = -1

        # iter-137：规则配置全局化——启动时一次性快照全局 RulesController.ruleset
        # 作为占位（避免 None 状态），startScan 时再次取最新（保证规则变更立即生效）
        self._ruleset = self._rules_controller.ruleset

    # ----------------------------- 扫描状态 -----------------------------

    @Property(str, notify=scanStateChanged)  # pyrefly: ignore [not-callable]
    def scanState(self) -> str:
        """扫描状态（setup/scanning/results）。"""
        return self._scan_state

    @Property(bool, notify=restoringChanged)  # pyrefly: ignore [not-callable]
    def restoring(self) -> bool:
        """是否正在后台恢复扫描结果（iter-128）。

        QML 据此显示「正在恢复扫描结果...」占位态，加载完成后无缝切换到结果列表。
        """
        return self._restoring

    @Property(bool, notify=scanStateChanged)  # pyrefly: ignore [not-callable]
    def isPaused(self) -> bool:
        """是否暂停中。"""
        return self._is_paused

    @Property(bool, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def cancelling(self) -> bool:
        """是否正在取消扫描中。

        cancelScan 设置为 True，_reset_scan_ui 重置为 False（取消完成回调）。
        QML 据此显示模态遮罩防止用户重复操作（与退出保存 Popup 同模式）。
        notify 复用 progressChanged：cancelScan 与 _reset_scan_ui 均已 emit 该信号。
        """
        return self._cancelling

    @Property(bool, notify=canStartScanChanged)  # pyrefly: ignore [not-callable]
    def canStartScan(self) -> bool:
        """是否可开始扫描（规则集已加载 + 目标已选）。"""
        if self._scan_state == STATE_SCANNING:
            return False
        if self._ruleset is None:
            return False
        return self._can_build_roots()

    @Property(str, notify=statusChanged)  # pyrefly: ignore [not-callable]
    def statusText(self) -> str:
        """状态徽标文本。"""
        return self._status_text

    # ----------------------------- 进度 -----------------------------

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def progressScanned(self) -> int:
        """已扫描文件数。"""
        return self._progress_scanned

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def progressTotal(self) -> int:
        """总文件数。"""
        return self._progress_total

    @Property(float, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def progress(self) -> float:
        """进度百分比（0-100）。

        ``progressTotal <= 0`` 时返回 0（避免除零导致 NaN）。
        扫描进行中按 ``progressScanned / progressTotal * 100`` 计算；
        扫描完成后（``scanDone=True``）固定返回 100，确保进度条与
        「已完成」状态文字对应（iter-125 修复：scan 阶段完成后 ``progressScanned``
        可能因错误文件未计入而小于 ``progressTotal``，导致进度条未满）。
        """
        if self._scan_done:
            return 100.0
        if self._progress_total <= 0:
            return 0.0
        return min(100.0, self._progress_scanned * 100.0 / self._progress_total)

    @Property(bool, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def progressIndeterminate(self) -> bool:
        """进度条是否为不确定模式。"""
        return self._progress_indeterminate

    @Property(str, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def currentFile(self) -> str:
        """当前正在扫描的文件路径。"""
        return self._current_file

    @Property(str, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def statusSummary(self) -> str:
        """状态栏摘要文本。"""
        return self._status_summary

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def passedCount(self) -> int:
        """已通过文件数。"""
        return self._passed_count

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def matchedCount(self) -> int:
        """命中文件数。"""
        return self._matched_count

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def skippedCount(self) -> int:
        """跳过文件数。"""
        return self._skipped_count

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def errorCount(self) -> int:
        """错误文件数。"""
        return self._error_count

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def archiveEntryCount(self) -> int:
        """压缩包内条目数（含在 scanned 中，iter-137）。

        用于 UI 注明"扫描 N"中包含的压缩包内条目数，
        避免 ``scanned > total_files`` 时产生误解。
        """
        return self._archive_entry_count

    # ----------------------------- 阶段与收集进度（iter-105 双进度条） -----------------------------

    @Property(str, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def scanPhase(self) -> str:
        """当前扫描阶段。

        - ``"setup"``：未开始
        - ``"walk"``：收集文件清单（FileStatsWorker 运行中）
        - ``"scan"``：解析文件内容（ScanWorker 主阶段）
        - ``"archive"``：扫描压缩包内条目
        - ``"done"``：全部完成
        """
        return self._scan_phase

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def walkDiscovered(self) -> int:
        """walk 阶段已发现的文件总数（持续增长，含跳过项）。"""
        return self._walk_discovered

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def walkSkipped(self) -> int:
        """walk 阶段按白名单跳过的文件数（未勾选的扩展名）。"""
        return self._walk_skipped

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def walkUserSkipped(self) -> int:
        """walk 阶段用户标记跳过的文件数。"""
        return self._walk_user_skipped

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def walkClassified(self) -> int:
        """walk 阶段收集到的符合文件类型的文件数（实际进入扫描阶段的文件数）。

        计算：``walkDiscovered - walkSkipped - walkUserSkipped``，下界为 0。
        用于统计 UI 展示「符合类型 N」。
        """
        classified = self._walk_discovered - self._walk_skipped - self._walk_user_skipped
        return max(0, classified)

    @Property(bool, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def walkIndeterminate(self) -> bool:
        """walk 阶段进度条是否为不确定模式（刚启动尚未收到首个进度）。"""
        return self._walk_indeterminate

    @Property(bool, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def walkDone(self) -> bool:
        """walk 阶段是否已完成（用于 UI 标记收集进度条为完成态）。"""
        return self._walk_done

    @Property(bool, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def scanDone(self) -> bool:
        """scan 阶段是否已完成（用于 UI 标记解析进度条为完成态）。"""
        return self._scan_done

    @Property(float, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def walkProgress(self) -> float:
        """walk 阶段进度百分比（0-100）。

        walk 阶段无确定的 ``total``（文件随遍历持续发现），用 ``discovered`` 自身
        作为分母计算"已发现并分类"的占比：``(discovered - skipped - user_skipped) / discovered``。
        ``discovered == 0`` 时返回 0（避免除零）。

        walk 完成后（``walkDone=True``）固定返回 100，确保进度条与「已完成」
        状态文字对应（iter-125 修复：walk 完成后若有白名单跳过文件，
        ``classified < discovered`` 导致进度条未满）。
        """
        if self._walk_done:
            return 100.0
        if self._walk_discovered <= 0:
            return 0.0
        # 已分类文件占比 = (发现 - 跳过 - 用户跳过) / 发现
        classified = self._walk_discovered - self._walk_skipped - self._walk_user_skipped
        return min(100.0, max(0.0, classified * 100.0 / self._walk_discovered))

    # ----------------------------- 扫描模式与目标 -----------------------------

    @Property(int, notify=scanModeChanged)  # pyrefly: ignore [not-callable]
    def scanModeIndex(self) -> int:
        """扫描模式索引（0=全盘 / 1=盘符 / 2=文件夹）。"""
        return self._scan_mode_index

    @Slot(int)  # pyrefly: ignore [not-callable]
    def setScanModeIndex(self, index: int) -> None:
        """设置扫描模式索引。"""
        mode_str = scan_mode_index_to_str(index)
        if mode_str is not None and index != self._scan_mode_index:
            self._scan_mode_index = index
            self._config.scan_mode = mode_str
            self._config_controller.save()
            self.scanModeChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.canStartScanChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property(str, notify=selectedDriveChanged)  # pyrefly: ignore [not-callable]
    def selectedDrive(self) -> str:
        """当前选中的盘符（盘符模式）。"""
        return self._selected_drive

    @Slot(str)  # pyrefly: ignore [not-callable]
    def setSelectedDrive(self, drive: str) -> None:
        """设置当前选中盘符。"""
        if drive != self._selected_drive:
            self._selected_drive = drive
            self._config.last_drive = drive
            self._config_controller.save()
            self.selectedDriveChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.canStartScanChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property(str, notify=folderRootChanged)  # pyrefly: ignore [not-callable]
    def folderRoot(self) -> str:
        """文件夹模式根路径。"""
        return self._folder_root

    @Slot(str)  # pyrefly: ignore [not-callable]
    def setFolderRoot(self, path_str: str) -> None:
        """设置文件夹模式根路径。"""
        if path_str and path_str != self._folder_root:
            self._folder_root = path_str
            self._config_controller.add_scan_path(path_str)
            self.folderRootChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.canStartScanChanged.emit()  # pyrefly: ignore [missing-attribute]

    # ----------------------------- 任务级配置覆盖（iter-104） -----------------------------

    @Slot(str, object)  # pyrefly: ignore [not-callable]
    def setTaskOverride(self, key: str, value: object) -> None:
        """设置任务级配置覆盖（iter-104）。

        :param key: Config 字段名（``scan_archives``/``max_workers``/
            ``max_file_size``/``max_depth``/``ignore_dirs``）
        :param value: 覆盖值（类型须与 Config 字段一致）

        覆盖值在 :meth:`_effective_scan_archives`/`_effective_max_workers` 等
        方法中优先读取，未设置时回退到全局 :attr:`_config`。
        """
        self._task_overrides[key] = value

    def _effective_scan_archives(self) -> bool:
        """任务级覆盖优先的 scan_archives。"""
        return effective_scan_archives(self._task_overrides, self._config)

    def _effective_max_workers(self) -> int:
        """任务级覆盖优先的 max_workers。"""
        return effective_max_workers(self._task_overrides, self._config)

    def _effective_max_file_size(self) -> int:
        """任务级覆盖优先的 max_file_size。"""
        return effective_max_file_size(self._task_overrides, self._config)

    def _effective_max_depth(self) -> int | None:
        """任务级覆盖优先的 max_depth（None 表示不限深度）。

        与 :meth:`fuscan.gui.controllers.config_controller.ConfigController.setMaxDepth`
        保持语义一致：``0`` 归一化为 ``None``（无限深度），避免 walker 把 ``0``
        误解为「仅根目录直接子项」。
        """
        return effective_max_depth(self._task_overrides, self._config)

    def _effective_ignore_dirs(self) -> tuple[str, ...]:
        """任务级覆盖优先的 ignore_dirs。"""
        return effective_ignore_dirs(self._task_overrides, self._config)

    @Property(int, notify=rulesCountChanged)  # pyrefly: ignore [not-callable]
    def rulesCount(self) -> int:
        """当前规则集规则数。"""
        return len(self._ruleset.rules) if self._ruleset is not None else 0

    @Property(QObject, notify=scanStateChanged)  # pyrefly: ignore [not-callable]
    def resultModel(self) -> ResultListModel:
        """结果列表模型。

        用 ``QObject`` 作为 Property 类型，避免 PySide2 元类型系统对
        ``QAbstractListModel*`` 未注册导致的 ``QMetaObjectBuilder`` 警告。
        """
        return self._result_model

    # ----------------------------- 选中结果 -----------------------------

    @Property(int, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def selectedResultIndex(self) -> int:
        """选中结果行号（-1 表示未选中）。"""
        return self._selected_result_index

    @Slot(int)  # pyrefly: ignore [not-callable]
    def setSelectedResultIndex(self, index: int) -> None:
        """设置选中结果行号。"""
        if index != self._selected_result_index:
            self._selected_result_index = index
            self.selectedResultChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property(str, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def detailFilePath(self) -> str:
        """选中结果文件路径。"""
        result = self._get_selected_result()
        return str(result.path) if result is not None else ""

    @Property(int, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def detailHitsCount(self) -> int:
        """选中结果命中数。"""
        result = self._get_selected_result()
        return len(result.hits) if result is not None else 0

    @Property("QVariantList", notify=selectedResultChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def detailHitsModel(self) -> list[dict[str, object]]:
        """选中结果的命中详情列表（QML 直接 ListView 绑定）。

        每条命中包含：规则名、严重度文本/色值、上下文（detail）、匹配文本、
        匹配条数、匹配目标（filename/content/path）、规则描述（供详情面板展示）。
        """
        return build_detail_hits_model(self._get_selected_result())

    @Property(str, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def detailFileSize(self) -> str:
        """选中结果文件大小（人类可读，如 ``12.3 KB``）。"""
        result = self._get_selected_result()
        if result is None:
            return ""
        return format_size(result.size)

    @Property(bool, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def detailIsArchiveEntry(self) -> bool:
        """选中结果是否为压缩包内部条目（不可替换、不可打开位置）。"""
        result = self._get_selected_result()
        return result is not None and result.archive_path is not None

    @Property(bool, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def canSelectNext(self) -> bool:
        """是否可选中下一条结果。"""
        count = self._result_model.rowCount()
        return 0 <= self._selected_result_index < count - 1

    @Property(bool, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def canSelectPrev(self) -> bool:
        """是否可选中上一条结果。"""
        return self._selected_result_index > 0

    @Property(bool, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def canReplaceSelected(self) -> bool:
        """当前选中结果是否可执行替换。

        条件：已选中结果、规则集已加载、非压缩包内部条目、命中规则中存在
        ``replace=True`` 的规则（否则按钮无意义）。
        """
        return can_replace_result(self._get_selected_result(), self._ruleset)

    @Slot()  # pyrefly: ignore [not-callable]
    def selectNextResult(self) -> None:
        """选中下一条结果（越界自动忽略）。"""
        if 0 <= self._selected_result_index < self._result_model.rowCount() - 1:
            self.setSelectedResultIndex(self._selected_result_index + 1)

    @Slot()  # pyrefly: ignore [not-callable]
    def selectPrevResult(self) -> None:
        """选中上一一条结果（越界自动忽略）。"""
        if self._selected_result_index > 0:
            self.setSelectedResultIndex(self._selected_result_index - 1)

    # ----------------------------- iter-112 过滤+排序 -----------------------------

    @Slot(str)  # pyrefly: ignore [not-callable]
    def setResultFilterText(self, text: str) -> None:
        """设置结果列表的文件路径模糊过滤（不区分大小写）。

        :param text: 搜索文本；空字符串清除该维度过滤
        """
        self._result_model.set_filter_text(text)
        # 过滤后选中索引可能越界，重置为 -1 避免显示错误详情
        if self._selected_result_index >= self._result_model.rowCount():
            self.setSelectedResultIndex(-1)
        self.selectedResultChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot("QVariantList")  # pyrefly: ignore [not-callable]
    def setResultFilterRules(self, rule_names: list[str]) -> None:
        """设置结果列表的规则名多选过滤。

        :param rule_names: 选中的规则名列表；空列表清除该维度过滤
        """
        self._result_model.set_filter_rules(rule_names)
        if self._selected_result_index >= self._result_model.rowCount():
            self.setSelectedResultIndex(-1)
        self.selectedResultChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot("QVariantList")  # pyrefly: ignore [not-callable]
    def setResultFilterSeverities(self, severities: list[str]) -> None:
        """设置结果列表的严重度多选过滤。

        :param severities: 选中的严重度文本列表（"信息"/"警告"/"严重"）；
            空列表清除该维度过滤
        """
        from fuscan.gui.severity_utils import severity_text

        # 将中文文本反向映射为 Severity 枚举
        text_to_sev = {severity_text(sev): sev for sev in (Severity.INFO, Severity.WARNING, Severity.CRITICAL)}
        selected = [text_to_sev[s] for s in severities if s in text_to_sev]
        self._result_model.set_filter_severities(selected)
        if self._selected_result_index >= self._result_model.rowCount():
            self.setSelectedResultIndex(-1)
        self.selectedResultChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot(str, bool)  # pyrefly: ignore [not-callable]
    def setResultSort(self, field: str, ascending: bool) -> None:
        """设置结果列表排序。

        :param field: 排序字段，``"default"/"filePath"/"hitsCount"/"severity"``
        :param ascending: True 升序，False 降序
        """
        self._result_model.set_sort(field, ascending)
        if self._selected_result_index >= self._result_model.rowCount():
            self.setSelectedResultIndex(-1)
        self.selectedResultChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def clearResultFilters(self) -> None:
        """清除结果列表所有过滤条件（保留排序）。"""
        self._result_model.clear_filters()
        # 清除过滤后 rowCount 通常增加，选中索引仍有效，无需重置
        self.selectedResultChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property(int, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def resultTotalCount(self) -> int:
        """原始结果总数（未过滤）。"""
        return self._result_model.total_count

    @Property(int, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def resultFilteredCount(self) -> int:
        """过滤后结果数。"""
        return self._result_model.filtered_count

    @Property("QVariantList", notify=scanStateChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def resultRuleNames(self) -> list[str]:
        """当前结果中出现的所有规则名（供 QML 规则过滤 ComboBox 选择）。"""
        seen: list[str] = []
        for result in self._result_model.results:
            for name in result.rule_names:
                if name not in seen:
                    seen.append(name)
        return seen

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def replaceSelectedResult(self, replace_with: str = "") -> str:
        """替换当前选中结果的命中内容。

        iter-124：接受用户自定义替换文本 ``replace_with``（QML 输入框提供，
        默认 ``...``）。非空时覆盖所有规则的 ``replace_with``，且不要求规则
        ``replace=True``，实现「默认用 ... 替换被命中内容，支持设置自定义」。

        调用 :func:`fuscan.replacer.replace_in_file` 执行备份 + 原子替换。
        返回操作消息供 QML 显示（成功/失败原因）。

        :param replace_with: 用户自定义替换文本（空字符串走规则驱动模式）
        :return: 操作消息字符串
        """
        last_root = self._last_report.root if self._last_report is not None else None
        return replace_selected(
            result=self._get_selected_result(),
            ruleset=self._ruleset,
            backup_dir_str=self._config.backup_dir,
            backup_preserve_relative=self._config.backup_preserve_relative_path,
            last_report_root=last_root,
            override_replace_with=replace_with if replace_with else None,
        )

    # ----------------------------- iter-113 批量替换与撤销 -----------------------------

    def _resolve_backup_dir(self) -> Path:
        """解析当前生效的备份目录 Path。"""
        from fuscan.config import default_backup_dir

        return Path(self._config.backup_dir) if self._config.backup_dir else default_backup_dir()

    def _resolve_scan_root(self) -> Path:
        """解析当前生效的扫描根目录（用于相对路径计算）。"""
        if self._last_report is not None and self._last_report.root is not None:
            return self._last_report.root
        # 回退到选中结果的父目录（防御性）
        selected = self._get_selected_result()
        return selected.path.parent if selected is not None else Path.cwd()

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def replaceAllFilteredResults(self, replace_with: str = "") -> str:
        """对当前过滤后的所有结果执行批量替换。

        iter-124：接受用户自定义替换文本 ``replace_with``（QML 输入框提供，
        默认 ``...``）。非空时覆盖所有规则的 ``replace_with``，且不要求规则
        ``replace=True``。

        调用 :func:`fuscan.replacer.replace_batch`，传入
        ``ResultListModel.filtered_results``。返回 :class:`BatchReplaceResult.message`
        供 QML 显示。

        :param replace_with: 用户自定义替换文本（空字符串走规则驱动模式）
        :return: 操作消息字符串
        """
        if self._ruleset is None and not replace_with:
            return "规则集未加载"
        filtered = self._result_model.filtered_results
        if not filtered:
            return "无待替换的结果"

        backup_dir = self._resolve_backup_dir()
        scan_root = self._resolve_scan_root()

        from fuscan.replacer import replace_batch

        batch_result = replace_batch(
            results=filtered,
            ruleset=self._ruleset,
            backup_root=backup_dir,
            scan_root=scan_root,
            preserve_relative=self._config.backup_preserve_relative_path,
            override_replace_with=replace_with if replace_with else None,
        )
        logger.info(
            "批量替换完成: 成功 %d/%d, 跳过 %d, 失败 %d",
            batch_result.succeeded,
            batch_result.total,
            batch_result.skipped,
            batch_result.failed,
        )
        # 记录最近一次批量替换的 (src, backup) 配对，供 undoLastBatchReplace 撤销。
        # 从 batch_result.details 提取成功项的 (path, backup_path) 配对。
        self._last_batch_backup_paths = tuple(
            (src, result.backup_path)
            for src, result in batch_result.details
            if result.status == ReplaceStatus.SUCCESS and result.backup_path is not None
        )
        return batch_result.message

    @Slot(result=str)  # pyrefly: ignore [not-callable]
    def undoLastBatchReplace(self) -> str:
        """撤销最近一次批量替换，从 ``.bak`` 备份恢复所有文件。

        逐个调用 :func:`fuscan.replacer.restore_from_backup`，按 (src, backup) 配对
        从备份恢复到原源文件路径。无可撤销操作时返回提示。

        :return: 操作消息字符串
        """
        if not self._last_batch_backup_paths:
            return "无可撤销的批量替换"

        from fuscan.replacer import restore_from_backup

        succeeded = 0
        failed = 0
        for src_path, backup_path in self._last_batch_backup_paths:
            msg = restore_from_backup(backup_path, src_path)
            if msg.startswith("已从备份恢复"):
                succeeded += 1
            else:
                failed += 1
                logger.warning("撤销失败: %s", msg)

        # 清除撤销记录，避免重复撤销
        self._last_batch_backup_paths = ()
        summary = f"批量撤销完成：恢复 {succeeded} 个文件"
        if failed:
            summary += f"，{failed} 个失败"
        return summary

    @Slot(result=str)  # pyrefly: ignore [not-callable]
    def undoSelectedReplace(self) -> str:
        """撤销当前选中结果的最近一次替换（从 .bak 恢复）。

        根据选中结果路径反推备份路径（``{src}.bak``），调用
        :func:`fuscan.replacer.restore_from_backup` 恢复。

        :return: 操作消息字符串
        """
        result = self._get_selected_result()
        if result is None:
            return "未选中结果"
        backup_dir = self._resolve_backup_dir()
        scan_root = self._resolve_scan_root()
        # 复用 _resolve_backup_path 计算备份路径
        from fuscan.replacer import _resolve_backup_path, restore_from_backup

        backup_path = _resolve_backup_path(
            src=result.path,
            backup_root=backup_dir,
            scan_root=scan_root,
            preserve_relative=self._config.backup_preserve_relative_path,
        )
        return restore_from_backup(backup_path, result.path)

    @Property(bool, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def canReplaceAllFiltered(self) -> bool:
        """是否可对过滤后结果执行批量替换。

        iter-124：放宽条件——过滤后结果非空且至少一个结果可替换即可
        （不要求规则 ``replace=True``，用户自定义替换文本模式）。
        """
        filtered = self._result_model.filtered_results
        if not filtered:
            return False
        return any(can_replace_result(r, self._ruleset) for r in filtered)

    @Property(bool, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def canUndoLastBatchReplace(self) -> bool:
        """是否有可撤销的批量替换记录。"""
        return bool(self._last_batch_backup_paths)

    @Slot(result=str)  # pyrefly: ignore [not-callable]
    def moveSelectedToStaging(self) -> str:
        """将当前选中结果文件复制到暂存区隔离目录并标记为跳过。

        流程：

        1. 校验选中结果、规则集、非压缩包内部条目
        2. 计算暂存区隔离目录：``<staging_dir>/quarantine/`` 或
           ``<默认暂存区>/quarantine/``
        3. 保留源文件相对扫描根目录的目录结构，复制到隔离目录
        4. 调用 :class:`SkipStore.add` 标记为跳过，后续扫描自动跳过
        5. 返回操作消息供 QML 显示

        - 未选中结果 → ``未选中结果``
        - 压缩包内部条目 → ``压缩包内部条目不支持移至暂存``
        - 复制成功 → ``已移至暂存: <隔离路径>`` 并标记跳过
        - 复制失败 → ``移至暂存失败: <错误>``
        """
        last_root = self._last_report.root if self._last_report is not None else None
        return move_to_staging(
            result=self._get_selected_result(),
            staging_dir_str=self._config.staging_dir,
            last_report_root=last_root,
            skip_store=self._skip_store,
        )

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def markAsFalsePositive(self, rule_filter: str = "") -> str:
        """将当前选中结果加入误报白名单。

        将选中文件的路径与命中规则加入 :class:`WhitelistController`，下次扫描起
        在命中聚合阶段过滤。加入后调用 :meth:`invalidate_manifest` 强制下次
        扫描为全量（白名单变更后增量扫描的 prev_report 仍含误报命中，需重扫）。

        :param rule_filter: 指定规则名（精确匹配）；空字符串表示该文件全部命中
            规则均标记为误报（``*`` 通配）。默认空字符串。
        :return: 操作消息供 QML 显示

        返回值语义：

        - 未选中结果 → ``未选中结果``
        - 压缩包内部条目 → ``压缩包内部条目不支持标记误报``（路径含 ``!`` 无法 glob）
        - 成功 → ``已标记为误报: <路径> (<规则>)``
        """
        result = self._get_selected_result()
        if result is None:
            return "未选中结果"
        if result.archive_path is not None:
            return "压缩包内部条目不支持标记误报"
        rule_name = rule_filter.strip() or "*"
        # 路径 glob 用绝对路径字符串（与 Scanner 中 str(Path) 一致）
        path_glob = str(result.path)
        msg = self._whitelist_controller.addEntry(path_glob, rule_name, "")
        # 白名单变更需强制全量重扫，使本工作区下次扫描过滤误报
        if self._pending_ws_id:
            self.invalidate_manifest(self._pending_ws_id)
        return msg

    # ----------------------------- QML 调用槽 -----------------------------

    @Slot()  # pyrefly: ignore [not-callable]
    def startScan(self) -> None:
        """开始扫描（启动 stats worker → scan worker 串行）。"""
        # iter-135：提前读取并重置回退标志，确保所有提前返回路径都不会
        # 遗留 _fallback_from_incremental=True 导致下次全量扫描误合并旧 hits
        fallback = self._fallback_from_incremental
        self._fallback_from_incremental = False

        if self._scan_state == STATE_SCANNING:
            return
        # iter-137：每次扫描前重新取最新全局 ruleset，保证规则变更立即生效
        self._ruleset = self._rules_controller.ruleset
        if self._ruleset is None:
            logger.warning("未加载规则集，无法开始扫描")
            return

        roots = self._build_scan_roots()
        if not roots:
            logger.warning("未选择有效扫描目标")
            return

        # iter-124：全量扫描不合并 prev_report（_pending_prev_report=None）。
        # 注意：不重置 _pending_ws_id——若由 startIncrementalScan 回退调用，
        # _pending_ws_id 已设置为工作区 ID，仍需持久化 manifest 以便下次增量扫描。
        # iter-135：若由 startIncrementalScan 回退调用（fallback=True），保留
        # _pending_prev_report 供 _on_scan_finished 在本次无命中时合并旧 hits。
        if not fallback:
            self._pending_prev_report = None

        self._result_model.clear()
        self._selected_result_index = -1
        self._cancelling = False
        self._is_paused = False
        self._set_scan_state(STATE_SCANNING)
        self._set_status("扫描中...", "准备统计...")
        # 阶段重置：进入 walk 阶段（iter-105 双进度条）
        self._scan_phase = PHASE_WALK
        self._walk_indeterminate = True
        self._walk_done = False
        self._scan_done = False
        self._walk_discovered = 0
        self._walk_skipped = 0
        self._walk_user_skipped = 0
        # scan 阶段进度字段重置
        self._progress_indeterminate = True
        self._progress_scanned = 0
        self._progress_total = 0
        self._passed_count = 0
        self._matched_count = 0
        self._skipped_count = 0
        self._error_count = 0
        self._archive_entry_count = 0
        self._current_file = "准备统计..."
        self.progressChanged.emit()  # pyrefly: ignore [missing-attribute]

        # 阶段 1：FileStatsWorker 执行 walk 收集文件清单
        self._stats_worker = FileStatsWorker(
            ruleset=self._ruleset,
            roots=roots,
            scan_archives=self._effective_scan_archives(),
            max_depth=self._effective_max_depth(),
            ignore_dirs=self._effective_ignore_dirs(),
            scan_extensions=self._config_controller.enabled_extensions(),
            skip_paths=self._skip_store.paths(),
        )
        self._stats_worker.progress_info.connect(self._on_scan_progress)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.finished_stats.connect(self._on_stats_finished)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.failed.connect(self._on_stats_failed)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.cancelled.connect(self._on_stats_cancelled)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.start()

    @Slot(str)  # pyrefly: ignore [not-callable]
    def startIncrementalScan(self, ws_id: str) -> None:
        """启动增量扫描（iter-124）。

        加载上次 ScanReport（``_last_report``）与 manifest（``~/.fuscan/manifests/<ws_id>.json``），
        传入 FileStatsWorker 启用增量模式：walk 阶段对比指纹跳过未变更文件，
        scan 阶段合并未变更文件的命中结果。

        若无上次 ScanReport 或 manifest，回退到 :meth:`startScan` 全量扫描，
        但仍持久化本次构建的 manifest（``_pending_ws_id`` 已设置），使下次可启用增量。

        :param ws_id: 工作区 ID（用于 manifest 持久化路径 ``<ws_id>.json``）
        """
        # 标记当前工作区 ID，_on_scan_finished 据此持久化 manifest
        self._pending_ws_id = ws_id

        prev_report = self._last_report
        manifest = self._load_manifest(ws_id)
        if prev_report is None or manifest is None:
            # 回退到全量扫描（_pending_ws_id 已设置，仍会持久化 manifest）
            logger.info("工作区 %s 无上次扫描结果或清单，回退到全量扫描", ws_id)
            # iter-135：标记回退，startScan 据此保留 _pending_prev_report，
            # _on_scan_finished 在本次无命中时合并旧 hits
            self._fallback_from_incremental = True
            self._pending_prev_report = prev_report  # 可能为 None，但保留供 _on_scan_finished 检查
            self.startScan()
            return

        if self._scan_state == STATE_SCANNING:
            return
        # iter-137：每次扫描前重新取最新全局 ruleset，保证规则变更立即生效
        self._ruleset = self._rules_controller.ruleset
        if self._ruleset is None:
            logger.warning("未加载规则集，无法开始增量扫描")
            return

        roots = self._build_scan_roots()
        if not roots:
            logger.warning("未选择有效扫描目标")
            return

        # 设置增量上下文：prev_report 传给 ScanWorker，manifest 传给 FileStatsWorker
        self._pending_prev_report = prev_report

        self._result_model.clear()
        self._selected_result_index = -1
        self._cancelling = False
        self._is_paused = False
        self._set_scan_state(STATE_SCANNING)
        self._set_status("增量扫描中...", "准备统计...")
        # 阶段重置：进入 walk 阶段（iter-105 双进度条）
        self._scan_phase = PHASE_WALK
        self._walk_indeterminate = True
        self._walk_done = False
        self._scan_done = False
        self._walk_discovered = 0
        self._walk_skipped = 0
        self._walk_user_skipped = 0
        # scan 阶段进度字段重置
        self._progress_indeterminate = True
        self._progress_scanned = 0
        self._progress_total = 0
        self._passed_count = 0
        self._matched_count = 0
        self._skipped_count = 0
        self._error_count = 0
        self._archive_entry_count = 0
        self._current_file = "准备统计..."
        self.progressChanged.emit()  # pyrefly: ignore [missing-attribute]

        # 阶段 1：FileStatsWorker 执行 walk 收集文件清单（传入 incremental_manifest 启用增量）
        self._stats_worker = FileStatsWorker(
            ruleset=self._ruleset,
            roots=roots,
            scan_archives=self._effective_scan_archives(),
            max_depth=self._effective_max_depth(),
            ignore_dirs=self._effective_ignore_dirs(),
            scan_extensions=self._config_controller.enabled_extensions(),
            skip_paths=self._skip_store.paths(),
            incremental_manifest=manifest,
        )
        self._stats_worker.progress_info.connect(self._on_scan_progress)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.finished_stats.connect(self._on_stats_finished)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.failed.connect(self._on_stats_failed)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.cancelled.connect(self._on_stats_cancelled)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.start()

    @Slot()  # pyrefly: ignore [not-callable]
    def togglePause(self) -> None:
        """暂停/继续扫描。"""
        if self._is_paused:
            if self._stats_worker is not None:
                self._stats_worker.resume()
            if self._worker is not None:
                self._worker.resume()
            self._is_paused = False
            self._set_status("扫描中...", "扫描中...")
        else:
            if self._stats_worker is not None:
                self._stats_worker.pause()
            if self._worker is not None:
                self._worker.pause()
            self._is_paused = True
            self._set_status(STR_STATUS_PAUSED, STR_STATUS_PAUSED)
        self.scanStateChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def cancelScan(self) -> None:
        """取消扫描。"""
        if self._worker is None and self._stats_worker is None:
            return
        self._cancelling = True
        self._set_status("取消中...", "正在取消扫描...")
        self._current_file = "正在取消扫描..."
        self.progressChanged.emit()  # pyrefly: ignore [missing-attribute]
        if self._stats_worker is not None:
            self._stats_worker.cancel()
        if self._worker is not None:
            self._worker.cancel()

    @Slot(str, str)  # pyrefly: ignore [not-callable]
    def exportResults(self, fmt: str, path_str: str) -> None:
        """导出结果到文件（路径由 QML FileDialog 选定后传入）。

        :param fmt: 格式 ``"pdf"``/``"csv"``/``"json"``/``"sarif"``/``"text"``
        :param path_str: 导出文件绝对路径
        """
        if self._last_report is None or not self._last_report.hits:
            return
        if not path_str:
            return
        try:
            export_report(self._last_report, Path(path_str), fmt)
            self._set_status("已导出", f"已导出到 {path_str}")
        except (OSError, ValueError) as exc:
            logger.warning("导出失败: %s", exc, exc_info=True)
            self._set_status("导出失败", f"导出失败: {exc}")

    @Slot()  # pyrefly: ignore [not-callable]
    def openLocation(self) -> None:
        """在文件管理器中打开选中结果文件位置。

        iter-133：压缩包内部条目（``archive_path`` 非 None）时定位到压缩包
        文件本身——内部条目路径形如 ``archive.zip!inner/file.txt`` 无法直接
        被 explorer 识别，定位到压缩包根让用户在文件管理器中查看压缩包。
        """
        result = self._get_selected_result()
        if result is None:
            return
        # 压缩包内部条目：定位到压缩包文件本身
        target = result.archive_path if result.archive_path is not None else result.path
        try:
            open_path_in_explorer(target)
        except OSError as exc:
            logger.warning("打开文件位置失败: %s", exc, exc_info=True)

    @Slot()  # pyrefly: ignore [not-callable]
    def copyPath(self) -> None:
        """复制选中结果文件路径到剪贴板。"""
        result = self._get_selected_result()
        if result is None:
            return
        try:
            from PySide2.QtGui import QGuiApplication  # type: ignore

            clipboard = QGuiApplication.clipboard()
            clipboard.setText(str(result.path))
            self._set_status("已复制", "已复制路径到剪贴板")
        except ImportError:  # pragma: no cover
            from PySide6.QtGui import QGuiApplication  # type: ignore

            clipboard = QGuiApplication.clipboard()
            clipboard.setText(str(result.path))
            self._set_status("已复制", "已复制路径到剪贴板")

    # ----------------------------- 内部槽（worker 信号） -----------------------------

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_scan_progress(self, info: ProgressInfo) -> None:
        """扫描实时进度回调（节流由 worker 内部完成）。

        根据 ``info.phase`` 分别更新 walk / scan / archive 阶段的独立字段，
        使 QML 双进度条能分别反映收集与解析进度。
        """
        if self._cancelling:
            return
        # 阶段切换：phase 变化时同步 _scan_phase
        if info.phase != self._scan_phase:
            self._scan_phase = info.phase
            # walk → scan/archive 切换时标记 walk 阶段完成
            if info.phase in (PHASE_SCAN, PHASE_ARCHIVE) and not self._walk_done:
                self._walk_done = True
                self._walk_indeterminate = False
        # walk 阶段：仅 discovered/skipped/user_skipped 增长，scanned/matched/errors 恒为 0
        if info.phase == PHASE_WALK:
            self._walk_indeterminate = False
            self._walk_discovered = info.total
            self._walk_skipped = info.skipped
            self._walk_user_skipped = info.user_skipped
        else:
            # scan/archive 阶段：更新解析进度
            self._progress_indeterminate = False
            self._progress_scanned = info.scanned
            self._progress_total = info.total
            self._matched_count = info.matched
            self._skipped_count = info.skipped
            self._error_count = info.errors
            self._passed_count = max(info.scanned - info.matched - info.errors, 0)
        # 当前文件截断显示
        if info.current_file:
            path_text = info.current_file
            if len(path_text) > 100:
                path_text = "..." + path_text[-97:]
            self._current_file = path_text
        self._status_summary = info.summary()
        self.progressChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_stats_finished(self, results: list[WalkResult]) -> None:
        """stats worker 完成：标记 walk 阶段完成，构造带 precollected 的 ScanWorker 启动 scan 阶段。"""
        # iter-124：在 cleanup 前读取本次构建的 manifest（_cleanup_stats_worker 置空 _stats_worker）
        if self._stats_worker is not None:
            self._pending_manifest = self._stats_worker.manifest
        self._cleanup_stats_worker()
        # walk 阶段完成：从最终 WalkResult 同步收集统计（iter-105 双进度条）
        total_discovered = sum(wr.total for wr in results)
        total_skipped = sum(wr.skipped for wr in results)
        total_user_skipped = sum(wr.user_skipped for wr in results)
        self._walk_discovered = total_discovered
        self._walk_skipped = total_skipped
        self._walk_user_skipped = total_user_skipped
        self._walk_done = True
        self._walk_indeterminate = False
        # scan 阶段总文件数 = walk 收集的 entries 总数（不含跳过项）
        self._progress_total = sum(len(wr.entries) for wr in results)
        self._progress_indeterminate = False
        self._scan_phase = PHASE_SCAN
        self.progressChanged.emit()  # pyrefly: ignore [missing-attribute]
        cache, source_files = self._build_cache_context()
        assert self._ruleset is not None
        self._worker = ScanWorker(
            ruleset=self._ruleset,
            roots=[wr.root for wr in results],
            scan_archives=self._effective_scan_archives(),
            max_workers=self._effective_max_workers(),
            max_depth=self._effective_max_depth(),
            max_file_size=self._effective_max_file_size(),
            ignore_dirs=self._effective_ignore_dirs(),
            cache=cache,
            source_files=source_files,
            scan_extensions=self._config_controller.enabled_extensions(),
            skip_paths=self._skip_store.paths(),
            precollected=results,
            # iter-124：传入上次报告供 Scanner 合并未变更文件命中结果
            # （_pending_prev_report 由 startScan 置 None 或 startIncrementalScan 设置）
            prev_report=self._pending_prev_report,
            # iter-133：传入白名单快照，Scanner 在命中聚合阶段过滤误报
            whitelist=self._whitelist_controller.snapshot(),
            # iter-134：高熵字符串检测配置（从全局 Config 读取，实时生效）
            entropy_enabled=self._config.entropy_enabled,
            entropy_threshold=self._config.entropy_threshold,
        )
        self._worker.progress_info.connect(self._on_scan_progress)  # pyrefly: ignore [missing-attribute]
        self._worker.finished_report.connect(self._on_scan_finished)  # pyrefly: ignore [missing-attribute]
        self._worker.failed.connect(self._on_scan_failed)  # pyrefly: ignore [missing-attribute]
        self._worker.cancelled.connect(self._on_scan_cancelled)  # pyrefly: ignore [missing-attribute]
        self._worker.start()

    @Slot(str)  # pyrefly: ignore [not-callable]
    def _on_stats_failed(self, error: str) -> None:
        """stats 失败：切回 setup 并提示。"""
        self._reset_scan_ui()
        self._set_status("统计失败", error)
        self._set_scan_state(STATE_SETUP)

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_stats_cancelled(self, results: list[WalkResult]) -> None:  # noqa: ARG002
        """stats 被取消：切回 setup。"""
        self._reset_scan_ui()
        self._set_status("已取消", "已取消统计")
        self._set_scan_state(STATE_SETUP)

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_scan_finished(self, report: ScanReport) -> None:
        """扫描完成：填充结果模型并切到 results 态。

        iter-135：增量扫描回退全量时，若本次无命中但 ``_pending_prev_report``
        有 hits，将旧 hits 合并到 results 中。回退全量因 ``_unchanged_count=0``
        导致 Scanner 层合并条件不满足，此处做 controller 层补救，避免用户
        在运行时丢失之前扫描的结果。
        """
        if not report.hits and self._pending_prev_report is not None and self._pending_prev_report.hits:
            old_hits = self._pending_prev_report.hits
            report = ScanReport(
                root=report.root,
                results=report.results + old_hits,
                stats=report.stats,
                cancelled=report.cancelled,
            )
            logger.info("本次扫描无命中，合并上次扫描的 %d 条命中结果", len(old_hits))
        self._last_report = report
        self._result_model.set_results(report.hits)
        # 从 report.stats 同步最终统计，确保扫描完成后统计页展示正确数值
        self._sync_stats_from_report(report)
        # 标记 scan 阶段完成（iter-105 双进度条）
        self._scan_done = True
        self._scan_phase = PHASE_DONE
        # iter-124：持久化本次构建的 manifest（仅 startIncrementalScan 设置了 _pending_ws_id）
        if self._pending_ws_id and self._pending_manifest is not None:
            self._save_manifest(self._pending_ws_id, self._pending_manifest)
        self._reset_scan_ui()
        summary = report.summary()
        speed = report.stats.speed
        if speed > 0:
            summary += f" | 速度 {speed:.0f} 文件/s"
        self._set_status(STR_STATUS_DONE if not report.cancelled else STR_STATUS_CANCELLED, summary)
        self._set_scan_state(STATE_RESULTS if report.hits else STATE_SETUP)

    @Slot(str)  # pyrefly: ignore [not-callable]
    def _on_scan_failed(self, error: str) -> None:
        """扫描失败：切回 setup 并提示。"""
        self._reset_scan_ui()
        self._set_status("扫描失败", error)
        self._set_scan_state(STATE_SETUP)

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_scan_cancelled(self, report: ScanReport) -> None:
        """扫描被取消：有结果切 results，无结果切 setup。"""
        self._last_report = report
        self._result_model.set_results(report.hits)
        self._sync_stats_from_report(report)
        # 取消时标记 scan 阶段完成（避免进度条卡在中间）
        self._scan_done = True
        self._scan_phase = PHASE_DONE
        self._reset_scan_ui()
        self._set_status(STR_STATUS_CANCELLED, report.summary())
        self._set_scan_state(STATE_RESULTS if report.hits else STATE_SETUP)

    # ----------------------------- 内部方法 -----------------------------

    def _sync_stats_from_report(self, report: ScanReport) -> None:
        """从 ScanReport.stats 同步最终统计到进度字段。

        扫描完成/取消时调用，确保统计页展示的 scanned/total/matched/skipped/errors
        与报告一致（避免 ``_reset_scan_ui`` 前后数值不一致或归零）。
        """
        stats = report.stats
        self._progress_total = stats.total_files
        self._progress_scanned = stats.scanned_files
        self._matched_count = stats.matched_files
        self._skipped_count = stats.skipped_files
        self._error_count = stats.errors
        self._passed_count = max(stats.scanned_files - stats.matched_files - stats.errors, 0)
        self._archive_entry_count = stats.archive_entries

    def _can_build_roots(self) -> bool:
        """判断当前是否可构建扫描根路径列表。"""
        return can_build_roots(self._scan_mode_index, self._selected_drive, self._folder_root)

    def _build_scan_roots(self) -> list[Path]:
        """构建扫描根路径列表。"""
        return build_scan_roots(
            scan_mode_index=self._scan_mode_index,
            selected_drive=self._selected_drive,
            folder_root=self._folder_root,
            config=self._config,
        )

    def _build_cache_context(self) -> tuple[CacheStore | None, dict[Path, str] | None]:
        """构造扫描缓存上下文（iter-137：使用全局规则路径与内置开关）。"""
        if not self._config.cache_enabled:
            return None, None
        if self._cache is None:
            from fuscan.cache import CacheStore, default_cache_path

            cache_path = Path(self._config.cache_path) if self._config.cache_path else default_cache_path()
            self._cache = CacheStore(cache_path)
        from fuscan.cache import compute_source_files

        # iter-137：直接读取全局 RulesController 的 rules_paths/use_builtin
        global_paths = self._rules_controller.rules_paths
        source_files = compute_source_files(
            global_paths,
            use_builtin=self._rules_controller.use_builtin,
        )
        return self._cache, source_files

    # ----------------------------- iter-124 增量扫描清单持久化 -----------------------------

    def _load_manifest(self, ws_id: str) -> IncrementalManifest | None:
        """从 ``~/.fuscan/manifests/<ws_id>.json`` 加载增量扫描清单。

        :param ws_id: 工作区 ID
        :return: :class:`IncrementalManifest` 实例；文件不存在或解析失败返回 ``None``
        """
        manifest_file = _MANIFESTS_DIR / f"{ws_id}.json"
        if not manifest_file.exists():
            return None
        try:
            json_str = manifest_file.read_text(encoding="utf-8")
            return IncrementalManifest.from_json(json_str)
        except (OSError, ValueError) as exc:
            logger.warning("工作区 %s 增量清单加载失败: %s", ws_id, exc)
            return None

    def _save_manifest(self, ws_id: str, manifest: IncrementalManifest) -> None:
        """持久化增量扫描清单到 ``~/.fuscan/manifests/<ws_id>.json``。

        :param ws_id: 工作区 ID
        :param manifest: 本次扫描构建的新清单
        """
        try:
            _MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
            manifest_file = _MANIFESTS_DIR / f"{ws_id}.json"
            manifest_file.write_text(manifest.to_json(), encoding="utf-8")
            logger.debug("工作区 %s 增量清单已持久化（%d 项指纹）", ws_id, len(manifest.fingerprints))
        except OSError as exc:
            logger.warning("工作区 %s 增量清单持久化失败: %s", ws_id, exc)

    def invalidate_manifest(self, ws_id: str) -> None:
        """删除工作区的增量扫描清单（iter-136）。

        规则变更（新增/修改/删除/导入规则）时由 :meth:`WorkspaceController.updateWorkspaceRules`
        调用，使下次增量扫描因 manifest 不存在而回退为全量扫描，确保新规则被实际执行。
        """
        manifest_file = _MANIFESTS_DIR / f"{ws_id}.json"
        if manifest_file.exists():
            try:
                manifest_file.unlink()
                logger.info("工作区 %s 规则已变更，增量清单已清除", ws_id)
            except OSError as exc:
                logger.warning("工作区 %s 增量清单清除失败: %s", ws_id, exc)

    def _get_selected_result(self) -> ScanResult | None:
        """获取当前选中的 :class:`ScanResult`。"""
        return self._result_model.get_result(self._selected_result_index)

    def build_history_entry(self, workspace_id: str, workspace_name: str) -> ScanHistoryEntry | None:
        """从最近一次 :class:`ScanReport` 构建扫描历史条目（iter-115）。

        在扫描完成/取消后由 :class:`WorkspaceController` 调用，将本次扫描
        关键指标归档到 :class:`fuscan.history.HistoryStore`。无 ``_last_report``
        时返回 ``None``。

        :param workspace_id: 工作区 ID
        :param workspace_name: 工作区名称快照
        :return: :class:`fuscan.history.ScanHistoryEntry` 或 ``None``
        """
        if self._last_report is None:
            return None
        from fuscan.history import STATUS_CANCELLED, STATUS_COMPLETED, ScanHistoryEntry

        report = self._last_report
        stats = report.stats
        status = STATUS_CANCELLED if report.cancelled else STATUS_COMPLETED
        # 命中文件路径排序元组（用于对比）
        hit_paths = tuple(sorted(str(r.path) for r in report.hits))
        # 规则名排序元组
        rule_names = tuple(sorted(report.rule_names))
        return ScanHistoryEntry(
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            status=status,
            total_files=stats.total_files,
            scanned_files=stats.scanned_files,
            matched_files=stats.matched_files,
            skipped_files=stats.skipped_files,
            error_count=stats.errors,
            duration_seconds=stats.duration_seconds,
            hit_paths=hit_paths,
            rule_names=rule_names,
            summary=self._status_summary,
        )

    def _set_restoring(self, value: bool) -> None:
        """设置后台恢复加载态（由 WorkspaceController 调用）。"""
        if self._restoring != value:
            self._restoring = value
            self.restoringChanged.emit()  # pyrefly: ignore [missing-attribute]

    def restoreFromReport(self, report: ScanReport) -> None:
        """从持久化的 :class:`ScanReport` 恢复扫描结果（iter-123）。

        重启后由 :class:`WorkspaceController` 调用，从 ``~/.fuscan/results/<ws_id>.json``
        加载上次扫描结果并恢复到 ``_result_model`` 与 ``_last_report``，
        使用户无需重新扫描即可查看历史命中。

        恢复后扫描状态切到 ``results``（有命中）或保持 ``setup``（无命中），
        ``statusText`` 恢复为「已完成」或「已取消」。

        :param report: 持久化的 :class:`ScanReport` 实例
        """
        self._last_report = report
        self._result_model.set_results(report.hits)
        self._sync_stats_from_report(report)
        # 标记 scan 阶段完成（恢复后的状态与正常扫描完成一致）
        self._scan_done = True
        self._scan_phase = PHASE_DONE
        self._reset_scan_ui()
        summary = report.summary()
        speed = report.stats.speed
        if speed > 0:
            summary += f" | 速度 {speed:.0f} 文件/s"
        self._set_status(
            STR_STATUS_DONE if not report.cancelled else STR_STATUS_CANCELLED,
            summary,
        )
        self._set_scan_state(STATE_RESULTS if report.hits else STATE_SETUP)

    def _set_scan_state(self, state: str) -> None:
        """设置扫描状态并 emit 信号。

        scanState 变化会影响 canStartScan（scanning 态返回 False），
        故同时 emit canStartScanChanged。
        """
        if state != self._scan_state:
            self._scan_state = state
            self.scanStateChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.canStartScanChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _set_status(self, text: str, summary: str | None = None) -> None:
        """设置状态文本（同时更新 summary）。"""
        self._status_text = text
        if summary is not None:
            self._status_summary = summary
        self.statusChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.progressChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _reset_scan_ui(self) -> None:
        """重置扫描 UI 到空闲状态。

        保留 ``_progress_scanned`` / ``_progress_total`` / 计数字段，
        使扫描完成后统计页仍能展示最终进度与计数；
        这些字段在下一次 :meth:`startScan` 开头被重置为零。
        """
        self._cancelling = False
        self._is_paused = False
        self._progress_indeterminate = False
        self._current_file = ""
        self._cleanup_workers()
        self.progressChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _cleanup_stats_worker(self) -> None:
        """清理 stats worker。"""
        if self._stats_worker is None:
            return
        self._stats_worker.wait(2000)
        self._stats_worker.deleteLater()
        self._stats_worker = None

    def _cleanup_workers(self) -> None:
        """清理所有 worker（stats + scan）。"""
        if self._worker is not None:
            self._worker.wait(2000)
            self._worker.deleteLater()
            self._worker = None
        self._cleanup_stats_worker()

    def cleanup(self) -> None:
        """窗口关闭时清理资源（worker + cache）。

        iter-124：worker wait 超时从 3000ms 降至 500ms，避免多工作区关闭时
        累计等待过久（10 个工作区 6s → 1s）。超时后 worker 线程随进程退出
        自然终止，不阻塞用户。
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(500)
        if self._stats_worker is not None and self._stats_worker.isRunning():
            self._stats_worker.cancel()
            self._stats_worker.wait(500)
        self._cleanup_workers()
        if self._cache is not None:
            try:
                self._cache.close()
            except (sqlite3.Error, OSError):
                logger.warning("缓存关闭失败", exc_info=True)
            self._cache = None

    def quick_cancel(self) -> None:
        """退出时快速取消所有 worker。

        iter-132：cancel + 短暂 wait(500ms) + terminate 后备。
        原 iter-127 实现仅设 cancel 标志不 wait，导致 QThread 在后台继续运行，
        阻止进程退出（用户看到"界面退出后后台一直还在"）。
        现改为：cancel 后 wait 最多 500ms（大部分 worker < 100ms 退出），
        超时则 terminate 强制终止。不 close SQLite（进程退出由 OS 回收）。
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(500)
            if self._worker.isRunning():
                self._worker.terminate()
                self._worker.wait(200)
        if self._stats_worker is not None and self._stats_worker.isRunning():
            self._stats_worker.cancel()
            self._stats_worker.wait(500)
            if self._stats_worker.isRunning():
                self._stats_worker.terminate()
                self._stats_worker.wait(200)
        # iter-132：取消未完成的 FilterWorker
        self._result_model.cleanup()
