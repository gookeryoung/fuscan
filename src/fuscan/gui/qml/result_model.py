"""扫描结果列表模型（QAbstractListModel）。

供 QML ``ListView`` 直接绑定，按 role 返回每个 :class:`ScanResult` 的
展示字段（文件路径、命中规则名、严重度文本/色值、命中数等）。大数据量
（数千条命中）必须用 Model，禁止 QML 侧 ``ListModel`` 动态 append。

公共 API：

- :class:`ResultListModel`：``QAbstractListModel`` 子类
- :meth:`ResultListModel.set_results`：批量替换结果并 emit 信号
- :meth:`ResultListModel.clear`：清空
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt  # pyrefly: ignore [missing-import]

from fuscan.gui.qml._severity_utils import severity_color_hex, severity_text
from fuscan.rules.model import Severity

if TYPE_CHECKING:
    from fuscan.scanner.result import ScanResult

__all__ = ["ResultListModel"]

# QML role 名称（与 ScanPage.qml delegate 中 model.* 一致）
_ROLE_FILE_PATH = b"filePath"
_ROLE_RULE_NAME = b"ruleName"
_ROLE_SEVERITY_TEXT = b"severityText"
_ROLE_SEVERITY_COLOR = b"severityColor"
_ROLE_HITS_COUNT = b"hitsCount"
_ROLE_INDEX = b"index"

_ROLES: dict[int, bytes] = {
    Qt.UserRole + 1: _ROLE_FILE_PATH,
    Qt.UserRole + 2: _ROLE_RULE_NAME,
    Qt.UserRole + 3: _ROLE_SEVERITY_TEXT,
    Qt.UserRole + 4: _ROLE_SEVERITY_COLOR,
    Qt.UserRole + 5: _ROLE_HITS_COUNT,
    Qt.UserRole + 6: _ROLE_INDEX,
}


class ResultListModel(QAbstractListModel):  # pyrefly: ignore [invalid-inheritance]
    """扫描结果列表模型。

    存储 :class:`ScanResult` 列表，按 role 返回展示字段。
    """

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._results: tuple[ScanResult, ...] = ()

    def rowCount(self, parent: QModelIndex = None) -> int:  # type: ignore[assignment]
        """返回行数。"""
        if parent is None:
            parent = QModelIndex()
        return 0 if parent.isValid() else len(self._results)

    def roleNames(self) -> dict[int, bytes]:
        """返回 role 名称映射。"""
        return _ROLES

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:
        """按 role 返回对应字段值。"""
        if not index.isValid() or not (0 <= index.row() < len(self._results)):
            return ""
        result = self._results[index.row()]
        if role == Qt.UserRole + 1:
            return str(result.path)
        if role == Qt.UserRole + 2:
            # 多规则命中时取第一个规则名，QML 显示主要规则
            return result.rule_names[0] if result.rule_names else ""
        if role == Qt.UserRole + 3:
            return severity_text(result.max_severity)
        if role == Qt.UserRole + 4:
            return severity_color_hex(result.max_severity)
        if role == Qt.UserRole + 5:
            return len(result.hits)
        if role == Qt.UserRole + 6:
            return index.row()
        return ""

    # ----------------------------- 公共 API -----------------------------

    def set_results(self, results: tuple[ScanResult, ...]) -> None:
        """批量替换结果（emit beginResetModel/endResetModel）。"""
        self.beginResetModel()
        self._results = results
        self.endResetModel()

    def clear(self) -> None:
        """清空结果。"""
        self.set_results(())

    def get_result(self, row: int) -> ScanResult | None:
        """按行号返回原始 :class:`ScanResult`，越界返回 None。"""
        if 0 <= row < len(self._results):
            return self._results[row]
        return None

    @property
    def results(self) -> tuple[ScanResult, ...]:
        """原始结果元组（只读）。"""
        return self._results

    @staticmethod
    def _severity_to_text(severity: Severity) -> str:
        """严重度枚举转中文文本（向后兼容）。"""
        return severity_text(severity)

    @staticmethod
    def _severity_to_color(severity: Severity) -> str:
        """严重度枚举转色值（向后兼容）。"""
        return severity_color_hex(severity)
