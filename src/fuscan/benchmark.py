"""扫描性能基准测量（CLI ``benchmark`` 子命令后端）。

对指定路径执行多轮扫描，复用 :class:`~fuscan.scanner.Scanner` 全链路埋点的
:class:`~fuscan.perf.PerfStats` 各阶段计时（walk/read_bytes/hash/extract/match/cache_* 等），聚合出每阶段的均值/最小/最大/标准差，便于一眼识别瓶颈。

支持将结果导出为**基准线**（JSON），并在后续运行时加载历史基准线与本次结果
逐阶段对比，输出变化百分比与回归提示。

设计要点：
- 复用 :attr:`ScanStats.perf_summary`：每次 :meth:`Scanner.scan` 会 ``reset``
  性能统计，故复用同一 Scanner 多轮扫描时每轮统计独立，无需重建 Scanner。
- 多轮取统计而非单轮：单轮扫描受磁盘缓存/系统调度扰动大，多轮聚合更稳健，
  预热轮（warmup）不计入统计以剔除冷启动偏差。
- 基准线格式基于 :meth:`PerfStats.save_to_json` 的 ``{timestamp, stages, meta}``
  约定扩展，``stages`` 存各阶段 ``mean_ms``，便于跨版本对比。

公共 API：
- :class:`StageAggregate`：单阶段多轮聚合统计
- :class:`BenchmarkResult`：一次基准测量的完整结果（各阶段聚合 + 元信息）
- :class:`StageDelta`：基准线对比中单阶段的变化
- :class:`BaselineComparison`：本次结果与基准线的逐阶段对比
- :func:`run_benchmark`：执行多轮扫描并聚合
- :func:`save_baseline` / :func:`load_baseline`：基准线导出与加载
- :func:`compare_to_baseline`：与基准线对比
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fuscan.scanner import Scanner
from fuscan.utils.time import now_iso_local

__all__ = [
    "BaselineComparison",
    "BenchmarkResult",
    "StageAggregate",
    "StageDelta",
    "compare_to_baseline",
    "load_baseline",
    "run_benchmark",
    "save_baseline",
]

# 回归判定阈值：阶段耗时相对基准线增长超过此比例（10%）视为回归
DEFAULT_REGRESSION_THRESHOLD: float = 0.10


@dataclass(frozen=True, slots=True)
class StageAggregate:
    """单个扫描阶段在多轮测量下的聚合统计（单位：毫秒）。

    :ivar name: 阶段名称（如 ``read_bytes`` / ``extract`` / ``match``）
    :ivar mean_ms: 各轮该阶段总耗时的均值
    :ivar min_ms: 各轮最小值
    :ivar max_ms: 各轮最大值
    :ivar stddev_ms: 各轮总耗时的样本标准差（轮数<2 时为 0）
    :ivar samples: 参与聚合的轮数（即每轮该阶段的总耗时样本数）
    """

    name: str
    mean_ms: float
    min_ms: float
    max_ms: float
    stddev_ms: float
    samples: int


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """一次基准测量的完整结果。

    :ivar stages: 各阶段聚合统计（按均值降序，热点在前）
    :ivar rounds: 计入统计的正式轮数
    :ivar warmup: 预热轮数（不计入统计）
    :ivar scanned_files: 单轮扫描的文件数（末轮为准，各轮一致）
    :ivar mean_duration_ms: 单轮端到端耗时均值（毫秒）
    :ivar root: 扫描根路径
    :ivar timestamp: 测量完成时间（ISO 格式，秒精度）
    """

    stages: tuple[StageAggregate, ...]
    rounds: int
    warmup: int
    scanned_files: int
    mean_duration_ms: float
    root: str
    timestamp: str = field(default_factory=now_iso_local)

    def to_baseline_dict(self) -> dict[str, object]:
        """导出为基准线 JSON 结构（``{timestamp, stages, meta}``）。

        ``stages`` 仅保留 ``mean_ms``（对比核心指标），``meta`` 记录测量上下文。
        """
        return {
            "timestamp": self.timestamp,
            "stages": {s.name: {"mean_ms": s.mean_ms} for s in self.stages},
            "meta": {
                "rounds": self.rounds,
                "warmup": self.warmup,
                "scanned_files": self.scanned_files,
                "mean_duration_ms": round(self.mean_duration_ms, 3),
                "root": self.root,
            },
        }


@dataclass(frozen=True, slots=True)
class StageDelta:
    """基准线对比中单个阶段的变化。

    :ivar name: 阶段名称
    :ivar current_ms: 本次均值耗时（缺失该阶段时为 None）
    :ivar baseline_ms: 基准线均值耗时（基准线无该阶段时为 None）
    :ivar change_ratio: 相对变化比例 ``(current - baseline) / baseline``；
        任一侧缺失时为 None
    :ivar regressed: 是否判定为回归（增长超过阈值）
    """

    name: str
    current_ms: float | None
    baseline_ms: float | None
    change_ratio: float | None
    regressed: bool


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    """本次基准结果与历史基准线的逐阶段对比。

    :ivar deltas: 各阶段变化（并集，按本次均值降序、缺失项排后）
    :ivar baseline_timestamp: 基准线记录的测量时间
    :ivar threshold: 判定回归的增长比例阈值
    """

    deltas: tuple[StageDelta, ...]
    baseline_timestamp: str
    threshold: float

    @property
    def has_regression(self) -> bool:
        """是否存在任一阶段回归。"""
        return any(d.regressed for d in self.deltas)


def _aggregate_stage(name: str, totals_ms: list[float]) -> StageAggregate:
    """将某阶段各轮的总耗时列表聚合为统计量。"""
    samples = len(totals_ms)
    mean = sum(totals_ms) / samples if samples else 0.0
    if samples >= 2:
        variance = sum((v - mean) ** 2 for v in totals_ms) / (samples - 1)
        stddev = math.sqrt(variance)
    else:
        stddev = 0.0
    return StageAggregate(
        name=name,
        mean_ms=round(mean, 3),
        min_ms=round(min(totals_ms), 3) if totals_ms else 0.0,
        max_ms=round(max(totals_ms), 3) if totals_ms else 0.0,
        stddev_ms=round(stddev, 3),
        samples=samples,
    )


def run_benchmark(
    scanner: Scanner,
    root: Path,
    *,
    rounds: int = 5,
    warmup: int = 1,
    on_round: Callable[[int, int, str], None] | None = None,
) -> BenchmarkResult:
    """对 ``root`` 执行多轮扫描并聚合各阶段性能。

    先执行 ``warmup`` 轮预热（结果丢弃，剔除冷启动/磁盘缓存偏差），再执行
    ``rounds`` 轮正式测量。每轮复用同一 ``scanner``（其内部每次扫描会重置
    :class:`PerfStats`，故各轮统计独立）。

    :param scanner: 已配置好的扫描器（规则/缓存/阈值等）
    :param root: 扫描根路径
    :param rounds: 正式测量轮数（至少 1）
    :param warmup: 预热轮数（不计入统计，至少 0）
    :param on_round: 每轮开始前的进度回调 ``(阶段序号从1起, 总轮数, 阶段标签)``，
        阶段标签为 ``"预热"`` 或 ``"测量"``；默认 None 不回调
    :return: 聚合后的基准测量结果
    :raises ValueError: ``rounds < 1`` 或 ``warmup < 0``
    """
    if rounds < 1:
        raise ValueError(f"rounds 必须 >= 1，收到 {rounds}")
    if warmup < 0:
        raise ValueError(f"warmup 必须 >= 0，收到 {warmup}")

    total = warmup + rounds
    # 每阶段收集各轮的总耗时（毫秒）
    per_stage: dict[str, list[float]] = {}
    durations_ms: list[float] = []
    scanned_files = 0

    for i in range(total):
        is_warmup = i < warmup
        if on_round is not None:
            on_round(i + 1, total, "预热" if is_warmup else "测量")
        start = time.perf_counter()
        report = scanner.scan(root)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if is_warmup:
            continue
        durations_ms.append(elapsed_ms)
        scanned_files = report.stats.scanned_files
        perf = report.stats.perf_summary or {}
        for name, info in perf.items():
            per_stage.setdefault(name, []).append(info.get("total_ms", 0.0))

    stages = tuple(
        sorted(
            (_aggregate_stage(name, totals) for name, totals in per_stage.items()),
            key=lambda s: -s.mean_ms,
        )
    )
    mean_duration = sum(durations_ms) / len(durations_ms) if durations_ms else 0.0
    return BenchmarkResult(
        stages=stages,
        rounds=rounds,
        warmup=warmup,
        scanned_files=scanned_files,
        mean_duration_ms=mean_duration,
        root=str(root),
    )


def save_baseline(result: BenchmarkResult, path: Path) -> None:
    """将基准测量结果导出为基准线 JSON 文件。

    :param result: 基准测量结果
    :param path: 目标 JSON 路径（父目录自动创建）
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_baseline_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_baseline(path: Path) -> dict[str, object]:
    """加载基准线 JSON 文件。

    :param path: 基准线文件路径
    :return: 解析后的字典（``{timestamp, stages, meta}``）
    :raises FileNotFoundError: 文件不存在
    :raises ValueError: 内容非合法 JSON 或结构不符（缺少 ``stages``）
    """
    if not path.exists():
        raise FileNotFoundError(f"基准线文件不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"基准线文件不是合法 JSON: {path}（{exc}）") from exc
    if not isinstance(data, dict) or "stages" not in data:
        raise ValueError(f"基准线文件结构不符（缺少 stages 字段）: {path}")
    return data


def _baseline_stage_means(baseline: dict[str, object]) -> dict[str, float]:
    """从基准线字典提取 ``{阶段名: mean_ms}`` 映射（容错缺失/异常类型）。"""
    stages = baseline.get("stages")
    if not isinstance(stages, dict):
        return {}
    means: dict[str, float] = {}
    for name, info in stages.items():
        if isinstance(info, dict):
            value = info.get("mean_ms")
            if isinstance(value, (int, float)):
                means[str(name)] = float(value)
    return means


def compare_to_baseline(
    result: BenchmarkResult,
    baseline: dict[str, object],
    *,
    threshold: float = DEFAULT_REGRESSION_THRESHOLD,
) -> BaselineComparison:
    """将本次基准结果与历史基准线逐阶段对比。

    取本次与基准线阶段名的并集：两侧都有则计算变化比例并按阈值判定回归；
    仅本次有（新增阶段）或仅基准线有（消失阶段）时 ``change_ratio`` 为 None，
    不判回归。排序：先按本次均值降序，本次缺失的阶段排在末尾。

    :param result: 本次基准测量结果
    :param baseline: :func:`load_baseline` 返回的基准线字典
    :param threshold: 回归判定阈值（相对增长比例），默认 10%
    :return: 逐阶段对比结果
    """
    current: dict[str, float] = {s.name: s.mean_ms for s in result.stages}
    base_means = _baseline_stage_means(baseline)

    deltas: list[StageDelta] = []
    for name in current.keys() | base_means.keys():
        cur = current.get(name)
        base = base_means.get(name)
        if cur is not None and base is not None and base > 0:
            ratio = (cur - base) / base
            regressed = ratio > threshold
        else:
            ratio = None
            regressed = False
        deltas.append(
            StageDelta(
                name=name,
                current_ms=cur,
                baseline_ms=base,
                change_ratio=ratio,
                regressed=regressed,
            )
        )

    # 排序：本次有耗时的阶段按均值降序在前，本次缺失（current_ms=None）的排末尾
    deltas.sort(key=lambda d: (d.current_ms is None, -(d.current_ms or 0.0)))

    ts = baseline.get("timestamp")
    baseline_ts = ts if isinstance(ts, str) else "(未知)"
    return BaselineComparison(deltas=tuple(deltas), baseline_timestamp=baseline_ts, threshold=threshold)
