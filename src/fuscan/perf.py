"""性能测量基础设施（GUI 与扫描器共用）。

提供四类工具：

- :class:`PerfTimer`：单阶段上下文计时器，用于 GUI 卡滞定位
- :class:`PerfStats`：线程安全的聚合统计，用于扫描器分阶段瓶颈分析
- :class:`timed`：装饰器 + 上下文两用计时器，用于入口流程（如 GUI 启动）
  分阶段展示各部分用时（INFO 级）
- :class:`PerfReport` + :func:`render_startup_summary`：启动流程分阶段计时收集器
  与 rich 汇总表渲染器，把各环节耗时与占比汇总为**单张表格**直观展示，
  便于一眼识别瓶颈（rich 惰性导入，缺失时回退纯文本 INFO 汇总）

启用方式：
- :class:`PerfStats` **始终启用**（iter-66 起）：仅做聚合统计（无日志输出），
  开销约 1-2μs/次，对扫描性能影响 < 0.3%。扫描结果通过 :meth:`PerfStats.to_dict`
  导出，填入 :attr:`ScanStats.perf_summary` 供 GUI/CLI 展示与持久化。
- :class:`PerfTimer` / :class:`timed` / :func:`record_event` /
  :func:`render_startup_summary` 需 ``FUSCAN_PERF=1`` 或 CLI ``--perf`` 启用
  （发布版默认关闭，零开销），适合定向卡滞定位与启动耗时分析，不适合日常使用。

设计要点：
- :class:`PerfStats` 始终记录：``measure`` 仅 ``perf_counter`` + Lock，无 enabled 检查
- :class:`PerfTimer` 默认零开销：未启用时仅一次 bool 检查 + yield
- 上下文管理器：``with PerfTimer("stage"): ...`` 自动记录进入/退出时间
- 嵌套支持：``PerfTimer`` 通过 ``logger.debug`` 输出层级缩进，便于阅读
- 聚合统计：``PerfStats`` 累计各阶段总耗时/调用次数/最大值，扫描结束时
  :meth:`PerfStats.report` 输出汇总，便于一眼定位瓶颈
- 持久化：:meth:`PerfStats.save_to_json` 将统计写入 JSON 文件供后续分析
- 启动汇总：``timed`` 传入 :class:`PerfReport` 时额外登记各阶段耗时，外层块退出后
  :func:`render_startup_summary` 渲染为单张 rich 表格（占比以最外层为 100% 基准）

公共 API：
- :data:`PERF_ENABLED`：PerfTimer 详细日志开关（模块加载时快照，运行时切换用 :func:`set_perf_enabled`）
- :class:`PerfTimer`：上下文管理器计时器（单阶段，需启用）
- :class:`PerfStats`：聚合统计计时器（多阶段累计，始终启用）
- :class:`timed`：装饰器 + 上下文两用计时器（入口流程分阶段用时，需启用）
- :class:`StageTiming`：单个启动阶段计时记录（供 :class:`PerfReport` 收集）
- :class:`PerfReport`：启动流程分阶段计时收集器
- :func:`render_startup_summary`：将 :class:`PerfReport` 渲染为 rich 汇总表（需启用）
- :func:`record_event`：记录离散事件（需启用）
- :func:`set_perf_enabled`：运行时切换 PerfTimer/timed/record_event 开关
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Generator
from contextlib import ContextDecorator, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import TracebackType

__all__ = [
    "PERF_ENABLED",
    "PerfReport",
    "PerfStats",
    "PerfTimer",
    "StageTiming",
    "record_event",
    "render_startup_summary",
    "set_perf_enabled",
    "timed",
]

logger = logging.getLogger(__name__)


class _PerfState:
    """PerfTimer 详细日志运行时可变状态。

    用类属性封装可变状态，避免 ``global`` 声明（PLW0603）。
    仅供模块内部使用，外部通过 :data:`PERF_ENABLED` 与 :func:`set_perf_enabled` 间接访问。

    注意：iter-66 起 ``enabled`` 仅控制 :class:`PerfTimer` / :func:`record_event`
    的详细日志输出。:class:`PerfStats` 始终启用，不受此开关影响。
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
def PerfTimer(name: str, *, threshold_ms: float = 0.0) -> Generator[None, None, None]:
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


