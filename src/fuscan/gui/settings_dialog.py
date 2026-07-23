"""设置对话框。

使用 QTabWidget 实现多页面切换，避免设置项过于臃肿：

1. 通用设置：是否包含网络映射盘、是否启用内置规则、缓存设置、暂存区路径
2. 扫描设置：最大工作线程数、最大扫描深度、是否扫描压缩包、大文件跳过

忽略项（忽略目录/忽略扩展名）已移至主窗口配置页文件类型区右侧
（iter-79），通过 QTabWidget 切换，便于与文件类型勾选联动查看。

UI 装配委托给 ``Ui_SettingsDialog``（对应 ``settings_dialog.ui``），
本模块仅负责信号槽连接、配置加载与保存等业务逻辑。
"""

from __future__ import annotations

try:
    from PySide2.QtCore import Slot
    from PySide2.QtWidgets import QDialog, QFileDialog, QWidget
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Slot  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import QDialog, QFileDialog, QWidget  # pyrefly: ignore [missing-import]

from fuscan.config import Config
from fuscan.gui.settings_dialog_ui import Ui_SettingsDialog

__all__ = ["SettingsDialog"]


class SettingsDialog(QDialog, Ui_SettingsDialog):  # pyrefly: ignore [invalid-inheritance]
    """设置对话框，多页面 Tab 形式展示。"""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setupUi(self)

        self._configure_ui()
        self._load_config()

    def _configure_ui(self) -> None:
        """配置 .ui 无法静态表达的信号槽连接。"""
        self.button_box.accepted.connect(self._on_accept)
        self.button_box.rejected.connect(self.reject)

        self.staging_dir_browse_btn.clicked.connect(self._on_browse_staging_dir)

    def _load_config(self) -> None:
        """加载当前配置到控件。"""
        self.max_workers_spin.setValue(self.config.max_workers)
        self.max_depth_spin.setValue(self.config.max_depth or 0)
        # 字节转 MB（0 表示不限制，SpinBox 最小值 0 + specialValueText "不限制"）
        self.max_file_size_spin.setValue(self.config.max_file_size // (1024 * 1024))
        self.scan_archives_check.setChecked(self.config.scan_archives)
        self.include_network_check.setChecked(self.config.include_network_drives)
        self.use_builtin_check.setChecked(self.config.use_builtin)
        self.cache_enabled_check.setChecked(self.config.cache_enabled)
        self.cache_path_edit.setText(self.config.cache_path or "")
        self.staging_dir_edit.setText(self.config.staging_dir or "")

    def _save_config(self) -> None:
        """将控件值保存到配置。"""
        self.config.max_workers = self.max_workers_spin.value()

        depth = self.max_depth_spin.value()
        self.config.max_depth = depth if depth > 0 else None
        # MB 转 bytes（0 表示不限制，与 Scanner._normalize_max_file_size 语义一致）
        self.config.max_file_size = self.max_file_size_spin.value() * 1024 * 1024
        self.config.scan_archives = self.scan_archives_check.isChecked()
        self.config.include_network_drives = self.include_network_check.isChecked()
        self.config.use_builtin = self.use_builtin_check.isChecked()
        self.config.cache_enabled = self.cache_enabled_check.isChecked()
        path_text = self.cache_path_edit.text().strip()
        self.config.cache_path = path_text or None
        staging_text = self.staging_dir_edit.text().strip()
        self.config.staging_dir = staging_text or None

    @Slot()  # pyrefly: ignore [not-callable]
    def _on_browse_staging_dir(self) -> None:
        """打开目录选择对话框，将所选路径填入暂存区路径编辑框（iter-77）。

        起始目录优先使用当前编辑框中的路径，否则回退到用户主目录。
        取消选择时保持编辑框内容不变。
        """
        current = self.staging_dir_edit.text().strip()
        start_dir = current if current else ""
        chosen = QFileDialog.getExistingDirectory(self, "选择暂存区目录", start_dir)
        if chosen:
            self.staging_dir_edit.setText(chosen)

    def _on_accept(self) -> None:
        """确定按钮：保存配置并关闭对话框。"""
        self._save_config()
        self.accept()
