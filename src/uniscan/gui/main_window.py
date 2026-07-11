"""GUI 主窗口。

提供：

- 三种扫描模式：全盘扫描 / 选择盘符 / 选择文件夹
- 规则文件加载与展示
- 后台扫描触发与进度显示
- 结果树形展示（按文件分组）
- 结果导出（CSV/JSON）

设计要点：

- 杀毒软件风格：模式卡片 + 醒目扫描按钮 + 大进度条
- 扫描在 ScanWorker（QThread）中执行，避免阻塞 UI
- 结果以 QTreeWidget 展示，顶层节点为文件，子节点为规则命中
"""

from __future__ import annotations

import csv
import enum
import io
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QAction,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from uniscan.builtin import load_with_builtin
from uniscan.config import MAX_HISTORY, Config, load_config, save_config
from uniscan.gui.detail_dialog import HitDetailDialog
from uniscan.gui.worker import ScanWorker
from uniscan.rules import RuleError, load_ruleset, merge_multiple_rulesets
from uniscan.rules.model import RuleSet
from uniscan.scanner import ScanReport, list_drives
from uniscan.scanner.result import RuleHit, ScanResult

__all__ = ["MainWindow", "ScanState"]

logger = logging.getLogger(__name__)


class ScanState(enum.Enum):
    """扫描状态。"""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


