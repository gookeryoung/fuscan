"""GUI 模型层：``QAbstractListModel`` 子类，大数据量列表绑定。

按 rule-12-pyside-dev.md，结果/规则/文件类型列表必须用 Model，
禁止视图侧动态 append 大量元素。子模块：

- :class:`ExtractorListModel`：提取器勾选列表（扁平化，按 display_name 排序）
- :class:`RuleListModel`：规则列表（按规则文件分组）
- :class:`ResultListModel`：扫描结果列表（含命中规则数/严重度/跳过标记）
- :class:`WorkspaceListModel`：工作区列表（多任务管理）
- :class:`FileMonitorModel`：文件监控命中列表（实时增量追加 + FIFO 限容）
"""

from __future__ import annotations

from fuscan.gui.models.extractor_model import ExtractorListModel
from fuscan.gui.models.file_monitor_model import FileMonitorModel
from fuscan.gui.models.result_model import ResultListModel
from fuscan.gui.models.rule_model import RuleListModel
from fuscan.gui.models.workspace_model import WorkspaceItem, WorkspaceListModel

__all__ = [
    "ExtractorListModel",
    "FileMonitorModel",
    "ResultListModel",
    "RuleListModel",
    "WorkspaceItem",
    "WorkspaceListModel",
]
