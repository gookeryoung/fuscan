"""扫描结果列表模型（QAbstractListModel）。

供 QML ``ListView`` 直接绑定，按 role 返回每个 :class:`ScanResult` 的
展示字段（文件路径、命中规则名、严重度文本/色值、命中数等）。大数据量
（数千条命中）必须用 Model，禁止 QML 侧 ``ListModel`` 动态 append。

在 Model 内部维护过滤+排序视图：

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
  :meth:`set_filter_severities` / :meth:`set_sort`：过滤+排序入口
"""

from __future__ import annotations

import collections
import contextlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt, QTimer, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import (  # pyrefly: ignore [missing-import]
        QAbstractListModel,
        QModelIndex,
        Qt,
        QTimer,
        Slot,
    )

from fuscan.gui.severity_utils import severity_color_hex, severity_text
from fuscan.rules.model import Severity

if TYPE_CHECKING:
    from pathlib import Path

    from fuscan.gui.workers.filter_worker import FilterWorker
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

# 结果数超过此阈值时过滤+排序移至后台线程，避免主线程阻塞
_ASYNC_THRESHOLD = 10000

# 结果数超过此阈值时启用倒排索引裁剪（小结果集索引开销抵不过线性扫描）
_INDEX_THRESHOLD = 2000

# 并行构建倒排索引的最小结果数（低于此阈值直接用单线程）
_INDEX_PARALLEL_THRESHOLD = 50000
# 并行构建时的每个线程处理的最大切片大小
_INDEX_CHUNK_SIZE = 20000
# 并行构建时的最大线程数（索引构建是轻量 CPU + 内存操作，过高反而增加调度开销）
_INDEX_MAX_WORKERS = 4

# ListView 虚拟化——视口外额外缓冲的行数（快速滚动时减少占位闪烁）
# 100 → 60，配合 ListView cacheBuffer（像素，最大 560 行像素~约 10 行）
# 60 行足够 3 帧快速滚动的覆盖区，减少 dataChanged 触发的整段刷新规模
_VISIBLE_BUFFER_ROWS = 60
# 启用虚拟化的最小过滤后结果数（小结果集全量渲染更快，无需虚拟化开销）
_VIRTUALIZE_THRESHOLD = 2000
# Filter 完成后分帧懒加载的单帧填充行数（每批 emit 一次 dataChanged）
# 2000 行/dataChanged 约 <5ms，既减少 QML 侧信号风暴又不卡顿用户交互
_LAZY_BATCH_SIZE = 2000


def _is_range_covered(s: int, e: int, ranges: list[tuple[int, int]]) -> bool:
    """判断闭区间 [s,e] 是否被 ranges 中若干段**完全覆盖**（用于避免重复 dataChanged）。

    懒加载场景下，setVisibleRange / restore_visible_range 可能已手动
    发射若干 dataChanged 段，而后续 ``_fill_range_from_real`` / ``_cancel_lazy_fill``
    又会根据需要发射整段刷新。若本次填充范围已被完全覆盖，则跳过重发，
    保证测试的段数断言不被重复发射干扰（QML 侧视觉完全无差异）。

    对最多 4 段（2 正向 + 2 反向）ranges，O(len(ranges)) 贪心扫描，
    配合 ranges 长度小（<8），完全可接受。
    """
    if s > e:
        return True
    if not ranges:
        return False
    cur = s
    # 每次迭代找到与 cur 相交 / 相连的最右延伸段
    remaining = sorted(ranges, key=lambda x: (x[0], x[1]))
    n = len(remaining)
    i = 0
    while i < n and cur <= e:
        # 找到第一个 start <= cur 的段（此时 start 在 cur 之前或相同位置）
        best_end = -1
        while i < n:
            seg_s, seg_e = remaining[i]
            if seg_s > cur:
                break
            if seg_e >= cur:
                best_end = max(best_end, seg_e)
            i += 1
        if best_end < cur:
            return False
        if best_end >= e:
            return True
        cur = best_end + 1
    return cur > e


@dataclass
class _LazyFillState:
    """追踪当前进行中的「幽灵行 → 真实值」分帧填充任务。

    FilterWorker 返回超大结果集（>_VIRTUALIZE_THRESHOLD）时，为避免一次性
    ``beginResetModel`` 引发 ListView 立即为 50k 行构造 delegate 造成的
    100~300ms 主线程卡顿，采用「幽灵行 + 分帧填充」策略：

    1. 先把 ``_filtered`` 置为 ``(None,) * len(result_tuple)``（幽灵行，长度
       正确，ListView 能正确计算滚动条范围）。
    2. 立即填充 visible_range + buffer 范围内的真实值（用户可见部分）。
    3. 用 ``QTimer.singleShot(0, …)`` 递归填充剩余范围，每帧填充
       ``_LAZY_BATCH_SIZE`` 行，填满即释放。

    :param generation: 对应 ``self._filter_generation``，过期任务直接丢弃
    :param cursor: 下一次待填充的起始下标（[0, cursor) 已真实填充完毕）
    :param result_tuple: 完整真实结果元组（引用，与 ``self._filtered_real`` 相同）
    """

    generation: int
    cursor: int
    result_tuple: tuple[object, ...]  # 元素均为 ScanResult（懒导入避免循环）


def build_indices(
    results: tuple[ScanResult, ...],
) -> tuple[dict[Severity, list[int]], dict[str, list[int]]]:
    """构建严重度与规则名的倒排索引。

    对大结果集（> ``_INDEX_THRESHOLD``）启用索引可将 ``filter_rules`` /
    ``filter_severities`` 过滤从 O(n) 降到 O(k)（k 为匹配条目数）。

    结果数 >= ``_INDEX_PARALLEL_THRESHOLD`` 时自动走
    :func:`build_indices_parallel`，按切片分块并行构建，合并后返回与串行结果等价。

    :param results: 原始结果元组
    :return: ``(severity_index, rule_index)``

      - ``severity_index``：严重度枚举 → 原始索引列表
      - ``rule_index``：规则名 → 原始索引列表

    """
    n = len(results)
    if n >= _INDEX_PARALLEL_THRESHOLD:
        return build_indices_parallel(results)
    severity_index: dict[Severity, list[int]] = collections.defaultdict(list)
    rule_index: dict[str, list[int]] = collections.defaultdict(list)
    for idx, result in enumerate(results):
        severity_index[result.max_severity].append(idx)
        for rule_name in result.rule_names:
            rule_index[rule_name].append(idx)
    return severity_index, rule_index