class MainWindow(QMainWindow):
    """主窗口：扫描器 GUI 入口。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("uniscan 通用文件扫描器")

        self._config: Config = load_config()
        self._ruleset: Optional[RuleSet] = None
        self._rules_paths: List[Path] = []
        self._scan_root: Optional[Path] = None
        self._last_report: Optional[ScanReport] = None
        self._worker: Optional[ScanWorker] = None
        self._scan_state: ScanState = ScanState.IDLE
        self._use_builtin: bool = True
        # 扫描模式："full"（全盘）、"drive"（盘符）、"folder"（文件夹）
        self._scan_mode: str = "folder"

        self._init_ui()
        self._init_menu()
        self._init_toolbar()
        self._apply_qss()
        self._apply_config()
        self._init_rules()

    # ----------------------------- UI 初始化 -----------------------------

    def _init_ui(self) -> None:
        """初始化中央 widget：模式区 + 控制区 + 主体分割器。"""
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._build_scan_mode_area())
        layout.addWidget(self._build_scan_control_area())
        layout.addWidget(self._build_main_splitter(), stretch=1)

    def _build_scan_mode_area(self) -> QWidget:
        """构造扫描模式选择区：三张卡片按钮 + 盘符下拉。"""
        container = QFrame()
        container.setObjectName("modeArea")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        self._mode_btn_group = QButtonGroup(self)
        self._mode_btn_group.setExclusive(True)

        self._full_btn = QPushButton("全盘扫描\n扫描所有盘符")
        self._full_btn.setCheckable(True)
        self._full_btn.setObjectName("modeCard")
        self._full_btn.setCursor(Qt.PointingHandCursor)

        self._drive_btn = QPushButton("选择盘符\n扫描指定盘符")
        self._drive_btn.setCheckable(True)
        self._drive_btn.setObjectName("modeCard")
        self._drive_btn.setCursor(Qt.PointingHandCursor)

        self._folder_btn = QPushButton("选择文件夹\n扫描指定目录")
        self._folder_btn.setCheckable(True)
        self._folder_btn.setObjectName("modeCard")
        self._folder_btn.setCursor(Qt.PointingHandCursor)
        self._folder_btn.setChecked(True)

        self._mode_btn_group.addButton(self._full_btn, 0)
        self._mode_btn_group.addButton(self._drive_btn, 1)
        self._mode_btn_group.addButton(self._folder_btn, 2)
        self._mode_btn_group.buttonClicked.connect(self._on_scan_mode_changed)

        layout.addWidget(self._full_btn)
        layout.addWidget(self._drive_btn)
        layout.addWidget(self._folder_btn)
        layout.addStretch()

        self._drive_label = QLabel("盘符:")
        self._drive_combo = QComboBox()
        self._drive_combo.setToolTip("选择要扫描的盘符")
        self._drive_combo.setMinimumWidth(80)
        self._drive_combo.currentIndexChanged.connect(self._on_drive_selected)
        self._refresh_drive_combo()
        layout.addWidget(self._drive_label)
        layout.addWidget(self._drive_combo)

        return container

    def _build_scan_control_area(self) -> QWidget:
        """构造扫描控制区：规则行 + 路径行 + 醒目扫描按钮 + 进度条 + 统计。"""
        container = QFrame()
        container.setObjectName("controlArea")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(6)

        layout.addLayout(self._build_rules_row())
        layout.addWidget(self._build_target_row())

        # 扫描控制按钮行：扫描按钮（开始/暂停/继续）+ 停止按钮
        btn_row = QHBoxLayout()
        self._scan_btn = QPushButton("开始扫描")
        self._scan_btn.setObjectName("scanBtn")
        self._scan_btn.setCursor(Qt.PointingHandCursor)
        self._scan_btn.setMinimumHeight(48)
        self._scan_btn.clicked.connect(self._on_scan)
        self._scan_btn.setEnabled(False)
        btn_row.addWidget(self._scan_btn, stretch=3)

        self._stop_btn = QPushButton("停止")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.setMinimumHeight(48)
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setVisible(False)
        btn_row.addWidget(self._stop_btn, stretch=1)
        layout.addLayout(btn_row)

        # 大进度条
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setTextVisible(True)
        self._progress.setMinimumHeight(22)
        layout.addWidget(self._progress)

        # 当前文件标签
        self._current_file_label = QLabel("")
        self._current_file_label.setObjectName("currentFileLabel")
        self._current_file_label.setVisible(False)
        layout.addWidget(self._current_file_label)

        # 统计标签
        self._stats_label = QLabel("就绪")
        self._stats_label.setObjectName("statsLabel")
        layout.addWidget(self._stats_label)

        return container

    def _build_rules_row(self) -> QHBoxLayout:
        """构造规则加载行。"""
        self._load_rules_btn = QPushButton("加载规则...")
        self._load_rules_btn.clicked.connect(self._on_load_rules)
        self._rules_label = QLabel("规则: 未加载")
        self._rules_label.setStyleSheet("padding: 4px;")
        self._use_builtin_checkbox = QCheckBox("使用通用规则")
        self._use_builtin_checkbox.setChecked(True)
        self._use_builtin_checkbox.setToolTip("勾选后加载软件内置通用规则，用户规则中同名规则会覆盖通用规则")
        self._use_builtin_checkbox.stateChanged.connect(self._on_toggle_builtin)

        rules_row = QHBoxLayout()
        rules_row.addWidget(self._load_rules_btn)
        rules_row.addWidget(self._rules_label, stretch=1)
        rules_row.addWidget(self._use_builtin_checkbox)
        return rules_row

    def _build_target_row(self) -> QWidget:
        """构造目标路径行（仅 folder 模式可见）。"""
        self._target_row = QWidget()
        target_layout = QHBoxLayout(self._target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        self._path_label = QLabel("扫描路径:")
        self._path_combo = QComboBox()
        self._path_combo.setToolTip("扫描路径（可从历史记录中选择）")
        self._path_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._path_combo.currentIndexChanged.connect(self._on_path_selected)
        self._select_path_btn = QPushButton("选择路径...")
        self._select_path_btn.clicked.connect(self._on_select_path)
        target_layout.addWidget(self._path_label)
        target_layout.addWidget(self._path_combo, stretch=1)
        target_layout.addWidget(self._select_path_btn)
        return self._target_row

    def _build_main_splitter(self) -> QSplitter:
        """构造主体分割器：左侧规则面板 + 右侧结果树。"""
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.addWidget(self._build_left_panel())

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        right_layout.addWidget(self._build_result_filter_bar())

        self._result_tree = QTreeWidget()
        self._result_tree.setObjectName("resultTree")
        self._result_tree.setHeaderLabels(["路径", "规则", "严重等级", "命中数", "详情"])
        self._result_tree.setColumnWidth(0, 400)
        self._result_tree.setColumnWidth(1, 150)
        self._result_tree.setColumnWidth(2, 80)
        self._result_tree.setColumnWidth(3, 60)
        self._result_tree.setAlternatingRowColors(True)
        self._result_tree.setRootIsDecorated(True)
        self._result_tree.setSortingEnabled(True)
        self._result_tree.itemDoubleClicked.connect(self._on_result_double_clicked)
        right_layout.addWidget(self._result_tree, stretch=1)

        self._splitter.addWidget(right_panel)

        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 4)
        return self._splitter

    def _build_result_filter_bar(self) -> QWidget:
        """构造结果筛选栏：路径筛选 + 规则筛选 + 分组模式。"""
        bar = QFrame()
        bar.setObjectName("filterBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        layout.addWidget(QLabel("筛选:"))

        self._path_filter_input = QLineEdit()
        self._path_filter_input.setPlaceholderText("按路径筛选...")
        self._path_filter_input.setClearButtonEnabled(True)
        self._path_filter_input.textChanged.connect(self._refresh_result_tree)
        layout.addWidget(self._path_filter_input, stretch=2)

        layout.addWidget(QLabel("规则:"))
        self._rule_filter_combo = QComboBox()
        self._rule_filter_combo.addItem("全部规则", "")
        self._rule_filter_combo.currentIndexChanged.connect(self._refresh_result_tree)
        layout.addWidget(self._rule_filter_combo, stretch=1)

        layout.addWidget(QLabel("分组:"))
        self._group_mode_combo = QComboBox()
        self._group_mode_combo.addItem("不分组", "flat")
        self._group_mode_combo.addItem("按规则", "rule")
        self._group_mode_combo.addItem("按严重等级", "severity")
        self._group_mode_combo.currentIndexChanged.connect(self._refresh_result_tree)
        layout.addWidget(self._group_mode_combo, stretch=1)

        return bar

    def _build_left_panel(self) -> QWidget:
        """构造左侧面板：规则文件列表 + 排序按钮 + 规则树。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("规则文件（顺序从上到下，后者覆盖前者）"))

        self._rules_file_list = QListWidget()
        self._rules_file_list.setToolTip("已加载的规则文件，列表顺序代表优先级（从低到高）")
        layout.addWidget(self._rules_file_list)

        btn_layout = QHBoxLayout()
        self._move_up_btn = QPushButton("上移")
        self._move_up_btn.setToolTip("将选中的规则文件上移（优先级降低）")
        self._move_up_btn.clicked.connect(self._on_move_rule_up)
        self._move_down_btn = QPushButton("下移")
        self._move_down_btn.setToolTip("将选中的规则文件下移（优先级升高）")
        self._move_down_btn.clicked.connect(self._on_move_rule_down)
        self._remove_rule_btn = QPushButton("移除")
        self._remove_rule_btn.setToolTip("移除选中的规则文件")
        self._remove_rule_btn.clicked.connect(self._on_remove_rule)
        self._edit_rule_btn = QPushButton("编辑")
        self._edit_rule_btn.setToolTip("编辑选中的规则文件")
        self._edit_rule_btn.clicked.connect(self._on_edit_rules)
        btn_layout.addWidget(self._move_up_btn)
        btn_layout.addWidget(self._move_down_btn)
        btn_layout.addWidget(self._remove_rule_btn)
        btn_layout.addWidget(self._edit_rule_btn)
        layout.addLayout(btn_layout)

        self._rules_tree = QTreeWidget()
        self._rules_tree.setHeaderLabels(["规则名", "严重等级", "扩展名"])
        self._rules_tree.setRootIsDecorated(False)
        layout.addWidget(self._rules_tree, stretch=1)
        return panel

    def _init_menu(self) -> None:
        """初始化菜单栏。"""
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        load_rules_action = QAction("加载规则...", self)
        load_rules_action.triggered.connect(self._on_load_rules)
        file_menu.addAction(load_rules_action)

        edit_rules_action = QAction("编辑规则...", self)
        edit_rules_action.triggered.connect(self._on_edit_rules)
        file_menu.addAction(edit_rules_action)

        export_csv_action = QAction("导出 CSV...", self)
        export_csv_action.triggered.connect(lambda: self._on_export("csv"))
        file_menu.addAction(export_csv_action)

        export_json_action = QAction("导出 JSON...", self)
        export_json_action.triggered.connect(lambda: self._on_export("json"))
        file_menu.addAction(export_json_action)

        quit_action = QAction("退出(&Q)", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        scan_menu = menubar.addMenu("扫描(&S)")
        select_path_action = QAction("选择扫描路径...", self)
        select_path_action.triggered.connect(self._on_select_path)
        scan_menu.addAction(select_path_action)

        self._scan_action = QAction("开始扫描", self)
        self._scan_action.setShortcut("F5")
        self._scan_action.triggered.connect(self._on_scan)
        scan_menu.addAction(self._scan_action)

        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _init_toolbar(self) -> None:
        """工具栏与菜单复用 action。"""
        toolbar = self.addToolBar("主工具栏")
        toolbar.addAction(self._scan_action)

    def _apply_qss(self) -> None:
        """应用杀毒软件风格样式表。

        字体层级：扫描按钮（主操作）18px > 模式卡片 14px > 统计标签 13px > 当前文件 12px。
        """
        self.setStyleSheet(
            """
            QPushButton#modeCard {
                text-align: left;
                padding: 14px 18px;
                border: 2px solid #d0d0d0;
                border-radius: 8px;
                background: #fafafa;
                font-size: 14px;
                min-width: 120px;
            }
            QPushButton#modeCard:hover {
                border-color: #90CAF9;
                background: #F5FBFF;
            }
            QPushButton#modeCard:checked {
                border-color: #1976D2;
                background: #E3F2FD;
                font-weight: bold;
                color: #1565C0;
            }
            QPushButton#scanBtn {
                background: #43A047;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 18px;
                font-weight: bold;
                padding: 14px 32px;
                min-height: 24px;
            }
            QPushButton#scanBtn:hover {
                background: #388E3C;
            }
            QPushButton#scanBtn:pressed {
                background: #2E7D32;
            }
            QPushButton#scanBtn:disabled {
                background: #BDBDBD;
                color: #EEEEEE;
            }
            QPushButton#stopBtn {
                background: #E53935;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: bold;
                padding: 14px 24px;
                min-height: 24px;
            }
            QPushButton#stopBtn:hover {
                background: #C62828;
            }
            QPushButton#stopBtn:pressed {
                background: #B71C1C;
            }
            QPushButton#stopBtn:disabled {
                background: #BDBDBD;
                color: #EEEEEE;
            }
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 4px;
                text-align: center;
                background: #f0f0f0;
            }
            QProgressBar::chunk {
                background: #43A047;
                border-radius: 3px;
            }
            QLabel#currentFileLabel {
                font-size: 12px;
                color: #757575;
                padding: 1px 4px;
            }
            QLabel#statsLabel {
                font-size: 13px;
                color: #424242;
                padding: 2px 4px;
            }
            QTreeWidget#resultTree {
                font-size: 13px;
                alternate-background-color: #F7FBFF;
            }
            QTreeWidget#resultTree::item {
                min-height: 22px;
            }
            QFrame#modeArea, QFrame#controlArea {
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                background: #ffffff;
            }
            """
        )

    # ----------------------------- 配置持久化 -----------------------------

    def _apply_config(self) -> None:
        """应用配置：恢复窗口几何、分割器、扫描模式、规则路径、扫描历史。"""
        if self._config.window_geometry and len(self._config.window_geometry) == 4:
            x, y, w, h = self._config.window_geometry
            self.setGeometry(x, y, w, h)
        if self._config.window_state == "maximized":
            self.showMaximized()

        if self._config.splitter_sizes:
            self._splitter.setSizes(self._config.splitter_sizes)

        # 恢复扫描模式
        self._scan_mode = self._config.scan_mode if self._config.scan_mode in ("full", "drive", "folder") else "folder"
        mode_btn_map = {"full": self._full_btn, "drive": self._drive_btn, "folder": self._folder_btn}
        mode_btn_map[self._scan_mode].setChecked(True)
        self._update_target_visibility()

        # 恢复上次选择的盘符
        if self._config.last_drive:
            idx = self._drive_combo.findData(self._config.last_drive)
            if idx >= 0:
                self._drive_combo.blockSignals(True)
                self._drive_combo.setCurrentIndex(idx)
                self._drive_combo.blockSignals(False)

        self._use_builtin = self._config.use_builtin
        self._use_builtin_checkbox.blockSignals(True)
        self._use_builtin_checkbox.setChecked(self._config.use_builtin)
        self._use_builtin_checkbox.blockSignals(False)

        self._rules_paths = [Path(p) for p in self._config.rules_paths if Path(p).exists()]

        self._path_combo.blockSignals(True)
        for p in self._config.scan_paths:
            self._path_combo.addItem(p)
        self._path_combo.blockSignals(False)

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
        self._config.last_drive = self._drive_combo.currentData() if self._drive_combo.count() > 0 else None
        self._config.rules_paths = [str(p) for p in self._rules_paths]
        self._config.use_builtin = self._use_builtin
        self._config.scan_paths = [self._path_combo.itemText(i) for i in range(self._path_combo.count())]
        save_config(self._config)

    def _add_scan_path_history(self, path_str: str) -> None:
        """将路径添加到扫描历史下拉（去重、最近优先、限制数量）。"""
        self._path_combo.blockSignals(True)
        idx = self._path_combo.findText(path_str)
        if idx >= 0:
            self._path_combo.removeItem(idx)
        self._path_combo.insertItem(0, path_str)
        while self._path_combo.count() > MAX_HISTORY:
            self._path_combo.removeItem(self._path_combo.count() - 1)
        self._path_combo.setCurrentIndex(0)
        self._path_combo.blockSignals(False)

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

    def _on_scan_mode_changed(self, button) -> None:  # type: ignore[no-untyped-def]
        """扫描模式切换：更新目标选择器可见性与扫描按钮状态。"""
        btn_id = self._mode_btn_group.id(button)
        self._scan_mode = {0: "full", 1: "drive", 2: "folder"}.get(btn_id, "folder")
        self._update_target_visibility()
        self._update_scan_button()

    def _update_target_visibility(self) -> None:
        """根据扫描模式更新目标选择器可见性。"""
        is_drive = self._scan_mode == "drive"
        is_folder = self._scan_mode == "folder"
        self._drive_label.setVisible(is_drive)
        self._drive_combo.setVisible(is_drive)
        self._target_row.setVisible(is_folder)

    def _refresh_drive_combo(self) -> None:
        """刷新盘符下拉列表。"""
        self._drive_combo.blockSignals(True)
        self._drive_combo.clear()
        for drive in list_drives():
            self._drive_combo.addItem(str(drive), str(drive))
        self._drive_combo.blockSignals(False)

    def _on_drive_selected(self, _index: int) -> None:
        """盘符选择变更。"""
        self._update_scan_button()

    def _build_scan_roots(self) -> List[Path]:
        """根据扫描模式构造根路径列表。"""
        if self._scan_mode == "full":
            return list_drives()
        if self._scan_mode == "drive":
            data = self._drive_combo.currentData()
            return [Path(data)] if data else []
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
        """同步设置扫描按钮与菜单/工具栏 action 的文本。"""
        self._scan_btn.setText(text)
        self._scan_action.setText(text)

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
        self._scan_state = ScanState.RUNNING
        self._set_scan_controls_text("暂停扫描")
        self._stop_btn.setVisible(True)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._current_file_label.setVisible(True)
        self._current_file_label.setText("准备扫描...")
        self._stats_label.setText("扫描中...")

        self._worker = ScanWorker(
            ruleset=self._ruleset,
            roots=roots,
            scan_archives=True,
            max_workers=8,
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
        self._stats_label.setText("已暂停")

    def _resume_scan(self) -> None:
        """恢复扫描。"""
        if self._worker is not None:
            self._worker.resume()
        self._scan_state = ScanState.RUNNING
        self._set_scan_controls_text("暂停扫描")
        self._stats_label.setText("扫描中...")

    def _on_stop(self) -> None:
        """停止扫描。"""
        if self._worker is not None:
            self._worker.cancel()

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
        self._stop_btn.setVisible(False)
        self._set_scan_controls_text("开始扫描")
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

    def _on_scan_failed(self, error: str) -> None:
        """扫描失败回调。"""
        self._reset_scan_ui()
        self._stats_label.setText("扫描失败")
        QMessageBox.critical(self, "扫描失败", error)

    def _on_export(self, fmt: str) -> None:
        """导出扫描结果。"""
        if self._last_report is None:
            QMessageBox.information(self, "提示", "无可导出的扫描结果")
            return

        filter_str = "CSV 文件 (*.csv)" if fmt == "csv" else "JSON 文件 (*.json)"
        default_name = f"uniscan_report.{fmt}"
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
        from uniscan import __version__

        QMessageBox.about(
            self,
            "关于 uniscan",
            f"uniscan {__version__}\n\n通用文件扫描器\n支持多格式与压缩文件扫描\n\n技术栈: Python + PySide2",
        )

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
        from uniscan.gui.rule_editor import RuleEditorDialog

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

    def _on_result_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """双击结果项弹出详情对话框。

        ScanResult 存储在文件项的 UserRole 中。不分组模式下文件为顶层项，
        分组模式下文件为子项——因此优先取当前项，取不到再取父项。
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
            ready = self._drive_combo.count() > 0 and self._drive_combo.currentData() is not None
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
