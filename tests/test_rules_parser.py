"""规则解析器单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fuscan.rules import (
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
        assert match.target == MatchTarget.CONTENT  # pyrefly: ignore [missing-attribute]
        assert match.mode == MatchMode.REGEX  # pyrefly: ignore [missing-attribute]
        assert match.case_sensitive is True  # pyrefly: ignore [missing-attribute]

    def test_parse_path_endswith(self) -> None:
        match = parse_match({"type": "path", "mode": "endswith", "pattern": ".conf"})
        assert match.target == MatchTarget.PATH  # pyrefly: ignore [missing-attribute]
        assert match.mode == MatchMode.ENDSWITH  # pyrefly: ignore [missing-attribute]

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

    def test_parse_leaf_description(self) -> None:
        """叶子匹配条件应解析 description 字段。"""
        match = parse_match(
            {
                "type": "filename",
                "mode": "contains",
                "pattern": "password",
                "description": "敏感凭证关键词",
            }
        )
        assert isinstance(match, LeafMatch)
        assert match.description == "敏感凭证关键词"

    def test_parse_leaf_description_default_empty(self) -> None:
        """叶子匹配条件未指定 description 时应为空字符串。"""
        match = parse_match({"type": "filename", "mode": "contains", "pattern": "x"})
        assert isinstance(match, LeafMatch)
        assert match.description == ""

    def test_parse_and_description(self) -> None:
        """AndMatch 应解析 description 字段。"""
        match = parse_match(
            {
                "type": "and",
                "description": "配置文件含密码",
                "children": [
                    {"type": "filename", "mode": "equals", "pattern": "a.txt"},
                    {"type": "content", "mode": "contains", "pattern": "secret"},
                ],
            }
        )
        assert isinstance(match, AndMatch)
        assert match.description == "配置文件含密码"

    def test_parse_or_description(self) -> None:
        """OrMatch 应解析 description 字段。"""
        match = parse_match(
            {
                "type": "or",
                "description": "凭证关键词命中",
                "children": [
                    {"type": "content", "mode": "contains", "pattern": "a"},
                    {"type": "content", "mode": "contains", "pattern": "b"},
                ],
            }
        )
        assert isinstance(match, OrMatch)
        assert match.description == "凭证关键词命中"

    def test_parse_not_description(self) -> None:
        """NotMatch 应解析 description 字段。"""
        match = parse_match(
            {
                "type": "not",
                "description": "非备份目录文件",
                "child": {"type": "path", "mode": "contains", "pattern": "backup"},
            }
        )
        assert isinstance(match, NotMatch)
        assert match.description == "非备份目录文件"

    def test_parse_composite_description_default_empty(self) -> None:
        """组合匹配条件未指定 description 时应为空字符串。"""
        match = parse_match(
            {
                "type": "and",
                "children": [
                    {"type": "filename", "mode": "equals", "pattern": "a.txt"},
                ],
            }
        )
        assert isinstance(match, AndMatch)
        assert match.description == ""


class TestParseRule:
    def test_parse_rule_minimal(self) -> None:
        rule = parse_rule({"name": "r1", "match": {"type": "filename", "mode": "contains", "pattern": "x"}})
        assert rule.name == "r1"
        assert rule.severity == Severity.INFO

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

    def test_parse_rule_legacy_file_extensions_ignored(self) -> None:
        """旧规则文件中保留的 file_extensions 字段应被静默忽略（iter-86 起字段已移除）。"""
        rule = parse_rule(
            {
                "name": "r1",
                "match": {"type": "filename", "mode": "contains", "pattern": "x"},
                "file_extensions": [".conf", "ini", "YAML"],
            }
        )
        assert rule.name == "r1"
        assert not hasattr(rule, "file_extensions")

    def test_parse_rule_replace_defaults(self) -> None:
        """未指定 replace/replace_with 时默认值：replace=False, replace_with=''。"""
        rule = parse_rule({"name": "r1", "match": {"type": "filename", "mode": "contains", "pattern": "x"}})
        assert rule.replace is False
        assert rule.replace_with == ""

    def test_parse_rule_replace_enabled(self) -> None:
        """replace: true + replace_with 字符串应正确解析。"""
        rule = parse_rule(
            {
                "name": "r1",
                "match": {"type": "content", "mode": "regex", "pattern": "AKIA[0-9]+"},
                "replace": True,
                "replace_with": "***REDACTED***",
            }
        )
        assert rule.replace is True
        assert rule.replace_with == "***REDACTED***"

    def test_parse_rule_replace_with_empty_string(self) -> None:
        """replace: true 但 replace_with 显式为空字符串：解析为空，触发替换时由调用方提示。"""
        rule = parse_rule(
            {
                "name": "r1",
                "match": {"type": "content", "mode": "contains", "pattern": "x"},
                "replace": True,
                "replace_with": "",
            }
        )
        assert rule.replace is True
        assert rule.replace_with == ""

    def test_parse_rule_replace_non_bool_coerced(self) -> None:
        """replace 非布尔值经 bool() 强制转换（YAML 中 truthy/falsy 字面量兼容）。"""
        rule = parse_rule(
            {
                "name": "r1",
                "match": {"type": "content", "mode": "contains", "pattern": "x"},
                "replace": "true",  # YAML 解析后为字符串 "true"
                "replace_with": "Y",
            }
        )
        # 非空字符串经 bool() 为 True
        assert rule.replace is True
        assert rule.replace_with == "Y"


class TestParseRuleset:
    def test_parse_ruleset_minimal(self) -> None:
        rs = parse_ruleset({"version": "1.0", "rules": []})
        assert rs.version == "1.0"
        assert rs.rules == ()

    def test_parse_ruleset_with_deprecated_ignores(self) -> None:
        """ignore_dirs/ignore_extensions 已弃用，解析时静默忽略不存入 RuleSet。"""
        rs = parse_ruleset(
            {
                "version": "1.0",
                "ignore_dirs": [".git", "node_modules"],
                "ignore_extensions": ["pyc", ".pyo"],
                "rules": [],
            }
        )
        assert rs.ignore_paths == ()
        assert not hasattr(rs, "ignore_dirs")
        assert not hasattr(rs, "ignore_extensions")

    def test_parse_ruleset_with_ignore_paths(self) -> None:
        rs = parse_ruleset(
            {
                "version": "1.0",
                "ignore_paths": ["*/vendor/*", "*/.cache/*"],
                "rules": [],
            }
        )
        assert rs.ignore_paths == ("*/vendor/*", "*/.cache/*")

    def test_parse_ruleset_ignore_paths_default_empty(self) -> None:
        rs = parse_ruleset({"version": "1.0", "rules": []})
        assert rs.ignore_paths == ()

    def test_parse_ruleset_ignore_paths_wrong_type_raises(self) -> None:
        with pytest.raises(RuleParseError, match="ignore_paths"):
            parse_ruleset({"version": "1.0", "ignore_paths": "vendor"})

    def test_parse_ruleset_default_version(self) -> None:
        rs = parse_ruleset({"rules": []})
        assert rs.version == "1.0"

    def test_parse_ruleset_non_dict_raises(self) -> None:
        with pytest.raises(RuleParseError, match="字典"):
            parse_ruleset(["version"])  # type: ignore[arg-type]

    def test_parse_ruleset_rules_wrong_type_raises(self) -> None:
        with pytest.raises(RuleParseError, match="rules"):
            parse_ruleset({"version": "1.0", "rules": "not-a-list"})

    # ------------------ iter-122：版本兼容性检查 ------------------

    def test_parse_ruleset_unsupported_version_raises(self) -> None:
        """不支持的规则集版本应抛 RuleParseError，提示升级或降级。"""
        with pytest.raises(RuleParseError, match="不支持的规则集版本"):
            parse_ruleset({"version": "2.0", "rules": []})

    def test_parse_ruleset_version_1_1_unsupported(self) -> None:
        """1.1 版本当前不支持，应抛 RuleParseError。"""
        with pytest.raises(RuleParseError, match=r"1\.1"):
            parse_ruleset({"version": "1.1", "rules": []})

    def test_parse_ruleset_version_float_coerced_to_string(self) -> None:
        """version 字段为数值时经 str() 转换后校验（YAML 解析 1.0 为 float）。"""
        # YAML 中 version: 1.0 会被解析为 float 1.0，str(1.0) == "1.0"
        rs = parse_ruleset({"version": 1.0, "rules": []})
        assert rs.version == "1.0"

    def test_parse_ruleset_unsupported_version_error_message_lists_supported(self) -> None:
        """错误信息应列出当前支持的版本，便于用户排查。"""
        with pytest.raises(RuleParseError, match=r"1\.0"):
            parse_ruleset({"version": "3.0", "rules": []})


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
        assert len(rs.rules) == 6


# ---------------------------------------------------------------------------
# iter-162 AST 共享节点去重
# ---------------------------------------------------------------------------


class TestIter162AstDedup:
    """iter-162：parse_ruleset 内部 AST 共享节点去重。

    相同结构的 LeafMatch/AndMatch/OrMatch/NotMatch 在同一个 parse_ruleset
    执行中共享同一 Python 对象，减少内存+加速后续 build_matcher 的 is/id
    判断路径。测试验证：
    1. 两个完全相同的 LeafMatch（type/mode/pattern/case_sensitive/description）
       解析后是同一对象（id 相等）
    2. description 不同但其余字段相同的 LeafMatch 不共享（保证用户可见信息不串号）
    3. 相同子结构的 AndMatch 共享同一对象（children 为相同对象元组时）
    4. dedup ratio 可量化（100 规则 × 20% 重复模式 → 重复节点 id 计数下降）
    """

    def test_identical_leaf_match_shares_same_object(self) -> None:
        """两个相同 LeafMatch 定义应返回同一 Python 对象（parse_ruleset 内去重）。"""
        data = {
            "version": "1.0",
            "rules": [
                {
                    "name": "r1",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": r"(?i)password[=:]\S+",
                        "case_sensitive": False,
                        "description": "相同描述",
                    },
                },
                {
                    "name": "r2",
                    "severity": "info",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": r"(?i)password[=:]\S+",
                        "case_sensitive": False,
                        "description": "相同描述",
                    },
                },
            ],
        }
        rs = parse_ruleset(data)
        m1 = rs.rules[0].match
        m2 = rs.rules[1].match
        assert m1 is m2, "两个相同 LeafMatch 应共享同一对象（iter-162 dedup 失效）"
        # 同时保持语义等价（属性相等仍成立）
        assert m1 == m2

    def test_different_description_does_not_share(self) -> None:
        """description 不同（其他字段相同）的两个 LeafMatch 不得共享对象。"""
        data = {
            "version": "1.0",
            "rules": [
                {
                    "name": "r1",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "contains",
                        "pattern": "secret",
                        "case_sensitive": False,
                        "description": "规则A：检测 secret",
                    },
                },
                {
                    "name": "r2",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "contains",
                        "pattern": "secret",
                        "case_sensitive": False,
                        "description": "规则B：也检测 secret 但用于其他上下文",
                    },
                },
            ],
        }
        rs = parse_ruleset(data)
        m1 = rs.rules[0].match
        m2 = rs.rules[1].match
        # 对象不同（description 不同，避免 UI 串号）
        assert m1 is not m2

    def test_identical_and_match_children_shared(self) -> None:
        """children 为同批解析出的相同子节点时，AndMatch 也应共享对象。"""
        shared_child = {
            "type": "content",
            "mode": "contains",
            "pattern": "AKIA",
            "case_sensitive": True,
        }
        data = {
            "version": "1.0",
            "rules": [
                {
                    "name": "aws1",
                    "match": {
                        "type": "and",
                        "children": [
                            shared_child,
                            {
                                "type": "filename",
                                "mode": "endswith",
                                "pattern": ".env",
                            },
                        ],
                    },
                },
                {
                    "name": "aws2",
                    "match": {
                        "type": "and",
                        "children": [
                            # 完全相同的子树
                            {
                                "type": "content",
                                "mode": "contains",
                                "pattern": "AKIA",
                                "case_sensitive": True,
                            },
                            {
                                "type": "filename",
                                "mode": "endswith",
                                "pattern": ".env",
                            },
                        ],
                    },
                },
            ],
        }
        rs = parse_ruleset(data)
        a1 = rs.rules[0].match
        a2 = rs.rules[1].match
        # 子节点已共享（LeafMatch dedup）
        assert a1.children[0] is a2.children[0]  # type: ignore[union-attr]
        assert a1.children[1] is a2.children[1]  # type: ignore[union-attr]
        # 父 AndMatch 也共享
        assert a1 is a2, "children 全共享且描述相同的 AndMatch 也应共享对象"

    def test_dedup_reduces_unique_ast_nodes_in_duplicate_pattern_ruleset(self) -> None:
        """100 规则规则集（10 种模式各重复 10 次）：唯一 MatchSpec 对象数应 < 100（至少去重 >90%）。"""
        patterns = [f"pattern-{i}-[a-z]+-{i}" for i in range(10)]
        rules_raw: list[dict[str, object]] = []
        for i in range(100):
            p = patterns[i % 10]
            rules_raw.append(
                {
                    "name": f"r{i:03d}",
                    "severity": "info",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": p,
                        "case_sensitive": False,
                        "description": f"重复模式 {i % 10}",
                    },
                }
            )
        data = {"version": "1.0", "rules": rules_raw}
        rs = parse_ruleset(data)
        unique_ids = {id(r.match) for r in rs.rules}
        # 100 规则，10 种模式，去重后应恰好 10 个唯一对象（1/10 比例）
        assert len(unique_ids) <= 15, f"去重后唯一对象数 {len(unique_ids)} 超过阈值 15（应 ≈10）"
