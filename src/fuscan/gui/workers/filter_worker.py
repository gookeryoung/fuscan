"""后台过滤+排序扫描结果：避免大结果集时主线程阻塞。

FilterWorker 在独立 QThread 中执行 filter_and_sort 纯函数，通过信号将
过滤后的元组传回主线程。10 万结果过滤+排序约 50-100ms，移至后台后
UI 不阻塞。

信号：
- ``done``：(tuple[ScanResult, ...]) 过滤+排序后的结果元组
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QThread, Signal
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QThread, Signal  # pyrefly: ignore [missing-import]

from fuscan.gui.models.result_model import filter_and_sort
from fuscan.rules.model import Severity

if TYPE_CHECKING:
    from fuscan.scanner.result import ScanResult

__all__ = ["FilterWorker"]

logger = logging.getLogger(__name__)


class FilterWorker(QThread):  # pyrefly: ignore [invalid-inheritance]
    """后台过滤+排序工作线程。

    :param results: 原始结果元组
    :param filter_text: 文件路径模糊匹配文本（空串表示不过滤）
    :param filter_rules: 规则名过滤集合（空集合表示不过滤）
    :param filter_severities: 严重度过滤集合（空集合表示不过滤）
    :param sort_field: 排序字段（``SORT_DEFAULT`` / ``SORT_FILE_PATH`` / …）
    :param sort_ascending: True 升序，False 降序
    """

    done = Signal(tuple)

    def __init__(
        self,
        results: tuple[ScanResult, ...],
        filter_text: str,
        filter_rules: frozenset[str],
        filter_severities: frozenset[Severity],
        sort_field: str,
        sort_ascending: bool,
    ) -> None:
        """初始化过滤线程。"""
        super().__init__()
        self._results = results
        self._filter_text = filter_text
        self._filter_rules = filter_rules
        self._filter_severities = filter_severities
        self._sort_field = sort_field
        self._sort_ascending = sort_ascending

    def run(self) -> None:
        """线程入口：执行过滤+排序，通过 ``done`` 信号回传结果。"""
        filtered = filter_and_sort(
            self._results,
            self._filter_text,
            self._filter_rules,
            self._filter_severities,
            self._sort_field,
            self._sort_ascending,
        )
        self.done.emit(filtered)  # pyrefly: ignore [missing-attribute]
