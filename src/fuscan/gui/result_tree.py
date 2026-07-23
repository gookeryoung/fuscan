"""扫描结果树视图（Model/View 架构）。

将结果树的模型管理、分组填充、选中/双击事件处理从 ``main_window.py`` 拆分
到独立的 ``ResultTreeView`` 控件，使主窗口仅负责筛选控件与信号路由，
结果树逻辑内聚到本模块。

公共 API：

- :class:`ResultTreeView`：QTreeView 子类，封装 ``QStandardItemModel`` 与三种
  分组模式（flat/by-rule/by-severity）的填充逻辑
- 信号 ``result_selected``/``context_menu_requested``：
  解耦视图与主窗口，主窗口通过信号接收选中/右键事件
"""

from __future__ import annotations

from typing import Sequence

try:
    from PySide2.QtCore import QPoint, Qt, Signal
    from PySide2.QtGui import QStandardItem, QStandardItemModel
    from PySide2.QtWidgets import QHeaderView, QTreeView
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QPoint, Qt, Signal  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QStandardItem, QStandardItemModel  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import QHeaderView, QTreeView  # pyrefly: ignore [missing-import]

from fuscan.gui.preview_utils import SEVERITY_BACKGROUNDS, severity_text
from fuscan.rules.model import Severity
from fuscan.scanner import ScanReport

__all__ = ["ResultTreeView"]

# 严重等级 → 排序权重（CRITICAL 优先）
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.CRITICAL: 2,
}

# 结果树表头（4 列：文件名/规则/严重等级/详情）
# iter-86：移除"命中数/条数"列——这两列信息已包含在"详情"列（sr.summary() 返回"N 条规则 / M 处匹配"）
# 与右侧详情区 file_info_html 中，保留会重复且浪费横向空间
_HEADERS: list[str] = ["文件名", "规则", "严重等级", "详情"]


def _apply_severity_to_standard_item(item: QStandardItem, severity: Severity) -> None:
    """为 QStandardItem 设置中文严重等级标签与背景色。

    仅设置背景色（浅红/浅橙/浅蓝），不设置前景色——避免 ``setForeground``
    覆盖 QSS ``::item:selected`` 的选中态白字（需求1：选中项字体统一白色）。
    未选中态字色由 ``QTreeWidget`` 的 ``color`` 令牌（COLOR_TEXT_PRIMARY）提供，
    浅底深字对比度高于原"浅底+红/橙/蓝字"配色。

    :param item: 结果树中代表"严重等级"列的 QStandardItem
    :param severity: 严重等级枚举值
    """
    item.setText(severity_text(severity))
    item.setBackground(SEVERITY_BACKGROUNDS[severity])


def _make_result_row(texts: Sequence[str]) -> list[QStandardItem]:
    """根据文本序列构造一行不可编辑的 QStandardItem 列表。

    :param texts: 各列文本（顺序对应表头：文件名/规则/严重等级/详情）
    :returns: QStandardItem 列表，长度与 ``texts`` 相同，每项已禁用编辑
    """
    row: list[QStandardItem] = []
    for text in texts:
        cell = QStandardItem(text)
        cell.setEditable(False)
        row.append(cell)
    return row


def _clear_row_selectable(row: list[QStandardItem]) -> None:
    """清除一行 QStandardItem 的选择标志（用于分组顶层项不可选）。

    :param row: QStandardItem 列表，每个 cell 将清除 ``Qt.ItemIsSelectable`` 标志
    """
    for cell in row:
        cell.setFlags(cell.flags() & ~Qt.ItemIsSelectable)


