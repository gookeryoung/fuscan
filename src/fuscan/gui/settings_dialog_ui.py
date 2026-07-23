# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'settings_dialog.ui'
##
## Created by: Qt User Interface Compiler version 5.15.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide2.QtCore import *
from PySide2.QtGui import *
from PySide2.QtWidgets import *

import resources_rc

class Ui_SettingsDialog(object):
    def setupUi(self, SettingsDialog):
        if not SettingsDialog.objectName():
            SettingsDialog.setObjectName(u"SettingsDialog")
        SettingsDialog.resize(640, 620)
        SettingsDialog.setMinimumSize(QSize(640, 620))
        icon = QIcon()
        icon.addFile(u":/assets/icons/settings.svg", QSize(), QIcon.Normal, QIcon.Off)
        SettingsDialog.setWindowIcon(icon)
        self.main_layout = QVBoxLayout(SettingsDialog)
        self.main_layout.setSpacing(12)
        self.main_layout.setObjectName(u"main_layout")
        self.main_layout.setContentsMargins(16, 16, 16, 16)
        self.settings_tab_widget = QTabWidget(SettingsDialog)
        self.settings_tab_widget.setObjectName(u"settings_tab_widget")
        self.general_page = QWidget()
        self.general_page.setObjectName(u"general_page")
        self.general_page_layout = QVBoxLayout(self.general_page)
        self.general_page_layout.setSpacing(12)
        self.general_page_layout.setObjectName(u"general_page_layout")
        self.general_page_layout.setContentsMargins(8, 8, 8, 8)
        self.drive_group = QGroupBox(self.general_page)
        self.drive_group.setObjectName(u"drive_group")
        self.drive_layout = QVBoxLayout(self.drive_group)
        self.drive_layout.setSpacing(8)
        self.drive_layout.setObjectName(u"drive_layout")
        self.include_network_check = QCheckBox(self.drive_group)
        self.include_network_check.setObjectName(u"include_network_check")

        self.drive_layout.addWidget(self.include_network_check)


        self.general_page_layout.addWidget(self.drive_group)

        self.rules_group = QGroupBox(self.general_page)
        self.rules_group.setObjectName(u"rules_group")
        self.rules_layout = QVBoxLayout(self.rules_group)
        self.rules_layout.setSpacing(8)
        self.rules_layout.setObjectName(u"rules_layout")
        self.use_builtin_check = QCheckBox(self.rules_group)
        self.use_builtin_check.setObjectName(u"use_builtin_check")

        self.rules_layout.addWidget(self.use_builtin_check)


        self.general_page_layout.addWidget(self.rules_group)

        self.cache_group = QGroupBox(self.general_page)
        self.cache_group.setObjectName(u"cache_group")
        self.cache_layout = QFormLayout(self.cache_group)
        self.cache_layout.setObjectName(u"cache_layout")
        self.cache_layout.setHorizontalSpacing(8)
        self.cache_layout.setVerticalSpacing(8)
        self.cache_enabled_check = QCheckBox(self.cache_group)
        self.cache_enabled_check.setObjectName(u"cache_enabled_check")

        self.cache_layout.setWidget(0, QFormLayout.SpanningRole, self.cache_enabled_check)

        self.cache_path_label = QLabel(self.cache_group)
        self.cache_path_label.setObjectName(u"cache_path_label")

        self.cache_layout.setWidget(1, QFormLayout.LabelRole, self.cache_path_label)

        self.cache_path_edit = QLineEdit(self.cache_group)
        self.cache_path_edit.setObjectName(u"cache_path_edit")

        self.cache_layout.setWidget(1, QFormLayout.FieldRole, self.cache_path_edit)


        self.general_page_layout.addWidget(self.cache_group)

        self.staging_group = QGroupBox(self.general_page)
        self.staging_group.setObjectName(u"staging_group")
        self.staging_layout = QFormLayout(self.staging_group)
        self.staging_layout.setObjectName(u"staging_layout")
        self.staging_layout.setHorizontalSpacing(8)
        self.staging_layout.setVerticalSpacing(8)
        self.staging_dir_label = QLabel(self.staging_group)
        self.staging_dir_label.setObjectName(u"staging_dir_label")

        self.staging_layout.setWidget(0, QFormLayout.LabelRole, self.staging_dir_label)

        self.staging_dir_row_layout = QHBoxLayout()
        self.staging_dir_row_layout.setSpacing(4)
        self.staging_dir_row_layout.setObjectName(u"staging_dir_row_layout")
        self.staging_dir_edit = QLineEdit(self.staging_group)
        self.staging_dir_edit.setObjectName(u"staging_dir_edit")

        self.staging_dir_row_layout.addWidget(self.staging_dir_edit)

        self.staging_dir_browse_btn = QPushButton(self.staging_group)
        self.staging_dir_browse_btn.setObjectName(u"staging_dir_browse_btn")

        self.staging_dir_row_layout.addWidget(self.staging_dir_browse_btn)


        self.staging_layout.setLayout(0, QFormLayout.FieldRole, self.staging_dir_row_layout)


        self.general_page_layout.addWidget(self.staging_group)

        self.general_page_spacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)

        self.general_page_layout.addItem(self.general_page_spacer)

        self.settings_tab_widget.addTab(self.general_page, "")
        self.scan_page = QWidget()
        self.scan_page.setObjectName(u"scan_page")
        self.scan_page_layout = QVBoxLayout(self.scan_page)
        self.scan_page_layout.setSpacing(12)
        self.scan_page_layout.setObjectName(u"scan_page_layout")
        self.scan_page_layout.setContentsMargins(8, 8, 8, 8)
        self.workers_group = QGroupBox(self.scan_page)
        self.workers_group.setObjectName(u"workers_group")
        self.workers_layout = QFormLayout(self.workers_group)
        self.workers_layout.setObjectName(u"workers_layout")
        self.workers_layout.setHorizontalSpacing(8)
        self.workers_layout.setVerticalSpacing(8)
        self.max_workers_label = QLabel(self.workers_group)
        self.max_workers_label.setObjectName(u"max_workers_label")

        self.workers_layout.setWidget(0, QFormLayout.LabelRole, self.max_workers_label)

        self.max_workers_spin = QSpinBox(self.workers_group)
        self.max_workers_spin.setObjectName(u"max_workers_spin")
        self.max_workers_spin.setMinimum(1)
        self.max_workers_spin.setMaximum(32)

        self.workers_layout.setWidget(0, QFormLayout.FieldRole, self.max_workers_spin)


        self.scan_page_layout.addWidget(self.workers_group)

        self.depth_group = QGroupBox(self.scan_page)
        self.depth_group.setObjectName(u"depth_group")
        self.depth_layout = QFormLayout(self.depth_group)
        self.depth_layout.setObjectName(u"depth_layout")
        self.depth_layout.setHorizontalSpacing(8)
        self.depth_layout.setVerticalSpacing(8)
        self.max_depth_label = QLabel(self.depth_group)
        self.max_depth_label.setObjectName(u"max_depth_label")

        self.depth_layout.setWidget(0, QFormLayout.LabelRole, self.max_depth_label)

        self.max_depth_spin = QSpinBox(self.depth_group)
        self.max_depth_spin.setObjectName(u"max_depth_spin")
        self.max_depth_spin.setMinimum(0)
        self.max_depth_spin.setMaximum(999)

        self.depth_layout.setWidget(0, QFormLayout.FieldRole, self.max_depth_spin)


        self.scan_page_layout.addWidget(self.depth_group)

        self.file_size_group = QGroupBox(self.scan_page)
        self.file_size_group.setObjectName(u"file_size_group")
        self.file_size_layout = QFormLayout(self.file_size_group)
        self.file_size_layout.setObjectName(u"file_size_layout")
        self.file_size_layout.setHorizontalSpacing(8)
        self.file_size_layout.setVerticalSpacing(8)
        self.max_file_size_label = QLabel(self.file_size_group)
        self.max_file_size_label.setObjectName(u"max_file_size_label")

        self.file_size_layout.setWidget(0, QFormLayout.LabelRole, self.max_file_size_label)

        self.max_file_size_spin = QSpinBox(self.file_size_group)
        self.max_file_size_spin.setObjectName(u"max_file_size_spin")
        self.max_file_size_spin.setMinimum(0)
        self.max_file_size_spin.setMaximum(4096)
        self.max_file_size_spin.setValue(100)

        self.file_size_layout.setWidget(0, QFormLayout.FieldRole, self.max_file_size_spin)


        self.scan_page_layout.addWidget(self.file_size_group)

        self.options_group = QGroupBox(self.scan_page)
        self.options_group.setObjectName(u"options_group")
        self.options_layout = QVBoxLayout(self.options_group)
        self.options_layout.setSpacing(8)
        self.options_layout.setObjectName(u"options_layout")
        self.scan_archives_check = QCheckBox(self.options_group)
        self.scan_archives_check.setObjectName(u"scan_archives_check")

        self.options_layout.addWidget(self.scan_archives_check)


        self.scan_page_layout.addWidget(self.options_group)

        self.scan_page_spacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)

        self.scan_page_layout.addItem(self.scan_page_spacer)

        self.settings_tab_widget.addTab(self.scan_page, "")

        self.main_layout.addWidget(self.settings_tab_widget)

        self.button_box = QDialogButtonBox(SettingsDialog)
        self.button_box.setObjectName(u"button_box")
        self.button_box.setStandardButtons(QDialogButtonBox.Cancel|QDialogButtonBox.Ok)

        self.main_layout.addWidget(self.button_box)


        self.retranslateUi(SettingsDialog)

        self.settings_tab_widget.setCurrentIndex(0)


        QMetaObject.connectSlotsByName(SettingsDialog)
    # setupUi

    def retranslateUi(self, SettingsDialog):
        SettingsDialog.setWindowTitle(QCoreApplication.translate("SettingsDialog", u"\u8bbe\u7f6e", None))
        self.drive_group.setTitle(QCoreApplication.translate("SettingsDialog", u"\u76d8\u7b26\u626b\u63cf", None))
