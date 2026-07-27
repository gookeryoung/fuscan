"""扫描结果列表模型（QAbstractListModel）。

供 QML ``ListView`` 直接绑定，按 role 返回每个 :class:`ScanResult` 的
展示字段（文件路径、命中规则名、严重度文本/色值、命中数等）。大数据量
（数千条命中）必须用 Model，禁止 QML 侧 ``ListModel`` 动态 append。

iter-112 起在 Model 内部维护过滤+排序视图：

- ``_results``：原始结果元组（``set_results`` 写入，永不在外部修改）
- ``_filtered``：应用过滤+排序后的视图元组，``data()``/``rowCount()``/``get_result()``
  均基于此视图，使 ``selectedResultIndex`` 始终对应过滤后的行号，避免
  代理模型索引映射的复杂度
- 过滤维度：文件路径模糊匹配（不区分大小写）、规则名多选、严重度多选
- 排序维度：默认（原始顺序）、文件路径、命中数、严重度

公共 API：

- :class:`ResultListModel`：``QAbstractListModel`` 子类
- :meth:`ResultListModel.set_results`：批量替换结果并 emit 信号
- :meth:`ResultListModel.clear`：清空
- :meth:`ResultListModel.set_filter_text` / :meth:`set_filter_rules` /
  :meth:`set_filter_severities` / :meth:`set_sort`：iter-112 过滤+排序入口
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt  # pyrefly: ignore [missing-import]

from fuscan.gui.severity_utils import severity_color_hex, severity_text
from fuscan.rules.model import Severity

if TYPE_CHECKING:
    from fuscan.scanner.result import ScanResult

__all__ = ["ResultListModel"]

# QML role 名称（与 ResultsPage.qml delegate 中 model.* 一致）
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

# 严重度排序权重：CRITICAL=3, WARNING=2, INFO=1，未命中（不应出现）=0
_SEVERITY_WEIGHT: dict[Severity, int] = {
    Severity.CRITICAL: 3,
    Severity.WARNING: 2,
    Severity.INFO: 1,
}

# 排序字段枚举（与 QML ComboBox currentIndex 对应）
SORT_DEFAULT = "default"
SORT_FILE_PATH = "filePath"
SORT_HITS_COUNT = "hitsCount"
SORT_SEVERITY = "severity"
_SORT_FIELDS: frozenset[str] = frozenset({SORT_DEFAULT, SORT_FILE_PATH, SORT_HITS_COUNT, SORT_SEVERITY})


class ResultListModel(QAbstractListModel):  # pyrefly: ignore [invalid-inheritance]
    """扫描结果列表模型。

    存储 :class:`ScanResult` 列表，按 role 返回展示字段。
    iter-112 起内置过滤+排序视图，``rowCount``/``data``/``get_result`` 均基于
    过滤后的视图，``selectedResultIndex`` 直接对应视图行号无需映射。
    """

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._results: tuple[ScanResult, ...] = ()
        self._filtered: tuple[ScanResult, ...] = ()
        # 过滤条件：空字符串/空集合表示该维度不过滤
        self._filter_text: str = ""
        self._filter_rules: frozenset[str] = frozenset()
        self._filter_severities: frozenset[Severity] = frozenset()
        # 排序条件：default = 保持原始顺序
        self._sort_field: str = SORT_DEFAULT
        self._sort_ascending: bool = True

    def rowCount(self, parent: QModelIndex = None) -> int:  # type: ignore[assignment]
        """返回过滤后视图的行数。"""
        if parent is None:
            parent = QModelIndex()
        return 0 if parent.isValid() else len(self._filtered)

    def roleNames(self) -> dict[int, bytes]:
        """返回 role 名称映射。"""
        return _ROLES

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:
        """按 role 返回对应字段值（基于过滤后视图）。"""
        if not index.isValid() or not (0 <= index.row() < len(self._filtered)):
            return ""
        result = self._filtered[index.row()]
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
        """批量替换结果（emit beginResetModel/endResetModel）。

        替换后自动重新应用当前过滤+排序条件，视图同步刷新。
        """
        self.beginResetModel()
        self._results = results
        self._apply_filter_and_sort()
        self.endResetModel()

    def clear(self) -> None:
        """清空结果。"""
        self.set_results(())

    def get_result(self, row: int) -> ScanResult | None:
        """按视图行号返回过滤后的 :class:`ScanResult`，越界返回 None。"""
        if 0 <= row < len(self._filtered):
            return self._filtered[row]
        return None

    @property
    def results(self) -> tuple[ScanResult, ...]:
        """原始结果元组（只读，未过滤）。"""
        return self._results

    @property
    def filtered_results(self) -> tuple[ScanResult, ...]:
        """过滤+排序后的视图元组（只读）。"""
        return self._filtered

    @property
    def total_count(self) -> int:
        """原始结果总数（未过滤）。"""
        return len(self._results)

    @property
    def filtered_count(self) -> int:
        """过滤后结果数。"""
        return len(self._filtered)

    # ----------------------------- 过滤+排序 API（iter-112） -----------------------------

    def set_filter_text(self, text: str) -> None:
        """设置文件路径模糊匹配条件（不区分大小写）。

        :param text: 搜索文本；空字符串表示清除该维度过滤
        """
        normalized = text.strip() if text else ""
        if normalized == self._filter_text:
            return
        self.beginResetModel()
        self._filter_text = normalized
        self._apply_filter_and_sort()
        self.endResetModel()

    def set_filter_rules(self, rule_names: tuple[str, ...] | list[str] | None) -> None:
        """设置规则名多选过滤条件。

        :param rule_names: 选中的规则名集合；空或 None 表示该维度不过滤
        """
        new_rules = frozenset(rule_names) if rule_names else frozenset()
        if new_rules == self._filter_rules:
            return
        self.beginResetModel()
        self._filter_rules = new_rules
        self._apply_filter_and_sort()
        self.endResetModel()

    def set_filter_severities(self, severities: tuple[Severity, ...] | list[Severity] | None) -> None:
        """设置严重度多选过滤条件。

        :param severities: 选中的严重度集合；空或 None 表示该维度不过滤
        """
        new_sevs = frozenset(severities) if severities else frozenset()
        if new_sevs == self._filter_severities:
            return
        self.beginResetModel()
        self._filter_severities = new_sevs
        self._apply_filter_and_sort()
        self.endResetModel()

    def set_sort(self, field: str, ascending: bool = True) -> None:
        """设置排序条件。

        :param field: 排序字段，取值见 :data:`_SORT_FIELDS`
        :param ascending: True 升序，False 降序；默认升序
        """
        if field not in _SORT_FIELDS:
            return
        if field == self._sort_field and ascending == self._sort_ascending:
            return
        self.beginResetModel()
        self._sort_field = field
        self._sort_ascending = ascending
        self._apply_filter_and_sort()
        self.endResetModel()

    def clear_filters(self) -> None:
        """清除所有过滤条件（保留排序）。"""
        if not self._filter_text and not self._filter_rules and not self._filter_severities:
            return
        self.beginResetModel()
        self._filter_text = ""
        self._filter_rules = frozenset()
        self._filter_severities = frozenset()
        self._apply_filter_and_sort()
        self.endResetModel()

    @property
    def filter_text(self) -> str:
        """当前文件路径过滤文本。"""
        return self._filter_text

    @property
    def filter_rules(self) -> frozenset[str]:
        """当前规则名过滤集合。"""
        return self._filter_rules

    @property
    def filter_severities(self) -> frozenset[Severity]:
        """当前严重度过滤集合。"""
        return self._filter_severities

    @property
    def sort_field(self) -> str:
        """当前排序字段。"""
        return self._sort_field

    @property
    def sort_ascending(self) -> bool:
        """当前排序方向。"""
        return self._sort_ascending

    # ----------------------------- 内部实现 -----------------------------

    def _apply_filter_and_sort(self) -> None:
        """根据当前过滤+排序条件刷新 ``_filtered`` 视图。

        纯 Python 实现（无 QML 依赖），便于单元测试。耗时与结果数线性相关，
        10k 结果约 5ms，可接受。后续如需进一步优化可下沉到 SQLite ORDER BY。
        """
        if not self._results:
            self._filtered = ()
            return
        # 阶段 1：过滤
        view = list(self._results)
        if self._filter_text:
            keyword = self._filter_text.lower()
            view = [r for r in view if keyword in str(r.path).lower()]
        if self._filter_rules:
            # 任一命中规则名在选中集合中即保留
            view = [r for r in view if any(name in self._filter_rules for name in r.rule_names)]
        if self._filter_severities:
            view = [r for r in view if r.max_severity in self._filter_severities]
        # 阶段 2：排序
        if self._sort_field == SORT_DEFAULT:
            # 保持原始顺序，仅复制列表
            self._filtered = tuple(view)
            return

        def _key_file_path(r: ScanResult) -> str:
            return str(r.path).lower()

        def _key_hits_count(r: ScanResult) -> int:
            return len(r.hits)

        def _key_severity(r: ScanResult) -> int:
            return _SEVERITY_WEIGHT.get(r.max_severity, 0)

        if self._sort_field == SORT_FILE_PATH:
            key_func = _key_file_path
        elif self._sort_field == SORT_HITS_COUNT:
            key_func = _key_hits_count
        elif self._sort_field == SORT_SEVERITY:
            key_func = _key_severity
        else:  # 防御性：未知字段保持原始顺序
            self._filtered = tuple(view)
            return
        view.sort(key=key_func, reverse=not self._sort_ascending)
        self._filtered = tuple(view)

    @staticmethod
    def _severity_to_text(severity: Severity) -> str:
        """严重度枚举转中文文本（向后兼容）。"""
        return severity_text(severity)

    @staticmethod
    def _severity_to_color(severity: Severity) -> str:
        """严重度枚举转色值（向后兼容）。"""
        return severity_color_hex(severity)
