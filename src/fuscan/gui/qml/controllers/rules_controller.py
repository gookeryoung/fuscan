"""规则控制器：QML ↔ RuleSet/规则文件管理桥接。

管理规则文件路径列表、内置规则勾选、规则集加载与合并。规则列表通过
:class:`RuleListModel` 暴露给 QML ``ListView`` 绑定，规则文件列表通过
``@Property`` 暴露简单字符串列表（条目数少，无需 Model）。

公共 API：

- :class:`RulesController`：``QObject`` 子类
- :meth:`RulesController.load_file`：弹出 QFileDialog 加载规则文件
- :meth:`RulesController.move_up` / :meth:`move_down` / :meth:`remove_selected`：顺序管理
- :meth:`RulesController.set_use_builtin`：勾选内置规则
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
    from PySide2.QtWidgets import QFileDialog, QMessageBox
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import QFileDialog, QMessageBox  # pyrefly: ignore [missing-import]

from fuscan.config import Config
from fuscan.gui.qml.models.rule_model import RuleListModel
from fuscan.rules import RuleError, load_ruleset, merge_multiple_rulesets
from fuscan.rules.model import RuleSet

if TYPE_CHECKING:
    pass

__all__ = ["RulesController"]

logger = logging.getLogger(__name__)


class RulesController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """规则控制器。

    :param config_controller: 配置控制器（共享 :class:`Config` 实例）
    :param parent: 父 QObject
    """

    rulesetChanged = Signal()
    rulesFileListChanged = Signal()
    selectionChanged = Signal()
    useBuiltinChanged = Signal()

    def __init__(self, config_controller: object, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config_controller = config_controller
        self._config: Config = config_controller.config  # pyrefly: ignore [missing-attribute]
        self._ruleset: RuleSet | None = None
        self._rule_model: RuleListModel = RuleListModel(self)
        self._selected_file_index: int = -1
        # 初始加载规则集
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)

    @property
    def ruleset(self) -> RuleSet | None:
        """当前规则集（供 ScanController 读取）。"""
        return self._ruleset

    @property
    def rules_paths(self) -> list[Path]:
        """规则文件路径列表（供 ScanController 构造缓存上下文）。"""
        return [Path(p) for p in self._config.rules_paths if Path(p).exists()]

    @property
    def use_builtin(self) -> bool:
        """是否启用内置规则。"""
        return self._config.use_builtin

    # ----------------------------- QML 属性 -----------------------------

    @Property(RuleListModel)  # pyrefly: ignore [not-callable]
    def ruleModel(self) -> RuleListModel:
        """规则列表模型。"""
        return self._rule_model

    @Property(int, notify=rulesetChanged)  # pyrefly: ignore [not-callable]
    def ruleCount(self) -> int:
        """当前规则数。"""
        return len(self._ruleset.rules) if self._ruleset is not None else 0

    @Property(bool, notify=useBuiltinChanged)  # pyrefly: ignore [not-callable]
    def useBuiltin(self) -> bool:
        """是否启用内置规则。"""
        return self._config.use_builtin

    @Slot(bool)  # pyrefly: ignore [not-callable]
    def setUseBuiltin(self, value: bool) -> None:
        """设置是否启用内置规则。"""
        if value != self._config.use_builtin:
            self._config.use_builtin = value
            self._config_controller.save()  # pyrefly: ignore [missing-attribute]
            self.useBuiltinChanged.emit()  # pyrefly: ignore [missing-attribute]
            self._reload_ruleset()
            self._rule_model.set_ruleset(self._ruleset)
            self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property("QVariantList", notify=rulesFileListChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def rulesFileModel(self) -> list[dict[str, str]]:
        """规则文件列表（QML 直接 ListView 绑定）。"""
        return [{"fileName": Path(p).name, "path": p} for p in self._config.rules_paths]

    @Property(int, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def selectedFileIndex(self) -> int:
        """选中规则文件行号。"""
        return self._selected_file_index

    @Slot(int)  # pyrefly: ignore [not-callable]
    def setSelectedFileIndex(self, index: int) -> None:
        """设置选中规则文件行号。"""
        if index != self._selected_file_index:
            self._selected_file_index = index
            self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property(bool, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def canMoveUp(self) -> bool:
        """是否可上移选中规则文件。"""
        return self._selected_file_index > 0

    @Property(bool, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def canMoveDown(self) -> bool:
        """是否可下移选中规则文件。"""
        return 0 <= self._selected_file_index < len(self._config.rules_paths) - 1

    @Property(bool, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def canRemove(self) -> bool:
        """是否可移除选中规则文件。"""
        return 0 <= self._selected_file_index < len(self._config.rules_paths)

    # ----------------------------- QML 调用槽 -----------------------------

    @Slot()  # pyrefly: ignore [not-callable]
    def loadFile(self) -> None:
        """弹出 QFileDialog 选择规则文件并加载。"""
        last_dir = str(Path(self._config.rules_paths[-1]).parent) if self._config.rules_paths else str(Path.home())
        path_str, _ = QFileDialog.getOpenFileName(
            None,
            "选择规则文件",
            last_dir,
            "YAML 文件 (*.yaml *.yml);;所有文件 (*.*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if str(path) in self._config.rules_paths:
            # 已加载：询问是否重新加载
            reply = QMessageBox.question(
                None,
                "规则文件已加载",
                f"该规则文件已在列表中:\n{path.name}\n\n是否重新加载以应用最新内容？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
        else:
            self._config.rules_paths.append(str(path))
        try:
            self._reload_ruleset()
            self._rule_model.set_ruleset(self._ruleset)
            self._config_controller.save()  # pyrefly: ignore [missing-attribute]
            self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]
        except RuleError as exc:
            # 加载失败：从列表中移除新加的（如果是新加的）
            if str(path) in self._config.rules_paths and path not in [Path(p) for p in self._config.rules_paths[:-1]]:
                self._config.rules_paths.remove(str(path))
            QMessageBox.warning(None, "规则错误", f"加载规则失败:\n{exc}")

    @Slot()  # pyrefly: ignore [not-callable]
    def moveUp(self) -> None:
        """上移选中规则文件。"""
        if not self.canMoveUp:
            return
        idx = self._selected_file_index
        paths = self._config.rules_paths
        paths[idx - 1], paths[idx] = paths[idx], paths[idx - 1]
        self._selected_file_index = idx - 1
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def moveDown(self) -> None:
        """下移选中规则文件。"""
        if not self.canMoveDown:
            return
        idx = self._selected_file_index
        paths = self._config.rules_paths
        paths[idx + 1], paths[idx] = paths[idx], paths[idx + 1]
        self._selected_file_index = idx + 1
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def removeSelected(self) -> None:
        """移除选中规则文件。"""
        if not self.canRemove:
            return
        idx = self._selected_file_index
        self._config.rules_paths.pop(idx)
        self._selected_file_index = -1
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    # ----------------------------- 内部方法 -----------------------------

    def _reload_ruleset(self) -> None:
        """重新加载规则集（按 use_builtin + rules_paths 合并）。"""
        from fuscan.config import load_with_builtin

        try:
            if self._config.use_builtin:
                self._ruleset = load_with_builtin(self.rules_paths)
            elif self.rules_paths:
                rulesets = [load_ruleset(p) for p in self.rules_paths]
                self._ruleset = merge_multiple_rulesets(*rulesets)
            else:
                self._ruleset = None
        except RuleError as exc:
            logger.warning("规则集加载失败: %s", exc)
            self._ruleset = None