#if QT_CONFIG(tooltip)
        self.include_network_check.setToolTip(QCoreApplication.translate("SettingsDialog", u"\u5168\u76d8\u626b\u63cf\u548c\u76d8\u7b26\u9009\u62e9\u65f6\u5305\u542b\u7f51\u7edc\u9a71\u52a8\u5668", None))
#endif // QT_CONFIG(tooltip)
        self.include_network_check.setText(QCoreApplication.translate("SettingsDialog", u"\u5305\u542b\u7f51\u7edc\u6620\u5c04\u76d8", None))
        self.rules_group.setTitle(QCoreApplication.translate("SettingsDialog", u"\u89c4\u5219\u8bbe\u7f6e", None))
#if QT_CONFIG(tooltip)
        self.use_builtin_check.setToolTip(QCoreApplication.translate("SettingsDialog", u"\u542f\u7528\u968f\u5305\u5206\u53d1\u7684\u5b89\u5168\u626b\u63cf\u89c4\u5219", None))
#endif // QT_CONFIG(tooltip)
        self.use_builtin_check.setText(QCoreApplication.translate("SettingsDialog", u"\u542f\u7528\u5185\u7f6e\u901a\u7528\u89c4\u5219", None))
        self.cache_group.setTitle(QCoreApplication.translate("SettingsDialog", u"\u7f13\u5b58\u8bbe\u7f6e", None))
