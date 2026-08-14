"""rules.rule_edit 纯函数测试：定位/替换/追加/删除/构造/序列化。

覆盖 :mod:`fuscan.rules.rule_edit` 的全部公共 API，无 Qt 依赖，验证叶子规则
构造校验、规则元组的不可变替换/追加/删除、重名冲突与未找到错误路径，以及
编辑器友好序列化对叶子/组合规则的差异化输出。
"""

from __future__ import annotations

import pytest

from fuscan.rules.errors import RuleError
from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)
from fuscan.rules.rule_edit import (
    append_rule,
    find_rule,
    is_leaf_rule,
    make_leaf_rule,
    remove_rule,
    replace_rule,
    serialize_rule_for_editor,
)


def _leaf(name: str = "r1", pattern: str = "secret") -> Rule:
    """构造叶子规则（content/contains/warning）。"""
    return Rule(
        name=name,
        match=LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern=pattern,
        ),
        severity=Severity.WARNING,
    )


def _ruleset(*rules: Rule) -> RuleSet:
    return RuleSet(version="1.0", rules=rules)


# ---------- find_rule ----------


class TestFindRule:
    def test_found(self) -> None:
        rs = _ruleset(_leaf("a"), _leaf("b"))
        found = find_rule(rs, "b")
        assert found is not None
        assert found.name == "b"

    def test_not_found_returns_none(self) -> None:
        assert find_rule(_ruleset(_leaf("a")), "missing") is None


# ---------- is_leaf_rule ----------


class TestIsLeafRule:
    def test_leaf(self) -> None:
        assert is_leaf_rule(_leaf()) is True

    def test_composite(self) -> None:
        rule = Rule(name="c", match=AndMatch(children=(_leaf().match,)), severity=Severity.INFO)
        assert is_leaf_rule(rule) is False


# ---------- make_leaf_rule ----------


class TestMakeLeafRule:
    def test_valid(self) -> None:
        r = make_leaf_rule(
            name="x",
            severity="critical",
            target="filename",
            mode="regex",
            pattern="^test",
            case_sensitive=True,
            description="d",
            replace=True,
            replace_with="R",
        )
        assert r.name == "x"
        assert r.severity is Severity.CRITICAL
        assert r.description == "d"
        assert r.replace is True
        assert r.replace_with == "R"
        m = r.match
        assert isinstance(m, LeafMatch)
        assert m.target is MatchTarget.FILENAME
        assert m.mode is MatchMode.REGEX
        assert m.case_sensitive is True

    def test_empty_name(self) -> None:
        with pytest.raises(RuleError):
            make_leaf_rule(name="  ", severity="info", target="content", mode="contains", pattern="p")

    def test_empty_pattern(self) -> None:
        with pytest.raises(RuleError):
            make_leaf_rule(name="x", severity="info", target="content", mode="contains", pattern="  ")

    def test_bad_enum(self) -> None:
        with pytest.raises(RuleError):
            make_leaf_rule(name="x", severity="info", target="bogus", mode="contains", pattern="p")

    def test_bad_regex(self) -> None:
        with pytest.raises(RuleError):
            make_leaf_rule(name="x", severity="info", target="content", mode="regex", pattern="(unclosed")

    def test_strips_whitespace(self) -> None:
        r = make_leaf_rule(name="  x  ", severity="info", target="content", mode="contains", pattern="  p  ")
        assert r.name == "x"
        m = r.match
        assert isinstance(m, LeafMatch)
        assert m.pattern == "p"


# ---------- replace_rule ----------


class TestReplaceRule:
    def test_replace_keeps_others(self) -> None:
        rs = _ruleset(_leaf("a"), _leaf("b"), _leaf("c"))
        out = replace_rule(rs, "b", _leaf("b2", pattern="updated"))
        assert [r.name for r in out.rules] == ["a", "b2", "c"]
        m = out.rules[1].match
        assert isinstance(m, LeafMatch)
        assert m.pattern == "updated"

    def test_not_found(self) -> None:
        with pytest.raises(RuleError):
            replace_rule(_ruleset(_leaf("a")), "missing", _leaf("a2"))

    def test_rename_conflict(self) -> None:
        rs = _ruleset(_leaf("a"), _leaf("b"))
        with pytest.raises(RuleError):
            replace_rule(rs, "a", _leaf("b"))

    def test_rename_to_self_ok(self) -> None:
        rs = _ruleset(_leaf("a"))
        out = replace_rule(rs, "a", _leaf("a", pattern="new"))
        m = out.rules[0].match
        assert isinstance(m, LeafMatch)
        assert m.pattern == "new"

    def test_original_set_unchanged(self) -> None:
        rs = _ruleset(_leaf("a"), _leaf("b"))
        replace_rule(rs, "a", _leaf("a2"))
        assert [r.name for r in rs.rules] == ["a", "b"]


# ---------- append_rule ----------


class TestAppendRule:
    def test_append(self) -> None:
        rs = _ruleset(_leaf("a"))
        out = append_rule(rs, _leaf("b"))
        assert [r.name for r in out.rules] == ["a", "b"]

    def test_conflict(self) -> None:
        with pytest.raises(RuleError):
            append_rule(_ruleset(_leaf("a")), _leaf("a"))


# ---------- remove_rule ----------


class TestRemoveRule:
    def test_remove(self) -> None:
        rs = _ruleset(_leaf("a"), _leaf("b"))
        out = remove_rule(rs, "a")
        assert [r.name for r in out.rules] == ["b"]

    def test_not_found(self) -> None:
        with pytest.raises(RuleError):
            remove_rule(_ruleset(_leaf("a")), "missing")


# ---------- serialize_rule_for_editor ----------


class TestSerializeForEditor:
    def test_leaf(self) -> None:
        rule = Rule(
            name="r1",
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="secret",
                case_sensitive=True,
            ),
            severity=Severity.CRITICAL,
            description="d",
            replace=True,
            replace_with="R",
        )
        d = serialize_rule_for_editor(rule)
        assert d["isLeaf"] is True
        assert d["name"] == "r1"
        assert d["severity"] == "critical"
        assert d["target"] == "content"
        assert d["mode"] == "contains"
        assert d["pattern"] == "secret"
        assert d["caseSensitive"] is True
        assert d["replace"] is True
        assert d["replaceWith"] == "R"
        assert d["description"] == "d"

    def test_composite(self) -> None:
        rule = Rule(name="c", match=AndMatch(children=(_leaf().match,)), severity=Severity.INFO)
        d = serialize_rule_for_editor(rule)
        assert d["isLeaf"] is False
        assert d["target"] == ""
        assert d["pattern"] == ""
        assert d["name"] == "c"
