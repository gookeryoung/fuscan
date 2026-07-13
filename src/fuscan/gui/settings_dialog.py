"""设置对话框。

使用 QTabWidget 实现多页面切换，避免设置项过于臃肿：

1. 扫描设置：最大工作线程数、最大扫描深度、是否扫描压缩包、忽略目录/扩展名
2. 通用设置：是否包含网络映射盘、是否启用内置规则、缓存设置

UI 装配委托给 ``Ui_SettingsDialog``（对应 ``settings_dialog.ui``），
本模块仅负责信号槽连接、配置加载与保存等业务逻辑。
"""

from __future__ import annotations

try:
    from PySide2.QtWidgets import QDialog, QWidget
except ImportError:  # pragma: no cover
    from PySide6.QtWidgets import QDialog, QWidget

from fuscan.config import Config
from fuscan.gui.settings_dialog_ui import Ui_SettingsDialog

__all__ = ["SettingsDialog"]


class SettingsDialog(QDialog):
    """设置对话框，多页面 Tab 形式展示。"""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._ui = Ui_SettingsDialog()
        self._ui.setupUi(self)
        self._configure_ui()
        self._load_config()

    def _configure_ui(self) -> None:
        """配置 .ui 无法静态表达的信号槽连接。"""
        ui = self._ui
        ui.button_box.accepted.connect(self._on_accept)
        ui.button_box.rejected.connect(self.reject)

    def _load_config(self) -> None:
        """加载当前配置到控件。"""
        ui = self._ui
        ui.max_workers_spin.setValue(self._config.max_workers)
        ui.max_depth_spin.setValue(self._config.max_depth or 0)
        ui.scan_archives_check.setChecked(self._config.scan_archives)
        ui.include_network_check.setChecked(self._config.include_network_drives)
        ui.use_builtin_check.setChecked(self._config.use_builtin)
        ui.ignore_dirs_edit.setPlainText("\n".join(self._config.ignore_dirs))
        ui.ignore_extensions_edit.setPlainText("\n".join(self._config.ignore_extensions))
        ui.cache_enabled_check.setChecked(self._config.cache_enabled)
        ui.cache_path_edit.setText(self._config.cache_path or "")

    def _save_config(self) -> None:
        """将控件值保存到配置。"""
        ui = self._ui
        self._config.max_workers = ui.max_workers_spin.value()
        depth = ui.max_depth_spin.value()
        self._config.max_depth = depth if depth > 0 else None
        self._config.scan_archives = ui.scan_archives_check.isChecked()
        self._config.include_network_drives = ui.include_network_check.isChecked()
        self._config.use_builtin = ui.use_builtin_check.isChecked()
        self._config.ignore_dirs = [
            line.strip() for line in ui.ignore_dirs_edit.toPlainText().splitlines() if line.strip()
        ]
        self._config.ignore_extensions = [
            line.strip() for line in ui.ignore_extensions_edit.toPlainText().splitlines() if line.strip()
        ]
        self._config.cache_enabled = ui.cache_enabled_check.isChecked()
        path_text = ui.cache_path_edit.text().strip()
        self._config.cache_path = path_text or None

    def _on_accept(self) -> None:
        """确定按钮：保存配置并关闭对话框。"""
        self._save_config()
        self.accept()

    def get_config(self) -> Config:
        """获取当前对话框中的配置。"""
        return self._config
