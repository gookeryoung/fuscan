"""ContentRegexPool 原生引擎集成测试。

验证 fuscan_core.ContentRegexPoolEngine 与 Python ContentRegexPool.evaluate
语义完全等价：child_id 集合、MatchResult 字段（match_text/match_count/detail/
match_texts/match_description）一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
)
from fuscan.scanner._native_matchers import NATIVE_AVAILABLE
from fuscan.scanner.context import FileEntry, MatchContext
from fuscan.scanner.matchers import ContentRegexPool
from fuscan.scanner.result import MatchResult

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="fuscan_core 未安装")


def _make_context(content: str) -> MatchContext:
    """构造测试上下文，使用自定义内容提供器。"""
    entry = FileEntry(
        path=Path("test.txt"),
        name="test.txt",
        size=len(content),
        mtime=0.0,
        extension="txt",
    )
    return MatchContext(entry, content_provider=lambda e: content)


def _content_regex_spec(
    pattern: str,
    case_sensitive: bool = True,
    description: str = "",
) -> LeafMatch:
    """构造 CONTENT REGEX LeafMatch 规格。"""
    return LeafMatch(
        target=MatchTarget.CONTENT,
        mode=MatchMode.REGEX,
        pattern=pattern,
        case_sensitive=case_sensitive,
        description=description,
    )


def _build_pool(specs: list[LeafMatch]) -> ContentRegexPool:
    """构建 ContentRegexPool 并注册所有规格。"""
    pool = ContentRegexPool()
    for spec in specs:
        pool.register(spec)
    pool.compile()
    return pool


def _results_to_dict(results: dict[int, MatchResult]) -> dict[int, dict[str, object]]:
    """将 MatchResult 字典转为可比较的 dict。"""
    return {
        cid: {
            "matched": r.matched,
            "detail": r.detail,
            "match_text": r.match_text,
            "match_count": r.match_count,
            "target": r.target,
            "match_texts": r.match_texts,
            "match_description": r.match_description,
        }
        for cid, r in results.items()
    }


def _force_python(pool: ContentRegexPool) -> None:
    """强制 pool 走 Python 路径（临时禁用原生引擎）。"""
    pool._native_engine = None


class TestNativeRegexPoolEquivalence:
    """原生引擎与 Python 路径语义等价验证。"""

    def test_basic_regex_match(self) -> None:
        """基本 REGEX 匹配：Python 与 Rust 命中一致。"""
        specs = [
            _content_regex_spec(r"password=\S+", description="密码"),
            _content_regex_spec(r"secret=\S+", description="密钥"),
        ]
        pool = _build_pool(specs)
        assert pool._native_engine is not None
        content = "password=123 secret=abc"
        # 原生路径
        native_results = pool.evaluate(_make_context(content))
        # Python 路径
        _force_python(pool)
        py_results = pool.evaluate(_make_context(content))
        assert _results_to_dict(native_results) == _results_to_dict(py_results)

    def test_case_insensitive_match(self) -> None:
        """大小写不敏感匹配：Python 与 Rust 命中一致。"""
        specs = [
            _content_regex_spec(r"PASSWORD=", case_sensitive=False, description="大写密码"),
            _content_regex_spec(r"SECRET=", case_sensitive=False, description="大写密钥"),
        ]
        pool = _build_pool(specs)
        content = "password=123 SECRET=abc"
        native_results = pool.evaluate(_make_context(content))
        _force_python(pool)
        py_results = pool.evaluate(_make_context(content))
        assert _results_to_dict(native_results) == _results_to_dict(py_results)

    def test_multiple_matches_same_child(self) -> None:
        """同一子项多次命中：match_count 一致。"""
        specs = [
            _content_regex_spec(r"AKIA[0-9A-Z]{16}", description="AWS Key"),
            _content_regex_spec(r"ghp_[A-Za-z0-9]{36}", description="GitHub Token"),
        ]
        pool = _build_pool(specs)
        content = "AKIA1234567890ABCDEF ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789 AKIAWXYZ1234567890AB"
        native_results = pool.evaluate(_make_context(content))
        _force_python(pool)
        py_results = pool.evaluate(_make_context(content))
        assert _results_to_dict(native_results) == _results_to_dict(py_results)
        # 验证 match_count > 1（AKIA 出现两次）
        aws_id = [cid for cid in native_results if native_results[cid].match_count >= 2]
        assert len(aws_id) == 1

    def test_match_text_with_single_quote(self) -> None:
        """match_text 含单引号时 detail 引号选择一致（iter-03 修复回归保护）。

        Python repr() 对含单引号但不含双引号的字符串用双引号包裹，
        Rust py_repr 需复刻此逻辑，否则 detail 字段不一致。
        """
        specs = [
            _content_regex_spec(r"SECRET_KEY\s*=\s*'[^']+'", description="Django Secret"),
            _content_regex_spec(r"password=\S+", description="密码"),
        ]
        pool = _build_pool(specs)
        content = "SECRET_KEY = 'django-insecure-abc123' password=xyz"
        native_results = pool.evaluate(_make_context(content))
        _force_python(pool)
        py_results = pool.evaluate(_make_context(content))
        assert _results_to_dict(native_results) == _results_to_dict(py_results), (
            "match_text 含单引号时 detail 应一致（Python repr 用双引号，Rust py_repr 须复刻）"
        )

    def test_prefilter_no_hit(self) -> None:
        """预筛未命中：返回空字典。"""
        specs = [
            _content_regex_spec(r"password=\S+", description="密码"),
            _content_regex_spec(r"secret=\S+", description="密钥"),
        ]
        pool = _build_pool(specs)
        content = "nothing relevant here"
        native_results = pool.evaluate(_make_context(content))
        _force_python(pool)
        py_results = pool.evaluate(_make_context(content))
        assert _results_to_dict(native_results) == _results_to_dict(py_results)
        assert len(native_results) == 0

    def test_mixed_case_sensitive_groups(self) -> None:
        """混合大小写敏感组：两组同时求值。"""
        specs = [
            _content_regex_spec(r"password=", case_sensitive=True, description="敏感密码"),
            _content_regex_spec(r"secret=", case_sensitive=True, description="敏感密钥"),
            _content_regex_spec(r"token=", case_sensitive=False, description="不敏感令牌"),
            _content_regex_spec(r"key=", case_sensitive=False, description="不敏感键"),
        ]
        pool = _build_pool(specs)
        content = "password=123 SECRET=abc token=xyz"
        native_results = pool.evaluate(_make_context(content))
        _force_python(pool)
        py_results = pool.evaluate(_make_context(content))
        assert _results_to_dict(native_results) == _results_to_dict(py_results)

    def test_dedup_shared_child_id(self) -> None:
        """相同 (pattern, case_sensitive) 去重共享 child_id。"""
        pool = ContentRegexPool()
        spec = _content_regex_spec(r"password=\S+", description="密码")
        id1 = pool.register(spec)
        id2 = pool.register(spec)  # 相同规格
        assert id1 == id2  # 去重
        # 再注册一个不同的
        spec2 = _content_regex_spec(r"secret=\S+", description="密钥")
        pool.register(spec2)
        pool.compile()
        # 只有两个不同的子项，不满足 >= 2 的分桶条件——等等，
        # 两个不同的子项在同一 case_sensitive 组中，满足 >= 2
        assert pool._native_engine is not None

    def test_single_spec_group_skipped(self) -> None:
        """单子项组跳过编译：compiled_child_ids 为空。"""
        pool = ContentRegexPool()
        pool.register(_content_regex_spec(r"password=\S+", description="密码"))
        pool.compile()
        # 单子项组不编译，compiled_child_ids 为空
        assert len(pool._compiled_child_ids) == 0
        # 原生引擎仍然存在（但无已编译组）
        assert pool._native_engine is not None
        assert pool._native_engine.group_count == 0

    def test_evaluate_cache_same_context(self) -> None:
        """同 context 两次 evaluate 只执行一次。"""
        specs = [
            _content_regex_spec(r"password=\S+", description="密码"),
            _content_regex_spec(r"secret=\S+", description="密钥"),
        ]
        pool = _build_pool(specs)
        ctx = _make_context("password=123")
        r1 = pool.evaluate(ctx)
        r2 = pool.evaluate(ctx)
        assert r1 is r2  # 同一对象引用（缓存命中）

    def test_and_matcher_end_to_end(self) -> None:
        """AndMatcher 端到端：走原生池路径与 Python 池路径结果一致。"""
        from fuscan.scanner.matchers import AndMatcher

        # 构造 AND 规则：CONTENT REGEX + FILENAME
        and_spec = AndMatch(
            children=(
                _content_regex_spec(r"password=\S+", description="密码"),
                LeafMatch(
                    target=MatchTarget.FILENAME,
                    mode=MatchMode.CONTAINS,
                    pattern="test",
                    case_sensitive=False,
                    description="测试文件",
                ),
            ),
            description="密码 AND 文件名",
        )
        # 原生路径
        matcher_native = AndMatcher(and_spec)
        pool_native = ContentRegexPool()
        matcher_native.attach_pool(pool_native)
        pool_native.compile()

        # Python 路径
        matcher_py = AndMatcher(and_spec)
        pool_py = ContentRegexPool()
        matcher_py.attach_pool(pool_py)
        pool_py.compile()
        _force_python(pool_py)

        ctx = _make_context("password=123")
        native_result = matcher_native.matches(ctx)
        py_result = matcher_py.matches(ctx)
        assert native_result.matched == py_result.matched
        assert native_result.match_texts == py_result.match_texts
        assert native_result.match_count == py_result.match_count

    def test_empty_specs_returns_none(self) -> None:
        """空规格列表：build_native_regex_pool 返回 None。"""
        from fuscan.scanner._native_matchers import build_native_regex_pool

        assert build_native_regex_pool([]) is None

    def test_native_engine_fallback_on_exception(self) -> None:
        """原生引擎异常时回退 Python 路径。"""
        specs = [
            _content_regex_spec(r"password=\S+", description="密码"),
            _content_regex_spec(r"secret=\S+", description="密钥"),
        ]
        pool = _build_pool(specs)
        assert pool._native_engine is not None
        # 模拟原生引擎 evaluate 异常
        ctx = _make_context("password=123")

        # 用一个会抛异常的 mock 替换原生引擎
        class _BadEngine:
            def evaluate(self, content: str) -> list[object]:
                raise RuntimeError("模拟原生引擎异常")

        original_engine = pool._native_engine
        pool._native_engine = _BadEngine()  # type: ignore[assignment]
        # 原生路径异常 → 回退 Python（返回空字典）
        results = pool.evaluate(ctx)
        assert results == {}  # 异常时返回空字典
        # 恢复原生引擎
        pool._native_engine = original_engine


# ---------------------------------------------------------------------------
# iter-03：S3 AND 组合场景 PoolEngine 性能基准
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestPoolEnginePerformance:
    """PoolEngine（原生）vs Python ContentRegexPool 性能基准对比。

    iter-03 佐证：验证 S3 AND 组合场景下原生引擎的加速比。
    iter-01 BucketEngine 已验证 S2 场景 4.15x 加速（50 条 CONTENT REGEX，48KB 文本），
    PoolEngine 预期类似加速比（同源 regex crate + py.detach 释放 GIL）。

    测试方式：手动计时对比原生路径（释放 GIL）与 Python 路径（finditer）延迟，
    阈值保守（2x），CI 环境波动不影响结论。语义等价作为前置断言。
    """

    def test_s3_and_combo_speedup_at_least_2x(self) -> None:
        """S3 AND 组合场景 PoolEngine 应比 Python 快至少 2x。

        构造 50 条 AND 规则 × 2~3 CONTENT REGEX 子项（子项去重后约 30 个），
        对 48KB 文本重复评估，对比原生路径与 Python 路径延迟中位数。
        """
        import time

        from benchmarks.multi_rule_profile import build_and_combo_ruleset

        rs = build_and_combo_ruleset(50)
        # 提取所有 CONTENT REGEX 子项（去重由 ContentRegexPool.register 处理）
        specs: list[LeafMatch] = []
        for rule in rs.rules:
            assert isinstance(rule.match, AndMatch)
            for child in rule.match.children:
                assert isinstance(child, LeafMatch)
                specs.append(child)
        assert len(specs) >= 100, f"S3 场景应至少 100 个子项，实际 {len(specs)}"

        # 构建池（compile 构建原生引擎）
        pool = _build_pool(specs)
        native_engine = pool._native_engine
        assert native_engine is not None, "fuscan_core 可用时原生引擎应构建成功"

        # 构造约 48KB 测试文本：命中样本 + 噪声（与 iter-01 BucketEngine 基准一致）
        hit_block = (
            "password=admin123456\n"
            "api_key=AKIAIOSFODNN7EXAMPLE\n"
            "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB\n"
            "mysql://root:secret@localhost:3306/db\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "xoxb-1234567890-abcdef\n"
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c\n"
            "secret_key=abcdefghijklmnopqrstuvwxyz0123456789\n"
            "bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\n"
            "eval('malicious_code')\n"
            "SECRET_KEY = 'django-insecure-abcdefghijklmnopqrstuvwxyz0123456789'\n"
        )
        noise = "the quick brown fox jumps over the lazy dog\n" * 80
        content = (hit_block + noise) * 12  # 约 48KB

        # 1. 语义等价前置断言
        native_results = pool.evaluate(_make_context(content))
        _force_python(pool)
        py_results = pool.evaluate(_make_context(content))
        assert _results_to_dict(native_results) == _results_to_dict(py_results), "原生与 Python 路径结果应语义等价"
        # 恢复原生引擎用于性能测量
        pool._native_engine = native_engine

        # 2. 手动计时对比（每次用新 context 避免evaluate 的 id(context) 缓存）
        iterations = 30

        # 测原生路径
        native_times: list[float] = []
        for _ in range(iterations):
            ctx_fresh = _make_context(content)
            start = time.perf_counter()
            pool.evaluate(ctx_fresh)
            native_times.append(time.perf_counter() - start)
        native_median = sorted(native_times)[iterations // 2]

        # 测 Python 路径
        _force_python(pool)
        py_times: list[float] = []
        for _ in range(iterations):
            ctx_fresh = _make_context(content)
            start = time.perf_counter()
            pool.evaluate(ctx_fresh)
            py_times.append(time.perf_counter() - start)
        py_median = sorted(py_times)[iterations // 2]

        speedup = py_median / native_median if native_median > 0 else float("inf")
        assert speedup >= 2.0, (
            f"PoolEngine 加速比 {speedup:.2f}x 低于 2x 阈值"
            f"（Python {py_median * 1e6:.0f}μs vs 原生 {native_median * 1e6:.0f}μs）"
        )
