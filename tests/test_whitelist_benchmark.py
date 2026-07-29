"""误报白名单扫描吞吐量基准测试（iter-133）。

验证白名单过滤对扫描吞吐量的影响 < 5%，满足 iter-133 验收要求。

运行方式::

    uv run pytest -m slow tests/test_whitelist_benchmark.py -q
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

import pytest

from benchmarks.sample_files import generate_files
from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity
from fuscan.rules.whitelist import Whitelist, WhitelistEntry
from fuscan.scanner import Scanner

# 测试文件数（保守，兼顾 CI 速度与统计意义）
_FILE_COUNT = 500

# 每个配置重复测量次数（取中位数，消除顺序效应与缓存预热偏差）
_REPEAT = 5


def _content_ruleset() -> RuleSet:
    """内容规则集。"""
    return RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="明文密码",
                severity=Severity.WARNING,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            ),
        ),
    )


def _measure_fps(scanner: Scanner, root: Path) -> float:
    """单次测量吞吐量（files/s）。"""
    start = time.perf_counter()
    report = scanner.scan(root)
    duration = time.perf_counter() - start
    return report.stats.scanned_files / duration


@pytest.fixture()
def bench_dir(tmp_path: Path) -> Path:
    """生成 500 个混合格式测试文件。"""
    root = tmp_path / "bench_wl"
    generate_files(root, _FILE_COUNT)
    return root


@pytest.mark.slow
class TestWhitelistThroughputImpact:
    """白名单过滤对扫描吞吐量影响 < 5% 基准测试。"""

    def test_whitelist_overhead_under_5_percent(self, bench_dir: Path) -> None:
        """白名单过滤对扫描吞吐量影响应 < 5%。

        对比无白名单与带白名单（过滤全部命中）两种配置的吞吐量中位数，
        验证白名单过滤在命中聚合阶段的额外开销 < 5%。

        测量方法：预热 1 次 + 每配置重复 5 次取中位数，消除顺序效应与
        文件系统缓存预热偏差。
        """
        rs = _content_ruleset()

        # 预热：首次扫描触发文件系统缓存与字节码缓存，结果丢弃
        Scanner(rs, max_workers=4).scan(bench_dir)

        # 构造覆盖全部命中的白名单（全部命中被过滤）
        wl = Whitelist(entries=(WhitelistEntry(path_glob=str(bench_dir / "*"), rule_name="*"),))

        # 交替测量，进一步消除顺序效应
        baseline_samples: list[float] = []
        wl_samples: list[float] = []
        for _ in range(_REPEAT):
            baseline_samples.append(_measure_fps(Scanner(rs, max_workers=4), bench_dir))
            wl_samples.append(_measure_fps(Scanner(rs, max_workers=4, whitelist=wl), bench_dir))

        baseline_fps = statistics.median(baseline_samples)
        wl_fps = statistics.median(wl_samples)

        # 白名单后所有命中被过滤，但 scanned_files 不变（仍是 500）
        report_wl = Scanner(rs, max_workers=4, whitelist=wl).scan(bench_dir)
        report_baseline = Scanner(rs, max_workers=4).scan(bench_dir)
        assert report_wl.stats.scanned_files == report_baseline.stats.scanned_files
        assert report_wl.stats.matched_files == 0
        assert report_baseline.stats.matched_files > 0

        # 吞吐量下降 < 5%
        overhead_ratio = (baseline_fps - wl_fps) / baseline_fps
        assert overhead_ratio < 0.05, (
            f"白名单过滤导致吞吐量下降 {overhead_ratio * 100:.2f}%，超过 5% 阈值"
            f"（baseline={baseline_fps:.1f} fps, with_wl={wl_fps:.1f} fps）"
        )

    def test_whitelist_no_match_no_overhead(self, bench_dir: Path) -> None:
        """白名单不匹配任何文件时，与基线吞吐量接近（< 10% 偏差）。

        白名单条目存在但不命中任何文件，``fnmatch`` 调用本身的开销可忽略。

        测量方法：预热 1 次 + 每配置重复 5 次取中位数，消除顺序效应。
        阈值 10% 容忍 CI 性能波动（fnmatch 调用本身开销 << 10%，但
        线程调度与文件系统缓存的随机波动可能达数个百分点）。
        """
        rs = _content_ruleset()

        # 预热
        Scanner(rs, max_workers=4).scan(bench_dir)

        # 不匹配的白名单
        wl = Whitelist(entries=(WhitelistEntry(path_glob="/nonexistent/*", rule_name="*"),))

        # 交替测量
        baseline_samples: list[float] = []
        wl_samples: list[float] = []
        for _ in range(_REPEAT):
            baseline_samples.append(_measure_fps(Scanner(rs, max_workers=4), bench_dir))
            wl_samples.append(_measure_fps(Scanner(rs, max_workers=4, whitelist=wl), bench_dir))

        baseline_fps = statistics.median(baseline_samples)
        wl_fps = statistics.median(wl_samples)

        # 命中数相同（白名单未过滤任何结果）
        report_wl = Scanner(rs, max_workers=4, whitelist=wl).scan(bench_dir)
        report_baseline = Scanner(rs, max_workers=4).scan(bench_dir)
        assert report_wl.stats.matched_files == report_baseline.stats.matched_files

        # 偏差 < 10%
        diff_ratio = abs(baseline_fps - wl_fps) / baseline_fps
        assert diff_ratio < 0.10, (
            f"白名单无匹配时吞吐量偏差 {diff_ratio * 100:.2f}% 超过 10%"
            f"（baseline={baseline_fps:.1f} fps, with_wl={wl_fps:.1f} fps）"
        )
