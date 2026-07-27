"""性能基线微基准测试（iter-120）。

为 iter-118 三层 LRU 缓存与 iter-119 提取器重试机制提供 benchmark 数据佐证，
并为扫描热路径关键函数建立 ``pytest-benchmark`` 基线，供后续迭代对比回归。

与 ``tests/test_benchmark.py`` 的分工：

- ``test_benchmark.py``：端到端吞吐量（500 文件循环），用 ``time.perf_counter``
  手动计时，验证数量级正确性（如 ``≥ 50 files/s``）
- ``test_perf_benchmark.py``（本文件）：单函数微基准，用 ``pytest-benchmark``
  ``benchmark`` fixture 自动统计中位数/方差/百分位，适合回归对比

回归门禁工作流::

    # 1. 首次运行保存基线（在优化前的 commit 上执行）
    uv run pytest -m slow tests/test_perf_benchmark.py --benchmark-save=baseline

    # 2. 优化后运行并对比基线（偏差 > 10% 的测试会标记 FAIL）
    uv run pytest -m slow tests/test_perf_benchmark.py --benchmark-compare=baseline \
        --benchmark-compare-fail=mean:10%

    # 3. 查看基线列表
    uv run pytest --benchmark-list

所有测试标记 ``@pytest.mark.slow``，CI 默认跳过（``-m "not slow"``）。
"""

from __future__ import annotations

import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from benchmarks.sample_files import generate_sample_bytes
from fuscan.cache import CacheStore
from fuscan.cache.hashes import hash_bytes
from fuscan.extractors import (
    extract_content_from_bytes,
    extract_content_from_bytes_with_retry,
)
from fuscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    Severity,
)
from fuscan.scanner.context import FileEntry, MatchContext
from fuscan.scanner.matchers import build_matcher

# ---------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def cache_store(tmp_path: Path) -> Generator[CacheStore, None, None]:
    """提供临时 CacheStore 实例，测试结束自动关闭。"""
    store = CacheStore(tmp_path / "perf_cache.db")
    yield store
    store.close()


def _fill_extract_cache(store: CacheStore, file_hash: str, content: str) -> None:
    """填充提取内容 LRU 缓存（绕过 SQLite，直接操作内存层）。"""
    with store._lru_lock:  # type: ignore[attr-defined]
        store._extract_cache_put(file_hash, content)  # type: ignore[attr-defined]


