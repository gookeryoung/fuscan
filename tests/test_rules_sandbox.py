"""规则测试沙盒单元测试。

覆盖 :func:`fuscan.rules.sandbox.test_rule_against_text` 对各 ``MatchSpec``
类型（CONTENT/FILENAME/PATH/AND/OR/NOT）与各匹配模式（contains/regex/
equals/startswith/endswith）的求值，以及非法正则的容错。纯函数无 Qt 依赖，
在普通 pytest 下运行（无 ``gui`` marker）。
"""

from __future__ import annotations

from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    NotMatch,
    OrMatch,
    Rule,
    Severity,
)
from fuscan.rules.sandbox import match_rule_against_text


def _leaf(
    target: MatchTarget,
    pattern: str,
    mode: MatchMode = MatchMode.CONTAINS,
    case_sensitive: bool = False,
) -> LeafMatch:
    """构造叶子匹配条件。"""
    return LeafMatch(target=target, mode=mode, pattern=pattern, case_sensitive=case_sensitive)


def _rule(match: object, name: str = "t", severity: Severity = Severity.WARNING) -> Rule:
    """构造一条规则（match 为 MatchSpec 实例）。"""
    return Rule(name=name, match=match, severity=severity)  # type: ignore[arg-type]


class TestContentMatch:
    """CONTENT 目标 + 各模式。"""

    def test_contains_hit(self) -> None:
        r = match_rule_against_text(_rule(_leaf(MatchTarget.CONTENT, "password")), "my password is 123")
        assert r.matched
        assert r.match_count >= 1
        assert "password" in r.match_texts
        assert r.target == "content"

    def test_contains_case_insensitive_default(self) -> None:
        r = match_rule_against_text(_rule(_leaf(MatchTarget.CONTENT, "SECRET")), "top secret value")
        assert r.matched

    def test_contains_case_sensitive_miss(self) -> None:
        r = match_rule_against_text(
            _rule(_leaf(MatchTarget.CONTENT, "SECRET", case_sensitive=True)), "top secret value"
        )
        assert not r.matched

    def test_contains_count_non_overlapping(self) -> None:
        r = match_rule_against_text(_rule(_leaf(MatchTarget.CONTENT, "ab")), "ab ab ab")
        assert r.matched
        assert r.match_count == 3

    def test_contains_miss(self) -> None:
        r = match_rule_against_text(_rule(_leaf(MatchTarget.CONTENT, "nonexistent")), "some text")
        assert not r.matched

    def test_regex_hit(self) -> None:
        r = match_rule_against_text(
            _rule(_leaf(MatchTarget.CONTENT, r"\bAKIA[0-9A-Z]{16}\b", mode=MatchMode.REGEX)),
            "key=AKIAIOSFODNN7EXAMPLE end",
        )
        assert r.matched
        assert any("AKIA" in t for t in r.match_texts)

    def test_regex_multiple_matches(self) -> None:
        r = match_rule_against_text(_rule(_leaf(MatchTarget.CONTENT, r"\d+", mode=MatchMode.REGEX)), "a1 b22 c333")
        assert r.matched
        assert r.match_count == 3

    def test_equals_hit(self) -> None:
        r = match_rule_against_text(_rule(_leaf(MatchTarget.CONTENT, "exact", mode=MatchMode.EQUALS)), "exact")
        assert r.matched
        assert r.match_count == 1

    def test_equals_miss(self) -> None:
        r = match_rule_against_text(_rule(_leaf(MatchTarget.CONTENT, "exact", mode=MatchMode.EQUALS)), "exact text")
        assert not r.matched

    def test_startswith_hit(self) -> None:
        r = match_rule_against_text(
            _rule(_leaf(MatchTarget.CONTENT, "hello", mode=MatchMode.STARTSWITH)), "hello world"
        )
        assert r.matched

    def test_endswith_hit(self) -> None:
        r = match_rule_against_text(_rule(_leaf(MatchTarget.CONTENT, "world", mode=MatchMode.ENDSWITH)), "hello world")
        assert r.matched


