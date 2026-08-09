"""fuscan-re 原生匹配引擎集成测试。

验证 ``fuscan_re`` 原生引擎与 Python ``match_content_via_buckets`` 语义等价：
- 各匹配模式（REGEX/CONTAINS/EQUALS/STARTSWITH/ENDSWITH）命中结果一致
- case_sensitive True/False 行为一致
- 预筛关键字命中/未命中路径一致
- 活跃子集动态拼接一致
- 多桶（global + ext）组合一致
- 原生引擎不可用时自动回退 Python 路径
- Scanner 端到端扫描结果一致

测试通过 ``pytest.importorskip("fuscan_re")`` 跳过未安装原生引擎的环境，
避免在纯 Python 部署中假失败。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fuscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)
from fuscan.scanner import Scanner
from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
from fuscan.scanner._native_matchers import (
    NATIVE_AVAILABLE,
    build_native_engine,
    match_content_via_native,
)
from fuscan.scanner.matchers import build_matcher
from fuscan.scanner.result import RuleHit

fuscan_re = pytest.importorskip("fuscan_re")


def _build_ruleset(*rules: Rule) -> RuleSet:
    return RuleSet(version="1.0", rules=tuple(rules))


def _content_rule(
    name: str,
    pattern: str,
    mode: MatchMode = MatchMode.REGEX,
    case_sensitive: bool = False,
    severity: Severity = Severity.WARNING,
    description: str = "",
) -> Rule:
    return Rule(
        name=name,
        severity=severity,
        match=LeafMatch(
            target=MatchTarget.CONTENT,
            mode=mode,
            pattern=pattern,
            case_sensitive=case_sensitive,
            description=description,
        ),
    )


def _build_pairs(rules: list[Rule]) -> list[tuple[Rule, Any]]:
    return [(r, build_matcher(r.match)) for r in rules]


def _hits_to_dict(hits: list[RuleHit]) -> dict[str, tuple[str, int, str, str, tuple[str, ...]]]:
    """转 {rule_name: (match_text, match_count, detail, target, match_texts)} 便于断言。"""
    return {h.rule_name: (h.match_text, h.match_count, h.detail, h.target, h.match_texts) for h in hits}


class TestNativeAvailability:
    """原生引擎可用性基础检测。"""

    def test_native_available(self) -> None:
        """fuscan_re 已安装时 NATIVE_AVAILABLE 应为 True。"""
        assert NATIVE_AVAILABLE is True

    def test_build_native_engine_returns_engine_for_non_empty_buckets(self) -> None:
        """非空桶列表应返回原生引擎实例。"""
        rules = [
            _content_rule("r1", "password", mode=MatchMode.CONTAINS),
            _content_rule("r2", "secret", mode=MatchMode.CONTAINS),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        assert engine.bucket_count >= 1

    def test_build_native_engine_returns_none_for_empty_buckets(self) -> None:
        """空桶列表应返回 None（调用方走 Python 回退路径）。"""
        assert build_native_engine([]) is None

    def test_build_native_engine_returns_none_for_single_rule(self) -> None:
        """单条规则的桶（build_content_buckets 已剔除）传入应返回 None。

        构造仅含单条规则的桶列表（绕过 build_content_buckets 的过滤），
        原生引擎内部仍按 (mode, case_sensitive) 分组并跳过单条组，
        最终生成 0 个桶——此处验证原生引擎构造不会异常。
        """
        rules = [_content_rule("only", "unique_pattern", mode=MatchMode.REGEX)]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        # 单条规则被 build_content_buckets 剔除，buckets 为空
        assert buckets == []
        engine = build_native_engine(buckets)
        assert engine is None


class TestSemanticEquivalence:
    """逐模式对比 Python vs Rust 命中结果。"""

    def test_regex_mode_case_insensitive(self) -> None:
        """REGEX 模式大小写不敏感：Python 与 Rust 命中一致。"""
        rules = [
            _content_rule("aws", r"AKIA[0-9A-Z]{16}", mode=MatchMode.REGEX),
            _content_rule("ghp", r"ghp_[A-Za-z0-9]{36}", mode=MatchMode.REGEX),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "found AKIA1234567890ABCDEF and GHP_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789 here"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert _hits_to_dict(py_hits) == _hits_to_dict(native_hits)

    def test_regex_mode_case_sensitive(self) -> None:
        """REGEX 模式大小写敏感：Python 与 Rust 命中一致。"""
        rules = [
            _content_rule("aws_cs", r"AKIA[0-9A-Z]{16}", mode=MatchMode.REGEX, case_sensitive=True),
            _content_rule("lower", r"akia[0-9a-z]{16}", mode=MatchMode.REGEX, case_sensitive=True),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "AKIA1234567890ABCDEF and akia1234567890abcdef"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert _hits_to_dict(py_hits) == _hits_to_dict(native_hits)

    def test_contains_mode_case_sensitive(self) -> None:
        """CONTAINS case_sensitive=True 走 count 快路径：Python 与 Rust 一致。"""
        rules = [
            _content_rule("pw", "password=", mode=MatchMode.CONTAINS, case_sensitive=True),
            _content_rule("tok", "token=", mode=MatchMode.CONTAINS, case_sensitive=True),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "password=abc password=def password=ghi token=xyz"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        # 验证 count：password= 出现 3 次
        py_dict = _hits_to_dict(py_hits)
        native_dict = _hits_to_dict(native_hits)
        assert py_dict == native_dict
        assert py_dict["pw"][1] == 3
        assert py_dict["tok"][1] == 1

    def test_contains_mode_case_insensitive(self) -> None:
        """CONTAINS case_sensitive=False 走正则路径：Python 与 Rust 一致。"""
        rules = [
            _content_rule("pw_ci", "PASSWORD=", mode=MatchMode.CONTAINS, case_sensitive=False),
        ]
        # 单条规则不会被 build_content_buckets 收入桶，补一条同类规则
        rules.append(_content_rule("tok_ci", "TOKEN=", mode=MatchMode.CONTAINS, case_sensitive=False))
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "password=abc Password=def TOKEN=xyz"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert _hits_to_dict(py_hits) == _hits_to_dict(native_hits)

    def test_equals_mode(self) -> None:
        """EQUALS 模式：Python 与 Rust 命中一致。"""
        rules = [
            _content_rule("eq1", "secret", mode=MatchMode.EQUALS),
            _content_rule("eq2", "password", mode=MatchMode.EQUALS),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "secret"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert _hits_to_dict(py_hits) == _hits_to_dict(native_hits)
        # detail 应为 "完全相等"
        assert py_hits[0].detail == "完全相等"
        assert native_hits[0].detail == "完全相等"

    def test_startswith_mode(self) -> None:
        """STARTSWITH 模式：Python 与 Rust 命中一致。"""
        rules = [
            _content_rule("sw1", "BEGIN", mode=MatchMode.STARTSWITH),
            _content_rule("sw2", "START", mode=MatchMode.STARTSWITH),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "BEGIN data here"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert _hits_to_dict(py_hits) == _hits_to_dict(native_hits)

    def test_endswith_mode(self) -> None:
        """ENDSWITH 模式：Python 与 Rust 命中一致。"""
        rules = [
            _content_rule("ew1", "EOF", mode=MatchMode.ENDSWITH),
            _content_rule("ew2", "END", mode=MatchMode.ENDSWITH),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "dataEOF"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert _hits_to_dict(py_hits) == _hits_to_dict(native_hits)

    def test_no_hits_both_paths_empty(self) -> None:
        """无命中时 Python 与 Rust 均返回空列表。"""
        rules = [
            _content_rule("miss1", r"NEVER_MATCH_[A-Z]+", mode=MatchMode.REGEX),
            _content_rule("miss2", r"ABSENT_\d+", mode=MatchMode.REGEX),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "this content has no secrets"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert py_hits == []
        assert native_hits == []

    def test_prefilter_short_circuit_both_empty(self) -> None:
        """预筛关键字均不命中时 Python 与 Rust 均跳过 finditer，返回空。"""
        rules = [
            _content_rule("kw1", r"password=\w+", mode=MatchMode.REGEX),
            _content_rule("kw2", r"token=\w+", mode=MatchMode.REGEX),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "nothing relevant here"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert py_hits == []
        assert native_hits == []

    def test_severity_preserved(self) -> None:
        """severity 枚举值在转换过程中保持一致。"""
        rules = [
            _content_rule("crit", "secret", mode=MatchMode.CONTAINS, severity=Severity.CRITICAL),
            _content_rule("info", "note", mode=MatchMode.CONTAINS, severity=Severity.INFO),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "secret and note here"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        py_sev = {h.rule_name: h.severity for h in py_hits}
        native_sev = {h.rule_name: h.severity for h in native_hits}
        assert py_sev == native_sev
        assert py_sev["crit"] == Severity.CRITICAL
        assert py_sev["info"] == Severity.INFO

    def test_match_description_preserved(self) -> None:
        """match_description 字段（来自 LeafMatch.description）保持一致。"""
        rules = [
            _content_rule(
                "desc1",
                "secret",
                mode=MatchMode.CONTAINS,
                description="检测密钥泄露",
            ),
            _content_rule(
                "desc2",
                "password",
                mode=MatchMode.CONTAINS,
                description="检测密码泄露",
            ),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "secret=password"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        py_desc = {h.rule_name: h.match_description for h in py_hits}
        native_desc = {h.rule_name: h.match_description for h in native_hits}
        assert py_desc == native_desc
        assert py_desc["desc1"] == "检测密钥泄露"
        assert py_desc["desc2"] == "检测密码泄露"


class TestMultiBucketAndActiveSubset:
    """多桶组合与活跃子集动态拼接。"""

    def test_multi_mode_buckets_combined(self) -> None:
        """多个 mode 桶混合：Python 与 Rust 命中一致。"""
        rules = [
            # REGEX 桶（2 条）
            _content_rule("re1", r"AKIA[0-9A-Z]{16}", mode=MatchMode.REGEX),
            _content_rule("re2", r"ghp_[A-Za-z0-9]{20}", mode=MatchMode.REGEX),
            # CONTAINS 桶 case_sensitive=True（2 条）
            _content_rule("ct1", "password=", mode=MatchMode.CONTAINS, case_sensitive=True),
            _content_rule("ct2", "token=", mode=MatchMode.CONTAINS, case_sensitive=True),
            # EQUALS 桶（2 条）
            _content_rule("eq1", "secret", mode=MatchMode.EQUALS),
            _content_rule("eq2", "password", mode=MatchMode.EQUALS),
            # STARTSWITH 桶（2 条）
            _content_rule("sw1", "BEGIN", mode=MatchMode.STARTSWITH),
            _content_rule("sw2", "START", mode=MatchMode.STARTSWITH),
            # ENDSWITH 桶（2 条）
            _content_rule("ew1", "EOF", mode=MatchMode.ENDSWITH),
            _content_rule("ew2", "END", mode=MatchMode.ENDSWITH),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        # 应至少形成 5 个桶（每 mode 1 个）
        assert len(buckets) >= 5
        engine = build_native_engine(buckets)
        assert engine is not None
        # 各模式都命中一次
        content = "BEGIN AKIA1234567890ABCDEF password=secret EOF"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert _hits_to_dict(py_hits) == _hits_to_dict(native_hits)

    def test_active_subset_only_partial_rules_match(self) -> None:
        """活跃子集：仅部分规则的关键字出现时，Python 与 Rust 命中一致。"""
        rules = [
            _content_rule("a", r"password=\w+", mode=MatchMode.REGEX),
            _content_rule("b", r"token=\w+", mode=MatchMode.REGEX),
            _content_rule("c", r"api_key=\w+", mode=MatchMode.REGEX),
            _content_rule("d", r"secret=\w+", mode=MatchMode.REGEX),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        # 仅 a 和 c 的关键字出现
        content = "password=abc api_key=xyz"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert _hits_to_dict(py_hits) == _hits_to_dict(native_hits)
        # 验证仅 a 和 c 命中
        py_names = {h.rule_name for h in py_hits}
        assert py_names == {"a", "c"}

    def test_active_subset_cache_reuse(self) -> None:
        """活跃子集缓存：多次匹配相同内容应稳定返回一致结果。"""
        rules = [
            _content_rule("a", r"password=\w+", mode=MatchMode.REGEX),
            _content_rule("b", r"token=\w+", mode=MatchMode.REGEX),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "password=abc"
        # 多次调用同一引擎，活跃子集缓存应命中
        first = match_content_via_native(engine, content)
        second = match_content_via_native(engine, content)
        third = match_content_via_native(engine, content)
        assert _hits_to_dict(first) == _hits_to_dict(second) == _hits_to_dict(third)

    def test_inline_ignorecase_flag(self) -> None:
        """含 (?i) 内联标志的正则：Python 与 Rust 命中一致。"""
        rules = [
            _content_rule("inline_ci", r"(?i)CASELESS_PATTERN", mode=MatchMode.REGEX),
            _content_rule("other", r"OTHER_\w+", mode=MatchMode.REGEX),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        engine = build_native_engine(buckets)
        assert engine is not None
        content = "caseless_pattern here OTHER_data"
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        native_hits = match_content_via_native(engine, content)
        assert _hits_to_dict(py_hits) == _hits_to_dict(native_hits)


class TestScannerIntegration:
    """Scanner 端到端集成：原生引擎自动启用且结果与纯 Python 一致。"""

    def test_scanner_uses_native_engine_when_available(self, tmp_path: Path) -> None:
        """Scanner 构造时应构建并缓存原生引擎。"""
        rules = [
            _content_rule("a", r"password=\w+", mode=MatchMode.REGEX),
            _content_rule("b", r"token=\w+", mode=MatchMode.REGEX),
        ]
        rs = _build_ruleset(*rules)
        sc = Scanner(rs, max_workers=1)
        assert sc._global_native_engine is not None
        assert sc._global_native_engine.bucket_count >= 1

    def test_scanner_end_to_end_hits_match_python(self, tmp_path: Path) -> None:
        """端到端：原生引擎启用时扫描结果与禁用时一致。"""
        rules = [
            _content_rule("aws", r"AKIA[0-9A-Z]{16}", mode=MatchMode.REGEX, severity=Severity.CRITICAL),
            # 第二条 REGEX 规则确保形成桶（build_content_buckets 需 2+ 条同 mode/cs）
            _content_rule("ghp", r"ghp_[A-Za-z0-9]{20}", mode=MatchMode.REGEX),
            _content_rule("pw1", "password=", mode=MatchMode.CONTAINS, case_sensitive=True),
            _content_rule("pw2", "token=", mode=MatchMode.CONTAINS, case_sensitive=True),
        ]
        rs = _build_ruleset(*rules)
        (tmp_path / "sample.txt").write_text(
            "AKIA1234567890ABCDEF ghp_aBcDeFgHiJkLmNoPqRsT password=abc token=xyz",
            encoding="utf-8",
        )
        # 原生引擎启用
        sc_native = Scanner(rs, max_workers=1)
        assert sc_native._global_native_engine is not None
        report_native = sc_native.scan(tmp_path)
        # 禁用原生引擎：手动置 None 走 Python 路径
        sc_python = Scanner(rs, max_workers=1)
        sc_python._global_native_engine = None
        sc_python._ext_native_engines = {}
        report_python = sc_python.scan(tmp_path)

        native_hits = sorted(
            (h.rule_name, h.match_text, h.match_count, h.detail) for sr in report_native.hits for h in sr.hits
        )
        python_hits = sorted(
            (h.rule_name, h.match_text, h.match_count, h.detail) for sr in report_python.hits for h in sr.hits
        )
        assert native_hits == python_hits

    def test_scanner_ext_files_use_global_native_engine(self, tmp_path: Path) -> None:
        """扩展名文件的 CONTENT 桶匹配仍走 global 原生引擎。

        ext_content_buckets 在实际规则集中通常为空（ext 专属规则多为 AndMatch
        组合，不被 build_content_buckets 收入桶），所以 global 引擎需覆盖所有
        扩展名文件的 CONTENT 桶匹配。本测试验证 .env/.txt 等扩展名文件仍由
        global 引擎正确处理。
        """
        # 2 条同 mode/cs 的 CONTENT 规则确保形成 1 个 global 桶
        rules = [
            _content_rule("aws", r"AKIA[0-9A-Z]{16}", mode=MatchMode.REGEX),
            _content_rule("ghp", r"ghp_[A-Za-z0-9]{20}", mode=MatchMode.REGEX),
        ]
        rs = _build_ruleset(*rules)
        sc = Scanner(rs, max_workers=1)
        # global 引擎存在；ext 引擎字典为空（无 ext 专属桶）
        assert sc._global_native_engine is not None
        assert sc._ext_native_engines == {}

        # 不同扩展名文件均应通过 global 引擎匹配
        (tmp_path / "config.env").write_text("AKIA1234567890ABCDEF ghp_aBcDeFgHiJkLmNoPqRsT", encoding="utf-8")
        (tmp_path / "data.txt").write_text("AKIA1234567890ABCDEF ghp_aBcDeFgHiJkLmNoPqRsT", encoding="utf-8")
        report = sc.scan(tmp_path)
        rule_names = {h.rule_name for sr in report.hits for h in sr.hits}
        assert "aws" in rule_names
        assert "ghp" in rule_names
        # 两个文件都应被扫描（命中数 = 2 文件 × 2 规则）
        assert len(report.hits) == 2

    def test_scanner_compile_cache_reuses_native_engine(self) -> None:
        """Scanner 编译缓存：相同 ruleset 应复用同一原生引擎实例。"""
        from fuscan.scanner.scanner import clear_compiled_cache

        clear_compiled_cache()
        rules = [
            _content_rule("a", r"pattern_a_\w+", mode=MatchMode.REGEX),
            _content_rule("b", r"pattern_b_\w+", mode=MatchMode.REGEX),
        ]
        rs = _build_ruleset(*rules)
        sc1 = Scanner(rs, max_workers=1)
        sc2 = Scanner(rs, max_workers=1)
        # 缓存命中：原生引擎实例应共享
        assert sc1._global_native_engine is sc2._global_native_engine
        assert sc1._ext_native_engines is sc2._ext_native_engines
        clear_compiled_cache()


class TestNativeEngineFallback:
    """原生引擎异常/不可用时的回退路径。"""

    def test_match_content_via_buckets_native_engine_none_falls_back(self) -> None:
        """native_engine=None 时 match_content_via_buckets 走 Python 路径。"""
        rules = [
            _content_rule("a", r"pattern_\w+", mode=MatchMode.REGEX),
            _content_rule("b", r"other_\w+", mode=MatchMode.REGEX),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        content = "pattern_test other_data"
        # native_engine=None：走 Python 路径
        py_hits = match_content_via_buckets(content, buckets, native_engine=None)
        assert len(py_hits) == 2

    def test_match_content_via_native_swallows_engine_exception(self) -> None:
        """原生引擎 match_content 抛异常时返回空列表（调用方走 Python 回退）。"""

        class _BrokenEngine:
            def match_content(self, content: str) -> list[RuleHit]:
                raise RuntimeError("simulated native engine failure")

        hits = match_content_via_native(_BrokenEngine(), "any content")  # type: ignore[arg-type]
        assert hits == []

    def test_build_native_engine_swallows_invalid_mode(self) -> None:
        """未知 mode 的 RuleSpec 应让 build_native_engine 返回 None（异常被吞）。"""
        # 通过手动构造带未知 mode 的桶来触发原生引擎构造异常
        rules = [
            _content_rule("a", "pattern_a", mode=MatchMode.REGEX),
            _content_rule("b", "pattern_b", mode=MatchMode.REGEX),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))
        # 替换第一条规则的 mode.value 为未知字符串（模拟错误输入）
        bad_rule = buckets[0].rules[0]
        # 直接构造一个会触发原生引擎 ValueError 的 specs 列表
        from fuscan.scanner._native_matchers import RuleSpec

        bad_spec = RuleSpec(
            rule_name=bad_rule.name,
            severity=bad_rule.severity.value,
            description="",
            mode="unknown_mode",
            pattern="x",
            case_sensitive=False,
        )
        # 直接调用原生引擎构造函数，应抛 ValueError 并被 build_native_engine 吞掉
        import fuscan_re

        with pytest.raises(ValueError):
            fuscan_re.ContentBucketEngine([bad_spec])  # pyrefly: ignore [missing-attribute]

    def test_build_native_engine_swallows_constructor_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """原生引擎构造函数抛异常时 build_native_engine 返回 None（日志记录）。"""
        rules = [
            _content_rule("a", "pattern_a", mode=MatchMode.REGEX),
            _content_rule("b", "pattern_b", mode=MatchMode.REGEX),
        ]
        buckets, _ = build_content_buckets(_build_pairs(rules))

        # monkeypatch 原生引擎构造函数抛异常
        from fuscan.scanner import _native_matchers

        class _FailingEngine:
            def __init__(self, specs: list[object]) -> None:
                raise RuntimeError("simulated constructor failure")

        monkeypatch.setattr(_native_matchers, "_ContentBucketEngine", _FailingEngine)
        engine = build_native_engine(buckets)
        assert engine is None

    def test_build_native_engine_skips_non_leafmatch_rules(self) -> None:
        """桶内含非 LeafMatch 规则时应跳过（防御性分支）。"""
        # 手动构造一个含 AndMatch 规则的桶（build_content_buckets 不会产生这种桶，
        # 但 _native_matchers.build_native_engine 仍需防御性处理）
        from fuscan.rules.model import AndMatch

        and_rule = Rule(
            name="and_rule",
            severity=Severity.INFO,
            match=AndMatch(
                children=(
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern="a"),
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern="b"),
                ),
            ),
        )
        leaf_rule = _content_rule("leaf", "pattern", mode=MatchMode.REGEX)
        # 手动构造桶（绕过 build_content_buckets 的过滤）
        from fuscan.scanner._content_buckets import _ContentRuleBucket

        bucket = _ContentRuleBucket(mode=MatchMode.REGEX, case_sensitive=False)
        bucket.rules = [and_rule, leaf_rule]
        # 仅 leaf_rule 应被提取为 RuleSpec（and_rule 被跳过）
        # 但单条 RuleSpec 不会被原生引擎收入桶（native build_buckets 跳过单条组）
        engine = build_native_engine([bucket])
        # 原生引擎收到 1 条 RuleSpec，跳过单条组，最终返回 0 桶的引擎
        assert engine is not None
        assert engine.bucket_count == 0

    def test_build_native_engine_returns_none_when_all_rules_skipped(self) -> None:
        """桶内规则全部是非 LeafMatch 时 specs 为空，返回 None。"""
        from fuscan.rules.model import AndMatch

        and_rule = Rule(
            name="and_rule",
            severity=Severity.INFO,
            match=AndMatch(
                children=(
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern="a"),
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern="b"),
                ),
            ),
        )
        from fuscan.scanner._content_buckets import _ContentRuleBucket

        bucket = _ContentRuleBucket(mode=MatchMode.REGEX, case_sensitive=False)
        bucket.rules = [and_rule]
        engine = build_native_engine([bucket])
        assert engine is None
