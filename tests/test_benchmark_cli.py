"""benchmark 模块单元测试：多轮聚合、基准线导出/加载、对比回归判定。

区别于 ``tests/test_benchmark.py`` 的 slow 吞吐量回归测试，本文件针对
:mod:`fuscan.benchmark` 的纯逻辑（聚合/序列化/对比），用桩扫描器避免真实扫描，
运行快、可纳入常规覆盖率门禁。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fuscan.benchmark import (
    BenchmarkResult,
    StageAggregate,
    compare_to_baseline,
    load_baseline,
    run_benchmark,
    save_baseline,
)
from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity
from fuscan.scanner import Scanner
from fuscan.scanner.result import ScanReport, ScanStats


def _make_ruleset() -> RuleSet:
    """构造最小规则集。"""
    rule = Rule(
        name="含密码",
        severity=Severity.WARNING,
        match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
    )
    return RuleSet(version="1.0", rules=(rule,))


def _fake_report(perf: dict[str, dict[str, float]], scanned: int = 10) -> ScanReport:
    """构造带指定 perf_summary 的假 ScanReport。"""
    stats = ScanStats(total_files=scanned, scanned_files=scanned, duration_seconds=0.1, perf_summary=perf)
    return ScanReport(root=Path(), stats=stats)


class _StubScanner(Scanner):
    """按预设 perf 序列逐轮返回假 report 的扫描器（不做真实扫描）。"""

    def __init__(self, perf_rounds: list[dict[str, dict[str, float]]], scanned: int = 10) -> None:
        super().__init__(_make_ruleset())
        self._perf_rounds = perf_rounds
        self._scanned = scanned
        self._call = 0

    def scan(self, root: Path) -> ScanReport:  # type: ignore[override]
        """返回预设序列中的下一个 report（超出则复用末尾）。"""
        idx = min(self._call, len(self._perf_rounds) - 1)
        self._call += 1
        return _fake_report(self._perf_rounds[idx], self._scanned)


class TestRunBenchmark:
    def test_aggregates_multiple_rounds(self) -> None:
        """多轮各阶段耗时正确聚合为均值/最小/最大/标准差。"""
        rounds = [
            {"match": {"total_ms": 10.0, "count": 5, "max_ms": 3.0}},
            {"match": {"total_ms": 20.0, "count": 5, "max_ms": 6.0}},
            {"match": {"total_ms": 30.0, "count": 5, "max_ms": 9.0}},
        ]
        scanner = _StubScanner(rounds)
        result = run_benchmark(scanner, Path(), rounds=3, warmup=0)
        assert result.rounds == 3
        assert result.warmup == 0
        assert len(result.stages) == 1
        stage = result.stages[0]
        assert stage.name == "match"
        assert stage.mean_ms == pytest.approx(20.0)
        assert stage.min_ms == pytest.approx(10.0)
        assert stage.max_ms == pytest.approx(30.0)
        assert stage.samples == 3
        # 样本标准差 sqrt(((10-20)^2+(20-20)^2+(30-20)^2)/2)=sqrt(100)=10
        assert stage.stddev_ms == pytest.approx(10.0)

    def test_warmup_rounds_excluded(self) -> None:
        """预热轮不计入统计（仅正式轮参与聚合）。"""
        rounds = [
            {"match": {"total_ms": 999.0, "count": 1, "max_ms": 999.0}},  # 预热，应丢弃
            {"match": {"total_ms": 10.0, "count": 1, "max_ms": 10.0}},
            {"match": {"total_ms": 10.0, "count": 1, "max_ms": 10.0}},
        ]
        scanner = _StubScanner(rounds)
        result = run_benchmark(scanner, Path(), rounds=2, warmup=1)
        assert result.stages[0].mean_ms == pytest.approx(10.0)
        assert result.stages[0].samples == 2

    def test_stages_sorted_by_mean_desc(self) -> None:
        """阶段按均值降序排列（热点在前）。"""
        rounds = [
            {
                "walk": {"total_ms": 1.0, "count": 1, "max_ms": 1.0},
                "match": {"total_ms": 50.0, "count": 1, "max_ms": 50.0},
                "read_bytes": {"total_ms": 10.0, "count": 1, "max_ms": 10.0},
            }
        ]
        scanner = _StubScanner(rounds)
        result = run_benchmark(scanner, Path(), rounds=1, warmup=0)
        names = [s.name for s in result.stages]
        assert names == ["match", "read_bytes", "walk"]

    def test_single_round_stddev_zero(self) -> None:
        """单轮测量标准差为 0（无法估计离散度）。"""
        scanner = _StubScanner([{"match": {"total_ms": 5.0, "count": 1, "max_ms": 5.0}}])
        result = run_benchmark(scanner, Path(), rounds=1, warmup=0)
        assert result.stages[0].stddev_ms == 0.0

    def test_empty_perf_summary(self) -> None:
        """perf_summary 为空时无阶段，但仍记录轮数与文件数。"""
        scanner = _StubScanner([{}], scanned=7)
        result = run_benchmark(scanner, Path(), rounds=1, warmup=0)
        assert result.stages == ()
        assert result.scanned_files == 7

    def test_on_round_callback_invoked(self) -> None:
        """on_round 回调每轮触发一次，含预热轮。"""
        calls: list[tuple[int, int, str]] = []
        scanner = _StubScanner([{"match": {"total_ms": 1.0, "count": 1, "max_ms": 1.0}}])
        run_benchmark(
            scanner,
            Path(),
            rounds=2,
            warmup=1,
            on_round=lambda i, t, label: calls.append((i, t, label)),
        )
        assert calls == [(1, 3, "预热"), (2, 3, "测量"), (3, 3, "测量")]

    def test_invalid_rounds_raises(self) -> None:
        """rounds < 1 抛 ValueError。"""
        scanner = _StubScanner([{}])
        with pytest.raises(ValueError, match="rounds 必须"):
            run_benchmark(scanner, Path(), rounds=0)

    def test_invalid_warmup_raises(self) -> None:
        """warmup < 0 抛 ValueError。"""
        scanner = _StubScanner([{}])
        with pytest.raises(ValueError, match="warmup 必须"):
            run_benchmark(scanner, Path(), rounds=1, warmup=-1)


class TestBaselineRoundTrip:
    def test_save_and_load(self, tmp_path: Path) -> None:
        """导出的基准线可原样加载，结构含 timestamp/stages/meta。"""
        result = BenchmarkResult(
            stages=(StageAggregate("match", 20.0, 10.0, 30.0, 8.0, 3),),
            rounds=3,
            warmup=1,
            scanned_files=10,
            mean_duration_ms=25.0,
            root="/tmp/x",
        )
        path = tmp_path / "baseline.json"
        save_baseline(result, path)
        assert path.exists()
        loaded = load_baseline(path)
        assert loaded["stages"] == {"match": {"mean_ms": 20.0}}
        assert loaded["meta"]["rounds"] == 3  # type: ignore[index]
        assert loaded["meta"]["scanned_files"] == 10  # type: ignore[index]

    def test_save_creates_parent_dir(self, tmp_path: Path) -> None:
        """导出时自动创建父目录。"""
        result = BenchmarkResult(stages=(), rounds=1, warmup=0, scanned_files=0, mean_duration_ms=0.0, root=".")
        path = tmp_path / "sub" / "dir" / "baseline.json"
        save_baseline(result, path)
        assert path.exists()

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        """加载不存在的文件抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="不存在"):
            load_baseline(tmp_path / "nope.json")

    def test_load_invalid_json_raises(self, tmp_path: Path) -> None:
        """加载非法 JSON 抛 ValueError。"""
        path = tmp_path / "bad.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(ValueError, match="合法 JSON"):
            load_baseline(path)

    def test_load_wrong_structure_raises(self, tmp_path: Path) -> None:
        """缺少 stages 字段抛 ValueError。"""
        path = tmp_path / "wrong.json"
        path.write_text(json.dumps({"foo": 1}), encoding="utf-8")
        with pytest.raises(ValueError, match="缺少 stages"):
            load_baseline(path)


