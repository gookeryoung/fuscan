"""后台过滤+排序扫描结果：避免大结果集时主线程阻塞。

FilterWorker 在独立 QThread 中执行 filter_and_sort 纯函数，通过信号将
过滤后的元组传回主线程。10 万结果过滤+排序约 50-100ms，移至后台后
UI 不阻塞。

同时在后台构建倒排索引（严重度 / 规则名），通过 ``done``
信号一并传回，避免主线程在 ``set_results`` 阶段同步构建索引阻塞 UI。

信号：
- ``done``：(tuple[ScanResult, ...], dict[Severity, list[int]], dict[str, list[int]])
  过滤+排序后的结果元组 + 严重度倒排索引 + 规则名倒排索引
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide2.QtCore import QThread, Signal

from fuscan.gui.models.result_model import build_indices, filter_and_sort
from fuscan.rules.model import Severity

if TYPE_CHECKING:
    from fuscan.scanner.result import ScanResult

__all__ = ["FilterWorker"]

logger = logging.getLogger(__name__)


class FilterWorker(QThread):  # pyrefly: ignore [invalid-inheritance]
    """后台过滤+排序工作线程。

    同时在后台线程构建倒排索引（严重度/规则名），避免主线程
    在结果集较大（>= ``_INDEX_THRESHOLD``）时同步构建索引阻塞 UI。
    索引仅在结果数 >= ``_INDEX_THRESHOLD`` 时构建；小结果集返回空字典。

    :param results: 原始结果元组
    :param filter_text: 文件路径模糊匹配文本（空串表示不过滤）
    :param filter_rules: 规则名过滤集合（空集合表示不过滤）
    :param filter_severities: 严重度过滤集合（空集合表示不过滤）
    :param sort_field: 排序字段（``SORT_DEFAULT`` / ``SORT_FILE_PATH`` / …）
    :param sort_ascending: True 升序，False 降序
    :param filter_replaced: 已替换维度过滤；None 不过滤，True 仅显示已替换，
        False 仅显示未替换（用于「待处理 / 已替换」Tab 切换）
    """

    # 扩展信号签名，同时回传过滤结果 + 严重度/规则名倒排索引
    done = Signal(tuple, dict, dict)

    def __init__(
        self,
        results: tuple[ScanResult, ...],
        filter_text: str,
        filter_rules: frozenset[str],
        filter_severities: frozenset[Severity],
        sort_field: str,
        sort_ascending: bool,
        *,
        filter_replaced: bool | None = None,
        build_index: bool = True,
        index_threshold: int = 2000,
        index_results: tuple[ScanResult, ...] | None = None,
    ) -> None:
        """初始化过滤线程。

        :param filter_replaced: 已替换维度过滤；None 不过滤，True 仅显示已替换，
            False 仅显示未替换
        :param build_index: 是否在后台同时构建倒排索引（默认 True）
        :param index_threshold: 结果数达到该阈值才构建索引（默认 2000，与
            ``result_model._INDEX_THRESHOLD`` 保持一致）
        :param index_results: 索引构建所使用的结果元组（默认 ``results``）。
            当 ``results`` 为经过倒排索引裁剪后的候选子集时，调用方可传入
            完整原始结果元组，使索引位置与 ``self._results`` 保持一致。
        """
        super().__init__()

        self._results = results
        self._filter_text = filter_text
        self._filter_rules = filter_rules
        self._filter_severities = filter_severities
        self._sort_field = sort_field
        self._sort_ascending = sort_ascending
        self._filter_replaced = filter_replaced
        self._build_index = build_index
        self._index_threshold = index_threshold
        # 索引构建使用的结果集（默认与过滤输入相同；调用方可传入完整结果保持索引位置对齐）
        self._index_results = index_results if index_results is not None else results

    def run(self) -> None:
        """线程入口：执行过滤+排序，同时可选构建倒排索引。"""
        filtered = filter_and_sort(
            self._results,
            self._filter_text,
            self._filter_rules,
            self._filter_severities,
            self._sort_field,
            self._sort_ascending,
            self._filter_replaced,
        )
        # 后台构建倒排索引（仅对索引结果集 >= 阈值时）
        if self._build_index and len(self._index_results) >= self._index_threshold:
            severity_index, rule_index = build_indices(self._index_results)
        else:
            severity_index: dict[Severity, list[int]] = {}
            rule_index: dict[str, list[int]] = {}
        self.done.emit(filtered, severity_index, rule_index)  # pyrefly: ignore [missing-attribute]