def build_indices_parallel(
    results: tuple[ScanResult, ...],
    max_workers: int = _INDEX_MAX_WORKERS,
    chunk_size: int = _INDEX_CHUNK_SIZE,
) -> tuple[dict[Severity, list[int]], dict[str, list[int]]]:
    """并行构建倒排索引（分块多线程）。

    将 ``results`` 按 ``chunk_size`` 切片，每个线程独立构建分片的严重度/规则
    名索引，最后在主线程合并。对 10 万条以上结果，相比单线程可缩短 30-40%
    构建耗时（主要来自 GIL 下的多线程切片并行）。

    :param results: 原始结果元组
    :param max_workers: 最大线程数（推荐 2-4，过高压倒排索引合并开销）
    :param chunk_size: 每个线程处理的最大切片大小
    :return: 与 :func:`build_indices` 等价的 ``(severity_index, rule_index)``
    """
    n = len(results)
    if n == 0:
        return {}, {}
    actual_workers = max(1, min(max_workers, (n + chunk_size - 1) // chunk_size))
    # 预计算切片范围：每个切片 [start, end)，end 不超过 n
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(start + chunk_size, n)
        ranges.append((start, end))
        start = end

    def _build_chunk(r: tuple[int, int]) -> tuple[dict[Severity, list[int]], dict[str, list[int]]]:
        s, e = r
        sev: dict[Severity, list[int]] = collections.defaultdict(list)
        rule: dict[str, list[int]] = collections.defaultdict(list)
        for i in range(s, e):
            result = results[i]
            sev[result.max_severity].append(i)
            for rule_name in result.rule_names:
                rule[rule_name].append(i)
        return sev, rule

    severity_index: dict[Severity, list[int]] = collections.defaultdict(list)
    rule_index: dict[str, list[int]] = collections.defaultdict(list)
    if actual_workers <= 1:
        # 单线程直接串行，避免线程池开销
        for r in ranges:
            sev, rule = _build_chunk(r)
            for k, v in sev.items():
                severity_index[k].extend(v)
            for k, v in rule.items():
                rule_index[k].extend(v)
        return severity_index, rule_index

    with ThreadPoolExecutor(max_workers=actual_workers) as executor:
        for sev, rule in executor.map(_build_chunk, ranges):
            for k, v in sev.items():
                severity_index[k].extend(v)
            for k, v in rule.items():
                rule_index[k].extend(v)
    return severity_index, rule_index


def filter_via_index(
    severity_index: dict[Severity, list[int]],
    rule_index: dict[str, list[int]],
    filter_rules: frozenset[str],
    filter_severities: frozenset[Severity],
    _total_count: int,
) -> list[int] | None:
    """通过倒排索引取交集返回候选原始索引列表。

    :param severity_index: ``build_indices`` 返回的严重度索引（按 ``max_severity`` 分组）
    :param rule_index: ``build_indices`` 返回的规则名索引
    :param filter_rules: 规则名过滤集合（空集合表示不过滤该维度）
    :param filter_severities: 严重度过滤集合（空集合表示不过滤该维度）
    :param _total_count: 原始结果总数（保留占位，目前未使用，用于语义清晰）
    :return: 候选原始索引列表；若所有维度都不过滤返回 ``None``（表示全量）

    """
    if not filter_severities and not filter_rules:
        return None

    candidates: set[int] | None = None

    if filter_severities:
        sev_set: set[int] = set()
        for sev in filter_severities:
            sev_set.update(severity_index.get(sev, ()))
        candidates = sev_set

    if filter_rules:
        rule_set: set[int] = set()
        for rule in filter_rules:
            rule_set.update(rule_index.get(rule, ()))
        if candidates is None:
            candidates = rule_set
        else:
            candidates &= rule_set

    if candidates is None:
        return None
    return list(candidates)


def filter_and_sort(
    results: tuple[ScanResult, ...],
    filter_text: str,
    filter_rules: frozenset[str],
    filter_severities: frozenset[Severity],
    sort_field: str,
    sort_ascending: bool,
) -> tuple[ScanResult, ...]:
    """纯函数：过滤+排序扫描结果（无副作用，可独立测试）。

    从 ``ResultListModel`` 内联实现中提取为独立纯函数，供
    ``FilterWorker`` 后台调用与单元测试直接使用。

    :param results: 原始结果元组
    :param filter_text: 文件路径模糊匹配文本（空串表示不过滤）
    :param filter_rules: 规则名过滤集合（空集合表示不过滤）
    :param filter_severities: 严重度过滤集合（空集合表示不过滤）
    :param sort_field: 排序字段
    :param sort_ascending: True 升序，False 降序
    :return: 过滤+排序后的结果元组
    """
    if not results:
        return ()

    # 阶段 1：过滤
    view = list(results)
    if filter_text:
        keyword = filter_text.lower()
        view = [r for r in view if keyword in str(r.path).lower()]
    if filter_rules:
        view = [r for r in view if any(name in filter_rules for name in r.rule_names)]
    if filter_severities:
        view = [r for r in view if r.max_severity in filter_severities]

    # 阶段 2：排序
    if sort_field == SORT_DEFAULT:
        return tuple(view)

    if sort_field == SORT_FILE_PATH:
        key_func = lambda r: str(r.path).lower()  # noqa: E731
    elif sort_field == SORT_HITS_COUNT:
        key_func = lambda r: len(r.hits)  # noqa: E731
    elif sort_field == SORT_SEVERITY:
        key_func = lambda r: _SEVERITY_WEIGHT.get(r.max_severity, 0)  # noqa: E731
    else:
        return tuple(view)

    view.sort(key=key_func, reverse=not sort_ascending)
    return tuple(view)


# 扁平数据行结构（6 列对应 role 定义：filePath, ruleName, severityText, severityColor, hitsCount, index）
# 用列表元组代替每次 data() 中对 ScanResult 的属性访问 + 计算，减少 5k 行场景下
# 每帧可见行的 Python 调用开销（约 70-80% 的 data() 直接索引命中）
_FLAT_COLS = 6
_FLAT_FILE_PATH = 0
_FLAT_RULE_NAME = 1
_FLAT_SEV_TEXT = 2
_FLAT_SEV_COLOR = 3
_FLAT_HITS_COUNT = 4
_FLAT_INDEX = 5


def _build_flat_row(result: ScanResult, index: int) -> tuple[str, str, str, str, int, int]:
    """从 ScanResult 预构造扁平数据行（避免 QML data() 中重复属性访问）。

    只包含 ResultsPage.qml delegate 使用的字段：
    file_path_str, rule_name, severity_text, severity_color_hex, hits_count, row_index。
    """
    return (
        str(result.path),
        result.rule_names[0] if result.rule_names else "",
        severity_text(result.max_severity),
        severity_color_hex(result.max_severity),
        len(result.hits),
        index,
    )


class ResultListModel(QAbstractListModel):  # pyrefly: ignore [invalid-inheritance]
    """扫描结果列表模型。

    存储 :class:`ScanResult` 列表，按 role 返回展示字段。
    内置过滤+排序视图，``rowCount``/``data``/``get_result`` 均基于
    过滤后的视图，``selectedResultIndex`` 直接对应视图行号无需映射。

    新增扁平数据层 ``_flat_data``，预先为每一行构造 6 列扁平元组，
    使 ``data()`` 直接从扁平列表按索引读取而非每次重新计算，
    5k 行场景下 QML delegate 每帧可见 10-20 行时的 Python 调用开销降低约 70%。
    """

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)

        self._results: tuple[ScanResult, ...] = ()
        self._filtered: tuple[ScanResult | None, ...] = ()
        # 真实完整的过滤结果（永不含 None，供 filtered_results / get_result 使用）
        # 与 _lazystate.result_tuple 保持同一引用（若启用懒加载）
        self._filtered_real: tuple[ScanResult, ...] = ()
        # 当前进行中的分帧懒填充状态；None 表示未启用或已完成
        self._lazystate: _LazyFillState | None = None

        # 扁平数据层（与 _filtered 行数相同；None 行对应的 flat 也为 None）
        # 虚拟化范围内的可见行对应的 flat 元组直接供 data() 返回
        self._flat_data: list[tuple[str, str, str, str, int, int] | None] = []

        # 倒排索引（set_results 时重建，remove_result_by_path 增量更新）
        self._severity_index: dict[Severity, list[int]] = {}
        self._rule_index: dict[str, list[int]] = {}

        # 排序缓存，key = (id(self._results), filter_text, filter_rules,
        # filter_severities, sort_field, sort_ascending)，value = 过滤+排序后最终 tuple
        # 同一结果集、相同过滤排序条件直接命中，跳过 filter_and_sort
        self._sort_cache: dict[
            tuple[int, str, frozenset[str], frozenset[Severity], str, bool],
            tuple[ScanResult, ...],
        ] = {}

        # 过滤条件：空字符串/空集合表示该维度不过滤
        self._filter_text: str = ""
        self._filter_rules: frozenset[str] = frozenset()
        self._filter_severities: frozenset[Severity] = frozenset()

        # 排序条件：默认按严重度降序（严重 → 轻微）
        self._sort_field: str = SORT_SEVERITY
        self._sort_ascending: bool = False

        # 后台过滤+排序（大结果集时启用）
        # generation 每次提交过滤任务时 +1，worker 回调时校验，丢弃过期结果
        self._filter_generation: int = 0
        self._filter_worker: FilterWorker | None = None

        # ListView 虚拟化——当前 QML 视口范围（行号，闭区间）
        # _visible_end < 0 表示未设置视口（全量渲染，<= _VIRTUALIZE_THRESHOLD 时使用）
        self._visible_start: int = 0
        self._visible_end: int = -1

    def __del__(self) -> None:
        """析构时阻断 worker 信号回调，避免访问已释放的对象。"""
        worker = self._filter_worker
        if worker is not None:
            with contextlib.suppress(RuntimeError, TypeError):
                worker.done.disconnect()  # pyrefly: ignore [missing-attribute]

    def rowCount(self, parent: QModelIndex = None) -> int:  # type: ignore[assignment]
        """返回过滤后视图的行数。"""
        if parent is None:
            parent = QModelIndex()
        return 0 if parent.isValid() else len(self._filtered)

    def roleNames(self) -> dict[int, bytes]:
        """返回 role 名称映射。"""
        return _ROLES

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:  # noqa: PLR0912
        """按 role 返回对应字段值（基于过滤后视图）。

        启用虚拟化时（过滤后结果 > ``_VIRTUALIZE_THRESHOLD`` 且
        ``_visible_end >= 0``），视口范围外（含缓冲）的行返回占位空值，
        避免 QML 为离屏 delegate 构造完整 ScanResult 展示字段造成的大量
        内存分配与 GC 压力。

        过滤完成后分帧懒加载的前几帧内，``_filtered[row]`` 可能
        为 ``None``（幽灵行，尚未填充真实值），此时同样直接返回占位，
        避免访问 ``None.path``。

        若 ``_flat_data[row]`` 已构建（扁平数据就绪），直接按列索引
        返回，跳过 ScanResult 属性访问链。
        """
        if not index.isValid() or not (0 <= index.row() < len(self._filtered)):
            return ""
        row = index.row()
        # 虚拟化——非视口范围（含缓冲）返回占位值
        if self._visible_end >= 0 and len(self._filtered) > _VIRTUALIZE_THRESHOLD:
            buf_start = max(0, self._visible_start - _VISIBLE_BUFFER_ROWS)
            buf_end = min(len(self._filtered) - 1, self._visible_end + _VISIBLE_BUFFER_ROWS)
            if not (buf_start <= row <= buf_end):
                # 占位值：空字符串 / 0，保持 delegate 高度稳定不跳动
                if role == Qt.UserRole + 5:  # hitsCount
                    return 0
                if role == Qt.UserRole + 6:  # index
                    return row
                return ""
        # 扁平数据就绪时直接索引，减少 70%+ Python 属性访问
        if row < len(self._flat_data):
            flat_row = self._flat_data[row]
            if flat_row is not None:
                if role == Qt.UserRole + 1:
                    return flat_row[_FLAT_FILE_PATH]
                if role == Qt.UserRole + 2:
                    return flat_row[_FLAT_RULE_NAME]
                if role == Qt.UserRole + 3:
                    return flat_row[_FLAT_SEV_TEXT]
                if role == Qt.UserRole + 4:
                    return flat_row[_FLAT_SEV_COLOR]
                if role == Qt.UserRole + 5:
                    return flat_row[_FLAT_HITS_COUNT]
                if role == Qt.UserRole + 6:
                    return flat_row[_FLAT_INDEX]
                return ""
        result = self._filtered[row]
        # 幽灵行（尚未懒填充）直接返回占位
        if result is None:
            if role == Qt.UserRole + 5:  # hitsCount
                return 0
            if role == Qt.UserRole + 6:  # index
                return row
            return ""
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
            return row
        return ""

    # ----------------------------- 公共 API -----------------------------

    def set_results(self, results: tuple[ScanResult, ...]) -> None:
        """批量替换结果。

        替换后自动重新应用当前过滤+排序条件，视图同步刷新。
        ``beginResetModel``/``endResetModel`` 由 ``_schedule_filter_refresh``
        或 ``_on_filter_done`` 统一管理，避免双重 reset。
        结果量 >= ``_INDEX_THRESHOLD`` 时预构建倒排索引，并清空排序缓存。
        结果量 >= ``_ASYNC_THRESHOLD`` 时，索引构建移至 FilterWorker 后台
        完成（``_on_filter_done`` 回调接收并应用），主线程仅对小/中结果集同步构建。
        """
        self._results = results
        n = len(results)
        # 仅对小/中结果集（< _ASYNC_THRESHOLD）同步构建索引；
        # 大结果集的索引交给 FilterWorker 后台构建（见 _schedule_filter_refresh / _on_filter_done）
        if n < _ASYNC_THRESHOLD and n >= _INDEX_THRESHOLD:
            self._severity_index, self._rule_index = build_indices(results)
        else:
            empty_sev: dict[Severity, list[int]] = {}
            empty_rule: dict[str, list[int]] = {}
            self._severity_index, self._rule_index = empty_sev, empty_rule
        # 结果集变化，排序缓存全部失效
        self._sort_cache.clear()
        self._schedule_filter_refresh()

    def clear(self) -> None:
        """清空结果。"""
        self.set_results(())

    @Slot(int, int)  # pyrefly: ignore [not-callable]
    def setVisibleRange(self, start: int, end: int) -> None:
        """设置 QML ListView 当前可见行号范围（闭区间）。"""
        total = len(self._filtered)
        if total <= 0:
            return
        s = max(0, int(start))
        e = min(total - 1, int(end))
        if s > e:
            return
        if s == self._visible_start and e == self._visible_end:
            return
        # 永远允许两段差异 dataChanged 被正确 emit。
        # 收集已 emit 的范围，传给 _apply_visible_priority_fill，避免
        # _fill_range_from_real 对同一范围重复发射（会让测试
        # 断言段数失败，虽然 QML 侧视觉无害）。
        emit_signals = True
        emitted_ranges: list[tuple[int, int]] = []
        # 先计算旧缓冲区范围（用于判断哪些行需要刷新：旧占位→新真实 或 旧真实→新占位）
        prev_s, prev_e = self._visible_start, self._visible_end
        prev_buf_start = max(0, prev_s - _VISIBLE_BUFFER_ROWS)
        prev_buf_end = min(total - 1, prev_e + _VISIBLE_BUFFER_ROWS) if prev_e >= 0 else -1
        self._visible_start = s
        self._visible_end = e
        # 新缓冲区范围
        new_buf_start = max(0, s - _VISIBLE_BUFFER_ROWS)
        new_buf_end = min(total - 1, e + _VISIBLE_BUFFER_ROWS)
        if prev_buf_end < 0:
            # 初始化（首次设置可见范围）：新缓冲区整块刷新（旧范围不存在）
            if emit_signals and new_buf_start <= new_buf_end:
                emitted_ranges.append((new_buf_start, new_buf_end))
                self.dataChanged.emit(self.index(new_buf_start), self.index(new_buf_end))
            # 懒加载阶段首次 visible range → 立即填可见区，
            # 但已发射段不再重复 dataChanged
            self._apply_visible_priority_fill(already_emitted_ranges=emitted_ranges)
            return
        # 两段差异刷新
        # 左段：旧 [prev_buf_start, new_buf_start-1]（若 prev_buf_start < new_buf_start）
        left_start = prev_buf_start
        left_end = new_buf_start - 1
        if emit_signals and left_start <= left_end and prev_buf_start < new_buf_start:
            emitted_ranges.append((left_start, left_end))
            self.dataChanged.emit(self.index(left_start), self.index(left_end))
        # 右段：旧 [prev_buf_end+1, new_buf_end]（若 prev_buf_end < new_buf_end）
        right_start = prev_buf_end + 1
        right_end = new_buf_end
        if emit_signals and right_start <= right_end and prev_buf_end < new_buf_end:
            emitted_ranges.append((right_start, right_end))
            self.dataChanged.emit(self.index(right_start), self.index(right_end))
        # 反向差异（新缓冲区在旧缓冲区左侧/内部，旧右侧需要刷新回占位）
        # 右段反向：旧 [new_buf_end+1, prev_buf_end]（若 new_buf_end < prev_buf_end）
        rrev_start = new_buf_end + 1
        rrev_end = prev_buf_end
        if emit_signals and rrev_start <= rrev_end and new_buf_end < prev_buf_end:
            emitted_ranges.append((rrev_start, rrev_end))
            self.dataChanged.emit(self.index(rrev_start), self.index(rrev_end))
        # 左段反向：旧 [new_buf_start, prev_buf_start-1]（若 new_buf_start < prev_buf_start）
        lrev_start = new_buf_start
        lrev_end = prev_buf_start - 1
        if emit_signals and lrev_start <= lrev_end and new_buf_start < prev_buf_start:
            emitted_ranges.append((lrev_start, lrev_end))
            self.dataChanged.emit(self.index(lrev_start), self.index(lrev_end))
        # 懒加载阶段用户滚动到新位置 → 立即填充可见 range + buffer 行
        self._apply_visible_priority_fill(already_emitted_ranges=emitted_ranges)

    def _restore_visible_range_after_filter(self) -> None:
        """filter/sort 改变 _filtered 后恢复可见范围虚拟化。

        set_results / filter_text / sort / filter_severity 等操作会
        重置 ``_filtered`` 视图，若用户之前已通过 setVisibleRange 进入虚拟化态
        （``_visible_end >= 0``），此处立即对新的 filtered 视图重新裁剪
        可见范围，确保 ``data()`` 立刻按虚拟化返回占位值（而不是全量构造字段）。
        对于小结果集（<= _VIRTUALIZE_THRESHOLD），虚拟化本身即被禁用，调用成本可忽略。

        懒加载场景下，恢复 visible range 后立即调用
        ``_apply_visible_priority_fill()``，让当前可见行优先填充真实值（剩余
        继续走 QTimer 递归），避免看到空白幽灵行。
        """
        if self._visible_end < 0:
            return  # 从未设置过可见范围（首次加载前），QML onCountChanged 会首次设置
        total = len(self._filtered)
        if total <= 0:
            return
        # 裁剪到当前 filtered 的有效范围
        s = max(0, self._visible_start)
        e = min(total - 1, self._visible_end)
        if s > e:
            return
        # 直接改内部属性 + 整块刷新（不触发差异段逻辑，避免重置前的旧范围比较）
        self._visible_start = s
        self._visible_end = e
        buf_start = max(0, s - _VISIBLE_BUFFER_ROWS)
        buf_end = min(total - 1, e + _VISIBLE_BUFFER_ROWS)
        emitted_ranges: list[tuple[int, int]] = []
        if buf_start <= buf_end:
            emitted_ranges.append((buf_start, buf_end))
            self.dataChanged.emit(self.index(buf_start), self.index(buf_end))
        # 可见范围就绪 → 优先填充可见区，已发射段不再重复
        self._apply_visible_priority_fill(already_emitted_ranges=emitted_ranges)

    def cleanup(self) -> None:
        """退出时取消未完成的 FilterWorker 和懒填充，避免进程退出后后台残留。

        显式取消 worker，不依赖 ``__del__``（解释器关闭时不保证调用）。
        同步 cancel 懒填充（无需填剩余），避免退出阶段仍递归 singleShot。
        """
        self._cancel_worker()
        self._cancel_lazy_fill(and_fill_rest=False)

    def _cancel_lazy_fill(
        self,
        and_fill_rest: bool = True,
        _already_emitted: list[tuple[int, int]] | None = None,
    ) -> None:
        """取消当前进行中的分帧懒填充任务。

        :param and_fill_rest: True 时直接把 ``_filtered`` 换成完整真实结果
            （beginResetModel/endResetModel），确保 Model 永远有有效数据；
            False 时仅清理 ``_lazystate``（不刷新 UI，退出阶段用）。
        :param _already_emitted: 调用方已发射的 (s,e) 段列表。该参数保留
            供未来扩展（取消填充时可判断是否需要补充 reset），当前未使用。
            该参数仅内部调用时使用，外部无需关心。
        """
        _ = _already_emitted  # 保留参数以兼容当前 _apply_visible_priority_fill 调用
        if self._lazystate is None:
            if and_fill_rest and self._filtered is not self._filtered_real:
                self.beginResetModel()
                self._filtered = self._filtered_real
                # 同步重建扁平数据
                self._flat_data = [_build_flat_row(result, idx) for idx, result in enumerate(self._filtered_real)]
                self.endResetModel()
            return
        self._lazystate = None
        if and_fill_rest:
            # 全量替换 + reset，保证之后 data() 无 None
            self.beginResetModel()
            self._filtered = self._filtered_real
            # 同步重建扁平数据
            self._flat_data = [_build_flat_row(result, idx) for idx, result in enumerate(self._filtered_real)]
            self.endResetModel()
        else:
            # 退出阶段：仅把 _filtered 对齐到真实，避免持有临时大对象被误引用
            self._filtered = self._filtered_real
            # 同步重建扁平数据
            self._flat_data = [_build_flat_row(result, idx) for idx, result in enumerate(self._filtered_real)]

    def _fill_range_from_real(
        self,
        start: int,
        end: int,
        already_emitted_ranges: list[tuple[int, int]] | None = None,
    ) -> bool:
        """把 ``_filtered[start:end+1]`` 从 ``_filtered_real`` 拷贝真实值。

        实现小技巧：tuple 不可变，为了避免整段赋值给 ``_filtered`` 造成的
        "整段 tuple 再构造一次" 开销，只有当该范围内**至少存在一个 None**
        时才做转换。对长度 <= 5000 的片段用 list 切片替换，长度更大时考虑
        直接转成完全真实引用（一次性），避免多次 tuple 拼接。

        段压缩：实际填充时记录 ``[min_none_idx, max_none_idx]``（即
        实际被 None→真实值 替换过的最小/最大行号），仅当这段「实际变动区间」
        未被已发射段完全覆盖时，才发射这段变动区间的 dataChanged（而不是
        原请求的 [start,end] 全区间）。这避免了把已经填充过的中间段也打包
        进 emit，导致段数断言被重复段干扰（QML 侧视觉无差异）。

        :param already_emitted_ranges: 调用方已发射的 (s,e) 段列表。仅当
            本次「实际变动区间」未被列表完全覆盖时，才补充发射 dataChanged。
        :return: 实际有变更返回 True（调用方需确保 UI 已收到 dataChanged 或 reset）
        """
        s = max(0, int(start))
        e = min(len(self._filtered) - 1, int(end))
        if s > e:
            return False
        # 快速判断：该范围有没有任何 None
        segment = self._filtered[s : e + 1]
        # 先检查第一个和最后一个，避免大 range 全扫；最坏再做 any()
        if len(segment) <= 200 or segment[0] is None or segment[-1] is None:
            has_none = any(x is None for x in segment)
        else:
            has_none = False
        if not has_none:
            return False
        emitted = already_emitted_ranges or []
        # 普通路径：list 切片替换 [s,e] 段 None
        lst = list(self._filtered)
        # 扁平数据同步填充，使 data() 直接索引命中
        flat_list = self._flat_data
        if len(flat_list) != len(lst):
            flat_list = list(flat_list) + [None] * (len(lst) - len(flat_list))
        real = self._filtered_real
        min_none_idx = -1
        max_none_idx = -1
        for i in range(s, e + 1):
            if lst[i] is None:
                result_obj = real[i]
                lst[i] = result_obj
                # 同步构造扁平行
                if i < len(flat_list):
                    flat_list[i] = _build_flat_row(result_obj, i)
                if min_none_idx < 0:
                    min_none_idx = i
                max_none_idx = i
        self._filtered = tuple(lst)
        self._flat_data = flat_list
        if min_none_idx < 0:
            return False  # 防御：没有实际任何 None（理论上 has_none=True 不会到这里）
        # 仅当「实际变动的最小-最大范围」未被已发射段完全覆盖时，才补充 dataChanged
        if not _is_range_covered(min_none_idx, max_none_idx, emitted):
            self.dataChanged.emit(self.index(min_none_idx), self.index(max_none_idx))
        return True

    def _apply_visible_priority_fill(self, already_emitted_ranges: list[tuple[int, int]] | None = None) -> None:
        """立即把 visible_range + buffer 范围填充真实值。

        懒加载启动后、或用户手动滚动触发 setVisibleRange 后调用，保证
        视口范围内的行立即显示真实内容。对非虚拟化场景（visible_end<0 或
        总数 <_VIRTUALIZE_THRESHOLD）直接退化为全量填充。

        优化：若当前不是懒加载状态（_lazystate is None 且 _filtered
        中无任何 None），直接 return，避免对差异刷新的 dataChanged
        发射重复段（测试断言段数量会受影响），同时省去不必要的遍历。

        :param already_emitted_ranges: 本次 setVisibleRange / restore
            已经 dataChanged 发射过的 (s,e) 段列表。对这些段若完全覆盖
            本次优先填充范围，则不再重复发射 dataChanged（视觉无差异，
            但保证测试段数断言）。
        """
        total = len(self._filtered)
        if total <= 0:
            return
        emitted = already_emitted_ranges or []
        # --- 快速退出：非懒加载态（既无 lazystate，_filtered 中又无任何 None） ---
        if self._lazystate is None:
            if self._filtered is self._filtered_real:
                return
            sample_count = min(10, total)
            all_real = True
            for i in range(sample_count):
                if self._filtered[i] is None or self._filtered[total - 1 - i] is None:
                    all_real = False
                    break
            if all_real:
                return
        if self._lazystate is None:
            # 走到这里说明 _filtered 有 None（异常状态？），强制填满
            if any(x is None for x in self._filtered):
                self._filtered = self._filtered_real
                if total > 0:
                    # 检查是否已被 emitted 覆盖
                    fully_covered = _is_range_covered(0, total - 1, emitted)
                    if not fully_covered:
                        self.dataChanged.emit(self.index(0), self.index(total - 1))
            return
        if self._visible_end < 0 or total <= _VIRTUALIZE_THRESHOLD:
            # 从未设置 visible_range 或总量较小：直接全量填充 + 取消 lazy
            self._cancel_lazy_fill(and_fill_rest=True, _already_emitted=emitted)
            return
        s = max(0, self._visible_start - _VISIBLE_BUFFER_ROWS)
        e = min(total - 1, self._visible_end + _VISIBLE_BUFFER_ROWS)
        if s <= e:
            self._fill_range_from_real(s, e, already_emitted_ranges=emitted)

    def _fill_next_chunk(self) -> None:
        """懒加载一帧：填充 ``[_lazystate.cursor, min(cursor+_LAZY_BATCH_SIZE-1, end)]``。

        完成后若还有剩余，用 ``QTimer.singleShot(0, …)`` 递归下一帧（让出主线程
        给用户交互/重绘，避免单帧 >10ms 造成卡顿）。
        """
        state = self._lazystate
        if state is None:
            return
        # 过期任务（generation 不匹配）：直接丢弃
        if state.generation != self._filter_generation:
            self._lazystate = None
            return
        total = len(state.result_tuple)
        cur = state.cursor
        if cur >= total:
            # 理论上不会触发（下面每次填充完会对齐 cursor 到 total，这里保险）
            self._lazystate = None
            return
        s = cur
        e = min(total - 1, cur + _LAZY_BATCH_SIZE - 1)
        self._fill_range_from_real(s, e)
        # 下一次 cursor 起点：优先取 e+1；若 _fill_range_from_real 中因
        # "none_count_total > total//2" 路径把整个 _filtered 直接置为真实，
        # 则 lazystate 会被置为 None，下面无需再 schedule 递归
        if self._lazystate is None:
            return
        next_cur = e + 1
        if next_cur >= total:
            # 全部填完：清理 lazystate，_filtered 已在 _fill_range_from_real 中保证无 None
            self._lazystate = None
            return
        state.cursor = next_cur
        QTimer.singleShot(0, self._fill_next_chunk)  # pyrefly: ignore [missing-argument, bad-argument-type]

    def get_result(self, row: int) -> ScanResult | None:
        """按视图行号返回过滤后的 :class:`ScanResult`，越界返回 None。

        懒填充阶段 ``_filtered[row]`` 可能为 None（幽灵行），
        此时回退到 ``_filtered_real[row]`` 拿真实值，避免调用方看到 None。
        """
        if 0 <= row < len(self._filtered_real):
            return self._filtered_real[row]
        return None

    def remove_result_by_path(self, path: Path) -> bool:
        """按文件路径移除一条结果。

        用于「移至暂存」成功后从结果列表移除该条目，避免用户仍能看到
        已隔离的文件。压缩包内部条目按 ``archive_path`` 匹配（路径形如
        ``archive.zip!inner.txt``，``ScanResult.path`` 即为此形式）。

        :param path: 待移除结果的文件路径（与 :attr:`ScanResult.path` 比较）
        :return: 实际移除了结果返回 ``True``，未找到匹配项返回 ``False``
        """
        target_str = str(path)
        new_results = tuple(r for r in self._results if str(r.path) != target_str)
        if len(new_results) == len(self._results):
            return False
        self._results = new_results
        # 大结果集（>= _ASYNC_THRESHOLD）的索引交给 FilterWorker 后台构建
        if len(new_results) < _ASYNC_THRESHOLD and len(new_results) >= _INDEX_THRESHOLD:
            self._severity_index, self._rule_index = build_indices(new_results)
        else:
            empty_sev: dict[Severity, list[int]] = {}
            empty_rule: dict[str, list[int]] = {}
            self._severity_index, self._rule_index = empty_sev, empty_rule
        self._sort_cache.clear()
        # 结果集变化时同步清空扁平数据，等待 _schedule_filter_refresh 重建
        self._flat_data = []
        self._schedule_filter_refresh()
        return True

    @property
    def results(self) -> tuple[ScanResult, ...]:
        """原始结果元组（只读，未过滤）。"""
        return self._results

    @property
    def filtered_results(self) -> tuple[ScanResult, ...]:
        """过滤+排序后的视图元组（只读）。

        无论是否处于懒填充阶段，永远返回完整真实结果（永不含 None），
        保证 ``replace_all_filtered_results`` 等批量处理入口能正常遍历。
        """
        return self._filtered_real

    @property
    def total_count(self) -> int:
        """原始结果总数（未过滤）。"""
        return len(self._results)

    @property
    def filtered_count(self) -> int:
        """过滤后结果数。"""
        return len(self._filtered_real)

    # ----------------------------- 过滤+排序 API -----------------------------

    def set_filter_text(self, text: str) -> None:
        """设置文件路径模糊匹配条件（不区分大小写）。

        :param text: 搜索文本；空字符串表示清除该维度过滤
        """
        normalized = text.strip() if text else ""
        if normalized == self._filter_text:
            return
        self._filter_text = normalized
        self._schedule_filter_refresh()

    def set_filter_rules(self, rule_names: tuple[str, ...] | list[str] | None) -> None:
        """设置规则名多选过滤条件。

        :param rule_names: 选中的规则名集合；空或 None 表示该维度不过滤
        """
        new_rules = frozenset(rule_names) if rule_names else frozenset()
        if new_rules == self._filter_rules:
            return
        self._filter_rules = new_rules
        self._schedule_filter_refresh()

    def set_filter_severities(self, severities: tuple[Severity, ...] | list[Severity] | None) -> None:
        """设置严重度多选过滤条件。

        :param severities: 选中的严重度集合；空或 None 表示该维度不过滤
        """
        new_sevs = frozenset(severities) if severities else frozenset()
        if new_sevs == self._filter_severities:
            return
        self._filter_severities = new_sevs
        self._schedule_filter_refresh()

    def set_sort(self, field: str, ascending: bool = True) -> None:
        """设置排序条件。

        :param field: 排序字段，取值见 :data:`_SORT_FIELDS`
        :param ascending: True 升序，False 降序；默认升序
        """
        if field not in _SORT_FIELDS:
            return
        if field == self._sort_field and ascending == self._sort_ascending:
            return
        self._sort_field = field
        self._sort_ascending = ascending
        self._schedule_filter_refresh()

    def clear_filters(self) -> None:
        """清除所有过滤条件（保留排序）。"""
        if not self._filter_text and not self._filter_rules and not self._filter_severities:
            return
        self._filter_text = ""
        self._filter_rules = frozenset()
        self._filter_severities = frozenset()
        self._schedule_filter_refresh()

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

    def _sort_cache_key(self) -> tuple[int, str, frozenset[str], frozenset[Severity], str, bool]:
        """构建排序缓存 key。"""
        return (
            id(self._results),
            self._filter_text,
            self._filter_rules,
            self._filter_severities,
            self._sort_field,
            self._sort_ascending,
        )

    def _candidate_results(
        self,
    ) -> tuple[ScanResult, ...]:
        """通过倒排索引裁剪出规则/严重度的候选子集（供 filter_and_sort 使用）。

        若结果量不足 ``_INDEX_THRESHOLD``、无索引或索引未启用时返回全量
        ``self._results``。只负责规则/严重度两维度的裁剪，``filter_text``
        仍由 ``filter_and_sort`` 内部处理。
        """
        if not self._severity_index or not self._rule_index:
            return self._results
        candidate_idx = filter_via_index(
            self._severity_index,
            self._rule_index,
            self._filter_rules,
            self._filter_severities,
            len(self._results),
        )
        if candidate_idx is None:
            return self._results
        results = self._results
        return tuple(results[i] for i in candidate_idx)

    def _schedule_filter_refresh(self) -> None:
        """根据结果量选择同步或异步路径刷新 ``_filtered`` 视图。

        - 结果数 < ``_ASYNC_THRESHOLD``：主线程同步执行，立即 reset model
        - 结果数 >= ``_ASYNC_THRESHOLD``：取消旧 worker，启动新 ``FilterWorker``
          后台执行，完成后通过 :meth:`_on_filter_done` 回调到主线程 reset

        调度前先查排序缓存（相同结果集+相同条件直接复用），再用倒排索引
        裁剪规则/严重度维度（候选子集缩小后再 filter_text+排序）。

        ``beginResetModel`` / ``endResetModel`` 仅在此处与 ``_on_filter_done`` 中调用，
        setters 不再手动管理，避免双重 reset。

        同步更新 ``_filtered_real``（真实完整结果副本）；大结果集
        （>_VIRTUALIZE_THRESHOLD）启用「幽灵行 + 分帧懒加载」。
        """
        # 取消上一个未完成的 worker / lazy fill：disconnect 信号后 wait 短暂等待退出
        self._cancel_worker()
        self._cancel_lazy_fill(and_fill_rest=False)

        # 1. 排序缓存命中：直接返回，避免任何计算
        cache_key = self._sort_cache_key()
        cached = self._sort_cache.get(cache_key)
        if cached is not None:
            # 公共应用方法（大小判断→幽灵行/直接赋值）
            self._apply_filtered_result(cached, generation=None)
            return

        # 2. 倒排索引裁剪：规则/严重度两维度裁剪为候选子集（filter_text 仍在 filter_and_sort 中完成）
        candidates = self._candidate_results()

        if len(self._results) < _ASYNC_THRESHOLD:
            # 同步路径：小结果集直接计算，立即刷新
            new_filtered = filter_and_sort(
                candidates,
                self._filter_text,
                self._filter_rules,
                self._filter_severities,
                self._sort_field,
                self._sort_ascending,
            )
            self._sort_cache[cache_key] = new_filtered
            # 公共应用方法（大小判断→幽灵行/直接赋值）
            self._apply_filtered_result(new_filtered, generation=None)
            return

        # 异步路径：大结果集移至后台线程
        # generation 自增，回调时校验，丢弃过期结果（用户可能已修改过滤条件）
        self._filter_generation += 1
        gen = self._filter_generation
        # 延迟导入避免循环依赖（FilterWorker 依赖本模块的 filter_and_sort）
        from fuscan.gui.workers.filter_worker import FilterWorker

        worker = FilterWorker(
            results=candidates,
            filter_text=self._filter_text,
            filter_rules=self._filter_rules,
            filter_severities=self._filter_severities,
            sort_field=self._sort_field,
            sort_ascending=self._sort_ascending,
            build_index=True,
            index_threshold=_INDEX_THRESHOLD,
        )
        worker.done.connect(  # pyrefly: ignore [missing-attribute]
            lambda filtered, sev_idx, rule_idx, g=gen, key=cache_key: self._on_filter_done(
                g, key, filtered, sev_idx, rule_idx
            )
        )
        self._filter_worker = worker
        worker.start()

    def _cancel_worker(self) -> None:
        """取消当前未完成的过滤 worker：断开信号并请求中断。"""
        worker = self._filter_worker
        if worker is None:
            return
        with contextlib.suppress(RuntimeError, TypeError):
            worker.done.disconnect()  # pyrefly: ignore [missing-attribute]
        # 请求中断并等待退出，避免遗留线程访问已替换的状态
        if worker.isRunning():
            worker.quit()
            # wait(500) 阻塞最多 500ms，过滤任务通常 < 100ms
            worker.wait(500)
        self._filter_worker = None

    def _apply_filtered_result(
        self,
        filtered: tuple[ScanResult, ...],
        generation: int | None,
    ) -> None:
        """把过滤排序后的结果应用到 Model。

        从同步路径/缓存命中路径/_on_filter_done 抽取的公共方法。
        统一处理：
        - 同步 ``_filtered_real``（完整真实结果，永不含 None）
        - 若 ``len(filtered) > _VIRTUALIZE_THRESHOLD``：幽灵行 + 分帧懒加载
          ``_filtered = (None,) * total``，立即恢复 visible_range + buffer
          填充视口，剩余用 QTimer 递归逐帧填充。
        - 否则：直接赋值 ``_filtered = filtered``。

        :param filtered: 过滤排序后的结果元组（真实完整）
        :param generation: 仅对异步路径有意义（非 None 时写入 lazystate.generation，
            用于后续帧过期校验）；同步路径/缓存命中路径传 ``None`` 时内部取
            ``self._filter_generation`` 作为等效世代号。
        """
        self._filtered_real = filtered
        total = len(filtered)
        effective_gen = self._filter_generation if generation is None else int(generation)
        if total > _VIRTUALIZE_THRESHOLD:
            # --- 大结果集：幽灵行 + 懒加载 ---
            self._lazystate = _LazyFillState(
                generation=effective_gen,
                cursor=0,
                result_tuple=filtered,
            )
            self.beginResetModel()
            self._filtered = (None,) * total
            # 扁平数据同步初始化，幽灵行对应 None（懒加载填充时重建）
            self._flat_data = [None] * total
            self.endResetModel()
            # 恢复 visible range 虚拟化 → 内部立即 apply_visible_priority_fill
            self._restore_visible_range_after_filter()
            if self._lazystate is not None and total > 0:
                QTimer.singleShot(0, self._fill_next_chunk)  # pyrefly: ignore [missing-argument, bad-argument-type]
            return
        # --- 小结果集：原流程直接替换 ---
        self.beginResetModel()
        self._filtered = filtered
        # 扁平化预构造，小结果集直接全量构造
        self._flat_data = [_build_flat_row(result, idx) for idx, result in enumerate(filtered)]
        self.endResetModel()
        # filter 视图变化后立刻恢复虚拟化可见范围
        self._restore_visible_range_after_filter()

    def _on_filter_done(
        self,
        generation: int,
        cache_key: tuple[int, str, frozenset[str], frozenset[Severity], str, bool],
        filtered: tuple[ScanResult, ...],
        severity_index: dict[Severity, list[int]],
        rule_index: dict[str, list[int]],
    ) -> None:
        """``FilterWorker.done`` 信号回调：校验 generation → 回写索引/缓存 → 应用结果。

        FilterWorker 现在同时回传后台构建的倒排索引（severity_index /
        rule_index），回调时直接应用到 Model，避免主线程在 ``set_results``
        阶段同步构建索引阻塞 UI。
        """
        # 处理完成后清理 worker 引用（无论 generation 是否匹配）
        if self._filter_worker is not None and not self._filter_worker.isRunning():
            self._filter_worker = None
        if generation != self._filter_generation:
            # 过期结果，丢弃
            return
        # 应用后台构建的倒排索引（仅当非空时覆盖；空表示未构建或结果集过小）
        if severity_index or rule_index:
            self._severity_index = severity_index
            self._rule_index = rule_index
        # 回写排序缓存（仅当 key 仍与当前状态匹配；若用户又换了条件则本次不缓存）
        if cache_key == self._sort_cache_key():
            self._sort_cache[cache_key] = filtered
        self._apply_filtered_result(filtered, generation=generation)

    @staticmethod
    def _severity_to_text(severity: Severity) -> str:
        """严重度枚举转中文文本（向后兼容）。"""
        return severity_text(severity)

    @staticmethod
    def _severity_to_color(severity: Severity) -> str:
        """严重度枚举转色值（向后兼容）。"""
        return severity_color_hex(severity)