#if QT_CONFIG(tooltip)
        self.cache_enabled_check.setToolTip(QCoreApplication.translate("SettingsDialog", u"\u57fa\u4e8e\u5185\u5bb9\u54c8\u5e0c\u8df3\u8fc7\u672a\u53d8\u5316\u6587\u4ef6\uff0c\u63d0\u5347\u4e8c\u6b21\u626b\u63cf\u901f\u5ea6\uff1b\u7981\u7528\u540e\u6bcf\u6b21\u5168\u91cf\u626b\u63cf", None))
#endif // QT_CONFIG(tooltip)
        self.cache_enabled_check.setText(QCoreApplication.translate("SettingsDialog", u"\u542f\u7528\u626b\u63cf\u7ed3\u679c\u7f13\u5b58", None))
        self.cache_path_label.setText(QCoreApplication.translate("SettingsDialog", u"\u7f13\u5b58\u8def\u5f84:", None))
#if QT_CONFIG(tooltip)
        self.cache_path_edit.setToolTip(QCoreApplication.translate("SettingsDialog", u"\u81ea\u5b9a\u4e49\u7f13\u5b58\u6570\u636e\u5e93\u8def\u5f84", None))
#endif // QT_CONFIG(tooltip)
        self.cache_path_edit.setPlaceholderText(QCoreApplication.translate("SettingsDialog", u"\u7559\u7a7a\u4f7f\u7528\u9ed8\u8ba4\u8def\u5f84 ~/.fuscan/cache.db", None))
        self.staging_group.setTitle(QCoreApplication.translate("SettingsDialog", u"\u6682\u5b58\u533a", None))
        self.staging_dir_label.setText(QCoreApplication.translate("SettingsDialog", u"\u6682\u5b58\u533a\u8def\u5f84:", None))