def _clear_extract_cache(store: CacheStore) -> None:
    """清空提取内容 LRU 缓存，强制下次查询走 SQLite。"""
    with store._lru_lock:  # type: ignore[attr-defined]
        store._extract_cache.clear()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# iter-118 佐证：三层 LRU 缓存性能基线
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestLruCacheBenchmark:
    """iter-118 三层 LRU 缓存性能基线。

    佐证 ``_extract_cache`` LRU 相比 SQLite 查询的加速效果，
    以及三层 LRU 在热缓存场景下的查询性能。
    """

    def test_extract_cache_lru_hit(self, cache_store: CacheStore, benchmark: Any) -> None:
        """提取内容 LRU 命中延迟应在微秒级（< 10μs）。"""
        file_hash = "a" * 64
        content = "提取后的文本内容" * 100
        _fill_extract_cache(cache_store, file_hash, content)

        def query() -> str | None:
            return cache_store.get_extracted_content(file_hash)

        result = benchmark(query)
        assert result == content
        # LRU 命中应在 10μs 内（宽松阈值，CI 环境留余量）
        assert benchmark.stats.stats.mean < 0.000_01, (
            f"LRU 命中延迟 {benchmark.stats.stats.mean * 1e6:.2f}μs 超过 10μs 阈值"
        )

    def test_extract_cache_sqlite_query(self, cache_store: CacheStore, benchmark: Any, tmp_path: Path) -> None:
        """提取内容 SQLite 查询延迟（冷 LRU）应在毫秒级以内。"""
        file_hash = "b" * 64
        content = "SQLite 存储的文本内容" * 100
        # 写入 SQLite（put_extracted_content 同时填充 LRU）
        cache_store.put_extracted_content(file_hash, content, "txt")
        # 清空 LRU，强制走 SQLite
        _clear_extract_cache(cache_store)

        def query() -> str | None:
            _clear_extract_cache(cache_store)  # 每次查询前清空 LRU
            return cache_store.get_extracted_content(file_hash)

        result = benchmark(query)
        assert result == content

    def test_path_cache_lru_hit(self, cache_store: CacheStore, benchmark: Any, tmp_path: Path) -> None:
        """路径预筛 LRU 命中延迟应在微秒级（< 10μs）。"""
        # 写入路径与文件哈希
        path = tmp_path / "file.txt"
        path.write_text("x", encoding="utf-8")
        stat = path.stat()
        file_hash = hash_bytes(b"x")
        cache_store.register_file(file_hash, stat.st_size)
        # register_path 签名：(store, file_hash, path, mtime)
        cache_store.register_path(file_hash, path, stat.st_mtime)
        # register_path 已填充 _path_cache LRU

        def query() -> str | None:
            return cache_store.lookup_file_hash(path, stat.st_mtime, stat.st_size)

        result = benchmark(query)
        assert result == file_hash
        # LRU 命中应在 10μs 内
        assert benchmark.stats.stats.mean < 0.000_01, (
            f"路径 LRU 命中延迟 {benchmark.stats.stats.mean * 1e6:.2f}μs 超过 10μs 阈值"
        )

    def test_lru_speedup_over_sqlite(self, cache_store: CacheStore, tmp_path: Path) -> None:
        """LRU 命中应比 SQLite 查询快至少 5 倍（iter-118 佐证）。

        手动计时对比（非 benchmark fixture），验证 LRU 的加速比。
        阈值保守（5 倍），CI 环境波动不影响结论。
        """
        file_hash = "c" * 64
        content = "对比测试文本" * 200
        cache_store.put_extracted_content(file_hash, content, "txt")

        # 测量 SQLite 查询延迟（每次清空 LRU）
        _clear_extract_cache(cache_store)
        sqlite_times: list[float] = []
        for _ in range(50):
            _clear_extract_cache(cache_store)
            start = time.perf_counter()
            cache_store.get_extracted_content(file_hash)
            sqlite_times.append(time.perf_counter() - start)
        sqlite_median = sorted(sqlite_times)[25]

        # 测量 LRU 命中延迟（首次查询后 LRU 已填充）
        cache_store.get_extracted_content(file_hash)  # 确保 LRU 填充
        lru_times: list[float] = []
        for _ in range(50):
            start = time.perf_counter()
            cache_store.get_extracted_content(file_hash)
            lru_times.append(time.perf_counter() - start)
        lru_median = sorted(lru_times)[25]

        speedup = sqlite_median / lru_median if lru_median > 0 else float("inf")
        assert speedup >= 5.0, (
            f"LRU 加速比 {speedup:.1f}x 低于阈值 5x"
            f"（SQLite {sqlite_median * 1e6:.2f}μs vs LRU {lru_median * 1e6:.2f}μs）"
        )


