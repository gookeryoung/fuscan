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

from fuscan.config import Config
from fuscan.gui.explorer import open_path_in_explorer
from fuscan.gui.models.result_model import ResultListModel
from fuscan.gui.severity_utils import severity_color_hex, severity_text
from fuscan.scanner import ScanReport
from fuscan.scanner.result import ProgressInfo, ScanResult, WalkResult
from fuscan.skip_store import SkipStore
from fuscan.workers import FileStatsWorker, ScanWorker

if TYPE_CHECKING:
    from fuscan.cache import CacheStore
    from fuscan.gui.controllers.config_controller import ConfigController
    from fuscan.gui.controllers.rules_controller import RulesController
    from fuscan.rules.model import RuleSet

__all__ = ["ScanController"]

logger = logging.getLogger(__name__)

# 扫描模式索引 ↔ Config.scan_mode 字符串映射
_SCAN_MODE_INDEX_TO_STR: tuple[str, ...] = ("full", "drive", "folder")
_SCAN_MODE_STR_TO_INDEX: dict[str, int] = {s: i for i, s in enumerate(_SCAN_MODE_INDEX_TO_STR)}


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

    def __init__(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        parent: QObject | None = None,
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
        self._skip_store: SkipStore = SkipStore()
        self._result_model: ResultListModel = ResultListModel(self)

        # 扫描状态
        self._scan_state: str = "setup"  # setup / scanning / results
        self._cancelling: bool = False
        self._is_paused: bool = False

        # 进度信息
        self._progress_scanned: int = 0
        self._progress_total: int = 0
        self._progress_indeterminate: bool = False
        self._current_file: str = ""
        self._status_summary: str = "就绪"
        self._status_text: str = "就绪"
        self._passed_count: int = 0
        self._matched_count: int = 0
        self._skipped_count: int = 0
        self._error_count: int = 0

        # 扫描目标
        self._scan_mode_index: int = _SCAN_MODE_STR_TO_INDEX.get(self._config.scan_mode, 2)
        self._selected_drive: str = self._config.last_drive or ""
        self._folder_root: str = ""
        if self._config_controller.scanPaths:
            self._folder_root = self._config_controller.scanPaths[0]

        # 选中结果
        self._selected_result_index: int = -1

        # 监听 rules_controller 规则集变化
        self._rules_controller.rulesetChanged.connect(self._on_ruleset_changed)  # pyrefly: ignore [missing-attribute]
        # 初始加载规则集
        self._on_ruleset_changed()

    # ----------------------------- 扫描状态 -----------------------------

    @Property(str, notify=scanStateChanged)  # pyrefly: ignore [not-callable]
    def scanState(self) -> str:
        """扫描状态（setup/scanning/results）。"""
        return self._scan_state

    @Property(bool, notify=scanStateChanged)  # pyrefly: ignore [not-callable]
    def isPaused(self) -> bool:
        """是否暂停中。"""
        return self._is_paused

    @Property(bool, notify=canStartScanChanged)  # pyrefly: ignore [not-callable]
    def canStartScan(self) -> bool:
        """是否可开始扫描（规则集已加载 + 目标已选）。"""
        if self._scan_state == "scanning":
            return False
        if self._ruleset is None:
            return False
        return self._can_build_roots()

    @Property(str, notify=statusChanged)  # pyrefly: ignore [not-callable]
    def statusText(self) -> str:
        """状态徽标文本。"""
        return self._status_text

    @Property(str, notify=statusChanged)  # pyrefly: ignore [not-callable]
    def statusBadgeColor(self) -> str:
        """状态徽标背景色。"""
        if self._scan_state == "scanning":
            return "#E8F5E9"
        if self._scan_state == "results":
            return "#E3F2FD" if self._matched_count > 0 else "#F0F0F0"
        return "#F0F0F0"

    @Property(str, notify=statusChanged)  # pyrefly: ignore [not-callable]
    def statusBadgeBorder(self) -> str:
        """状态徽标边框色。"""
        if self._scan_state == "scanning":
            return "#4CAF50"
        if self._scan_state == "results":
            return "#0366D6" if self._matched_count > 0 else "#CCC"
        return "#CCC"

    @Property(str, notify=statusChanged)  # pyrefly: ignore [not-callable]
    def statusBadgeText(self) -> str:
        """状态徽标文本色。"""
        if self._scan_state == "scanning":
            return "#2E7D32"
        if self._scan_state == "results":
            return "#0D47A1" if self._matched_count > 0 else "#888"
        return "#888"

    # ----------------------------- 进度 -----------------------------

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def progressScanned(self) -> int:
        """已扫描文件数。"""
        return self._progress_scanned

    @Property(int, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def progressTotal(self) -> int:
        """总文件数。"""
        return self._progress_total

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

    # ----------------------------- 扫描模式与目标 -----------------------------

    @Property(int, notify=scanModeChanged)  # pyrefly: ignore [not-callable]
    def scanModeIndex(self) -> int:
        """扫描模式索引（0=全盘 / 1=盘符 / 2=文件夹）。"""
        return self._scan_mode_index

    @Slot(int)  # pyrefly: ignore [not-callable]
    def setScanModeIndex(self, index: int) -> None:
        """设置扫描模式索引。"""
        if 0 <= index < len(_SCAN_MODE_INDEX_TO_STR) and index != self._scan_mode_index:
            self._scan_mode_index = index
            self._config.scan_mode = _SCAN_MODE_INDEX_TO_STR[index]
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

    @Property(int, notify=rulesCountChanged)  # pyrefly: ignore [not-callable]
    def rulesCount(self) -> int:
        """当前规则集规则数。"""
        return len(self._ruleset.rules) if self._ruleset is not None else 0

    @Property(QObject)  # pyrefly: ignore [not-callable]
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
    def detailHitsModel(self) -> list[dict[str, str]]:
        """选中结果的命中详情列表（QML 直接 ListView 绑定）。"""
        result = self._get_selected_result()
        if result is None:
            return []
        return [
            {
                "ruleName": hit.rule_name,
                "severityText": severity_text(hit.severity),
                "severityColor": severity_color_hex(hit.severity),
                "context": hit.detail,
            }
            for hit in result.hits
        ]

    # ----------------------------- QML 调用槽 -----------------------------

    @Slot()  # pyrefly: ignore [not-callable]
    def startScan(self) -> None:
        """开始扫描（启动 stats worker → scan worker 串行）。"""
        if self._scan_state == "scanning":
            return
        if self._ruleset is None:
            logger.warning("未加载规则集，无法开始扫描")
            return

        roots = self._build_scan_roots()
        if not roots:
            logger.warning("未选择有效扫描目标")
            return

        self._result_model.clear()
        self._selected_result_index = -1
        self._cancelling = False
        self._is_paused = False
        self._set_scan_state("scanning")
        self._set_status("扫描中...", "准备统计...")
        self._progress_indeterminate = True
        self._progress_scanned = 0
        self._progress_total = 0
        self._passed_count = 0
        self._matched_count = 0
        self._skipped_count = 0
        self._error_count = 0
        self._current_file = "准备统计..."
        self.progressChanged.emit()  # pyrefly: ignore [missing-attribute]

        # 阶段 1：FileStatsWorker 执行 walk 收集文件清单
        self._stats_worker = FileStatsWorker(
            ruleset=self._ruleset,
            roots=roots,
            scan_archives=self._config.scan_archives,
            max_depth=self._config.max_depth,
            ignore_dirs=tuple(self._config.ignore_dirs),
            scan_extensions=self._config_controller.enabled_extensions(),
            skip_paths=self._skip_store.paths(),
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
            self._set_status("已暂停", "已暂停")
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
        """导出结果为 CSV/JSON 文件（路径由 QML FileDialog 选定后传入）。

        :param fmt: ``"csv"`` 或 ``"json"``
        :param path_str: 导出文件绝对路径
        """
        if self._last_report is None or not self._last_report.hits:
            return
        if not path_str:
            return
        try:
            content = self._last_report.to_format(fmt)
            Path(path_str).write_text(content, encoding="utf-8")
            self._set_status("已导出", f"已导出到 {path_str}")
        except OSError as exc:
            logger.warning("导出失败: %s", exc, exc_info=True)
            self._set_status("导出失败", f"导出失败: {exc}")

    @Slot()  # pyrefly: ignore [not-callable]
    def openLocation(self) -> None:
        """在文件管理器中打开选中结果文件位置。"""
        result = self._get_selected_result()
        if result is None:
            return
        try:
            open_path_in_explorer(result.path)
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
        """扫描实时进度回调（节流由 worker 内部完成）。"""
        if self._cancelling:
            return
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
        """stats worker 完成：构造带 precollected 的 ScanWorker 启动 scan 阶段。"""
        self._cleanup_stats_worker()
        cache, source_files = self._build_cache_context()
        assert self._ruleset is not None
        self._worker = ScanWorker(
            ruleset=self._ruleset,
            roots=[wr.root for wr in results],
            scan_archives=self._config.scan_archives,
            max_workers=self._config.max_workers,
            max_depth=self._config.max_depth,
            max_file_size=self._config.max_file_size,
            ignore_dirs=tuple(self._config.ignore_dirs),
            cache=cache,
            source_files=source_files,
            scan_extensions=self._config_controller.enabled_extensions(),
            skip_paths=self._skip_store.paths(),
            precollected=results,
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
        self._set_scan_state("setup")

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_stats_cancelled(self, results: list[WalkResult]) -> None:  # noqa: ARG002
        """stats 被取消：切回 setup。"""
        self._reset_scan_ui()
        self._set_status("已取消", "已取消统计")
        self._set_scan_state("setup")

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_scan_finished(self, report: ScanReport) -> None:
        """扫描完成：填充结果模型并切到 results 态。"""
        self._last_report = report
        self._result_model.set_results(report.hits)
        self._matched_count = len(report.hits)
        self._reset_scan_ui()
        summary = report.summary()
        speed = report.stats.speed
        if speed > 0:
            summary += f" | 速度 {speed:.0f} 文件/s"
        self._set_status("已完成" if not report.cancelled else "已取消", summary)
        self._set_scan_state("results" if report.hits else "setup")

    @Slot(str)  # pyrefly: ignore [not-callable]
    def _on_scan_failed(self, error: str) -> None:
        """扫描失败：切回 setup 并提示。"""
        self._reset_scan_ui()
        self._set_status("扫描失败", error)
        self._set_scan_state("setup")

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_scan_cancelled(self, report: ScanReport) -> None:
        """扫描被取消：有结果切 results，无结果切 setup。"""
        self._last_report = report
        self._result_model.set_results(report.hits)
        self._matched_count = len(report.hits)
        self._reset_scan_ui()
        self._set_status("已取消", report.summary())
        self._set_scan_state("results" if report.hits else "setup")

    # ----------------------------- 内部方法 -----------------------------

    def _on_ruleset_changed(self) -> None:
        """规则集变化：刷新本地缓存与 canStartScan。"""
        self._ruleset = self._rules_controller.ruleset
        self.rulesCountChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.canStartScanChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _can_build_roots(self) -> bool:
        """判断当前是否可构建扫描根路径列表。"""
        if self._scan_mode_index == 0:  # full
            return True
        if self._scan_mode_index == 1:  # drive
            return bool(self._selected_drive)
        return bool(self._folder_root)  # folder

    def _build_scan_roots(self) -> list[Path]:
        """构建扫描根路径列表。"""
        if self._scan_mode_index == 0:  # full
            from fuscan.scanner.walker import list_drives

            return list_drives(include_network=self._config.include_network_drives)
        if self._scan_mode_index == 1:  # drive
            return [Path(self._selected_drive)] if self._selected_drive else []
        # folder
        return [Path(self._folder_root)] if self._folder_root else []

    def _build_cache_context(self) -> tuple[CacheStore | None, dict[Path, str] | None]:
        """构造扫描缓存上下文。"""
        if not self._config.cache_enabled:
            return None, None
        if self._cache is None:
            from fuscan.cache import CacheStore, default_cache_path

            cache_path = Path(self._config.cache_path) if self._config.cache_path else default_cache_path()
            self._cache = CacheStore(cache_path)
        from fuscan.cache import compute_source_files

        source_files = compute_source_files(
            self._rules_controller.rules_paths,
            use_builtin=self._rules_controller.use_builtin,
        )
        return self._cache, source_files

    def _get_selected_result(self) -> ScanResult | None:
        """获取当前选中的 :class:`ScanResult`。"""
        return self._result_model.get_result(self._selected_result_index)

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
        """重置扫描 UI 到空闲状态。"""
        self._cancelling = False
        self._is_paused = False
        self._progress_indeterminate = False
        self._progress_scanned = 0
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
        """窗口关闭时清理资源（worker + cache）。"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        if self._stats_worker is not None and self._stats_worker.isRunning():
            self._stats_worker.cancel()
            self._stats_worker.wait(3000)
        self._cleanup_workers()
        if self._cache is not None:
            try:
                self._cache.close()
            except (sqlite3.Error, OSError):
                logger.warning("缓存关闭失败", exc_info=True)
            self._cache = None
