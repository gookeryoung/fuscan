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

import contextlib
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt  # pyrefly: ignore [missing-import]

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

# iter-129：结果数超过此阈值时过滤+排序移至后台线程，避免主线程阻塞
_ASYNC_THRESHOLD = 10000


def filter_and_sort(
    results: tuple[ScanResult, ...],
    filter_text: str,
    filter_rules: frozenset[str],
    filter_severities: frozenset[Severity],
    sort_field: str,
    sort_ascending: bool,
) -> tuple[ScanResult, ...]:
    """纯函数：过滤+排序扫描结果（无副作用，可独立测试）。

    iter-129 从 ``ResultListModel`` 内联实现中提取为独立纯函数，供
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

        # 排序条件：iter-137 默认按严重度降序（严重 → 轻微）
        self._sort_field: str = SORT_SEVERITY
        self._sort_ascending: bool = False

        # iter-129：后台过滤+排序（大结果集时启用）
        # generation 每次提交过滤任务时 +1，worker 回调时校验，丢弃过期结果
        self._filter_generation: int = 0
        self._filter_worker: FilterWorker | None = None

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
        """批量替换结果。

        替换后自动重新应用当前过滤+排序条件，视图同步刷新。
        iter-129：``beginResetModel``/``endResetModel`` 由 ``_schedule_filter_refresh``
        或 ``_on_filter_done`` 统一管理，避免双重 reset。
        """
        self._results = results
        self._schedule_filter_refresh()

    def clear(self) -> None:
        """清空结果。"""
        self.set_results(())

    def cleanup(self) -> None:
        """退出时取消未完成的 FilterWorker，避免进程退出后后台残留。

        iter-132：显式取消 worker，不依赖 ``__del__``（解释器关闭时不保证调用）。
        """
        self._cancel_worker()

    def get_result(self, row: int) -> ScanResult | None:
        """按视图行号返回过滤后的 :class:`ScanResult`，越界返回 None。"""
        if 0 <= row < len(self._filtered):
            return self._filtered[row]
        return None

    def remove_result_by_path(self, path: Path) -> bool:
        """按文件路径移除一条结果（iter-139）。

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
        self._schedule_filter_refresh()
        return True

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

    def _schedule_filter_refresh(self) -> None:
        """根据结果量选择同步或异步路径刷新 ``_filtered`` 视图。

        - 结果数 < ``_ASYNC_THRESHOLD``：主线程同步执行，立即 reset model
        - 结果数 >= ``_ASYNC_THRESHOLD``：取消旧 worker，启动新 ``FilterWorker``
          后台执行，完成后通过 :meth:`_on_filter_done` 回调到主线程 reset

        ``beginResetModel`` / ``endResetModel`` 仅在此处与 ``_on_filter_done`` 中调用，
        setters 不再手动管理，避免双重 reset。
        """
        # 取消上一个未完成的 worker：disconnect 信号后 wait 短暂等待退出
        self._cancel_worker()

        if len(self._results) < _ASYNC_THRESHOLD:
            # 同步路径：小结果集直接计算，立即刷新
            new_filtered = filter_and_sort(
                self._results,
                self._filter_text,
                self._filter_rules,
                self._filter_severities,
                self._sort_field,
                self._sort_ascending,
            )
            self.beginResetModel()
            self._filtered = new_filtered
            self.endResetModel()
            return

        # 异步路径：大结果集移至后台线程
        # generation 自增，回调时校验，丢弃过期结果（用户可能已修改过滤条件）
        self._filter_generation += 1
        gen = self._filter_generation
        # 延迟导入避免循环依赖（FilterWorker 依赖本模块的 filter_and_sort）
        from fuscan.gui.workers.filter_worker import FilterWorker

        worker = FilterWorker(
            results=self._results,
            filter_text=self._filter_text,
            filter_rules=self._filter_rules,
            filter_severities=self._filter_severities,
            sort_field=self._sort_field,
            sort_ascending=self._sort_ascending,
        )
        worker.done.connect(  # pyrefly: ignore [missing-attribute]
            lambda filtered, g=gen: self._on_filter_done(g, filtered)
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

    def _on_filter_done(self, generation: int, filtered: tuple[ScanResult, ...]) -> None:
        """``FilterWorker.done`` 信号回调：替换视图并 reset model。

        :param generation: 提交 worker 时的 generation 编号
        :param filtered: 后台过滤+排序后的结果元组

        若 generation 不匹配当前 ``_filter_generation``，说明用户在 worker 运行期间
        又修改了过滤条件并启动了新 worker，本次结果作废，避免覆盖最新视图。
        """
        # 处理完成后清理 worker 引用（无论 generation 是否匹配）
        if self._filter_worker is not None and not self._filter_worker.isRunning():
            self._filter_worker = None
        if generation != self._filter_generation:
            # 过期结果，丢弃
            return
        self.beginResetModel()
        self._filtered = filtered
        self.endResetModel()

    @staticmethod
    def _severity_to_text(severity: Severity) -> str:
        """严重度枚举转中文文本（向后兼容）。"""
        return severity_text(severity)

    @staticmethod
    def _severity_to_color(severity: Severity) -> str:
        """严重度枚举转色值（向后兼容）。"""
        return severity_color_hex(severity)
