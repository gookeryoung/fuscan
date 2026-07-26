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
import shutil
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]

from fuscan.config import Config, default_backup_dir, detect_default_staging_dir
from fuscan.gui.explorer import open_path_in_explorer
from fuscan.gui.models.result_model import ResultListModel
from fuscan.gui.severity_utils import severity_color_hex, severity_text
from fuscan.replacer import ReplaceStatus, replace_in_file
from fuscan.scanner import ScanReport
from fuscan.scanner.result import ProgressInfo, ScanResult, WalkResult, format_size
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
        # 任务级配置覆盖（iter-104）：键为 Config 字段名，值为该任务专属覆盖值
        # 通过 _effective_<field>() 方法优先读取覆盖值，回退到全局 Config
        self._task_overrides: dict[str, object] = {}
        # 工作区专属规则集（iter-107 规则与工作区绑定）
        # 由 WorkspaceController.setWorkspaceRuleset 注入，本控制器不再依赖
        # 全局 RulesController.ruleset，避免工作区之间规则相互污染
        self._workspace_rules_paths: tuple[str, ...] = ()
        self._workspace_use_builtin: bool = True

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
        # 阶段独立进度（iter-105 双进度条）：
        # walk 阶段：discovered 持续增长，skipped/user_skipped 反映白名单与用户标记跳过
        # scan 阶段：scanned/total 反映解析进度，与上方 progressScanned/progressTotal 同步
        self._scan_phase: str = "setup"  # setup / walk / scan / archive / done
        self._walk_discovered: int = 0
        self._walk_skipped: int = 0
        self._walk_user_skipped: int = 0
        self._walk_indeterminate: bool = False
        self._walk_done: bool = False
        self._scan_done: bool = False

        # 扫描目标
        self._scan_mode_index: int = _SCAN_MODE_STR_TO_INDEX.get(self._config.scan_mode, 2)
        self._selected_drive: str = self._config.last_drive or ""
        self._folder_root: str = ""
        if self._config_controller.scanPaths:
            self._folder_root = self._config_controller.scanPaths[0]

        # 选中结果
        self._selected_result_index: int = -1

        # iter-107：ScanController 不再监听全局 RulesController.rulesetChanged，
        # ruleset 由 WorkspaceController.setWorkspaceRuleset 在工作区创建/规则
        # 更新时注入。为保证向后兼容（独立构造的 ScanController 仍可读取全局
        # ruleset），此处做一次性快照；WorkspaceController 注入后会覆盖。
        self._ruleset = self._rules_controller.ruleset
        # 同步初始 rules_paths/use_builtin（缓存上下文用）
        self._workspace_rules_paths = tuple(str(p) for p in self._rules_controller.rules_paths)
        self._workspace_use_builtin = self._rules_controller.use_builtin

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

    @Property(float, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def progress(self) -> float:
        """进度百分比（0-100）。

        ``progressTotal <= 0`` 时返回 0（避免除零导致 NaN）。
        扫描进行中按 ``progressScanned / progressTotal * 100`` 计算；
        扫描完成后保留最终值（``_reset_scan_ui`` 不重置 ``_progress_scanned``）。
        """
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
        """
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
        value = self._task_overrides.get("scan_archives")
        if isinstance(value, bool):
            return value
        return self._config.scan_archives

    def _effective_max_workers(self) -> int:
        """任务级覆盖优先的 max_workers。"""
        value = self._task_overrides.get("max_workers")
        if isinstance(value, int):
            return value
        return self._config.max_workers

    def _effective_max_file_size(self) -> int:
        """任务级覆盖优先的 max_file_size。"""
        value = self._task_overrides.get("max_file_size")
        if isinstance(value, int):
            return value
        return self._config.max_file_size

    def _effective_max_depth(self) -> int | None:
        """任务级覆盖优先的 max_depth（None 表示不限深度）。

        与 :meth:`fuscan.gui.controllers.config_controller.ConfigController.setMaxDepth`
        保持语义一致：``0`` 归一化为 ``None``（无限深度），避免 walker 把 ``0``
        误解为「仅根目录直接子项」。
        """
        value = self._task_overrides.get("max_depth")
        if isinstance(value, int):
            return value if value > 0 else None
        return self._config.max_depth

    def _effective_ignore_dirs(self) -> tuple[str, ...]:
        """任务级覆盖优先的 ignore_dirs。"""
        value = self._task_overrides.get("ignore_dirs")
        if isinstance(value, tuple):
            return value
        return tuple(self._config.ignore_dirs)

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
        result = self._get_selected_result()
        if result is None:
            return []
        return [
            {
                "ruleName": hit.rule_name,
                "severityText": severity_text(hit.severity),
                "severityColor": severity_color_hex(hit.severity),
                "context": hit.detail,
                "matchText": hit.match_text,
                "matchCount": hit.match_count,
                "target": hit.target,
                "description": hit.match_description,
            }
            for hit in result.hits
        ]

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
        result = self._get_selected_result()
        if result is None or self._ruleset is None or result.archive_path is not None:
            return False
        rule_map = {r.name: r for r in self._ruleset.rules}
        return any(rule_map.get(h.rule_name) is not None and rule_map[h.rule_name].replace for h in result.hits)

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

    @Slot(result=str)  # pyrefly: ignore [not-callable]
    def replaceSelectedResult(self) -> str:
        """替换当前选中结果的命中内容。

        调用 :func:`fuscan.replacer.replace_in_file` 执行备份 + 原子替换。
        返回操作消息供 QML 显示（成功/失败原因）。

        - 未选中结果 → ``未选中结果``
        - 规则集未加载 → ``规则集未加载``
        - 压缩包内部条目 → ``压缩包内部条目不支持替换``
        - 无 ``replace=True`` 规则 → ``未启用替换的规则``
        - 其他状态 → ``ReplaceResult.message``
        """
        result = self._get_selected_result()
        if result is None:
            return "未选中结果"
        if self._ruleset is None:
            return "规则集未加载"
        if result.archive_path is not None:
            return "压缩包内部条目不支持替换"

        backup_dir = Path(self._config.backup_dir) if self._config.backup_dir else default_backup_dir()
        scan_root = self._last_report.root if self._last_report is not None else result.path.parent

        replace_result = replace_in_file(
            src=result.path,
            hits=result.hits,
            ruleset=self._ruleset,
            backup_root=backup_dir,
            scan_root=scan_root,
            preserve_relative=self._config.backup_preserve_relative_path,
        )
        if replace_result.status == ReplaceStatus.SUCCESS:
            logger.info(
                "已替换 %s 中 %d 条规则命中，备份: %s",
                result.path,
                replace_result.replaced_count,
                replace_result.backup_path,
            )
            return replace_result.message or f"替换成功（{replace_result.replaced_count} 条）"
        logger.warning("替换失败: %s", replace_result.message)
        return replace_result.message or "替换失败"

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
        result = self._get_selected_result()
        if result is None:
            return "未选中结果"
        if result.archive_path is not None:
            return "压缩包内部条目不支持移至暂存"

        # 计算暂存区隔离目录
        staging_root = Path(self._config.staging_dir) if self._config.staging_dir else detect_default_staging_dir()
        quarantine_dir = staging_root / "quarantine"
        scan_root = self._last_report.root if self._last_report is not None else result.path.parent

        # 保留相对扫描根目录的目录结构
        try:
            rel_path = result.path.relative_to(scan_root)
        except ValueError:
            # 不在扫描根下（如绝对路径跨盘符），仅保留文件名
            rel_path = Path(result.path.name)

        dest = quarantine_dir / rel_path
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result.path, dest)
        except OSError as exc:
            logger.warning("移至暂存失败: %s -> %s", result.path, dest, exc_info=True)
            return f"移至暂存失败: {exc}"

        # 标记为跳过，后续扫描自动跳过该文件
        self._skip_store.add(str(result.path))
        logger.info("已移至暂存: %s -> %s（已标记跳过）", result.path, dest)
        return f"已移至暂存: {dest}（已标记跳过）"

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
        # 阶段重置：进入 walk 阶段（iter-105 双进度条）
        self._scan_phase = "walk"
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
            if info.phase in ("scan", "archive") and not self._walk_done:
                self._walk_done = True
                self._walk_indeterminate = False
        # walk 阶段：仅 discovered/skipped/user_skipped 增长，scanned/matched/errors 恒为 0
        if info.phase == "walk":
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
        self._scan_phase = "scan"
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
        # 从 report.stats 同步最终统计，确保扫描完成后统计页展示正确数值
        self._sync_stats_from_report(report)
        # 标记 scan 阶段完成（iter-105 双进度条）
        self._scan_done = True
        self._scan_phase = "done"
        self._reset_scan_ui()
        summary = report.summary()
        speed = report.stats.speed
        if speed > 0:
            summary += f" | 速度 {speed:.0f} 文件/s"
        self._set_status("已完成" if not report.cancelled else "已完成[用户取消]", summary)
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
        self._sync_stats_from_report(report)
        # 取消时标记 scan 阶段完成（避免进度条卡在中间）
        self._scan_done = True
        self._scan_phase = "done"
        self._reset_scan_ui()
        self._set_status("已完成[用户取消]", report.summary())
        self._set_scan_state("results" if report.hits else "setup")

    # ----------------------------- 内部方法 -----------------------------

    @Slot(object, list, bool)  # pyrefly: ignore [not-callable]
    def setWorkspaceRuleset(
        self,
        ruleset: object | None,
        rules_paths: list[str],
        use_builtin: bool,
    ) -> None:
        """注入工作区专属 :class:`RuleSet`（iter-107 规则与工作区绑定）。

        :param ruleset: 工作区专属 RuleSet 实例（加载失败为 ``None``）
        :param rules_paths: 工作区规则文件路径列表
        :param use_builtin: 是否启用内置规则

        由 :class:`WorkspaceController` 在工作区创建与 :meth:`update_workspace_rules`
        调用时注入。本控制器据此刷新 ``_ruleset`` 与 ``rulesCount``/``canStartScan``，
        并在缓存上下文构建时使用工作区专属 rules_paths/use_builtin，避免依赖
        全局 RulesController 的全局规则集。
        """
        # 调用方（WorkspaceController）保证类型正确，此处直接赋值
        self._ruleset = ruleset  # type: ignore[assignment]
        self._workspace_rules_paths = tuple(str(p) for p in rules_paths)
        self._workspace_use_builtin = bool(use_builtin)
        self.rulesCountChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.canStartScanChanged.emit()  # pyrefly: ignore [missing-attribute]

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
        """构造扫描缓存上下文（iter-107：使用工作区专属规则路径与内置开关）。"""
        if not self._config.cache_enabled:
            return None, None
        if self._cache is None:
            from fuscan.cache import CacheStore, default_cache_path

            cache_path = Path(self._config.cache_path) if self._config.cache_path else default_cache_path()
            self._cache = CacheStore(cache_path)
        from fuscan.cache import compute_source_files

        # iter-107：使用工作区专属 rules_paths/use_builtin，避免依赖全局 RulesController
        workspace_paths = [Path(p) for p in self._workspace_rules_paths if Path(p).exists()]
        source_files = compute_source_files(
            workspace_paths,
            use_builtin=self._workspace_use_builtin,
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
