"""文件监控命中列表模型（QAbstractListModel）。

供 QML ``ListView`` 直接绑定，按 role 返回每条监控命中记录的展示字段
（时间、文件路径、规则名、严重度文本/色值、匹配文本摘要）。

与 :class:`ResultListModel` 不同，本模型面向**实时增量追加**场景：

- :meth:`append_hit`：单条追加（``beginInsertRows``/``endInsertRows``），
  适合 watchdog 事件触发后逐条推送
- :meth:`clear`：清空（``beginResetModel``/``endResetModel``）

为避免无界增长，构造时传入 ``max_rows``，超过阈值时自动从头部丢弃最旧记录
（FIFO），保证长时间运行内存稳定。默认 1000 条。

公共 API：

- :class:`FileMonitorModel`：``QAbstractListModel`` 子类
- :meth:`FileMonitorModel.append_hit`：追加一条命中记录
- :meth:`FileMonitorModel.clear`：清空所有记录
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import (  # pyrefly: ignore [missing-import]
        QAbstractListModel,
        QModelIndex,
        Qt,
        Slot,
    )

from fuscan.gui.severity_utils import severity_color_hex, severity_text
from fuscan.rules.model import Severity

__all__ = ["FileMonitorModel"]

# QML role 名称（与 FileMonitorPanel.qml delegate 中 model.* 一致）
_ROLE_TIME = b"time"
_ROLE_FILE_PATH = b"filePath"
_ROLE_RULE_NAME = b"ruleName"
_ROLE_SEVERITY_TEXT = b"severityText"
_ROLE_SEVERITY_COLOR = b"severityColor"
_ROLE_MATCH_TEXT = b"matchText"

_ROLES: dict[int, bytes] = {
    Qt.UserRole + 1: _ROLE_TIME,
    Qt.UserRole + 2: _ROLE_FILE_PATH,
    Qt.UserRole + 3: _ROLE_RULE_NAME,
    Qt.UserRole + 4: _ROLE_SEVERITY_TEXT,
    Qt.UserRole + 5: _ROLE_SEVERITY_COLOR,
    Qt.UserRole + 6: _ROLE_MATCH_TEXT,
}

# 默认最大保留行数（超过后从头部丢弃最旧记录）
_DEFAULT_MAX_ROWS = 1000


@dataclass(frozen=True)
class MonitorHitRecord:
    """单条监控命中记录（扁平数据，避免每次 data() 重新计算展示字段）。

    :param time: 命中时间字符串（已格式化，如 ``"14:32:08"``）
    :param file_path: 文件路径
    :param rule_name: 规则名（多规则取首条）
    :param severity: 严重度枚举
    :param match_text: 匹配文本摘要（截断后的预览）
    """

    time: str
    file_path: str
    rule_name: str
    severity: Severity
    match_text: str


class FileMonitorModel(QAbstractListModel):  # pyrefly: ignore [invalid-inheritance]
    """文件监控命中列表模型。

    :param max_rows: 最大保留行数，超过后从头部丢弃最旧记录；默认 1000
    :param parent: 父 QObject
    """

    def __init__(self, max_rows: int = _DEFAULT_MAX_ROWS, parent: object | None = None) -> None:
        super().__init__(parent)
        self._records: list[MonitorHitRecord] = []
        self._max_rows: int = max(1, max_rows)

    def rowCount(self, parent: QModelIndex = None) -> int:  # type: ignore[assignment]
        """返回当前记录数。"""
        if parent is None:
            parent = QModelIndex()
        return 0 if parent.isValid() else len(self._records)

    def roleNames(self) -> dict[int, bytes]:
        """返回 role 名称映射。"""
        return _ROLES

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:
        """按 role 返回对应字段值。"""
        if not index.isValid() or not (0 <= index.row() < len(self._records)):
            return ""
        record = self._records[index.row()]
        if role == Qt.UserRole + 1:
            return record.time
        if role == Qt.UserRole + 2:
            return record.file_path
        if role == Qt.UserRole + 3:
            return record.rule_name
        if role == Qt.UserRole + 4:
            return severity_text(record.severity)
        if role == Qt.UserRole + 5:
            return severity_color_hex(record.severity)
        if role == Qt.UserRole + 6:
            return record.match_text
        return ""

    # ----------------------------- 公共 API -----------------------------

    @Slot(str, str, str, str, str)  # pyrefly: ignore [not-callable]
    def append_hit(
        self,
        time: str,
        file_path: str,
        rule_name: str,
        severity: str,
        match_text: str,
    ) -> None:
        """追加一条命中记录。

        超过 ``max_rows`` 时从头部丢弃最旧记录（用 ``beginRemoveRows``/
        ``endRemoveRows`` 通知 QML）。

        :param time: 时间字符串
        :param file_path: 文件路径
        :param rule_name: 规则名
        :param severity: 严重度枚举值字符串（``"info"``/``"warning"``/``"critical"``）
        :param match_text: 匹配文本摘要
        """
        try:
            sev = Severity(severity)
        except ValueError:
            sev = Severity.INFO
        record = MonitorHitRecord(
            time=time,
            file_path=file_path,
            rule_name=rule_name,
            severity=sev,
            match_text=match_text,
        )
        # 容量超限时先移除最旧一条，再追加新的
        if len(self._records) >= self._max_rows:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            self._records.pop(0)
            self.endRemoveRows()
        new_row = len(self._records)
        self.beginInsertRows(QModelIndex(), new_row, new_row)
        self._records.append(record)
        self.endInsertRows()

    @Slot()  # pyrefly: ignore [not-callable]
    def clear(self) -> None:
        """清空所有记录。"""
        if not self._records:
            return
        self.beginResetModel()
        self._records.clear()
        self.endResetModel()

    @property
    def records(self) -> tuple[MonitorHitRecord, ...]:
        """当前所有记录（只读元组）。"""
        return tuple(self._records)

    @property
    def count(self) -> int:
        """当前记录数。"""
        return len(self._records)
