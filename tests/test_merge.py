"""规则集合并单元测试。"""

from __future__ import annotations

from fuscan.rules.merge import merge_multiple_rulesets, merge_rulesets
from fuscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    ScanParams,
    Severity,
)
from fuscan.rules.whitelist import WhitelistEntry


def _make_rule(name: str, pattern: str = "x", severity: Severity = Severity.INFO) -> Rule:
    """构造简单规则用于测试。"""
    return Rule(
        name=name,
        severity=severity,
        match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern=pattern),
    )


def _make_ruleset(
    rules: tuple[Rule, ...] = (),
    ignore_paths: tuple[str, ...] = (),
    version: str = "1.0",
) -> RuleSet:
    """构造测试用规则集。"""
    return RuleSet(
        version=version,
        rules=rules,
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
        assert rule.match.pattern == "override"  # pyrefly: ignore [missing-attribute]

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

    def test_merge_ignore_paths_union(self) -> None:
        """ignore_paths 取并集。"""
        base = _make_ruleset(ignore_paths=("*/vendor/*", "*/.cache/*"))
        override = _make_ruleset(ignore_paths=("*/third_party/*", "*/vendor/*"))

        merged = merge_rulesets(base, override)
        assert set(merged.ignore_paths) == {"*/vendor/*", "*/.cache/*", "*/third_party/*"}

    def test_merge_empty_base(self) -> None:
        """base 为空时结果等于 override。"""
        base = _make_ruleset()
        override = _make_ruleset(rules=(_make_rule("r1"),), ignore_paths=("*/vendor/*",))

        merged = merge_rulesets(base, override)
        assert len(merged.rules) == 1
        assert merged.ignore_paths == ("*/vendor/*",)

    def test_merge_empty_override(self) -> None:
        """override 为空时结果等于 base。"""
        base = _make_ruleset(rules=(_make_rule("r1"),), ignore_paths=("*/vendor/*",))
        override = _make_ruleset()

        merged = merge_rulesets(base, override)
        assert len(merged.rules) == 1
        assert merged.ignore_paths == ("*/vendor/*",)

    def test_merge_both_empty(self) -> None:
        """两者都为空时结果也为空。"""
        base = _make_ruleset()
        override = _make_ruleset()

        merged = merge_rulesets(base, override)
        assert merged.rules == ()
        assert merged.ignore_paths == ()

    def test_merge_mixed_scenario(self) -> None:
        """综合场景：部分覆盖 + 部分新增 + 并集。"""
        base = _make_ruleset(
            rules=(_make_rule("base1"), _make_rule("shared"), _make_rule("base2")),
            ignore_paths=("*/vendor/*",),
        )
        override = _make_ruleset(
            rules=(_make_rule("shared", pattern="new"), _make_rule("user1")),
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
        assert shared_rule.match.pattern == "new"  # pyrefly: ignore [missing-attribute]
        # ignore_paths 并集
        assert set(merged.ignore_paths) == {"*/vendor/*", "*/.cache/*"}


class TestMergeMultipleRulesets:
    """多规则集顺序合并测试。"""

    def test_no_args_returns_empty(self) -> None:
        """无参数时返回空规则集。"""
        merged = merge_multiple_rulesets()
        assert merged.rules == ()
        assert merged.ignore_paths == ()

    def test_single_arg_returns_equal(self) -> None:
        """单个参数时返回的规则集内容应与输入一致。"""
        rs = _make_ruleset(
            rules=(_make_rule("r1"),),
            ignore_paths=("*/vendor/*",),
        )
        merged = merge_multiple_rulesets(rs)
        assert len(merged.rules) == 1
        assert merged.rules[0].name == "r1"
        assert merged.ignore_paths == ("*/vendor/*",)

    def test_two_args_last_overrides(self) -> None:
        """两个参数时后者覆盖前者同名规则。"""
        rs1 = _make_ruleset(rules=(_make_rule("shared", pattern="v1"),))
        rs2 = _make_ruleset(rules=(_make_rule("shared", pattern="v2"),))

        merged = merge_multiple_rulesets(rs1, rs2)
        assert len(merged.rules) == 1
        assert merged.rules[0].match.pattern == "v2"  # pyrefly: ignore [missing-attribute]

    def test_three_args_chained_override(self) -> None:
        """三个参数时按顺序链式覆盖：最后一个胜出。"""
        rs1 = _make_ruleset(rules=(_make_rule("shared", pattern="v1"),))
        rs2 = _make_ruleset(rules=(_make_rule("shared", pattern="v2"),))
        rs3 = _make_ruleset(rules=(_make_rule("shared", pattern="v3"),))

        merged = merge_multiple_rulesets(rs1, rs2, rs3)
        assert merged.rules[0].match.pattern == "v3"  # pyrefly: ignore [missing-attribute]

    def test_disjoint_rules_all_preserved(self) -> None:
        """不同名规则全部保留。"""
        rs1 = _make_ruleset(rules=(_make_rule("r1"),))
        rs2 = _make_ruleset(rules=(_make_rule("r2"),))
        rs3 = _make_ruleset(rules=(_make_rule("r3"),))

        merged = merge_multiple_rulesets(rs1, rs2, rs3)
        names = {r.name for r in merged.rules}
        assert names == {"r1", "r2", "r3"}

    def test_ignore_paths_union_across_all(self) -> None:
        """ignore_paths 跨所有规则集取并集。"""
        rs1 = _make_ruleset(ignore_paths=("*/a/*",))
        rs2 = _make_ruleset(ignore_paths=("*/b/*",))
        rs3 = _make_ruleset(ignore_paths=("*/a/*",))

        merged = merge_multiple_rulesets(rs1, rs2, rs3)
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
        assert rule_a.match.pattern == "a3"  # pyrefly: ignore [missing-attribute]
        assert rule_b.match.pattern == "b2"  # pyrefly: ignore [missing-attribute]
        assert rule_c.match.pattern == "c2"  # pyrefly: ignore [missing-attribute]


# ---------------------------------------------------------------------------
# scan_params / scan_extensions / whitelist 合并
# ---------------------------------------------------------------------------


class TestMergeScanParams:
    """``scan_params`` 字段级合并测试。"""

    def test_both_none_returns_none(self) -> None:
        """两者都 None 时返回 None。"""
        base = RuleSet(version="1.0")
        override = RuleSet(version="1.0")
        merged = merge_rulesets(base, override)
        assert merged.scan_params is None

    def test_override_none_keeps_base(self) -> None:
        """override 为 None 时保留 base 的 scan_params。"""
        base = RuleSet(version="1.0", scan_params=ScanParams(max_workers=4))
        override = RuleSet(version="1.0")
        merged = merge_rulesets(base, override)
        assert merged.scan_params is not None
        assert merged.scan_params.max_workers == 4

    def test_base_none_uses_override(self) -> None:
        """base 为 None 时使用 override 的 scan_params。"""
        base = RuleSet(version="1.0")
        override = RuleSet(version="1.0", scan_params=ScanParams(max_workers=8))
        merged = merge_rulesets(base, override)
        assert merged.scan_params is not None
        assert merged.scan_params.max_workers == 8

    def test_field_level_override(self) -> None:
        """override 非 None 字段覆盖 base，None 字段保留 base。"""
        base = RuleSet(
            version="1.0",
            scan_params=ScanParams(max_workers=4, max_depth=10, scan_archives=True),
        )
        override = RuleSet(
            version="1.0",
            scan_params=ScanParams(max_workers=8, scan_archives=False),
        )
        merged = merge_rulesets(base, override)
        assert merged.scan_params is not None
        # override 非 None 字段覆盖
        assert merged.scan_params.max_workers == 8
        assert merged.scan_params.scan_archives is False
        # override None 字段保留 base
        assert merged.scan_params.max_depth == 10


class TestMergeScanExtensions:
    """``scan_extensions`` 合并测试。"""

    def test_both_none_returns_none(self) -> None:
        """两者都 None 时返回 None。"""
        base = RuleSet(version="1.0")
        override = RuleSet(version="1.0")
        merged = merge_rulesets(base, override)
        assert merged.scan_extensions is None

    def test_override_none_keeps_base(self) -> None:
        """override 为 None 时保留 base 的 scan_extensions。"""
        base = RuleSet(version="1.0", scan_extensions=("py", "js"))
        override = RuleSet(version="1.0")
        merged = merge_rulesets(base, override)
        assert merged.scan_extensions == ("py", "js")

    def test_override_overrides_base(self) -> None:
        """override 非 None 时覆盖 base（含空 tuple）。"""
        base = RuleSet(version="1.0", scan_extensions=("py", "js"))
        override = RuleSet(version="1.0", scan_extensions=("txt",))
        merged = merge_rulesets(base, override)
        assert merged.scan_extensions == ("txt",)

    def test_override_empty_tuple_overrides(self) -> None:
        """override 空 tuple 覆盖 base（空表都不扫描语义）。"""
        base = RuleSet(version="1.0", scan_extensions=("py", "js"))
        override = RuleSet(version="1.0", scan_extensions=())
        merged = merge_rulesets(base, override)
        assert merged.scan_extensions == ()


class TestMergeWhitelist:
    """``whitelist`` 并集合并测试。"""

    def test_both_empty_returns_empty(self) -> None:
        """两者都为空时返回空元组。"""
        base = RuleSet(version="1.0")
        override = RuleSet(version="1.0")
        merged = merge_rulesets(base, override)
        assert merged.whitelist == ()

    def test_disjoint_entries_union(self) -> None:
        """不同条目取并集。"""
        base = RuleSet(
            version="1.0",
            whitelist=(WhitelistEntry(path_glob="/a", rule_name="r1", created_at="", note="", source="rules"),),
        )
        override = RuleSet(
            version="1.0",
            whitelist=(WhitelistEntry(path_glob="/b", rule_name="r2", created_at="", note="", source="rules"),),
        )
        merged = merge_rulesets(base, override)
        assert len(merged.whitelist) == 2
        keys = {(e.path_glob, e.rule_name) for e in merged.whitelist}
        assert keys == {("/a", "r1"), ("/b", "r2")}

    def test_same_key_override_wins(self) -> None:
        """相同 (path_glob, rule_name) 时 override 覆盖 base。"""
        base = RuleSet(
            version="1.0",
            whitelist=(WhitelistEntry(path_glob="/a", rule_name="r1", created_at="old", note="base", source="rules"),),
        )
        override = RuleSet(
            version="1.0",
            whitelist=(
                WhitelistEntry(path_glob="/a", rule_name="r1", created_at="new", note="override", source="runtime"),
            ),
        )
        merged = merge_rulesets(base, override)
        assert len(merged.whitelist) == 1
        entry = merged.whitelist[0]
        assert entry.created_at == "new"
        assert entry.note == "override"
        assert entry.source == "runtime"

    def test_base_only_kept(self) -> None:
        """override 为空时保留 base 全部条目。"""
        base = RuleSet(
            version="1.0",
            whitelist=(
                WhitelistEntry(path_glob="/a", rule_name="r1", created_at="", note="", source="rules"),
                WhitelistEntry(path_glob="/b", rule_name="r2", created_at="", note="", source="rules"),
            ),
        )
        override = RuleSet(version="1.0")
        merged = merge_rulesets(base, override)
        assert len(merged.whitelist) == 2


class TestMergeIgnoreDirs:
    """``ignore_dirs`` 并集合并测试。"""

    def test_both_empty_returns_empty(self) -> None:
        base = RuleSet(version="1.0")
        override = RuleSet(version="1.0")
        merged = merge_rulesets(base, override)
        assert merged.ignore_dirs == ()

    def test_union_dedup(self) -> None:
        """ignore_dirs 取并集去重保序。"""
        base = RuleSet(version="1.0", ignore_dirs=(".git", "node_modules"))
        override = RuleSet(version="1.0", ignore_dirs=("node_modules", ".cache"))
        merged = merge_rulesets(base, override)
        assert ".git" in merged.ignore_dirs
        assert "node_modules" in merged.ignore_dirs
        assert ".cache" in merged.ignore_dirs
        assert len(merged.ignore_dirs) == 3
