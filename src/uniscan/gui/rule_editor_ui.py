# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'rule_editor.ui'
##
## Created by: Qt User Interface Compiler version 5.15.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide2.QtCore import *
from PySide2.QtGui import *
from PySide2.QtWidgets import *


class Ui_RuleEditorDialog(object):
    def setupUi(self, RuleEditorDialog):
        if not RuleEditorDialog.objectName():
            RuleEditorDialog.setObjectName(u"RuleEditorDialog")
        RuleEditorDialog.resize(700, 500)
        self.main_layout = QVBoxLayout(RuleEditorDialog)
        self.main_layout.setObjectName(u"main_layout")
        self.file_layout = QHBoxLayout()
        self.file_layout.setObjectName(u"file_layout")
        self.file_label = QLabel(RuleEditorDialog)
        self.file_label.setObjectName(u"file_label")

        self.file_layout.addWidget(self.file_label)

        self.file_combo = QComboBox(RuleEditorDialog)
        self.file_combo.setObjectName(u"file_combo")

        self.file_layout.addWidget(self.file_combo)


        self.main_layout.addLayout(self.file_layout)

        self.empty_label = QLabel(RuleEditorDialog)
        self.empty_label.setObjectName(u"empty_label")
        self.empty_label.setVisible(False)

        self.main_layout.addWidget(self.empty_label)

        self.editor = QTextEdit(RuleEditorDialog)
        self.editor.setObjectName(u"editor")
        self.editor.setStyleSheet(u"font-family: Consolas, 'Courier New', monospace; font-size: 13px;")

        self.main_layout.addWidget(self.editor)

        self.btn_layout = QHBoxLayout()
        self.btn_layout.setObjectName(u"btn_layout")
        self.reload_btn = QPushButton(RuleEditorDialog)
        self.reload_btn.setObjectName(u"reload_btn")

        self.btn_layout.addWidget(self.reload_btn)

        self.btn_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.btn_layout.addItem(self.btn_spacer)

        self.save_btn = QPushButton(RuleEditorDialog)
        self.save_btn.setObjectName(u"save_btn")

        self.btn_layout.addWidget(self.save_btn)

        self.close_btn = QPushButton(RuleEditorDialog)
        self.close_btn.setObjectName(u"close_btn")

        self.btn_layout.addWidget(self.close_btn)


        self.main_layout.addLayout(self.btn_layout)


        self.retranslateUi(RuleEditorDialog)
        self.close_btn.clicked.connect(RuleEditorDialog.accept)

        QMetaObject.connectSlotsByName(RuleEditorDialog)
    # setupUi

    def retranslateUi(self, RuleEditorDialog):
        RuleEditorDialog.setWindowTitle(QCoreApplication.translate("RuleEditorDialog", u"\u89c4\u5219\u7f16\u8f91\u5668", None))
        self.file_label.setText(QCoreApplication.translate("RuleEditorDialog", u"\u89c4\u5219\u6587\u4ef6:", None))
        self.empty_label.setText(QCoreApplication.translate("RuleEditorDialog", u"\uff08\u672a\u52a0\u8f7d\u4efb\u4f55\u89c4\u5219\u6587\u4ef6\uff09", None))
        self.editor.setFontFamily(QCoreApplication.translate("RuleEditorDialog", u"Consolas", None))
#if QT_CONFIG(tooltip)
        self.reload_btn.setToolTip(QCoreApplication.translate("RuleEditorDialog", u"\u653e\u5f03\u4fee\u6539\uff0c\u4ece\u6587\u4ef6\u91cd\u65b0\u52a0\u8f7d\u5185\u5bb9", None))
#endif // QT_CONFIG(tooltip)
        self.reload_btn.setText(QCoreApplication.translate("RuleEditorDialog", u"\u91cd\u65b0\u52a0\u8f7d", None))
#if QT_CONFIG(tooltip)
        self.save_btn.setToolTip(QCoreApplication.translate("RuleEditorDialog", u"\u4fdd\u5b58\u6587\u4ef6\u5e76\u91cd\u65b0\u52a0\u8f7d\u89c4\u5219\u96c6", None))
#endif // QT_CONFIG(tooltip)
        self.save_btn.setText(QCoreApplication.translate("RuleEditorDialog", u"\u4fdd\u5b58\u5e76\u5e94\u7528", None))
        self.close_btn.setText(QCoreApplication.translate("RuleEditorDialog", u"\u5173\u95ed", None))
    # retranslateUi