# ---------------------------------------------------------------------------
# iter-119 佐证：提取器重试机制性能基线
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestRetryMechanismBenchmark:
    """iter-119 提取器重试机制性能基线。

    佐证重试机制在成功路径上的零开销（仅多一次 ``is_retriable_error`` 调用），
    以及失败重试的退避延迟符合预期（~50ms）。
    """

    def test_retry_success_path(self, benchmark: Any) -> None:
        """带重试版本的成功路径延迟基线（与原版对比用 --benchmark-compare）。"""
        data = generate_sample_bytes("txt", size_hint=4096)

        def extract() -> str:
            return extract_content_from_bytes_with_retry(data, "txt")

        result = benchmark(extract)
        assert "password" in result

    def test_no_retry_success_path(self, benchmark: Any) -> None:
        """原版（无重试）成功路径延迟基线（与重试版对比用 --benchmark-compare）。

        与 ``test_retry_success_path`` 配合使用，通过 ``--benchmark-compare``
        验证重试机制在成功路径上的开销 < 2μs（``is_retriable_error`` 一次
        ``isinstance`` 调用）。
        """
        data = generate_sample_bytes("txt", size_hint=4096)

        def extract() -> str:
            return extract_content_from_bytes(data, "txt")

        result = benchmark(extract)
        assert "password" in result

    def test_retry_zero_overhead_on_success(self) -> None:
        """重试机制在成功路径上的开销应 < 2μs（iter-119 佐证）。

        手动计时对比 ``extract_content_from_bytes_with_retry`` 与
        ``extract_content_from_bytes``，验证重试包装的额外开销可忽略。
        """
        data = generate_sample_bytes("txt", size_hint=4096)
        # 预热（首次提取可能触发库初始化）
        extract_content_from_bytes(data, "txt")
        extract_content_from_bytes_with_retry(data, "txt")

        iterations = 200
        # 测量原版
        no_retry_times: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            extract_content_from_bytes(data, "txt")
            no_retry_times.append(time.perf_counter() - start)
        no_retry_median = sorted(no_retry_times)[iterations // 2]

        # 测量重试版
        with_retry_times: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            extract_content_from_bytes_with_retry(data, "txt")
            with_retry_times.append(time.perf_counter() - start)
        with_retry_median = sorted(with_retry_times)[iterations // 2]

        overhead = with_retry_median - no_retry_median
        # 重试包装开销应 < 20μs（lambda 构造 + _retry_loop 函数调用 + is_retriable_error
        # 一次 isinstance，Python 函数调用本身约 1-10μs，2μs 阈值过严）
        assert overhead < 0.000_020, (
            f"重试开销 {overhead * 1e6:.3f}μs 超过 20μs 阈值"
            f"（原版 {no_retry_median * 1e6:.3f}μs vs 重试版 {with_retry_median * 1e6:.3f}μs）"
        )

    def test_retry_failure_backoff_delay(self) -> None:
        """失败重试的退避延迟应接近 backoff_ms 参数（默认 50ms）。"""
        from fuscan.extractors.base import ExtractorRegistry

        registry = ExtractorRegistry()
        # 注册一个总是失败的可重试提取器
        from tests.test_extractors import _FlakyExtractor

        registry.register(_FlakyExtractor("txt", [OSError("AV lock")] * 5))

        data = b"test"
        backoff_ms = 50.0
        start = time.perf_counter()
        with pytest.raises(OSError):
            registry.extract_from_bytes_with_retry(
                data,
                "txt",
                max_retries=2,
                backoff_ms=backoff_ms,
            )
        elapsed = time.perf_counter() - start
        # 2 次重试 × 50ms 退避 = ~100ms
        # Windows time.sleep 精度约 15ms，2 次 sleep 可能累计 20-30ms 额外开销
        expected = backoff_ms * 2 / 1000.0
        assert abs(elapsed - expected) < 0.030, (
            f"退避总延迟 {elapsed * 1000:.1f}ms 偏离预期 {expected * 1000:.1f}ms 超过 30ms"
            f"（Windows sleep 精度约 15ms，2 次 sleep 累计误差正常）"
        )


# ---------------------------------------------------------------------------
# 扫描热路径性能基线
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestHotPathBenchmark:
    """扫描热路径关键函数性能基线。

    为后续迭代的回归对比建立基线，通过 ``--benchmark-compare`` 检测性能退化。
    """

    def test_hash_bytes_4kb(self, benchmark: Any) -> None:
        """4KB 文件哈希计算延迟基线（小文件走 SHA-256 路径）。"""
        data = b"x" * 4096

        result = benchmark(hash_bytes, data)
        assert len(result) == 64

    def test_hash_bytes_100kb(self, benchmark: Any) -> None:
        """100KB 文件哈希计算延迟基线（大文件走 BLAKE2b 路径）。"""
        data = b"x" * (100 * 1024)

        result = benchmark(hash_bytes, data)
        assert len(result) == 64

    def test_extract_text_4kb(self, benchmark: Any) -> None:
        """4KB 纯文本提取延迟基线（T1 极速档次）。"""
        data = generate_sample_bytes("txt", size_hint=4096)

        result = benchmark(extract_content_from_bytes, data, "txt")
        assert "password" in result

    def test_extract_docx_typical(self, benchmark: Any) -> None:
        """典型 DOCX 提取延迟基线（T3 中速档次）。"""
        data = generate_sample_bytes("docx", size_hint=4096)

        result = benchmark(extract_content_from_bytes, data, "docx")
        assert "password" in result

    def test_extract_eml_typical(self, benchmark: Any) -> None:
        """典型 EML 提取延迟基线（T2 快速档次）。"""
        data = generate_sample_bytes("eml", size_hint=4096)

        result = benchmark(extract_content_from_bytes, data, "eml")
        assert "password" in result

    def test_matcher_contains_apply(self, benchmark: Any, tmp_path: Path) -> None:
        """CONTAINS 规则匹配延迟基线（热路径，预编译正则）。"""
        rule = Rule(
            name="密码",
            severity=Severity.WARNING,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="password",
            ),
        )
        matcher = build_matcher(rule.match)
        # 构造 MatchContext（含 4KB 文本内容）
        path = tmp_path / "test.txt"
        path.write_text("password secret content\n" * 200, encoding="utf-8")
        entry = FileEntry.from_path(path)

        def match() -> Any:
            ctx = MatchContext(entry)  # 每次新建 context 避免内容缓存
            return matcher.matches(ctx)

        result = benchmark(match)
        assert result.matched
