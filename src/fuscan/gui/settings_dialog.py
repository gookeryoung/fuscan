"""设置对话框。

使用 QTabWidget 实现多页面切换，避免设置项过于臃肿：

1. 扫描设置：最大工作线程数、最大扫描深度、是否扫描压缩包
2. 通用设置：是否包含网络映射盘、是否启用内置规则

对话框读取当前配置，用户修改后点击确定保存并应用。
"""

from __future__ import annotations

try:
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import (
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QGroupBox,
        QLineEdit,
        QPlainTextEdit,
        QSpinBox,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QGroupBox,
        QLineEdit,
        QPlainTextEdit,
        QSpinBox,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

from fuscan.config import Config

__all__ = ["SettingsDialog"]


class SettingsDialog(QDialog):
    """设置对话框，多页面 Tab 形式展示。"""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._setup_ui()
        self._load_config()

    def _setup_ui(self) -> None:
        """构建 UI：QTabWidget + 两个页面 + 按钮组。"""
        self.setWindowTitle("设置")
        self.setMinimumSize(500, 460)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)

        tab_widget = QTabWidget(self)
        tab_widget.setObjectName("settings_tab_widget")

        scan_page = self._build_scan_settings_page()
        general_page = self._build_general_settings_page()

        tab_widget.addTab(scan_page, "扫描设置")
        tab_widget.addTab(general_page, "通用设置")

        main_layout.addWidget(tab_widget)

        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal,
            self,
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def _build_scan_settings_page(self) -> QWidget:
        """构建扫描设置页面。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(8, 8, 8, 8)

        workers_group = QGroupBox("扫描线程")
        workers_layout = QFormLayout(workers_group)
        workers_layout.setSpacing(8)

        self._max_workers_spin = QSpinBox(workers_group)
        self._max_workers_spin.setRange(1, 32)
        self._max_workers_spin.setToolTip("扫描时使用的最大线程数")
        workers_layout.addRow("最大工作线程数:", self._max_workers_spin)

        depth_group = QGroupBox("扫描深度")
        depth_layout = QFormLayout(depth_group)
        depth_layout.setSpacing(8)

        self._max_depth_spin = QSpinBox(depth_group)
        self._max_depth_spin.setRange(0, 999)
        self._max_depth_spin.setSpecialValueText("无限制")
        self._max_depth_spin.setToolTip("0 表示无限制")
        depth_layout.addRow("最大扫描深度:", self._max_depth_spin)

        options_group = QGroupBox("扫描选项")
        options_layout = QVBoxLayout(options_group)
        options_layout.setSpacing(8)

        self._scan_archives_check = QCheckBox("扫描压缩包（ZIP/RAR）", options_group)
        self._scan_archives_check.setToolTip("扫描压缩文件内的文件内容")
        options_layout.addWidget(self._scan_archives_check)

        ignore_group = QGroupBox("忽略项")
        ignore_layout = QFormLayout(ignore_group)
        ignore_layout.setSpacing(8)

        self._ignore_dirs_edit = QPlainTextEdit(ignore_group)
        self._ignore_dirs_edit.setPlaceholderText("一行一个目录名（大小写不敏感）\n如：.git\n    node_modules")
        self._ignore_dirs_edit.setMaximumHeight(80)

        self._ignore_extensions_edit = QPlainTextEdit(ignore_group)
        self._ignore_extensions_edit.setPlaceholderText("一行一个扩展名（不含点）\n如：pyc\n    exe")
        self._ignore_extensions_edit.setMaximumHeight(80)

        ignore_layout.addRow("忽略目录:", self._ignore_dirs_edit)
        ignore_layout.addRow("忽略扩展名:", self._ignore_extensions_edit)

        layout.addWidget(workers_group)
        layout.addWidget(depth_group)
        layout.addWidget(options_group)
        layout.addWidget(ignore_group)
        layout.addStretch()

        return page

    def _build_general_settings_page(self) -> QWidget:
        """构建通用设置页面。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(8, 8, 8, 8)

        drive_group = QGroupBox("盘符扫描")
        drive_layout = QVBoxLayout(drive_group)
        drive_layout.setSpacing(8)

        self._include_network_check = QCheckBox("包含网络映射盘", drive_group)
        self._include_network_check.setToolTip("全盘扫描和盘符选择时包含网络驱动器")
        drive_layout.addWidget(self._include_network_check)

        rules_group = QGroupBox("规则设置")
        rules_layout = QVBoxLayout(rules_group)
        rules_layout.setSpacing(8)

        self._use_builtin_check = QCheckBox("启用内置通用规则", rules_group)
        self._use_builtin_check.setToolTip("启用随包分发的安全扫描规则")
        rules_layout.addWidget(self._use_builtin_check)

        cache_group = QGroupBox("缓存设置")
        cache_layout = QFormLayout(cache_group)
        cache_layout.setSpacing(8)

        self._cache_enabled_check = QCheckBox("启用扫描结果缓存", cache_group)
        self._cache_enabled_check.setToolTip("基于内容哈希跳过未变化文件，提升二次扫描速度；禁用后每次全量扫描")
        cache_layout.addRow(self._cache_enabled_check)

        self._cache_path_edit = QLineEdit(cache_group)
        self._cache_path_edit.setPlaceholderText("留空使用默认路径 ~/.fuscan/cache.db")
        self._cache_path_edit.setToolTip("自定义缓存数据库路径")
        cache_layout.addRow("缓存路径:", self._cache_path_edit)

        layout.addWidget(drive_group)
        layout.addWidget(rules_group)
        layout.addWidget(cache_group)
        layout.addStretch()

        return page

    def _load_config(self) -> None:
        """加载当前配置到控件。"""
        self._max_workers_spin.setValue(self._config.max_workers)
        self._max_depth_spin.setValue(self._config.max_depth or 0)
        self._scan_archives_check.setChecked(self._config.scan_archives)
        self._include_network_check.setChecked(self._config.include_network_drives)
        self._use_builtin_check.setChecked(self._config.use_builtin)
        self._ignore_dirs_edit.setPlainText("\n".join(self._config.ignore_dirs))
        self._ignore_extensions_edit.setPlainText("\n".join(self._config.ignore_extensions))
        self._cache_enabled_check.setChecked(self._config.cache_enabled)
        self._cache_path_edit.setText(self._config.cache_path or "")

    def _save_config(self) -> None:
        """将控件值保存到配置。"""
        self._config.max_workers = self._max_workers_spin.value()
        depth = self._max_depth_spin.value()
        self._config.max_depth = depth if depth > 0 else None
        self._config.scan_archives = self._scan_archives_check.isChecked()
        self._config.include_network_drives = self._include_network_check.isChecked()
        self._config.use_builtin = self._use_builtin_check.isChecked()
        self._config.ignore_dirs = [
            line.strip() for line in self._ignore_dirs_edit.toPlainText().splitlines() if line.strip()
        ]
        self._config.ignore_extensions = [
            line.strip() for line in self._ignore_extensions_edit.toPlainText().splitlines() if line.strip()
        ]
        self._config.cache_enabled = self._cache_enabled_check.isChecked()
        path_text = self._cache_path_edit.text().strip()
        self._config.cache_path = path_text or None

    def _on_accept(self) -> None:
        """确定按钮：保存配置并关闭对话框。"""
        self._save_config()
        self.accept()

    def get_config(self) -> Config:
        """获取当前对话框中的配置。"""
        return self._config
