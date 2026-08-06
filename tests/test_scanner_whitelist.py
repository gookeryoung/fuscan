"""Scanner 误报白名单集成测试（iter-133）。

覆盖 ``Scanner`` 在 ``scan_entries`` 命中聚合阶段对白名单的过滤：

- 全部命中规则被白名单覆盖 → 整体过滤
- 部分命中规则被白名单覆盖 → 不过滤（保留结果）
- 白名单为 None → 不过滤
- 白名单为空 → 不过滤
- 统计同步修正（matched/matches）
"""

from __future__ import annotations

from pathlib import Path

from fuscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)
from fuscan.rules.whitelist import Whitelist, WhitelistEntry
from fuscan.scanner import Scanner
from fuscan.scanner.result import WalkResult


def _build_ruleset(*rules: Rule) -> RuleSet:
    """构造测试用规则集。"""
    return RuleSet(version="1.0", rules=tuple(rules))


def _filename_rule(name: str, pattern: str, severity: Severity = Severity.WARNING) -> Rule:
    """文件名匹配规则。"""
    return Rule(
        name=name,
        severity=severity,
        match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern=pattern),
    )


def _walk_result(root: Path) -> WalkResult:
    """构造空 WalkResult（扫描根无文件，用于直接测试 scan_entries）。"""
    return WalkResult(root=root, entries=(), total=0, skipped=0, user_skipped=0)


class TestScannerWhitelistFilter:
    def test_no_whitelist_keeps_all_hits(self, tmp_path: Path) -> None:
        """无白名单时所有命中保留。"""
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1
        assert len(report.hits) == 1

    def test_empty_whitelist_keeps_all_hits(self, tmp_path: Path) -> None:
        """空白名单（无条目）时所有命中保留。"""
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        empty_wl = Whitelist()
        scanner = Scanner(rs, whitelist=empty_wl)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1
        assert len(report.hits) == 1

    def test_wildcard_rule_whitelist_filters_hit(self, tmp_path: Path) -> None:
        """rule_name=* 的白名单覆盖任意规则，命中被过滤。"""
        path = tmp_path / "secret.txt"
        path.write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        # 白名单：此路径全部规则均为误报
        wl = Whitelist(entries=(WhitelistEntry(path_glob=str(path), rule_name="*"),))
        scanner = Scanner(rs, whitelist=wl)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 0
        assert report.hits == ()

    def test_exact_rule_whitelist_filters_hit(self, tmp_path: Path) -> None:
        """精确规则名匹配的白名单过滤命中。"""
        path = tmp_path / "secret.txt"
        path.write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        wl = Whitelist(entries=(WhitelistEntry(path_glob=str(path), rule_name="敏感名"),))
        scanner = Scanner(rs, whitelist=wl)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 0
        assert report.hits == ()

    def test_rule_name_mismatch_keeps_hit(self, tmp_path: Path) -> None:
        """白名单规则名不匹配时命中保留。"""
        path = tmp_path / "secret.txt"
        path.write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        # 白名单规则名为 other，不匹配
        wl = Whitelist(entries=(WhitelistEntry(path_glob=str(path), rule_name="other"),))
        scanner = Scanner(rs, whitelist=wl)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1
        assert len(report.hits) == 1

    def test_partial_rule_coverage_keeps_hit(self, tmp_path: Path) -> None:
        """命中两条规则但白名单仅覆盖一条 → 不过滤（保留结果）。

        ``matches_any_rule`` 要求所有规则都被白名单覆盖才整体过滤，
        部分覆盖时保留结果避免用户漏看。
        """
        # 文件名同时匹配两个规则
        path = tmp_path / "secret_password.txt"
        path.write_text("x", encoding="utf-8")
        rs = _build_ruleset(
            _filename_rule("规则A", "secret"),
            _filename_rule("规则B", "password"),
        )
        # 白名单仅覆盖 规则A
        wl = Whitelist(entries=(WhitelistEntry(path_glob=str(path), rule_name="规则A"),))
        scanner = Scanner(rs, whitelist=wl)
        report = scanner.scan(tmp_path)
        # 仍命中（规则B 未被覆盖）
        assert report.stats.matched_files == 1
        assert len(report.hits) == 1

    def test_all_rules_covered_filters_hit(self, tmp_path: Path) -> None:
        """命中两条规则且白名单覆盖全部 → 过滤。"""
        path = tmp_path / "secret_password.txt"
        path.write_text("x", encoding="utf-8")
        rs = _build_ruleset(
            _filename_rule("规则A", "secret"),
            _filename_rule("规则B", "password"),
        )
        wl = Whitelist(
            entries=(
                WhitelistEntry(path_glob=str(path), rule_name="规则A"),
                WhitelistEntry(path_glob=str(path), rule_name="规则B"),
            )
        )
        scanner = Scanner(rs, whitelist=wl)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 0
        assert report.hits == ()

    def test_glob_pattern_filters_multiple_files(self, tmp_path: Path) -> None:
        """glob 模式过滤目录下所有匹配文件。"""
        # 用 libs 而非 vendor：vendor 已进入 FileWalker 内置默认忽略目录，
        # 会被目录级跳过而非走白名单过滤，会污染本用例对白名单 glob 的验证意图。
        (tmp_path / "libs").mkdir()
        (tmp_path / "libs" / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "libs" / "b.txt").write_text("x", encoding="utf-8")
        (tmp_path / "other.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", ".txt"))
        # 白名单：libs 目录下全部 *.txt
        glob_pattern = str(tmp_path / "libs" / "*.txt")
        wl = Whitelist(entries=(WhitelistEntry(path_glob=glob_pattern, rule_name="*"),))
        scanner = Scanner(rs, whitelist=wl)
        report = scanner.scan(tmp_path)
        # libs 下 2 个被过滤，仅 other.txt 保留
        assert report.stats.matched_files == 1
        assert len(report.hits) == 1
        assert report.hits[0].path.name == "other.txt"

    def test_whitelist_does_not_affect_non_hit_files(self, tmp_path: Path) -> None:
        """白名单不影响未命中的文件统计。"""
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        (tmp_path / "clean.md").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        wl = Whitelist(entries=(WhitelistEntry(path_glob=str(tmp_path / "secret.txt"), rule_name="*"),))
        scanner = Scanner(rs, whitelist=wl)
        report = scanner.scan(tmp_path)
        # 总文件数 2，命中 0（secret 被白名单过滤），clean.md 未命中
        assert report.stats.total_files == 2
        assert report.stats.matched_files == 0
        assert report.hits == ()

    def test_scan_entries_with_whitelist_and_walk_result(self, tmp_path: Path) -> None:
        """scan_entries 直接调用，白名单在增量合并后过滤。"""
        path = tmp_path / "secret.txt"
        path.write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        wl = Whitelist(entries=(WhitelistEntry(path_glob=str(path), rule_name="*"),))
        scanner = Scanner(rs, whitelist=wl)
        # 直接调用 scan_entries（非 scan 入口）
        report = scanner.scan_entries(tmp_path, _walk_result(tmp_path))
        assert report.stats.matched_files == 0
        assert report.hits == ()
