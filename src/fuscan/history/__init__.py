"""扫描历史归档与多次扫描对比摘要（iter-115）。

将每次扫描的关键指标（命中数、文件路径集合、规则名、耗时等）归档到
``~/.fuscan/history.json``，重启后仍可查看。提供 :func:`compare_scans`
对比两次扫描，计算新增/已解决/持续命中文件集合，用于趋势分析。

公共 API：

- :class:`ScanHistoryEntry`：单次扫描归档条目（frozen dataclass）
- :class:`HistoryStore`：JSON 持久化存储，线程安全
- :class:`ScanComparison`：两次扫描对比结果
- :func:`compare_scans`：对比两次扫描，生成 :class:`ScanComparison`
"""

from __future__ import annotations

from fuscan.history.comparator import ScanComparison, compare_scans
from fuscan.history.model import ScanHistoryEntry
from fuscan.history.store import HistoryStore, default_history_store_path

__all__ = [
    "HistoryStore",
    "ScanComparison",
    "ScanHistoryEntry",
    "compare_scans",
    "default_history_store_path",
]
