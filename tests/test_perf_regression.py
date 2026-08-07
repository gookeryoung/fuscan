"""多规则扫描性能回归门禁（iter-1 ContentRegexPool 优化成果保护）。

本文件为 ``benchmarks/multi_rule_profile.py`` 三场景（S1/S2/S3）的 pytest-benchmark
回归门禁镜像，保护 ``ContentRegexPool``（matchers.py）与 ``_content_buckets`` 优化
成果不被回退。所有测试标记 ``@pytest.mark.slow``，CI 默认跳过（``-m "not slow"``），
通过 ``make perf`` 手动触发并保存基线、``make perf-compare`` 与基线对比。

场景设计（与 multi_rule_profile.py 对齐，规模缩小至 30 文件以适配 CI 时长）：

- **S1 内置规则**：``builtin.yaml``（14 条规则），验证端到端扫描无显著回退
- **S2 纯 CONTENT REGEX**：50 条顶层 LeafMatch(CONTENT, REGEX)（全部进 CONTENT 桶，
  验证 ``match_content_via_buckets`` 桶合并 + 逐规则预筛 + 活跃子集动态编译）
- **S3 AND 组合**：50 条 AND 组合规则 × 2~3 个 CONTENT REGEX 子项（验证
  ``ContentRegexPool`` 跨规则共享池对 AndMatcher/OrMatcher 的加速）
- **S2 微基准**：直接调 ``match_content_via_buckets`` 绕过 I/O，纯桶匹配吞吐

回归门禁工作流::

    # 1. 首次保存基线（在优化前的 commit 上执行）
    make perf

    # 2. 优化后对比（mean 退化 > 10% 失败）
    make perf-compare

    # 3. 查看基线
    uv run pytest --benchmark-list

阈值策略：不在测试内硬编码绝对耗时断言（CI 硬件差异大），仅功能正确性断言 +
``benchmark`` fixture 自动统计；回归门禁由 ``--benchmark-compare-fail=mean:10%``
在 ``make perf-compare`` 时执行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from benchmarks.multi_rule_profile import (
    build_and_combo_ruleset,
    build_content_regex_ruleset,
)
from fuscan.rules.builtin import load_builtin_ruleset
from fuscan.scanner import Scanner
from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
from fuscan.scanner.matchers import build_matcher

__all__ = [
    "TestContentBucketMicroBenchmark",
    "TestScannerMultiRuleRegression",
]


# 测试规模：30 文件 × 4KB，单线程。pytest-benchmark min-rounds 默认 5，
# 单场景 5-10s，三场景合计 < 30s，CI 可接受
_TEST_FILE_COUNT = 30
_TEST_FILE_SIZE = 4096
# S2/S3 规则数：与 multi_rule_profile.py 默认一致，验证 50 条规则下的优化路径
_TEST_RULE_COUNT = 50


# ---------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_files(tmp_path: Path) -> Path:
    """生成 30 个混合格式测试文件（约 30% 注入敏感命中样本）。

    复用 ``benchmarks.multi_rule_profile._generate_test_files`` 的样本池，
    保证命中场景与生产基准一致。
    """
    from benchmarks.multi_rule_profile import _generate_test_files

    files_dir = tmp_path / "files"
    _generate_test_files(files_dir, _TEST_FILE_COUNT, _TEST_FILE_SIZE, seed=42)
    return files_dir


# ---------------------------------------------------------------------------
# 端到端扫描回归（S1 / S2 / S3）
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestScannerMultiRuleRegression:
    """端到端扫描性能回归基线（iter-1 优化成果保护）。

    三场景对齐 ``benchmarks/multi_rule_profile.py``，规模缩小至 30 文件 / 4KB，
    单线程（``max_workers=1``）便于 cProfile 与 benchmark 复现。
    """

    def test_s1_builtin_ruleset(self, benchmark: Any, sample_files: Path) -> None:
        """S1 内置规则端到端扫描基线（14 条 builtin.yaml 规则）。

        覆盖路径：builtin 规则混合 FILENAME/CONTENT/AND 组合，验证既有路径
        无显著回退（``match_content_via_buckets`` + ``_remaining_rules`` 双路径）。
        """
        rs = load_builtin_ruleset()
        scanner = Scanner(rs, max_workers=1)

        def run() -> int:
            report = scanner.scan(sample_files)
            return report.stats.total_matches

        result = run()
        # 功能正确性：sample_files 约 30% 文件注入敏感样本，至少应有命中
        assert result > 0, "内置规则扫描应至少有 1 条命中"
        benchmark(run)

    def test_s2_50_content_regex_rules(self, benchmark: Any, sample_files: Path) -> None:
        """S2 纯 CONTENT REGEX 端到端扫描基线（50 条顶层 LeafMatch）。

        覆盖路径：50 条规则全部进 CONTENT 桶，验证 ``match_content_via_buckets``
        桶合并 + 逐规则预筛 + 活跃子集动态编译（``_compute_active_indices`` +
        ``_get_active_compiled``）无回退。
        """
        rs = build_content_regex_ruleset(_TEST_RULE_COUNT)
        scanner = Scanner(rs, max_workers=1)

        def run() -> int:
            report = scanner.scan(sample_files)
            return report.stats.total_matches

        result = run()
        assert result > 0, "S2 纯 CONTENT REGEX 扫描应至少有 1 条命中"
        benchmark(run)

    def test_s3_50_and_combo_rules(self, benchmark: Any, sample_files: Path) -> None:
        """S3 AND 组合规则端到端扫描基线（50 条 × 2~3 CONTENT 子项）。

        覆盖路径：组合规则无法进桶，全部走 ``_remaining_rules`` 逐条求值，
        AndMatcher.matches 经 ``ContentRegexPool`` 共享池加速子项匹配，
        验证 iter-1 ContentRegexPool 优化成果。
        """
        rs = build_and_combo_ruleset(_TEST_RULE_COUNT)
        scanner = Scanner(rs, max_workers=1)

        def run() -> int:
            report = scanner.scan(sample_files)
            return report.stats.total_matches

        result = run()
        # S3 命中较少（AND 组合条件严格），功能正确性仅断言扫描完成
        assert result >= 0, "S3 AND 组合扫描应正常完成"
        benchmark(run)


# ---------------------------------------------------------------------------
# 微基准：纯桶匹配吞吐（绕过 I/O）
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestContentBucketMicroBenchmark:
    """``match_content_via_buckets`` 微基准（绕过 I/O，纯桶匹配吞吐）。

    直接调用桶匹配函数，避免文件 I/O 噪声，专注测量 iter-1 桶合并 + 逐规则
    预筛 + 活跃子集动态编译路径的纯计算开销。用于捕获桶路径本身的回退
    （端到端测试因 I/O 占比高，桶路径小幅回退可能被淹没）。
    """

    def test_match_50_rules_throughput(self, benchmark: Any) -> None:
        """50 条 CONTENT REGEX 桶匹配吞吐基线（无 I/O）。

        构造 50 条 CONTENT REGEX 规则桶，对固定文本（含少量命中样本 + 大量噪声）
        重复执行 ``match_content_via_buckets``，测量纯桶匹配延迟。
        """
        rs = build_content_regex_ruleset(_TEST_RULE_COUNT)
        # 编译为 (Rule, Matcher) 对并构建桶
        pairs = [(rule, build_matcher(rule.match)) for rule in rs.rules]
        buckets, remaining = build_content_buckets(pairs)
        assert len(buckets) >= 1, "50 条 CONTENT REGEX 应至少构建 1 个桶"
        assert not remaining, "纯 CONTENT REGEX 规则不应有 remaining"

        # 固定文本：少量命中样本 + 大量噪声（约 4KB），模拟真实文件内容
        hit_samples = (
            "password=admin123456\n"
            "api_key=AKIAIOSFODNN7EXAMPLE\n"
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB\n"
            "mysql://root:password@localhost:3306/db\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
        )
        noise = "the quick brown fox jumps over the lazy dog\n" * 80
        content = hit_samples + noise

        def run() -> int:
            hits = match_content_via_buckets(content, buckets)
            return len(hits)

        result = run()
        assert result > 0, "桶匹配应至少命中 1 条规则"
        benchmark(run)
