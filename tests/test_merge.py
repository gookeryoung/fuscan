"""规则集合并单元测试。"""

from __future__ import annotations

from uniscan.rules.merge import merge_multiple_rulesets, merge_rulesets
from uniscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)


def _make_rule(name: str, pattern: str = "x", severity: Severity = Severity.INFO) -> Rule:
    """构造简单规则用于测试。"""
    return Rule(
        name=name,
        severity=severity,
        match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern=pattern),
    )


def _make_ruleset(
    rules: tuple[Rule, ...] = (),
    ignore_dirs: tuple[str, ...] = (),
    ignore_extensions: tuple[str, ...] = (),
    ignore_paths: tuple[str, ...] = (),
    version: str = "1.0",
) -> RuleSet:
    """构造测试用规则集。"""
    return RuleSet(
        version=version,
        rules=rules,
        ignore_dirs=ignore_dirs,
        ignore_extensions=ignore_extensions,
        ignore_paths=ignore_paths,
    )


class TestMergeRulesets:
    def test_merge_disjoint_rules(self) -> None:
        """不同名规则合并后全部保留。"""
        base = _make_ruleset(rules=(_make_rule("r1"), _make_rule("r2")))
        override = _make_ruleset(rules=(_make_rule("r3"),))

        merged = merge_rulesets(base, override)
        names = {r.name for r in merged.rules}
        assert names == {"r1", "r2", "r3"}

    def test_merge_override_by_name(self) -> None:
        """override 中同名规则覆盖 base。"""
        base = _make_ruleset(rules=(_make_rule("r1", pattern="base"),))
        override = _make_ruleset(rules=(_make_rule("r1", pattern="override"),))

        merged = merge_rulesets(base, override)
        assert len(merged.rules) == 1
        rule = merged.rules[0]
        assert rule.name == "r1"
        # 验证使用的是 override 的 pattern
        assert rule.match.pattern == "override"

    def test_merge_override_severity(self) -> None:
        """override 规则的 severity 也应覆盖 base。"""
        base = _make_ruleset(rules=(_make_rule("r1", severity=Severity.INFO),))
        override = _make_ruleset(rules=(_make_rule("r1", severity=Severity.CRITICAL),))

        merged = merge_rulesets(base, override)
        assert merged.rules[0].severity == Severity.CRITICAL

    def test_merge_uses_override_version(self) -> None:
        """合并后版本号采用 override 的版本。"""
        base = _make_ruleset(version="1.0")
        override = _make_ruleset(version="2.0")

        merged = merge_rulesets(base, override)
        assert merged.version == "2.0"

    def test_merge_ignore_dirs_union(self) -> None:
        """ignore_dirs 取并集。"""
        base = _make_ruleset(ignore_dirs=(".git", "node_modules"))
        override = _make_ruleset(ignore_dirs=("node_modules", ".cache"))

        merged = merge_rulesets(base, override)
        assert set(merged.ignore_dirs) == {".git", "node_modules", ".cache"}

    def test_merge_ignore_extensions_union(self) -> None:
        """ignore_extensions 取并集。"""
        base = _make_ruleset(ignore_extensions=("pyc", "pyo"))
        override = _make_ruleset(ignore_extensions=("pyc", "log"))

        merged = merge_rulesets(base, override)
        assert set(merged.ignore_extensions) == {"pyc", "pyo", "log"}

    def test_merge_ignore_paths_union(self) -> None:
        """ignore_paths 取并集。"""
        base = _make_ruleset(ignore_paths=("*/vendor/*", "*/.cache/*"))
        override = _make_ruleset(ignore_paths=("*/third_party/*", "*/vendor/*"))

        merged = merge_rulesets(base, override)
        assert set(merged.ignore_paths) == {"*/vendor/*", "*/.cache/*", "*/third_party/*"}

    def test_merge_ignore_dirs_dedup_preserves_order(self) -> None:
        """并集去重并保持插入顺序（base 优先）。"""
        base = _make_ruleset(ignore_dirs=(".git", "node_modules"))
        override = _make_ruleset(ignore_dirs=("node_modules", ".cache"))

        merged = merge_rulesets(base, override)
        assert merged.ignore_dirs == (".git", "node_modules", ".cache")

    def test_merge_empty_base(self) -> None:
        """base 为空时结果等于 override。"""
        base = _make_ruleset()
        override = _make_ruleset(rules=(_make_rule("r1"),), ignore_dirs=(".git",))

        merged = merge_rulesets(base, override)
        assert len(merged.rules) == 1
        assert merged.ignore_dirs == (".git",)

    def test_merge_empty_override(self) -> None:
        """override 为空时结果等于 base。"""
        base = _make_ruleset(rules=(_make_rule("r1"),), ignore_dirs=(".git",))
        override = _make_ruleset()

        merged = merge_rulesets(base, override)
        assert len(merged.rules) == 1
        assert merged.ignore_dirs == (".git",)

    def test_merge_both_empty(self) -> None:
        """两者都为空时结果也为空。"""
        base = _make_ruleset()
        override = _make_ruleset()

        merged = merge_rulesets(base, override)
        assert merged.rules == ()
        assert merged.ignore_dirs == ()
        assert merged.ignore_paths == ()

    def test_merge_mixed_scenario(self) -> None:
        """综合场景：部分覆盖 + 部分新增 + 并集。"""
        base = _make_ruleset(
            rules=(_make_rule("base1"), _make_rule("shared"), _make_rule("base2")),
            ignore_dirs=(".git",),
            ignore_paths=("*/vendor/*",),
        )
        override = _make_ruleset(
            rules=(_make_rule("shared", pattern="new"), _make_rule("user1")),
            ignore_dirs=("node_modules", ".git"),
            ignore_paths=("*/.cache/*",),
        )

        merged = merge_rulesets(base, override)
        names = [r.name for r in merged.rules]
        # base1, base2 保留，shared 被 override 覆盖，user1 新增
        assert "base1" in names
        assert "base2" in names
        assert "shared" in names
        assert "user1" in names
        assert len(merged.rules) == 4
        # shared 的 pattern 应为 override 版本
        shared_rule = next(r for r in merged.rules if r.name == "shared")
        assert shared_rule.match.pattern == "new"
        # ignore_dirs 并集
        assert set(merged.ignore_dirs) == {".git", "node_modules"}
        # ignore_paths 并集
        assert set(merged.ignore_paths) == {"*/vendor/*", "*/.cache/*"}


