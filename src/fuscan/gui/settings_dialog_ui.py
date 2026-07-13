"""设置对话框 UI 定义。

由 ``settings_dialog.ui`` 手工同步而来，供 ``SettingsDialog`` 通过
``Ui_SettingsDialog().setupUi(self)`` 装配界面。业务逻辑位于
``settings_dialog.py``，本模块仅负责部件创建与布局组装。

与 ``rule_editor_ui.py`` 保持一致：使用通配符导入以匹配 pyside-uic 生成风格。
"""

try:
    from PySide2.QtCore import *
    from PySide2.QtWidgets import *
except ImportError:  # pragma: no cover
    from PySide6.QtCore import *
    from PySide6.QtWidgets import *


class Ui_SettingsDialog:
    """设置对话框 UI 装配类，对应 ``settings_dialog.ui``。"""

    def setupUi(self, SettingsDialog: QDialog) -> None:
        """装配设置对话框界面。"""
        if not SettingsDialog.objectName():
            SettingsDialog.setObjectName("SettingsDialog")
        SettingsDialog.resize(500, 460)
        SettingsDialog.setMinimumSize(QSize(500, 460))

        # 主布局
        self.main_layout = QVBoxLayout(SettingsDialog)
        self.main_layout.setObjectName("main_layout")
        self.main_layout.setSpacing(12)
        self.main_layout.setContentsMargins(16, 16, 16, 16)

        # Tab 容器
        self.settings_tab_widget = QTabWidget(SettingsDialog)
        self.settings_tab_widget.setObjectName("settings_tab_widget")

        # ---------- 扫描设置页 ----------
        self.scan_page = QWidget()
        self.scan_page.setObjectName("scan_page")
        self.scan_page_layout = QVBoxLayout(self.scan_page)
        self.scan_page_layout.setObjectName("scan_page_layout")
        self.scan_page_layout.setSpacing(12)
        self.scan_page_layout.setContentsMargins(8, 8, 8, 8)

        # 扫描线程分组
        self.workers_group = QGroupBox(self.scan_page)
        self.workers_group.setObjectName("workers_group")
        self.workers_layout = QFormLayout(self.workers_group)
        self.workers_layout.setObjectName("workers_layout")
        self.workers_layout.setSpacing(8)

        self.max_workers_label = QLabel(self.workers_group)
        self.max_workers_label.setObjectName("max_workers_label")
        self.workers_layout.setWidget(0, QFormLayout.LabelRole, self.max_workers_label)

        self.max_workers_spin = QSpinBox(self.workers_group)
        self.max_workers_spin.setObjectName("max_workers_spin")
        self.max_workers_spin.setMinimum(1)
        self.max_workers_spin.setMaximum(32)
        self.workers_layout.setWidget(0, QFormLayout.FieldRole, self.max_workers_spin)

        self.scan_page_layout.addWidget(self.workers_group)

        # 扫描深度分组
        self.depth_group = QGroupBox(self.scan_page)
        self.depth_group.setObjectName("depth_group")
        self.depth_layout = QFormLayout(self.depth_group)
        self.depth_layout.setObjectName("depth_layout")
        self.depth_layout.setSpacing(8)

        self.max_depth_label = QLabel(self.depth_group)
        self.max_depth_label.setObjectName("max_depth_label")
        self.depth_layout.setWidget(0, QFormLayout.LabelRole, self.max_depth_label)

        self.max_depth_spin = QSpinBox(self.depth_group)
        self.max_depth_spin.setObjectName("max_depth_spin")
        self.max_depth_spin.setMinimum(0)
        self.max_depth_spin.setMaximum(999)
        self.max_depth_spin.setSpecialValueText("无限制")
        self.depth_layout.setWidget(0, QFormLayout.FieldRole, self.max_depth_spin)

        self.scan_page_layout.addWidget(self.depth_group)

        # 扫描选项分组
        self.options_group = QGroupBox(self.scan_page)
        self.options_group.setObjectName("options_group")
        self.options_layout = QVBoxLayout(self.options_group)
        self.options_layout.setObjectName("options_layout")
        self.options_layout.setSpacing(8)

        self.scan_archives_check = QCheckBox(self.options_group)
        self.scan_archives_check.setObjectName("scan_archives_check")
        self.options_layout.addWidget(self.scan_archives_check)

        self.scan_page_layout.addWidget(self.options_group)

        # 忽略项分组
        self.ignore_group = QGroupBox(self.scan_page)
        self.ignore_group.setObjectName("ignore_group")
        self.ignore_layout = QFormLayout(self.ignore_group)
        self.ignore_layout.setObjectName("ignore_layout")
        self.ignore_layout.setSpacing(8)

        self.ignore_dirs_label = QLabel(self.ignore_group)
        self.ignore_dirs_label.setObjectName("ignore_dirs_label")
        self.ignore_layout.setWidget(0, QFormLayout.LabelRole, self.ignore_dirs_label)

        self.ignore_dirs_edit = QPlainTextEdit(self.ignore_group)
        self.ignore_dirs_edit.setObjectName("ignore_dirs_edit")
        self.ignore_dirs_edit.setMaximumHeight(80)
        self.ignore_layout.setWidget(0, QFormLayout.FieldRole, self.ignore_dirs_edit)

        self.ignore_extensions_label = QLabel(self.ignore_group)
        self.ignore_extensions_label.setObjectName("ignore_extensions_label")
        self.ignore_layout.setWidget(1, QFormLayout.LabelRole, self.ignore_extensions_label)

        self.ignore_extensions_edit = QPlainTextEdit(self.ignore_group)
        self.ignore_extensions_edit.setObjectName("ignore_extensions_edit")
        self.ignore_extensions_edit.setMaximumHeight(80)
        self.ignore_layout.setWidget(1, QFormLayout.FieldRole, self.ignore_extensions_edit)

        self.scan_page_layout.addWidget(self.ignore_group)

        self.scan_page_spacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.scan_page_layout.addItem(self.scan_page_spacer)

        self.settings_tab_widget.addTab(self.scan_page, "")

        # ---------- 通用设置页 ----------
        self.general_page = QWidget()
        self.general_page.setObjectName("general_page")
        self.general_page_layout = QVBoxLayout(self.general_page)
        self.general_page_layout.setObjectName("general_page_layout")
        self.general_page_layout.setSpacing(12)
        self.general_page_layout.setContentsMargins(8, 8, 8, 8)

        # 盘符扫描分组
        self.drive_group = QGroupBox(self.general_page)
        self.drive_group.setObjectName("drive_group")
        self.drive_layout = QVBoxLayout(self.drive_group)
        self.drive_layout.setObjectName("drive_layout")
        self.drive_layout.setSpacing(8)

        self.include_network_check = QCheckBox(self.drive_group)
        self.include_network_check.setObjectName("include_network_check")
        self.drive_layout.addWidget(self.include_network_check)

        self.general_page_layout.addWidget(self.drive_group)

        # 规则设置分组
        self.rules_group = QGroupBox(self.general_page)
        self.rules_group.setObjectName("rules_group")
        self.rules_layout = QVBoxLayout(self.rules_group)
        self.rules_layout.setObjectName("rules_layout")
        self.rules_layout.setSpacing(8)

        self.use_builtin_check = QCheckBox(self.rules_group)
        self.use_builtin_check.setObjectName("use_builtin_check")
        self.rules_layout.addWidget(self.use_builtin_check)

        self.general_page_layout.addWidget(self.rules_group)

        # 缓存设置分组
        self.cache_group = QGroupBox(self.general_page)
        self.cache_group.setObjectName("cache_group")
        self.cache_layout = QFormLayout(self.cache_group)
        self.cache_layout.setObjectName("cache_layout")
        self.cache_layout.setSpacing(8)

        self.cache_enabled_check = QCheckBox(self.cache_group)
        self.cache_enabled_check.setObjectName("cache_enabled_check")
        self.cache_layout.setWidget(0, QFormLayout.SpanningRole, self.cache_enabled_check)

        self.cache_path_label = QLabel(self.cache_group)
        self.cache_path_label.setObjectName("cache_path_label")
        self.cache_layout.setWidget(1, QFormLayout.LabelRole, self.cache_path_label)

        self.cache_path_edit = QLineEdit(self.cache_group)
        self.cache_path_edit.setObjectName("cache_path_edit")
        self.cache_layout.setWidget(1, QFormLayout.FieldRole, self.cache_path_edit)

        self.general_page_layout.addWidget(self.cache_group)

        self.general_page_spacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.general_page_layout.addItem(self.general_page_spacer)

        self.settings_tab_widget.addTab(self.general_page, "")

        self.main_layout.addWidget(self.settings_tab_widget)

        # ---------- 按钮组 ----------
        self.button_box = QDialogButtonBox(SettingsDialog)
        self.button_box.setObjectName("button_box")
        self.button_box.setStandardButtons(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        self.main_layout.addWidget(self.button_box)

        self.retranslateUi(SettingsDialog)
        QMetaObject.connectSlotsByName(SettingsDialog)

    def retranslateUi(self, SettingsDialog: QDialog) -> None:
        """填充所有可翻译文本（与 .ui 中的 string 属性一一对应）。"""
        SettingsDialog.setWindowTitle(QCoreApplication.translate("SettingsDialog", "设置", None))
        self.workers_group.setTitle(QCoreApplication.translate("SettingsDialog", "扫描线程", None))
        self.max_workers_label.setText(
            QCoreApplication.translate("SettingsDialog", "最大工作线程数:", None)
        )
        self.max_workers_spin.setToolTip(
            QCoreApplication.translate("SettingsDialog", "扫描时使用的最大线程数", None)
        )
        self.depth_group.setTitle(QCoreApplication.translate("SettingsDialog", "扫描深度", None))
        self.max_depth_label.setText(QCoreApplication.translate("SettingsDialog", "最大扫描深度:", None))
        self.max_depth_spin.setToolTip(QCoreApplication.translate("SettingsDialog", "0 表示无限制", None))
        self.options_group.setTitle(QCoreApplication.translate("SettingsDialog", "扫描选项", None))
        self.scan_archives_check.setToolTip(
            QCoreApplication.translate("SettingsDialog", "扫描压缩文件内的文件内容", None)
        )
        self.scan_archives_check.setText(
            QCoreApplication.translate("SettingsDialog", "扫描压缩包（ZIP/RAR）", None)
        )
        self.ignore_group.setTitle(QCoreApplication.translate("SettingsDialog", "忽略项", None))
        self.ignore_dirs_label.setText(QCoreApplication.translate("SettingsDialog", "忽略目录:", None))
        self.ignore_dirs_edit.setPlaceholderText(
            QCoreApplication.translate(
                "SettingsDialog",
                "一行一个目录名（大小写不敏感）\n如：.git\n    node_modules",
                None,
            )
        )
        self.ignore_extensions_label.setText(
            QCoreApplication.translate("SettingsDialog", "忽略扩展名:", None)
        )
        self.ignore_extensions_edit.setPlaceholderText(
            QCoreApplication.translate(
                "SettingsDialog",
                "一行一个扩展名（不含点）\n如：pyc\n    exe",
                None,
            )
        )
        self.settings_tab_widget.setTabText(
            self.settings_tab_widget.indexOf(self.scan_page),
            QCoreApplication.translate("SettingsDialog", "扫描设置", None),
        )
        self.drive_group.setTitle(QCoreApplication.translate("SettingsDialog", "盘符扫描", None))
        self.include_network_check.setToolTip(
            QCoreApplication.translate("SettingsDialog", "全盘扫描和盘符选择时包含网络驱动器", None)
        )
        self.include_network_check.setText(
            QCoreApplication.translate("SettingsDialog", "包含网络映射盘", None)
        )
        self.rules_group.setTitle(QCoreApplication.translate("SettingsDialog", "规则设置", None))
        self.use_builtin_check.setToolTip(
            QCoreApplication.translate("SettingsDialog", "启用随包分发的安全扫描规则", None)
        )
        self.use_builtin_check.setText(
            QCoreApplication.translate("SettingsDialog", "启用内置通用规则", None)
        )
        self.cache_group.setTitle(QCoreApplication.translate("SettingsDialog", "缓存设置", None))
        self.cache_enabled_check.setToolTip(
            QCoreApplication.translate(
                "SettingsDialog",
                "基于内容哈希跳过未变化文件，提升二次扫描速度；禁用后每次全量扫描",
                None,
            )
        )
        self.cache_enabled_check.setText(
            QCoreApplication.translate("SettingsDialog", "启用扫描结果缓存", None)
        )
        self.cache_path_label.setText(QCoreApplication.translate("SettingsDialog", "缓存路径:", None))
        self.cache_path_edit.setToolTip(
            QCoreApplication.translate("SettingsDialog", "自定义缓存数据库路径", None)
        )
        self.cache_path_edit.setPlaceholderText(
            QCoreApplication.translate("SettingsDialog", "留空使用默认路径 ~/.fuscan/cache.db", None)
        )
        self.settings_tab_widget.setTabText(
            self.settings_tab_widget.indexOf(self.general_page),
            QCoreApplication.translate("SettingsDialog", "通用设置", None),
        )
