"""规则文件编辑对话框。

提供 GUI 内嵌的 YAML 规则文件编辑器，支持选择已加载的规则文件、
编辑内容、保存并重新加载规则集。
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from PySide2.QtCore import Signal
from PySide2.QtWidgets import (
    QDialog,
    QMessageBox,
    QWidget,
)

from uniscan.gui.rule_editor_ui import Ui_RuleEditorDialog
from uniscan.rules import RuleError, load_ruleset

__all__ = ["RuleEditorDialog"]

logger = logging.getLogger(__name__)


class RuleEditorDialog(QDialog):
    """规则文件编辑对话框。

    信号：

    - ``rules_saved``：规则文件保存后发射，携带被修改的文件路径
    """

    rules_saved = Signal(str)

    def __init__(
        self,
        rules_paths: list[Path],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._rules_paths = rules_paths
        self._ui = Ui_RuleEditorDialog()
        self._ui.setupUi(self)
        self._bind_widgets()
        self._configure_ui()

    def _bind_widgets(self) -> None:
        """将 Ui_RuleEditorDialog 的部件绑定到本类私有属性，保持业务逻辑兼容。"""
        ui = self._ui
        self._file_combo = ui.file_combo
        self._editor = ui.editor

    def _configure_ui(self) -> None:
        """配置 .ui 无法静态表达的动态属性、初始内容与信号槽连接。"""
        ui = self._ui
        # 填充文件下拉框
        for path in self._rules_paths:
            self._file_combo.addItem(path.name, str(path))

        # 信号槽连接
        self._file_combo.currentIndexChanged.connect(self._on_file_changed)
        ui.reload_btn.clicked.connect(self._on_reload)
        ui.save_btn.clicked.connect(self._on_save)

        # 无规则文件时的空态处理
        if not self._rules_paths:
            ui.empty_label.setVisible(True)
            self._editor.setEnabled(False)
            ui.reload_btn.setEnabled(False)
            ui.save_btn.setEnabled(False)
            return

        # layout 伸缩比例（编辑区占主要空间）
        ui.main_layout.setStretch(0, 0)
        ui.main_layout.setStretch(1, 0)
        ui.main_layout.setStretch(2, 1)
        ui.main_layout.setStretch(3, 0)

        self._load_file_content(0)

    def _on_file_changed(self, index: int) -> None:
        """切换文件时加载对应内容。"""
        self._load_file_content(index)

    def _load_file_content(self, index: int) -> None:
        """加载指定索引的文件内容到编辑器。"""
        if index < 0 or index >= len(self._rules_paths):
            self._editor.setPlainText("")
            return
        path = self._rules_paths[index]
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("读取规则文件失败 %s", path, exc_info=True)
            self._editor.setPlainText(f"读取文件失败: {exc}")
            self._editor.setEnabled(False)
            return
        self._editor.setPlainText(content)
        self._editor.setEnabled(True)

    def _on_reload(self) -> None:
        """放弃修改，从文件重新加载。"""
        idx = self._file_combo.currentIndex()
        self._load_file_content(idx)

    def _on_save(self) -> None:
        """保存当前编辑内容到文件，验证后通知主窗口重新加载。"""
        idx = self._file_combo.currentIndex()
        if idx < 0 or idx >= len(self._rules_paths):
            return
        path = self._rules_paths[idx]
        content = self._editor.toPlainText()

        # 验证 YAML 语法
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as exc:
            QMessageBox.warning(self, "YAML 语法错误", f"YAML 解析失败:\n{exc}")
            return

        # 验证规则可加载
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"写入文件失败:\n{exc}")
            return

        # 验证规则解析
        try:
            load_ruleset(path)
        except RuleError as exc:
            QMessageBox.warning(
                self,
                "规则解析错误",
                f"文件已保存但规则解析失败:\n{exc}\n\n请修正后重新保存。",
            )
            self.rules_saved.emit(str(path))
            return

        self.rules_saved.emit(str(path))
        QMessageBox.information(self, "保存成功", f"规则文件已保存并重新加载:\n{path.name}")