class TestMergeMultipleRulesets:
    """多规则集顺序合并测试。"""

    def test_no_args_returns_empty(self) -> None:
        """无参数时返回空规则集。"""
        merged = merge_multiple_rulesets()
        assert merged.rules == ()
        assert merged.ignore_dirs == ()

    def test_single_arg_returns_equal(self) -> None:
        """单个参数时返回的规则集内容应与输入一致。"""
        rs = _make_ruleset(
            rules=(_make_rule("r1"),),
            ignore_dirs=(".git",),
        )
        merged = merge_multiple_rulesets(rs)
        assert len(merged.rules) == 1
        assert merged.rules[0].name == "r1"
        assert merged.ignore_dirs == (".git",)

    def test_two_args_last_overrides(self) -> None:
        """两个参数时后者覆盖前者同名规则。"""
        rs1 = _make_ruleset(rules=(_make_rule("shared", pattern="v1"),))
        rs2 = _make_ruleset(rules=(_make_rule("shared", pattern="v2"),))

        merged = merge_multiple_rulesets(rs1, rs2)
        assert len(merged.rules) == 1
        assert merged.rules[0].match.pattern == "v2"

    def test_three_args_chained_override(self) -> None:
        """三个参数时按顺序链式覆盖：最后一个胜出。"""
        rs1 = _make_ruleset(rules=(_make_rule("shared", pattern="v1"),))
        rs2 = _make_ruleset(rules=(_make_rule("shared", pattern="v2"),))
        rs3 = _make_ruleset(rules=(_make_rule("shared", pattern="v3"),))

        merged = merge_multiple_rulesets(rs1, rs2, rs3)
        assert merged.rules[0].match.pattern == "v3"

    def test_disjoint_rules_all_preserved(self) -> None:
        """不同名规则全部保留。"""
        rs1 = _make_ruleset(rules=(_make_rule("r1"),))
        rs2 = _make_ruleset(rules=(_make_rule("r2"),))
        rs3 = _make_ruleset(rules=(_make_rule("r3"),))

        merged = merge_multiple_rulesets(rs1, rs2, rs3)
        names = {r.name for r in merged.rules}
        assert names == {"r1", "r2", "r3"}

    def test_ignore_lists_union_across_all(self) -> None:
        """ignore_dirs / ignore_paths 跨所有规则集取并集。"""
        rs1 = _make_ruleset(ignore_dirs=(".git",), ignore_paths=("*/a/*",))
        rs2 = _make_ruleset(ignore_dirs=("node_modules",), ignore_paths=("*/b/*",))
        rs3 = _make_ruleset(ignore_dirs=(".cache",), ignore_paths=("*/a/*",))

        merged = merge_multiple_rulesets(rs1, rs2, rs3)
        assert set(merged.ignore_dirs) == {".git", "node_modules", ".cache"}
        assert set(merged.ignore_paths) == {"*/a/*", "*/b/*"}

    def test_version_uses_last(self) -> None:
        """版本号采用最后一个规则集的版本。"""
        rs1 = _make_ruleset(version="1.0")
        rs2 = _make_ruleset(version="2.0")
        rs3 = _make_ruleset(version="3.0")

        merged = merge_multiple_rulesets(rs1, rs2, rs3)
        assert merged.version == "3.0"

    def test_mixed_override_and_new(self) -> None:
        """综合：部分覆盖 + 部分新增。"""
        rs1 = _make_ruleset(rules=(_make_rule("a", pattern="a1"), _make_rule("b", pattern="b1")))
        rs2 = _make_ruleset(rules=(_make_rule("b", pattern="b2"), _make_rule("c", pattern="c2")))
        rs3 = _make_ruleset(rules=(_make_rule("a", pattern="a3"),))

        merged = merge_multiple_rulesets(rs1, rs2, rs3)
        names = {r.name for r in merged.rules}
        assert names == {"a", "b", "c"}
        rule_a = next(r for r in merged.rules if r.name == "a")
        rule_b = next(r for r in merged.rules if r.name == "b")
        rule_c = next(r for r in merged.rules if r.name == "c")
        assert rule_a.match.pattern == "a3"
        assert rule_b.match.pattern == "b2"
        assert rule_c.match.pattern == "c2"
