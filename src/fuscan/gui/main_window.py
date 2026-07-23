"""GUI 主窗口。

提供 GitHub Desktop 风格的布局：

1. 菜单栏（文件/扫描/视图/帮助）
2. 顶部操作区（QGroupBox 分组：扫描模式 / 规则 + 扫描按钮）
3. 列表区（QTabWidget 切换扫描结果/规则文件/扫描历史）
4. 详情区（操作栏 QStackedWidget 两态 + 主体 QStackedWidget 两态，非空态含备注与导出）
5. 底部进度条 + 状态栏（统计/当前文件）

设计要点：

- GitHub Desktop 风格：QGroupBox 分组操作区，状态栏承载扫描统计
- 扫描在 ScanWorker（QThread）中执行，避免阻塞 UI
- 结果以 QTreeView + QStandardItemModel 展示（rule-12 Model/View 架构）
- 详情区嵌入命中预览（文件信息+命中表+内容预览+命中导航+备注+导出）
"""

from __future__ import annotations

import enum
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QPoint, QUrl, Slot
    from PySide2.QtGui import (
        QDesktopServices,
        QKeySequence,
    )
    from PySide2.QtWidgets import (
        QAction,
        QApplication,
        QButtonGroup,
        QDialog,
        QFileDialog,
        QLabel,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QShortcut,
        QTextEdit,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QPoint, QUrl, Slot  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import (  # pyrefly: ignore [missing-import]
        QAction,
        QKeySequence,
        QShortcut,
    )
    from PySide6.QtWidgets import (  # pyrefly: ignore [missing-import]
        QApplication,
        QButtonGroup,
        QDialog,
        QFileDialog,
        QLabel,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QTextEdit,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )

from fuscan import __author__, __description__, __license__, __version__, theme
from fuscan.builtin import load_with_builtin
from fuscan.config import Config, detect_default_staging_dir, load_config, save_config
from fuscan.gui import resources_rc  # noqa: F401 注册 .qrc 资源（:/ 前缀图标）
from fuscan.gui.content_panel import ContentTabPanel
from fuscan.gui.detail_panel import DetailControls, DetailPanel
from fuscan.gui.explorer import open_path_in_explorer
from fuscan.gui.export_controller import ExportController
from fuscan.gui.icons import (
    ICON_ABOUT as _ICON_ABOUT,
)
from fuscan.gui.icons import (
    ICON_ALL_DISK as _ICON_ALL_DISK,
)
from fuscan.gui.icons import (
    ICON_DISK as _ICON_DISK,
)
from fuscan.gui.icons import (
    ICON_EDIT as _ICON_EDIT,
)
from fuscan.gui.icons import (
    ICON_EXPORT as _ICON_EXPORT,
)
from fuscan.gui.icons import (
    ICON_EXPORT_CSV as _ICON_EXPORT_CSV,
)
from fuscan.gui.icons import (
    ICON_EXPORT_JSON as _ICON_EXPORT_JSON,
)
from fuscan.gui.icons import (
    ICON_FOLDER as _ICON_FOLDER,
)
from fuscan.gui.icons import (
    ICON_HARD_DISK as _ICON_HARD_DISK,
)
from fuscan.gui.icons import (
    ICON_HISTORY as _ICON_HISTORY,
)
from fuscan.gui.icons import (
    ICON_LOAD_LIST as _ICON_LOAD_LIST,
)
from fuscan.gui.icons import (
    ICON_MANUAL as _ICON_MANUAL,
)
from fuscan.gui.icons import (
    ICON_PAUSE as _ICON_PAUSE,
)
from fuscan.gui.icons import (
    ICON_RESCAN as _ICON_RESCAN,
)
from fuscan.gui.icons import (
    ICON_SCAN as _ICON_SCAN,
)
from fuscan.gui.icons import (
    ICON_SEARCH as _ICON_SEARCH,
)
from fuscan.gui.icons import (
    ICON_SETTINGS as _ICON_SETTINGS,
)
from fuscan.gui.icons import (
    ICON_STOP as _ICON_STOP,
)
from fuscan.gui.icons import (
    MANUAL_PDF as _MANUAL_PDF,
)
from fuscan.gui.icons import (
    load_themed_icon as _load_themed_icon,
)
from fuscan.gui.main_window_ui import Ui_MainWindow
from fuscan.gui.preview_utils import SEVERITY_BACKGROUNDS, severity_text
from fuscan.gui.result_filter_panel import ResultFilterPanel
from fuscan.gui.rules_panel import RulesFilePanel
from fuscan.gui.scan_mode_panel import ScanModePanel
from fuscan.gui.scan_path_history import ScanPathHistory
from fuscan.gui.scan_progress_lists import ScanListUpdater
from fuscan.gui.worker import ScanWorker
from fuscan.perf import PerfTimer, set_perf_enabled
from fuscan.rules import RuleError, load_ruleset, merge_multiple_rulesets
from fuscan.rules.model import RuleSet, Severity
from fuscan.scanner import ScanReport
from fuscan.scanner.result import ScanResult
from fuscan.skip_store import SkipStore

if TYPE_CHECKING:
    from PySide2.QtGui import QIcon

    from fuscan.cache import CacheStore

__all__ = ["MainWindow", "ScanState", "WorkflowStage"]

logger = logging.getLogger(__name__)


def _apply_severity_to_tree_item(item: QTreeWidgetItem, column: int, severity: Severity) -> None:
    """为 QTreeWidgetItem 的指定列设置中文标签与背景色。

    仅设置背景色（浅红/浅橙/浅蓝），不设置前景色——避免 ``setForeground``
    覆盖 QSS ``::item:selected`` 的选中态白字（需求1：选中项字体统一白色）。
    """
    item.setText(column, severity_text(severity))
    item.setBackground(column, SEVERITY_BACKGROUNDS[severity])


# 主色变体图标 → 控件属性名映射（同一路径在缓存中只加载一次，可绑定多个控件）
_PRIMARY_ICON_TARGETS: tuple[tuple[str, str], ...] = (
    (_ICON_SCAN, "scan_btn"),
    (_ICON_PAUSE, "pause_resume_btn"),
    (_ICON_RESCAN, "rescan_btn"),
    (_ICON_LOAD_LIST, "load_rules_btn"),
    (_ICON_LOAD_LIST, "load_rules_action"),
    (_ICON_SCAN, "scan_action"),
    (_ICON_EDIT, "edit_rule_btn"),
    (_ICON_EDIT, "edit_rules_action"),
    (_ICON_EXPORT, "export_btn"),
    (_ICON_EXPORT_CSV, "export_csv_action"),
    (_ICON_EXPORT_JSON, "export_json_action"),
    (_ICON_SETTINGS, "settings_action"),
    (_ICON_MANUAL, "manual_action"),
    (_ICON_SEARCH, "select_path_action"),
    (_ICON_ABOUT, "about_action"),
    (_ICON_STOP, "cancel_btn"),
)

# scan_mode_combo 下拉项图标（按 index 顺序：全盘 / 盘符 / 文件夹）
_COMBO_ITEM_ICONS: tuple[str, ...] = (_ICON_ALL_DISK, _ICON_DISK, _ICON_FOLDER)

# 深色背景白色变体图标 → 控件属性名映射（头部 Tab 按钮）
_ON_PRIMARY_ICON_TARGETS: tuple[tuple[str, str], ...] = (
    (_ICON_SCAN, "tab_scan_btn"),
    (_ICON_LOAD_LIST, "tab_rules_btn"),
    (_ICON_HISTORY, "tab_history_btn"),
    (_ICON_SETTINGS, "settings_btn"),
    (_ICON_ABOUT, "about_btn"),
)

# 导出格式定义与查找表已移到 ExportController（iter-79 续解耦）

# 扫描模式 ↔ combo index 双向映射已移到 ScanModePanel（iter-79 续解耦）

# 扫描阶段 ↔ current_file_label 前缀文案映射（iter-79 四阶段）：
# ProgressInfo.phase 取值见 fuscan.scanner.result.ProgressInfo；缺省回退到"文件解析"。
# 四阶段：准备扫描（_on_scan 设置）→ 解析目录（walk）→ 文件解析（scan）→ 扫描完成（_on_scan_finished）
_PHASE_LABELS: dict[str, str] = {
    "walk": "解析目录",
    "scan": "文件解析",
    "archive": "扫描压缩包",
}


class ScanState(enum.Enum):
    """扫描状态。"""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


class WorkflowStage(enum.Enum):
    """工作流阶段，决定主界面 QStackedWidget 显示哪一页。"""

    SETUP = "setup"
    SCANNING = "scanning"
    RESULTS = "results"


# 工作流阶段 ↔ main_stack page index / sidebar row 双向映射（定义在 WorkflowStage 之后）
_STAGE_TO_PAGE_INDEX: dict[WorkflowStage, int] = {
    WorkflowStage.SETUP: 0,
    WorkflowStage.SCANNING: 1,
    WorkflowStage.RESULTS: 2,
}
_SIDEBAR_ROW_TO_STAGE: dict[int, WorkflowStage] = {v: k for k, v in _STAGE_TO_PAGE_INDEX.items()}


class MainWindow(QMainWindow, Ui_MainWindow):  # pyrefly: ignore [invalid-inheritance]
    """主窗口：扫描器 GUI 入口，基于工作流阶段的三页整页切换布局。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        with PerfTimer("MainWindow.__init__"):
            with PerfTimer("MainWindow.setupUi"):
                self.setupUi(self)

            self._config: Config = load_config()
            self._ruleset: RuleSet | None = None
            # _rules_paths / _use_builtin 由 RulesFilePanel 持有，通过 property 转发访问
            self._last_report: ScanReport | None = None
            self._worker: ScanWorker | None = None
            self._scan_state: ScanState = ScanState.IDLE
            self._workflow_stage: WorkflowStage = WorkflowStage.SETUP
            # 取消中标志（iter-79）：用户点击取消后置 True，扫描线程退出前
            # 进度回调 _on_scan_progress 据此跳过 UI 覆盖，保留"取消中..."文案
            # 与不确定进度动画；_reset_scan_ui 中重置为 False
            self._cancelling: bool = False
            # 详情面板控制器：setupUi 已创建详情区 UI 控件，立即构造 DetailPanel，
            # 使后续 _connect_signals/_setup_shortcuts 可安全引用（非 None）
            self._detail_panel: DetailPanel = self._create_detail_panel()
            # 扫描路径历史：维护去重 + 最近优先 + 限量的路径列表，同步
            # path_combo 与 history_list 两个控件（单一数据源避免内容漂移）
            self._path_history: ScanPathHistory = ScanPathHistory(self.path_combo, self.history_list)
            # 扫描结果缓存（启用时惰性创建，关闭窗口时释放）
            self._cache: CacheStore | None = None
            # 用户跳过路径持久化存储（iter-77）：详情区「标记为跳过」按钮写入，
            # 下次扫描时通过 skip_paths 参数传入 Scanner 在 walk 阶段跳过
            self._skip_store: SkipStore = SkipStore()
            # 扫描中列表增量更新器：封装跳过目录与命中文件列表的 0.5 秒节流 + 增量
            # append 算法，避免每次进度回调全量 clear+重添导致主线程阻塞（点击设置卡滞根因）
            self._list_updater: ScanListUpdater = ScanListUpdater(self.skipped_dirs_list, self.matched_files_list)

            with PerfTimer("MainWindow._configure_ui"):
                self._configure_ui()
            with PerfTimer("MainWindow._apply_config"):
                self._apply_config()
            with PerfTimer("MainWindow._init_rules"):
                self._init_rules()

    # ----------------------------- UI 配置 -----------------------------

    def _create_detail_panel(self) -> DetailPanel:
        """构造详情面板控制器：封装详情区状态、填充、导航与文件操作。

        详情区 UI 控件由 ``setupUi`` 创建，本方法构造 :class:`DetailControls` 引用集合
        并传入 :class:`DetailPanel`，后续主窗口通过 ``self._detail_panel`` 公共 API 驱动详情区。
        信号路由：``path_copy_requested`` / ``open_location_requested`` /
        ``move_to_staging_requested`` / ``toggle_skip_requested`` 连接到主窗口槽，
        由主窗口更新状态栏、定位文件、移动至暂存区或切换跳过标记（iter-77）。
        """
        controls = DetailControls(
            action_stack=self.detail_action_stack,
            main_stack=self.detail_main_stack,
            prev_btn=self.detail_prev_btn,
            next_btn=self.detail_next_btn,
            nav_label=self.detail_nav_label,
            open_location_btn=self.detail_open_location_btn,
            info_label=self.detail_info_label,
            hits_table=self.detail_hits_table,
            preview=self.detail_preview,
            move_to_staging_btn=self.move_to_staging_btn,
            toggle_skip_btn=self.toggle_skip_btn,
        )
        return DetailPanel(controls, parent=self)

    def _configure_ui(self) -> None:
        """配置 .ui 无法静态表达的动态属性、layout stretch 与信号槽连接。"""
        self._setup_status_bar()
        self._setup_result_filter_panel()
        self._setup_splitters()
        self._setup_layouts()
        self._setup_scan_stats_panel()
        self._setup_icons()
        self._setup_button_groups()
        self._setup_scan_mode_panel()
        self._setup_export_controller()
        self._setup_rules_panel()
        self._setup_sidebar()
        self._setup_file_types()
        self._connect_signals()
        self._setup_context_menus()
        self._setup_shortcuts()
        # 初始阶段：配置页
        self._switch_stage(WorkflowStage.SETUP)

    def _setup_status_bar(self) -> None:
        """创建状态栏组件：左侧汇总文本，右侧进度条 + 当前文件（仅扫描中可见）。"""
        self.stats_label = QLabel("就绪")
        self.stats_label.setObjectName("stats_label")
        self.statusBar().addWidget(self.stats_label, 1)
        self.current_file_label = QLabel("")
        self.current_file_label.setObjectName("current_file_label")
        self.current_file_label.setMaximumWidth(400)
        self.current_file_label.setVisible(False)
        self.statusBar().addPermanentWidget(self.current_file_label)
        self.progress = QProgressBar()
        self.progress.setObjectName("progress")
        self.progress.setFixedWidth(200)
        # 初始为确定模式（0/100），避免未启动扫描时显示 indeterminate 动画；
        # 扫描真正启动时（_start_scan）才切换为 setRange(0, 0)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)

    def _setup_result_filter_panel(self) -> None:
        """构造结果树筛选面板控制器（路径输入 + 规则筛选 + 分组模式 + 结果树）。

        委托 :class:`ResultFilterPanel` 封装 ``path_filter_input`` /
        ``rule_filter_combo`` / ``group_mode_combo`` 三个筛选控件的初始化、
        节流 timer 与结果树刷新逻辑（iter-79 续内聚重构）。主窗口通过公共
        API（``populate`` / ``refresh`` / ``clear``）驱动，不直接操作底层控件。
        ``result_tree`` 的 ``result_selected`` / ``context_menu_requested``
        信号仍由主窗口连接（响应逻辑在主窗口）。
        """
        self._result_filter_panel = ResultFilterPanel(
            path_filter_input=self.path_filter_input,
            rule_filter_combo=self.rule_filter_combo,
            group_mode_combo=self.group_mode_combo,
            result_tree=self.result_tree,
            report_getter=lambda: self._last_report,
            parent=self,
        )

    def _setup_splitters(self) -> None:
        """设置 QSplitter 伸缩比例与初始尺寸（.ui 不支持 setStretchFactor）。"""
        # results_splitter: 结果列表 : 详情区 = 2:3
        self.results_splitter.setStretchFactor(0, 2)
        self.results_splitter.setStretchFactor(1, 3)
        # sidebar_splitter: sidebar(0) / main_stack(1) 初始比例 220:1060
        self.sidebar_splitter.setStretchFactor(0, 0)
        self.sidebar_splitter.setStretchFactor(1, 1)
        self.sidebar_splitter.setSizes([220, 1060])

    def _setup_layouts(self) -> None:
        """设置各 layout 伸缩因子（.ui 不支持 stretch vector）。"""
        # 配置页：target_group 自然尺寸 + file_types_group 伸展填充 + setup_action_bar 固定底部
        self.setup_layout.setStretch(0, 0)
        self.setup_layout.setStretch(1, 1)
        self.setup_layout.setStretch(2, 0)
        # target_group 内：scan_mode_layout（history 已移至 history_tab）
        self.target_group_layout.setStretch(0, 0)
        # rules_group 内：rules_btn_row / rules_file_label / rules_file_list / rules_tree
        self.rules_group_layout.setStretch(0, 0)
        self.rules_group_layout.setStretch(1, 0)
        self.rules_group_layout.setStretch(2, 0)
        self.rules_group_layout.setStretch(3, 1)
        # rules_tab_layout: rules_group 占满
        self.rules_tab_layout.setStretch(0, 1)
        # history_tab_layout: history_label(0) / history_list(1)
        self.history_tab_layout.setStretch(0, 0)
        self.history_tab_layout.setStretch(1, 1)
        # filter_layout: path_filter_input / rule_filter_combo / group_mode_combo
        self.filter_layout.setStretch(0, 2)
        self.filter_layout.setStretch(1, 1)
        self.filter_layout.setStretch(2, 1)
        # results_list_layout: filter_bar / result_tree
        self.results_list_layout.setStretch(0, 0)
        self.results_list_layout.setStretch(1, 1)
        # detail_layout: detail_action_stack / detail_main_stack
        self.detail_layout.setStretch(0, 0)
        self.detail_layout.setStretch(1, 1)
        self.detail_nonempty_main_layout.setStretch(0, 0)
        self.detail_nonempty_main_layout.setStretch(1, 0)
        self.detail_nonempty_main_layout.setStretch(2, 1)
        self.detail_nonempty_main_layout.setStretch(3, 0)
        self.detail_nonempty_main_layout.setStretch(4, 2)
        self.detail_nonempty_main_layout.setStretch(5, 0)

    def _setup_scan_stats_panel(self) -> None:
        """初始化扫描中页的已扫描文件分类统计面板。

        ``scan_stats_label`` 已在 ``main_window.ui`` 中声明（位于 ``lists_splitter``
        与 ``scanning_btn_row`` 之间），本方法仅设置初始文本。面板用 HTML 富文本
        显示五类计数与颜色标识：

        - 绿色：已通过（已扫描且未命中且未错误的文件）
        - 红色：命中
        - 黄色：跳过（按扩展名/目录过滤）
        - 紫色：用户跳过（iter-77，用户标记跳过的文件）
        - 红色：错误

        颜色标识使用 ``<span style="color: ...">`` 内联样式，避免引入 QSS 样式表。
        """
        self._update_scan_stats(0, 0, 0, 0, 0)

    def _update_scan_stats(self, passed: int, matched: int, skipped: int, errors: int, user_skipped: int = 0) -> None:
        """更新扫描中页的分类统计面板。

        :param passed: 已通过文件数（已扫描且未命中且未错误）
        :param matched: 命中文件数
        :param skipped: 跳过文件数（按扩展名/目录过滤）
        :param errors: 错误文件数
        :param user_skipped: 用户标记跳过的文件数（iter-77）
        """
        self.scan_stats_label.setText(
            f'<span style="color: #28A745; font-weight: bold;">已通过 {passed}</span>'
            f" &nbsp;|&nbsp; "
            f'<span style="color: #DC3545; font-weight: bold;">命中 {matched}</span>'
            f" &nbsp;|&nbsp; "
            f'<span style="color: #FFC107; font-weight: bold;">跳过 {skipped}</span>'
            f" &nbsp;|&nbsp; "
            f'<span style="color: #6F42C1; font-weight: bold;">用户跳过 {user_skipped}</span>'
            f" &nbsp;|&nbsp; "
            f'<span style="color: #DC3545; font-weight: bold;">错误 {errors}</span>'
        )

    def _setup_icons(self) -> None:
        """加载主题图标并设置到各按钮、菜单 actions 与下拉项。

        采用表驱动模式：``_PRIMARY_ICON_TARGETS`` / ``_ON_PRIMARY_ICON_TARGETS``
        描述 ``(图标路径, 控件属性名)`` 映射，按需加载并缓存到局部 dict，
        避免相同路径重复调用 ``_load_themed_icon``（如 ``_ICON_SCAN`` 同时绑定
        ``scan_btn`` 与 ``scan_action``，``_ICON_LOAD_LIST`` 同时绑定 ``load_rules_btn``
        与 ``load_rules_action``）。
        """
        # 主色变体图标缓存：key=SVG 路径，value=已着色 QIcon
        primary_cache: dict[str, QIcon] = {}
        for icon_path, attr_name in _PRIMARY_ICON_TARGETS:
            if icon_path not in primary_cache:
                primary_cache[icon_path] = _load_themed_icon(icon_path, theme.COLOR_PRIMARY)
            getattr(self, attr_name).setIcon(primary_cache[icon_path])

        # scan_mode_combo 下拉项图标（index 顺序对应 _COMBO_ITEM_ICONS）
        for index, icon_path in enumerate(_COMBO_ITEM_ICONS):
            if icon_path not in primary_cache:
                primary_cache[icon_path] = _load_themed_icon(icon_path, theme.COLOR_PRIMARY)
            self.scan_mode_combo.setItemIcon(index, primary_cache[icon_path])

        # 盘符按钮复用主色 hard_disk 变体（ScanModePanel 构造时按需读取）
        if _ICON_HARD_DISK not in primary_cache:
            primary_cache[_ICON_HARD_DISK] = _load_themed_icon(_ICON_HARD_DISK, theme.COLOR_PRIMARY)
        self._icon_hard_disk = primary_cache[_ICON_HARD_DISK]

        # 深色背景白色变体图标缓存
        on_primary_cache: dict[str, QIcon] = {}
        for icon_path, attr_name in _ON_PRIMARY_ICON_TARGETS:
            if icon_path not in on_primary_cache:
                on_primary_cache[icon_path] = _load_themed_icon(icon_path, theme.COLOR_TEXT_ON_PRIMARY)
            getattr(self, attr_name).setIcon(on_primary_cache[icon_path])

        # 侧边栏阶段项复用白色变体（_setup_sidebar 时按需读取，避免重复加载）
        if _ICON_FOLDER not in on_primary_cache:
            on_primary_cache[_ICON_FOLDER] = _load_themed_icon(_ICON_FOLDER, theme.COLOR_TEXT_ON_PRIMARY)
        self._icon_folder_on_primary = on_primary_cache[_ICON_FOLDER]
        self._icon_scan_on_primary = on_primary_cache[_ICON_SCAN]
        self._icon_history_on_primary = on_primary_cache[_ICON_HISTORY]

    def _setup_button_groups(self) -> None:
        """初始化头部 Tab 按钮互斥组（盘符按钮组已移到 ScanModePanel）。"""
        # 头部 Tab 按钮互斥组（id 0=扫描 / 1=规则 / 2=历史）
        self._header_button_group = QButtonGroup(self)
        self._header_button_group.setExclusive(True)
        self._header_button_group.addButton(self.tab_scan_btn, 0)
        self._header_button_group.addButton(self.tab_rules_btn, 1)
        self._header_button_group.addButton(self.tab_history_btn, 2)

    def _setup_sidebar(self) -> None:
        """填充侧边栏阶段项（深色背景用白色变体；配置 / 扫描中 / 结果）。"""
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        self.sidebar.addItem(QListWidgetItem(self._icon_folder_on_primary, "配置"))  # pyrefly: ignore [missing-argument]
        self.sidebar.addItem(QListWidgetItem(self._icon_scan_on_primary, "扫描中"))  # pyrefly: ignore [missing-argument]
        self.sidebar.addItem(QListWidgetItem(self._icon_history_on_primary, "结果"))  # pyrefly: ignore [missing-argument]
        self.sidebar.setCurrentRow(0)  # pyrefly: ignore [missing-argument]
        self.sidebar.blockSignals(False)

    def _setup_scan_mode_panel(self) -> None:
        """构造扫描模式选择面板控制器（模式 combo + 盘符按钮组 + folder 路径）。

        委托 :class:`ScanModePanel` 封装 ``scan_mode_combo`` 切换、盘符按钮组
        创建/刷新/选择、folder 路径状态管理（iter-79 续内聚重构）。
        主窗口通过公共 API（``apply_config`` / ``save_config`` /
        ``can_start_scan`` / ``build_scan_roots`` / ``set_folder_root`` 等）驱动，
        不直接操作底层控件。``mode_changed`` 信号触发 ``_update_scan_button``。
        """
        self._scan_mode_panel = ScanModePanel(
            combo=self.scan_mode_combo,
            target_stack=self.target_stack,
            drive_buttons_layout=self.drive_buttons_layout,
            hard_disk_icon=self._icon_hard_disk,
            config=self._config,
            parent=self,
        )

    def _setup_export_controller(self) -> None:
        """构造扫描结果导出控制器（格式选择 + 文件保存 + 后台导出）。

        委托 :class:`ExportController` 封装导出流程（格式选择对话框 → 文件
        保存对话框 → 启动 ExportWorker → 完成/失败回调），主窗口通过公共
        API（``show_menu`` / ``export`` / ``cleanup``）驱动（iter-79 续内聚重构）。
        导出期间禁用 ``export_btn``，完成/失败后通过
        ``button_restore_requested`` 信号通知主窗口调 ``_update_stage_actions``
        重新计算按钮状态。非导出期间按钮状态由 ``_update_stage_actions`` 统一管理。
        """
        self._export_controller = ExportController(
            export_btn=self.export_btn,
            stats_label=self.stats_label,
            report_getter=lambda: self._last_report,
            parent_widget=self,
            parent=self,
        )
        self._export_controller.button_restore_requested.connect(self._update_stage_actions)  # pyrefly: ignore [missing-attribute]

    def _setup_rules_panel(self) -> None:
        """构造规则文件列表面板控制器（列表显示 + 顺序操作 + 内置勾选 + 右键菜单）。

        委托 :class:`RulesFilePanel` 封装 ``rules_file_list`` 的列表刷新、上移/
        下移/移除、内置规则勾选与右键菜单（iter-79 续内聚重构）。主窗口通过
        ``_use_builtin`` / ``_rules_paths`` property 转发访问 panel 状态，
        保持向后兼容；``rules_changed`` 信号触发主窗口 ``_on_rules_changed``
        重新加载规则集并保存配置。
        """
        self._rules_panel = RulesFilePanel(self.rules_file_list, parent=self)
        self._rules_panel.rules_changed.connect(self._on_rules_changed)  # pyrefly: ignore [missing-attribute]

    def _setup_file_types(self) -> None:
        """构造内容 TAB 面板控制器（文件类型树 + 忽略目录 + 忽略扩展名）。

        委托 :class:`ContentTabPanel` 封装 ``ExtractorTreeModel`` 勾选管理、
        忽略项编辑器节流保存、计数标签同步等逻辑（iter-79 内聚重构）。
        主窗口通过公共 API（``enabled_extensions`` / ``archives_enabled`` /
        ``apply_config`` / ``flush_pending_save``）驱动，不直接操作底层控件。
        """
        self._content_panel = ContentTabPanel(
            view=self.file_types_view,
            count_label=self.file_types_count_label,
            dirs_edit=self.ignore_dirs_edit,
            exts_edit=self.ignore_extensions_edit,
            config=self._config,
            parent=self,
        )

    def _connect_signals(self) -> None:
        """连接所有信号槽（按钮、actions、worker、头部栏与侧边栏）。"""
        # 扫描控制
        self.scan_btn.clicked.connect(self._on_scan)
        self.view_results_btn.clicked.connect(self._on_view_results)
        self.pause_resume_btn.clicked.connect(self._on_pause_resume)
        self.cancel_btn.clicked.connect(self._on_cancel_scan)
        self.rescan_btn.clicked.connect(self._on_rescan)
        # 扫描目标（scan_mode_combo 信号已由 ScanModePanel 内部连接）
        self._scan_mode_panel.mode_changed.connect(self._update_scan_button)  # pyrefly: ignore [missing-attribute]
        self.path_combo.currentIndexChanged.connect(self._on_path_selected)
        self.select_path_btn.clicked.connect(self._on_select_path)
        # 规则
        self.load_rules_btn.clicked.connect(self._on_load_rules)
        self.edit_rule_btn.clicked.connect(self._on_edit_rules)
        # 结果树（ResultTreeView 信号路由：选中/双击/右键均通过自定义信号转发）
        self.result_tree.result_selected.connect(self._on_result_selected)  # pyrefly: ignore [missing-attribute]
        self.result_tree.context_menu_requested.connect(self._on_result_tree_context_menu)  # pyrefly: ignore [missing-attribute]
        # 筛选信号（path_filter_input / rule_filter_combo / group_mode_combo）
        # 已由 ResultFilterPanel 内部连接（节流 timer + 立即刷新）
        # 历史
        self.history_list.itemDoubleClicked.connect(self._on_history_item_double_clicked)
        # 详情区（导出按钮 → ExportController.show_menu）
        self.export_btn.clicked.connect(self._export_controller.show_menu)
        # 详情面板信号路由：复制路径/打开位置/移动至暂存区/切换跳过由 DetailPanel 发信号，主窗口响应
        self._detail_panel.path_copy_requested.connect(self._on_path_copy_requested)  # pyrefly: ignore [missing-attribute]
        self._detail_panel.open_location_requested.connect(self._on_open_location_requested)  # pyrefly: ignore [missing-attribute]
        self._detail_panel.move_to_staging_requested.connect(self._on_move_to_staging)  # pyrefly: ignore [missing-attribute]
        self._detail_panel.toggle_skip_requested.connect(self._on_toggle_skip)  # pyrefly: ignore [missing-attribute]
        # 扫描中页命中文件列表双击：弹出简化详情与定位按钮（需求5）
        self.matched_files_list.itemDoubleClicked.connect(self._on_matched_file_double_clicked)
        # 头部栏与侧边栏（rule-12 HeaderBar + Sidebar）
        self._header_button_group.idClicked.connect(self._on_header_tab_changed)
        self.sidebar.currentRowChanged.connect(self._on_sidebar_stage_changed)
        self.settings_btn.clicked.connect(self._on_settings)
        self.about_btn.clicked.connect(self._on_about)
        # actions
        self.load_rules_action.triggered.connect(self._on_load_rules)
        self.edit_rules_action.triggered.connect(self._on_edit_rules)
        self.export_csv_action.triggered.connect(lambda: self._export_controller.export("csv"))
        self.export_json_action.triggered.connect(lambda: self._export_controller.export("json"))
        self.quit_action.triggered.connect(self.close)
        self.select_path_action.triggered.connect(self._on_select_path)
        self.scan_action.triggered.connect(self._on_scan)
        self.about_action.triggered.connect(self._on_about)
        self.manual_action.triggered.connect(self._on_open_manual)
        self.settings_action.triggered.connect(self._on_settings)
        self.perf_stats_action.triggered.connect(self._on_show_perf_stats)
        self.perf_log_action.toggled.connect(self._on_toggle_perf_log)

    def _setup_context_menus(self) -> None:
        """配置结果树右键菜单策略（规则文件列表右键已由 RulesFilePanel 内部处理）。"""
        # 结果树右键由 ResultTreeView 信号路由；规则文件列表的右键菜单与
        # itemChanged 信号已迁入 RulesFilePanel（iter-79 续解耦）
        pass

    def _on_result_tree_context_menu(self, pos: QPoint) -> None:  # type: ignore[unknown-name]
        """结果树右键菜单：复制路径 / 打开文件位置。"""
        if self._detail_panel.current_result is None:
            return
        menu = QMenu(self.result_tree)
        action_copy = QAction("复制路径", menu)
        action_open_location = QAction("打开文件位置", menu)
        action_copy.triggered.connect(self._detail_panel.copy_path)
        action_open_location.triggered.connect(self._detail_panel.open_location)
        menu.addAction(action_copy)  # pyrefly: ignore [missing-argument]
        menu.addAction(action_open_location)  # pyrefly: ignore [missing-argument]
        menu.exec_(self.result_tree.viewport().mapToGlobal(pos))  # pyrefly: ignore [missing-argument]

    def _setup_shortcuts(self) -> None:
        """创建全局快捷键：F3 下一条命中、Shift+F3 上一条命中、Delete 移除规则文件。"""
        self._shortcut_next = QShortcut(QKeySequence("F3"), self)
        self._shortcut_next.activated.connect(self._detail_panel.next_hit)
        self._shortcut_prev = QShortcut(QKeySequence("Shift+F3"), self)
        self._shortcut_prev.activated.connect(self._detail_panel.prev_hit)
        self._shortcut_remove_rule = QShortcut(QKeySequence.Delete, self.rules_file_list)
        self._shortcut_remove_rule.activated.connect(self._rules_panel.remove_selected)

    def _set_use_builtin(self, enabled: bool) -> None:
        """统一设置通用规则开关并刷新规则集。

        替代原 _on_toggle_builtin 的散落逻辑，供 _on_settings 和测试统一调用。
        通过 ``_use_builtin`` property setter 转发到 RulesFilePanel（仅赋值，
        不 emit ``rules_changed``），随后调 ``_apply_ruleset_loaded`` 刷新 UI。
        """
        self._use_builtin = enabled
        try:
            self._apply_ruleset_loaded()
            if self._ruleset is not None:
                self.stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条规则")
            else:
                self.stats_label.setText("未加载规则")
        except RuleError as exc:
            QMessageBox.warning(self, "规则错误", f"重新加载规则失败:\n{exc}")

    def _on_rules_changed(self) -> None:
        """规则文件列表变化槽：重新加载规则集并保存配置。

        由 RulesFilePanel ``rules_changed`` 信号触发（用户勾选内置规则 / 上移 /
        下移 / 移除操作后）。panel 内部已刷新列表显示，本槽仅处理规则集重载
        与持久化。
        """
        self._reload_and_refresh()
        self._save_config()

    # ----------------------------- RulesFilePanel 状态转发 -----------------------------

    # _use_builtin / _rules_paths 实际由 RulesFilePanel 持有，此处 property
    # 转发保持主窗口各方法（_reload_ruleset / _apply_ruleset_loaded /
    # _on_load_rules / _build_cache_context / _apply_config / _save_config 等）
    # 与测试用例无需改动（iter-79 续解耦）。
    @property
    def _use_builtin(self) -> bool:
        return self._rules_panel.use_builtin

    @_use_builtin.setter
    def _use_builtin(self, value: bool) -> None:
        self._rules_panel.use_builtin = value

    @property
    def _rules_paths(self) -> list[Path]:
        return self._rules_panel.rules_paths

    @_rules_paths.setter
    def _rules_paths(self, value: list[Path]) -> None:
        self._rules_panel.rules_paths = value

    # ----------------------------- 工作流阶段切换 -----------------------------

    def _switch_stage(self, stage: WorkflowStage) -> None:
        """切换工作流阶段页面并更新控件状态。

        SETUP=0 配置页、SCANNING=1 扫描中页、RESULTS=2 结果页。
        同步侧边栏选中项，避免循环触发信号。
        """
        self._workflow_stage = stage
        page_index = _STAGE_TO_PAGE_INDEX[stage]
        self.main_stack.setCurrentIndex(page_index)
        self.sidebar.blockSignals(True)
        self.sidebar.setCurrentRow(page_index)  # pyrefly: ignore [missing-argument]
        self.sidebar.blockSignals(False)
        self._update_stage_actions()

    def _on_header_tab_changed(self, tab_id: int) -> None:
        """头部 Tab 切换：切换 tab_stack 页面，非扫描 Tab 隐藏侧边栏。

        :param tab_id: 0=扫描 / 1=规则管理 / 2=扫描历史
        """
        self.tab_stack.setCurrentIndex(tab_id)
        self.sidebar.setVisible(tab_id == 0)

    def _on_sidebar_stage_changed(self, row: int) -> None:
        """侧边栏阶段项切换：映射 row 到 WorkflowStage 并切换页面。

        :param row: 0=配置 / 1=扫描中 / 2=结果
        """
        stage = _SIDEBAR_ROW_TO_STAGE.get(row)
        if stage is not None:
            self._switch_stage(stage)

    def _update_stage_actions(self) -> None:
        """根据当前阶段与扫描状态更新按钮和菜单的可用性。"""
        is_setup = self._workflow_stage == WorkflowStage.SETUP
        is_scanning = self._workflow_stage == WorkflowStage.SCANNING
        is_results = self._workflow_stage == WorkflowStage.RESULTS
        has_report = self._last_report is not None

        # 配置页：scan_btn 仅在 SETUP 可用；view_results_btn 始终可见，根据是否有结果启用
        self.scan_btn.setEnabled(is_setup and self._can_start_scan())
        self.view_results_btn.setVisible(is_setup)
        self.view_results_btn.setEnabled(has_report)

        # 状态栏进度条与当前文件标签：扫描中阶段可见，其余阶段隐藏。
        # 进度条初始为确定模式（0/100），不会显示 indeterminate 动画；
        # 仅在 _start_scan 中切换为 indeterminate 模式。
        self.progress.setVisible(is_scanning)
        self.current_file_label.setVisible(is_scanning)

        # 扫描中页：pause_resume_btn 文本随 ScanState 切换
        if self._workflow_stage == WorkflowStage.SCANNING:
            if self._scan_state == ScanState.PAUSED:
                self.pause_resume_btn.setText("继续扫描")
            else:
                self.pause_resume_btn.setText("暂停扫描")

        # 暂停/取消按钮仅在扫描中阶段可用（配置页与结果页均禁用，
        # 避免用户在未扫描时误触发出无效操作）
        self.pause_resume_btn.setEnabled(is_scanning)
        self.cancel_btn.setEnabled(is_scanning)

        # 侧边栏在扫描中阶段禁用（iter-79 需求4）：防止用户在解析目录/文件解析
        # 阶段切换到其他页面导致扫描状态不一致；准备扫描与扫描完成阶段恢复可用
        self.sidebar.setEnabled(not is_scanning)

        # 结果页
        self.rescan_btn.setEnabled(is_results)
        if is_results and has_report:
            self.export_btn.setEnabled(len(self._last_report.hits) > 0)  # pyrefly: ignore [missing-attribute]
        else:
            self.export_btn.setEnabled(False)

        # 菜单 actions
        self.scan_action.setEnabled(is_setup and self._can_start_scan())
        self.select_path_action.setEnabled(is_setup)
        self.export_csv_action.setEnabled(is_results and has_report)
        self.export_json_action.setEnabled(is_results and has_report)
        self.load_rules_action.setEnabled(is_setup)
        self.edit_rules_action.setEnabled(is_setup)

    def _can_start_scan(self) -> bool:
        """判断是否满足开始扫描的条件。"""
        if self._scan_state in (ScanState.RUNNING, ScanState.PAUSED):
            return True
        if self._ruleset is None:
            return False
        return self._scan_mode_panel.can_start_scan()

    def _on_view_results(self) -> None:
        """配置页"查看结果"按钮：切换到结果页。"""
        if self._last_report is not None:
            self._switch_stage(WorkflowStage.RESULTS)

    def _on_rescan(self) -> None:
        """结果页"重新扫描"按钮：返回配置页。"""
        self._switch_stage(WorkflowStage.SETUP)

    def _on_pause_resume(self) -> None:
        """扫描中页"暂停/继续"按钮：根据 ScanState 切换。"""
        if self._scan_state == ScanState.RUNNING:
            self._pause_scan()
        elif self._scan_state == ScanState.PAUSED:
            self._resume_scan()

    def _on_cancel_scan(self) -> None:
        """扫描中页"取消扫描"按钮：立即显示取消状态，后台异步取消扫描。

        点击后立即禁用暂停/取消按钮、切换进度条为不确定动画（转圈）并显示
        "取消中..."，给用户即时反馈；``worker.cancel()`` 仅设置取消标志（非阻塞），
        实际取消由扫描线程在下次 ``_check_control()`` 检查时生效。

        ``_cancelling`` 标志防止扫描线程退出前的进度回调覆盖"取消中..."文案
        与不确定动画（扫描线程退出前仍会 emit progress_info 信号）。
        """
        if self._worker is not None:
            self._cancelling = True
            self.cancel_btn.setEnabled(False)
            self.pause_resume_btn.setEnabled(False)
            # 进度条切换为不确定模式（转圈动画），给用户"正在取消"的视觉反馈
            self.progress.setRange(0, 0)
            self.stats_label.setText("取消中...")
            self.current_file_label.setText("正在取消扫描...")
            self._worker.cancel()

    # ----------------------------- 配置持久化 -----------------------------

    def _apply_config(self) -> None:
        """应用配置：恢复窗口几何、分割器、扫描模式、规则路径、扫描历史。"""
        self._restore_window_geometry()

        if self._config.splitter_sizes:
            self.results_splitter.setSizes(self._config.splitter_sizes)

        # 恢复扫描模式与盘符选择（委托 ScanModePanel，内部 blockSignals
        # 避免 currentIndexChanged 触发 _on_mode_changed 重复 emit mode_changed）
        self._scan_mode_panel.apply_config(self._config)

        self._use_builtin = self._config.use_builtin

        self._rules_paths = [Path(p) for p in self._config.rules_paths if Path(p).exists()]

        # 恢复扫描路径历史（同步 path_combo 与 history_list 两个控件）
        self._path_history.load_from_config(self._config.scan_paths)

        # 恢复首个有效路径作为 folder 模式根路径（委托 ScanModePanel.set_folder_root）
        if self.path_combo.count() > 0:
            first_path = Path(self.path_combo.itemText(0))
            self._scan_mode_panel.set_folder_root(first_path if first_path.exists() else None)
        self._update_scan_button()

        # 恢复性能日志开关（blockSignals 避免 toggled 触发 _save_config 循环）
        self.perf_log_action.blockSignals(True)
        self.perf_log_action.setChecked(self._config.perf_log_enabled)
        self.perf_log_action.blockSignals(False)
        set_perf_enabled(self._config.perf_log_enabled)

        # 恢复文件类型勾选状态与忽略项内容（委托 ContentTabPanel，内部 blockSignals
        # 避免触发保存循环；含向后兼容逻辑：旧配置 scan_archives=False 补充禁用）
        self._content_panel.apply_config(self._config)

    def _restore_window_geometry(self) -> None:
        """从配置恢复窗口几何（含屏幕边界夹紧算法）。

        若配置中有完整 4 元组 geometry，则按 (x, y, w, h) 恢复并将窗口
        夹紧到当前屏幕可用区域内（避免恢复到已不存在的多屏坐标）；
        否则将窗口居中到主屏幕。最后若 ``window_state`` 为 ``maximized`` 则最大化。
        """
        min_w, min_h = self.minimumSize().width(), self.minimumSize().height()
        screen_geo = QApplication.primaryScreen().availableGeometry()

        if self._config.window_geometry and len(self._config.window_geometry) == 4:
            x, y, w, h = self._config.window_geometry
            w = max(w, min_w)
            h = max(h, min_h)
            if screen_geo.width() > w:
                x = max(0, min(x, screen_geo.width() - w))
            if screen_geo.height() > h:
                y = max(0, min(y, screen_geo.height() - h))
            self.setGeometry(x, y, w, h)
        else:
            w, h = self.size().width(), self.size().height()
            if screen_geo.width() > w and screen_geo.height() > h:
                x = (screen_geo.width() - w) // 2
                y = (screen_geo.height() - h) // 2
                self.move(x, y)

        if self._config.window_state == "maximized":
            self.showMaximized()

    def _save_config(self) -> None:
        """保存当前状态到配置文件。"""
        geo = self.geometry()
        self._config.window_geometry = [geo.x(), geo.y(), geo.width(), geo.height()]
        self._config.window_state = "maximized" if self.isMaximized() else "normal"
        self._config.splitter_sizes = list(self.results_splitter.sizes())
        # 扫描模式与盘符选择委托 ScanModePanel 保存
        self._scan_mode_panel.save_config(self._config)
        self._config.rules_paths = [str(p) for p in self._rules_paths]
        self._config.use_builtin = self._use_builtin
        self._config.scan_paths = self._path_history.get_paths()
        # perf_log_enabled 由 _on_toggle_perf_log 实时更新到 _config，此处无需再读 perf_log_action
        save_config(self._config)

    def _add_scan_path_history(self, path_str: str) -> None:
        """将路径添加到扫描历史（去重、最近优先、限制数量，同步两个控件）。

        立即持久化到配置文件（iter-69），避免应用异常退出时历史丢失。
        """
        self._path_history.add(path_str)
        self._save_config()

    def _on_history_item_double_clicked(self, item: QListWidgetItem) -> None:
        """双击历史列表项切换到 folder 模式并选择该路径。"""
        path_str = item.text()
        path = Path(path_str)
        if not path.exists():
            QMessageBox.information(self, "提示", f"路径不存在:\n{path_str}")
            return
        self._scan_mode_panel.select_folder_mode()
        self._scan_mode_panel.set_folder_root(path)
        self._add_scan_path_history(path_str)
        self._update_stage_actions()

    # ----------------------------- 规则加载 -----------------------------

    def _init_rules(self) -> None:
        """启动时加载规则：默认加载内置通用规则。"""
        try:
            self._apply_ruleset_loaded()
            if self._ruleset is not None:
                self.stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条通用规则")
        except RuleError as exc:
            logger.warning("内置规则加载失败: %s", exc)
            self.stats_label.setText("内置规则加载失败")

    def _reload_ruleset(self) -> None:
        """根据当前通用规则开关与用户规则路径列表重新加载规则集。"""
        if self._use_builtin:
            self._ruleset = load_with_builtin(self._rules_paths)
        elif self._rules_paths:
            rulesets = [load_ruleset(p) for p in self._rules_paths]
            self._ruleset = merge_multiple_rulesets(*rulesets)
        else:
            self._ruleset = None

    def _apply_ruleset_loaded(self) -> None:
        """重新加载规则集并同步刷新 UI（rules_tree / rules_file_list / scan_button）。

        统一封装 4 处重复的 ``_reload_ruleset + _refresh_rules_tree +
        _rules_panel.refresh + _update_scan_button`` 调用序列。
        ``stats_label`` 文案因调用场景不同（内置/用户加载），由调用方在调用后设置。
        """
        self._reload_ruleset()
        self._refresh_rules_tree()
        self._rules_panel.refresh()
        self._update_scan_button()

    # 扫描模式相关方法（_on_scan_mode_changed / _update_target_visibility /
    # _refresh_drive_buttons / _on_drive_selected / _build_scan_roots）已移到
    # ScanModePanel（iter-79 续解耦），主窗口通过 self._scan_mode_panel 公共 API 驱动

    # ----------------------------- 槽函数 -----------------------------

    def _on_load_rules(self) -> None:
        """加载规则文件，追加到已加载列表末尾。

        若选择的文件已在列表中（如用户在外部编辑器修改后想刷新），
        弹出询问对话框确认后重新加载规则集，不重复添加路径。
        """
        last_dir = str(self._rules_paths[-1].parent) if self._rules_paths else str(Path.home())
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "选择规则文件",
            last_dir,
            "YAML 文件 (*.yaml *.yml);;所有文件 (*.*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if path in self._rules_paths:
            # 文件已加载：询问是否重新加载（用户可能在外部修改了文件内容）
            reply = QMessageBox.question(
                self,
                "规则文件已加载",
                f"该规则文件已在列表中:\n{path.name}\n\n是否重新加载以应用最新内容？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
            # _reload_and_refresh 内部已捕获 RuleError 并弹警告框
            self._reload_and_refresh()
            return
        self._rules_paths.append(path)
        try:
            self._apply_ruleset_loaded()
            if self._ruleset is not None:
                self.stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条规则")
        except RuleError as exc:
            self._rules_paths.remove(path)
            self._apply_ruleset_loaded()
            QMessageBox.warning(self, "规则错误", f"加载规则失败:\n{exc}")

    def _on_select_path(self) -> None:
        """选择扫描路径。"""
        path_str = QFileDialog.getExistingDirectory(
            self,
            "选择扫描目录",
            str(self._scan_mode_panel.folder_root or Path.home()),
        )
        if not path_str:
            return
        path = Path(path_str)
        self._scan_mode_panel.set_folder_root(path)
        self._add_scan_path_history(str(path))

    def _on_path_selected(self, index: int) -> None:
        """从历史下拉选择扫描路径。"""
        if index < 0:
            self._scan_mode_panel.set_folder_root(None)
            return
        path_str = self.path_combo.itemText(index)
        if not path_str:
            self._scan_mode_panel.set_folder_root(None)
        else:
            path = Path(path_str)
            self._scan_mode_panel.set_folder_root(path if path.exists() else None)

    def _on_scan(self) -> None:
        """开始扫描（仅配置页可触发，扫描中页的暂停/继续由 _on_pause_resume 处理）。"""
        if self._workflow_stage != WorkflowStage.SETUP:
            return
        if self._scan_state in (ScanState.RUNNING, ScanState.PAUSED):
            return

        if self._ruleset is None:
            return

        roots = self._scan_mode_panel.build_scan_roots()
        if not roots:
            QMessageBox.warning(self, "提示", "未选择有效的扫描目标")
            return

        # flush 忽略项节流保存（iter-79）：用户编辑后可能未满 500ms 就点扫描，
        # 此处立即保存确保扫描使用最新忽略配置
        self._content_panel.flush_pending_save()

        self._result_filter_panel.clear()
        self._detail_panel.clear()
        self._scan_state = ScanState.RUNNING
        self.progress.setRange(0, 0)
        self.current_file_label.setText("准备扫描...")
        self.stats_label.setText("准备扫描...")
        # 重置扫描中页列表与增量更新状态：避免上次扫描数据残留、快照干扰本次增量对比
        self._list_updater.reset()
        # 重置扫描中页的分类统计面板（需求6/7）
        self._update_scan_stats(0, 0, 0, 0, 0)
        self._switch_stage(WorkflowStage.SCANNING)

        cache, source_files = self._build_cache_context()
        self._worker = ScanWorker(
            ruleset=self._ruleset,
            roots=roots,
            scan_archives=self._config.scan_archives,
            max_workers=self._config.max_workers,
            max_depth=self._config.max_depth,
            max_file_size=self._config.max_file_size,
            ignore_dirs=tuple(self._config.ignore_dirs),
            ignore_extensions=tuple(self._config.ignore_extensions),
            cache=cache,
            source_files=source_files,
            scan_extensions=self._content_panel.enabled_extensions(),
            skip_paths=self._skip_store.paths(),
        )
        self._worker.progress_info.connect(self._on_scan_progress)  # pyrefly: ignore [missing-attribute]
        self._worker.finished_report.connect(self._on_scan_finished)  # pyrefly: ignore [missing-attribute]
        self._worker.failed.connect(self._on_scan_failed)  # pyrefly: ignore [missing-attribute]
        self._worker.cancelled.connect(self._on_scan_cancelled)  # pyrefly: ignore [missing-attribute]
        self._worker.start()

    def _build_cache_context(self) -> tuple[CacheStore | None, dict[Path, str] | None]:
        """构造扫描缓存上下文。

        根据配置启用缓存时惰性创建 CacheStore，并计算规则来源文件哈希映射。
        缓存对象在整个主窗口生命周期内复用，关闭窗口时统一释放。

        :returns: (cache, source_files)，禁用时均为 None
        """
        if not self._config.cache_enabled:
            return None, None
        if self._cache is None:
            # 延迟加载 cache 模块，避免主窗口启动时初始化 SQLite
            from fuscan.cache import CacheStore, default_cache_path

            cache_path = Path(self._config.cache_path) if self._config.cache_path else default_cache_path()
            self._cache = CacheStore(cache_path)
        # 同上，cache 模块按需加载
        from fuscan.cache import compute_source_files

        source_files = compute_source_files(self._rules_paths, use_builtin=self._use_builtin)
        return self._cache, source_files

    def _pause_scan(self) -> None:
        """暂停扫描。"""
        if self._worker is not None:
            self._worker.pause()
        self._scan_state = ScanState.PAUSED
        self.pause_resume_btn.setText("继续扫描")
        self.stats_label.setText("已暂停")

    def _resume_scan(self) -> None:
        """恢复扫描。"""
        if self._worker is not None:
            self._worker.resume()
        self._scan_state = ScanState.RUNNING
        self.pause_resume_btn.setText("暂停扫描")
        self.stats_label.setText("扫描中...")

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_scan_cancelled(self, report: ScanReport) -> None:
        """扫描被取消后的回调：有结果切结果页，无结果切配置页。"""
        self._last_report = report
        self._reset_scan_ui()
        self._populate_results(report)
        # 显式以 cancelled=True 渲染摘要，避免依赖 report.cancelled 字段
        # （scanner.scan 直接返回的 report 默认 cancelled=False）
        self.stats_label.setText(report.stats.summary(cancelled=True))
        if len(report.hits) > 0:
            self._switch_stage(WorkflowStage.RESULTS)
        else:
            self._switch_stage(WorkflowStage.SETUP)

    def _reset_scan_ui(self) -> None:
        """重置扫描 UI 到空闲状态。"""
        self._scan_state = ScanState.IDLE
        self._cancelling = False
        self.pause_resume_btn.setText("暂停扫描")
        # 重置进度条为确定模式（0/100），避免下次进入扫描页时残留 indeterminate 动画
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.current_file_label.setText("")
        self._cleanup_worker()

    def _cleanup_worker(self) -> None:
        """清理后台扫描线程：等待退出后释放引用。"""
        if self._worker is None:
            return
        self._worker.wait(2000)
        self._worker.deleteLater()
        self._worker = None

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_scan_progress(self, info) -> None:  # type: ignore[no-untyped-def]
        """扫描实时进度回调：更新进度条、当前文件、状态栏汇总与两个列表。

        根据 ``info.phase`` 显示不同阶段提示文案（iter-75）：walk 阶段
        突出"正在分析目录结构"，scan 阶段显示"正在解析"，archive 阶段
        显示"正在扫描压缩包"，避免用户在 walk 阶段（scanned=0）误以为
        扫描卡住。

        列表更新采用增量 append + 独立节流（0.5 秒），避免每次回调全量
        clear+重添 O(N) 阻塞主线程导致点击设置等交互卡滞。

        取消中状态（``_cancelling=True``）跳过全部 UI 更新，保留"取消中..."
        文案与不确定进度动画，避免扫描线程退出前的最终进度回调覆盖取消反馈。
        """
        if self._cancelling:
            return
        # 切换为确定进度模式（walk 阶段 total 仍在增长，scan 阶段 total 已固定）
        if info.total > 0 and self.progress.maximum() != info.total:
            self.progress.setRange(0, info.total)
        self.progress.setValue(info.scanned)

        # 当前文件（截断显示，挂载在状态栏右侧）：按 phase 切换前缀文案
        if info.current_file:
            path_text = info.current_file
            if len(path_text) > 100:
                path_text = "..." + path_text[-97:]
            prefix = _PHASE_LABELS.get(info.phase, "正在解析")
            self.current_file_label.setText(f"{prefix}: {path_text}")

        # 状态栏汇总文本（按 phase 切换文案，速度计算下沉到 ProgressInfo.summary）
        self.stats_label.setText(info.summary())

        # 列表更新下沉到 ScanListUpdater：0.5 秒节流 + 增量 append，避免高频回调阻塞主线程。
        # 仅在本次实际刷新列表时同步刷新分类统计面板（需求6/7），被节流跳过时不重复计算。
        if self._list_updater.try_update(info.skipped_dirs, info.matched_files):
            passed = max(info.scanned - info.matched - info.errors, 0)
            self._update_scan_stats(passed, info.matched, info.skipped, info.errors, info.user_skipped)

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_scan_finished(self, report: ScanReport) -> None:
        """扫描完成回调：填充结果并切换到结果页。"""
        self._last_report = report
        self._reset_scan_ui()

        self._populate_results(report)

        # 状态栏追加吞吐量与性能热点摘要（iter-66）
        summary = report.summary()
        speed = report.stats.speed
        if speed > 0:
            summary += f" | 速度 {speed:.0f} 文件/s"
        perf = report.stats.perf_summary
        if perf:
            # 构建简要热点文本：取总耗时前 3 阶段计算占比
            total_ms = sum(s.get("total_ms", 0.0) for s in perf.values()) or 1.0
            ranked = sorted(perf.items(), key=lambda x: -x[1].get("total_ms", 0.0))[:3]
            hotspots = " | ".join(f"{name} {info.get('total_ms', 0.0) / total_ms * 100:.0f}%" for name, info in ranked)
            summary += f" | 热点: {hotspots}"
        self.stats_label.setText(summary)
        self._switch_stage(WorkflowStage.RESULTS)

    @Slot(str)  # pyrefly: ignore [not-callable]
    def _on_scan_failed(self, error: str) -> None:
        """扫描失败回调：切回配置页并提示。"""
        self._reset_scan_ui()
        self.stats_label.setText("扫描失败")
        self._switch_stage(WorkflowStage.SETUP)
        QMessageBox.critical(self, "扫描失败", error)

    def _on_about(self) -> None:
        """关于对话框。"""
        QMessageBox.about(
            self,
            "关于 fuscan",
            (
                f"<h3>fuscan {__version__}</h3>"
                f"<p>{__description__}</p>"
                f"<p>基于 YAML 规则对多种格式文件进行内容匹配，"
                f"快速发现敏感信息、合规风险与代码安全问题；"
                f"支持压缩文件扫描与缓存加速。</p>"
                f"<p><b>技术栈</b>: Python + PySide</p>"
                f"<p><b>作者</b>: {__author__}<br>"
                f"<b>许可证</b>: {__license__}</p>"
            ),
        )

    def _on_open_manual(self) -> None:
        """打开用户手册 PDF（随包分发的 assets/docs/fuscan-用户手册.pdf）。

        使用系统默认 PDF 阅读器打开。PDF 缺失时提示用户并通过日志记录，
        不阻塞主流程。PDF 由 ``scripts/generate_manual_pdf.py`` 生成，
        版本升级时须重新生成（见 ``.trae/rules/rule-12-文档与版本发布.md``）。
        """
        url = QUrl.fromLocalFile(str(_MANUAL_PDF))
        if not _MANUAL_PDF.exists():
            logger.warning("用户手册 PDF 不存在: %s", _MANUAL_PDF)
            QMessageBox.information(
                self,
                "提示",
                f"用户手册 PDF 未找到:\n{_MANUAL_PDF}\n\n请运行 scripts/generate_manual_pdf.py 生成。",
            )
            return
        if not QDesktopServices.openUrl(url):
            logger.warning("无法打开用户手册 PDF: %s", _MANUAL_PDF)
            QMessageBox.warning(
                self,
                "打开失败",
                f"无法打开用户手册 PDF，请检查系统是否安装 PDF 阅读器:\n{_MANUAL_PDF}",
            )

    def _on_show_perf_stats(self) -> None:
        """展示本次扫描的性能统计详情（iter-66）。

        从最近一次 ScanReport 的 perf_summary 读取各阶段耗时，
        以表格形式展示在对话框中，支持保存为 JSON 文件。
        """
        if self._last_report is None or not self._last_report.stats.perf_summary:
            QMessageBox.information(self, "性能统计", "暂无性能统计数据，请先完成一次扫描。")
            return

        perf = self._last_report.stats.perf_summary
        stats = self._last_report.stats
        total_ms = sum(s.get("total_ms", 0.0) for s in perf.values()) or 1.0

        # 构建 HTML 表格
        rows = []
        for name, info in perf.items():
            pct = info.get("total_ms", 0.0) / total_ms * 100
            avg = info.get("total_ms", 0.0) / info.get("count", 1)
            rows.append(
                f"<tr>"
                f"<td>{name}</td>"
                f"<td style='text-align:right'>{info.get('total_ms', 0.0):.1f}</td>"
                f"<td style='text-align:right'>{pct:.1f}%</td>"
                f"<td style='text-align:right'>{info.get('count', 0)}</td>"
                f"<td style='text-align:right'>{avg:.2f}</td>"
                f"<td style='text-align:right'>{info.get('max_ms', 0.0):.1f}</td>"
                f"</tr>"
            )
        speed = stats.speed
        html = (
            f"<h3>性能统计</h3>"
            f"<p>扫描文件: {stats.scanned_files} | 耗时: {stats.duration_seconds:.2f}s | "
            f"速度: {speed:.0f} 文件/s</p>"
            f"<table border='1' cellspacing='0' cellpadding='4' style='border-collapse:collapse'>"
            f"<tr><th>阶段</th><th>总计(ms)</th><th>占比</th><th>调用次数</th>"
            f"<th>平均(ms)</th><th>最大(ms)</th></tr>"
            f"{''.join(rows)}"
            f"</table>"
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("性能统计")
        dialog.setMinimumSize(560, 400)  # pyrefly: ignore [missing-argument]
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml(html)
        layout.addWidget(text)  # pyrefly: ignore [missing-argument]

        from fuscan.perf import PerfStats

        save_btn = QPushButton("保存为 JSON...")
        report = self._last_report

        def _on_save() -> None:
            path, _ = QFileDialog.getSaveFileName(dialog, "保存性能统计", "fuscan-perf.json", "JSON (*.json)")
            if not path:
                return
            perf_obj = PerfStats()
            perf_obj.merge_dict(perf)
            perf_obj.save_to_json(
                Path(path),
                meta={
                    "scanned_files": stats.scanned_files,
                    "duration_seconds": stats.duration_seconds,
                    "speed_files_per_sec": round(speed, 1),
                    "root": str(report.root),
                },
            )
            QMessageBox.information(dialog, "已保存", f"性能统计已保存到:\n{path}")

        save_btn.clicked.connect(_on_save)
        layout.addWidget(save_btn)  # pyrefly: ignore [missing-argument]
        dialog.exec_()

    def _on_toggle_perf_log(self, enabled: bool) -> None:
        """切换 PerfTimer 详细日志开关（iter-66）。

        启用后输出各阶段进入/退出 DEBUG 日志，适合定向卡滞定位。
        PerfStats 聚合统计始终启用，不受此开关影响。
        状态持久化到配置文件（iter-69），下次启动自动恢复。
        """
        set_perf_enabled(enabled)
        self._config.perf_log_enabled = enabled
        self._save_config()
        if enabled:
            logger.info("已启用性能详细日志（PerfTimer），扫描日志将包含各阶段耗时")
        else:
            logger.info("已关闭性能详细日志")

    def _on_settings(self) -> None:
        """打开设置对话框，修改后保存配置并应用。"""
        # 延迟加载 GUI 子对话框，加速主窗口启动
        from fuscan.gui.settings_dialog import SettingsDialog

        prev_cache_enabled = self._config.cache_enabled
        prev_cache_path = self._config.cache_path
        dialog = SettingsDialog(self._config, self)
        if dialog.exec_() == QDialog.Accepted:
            self._save_config()
            self._set_use_builtin(self._config.use_builtin)
            self._scan_mode_panel.refresh_drives()
            # 缓存配置变更时释放旧 CacheStore，下次扫描按新配置重建
            cache_changed = not self._config.cache_enabled or self._config.cache_path != prev_cache_path
            if prev_cache_enabled and cache_changed and self._cache is not None:
                try:
                    self._cache.close()
                except (sqlite3.Error, OSError):
                    logger.warning("缓存关闭失败", exc_info=True)
                self._cache = None

    # ----------------------------- 详情区更新 -----------------------------

    def _on_result_selected(self, result: object) -> None:
        """结果树选中变化：更新详情区主体。

        由 :attr:`ResultTreeView.result_selected` 信号触发，``result`` 为
        :class:`ScanResult` 或 ``None``（空选/分组顶层项）。
        """
        if result is None:
            self._detail_panel.clear()
            return
        assert isinstance(result, ScanResult)
        logger.debug("选中结果项: %s, 命中数=%d", result.path, len(result.hits))
        self._detail_panel.show_result(result)
        # iter-77：根据 SkipStore 持久化状态同步「标记为跳过」按钮勾选与文案
        self._detail_panel.set_skip_state(self._skip_store.contains(str(result.path)))

    def _on_path_copy_requested(self, _path_str: str) -> None:
        """响应 DetailPanel 复制路径信号：更新状态栏提示。

        :param _path_str: 已复制到剪贴板的路径字符串（未使用，仅匹配信号签名）
        """
        self.stats_label.setText("已复制路径到剪贴板")

    def _on_open_location_requested(self, path: object) -> None:
        """响应 DetailPanel 打开文件位置信号：在文件管理器中定位文件。

        :param path: 待定位的文件路径（:class:`Path`）
        """
        assert isinstance(path, Path)
        self._open_path_in_explorer(path)

    def _resolve_staging_dir(self) -> Path:
        """解析暂存区目录路径：优先使用用户配置，否则探测剩余空间最大的盘符。

        :return: 暂存区目录路径（已确保存在，调用方可直接写入）
        """
        configured = self._config.staging_dir
        if configured:
            staging = Path(configured)
        else:
            staging = detect_default_staging_dir()
        staging.mkdir(parents=True, exist_ok=True)
        return staging

    def _on_move_to_staging(self, result: object) -> None:
        """响应 DetailPanel 移动至暂存区信号：将文件移动到暂存区目录（iter-77）。

        移动策略：保留原文件名，若目标已存在同名文件则追加 ``.1``/``.2`` 序号避免覆盖。
        移动成功后从结果树移除该项并刷新详情区。

        :param result: 待移动的扫描结果（:class:`ScanResult`）
        """
        assert isinstance(result, ScanResult)
        src = result.path
        if not src.exists():
            QMessageBox.warning(self, "提示", f"源文件不存在:\n{src}")
            return
        try:
            staging = self._resolve_staging_dir()
        except OSError as exc:
            logger.error("创建暂存区目录失败", exc_info=True)
            QMessageBox.warning(self, "提示", f"创建暂存区目录失败:\n{exc}")
            return
        # 目标路径：保留原文件名，冲突时追加序号
        dest = staging / src.name
        if dest.exists():
            for i in range(1, 10000):
                candidate = staging / f"{src.stem}.{i}{src.suffix}"
                if not candidate.exists():
                    dest = candidate
                    break
        try:
            src.replace(dest)
        except OSError as exc:
            logger.warning("移动文件至暂存区失败: %s -> %s", src, dest, exc_info=True)
            QMessageBox.warning(self, "提示", f"移动文件失败:\n{exc}")
            return
        logger.info("已移动文件至暂存区: %s -> %s", src, dest)
        self.stats_label.setText(f"已移动至暂存区: {dest}")
        # 从最近一次扫描报告中移除该结果并刷新结果树
        self._remove_result_from_report(src)

    def _on_toggle_skip(self, result: object) -> None:
        """响应 DetailPanel 切换跳过标记信号：在 SkipStore 中添加/移除路径（iter-77）。

        按钮的 checked 状态不作为判断依据——以 SkipStore 当前持久化状态为准取反，
        避免按钮状态与持久化存储不一致。处理完成后通过 :meth:`set_skip_state`
        同步按钮文案。

        :param result: 待切换跳过标记的扫描结果（:class:`ScanResult`）
        """
        assert isinstance(result, ScanResult)
        path_str = str(result.path)
        if self._skip_store.contains(path_str):
            self._skip_store.remove(path_str)
            self._detail_panel.set_skip_state(False)
            self.stats_label.setText(f"已取消跳过: {result.path.name}")
            logger.info("取消跳过标记: %s", path_str)
        else:
            self._skip_store.add(path_str)
            self._detail_panel.set_skip_state(True)
            self.stats_label.setText(f"已标记跳过: {result.path.name}")
            logger.info("标记跳过: %s", path_str)

    def _remove_result_from_report(self, path: Path) -> None:
        """从最近一次扫描报告中移除指定路径的结果并刷新结果树（iter-77）。

        移动至暂存区或标记跳过后调用，使结果树与实际文件状态保持一致。

        :param path: 待移除的文件路径
        """
        if self._last_report is None:
            return
        new_results = tuple(r for r in self._last_report.results if r.path != path)
        if len(new_results) == len(self._last_report.results):
            return  # 未找到对应结果，无需刷新
        self._last_report = ScanReport(
            root=self._last_report.root,
            results=new_results,
            stats=self._last_report.stats,
            cancelled=self._last_report.cancelled,
        )
        # 刷新结果树（panel 通过 report_getter 回调读取最新 _last_report）
        self._result_filter_panel.refresh()
        # 移除后详情区清空（已无对应文件可展示）
        self._detail_panel.clear()

    def _open_path_in_explorer(self, path: Path) -> None:
        """在文件管理器中打开指定文件所在目录并选中该文件。

        跨平台命令分派委托给 :func:`fuscan.gui.explorer.open_path_in_explorer`，
        本方法仅负责异常捕获与用户提示。失败时弹 warning 不抛异常。

        :param path: 待定位的文件路径
        """
        try:
            open_path_in_explorer(path)
        except OSError as exc:
            logger.warning("打开文件位置失败: %s", exc, exc_info=True)
            QMessageBox.warning(self, "提示", f"打开文件位置失败:\n{exc}")

    def _on_matched_file_double_clicked(self, item: QListWidgetItem) -> None:
        """扫描中页命中文件列表双击：弹出简化详情并提供文件定位按钮（需求5）。

        列表项格式为 ``"路径 → 规则名"``，从右侧分割一次以容忍路径中含 ``" → "`` 的
        极端情况。用 ``QMessageBox.question`` 静态方法提供"打开"/"关闭"两个标准按钮，
        选中"打开"时委托 :meth:`_open_path_in_explorer` 跨平台定位。
        """
        text = item.text()
        if " → " not in text:
            return
        file_path_str, rule_name = text.rsplit(" → ", 1)
        reply = QMessageBox.question(
            self,
            "命中详情",
            f"文件路径:\n{file_path_str}\n\n命中规则: {rule_name}",
            QMessageBox.Open | QMessageBox.Close,
            QMessageBox.Close,
        )
        if reply == QMessageBox.Open:
            self._open_path_in_explorer(Path(file_path_str))

    # ----------------------------- 辅助方法 -----------------------------

    def _refresh_rules_tree(self) -> None:
        """刷新规则列表展示。"""
        self.rules_tree.clear()
        if self._ruleset is None:
            return
        for rule in self._ruleset.rules:
            item = QTreeWidgetItem(
                [
                    rule.name,
                    "",
                    # iter-71：file_extensions 已废弃，所有规则对全局过滤后的文件均适用
                    "(全局)",
                ]
            )
            _apply_severity_to_tree_item(item, 1, rule.severity)
            self.rules_tree.addTopLevelItem(item)

    # 规则文件列表相关方法（_refresh_rules_file_list / _on_move_rule_up /
    # _on_move_rule_down / _on_remove_rule / _on_rules_file_item_changed /
    # _on_rules_file_list_context_menu）已移到 RulesFilePanel（iter-79 续解耦），
    # 主窗口通过 self._rules_panel 公共 API 驱动，rules_changed 信号触发
    # _on_rules_changed 统一处理重载与持久化

    def _on_edit_rules(self) -> None:
        """打开规则编辑器对话框。"""
        # 延迟加载 GUI 子对话框，加速主窗口启动
        from fuscan.gui.rule_editor import RuleEditorDialog

        if not self._rules_paths:
            QMessageBox.information(self, "提示", "未加载任何规则文件，请先加载规则。")
            return
        dialog = RuleEditorDialog(self._rules_paths, self)
        dialog.rules_saved.connect(self._on_rules_saved)  # pyrefly: ignore [missing-attribute]
        dialog.exec_()

    def _on_rules_saved(self, _path: str) -> None:
        """规则文件保存后重新加载规则集。"""
        self._reload_and_refresh()

    def _reload_and_refresh(self) -> None:
        """重新加载规则集并刷新相关 UI 组件。"""
        try:
            self._reload_ruleset()
            self._refresh_rules_tree()
            self._update_scan_button()
            if self._ruleset is not None:
                self.stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条规则")
            else:
                self.stats_label.setText("未加载规则")
        except RuleError as exc:
            QMessageBox.warning(self, "规则错误", f"重新加载规则失败:\n{exc}")

    def _populate_results(self, report: ScanReport) -> None:
        """填充结果树：存储报告、更新规则筛选下拉、刷新结果树。

        委托 :class:`ResultFilterPanel` 一站式完成（停止节流 + populate +
        更新规则下拉 + 刷新），主窗口仅保留报告引用与导出按钮状态同步。
        """
        self._last_report = report
        self._result_filter_panel.populate(report)
        # 有结果时启用导出按钮
        self.export_btn.setEnabled(len(report.hits) > 0)

    def _update_scan_button(self) -> None:
        """更新扫描按钮状态（委托给 _update_stage_actions 统一管理）。"""
        self._update_stage_actions()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """关闭时保存配置、释放缓存并终止后台线程。"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._export_controller.cleanup()
        if self._cache is not None:
            try:
                self._cache.close()
            except (sqlite3.Error, OSError):
                logger.warning("缓存关闭失败", exc_info=True)
            self._cache = None
        self._save_config()
        super().closeEvent(event)
