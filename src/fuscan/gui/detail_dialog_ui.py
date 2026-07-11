# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'detail_dialog.ui'
##
## Created by: Qt User Interface Compiler version 5.15.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide2.QtCore import *
from PySide2.QtGui import *
from PySide2.QtWidgets import *


class Ui_HitDetailDialog(object):
    def setupUi(self, HitDetailDialog):
        if not HitDetailDialog.objectName():
            HitDetailDialog.setObjectName(u"HitDetailDialog")
        HitDetailDialog.resize(800, 600)
        self.main_layout = QVBoxLayout(HitDetailDialog)
        self.main_layout.setObjectName(u"main_layout")
        self.info_label = QLabel(HitDetailDialog)
        self.info_label.setObjectName(u"info_label")
        self.info_label.setStyleSheet(u"padding: 8px; background: #f5f5f5; border: 1px solid #ddd;")
        self.info_label.setTextFormat(Qt.RichText)
        self.info_label.setWordWrap(True)

        self.main_layout.addWidget(self.info_label)

        self.hits_title_label = QLabel(HitDetailDialog)
        self.hits_title_label.setObjectName(u"hits_title_label")

        self.main_layout.addWidget(self.hits_title_label)

        self.hits_table = QTableWidget(HitDetailDialog)
        if (self.hits_table.columnCount() < 3):
            self.hits_table.setColumnCount(3)
        __qtablewidgetitem = QTableWidgetItem()
        self.hits_table.setHorizontalHeaderItem(0, __qtablewidgetitem)
        __qtablewidgetitem1 = QTableWidgetItem()
        self.hits_table.setHorizontalHeaderItem(1, __qtablewidgetitem1)
        __qtablewidgetitem2 = QTableWidgetItem()
        self.hits_table.setHorizontalHeaderItem(2, __qtablewidgetitem2)
        self.hits_table.setObjectName(u"hits_table")
        self.hits_table.horizontalHeader().setStretchLastSection(True)
        self.hits_table.verticalHeader().setVisible(False)

        self.main_layout.addWidget(self.hits_table)

        self.preview_title_label = QLabel(HitDetailDialog)
        self.preview_title_label.setObjectName(u"preview_title_label")

        self.main_layout.addWidget(self.preview_title_label)

        self.preview = QTextEdit(HitDetailDialog)
        self.preview.setObjectName(u"preview")
        self.preview.setReadOnly(True)

        self.main_layout.addWidget(self.preview)

        self.nav_layout = QHBoxLayout()
        self.nav_layout.setObjectName(u"nav_layout")
        self.nav_title_label = QLabel(HitDetailDialog)
        self.nav_title_label.setObjectName(u"nav_title_label")

        self.nav_layout.addWidget(self.nav_title_label)

        self.prev_btn = QPushButton(HitDetailDialog)
        self.prev_btn.setObjectName(u"prev_btn")

        self.nav_layout.addWidget(self.prev_btn)

        self.next_btn = QPushButton(HitDetailDialog)
        self.next_btn.setObjectName(u"next_btn")

        self.nav_layout.addWidget(self.next_btn)

        self.nav_label = QLabel(HitDetailDialog)
        self.nav_label.setObjectName(u"nav_label")

        self.nav_layout.addWidget(self.nav_label)

        self.nav_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.nav_layout.addItem(self.nav_spacer)


        self.main_layout.addLayout(self.nav_layout)

        self.btn_layout = QHBoxLayout()
        self.btn_layout.setObjectName(u"btn_layout")
        self.btn_spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.btn_layout.addItem(self.btn_spacer)

        self.close_btn = QPushButton(HitDetailDialog)
        self.close_btn.setObjectName(u"close_btn")

        self.btn_layout.addWidget(self.close_btn)


        self.main_layout.addLayout(self.btn_layout)


        self.retranslateUi(HitDetailDialog)
        self.close_btn.clicked.connect(HitDetailDialog.accept)

        QMetaObject.connectSlotsByName(HitDetailDialog)
    # setupUi

    def retranslateUi(self, HitDetailDialog):
        HitDetailDialog.setWindowTitle(QCoreApplication.translate("HitDetailDialog", u"\u547d\u4e2d\u8be6\u60c5", None))
        self.info_label.setText("")
        self.hits_title_label.setText(QCoreApplication.translate("HitDetailDialog", u"\u547d\u4e2d\u89c4\u5219:", None))
        ___qtablewidgetitem = self.hits_table.horizontalHeaderItem(0)
        ___qtablewidgetitem.setText(QCoreApplication.translate("HitDetailDialog", u"\u89c4\u5219\u540d", None));
        ___qtablewidgetitem1 = self.hits_table.horizontalHeaderItem(1)
        ___qtablewidgetitem1.setText(QCoreApplication.translate("HitDetailDialog", u"\u4e25\u91cd\u7b49\u7ea7", None));
        ___qtablewidgetitem2 = self.hits_table.horizontalHeaderItem(2)
        ___qtablewidgetitem2.setText(QCoreApplication.translate("HitDetailDialog", u"\u8be6\u60c5", None));
        self.preview_title_label.setText(QCoreApplication.translate("HitDetailDialog", u"\u5185\u5bb9\u9884\u89c8 (\u5173\u952e\u8bcd\u9ad8\u4eae):", None))
        self.nav_title_label.setText(QCoreApplication.translate("HitDetailDialog", u"\u547d\u4e2d\u5b9a\u4f4d:", None))
#if QT_CONFIG(tooltip)
        self.prev_btn.setToolTip(QCoreApplication.translate("HitDetailDialog", u"\u8df3\u8f6c\u5230\u4e0a\u4e00\u4e2a\u547d\u4e2d\u4f4d\u7f6e", None))
#endif // QT_CONFIG(tooltip)
        self.prev_btn.setText(QCoreApplication.translate("HitDetailDialog", u"\u4e0a\u4e00\u4e2a", None))
#if QT_CONFIG(tooltip)
        self.next_btn.setToolTip(QCoreApplication.translate("HitDetailDialog", u"\u8df3\u8f6c\u5230\u4e0b\u4e00\u4e2a\u547d\u4e2d\u4f4d\u7f6e", None))
#endif // QT_CONFIG(tooltip)
        self.next_btn.setText(QCoreApplication.translate("HitDetailDialog", u"\u4e0b\u4e00\u4e2a", None))
        self.nav_label.setText(QCoreApplication.translate("HitDetailDialog", u"0 / 0", None))
        self.close_btn.setText(QCoreApplication.translate("HitDetailDialog", u"\u5173\u95ed", None))
    # retranslateUi

