"""扫描性能基准回归测试（slow）。

验证扫描吞吐量与缓存收益的数量级正确性，阈值保守以适应 CI 性能波动。
测试文件使用 ``benchmarks/sample_files.py`` 动态生成，覆盖纯文本与二进制格式。

运行方式::

    uv run pytest -m slow tests/test_benchmark.py -q
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from benchmarks.sample_files import generate_files
from fuscan.cache import CacheStore
from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity
from fuscan.scanner import Scanner

# 测试文件数（保守，兼顾 CI 速度与统计意义）
_FILE_COUNT = 500


def _generate_bench_files(root: Path, count: int, seed: int = 42) -> None:
    """生成混合格式测试文件（纯文本 + 二进制），每个含 password 关键词。"""
    generate_files(root, count, seed)


def _content_ruleset() -> RuleSet:
    """内容规则集（触发文件读取 + 哈希计算）。"""
    return RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="AWS密钥",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="AKIA"),
            ),
            Rule(
                name="明文密码",
                severity=Severity.WARNING,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            ),
        ),
    )


def _filename_ruleset() -> RuleSet:
    """文件名规则集（不含 CONTENT 目标，缓存模式可跳过文件 I/O）。"""
    return RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="敏感文件名",
                severity=Severity.INFO,
                match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="file_"),
            ),
        ),
    )


@pytest.fixture()
def bench_dir(tmp_path: Path) -> Path:
    """生成 500 个混合格式测试文件。"""
    root = tmp_path / "bench"
    _generate_bench_files(root, _FILE_COUNT)
    return root


@pytest.mark.slow
class TestScanBenchmark:
    """扫描性能基准回归测试。"""

    def test_sequential_throughput(self, bench_dir: Path) -> None:
        """单线程扫描 500 文件应 ≥ 50 files/s。"""
        rs = _content_ruleset()
        scanner = Scanner(rs, max_workers=1)
        start = time.perf_counter()
        report = scanner.scan(bench_dir)
        duration = time.perf_counter() - start
        files_per_sec = report.stats.scanned_files / duration
        assert files_per_sec >= 50, f"单线程吞吐量 {files_per_sec:.1f} files/s 低于阈值 50"

    def test_concurrent_throughput(self, bench_dir: Path) -> None:
        """4 线程扫描 500 文件应 ≥ 50 files/s。"""
        rs = _content_ruleset()
        scanner = Scanner(rs, max_workers=4)
        start = time.perf_counter()
        report = scanner.scan(bench_dir)
        duration = time.perf_counter() - start
        files_per_sec = report.stats.scanned_files / duration
        assert files_per_sec >= 50, f"并发吞吐量 {files_per_sec:.1f} files/s 低于阈值 50"

    def test_cache_throughput(self, tmp_path: Path) -> None:
        """缓存命中后扫描 500 文件应 ≥ 200 files/s（filename 规则跳过文件 I/O）。"""
        root = tmp_path / "bench"
        _generate_bench_files(root, _FILE_COUNT)
        rs = _filename_ruleset()
        cache = CacheStore(tmp_path / "cache.db")
        try:
            # 首次扫描（填充缓存）
            Scanner(rs, max_workers=4, cache=cache).scan(root)
            # 二次扫描（热缓存，filename 规则跳过 I/O）
            scanner = Scanner(rs, max_workers=4, cache=cache)
            start = time.perf_counter()
            report = scanner.scan(root)
            duration = time.perf_counter() - start
            files_per_sec = report.stats.scanned_files / duration
            assert files_per_sec >= 200, f"缓存吞吐量 {files_per_sec:.1f} files/s 低于阈值 200"
        finally:
            cache.close()

    def test_cache_hit_ratio(self, tmp_path: Path) -> None:
        """二次扫描缓存命中率应 ≥ 95%。"""
        root = tmp_path / "bench"
        _generate_bench_files(root, _FILE_COUNT)
        rs = _content_ruleset()
        cache = CacheStore(tmp_path / "cache.db")
        try:
            # 首次扫描（填充缓存）
            Scanner(rs, max_workers=4, cache=cache).scan(root)
            entries_after_first = cache.stats().scan_results
            assert entries_after_first > 0, "首次扫描应写入缓存条目"
            # 二次扫描（热缓存）
            Scanner(rs, max_workers=4, cache=cache).scan(root)
            entries_after_second = cache.stats().scan_results
            # 命中率 = 1 - 新增条数 / 首次条数（新增条数 = 未命中数）
            misses = entries_after_second - entries_after_first
            hit_ratio = 1.0 - misses / entries_after_first
            assert hit_ratio >= 0.95, f"缓存命中率 {hit_ratio:.1%} 低于阈值 95%"
        finally:
            cache.close()


# ---------------------------------------------------------------------------
# 提取器单格式速度基准
# ---------------------------------------------------------------------------

# 各格式单次提取耗时上限（毫秒），保守阈值以适应 CI 波动
# 基于 4KB 内容在开发机上的实测均值 × 5-15 倍余量
_EXTRACTOR_TIME_LIMITS_MS: dict[str, float] = {
    "txt": 15.0,
    "json": 15.0,
    "yaml": 15.0,
    "xml": 15.0,
    "csv": 15.0,
    "md": 15.0,
    "html": 15.0,
    "docx": 30.0,
    "xlsx": 30.0,
    "pptx": 50.0,
    "eml": 15.0,
}

# 基准测量迭代次数（取平均）
_EXTRACTOR_ITERATIONS = 20


@pytest.mark.slow
class TestExtractorBenchmark:
    """提取器单格式提取速度基准回归测试。"""

    @pytest.mark.parametrize("ext", sorted(_EXTRACTOR_TIME_LIMITS_MS))
    def test_extract_speed(self, ext: str) -> None:
        """单格式 extract_from_bytes 平均耗时应在阈值内。"""
        from benchmarks.sample_files import generate_sample_bytes
        from fuscan.extractors import extract_content_from_bytes

        data = generate_sample_bytes(ext, size_hint=4096)
        assert len(data) > 0, f"格式 {ext} 生成的文件为空"

        # 预热（首次提取可能触发库初始化）
        extract_content_from_bytes(data, ext)

        durations: list[float] = []
        for _ in range(_EXTRACTOR_ITERATIONS):
            start = time.perf_counter()
            extract_content_from_bytes(data, ext)
            durations.append((time.perf_counter() - start) * 1000)

        avg_ms = sum(durations) / len(durations)
        limit = _EXTRACTOR_TIME_LIMITS_MS[ext]
        assert avg_ms <= limit, f"格式 {ext} 平均提取耗时 {avg_ms:.2f}ms 超过阈值 {limit:.1f}ms"
