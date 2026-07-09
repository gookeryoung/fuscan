"""规则解析器单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyfilescan.rules import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    NotMatch,
    OrMatch,
    RuleLoadError,
    RuleParseError,
    Severity,
    load_ruleset,
    parse_match,
    parse_rule,
    parse_ruleset,
)


class TestParseMatch:
    def test_parse_filename_contains(self) -> None:
        match = parse_match({"type": "filename", "mode": "contains", "pattern": "password"})
        assert isinstance(match, LeafMatch)
        assert match.target == MatchTarget.FILENAME
        assert match.mode == MatchMode.CONTAINS
        assert match.pattern == "password"
        assert match.case_sensitive is False

    def test_parse_content_regex_case_sensitive(self) -> None:
        match = parse_match({"type": "content", "mode": "regex", "pattern": "AKIA[0-9]+", "case_sensitive": True})
        assert match.target == MatchTarget.CONTENT
        assert match.mode == MatchMode.REGEX
        assert match.case_sensitive is True

    def test_parse_path_endswith(self) -> None:
        match = parse_match({"type": "path", "mode": "endswith", "pattern": ".conf"})
        assert match.target == MatchTarget.PATH
        assert match.mode == MatchMode.ENDSWITH

    def test_parse_and(self) -> None:
        match = parse_match(
            {
                "type": "and",
                "children": [
                    {"type": "filename", "mode": "equals", "pattern": "a.txt"},
                    {"type": "content", "mode": "contains", "pattern": "secret"},
                ],
            }
        )
        assert isinstance(match, AndMatch)
        assert len(match.children) == 2

    def test_parse_or(self) -> None:
        match = parse_match(
            {
                "type": "or",
                "children": [
                    {"type": "content", "mode": "contains", "pattern": "a"},
                    {"type": "content", "mode": "contains", "pattern": "b"},
                ],
            }
        )
        assert isinstance(match, OrMatch)
        assert len(match.children) == 2

    def test_parse_not(self) -> None:
        match = parse_match({"type": "not", "child": {"type": "path", "mode": "contains", "pattern": "backup"}})
        assert isinstance(match, NotMatch)
        assert isinstance(match.child, LeafMatch)

    def test_missing_type_raises(self) -> None:
        with pytest.raises(RuleParseError, match="type"):
            parse_match({"mode": "contains"})

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(RuleParseError, match="未知匹配类型"):
            parse_match({"type": "fuzzy", "pattern": "x"})

    def test_non_dict_raises(self) -> None:
        with pytest.raises(RuleParseError, match="字典"):
            parse_match(["filename"])  # type: ignore[arg-type]

    def test_leaf_missing_mode_raises(self) -> None:
        with pytest.raises(RuleParseError, match="mode"):
            parse_match({"type": "filename", "pattern": "x"})

    def test_leaf_unknown_mode_raises(self) -> None:
        with pytest.raises(RuleParseError, match="未知匹配模式"):
            parse_match({"type": "filename", "mode": "fuzzy", "pattern": "x"})

    def test_leaf_missing_pattern_raises(self) -> None:
        with pytest.raises(RuleParseError, match="pattern"):
            parse_match({"type": "filename", "mode": "contains"})

    def test_and_missing_children_raises(self) -> None:
        with pytest.raises(RuleParseError, match="children"):
            parse_match({"type": "and"})

    def test_and_empty_children_raises(self) -> None:
        with pytest.raises(RuleParseError, match="不能为空"):
            parse_match({"type": "and", "children": []})

    def test_not_missing_child_raises(self) -> None:
        with pytest.raises(RuleParseError, match="child"):
            parse_match({"type": "not"})

    def test_children_wrong_type_raises(self) -> None:
        with pytest.raises(RuleParseError, match="children"):
            parse_match({"type": "and", "children": "not-a-list"})


class TestParseRule:
    def test_parse_rule_minimal(self) -> None:
        rule = parse_rule({"name": "r1", "match": {"type": "filename", "mode": "contains", "pattern": "x"}})
        assert rule.name == "r1"
        assert rule.severity == Severity.INFO

    def test_parse_rule_with_extensions(self) -> None:
        rule = parse_rule(
            {
                "name": "r1",
                "match": {"type": "filename", "mode": "contains", "pattern": "x"},
                "file_extensions": [".conf", "ini", "YAML"],
            }
        )
        assert rule.file_extensions == ("conf", "ini", "yaml")

    def test_parse_rule_unknown_severity_raises(self) -> None:
        with pytest.raises(RuleParseError, match="严重等级"):
            parse_rule(
                {
                    "name": "r1",
                    "match": {"type": "filename", "mode": "contains", "pattern": "x"},
                    "severity": "fatal",
                }
            )

    def test_parse_rule_missing_name_raises(self) -> None:
        with pytest.raises(RuleParseError, match="name"):
            parse_rule({"match": {"type": "filename", "mode": "contains", "pattern": "x"}})

    def test_parse_rule_missing_match_raises(self) -> None:
        with pytest.raises(RuleParseError, match="match"):
            parse_rule({"name": "r1"})

    def test_parse_rule_non_dict_raises(self) -> None:
        with pytest.raises(RuleParseError, match="字典"):
            parse_rule(["r1"])  # type: ignore[arg-type]

    def test_parse_rule_extensions_wrong_type_raises(self) -> None:
        with pytest.raises(RuleParseError, match="file_extensions"):
            parse_rule(
                {
                    "name": "r1",
                    "match": {"type": "filename", "mode": "contains", "pattern": "x"},
                    "file_extensions": "conf",
                }
            )


class TestParseRuleset:
    def test_parse_ruleset_minimal(self) -> None:
        rs = parse_ruleset({"version": "1.0", "rules": []})
        assert rs.version == "1.0"
        assert rs.rules == ()

    def test_parse_ruleset_with_ignores(self) -> None:
        rs = parse_ruleset(
            {
                "version": "1.0",
                "ignore_dirs": [".git", "node_modules"],
                "ignore_extensions": ["pyc", ".pyo"],
                "rules": [],
            }
        )
        assert rs.ignore_dirs == (".git", "node_modules")
        assert rs.ignore_extensions == ("pyc", "pyo")

    def test_parse_ruleset_default_version(self) -> None:
        rs = parse_ruleset({"rules": []})
        assert rs.version == "1.0"

    def test_parse_ruleset_non_dict_raises(self) -> None:
        with pytest.raises(RuleParseError, match="字典"):
            parse_ruleset(["version"])  # type: ignore[arg-type]

    def test_parse_ruleset_rules_wrong_type_raises(self) -> None:
        with pytest.raises(RuleParseError, match="rules"):
            parse_ruleset({"version": "1.0", "rules": "not-a-list"})

    def test_parse_ruleset_ignore_dirs_wrong_type_raises(self) -> None:
        with pytest.raises(RuleParseError, match="ignore_dirs"):
            parse_ruleset({"version": "1.0", "ignore_dirs": ".git"})


class TestLoadRuleset:
    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        yaml_content = """
version: "1.0"
ignore_dirs:
  - .git
rules:
  - name: 测试规则
    severity: warning
    match:
      type: filename
      mode: contains
      pattern: password
"""
        path = tmp_path / "rules.yaml"
        path.write_text(yaml_content, encoding="utf-8")

        rs = load_ruleset(path)
        assert rs.version == "1.0"
        assert len(rs.rules) == 1
        assert rs.rules[0].name == "测试规则"

    def test_load_nonexistent_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.yaml"
        with pytest.raises(RuleLoadError, match="不存在"):
            load_ruleset(path)

    def test_load_invalid_yaml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(":\n  - invalid: yaml: content\n", encoding="utf-8")
        with pytest.raises(RuleLoadError, match="YAML 解析失败"):
            load_ruleset(path)

    def test_load_empty_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        with pytest.raises(RuleParseError, match="为空"):
            load_ruleset(path)

    def test_load_example_ruleset(self) -> None:
        """加载项目自带的示例规则文件。"""
        path = Path(__file__).parent.parent / "rules" / "example.yaml"
        rs = load_ruleset(path)
        assert rs.version == "1.0"
        assert len(rs.rules) == 5
        assert ".git" in rs.ignore_dirs
        assert "pyc" in rs.ignore_extensions
