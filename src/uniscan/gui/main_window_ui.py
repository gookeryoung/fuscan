# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main_window.ui'
##
## Created by: Qt User Interface Compiler version 5.15.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide2.QtCore import *
from PySide2.QtGui import *
from PySide2.QtWidgets import *


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        self.load_rules_action = QAction(MainWindow)
        self.load_rules_action.setObjectName(u"load_rules_action")
        self.edit_rules_action = QAction(MainWindow)
        self.edit_rules_action.setObjectName(u"edit_rules_action")
        self.export_csv_action = QAction(MainWindow)
        self.export_csv_action.setObjectName(u"export_csv_action")
        self.export_json_action = QAction(MainWindow)
        self.export_json_action.setObjectName(u"export_json_action")
        self.quit_action = QAction(MainWindow)
        self.quit_action.setObjectName(u"quit_action")
        self.select_path_action = QAction(MainWindow)
        self.select_path_action.setObjectName(u"select_path_action")
        self.scan_action = QAction(MainWindow)
        self.scan_action.setObjectName(u"scan_action")
        self.stop_action = QAction(MainWindow)
        self.stop_action.setObjectName(u"stop_action")
        self.view_results_action = QAction(MainWindow)
        self.view_results_action.setObjectName(u"view_results_action")
        self.view_rules_action = QAction(MainWindow)
        self.view_rules_action.setObjectName(u"view_rules_action")
        self.view_history_action = QAction(MainWindow)
        self.view_history_action.setObjectName(u"view_history_action")
        self.about_action = QAction(MainWindow)
        self.about_action.setObjectName(u"about_action")
        self.central = QWidget(MainWindow)
        self.central.setObjectName(u"central")
        self.central_layout = QVBoxLayout(self.central)
        self.central_layout.setSpacing(6)
        self.central_layout.setObjectName(u"central_layout")
        self.central_layout.setContentsMargins(8, 8, 8, 8)
        self.control_area = QFrame(self.central)
        self.control_area.setObjectName(u"control_area")
        self.control_layout = QVBoxLayout(self.control_area)
        self.control_layout.setSpacing(6)
        self.control_layout.setObjectName(u"control_layout")
        self.control_layout.setContentsMargins(8, 8, 8, 8)
        self.scan_mode_row = QHBoxLayout()
        self.scan_mode_row.setSpacing(8)
        self.scan_mode_row.setObjectName(u"scan_mode_row")
        self.full_btn = QPushButton(self.control_area)
        self.full_btn.setObjectName(u"full_btn")
        self.full_btn.setCheckable(True)
        self.full_btn.setCursor(QCursor(Qt.PointingHandCursor))

        self.scan_mode_row.addWidget(self.full_btn)

        self.drive_btn = QPushButton(self.control_area)
        self.drive_btn.setObjectName(u"drive_btn")
        self.drive_btn.setCheckable(True)
        self.drive_btn.setCursor(QCursor(Qt.PointingHandCursor))

        self.scan_mode_row.addWidget(self.drive_btn)

        self.folder_btn = QPushButton(self.control_area)
        self.folder_btn.setObjectName(u"folder_btn")
        self.folder_btn.setCheckable(True)
        self.folder_btn.setChecked(True)
        self.folder_btn.setCursor(QCursor(Qt.PointingHandCursor))

        self.scan_mode_row.addWidget(self.folder_btn)

        self.mode_row_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.scan_mode_row.addItem(self.mode_row_spacer)

        self.drive_label = QLabel(self.control_area)
        self.drive_label.setObjectName(u"drive_label")

        self.scan_mode_row.addWidget(self.drive_label)

        self.drive_combo = QComboBox(self.control_area)
        self.drive_combo.setObjectName(u"drive_combo")
        self.drive_combo.setMinimumWidth(80)

        self.scan_mode_row.addWidget(self.drive_combo)


        self.control_layout.addLayout(self.scan_mode_row)

        self.target_row = QWidget(self.control_area)
        self.target_row.setObjectName(u"target_row")
        self.target_layout = QHBoxLayout(self.target_row)
        self.target_layout.setObjectName(u"target_layout")
        self.target_layout.setContentsMargins(0, 0, 0, 0)
        self.path_label = QLabel(self.target_row)
        self.path_label.setObjectName(u"path_label")

        self.target_layout.addWidget(self.path_label)

        self.path_combo = QComboBox(self.target_row)
        self.path_combo.setObjectName(u"path_combo")
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.path_combo.sizePolicy().hasHeightForWidth())
        self.path_combo.setSizePolicy(sizePolicy)

        self.target_layout.addWidget(self.path_combo)

        self.select_path_btn = QPushButton(self.target_row)
        self.select_path_btn.setObjectName(u"select_path_btn")

        self.target_layout.addWidget(self.select_path_btn)


        self.control_layout.addWidget(self.target_row)

        self.rules_row = QHBoxLayout()
        self.rules_row.setObjectName(u"rules_row")
        self.load_rules_btn = QPushButton(self.control_area)
        self.load_rules_btn.setObjectName(u"load_rules_btn")

        self.rules_row.addWidget(self.load_rules_btn)

        self.rules_label = QLabel(self.control_area)
        self.rules_label.setObjectName(u"rules_label")

        self.rules_row.addWidget(self.rules_label)

        self.use_builtin_checkbox = QCheckBox(self.control_area)
        self.use_builtin_checkbox.setObjectName(u"use_builtin_checkbox")
        self.use_builtin_checkbox.setChecked(True)

        self.rules_row.addWidget(self.use_builtin_checkbox)


        self.control_layout.addLayout(self.rules_row)

        self.scan_btn_row = QHBoxLayout()
        self.scan_btn_row.setObjectName(u"scan_btn_row")
        self.scan_btn = QPushButton(self.control_area)
        self.scan_btn.setObjectName(u"scan_btn")
        self.scan_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.scan_btn.setMinimumHeight(40)
        self.scan_btn.setEnabled(False)

        self.scan_btn_row.addWidget(self.scan_btn)

        self.stop_btn = QPushButton(self.control_area)
        self.stop_btn.setObjectName(u"stop_btn")
        self.stop_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setVisible(False)

        self.scan_btn_row.addWidget(self.stop_btn)


        self.control_layout.addLayout(self.scan_btn_row)

        self.progress = QProgressBar(self.control_area)
        self.progress.setObjectName(u"progress")
        self.progress.setVisible(False)
        self.progress.setMinimumHeight(20)
        self.progress.setTextVisible(True)

        self.control_layout.addWidget(self.progress)

        self.current_file_label = QLabel(self.control_area)
        self.current_file_label.setObjectName(u"current_file_label")
        self.current_file_label.setVisible(False)

        self.control_layout.addWidget(self.current_file_label)

        self.stats_label = QLabel(self.control_area)
        self.stats_label.setObjectName(u"stats_label")

        self.control_layout.addWidget(self.stats_label)


        self.central_layout.addWidget(self.control_area)

        self.splitter = QSplitter(self.central)
        self.splitter.setObjectName(u"splitter")
        self.splitter.setOrientation(Qt.Horizontal)
        self.list_area = QWidget(self.splitter)
        self.list_area.setObjectName(u"list_area")
        sizePolicy1 = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.list_area.sizePolicy().hasHeightForWidth())
        self.list_area.setSizePolicy(sizePolicy1)
        self.list_layout = QVBoxLayout(self.list_area)
        self.list_layout.setSpacing(4)
        self.list_layout.setObjectName(u"list_layout")
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.tab_widget = QTabWidget(self.list_area)
        self.tab_widget.setObjectName(u"tab_widget")
        self.results_tab = QWidget()
        self.results_tab.setObjectName(u"results_tab")
        self.results_layout = QVBoxLayout(self.results_tab)
        self.results_layout.setSpacing(4)
        self.results_layout.setObjectName(u"results_layout")
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_bar = QFrame(self.results_tab)
        self.filter_bar.setObjectName(u"filter_bar")
        self.filter_layout = QHBoxLayout(self.filter_bar)
        self.filter_layout.setSpacing(6)
        self.filter_layout.setObjectName(u"filter_layout")
        self.filter_layout.setContentsMargins(4, 2, 4, 2)
        self.filter_label = QLabel(self.filter_bar)
        self.filter_label.setObjectName(u"filter_label")

        self.filter_layout.addWidget(self.filter_label)

        self.path_filter_input = QLineEdit(self.filter_bar)
        self.path_filter_input.setObjectName(u"path_filter_input")
        self.path_filter_input.setClearButtonEnabled(True)

        self.filter_layout.addWidget(self.path_filter_input)

        self.rule_filter_label = QLabel(self.filter_bar)
        self.rule_filter_label.setObjectName(u"rule_filter_label")

        self.filter_layout.addWidget(self.rule_filter_label)

        self.rule_filter_combo = QComboBox(self.filter_bar)
        self.rule_filter_combo.setObjectName(u"rule_filter_combo")

        self.filter_layout.addWidget(self.rule_filter_combo)

        self.group_mode_label = QLabel(self.filter_bar)
        self.group_mode_label.setObjectName(u"group_mode_label")

        self.filter_layout.addWidget(self.group_mode_label)

        self.group_mode_combo = QComboBox(self.filter_bar)
        self.group_mode_combo.setObjectName(u"group_mode_combo")

        self.filter_layout.addWidget(self.group_mode_combo)


        self.results_layout.addWidget(self.filter_bar)

        self.result_tree = QTreeWidget(self.results_tab)
        self.result_tree.setObjectName(u"result_tree")
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.setRootIsDecorated(True)
        self.result_tree.setSortingEnabled(True)

        self.results_layout.addWidget(self.result_tree)

        self.tab_widget.addTab(self.results_tab, "")
        self.rules_tab = QWidget()
        self.rules_tab.setObjectName(u"rules_tab")
        self.rules_tab_layout = QVBoxLayout(self.rules_tab)
        self.rules_tab_layout.setSpacing(4)
        self.rules_tab_layout.setObjectName(u"rules_tab_layout")
        self.rules_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.rules_file_label = QLabel(self.rules_tab)
        self.rules_file_label.setObjectName(u"rules_file_label")

        self.rules_tab_layout.addWidget(self.rules_file_label)

        self.rules_file_list = QListWidget(self.rules_tab)
        self.rules_file_list.setObjectName(u"rules_file_list")

        self.rules_tab_layout.addWidget(self.rules_file_list)

        self.rules_btn_row = QHBoxLayout()
        self.rules_btn_row.setObjectName(u"rules_btn_row")
        self.move_up_btn = QPushButton(self.rules_tab)
        self.move_up_btn.setObjectName(u"move_up_btn")

        self.rules_btn_row.addWidget(self.move_up_btn)

        self.move_down_btn = QPushButton(self.rules_tab)
        self.move_down_btn.setObjectName(u"move_down_btn")

        self.rules_btn_row.addWidget(self.move_down_btn)

        self.remove_rule_btn = QPushButton(self.rules_tab)
        self.remove_rule_btn.setObjectName(u"remove_rule_btn")

        self.rules_btn_row.addWidget(self.remove_rule_btn)

        self.edit_rule_btn = QPushButton(self.rules_tab)
        self.edit_rule_btn.setObjectName(u"edit_rule_btn")

        self.rules_btn_row.addWidget(self.edit_rule_btn)


        self.rules_tab_layout.addLayout(self.rules_btn_row)

        self.rules_tree = QTreeWidget(self.rules_tab)
        self.rules_tree.setObjectName(u"rules_tree")
        self.rules_tree.setRootIsDecorated(False)

        self.rules_tab_layout.addWidget(self.rules_tree)

        self.tab_widget.addTab(self.rules_tab, "")
        self.history_tab = QWidget()
        self.history_tab.setObjectName(u"history_tab")
        self.history_layout = QVBoxLayout(self.history_tab)
        self.history_layout.setSpacing(4)
        self.history_layout.setObjectName(u"history_layout")
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_label = QLabel(self.history_tab)
        self.history_label.setObjectName(u"history_label")

        self.history_layout.addWidget(self.history_label)

        self.history_list = QListWidget(self.history_tab)
        self.history_list.setObjectName(u"history_list")

        self.history_layout.addWidget(self.history_list)

        self.tab_widget.addTab(self.history_tab, "")

        self.list_layout.addWidget(self.tab_widget)

        self.list_action_bar = QFrame(self.list_area)
        self.list_action_bar.setObjectName(u"list_action_bar")
        self.list_action_layout = QVBoxLayout(self.list_action_bar)
        self.list_action_layout.setSpacing(4)
        self.list_action_layout.setObjectName(u"list_action_layout")
        self.list_action_layout.setContentsMargins(8, 4, 8, 4)
        self.note_edit = QPlainTextEdit(self.list_action_bar)
        self.note_edit.setObjectName(u"note_edit")
        self.note_edit.setMaximumHeight(80)

        self.list_action_layout.addWidget(self.note_edit)

        self.action_btn_row = QHBoxLayout()
        self.action_btn_row.setObjectName(u"action_btn_row")
        self.batch_btn = QPushButton(self.list_action_bar)
        self.batch_btn.setObjectName(u"batch_btn")
        self.batch_btn.setEnabled(False)

        self.action_btn_row.addWidget(self.batch_btn)

        self.action_row_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.action_btn_row.addItem(self.action_row_spacer)

        self.export_btn = QPushButton(self.list_action_bar)
        self.export_btn.setObjectName(u"export_btn")
        self.export_btn.setEnabled(False)

        self.action_btn_row.addWidget(self.export_btn)


        self.list_action_layout.addLayout(self.action_btn_row)


        self.list_layout.addWidget(self.list_action_bar)

        self.splitter.addWidget(self.list_area)
        self.detail_area = QWidget(self.splitter)
        self.detail_area.setObjectName(u"detail_area")
        sizePolicy1.setHeightForWidth(self.detail_area.sizePolicy().hasHeightForWidth())
        self.detail_area.setSizePolicy(sizePolicy1)
        self.detail_layout = QVBoxLayout(self.detail_area)
        self.detail_layout.setSpacing(4)
        self.detail_layout.setObjectName(u"detail_layout")
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_action_stack = QStackedWidget(self.detail_area)
        self.detail_action_stack.setObjectName(u"detail_action_stack")
        self.detail_empty_action = QFrame()
        self.detail_empty_action.setObjectName(u"detail_empty_action")
        self.detail_empty_action_layout = QHBoxLayout(self.detail_empty_action)
        self.detail_empty_action_layout.setSpacing(6)
        self.detail_empty_action_layout.setObjectName(u"detail_empty_action_layout")
        self.detail_empty_action_layout.setContentsMargins(8, 4, 8, 4)
        self.detail_action_title_label = QLabel(self.detail_empty_action)
        self.detail_action_title_label.setObjectName(u"detail_action_title_label")

        self.detail_empty_action_layout.addWidget(self.detail_action_title_label)

        self.detail_action_hint = QLabel(self.detail_empty_action)
        self.detail_action_hint.setObjectName(u"detail_action_hint")

        self.detail_empty_action_layout.addWidget(self.detail_action_hint)

        self.detail_empty_action_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.detail_empty_action_layout.addItem(self.detail_empty_action_spacer)

        self.detail_start_btn = QPushButton(self.detail_empty_action)
        self.detail_start_btn.setObjectName(u"detail_start_btn")

        self.detail_empty_action_layout.addWidget(self.detail_start_btn)

        self.detail_load_rules_btn = QPushButton(self.detail_empty_action)
        self.detail_load_rules_btn.setObjectName(u"detail_load_rules_btn")

        self.detail_empty_action_layout.addWidget(self.detail_load_rules_btn)

        self.detail_view_history_btn = QPushButton(self.detail_empty_action)
        self.detail_view_history_btn.setObjectName(u"detail_view_history_btn")

        self.detail_empty_action_layout.addWidget(self.detail_view_history_btn)

        self.detail_action_stack.addWidget(self.detail_empty_action)
        self.detail_nonempty_action = QFrame()
        self.detail_nonempty_action.setObjectName(u"detail_nonempty_action")
        self.detail_nonempty_action_layout = QHBoxLayout(self.detail_nonempty_action)
        self.detail_nonempty_action_layout.setSpacing(6)
        self.detail_nonempty_action_layout.setObjectName(u"detail_nonempty_action_layout")
        self.detail_nonempty_action_layout.setContentsMargins(8, 4, 8, 4)
        self.detail_locate_btn = QPushButton(self.detail_nonempty_action)
        self.detail_locate_btn.setObjectName(u"detail_locate_btn")

        self.detail_nonempty_action_layout.addWidget(self.detail_locate_btn)

        self.detail_prev_btn = QPushButton(self.detail_nonempty_action)
        self.detail_prev_btn.setObjectName(u"detail_prev_btn")

        self.detail_nonempty_action_layout.addWidget(self.detail_prev_btn)

        self.detail_next_btn = QPushButton(self.detail_nonempty_action)
        self.detail_next_btn.setObjectName(u"detail_next_btn")

        self.detail_nonempty_action_layout.addWidget(self.detail_next_btn)

        self.detail_nav_label = QLabel(self.detail_nonempty_action)
        self.detail_nav_label.setObjectName(u"detail_nav_label")

        self.detail_nonempty_action_layout.addWidget(self.detail_nav_label)

        self.detail_nonempty_action_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.detail_nonempty_action_layout.addItem(self.detail_nonempty_action_spacer)

        self.detail_open_location_btn = QPushButton(self.detail_nonempty_action)
        self.detail_open_location_btn.setObjectName(u"detail_open_location_btn")

        self.detail_nonempty_action_layout.addWidget(self.detail_open_location_btn)

        self.detail_copy_path_btn = QPushButton(self.detail_nonempty_action)
        self.detail_copy_path_btn.setObjectName(u"detail_copy_path_btn")

        self.detail_nonempty_action_layout.addWidget(self.detail_copy_path_btn)

        self.detail_open_window_btn = QPushButton(self.detail_nonempty_action)
        self.detail_open_window_btn.setObjectName(u"detail_open_window_btn")

        self.detail_nonempty_action_layout.addWidget(self.detail_open_window_btn)

        self.detail_action_stack.addWidget(self.detail_nonempty_action)

        self.detail_layout.addWidget(self.detail_action_stack)

        self.detail_main_stack = QStackedWidget(self.detail_area)
        self.detail_main_stack.setObjectName(u"detail_main_stack")
        self.detail_empty_main = QFrame()
        self.detail_empty_main.setObjectName(u"detail_empty_main")
        self.detail_empty_main_layout = QVBoxLayout(self.detail_empty_main)
        self.detail_empty_main_layout.setObjectName(u"detail_empty_main_layout")
        self.detail_empty_main_layout.setAlignment(Qt.AlignCenter)
        self.detail_empty_hint = QLabel(self.detail_empty_main)
        self.detail_empty_hint.setObjectName(u"detail_empty_hint")
        self.detail_empty_hint.setAlignment(Qt.AlignCenter)

        self.detail_empty_main_layout.addWidget(self.detail_empty_hint)

        self.detail_main_stack.addWidget(self.detail_empty_main)
        self.detail_nonempty_main = QFrame()
        self.detail_nonempty_main.setObjectName(u"detail_nonempty_main")
        self.detail_nonempty_main_layout = QVBoxLayout(self.detail_nonempty_main)
        self.detail_nonempty_main_layout.setSpacing(6)
        self.detail_nonempty_main_layout.setObjectName(u"detail_nonempty_main_layout")
        self.detail_nonempty_main_layout.setContentsMargins(8, 8, 8, 8)
        self.detail_info_label = QLabel(self.detail_nonempty_main)
        self.detail_info_label.setObjectName(u"detail_info_label")
        self.detail_info_label.setTextFormat(Qt.RichText)
        self.detail_info_label.setWordWrap(True)

        self.detail_nonempty_main_layout.addWidget(self.detail_info_label)

        self.detail_hits_title_label = QLabel(self.detail_nonempty_main)
        self.detail_hits_title_label.setObjectName(u"detail_hits_title_label")

        self.detail_nonempty_main_layout.addWidget(self.detail_hits_title_label)

        self.detail_hits_table = QTableWidget(self.detail_nonempty_main)
        if (self.detail_hits_table.columnCount() < 3):
            self.detail_hits_table.setColumnCount(3)
        __qtablewidgetitem = QTableWidgetItem()
        self.detail_hits_table.setHorizontalHeaderItem(0, __qtablewidgetitem)
        __qtablewidgetitem1 = QTableWidgetItem()
        self.detail_hits_table.setHorizontalHeaderItem(1, __qtablewidgetitem1)
        __qtablewidgetitem2 = QTableWidgetItem()
        self.detail_hits_table.setHorizontalHeaderItem(2, __qtablewidgetitem2)
        self.detail_hits_table.setObjectName(u"detail_hits_table")

        self.detail_nonempty_main_layout.addWidget(self.detail_hits_table)

        self.detail_preview_title_label = QLabel(self.detail_nonempty_main)
        self.detail_preview_title_label.setObjectName(u"detail_preview_title_label")

        self.detail_nonempty_main_layout.addWidget(self.detail_preview_title_label)

        self.detail_preview = QTextEdit(self.detail_nonempty_main)
        self.detail_preview.setObjectName(u"detail_preview")
        self.detail_preview.setReadOnly(True)

        self.detail_nonempty_main_layout.addWidget(self.detail_preview)

        self.detail_main_stack.addWidget(self.detail_nonempty_main)

        self.detail_layout.addWidget(self.detail_main_stack)

        self.splitter.addWidget(self.detail_area)

        self.central_layout.addWidget(self.splitter)

        MainWindow.setCentralWidget(self.central)
        self.menubar = QMenuBar(MainWindow)
        self.menubar.setObjectName(u"menubar")
        self.file_menu = QMenu(self.menubar)
        self.file_menu.setObjectName(u"file_menu")
        self.scan_menu = QMenu(self.menubar)
        self.scan_menu.setObjectName(u"scan_menu")
        self.view_menu = QMenu(self.menubar)
        self.view_menu.setObjectName(u"view_menu")
        self.help_menu = QMenu(self.menubar)
        self.help_menu.setObjectName(u"help_menu")
        MainWindow.setMenuBar(self.menubar)
        self.toolbar = QToolBar(MainWindow)
        self.toolbar.setObjectName(u"toolbar")
        MainWindow.addToolBar(Qt.TopToolBarArea, self.toolbar)

        self.menubar.addAction(self.file_menu.menuAction())
        self.menubar.addAction(self.scan_menu.menuAction())
        self.menubar.addAction(self.view_menu.menuAction())
        self.menubar.addAction(self.help_menu.menuAction())
        self.file_menu.addAction(self.load_rules_action)
        self.file_menu.addAction(self.edit_rules_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.export_csv_action)
        self.file_menu.addAction(self.export_json_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.quit_action)
        self.scan_menu.addAction(self.select_path_action)
        self.scan_menu.addAction(self.scan_action)
        self.scan_menu.addAction(self.stop_action)
        self.view_menu.addAction(self.view_results_action)
        self.view_menu.addAction(self.view_rules_action)
        self.view_menu.addAction(self.view_history_action)
        self.help_menu.addAction(self.about_action)
        self.toolbar.addAction(self.scan_action)
        self.toolbar.addAction(self.load_rules_action)
        self.toolbar.addAction(self.export_csv_action)

        self.retranslateUi(MainWindow)

        self.tab_widget.setCurrentIndex(0)
        self.detail_action_stack.setCurrentIndex(0)
        self.detail_main_stack.setCurrentIndex(0)


        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"uniscan \u901a\u7528\u6587\u4ef6\u626b\u63cf\u5668", None))
        self.load_rules_action.setText(QCoreApplication.translate("MainWindow", u"\u52a0\u8f7d\u89c4\u5219...", None))
