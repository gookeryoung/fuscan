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
- 结果以 QTreeWidget 展示（QAbstractItemView 迁移见后续迭代）
- 详情区嵌入命中预览（文件信息+命中表+内容预览+命中导航+备注+导出）
"""

from __future__ import annotations

import enum
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

try:
    from PySide2.QtCore import QByteArray, QPoint, QSize, Qt
    from PySide2.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QTextCharFormat, QTextCursor
    from PySide2.QtSvg import QSvgRenderer
    from PySide2.QtWidgets import (
        QAbstractButton,
        QAction,
        QApplication,
        QButtonGroup,
        QDialog,
        QFileDialog,
        QHeaderView,
        QInputDialog,
        QLabel,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QShortcut,
        QTableWidgetItem,
        QTextEdit,
        QTreeWidgetItem,
        QWidget,
    )
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QPoint, QSize, Qt
    from PySide6.QtGui import (
        QAction,
        QColor,
        QIcon,
        QKeySequence,
        QShortcut,
        QTextCharFormat,
        QTextCursor,
    )
    from PySide6.QtWidgets import (
        QAbstractButton,
        QApplication,
        QButtonGroup,
        QDialog,
        QFileDialog,
        QHeaderView,
        QInputDialog,
        QLabel,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QTableWidgetItem,
        QTextEdit,
        QTreeWidgetItem,
        QWidget,
    )

from fuscan import __version__, theme
from fuscan.builtin import load_with_builtin
from fuscan.config import MAX_HISTORY, Config, load_config, save_config
from fuscan.extractors import extract_content_with_fallback
from fuscan.gui.detail_dialog import HitDetailDialog
from fuscan.gui.main_window_ui import Ui_MainWindow
from fuscan.gui.preview_utils import (
    PREVIEW_MAX_CHARS,
    SEVERITY_COLORS,
    SEVERITY_LABELS,
    build_keyword_to_rule_map,
    build_preview_html,
    compile_keyword_pattern,
    extract_keywords,
)
from fuscan.gui.worker import ScanWorker
from fuscan.rules import RuleError, load_ruleset, merge_multiple_rulesets
from fuscan.rules.model import RuleSet, Severity
from fuscan.scanner import ScanReport, list_drives
from fuscan.scanner.result import RuleHit, ScanResult

if TYPE_CHECKING:
    from fuscan.cache import CacheStore

__all__ = ["MainWindow", "ScanState", "WorkflowStage"]

logger = logging.getLogger(__name__)

# 严重等级 → 背景色（浅色，用于整行高亮）
_SEVERITY_BACKGROUNDS: dict[Severity, QColor] = {
    Severity.CRITICAL: QColor(255, 235, 235),  # 浅红
    Severity.WARNING: QColor(255, 243, 224),  # 浅橙
    Severity.INFO: QColor(235, 244, 255),  # 浅蓝
}

# 严重等级 → 排序权重（CRITICAL 优先）
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.CRITICAL: 2,
}


def _severity_text(severity: Severity) -> str:
    """返回严重等级的中文标签。"""
    return SEVERITY_LABELS.get(severity, severity.value)


def _apply_severity_to_tree_item(item: QTreeWidgetItem, column: int, severity: Severity) -> None:
    """为 QTreeWidgetItem 的指定列设置中文标签、前景色和背景色。"""
    item.setText(column, _severity_text(severity))
    item.setForeground(column, SEVERITY_COLORS[severity])
    item.setBackground(column, _SEVERITY_BACKGROUNDS[severity])


def _apply_severity_to_table_item(item: QTableWidgetItem, severity: Severity) -> None:
    """为 QTableWidgetItem 设置中文标签、前景色和背景色。"""
    item.setText(_severity_text(severity))
    item.setForeground(SEVERITY_COLORS[severity])
    item.setBackground(_SEVERITY_BACKGROUNDS[severity])


# 图标路径（assets/icons 目录下）
_ICONS_DIR = Path(__file__).parent.parent / "assets" / "icons"
_ICON_ABOUT = str(_ICONS_DIR / "about.svg")
_ICON_ALL_DISK = str(_ICONS_DIR / "all_disk.svg")
_ICON_DISK = str(_ICONS_DIR / "disk.svg")
_ICON_EDIT = str(_ICONS_DIR / "edit.svg")
_ICON_EXPORT = str(_ICONS_DIR / "export.svg")
_ICON_EXPORT_CSV = str(_ICONS_DIR / "export_csv.svg")
_ICON_EXPORT_JSON = str(_ICONS_DIR / "export_json.svg")
_ICON_FOLDER = str(_ICONS_DIR / "folder.svg")
_ICON_HARD_DISK = str(_ICONS_DIR / "hard_disk.svg")
_ICON_HISTORY = str(_ICONS_DIR / "history.svg")
_ICON_LOAD_LIST = str(_ICONS_DIR / "load_list.svg")
_ICON_PAUSE = str(_ICONS_DIR / "pause.svg")
_ICON_RESCAN = str(_ICONS_DIR / "rescan.svg")
_ICON_SCAN = str(_ICONS_DIR / "scan.svg")
_ICON_SETTINGS = str(_ICONS_DIR / "settings.svg")
_ICON_STOP = str(_ICONS_DIR / "stop.svg")

# 主题图标渲染分辨率（高分辨率保证 DPI 缩放下清晰）
_ICON_RENDER_SIZE = 128
# 移除 SVG 中所有 fill="..." 属性的正则
_SVG_FILL_RE = re.compile(r'\sfill="[^"]*"')


def _load_themed_icon(svg_path: str, color: str) -> QIcon:
    """加载 SVG 文件并以指定主题色着色后返回 QIcon。

    读取 SVG 文本后:(1) 移除所有 fill 属性消除原色;(2) 在根 <svg> 标签注入
    ``fill="<color>"`` 作为默认填充色;(3) 通过 QSvgRenderer 渲染到透明 QPixmap
    后构造 QIcon。主题色变更时需重新调用本函数重建图标。

    :param svg_path: SVG 文件绝对路径
    :param color: 主题色 hex 字符串（如 ``theme.COLOR_PRIMARY``）
    :returns: 已着色的 QIcon，渲染失败时回退到原始文件加载
    """
    try:
        text = Path(svg_path).read_text(encoding="utf-8")
        # 移除所有 fill 属性，确保主题色统一覆盖原图标颜色
        text = _SVG_FILL_RE.sub("", text)
        # 在首个 <svg ...> 开标签内注入 fill 属性作为默认填充
        text = re.sub(
            r"(<svg\b[^>]*?)(/?>)",
            rf'\1 fill="{color}"\2',
            text,
            count=1,
        )
        renderer = QSvgRenderer(QByteArray(text.encode("utf-8")))
        if not renderer.isValid():
            return QIcon(svg_path)
        pixmap = QPixmap(_ICON_RENDER_SIZE, _ICON_RENDER_SIZE)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)
    except (OSError, ValueError):
        logger.warning("主题图标加载失败，回退原始文件: %s", svg_path, exc_info=True)
        return QIcon(svg_path)


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


class MainWindow(QMainWindow, Ui_MainWindow):
    """主窗口：扫描器 GUI 入口，基于工作流阶段的三页整页切换布局。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setupUi(self)

        self._config: Config = load_config()
        self._ruleset: RuleSet | None = None
        self._rules_paths: list[Path] = []
        self._scan_root: Path | None = None
        self._last_report: ScanReport | None = None
        self._worker: ScanWorker | None = None
        self._scan_state: ScanState = ScanState.IDLE
        self._workflow_stage: WorkflowStage = WorkflowStage.SETUP
        self._use_builtin: bool = True
        # 扫描模式："full"（全盘）、"drive"（盘符）、"folder"（文件夹）
        self._scan_mode: str = "folder"
        # 详情区命中导航状态：每个位置记录 (start, end, rule_index)
        self._detail_hit_positions: list[tuple[int, int, int]] = []
        self._detail_current_hit_index: int = -1
        self._detail_current_result: ScanResult | None = None
        # 扫描历史记录
        self._scan_history: list[str] = []
        # 盘符按钮组（平铺选择，替代下拉）
        self._drive_button_group: QButtonGroup | None = None
        self._drive_buttons: list[QPushButton] = []
        self._selected_drive: str | None = None
        # 扫描结果缓存（启用时惰性创建，关闭窗口时释放）
        self._cache: CacheStore | None = None
        # 扫描中列表增量更新状态：记录上次已显示的列表快照与节流时间戳，
        # 避免每次进度回调全量 clear+重添导致主线程阻塞（点击设置卡滞根因）
        self._last_skipped_dirs: tuple[str, ...] = ()
        self._last_matched_files: tuple[tuple[str, str], ...] = ()
        # 初始 -1.0 确保首次回调不被节流（time.perf_counter 在新进程可能返回小值）
        self._last_list_update_time: float = -1.0

        self._configure_ui()
        self._apply_config()
        self._init_rules()

    # ----------------------------- UI 配置 -----------------------------

    def _configure_ui(self) -> None:
        """配置 .ui 无法静态表达的动态属性、layout stretch 与信号槽连接。"""
        self._setup_status_bar()
        self._setup_results_tree()
        self._setup_detail_table()
        self._setup_comboboxes()
        self._setup_splitters()
        self._setup_layouts()
        self._setup_icons()
        self._setup_button_groups()
        self._setup_sidebar()
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

    def _setup_results_tree(self) -> None:
        """设置结果树列宽（.ui 不支持每列独立宽度）。"""
        self.result_tree.setColumnWidth(0, 400)
        self.result_tree.setColumnWidth(1, 150)
        self.result_tree.setColumnWidth(2, 80)
        self.result_tree.setColumnWidth(3, 60)
        self.result_tree.setColumnWidth(4, 60)

    def _setup_detail_table(self) -> None:
        """设置详情区命中表：全列拉伸 + 行点击信号（editTriggers/selectionBehavior 已在 .ui 中声明）。"""
        self.detail_hits_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.detail_hits_table.cellClicked.connect(self._on_detail_hits_row_clicked)

    def _setup_comboboxes(self) -> None:
        """填充 QComboBox 初始项（带 userData，.ui 不便表达）。"""
        self.rule_filter_combo.addItem("全部规则", "")
        self.group_mode_combo.addItem("不分组", "flat")
        self.group_mode_combo.addItem("按规则", "rule")
        self.group_mode_combo.addItem("按严重等级", "severity")

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
        # 配置页：target_group 自然尺寸 + setup_action_bar 紧随其后 + 底部弹簧填充剩余空间
        self.setup_layout.setStretch(0, 0)
        self.setup_layout.setStretch(1, 0)
        self.setup_layout.addStretch()
        self.setup_layout.setStretch(2, 1)
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

    def _setup_icons(self) -> None:
        """加载主题图标并设置到各按钮、菜单 actions 与下拉项。"""
        # 主色变体（浅色背景）
        self._icon_scan = _load_themed_icon(_ICON_SCAN, theme.COLOR_PRIMARY)
        self._icon_pause = _load_themed_icon(_ICON_PAUSE, theme.COLOR_PRIMARY)
        self._icon_rescan = _load_themed_icon(_ICON_RESCAN, theme.COLOR_PRIMARY)
        self._icon_all_disk = _load_themed_icon(_ICON_ALL_DISK, theme.COLOR_PRIMARY)
        self._icon_disk = _load_themed_icon(_ICON_DISK, theme.COLOR_PRIMARY)
        self._icon_folder = _load_themed_icon(_ICON_FOLDER, theme.COLOR_PRIMARY)
        self._icon_history = _load_themed_icon(_ICON_HISTORY, theme.COLOR_PRIMARY)
        self._icon_load_list = _load_themed_icon(_ICON_LOAD_LIST, theme.COLOR_PRIMARY)
        self._icon_hard_disk = _load_themed_icon(_ICON_HARD_DISK, theme.COLOR_PRIMARY)
        self._icon_edit = _load_themed_icon(_ICON_EDIT, theme.COLOR_PRIMARY)
        self._icon_export = _load_themed_icon(_ICON_EXPORT, theme.COLOR_PRIMARY)
        self._icon_export_csv = _load_themed_icon(_ICON_EXPORT_CSV, theme.COLOR_PRIMARY)
        self._icon_export_json = _load_themed_icon(_ICON_EXPORT_JSON, theme.COLOR_PRIMARY)
        self._icon_settings = _load_themed_icon(_ICON_SETTINGS, theme.COLOR_PRIMARY)
        self._icon_about = _load_themed_icon(_ICON_ABOUT, theme.COLOR_PRIMARY)
        self._icon_stop = _load_themed_icon(_ICON_STOP, theme.COLOR_PRIMARY)
        # 深色背景（头部栏/侧边栏）专用白色变体
        self._icon_scan_on_primary = _load_themed_icon(_ICON_SCAN, theme.COLOR_TEXT_ON_PRIMARY)
        self._icon_folder_on_primary = _load_themed_icon(_ICON_FOLDER, theme.COLOR_TEXT_ON_PRIMARY)
        self._icon_history_on_primary = _load_themed_icon(_ICON_HISTORY, theme.COLOR_TEXT_ON_PRIMARY)
        self._icon_load_list_on_primary = _load_themed_icon(_ICON_LOAD_LIST, theme.COLOR_TEXT_ON_PRIMARY)
        self._icon_settings_on_primary = _load_themed_icon(_ICON_SETTINGS, theme.COLOR_TEXT_ON_PRIMARY)
        self._icon_about_on_primary = _load_themed_icon(_ICON_ABOUT, theme.COLOR_TEXT_ON_PRIMARY)
        # 应用到扫描控制按钮
        self.scan_btn.setIcon(self._icon_scan)
        self.scan_mode_combo.setItemIcon(0, self._icon_all_disk)
        self.scan_mode_combo.setItemIcon(1, self._icon_disk)
        self.scan_mode_combo.setItemIcon(2, self._icon_folder)
        # 加载规则按钮与菜单
        self.load_rules_btn.setIcon(self._icon_load_list)
        self.load_rules_action.setIcon(self._icon_load_list)
        self.scan_action.setIcon(self._icon_scan)
        self.edit_rule_btn.setIcon(self._icon_edit)
        self.edit_rules_action.setIcon(self._icon_edit)
        self.export_btn.setIcon(self._icon_export)
        self.export_csv_action.setIcon(self._icon_export_csv)
        self.export_json_action.setIcon(self._icon_export_json)
        self.settings_action.setIcon(self._icon_settings)
        self.about_action.setIcon(self._icon_about)
        self.rescan_btn.setIcon(self._icon_rescan)
        self.cancel_btn.setIcon(self._icon_stop)
        self.pause_resume_btn.setIcon(self._icon_pause)
        # 头部栏按钮（深色背景用白色变体，rule-12 HeaderBar）
        self.tab_scan_btn.setIcon(self._icon_scan_on_primary)
        self.tab_rules_btn.setIcon(self._icon_load_list_on_primary)
        self.tab_history_btn.setIcon(self._icon_history_on_primary)
        self.settings_btn.setIcon(self._icon_settings_on_primary)
        self.about_btn.setIcon(self._icon_about_on_primary)

    def _setup_button_groups(self) -> None:
        """初始化头部 Tab 按钮互斥组与盘符按钮组。"""
        # 头部 Tab 按钮互斥组（id 0=扫描 / 1=规则 / 2=历史）
        self._header_button_group = QButtonGroup(self)
        self._header_button_group.setExclusive(True)
        self._header_button_group.addButton(self.tab_scan_btn, 0)
        self._header_button_group.addButton(self.tab_rules_btn, 1)
        self._header_button_group.addButton(self.tab_history_btn, 2)
        # 盘符按钮组（平铺选择，替代下拉）
        self._drive_button_group = QButtonGroup(self)
        self._drive_button_group.setExclusive(True)
        self._drive_button_group.buttonClicked.connect(self._on_drive_selected)
        self._refresh_drive_buttons()

    def _setup_sidebar(self) -> None:
        """填充侧边栏阶段项（深色背景用白色变体；配置 / 扫描中 / 结果）。"""
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        self.sidebar.addItem(QListWidgetItem(self._icon_folder_on_primary, "配置"))
        self.sidebar.addItem(QListWidgetItem(self._icon_scan_on_primary, "扫描中"))
        self.sidebar.addItem(QListWidgetItem(self._icon_history_on_primary, "结果"))
        self.sidebar.setCurrentRow(0)
        self.sidebar.blockSignals(False)

    def _connect_signals(self) -> None:
        """连接所有信号槽（按钮、actions、worker、头部栏与侧边栏）。"""
        # 扫描控制
        self.scan_btn.clicked.connect(self._on_scan)
        self.view_results_btn.clicked.connect(self._on_view_results)
        self.pause_resume_btn.clicked.connect(self._on_pause_resume)
        self.cancel_btn.clicked.connect(self._on_cancel_scan)
        self.rescan_btn.clicked.connect(self._on_rescan)
        # 扫描目标
        self.scan_mode_combo.currentIndexChanged.connect(self._on_scan_mode_changed)
        self.path_combo.currentIndexChanged.connect(self._on_path_selected)
        self.select_path_btn.clicked.connect(self._on_select_path)
        # 规则
        self.load_rules_btn.clicked.connect(self._on_load_rules)
        self.edit_rule_btn.clicked.connect(self._on_edit_rules)
        # 结果树
        self.result_tree.itemDoubleClicked.connect(self._on_result_double_clicked)
        self.result_tree.itemSelectionChanged.connect(self._on_result_selection_changed)
        # 筛选
        self.path_filter_input.textChanged.connect(self._refresh_result_tree)
        self.rule_filter_combo.currentIndexChanged.connect(self._refresh_result_tree)
        self.group_mode_combo.currentIndexChanged.connect(self._refresh_result_tree)
        # 历史
        self.history_list.itemDoubleClicked.connect(self._on_history_item_double_clicked)
        # 详情区
        self.export_btn.clicked.connect(self._on_export_menu)
        self.detail_prev_btn.clicked.connect(self._on_prev_detail_hit)
        self.detail_next_btn.clicked.connect(self._on_next_detail_hit)
        self.detail_open_location_btn.clicked.connect(self._on_open_file_location)
        # 头部栏与侧边栏（rule-12 HeaderBar + Sidebar）
        self._header_button_group.idClicked.connect(self._on_header_tab_changed)
        self.sidebar.currentRowChanged.connect(self._on_sidebar_stage_changed)
        self.settings_btn.clicked.connect(self._on_settings)
        self.about_btn.clicked.connect(self._on_about)
        # actions
        self.load_rules_action.triggered.connect(self._on_load_rules)
        self.edit_rules_action.triggered.connect(self._on_edit_rules)
        self.export_csv_action.triggered.connect(lambda: self._on_export("csv"))
        self.export_json_action.triggered.connect(lambda: self._on_export("json"))
        self.quit_action.triggered.connect(self.close)
        self.select_path_action.triggered.connect(self._on_select_path)
        self.scan_action.triggered.connect(self._on_scan)
        self.about_action.triggered.connect(self._on_about)
        self.settings_action.triggered.connect(self._on_settings)

    def _setup_context_menus(self) -> None:
        """为结果树和规则文件列表配置右键菜单策略。"""
        self.result_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_tree.customContextMenuRequested.connect(self._on_result_tree_context_menu)
        self.rules_file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.rules_file_list.customContextMenuRequested.connect(self._on_rules_file_list_context_menu)

    def _on_result_tree_context_menu(self, pos: QPoint) -> None:  # type: ignore[unknown-name]
        """结果树右键菜单：复制路径 / 在新窗口打开 / 打开文件位置。"""
        if self._detail_current_result is None:
            return
        menu = QMenu(self.result_tree)
        action_copy = QAction("复制路径", menu)
        action_open_window = QAction("在新窗口打开", menu)
        action_open_location = QAction("打开文件位置", menu)
        action_copy.triggered.connect(self._on_copy_path)
        action_open_window.triggered.connect(self._on_open_in_window)
        action_open_location.triggered.connect(self._on_open_file_location)
        menu.addAction(action_copy)
        menu.addAction(action_open_window)
        menu.addAction(action_open_location)
        menu.exec_(self.result_tree.viewport().mapToGlobal(pos))

    def _on_rules_file_list_context_menu(self, pos: QPoint) -> None:  # type: ignore[unknown-name]
        """规则文件列表右键菜单：上移 / 下移 / 移除。"""
        if self.rules_file_list.currentRow() < 0:
            return
        menu = QMenu(self.rules_file_list)
        action_up = QAction("上移", menu)
        action_down = QAction("下移", menu)
        action_remove = QAction("移除", menu)
        action_up.triggered.connect(self._on_move_rule_up)
        action_down.triggered.connect(self._on_move_rule_down)
        action_remove.triggered.connect(self._on_remove_rule)
        menu.addAction(action_up)
        menu.addAction(action_down)
        menu.addSeparator()
        menu.addAction(action_remove)
        menu.exec_(self.rules_file_list.viewport().mapToGlobal(pos))

    def _setup_shortcuts(self) -> None:
        """创建全局快捷键：F3 下一条命中、Shift+F3 上一条命中、Delete 移除规则文件。"""
        self._shortcut_next = QShortcut(QKeySequence("F3"), self)
        self._shortcut_next.activated.connect(self._on_next_detail_hit)
        self._shortcut_prev = QShortcut(QKeySequence("Shift+F3"), self)
        self._shortcut_prev.activated.connect(self._on_prev_detail_hit)
        self._shortcut_remove_rule = QShortcut(QKeySequence.Delete, self.rules_file_list)
        self._shortcut_remove_rule.activated.connect(self._on_remove_rule)

    def _set_use_builtin(self, enabled: bool) -> None:
        """统一设置通用规则开关并刷新规则集。

        替代原 _on_toggle_builtin 的散落逻辑，供 _on_settings 和测试统一调用。
        """
        self._use_builtin = enabled
        try:
            self._reload_ruleset()
            self._refresh_rules_tree()
            self._refresh_rules_file_list()
            self._update_scan_button()
            if self._ruleset is not None:
                self.stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条规则")
            else:
                self.stats_label.setText("未加载规则")
        except RuleError as exc:
            QMessageBox.warning(self, "规则错误", f"重新加载规则失败:\n{exc}")

    # ----------------------------- 工作流阶段切换 -----------------------------

    def _switch_stage(self, stage: WorkflowStage) -> None:
        """切换工作流阶段页面并更新控件状态。

        SETUP=0 配置页、SCANNING=1 扫描中页、RESULTS=2 结果页。
        同步侧边栏选中项，避免循环触发信号。
        """
        self._workflow_stage = stage
        page_index = {
            WorkflowStage.SETUP: 0,
            WorkflowStage.SCANNING: 1,
            WorkflowStage.RESULTS: 2,
        }[stage]
        self.main_stack.setCurrentIndex(page_index)
        self.sidebar.blockSignals(True)
        self.sidebar.setCurrentRow(page_index)
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
        stage_map = {0: WorkflowStage.SETUP, 1: WorkflowStage.SCANNING, 2: WorkflowStage.RESULTS}
        stage = stage_map.get(row)
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

        # 结果页
        self.rescan_btn.setEnabled(is_results)
        if is_results and has_report:
            self.export_btn.setEnabled(len(self._last_report.hits) > 0)
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
        if self._scan_mode == "full":
            return True
        if self._scan_mode == "drive":
            return self._selected_drive is not None
        return self._scan_root is not None

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
        """扫描中页"取消扫描"按钮：取消后台扫描。"""
        if self._worker is not None:
            self._worker.cancel()

    # ----------------------------- 配置持久化 -----------------------------

    def _apply_config(self) -> None:
        """应用配置：恢复窗口几何、分割器、扫描模式、规则路径、扫描历史。"""
        min_w, min_h = self.minimumSize().width(), self.minimumSize().height()

        if self._config.window_geometry and len(self._config.window_geometry) == 4:
            x, y, w, h = self._config.window_geometry
            w = max(w, min_w)
            h = max(h, min_h)

            screen_geo = QApplication.primaryScreen().availableGeometry()
            if screen_geo.width() > w:
                x = max(0, min(x, screen_geo.width() - w))
            if screen_geo.height() > h:
                y = max(0, min(y, screen_geo.height() - h))

            self.setGeometry(x, y, w, h)
        else:
            screen_geo = QApplication.primaryScreen().availableGeometry()
            w, h = self.size().width(), self.size().height()
            if screen_geo.width() > w and screen_geo.height() > h:
                x = (screen_geo.width() - w) // 2
                y = (screen_geo.height() - h) // 2
                self.move(x, y)

        if self._config.window_state == "maximized":
            self.showMaximized()

        if self._config.splitter_sizes:
            self.results_splitter.setSizes(self._config.splitter_sizes)

        # 恢复扫描模式
        self._scan_mode = self._config.scan_mode if self._config.scan_mode in ("full", "drive", "folder") else "folder"
        mode_index_map = {"full": 0, "drive": 1, "folder": 2}
        self.scan_mode_combo.blockSignals(True)
        self.scan_mode_combo.setCurrentIndex(mode_index_map[self._scan_mode])
        self.scan_mode_combo.blockSignals(False)
        self._update_target_visibility()

        # 恢复上次选择的盘符
        if self._config.last_drive:
            target = self._config.last_drive
            for btn in self._drive_buttons:
                if btn.property("drive") == target:
                    btn.setChecked(True)
                    self._selected_drive = target
                    break

        self._use_builtin = self._config.use_builtin

        self._rules_paths = [Path(p) for p in self._config.rules_paths if Path(p).exists()]

        self.path_combo.blockSignals(True)
        for p in self._config.scan_paths:
            self.path_combo.addItem(p)
        self.path_combo.blockSignals(False)

        # 恢复扫描历史
        self._scan_history = list(self._config.scan_paths)
        self._refresh_history_list()

        # 恢复首个有效路径作为扫描目标，启用扫描按钮
        if self._scan_mode == "folder" and self.path_combo.count() > 0:
            first_path = Path(self.path_combo.itemText(0))
            self._scan_root = first_path if first_path.exists() else None
        self._update_scan_button()

    def _save_config(self) -> None:
        """保存当前状态到配置文件。"""
        geo = self.geometry()
        self._config.window_geometry = [geo.x(), geo.y(), geo.width(), geo.height()]
        self._config.window_state = "maximized" if self.isMaximized() else "normal"
        self._config.splitter_sizes = list(self.results_splitter.sizes())
        self._config.scan_mode = self._scan_mode
        self._config.last_drive = self._selected_drive
        self._config.rules_paths = [str(p) for p in self._rules_paths]
        self._config.use_builtin = self._use_builtin
        self._config.scan_paths = [self.path_combo.itemText(i) for i in range(self.path_combo.count())]
        save_config(self._config)

    def _add_scan_path_history(self, path_str: str) -> None:
        """将路径添加到扫描历史下拉与历史列表（去重、最近优先、限制数量）。"""
        self.path_combo.blockSignals(True)
        idx = self.path_combo.findText(path_str)
        if idx >= 0:
            self.path_combo.removeItem(idx)
        self.path_combo.insertItem(0, path_str)
        while self.path_combo.count() > MAX_HISTORY:
            self.path_combo.removeItem(self.path_combo.count() - 1)
        self.path_combo.setCurrentIndex(0)
        self.path_combo.blockSignals(False)

        # 同步扫描历史
        if path_str in self._scan_history:
            self._scan_history.remove(path_str)
        self._scan_history.insert(0, path_str)
        while len(self._scan_history) > MAX_HISTORY:
            self._scan_history.pop()
        self._refresh_history_list()

    def _refresh_history_list(self) -> None:
        """刷新扫描历史列表。"""
        self.history_list.clear()
        for path_str in self._scan_history:
            item = QListWidgetItem(path_str)
            item.setToolTip(path_str)
            self.history_list.addItem(item)

    def _on_history_item_double_clicked(self, item: QListWidgetItem) -> None:
        """双击历史列表项切换到 folder 模式并选择该路径。"""
        path_str = item.text()
        path = Path(path_str)
        if not path.exists():
            QMessageBox.information(self, "提示", f"路径不存在:\n{path_str}")
            return
        self.scan_mode_combo.setCurrentIndex(2)
        self._scan_root = path
        self._add_scan_path_history(path_str)
        self._update_stage_actions()

    # ----------------------------- 规则加载 -----------------------------

    def _init_rules(self) -> None:
        """启动时加载规则：默认加载内置通用规则。"""
        try:
            self._reload_ruleset()
            self._refresh_rules_tree()
            self._refresh_rules_file_list()
            self._update_scan_button()
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

    # ----------------------------- 扫描模式 -----------------------------

    def _on_scan_mode_changed(self, index: int) -> None:
        """扫描模式切换：更新目标选择器可见性与扫描按钮状态。"""
        self._scan_mode = {0: "full", 1: "drive", 2: "folder"}.get(index, "folder")
        self._update_target_visibility()
        self._update_scan_button()

    def _update_target_visibility(self) -> None:
        """根据扫描模式切换目标选择区页面（QStackedWidget 保持布局稳定）。"""
        page_map = {"full": 0, "drive": 1, "folder": 2}
        self.target_stack.setCurrentIndex(page_map.get(self._scan_mode, 2))

    def _refresh_drive_buttons(self) -> None:
        """刷新盘符按钮列表（hard_disk 图标 + 盘符字母，平铺展示）。"""
        # 清除旧按钮
        for btn in self._drive_buttons:
            self._drive_button_group.removeButton(btn)
            self.drive_buttons_layout.removeWidget(btn)
            btn.deleteLater()
        self._drive_buttons.clear()

        for drive in list_drives(include_network=self._config.include_network_drives):
            letter = str(drive)[:1]
            btn = QPushButton(letter, self.target_stack.widget(1))
            btn.setObjectName(f"drive_btn_{letter}")
            btn.setCheckable(True)
            btn.setProperty("drive", str(drive))
            btn.setIcon(self._icon_hard_disk)
            btn.setIconSize(QSize(14, self._config.drive_icon_size))
            self.drive_buttons_layout.addWidget(btn)
            self._drive_button_group.addButton(btn)
            self._drive_buttons.append(btn)

    def _on_drive_selected(self, _button: QAbstractButton) -> None:
        """盘符按钮选择变更。"""
        checked = self._drive_button_group.checkedButton() if self._drive_button_group else None
        self._selected_drive = checked.property("drive") if checked is not None else None
        self._update_scan_button()

    def _build_scan_roots(self) -> list[Path]:
        """根据扫描模式构造根路径列表。"""
        if self._scan_mode == "full":
            return list_drives(include_network=self._config.include_network_drives)
        if self._scan_mode == "drive":
            return [Path(self._selected_drive)] if self._selected_drive else []
        # folder 模式
        return [self._scan_root] if self._scan_root else []

    # ----------------------------- 槽函数 -----------------------------

    def _on_load_rules(self) -> None:
        """加载规则文件，追加到已加载列表末尾。"""
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
            QMessageBox.information(self, "提示", f"该规则文件已在列表中:\n{path.name}")
            return
        self._rules_paths.append(path)
        try:
            self._reload_ruleset()
            self._refresh_rules_tree()
            self._refresh_rules_file_list()
            self._update_scan_button()
            if self._ruleset is not None:
                self.stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条规则")
        except RuleError as exc:
            self._rules_paths.remove(path)
            self._reload_ruleset()
            self._refresh_rules_tree()
            self._refresh_rules_file_list()
            self._update_scan_button()
            QMessageBox.warning(self, "规则错误", f"加载规则失败:\n{exc}")

    def _on_select_path(self) -> None:
        """选择扫描路径。"""
        path_str = QFileDialog.getExistingDirectory(
            self,
            "选择扫描目录",
            str(self._scan_root or Path.home()),
        )
        if not path_str:
            return
        path = Path(path_str)
        self._scan_root = path
        self._add_scan_path_history(str(path))
        self._update_scan_button()

    def _on_path_selected(self, index: int) -> None:
        """从历史下拉选择扫描路径。"""
        if index < 0:
            self._scan_root = None
            self._update_scan_button()
            return
        path_str = self.path_combo.itemText(index)
        if not path_str:
            self._scan_root = None
        else:
            path = Path(path_str)
            self._scan_root = path if path.exists() else None
        self._update_scan_button()

    def _on_scan(self) -> None:
        """开始扫描（仅配置页可触发，扫描中页的暂停/继续由 _on_pause_resume 处理）。"""
        if self._workflow_stage != WorkflowStage.SETUP:
            return
        if self._scan_state in (ScanState.RUNNING, ScanState.PAUSED):
            return

        if self._ruleset is None:
            return

        roots = self._build_scan_roots()
        if not roots:
            QMessageBox.warning(self, "提示", "未选择有效的扫描目标")
            return

        self.result_tree.clear()
        self._detail_clear()
        self._scan_state = ScanState.RUNNING
        self.progress.setRange(0, 0)
        self.current_file_label.setText("准备扫描...")
        self.stats_label.setText("扫描中...")
        # 清空扫描中页的列表，避免残留上次扫描数据（统计已由状态栏 stats_label 承载）
        self.skipped_dirs_list.clear()
        self.matched_files_list.clear()
        # 重置增量更新状态，避免上次扫描的快照干扰本次增量对比
        self._last_skipped_dirs = ()
        self._last_matched_files = ()
        self._last_list_update_time = -1.0
        self._switch_stage(WorkflowStage.SCANNING)

        cache, source_files = self._build_cache_context()
        self._worker = ScanWorker(
            ruleset=self._ruleset,
            roots=roots,
            scan_archives=self._config.scan_archives,
            max_workers=self._config.max_workers,
            max_depth=self._config.max_depth,
            ignore_dirs=tuple(self._config.ignore_dirs),
            ignore_extensions=tuple(self._config.ignore_extensions),
            cache=cache,
            source_files=source_files,
        )
        self._worker.progress_info.connect(self._on_scan_progress)
        self._worker.finished_report.connect(self._on_scan_finished)
        self._worker.failed.connect(self._on_scan_failed)
        self._worker.cancelled.connect(self._on_scan_cancelled)
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

    def _on_scan_progress(self, info) -> None:  # type: ignore[no-untyped-def]
        """扫描实时进度回调：更新进度条、当前文件、状态栏汇总与两个列表。

        列表更新采用增量 append + 独立节流（0.5 秒），避免每次回调全量
        clear+重添 O(N) 阻塞主线程导致点击设置等交互卡滞。
        """
        # 切换为确定进度模式
        if info.total > 0 and self.progress.maximum() != info.total:
            self.progress.setRange(0, info.total)
        self.progress.setValue(info.scanned)

        # 当前文件（截断显示，挂载在状态栏右侧）
        if info.current_file:
            path_text = info.current_file
            if len(path_text) > 100:
                path_text = "..." + path_text[-97:]
            self.current_file_label.setText(f"正在解析: {path_text}")

        # 状态栏汇总文本（速度计算下沉到 ProgressInfo.summary）
        self.stats_label.setText(info.summary())

        # 列表更新独立节流：0.5 秒一次，低于进度条/状态栏频率
        now = time.perf_counter()
        if now - self._last_list_update_time < 0.5:
            return
        self._last_list_update_time = now

        self._update_skipped_dirs_list(info.skipped_dirs)
        self._update_matched_files_list(info.matched_files)

    def _update_skipped_dirs_list(self, new_dirs: tuple[str, ...]) -> None:
        """增量更新跳过目录列表。

        若新列表是旧列表的扩展（旧列表是新列表前缀），只 append 新增尾部条目；
        否则（滚动截断或内容变化）全量重建用 addItems 批量添加。
        """
        old_dirs = self._last_skipped_dirs
        if not new_dirs:
            return
        if new_dirs == old_dirs:
            return
        # 关闭更新以避免逐项 addItems 触发重绘，批量完成后统一刷新
        self.skipped_dirs_list.setUpdatesEnabled(False)
        try:
            if len(new_dirs) > len(old_dirs) and new_dirs[: len(old_dirs)] == old_dirs:
                # 增量 append：旧列表是新列表前缀，只添加新增尾部
                self.skipped_dirs_list.addItems(new_dirs[len(old_dirs) :])
            else:
                # 全量重建（滚动截断或内容变化）
                self.skipped_dirs_list.clear()
                self.skipped_dirs_list.addItems(new_dirs)
        finally:
            self.skipped_dirs_list.setUpdatesEnabled(True)
        self.skipped_dirs_list.scrollToBottom()
        self._last_skipped_dirs = new_dirs

    def _update_matched_files_list(self, new_files: tuple[tuple[str, str], ...]) -> None:
        """增量更新命中文件列表，逻辑同 _update_skipped_dirs_list。"""
        old_files = self._last_matched_files
        if not new_files:
            return
        if new_files == old_files:
            return
        # 关闭更新以避免逐项 addItems 触发重绘，批量完成后统一刷新
        self.matched_files_list.setUpdatesEnabled(False)
        try:
            if len(new_files) > len(old_files) and new_files[: len(old_files)] == old_files:
                # 增量 append：格式 "路径 → 规则名"
                items = [f"{fp} → {rn}" for fp, rn in new_files[len(old_files) :]]
                self.matched_files_list.addItems(items)
            else:
                # 全量重建
                self.matched_files_list.clear()
                items = [f"{fp} → {rn}" for fp, rn in new_files]
                self.matched_files_list.addItems(items)
        finally:
            self.matched_files_list.setUpdatesEnabled(True)
        self.matched_files_list.scrollToBottom()
        self._last_matched_files = new_files

    def _on_scan_finished(self, report: ScanReport) -> None:
        """扫描完成回调：填充结果并切换到结果页。"""
        self._last_report = report
        self._reset_scan_ui()

        self._populate_results(report)

        self.stats_label.setText(report.summary())
        self._switch_stage(WorkflowStage.RESULTS)

    def _on_scan_failed(self, error: str) -> None:
        """扫描失败回调：切回配置页并提示。"""
        self._reset_scan_ui()
        self.stats_label.setText("扫描失败")
        self._switch_stage(WorkflowStage.SETUP)
        QMessageBox.critical(self, "扫描失败", error)

    def _on_export_menu(self) -> None:
        """导出按钮：弹出格式选择对话框。"""
        if self._last_report is None:
            QMessageBox.information(self, "提示", "无可导出的扫描结果")
            return
        # 简单弹窗选择格式
        items = ["CSV 文件 (*.csv)", "JSON 文件 (*.json)"]
        item, ok = QInputDialog.getItem(self, "导出扫描结果", "选择导出格式:", items, 0, False)
        if not ok:
            return
        fmt = "csv" if "CSV" in item else "json"
        self._on_export(fmt)

    def _on_export(self, fmt: str) -> None:
        """导出扫描结果。"""
        if self._last_report is None:
            QMessageBox.information(self, "提示", "无可导出的扫描结果")
            return

        filter_str = "CSV 文件 (*.csv)" if fmt == "csv" else "JSON 文件 (*.json)"
        default_name = f"fuscan_report.{fmt}"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "导出扫描结果",
            default_name,
            filter_str,
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            content = self._last_report.to_format(fmt)
            path.write_text(content, encoding="utf-8")
            QMessageBox.information(self, "导出成功", f"已导出到:\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _on_about(self) -> None:
        """关于对话框。"""
        QMessageBox.about(
            self,
            "关于 fuscan",
            f"fuscan {__version__}\n\n通用文件扫描器\n支持多格式与压缩文件扫描\n\n技术栈: Python + PySide",
        )

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
            self._refresh_drive_buttons()
            # 缓存配置变更时释放旧 CacheStore，下次扫描按新配置重建
            cache_changed = not self._config.cache_enabled or self._config.cache_path != prev_cache_path
            if prev_cache_enabled and cache_changed and self._cache is not None:
                try:
                    self._cache.close()
                except Exception:
                    logger.warning("缓存关闭失败", exc_info=True)
                self._cache = None

    # ----------------------------- 详情区更新 -----------------------------

    def _on_result_selection_changed(self) -> None:
        """结果树选中变化：更新详情区主体。"""
        items = self.result_tree.selectedItems()
        if not items:
            self._detail_clear()
            return
        item = items[0]
        result = item.data(0, Qt.UserRole)
        if result is None and item.parent() is not None:
            result = item.parent().data(0, Qt.UserRole)
        if result is None:
            # 选中分组顶层项（无文件数据）：保持详情区空态，避免误显示"无命中"
            self._detail_clear()
            return
        logger.debug("选中结果项: %s, 命中数=%d", result.path, len(result.hits))
        self._detail_show_result(result)

    def _detail_clear(self) -> None:
        """清空详情区，切换到空态。"""
        self.detail_action_stack.setCurrentIndex(0)
        self.detail_main_stack.setCurrentIndex(0)
        self._detail_current_result = None
        self._detail_hit_positions = []
        self._detail_current_hit_index = -1
        self.detail_preview.clear()
        self.detail_hits_table.setRowCount(0)
        self.detail_info_label.setText("")

    def _detail_show_result(self, result: ScanResult) -> None:
        """在详情区展示选中项的详情，切换到非空态。"""
        self._detail_current_result = result
        self.detail_action_stack.setCurrentIndex(1)
        self.detail_main_stack.setCurrentIndex(1)
        # 先填充预览以计算高亮位置，再填充文件信息和命中表（均依赖位置数据）
        self._populate_detail_preview(result)
        self._populate_detail_file_info(result)
        self._populate_detail_hits_table(result)
        # 强制刷新当前详情页，避免 Qt 渲染时序导致 stack 未生效
        self.detail_main_stack.currentWidget().update()

    def _populate_detail_file_info(self, result: ScanResult) -> None:
        """填充详情区文件元信息。"""
        # 文件信息 HTML 由 ScanResult.file_info_html 构造，GUI 仅追加自身状态字段
        extra = f"<b>可切换位置:</b> {len(self._detail_hit_positions)}"
        self.detail_info_label.setText(result.file_info_html(extra=extra))

    def _populate_detail_hits_table(self, result: ScanResult) -> None:
        """填充详情区命中规则表。"""
        hits = result.hits
        logger.debug("填充命中表: %s, 命中数=%d", result.path, len(hits))
        self.detail_hits_table.setRowCount(len(hits))
        # 统计每条规则在预览中的高亮位置数
        position_counts: dict[int, int] = {}
        for _, _, rule_idx in self._detail_hit_positions:
            position_counts[rule_idx] = position_counts.get(rule_idx, 0) + 1
        for row, hit in enumerate(hits):
            self.detail_hits_table.setItem(row, 0, QTableWidgetItem(hit.rule_name))
            sev_item = QTableWidgetItem("")
            _apply_severity_to_table_item(sev_item, hit.severity)
            self.detail_hits_table.setItem(row, 1, sev_item)
            count_item = QTableWidgetItem(str(hit.match_count))
            count_item.setTextAlignment(Qt.AlignCenter)
            self.detail_hits_table.setItem(row, 2, count_item)
            if hit.target == "filename":
                pos_item = QTableWidgetItem("-")
                pos_item.setToolTip("仅匹配文件名，无内容高亮位置")
            else:
                pos_item = QTableWidgetItem(str(position_counts.get(row, 0)))
                pos_item.setToolTip("该规则在预览中可高亮跳转的位置数")
            pos_item.setTextAlignment(Qt.AlignCenter)
            self.detail_hits_table.setItem(row, 3, pos_item)
            detail_text = hit.detail
            if hit.target == "filename":
                detail_text = f"{detail_text}（仅文件名）"
            self.detail_hits_table.setItem(row, 4, QTableWidgetItem(detail_text))

    def _populate_detail_preview(self, result: ScanResult) -> None:
        """填充详情区内容预览，命中关键词高亮并定位到首个命中。"""
        path = result.path
        truncated = False

        # 优先使用提取器（支持 PDF/DOCX 等格式），失败回退到纯文本
        try:
            content = extract_content_with_fallback(path)
        except OSError as exc:
            logger.warning("读取内容预览失败 %s", path, exc_info=True)
            self.detail_preview.setPlainText(f"无法读取文件内容: {exc}")
            self._update_detail_nav_label()
            return

        if not content:
            self.detail_preview.setPlainText("(文件内容为空或为二进制)")
            self._update_detail_nav_label()
            return

        # 截断过长内容
        if len(content) > PREVIEW_MAX_CHARS:
            content = content[:PREVIEW_MAX_CHARS]
            truncated = True

        keywords = extract_keywords(result.hits)
        # 命中规则但无法提取关键词（如纯文件名/路径匹配），显示提示避免误判为"无命中"
        if not keywords and result.hits:
            rule_names = "、".join(h.rule_name for h in result.hits)
            self.detail_preview.setPlainText(
                f"（此文件因【{rule_names}】规则命中，但无内容关键词可高亮。命中详情见上方表格。）"
            )
            self._detail_hit_positions = []
            self._detail_current_hit_index = -1
            self._update_detail_nav_label()
            return
        html_content = build_preview_html(content, keywords)
        if truncated:
            html_content += "<p style='color: #888; font-size: 11px;'>(内容已截断，仅显示前 100KB)</p>"
        self.detail_preview.setHtml(html_content)

        # 查找所有关键词位置并定位到首个命中
        self._find_detail_hit_positions(result.hits)
        if self._detail_hit_positions:
            self._detail_current_hit_index = 0
            self._highlight_current_detail_hit()
            self._scroll_to_current_detail_hit()
        self._update_detail_nav_label()

    def _find_detail_hit_positions(self, hits: Sequence[RuleHit]) -> None:
        """在详情区预览文档中查找所有关键词出现位置，按位置排序后存储。

        使用 Python :func:`re.finditer` 在 :meth:`toPlainText` 返回的纯文本上查找，
        避免 :meth:`QTextDocument.find` 无法跨越段落边界的限制。
        关键词中的换行符（\\r\\n/\\r/\\n）规范化为 ``\\s+`` 正则，支持跨行命中的定位。

        每个位置记录为 ``(start, end, rule_index)`` 三元组，``rule_index`` 为命中
        规则在 ``hits`` 中的索引，用于点击规则表行时跳转到对应高亮位置。
        同一关键词若被多条规则命中，仅归属到首条规则（避免位置重复计数）。
        """
        self._detail_hit_positions = []
        if not hits:
            return
        plain = self.detail_preview.toPlainText()
        if not plain:
            return
        keyword_to_rule = build_keyword_to_rule_map(hits)
        seen: set[tuple[int, int]] = set()
        for kw, rule_idx in sorted(keyword_to_rule.items(), key=lambda x: len(x[0]), reverse=True):
            pattern = compile_keyword_pattern(kw)
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error:
                continue
            for m in regex.finditer(plain):
                pos = (m.start(), m.end())
                if pos not in seen:
                    seen.add(pos)
                    self._detail_hit_positions.append((m.start(), m.end(), rule_idx))
        self._detail_hit_positions.sort()

    def _highlight_current_detail_hit(self) -> None:
        """用橙色背景高亮当前命中位置，区别于其他命中的黄色高亮。"""
        if self._detail_current_hit_index < 0 or self._detail_current_hit_index >= len(self._detail_hit_positions):
            self.detail_preview.setExtraSelections([])
            return
        start, end, _ = self._detail_hit_positions[self._detail_current_hit_index]
        doc_length = len(self.detail_preview.toPlainText())
        if start >= doc_length or end > doc_length:
            self.detail_preview.setExtraSelections([])
            return
        sel = QTextEdit.ExtraSelection()
        cursor = self.detail_preview.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        sel.cursor = cursor
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 165, 0))
        sel.format = fmt
        self.detail_preview.setExtraSelections([sel])

    def _scroll_to_current_detail_hit(self) -> None:
        """滚动详情区预览使当前命中位置可见。"""
        if self._detail_current_hit_index < 0 or self._detail_current_hit_index >= len(self._detail_hit_positions):
            return
        start, _, _ = self._detail_hit_positions[self._detail_current_hit_index]
        doc_length = len(self.detail_preview.toPlainText())
        if start >= doc_length:
            return
        cursor = self.detail_preview.textCursor()
        cursor.setPosition(start)
        self.detail_preview.setTextCursor(cursor)
        self.detail_preview.ensureCursorVisible()

    def _on_prev_detail_hit(self) -> None:
        """跳转到上一个命中位置。"""
        if not self._detail_hit_positions:
            return
        self._detail_current_hit_index = (self._detail_current_hit_index - 1) % len(self._detail_hit_positions)
        self._highlight_current_detail_hit()
        self._scroll_to_current_detail_hit()
        self._update_detail_nav_label()

    def _on_next_detail_hit(self) -> None:
        """跳转到下一个命中位置。"""
        if not self._detail_hit_positions:
            return
        self._detail_current_hit_index = (self._detail_current_hit_index + 1) % len(self._detail_hit_positions)
        self._highlight_current_detail_hit()
        self._scroll_to_current_detail_hit()
        self._update_detail_nav_label()

    def _on_detail_hits_row_clicked(self, row: int, _col: int) -> None:
        """点击命中规则表行，跳转到该规则对应的高亮位置。

        若当前已处于该规则的某个位置，则跳转到该规则的下一个位置（循环）；
        否则跳转到该规则的首个高亮位置。
        """
        if not self._detail_hit_positions:
            return
        rule_indices = [i for i, (_, _, ri) in enumerate(self._detail_hit_positions) if ri == row]
        if not rule_indices:
            return
        target = rule_indices[0]
        for i in rule_indices:
            if i > self._detail_current_hit_index:
                target = i
                break
        self._detail_current_hit_index = target
        self._highlight_current_detail_hit()
        self._scroll_to_current_detail_hit()
        self._update_detail_nav_label()

    def _update_detail_nav_label(self) -> None:
        """更新详情区导航标签与按钮状态。"""
        total = len(self._detail_hit_positions)
        if total == 0:
            self.detail_nav_label.setText("无命中")
            self.detail_prev_btn.setEnabled(False)
            self.detail_next_btn.setEnabled(False)
        else:
            self.detail_nav_label.setText(f"{self._detail_current_hit_index + 1} / {total}")
            self.detail_prev_btn.setEnabled(True)
            self.detail_next_btn.setEnabled(True)

    def _on_open_in_window(self) -> None:
        """在新窗口打开完整详情对话框。"""
        if self._detail_current_result is None:
            return
        dialog = HitDetailDialog(self._detail_current_result, self)
        dialog.exec_()

    def _on_copy_path(self) -> None:
        """复制文件路径到剪贴板。"""
        if self._detail_current_result is None:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(str(self._detail_current_result.path))
            self.stats_label.setText("已复制路径到剪贴板")

    def _on_open_file_location(self) -> None:
        """在文件管理器中打开所在目录。"""
        if self._detail_current_result is None:
            return
        path = self._detail_current_result.path
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except (OSError, FileNotFoundError) as exc:
            logger.warning("打开文件位置失败: %s", exc, exc_info=True)
            QMessageBox.warning(self, "提示", f"打开文件位置失败:\n{exc}")

    # ----------------------------- 辅助方法 -----------------------------

    def _refresh_rules_tree(self) -> None:
        """刷新规则列表展示。"""
        self.rules_tree.clear()
        if self._ruleset is None:
            return
        for rule in self._ruleset.rules:
            item = QTreeWidgetItem([
                rule.name,
                "",
                ", ".join(rule.file_extensions) if rule.file_extensions else "(全部)",
            ])
            _apply_severity_to_tree_item(item, 1, rule.severity)
            self.rules_tree.addTopLevelItem(item)

    def _refresh_rules_file_list(self) -> None:
        """刷新规则文件列表展示。"""
        self.rules_file_list.clear()
        for path in self._rules_paths:
            item = QListWidgetItem(str(path))
            item.setToolTip(str(path))
            self.rules_file_list.addItem(item)

    def _on_move_rule_up(self) -> None:
        """将选中的规则文件上移一位。"""
        row = self.rules_file_list.currentRow()
        if row <= 0:
            return
        self._rules_paths[row - 1], self._rules_paths[row] = (
            self._rules_paths[row],
            self._rules_paths[row - 1],
        )
        self._refresh_rules_file_list()
        self.rules_file_list.setCurrentRow(row - 1)
        self._reload_and_refresh()

    def _on_move_rule_down(self) -> None:
        """将选中的规则文件下移一位。"""
        row = self.rules_file_list.currentRow()
        if row < 0 or row >= len(self._rules_paths) - 1:
            return
        self._rules_paths[row + 1], self._rules_paths[row] = (
            self._rules_paths[row],
            self._rules_paths[row + 1],
        )
        self._refresh_rules_file_list()
        self.rules_file_list.setCurrentRow(row + 1)
        self._reload_and_refresh()

    def _on_remove_rule(self) -> None:
        """移除选中的规则文件。"""
        row = self.rules_file_list.currentRow()
        if row < 0:
            return
        del self._rules_paths[row]
        self._refresh_rules_file_list()
        self._reload_and_refresh()

    def _on_edit_rules(self) -> None:
        """打开规则编辑器对话框。"""
        # 延迟加载 GUI 子对话框，加速主窗口启动
        from fuscan.gui.rule_editor import RuleEditorDialog

        if not self._rules_paths:
            QMessageBox.information(self, "提示", "未加载任何规则文件，请先加载规则。")
            return
        dialog = RuleEditorDialog(self._rules_paths, self)
        dialog.rules_saved.connect(self._on_rules_saved)
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
        """填充结果树：存储报告、更新规则筛选下拉、刷新结果树。"""
        self._last_report = report
        self._update_rule_filter_options(report)
        self._refresh_result_tree()
        # 有结果时启用导出按钮
        self.export_btn.setEnabled(len(report.hits) > 0)

    def _update_rule_filter_options(self, report: ScanReport) -> None:
        """根据扫描结果更新规则筛选下拉项。"""
        current_rule = self.rule_filter_combo.currentData()
        self.rule_filter_combo.blockSignals(True)
        self.rule_filter_combo.clear()
        self.rule_filter_combo.addItem("全部规则", "")
        for name in sorted(report.rule_names):
            self.rule_filter_combo.addItem(name, name)
        # 恢复之前选中的规则
        if current_rule:
            idx = self.rule_filter_combo.findData(current_rule)
            if idx >= 0:
                self.rule_filter_combo.setCurrentIndex(idx)
        self.rule_filter_combo.blockSignals(False)

    def _refresh_result_tree(self) -> None:
        """根据当前筛选条件与分组模式刷新结果树。"""
        self.result_tree.clear()
        if self._last_report is None:
            return

        path_filter = self.path_filter_input.text().strip()
        rule_filter = self.rule_filter_combo.currentData() or ""
        group_mode = self.group_mode_combo.currentData() or "flat"

        # 筛选下沉到 ScanReport.filter，仅返回 results 过滤后的新报告
        filtered_report = self._last_report.filter(path_query=path_filter, rule_name=rule_filter)

        if group_mode == "rule":
            self._populate_grouped_by_rule(filtered_report)
        elif group_mode == "severity":
            self._populate_grouped_by_severity(filtered_report)
        else:
            self._populate_flat(filtered_report)

    def _populate_flat(self, report: ScanReport) -> None:
        """不分组：文件为顶层项，规则命中为子项。"""
        for sr in report.hits:
            file_item = QTreeWidgetItem([
                str(sr.path),
                "",
                "",
                str(len(sr.hits)),
                str(sr.total_match_count),
                sr.summary(),
            ])
            file_item.setData(0, Qt.UserRole, sr)
            _apply_severity_to_tree_item(file_item, 2, sr.max_severity)
            file_item.setTextAlignment(3, Qt.AlignCenter)
            file_item.setTextAlignment(4, Qt.AlignCenter)
            # critical 整行背景高亮，区别于仅 severity 列着色
            if sr.max_severity == Severity.CRITICAL:
                for col in range(file_item.columnCount()):
                    file_item.setBackground(col, _SEVERITY_BACKGROUNDS[Severity.CRITICAL])
            for hit in sr.hits:
                child = QTreeWidgetItem([
                    "",
                    hit.rule_name,
                    "",
                    "",
                    str(hit.match_count),
                    hit.detail,
                ])
                _apply_severity_to_tree_item(child, 2, hit.severity)
                child.setTextAlignment(4, Qt.AlignCenter)
                file_item.addChild(child)
            self.result_tree.addTopLevelItem(file_item)

    def _populate_grouped_by_rule(self, report: ScanReport) -> None:
        """按规则分组：规则名为顶层项，文件为子项。"""
        rule_map = report.group_by_rule()

        for rule_name in sorted(rule_map.keys()):
            entries = rule_map[rule_name]
            hit_count = len(entries)
            match_sum = sum(h.match_count for _, h in entries)
            top = QTreeWidgetItem([
                "",
                rule_name,
                "",
                str(hit_count),
                str(match_sum),
                f"{hit_count} 个文件 / {match_sum} 处匹配",
            ])
            # 分组项不可选中，避免选中后详情区被清空产生"无命中"误解
            top.setFlags(top.flags() & ~Qt.ItemIsSelectable)
            top.setTextAlignment(3, Qt.AlignCenter)
            top.setTextAlignment(4, Qt.AlignCenter)
            for sr, hit in entries:
                child = QTreeWidgetItem([
                    str(sr.path),
                    "",
                    "",
                    "",
                    str(hit.match_count),
                    hit.detail,
                ])
                _apply_severity_to_tree_item(child, 2, hit.severity)
                child.setTextAlignment(4, Qt.AlignCenter)
                child.setData(0, Qt.UserRole, sr)
                top.addChild(child)
            self.result_tree.addTopLevelItem(top)

    def _populate_grouped_by_severity(self, report: ScanReport) -> None:
        """按严重等级分组：等级为顶层项，文件为子项。"""
        severity_map = report.group_by_severity()

        for severity in sorted(severity_map.keys(), key=lambda s: _SEVERITY_RANK[s], reverse=True):
            entries = severity_map[severity]
            file_count = len(entries)
            match_sum = sum(sr.total_match_count for sr in entries)
            top = QTreeWidgetItem([
                "",
                "",
                "",
                str(file_count),
                str(match_sum),
                f"{file_count} 个文件 / {match_sum} 处匹配",
            ])
            _apply_severity_to_tree_item(top, 2, severity)
            # 分组项不可选中，避免选中后详情区被清空产生"无命中"误解
            top.setFlags(top.flags() & ~Qt.ItemIsSelectable)
            top.setTextAlignment(3, Qt.AlignCenter)
            top.setTextAlignment(4, Qt.AlignCenter)
            for sr in entries:
                child = QTreeWidgetItem([
                    str(sr.path),
                    "",
                    "",
                    str(len(sr.hits)),
                    str(sr.total_match_count),
                    sr.summary(),
                ])
                _apply_severity_to_tree_item(child, 2, sr.max_severity)
                child.setData(0, Qt.UserRole, sr)
                child.setTextAlignment(3, Qt.AlignCenter)
                child.setTextAlignment(4, Qt.AlignCenter)
                # critical 整行背景高亮，区别于仅 severity 列着色
                if sr.max_severity == Severity.CRITICAL:
                    for col in range(child.columnCount()):
                        child.setBackground(col, _SEVERITY_BACKGROUNDS[Severity.CRITICAL])
                top.addChild(child)
            self.result_tree.addTopLevelItem(top)

    def _on_result_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """双击结果项：在新窗口打开详情对话框。

        选中变化已通过 itemSelectionChanged 触发详情区更新，
        双击额外弹出独立对话框供放大查看。
        """
        result = item.data(0, Qt.UserRole)
        if result is None and item.parent() is not None:
            result = item.parent().data(0, Qt.UserRole)
        if result is None:
            return
        dialog = HitDetailDialog(result, self)
        dialog.exec_()

    def _update_scan_button(self) -> None:
        """更新扫描按钮状态（委托给 _update_stage_actions 统一管理）。"""
        self._update_stage_actions()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """关闭时保存配置、释放缓存并终止后台线程。"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        if self._cache is not None:
            try:
                self._cache.close()
            except Exception:
                logger.warning("缓存关闭失败", exc_info=True)
            self._cache = None
        self._save_config()
        super().closeEvent(event)