def _result_with(stages: dict[str, float]) -> BenchmarkResult:
    """按 {阶段: 均值} 构造基准结果。"""
    aggs = tuple(StageAggregate(n, v, v, v, 0.0, 1) for n, v in stages.items())
    return BenchmarkResult(stages=aggs, rounds=1, warmup=0, scanned_files=1, mean_duration_ms=0.0, root=".")


def _baseline_with(stages: dict[str, float], timestamp: str = "2026-01-01T00:00:00") -> dict[str, object]:
    """按 {阶段: 均值} 构造基准线字典。"""
    return {
        "timestamp": timestamp,
        "stages": {n: {"mean_ms": v} for n, v in stages.items()},
        "meta": {},
    }


class TestCompareToBaseline:
    def test_regression_detected(self) -> None:
        """本次耗时超基准线阈值判定回归。"""
        result = _result_with({"match": 120.0})
        baseline = _baseline_with({"match": 100.0})
        cmp = compare_to_baseline(result, baseline, threshold=0.10)
        delta = cmp.deltas[0]
        assert delta.name == "match"
        assert delta.change_ratio == pytest.approx(0.20)
        assert delta.regressed is True
        assert cmp.has_regression is True

    def test_improvement_not_regression(self) -> None:
        """本次耗时低于基准线不判回归。"""
        result = _result_with({"match": 80.0})
        baseline = _baseline_with({"match": 100.0})
        cmp = compare_to_baseline(result, baseline, threshold=0.10)
        assert cmp.deltas[0].change_ratio == pytest.approx(-0.20)
        assert cmp.deltas[0].regressed is False
        assert cmp.has_regression is False

    def test_within_threshold_not_regression(self) -> None:
        """增长在阈值内不判回归。"""
        result = _result_with({"match": 105.0})
        baseline = _baseline_with({"match": 100.0})
        cmp = compare_to_baseline(result, baseline, threshold=0.10)
        assert cmp.deltas[0].regressed is False

    def test_new_stage_no_ratio(self) -> None:
        """本次新增而基准线无的阶段，change_ratio 为 None 且不判回归。"""
        result = _result_with({"match": 50.0, "entropy": 30.0})
        baseline = _baseline_with({"match": 50.0})
        cmp = compare_to_baseline(result, baseline)
        entropy_delta = next(d for d in cmp.deltas if d.name == "entropy")
        assert entropy_delta.baseline_ms is None
        assert entropy_delta.change_ratio is None
        assert entropy_delta.regressed is False

    def test_missing_stage_no_ratio(self) -> None:
        """基准线有而本次无的阶段，current_ms 为 None 并排在末尾。"""
        result = _result_with({"match": 50.0})
        baseline = _baseline_with({"match": 50.0, "extract": 20.0})
        cmp = compare_to_baseline(result, baseline)
        assert cmp.deltas[-1].name == "extract"
        assert cmp.deltas[-1].current_ms is None
        assert cmp.deltas[-1].change_ratio is None

    def test_sorted_current_desc(self) -> None:
        """对比结果按本次均值降序，缺失项排末尾。"""
        result = _result_with({"match": 50.0, "walk": 10.0})
        baseline = _baseline_with({"match": 40.0, "walk": 8.0, "hash": 5.0})
        cmp = compare_to_baseline(result, baseline)
        names = [d.name for d in cmp.deltas]
        assert names[0] == "match"
        assert names[1] == "walk"
        assert names[-1] == "hash"

    def test_zero_baseline_no_ratio(self) -> None:
        """基准线耗时为 0 时避免除零，change_ratio 为 None。"""
        result = _result_with({"match": 50.0})
        baseline = _baseline_with({"match": 0.0})
        cmp = compare_to_baseline(result, baseline)
        assert cmp.deltas[0].change_ratio is None
        assert cmp.deltas[0].regressed is False

    def test_baseline_missing_timestamp(self) -> None:
        """基准线缺 timestamp 时回退占位符。"""
        result = _result_with({"match": 50.0})
        baseline: dict[str, object] = {"stages": {"match": {"mean_ms": 50.0}}}
        cmp = compare_to_baseline(result, baseline)
        assert cmp.baseline_timestamp == "(未知)"

    def test_baseline_stages_not_dict(self) -> None:
        """基准线 stages 非字典时视为空基准，本次阶段全为新增。"""
        result = _result_with({"match": 50.0})
        baseline: dict[str, object] = {"timestamp": "t", "stages": []}
        cmp = compare_to_baseline(result, baseline)
        assert cmp.deltas[0].baseline_ms is None
        assert cmp.deltas[0].change_ratio is None

    def test_baseline_stage_value_wrong_type(self) -> None:
        """基准线阶段值类型异常时被忽略（视为该阶段无基准）。"""
        result = _result_with({"match": 50.0})
        baseline: dict[str, object] = {"timestamp": "t", "stages": {"match": {"mean_ms": "bad"}}}
        cmp = compare_to_baseline(result, baseline)
        assert cmp.deltas[0].baseline_ms is None
