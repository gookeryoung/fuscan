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

import csv
import datetime
import enum
import html
import io
import json
import logging
import re
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from PySide2.QtCore import QSize, Qt
from PySide2.QtGui import QColor, QIcon, QTextCharFormat, QTextCursor
from PySide2.QtWidgets import (
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
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidgetItem,
    QWidget,
)

from fuscan.builtin import load_with_builtin
from fuscan.config import MAX_HISTORY, Config, load_config, save_config
from fuscan.extractors import extract_content
from fuscan.gui.detail_dialog import HitDetailDialog
from fuscan.gui.main_window_ui import Ui_MainWindow
from fuscan.gui.worker import ScanWorker
from fuscan.rules import RuleError, load_ruleset, merge_multiple_rulesets
from fuscan.rules.model import RuleSet
from fuscan.scanner import ScanReport, list_drives
from fuscan.scanner.result import RuleHit, ScanResult

__all__ = ["MainWindow", "ScanState"]

logger = logging.getLogger(__name__)

# 内容预览最大字符数，避免大文件阻塞 UI
_PREVIEW_MAX_CHARS = 100 * 1024

# 从 detail 中提取关键词的正则，匹配单引号包裹的内容
_KEYWORD_RE = re.compile(r"'([^']+)'")

# 内容预览 pre 标签样式
_PREVIEW_STYLE = (
    "font-family: Consolas, 'Courier New', monospace; font-size: 12px; white-space: pre-wrap; word-wrap: break-word;"
)

# 关键词高亮 span 样式
_HIGHLIGHT_STYLE = "background-color: yellow; color: black;"

# 图标路径（assets/icons 目录下）
_ICONS_DIR = Path(__file__).parent.parent / "assets" / "icons"
_ICON_SCAN = str(_ICONS_DIR / "scan.svg")
_ICON_PAUSE = str(_ICONS_DIR / "pause.svg")
_ICON_RESCAN = str(_ICONS_DIR / "rescan.svg")
_ICON_ALL_DISK = str(_ICONS_DIR / "all_disk.svg")
_ICON_DISK = str(_ICONS_DIR / "disk.svg")
_ICON_FOLDER = str(_ICONS_DIR / "folder.svg")
_ICON_HISTORY = str(_ICONS_DIR / "history.svg")
_ICON_LOAD_LIST = str(_ICONS_DIR / "load_list.svg")
_ICON_RIGHT = str(_ICONS_DIR / "right.svg")
_ICON_HARD_DISK = str(_ICONS_DIR / "hard_disk.svg")