class timed(ContextDecorator):
    """两用计时工具：既是装饰器又是上下文管理器，测量并记录代码段耗时。

    继承 :class:`contextlib.ContextDecorator`，因此同一实例可两种方式使用：

    装饰器（自动用函数名，也可显式命名）::

        @timed("加载配置")
        def load() -> Config: ...

        @timed()  # 未命名时用被装饰函数的限定名
        def build() -> None: ...

    上下文管理器::

        with timed("构造主控制器"):
            controller = AppController()

    **发布后零开销**：受 :data:`_PerfState.enabled` 控制（由 ``FUSCAN_PERF=1``
    环境变量、CLI ``--perf`` 或 :func:`set_perf_enabled` 开启）。未启用时
    ``__enter__`` 仅一次 bool 检查即返回，``__exit__`` 直接返回，不调用
    ``perf_counter``、不记日志，装饰函数调用开销可忽略。因此发布版默认关闭，
    无需担心性能损失。

    与 :class:`PerfTimer` 的区别：``PerfTimer`` 仅上下文管理器且固定 DEBUG 级、
    带嵌套缩进；``timed`` 额外支持装饰器语法与自定义日志级别，适合入口流程
    （如 GUI 启动各阶段）用 INFO 级直接展示各部分用时。

    :param name: 阶段名称；``None`` 时装饰器模式自动取函数限定名，
        上下文模式取 ``"<anonymous>"``
    :param level: 日志级别（如 :data:`logging.INFO`），默认 :data:`logging.INFO`
    :param threshold_ms: 仅当耗时超过该阈值（毫秒）才记录耗时行，默认 0 总是记录
    :param report: 传入 :class:`PerfReport` 时，在退出后额外登记本阶段耗时与嵌套层级，
        供外层块结束时 :func:`render_startup_summary` 渲染汇总表；默认 ``None`` 保持
        纯日志行为（不影响任何既有调用点与测试）
    """

    __slots__ = ("_depth", "_level", "_name", "_report", "_start", "_threshold_ms")

    def __init__(
        self,
        name: str | None = None,
        *,
        level: int = logging.INFO,
        threshold_ms: float = 0.0,
        report: PerfReport | None = None,
    ) -> None:
        self._name = name
        self._level = level
        self._threshold_ms = threshold_ms
        self._report = report
        self._start: float = 0.0
        self._depth: int = 0

    def __call__(self, func: Callable[..., object]) -> Callable[..., object]:
        """装饰器入口：未显式命名时用被装饰函数的限定名。"""
        if self._name is None:
            self._name = getattr(func, "__qualname__", None) or getattr(func, "__name__", "func")
        return super().__call__(func)

    def __enter__(self) -> timed:
        if not _PerfState.enabled:
            return self
        self._start = time.perf_counter()
        # 仅在收集模式下维护嵌套层级（记录进入时的层级供退出登记）
        if self._report is not None:
            self._depth = _PerfState.depth
            _PerfState.depth += 1
        logger.log(self._level, "%s…", self._name or "<anonymous>")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if not _PerfState.enabled:
            return False
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        if self._report is not None:
            _PerfState.depth -= 1
            self._report.add(self._name or "<anonymous>", elapsed_ms, self._depth)
        if elapsed_ms >= self._threshold_ms:
            logger.log(self._level, "%s 完成，用时 %.1fms", self._name or "<anonymous>", elapsed_ms)
        return False


