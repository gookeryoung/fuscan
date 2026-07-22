"""性能测量基础设施（GUI 与扫描器共用）。

提供两类工具：

- :class:`PerfTimer`：单阶段上下文计时器，用于 GUI 卡滞定位
- :class:`PerfStats`：线程安全的聚合统计，用于扫描器分阶段瓶颈分析

启用方式：
- 环境变量 ``FUSCAN_PERF=1`` 开启计时记录（默认关闭，零开销）
- 通过 :data:`PERF_ENABLED` 全局开关，所有计时器均检查此开关
- 输出到 ``fuscan.perf`` logger，级别 DEBUG，可被统一日志配置捕获

设计要点：
- 默认零开销：未启用时 ``PerfTimer`` / ``PerfStats.measure`` 仅做一次全局开关检查
- 上下文管理器：``with PerfTimer("stage"): ...`` 自动记录进入/退出时间
- 嵌套支持：``PerfTimer`` 通过 ``logger.debug`` 输出层级缩进，便于阅读
- 聚合统计：``PerfStats`` 累计各阶段总耗时/调用次数/最大值，扫描结束时
  :meth:`PerfStats.report` 输出汇总，便于一眼定位瓶颈
- 进度回调计数：:func:`record_event` 记录关键事件触发次数

公共 API：
- :data:`PERF_ENABLED`：性能测量总开关（模块加载时快照，运行时切换用 :func:`set_perf_enabled`）
- :class:`PerfTimer`：上下文管理器计时器（单阶段）
- :class:`PerfStats`：聚合统计计时器（多阶段累计）
- :func:`record_event`：记录离散事件
- :func:`set_perf_enabled`：运行时切换开关（测试用）
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator

__all__ = ["PERF_ENABLED", "PerfStats", "PerfTimer", "record_event", "set_perf_enabled"]

logger = logging.getLogger(__name__)


class _PerfState:
    """性能测量运行时可变状态。

    用类属性封装可变状态，避免 ``global`` 声明（PLW0603）。
    仅供模块内部使用，外部通过 :data:`PERF_ENABLED` 与 :func:`set_perf_enabled` 间接访问。
    """

    enabled: bool = os.environ.get("FUSCAN_PERF", "") == "1"
    # 嵌套层级跟踪（线程局部可避免并发干扰，但 GUI 主线程单线程足够）
    depth: int = 0


# 性能测量总开关：模块加载时快照（只读视图），运行时切换请用 set_perf_enabled
PERF_ENABLED: bool = _PerfState.enabled


def set_perf_enabled(enabled: bool) -> None:
    """运行时切换性能测量开关（测试用）。

    :param enabled: True 开启计时记录，False 关闭
    """
    _PerfState.enabled = enabled


@contextmanager
def PerfTimer(name: str, *, threshold_ms: float = 0.0) -> Iterator[None]:
    """计时上下文管理器：记录代码块耗时。

    未启用时（``_PerfState.enabled=False``）直接 yield 不做任何记录，保证零开销。
    启用后通过 ``logger.debug`` 输出形如 ``[perf] > stage_name 12.3ms`` 的日志，
    嵌套层级通过缩进前缀表达。

    :param name: 代码块名称（如 ``MainWindow.__init__``）
    :param threshold_ms: 仅当耗时超过该阈值（毫秒）时记录，默认 0 总是记录
    """
    if not _PerfState.enabled:
        yield
        return
    start = time.perf_counter()
    _PerfState.depth += 1
    indent = "  " * (_PerfState.depth - 1)
    logger.debug("[perf] %s> %s begin", indent, name)
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - start) * 1000.0
        _PerfState.depth -= 1
        if elapsed >= threshold_ms:
            logger.debug("[perf] %s< %s %.1fms", indent, name, elapsed)


def record_event(name: str, **fields: object) -> None:
    """记录离散事件及其关联字段（如计数、状态）。

    与 :class:`PerfTimer` 不同，本函数记录瞬时事件而非代码块耗时，
    适用于"扫描进度回调触发 N 次"等计数场景。

    :param name: 事件名称
    :param fields: 附加字段，以 ``key=value`` 形式记录到日志
    """
    if not _PerfState.enabled:
        return
    pairs = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.debug("[perf] event %s %s", name, pairs)


class _StageStats:
    """单阶段聚合统计（内部使用，``__slots__`` 降低内存开销）。"""

    __slots__ = ("count", "max_val", "total")

    def __init__(self) -> None:
        self.total: float = 0.0
        self.count: int = 0
        self.max_val: float = 0.0


class PerfStats:
    """线程安全的性能聚合统计。

    累计各阶段总耗时、调用次数与最大单次耗时，扫描结束时通过
    :meth:`report` 输出汇总，便于一眼定位瓶颈阶段。

    启用方式同 :class:`PerfTimer`（``FUSCAN_PERF=1``）。未启用时
    :meth:`measure` 与 :meth:`record` 直接 return 不做任何记录，保证零开销。

    用法：

    >>> stats = PerfStats()
    >>> with stats.measure("read_bytes"):
    ...     data = path.read_bytes()
    >>> stats.report(logger)

    线程安全：所有写入操作经 ``threading.Lock`` 保护，可在多 worker
    线程下并发调用 :meth:`measure` / :meth:`record`。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stages: dict[str, _StageStats] = {}

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        """计时上下文：累计阶段耗时。未启用时零开销。

        :param name: 阶段名称（如 ``read_bytes`` / ``hash`` / ``match``）
        """
        if not _PerfState.enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self._record_locked(name, elapsed)

    def record(self, name: str, elapsed: float) -> None:
        """直接记录一段耗时（非上下文模式）。未启用时零开销。

        适用于无法用 ``with`` 包裹的阶段（如回调内手动计时）。

        :param name: 阶段名称
        :param elapsed: 已测得的耗时（秒）
        """
        if not _PerfState.enabled:
            return
        self._record_locked(name, elapsed)

    def _record_locked(self, name: str, elapsed: float) -> None:
        """在锁保护下累计阶段统计。"""
        with self._lock:
            stage = self._stages.get(name)
            if stage is None:
                stage = _StageStats()
                self._stages[name] = stage
            stage.total += elapsed
            stage.count += 1
            stage.max_val = max(stage.max_val, elapsed)

    def report(self, log: logging.Logger) -> None:
        """输出汇总日志。未启用或无数据时不输出。

        按总耗时降序排列，便于一眼定位热点阶段。

        :param log: 接收汇总日志的 logger（通常为 ``logging.getLogger(__name__)``）
        """
        if not _PerfState.enabled or not self._stages:
            return
        with self._lock:
            items = sorted(self._stages.items(), key=lambda x: -x[1].total)
        log.debug("[perf] === 性能汇总 ===")
        for name, stage in items:
            avg_ms = (stage.total / stage.count * 1000.0) if stage.count else 0.0
            log.debug(
                "[perf] %-24s 总计 %8.1fms  调用 %6d 次  平均 %7.2fms  最大 %8.1fms",
                name,
                stage.total * 1000.0,
                stage.count,
                avg_ms,
                stage.max_val * 1000.0,
            )

    def reset(self) -> None:
        """清空所有阶段统计（用于 Scanner 复用时重置上下文）。"""
        with self._lock:
            self._stages.clear()
