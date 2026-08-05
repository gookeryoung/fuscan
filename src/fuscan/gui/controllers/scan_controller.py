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
import threading
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]

from fuscan.config import CONFIG_DIR, Config
from fuscan.export.report import export_report
from fuscan.gui.controllers._batch_actions import (
    mark_as_false_positive,
    replace_all_filtered_results,
    undo_last_batch_replace,
    undo_selected_replace,
)
from fuscan.gui.controllers._history import build_history_entry as _build_history_entry
from fuscan.gui.controllers._manifest import (
    invalidate_manifest as _invalidate_manifest,
)
from fuscan.gui.controllers._manifest import (
    load_manifest as _load_manifest_fn,
)
from fuscan.gui.controllers._manifest import (
    save_manifest as _save_manifest_fn,
)
from fuscan.gui.controllers._result_detail import (
    build_detail_hits_model,
    can_replace_result,
    move_to_staging,
    replace_selected,
)
from fuscan.gui.controllers._scan_roots import build_scan_roots, can_build_roots
from fuscan.gui.controllers._task_overrides import (
    effective_disabled_temp_rules_paths,
    effective_ignore_dirs,
    effective_max_depth,
    effective_max_file_size,
    effective_max_workers,
    effective_rules_paths,
    effective_scan_archives,
    effective_temp_rules_paths,
    effective_use_builtin,
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
from fuscan.gui.workers import FileStatsWorker, ScanWorker
from fuscan.processing.skip_store import SkipStore
from fuscan.rules import (
    RuleError,
    load_ruleset,
    load_with_builtin,
    merge_multiple_rulesets,
)
from fuscan.rules.model import Severity
from fuscan.scanner import ScanReport
from fuscan.scanner.manifest import IncrementalManifest
from fuscan.scanner.result import ProgressInfo, ScanResult, WalkResult, format_size

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
PHASE_FILTER: str = "filter"
PHASE_SCAN: str = "scan"
PHASE_ARCHIVE: str = "archive"
PHASE_DONE: str = "done"

# 增量扫描清单持久化目录（与 results 目录并行，存放 <ws_id>.json）
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
    # progressChanged 保留为「全量」进度信号（扫描完成/取消/重置时 emit），
    # 确保向后兼容；高频进度回调拆分到以下细粒度信号，减少 QML 绑定重算量。
    progressChanged = Signal()
    # walk 阶段独立进度信号：仅 walk 阶段属性（walkDiscovered/walkSkipped 等）
    # 绑定此信号，scan 阶段进度回调不触发 walk 属性重算。
    walkProgressChanged = Signal()
    # scan/archive 阶段独立进度信号：仅扫描进度与统计计数属性绑定，
    # walk 阶段进度回调不触发 scan 属性重算。
    scanProgressChanged = Signal()
    # 阶段切换信号：scanPhase/scanDone/walkDone/statusSummary 绑定，
    # 仅在阶段变更或扫描终结时 emit，避免每次进度回调触发。
    phaseChanged = Signal()
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
    # 后台恢复扫描结果时的加载态信号
    restoringChanged = Signal()
    # 任务级 effective 配置变更信号——max_workers/max_file_size/max_depth
    # 等任务级覆盖或全局 Config 变更时 emit，供 QML 重算 effective* 属性绑定。
    # 同时连接到 configController.configChanged 以反映全局配置变更。
    effectiveConfigChanged = Signal()

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
        # SkipStore 共享实例——由 WorkspaceController 注入全局共享
        # SkipStore，避免 N 个工作区各自读 ~/.fuscan/skips.json 造成的重复 I/O。
        # 独立构造（无 skip_store 参数）时回退到自建实例，保持向后兼容。
        self._skip_store: SkipStore = skip_store if skip_store is not None else SkipStore()
        # WhitelistController 共享实例——由 WorkspaceController 注入。
        # 为 None 时（独立测试）回退到自建实例，保持向后兼容。
        self._whitelist_controller: WhitelistController = (
            whitelist_controller if whitelist_controller is not None else _new_whitelist_controller()
        )
        self._result_model: ResultListModel = ResultListModel(self)
        # 任务级配置覆盖：键为 Config 字段名，值为该任务专属覆盖值
        # 通过 _effective_<field>() 方法优先读取覆盖值，回退到全局 Config
        self._task_overrides: dict[str, object] = {}
        # 规则配置全局化——本控制器不再持有工作区专属 ruleset 副本，
        # 启动时从全局 RulesController.ruleset 取占位，startScan 时再取最新
        # （保证规则变更立即生效）。缓存上下文构建时直接读取全局 rules_paths/use_builtin
        # 最近一次批量替换的 (源文件路径, 备份文件路径) 配对元组，供 undoLastBatchReplace 撤销。
        # 初始为空元组表示无可撤销记录；每次批量替换后由 replaceAllFilteredResults 更新。
        # 存储 (src, backup) 配对而非仅 backup_path，因为 backup_path 与 src 不在同一目录，
        # 直接 with_suffix('') 会得到备份区下的路径而非源文件路径。
        self._last_batch_backup_paths: tuple[tuple[Path, Path], ...] = ()
        # 增量扫描上下文（由 startIncrementalScan 设置，_on_stats_finished/
        # _on_scan_finished 读取）。_pending_manifest 由 stats worker 完成后填入，
        # _pending_prev_report 传给 ScanWorker 供 Scanner 合并未变更文件命中结果，
        # _pending_ws_id 标识当前工作区用于 manifest 持久化（空串表示全量扫描不持久化）。
        self._pending_manifest: IncrementalManifest | None = None
        self._pending_prev_report: ScanReport | None = None
        self._pending_ws_id: str = ""
        # 标记增量扫描回退为全量扫描，_on_scan_finished 据此在本次
        # 无命中时合并 _pending_prev_report 中的旧 hits，避免回退全量 0 命中
        # 导致用户丢失之前的结果。
        self._fallback_from_incremental: bool = False

        # 扫描状态
        self._scan_state: str = STATE_SETUP  # setup / scanning / results
        self._cancelling: bool = False
        self._is_paused: bool = False
        # 后台恢复扫描结果的加载态
        self._restoring: bool = False

        # 进度信息
        self._progress_scanned: int = 0
        self._progress_total: int = 0
        self._progress_indeterminate: bool = False
        self._current_file: str = ""
        # 当前文件单文件元信息（scan 阶段填入，walk/archive 阶段为 0/""/0.0）
        self._current_file_size: int = 0
        self._current_file_ext: str = ""
        self._current_file_elapsed_ms: float = 0.0
        self._status_summary: str = STR_STATUS_READY
        self._status_text: str = STR_STATUS_READY
        self._passed_count: int = 0
        self._matched_count: int = 0
        self._skipped_count: int = 0
        self._error_count: int = 0
        # 压缩包内条目数（含在 scanned 中，单独暴露供 UI 注明）
        self._archive_entry_count: int = 0
        # 增量扫描统计——未变更文件复用数与实际变更扫描数
        self._reused_files: int = 0
        # 阶段独立进度（双进度条）：
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

        # 规则配置——self._ruleset 缓存 effective ruleset
        # （任务级 rules_paths/use_builtin 覆盖优先，回退全局 RulesController.ruleset）。
        # 启动时计算一次占位，startScan 时再次计算最新（保证规则变更立即生效）。
        self._ruleset = self._compute_effective_ruleset()
        # 全局 Config 变更时同步 emit effectiveConfigChanged，
        # 让 QML effective* 绑定（如 effectiveMaxWorkers）跟随全局配置更新。
        # 任务级 override 变更由 setTaskOverride 内部 emit，不在此处处理。
        self._config_controller.configChanged.connect(  # pyrefly: ignore [missing-attribute]
            self._emit_effective_config_changed
        )
        # 规则集变更时同步 emit canStartScanChanged/rulesCountChanged，
        # 避免 canStartScan/rulesCount 读到陈旧缓存值导致规则加载后仍无法启动扫描。
        self._rules_controller.rulesetChanged.connect(  # pyrefly: ignore [missing-attribute]
            self._on_ruleset_changed
        )

    # ----------------------------- 扫描状态 -----------------------------

    @Property(str, notify=scanStateChanged)  # pyrefly: ignore [not-callable]
    def scanState(self) -> str:
        """扫描状态（setup/scanning/results）。"""
        return self._scan_state

    @Property(bool, notify=restoringChanged)  # pyrefly: ignore [not-callable]
    def restoring(self) -> bool:
        """是否正在后台恢复扫描结果。

        QML 据此显示「正在恢复扫描结果...」占位态，加载完成后无缝切换到结果列表。
        """
        return self._restoring

    @Property(bool, notify=scanStateChanged)  # pyrefly: ignore [not-callable]
    def isPaused(self) -> bool:
        """是否暂停中。"""
        return self._is_paused

    @Property(bool, notify=phaseChanged)  # pyrefly: ignore [not-callable]
    def cancelling(self) -> bool:
        """是否正在取消扫描中。

        cancelScan 设置为 True，_reset_scan_ui 重置为 False（取消完成回调）。
        QML 据此显示模态遮罩防止用户重复操作（与退出保存 Popup 同模式）。
        notify 用 phaseChanged：取消与重置均伴随 phaseChanged emit。
        """
        return self._cancelling

    @Property(bool, notify=canStartScanChanged)  # pyrefly: ignore [not-callable]
    def canStartScan(self) -> bool:
        """是否可开始扫描（effective 规则集已加载 + 目标已选）。

        读 ``self._ruleset``（effective ruleset 缓存，任务级覆盖优先回退全局），
        规则加载/移除/任务级覆盖变更后由 ``_on_ruleset_changed``/``setTaskOverride``
        重算并 emit ``canStartScanChanged`` 触发重算。
        """
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

    @Property(int, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def progressScanned(self) -> int:
        """已扫描文件数。"""
        return self._progress_scanned

    @Property(int, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def progressTotal(self) -> int:
        """总文件数。"""
        return self._progress_total

    @Property(float, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def progress(self) -> float:
        """进度百分比（0-100）。

        ``progressTotal <= 0`` 时返回 0（避免除零导致 NaN）。
        扫描进行中按 ``progressScanned / progressTotal * 100`` 计算；
        扫描完成后（``scanDone=True``）固定返回 100，确保进度条与
        「已完成」状态文字对应（修复：scan 阶段完成后 ``progressScanned``
        可能因错误文件未计入而小于 ``progressTotal``，导致进度条未满）。
        """
        if self._scan_done:
            return 100.0
        if self._progress_total <= 0:
            return 0.0
        return min(100.0, self._progress_scanned * 100.0 / self._progress_total)

    @Property(bool, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def progressIndeterminate(self) -> bool:
        """进度条是否为不确定模式。"""
        return self._progress_indeterminate

    @Property(str, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def currentFile(self) -> str:
        """当前正在扫描的文件路径。"""
        return self._current_file

    @Property(int, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def currentFileSize(self) -> int:
        """当前文件大小（字节）。scan 阶段填入，walk/archive 阶段为 0。"""
        return self._current_file_size

    @Property(str, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def currentFileExt(self) -> str:
        """当前文件扩展名（小写无点，如 ``"pdf"``）。scan 阶段填入，其余为空串。"""
        return self._current_file_ext

    @Property(float, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def currentFileElapsedMs(self) -> float:
        """当前文件已解析耗时（毫秒）。scan 阶段填入，其余为 0.0。"""
        return self._current_file_elapsed_ms

    @Property(str, notify=phaseChanged)  # pyrefly: ignore [not-callable]
    def statusSummary(self) -> str:
        """状态栏摘要文本。"""
        return self._status_summary

    @Property(int, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def passedCount(self) -> int:
        """已通过文件数。"""
        return self._passed_count

    @Property(int, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def matchedCount(self) -> int:
        """命中文件数。"""
        return self._matched_count

    @Property(int, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def skippedCount(self) -> int:
        """跳过文件数。"""
        return self._skipped_count

    @Property(int, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def errorCount(self) -> int:
        """错误文件数。"""
        return self._error_count

    @Property(int, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def archiveEntryCount(self) -> int:
        """压缩包内条目数（含在 scanned 中）。

        用于 UI 注明"扫描 N"中包含的压缩包内条目数，
        避免 ``scanned > total_files`` 时产生误解。
        """
        return self._archive_entry_count

    @Property(int, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def reusedFiles(self) -> int:
        """增量扫描：未变更直接复用上次结果的文件数。

        全量扫描时为 0；增量扫描越大，此值越接近 ``progressTotal``。
        """
        return self._reused_files

    @Property(int, notify=scanProgressChanged)  # pyrefly: ignore [not-callable]
    def changedFiles(self) -> int:
        """增量扫描：实际发生内容变更、重新做了 I/O 与规则匹配的文件数。

        等于 ``progressScanned``（不含复用未变更文件）与压缩包内条目
        之差的下限为 0（archive_entries 含在 scanned 中）。
        """
        return max(0, self._progress_scanned - self._archive_entry_count)

    # ----------------------------- 阶段与收集进度（双进度条） -----------------------------

    @Property(str, notify=phaseChanged)  # pyrefly: ignore [not-callable]
    def scanPhase(self) -> str:
        """当前扫描阶段。

        - ``"setup"``：未开始
        - ``"walk"``：收集文件清单（FileStatsWorker 运行中）
        - ``"scan"``：解析文件内容（ScanWorker 主阶段）
        - ``"archive"``：扫描压缩包内条目
        - ``"done"``：全部完成
        """
        return self._scan_phase

    @Property(int, notify=walkProgressChanged)  # pyrefly: ignore [not-callable]
    def walkDiscovered(self) -> int:
        """walk 阶段已发现的文件总数（持续增长，含跳过项）。"""
        return self._walk_discovered

    @Property(int, notify=walkProgressChanged)  # pyrefly: ignore [not-callable]
    def walkSkipped(self) -> int:
        """walk 阶段按白名单跳过的文件数（未勾选的扩展名）。"""
        return self._walk_skipped

    @Property(int, notify=walkProgressChanged)  # pyrefly: ignore [not-callable]
    def walkUserSkipped(self) -> int:
        """walk 阶段用户标记跳过的文件数。"""
        return self._walk_user_skipped

    @Property(int, notify=walkProgressChanged)  # pyrefly: ignore [not-callable]
    def walkClassified(self) -> int:
        """walk 阶段收集到的符合文件类型的文件数（实际进入扫描阶段的文件数）。

        计算：``walkDiscovered - walkSkipped - walkUserSkipped``，下界为 0。
        用于统计 UI 展示「符合类型 N」。
        """
        classified = self._walk_discovered - self._walk_skipped - self._walk_user_skipped
        return max(0, classified)

    @Property(bool, notify=walkProgressChanged)  # pyrefly: ignore [not-callable]
    def walkIndeterminate(self) -> bool:
        """walk 阶段进度条是否为不确定模式（刚启动尚未收到首个进度）。"""
        return self._walk_indeterminate

    @Property(bool, notify=phaseChanged)  # pyrefly: ignore [not-callable]
    def walkDone(self) -> bool:
        """walk 阶段是否已完成（用于 UI 标记收集进度条为完成态）。"""
        return self._walk_done

    @Property(bool, notify=phaseChanged)  # pyrefly: ignore [not-callable]
    def scanDone(self) -> bool:
        """scan 阶段是否已完成（用于 UI 标记解析进度条为完成态）。"""
        return self._scan_done

    @Property(float, notify=walkProgressChanged)  # pyrefly: ignore [not-callable]
    def walkProgress(self) -> float:
        """walk 阶段进度百分比（0-100）。

        walk 阶段无确定的 ``total``（文件随遍历持续发现），用 ``discovered`` 自身
        作为分母计算"已发现并分类"的占比：``(discovered - skipped - user_skipped) / discovered``。
        ``discovered == 0`` 时返回 0（避免除零）。

        walk 完成后（``walkDone=True``）固定返回 100，确保进度条与「已完成」
        状态文字对应（修复：walk 完成后若有白名单跳过文件，
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
        """扫描模式索引（0=盘符 / 1=文件夹）。"""
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

    # ----------------------------- 任务级配置覆盖 -----------------------------

    @Slot(str, object)  # pyrefly: ignore [not-callable]
    def setTaskOverride(self, key: str, value: object) -> None:
        """设置任务级配置覆盖。

        :param key: Config 字段名（``scan_archives``/``max_workers``/
            ``max_file_size``/``max_depth``/``ignore_dirs``/``rules_paths``/
            ``use_builtin``/``temp_rules_paths``/``disabled_temp_rules_paths``）
        :param value: 覆盖值（类型须与 Config 字段一致）

        覆盖值在 :meth:`_effective_scan_archives`/`_effective_max_workers` 等
        方法中优先读取，未设置时回退到全局 :attr:`_config`。

        影响 QML effective* 绑定的字段（``max_workers``/``max_file_size``/
        ``max_depth``）变更时 emit :attr:`effectiveConfigChanged`，让
        ``effectiveMaxWorkers``/``effectiveMaxFileSizeMB``/``effectiveMaxDepth``
        绑定重算。

        ``rules_paths``/``use_builtin``/``temp_rules_paths``/
        ``disabled_temp_rules_paths`` 变更时重算 effective ruleset 缓存并
        emit ``canStartScanChanged``/``rulesCountChanged``，让 QML 侧
        ``canStartScan``/``rulesCount`` 绑定反映任务级规则集。
        """
        self._task_overrides[key] = value
        if key in ("max_workers", "max_file_size", "max_depth", "scan_archives", "ignore_dirs"):
            self.effectiveConfigChanged.emit()  # pyrefly: ignore [missing-attribute]
        elif key in ("rules_paths", "use_builtin", "temp_rules_paths", "disabled_temp_rules_paths"):
            self._ruleset = self._compute_effective_ruleset()
            self.canStartScanChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.rulesCountChanged.emit()  # pyrefly: ignore [missing-attribute]

    def clearTaskOverride(self, key: str) -> None:
        """清除任务级配置覆盖的指定字段（回退到规则集/全局值）。

        :param key: Config 字段名（如 ``scan_archives``/``max_workers``）

        已迁移字段（scan_archives/max_workers 等）清除后由 ``_effective_*``
        方法回退到 :attr:`_ruleset` 读取；``rules_paths``/``use_builtin`` 等
        保留字段清除后回退到 :attr:`_config`。
        """
        if key not in self._task_overrides:
            return
        self._task_overrides.pop(key, None)
        if key in ("max_workers", "max_file_size", "max_depth", "scan_archives", "ignore_dirs"):
            self.effectiveConfigChanged.emit()  # pyrefly: ignore [missing-attribute]
        elif key in ("rules_paths", "use_builtin", "temp_rules_paths", "disabled_temp_rules_paths"):
            self._ruleset = self._compute_effective_ruleset()
            self.canStartScanChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.rulesCountChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _emit_effective_config_changed(self) -> None:
        """configController.configChanged → effectiveConfigChanged 桥接。

        全局 Config 变更时 QML 侧 ``configController.maxWorkers`` 等绑定自行重算，
        但 ``ScanController.effectiveMaxWorkers`` 等需通过本信号触发重算。
        """
        self.effectiveConfigChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _on_ruleset_changed(self) -> None:
        """rulesController.rulesetChanged → 同步 effective ruleset 缓存与 QML 绑定信号。

        全局规则集变更时重算 ``self._ruleset``（effective ruleset：任务级覆盖
        优先，回退全局）。无任务级覆盖时直接取全局；有任务级覆盖时按覆盖
        配置重新加载（覆盖优先级高于全局变更）。

        emit ``canStartScanChanged`` 与 ``rulesCountChanged`` 让 QML 侧绑定重算。
        """
        self._ruleset = self._compute_effective_ruleset()
        self.canStartScanChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesCountChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _effective_scan_archives(self) -> bool:
        """任务级覆盖优先的 scan_archives。"""
        return effective_scan_archives(self._task_overrides, self._ruleset)

    def _effective_max_workers(self) -> int:
        """任务级覆盖优先的 max_workers。"""
        return effective_max_workers(self._task_overrides, self._ruleset)

    def _effective_max_file_size(self) -> int:
        """任务级覆盖优先的 max_file_size。"""
        return effective_max_file_size(self._task_overrides, self._ruleset)

    def _effective_max_depth(self) -> int | None:
        """任务级覆盖优先的 max_depth（None 表示不限深度）。

        ``0`` 归一化为 ``None``（无限深度），避免 walker 把 ``0`` 误解为
        「仅根目录直接子项」。
        """
        return effective_max_depth(self._task_overrides, self._ruleset)

    def _effective_ignore_dirs(self) -> tuple[str, ...]:
        """任务级覆盖优先的 ignore_dirs。"""
        return effective_ignore_dirs(self._task_overrides, self._ruleset)

    def _effective_scan_extensions(self) -> tuple[str, ...] | None:
        """effective ruleset 的 scan_extensions（None=全选默认）。

        :return: ruleset.scan_extensions；ruleset 为 None 时返回 None
            （由 ScanWorker/Scanner 回退到全部注册提取器扩展名）。
        """
        if self._ruleset is None:
            return None
        return self._ruleset.scan_extensions

    def _effective_rules_paths(self) -> tuple[str, ...]:
        """任务级覆盖优先的 rules_paths（不过滤不存在文件）。"""
        return effective_rules_paths(self._task_overrides, self._config)

    def _effective_use_builtin(self) -> bool:
        """任务级覆盖优先的 use_builtin。"""
        return effective_use_builtin(self._task_overrides, self._config)

    def _compute_effective_ruleset(self) -> RuleSet | None:
        """计算 effective ruleset（任务级覆盖优先 + 临时规则叠加）。

        规则集来源（按优先级合并）：
        1. 全局规则集（:attr:`_rules_controller.ruleset`，已过滤禁用的全局规则文件）
        2. 任务级 ``rules_paths``/``use_builtin`` 覆盖（覆盖全局规则配置时重新加载）
        3. 任务级 ``temp_rules_paths`` 临时规则（叠加在上述规则集之上，
           跳过 ``disabled_temp_rules_paths`` 中禁用的路径）

        无任务级覆盖且无临时规则时直接取全局 :attr:`_rules_controller.ruleset`。
        有 ``rules_paths``/``use_builtin`` 覆盖时按 effective 配置重新加载
        （内置 + 用户规则合并）。临时规则始终在最后叠加合并（禁用的临时规则
        不参与合并但仍保留在 ``temp_rules_paths`` 中以便重新启用）。

        :return: :class:`RuleSet` 实例；无可用规则（未勾选内置且无用户规则文件，
            或加载失败）时返回 ``None``
        """
        has_override = "rules_paths" in self._task_overrides or "use_builtin" in self._task_overrides
        disabled_temp = effective_disabled_temp_rules_paths(self._task_overrides)
        temp_paths = [
            Path(p)
            for p in effective_temp_rules_paths(self._task_overrides)
            if Path(p).exists() and p not in disabled_temp
        ]

        # 无任务级覆盖且无临时规则：直接取全局 ruleset
        if not has_override and not temp_paths:
            return self._rules_controller.ruleset

        # 计算基础规则集（内置 + 全局/任务级规则文件）
        if has_override:
            paths = [Path(p) for p in self._effective_rules_paths() if Path(p).exists()]
            use_builtin = self._effective_use_builtin()
            try:
                if use_builtin:
                    base: RuleSet | None = load_with_builtin(paths)
                elif paths:
                    rulesets = [load_ruleset(p) for p in paths]
                    base = merge_multiple_rulesets(*rulesets)
                else:
                    base = None
            except RuleError as exc:
                logger.warning("任务级规则集加载失败: %s", exc)
                return None
        else:
            base = self._rules_controller.ruleset

        # 无临时规则：直接返回基础规则集
        if not temp_paths:
            return base

        # 叠加临时规则
        try:
            temp_rulesets = [load_ruleset(p) for p in temp_paths]
            if base is not None:
                return merge_multiple_rulesets(base, *temp_rulesets)
            return merge_multiple_rulesets(*temp_rulesets)
        except RuleError as exc:
            logger.warning("临时规则集加载失败: %s", exc)
            return base

    @Property(int, notify=effectiveConfigChanged)  # pyrefly: ignore [not-callable]
    def effectiveMaxWorkers(self) -> int:
        """任务级覆盖优先的最大工作线程数。

        供 QML ScanProgressCard 显示实际生效的线程数（任务级 override 优先，
        回退到全局 Config）。变更通知走 :attr:`effectiveConfigChanged`：
        ``setTaskOverride("max_workers", ...)`` 与全局 Config 变更均会触发。
        """
        return self._effective_max_workers()

    @Property(int, notify=effectiveConfigChanged)  # pyrefly: ignore [not-callable]
    def effectiveMaxFileSizeMB(self) -> int:
        """任务级覆盖优先的最大文件大小（MB）。

        与 :attr:`effectiveMaxWorkers` 同模式：任务级 override 优先，回退全局。
        """
        return self._effective_max_file_size() // (1024 * 1024)

    @Property(int, notify=effectiveConfigChanged)  # pyrefly: ignore [not-callable]
    def effectiveMaxDepth(self) -> int:
        """任务级覆盖优先的最大扫描深度（0=无限）。

        与 :attr:`effectiveMaxWorkers` 同模式：任务级 override 优先，回退全局。
        ``None`` 归一化为 ``0`` 与 :meth:`ConfigController.maxDepth` 一致。
        """
        depth = self._effective_max_depth()
        return depth or 0

    @Property(int, notify=rulesCountChanged)  # pyrefly: ignore [not-callable]
    def rulesCount(self) -> int:
        """effective 规则集规则数。

        读 ``self._ruleset``（effective ruleset 缓存，任务级覆盖优先回退全局），
        与 :attr:`canStartScan` 同步确保规则变更后 UI 立即更新。
        """
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

    # ----------------------------- 过滤+排序 -----------------------------

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

        接受用户自定义替换文本 ``replace_with``（QML 输入框提供，
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

    # ----------------------------- 批量替换与撤销 -----------------------------

    def _resolve_backup_dir(self) -> Path:
        """解析当前生效的备份目录 Path。"""
        from fuscan.processing.storage import default_backup_dir

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

        接受用户自定义替换文本 ``replace_with``（QML 输入框提供，
        默认 ``...``）。非空时覆盖所有规则的 ``replace_with``，且不要求规则
        ``replace=True``。

        委托 :func:`fuscan.gui.controllers._batch_actions.replace_all_filtered_results`
        执行批量替换，返回 :class:`BatchReplaceResult.message` 供 QML 显示，并
        记录成功项的 ``(源, 备份)`` 配对供 :meth:`undoLastBatchReplace` 撤销。

        :param replace_with: 用户自定义替换文本（空字符串走规则驱动模式）
        :return: 操作消息字符串
        """
        msg, last_batch = replace_all_filtered_results(
            filtered=self._result_model.filtered_results,
            ruleset=self._ruleset,
            backup_dir=self._resolve_backup_dir(),
            scan_root=self._resolve_scan_root(),
            backup_preserve_relative=self._config.backup_preserve_relative_path,
            override_replace_with=replace_with if replace_with else None,
        )
        # 前置校验失败时 last_batch 为 None，保留既有撤销记录不清空
        if last_batch is not None:
            self._last_batch_backup_paths = last_batch
        return msg

    @Slot(result=str)  # pyrefly: ignore [not-callable]
    def undoLastBatchReplace(self) -> str:
        """撤销最近一次批量替换，从 ``.bak`` 备份恢复所有文件。

        委托 :func:`fuscan.gui.controllers._batch_actions.undo_last_batch_replace`
        按 ``(源, 备份)`` 配对从备份恢复到原源文件路径。无可撤销操作时返回提示。
        调用后清除撤销记录，避免重复撤销。

        :return: 操作消息字符串
        """
        summary = undo_last_batch_replace(self._last_batch_backup_paths)
        # 清除撤销记录，避免重复撤销（空记录清除为 no-op）
        self._last_batch_backup_paths = ()
        return summary

    @Slot(result=str)  # pyrefly: ignore [not-callable]
    def undoSelectedReplace(self) -> str:
        """撤销当前选中结果的最近一次替换（从 .bak 恢复）。

        委托 :func:`fuscan.gui.controllers._batch_actions.undo_selected_replace`，
        根据选中结果路径反推备份路径（``{src}.bak``）后恢复。

        :return: 操作消息字符串
        """
        return undo_selected_replace(
            result=self._get_selected_result(),
            backup_dir=self._resolve_backup_dir(),
            scan_root=self._resolve_scan_root(),
            backup_preserve_relative=self._config.backup_preserve_relative_path,
        )

    @Property(bool, notify=selectedResultChanged)  # pyrefly: ignore [not-callable]
    def canReplaceAllFiltered(self) -> bool:
        """是否可对过滤后结果执行批量替换。

        放宽条件——过滤后结果非空且至少一个结果可替换即可
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

        移至暂存成功后从 :class:`ResultListModel` 与
        ``_last_report.hits`` 中移除该条目，避免用户仍能在结果列表中
        看到已隔离的文件。选中索引重置为 -1，emit ``selectedResultChanged``
        让 QML 详情面板清空。

        - 未选中结果 → ``未选中结果``
        - 压缩包内部条目 → ``压缩包内部条目不支持移至暂存``
        - 复制成功 → ``已移至暂存: <隔离路径>`` 并标记跳过
        - 复制失败 → ``移至暂存失败: <错误>``
        """
        selected = self._get_selected_result()
        last_root = self._last_report.root if self._last_report is not None else None
        msg = move_to_staging(
            result=selected,
            staging_dir_str=self._config.staging_dir,
            last_report_root=last_root,
            skip_store=self._skip_store,
        )
        # 成功后从结果列表与 last_report 中移除该条目
        if msg.startswith("已移至暂存") and selected is not None:
            removed = self._result_model.remove_result_by_path(selected.path)
            if removed and self._last_report is not None:
                # 同步从 _last_report.hits 中移除，保持与结果模型一致
                target_str = str(selected.path)
                new_hits = tuple(h for h in self._last_report.hits if str(h.path) != target_str)
                self._last_report = ScanReport(
                    root=self._last_report.root,
                    results=new_hits,
                    stats=self._last_report.stats,
                    cancelled=self._last_report.cancelled,
                )
            # 重置选中索引，触发 QML 详情面板清空
            self._selected_result_index = -1
            self.selectedResultChanged.emit()  # pyrefly: ignore [missing-attribute]
        return msg

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def markAsFalsePositive(self, rule_filter: str = "") -> str:
        """将当前选中结果加入误报白名单。

        委托 :func:`fuscan.gui.controllers._batch_actions.mark_as_false_positive`
        校验选中结果并计算白名单条目字段，随后调用
        :meth:`WhitelistController.addEntry` 写入白名单。加入后调用
        :meth:`invalidate_manifest` 强制下次扫描为全量（白名单变更后增量扫描的
        prev_report 仍含误报命中，需重扫）。

        :param rule_filter: 指定规则名（精确匹配）；空字符串表示该文件全部命中
            规则均标记为误报（``*`` 通配）。默认空字符串。
        :return: 操作消息供 QML 显示

        返回值语义：

        - 未选中结果 → ``未选中结果``
        - 压缩包内部条目 → ``压缩包内部条目不支持标记误报``（路径含 ``!`` 无法 glob）
        - 成功 → ``已标记为误报: <路径> (<规则>)``
        """
        path_glob, rule_name, error_msg = mark_as_false_positive(
            result=self._get_selected_result(),
            rule_filter=rule_filter,
        )
        if error_msg is not None:
            return error_msg
        msg = self._whitelist_controller.addEntry(path_glob, rule_name, "")
        # 白名单变更需强制全量重扫，使本工作区下次扫描过滤误报
        if self._pending_ws_id:
            self.invalidate_manifest(self._pending_ws_id)
        return msg

    # ----------------------------- QML 调用槽 -----------------------------

    @Slot()  # pyrefly: ignore [not-callable]
    def startScan(self) -> None:
        """开始扫描（启动 stats worker → scan worker 串行）。"""
        # 提前读取并重置回退标志，确保所有提前返回路径都不会
        # 遗留 _fallback_from_incremental=True 导致下次全量扫描误合并旧 hits
        fallback = self._fallback_from_incremental
        self._fallback_from_incremental = False

        if self._scan_state == STATE_SCANNING:
            return
        # 每次扫描前重新计算 effective ruleset（任务级覆盖优先，回退全局），
        # 保证规则变更或任务级覆盖变更立即生效
        self._ruleset = self._compute_effective_ruleset()
        if self._ruleset is None:
            logger.warning("未加载规则集，无法开始扫描")
            return

        # 同步性能日志开关（ruleset.scan_params.perf_log_enabled）
        self._sync_perf_enabled()

        roots = self._build_scan_roots()
        if not roots:
            logger.warning("未选择有效扫描目标")
            return

        # 全量扫描不合并 prev_report（_pending_prev_report=None）。
        # 注意：不重置 _pending_ws_id——若由 startIncrementalScan 回退调用，
        # _pending_ws_id 已设置为工作区 ID，仍需持久化 manifest 以便下次增量扫描。
        # 若由 startIncrementalScan 回退调用（fallback=True），保留
        # _pending_prev_report 供 _on_scan_finished 在本次无命中时合并旧 hits。
        if not fallback:
            self._pending_prev_report = None

        self._result_model.clear()
        self._selected_result_index = -1
        self._cancelling = False
        self._is_paused = False
        self._set_scan_state(STATE_SCANNING)
        self._set_status("扫描中...", "准备统计...")
        # 阶段重置：进入 walk 阶段（双进度条）
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
        # 增量扫描统计重置
        self._reused_files = 0
        self._current_file = "准备统计..."
        # 单文件元信息重置
        self._current_file_size = 0
        self._current_file_ext = ""
        self._current_file_elapsed_ms = 0.0
        self.phaseChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.scanProgressChanged.emit()  # pyrefly: ignore [missing-attribute]

        # 阶段 1：FileStatsWorker 执行 walk 收集文件清单
        self._stats_worker = FileStatsWorker(
            ruleset=self._ruleset,
            roots=roots,
            scan_archives=self._effective_scan_archives(),
            max_depth=self._effective_max_depth(),
            ignore_dirs=self._effective_ignore_dirs(),
            scan_extensions=self._effective_scan_extensions(),
            skip_paths=self._skip_store.paths(),
        )
        self._stats_worker.progress_info.connect(self._on_scan_progress)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.finished_stats.connect(self._on_stats_finished)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.failed.connect(self._on_stats_failed)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.cancelled.connect(self._on_stats_cancelled)  # pyrefly: ignore [missing-attribute]
        self._stats_worker.start()

    @Slot(str)  # pyrefly: ignore [not-callable]
    def startIncrementalScan(self, ws_id: str) -> None:
        """启动增量扫描。

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
            # 标记回退，startScan 据此保留 _pending_prev_report，
            # _on_scan_finished 在本次无命中时合并旧 hits
            self._fallback_from_incremental = True
            self._pending_prev_report = prev_report  # 可能为 None，但保留供 _on_scan_finished 检查
            self.startScan()
            return

        if self._scan_state == STATE_SCANNING:
            return
        # 每次扫描前重新计算 effective ruleset（任务级覆盖优先，回退全局），
        # 保证规则变更或任务级覆盖变更立即生效
        self._ruleset = self._compute_effective_ruleset()
        if self._ruleset is None:
            logger.warning("未加载规则集，无法开始增量扫描")
            return

        # 同步性能日志开关（ruleset.scan_params.perf_log_enabled）
        self._sync_perf_enabled()

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
        # 阶段重置：进入 walk 阶段（双进度条）
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
        # 增量扫描统计重置
        self._reused_files = 0
        self._current_file = "准备统计..."
        # 单文件元信息重置
        self._current_file_size = 0
        self._current_file_ext = ""
        self._current_file_elapsed_ms = 0.0
        self.phaseChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.scanProgressChanged.emit()  # pyrefly: ignore [missing-attribute]

        # 阶段 1：FileStatsWorker 执行 walk 收集文件清单（传入 incremental_manifest 启用增量）
        self._stats_worker = FileStatsWorker(
            ruleset=self._ruleset,
            roots=roots,
            scan_archives=self._effective_scan_archives(),
            max_depth=self._effective_max_depth(),
            ignore_dirs=self._effective_ignore_dirs(),
            scan_extensions=self._effective_scan_extensions(),
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
        self.phaseChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.scanProgressChanged.emit()  # pyrefly: ignore [missing-attribute]
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

        压缩包内部条目（``archive_path`` 非 None）时定位到压缩包
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
            from PySide2.QtGui import QGuiApplication
        except ImportError:  # pragma: no cover
            from PySide6.QtGui import QGuiApplication  # pyrefly: ignore [missing-import]
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(str(result.path))
        self._set_status("已复制", "已复制路径到剪贴板")

    # ----------------------------- 内部槽（worker 信号） -----------------------------

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_scan_progress(self, info: ProgressInfo) -> None:
        """扫描实时进度回调（节流由 worker 内部完成）。

        根据 ``info.phase`` 分别更新 walk / filter / scan / archive 阶段的独立字段，
        使 QML 双进度条能分别反映收集与解析进度。

        信号拆分：walk 阶段 emit ``walkProgressChanged``，filter/scan/archive 阶段
        emit ``scanProgressChanged``，阶段切换 emit ``phaseChanged``，
        避免单一 ``progressChanged`` 触发 22+ 个 QML 绑定全量重算。

        filter 阶段属于「非 walk」分支——``scanned`` 复用为「已处理文件数」，
        ``total`` 为待筛选 entries 长度。filter→scan 切换时若 walk 未标记完成
        则补标记（防御性，正常流程 walk 已在 stats_finished 标记完成）。
        """
        if self._cancelling:
            return
        # 阶段切换：phase 变化时同步 _scan_phase
        phase_changed = info.phase != self._scan_phase
        if phase_changed:
            self._scan_phase = info.phase
            # walk → filter/scan/archive 切换时标记 walk 阶段完成
            # filter 阶段在 walk 之后，filter→scan 切换时若 walk 未标记则补标记
            if info.phase in (PHASE_FILTER, PHASE_SCAN, PHASE_ARCHIVE) and not self._walk_done:
                self._walk_done = True
                self._walk_indeterminate = False
        # walk 阶段：仅 discovered/skipped/user_skipped 增长，scanned/matched/errors 恒为 0
        if info.phase == PHASE_WALK:
            self._walk_indeterminate = False
            self._walk_discovered = info.total
            self._walk_skipped = info.skipped
            self._walk_user_skipped = info.user_skipped
        else:
            # filter/scan/archive 阶段：更新解析进度
            # filter 阶段 scanned 字段复用为「已处理文件数」，total 为 entries 长度；
            # scan/archive 阶段 scanned/total 为常规扫描进度
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
        # 单文件元信息同步（walk/filter/archive 阶段为 0/""/0.0）
        self._current_file_size = info.current_file_size
        self._current_file_ext = info.current_file_ext
        self._current_file_elapsed_ms = info.current_file_elapsed_ms
        self._status_summary = info.summary()
        # 细粒度信号：阶段切换 emit phaseChanged，walk/scan 各自 emit 专属信号
        if phase_changed:
            self.phaseChanged.emit()  # pyrefly: ignore [missing-attribute]
        if info.phase == PHASE_WALK:
            self.walkProgressChanged.emit()  # pyrefly: ignore [missing-attribute]
        else:
            self.scanProgressChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_stats_finished(self, results: list[WalkResult]) -> None:
        """stats worker 完成：标记 walk 阶段完成，构造带 precollected 的 ScanWorker 启动 scan 阶段。"""
        # 在 cleanup 前读取本次构建的 manifest（_cleanup_stats_worker 置空 _stats_worker）
        if self._stats_worker is not None:
            self._pending_manifest = self._stats_worker.manifest
        self._cleanup_stats_worker()
        # walk 阶段完成：从最终 WalkResult 同步收集统计（双进度条）
        total_discovered = sum(wr.total for wr in results)
        total_skipped = sum(wr.skipped for wr in results)
        total_user_skipped = sum(wr.user_skipped for wr in results)
        # 增量扫描——未变更文件复用数从 WalkResult.unchanged_count 累加
        total_reused = sum(wr.unchanged_count for wr in results)
        self._walk_discovered = total_discovered
        self._walk_skipped = total_skipped
        self._walk_user_skipped = total_user_skipped
        self._reused_files = total_reused
        self._walk_done = True
        self._walk_indeterminate = False
        # scan 阶段总文件数 = walk 收集的 entries 总数（不含跳过项）
        self._progress_total = sum(len(wr.entries) for wr in results)
        self._progress_indeterminate = False
        self._scan_phase = PHASE_SCAN
        self.phaseChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.scanProgressChanged.emit()  # pyrefly: ignore [missing-attribute]
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
            scan_extensions=self._effective_scan_extensions(),
            skip_paths=self._skip_store.paths(),
            precollected=results,
            # 传入上次报告供 Scanner 合并未变更文件命中结果
            # （_pending_prev_report 由 startScan 置 None 或 startIncrementalScan 设置）
            prev_report=self._pending_prev_report,
            # 传入白名单快照，Scanner 在命中聚合阶段过滤误报
            whitelist=self._whitelist_controller.snapshot(),
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

        增量扫描回退全量时，若本次无命中但 ``_pending_prev_report``
        有 hits，将旧 hits 合并到 results 中。回退全量因 ``_unchanged_count=0``
        导致 Scanner 层合并条件不满足，此处做 controller 层补救，避免用户
        在运行时丢失之前扫描的结果。

        先切换扫描状态到 results/setup 与「已完成」状态文本，让 UI
        状态机立即跳出"扫描中"；再执行 ``set_results``/``_sync_stats_from_report``
        /``_save_manifest`` 等耗时操作。状态切换在前确保 Qt 信号 emit 后 QML
        绑定可立即重算（虽然实际重绘需等事件循环），避免 set_results 大结果集
        阻塞时 UI 状态机仍停在 STATE_SCANNING。
        """
        # 增量回退全量无命中时合并上次 hits
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
        # 标记 scan 阶段完成（双进度条）+ 切换 phase，让进度条满格
        self._scan_done = True
        self._scan_phase = PHASE_DONE
        # 先清理 worker 引用并复位 cancelling/paused 标志
        self._reset_scan_ui()
        # 立即 emit 状态文本与扫描状态：让 QML 状态机先切到"已完成/已取消"
        # 后续 set_results 等耗时操作即使阻塞主线程，Qt 信号已 emit，
        # QML 绑定在事件循环恢复后立即重算到正确状态
        summary = report.summary()
        speed = report.stats.speed
        if speed > 0:
            summary += f" | 速度 {speed:.0f} 文件/s"
        self._set_status(STR_STATUS_DONE if not report.cancelled else STR_STATUS_CANCELLED, summary)
        self._set_scan_state(STATE_RESULTS if report.hits else STATE_SETUP)
        # 耗时收尾：结果模型填充 + 统计同步 + manifest 持久化
        self._result_model.set_results(report.hits)
        self._sync_stats_from_report(report)
        # 持久化本次构建的 manifest（仅 startIncrementalScan 设置了 _pending_ws_id）
        if self._pending_ws_id and self._pending_manifest is not None:
            self._save_manifest(self._pending_ws_id, self._pending_manifest)
        # 细粒度信号：让统计页/进度条读取最新数值
        self.phaseChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.scanProgressChanged.emit()  # pyrefly: ignore [missing-attribute]

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
        # 最终未变更文件复用数以 ScanReport.stats.unchanged_files 为准
        self._reused_files = stats.unchanged_files

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
        """构造扫描缓存上下文（使用 effective 规则路径与内置开关）。

        缓存开关读 :attr:`_ruleset.scan_params.cache_enabled`：仅当显式为
        ``False`` 时禁用缓存；``None``（未设置）或 ``True`` 时启用（默认 True）。
        ruleset 为 None 时禁用缓存。
        """
        if self._ruleset is None:
            return None, None
        sp = self._ruleset.scan_params
        if sp is not None and sp.cache_enabled is False:
            return None, None
        if self._cache is None:
            from fuscan.cache import CacheStore, default_cache_path

            cache_path = Path(self._config.cache_path) if self._config.cache_path else default_cache_path()
            self._cache = CacheStore(cache_path)
        from fuscan.cache import compute_source_files

        # 使用 effective rules_paths/use_builtin（任务级覆盖优先，回退全局）
        effective_paths = [Path(p) for p in self._effective_rules_paths() if Path(p).exists()]
        source_files = compute_source_files(
            effective_paths,
            use_builtin=self._effective_use_builtin(),
        )
        return self._cache, source_files

    def _sync_perf_enabled(self) -> None:
        """同步性能日志开关到 :mod:`fuscan.perf`。

        读 :attr:`_ruleset.scan_params.perf_log_enabled`：仅当显式为 ``True``
        时启用性能日志；``None``（未设置）或 ``False`` 时关闭（默认关闭）。
        ruleset 为 None 时关闭。
        """
        from fuscan.perf import set_perf_enabled

        perf_enabled = (
            self._ruleset is not None
            and self._ruleset.scan_params is not None
            and self._ruleset.scan_params.perf_log_enabled is True
        )
        set_perf_enabled(perf_enabled)

    # ----------------------------- 增量扫描清单持久化 -----------------------------

    def _load_manifest(self, ws_id: str) -> IncrementalManifest | None:
        """从 ``~/.fuscan/manifests/<ws_id>.json`` 加载增量扫描清单。

        委托 :func:`fuscan.gui.controllers._manifest.load_manifest`。

        :param ws_id: 工作区 ID
        :return: :class:`IncrementalManifest` 实例；文件不存在或解析失败返回 ``None``
        """
        return _load_manifest_fn(ws_id, _MANIFESTS_DIR)

    def _save_manifest(self, ws_id: str, manifest: IncrementalManifest) -> None:
        """持久化增量扫描清单到 ``~/.fuscan/manifests/<ws_id>.json``。

        委托 :func:`fuscan.gui.controllers._manifest.save_manifest`。

        :param ws_id: 工作区 ID
        :param manifest: 本次扫描构建的新清单
        """
        _save_manifest_fn(ws_id, manifest, _MANIFESTS_DIR)

    def invalidate_manifest(self, ws_id: str) -> None:
        """删除工作区的增量扫描清单。

        委托 :func:`fuscan.gui.controllers._manifest.invalidate_manifest`。规则变更
        （新增/修改/删除/导入规则）时由 :meth:`WorkspaceController.updateWorkspaceRules`
        调用，使下次增量扫描因 manifest 不存在而回退为全量扫描，确保新规则被实际执行。
        """
        _invalidate_manifest(ws_id, _MANIFESTS_DIR)

    def _get_selected_result(self) -> ScanResult | None:
        """获取当前选中的 :class:`ScanResult`。"""
        return self._result_model.get_result(self._selected_result_index)

    def build_history_entry(self, workspace_id: str, workspace_name: str) -> ScanHistoryEntry | None:
        """从最近一次 :class:`ScanReport` 构建扫描历史条目。

        委托 :func:`fuscan.gui.controllers._history.build_history_entry`。在扫描
        完成/取消后由 :class:`WorkspaceController` 调用，将本次扫描关键指标归档到
        :class:`fuscan.history.HistoryStore`。无 ``_last_report`` 时返回 ``None``。

        :param workspace_id: 工作区 ID
        :param workspace_name: 工作区名称快照
        :return: :class:`fuscan.history.ScanHistoryEntry` 或 ``None``
        """
        return _build_history_entry(
            report=self._last_report,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            status_summary=self._status_summary,
        )

    def _set_restoring(self, value: bool) -> None:
        """设置后台恢复加载态（由 WorkspaceController 调用）。"""
        if self._restoring != value:
            self._restoring = value
            self.restoringChanged.emit()  # pyrefly: ignore [missing-attribute]

    def restoreFromReport(self, report: ScanReport) -> None:
        """从持久化的 :class:`ScanReport` 恢复扫描结果。

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
        """设置状态文本（同时更新 summary）。

        statusSummary 已迁移到 phaseChanged 信号，不再在此处 emit progressChanged，
        避免状态文本变更触发全量进度绑定重算。调用方如需通知进度/阶段变更，
        应自行 emit 对应信号。
        """
        self._status_text = text
        if summary is not None:
            self._status_summary = summary
        self.statusChanged.emit()  # pyrefly: ignore [missing-attribute]

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
        # 单文件元信息重置（扫描结束后不再展示）
        self._current_file_size = 0
        self._current_file_ext = ""
        self._current_file_elapsed_ms = 0.0
        self._cleanup_workers()
        self.phaseChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.scanProgressChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _cleanup_stats_worker(self) -> None:
        """清理 stats worker，非阻塞。

        不 ``wait()`` 线程，避免主线程冻结。stats worker 在 emit
        ``finished_stats``/``cancelled``/``failed`` 后 ``run()`` 即将退出，
        ``deleteLater`` 由 Qt 在 ``finished`` 信号后处理，对象生命周期安全。
        """
        if self._stats_worker is None:
            return
        try:
            self._stats_worker.progress_info.disconnect(self._on_scan_progress)  # pyrefly: ignore [missing-attribute]
            self._stats_worker.finished_stats.disconnect(self._on_stats_finished)  # pyrefly: ignore [missing-attribute]
            self._stats_worker.failed.disconnect(self._on_stats_failed)  # pyrefly: ignore [missing-attribute]
            self._stats_worker.cancelled.disconnect(self._on_stats_cancelled)  # pyrefly: ignore [missing-attribute]
        except RuntimeError:
            # 信号已断开或从未连接，忽略
            pass
        self._stats_worker.deleteLater()
        self._stats_worker = None

    def _cleanup_workers(self) -> None:
        """清理所有 worker（stats + scan），非阻塞。

        取消/完成/失败回调路径上调用，禁止 ``wait()`` 阻塞主线程——
        worker 在 emit 终结信号后 ``run()`` 即将退出，主线程 ``wait(2000)``
        会冻结 UI 2 秒（用户反馈"取消卡死"根因）。改为 disconnect 信号 +
        ``deleteLater``，让 Qt 在 worker ``finished`` 信号后回收对象；
        worker 仍持有 ``_scanner``/``_cache`` 引用由 GC 在 ``run()`` 退出后
        自然释放，``_cache`` 内部 RLock 保护并发访问安全。

        ``cleanup``/``quick_cancel``（窗口关闭/退出）路径仍走 ``wait`` + terminate
        强制清理，本方法仅用于扫描终结回调路径。
        """
        if self._worker is not None:
            try:
                self._worker.progress_info.disconnect(self._on_scan_progress)  # pyrefly: ignore [missing-attribute]
                self._worker.finished_report.disconnect(self._on_scan_finished)  # pyrefly: ignore [missing-attribute]
                self._worker.failed.disconnect(self._on_scan_failed)  # pyrefly: ignore [missing-attribute]
                self._worker.cancelled.disconnect(self._on_scan_cancelled)  # pyrefly: ignore [missing-attribute]
            except RuntimeError:
                # 信号已断开或从未连接，忽略
                pass
            self._worker.deleteLater()
            self._worker = None
        self._cleanup_stats_worker()

    def cleanup(self) -> None:
        """窗口关闭时清理资源（worker + cache）。

        worker wait 超时从 3000ms 降至 500ms，避免多工作区关闭时
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

        cancel + 短暂 wait(500ms) + terminate 后备。
        原实现仅设 cancel 标志不 wait，导致 QThread 在后台继续运行，
        阻止进程退出（用户看到"界面退出后后台一直还在"）。
        现改为：cancel 后 wait 最多 500ms（大部分 worker < 100ms 退出），
        超时则 terminate 强制终止。

        修复：原实现不 close SQLite（注释说"进程退出由 OS 回收"），
        但 workspace_controller.cleanup 用 quick_cancel 而非 cleanup，导致
        cache.close() 永不被调用，WAL 文件无限膨胀（cache.db
        15.7GB 根因）。现改为：cancel + wait + terminate + deleteLater 统一模式，
        末尾启动 daemon thread 异步关闭 cache，避免主线程阻塞（SQLite WAL
        checkpoint 可能慢），同时消除 quick_cancel/cleanup 路径不一致。
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(200)
            if self._worker.isRunning():
                self._worker.terminate()
                self._worker.wait(100)
            self._worker.deleteLater()
            self._worker = None
        if self._stats_worker is not None and self._stats_worker.isRunning():
            self._stats_worker.cancel()
            self._stats_worker.wait(200)
            if self._stats_worker.isRunning():
                self._stats_worker.terminate()
                self._stats_worker.wait(100)
            self._stats_worker.deleteLater()
            self._stats_worker = None
        # 取消未完成的 FilterWorker
        self._result_model.cleanup()
        # 异步关闭 cache，避免 WAL 文件膨胀
        # 用 daemon thread 避免 cache.close() 阻塞主线程（SQLite WAL checkpoint
        # 可能慢）；cache 实例设为 None 避免重复关闭，daemon thread 进程退出时
        # 由 OS 回收（若 close 未完成）
        self._close_cache_async()

    def _close_cache_async(self) -> None:
        """启动 daemon thread 异步关闭 cache。

         避免在主线程（特别是退出路径）阻塞于 SQLite WAL checkpoint。
         cache.close() 内部有 RLock 保护，与已终止 worker 的残留访问竞争安全
        （worker 的 except 会捕获 sqlite3.Error）。
        """
        if self._cache is None:
            return
        cache = self._cache
        self._cache = None

        def _close() -> None:
            try:
                cache.close()
            except (sqlite3.Error, OSError):
                logger.warning("异步关闭缓存失败", exc_info=True)

        t = threading.Thread(target=_close, name="cache-close", daemon=True)
        t.start()