#if QT_CONFIG(shortcut)
        self.load_rules_action.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+O", None))
#endif // QT_CONFIG(shortcut)
        self.edit_rules_action.setText(QCoreApplication.translate("MainWindow", u"\u7f16\u8f91\u89c4\u5219...", None))
#if QT_CONFIG(shortcut)
        self.edit_rules_action.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+E", None))
#endif // QT_CONFIG(shortcut)
        self.export_csv_action.setText(QCoreApplication.translate("MainWindow", u"\u5bfc\u51fa CSV...", None))
#if QT_CONFIG(shortcut)
        self.export_csv_action.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+S", None))
#endif // QT_CONFIG(shortcut)
        self.export_json_action.setText(QCoreApplication.translate("MainWindow", u"\u5bfc\u51fa JSON...", None))
#if QT_CONFIG(shortcut)
        self.export_json_action.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+Shift+S", None))
#endif // QT_CONFIG(shortcut)
        self.quit_action.setText(QCoreApplication.translate("MainWindow", u"\u9000\u51fa(&Q)", None))
#if QT_CONFIG(shortcut)
        self.quit_action.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+Q", None))
#endif // QT_CONFIG(shortcut)
        self.select_path_action.setText(QCoreApplication.translate("MainWindow", u"\u9009\u62e9\u626b\u63cf\u8def\u5f84...", None))
        self.scan_action.setText(QCoreApplication.translate("MainWindow", u"\u5f00\u59cb\u626b\u63cf", None))
