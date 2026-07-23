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

from fuscan.gui.result_tree import ResultTreeView

from  . import resources_rc

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(1059, 690)
        MainWindow.setMinimumSize(QSize(800, 600))
        self.load_rules_action = QAction(MainWindow)
        self.load_rules_action.setObjectName(u"load_rules_action")
        icon = QIcon()
        icon.addFile(u":/icons/load_list.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.load_rules_action.setIcon(icon)
        self.edit_rules_action = QAction(MainWindow)
        self.edit_rules_action.setObjectName(u"edit_rules_action")
        icon1 = QIcon()
        icon1.addFile(u":/icons/edit.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.edit_rules_action.setIcon(icon1)
        self.export_csv_action = QAction(MainWindow)
        self.export_csv_action.setObjectName(u"export_csv_action")
        icon2 = QIcon()
        icon2.addFile(u":/icons/export_csv.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.export_csv_action.setIcon(icon2)
        self.export_json_action = QAction(MainWindow)
        self.export_json_action.setObjectName(u"export_json_action")
        icon3 = QIcon()
        icon3.addFile(u":/icons/export_json.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.export_json_action.setIcon(icon3)
        self.quit_action = QAction(MainWindow)
        self.quit_action.setObjectName(u"quit_action")
        self.select_path_action = QAction(MainWindow)
        self.select_path_action.setObjectName(u"select_path_action")
        icon4 = QIcon()
        icon4.addFile(u":/icons/search.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.select_path_action.setIcon(icon4)
        self.scan_action = QAction(MainWindow)
        self.scan_action.setObjectName(u"scan_action")
        icon5 = QIcon()
        icon5.addFile(u":/icons/scan.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.scan_action.setIcon(icon5)
        self.manual_action = QAction(MainWindow)
        self.manual_action.setObjectName(u"manual_action")
        icon6 = QIcon()
        icon6.addFile(u":/icons/manual.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.manual_action.setIcon(icon6)
        self.about_action = QAction(MainWindow)
        self.about_action.setObjectName(u"about_action")
        icon7 = QIcon()
        icon7.addFile(u":/icons/about.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.about_action.setIcon(icon7)
        self.regex_tester_action = QAction(MainWindow)
        self.regex_tester_action.setObjectName(u"regex_tester_action")
        self.regex_tester_action.setIcon(icon4)
        self.settings_action = QAction(MainWindow)
        self.settings_action.setObjectName(u"settings_action")
        icon8 = QIcon()
        icon8.addFile(u":/icons/settings.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.settings_action.setIcon(icon8)
        self.perf_stats_action = QAction(MainWindow)
        self.perf_stats_action.setObjectName(u"perf_stats_action")
        icon9 = QIcon()
        icon9.addFile(u":/icons/stats.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.perf_stats_action.setIcon(icon9)
        self.perf_log_action = QAction(MainWindow)
        self.perf_log_action.setObjectName(u"perf_log_action")
        self.perf_log_action.setCheckable(True)
        self.central = QWidget(MainWindow)
        self.central.setObjectName(u"central")
        self.central_layout = QVBoxLayout(self.central)
        self.central_layout.setSpacing(0)
        self.central_layout.setObjectName(u"central_layout")
        self.central_layout.setContentsMargins(0, 0, 0, 0)
        self.header_bar = QFrame(self.central)
        self.header_bar.setObjectName(u"header_bar")
        self.header_bar.setFrameShape(QFrame.NoFrame)
        self.header_layout = QHBoxLayout(self.header_bar)
        self.header_layout.setSpacing(4)
        self.header_layout.setObjectName(u"header_layout")
        self.header_layout.setContentsMargins(8, 4, 8, 4)
        self.tab_scan_btn = QPushButton(self.header_bar)
        self.tab_scan_btn.setObjectName(u"tab_scan_btn")
        self.tab_scan_btn.setIcon(icon5)
        self.tab_scan_btn.setCheckable(True)
        self.tab_scan_btn.setChecked(True)

        self.header_layout.addWidget(self.tab_scan_btn)

        self.tab_rules_btn = QPushButton(self.header_bar)
        self.tab_rules_btn.setObjectName(u"tab_rules_btn")
        self.tab_rules_btn.setIcon(icon)
        self.tab_rules_btn.setCheckable(True)

        self.header_layout.addWidget(self.tab_rules_btn)

        self.tab_history_btn = QPushButton(self.header_bar)
        self.tab_history_btn.setObjectName(u"tab_history_btn")
        icon10 = QIcon()
        icon10.addFile(u":/icons/history.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.tab_history_btn.setIcon(icon10)
        self.tab_history_btn.setCheckable(True)

        self.header_layout.addWidget(self.tab_history_btn)

        self.header_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.header_layout.addItem(self.header_spacer)

        self.settings_btn = QPushButton(self.header_bar)
        self.settings_btn.setObjectName(u"settings_btn")
        self.settings_btn.setIcon(icon8)

        self.header_layout.addWidget(self.settings_btn)

        self.about_btn = QPushButton(self.header_bar)
        self.about_btn.setObjectName(u"about_btn")
        self.about_btn.setIcon(icon7)

        self.header_layout.addWidget(self.about_btn)


        self.central_layout.addWidget(self.header_bar)

        self.tab_stack = QStackedWidget(self.central)
        self.tab_stack.setObjectName(u"tab_stack")
        self.scan_tab = QWidget()
        self.scan_tab.setObjectName(u"scan_tab")
        self.scan_tab_layout = QVBoxLayout(self.scan_tab)
        self.scan_tab_layout.setSpacing(0)
        self.scan_tab_layout.setObjectName(u"scan_tab_layout")
        self.scan_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar_splitter = QSplitter(self.scan_tab)
        self.sidebar_splitter.setObjectName(u"sidebar_splitter")
        self.sidebar_splitter.setOrientation(Qt.Horizontal)
        self.sidebar_splitter.setHandleWidth(4)
        self.sidebar = QListWidget(self.sidebar_splitter)
        self.sidebar.setObjectName(u"sidebar")
        self.sidebar.setMinimumSize(QSize(160, 0))
        self.sidebar.setMaximumSize(QSize(280, 16777215))
        self.sidebar_splitter.addWidget(self.sidebar)
        self.main_stack = QStackedWidget(self.sidebar_splitter)
        self.main_stack.setObjectName(u"main_stack")
        self.setup_page = QWidget()
        self.setup_page.setObjectName(u"setup_page")
        self.setup_layout = QVBoxLayout(self.setup_page)
        self.setup_layout.setSpacing(8)
        self.setup_layout.setObjectName(u"setup_layout")
        self.setup_layout.setContentsMargins(12, 12, 12, 12)
        self.target_group = QGroupBox(self.setup_page)
        self.target_group.setObjectName(u"target_group")
        self.target_group_layout = QVBoxLayout(self.target_group)
        self.target_group_layout.setSpacing(8)
        self.target_group_layout.setObjectName(u"target_group_layout")
        self.target_group_layout.setContentsMargins(12, 16, 12, 12)
        self.scan_mode_layout = QHBoxLayout()
        self.scan_mode_layout.setSpacing(8)
        self.scan_mode_layout.setObjectName(u"scan_mode_layout")
        self.scan_mode_combo = QComboBox(self.target_group)
        self.scan_mode_combo.addItem("")
        self.scan_mode_combo.addItem("")
        self.scan_mode_combo.addItem("")
        self.scan_mode_combo.setObjectName(u"scan_mode_combo")
        sizePolicy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.scan_mode_combo.sizePolicy().hasHeightForWidth())
        self.scan_mode_combo.setSizePolicy(sizePolicy)
        self.scan_mode_combo.setMinimumSize(QSize(0, 40))

        self.scan_mode_layout.addWidget(self.scan_mode_combo)

        self.target_stack = QStackedWidget(self.target_group)
        self.target_stack.setObjectName(u"target_stack")
        sizePolicy1 = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sizePolicy1.setHorizontalStretch(1)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.target_stack.sizePolicy().hasHeightForWidth())
        self.target_stack.setSizePolicy(sizePolicy1)
        self.full_scan_page = QWidget()
        self.full_scan_page.setObjectName(u"full_scan_page")
        self.full_scan_layout = QHBoxLayout(self.full_scan_page)
        self.full_scan_layout.setObjectName(u"full_scan_layout")
        self.full_scan_layout.setContentsMargins(0, 0, 0, 0)
        self.full_scan_label = QLabel(self.full_scan_page)
        self.full_scan_label.setObjectName(u"full_scan_label")

        self.full_scan_layout.addWidget(self.full_scan_label)

        self.target_stack.addWidget(self.full_scan_page)
        self.drive_select_page = QWidget()
        self.drive_select_page.setObjectName(u"drive_select_page")
        self.drive_buttons_layout = QHBoxLayout(self.drive_select_page)
        self.drive_buttons_layout.setSpacing(4)
        self.drive_buttons_layout.setObjectName(u"drive_buttons_layout")
        self.drive_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.target_stack.addWidget(self.drive_select_page)
        self.folder_select_page = QWidget()
        self.folder_select_page.setObjectName(u"folder_select_page")
        self.folder_select_layout = QHBoxLayout(self.folder_select_page)
        self.folder_select_layout.setSpacing(4)
        self.folder_select_layout.setObjectName(u"folder_select_layout")
        self.folder_select_layout.setContentsMargins(0, 0, 0, 0)
        self.path_combo = QComboBox(self.folder_select_page)
        self.path_combo.setObjectName(u"path_combo")
        sizePolicy1.setHeightForWidth(self.path_combo.sizePolicy().hasHeightForWidth())
        self.path_combo.setSizePolicy(sizePolicy1)
        self.path_combo.setMinimumSize(QSize(0, 40))

        self.folder_select_layout.addWidget(self.path_combo)

        self.select_path_btn = QPushButton(self.folder_select_page)
        self.select_path_btn.setObjectName(u"select_path_btn")
        self.select_path_btn.setMinimumSize(QSize(0, 40))

        self.folder_select_layout.addWidget(self.select_path_btn)

        self.target_stack.addWidget(self.folder_select_page)

        self.scan_mode_layout.addWidget(self.target_stack)


        self.target_group_layout.addLayout(self.scan_mode_layout)


        self.setup_layout.addWidget(self.target_group)

        self.file_types_group = QGroupBox(self.setup_page)
        self.file_types_group.setObjectName(u"file_types_group")
        self.horizontalLayout_3 = QHBoxLayout(self.file_types_group)
        self.horizontalLayout_3.setSpacing(8)
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.horizontalLayout_3.setContentsMargins(12, 16, 12, 12)
        self.content_tab_widget = QTabWidget(self.file_types_group)
        self.content_tab_widget.setObjectName(u"content_tab_widget")
        self.file_types_tab = QWidget()
        self.file_types_tab.setObjectName(u"file_types_tab")
        self.file_types_tab_layout = QVBoxLayout(self.file_types_tab)
        self.file_types_tab_layout.setSpacing(4)
        self.file_types_tab_layout.setObjectName(u"file_types_tab_layout")
        self.file_types_tab_layout.setContentsMargins(4, 4, 4, 4)
        self.file_types_count_label = QLabel(self.file_types_tab)
        self.file_types_count_label.setObjectName(u"file_types_count_label")

        self.file_types_tab_layout.addWidget(self.file_types_count_label)

        self.file_types_view = QTreeView(self.file_types_tab)
        self.file_types_view.setObjectName(u"file_types_view")
        sizePolicy2 = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        sizePolicy2.setHorizontalStretch(0)
        sizePolicy2.setVerticalStretch(1)
        sizePolicy2.setHeightForWidth(self.file_types_view.sizePolicy().hasHeightForWidth())
        self.file_types_view.setSizePolicy(sizePolicy2)
        self.file_types_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.file_types_view.setUniformRowHeights(True)
        self.file_types_view.setHeaderHidden(True)
        self.file_types_view.setExpandsOnDoubleClick(False)

        self.file_types_tab_layout.addWidget(self.file_types_view)

        self.content_tab_widget.addTab(self.file_types_tab, "")
        self.ignore_dirs_tab = QWidget()
        self.ignore_dirs_tab.setObjectName(u"ignore_dirs_tab")
        self.ignore_dirs_tab_layout = QVBoxLayout(self.ignore_dirs_tab)
        self.ignore_dirs_tab_layout.setSpacing(4)
        self.ignore_dirs_tab_layout.setObjectName(u"ignore_dirs_tab_layout")
        self.ignore_dirs_tab_layout.setContentsMargins(4, 4, 4, 4)
        self.ignore_dirs_hint_label = QLabel(self.ignore_dirs_tab)
        self.ignore_dirs_hint_label.setObjectName(u"ignore_dirs_hint_label")
        self.ignore_dirs_hint_label.setWordWrap(True)

        self.ignore_dirs_tab_layout.addWidget(self.ignore_dirs_hint_label)

        self.ignore_dirs_edit = QPlainTextEdit(self.ignore_dirs_tab)
        self.ignore_dirs_edit.setObjectName(u"ignore_dirs_edit")

        self.ignore_dirs_tab_layout.addWidget(self.ignore_dirs_edit)

        self.content_tab_widget.addTab(self.ignore_dirs_tab, "")
        self.ignore_extensions_tab = QWidget()
        self.ignore_extensions_tab.setObjectName(u"ignore_extensions_tab")
        self.ignore_extensions_tab_layout = QVBoxLayout(self.ignore_extensions_tab)
        self.ignore_extensions_tab_layout.setSpacing(4)
        self.ignore_extensions_tab_layout.setObjectName(u"ignore_extensions_tab_layout")
        self.ignore_extensions_tab_layout.setContentsMargins(4, 4, 4, 4)
        self.ignore_extensions_hint_label = QLabel(self.ignore_extensions_tab)
        self.ignore_extensions_hint_label.setObjectName(u"ignore_extensions_hint_label")
        self.ignore_extensions_hint_label.setWordWrap(True)

        self.ignore_extensions_tab_layout.addWidget(self.ignore_extensions_hint_label)

        self.ignore_extensions_edit = QPlainTextEdit(self.ignore_extensions_tab)
        self.ignore_extensions_edit.setObjectName(u"ignore_extensions_edit")

        self.ignore_extensions_tab_layout.addWidget(self.ignore_extensions_edit)

        self.content_tab_widget.addTab(self.ignore_extensions_tab, "")

        self.horizontalLayout_3.addWidget(self.content_tab_widget)


        self.setup_layout.addWidget(self.file_types_group)

        self.setup_action_bar = QFrame(self.setup_page)
        self.setup_action_bar.setObjectName(u"setup_action_bar")
        self.setup_action_bar.setFrameShape(QFrame.NoFrame)
        self.setup_btn_row = QHBoxLayout(self.setup_action_bar)
        self.setup_btn_row.setSpacing(8)
        self.setup_btn_row.setObjectName(u"setup_btn_row")
        self.setup_btn_row.setContentsMargins(0, 12, 0, 0)
        self.setup_btn_leading_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.setup_btn_row.addItem(self.setup_btn_leading_spacer)

        self.view_results_btn = QPushButton(self.setup_action_bar)
        self.view_results_btn.setObjectName(u"view_results_btn")
        self.view_results_btn.setEnabled(False)
        self.view_results_btn.setMinimumSize(QSize(180, 40))
        self.view_results_btn.setCursor(QCursor(Qt.PointingHandCursor))

        self.setup_btn_row.addWidget(self.view_results_btn)

        self.scan_btn = QPushButton(self.setup_action_bar)
        self.scan_btn.setObjectName(u"scan_btn")
        self.scan_btn.setEnabled(False)
        self.scan_btn.setMinimumSize(QSize(180, 40))
        self.scan_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.scan_btn.setIcon(icon5)

        self.setup_btn_row.addWidget(self.scan_btn)


        self.setup_layout.addWidget(self.setup_action_bar)

        self.main_stack.addWidget(self.setup_page)
        self.scanning_page = QWidget()
        self.scanning_page.setObjectName(u"scanning_page")
        self.scanning_layout = QVBoxLayout(self.scanning_page)
        self.scanning_layout.setSpacing(12)
        self.scanning_layout.setObjectName(u"scanning_layout")
        self.scanning_layout.setContentsMargins(12, 24, 12, 12)
        self.tabWidget = QTabWidget(self.scanning_page)
        self.tabWidget.setObjectName(u"tabWidget")
        self.tab_matched_files_list = QWidget()
        self.tab_matched_files_list.setObjectName(u"tab_matched_files_list")
        self.horizontalLayout = QHBoxLayout(self.tab_matched_files_list)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.matched_files_list = QListWidget(self.tab_matched_files_list)
        self.matched_files_list.setObjectName(u"matched_files_list")

        self.horizontalLayout.addWidget(self.matched_files_list)

        self.tabWidget.addTab(self.tab_matched_files_list, "")
        self.tab_skipped_dirs_list = QWidget()
        self.tab_skipped_dirs_list.setObjectName(u"tab_skipped_dirs_list")
        self.horizontalLayout_2 = QHBoxLayout(self.tab_skipped_dirs_list)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.skipped_dirs_list = QListWidget(self.tab_skipped_dirs_list)
        self.skipped_dirs_list.setObjectName(u"skipped_dirs_list")

        self.horizontalLayout_2.addWidget(self.skipped_dirs_list)

        self.tabWidget.addTab(self.tab_skipped_dirs_list, "")

        self.scanning_layout.addWidget(self.tabWidget)

        self.scan_stats_label = QLabel(self.scanning_page)
        self.scan_stats_label.setObjectName(u"scan_stats_label")
        self.scan_stats_label.setTextFormat(Qt.RichText)
        self.scan_stats_label.setAlignment(Qt.AlignCenter)

        self.scanning_layout.addWidget(self.scan_stats_label)

        self.scanning_btn_row = QHBoxLayout()
        self.scanning_btn_row.setSpacing(12)
        self.scanning_btn_row.setObjectName(u"scanning_btn_row")
        self.scanning_btn_left_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.scanning_btn_row.addItem(self.scanning_btn_left_spacer)

        self.pause_resume_btn = QPushButton(self.scanning_page)
        self.pause_resume_btn.setObjectName(u"pause_resume_btn")
        self.pause_resume_btn.setMinimumSize(QSize(140, 44))
        icon11 = QIcon()
        icon11.addFile(u":/icons/pause.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.pause_resume_btn.setIcon(icon11)

        self.scanning_btn_row.addWidget(self.pause_resume_btn)

        self.cancel_btn = QPushButton(self.scanning_page)
        self.cancel_btn.setObjectName(u"cancel_btn")
        self.cancel_btn.setMinimumSize(QSize(140, 44))
        icon12 = QIcon()
        icon12.addFile(u":/icons/stop.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.cancel_btn.setIcon(icon12)

        self.scanning_btn_row.addWidget(self.cancel_btn)

        self.scanning_btn_right_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.scanning_btn_row.addItem(self.scanning_btn_right_spacer)


        self.scanning_layout.addLayout(self.scanning_btn_row)

        self.main_stack.addWidget(self.scanning_page)
        self.results_page = QWidget()
        self.results_page.setObjectName(u"results_page")
        self.results_page_layout = QVBoxLayout(self.results_page)
        self.results_page_layout.setSpacing(4)
        self.results_page_layout.setObjectName(u"results_page_layout")
        self.results_page_layout.setContentsMargins(0, 4, 0, 0)
        self.results_top_bar = QFrame(self.results_page)
        self.results_top_bar.setObjectName(u"results_top_bar")
        sizePolicy3 = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        sizePolicy3.setHorizontalStretch(0)
        sizePolicy3.setVerticalStretch(0)
        sizePolicy3.setHeightForWidth(self.results_top_bar.sizePolicy().hasHeightForWidth())
        self.results_top_bar.setSizePolicy(sizePolicy3)
        self.results_top_layout = QHBoxLayout(self.results_top_bar)
        self.results_top_layout.setSpacing(8)
        self.results_top_layout.setObjectName(u"results_top_layout")
        self.results_top_layout.setContentsMargins(8, 4, 8, 4)
        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.results_top_layout.addItem(self.horizontalSpacer)

        self.rescan_btn = QPushButton(self.results_top_bar)
        self.rescan_btn.setObjectName(u"rescan_btn")
        self.rescan_btn.setMinimumSize(QSize(160, 50))
        icon13 = QIcon()
        icon13.addFile(u":/icons/rescan.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.rescan_btn.setIcon(icon13)

        self.results_top_layout.addWidget(self.rescan_btn)

        self.export_btn = QPushButton(self.results_top_bar)
        self.export_btn.setObjectName(u"export_btn")
        self.export_btn.setEnabled(False)
        self.export_btn.setMinimumSize(QSize(160, 50))
        icon14 = QIcon()
        icon14.addFile(u":/icons/export.svg", QSize(), QIcon.Normal, QIcon.Off)
        self.export_btn.setIcon(icon14)

        self.results_top_layout.addWidget(self.export_btn)

        self.results_top_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.results_top_layout.addItem(self.results_top_spacer)


        self.results_page_layout.addWidget(self.results_top_bar)

        self.results_splitter = QSplitter(self.results_page)
        self.results_splitter.setObjectName(u"results_splitter")
        self.results_splitter.setOrientation(Qt.Horizontal)
        self.results_list_area = QWidget(self.results_splitter)
        self.results_list_area.setObjectName(u"results_list_area")
        sizePolicy4 = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        sizePolicy4.setHorizontalStretch(0)
        sizePolicy4.setVerticalStretch(0)
        sizePolicy4.setHeightForWidth(self.results_list_area.sizePolicy().hasHeightForWidth())
        self.results_list_area.setSizePolicy(sizePolicy4)
        self.results_list_layout = QVBoxLayout(self.results_list_area)
        self.results_list_layout.setSpacing(4)
        self.results_list_layout.setObjectName(u"results_list_layout")
        self.results_list_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_bar = QFrame(self.results_list_area)
        self.filter_bar.setObjectName(u"filter_bar")
        self.filter_layout = QHBoxLayout(self.filter_bar)
        self.filter_layout.setSpacing(6)
        self.filter_layout.setObjectName(u"filter_layout")
        self.filter_layout.setContentsMargins(4, 2, 4, 2)
        self.path_filter_input = QLineEdit(self.filter_bar)
        self.path_filter_input.setObjectName(u"path_filter_input")
        self.path_filter_input.setClearButtonEnabled(True)

        self.filter_layout.addWidget(self.path_filter_input)

        self.rule_filter_combo = QComboBox(self.filter_bar)
        self.rule_filter_combo.setObjectName(u"rule_filter_combo")

        self.filter_layout.addWidget(self.rule_filter_combo)

        self.group_mode_combo = QComboBox(self.filter_bar)
        self.group_mode_combo.setObjectName(u"group_mode_combo")

        self.filter_layout.addWidget(self.group_mode_combo)


        self.results_list_layout.addWidget(self.filter_bar)

        self.result_tree = ResultTreeView(self.results_list_area)
        self.result_tree.setObjectName(u"result_tree")
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.setRootIsDecorated(True)
        self.result_tree.setSortingEnabled(True)

        self.results_list_layout.addWidget(self.result_tree)

        self.results_splitter.addWidget(self.results_list_area)
        self.detail_area = QWidget(self.results_splitter)
        self.detail_area.setObjectName(u"detail_area")
        sizePolicy4.setHeightForWidth(self.detail_area.sizePolicy().hasHeightForWidth())
        self.detail_area.setSizePolicy(sizePolicy4)
        self.detail_layout = QVBoxLayout(self.detail_area)
        self.detail_layout.setSpacing(4)
        self.detail_layout.setObjectName(u"detail_layout")
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_action_stack = QStackedWidget(self.detail_area)
        self.detail_action_stack.setObjectName(u"detail_action_stack")
        self.detail_empty_action = QFrame()
        self.detail_empty_action.setObjectName(u"detail_empty_action")
        sizePolicy5 = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        sizePolicy5.setHorizontalStretch(0)
        sizePolicy5.setVerticalStretch(0)
        sizePolicy5.setHeightForWidth(self.detail_empty_action.sizePolicy().hasHeightForWidth())
        self.detail_empty_action.setSizePolicy(sizePolicy5)
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

        self.detail_action_stack.addWidget(self.detail_empty_action)
        self.detail_nonempty_action = QFrame()
        self.detail_nonempty_action.setObjectName(u"detail_nonempty_action")
        self.detail_nonempty_action_layout = QHBoxLayout(self.detail_nonempty_action)
        self.detail_nonempty_action_layout.setSpacing(6)
        self.detail_nonempty_action_layout.setObjectName(u"detail_nonempty_action_layout")
        self.detail_nonempty_action_layout.setContentsMargins(8, 4, 8, 4)
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

        self.detail_action_stack.addWidget(self.detail_nonempty_action)

        self.detail_layout.addWidget(self.detail_action_stack)

        self.detail_main_stack = QStackedWidget(self.detail_area)
        self.detail_main_stack.setObjectName(u"detail_main_stack")
        self.detail_empty_main = QFrame()
        self.detail_empty_main.setObjectName(u"detail_empty_main")
        self.detail_empty_main_layout = QVBoxLayout(self.detail_empty_main)
        self.detail_empty_main_layout.setObjectName(u"detail_empty_main_layout")
        self.detail_empty_top_spacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)

        self.detail_empty_main_layout.addItem(self.detail_empty_top_spacer)

        self.detail_empty_hint = QLabel(self.detail_empty_main)
        self.detail_empty_hint.setObjectName(u"detail_empty_hint")
        sizePolicy5.setHeightForWidth(self.detail_empty_hint.sizePolicy().hasHeightForWidth())
        self.detail_empty_hint.setSizePolicy(sizePolicy5)
        self.detail_empty_hint.setAlignment(Qt.AlignCenter)

        self.detail_empty_main_layout.addWidget(self.detail_empty_hint)

        self.detail_empty_bottom_spacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)

        self.detail_empty_main_layout.addItem(self.detail_empty_bottom_spacer)

        self.detail_main_stack.addWidget(self.detail_empty_main)
        self.detail_nonempty_main = QFrame()
        self.detail_nonempty_main.setObjectName(u"detail_nonempty_main")
        sizePolicy6 = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        sizePolicy6.setHorizontalStretch(0)
        sizePolicy6.setVerticalStretch(0)
        sizePolicy6.setHeightForWidth(self.detail_nonempty_main.sizePolicy().hasHeightForWidth())
        self.detail_nonempty_main.setSizePolicy(sizePolicy6)
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
        if (self.detail_hits_table.columnCount() < 6):
            self.detail_hits_table.setColumnCount(6)
        __qtablewidgetitem = QTableWidgetItem()
        self.detail_hits_table.setHorizontalHeaderItem(0, __qtablewidgetitem)
        __qtablewidgetitem1 = QTableWidgetItem()
        self.detail_hits_table.setHorizontalHeaderItem(1, __qtablewidgetitem1)
        __qtablewidgetitem2 = QTableWidgetItem()
        self.detail_hits_table.setHorizontalHeaderItem(2, __qtablewidgetitem2)
        __qtablewidgetitem3 = QTableWidgetItem()
        self.detail_hits_table.setHorizontalHeaderItem(3, __qtablewidgetitem3)
        __qtablewidgetitem4 = QTableWidgetItem()
        self.detail_hits_table.setHorizontalHeaderItem(4, __qtablewidgetitem4)
        __qtablewidgetitem5 = QTableWidgetItem()
        self.detail_hits_table.setHorizontalHeaderItem(5, __qtablewidgetitem5)
        self.detail_hits_table.setObjectName(u"detail_hits_table")
        self.detail_hits_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.detail_hits_table.setSelectionBehavior(QAbstractItemView.SelectRows)

        self.detail_nonempty_main_layout.addWidget(self.detail_hits_table)

        self.detail_preview_title_label = QLabel(self.detail_nonempty_main)
        self.detail_preview_title_label.setObjectName(u"detail_preview_title_label")

        self.detail_nonempty_main_layout.addWidget(self.detail_preview_title_label)

        self.detail_preview = QTextEdit(self.detail_nonempty_main)
        self.detail_preview.setObjectName(u"detail_preview")
        self.detail_preview.setReadOnly(True)

        self.detail_nonempty_main_layout.addWidget(self.detail_preview)

        self.detail_actions_layout = QHBoxLayout()
        self.detail_actions_layout.setSpacing(8)
        self.detail_actions_layout.setObjectName(u"detail_actions_layout")
        self.move_to_staging_btn = QPushButton(self.detail_nonempty_main)
        self.move_to_staging_btn.setObjectName(u"move_to_staging_btn")

        self.detail_actions_layout.addWidget(self.move_to_staging_btn)

        self.toggle_skip_btn = QPushButton(self.detail_nonempty_main)
        self.toggle_skip_btn.setObjectName(u"toggle_skip_btn")
        self.toggle_skip_btn.setCheckable(True)

        self.detail_actions_layout.addWidget(self.toggle_skip_btn)

        self.detail_actions_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.detail_actions_layout.addItem(self.detail_actions_spacer)


        self.detail_nonempty_main_layout.addLayout(self.detail_actions_layout)

        self.detail_main_stack.addWidget(self.detail_nonempty_main)

        self.detail_layout.addWidget(self.detail_main_stack)

        self.results_splitter.addWidget(self.detail_area)

        self.results_page_layout.addWidget(self.results_splitter)

        self.main_stack.addWidget(self.results_page)
        self.sidebar_splitter.addWidget(self.main_stack)

        self.scan_tab_layout.addWidget(self.sidebar_splitter)

        self.tab_stack.addWidget(self.scan_tab)
        self.rules_tab = QWidget()
        self.rules_tab.setObjectName(u"rules_tab")
        self.rules_tab_layout = QVBoxLayout(self.rules_tab)
        self.rules_tab_layout.setSpacing(8)
        self.rules_tab_layout.setObjectName(u"rules_tab_layout")
        self.rules_tab_layout.setContentsMargins(12, 12, 12, 12)
        self.rules_group = QGroupBox(self.rules_tab)
        self.rules_group.setObjectName(u"rules_group")
        self.rules_group_layout = QVBoxLayout(self.rules_group)
        self.rules_group_layout.setSpacing(6)
        self.rules_group_layout.setObjectName(u"rules_group_layout")
        self.rules_group_layout.setContentsMargins(12, 16, 12, 12)
        self.rules_btn_row = QHBoxLayout()
        self.rules_btn_row.setSpacing(8)
        self.rules_btn_row.setObjectName(u"rules_btn_row")
        self.load_rules_btn = QPushButton(self.rules_group)
        self.load_rules_btn.setObjectName(u"load_rules_btn")
        self.load_rules_btn.setMinimumSize(QSize(150, 40))
        self.load_rules_btn.setIcon(icon)

        self.rules_btn_row.addWidget(self.load_rules_btn)

        self.edit_rule_btn = QPushButton(self.rules_group)
        self.edit_rule_btn.setObjectName(u"edit_rule_btn")
        self.edit_rule_btn.setMinimumSize(QSize(0, 40))
        self.edit_rule_btn.setIcon(icon1)

        self.rules_btn_row.addWidget(self.edit_rule_btn)

        self.rules_btn_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.rules_btn_row.addItem(self.rules_btn_spacer)


        self.rules_group_layout.addLayout(self.rules_btn_row)

        self.rules_file_label = QLabel(self.rules_group)
        self.rules_file_label.setObjectName(u"rules_file_label")

        self.rules_group_layout.addWidget(self.rules_file_label)

        self.rules_file_list = QListWidget(self.rules_group)
        self.rules_file_list.setObjectName(u"rules_file_list")
        self.rules_file_list.setMaximumSize(QSize(16777215, 120))

        self.rules_group_layout.addWidget(self.rules_file_list)

        self.rules_tree = QTreeWidget(self.rules_group)
        self.rules_tree.setObjectName(u"rules_tree")
        self.rules_tree.setRootIsDecorated(False)

        self.rules_group_layout.addWidget(self.rules_tree)


        self.rules_tab_layout.addWidget(self.rules_group)

        self.tab_stack.addWidget(self.rules_tab)
        self.history_tab = QWidget()
        self.history_tab.setObjectName(u"history_tab")
        self.history_tab_layout = QVBoxLayout(self.history_tab)
        self.history_tab_layout.setSpacing(8)
        self.history_tab_layout.setObjectName(u"history_tab_layout")
        self.history_tab_layout.setContentsMargins(12, 12, 12, 12)
        self.history_label = QLabel(self.history_tab)
        self.history_label.setObjectName(u"history_label")

        self.history_tab_layout.addWidget(self.history_label)

        self.history_list = QListWidget(self.history_tab)
        self.history_list.setObjectName(u"history_list")

        self.history_tab_layout.addWidget(self.history_list)

        self.tab_stack.addWidget(self.history_tab)

        self.central_layout.addWidget(self.tab_stack)

        MainWindow.setCentralWidget(self.central)
        self.menubar = QMenuBar(MainWindow)
        self.menubar.setObjectName(u"menubar")
        self.menubar.setGeometry(QRect(0, 0, 1059, 27))
        self.file_menu = QMenu(self.menubar)
        self.file_menu.setObjectName(u"file_menu")
        self.scan_menu = QMenu(self.menubar)
        self.scan_menu.setObjectName(u"scan_menu")
        self.tools_menu = QMenu(self.menubar)
        self.tools_menu.setObjectName(u"tools_menu")
        self.help_menu = QMenu(self.menubar)
        self.help_menu.setObjectName(u"help_menu")
        MainWindow.setMenuBar(self.menubar)

        self.menubar.addAction(self.file_menu.menuAction())
        self.menubar.addAction(self.scan_menu.menuAction())
        self.menubar.addAction(self.tools_menu.menuAction())
        self.menubar.addAction(self.help_menu.menuAction())
        self.file_menu.addAction(self.load_rules_action)
        self.file_menu.addAction(self.edit_rules_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.export_csv_action)
        self.file_menu.addAction(self.export_json_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.settings_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.quit_action)
        self.scan_menu.addAction(self.select_path_action)
        self.scan_menu.addAction(self.scan_action)
        self.scan_menu.addSeparator()
        self.scan_menu.addAction(self.perf_stats_action)
        self.scan_menu.addAction(self.perf_log_action)
        self.tools_menu.addAction(self.regex_tester_action)
        self.help_menu.addAction(self.manual_action)
        self.help_menu.addAction(self.about_action)

        self.retranslateUi(MainWindow)

        self.tab_stack.setCurrentIndex(0)
        self.main_stack.setCurrentIndex(0)
        self.target_stack.setCurrentIndex(2)
        self.content_tab_widget.setCurrentIndex(0)
        self.tabWidget.setCurrentIndex(0)
        self.detail_action_stack.setCurrentIndex(0)
        self.detail_main_stack.setCurrentIndex(0)


        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"fuscan \u901a\u7528\u6587\u4ef6\u626b\u63cf\u5668", None))
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
        self.manual_action.setText(QCoreApplication.translate("MainWindow", u"\u7528\u6237\u624b\u518c", None))
#if QT_CONFIG(shortcut)
        self.manual_action.setShortcut(QCoreApplication.translate("MainWindow", u"F1", None))
#endif // QT_CONFIG(shortcut)
        self.about_action.setText(QCoreApplication.translate("MainWindow", u"\u5173\u4e8e", None))
        self.regex_tester_action.setText(QCoreApplication.translate("MainWindow", u"\u6b63\u5219\u8868\u8fbe\u5f0f\u6d4b\u8bd5\u5de5\u5177...", None))
#if QT_CONFIG(shortcut)
        self.regex_tester_action.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+R", None))
#endif // QT_CONFIG(shortcut)
        self.settings_action.setText(QCoreApplication.translate("MainWindow", u"\u8bbe\u7f6e...", None))
#if QT_CONFIG(shortcut)
        self.settings_action.setShortcut(QCoreApplication.translate("MainWindow", u"Ctrl+,", None))
#endif // QT_CONFIG(shortcut)
        self.perf_stats_action.setText(QCoreApplication.translate("MainWindow", u"\u6027\u80fd\u7edf\u8ba1...", None))
        self.perf_log_action.setText(QCoreApplication.translate("MainWindow", u"\u542f\u7528\u6027\u80fd\u65e5\u5fd7", None))
        self.tab_scan_btn.setText(QCoreApplication.translate("MainWindow", u"\u626b\u63cf", None))
        self.tab_rules_btn.setText(QCoreApplication.translate("MainWindow", u"\u89c4\u5219\u7ba1\u7406", None))
        self.tab_history_btn.setText(QCoreApplication.translate("MainWindow", u"\u626b\u63cf\u5386\u53f2", None))
        self.settings_btn.setText(QCoreApplication.translate("MainWindow", u"\u8bbe\u7f6e", None))
        self.about_btn.setText(QCoreApplication.translate("MainWindow", u"\u5173\u4e8e", None))
        self.target_group.setTitle(QCoreApplication.translate("MainWindow", u"\u626b\u63cf\u76ee\u6807", None))
        self.scan_mode_combo.setItemText(0, QCoreApplication.translate("MainWindow", u"\u5168\u76d8\u626b\u63cf", None))
        self.scan_mode_combo.setItemText(1, QCoreApplication.translate("MainWindow", u"\u9009\u62e9\u76d8\u7b26", None))
        self.scan_mode_combo.setItemText(2, QCoreApplication.translate("MainWindow", u"\u9009\u62e9\u6587\u4ef6\u5939", None))

#if QT_CONFIG(tooltip)
        self.scan_mode_combo.setToolTip(QCoreApplication.translate("MainWindow", u"\u9009\u62e9\u626b\u63cf\u6a21\u5f0f", None))
#endif // QT_CONFIG(tooltip)
        self.full_scan_label.setText(QCoreApplication.translate("MainWindow", u"\u5c06\u626b\u63cf\u6240\u6709\u76d8\u7b26", None))
#if QT_CONFIG(tooltip)
        self.path_combo.setToolTip(QCoreApplication.translate("MainWindow", u"\u626b\u63cf\u8def\u5f84\uff08\u53ef\u4ece\u5386\u53f2\u8bb0\u5f55\u4e2d\u9009\u62e9\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.select_path_btn.setText(QCoreApplication.translate("MainWindow", u"\u9009\u62e9...", None))
#if QT_CONFIG(tooltip)
        self.file_types_group.setToolTip(QCoreApplication.translate("MainWindow", u"\u901a\u8fc7 TAB \u5207\u6362\u6587\u4ef6\u7c7b\u578b\u3001\u5ffd\u7565\u76ee\u5f55\u4e0e\u5ffd\u7565\u6269\u5c55\u540d", None))
#endif // QT_CONFIG(tooltip)
        self.file_types_group.setTitle(QCoreApplication.translate("MainWindow", u"\u6587\u4ef6\u7c7b\u578b\u4e0e\u5ffd\u7565\u9879", None))
#if QT_CONFIG(tooltip)
        self.content_tab_widget.setToolTip(QCoreApplication.translate("MainWindow", u"\u5207\u6362\u6587\u4ef6\u7c7b\u578b\u3001\u5ffd\u7565\u76ee\u5f55\u4e0e\u5ffd\u7565\u6269\u5c55\u540d", None))
#endif // QT_CONFIG(tooltip)
        self.file_types_count_label.setText(QCoreApplication.translate("MainWindow", u"\u5df2\u52fe\u9009 15/15 \u9879", None))
#if QT_CONFIG(tooltip)
        self.file_types_view.setToolTip(QCoreApplication.translate("MainWindow", u"\u52fe\u9009\u8981\u626b\u63cf\u7684\u6587\u4ef6\u7c7b\u578b\uff0c\u53d6\u6d88\u53ef\u63d0\u9ad8\u626b\u63cf\u901f\u5ea6\uff1b\u70b9\u51fb\u7236\u7c7b\u522b\u6279\u91cf\u52fe\u9009", None))
#endif // QT_CONFIG(tooltip)
        self.content_tab_widget.setTabText(self.content_tab_widget.indexOf(self.file_types_tab), QCoreApplication.translate("MainWindow", u"\u6587\u4ef6\u7c7b\u578b", None))
        self.ignore_dirs_hint_label.setText(QCoreApplication.translate("MainWindow", u"\u6309\u76ee\u5f55\u540d\u5339\u914d\u4efb\u610f\u5c42\u7ea7\uff08\u5927\u5c0f\u5199\u4e0d\u654f\u611f\uff09\uff0c\u547d\u4e2d\u5373\u8df3\u8fc7\u5176\u4e0b\u6240\u6709\u6587\u4ef6", None))
#if QT_CONFIG(tooltip)
        self.ignore_dirs_edit.setToolTip(QCoreApplication.translate("MainWindow", u"\u4e00\u884c\u4e00\u4e2a\u76ee\u5f55\u540d\uff08\u5927\u5c0f\u5199\u4e0d\u654f\u611f\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.ignore_dirs_edit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u4e00\u884c\u4e00\u4e2a\u76ee\u5f55\u540d\uff08\u5927\u5c0f\u5199\u4e0d\u654f\u611f\uff09\n"
"\u5982\uff1a.git\n"
"    node_modules\n"
"    Program Files", None))
        self.content_tab_widget.setTabText(self.content_tab_widget.indexOf(self.ignore_dirs_tab), QCoreApplication.translate("MainWindow", u"\u5ffd\u7565\u76ee\u5f55", None))
        self.ignore_extensions_hint_label.setText(QCoreApplication.translate("MainWindow", u"\u4e0d\u542b\u70b9\uff0c\u5927\u5c0f\u5199\u4e0d\u654f\u611f\uff0c\u547d\u4e2d\u5373\u8df3\u8fc7\u8be5\u7c7b\u6587\u4ef6", None))
#if QT_CONFIG(tooltip)
        self.ignore_extensions_edit.setToolTip(QCoreApplication.translate("MainWindow", u"\u4e00\u884c\u4e00\u4e2a\u6269\u5c55\u540d\uff08\u4e0d\u542b\u70b9\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.ignore_extensions_edit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u4e00\u884c\u4e00\u4e2a\u6269\u5c55\u540d\uff08\u4e0d\u542b\u70b9\uff09\n"
"\u5982\uff1apyc\n"
"    exe\n"
"    dll", None))
        self.content_tab_widget.setTabText(self.content_tab_widget.indexOf(self.ignore_extensions_tab), QCoreApplication.translate("MainWindow", u"\u5ffd\u7565\u6269\u5c55\u540d", None))
#if QT_CONFIG(tooltip)
        self.view_results_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u67e5\u770b\u4e0a\u6b21\u626b\u63cf\u7ed3\u679c", None))
#endif // QT_CONFIG(tooltip)
        self.view_results_btn.setText(QCoreApplication.translate("MainWindow", u"\u67e5\u770b\u7ed3\u679c", None))
        self.scan_btn.setText(QCoreApplication.translate("MainWindow", u"\u5f00\u59cb\u626b\u63cf", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab_matched_files_list), QCoreApplication.translate("MainWindow", u"\u547d\u4e2d\u6587\u4ef6", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab_skipped_dirs_list), QCoreApplication.translate("MainWindow", u"\u8df3\u8fc7\u76ee\u5f55", None))
        self.scan_stats_label.setText("")
        self.pause_resume_btn.setText(QCoreApplication.translate("MainWindow", u"\u6682\u505c\u626b\u63cf", None))
        self.cancel_btn.setText(QCoreApplication.translate("MainWindow", u"\u53d6\u6d88\u626b\u63cf", None))
#if QT_CONFIG(tooltip)
        self.rescan_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u8fd4\u56de\u914d\u7f6e\u9875\u91cd\u65b0\u626b\u63cf", None))
#endif // QT_CONFIG(tooltip)
        self.rescan_btn.setText(QCoreApplication.translate("MainWindow", u"\u91cd\u65b0\u626b\u63cf", None))
#if QT_CONFIG(tooltip)
        self.export_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5bfc\u51fa\u626b\u63cf\u7ed3\u679c\u5230\u6587\u4ef6", None))
#endif // QT_CONFIG(tooltip)
        self.export_btn.setText(QCoreApplication.translate("MainWindow", u"\u5bfc\u51fa\u7ed3\u679c", None))
        self.path_filter_input.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u6309\u8def\u5f84\u7b5b\u9009...", None))
#if QT_CONFIG(tooltip)
        self.rule_filter_combo.setToolTip(QCoreApplication.translate("MainWindow", u"\u6309\u89c4\u5219\u7b5b\u9009", None))
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(tooltip)
        self.group_mode_combo.setToolTip(QCoreApplication.translate("MainWindow", u"\u5206\u7ec4\u6a21\u5f0f", None))
#endif // QT_CONFIG(tooltip)
        self.detail_action_title_label.setText(QCoreApplication.translate("MainWindow", u"\u8be6\u60c5\u64cd\u4f5c:", None))
        self.detail_action_hint.setText(QCoreApplication.translate("MainWindow", u"\u672a\u9009\u4e2d\u4efb\u4f55\u9879", None))
#if QT_CONFIG(tooltip)
        self.detail_prev_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u8df3\u8f6c\u5230\u4e0a\u4e00\u4e2a\u547d\u4e2d\u4f4d\u7f6e", None))
#endif // QT_CONFIG(tooltip)
        self.detail_prev_btn.setText(QCoreApplication.translate("MainWindow", u"\u4e0a\u4e00\u6761", None))
#if QT_CONFIG(tooltip)
        self.detail_next_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u8df3\u8f6c\u5230\u4e0b\u4e00\u4e2a\u547d\u4e2d\u4f4d\u7f6e", None))
#endif // QT_CONFIG(tooltip)
        self.detail_next_btn.setText(QCoreApplication.translate("MainWindow", u"\u4e0b\u4e00\u6761", None))
        self.detail_nav_label.setText(QCoreApplication.translate("MainWindow", u"0 / 0", None))
#if QT_CONFIG(tooltip)
        self.detail_open_location_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5728\u6587\u4ef6\u7ba1\u7406\u5668\u4e2d\u6253\u5f00\u6240\u5728\u76ee\u5f55", None))
#endif // QT_CONFIG(tooltip)
        self.detail_open_location_btn.setText(QCoreApplication.translate("MainWindow", u"\u6253\u5f00\u6587\u4ef6\u4f4d\u7f6e", None))
        self.detail_empty_hint.setText(QCoreApplication.translate("MainWindow", u"\u672a\u9009\u4e2d\u4efb\u4f55\u9879\n"
"\u8bf7\u9009\u62e9\u5de6\u4fa7\u7ed3\u679c\u9879\u67e5\u770b\u8be6\u60c5", None))
        self.detail_hits_title_label.setText(QCoreApplication.translate("MainWindow", u"\u547d\u4e2d\u89c4\u5219:", None))
        ___qtablewidgetitem = self.detail_hits_table.horizontalHeaderItem(0)
        ___qtablewidgetitem.setText(QCoreApplication.translate("MainWindow", u"\u89c4\u5219\u540d", None));
        ___qtablewidgetitem1 = self.detail_hits_table.horizontalHeaderItem(1)
        ___qtablewidgetitem1.setText(QCoreApplication.translate("MainWindow", u"\u4e25\u91cd\u7b49\u7ea7", None));
        ___qtablewidgetitem2 = self.detail_hits_table.horizontalHeaderItem(2)
        ___qtablewidgetitem2.setText(QCoreApplication.translate("MainWindow", u"\u6761\u6570", None));
        ___qtablewidgetitem3 = self.detail_hits_table.horizontalHeaderItem(3)
        ___qtablewidgetitem3.setText(QCoreApplication.translate("MainWindow", u"\u4f4d\u7f6e\u6570", None));
        ___qtablewidgetitem4 = self.detail_hits_table.horizontalHeaderItem(4)
        ___qtablewidgetitem4.setText(QCoreApplication.translate("MainWindow", u"\u8be6\u60c5", None));
        ___qtablewidgetitem5 = self.detail_hits_table.horizontalHeaderItem(5)
        ___qtablewidgetitem5.setText(QCoreApplication.translate("MainWindow", u"\u63cf\u8ff0", None));
        self.detail_preview_title_label.setText(QCoreApplication.translate("MainWindow", u"\u5185\u5bb9\u9884\u89c8 (\u5173\u952e\u8bcd\u9ad8\u4eae):", None))
#if QT_CONFIG(tooltip)
        self.move_to_staging_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u5c06\u5f53\u524d\u6587\u4ef6\u79fb\u52a8\u5230\u6682\u5b58\u533a\u76ee\u5f55\uff08\u53ef\u5728\u8bbe\u7f6e\u4e2d\u914d\u7f6e\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.move_to_staging_btn.setText(QCoreApplication.translate("MainWindow", u"\u79fb\u52a8\u81f3\u6682\u5b58\u533a", None))
#if QT_CONFIG(tooltip)
        self.toggle_skip_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u6807\u8bb0\u540e\u540e\u7eed\u626b\u63cf\u5c06\u76f4\u63a5\u8df3\u8fc7\u6b64\u6587\u4ef6", None))
#endif // QT_CONFIG(tooltip)
        self.toggle_skip_btn.setText(QCoreApplication.translate("MainWindow", u"\u6807\u8bb0\u4e3a\u8df3\u8fc7", None))
        self.rules_group.setTitle(QCoreApplication.translate("MainWindow", u"\u89c4\u5219\u914d\u7f6e", None))
        self.load_rules_btn.setText(QCoreApplication.translate("MainWindow", u"\u52a0\u8f7d\u89c4\u5219...", None))
#if QT_CONFIG(tooltip)
        self.edit_rule_btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u7f16\u8f91\u9009\u4e2d\u7684\u89c4\u5219\u6587\u4ef6", None))
#endif // QT_CONFIG(tooltip)
        self.edit_rule_btn.setText(QCoreApplication.translate("MainWindow", u"\u7f16\u8f91", None))
        self.rules_file_label.setText(QCoreApplication.translate("MainWindow", u"\u89c4\u5219\u6587\u4ef6\uff08\u987a\u5e8f\u4ece\u4e0a\u5230\u4e0b\uff0c\u540e\u8005\u8986\u76d6\u524d\u8005\uff09", None))
#if QT_CONFIG(tooltip)
        self.rules_file_list.setToolTip(QCoreApplication.translate("MainWindow", u"\u5df2\u52a0\u8f7d\u7684\u89c4\u5219\u6587\u4ef6\uff0c\u5217\u8868\u987a\u5e8f\u4ee3\u8868\u4f18\u5148\u7ea7\uff08\u4ece\u4f4e\u5230\u9ad8\uff09", None))
#endif // QT_CONFIG(tooltip)
        ___qtreewidgetitem = self.rules_tree.headerItem()
        ___qtreewidgetitem.setText(2, QCoreApplication.translate("MainWindow", u"\u6269\u5c55\u540d", None));
        ___qtreewidgetitem.setText(1, QCoreApplication.translate("MainWindow", u"\u4e25\u91cd\u7b49\u7ea7", None));
        ___qtreewidgetitem.setText(0, QCoreApplication.translate("MainWindow", u"\u89c4\u5219\u540d", None));
        self.history_label.setText(QCoreApplication.translate("MainWindow", u"\u626b\u63cf\u5386\u53f2\uff08\u53cc\u51fb\u5feb\u901f\u9009\u62e9\uff09", None))
#if QT_CONFIG(tooltip)
        self.history_list.setToolTip(QCoreApplication.translate("MainWindow", u"\u6700\u8fd1\u626b\u63cf\u8fc7\u7684\u8def\u5f84\uff0c\u53cc\u51fb\u53ef\u5feb\u901f\u9009\u62e9", None))
#endif // QT_CONFIG(tooltip)
        self.file_menu.setTitle(QCoreApplication.translate("MainWindow", u"\u6587\u4ef6(&F)", None))
        self.scan_menu.setTitle(QCoreApplication.translate("MainWindow", u"\u626b\u63cf(&S)", None))
        self.tools_menu.setTitle(QCoreApplication.translate("MainWindow", u"\u5de5\u5177(&T)", None))
        self.help_menu.setTitle(QCoreApplication.translate("MainWindow", u"\u5e2e\u52a9(&H)", None))
    # retranslateUi