class TestFilenamePathMatch:
    """FILENAME/PATH 目标对合成文件名/路径求值。"""

    def test_filename_contains_hit_default_name(self) -> None:
        # 默认 filename="test.txt"，FILENAME 对 entry.name 求值
        r = match_rule_against_text(_rule(_leaf(MatchTarget.FILENAME, "test")), "irrelevant content")
        assert r.matched
        assert r.target == "filename"

    def test_filename_contains_miss(self) -> None:
        r = match_rule_against_text(_rule(_leaf(MatchTarget.FILENAME, "nonexistent")), "any content")
        assert not r.matched

    def test_custom_filename_dotfile(self) -> None:
        # 传入 .env 文件名，验证 dotfile extension 提取不报错且 FILENAME 仍可匹配
        r = match_rule_against_text(_rule(_leaf(MatchTarget.FILENAME, "env")), "x", filename=".env")
        assert r.matched

    def test_path_contains_hit(self) -> None:
        # PATH 对 str(entry.path) 求值；pattern 选取与路径分隔符无关的子串
        r = match_rule_against_text(
            _rule(_leaf(MatchTarget.PATH, "secrets")), "any content", filename="config/secrets.txt"
        )
        assert r.matched
        assert r.target == "path"


class TestComboMatch:
    """AND/OR/NOT 组合规则。"""

    def test_and_all_match(self) -> None:
        rule = _rule(
            AndMatch(
                children=(
                    _leaf(MatchTarget.CONTENT, "foo"),
                    _leaf(MatchTarget.CONTENT, "bar"),
                )
            )
        )
        r = match_rule_against_text(rule, "foo and bar")
        assert r.matched

    def test_and_one_misses(self) -> None:
        rule = _rule(
            AndMatch(
                children=(
                    _leaf(MatchTarget.CONTENT, "foo"),
                    _leaf(MatchTarget.CONTENT, "baz"),
                )
            )
        )
        r = match_rule_against_text(rule, "foo and bar")
        assert not r.matched

    def test_or_any_match(self) -> None:
        rule = _rule(
            OrMatch(
                children=(
                    _leaf(MatchTarget.CONTENT, "foo"),
                    _leaf(MatchTarget.CONTENT, "baz"),
                )
            )
        )
        r = match_rule_against_text(rule, "foo and bar")
        assert r.matched

    def test_or_none_match(self) -> None:
        rule = _rule(
            OrMatch(
                children=(
                    _leaf(MatchTarget.CONTENT, "qux"),
                    _leaf(MatchTarget.CONTENT, "baz"),
                )
            )
        )
        r = match_rule_against_text(rule, "foo and bar")
        assert not r.matched

    def test_or_aggregates_match_texts(self) -> None:
        # OR 命中多个子项时 match_texts 聚合所有命中片段
        rule = _rule(
            OrMatch(
                children=(
                    _leaf(MatchTarget.CONTENT, "foo"),
                    _leaf(MatchTarget.CONTENT, "bar"),
                )
            )
        )
        r = match_rule_against_text(rule, "foo and bar")
        assert r.matched
        assert "foo" in r.match_texts
        assert "bar" in r.match_texts

    def test_not_child_misses_matches(self) -> None:
        rule = _rule(NotMatch(child=_leaf(MatchTarget.CONTENT, "secret")))
        r = match_rule_against_text(rule, "no sensitive words here")
        assert r.matched

    def test_not_child_hits_not_matched(self) -> None:
        rule = _rule(NotMatch(child=_leaf(MatchTarget.CONTENT, "secret")))
        r = match_rule_against_text(rule, "has secret word")
        assert not r.matched


class TestErrorHandling:
    """非法正则等异常输入的容错。"""

    def test_invalid_regex_returns_not_matched_with_detail(self) -> None:
        # 未闭合字符组 → re.error → compile_regex_cached 转 ValueError → 沙盒捕获
        rule = _rule(_leaf(MatchTarget.CONTENT, r"[", mode=MatchMode.REGEX))
        r = match_rule_against_text(rule, "any content")
        assert not r.matched
        assert "失败" in r.detail

    def test_invalid_regex_in_combo_child_does_not_raise(self) -> None:
        # 组合规则子项非法正则，ValueError 经 build_matcher 上抛被沙盒捕获
        rule = _rule(
            AndMatch(
                children=(
                    _leaf(MatchTarget.CONTENT, r"(", mode=MatchMode.REGEX),
                    _leaf(MatchTarget.CONTENT, "ok"),
                )
            )
        )
        r = match_rule_against_text(rule, "ok content")
        assert not r.matched
        assert "失败" in r.detail