#if QT_CONFIG(shortcut)
        self.scan_action.setShortcut(QCoreApplication.translate("MainWindow", u"F5", None))
#endif // QT_CONFIG(shortcut)
        self.stop_action.setText(QCoreApplication.translate("MainWindow", u"\u505c\u6b62\u626b\u63cf", None))
#if QT_CONFIG(shortcut)
        self.stop_action.setShortcut(QCoreApplication.translate("MainWindow", u"Esc", None))
#endif // QT_CONFIG(shortcut)
        self.view_results_action.setText(QCoreApplication.translate("MainWindow", u"\u5207\u6362\u5230\u626b\u63cf\u7ed3\u679c", None))
#if QT_CONFIG(shortcut)
        self.view_results_action.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+1", None))
#endif // QT_CONFIG(shortcut)
        self.view_rules_action.setText(QCoreApplication.translate("MainWindow", u"\u5207\u6362\u5230\u89c4\u5219\u6587\u4ef6", None))
#if QT_CONFIG(shortcut)
        self.view_rules_action.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+2", None))
#endif // QT_CONFIG(shortcut)
        self.view_history_action.setText(QCoreApplication.translate("MainWindow", u"\u5207\u6362\u5230\u626b\u63cf\u5386\u53f2", None))
#if QT_CONFIG(shortcut)
        self.view_history_action.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+3", None))