def _format_size(size: int) -> str:
    """将字节数格式化为人类可读字符串。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def _extract_keywords(hits: Sequence[RuleHit]) -> list[str]:
    """从命中规则的 detail 字段中提取关键词。

    detail 形如 "包含 'password'" / "正则命中: 'AKIA...'"，
    提取单引号内的模式用于内容高亮。
    """
    keywords: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        for match in _KEYWORD_RE.finditer(hit.detail):
            kw = match.group(1)
            if kw and kw not in seen:
                seen.add(kw)
                keywords.append(kw)
    return keywords


def _build_preview_html(content: str, keywords: Sequence[str]) -> str:
    """构建内容预览 HTML，关键词以黄色背景高亮。

    先对内容做 html.escape 转义，再用单次正则替换插入高亮 span，
    避免多次 replace 破坏已插入的 HTML 标签。
    """
    escaped = html.escape(content)
    if keywords:
        # 按长度降序排列，优先匹配最长关键词
        escaped_kws = sorted({html.escape(k) for k in keywords if k}, key=len, reverse=True)
        if escaped_kws:
            pattern = "|".join(re.escape(k) for k in escaped_kws)
            regex = re.compile(pattern, re.IGNORECASE)
            escaped = regex.sub(
                lambda m: f'<span style="{_HIGHLIGHT_STYLE}">{m.group(0)}</span>',
                escaped,
            )
    # 保留换行
    escaped = escaped.replace("\n", "<br>")
    return f"<pre style='{_PREVIEW_STYLE}'>{escaped}</pre>"


class ScanState(enum.Enum):
    """扫描状态。"""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


class MainWindow(QMainWindow):
    """主窗口：扫描器 GUI 入口，GitHub Desktop 风格 5 区布局。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ui = Ui_MainWindow()
        self._ui.setupUi(self)

        self._config: Config = load_config()
        self._ruleset: RuleSet | None = None
        self._rules_paths: list[Path] = []
        self._scan_root: Path | None = None
        self._last_report: ScanReport | None = None
        self._worker: ScanWorker | None = None
        self._scan_state: ScanState = ScanState.IDLE
        self._use_builtin: bool = True
        # 扫描模式："full"（全盘）、"drive"（盘符）、"folder"（文件夹）
        self._scan_mode: str = "folder"
        # 详情区命中导航状态
        self._detail_hit_positions: list[tuple[int, int]] = []
        self._detail_current_hit_index: int = -1
        self._detail_current_result: ScanResult | None = None
        # 扫描历史记录
        self._scan_history: list[str] = []
        # 盘符按钮组（平铺选择，替代下拉）
        self._drive_button_group: QButtonGroup | None = None
        self._drive_buttons: list[QPushButton] = []
        self._selected_drive: str | None = None

        self._bind_widgets()
        self._configure_ui()
        self._apply_config()
        self._init_rules()

    # ----------------------------- UI 绑定与配置 -----------------------------

    def _bind_widgets(self) -> None:
        """将 Ui_MainWindow 的部件绑定到本类私有属性，保持业务逻辑兼容。"""
        ui = self._ui
        # 主操作区
        self._scan_btn = ui.scan_btn
        self._progress = ui.progress
        self._scan_mode_combo = ui.scan_mode_combo
        self._target_stack = ui.target_stack
        self._drive_buttons_layout = ui.drive_buttons_layout
        self._path_combo = ui.path_combo
        self._select_path_btn = ui.select_path_btn
        self._load_rules_btn = ui.load_rules_btn
        self._rules_label = ui.rules_label
        self._use_builtin_checkbox = ui.use_builtin_checkbox
        # 状态栏（替代原顶部 stats_label/current_file_label）
        self._stats_label = QLabel("就绪")
        self._stats_label.setObjectName("stats_label")
        self._current_file_label = QLabel("")
        self._current_file_label.setObjectName("current_file_label")
        self._current_file_label.setVisible(False)
        self.statusBar().addWidget(self._stats_label, 1)
        self.statusBar().addPermanentWidget(self._current_file_label)
        # 列表区
        self._splitter = ui.splitter
        self._tab_widget = ui.tab_widget
        self._result_tree = ui.result_tree
        self._path_filter_input = ui.path_filter_input
        self._rule_filter_combo = ui.rule_filter_combo
        self._group_mode_combo = ui.group_mode_combo
        self._rules_file_list = ui.rules_file_list
        self._move_up_btn = ui.move_up_btn
        self._move_down_btn = ui.move_down_btn
        self._remove_rule_btn = ui.remove_rule_btn
        self._edit_rule_btn = ui.edit_rule_btn
        self._rules_tree = ui.rules_tree
        self._history_list = ui.history_list
        self._note_edit = ui.note_edit
        self._batch_btn = ui.batch_btn
        self._export_btn = ui.export_btn
        # 详情区
        self._detail_action_stack = ui.detail_action_stack
        self._detail_main_stack = ui.detail_main_stack
        self._detail_locate_btn = ui.detail_locate_btn
        self._detail_prev_btn = ui.detail_prev_btn
        self._detail_next_btn = ui.detail_next_btn
        self._detail_nav_label = ui.detail_nav_label
        self._detail_open_location_btn = ui.detail_open_location_btn
        self._detail_copy_path_btn = ui.detail_copy_path_btn
        self._detail_open_window_btn = ui.detail_open_window_btn
        self._detail_info_label = ui.detail_info_label
        self._detail_hits_table = ui.detail_hits_table
        self._detail_preview = ui.detail_preview
        # actions
        self._scan_action = ui.scan_action
        self._load_rules_action = ui.load_rules_action
        self._edit_rules_action = ui.edit_rules_action
        self._export_csv_action = ui.export_csv_action
        self._export_json_action = ui.export_json_action
        self._view_results_action = ui.view_results_action
        self._view_rules_action = ui.view_rules_action
        self._view_history_action = ui.view_history_action
        self._settings_action = ui.settings_action

    def _configure_ui(self) -> None:
        """配置 .ui 无法静态表达的动态属性、layout stretch 与信号槽连接。"""
        # 结果树列宽
        self._result_tree.setColumnWidth(0, 400)
        self._result_tree.setColumnWidth(1, 150)
        self._result_tree.setColumnWidth(2, 80)
        self._result_tree.setColumnWidth(3, 60)

        # 详情区命中表
        self._detail_hits_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._detail_hits_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._detail_hits_table.setSelectionBehavior(QTableWidget.SelectRows)

        # QComboBox 初始项
        self._rule_filter_combo.addItem("全部规则", "")
        self._group_mode_combo.addItem("不分组", "flat")
        self._group_mode_combo.addItem("按规则", "rule")
        self._group_mode_combo.addItem("按严重等级", "severity")

        # QSplitter 伸缩比例（左:右 = 2:3）
        self._splitter.setStretchFactor(0, 2)
        self._splitter.setStretchFactor(1, 3)

        # layout 伸缩因子（.ui 不支持 stretch vector）
        ui = self._ui
        # verticalLayout_2: 顶部操作行 / 分割器 / 进度条
        ui.verticalLayout_2.setSpacing(4)
        ui.verticalLayout_2.setStretch(0, 0)
        ui.verticalLayout_2.setStretch(1, 1)
        ui.verticalLayout_2.setStretch(2, 0)
        # control_layout: 扫描模式+目标 / 规则 / 扫描按钮（12px 间距分隔）
        ui.list_layout.setStretch(0, 1)
        ui.results_layout.setStretch(0, 0)
        ui.results_layout.setStretch(1, 1)
        ui.filter_layout.setStretch(0, 0)
        ui.filter_layout.setStretch(1, 2)
        ui.filter_layout.setStretch(2, 0)
        ui.filter_layout.setStretch(3, 1)
        ui.filter_layout.setStretch(4, 0)
        ui.filter_layout.setStretch(5, 1)
        ui.rules_tab_layout.setStretch(0, 0)
        ui.rules_tab_layout.setStretch(1, 0)
        ui.rules_tab_layout.setStretch(2, 0)
        ui.rules_tab_layout.setStretch(3, 1)
        ui.history_layout.setStretch(0, 0)
        ui.history_layout.setStretch(1, 1)
        ui.detail_layout.setStretch(0, 0)
        ui.detail_layout.setStretch(1, 1)
        ui.detail_nonempty_main_layout.setStretch(0, 0)
        ui.detail_nonempty_main_layout.setStretch(1, 0)
        ui.detail_nonempty_main_layout.setStretch(2, 1)
        ui.detail_nonempty_main_layout.setStretch(3, 0)
        ui.detail_nonempty_main_layout.setStretch(4, 2)
        ui.detail_nonempty_main_layout.setStretch(5, 0)
        ui.detail_nonempty_main_layout.setStretch(6, 0)

        # 进度条初始隐藏（.ui 中未设 visible 属性）
        self._progress.setVisible(False)

        # 空白详情面板居中（.ui 中 QVBoxLayout 不支持 alignment 属性）
        ui.detail_empty_main_layout.insertStretch(0)
        ui.detail_empty_main_layout.addStretch()

        # 加载图标并为扫描控制按钮设置
        self._icon_scan = QIcon(_ICON_SCAN)
        self._icon_pause = QIcon(_ICON_PAUSE)
        self._icon_rescan = QIcon(_ICON_RESCAN)
        self._icon_all_disk = QIcon(_ICON_ALL_DISK)
        self._icon_disk = QIcon(_ICON_DISK)
        self._icon_folder = QIcon(_ICON_FOLDER)
        self._icon_history = QIcon(_ICON_HISTORY)
        self._icon_load_list = QIcon(_ICON_LOAD_LIST)
        self._icon_right = QIcon(_ICON_RIGHT)
        self._icon_hard_disk = QIcon(_ICON_HARD_DISK)
        self._scan_btn.setIcon(self._icon_scan)
        # 扫描模式下拉项图标
        self._scan_mode_combo.setItemIcon(0, self._icon_all_disk)
        self._scan_mode_combo.setItemIcon(1, self._icon_disk)
        self._scan_mode_combo.setItemIcon(2, self._icon_folder)
        # 加载规则按钮图标
        self._load_rules_btn.setIcon(self._icon_load_list)
        self._load_rules_action.setIcon(self._icon_load_list)
        # 历史记录按钮图标
        self._view_history_action.setIcon(self._icon_history)
        # Tab 图标
        self._tab_widget.setTabIcon(0, self._icon_scan)
        self._tab_widget.setTabIcon(2, self._icon_history)
        # 菜单 actions 图标
        self._scan_action.setIcon(self._icon_scan)

        # 初始化盘符按钮组（平铺选择，替代下拉）
        self._drive_button_group = QButtonGroup(self)
        self._drive_button_group.setExclusive(True)
        self._drive_button_group.buttonClicked.connect(self._on_drive_selected)
        self._refresh_drive_buttons()

        # 信号槽连接
        self._scan_btn.clicked.connect(self._on_scan)
        self._scan_mode_combo.currentIndexChanged.connect(self._on_scan_mode_changed)
        self._path_combo.currentIndexChanged.connect(self._on_path_selected)
        self._select_path_btn.clicked.connect(self._on_select_path)
        self._load_rules_btn.clicked.connect(self._on_load_rules)
        self._use_builtin_checkbox.stateChanged.connect(self._on_toggle_builtin)
        self._result_tree.itemDoubleClicked.connect(self._on_result_double_clicked)
        self._result_tree.itemSelectionChanged.connect(self._on_result_selection_changed)
        self._path_filter_input.textChanged.connect(self._refresh_result_tree)
        self._rule_filter_combo.currentIndexChanged.connect(self._refresh_result_tree)
        self._group_mode_combo.currentIndexChanged.connect(self._refresh_result_tree)
        self._move_up_btn.clicked.connect(self._on_move_rule_up)
        self._move_down_btn.clicked.connect(self._on_move_rule_down)
        self._remove_rule_btn.clicked.connect(self._on_remove_rule)
        self._edit_rule_btn.clicked.connect(self._on_edit_rules)
        self._history_list.itemDoubleClicked.connect(self._on_history_item_double_clicked)
        self._batch_btn.clicked.connect(self._on_batch_process)
        self._export_btn.clicked.connect(self._on_export_menu)
        self._detail_locate_btn.clicked.connect(self._on_locate_hit)
        self._detail_prev_btn.clicked.connect(self._on_prev_detail_hit)
        self._detail_next_btn.clicked.connect(self._on_next_detail_hit)
        self._detail_open_location_btn.clicked.connect(self._on_open_file_location)
        self._detail_copy_path_btn.clicked.connect(self._on_copy_path)
        self._detail_open_window_btn.clicked.connect(self._on_open_in_window)

        # actions 信号槽
        self._load_rules_action.triggered.connect(self._on_load_rules)
        self._edit_rules_action.triggered.connect(self._on_edit_rules)
        self._export_csv_action.triggered.connect(lambda: self._on_export("csv"))
        self._export_json_action.triggered.connect(lambda: self._on_export("json"))
        self._ui.quit_action.triggered.connect(self.close)
        self._ui.select_path_action.triggered.connect(self._on_select_path)
        self._scan_action.triggered.connect(self._on_scan)
        self._view_results_action.triggered.connect(lambda: self._switch_tab(0))
        self._view_rules_action.triggered.connect(lambda: self._switch_tab(1))
        self._view_history_action.triggered.connect(lambda: self._switch_tab(2))
        self._ui.about_action.triggered.connect(self._on_about)
        self._settings_action.triggered.connect(self._on_settings)

    def _switch_tab(self, index: int) -> None:
        """切换列表区 Tab 视图。"""
        if 0 <= index < self._tab_widget.count():
            self._tab_widget.setCurrentIndex(index)

    def _on_view_history(self) -> None:
        """切换到扫描历史视图。"""
        self._switch_tab(2)

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
            self._splitter.setSizes(self._config.splitter_sizes)

        # 恢复扫描模式
        self._scan_mode = self._config.scan_mode if self._config.scan_mode in ("full", "drive", "folder") else "folder"
        mode_index_map = {"full": 0, "drive": 1, "folder": 2}
        self._scan_mode_combo.blockSignals(True)
        self._scan_mode_combo.setCurrentIndex(mode_index_map[self._scan_mode])
        self._scan_mode_combo.blockSignals(False)
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
        self._use_builtin_checkbox.blockSignals(True)
        self._use_builtin_checkbox.setChecked(self._config.use_builtin)
        self._use_builtin_checkbox.blockSignals(False)

        self._rules_paths = [Path(p) for p in self._config.rules_paths if Path(p).exists()]

        self._path_combo.blockSignals(True)
        for p in self._config.scan_paths:
            self._path_combo.addItem(p)
        self._path_combo.blockSignals(False)

        # 恢复扫描历史
        self._scan_history = list(self._config.scan_paths)
        self._refresh_history_list()

        # 恢复首个有效路径作为扫描目标，启用扫描按钮
        if self._scan_mode == "folder" and self._path_combo.count() > 0:
            first_path = Path(self._path_combo.itemText(0))
            self._scan_root = first_path if first_path.exists() else None
        self._update_scan_button()

    def _save_config(self) -> None:
        """保存当前状态到配置文件。"""
        geo = self.geometry()
        self._config.window_geometry = [geo.x(), geo.y(), geo.width(), geo.height()]
        self._config.window_state = "maximized" if self.isMaximized() else "normal"
        self._config.splitter_sizes = list(self._splitter.sizes())
        self._config.scan_mode = self._scan_mode
        self._config.last_drive = self._selected_drive
        self._config.rules_paths = [str(p) for p in self._rules_paths]
        self._config.use_builtin = self._use_builtin
        self._config.scan_paths = [self._path_combo.itemText(i) for i in range(self._path_combo.count())]
        save_config(self._config)

    def _add_scan_path_history(self, path_str: str) -> None:
        """将路径添加到扫描历史下拉与历史列表（去重、最近优先、限制数量）。"""
        self._path_combo.blockSignals(True)
        idx = self._path_combo.findText(path_str)
        if idx >= 0:
            self._path_combo.removeItem(idx)
        self._path_combo.insertItem(0, path_str)
        while self._path_combo.count() > MAX_HISTORY:
            self._path_combo.removeItem(self._path_combo.count() - 1)
        self._path_combo.setCurrentIndex(0)
        self._path_combo.blockSignals(False)

        # 同步扫描历史
        if path_str in self._scan_history:
            self._scan_history.remove(path_str)
        self._scan_history.insert(0, path_str)
        while len(self._scan_history) > MAX_HISTORY:
            self._scan_history.pop()
        self._refresh_history_list()

    def _refresh_history_list(self) -> None:
        """刷新扫描历史列表。"""
        self._history_list.clear()
        for path_str in self._scan_history:
            item = QListWidgetItem(path_str)
            item.setToolTip(path_str)
            self._history_list.addItem(item)

    def _on_history_item_double_clicked(self, item: QListWidgetItem) -> None:
        """双击历史列表项切换到 folder 模式并选择该路径。"""
        path_str = item.text()
        path = Path(path_str)
        if not path.exists():
            QMessageBox.information(self, "提示", f"路径不存在:\n{path_str}")
            return
        self._scan_mode_combo.setCurrentIndex(2)
        self._scan_root = path
        self._add_scan_path_history(path_str)
        self._update_scan_button()
        self._switch_tab(0)

    # ----------------------------- 规则加载 -----------------------------

    def _init_rules(self) -> None:
        """启动时加载规则：默认加载内置通用规则。"""
        try:
            self._reload_ruleset()
            self._refresh_rules_tree()
            self._refresh_rules_file_list()
            self._update_scan_button()
            if self._ruleset is not None:
                self._rules_label.setText("规则: 内置通用规则")
                self._stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条通用规则")
        except RuleError as exc:
            logger.warning("内置规则加载失败: %s", exc)
            self._rules_label.setText("规则文件: 内置规则加载失败")

    def _reload_ruleset(self) -> None:
        """根据当前通用规则开关与用户规则路径列表重新加载规则集。"""
        if self._use_builtin:
            self._ruleset = load_with_builtin(self._rules_paths)
        elif self._rules_paths:
            rulesets = [load_ruleset(p) for p in self._rules_paths]
            self._ruleset = merge_multiple_rulesets(*rulesets)
        else:
            self._ruleset = None

    def _on_toggle_builtin(self, state: int) -> None:
        """通用规则复选框状态变更。"""
        self._use_builtin = bool(state)
        try:
            self._reload_ruleset()
            self._refresh_rules_tree()
            self._refresh_rules_file_list()
            self._update_scan_button()
            if self._ruleset is not None:
                self._rules_label.setText(f"规则: {self._build_rules_label()}")
                self._stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条规则")
            else:
                self._rules_label.setText("规则: 未加载")
                self._stats_label.setText("未加载规则")
        except RuleError as exc:
            QMessageBox.warning(self, "规则错误", f"重新加载规则失败:\n{exc}")

    # ----------------------------- 扫描模式 -----------------------------

    def _on_scan_mode_changed(self, index: int) -> None:
        """扫描模式切换：更新目标选择器可见性与扫描按钮状态。"""
        self._scan_mode = {0: "full", 1: "drive", 2: "folder"}.get(index, "folder")
        self._update_target_visibility()
        self._update_scan_button()

    def _update_target_visibility(self) -> None:
        """根据扫描模式切换目标选择区页面（QStackedWidget 保持布局稳定）。"""
        page_map = {"full": 0, "drive": 1, "folder": 2}
        self._target_stack.setCurrentIndex(page_map.get(self._scan_mode, 2))

    def _refresh_drive_buttons(self) -> None:
        """刷新盘符按钮列表（hard_disk 图标 + 盘符字母，平铺展示）。"""
        # 清除旧按钮
        for btn in self._drive_buttons:
            self._drive_button_group.removeButton(btn)
            self._drive_buttons_layout.removeWidget(btn)
            btn.deleteLater()
        self._drive_buttons.clear()

        for drive in list_drives(include_network=self._config.include_network_drives):
            letter = str(drive)[:1]
            btn = QPushButton(letter, self._target_stack.widget(1))
            btn.setObjectName(f"drive_btn_{letter}")
            btn.setCheckable(True)
            btn.setProperty("drive", str(drive))
            btn.setIcon(self._icon_hard_disk)
            btn.setIconSize(QSize(14, 50))
            self._drive_buttons_layout.addWidget(btn)
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
            self._rules_label.setText(f"规则: {self._build_rules_label()}")
            self._refresh_rules_tree()
            self._refresh_rules_file_list()
            self._update_scan_button()
            if self._ruleset is not None:
                self._stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条规则")
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
        path_str = self._path_combo.itemText(index)
        if not path_str:
            self._scan_root = None
        else:
            path = Path(path_str)
            self._scan_root = path if path.exists() else None
        self._update_scan_button()

    def _set_scan_controls_text(self, text: str) -> None:
        """同步设置扫描按钮与菜单 action 的文本。"""
        self._scan_btn.setText(text)
        self._scan_action.setText(text)

    def _update_scan_button_icon(self) -> None:
        """根据扫描状态切换扫描按钮图标。"""
        if self._scan_state == ScanState.RUNNING:
            icon = self._icon_pause
        elif self._scan_state == ScanState.PAUSED:
            icon = self._icon_rescan
        else:
            icon = self._icon_scan
        self._scan_btn.setIcon(icon)
        self._scan_action.setIcon(icon)

    def _on_scan(self) -> None:
        """扫描按钮：根据当前状态执行开始/暂停/继续。"""
        if self._scan_state == ScanState.RUNNING:
            self._pause_scan()
            return
        if self._scan_state == ScanState.PAUSED:
            self._resume_scan()
            return

        if self._ruleset is None:
            return

        roots = self._build_scan_roots()
        if not roots:
            QMessageBox.warning(self, "提示", "未选择有效的扫描目标")
            return

        self._result_tree.clear()
        self._detail_clear()
        self._scan_state = ScanState.RUNNING
        self._set_scan_controls_text("暂停扫描")
        self._update_scan_button_icon()
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._current_file_label.setVisible(True)
        self._current_file_label.setText("准备扫描...")
        self._stats_label.setText("扫描中...")

        self._worker = ScanWorker(
            ruleset=self._ruleset,
            roots=roots,
            scan_archives=self._config.scan_archives,
            max_workers=self._config.max_workers,
            max_depth=self._config.max_depth,
        )
        self._worker.progress_info.connect(self._on_scan_progress)
        self._worker.finished_report.connect(self._on_scan_finished)
        self._worker.failed.connect(self._on_scan_failed)
        self._worker.cancelled.connect(self._on_scan_cancelled)
        self._worker.start()

    def _pause_scan(self) -> None:
        """暂停扫描。"""
        if self._worker is not None:
            self._worker.pause()
        self._scan_state = ScanState.PAUSED
        self._set_scan_controls_text("继续扫描")
        self._update_scan_button_icon()
        self._stats_label.setText("已暂停")

    def _resume_scan(self) -> None:
        """恢复扫描。"""
        if self._worker is not None:
            self._worker.resume()
        self._scan_state = ScanState.RUNNING
        self._set_scan_controls_text("暂停扫描")
        self._update_scan_button_icon()
        self._stats_label.setText("扫描中...")

    def _on_scan_cancelled(self, report: ScanReport) -> None:
        """扫描被取消后的回调。"""
        self._last_report = report
        self._reset_scan_ui()
        self._populate_results(report)
        stats = report.stats
        self._stats_label.setText(
            f"已取消: 总计 {stats.total_files} | 扫描 {stats.scanned_files} | "
            f"命中 {stats.matched_files} | 耗时 {stats.duration_seconds:.2f}s"
        )

    def _reset_scan_ui(self) -> None:
        """重置扫描 UI 到空闲状态。"""
        self._scan_state = ScanState.IDLE
        self._progress.setVisible(False)
        self._current_file_label.setVisible(False)
        self._set_scan_controls_text("开始扫描")
        self._update_scan_button_icon()
        self._cleanup_worker()
        self._update_scan_button()

    def _cleanup_worker(self) -> None:
        """清理后台扫描线程：等待退出后释放引用。"""
        if self._worker is None:
            return
        self._worker.wait(2000)
        self._worker.deleteLater()
        self._worker = None

    def _on_scan_progress(self, info) -> None:  # type: ignore[no-untyped-def]
        """扫描实时进度回调。"""
        # 切换为确定进度模式
        if info.total > 0 and self._progress.maximum() != info.total:
            self._progress.setRange(0, info.total)
        self._progress.setValue(info.scanned)

        # 当前文件（截断显示）
        if info.current_file:
            path_text = info.current_file
            if len(path_text) > 100:
                path_text = "..." + path_text[-97:]
            self._current_file_label.setText(f"正在解析: {path_text}")

        # 详细统计
        self._stats_label.setText(
            f"已扫描 {info.scanned} | 跳过 {info.skipped} | "
            f"命中 {info.matched} | 错误 {info.errors} | "
            f"已用 {info.elapsed:.1f}s"
        )

    def _on_scan_finished(self, report: ScanReport) -> None:
        """扫描完成回调。"""
        self._last_report = report
        self._reset_scan_ui()

        self._populate_results(report)

        stats = report.stats
        self._stats_label.setText(
            f"完成: 总计 {stats.total_files} | 扫描 {stats.scanned_files} | "
            f"跳过 {stats.skipped_files} | 命中 {stats.matched_files} | "
            f"错误 {stats.errors} | 耗时 {stats.duration_seconds:.2f}s"
        )
        # 扫描完成后启用导出按钮
        self._export_btn.setEnabled(report.hits is not None and len(report.hits) > 0)

    def _on_scan_failed(self, error: str) -> None:
        """扫描失败回调。"""
        self._reset_scan_ui()
        self._stats_label.setText("扫描失败")
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
            content = self._format_report(self._last_report, fmt)
            path.write_text(content, encoding="utf-8")
            QMessageBox.information(self, "导出成功", f"已导出到:\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _on_about(self) -> None:
        """关于对话框。"""
        from fuscan import __version__

        QMessageBox.about(
            self,
            "关于 fuscan",
            f"fuscan {__version__}\n\n通用文件扫描器\n支持多格式与压缩文件扫描\n\n技术栈: Python + PySide2",
        )

    def _on_settings(self) -> None:
        """打开设置对话框，修改后保存配置并应用。"""
        from fuscan.gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self._config, self)
        if dialog.exec_() == QDialog.Accepted:
            self._save_config()
            self._use_builtin = self._config.use_builtin
            self._use_builtin_checkbox.blockSignals(True)
            self._use_builtin_checkbox.setChecked(self._config.use_builtin)
            self._use_builtin_checkbox.blockSignals(False)
            self._reload_ruleset()
            self._refresh_rules_tree()
            self._refresh_rules_file_list()
            self._refresh_drive_buttons()
            self._update_scan_button()
            if self._ruleset is not None:
                self._rules_label.setText(f"规则: {self._build_rules_label()}")
                self._stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条规则")

    def _on_batch_process(self) -> None:
        """批量处理按钮（预留）：提示功能未实现。"""
        QMessageBox.information(self, "提示", "批量处理功能尚未实现，敬请期待。")

    # ----------------------------- 详情区更新 -----------------------------

    def _on_result_selection_changed(self) -> None:
        """结果树选中变化：更新详情区主体。"""
        items = self._result_tree.selectedItems()
        if not items:
            self._detail_clear()
            return
        item = items[0]
        result = item.data(0, Qt.UserRole)
        if result is None and item.parent() is not None:
            result = item.parent().data(0, Qt.UserRole)
        if result is None:
            self._detail_clear()
            return
        self._detail_show_result(result)

    def _detail_clear(self) -> None:
        """清空详情区，切换到空态。"""
        self._detail_action_stack.setCurrentIndex(0)
        self._detail_main_stack.setCurrentIndex(0)
        self._detail_current_result = None
        self._detail_hit_positions = []
        self._detail_current_hit_index = -1
        self._detail_preview.clear()
        self._detail_hits_table.setRowCount(0)
        self._detail_info_label.setText("")

    def _detail_show_result(self, result: ScanResult) -> None:
        """在详情区展示选中项的详情，切换到非空态。"""
        self._detail_current_result = result
        self._detail_action_stack.setCurrentIndex(1)
        self._detail_main_stack.setCurrentIndex(1)
        self._populate_detail_file_info(result)
        self._populate_detail_hits_table(result)
        self._populate_detail_preview(result)

    def _populate_detail_file_info(self, result: ScanResult) -> None:
        """填充详情区文件元信息。"""
        path = result.path
        size = result.size
        try:
            mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            mtime_str = mtime.strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            mtime_str = "无法获取"

        info_html = (
            f"<b>文件路径:</b> {html.escape(str(path))}<br>"
            f"<b>文件大小:</b> {_format_size(size)} ({size} 字节)<br>"
            f"<b>修改时间:</b> {html.escape(mtime_str)}<br>"
            f"<b>命中规则数:</b> {len(result.hits)}"
        )
        self._detail_info_label.setText(info_html)

    def _populate_detail_hits_table(self, result: ScanResult) -> None:
        """填充详情区命中规则表。"""
        hits = result.hits
        self._detail_hits_table.setRowCount(len(hits))
        for row, hit in enumerate(hits):
            self._detail_hits_table.setItem(row, 0, QTableWidgetItem(hit.rule_name))
            self._detail_hits_table.setItem(row, 1, QTableWidgetItem(hit.severity.value))
            self._detail_hits_table.setItem(row, 2, QTableWidgetItem(hit.detail))

    def _populate_detail_preview(self, result: ScanResult) -> None:
        """填充详情区内容预览，命中关键词高亮并定位到首个命中。"""
        path = result.path
        truncated = False

        # 优先使用提取器（支持 PDF/DOCX 等格式），失败回退到纯文本
        try:
            content = extract_content(path)
        except Exception:
            logger.debug("提取器提取失败，回退到纯文本: %s", path, exc_info=True)
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                logger.warning("读取内容预览失败 %s", path, exc_info=True)
                self._detail_preview.setPlainText(f"无法读取文件内容: {exc}")
                self._update_detail_nav_label()
                return

        if not content:
            self._detail_preview.setPlainText("(文件内容为空或为二进制)")
            self._update_detail_nav_label()
            return

        # 截断过长内容
        if len(content) > _PREVIEW_MAX_CHARS:
            content = content[:_PREVIEW_MAX_CHARS]
            truncated = True

        keywords = _extract_keywords(result.hits)
        html_content = _build_preview_html(content, keywords)
        if truncated:
            html_content += "<p style='color: #888; font-size: 11px;'>(内容已截断，仅显示前 100KB)</p>"
        self._detail_preview.setHtml(html_content)

        # 查找所有关键词位置并定位到首个命中
        self._find_detail_hit_positions(keywords)
        if self._detail_hit_positions:
            self._detail_current_hit_index = 0
            self._highlight_current_detail_hit()
            self._scroll_to_current_detail_hit()
        self._update_detail_nav_label()

    def _find_detail_hit_positions(self, keywords: Sequence[str]) -> None:
        """在详情区预览文档中查找所有关键词出现位置，按位置排序后存储。"""
        self._detail_hit_positions = []
        if not keywords:
            return
        doc = self._detail_preview.document()
        seen: set[tuple[int, int]] = set()
        for kw in sorted(set(keywords), key=len, reverse=True):
            cursor = doc.find(kw)
            while not cursor.isNull():
                pos = (cursor.selectionStart(), cursor.selectionEnd())
                if pos not in seen:
                    seen.add(pos)
                    self._detail_hit_positions.append(pos)
                cursor = doc.find(kw, cursor)
        self._detail_hit_positions.sort()

    def _highlight_current_detail_hit(self) -> None:
        """用橙色背景高亮当前命中位置，区别于其他命中的黄色高亮。"""
        if self._detail_current_hit_index < 0 or self._detail_current_hit_index >= len(self._detail_hit_positions):
            self._detail_preview.setExtraSelections([])
            return
        start, end = self._detail_hit_positions[self._detail_current_hit_index]
        sel = QTextEdit.ExtraSelection()
        cursor = self._detail_preview.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        sel.cursor = cursor
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 165, 0))
        sel.format = fmt
        self._detail_preview.setExtraSelections([sel])

    def _scroll_to_current_detail_hit(self) -> None:
        """滚动详情区预览使当前命中位置可见。"""
        if self._detail_current_hit_index < 0 or self._detail_current_hit_index >= len(self._detail_hit_positions):
            return
        start, _ = self._detail_hit_positions[self._detail_current_hit_index]
        cursor = self._detail_preview.textCursor()
        cursor.setPosition(start)
        self._detail_preview.setTextCursor(cursor)
        self._detail_preview.ensureCursorVisible()

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

    def _on_locate_hit(self) -> None:
        """定位命中：滚动到当前命中位置。"""
        self._scroll_to_current_detail_hit()

    def _update_detail_nav_label(self) -> None:
        """更新详情区导航标签与按钮状态。"""
        total = len(self._detail_hit_positions)
        if total == 0:
            self._detail_nav_label.setText("无命中")
            self._detail_prev_btn.setEnabled(False)
            self._detail_next_btn.setEnabled(False)
            self._detail_locate_btn.setEnabled(False)
        else:
            self._detail_nav_label.setText(f"{self._detail_current_hit_index + 1} / {total}")
            self._detail_prev_btn.setEnabled(True)
            self._detail_next_btn.setEnabled(True)
            self._detail_locate_btn.setEnabled(True)

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
            self._stats_label.setText("已复制路径到剪贴板")

    def _on_open_file_location(self) -> None:
        """在文件管理器中打开所在目录。"""
        if self._detail_current_result is None:
            return
        path = self._detail_current_result.path
        try:
            import subprocess
            import sys

            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception as exc:
            logger.warning("打开文件位置失败: %s", exc, exc_info=True)
            QMessageBox.warning(self, "提示", f"打开文件位置失败:\n{exc}")

    # ----------------------------- 辅助方法 -----------------------------

    def _refresh_rules_tree(self) -> None:
        """刷新规则列表展示。"""
        self._rules_tree.clear()
        if self._ruleset is None:
            return
        for rule in self._ruleset.rules:
            item = QTreeWidgetItem([
                rule.name,
                rule.severity.value,
                ", ".join(rule.file_extensions) if rule.file_extensions else "(全部)",
            ])
            self._rules_tree.addTopLevelItem(item)

    def _refresh_rules_file_list(self) -> None:
        """刷新规则文件列表展示。"""
        self._rules_file_list.clear()
        for path in self._rules_paths:
            item = QListWidgetItem(str(path))
            item.setToolTip(str(path))
            self._rules_file_list.addItem(item)

    def _build_rules_label(self) -> str:
        """构造规则标签文本。"""
        names = [p.name for p in self._rules_paths]
        user_part = ", ".join(names)
        if self._use_builtin:
            return f"通用规则 + {user_part}" if user_part else "通用规则"
        return user_part if user_part else "未加载"

    def _on_move_rule_up(self) -> None:
        """将选中的规则文件上移一位。"""
        row = self._rules_file_list.currentRow()
        if row <= 0:
            return
        self._rules_paths[row - 1], self._rules_paths[row] = (
            self._rules_paths[row],
            self._rules_paths[row - 1],
        )
        self._refresh_rules_file_list()
        self._rules_file_list.setCurrentRow(row - 1)
        self._reload_and_refresh()

    def _on_move_rule_down(self) -> None:
        """将选中的规则文件下移一位。"""
        row = self._rules_file_list.currentRow()
        if row < 0 or row >= len(self._rules_paths) - 1:
            return
        self._rules_paths[row + 1], self._rules_paths[row] = (
            self._rules_paths[row],
            self._rules_paths[row + 1],
        )
        self._refresh_rules_file_list()
        self._rules_file_list.setCurrentRow(row + 1)
        self._reload_and_refresh()

    def _on_remove_rule(self) -> None:
        """移除选中的规则文件。"""
        row = self._rules_file_list.currentRow()
        if row < 0:
            return
        del self._rules_paths[row]
        self._refresh_rules_file_list()
        self._reload_and_refresh()

    def _on_edit_rules(self) -> None:
        """打开规则编辑器对话框。"""
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
            self._rules_label.setText(f"规则: {self._build_rules_label()}")
            self._update_scan_button()
            if self._ruleset is not None:
                self._stats_label.setText(f"已加载 {len(self._ruleset.rules)} 条规则")
            else:
                self._stats_label.setText("未加载规则")
        except RuleError as exc:
            QMessageBox.warning(self, "规则错误", f"重新加载规则失败:\n{exc}")

    def _populate_results(self, report: ScanReport) -> None:
        """填充结果树：存储报告、更新规则筛选下拉、刷新结果树。"""
        self._last_report = report
        self._update_rule_filter_options(report)
        self._refresh_result_tree()
        # 有结果时启用导出按钮
        self._export_btn.setEnabled(len(report.hits) > 0)

    def _update_rule_filter_options(self, report: ScanReport) -> None:
        """根据扫描结果更新规则筛选下拉项。"""
        current_rule = self._rule_filter_combo.currentData()
        self._rule_filter_combo.blockSignals(True)
        self._rule_filter_combo.clear()
        self._rule_filter_combo.addItem("全部规则", "")
        rule_names: list[str] = []
        seen = set()
        for result in report.hits:
            for hit in result.hits:
                if hit.rule_name not in seen:
                    seen.add(hit.rule_name)
                    rule_names.append(hit.rule_name)
        for name in sorted(rule_names):
            self._rule_filter_combo.addItem(name, name)
        # 恢复之前选中的规则
        if current_rule:
            idx = self._rule_filter_combo.findData(current_rule)
            if idx >= 0:
                self._rule_filter_combo.setCurrentIndex(idx)
        self._rule_filter_combo.blockSignals(False)

    def _refresh_result_tree(self) -> None:
        """根据当前筛选条件与分组模式刷新结果树。"""
        self._result_tree.clear()
        if self._last_report is None:
            return

        path_filter = self._path_filter_input.text().strip().lower()
        rule_filter = self._rule_filter_combo.currentData() or ""
        group_mode = self._group_mode_combo.currentData() or "flat"

        # 筛选 + 收集命中
        filtered = self._filter_results(self._last_report, path_filter, rule_filter)

        if group_mode == "rule":
            self._populate_grouped_by_rule(filtered)
        elif group_mode == "severity":
            self._populate_grouped_by_severity(filtered)
        else:
            self._populate_flat(filtered)

    def _filter_results(
        self,
        report: ScanReport,
        path_filter: str,
        rule_filter: str,
    ) -> list[ScanResult]:
        """按路径与规则筛选结果，返回符合条件的 ScanResult 列表。

        路径筛选为大小写不敏感子串匹配；规则筛选时仅保留包含该规则命中的文件，
        且每个 ScanResult 的 hits 被过滤为仅匹配规则的命中。
        """
        result: list[ScanResult] = []
        for sr in report.hits:
            if path_filter and path_filter not in str(sr.path).lower():
                continue
            if rule_filter:
                matching_hits = tuple(h for h in sr.hits if h.rule_name == rule_filter)
                if not matching_hits:
                    continue
                result.append(ScanResult(path=sr.path, size=sr.size, hits=matching_hits, errors=sr.errors))
            else:
                result.append(sr)
        return result

    def _populate_flat(self, results: list[ScanResult]) -> None:
        """不分组：文件为顶层项，规则命中为子项。"""
        for sr in results:
            file_item = QTreeWidgetItem([
                str(sr.path),
                "",
                sr.max_severity.value,
                str(len(sr.hits)),
                f"{len(sr.hits)} 条命中",
            ])
            file_item.setData(0, Qt.UserRole, sr)
            file_item.setTextAlignment(3, Qt.AlignCenter)
            for hit in sr.hits:
                child = QTreeWidgetItem([
                    "",
                    hit.rule_name,
                    hit.severity.value,
                    "",
                    hit.detail,
                ])
                file_item.addChild(child)
            self._result_tree.addTopLevelItem(file_item)

    def _populate_grouped_by_rule(self, results: list[ScanResult]) -> None:
        """按规则分组：规则名为顶层项，文件为子项。"""
        rule_map: dict[str, list[tuple[ScanResult, RuleHit]]] = {}
        for sr in results:
            for hit in sr.hits:
                rule_map.setdefault(hit.rule_name, []).append((sr, hit))

        for rule_name in sorted(rule_map.keys()):
            entries = rule_map[rule_name]
            hit_count = len(entries)
            top = QTreeWidgetItem([
                "",
                rule_name,
                "",
                str(hit_count),
                f"{hit_count} 个文件",
            ])
            top.setTextAlignment(3, Qt.AlignCenter)
            for sr, hit in entries:
                child = QTreeWidgetItem([
                    str(sr.path),
                    "",
                    hit.severity.value,
                    "",
                    hit.detail,
                ])
                child.setData(0, Qt.UserRole, sr)
                top.addChild(child)
            self._result_tree.addTopLevelItem(top)

    def _populate_grouped_by_severity(self, results: list[ScanResult]) -> None:
        """按严重等级分组：等级为顶层项，文件为子项。"""
        severity_map: dict[str, list[ScanResult]] = {}
        for sr in results:
            sev = sr.max_severity.value
            severity_map.setdefault(sev, []).append(sr)

        for severity in sorted(severity_map.keys(), reverse=True):
            entries = severity_map[severity]
            file_count = len(entries)
            top = QTreeWidgetItem([
                "",
                "",
                severity,
                str(file_count),
                f"{file_count} 个文件",
            ])
            top.setTextAlignment(3, Qt.AlignCenter)
            for sr in entries:
                child = QTreeWidgetItem([
                    str(sr.path),
                    "",
                    severity,
                    str(len(sr.hits)),
                    f"{len(sr.hits)} 条命中",
                ])
                child.setData(0, Qt.UserRole, sr)
                child.setTextAlignment(3, Qt.AlignCenter)
                top.addChild(child)
            self._result_tree.addTopLevelItem(top)

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
        """根据规则、扫描模式与目标就绪状态更新扫描按钮。

        扫描进行中（RUNNING/PAUSED）时按钮始终可用，供暂停/继续使用，
        不受规则或目标就绪状态影响。
        """
        if self._scan_state in (ScanState.RUNNING, ScanState.PAUSED):
            self._scan_btn.setEnabled(True)
            self._scan_action.setEnabled(True)
            return
        if self._ruleset is None:
            ready = False
        elif self._scan_mode == "full":
            ready = True
        elif self._scan_mode == "drive":
            ready = self._selected_drive is not None
        else:  # folder
            ready = self._scan_root is not None
        self._scan_btn.setEnabled(ready)
        self._scan_action.setEnabled(ready)

    @staticmethod
    def _format_report(report: ScanReport, fmt: str) -> str:
        """格式化报告为字符串。"""
        if fmt == "json":
            data = {
                "root": str(report.root),
                "stats": asdict(report.stats),
                "hits": [
                    {
                        "path": str(r.path),
                        "size": r.size,
                        "max_severity": r.max_severity.value,
                        "rules": [asdict(h) for h in r.hits],
                    }
                    for r in report.hits
                ],
            }
            return json.dumps(data, ensure_ascii=False, indent=2)

        # CSV
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["path", "size", "severity", "rule", "detail"])
        for r in report.hits:
            for hit in r.hits:
                writer.writerow([str(r.path), r.size, hit.severity.value, hit.rule_name, hit.detail])
        return buf.getvalue()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """关闭时保存配置并终止后台线程。"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._save_config()
        super().closeEvent(event)
