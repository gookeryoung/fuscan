"""规则数据模型单元测试。"""

from __future__ import annotations

import pytest

from uniscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    NotMatch,
    OrMatch,
    Rule,
    RuleSet,
    Severity,
)


class TestLeafMatch:
    def test_create_leaf_match(self) -> None:
        match = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="password")
        assert match.target == MatchTarget.FILENAME
        assert match.mode == MatchMode.CONTAINS
        assert match.pattern == "password"
        assert match.case_sensitive is False

    def test_empty_pattern_raises(self) -> None:
        with pytest.raises(ValueError, match="pattern"):
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="")


class TestCompositeMatch:
    def test_and_match_default_children(self) -> None:
        match = AndMatch()
        assert match.children == ()

    def test_or_match_with_children(self) -> None:
        child = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="a.txt")
        match = OrMatch(children=(child,))
        assert len(match.children) == 1

    def test_not_match_requires_child(self) -> None:
        child = LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup")
        match = NotMatch(child=child)
        assert match.child is child


class TestRule:
    def test_create_rule(self) -> None:
        match = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="secret")
        rule = Rule(name="测试规则", match=match, severity=Severity.WARNING)
        assert rule.name == "测试规则"
        assert rule.severity == Severity.WARNING
        assert rule.description == ""
        assert rule.file_extensions == ()

    def test_empty_name_raises(self) -> None:
        match = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x")
        with pytest.raises(ValueError, match="name"):
            Rule(name="", match=match)

    def test_default_severity_is_info(self) -> None:
        match = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x")
        rule = Rule(name="r", match=match)
        assert rule.severity == Severity.INFO


class TestRuleSet:
    def test_default_ruleset(self) -> None:
        rs = RuleSet(version="1.0")
        assert rs.version == "1.0"
        assert rs.rules == ()
        assert rs.ignore_dirs == ()
        assert rs.ignore_extensions == ()

    def test_ruleset_with_data(self) -> None:
        match = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x")
        rule = Rule(name="r", match=match)
        rs = RuleSet(
            version="2.0",
            rules=(rule,),
            ignore_dirs=("node_modules",),
            ignore_extensions=("pyc",),
        )
        assert len(rs.rules) == 1
        assert rs.ignore_dirs == ("node_modules",)
        assert rs.ignore_extensions == ("pyc",)


class TestEnums:
    def test_match_target_values(self) -> None:
        assert MatchTarget.FILENAME.value == "filename"
        assert MatchTarget.CONTENT.value == "content"
        assert MatchTarget.PATH.value == "path"

    def test_match_mode_values(self) -> None:
        assert MatchMode.CONTAINS.value == "contains"
        assert MatchMode.REGEX.value == "regex"

    def test_severity_values(self) -> None:
        assert Severity.INFO.value == "info"
        assert Severity.WARNING.value == "warning"
        assert Severity.CRITICAL.value == "critical"