#endif // QT_CONFIG(shortcut)
        self.about_action.setText(QCoreApplication.translate("MainWindow", u"\u5173\u4e8e", None))
        self.full_btn.setText(QCoreApplication.translate("MainWindow", u"\u5168\u76d8\u626b\u63cf\n"
"\u626b\u63cf\u6240\u6709\u76d8\u7b26", None))
        self.drive_btn.setText(QCoreApplication.translate("MainWindow", u"\u9009\u62e9\u76d8\u7b26\n"
"\u626b\u63cf\u6307\u5b9a\u76d8\u7b26", None))
        self.folder_btn.setText(QCoreApplication.translate("MainWindow", u"\u9009\u62e9\u6587\u4ef6\u5939\n"
"\u626b\u63cf\u6307\u5b9a\u76ee\u5f55", None))
        self.drive_label.setText(QCoreApplication.translate("MainWindow", u"\u76d8\u7b26:", None))
#if QT_CONFIG(tooltip)
        self.drive_combo.setToolTip(QCoreApplication.translate("MainWindow", u"\u9009\u62e9\u8981\u626b\u63cf\u7684\u76d8\u7b26", None))
#endif // QT_CONFIG(tooltip)
        self.path_label.setText(QCoreApplication.translate("MainWindow", u"\u626b\u63cf\u8def\u5f84:", None))
