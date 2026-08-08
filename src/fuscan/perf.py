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
- :class:`PerfStats` **始终启用**：仅做聚合统计（无日志输出），
  开销约 1-2μs/次，对扫描性能影响 < 0.3%。扫描结果通过 :meth:`PerfStats.to_dict`
  导出，填入 :attr:`ScanStats.perf_summary` 供 GUI/CLI 展示与持久化。
- :class:`PerfTimer` / :class:`timed` / :func:`record_event` /
  :func:`render_startup_summary` 需 ``FUSCAN_PERF=1`` 或 CLI ``--perf`` 启用
  （发布版默认关闭，零开销），适合定向卡滞定位与启动耗时分析，不适合日常使用。

设计要点：
- :class:`PerfStats` 始终记录：``measure`` 仅 ``perf_counter`` + 线程本地字典写入，
  无锁（仅首次访问时一次性登记），无 enabled 检查
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
from pathlib import Path
from types import TracebackType

from fuscan.utils.time import now_iso_local

__all__ = [
    "PERF_ENABLED",
    "FilePerfDiff",
    "FilePerfRecord",
    "FilePerfRecorder",
    "FilePerfSummary",
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

    注意：``enabled`` 仅控制 :class:`PerfTimer` / :func:`record_event`
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

    线程安全策略（优化自原全锁版本，消除热路径锁竞争）：

    - **写路径**（:meth:`measure` / :meth:`record`，worker 线程高频调用）：
      无锁。每个线程持有独立的 ``stages`` 字典（``threading.local``），
      写入只更新本线程字典，无跨线程互斥。
      线程首次访问时把自己的 stages 字典登记到 ``_all_stages`` 列表
      （仅这一次 append 加锁），后续写入直接操作字典。
    - **读路径**（:meth:`to_dict` / :meth:`report` / :meth:`summary_text`，
      主线程扫描结束后调用）：合并所有线程的 stages 字典到一张汇总表
      后输出。合并为只读快照，无并发写入冲突。
    - **重置**（:meth:`reset`）：清空所有线程的 stages 字典内容，
      保留字典对象引用以供线程复用，避免重复创建与登记。

    该策略在 4 worker 线程 + 1000 文件热缓存场景下，相比原全锁版本
    消除了 ``_thread.lock.acquire`` 的 43% 累计耗时（cProfile 实测）。
    """

    def __init__(self) -> None:
        # 线程本地 stages 字典：worker 线程写路径无锁操作
        self._local = threading.local()
        # 所有已登记的线程 stages 字典列表（汇总时遍历）
        # 仅在 _get_local_stages 首次访问与 reset 时加锁
        self._stages_lock = threading.Lock()
        self._all_stages: list[dict[str, _StageStats]] = []

    def _get_local_stages(self) -> dict[str, _StageStats]:
        """获取当前线程的 stages 字典，首次访问时登记到全局列表。"""
        stages: dict[str, _StageStats] | None = getattr(self._local, "stages", None)
        if stages is None:
            stages = {}
            self._local.stages = stages
            with self._stages_lock:
                self._all_stages.append(stages)
        return stages

    def _accumulate(self, stages: dict[str, _StageStats], name: str, elapsed: float) -> None:
        """向指定 stages 字典累计一次耗时（无锁，调用方保证线程隔离）。"""
        stage = stages.get(name)
        if stage is None:
            stage = _StageStats()
            stages[name] = stage
        stage.total += elapsed
        stage.count += 1
        if elapsed > stage.max_val:  # noqa: PLR1730
            stage.max_val = elapsed

    @contextmanager
    def measure(self, name: str) -> Generator[None, None, None]:
        """计时上下文：累计阶段耗时。始终记录，无锁。

        :param name: 阶段名称（如 ``read_bytes`` / ``hash`` / ``match``）
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self._accumulate(self._get_local_stages(), name, elapsed)

    def record(self, name: str, elapsed: float) -> None:
        """直接记录一段耗时（非上下文模式）。始终记录，无锁。

        适用于无法用 ``with`` 包裹的阶段（如回调内手动计时）。

        :param name: 阶段名称
        :param elapsed: 已测得的耗时（秒）
        """
        self._accumulate(self._get_local_stages(), name, elapsed)

    def _merge_all_stages(self) -> dict[str, _StageStats]:
        """合并所有线程的 stages 字典到一张汇总表（只读快照）。

        主线程在 :meth:`to_dict` / :meth:`report` / :meth:`summary_text`
        中调用。snapshot 期间 worker 仍可能继续写入各自的 stages 字典，
        但 Python 字典遍历是 GIL 保护的，且本方法仅做拷贝，不影响 worker 写入。
        """
        merged: dict[str, _StageStats] = {}
        with self._stages_lock:
            all_stages = list(self._all_stages)
        for stages in all_stages:
            for name, stage in stages.items():
                merged_stage = merged.get(name)
                if merged_stage is None:
                    merged_stage = _StageStats()
                    merged[name] = merged_stage
                merged_stage.total += stage.total
                merged_stage.count += stage.count
                if stage.max_val > merged_stage.max_val:  # noqa: PLR1730
                    merged_stage.max_val = stage.max_val
        return merged

    def report(self, log: logging.Logger) -> None:
        """输出汇总日志到 DEBUG 级别。无数据时不输出。

        按总耗时降序排列，便于一眼定位热点阶段。

        :param log: 接收汇总日志的 logger（通常为 ``logging.getLogger(__name__)``）
        """
        merged = self._merge_all_stages()
        if not merged:
            return
        items = sorted(merged.items(), key=lambda x: -x[1].total)
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
        """清空所有线程的阶段统计（用于 Scanner 复用时重置上下文）。

        清空每个线程 stages 字典的内容但保留字典对象引用，避免下次访问时
        重新创建与登记；同时保留 ``_all_stages`` 列表以供线程复用。
        """
        with self._stages_lock:
            for stages in self._all_stages:
                stages.clear()

    def to_dict(self) -> dict[str, dict[str, float]]:
        """导出各阶段统计为可序列化字典。

        格式：``{stage_name: {"total_ms": float, "count": int, "max_ms": float}}``

        :return: 各阶段统计字典（总耗时降序），可直接 json.dumps
        """
        merged = self._merge_all_stages()
        items = sorted(merged.items(), key=lambda x: -x[1].total)
        return {
            name: {
                "total_ms": round(stage.total * 1000.0, 3),
                "count": stage.count,
                "max_ms": round(stage.max_val * 1000.0, 3),
            }
            for name, stage in items
        }

    def merge_dict(self, data: dict[str, dict[str, float]]) -> None:
        """合并外部字典数据到当前线程的 stages（用于多根路径扫描累计）。

        接受 :meth:`to_dict` 输出格式的字典，累加 total/count，取 max。
        在调用线程的本地 stages 字典中累加，:meth:`to_dict` 汇总时合并。

        :param data: :meth:`to_dict` 输出格式的字典
        """
        stages = self._get_local_stages()
        for name, info in data.items():
            stage = stages.get(name)
            if stage is None:
                stage = _StageStats()
                stages[name] = stage
            stage.total += info.get("total_ms", 0.0) / 1000.0
            stage.count += int(info.get("count", 0))
            info_max = info.get("max_ms", 0.0) / 1000.0
            if info_max > stage.max_val:  # noqa: PLR1730
                stage.max_val = info_max

    def summary_text(self, top: int = 3) -> str:
        """返回简要文本摘要（供 GUI 状态栏展示）。

        格式：``read 69% | extract 43% | match 18%``（按总耗时占比降序，取前 N 个）

        :param top: 返回前 N 个热点阶段，默认 3
        :return: 简要文本；无数据时返回空字符串
        """
        merged = self._merge_all_stages()
        if not merged:
            return ""
        items = sorted(merged.items(), key=lambda x: -x[1].total)
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
            "timestamp": now_iso_local(),
            "stages": self.to_dict(),
            "meta": meta or {},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 单文件性能基线记录
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FilePerfRecord:
    """单文件性能记录。

    :ivar path: 文件路径（扫描根的相对路径或绝对路径）
    :ivar extension: 文件扩展名（小写、去点）
    :ivar size: 文件大小（字节）
    :ivar total_ms: 该文件扫描总耗时（毫秒），含提取 + 匹配 + 缓存查找
    :ivar hit_count: 命中规则数
    """

    path: str
    extension: str
    size: int
    total_ms: float
    hit_count: int


@dataclass(slots=True)
class FilePerfSummary:
    """单文件性能汇总。

    :ivar total_files: 记录的文件总数
    :ivar total_ms: 所有文件累计耗时（毫秒）
    :ivar avg_ms: 平均每文件耗时（毫秒）
    :ivar max_ms: 最慢单文件耗时（毫秒）
    :ivar max_path: 最慢文件路径
    :ivar by_extension: 按扩展名分组的统计 ``{ext: {"count", "total_ms", "avg_ms"}}``
    :ivar slowest: 最慢的 N 个文件记录（按 total_ms 降序）
    """

    total_files: int
    total_ms: float
    avg_ms: float
    max_ms: float
    max_path: str
    by_extension: dict[str, dict[str, float]]
    slowest: list[FilePerfRecord]


@dataclass(slots=True)
class FilePerfDiff:
    """单文件性能对比结果。

    :ivar path: 文件路径
    :ivar baseline_ms: 基线耗时（毫秒）
    :ivar current_ms: 当前耗时（毫秒）
    :ivar delta_ms: 变化量（current - baseline，毫秒）
    :ivar delta_pct: 变化百分比（正=变慢/回归，负=变快/改善）
    """

    path: str
    baseline_ms: float
    current_ms: float
    delta_ms: float
    delta_pct: float


class FilePerfRecorder:
    """单文件性能基线记录器。

    记录每个文件的总扫描耗时，用于：

    - **调试**：识别异常慢的文件（如大 PDF 提取、正则回溯）
    - **优化**：对比优化前后的基线，量化改善幅度
    - **回归检测**：对比历史基线，发现性能回归

    线程安全：所有写入操作经 ``threading.Lock`` 保护，可在多 worker
    线程下并发调用 :meth:`record`。

    用法::

        recorder = FilePerfRecorder()
        scanner = Scanner(ruleset, file_perf=recorder)
        scanner.scan(root)
        recorder.save_to_json(Path("baseline.json"))

        # 对比基线
        baseline = FilePerfRecorder.load_from_json(Path("baseline.json"))
        diffs = recorder.compare(baseline, threshold_pct=20.0)
        for d in diffs:
            print(f"{d.path}: {d.baseline_ms:.1f}ms -> {d.current_ms:.1f}ms ({d.delta_pct:+.1f}%)")
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[FilePerfRecord] = []

    def record(
        self,
        path: str,
        extension: str,
        size: int,
        total_ms: float,
        hit_count: int,
    ) -> None:
        """记录单文件扫描性能。

        :param path: 文件路径
        :param extension: 扩展名（小写、去点）
        :param size: 文件大小（字节）
        :param total_ms: 总耗时（毫秒）
        :param hit_count: 命中规则数
        """
        with self._lock:
            self._records.append(
                FilePerfRecord(
                    path=path,
                    extension=extension,
                    size=size,
                    total_ms=total_ms,
                    hit_count=hit_count,
                )
            )

    @property
    def records(self) -> list[FilePerfRecord]:
        """已记录的全部文件性能记录（只读视图）。"""
        with self._lock:
            return list(self._records)

    @property
    def count(self) -> int:
        """已记录的文件数。"""
        with self._lock:
            return len(self._records)

    def summary(self, top: int = 10) -> FilePerfSummary:
        """生成性能汇总。

        :param top: 返回最慢的 N 个文件，默认 10
        :return: :class:`FilePerfSummary` 汇总对象
        """
        with self._lock:
            records = list(self._records)

        if not records:
            return FilePerfSummary(
                total_files=0,
                total_ms=0.0,
                avg_ms=0.0,
                max_ms=0.0,
                max_path="",
                by_extension={},
                slowest=[],
            )

        total_ms = sum(r.total_ms for r in records)
        max_record = max(records, key=lambda r: r.total_ms)

        # 按扩展名分组统计
        ext_groups: dict[str, list[float]] = {}
        for r in records:
            ext_groups.setdefault(r.extension, []).append(r.total_ms)

        by_extension: dict[str, dict[str, float]] = {}
        for ext, times in ext_groups.items():
            ext_total = sum(times)
            by_extension[ext] = {
                "count": len(times),
                "total_ms": round(ext_total, 3),
                "avg_ms": round(ext_total / len(times), 3),
            }

        slowest = sorted(records, key=lambda r: -r.total_ms)[:top]

        return FilePerfSummary(
            total_files=len(records),
            total_ms=round(total_ms, 3),
            avg_ms=round(total_ms / len(records), 3),
            max_ms=round(max_record.total_ms, 3),
            max_path=max_record.path,
            by_extension=by_extension,
            slowest=slowest,
        )

    def compare(
        self,
        baseline: FilePerfRecorder,
        threshold_pct: float = 20.0,
    ) -> list[FilePerfDiff]:
        """对比基线，返回超过阈值的性能差异。

        仅对比两次运行中都出现的文件（按路径匹配），按变化百分比降序排列。

        :param baseline: 基线记录器
        :param threshold_pct: 变化百分比阈值（正=变慢，仅返回超过此阈值的项）
        :return: :class:`FilePerfDiff` 列表（按 delta_pct 降序）
        """
        with self._lock:
            current_map = {r.path: r.total_ms for r in self._records}
        baseline_map = {r.path: r.total_ms for r in baseline.records}

        diffs: list[FilePerfDiff] = []
        for path, current_ms in current_map.items():
            baseline_ms = baseline_map.get(path)
            if baseline_ms is None or baseline_ms <= 0:
                continue
            delta_ms = current_ms - baseline_ms
            delta_pct = delta_ms / baseline_ms * 100.0
            if abs(delta_pct) >= threshold_pct:
                diffs.append(
                    FilePerfDiff(
                        path=path,
                        baseline_ms=round(baseline_ms, 3),
                        current_ms=round(current_ms, 3),
                        delta_ms=round(delta_ms, 3),
                        delta_pct=round(delta_pct, 1),
                    )
                )

        diffs.sort(key=lambda d: -d.delta_pct)
        return diffs

    def save_to_json(self, path: Path, *, meta: dict[str, object] | None = None) -> None:
        """持久化记录到 JSON 文件。

        :param path: 目标 JSON 文件路径（父目录自动创建）
        :param meta: 附加元信息（如扫描根路径、规则文件），写入 ``meta`` 字段
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            records = [
                {
                    "path": r.path,
                    "extension": r.extension,
                    "size": r.size,
                    "total_ms": round(r.total_ms, 3),
                    "hit_count": r.hit_count,
                }
                for r in self._records
            ]
        payload = {
            "timestamp": now_iso_local(),
            "records": records,
            "meta": meta or {},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load_from_json(cls, path: Path) -> FilePerfRecorder:
        """从 JSON 文件加载基线记录。

        :param path: JSON 文件路径
        :return: 加载后的 :class:`FilePerfRecorder` 实例
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        recorder = cls()
        for item in data.get("records", []):
            recorder._records.append(
                FilePerfRecord(
                    path=item["path"],
                    extension=item["extension"],
                    size=item["size"],
                    total_ms=item["total_ms"],
                    hit_count=item["hit_count"],
                )
            )
        return recorder

    def print_summary(self, *, top: int = 10, log: logging.Logger | None = None) -> None:
        """打印性能汇总到日志（INFO 级）。

        :param top: 展示最慢的 N 个文件，默认 10
        :param log: 目标 logger，默认模块 logger
        """
        log = log or logger
        s = self.summary(top=top)
        if s.total_files == 0:
            log.info("[file-perf] 无记录")
            return

        log.info("[file-perf] === 单文件性能汇总 ===")
        log.info(
            "[file-perf] 文件数: %d | 总耗时: %.1fms | 平均: %.2fms | 最慢: %.1fms (%s)",
            s.total_files,
            s.total_ms,
            s.avg_ms,
            s.max_ms,
            s.max_path,
        )

        log.info("[file-perf] --- 按扩展名 ---")
        for ext, info in sorted(s.by_extension.items(), key=lambda x: -x[1]["total_ms"]):
            log.info(
                "[file-perf]   .%-8s  %4d 文件  总计 %8.1fms  平均 %7.2fms",
                ext,
                int(info["count"]),
                info["total_ms"],
                info["avg_ms"],
            )

        log.info("[file-perf] --- 最慢 %d 个文件 ---", len(s.slowest))
        for r in s.slowest:
            log.info(
                "[file-perf]   %8.2fms  %6d B  %2d hits  %s",
                r.total_ms,
                r.size,
                r.hit_count,
                r.path,
            )