class ResultTreeView(QTreeView):  # pyrefly: ignore [invalid-inheritance]
    """扫描结果树视图，封装模型与三种分组模式的填充逻辑。

    主窗口通过 :meth:`populate` 传入报告，通过 :meth:`refresh` 触发按当前
    筛选条件与分组模式重建模型。选中/右键事件通过信号抛出，由主窗口
    路由到详情区或上下文菜单。
    """

    # 选中项变化：携带 ScanResult 或 None（空选/分组顶层项）
    result_selected = Signal(object)
    # 右键菜单：携带右键位置的 viewport 坐标
    context_menu_requested = Signal(QPoint)

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        """初始化结果树：创建模型、绑定视图、设置列宽、连接内部信号。"""
        super().__init__(parent)
        self._result_model: QStandardItemModel = QStandardItemModel()
        self._result_model.setHorizontalHeaderLabels(_HEADERS)
        self.setModel(self._result_model)
        # 当前暂存的扫描报告（populate 设置，clear_results 重置为 None）
        self._last_report: ScanReport | None = None
        # 列宽 resize 模式（iter-86）：
        #   - 文件名/规则/详情：Interactive（用户可拖动调整，初始宽度见下）
        #   - 严重等级：ResizeToContents（按内容自动收缩到最小所需宽度）
        #   最后一列（详情）由 header 自动 stretch 填充剩余空间
        header = self.header()
        header.setStretchLastSection(True)  # 详情列拉伸填充
        # 0 文件名 / 1 规则：Interactive，用户可调
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        self.setColumnWidth(0, 220)
        self.setColumnWidth(1, 140)
        # 2 严重等级：ResizeToContents，按内容自动收缩
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        # 3 详情：Interactive（默认由 stretchLastSection 拉伸填充）
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        self.setColumnWidth(3, 200)
        # 连接内部信号到本类的信号转发槽
        selection_model = self.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(self._handle_selection_changed)

    def populate(self, report: ScanReport) -> None:
        """存储当前报告数据，供 :meth:`refresh` 读取。"""
        self._result_model.clear()
        self._result_model.setHorizontalHeaderLabels(_HEADERS)
        # 直接刷新需传入筛选条件，此处仅暂存报告，刷新由主窗口触发
        self._last_report = report

    @property
    def last_report(self) -> ScanReport | None:
        """当前存储的扫描报告（由 :meth:`populate` 设置）。"""
        return self._last_report

    def refresh(self, report: ScanReport, path_query: str = "", rule_name: str = "", group_mode: str = "flat") -> None:
        """按筛选条件与分组模式重建结果树模型。

        :param report: 当前扫描报告（已通过 :meth:`populate` 暂存）
        :param path_query: 路径子串筛选（大小写不敏感，空字符串跳过）
        :param rule_name: 规则名精确筛选（空字符串跳过）
        :param group_mode: 分组模式 ``flat``/``rule``/``severity``
        """
        # 批量插入期间禁用重绘，避免每个 appendRow 触发一次重绘
        self.setUpdatesEnabled(False)
        try:
            self._result_model.clear()
            self._result_model.setHorizontalHeaderLabels(_HEADERS)
            # 筛选下沉到 ScanReport.filter，仅返回 results 过滤后的新报告
            filtered_report = report.filter(path_query=path_query, rule_name=rule_name)
            if group_mode == "rule":
                self._populate_grouped_by_rule(filtered_report)
            elif group_mode == "severity":
                self._populate_grouped_by_severity(filtered_report)
            else:
                self._populate_flat(filtered_report)
        finally:
            self.setUpdatesEnabled(True)

    def clear_results(self) -> None:
        """清空结果树模型与暂存报告。"""
        self._result_model.clear()
        self._result_model.setHorizontalHeaderLabels(_HEADERS)
        self._last_report = None

    def _populate_flat(self, report: ScanReport) -> None:
        """不分组：文件为顶层项，规则命中为子项。"""
        for sr in report.hits:
            file_row = _make_result_row([sr.path.name, "", "", sr.summary()])
            # ScanResult 存在该行第 0 列 UserRole，双击/选中时通过 sibling(row, 0) 取回
            file_row[0].setData(sr, Qt.UserRole)
            # 第 0 列 tooltip 显示完整路径，鼠标悬停可查看（iter-85：第 0 列仅显示文件名）
            file_row[0].setToolTip(str(sr.path))
            _apply_severity_to_standard_item(file_row[2], sr.max_severity)
            # critical 整行背景高亮，区别于仅 severity 列着色
            if sr.max_severity == Severity.CRITICAL:
                for cell in file_row:
                    cell.setBackground(SEVERITY_BACKGROUNDS[Severity.CRITICAL])
            for hit in sr.hits:
                child_row = _make_result_row(["", hit.rule_name, "", hit.detail])
                _apply_severity_to_standard_item(child_row[2], hit.severity)
                # 子行挂载在第 0 列 cell 上（QStandardItem.appendRow 是 cell 方法）
                file_row[0].appendRow(child_row)  # pyrefly: ignore [missing-argument]
            self._result_model.appendRow(file_row)  # pyrefly: ignore [missing-argument]

    def _populate_grouped_by_rule(self, report: ScanReport) -> None:
        """按规则分组：规则名为顶层项，文件为子项。"""
        rule_map = report.group_by_rule()

        for rule_name in sorted(rule_map.keys()):
            entries = rule_map[rule_name]
            hit_count = len(entries)
            match_sum = sum(h.match_count for _, h in entries)
            top_row = _make_result_row(["", rule_name, "", f"{hit_count} 个文件 / {match_sum} 处匹配"])
            # 分组项不可选中，避免选中后详情区被清空产生"无命中"误解
            _clear_row_selectable(top_row)
            for sr, hit in entries:
                child_row = _make_result_row([sr.path.name, "", "", hit.detail])
                _apply_severity_to_standard_item(child_row[2], hit.severity)
                child_row[0].setData(sr, Qt.UserRole)
                child_row[0].setToolTip(str(sr.path))
                top_row[0].appendRow(child_row)  # pyrefly: ignore [missing-argument]
            self._result_model.appendRow(top_row)  # pyrefly: ignore [missing-argument]

    def _populate_grouped_by_severity(self, report: ScanReport) -> None:
        """按严重等级分组：等级为顶层项，文件为子项。"""
        severity_map = report.group_by_severity()

        for severity in sorted(severity_map.keys(), key=lambda s: _SEVERITY_RANK[s], reverse=True):
            entries = severity_map[severity]
            file_count = len(entries)
            match_sum = sum(sr.total_match_count for sr in entries)
            top_row = _make_result_row(["", "", "", f"{file_count} 个文件 / {match_sum} 处匹配"])
            _apply_severity_to_standard_item(top_row[2], severity)
            # 分组项不可选中，避免选中后详情区被清空产生"无命中"误解
            _clear_row_selectable(top_row)
            for sr in entries:
                child_row = _make_result_row([sr.path.name, "", "", sr.summary()])
                _apply_severity_to_standard_item(child_row[2], sr.max_severity)
                child_row[0].setData(sr, Qt.UserRole)
                child_row[0].setToolTip(str(sr.path))
                # critical 整行背景高亮，区别于仅 severity 列着色
                if sr.max_severity == Severity.CRITICAL:
                    for cell in child_row:
                        cell.setBackground(SEVERITY_BACKGROUNDS[Severity.CRITICAL])
                top_row[0].appendRow(child_row)  # pyrefly: ignore [missing-argument]
            self._result_model.appendRow(top_row)  # pyrefly: ignore [missing-argument]

    def _handle_selection_changed(self, *_args: object) -> None:
        """选中变化：从 selectedIndexes 取当前项，发出 result_selected 信号。"""
        indexes = self.selectedIndexes()
        if not indexes:
            self.result_selected.emit(None)  # pyrefly: ignore [missing-attribute]
            return
        # selectedIndexes 可能包含多列；取第一个 index 所在行第 0 列 cell
        first = indexes[0]
        first_col = self._result_model.itemFromIndex(first.sibling(first.row(), 0))
        result = first_col.data(Qt.UserRole)
        if result is None:
            # _populate_flat 中命中规则子行未存 data，向上取父行（文件项）第 0 列
            parent = first_col.parent()
            if parent is not None:
                result = parent.data(Qt.UserRole)
        self.result_selected.emit(result)  # pyrefly: ignore [missing-attribute]

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """右键菜单：发出 context_menu_requested 信号，携带 viewport 坐标。"""
        self.context_menu_requested.emit(event.pos())  # pyrefly: ignore [missing-attribute]
        event.accept()
