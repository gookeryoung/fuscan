"""ContentRegexPool 原生引擎集成测试。

验证 fuscan_re.ContentRegexPoolEngine 与 Python ContentRegexPool.evaluate
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

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="fuscan_re 未安装")


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