@dataclass(slots=True)
class StageTiming:
    """单个启动阶段的计时记录。

    :ivar name: 阶段名称（如 ``"构造主控制器"``）
    :ivar elapsed_ms: 该阶段耗时（毫秒）
    :ivar depth: 嵌套层级，0 为最外层（如 ``"启动流程"``），子阶段为 1
    :ivar order: 登记顺序（保持时间顺序展示，启动各阶段天然串行）
    """

    name: str
    elapsed_ms: float
    depth: int
    order: int


@dataclass(slots=True)
class PerfReport:
    """启动流程分阶段计时收集器。

    由 :class:`timed` 在 ``report`` 参数非空时登记各阶段耗时，外层块退出后交给
    :func:`render_startup_summary` 渲染为单张 rich 表格。仅在 GUI 主线程顺序调用，
    无需加锁。

    用法::

        report = PerfReport()
        with timed("启动流程", report=report):
            with timed("构造主控制器", report=report):
                ...
        render_startup_summary(report)
    """

    stages: list[StageTiming] = field(default_factory=list)

    def add(self, name: str, elapsed_ms: float, depth: int) -> None:
        """登记一个阶段的耗时，``order`` 自动按登记顺序递增。"""
        self.stages.append(StageTiming(name, elapsed_ms, depth, len(self.stages)))

    def total_ms(self) -> float:
        """返回总计耗时：优先取最外层（``depth==0``）阶段耗时，无则取全部最大值。"""
        outer = [s.elapsed_ms for s in self.stages if s.depth == 0]
        if outer:
            return max(outer)
        return max((s.elapsed_ms for s in self.stages), default=0.0)


def _render_plain(rows: list[StageTiming], total_ms: float, log: logging.Logger) -> None:
    """rich 缺失时的纯文本回退：逐行 INFO 打印阶段耗时与占比 + 总计行。"""
    log.info("=== 启动性能汇总 ===")
    for stage in rows:
        indent = "  " * (stage.depth - 1)
        pct = stage.elapsed_ms / total_ms * 100.0
        log.info("%s%-24s %8.1f ms  %5.1f%%", indent, stage.name, stage.elapsed_ms, pct)
    log.info("%-24s %8.1f ms  100.0%%", "总计", total_ms)