#if QT_CONFIG(tooltip)
        self.path_combo.setToolTip(QCoreApplication.translate("MainWindow", u"\u626b\u63cf\u8def\u5f84\uff08\u53ef\u4ece\u5386\u53f2\u8bb0\u5f55\u4e2d\u9009\u62e9\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.select_path_btn.setText(QCoreApplication.translate("MainWindow", u"\u9009\u62e9\u8def\u5f84...", None))
        self.load_rules_btn.setText(QCoreApplication.translate("MainWindow", u"\u52a0\u8f7d\u89c4\u5219...", None))
        self.rules_label.setText(QCoreApplication.translate("MainWindow", u"\u89c4\u5219: \u672a\u52a0\u8f7d", None))
        self.use_builtin_checkbox.setText(QCoreApplication.translate("MainWindow", u"\u4f7f\u7528\u901a\u7528\u89c4\u5219", None))
#if QT_CONFIG(tooltip)
        self.use_builtin_checkbox.setToolTip(QCoreApplication.translate("MainWindow", u"\u52fe\u9009\u540e\u52a0\u8f7d\u8f6f\u4ef6\u5185\u7f6e\u901a\u7528\u89c4\u5219\uff0c\u7528\u6237\u89c4\u5219\u4e2d\u540c\u540d\u89c4\u5219\u4f1a\u8986\u76d6\u901a\u7528\u89c4\u5219", None))
#endif // QT_CONFIG(tooltip)
        self.scan_btn.setText(QCoreApplication.translate("MainWindow", u"\u5f00\u59cb\u626b\u63cf", None))
        self.stop_btn.setText(QCoreApplication.translate("MainWindow", u"\u505c\u6b62", None))
        self.current_file_label.setText("")
        self.stats_label.setText(QCoreApplication.translate("MainWindow", u"\u5c31\u7eea", None))
        self.filter_label.setText(QCoreApplication.translate("MainWindow", u"\u7b5b\u9009:", None))
        self.path_filter_input.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u6309\u8def\u5f84\u7b5b\u9009...", None))
        self.rule_filter_label.setText(QCoreApplication.translate("MainWindow", u"\u89c4\u5219:", None))
        self.group_mode_label.setText(QCoreApplication.translate("MainWindow", u"\u5206\u7ec4:", None))
        ___qtreewidgetitem = self.result_tree.headerItem()
        ___qtreewidgetitem.setText(4, QCoreApplication.translate("MainWindow", u"\u8be6\u60c5", None));
        ___qtreewidgetitem.setText(3, QCoreApplication.translate("MainWindow", u"\u547d\u4e2d\u6570", None));
        ___qtreewidgetitem.setText(2, QCoreApplication.translate("MainWindow", u"\u4e25\u91cd\u7b49\u7ea7", None));
        ___qtreewidgetitem.setText(1, QCoreApplication.translate("MainWindow", u"\u89c4\u5219", None));
        ___qtreewidgetitem.setText(0, QCoreApplication.translate("MainWindow", u"\u8def\u5f84", None));
        self.tab_widget.setTabText(self.tab_widget.indexOf(self.results_tab), QCoreApplication.translate("MainWindow", u"\u626b\u63cf\u7ed3\u679c", None))
        self.rules_file_label.setText(QCoreApplication.translate("MainWindow", u"\u89c4\u5219\u6587\u4ef6\uff08\u987a\u5e8f\u4ece\u4e0a\u5230\u4e0b\uff0c\u540e\u8005\u8986\u76d6\u524d\u8005\uff09", None))
#if QT_CONFIG(tooltip)
        self.rules_file_list.setToolTip(QCoreApplication.translate("MainWindow", u"\u5df2\u52a0\u8f7d\u7684\u89c4\u5219\u6587\u4ef6\uff0c\u5217\u8868\u987a\u5e8f\u4ee3\u8868\u4f18\u5148\u7ea7\uff08\u4ece\u4f4e\u5230\u9ad8\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.move_up_btn.setText(QCoreApplication.translate("MainWindow", u"\u4e0a\u79fb", None))
#if QT_CONFIG(tooltip)
        self.move_up_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5c06\u9009\u4e2d\u7684\u89c4\u5219\u6587\u4ef6\u4e0a\u79fb\uff08\u4f18\u5148\u7ea7\u964d\u4f4e\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.move_down_btn.setText(QCoreApplication.translate("MainWindow", u"\u4e0b\u79fb", None))
#if QT_CONFIG(tooltip)
        self.move_down_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5c06\u9009\u4e2d\u7684\u89c4\u5219\u6587\u4ef6\u4e0b\u79fb\uff08\u4f18\u5148\u7ea7\u5347\u9ad8\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.remove_rule_btn.setText(QCoreApplication.translate("MainWindow", u"\u79fb\u9664", None))
#if QT_CONFIG(tooltip)
        self.remove_rule_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u79fb\u9664\u9009\u4e2d\u7684\u89c4\u5219\u6587\u4ef6", None))
#endif // QT_CONFIG(tooltip)
        self.edit_rule_btn.setText(QCoreApplication.translate("MainWindow", u"\u7f16\u8f91", None))
#if QT_CONFIG(tooltip)
        self.edit_rule_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u7f16\u8f91\u9009\u4e2d\u7684\u89c4\u5219\u6587\u4ef6", None))
#endif // QT_CONFIG(tooltip)
        ___qtreewidgetitem1 = self.rules_tree.headerItem()
        ___qtreewidgetitem1.setText(2, QCoreApplication.translate("MainWindow", u"\u6269\u5c55\u540d", None));
        ___qtreewidgetitem1.setText(1, QCoreApplication.translate("MainWindow", u"\u4e25\u91cd\u7b49\u7ea7", None));
        ___qtreewidgetitem1.setText(0, QCoreApplication.translate("MainWindow", u"\u89c4\u5219\u540d", None));
        self.tab_widget.setTabText(self.tab_widget.indexOf(self.rules_tab), QCoreApplication.translate("MainWindow", u"\u89c4\u5219\u6587\u4ef6", None))
        self.history_label.setText(QCoreApplication.translate("MainWindow", u"\u626b\u63cf\u5386\u53f2\uff08\u6700\u8fd1\u4f18\u5148\uff09", None))
#if QT_CONFIG(tooltip)
        self.history_list.setToolTip(QCoreApplication.translate("MainWindow", u"\u6700\u8fd1\u626b\u63cf\u8fc7\u7684\u8def\u5f84\uff0c\u53cc\u51fb\u53ef\u5feb\u901f\u9009\u62e9", None))
#endif // QT_CONFIG(tooltip)
        self.tab_widget.setTabText(self.tab_widget.indexOf(self.history_tab), QCoreApplication.translate("MainWindow", u"\u626b\u63cf\u5386\u53f2", None))
        self.note_edit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u5907\u6ce8/\u6279\u6ce8/\u5bfc\u51fa\u8bf4\u660e...", None))
        self.batch_btn.setText(QCoreApplication.translate("MainWindow", u"\u6279\u91cf\u5904\u7406", None))
#if QT_CONFIG(tooltip)
        self.batch_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5bf9\u9009\u4e2d\u9879\u6279\u91cf\u5904\u7406\uff08\u9884\u7559\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.export_btn.setText(QCoreApplication.translate("MainWindow", u"\u5bfc\u51fa\u7ed3\u679c", None))
#if QT_CONFIG(tooltip)
        self.export_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5bfc\u51fa\u626b\u63cf\u7ed3\u679c\u5230\u6587\u4ef6", None))
#endif // QT_CONFIG(tooltip)
        self.detail_action_title_label.setText(QCoreApplication.translate("MainWindow", u"\u8be6\u60c5\u64cd\u4f5c:", None))
        self.detail_action_hint.setText(QCoreApplication.translate("MainWindow", u"\u672a\u9009\u4e2d\u4efb\u4f55\u9879", None))
        self.detail_start_btn.setText(QCoreApplication.translate("MainWindow", u"\u5f00\u59cb\u626b\u63cf", None))
#if QT_CONFIG(tooltip)
        self.detail_start_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5f00\u59cb\u626b\u63cf\uff08\u9700\u5148\u9009\u62e9\u76ee\u6807\u4e0e\u89c4\u5219\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.detail_load_rules_btn.setText(QCoreApplication.translate("MainWindow", u"\u52a0\u8f7d\u89c4\u5219", None))
#if QT_CONFIG(tooltip)
        self.detail_load_rules_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u52a0\u8f7d\u89c4\u5219\u6587\u4ef6", None))
#endif // QT_CONFIG(tooltip)
        self.detail_view_history_btn.setText(QCoreApplication.translate("MainWindow", u"\u67e5\u770b\u5386\u53f2", None))
#if QT_CONFIG(tooltip)
        self.detail_view_history_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5207\u6362\u5230\u626b\u63cf\u5386\u53f2\u89c6\u56fe", None))
#endif // QT_CONFIG(tooltip)
        self.detail_locate_btn.setText(QCoreApplication.translate("MainWindow", u"\u5b9a\u4f4d\u547d\u4e2d", None))
#if QT_CONFIG(tooltip)
        self.detail_locate_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u6eda\u52a8\u5230\u5f53\u524d\u547d\u4e2d\u4f4d\u7f6e", None))
#endif // QT_CONFIG(tooltip)
        self.detail_prev_btn.setText(QCoreApplication.translate("MainWindow", u"\u4e0a\u4e00\u6761", None))
#if QT_CONFIG(tooltip)
        self.detail_prev_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u8df3\u8f6c\u5230\u4e0a\u4e00\u4e2a\u547d\u4e2d\u4f4d\u7f6e", None))
#endif // QT_CONFIG(tooltip)
        self.detail_next_btn.setText(QCoreApplication.translate("MainWindow", u"\u4e0b\u4e00\u6761", None))
#if QT_CONFIG(tooltip)
        self.detail_next_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u8df3\u8f6c\u5230\u4e0b\u4e00\u4e2a\u547d\u4e2d\u4f4d\u7f6e", None))
#endif // QT_CONFIG(tooltip)
        self.detail_nav_label.setText(QCoreApplication.translate("MainWindow", u"0 / 0", None))
        self.detail_open_location_btn.setText(QCoreApplication.translate("MainWindow", u"\u6253\u5f00\u6587\u4ef6\u4f4d\u7f6e", None))
#if QT_CONFIG(tooltip)
        self.detail_open_location_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5728\u6587\u4ef6\u7ba1\u7406\u5668\u4e2d\u6253\u5f00\u6240\u5728\u76ee\u5f55", None))
#endif // QT_CONFIG(tooltip)
        self.detail_copy_path_btn.setText(QCoreApplication.translate("MainWindow", u"\u590d\u5236\u8def\u5f84", None))
#if QT_CONFIG(tooltip)
        self.detail_copy_path_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u590d\u5236\u6587\u4ef6\u8def\u5f84\u5230\u526a\u8d34\u677f", None))
#endif // QT_CONFIG(tooltip)
        self.detail_open_window_btn.setText(QCoreApplication.translate("MainWindow", u"\u5728\u65b0\u7a97\u53e3\u6253\u5f00", None))
#if QT_CONFIG(tooltip)
        self.detail_open_window_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5f39\u51fa\u72ec\u7acb\u5bf9\u8bdd\u6846\u67e5\u770b\u5b8c\u6574\u8be6\u60c5", None))
#endif // QT_CONFIG(tooltip)
        self.detail_empty_hint.setText(QCoreApplication.translate("MainWindow", u"\u672a\u9009\u4e2d\u4efb\u4f55\u9879\n"
"\u8bf7\u5148\u5f00\u59cb\u626b\u63cf\u6216\u5728\u5de6\u4fa7\u5217\u8868\u9009\u62e9\u4e00\u9879", None))
        self.detail_hits_title_label.setText(QCoreApplication.translate("MainWindow", u"\u547d\u4e2d\u89c4\u5219:", None))
        ___qtablewidgetitem = self.detail_hits_table.horizontalHeaderItem(0)
        ___qtablewidgetitem.setText(QCoreApplication.translate("MainWindow", u"\u89c4\u5219\u540d", None));
        ___qtablewidgetitem1 = self.detail_hits_table.horizontalHeaderItem(1)
        ___qtablewidgetitem1.setText(QCoreApplication.translate("MainWindow", u"\u4e25\u91cd\u7b49\u7ea7", None));
        ___qtablewidgetitem2 = self.detail_hits_table.horizontalHeaderItem(2)
        ___qtablewidgetitem2.setText(QCoreApplication.translate("MainWindow", u"\u8be6\u60c5", None));
        self.detail_preview_title_label.setText(QCoreApplication.translate("MainWindow", u"\u5185\u5bb9\u9884\u89c8 (\u5173\u952e\u8bcd\u9ad8\u4eae):", None))
        self.file_menu.setTitle(QCoreApplication.translate("MainWindow", u"\u6587\u4ef6(&F)", None))
        self.scan_menu.setTitle(QCoreApplication.translate("MainWindow", u"\u626b\u63cf(&S)", None))
        self.view_menu.setTitle(QCoreApplication.translate("MainWindow", u"\u89c6\u56fe(&V)", None))
        self.help_menu.setTitle(QCoreApplication.translate("MainWindow", u"\u5e2e\u52a9(&H)", None))
        self.toolbar.setWindowTitle(QCoreApplication.translate("MainWindow", u"\u4e3b\u5de5\u5177\u680f", None))
    # retranslateUi

