"""规则集序列化单元测试（iter-122）。

覆盖 ``fuscan.rules.serializer`` 模块：

- ``serialize_match``：叶子/组合匹配条件的字典化
- ``serialize_rule``：单条规则的字典化（含 replace/replace_with）
- ``serialize_ruleset``：规则集的字典化（含 ignore_paths）
- ``save_ruleset``：YAML/JSON 文件写入与回环一致性
- 与 :func:`parser.parse_ruleset` 的互逆性验证
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fuscan.rules import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    NotMatch,
    OrMatch,
    Rule,
    RuleSet,
    Severity,
    load_ruleset,
    parse_ruleset,
    save_ruleset,
    serialize_match,
    serialize_rule,
    serialize_ruleset,
)


class TestSerializeMatch:
    def test_serialize_leaf_minimal(self) -> None:
        """叶子匹配最小字段序列化：仅 type/mode/pattern。"""
        match = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="password")
        data = serialize_match(match)
        assert data == {"type": "filename", "mode": "contains", "pattern": "password"}

    def test_serialize_leaf_with_case_sensitive(self) -> None:
        """case_sensitive=True 时写入字段；False 时省略。"""
        match = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.REGEX,
            pattern="AKIA[0-9]+",
            case_sensitive=True,
        )
        data = serialize_match(match)
        assert data["case_sensitive"] is True

    def test_serialize_leaf_with_description(self) -> None:
        """非空 description 写入字段。"""
        match = LeafMatch(
            target=MatchTarget.FILENAME,
            mode=MatchMode.CONTAINS,
            pattern="x",
            description="敏感关键词",
        )
        data = serialize_match(match)
        assert data["description"] == "敏感关键词"

    def test_serialize_leaf_empty_description_omitted(self) -> None:
        """空 description 不写入字段。"""
        match = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x")
        data = serialize_match(match)
        assert "description" not in data

    def test_serialize_and(self) -> None:
        match = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="a.txt"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="secret"),
            )
        )
        data = serialize_match(match)
        assert data["type"] == "and"
        assert len(data["children"]) == 2
        assert data["children"][0]["pattern"] == "a.txt"

    def test_serialize_or_with_description(self) -> None:
        match = OrMatch(
            children=(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="a"),),
            description="凭证关键词",
        )
        data = serialize_match(match)
        assert data["type"] == "or"
        assert data["description"] == "凭证关键词"

    def test_serialize_not(self) -> None:
        match = NotMatch(child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup"))
        data = serialize_match(match)
        assert data["type"] == "not"
        assert data["child"]["type"] == "path"

    def test_serialize_unknown_type_raises(self) -> None:
        """未知匹配类型应抛 TypeError（理论不可达，覆盖防御性分支）。"""
        with pytest.raises(TypeError, match="未知匹配类型"):
            serialize_match("not-a-match")  # type: ignore[arg-type]


class TestSerializeRule:
    def test_serialize_rule_minimal(self) -> None:
        rule = Rule(
            name="r1",
            match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x"),
        )
        data = serialize_rule(rule)
        assert data["name"] == "r1"
        assert data["severity"] == "info"
        assert data["match"]["type"] == "filename"
        assert "description" not in data
        assert "replace" not in data

    def test_serialize_rule_with_description(self) -> None:
        rule = Rule(
            name="r1",
            match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x"),
            description="测试规则",
            severity=Severity.WARNING,
        )
        data = serialize_rule(rule)
        assert data["description"] == "测试规则"
        assert data["severity"] == "warning"

    def test_serialize_rule_with_replace(self) -> None:
        rule = Rule(
            name="r1",
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern="AKIA[0-9]+"),
            replace=True,
            replace_with="***REDACTED***",
        )
        data = serialize_rule(rule)
        assert data["replace"] is True
        assert data["replace_with"] == "***REDACTED***"

    def test_serialize_rule_replace_false_omitted(self) -> None:
        """replace=False 时不写入 replace/replace_with 字段。"""
        rule = Rule(
            name="r1",
            match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x"),
        )
        data = serialize_rule(rule)
        assert "replace" not in data
        assert "replace_with" not in data


class TestSerializeRuleset:
    def test_serialize_ruleset_minimal(self) -> None:
        rs = RuleSet(version="1.0", rules=())
        data = serialize_ruleset(rs)
        assert data == {"version": "1.0", "rules": []}
        assert "ignore_paths" not in data

    def test_serialize_ruleset_with_ignore_paths(self) -> None:
        rs = RuleSet(
            version="1.0",
            rules=(),
            ignore_paths=("*/vendor/*", "*/.cache/*"),
        )
        data = serialize_ruleset(rs)
        assert data["ignore_paths"] == ["*/vendor/*", "*/.cache/*"]

    def test_serialize_ruleset_with_rules(self) -> None:
        rs = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="r1",
                    match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x"),
                ),
            ),
        )
        data = serialize_ruleset(rs)
        assert len(data["rules"]) == 1
        assert data["rules"][0]["name"] == "r1"


class TestSaveRuleset:
    def test_save_yaml_roundtrip(self, tmp_path: Path) -> None:
        """YAML 导出后可被 load_ruleset 重新加载，行为一致。"""
        rs = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="AWS 密钥",
                    description="AKIA 开头的 AWS 访问密钥",
                    severity=Severity.CRITICAL,
                    match=LeafMatch(
                        target=MatchTarget.CONTENT,
                        mode=MatchMode.REGEX,
                        pattern="AKIA[0-9A-Z]{16}",
                        description="AWS Access Key ID",
                    ),
                ),
            ),
            ignore_paths=("*/vendor/*",),
        )
        target = tmp_path / "rules.yaml"
        save_ruleset(rs, target)
        assert target.exists()

        loaded = load_ruleset(target)
        assert loaded.version == "1.0"
        assert len(loaded.rules) == 1
        assert loaded.rules[0].name == "AWS 密钥"
        assert loaded.rules[0].severity == Severity.CRITICAL
        assert loaded.ignore_paths == ("*/vendor/*",)

    def test_save_json_roundtrip(self, tmp_path: Path) -> None:
        """JSON 导出后可被 load_ruleset 重新加载。"""
        rs = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="r1",
                    match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x"),
                ),
            ),
        )
        target = tmp_path / "rules.json"
        save_ruleset(rs, target)
        assert target.exists()

        # load_ruleset 内部使用 yaml.safe_load，YAML 是 JSON 超集，可解析 JSON
        loaded = load_ruleset(target)
        assert loaded.version == "1.0"
        assert len(loaded.rules) == 1

    def test_save_yaml_explicit_format(self, tmp_path: Path) -> None:
        """显式 fmt='yaml' 强制 YAML 格式（扩展名为 .json 也写 YAML）。"""
        rs = RuleSet(version="1.0", rules=())
        target = tmp_path / "rules.json"
        save_ruleset(rs, target, fmt="yaml")
        content = target.read_text(encoding="utf-8")
        # YAML 格式包含 version: 字符串
        assert "version: '1.0'" in content or 'version: "1.0"' in content or "version: 1.0" in content

    def test_save_json_explicit_format(self, tmp_path: Path) -> None:
        """显式 fmt='json' 强制 JSON 格式。"""
        rs = RuleSet(version="1.0", rules=())
        target = tmp_path / "rules.yaml"
        save_ruleset(rs, target, fmt="json")
        content = target.read_text(encoding="utf-8")
        # JSON 格式以 { 开头
        assert content.lstrip().startswith("{")

    def test_save_yml_extension(self, tmp_path: Path) -> None:
        """.yml 扩展名也按 YAML 格式写入。"""
        rs = RuleSet(version="1.0", rules=())
        target = tmp_path / "rules.yml"
        save_ruleset(rs, target)
        # YAML 内容应能被 yaml.safe_load 解析
        with target.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        assert data["version"] == "1.0"

    def test_save_unsupported_format_raises(self, tmp_path: Path) -> None:
        """未知格式抛 ValueError。"""
        rs = RuleSet(version="1.0", rules=())
        target = tmp_path / "rules.txt"
        with pytest.raises(ValueError, match="不支持的规则集格式"):
            save_ruleset(rs, target)

    def test_save_with_composite_match_roundtrip(self, tmp_path: Path) -> None:
        """组合匹配条件（and/or/not）的导出/导入回环。"""
        rs = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="组合规则",
                    severity=Severity.WARNING,
                    match=AndMatch(
                        children=(
                            LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="config.yaml"),
                            OrMatch(
                                children=(
                                    LeafMatch(
                                        target=MatchTarget.CONTENT,
                                        mode=MatchMode.CONTAINS,
                                        pattern="password",
                                    ),
                                    NotMatch(
                                        child=LeafMatch(
                                            target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="test"
                                        )
                                    ),
                                ),
                                description="password 或非 test 目录",
                            ),
                        ),
                        description="配置文件含密码",
                    ),
                ),
            ),
        )
        target = tmp_path / "composite.yaml"
        save_ruleset(rs, target)
        loaded = load_ruleset(target)

        assert len(loaded.rules) == 1
        rule = loaded.rules[0]
        assert rule.name == "组合规则"
        assert isinstance(rule.match, AndMatch)
        assert len(rule.match.children) == 2
        # 第二个子条件是 OrMatch
        or_match = rule.match.children[1]
        assert isinstance(or_match, OrMatch)
        assert len(or_match.children) == 2
        # OrMatch 的第二个子条件是 NotMatch
        not_match = or_match.children[1]
        assert isinstance(not_match, NotMatch)
        assert isinstance(not_match.child, LeafMatch)


class TestSerializeDeserializeEquivalence:
    """序列化 → 反序列化的等价性测试。"""

    def test_complex_ruleset_equivalence(self, tmp_path: Path) -> None:
        """复杂规则集序列化/反序列化后字段值完全一致。"""
        original = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="r1",
                    description="叶子规则",
                    severity=Severity.CRITICAL,
                    match=LeafMatch(
                        target=MatchTarget.CONTENT,
                        mode=MatchMode.REGEX,
                        pattern="AKIA[0-9]+",
                        case_sensitive=True,
                        description="AWS Key",
                    ),
                ),
                Rule(
                    name="r2",
                    severity=Severity.WARNING,
                    replace=True,
                    replace_with="REDACTED",
                    match=AndMatch(
                        children=(
                            LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="conf"),
                            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="secret"),
                        ),
                        description="配置文件含 secret",
                    ),
                ),
            ),
            ignore_paths=("*/vendor/*", "*/.cache/*"),
        )

        # 序列化为字典 → 反序列化
        data = serialize_ruleset(original)
        restored = parse_ruleset(data)

        assert restored.version == original.version
        assert restored.ignore_paths == original.ignore_paths
        assert len(restored.rules) == len(original.rules)

        r1_orig, r1_restored = original.rules[0], restored.rules[0]
        assert r1_restored.name == r1_orig.name
        assert r1_restored.severity == r1_orig.severity
        assert r1_restored.description == r1_orig.description
        assert isinstance(r1_restored.match, LeafMatch)
        assert r1_restored.match.case_sensitive is True
        assert r1_restored.match.pattern == r1_orig.match.pattern  # type: ignore[union-attr]

        r2_restored = restored.rules[1]
        assert r2_restored.replace is True
        assert r2_restored.replace_with == "REDACTED"
        assert isinstance(r2_restored.match, AndMatch)
        assert len(r2_restored.match.children) == 2