def render_startup_summary(report: PerfReport, *, log: logging.Logger | None = None) -> None:
    """将启动分阶段计时渲染为单张 rich 表格并打印到控制台。

    仅在性能测量启用（:data:`_PerfState.enabled`）且有数据时输出；未启用时直接返回，
    保证零开销。rich 采用惰性导入：缺失时回退 :func:`_render_plain` 纯文本 INFO 汇总，
    因此核心库无需强依赖 rich（作为 ``perf`` 可选依赖按需安装）。

    表格列：阶段（子阶段按 ``depth`` 缩进）/ 耗时 / 占比；占比 = 阶段耗时 / 总计
    （最外层 ``"启动流程"`` 耗时）× 100%。行序保持登记的时间顺序（启动阶段天然串行），
    末尾追加加粗 ``"总计"`` 行。

    :param report: 已收集完毕的启动阶段计时
    :param log: rich 缺失时的回退 logger，默认模块 logger
    """
    if not _PerfState.enabled or not report.stages:
        return

    log = log or logger
    total_ms = report.total_ms() or 1.0
    # 子阶段（排除最外层）构成表格主体，按登记顺序（时间顺序）展示
    rows = [s for s in sorted(report.stages, key=lambda s: s.order) if s.depth > 0]

    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        _render_plain(rows, total_ms, log)
        return

    table = Table(title="启动性能汇总", title_style="bold magenta")
    table.add_column("阶段", style="cyan", no_wrap=True)
    table.add_column("耗时", justify="right", style="green")
    table.add_column("占比", justify="right")
    for stage in rows:
        indent = "  " * (stage.depth - 1)
        pct = stage.elapsed_ms / total_ms * 100.0
        table.add_row(f"{indent}{stage.name}", f"{stage.elapsed_ms:.1f} ms", f"{pct:.1f}%")
    table.add_row("总计", f"{total_ms:.1f} ms", "100.0%", style="bold")
    Console().print(table)


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
    用法：

    >>> stats = PerfStats()
    >>> with stats.measure("read_bytes"):
    ...     data = path.read_bytes()
    >>> stats.report(logger)
    >>> stats.save_to_json(Path("perf.json"))

    线程安全：所有写入操作经 ``threading.Lock`` 保护，可在多 worker
    线程下并发调用 :meth:`measure` / :meth:`record`。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stages: dict[str, _StageStats] = {}

    @contextmanager
    def measure(self, name: str) -> Generator[None, None, None]:
        """计时上下文：累计阶段耗时。始终记录（iter-66 起）。

        :param name: 阶段名称（如 ``read_bytes`` / ``hash`` / ``match``）
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self._record_locked(name, elapsed)

    def record(self, name: str, elapsed: float) -> None:
        """直接记录一段耗时（非上下文模式）。始终记录（iter-66 起）。

        适用于无法用 ``with`` 包裹的阶段（如回调内手动计时）。

        :param name: 阶段名称
        :param elapsed: 已测得的耗时（秒）
        """
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
        """输出汇总日志到 DEBUG 级别。无数据时不输出。

        按总耗时降序排列，便于一眼定位热点阶段。

        :param log: 接收汇总日志的 logger（通常为 ``logging.getLogger(__name__)``）
        """
        if not self._stages:
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

    def to_dict(self) -> dict[str, dict[str, float]]:
        """导出各阶段统计为可序列化字典。

        格式：``{stage_name: {"total_ms": float, "count": int, "max_ms": float}}``

        :return: 各阶段统计字典（总耗时降序），可直接 json.dumps
        """
        with self._lock:
            items = sorted(self._stages.items(), key=lambda x: -x[1].total)
        return {
            name: {
                "total_ms": round(stage.total * 1000.0, 3),
                "count": stage.count,
                "max_ms": round(stage.max_val * 1000.0, 3),
            }
            for name, stage in items
        }

    def merge_dict(self, data: dict[str, dict[str, float]]) -> None:
        """合并外部字典数据到当前实例（用于多根路径扫描累计）。

        接受 :meth:`to_dict` 输出格式的字典，累加 total/count，取 max。
        线程安全。

        :param data: :meth:`to_dict` 输出格式的字典
        """
        with self._lock:
            for name, info in data.items():
                stage = self._stages.get(name)
                if stage is None:
                    stage = _StageStats()
                    self._stages[name] = stage
                stage.total += info.get("total_ms", 0.0) / 1000.0
                stage.count += int(info.get("count", 0))
                stage.max_val = max(stage.max_val, info.get("max_ms", 0.0) / 1000.0)

    def summary_text(self, top: int = 3) -> str:
        """返回简要文本摘要（供 GUI 状态栏展示）。

        格式：``read 69% | extract 43% | match 18%``（按总耗时占比降序，取前 N 个）

        :param top: 返回前 N 个热点阶段，默认 3
        :return: 简要文本；无数据时返回空字符串
        """
        with self._lock:
            if not self._stages:
                return ""
            items = sorted(self._stages.items(), key=lambda x: -x[1].total)
        grand_total = sum(s.total for _, s in items) or 1.0
        parts = [f"{name} {s.total / grand_total * 100:.0f}%" for name, s in items[:top]]
        return " | ".join(parts)

    def save_to_json(self, path: Path, *, meta: dict[str, object] | None = None) -> None:
        """持久化统计到 JSON 文件。

        写入格式包含时间戳、可选元信息与各阶段统计，供后续分析对比。

        :param path: 目标 JSON 文件路径（父目录自动创建）
        :param meta: 附加元信息（如文件数、耗时），写入 ``meta`` 字段
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "stages": self.to_dict(),
            "meta": meta or {},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