#if QT_CONFIG(tooltip)
        self.staging_dir_edit.setToolTip(QCoreApplication.translate("SettingsDialog", u"\u300c\u79fb\u52a8\u81f3\u6682\u5b58\u533a\u300d\u6309\u94ae\u5c06\u6587\u4ef6\u79fb\u52a8\u5230\u6b64\u76ee\u5f55\uff1b\u7559\u7a7a\u81ea\u52a8\u63a2\u6d4b\u5269\u4f59\u7a7a\u95f4\u6700\u5927\u7684\u76d8\u7b26\u4e0b .fuscan-cache", None))
#endif // QT_CONFIG(tooltip)
        self.staging_dir_edit.setPlaceholderText(QCoreApplication.translate("SettingsDialog", u"\u7559\u7a7a\u81ea\u52a8\u63a2\u6d4b\u5269\u4f59\u7a7a\u95f4\u6700\u5927\u7684\u76d8\u7b26\u4e0b .fuscan-cache", None))
        self.staging_dir_browse_btn.setText(QCoreApplication.translate("SettingsDialog", u"\u9009\u62e9...", None))
        self.settings_tab_widget.setTabText(self.settings_tab_widget.indexOf(self.general_page), QCoreApplication.translate("SettingsDialog", u"\u901a\u7528\u8bbe\u7f6e", None))
        self.workers_group.setTitle(QCoreApplication.translate("SettingsDialog", u"\u626b\u63cf\u7ebf\u7a0b", None))
        self.max_workers_label.setText(QCoreApplication.translate("SettingsDialog", u"\u6700\u5927\u5de5\u4f5c\u7ebf\u7a0b\u6570:", None))
#if QT_CONFIG(tooltip)
        self.max_workers_spin.setToolTip(QCoreApplication.translate("SettingsDialog", u"\u626b\u63cf\u65f6\u4f7f\u7528\u7684\u6700\u5927\u7ebf\u7a0b\u6570", None))
#endif // QT_CONFIG(tooltip)
        self.depth_group.setTitle(QCoreApplication.translate("SettingsDialog", u"\u626b\u63cf\u6df1\u5ea6", None))
        self.max_depth_label.setText(QCoreApplication.translate("SettingsDialog", u"\u6700\u5927\u626b\u63cf\u6df1\u5ea6:", None))
#if QT_CONFIG(tooltip)
        self.max_depth_spin.setToolTip(QCoreApplication.translate("SettingsDialog", u"0 \u8868\u793a\u65e0\u9650\u5236", None))
#endif // QT_CONFIG(tooltip)
        self.max_depth_spin.setSpecialValueText(QCoreApplication.translate("SettingsDialog", u"\u65e0\u9650\u5236", None))
        self.file_size_group.setTitle(QCoreApplication.translate("SettingsDialog", u"\u5927\u6587\u4ef6\u8df3\u8fc7", None))
        self.max_file_size_label.setText(QCoreApplication.translate("SettingsDialog", u"\u6587\u4ef6\u5927\u5c0f\u4e0a\u9650(MB):", None))
#if QT_CONFIG(tooltip)
        self.max_file_size_spin.setToolTip(QCoreApplication.translate("SettingsDialog", u"\u8df3\u8fc7\u5927\u4e8e\u6b64\u5927\u5c0f\u7684\u6587\u4ef6\uff0c\u907f\u514d\u5927\u6587\u4ef6\u8bfb\u53d6\u5bfc\u81f4\u5361\u6b7b\uff1b0 \u8868\u793a\u4e0d\u9650\u5236", None))
#endif // QT_CONFIG(tooltip)
        self.max_file_size_spin.setSpecialValueText(QCoreApplication.translate("SettingsDialog", u"\u4e0d\u9650\u5236", None))
        self.options_group.setTitle(QCoreApplication.translate("SettingsDialog", u"\u626b\u63cf\u9009\u9879", None))
#if QT_CONFIG(tooltip)
        self.scan_archives_check.setToolTip(QCoreApplication.translate("SettingsDialog", u"\u626b\u63cf\u538b\u7f29\u6587\u4ef6\u5185\u7684\u6587\u4ef6\u5185\u5bb9\uff08\u5f00\u542f\u540e\u81ea\u52a8\u653e\u884c\u5df2\u5ffd\u7565\u7684\u538b\u7f29\u5305\u6269\u5c55\u540d\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.scan_archives_check.setText(QCoreApplication.translate("SettingsDialog", u"\u626b\u63cf\u538b\u7f29\u5305\uff08ZIP/RAR/7Z\uff09", None))
        self.settings_tab_widget.setTabText(self.settings_tab_widget.indexOf(self.scan_page), QCoreApplication.translate("SettingsDialog", u"\u626b\u63cf\u8bbe\u7f6e", None))
    # retranslateUi

