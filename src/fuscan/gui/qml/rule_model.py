"""规则列表模型（QAbstractListModel）。

展示 :class:`RuleSet` 中所有 :class:`Rule` 的名称、严重度、描述。
供 QML ``ListView`` 直接绑定。

公共 API：

- :class:`RuleListModel`：``QAbstractListModel`` 子类
- :meth:`RuleListModel.set_rules`：批量替换规则并 emit 信号
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt  # pyrefly: ignore [missing-import]

from fuscan.gui.qml._severity_utils import severity_color_hex, severity_text

if TYPE_CHECKING:
    from fuscan.rules.model import Rule, RuleSet

__all__ = ["RuleListModel"]

_ROLE_NAME = b"name"
_ROLE_SEVERITY_TEXT = b"severityText"
_ROLE_SEVERITY_COLOR = b"severityColor"
_ROLE_DESCRIPTION = b"description"

_ROLES: dict[int, bytes] = {
    Qt.UserRole + 1: _ROLE_NAME,
    Qt.UserRole + 2: _ROLE_SEVERITY_TEXT,
    Qt.UserRole + 3: _ROLE_SEVERITY_COLOR,
    Qt.UserRole + 4: _ROLE_DESCRIPTION,
}


class RuleListModel(QAbstractListModel):  # pyrefly: ignore [invalid-inheritance]
    """规则列表模型。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._rules: tuple[Rule, ...] = ()

    def rowCount(self, parent: QModelIndex = None) -> int:  # type: ignore[assignment]
        if parent is None:
            parent = QModelIndex()
        return 0 if parent.isValid() else len(self._rules)

    def roleNames(self) -> dict[int, bytes]:
        return _ROLES

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:
        if not index.isValid() or not (0 <= index.row() < len(self._rules)):
            return ""
        rule = self._rules[index.row()]
        if role == Qt.UserRole + 1:
            return rule.name
        if role == Qt.UserRole + 2:
            return severity_text(rule.severity)
        if role == Qt.UserRole + 3:
            return severity_color_hex(rule.severity)
        if role == Qt.UserRole + 4:
            return rule.description
        return ""

    # ----------------------------- 公共 API -----------------------------

    def set_ruleset(self, ruleset: RuleSet | None) -> None:
        """从 :class:`RuleSet` 加载规则（emit beginResetModel/endResetModel）。"""
        self.beginResetModel()
        self._rules = ruleset.rules if ruleset is not None else ()
        self.endResetModel()

    def clear(self) -> None:
        """清空规则列表。"""
        self.beginResetModel()
        self._rules = ()
        self.endResetModel()

    @property
    def rules(self) -> tuple[Rule, ...]:
        """原始规则元组（只读）。"""
        return self._rules
