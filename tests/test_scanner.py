"""扫描器单元测试。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from fuscan.rules.model import (
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
from fuscan.scanner import Scanner, ScanReport, ScanResult
from fuscan.scanner.result import ProgressInfo, ScanStats


def _build_ruleset(*rules: Rule) -> RuleSet:
    return RuleSet(version="1.0", rules=tuple(rules))


def _filename_rule(name: str, pattern: str, severity: Severity = Severity.WARNING) -> Rule:
    return Rule(
        name=name,
        severity=severity,
        match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern=pattern),
    )


def _content_rule(name: str, pattern: str, severity: Severity = Severity.CRITICAL) -> Rule:
    return Rule(
        name=name,
        severity=severity,
        match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern=pattern),
    )


class TestScannerBasic:
    def test_scan_empty_dir(self, tmp_path: Path) -> None:
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.total_files == 0
        assert report.stats.matched_files == 0
        assert report.hits == ()

    def test_scan_single_file(self, tmp_path: Path) -> None:
        path = tmp_path / "secret.txt"
        path.write_text("content", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        scanner = Scanner(rs)
        result = scanner.scan_file(path)
        assert result.has_hit
        assert result.hits[0].rule_name == "敏感名"

    def test_scan_with_hits(self, tmp_path: Path) -> None:
        (tmp_path / "password.txt").write_text("db_password=x", encoding="utf-8")
        (tmp_path / "readme.md").write_text("normal", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "password"))
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.total_files == 2
        assert report.stats.matched_files == 1
        assert len(report.hits) == 1
        assert report.hits[0].path.name == "password.txt"

    def test_scan_respects_ignore_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "password.txt").write_text("", encoding="utf-8")
        (tmp_path / "password.txt").write_text("", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "password"))
        scanner = Scanner(rs, ignore_dirs=(".git",))
        report = scanner.scan(tmp_path)
        assert report.stats.total_files == 1  # .git 内被忽略
        assert report.stats.matched_files == 1

    def test_scan_respects_ignore_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "password.pyc").write_text("", encoding="utf-8")
        (tmp_path / "password.txt").write_text("", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "password"))
        scanner = Scanner(rs, ignore_extensions=("pyc",))
        report = scanner.scan(tmp_path)
        assert report.stats.total_files == 1  # pyc 被忽略
        assert report.stats.matched_files == 1


class TestScannerRules:
    def test_content_rule_triggers(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("contains AKIA key", encoding="utf-8")
        (tmp_path / "b.txt").write_text("nothing", encoding="utf-8")
        rs = _build_ruleset(_content_rule("ak", "AKIA"))
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1
        assert report.hits[0].path.name == "a.txt"

    def test_file_extensions_filter(self, tmp_path: Path) -> None:
        (tmp_path / "a.conf").write_text("password", encoding="utf-8")
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rule = Rule(
            name="conf-only",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            file_extensions=("conf",),
        )
        rs = _build_ruleset(rule)
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        # 总计 2 文件，但只扫描 .conf
        assert report.stats.total_files == 2
        assert report.stats.scanned_files == 1
        assert report.stats.matched_files == 1

    def test_and_composite_rule(self, tmp_path: Path) -> None:
        (tmp_path / "doc.conf").write_text("db_password=x", encoding="utf-8")
        (tmp_path / "doc.txt").write_text("db_password=x", encoding="utf-8")
        rule = Rule(
            name="conf-and-pwd",
            severity=Severity.WARNING,
            match=AndMatch(
                children=(
                    LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"\.conf$"),
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
                )
            ),
        )
        rs = _build_ruleset(rule)
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1
        assert report.hits[0].path.name == "doc.conf"

    def test_or_composite_rule(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("token here", encoding="utf-8")
        (tmp_path / "b.txt").write_text("api_key here", encoding="utf-8")
        (tmp_path / "c.txt").write_text("nothing", encoding="utf-8")
        rule = Rule(
            name="token-or-key",
            severity=Severity.INFO,
            match=OrMatch(
                children=(
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token"),
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="api_key"),
                )
            ),
        )
        rs = _build_ruleset(rule)
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 2

    def test_not_composite_rule(self, tmp_path: Path) -> None:
        (tmp_path / "password.txt").write_text("", encoding="utf-8")
        (tmp_path / "backup").mkdir()
        (tmp_path / "backup" / "password.txt").write_text("", encoding="utf-8")
        rule = Rule(
            name="not-backup",
            severity=Severity.WARNING,
            match=AndMatch(
                children=(
                    LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="password"),
                    NotMatch(child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup")),
                )
            ),
        )
        rs = _build_ruleset(rule)
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1
        assert "backup" not in str(report.hits[0].path)

    def test_multiple_rules_multiple_hits(self, tmp_path: Path) -> None:
        path = tmp_path / "password.conf"
        path.write_text("db_password=secret", encoding="utf-8")
        rs = _build_ruleset(
            _filename_rule("fn", "password"),
            _content_rule("ct", "password"),
        )
        scanner = Scanner(rs)
        result = scanner.scan_file(path)
        assert len(result.hits) == 2
        severities = {h.severity for h in result.hits}
        assert Severity.WARNING in severities
        assert Severity.CRITICAL in severities

    def test_total_matches_counts_multiple_occurrences(self, tmp_path: Path) -> None:
        """扫描含多处匹配的文件，total_matches 应为匹配文本条数总和。"""
        (tmp_path / "a.txt").write_text("password=abc\npassword=def\npassword=ghi", encoding="utf-8")
        (tmp_path / "b.txt").write_text("password=x", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        # 2 个文件命中，匹配条数 3 + 1 = 4
        assert report.stats.matched_files == 2
        assert report.stats.total_matches == 4
        # 首个文件 3 处匹配
        a_result = next(r for r in report.results if r.path.name == "a.txt")
        assert a_result.total_match_count == 3
        assert a_result.hits[0].match_count == 3

    def test_total_matches_zero_when_no_hits(self, tmp_path: Path) -> None:
        """无命中时 total_matches 应为 0。"""
        (tmp_path / "a.txt").write_text("nothing here", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 0
        assert report.stats.total_matches == 0

    def test_progress_info_includes_matches(self, tmp_path: Path) -> None:
        """ProgressInfo 应携带累计匹配条数。"""
        (tmp_path / "a.txt").write_text("password=1\npassword=2", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        captured: list[ProgressInfo] = []

        def on_progress(info: ProgressInfo) -> None:
            captured.append(info)

        scanner = Scanner(rs, on_progress=on_progress, progress_interval=0.0)
        scanner.scan(tmp_path)
        # 最终进度应反映 matches=2
        last = captured[-1]
        assert last.matches == 2


class TestScanResult:
    def test_has_hit(self) -> None:
        from fuscan.scanner.result import RuleHit

        result = ScanResult(path=Path("/x"), size=0, hits=(RuleHit("r", Severity.INFO, "d"),))
        assert result.has_hit is True

    def test_has_hit_empty(self) -> None:
        result = ScanResult(path=Path("/x"), size=0, hits=())
        assert result.has_hit is False

    def test_max_severity(self) -> None:
        from fuscan.scanner.result import RuleHit

        result = ScanResult(
            path=Path("/x"),
            size=0,
            hits=(
                RuleHit("r1", Severity.INFO, "d1"),
                RuleHit("r2", Severity.CRITICAL, "d2"),
                RuleHit("r3", Severity.WARNING, "d3"),
            ),
        )
        assert result.max_severity == Severity.CRITICAL

    def test_max_severity_empty(self) -> None:
        result = ScanResult(path=Path("/x"), size=0, hits=())
        assert result.max_severity == Severity.INFO

    def test_total_match_count_sums_hits(self) -> None:
        """total_match_count 应为所有 hits 的 match_count 之和。"""
        from fuscan.scanner.result import RuleHit

        result = ScanResult(
            path=Path("/x"),
            size=0,
            hits=(
                RuleHit("r1", Severity.INFO, "d1", match_count=3),
                RuleHit("r2", Severity.CRITICAL, "d2", match_count=5),
                RuleHit("r3", Severity.WARNING, "d3", match_count=1),
            ),
        )
        assert result.total_match_count == 9

    def test_total_match_count_empty(self) -> None:
        """无命中时 total_match_count 应为 0。"""
        result = ScanResult(path=Path("/x"), size=0, hits=())
        assert result.total_match_count == 0

    def test_total_match_count_default_is_1(self) -> None:
        """RuleHit 未指定 match_count 时默认为 1。"""
        from fuscan.scanner.result import RuleHit

        result = ScanResult(
            path=Path("/x"),
            size=0,
            hits=(RuleHit("r1", Severity.INFO, "d1"), RuleHit("r2", Severity.WARNING, "d2")),
        )
        assert result.total_match_count == 2

    def test_rule_names_dedup_preserves_order(self) -> None:
        """rule_names 应按首次出现顺序去重。"""
        from fuscan.scanner.result import RuleHit

        result = ScanResult(
            path=Path("/x"),
            size=0,
            hits=(
                RuleHit("r1", Severity.INFO, "d1"),
                RuleHit("r2", Severity.CRITICAL, "d2"),
                RuleHit("r1", Severity.WARNING, "d3"),
            ),
        )
        assert result.rule_names == ("r1", "r2")

    def test_rule_names_empty(self) -> None:
        """无命中时 rule_names 为空元组。"""
        result = ScanResult(path=Path("/x"), size=0, hits=())
        assert result.rule_names == ()

    def test_summary_format(self) -> None:
        """summary 应返回 ``N 条规则 / M 处匹配``。"""
        from fuscan.scanner.result import RuleHit

        result = ScanResult(
            path=Path("/x"),
            size=0,
            hits=(
                RuleHit("r1", Severity.INFO, "d1", match_count=3),
                RuleHit("r2", Severity.CRITICAL, "d2", match_count=2),
            ),
        )
        assert result.summary() == "2 条规则 / 5 处匹配"

    def test_summary_empty(self) -> None:
        """无命中时 summary 仍应返回 0 计数。"""
        result = ScanResult(path=Path("/x"), size=0, hits=())
        assert result.summary() == "0 条规则 / 0 处匹配"


class TestScanStats:
    def test_summary_default_complete(self) -> None:
        """summary 默认前缀为"完成"。"""

        stats = ScanStats(
            total_files=10,
            scanned_files=8,
            matched_files=3,
            skipped_files=2,
            errors=1,
            duration_seconds=1.5,
            total_matches=5,
        )
        s = stats.summary()
        assert s.startswith("完成:")
        assert "总计 10" in s
        assert "扫描 8" in s
        assert "跳过 2" in s
        assert "命中 3" in s
        assert "条数 5" in s
        assert "错误 1" in s
        assert "耗时 1.50s" in s

    def test_summary_cancelled_prefix(self) -> None:
        """cancelled=True 时前缀为"已取消"。"""

        stats = ScanStats(total_files=1, scanned_files=1, duration_seconds=0.0)
        assert stats.summary(cancelled=True).startswith("已取消:")
        assert stats.summary(cancelled=False).startswith("完成:")


class TestScanReport:
    def test_hits_filters_matched(self, tmp_path: Path) -> None:
        from fuscan.scanner.result import RuleHit

        results = (
            ScanResult(path=tmp_path / "a", size=0, hits=(RuleHit("r", Severity.INFO, "d"),)),
            ScanResult(path=tmp_path / "b", size=0, hits=()),
        )
        report = ScanReport(root=tmp_path, results=results, stats=ScanStats())
        assert len(report.hits) == 1
        assert report.hits[0].path == tmp_path / "a"

    def _build_report(self, tmp_path: Path) -> ScanReport:
        """构造测试报告：3 个文件命中 2 条规则，分属 WARNING/CRITICAL 两个等级。"""
        from fuscan.scanner.result import RuleHit

        (tmp_path / "secret.txt").mkdir(parents=True, exist_ok=True)
        results = (
            ScanResult(
                path=tmp_path / "secret.txt" / "a.txt",
                size=10,
                hits=(
                    RuleHit("敏感文件名", Severity.WARNING, "d1", match_count=1),
                    RuleHit("密钥内容", Severity.CRITICAL, "d2", match_count=2),
                ),
            ),
            ScanResult(
                path=tmp_path / "secret.txt" / "b.txt",
                size=20,
                hits=(RuleHit("密钥内容", Severity.CRITICAL, "d3", match_count=3),),
            ),
            ScanResult(path=tmp_path / "clean.txt", size=0, hits=()),
        )
        stats = ScanStats(
            total_files=3,
            scanned_files=3,
            matched_files=2,
            skipped_files=0,
            errors=0,
            duration_seconds=0.5,
            total_matches=6,
        )
        return ScanReport(root=tmp_path, results=results, stats=stats)

    def test_rule_names_dedup(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        # 两个文件均命中"密钥内容"，应去重
        assert report.rule_names == ("敏感文件名", "密钥内容")

    def test_rule_names_empty(self, tmp_path: Path) -> None:
        report = ScanReport(root=tmp_path, results=(), stats=ScanStats())
        assert report.rule_names == ()

    def test_summary_uses_stats_and_cancelled_flag(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        s = report.summary()
        assert s.startswith("完成:")
        assert "命中 2" in s

        cancelled_report = ScanReport(
            root=report.root,
            results=report.results,
            stats=report.stats,
            cancelled=True,
        )
        assert cancelled_report.summary().startswith("已取消:")

    def test_filter_no_args_returns_self(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        assert report.filter() is report

    def test_filter_by_path_query(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        filtered = report.filter(path_query="a.txt")
        assert len(filtered.hits) == 1
        assert filtered.hits[0].path.name == "a.txt"
        # stats 不变
        assert filtered.stats is report.stats

    def test_filter_path_case_insensitive(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        assert len(report.filter(path_query="A.TXT").hits) == 1

    def test_filter_by_rule_name_keeps_only_matching_hits(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        filtered = report.filter(rule_name="密钥内容")
        # 两个文件均命中"密钥内容"
        assert len(filtered.hits) == 2
        # a.txt 原本有 2 条规则命中，过滤后仅保留"密钥内容"
        a = next(r for r in filtered.hits if r.path.name == "a.txt")
        assert len(a.hits) == 1
        assert a.hits[0].rule_name == "密钥内容"
        assert a.total_match_count == 2

    def test_filter_combined_path_and_rule(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        filtered = report.filter(path_query="b.txt", rule_name="密钥内容")
        assert len(filtered.hits) == 1
        assert filtered.hits[0].path.name == "b.txt"

    def test_filter_no_match_returns_empty_hits(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        assert report.filter(path_query="nonexistent").hits == ()

    def test_filter_does_not_mutate_original(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        original_hits_count = len(report.hits)
        report.filter(rule_name="密钥内容")
        # 原报告 hits 不应被修改
        assert len(report.hits) == original_hits_count
        a = next(r for r in report.hits if r.path.name == "a.txt")
        assert len(a.hits) == 2

    def test_group_by_rule(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        groups = report.group_by_rule()
        assert set(groups.keys()) == {"敏感文件名", "密钥内容"}
        # "密钥内容"在 a.txt 和 b.txt 各命中一次，共 2 项
        assert len(groups["密钥内容"]) == 2
        # "敏感文件名"只在 a.txt 命中
        assert len(groups["敏感文件名"]) == 1
        sr, hit = groups["敏感文件名"][0]
        assert sr.path.name == "a.txt"
        assert hit.rule_name == "敏感文件名"

    def test_group_by_severity(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        groups = report.group_by_severity()
        # 两个命中文件 max_severity 都是 CRITICAL（a.txt 含 CRITICAL，b.txt 仅 CRITICAL）
        assert set(groups.keys()) == {Severity.CRITICAL}
        assert len(groups[Severity.CRITICAL]) == 2

    def test_group_by_severity_distinguishes_levels(self, tmp_path: Path) -> None:
        from fuscan.scanner.result import RuleHit

        results = (
            ScanResult(path=tmp_path / "warn.txt", size=0, hits=(RuleHit("r", Severity.WARNING, "d"),)),
            ScanResult(path=tmp_path / "crit.txt", size=0, hits=(RuleHit("r", Severity.CRITICAL, "d"),)),
        )
        report = ScanReport(root=tmp_path, results=results, stats=ScanStats())
        groups = report.group_by_severity()
        assert set(groups.keys()) == {Severity.WARNING, Severity.CRITICAL}

    def test_to_json_contains_expected_fields(self, tmp_path: Path) -> None:
        import json as _json

        report = self._build_report(tmp_path)
        data = _json.loads(report.to_json())
        assert data["root"] == str(tmp_path)
        assert data["stats"]["matched_files"] == 2
        assert data["cancelled"] is False
        assert len(data["hits"]) == 2
        first = data["hits"][0]
        assert first["max_severity"] == "critical"
        assert first["match_count"] == 3  # 1 + 2
        assert len(first["rules"]) == 2

    def test_to_json_cancelled_flag(self, tmp_path: Path) -> None:
        import json as _json

        report = ScanReport(
            root=tmp_path,
            results=self._build_report(tmp_path).results,
            stats=ScanStats(),
            cancelled=True,
        )
        assert _json.loads(report.to_json())["cancelled"] is True

    def test_to_csv_header_and_rows(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        csv_text = report.to_csv()
        lines = csv_text.strip().splitlines()
        assert lines[0] == "path,size,severity,rule,match_count,detail"
        # 3 条命中：a.txt 2 条 + b.txt 1 条
        assert len(lines) - 1 == 3
        # 第一条数据应包含 a.txt 路径
        assert "a.txt" in lines[1]

    def test_to_csv_empty_hits_only_header(self, tmp_path: Path) -> None:

        report = ScanReport(root=tmp_path, results=(), stats=ScanStats())
        csv_text = report.to_csv()
        assert csv_text.strip() == "path,size,severity,rule,match_count,detail"

    def test_to_text_contains_root_and_hits(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        text = report.to_text()
        assert str(tmp_path) in text
        assert "命中项 (2)" in text
        assert "敏感文件名" in text
        assert "密钥内容" in text

    def test_to_text_no_hits(self, tmp_path: Path) -> None:

        report = ScanReport(root=tmp_path, results=(), stats=ScanStats())
        text = report.to_text()
        assert "未发现命中项" in text

    def test_to_text_relative_path(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        text = report.to_text()
        # 命中项路径应以 root 为相对基准显示
        assert "secret.txt" in text
        assert str(tmp_path) not in text.split("命中项")[1]

    def test_notification_message_with_hits(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        msg = report.notification_message()
        # 2 个命中文件，total_matches=6
        assert "2 个文件" in msg
        assert "7 处匹配" not in msg  # 防止误读
        assert "6 处匹配" in msg

    def test_notification_message_no_hits(self, tmp_path: Path) -> None:
        report = ScanReport(root=tmp_path, results=(), stats=ScanStats())
        assert report.notification_message() == "未发现命中"

    def test_to_format_json(self, tmp_path: Path) -> None:
        import json as _json

        report = self._build_report(tmp_path)
        # to_format("json") 应等价于 to_json
        assert _json.loads(report.to_format("json")) == _json.loads(report.to_json())

    def test_to_format_csv(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        assert report.to_format("csv") == report.to_csv()

    def test_to_format_text(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        assert report.to_format("text") == report.to_text()

    def test_to_format_unknown_falls_back_to_text(self, tmp_path: Path) -> None:
        report = self._build_report(tmp_path)
        # 未知格式应回退到 text
        assert report.to_format("unknown") == report.to_text()


class TestFormatSize:
    def test_bytes(self) -> None:
        from fuscan.scanner.result import format_size

        assert format_size(0) == "0 B"
        assert format_size(1023) == "1023 B"

    def test_kb(self) -> None:
        from fuscan.scanner.result import format_size

        assert format_size(1024) == "1.0 KB"
        assert format_size(2048) == "2.0 KB"

    def test_mb(self) -> None:
        from fuscan.scanner.result import format_size

        assert format_size(1024 * 1024) == "1.0 MB"

    def test_gb(self) -> None:
        from fuscan.scanner.result import format_size

        assert format_size(1024 * 1024 * 1024) == "1.00 GB"


class TestProgressInfoSummary:
    def test_summary_with_speed(self) -> None:
        from fuscan.scanner.result import ProgressInfo

        info = ProgressInfo(scanned=100, elapsed=10.0, skipped=2, matched=5, errors=1, matches=8)
        s = info.summary()
        assert "已扫描 100" in s
        assert "跳过 2" in s
        assert "命中 5" in s
        assert "条数 8" in s
        assert "错误 1" in s
        assert "已用 10.0s" in s
        assert "速度 10 文件/s" in s  # 100/10.0

    def test_summary_zero_elapsed_speed_zero(self) -> None:
        from fuscan.scanner.result import ProgressInfo

        info = ProgressInfo(scanned=5, elapsed=0.0)
        s = info.summary()
        assert "速度 0 文件/s" in s


class TestScanResultFileInfoHtml:
    def test_html_contains_path_size_hits(self, tmp_path: Path) -> None:
        from fuscan.scanner.result import RuleHit

        path = tmp_path / "f.txt"
        path.write_text("hello", encoding="utf-8")
        result = ScanResult(
            path=path,
            size=5,
            hits=(RuleHit("r1", Severity.WARNING, "d1", match_count=2),),
        )
        html_text = result.file_info_html()
        assert "文件路径:" in html_text
        assert "f.txt" in html_text
        assert "5 B" in html_text
        assert "5 字节" in html_text
        assert "命中规则数:" in html_text
        assert "匹配条数:" in html_text

    def test_html_includes_extra(self, tmp_path: Path) -> None:
        result = ScanResult(path=tmp_path / "x", size=0, hits=())
        html_text = result.file_info_html(extra="<b>可切换位置:</b> 3")
        assert "可切换位置:" in html_text
        assert "3" in html_text

    def test_html_without_extra(self, tmp_path: Path) -> None:
        result = ScanResult(path=tmp_path / "x", size=0, hits=())
        # 无 extra 时不追加尾部分隔符
        assert not result.file_info_html().endswith("|")

    def test_html_mtime_unavailable_on_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from fuscan.scanner.result import RuleHit

        path = tmp_path / "f.txt"
        path.write_text("", encoding="utf-8")
        result = ScanResult(path=path, size=0, hits=(RuleHit("r", Severity.INFO, "d"),))

        def raise_oserror(self: Path, *args: object, **kwargs: object) -> object:
            raise OSError("mock")

        monkeypatch.setattr(Path, "stat", raise_oserror)
        html_text = result.file_info_html()
        assert "无法获取" in html_text

    def test_html_escapes_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # 路径含 HTML 特殊字符时应被转义（Windows 不允许文件名含 <，用 mock 路径绕过）
        result = ScanResult(path=Path("<weird&name>.txt"), size=0, hits=())

        # mock stat 避免 OSError 干扰
        class _FakeStat:
            st_mtime = 0.0

        monkeypatch.setattr(Path, "stat", lambda self, *a, **kw: _FakeStat())
        html_text = result.file_info_html()
        assert "<weird" not in html_text  # 原文不应直接出现
        assert "&lt;weird" in html_text


class TestScannerErrorHandling:
    def test_scan_continues_on_content_error(self, tmp_path: Path) -> None:
        """当内容提供器抛异常时，扫描器应记录错误并继续。"""
        from fuscan.scanner.context import FileEntry

        (tmp_path / "good.txt").write_text("password", encoding="utf-8")
        (tmp_path / "bad.txt").write_text("password", encoding="utf-8")

        def faulty_provider(entry: FileEntry) -> str:
            if entry.path.name == "bad.txt":
                raise RuntimeError("read error")
            return "password"

        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = Scanner(rs, content_provider=faulty_provider)
        report = scanner.scan(tmp_path)
        # bad.txt 的内容读取抛错被 _scan_entry 捕获，记录为 error
        assert report.stats.errors >= 1
        assert report.stats.matched_files == 1  # good.txt 命中


class TestScannerConcurrency:
    """多线程扫描测试：验证并发结果与单线程一致。"""

    def test_concurrent_matches_sequential(self, tmp_path: Path) -> None:
        """多线程扫描结果应与单线程一致（按路径排序后比较）。"""
        for i in range(20):
            (tmp_path / f"secret_{i}.txt").write_text(f"password_{i}", encoding="utf-8")
        (tmp_path / "normal.md").write_text("nothing", encoding="utf-8")

        rs = _build_ruleset(
            _filename_rule("fn", "secret"),
            _content_rule("ct", "password"),
        )

        # 单线程
        seq_scanner = Scanner(rs)
        seq_report = seq_scanner.scan(tmp_path)

        # 多线程
        con_scanner = Scanner(rs, max_workers=4)
        con_report = con_scanner.scan(tmp_path)

        # 统计一致
        assert con_report.stats.total_files == seq_report.stats.total_files
        assert con_report.stats.scanned_files == seq_report.stats.scanned_files
        assert con_report.stats.matched_files == seq_report.stats.matched_files
        assert con_report.stats.skipped_files == seq_report.stats.skipped_files
        assert con_report.stats.errors == seq_report.stats.errors

        # 命中文件集合一致（顺序可能不同，按路径排序比较）
        seq_paths = sorted(str(r.path) for r in seq_report.hits)
        con_paths = sorted(str(r.path) for r in con_report.hits)
        assert seq_paths == con_paths

    def test_max_workers_none_is_sequential(self, tmp_path: Path) -> None:
        """max_workers=None 应退化为单线程。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = Scanner(rs, max_workers=None)
        assert scanner._max_workers is None
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1

    def test_max_workers_one_is_sequential(self, tmp_path: Path) -> None:
        """max_workers=1 应走单线程路径。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = Scanner(rs, max_workers=1)
        assert scanner._max_workers == 1
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1

    def test_concurrent_error_handling(self, tmp_path: Path) -> None:
        """多线程模式下错误处理应正常工作。"""
        from fuscan.scanner.context import FileEntry

        for i in range(10):
            (tmp_path / f"file_{i}.txt").write_text("password", encoding="utf-8")

        def faulty_provider(entry: FileEntry) -> str:
            if "file_0" in entry.path.name or "file_5" in entry.path.name:
                raise RuntimeError("read error")
            return "password"

        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = Scanner(rs, content_provider=faulty_provider, max_workers=4)
        report = scanner.scan(tmp_path)
        assert report.stats.errors >= 2
        assert report.stats.matched_files == 8

    def test_concurrent_with_file_extensions_filter(self, tmp_path: Path) -> None:
        """多线程模式下 file_extensions 过滤应正常工作。"""
        rule = Rule(
            name="conf-only",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            file_extensions=("conf",),
        )
        rs = _build_ruleset(rule)
        for i in range(10):
            (tmp_path / f"a_{i}.conf").write_text("password", encoding="utf-8")
            (tmp_path / f"b_{i}.txt").write_text("password", encoding="utf-8")

        scanner = Scanner(rs, max_workers=4)
        report = scanner.scan(tmp_path)
        assert report.stats.total_files == 20
        assert report.stats.scanned_files == 10  # 只扫描 .conf
        assert report.stats.matched_files == 10

    def test_concurrent_empty_dir(self, tmp_path: Path) -> None:
        """多线程模式扫描空目录应正常。"""
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = Scanner(rs, max_workers=4)
        report = scanner.scan(tmp_path)
        assert report.stats.total_files == 0
        assert report.stats.matched_files == 0

    def test_pipelined_large_fileset_triggers_drain(self, tmp_path: Path) -> None:
        """流水线扫描文件数超过 drain 阈值（500）时应正确收集全部结果。

        验证 _drain_futures 在 walk 过程中非阻塞收集已完成 future 后，
        最终统计与单线程一致。
        """
        for i in range(600):
            (tmp_path / f"secret_{i}.txt").write_text(f"password_{i}", encoding="utf-8")

        rs = _build_ruleset(
            _filename_rule("fn", "secret"),
            _content_rule("ct", "password"),
        )

        seq_scanner = Scanner(rs)
        seq_report = seq_scanner.scan(tmp_path)

        con_scanner = Scanner(rs, max_workers=4)
        con_report = con_scanner.scan(tmp_path)

        assert con_report.stats.total_files == 600
        assert con_report.stats.scanned_files == seq_report.stats.scanned_files == 600
        assert con_report.stats.matched_files == seq_report.stats.matched_files == 600
        # 命中文件集合一致（顺序可能不同，按路径排序比较）
        seq_paths = sorted(str(r.path) for r in seq_report.hits)
        con_paths = sorted(str(r.path) for r in con_report.hits)
        assert seq_paths == con_paths

    def test_pipelined_drain_error_handling(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """流水线 drain 阶段 _scan_entry 抛异常应计 error 并继续。

        600 文件触发 drain 阈值，首个 future 抛异常后由 drain 非阻塞收集，
        ``future.result()`` 重抛被 ``except Exception`` 捕获。
        """
        for i in range(600):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "f"))
        scanner = Scanner(rs, max_workers=4)

        original_scan_entry = scanner._scan_entry
        call_count = {"n": 0}

        def fake_scan_entry(entry):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("模拟 drain 阶段失败")
            return original_scan_entry(entry)

        monkeypatch.setattr(scanner, "_scan_entry", fake_scan_entry)
        report = scanner.scan(tmp_path)
        assert report.stats.errors >= 1
        assert report.stats.scanned_files >= 1


class TestScannerProgress:
    """扫描进度回调测试。"""

    def test_progress_callback_called(self, tmp_path: Path) -> None:
        """on_progress 回调应在扫描过程中被调用。"""
        for i in range(10):
            (tmp_path / f"file_{i}.txt").write_text("password", encoding="utf-8")

        rs = _build_ruleset(_content_rule("r", "password"))
        received: list[ProgressInfo] = []
        scanner = Scanner(rs, on_progress=received.append)
        scanner.scan(tmp_path)

        assert len(received) >= 1
        # 最终进度应反映全部文件
        last = received[-1]
        assert last.total >= 10
        assert last.scanned >= 10
        assert last.elapsed > 0

    def test_progress_callback_concurrent(self, tmp_path: Path) -> None:
        """多线程模式下 on_progress 也应正常工作。"""
        for i in range(20):
            (tmp_path / f"file_{i}.txt").write_text("password", encoding="utf-8")

        rs = _build_ruleset(_content_rule("r", "password"))
        received: list[ProgressInfo] = []
        scanner = Scanner(rs, max_workers=4, on_progress=received.append)
        scanner.scan(tmp_path)

        assert len(received) >= 1
        last = received[-1]
        assert last.scanned >= 20
        assert last.matched >= 20

    def test_progress_callback_throttle(self, tmp_path: Path) -> None:
        """progress_interval 应限制回调频率。"""
        for i in range(100):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset(_filename_rule("r", "f"))
        received: list[ProgressInfo] = []
        # 设置较长间隔（1秒），扫描应很快完成，只有 force=True 的最终进度
        scanner = Scanner(rs, on_progress=received.append, progress_interval=1.0)
        scanner.scan(tmp_path)

        # 由于 1 秒间隔，中间进度被节流，最终 force=True 的进度一定到达
        assert len(received) >= 1
        assert received[-1].scanned >= 100

    def test_progress_callback_none_is_safe(self, tmp_path: Path) -> None:
        """on_progress=None 时扫描应正常完成。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1

    def test_matched_files_not_collected_without_callback(self, tmp_path: Path) -> None:
        """无 on_progress 回调时不应收集 matched_files 列表（优化 3：进度上报减负）。

        通过访问私有属性验证：当未注册 on_progress 时，命中文件的 (path, rule) 对
        不应被追加到 self._matched_files，避免大扫描量时的无谓列表增长与截断开销。
        """
        for i in range(5):
            (tmp_path / f"secret_{i}.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(
            _filename_rule("fn", "secret"),
            _content_rule("ct", "password"),
        )
        scanner = Scanner(rs)  # 无 on_progress
        report = scanner.scan(tmp_path)
        # 统计仍正确（命中数不受影响）
        assert report.stats.matched_files == 5
        # 但内部收集列表应为空（无回调时跳过收集）
        assert scanner._matched_files == []

    def test_progress_callback_final_force(self, tmp_path: Path) -> None:
        """最终进度应被强制发送（跳过节流）。"""
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset(_filename_rule("r", "f"))
        received: list[ProgressInfo] = []
        # 10 秒间隔，中间不会触发，但最终 force=True 必须触发
        scanner = Scanner(rs, on_progress=received.append, progress_interval=10.0)
        scanner.scan(tmp_path)

        assert len(received) >= 1
        last = received[-1]
        assert last.scanned >= 5
        assert last.total >= 5

    def test_progress_info_fields(self, tmp_path: Path) -> None:
        """ProgressInfo 字段应正确填充。"""
        (tmp_path / "secret.txt").write_text("password", encoding="utf-8")
        (tmp_path / "normal.md").write_text("hello", encoding="utf-8")

        rs = _build_ruleset(_content_rule("r", "password"))
        received: list[ProgressInfo] = []
        scanner = Scanner(rs, on_progress=received.append, progress_interval=0.0)
        scanner.scan(tmp_path)

        assert len(received) >= 1
        last = received[-1]
        assert last.total >= 2
        assert last.scanned >= 2
        assert last.matched >= 1  # secret.txt 命中
        assert last.errors == 0
        assert last.elapsed >= 0
        assert isinstance(last.current_file, str)
        # 新增字段：命中文件列表应包含 (路径, 规则名) 元组
        assert isinstance(last.matched_files, tuple)
        assert any(path.endswith("secret.txt") and rule == "r" for path, rule in last.matched_files)
        # 新增字段：跳过的目录列表（无忽略目录时为空 tuple）
        assert isinstance(last.skipped_dirs, tuple)

    def test_progress_info_skipped_dirs_collected(self, tmp_path: Path) -> None:
        """ignore_dirs 跳过的目录应出现在 ProgressInfo.skipped_dirs 中。"""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("", encoding="utf-8")
        (tmp_path / "app.py").write_text("password", encoding="utf-8")

        rs = _build_ruleset(_content_rule("r", "password"))
        received: list[ProgressInfo] = []
        scanner = Scanner(rs, on_progress=received.append, progress_interval=0.0, ignore_dirs=(".git",))
        scanner.scan(tmp_path)

        assert len(received) >= 1
        last = received[-1]
        # .git 目录应被收集到 skipped_dirs
        assert any(".git" in d for d in last.skipped_dirs)
        # app.py 命中应收集到 matched_files
        assert any(path.endswith("app.py") for path, _ in last.matched_files)

    def test_pipelined_drain_collects_matched_files_with_callback(self, tmp_path: Path) -> None:
        """流水线 drain 阶段有 on_progress 时应收集 matched_files（覆盖 drain guard True 分支）。

        600 文件触发 drain 阈值，drain 收集命中 future 时执行
        ``if self._on_progress is not None:`` True 分支，追加 matched_files。
        """
        for i in range(600):
            (tmp_path / f"secret_{i}.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(
            _filename_rule("fn", "secret"),
            _content_rule("ct", "password"),
        )
        received: list[ProgressInfo] = []
        scanner = Scanner(rs, max_workers=4, on_progress=received.append, progress_interval=0.0)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 600
        # drain 阶段应收集到 matched_files（含路径与规则名）
        assert len(scanner._matched_files) > 0
        assert any(rule == "fn" for _, rule in scanner._matched_files)

    def test_archive_phase_collects_matched_files_with_callback(self, tmp_path: Path) -> None:
        """压缩包扫描阶段有 on_progress 时应收集 matched_files（覆盖 archive guard True 分支）。

        scan_archives=True + on_progress 回调，zip 内条目命中时执行
        ``if self._on_progress is not None:`` True 分支。
        """
        import zipfile

        zip_path = tmp_path / "archive.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("secret.txt", "password")

        rs = _build_ruleset(
            _filename_rule("fn", "secret"),
            _content_rule("ct", "password"),
        )
        received: list[ProgressInfo] = []
        scanner = Scanner(rs, scan_archives=True, on_progress=received.append, progress_interval=0.0)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files >= 1
        # archive 阶段应收集到 matched_files
        assert len(scanner._matched_files) > 0


class TestScannerControl:
    """扫描器暂停/取消控制测试。"""

    def test_initial_state_not_paused_not_cancelled(self, tmp_path: Path) -> None:
        """新构造的 Scanner 应处于运行（非暂停、非取消）状态。"""
        scanner = Scanner(_build_ruleset(_filename_rule("r", "x")))
        assert not scanner.is_paused
        assert not scanner.is_cancelled

    def test_pause_sets_is_paused(self, tmp_path: Path) -> None:
        """pause() 后 is_paused 应为 True。"""
        scanner = Scanner(_build_ruleset(_filename_rule("r", "x")))
        scanner.pause()
        assert scanner.is_paused
        assert not scanner.is_cancelled

    def test_resume_clears_is_paused(self, tmp_path: Path) -> None:
        """resume() 后 is_paused 应为 False。"""
        scanner = Scanner(_build_ruleset(_filename_rule("r", "x")))
        scanner.pause()
        scanner.resume()
        assert not scanner.is_paused

    def test_cancel_sets_is_cancelled(self, tmp_path: Path) -> None:
        """cancel() 后 is_cancelled 应为 True。"""
        scanner = Scanner(_build_ruleset(_filename_rule("r", "x")))
        scanner.cancel()
        assert scanner.is_cancelled

    def test_cancel_unblocks_pause(self, tmp_path: Path) -> None:
        """cancel() 应解除暂停阻塞，_check_control() 立即返回 True。"""
        scanner = Scanner(_build_ruleset(_filename_rule("r", "x")))
        scanner.pause()
        scanner.cancel()
        # _check_control 不应阻塞
        assert scanner._check_control() is True

    def test_cancel_before_scan_returns_cancelled_report(self, tmp_path: Path) -> None:
        """扫描前取消：scan() 应立即返回 cancelled=True 的空报告。"""
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        scanner = Scanner(_build_ruleset(_filename_rule("r", "secret")))
        scanner.cancel()
        report = scanner.scan(tmp_path)
        assert report.cancelled
        assert report.stats.scanned_files == 0
        assert report.stats.matched_files == 0

    def test_cancel_during_scan_returns_partial(self, tmp_path: Path) -> None:
        """扫描中取消：应返回 cancelled=True。"""
        for i in range(50):
            (tmp_path / f"secret_{i}.txt").write_text("x", encoding="utf-8")
        scanner = Scanner(_build_ruleset(_filename_rule("r", "secret")), progress_interval=0.0)

        # 通过进度回调在首个进度事件时触发取消，确保扫描已开始
        def cancel_on_first_progress(_info: ProgressInfo) -> None:
            scanner.cancel()

        scanner._on_progress = cancel_on_first_progress
        report = scanner.scan(tmp_path)
        assert report.cancelled

    def test_cancel_during_concurrent_scan(self, tmp_path: Path) -> None:
        """并发扫描中取消：应返回 cancelled=True。"""
        for i in range(30):
            (tmp_path / f"secret_{i}.txt").write_text("x", encoding="utf-8")
        scanner = Scanner(
            _build_ruleset(_filename_rule("r", "secret")),
            max_workers=4,
            progress_interval=0.0,
        )

        def cancel_on_first_progress(_info: ProgressInfo) -> None:
            scanner.cancel()

        scanner._on_progress = cancel_on_first_progress
        report = scanner.scan(tmp_path)
        assert report.cancelled

    def test_pause_resume_completes_scan(self, tmp_path: Path) -> None:
        """暂停后恢复：扫描应正常完成且结果完整。"""
        for i in range(20):
            (tmp_path / f"secret_{i}.txt").write_text("x", encoding="utf-8")
        scanner = Scanner(_build_ruleset(_filename_rule("r", "secret")), progress_interval=0.0)

        started = threading.Event()

        def on_progress(_info: ProgressInfo) -> None:
            if not started.is_set():
                started.set()

        scanner._on_progress = on_progress

        report_holder: dict[str, ScanReport | None] = {"report": None}
        scan_thread = threading.Thread(target=lambda: report_holder.__setitem__("report", scanner.scan(tmp_path)))
        scan_thread.start()
        # 等待扫描线程开始工作后暂停
        assert started.wait(timeout=2)
        scanner.pause()
        assert scanner.is_paused
        time.sleep(0.05)
        scanner.resume()
        assert not scanner.is_paused
        scan_thread.join(timeout=5)

        assert not scan_thread.is_alive()
        report = report_holder["report"]
        assert report is not None
        assert not report.cancelled
        assert report.stats.matched_files == 20

    def test_pipelined_cancel_during_walk(self, tmp_path: Path) -> None:
        """流水线 walk 阶段取消应中断扫描（覆盖 walk 循环 _check_control break）。

        250 文件使 walk 阶段 ``total % 200 == 0`` 触发进度回调，
        回调中调用 cancel，下一次 walk 迭代 ``_check_control()`` 返回 True 并 break。
        """
        for i in range(250):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        scanner = Scanner(
            _build_ruleset(_filename_rule("r", "f")),
            max_workers=4,
            progress_interval=0.0,
        )

        def cancel_on_first_progress(_info: ProgressInfo) -> None:
            scanner.cancel()

        scanner._on_progress = cancel_on_first_progress
        report = scanner.scan(tmp_path)
        assert report.cancelled


class TestScannerExtraCoverage:
    """补充覆盖 scanner.py 异常路径与边界。"""

    def test_default_extract_content_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """extract_content 抛异常时回退到 read_text。"""
        from fuscan.scanner.context import FileEntry
        from fuscan.scanner.scanner import default_extract_content

        path = tmp_path / "a.txt"
        path.write_text("password fallback", encoding="utf-8")
        entry = FileEntry.from_path(path)

        def raise_extract(p: Path) -> str:
            raise RuntimeError("提取失败")

        monkeypatch.setattr("fuscan.extractors.base.extract_content", raise_extract)
        content = default_extract_content(entry)
        assert "password fallback" in content

    def test_scan_single_entry_exception_counts_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_scan_entry 抛异常时单线程扫描应计 error 并继续。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = Scanner(rs)

        original_scan_entry = scanner._scan_entry
        call_count = {"n": 0}

        def fake_scan_entry(entry):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("模拟扫描失败")
            return original_scan_entry(entry)

        monkeypatch.setattr(scanner, "_scan_entry", fake_scan_entry)
        report = scanner.scan(tmp_path)
        assert report.stats.errors >= 1

    def test_scan_concurrent_entry_exception_counts_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """并发扫描中 _scan_entry 抛异常应计 error。"""
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = Scanner(rs, max_workers=2)

        original_scan_entry = scanner._scan_entry
        call_count = {"n": 0}

        def fake_scan_entry(entry):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("模拟并发扫描失败")
            return original_scan_entry(entry)

        monkeypatch.setattr(scanner, "_scan_entry", fake_scan_entry)
        report = scanner.scan(tmp_path)
        assert report.stats.errors >= 1

    def test_should_scan_dir_returns_false(self) -> None:
        """_should_scan 对目录返回 False。"""
        from fuscan.scanner.context import FileEntry

        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = Scanner(rs)
        entry = FileEntry(path=Path("/tmp/somedir"), name="somedir", size=0, mtime=0.0, extension="", is_dir=True)
        assert scanner._should_scan(entry) is False

    def test_scan_archive_phase_exception_counts_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """压缩包扫描抛异常时计 error 并继续。"""
        import zipfile

        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("secret.txt", "x")

        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True)

        from fuscan.archive import scanner as archive_scanner_mod

        def fake_scan_archive(self, path):  # type: ignore[no-untyped-def]
            raise RuntimeError("模拟压缩包扫描失败")

        monkeypatch.setattr(archive_scanner_mod.ArchiveScanner, "scan_archive", fake_scan_archive)
        report = scanner.scan(tmp_path)
        assert report.stats.errors >= 1

    def test_scan_archive_phase_cancel_breaks(self, tmp_path: Path) -> None:
        """压缩包扫描阶段取消应中断。"""
        import zipfile

        for i in range(3):
            with zipfile.ZipFile(str(tmp_path / f"a{i}.zip"), "w") as zf:
                zf.writestr("secret.txt", "x")

        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True, progress_interval=0.0)

        scanner.cancel()
        report = scanner.scan(tmp_path)
        assert report.cancelled


class TestScannerCache:
    """缓存模式扫描测试。"""

    def test_cache_hit_reuses_result(self, tmp_path: Path) -> None:
        """第二次扫描应复用缓存结果，命中信息一致。"""
        from fuscan.cache import CacheStore

        (tmp_path / "secret.txt").write_text("password=abc", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner1 = Scanner(rs, cache=cache)
            report1 = scanner1.scan(tmp_path)
            assert report1.stats.matched_files == 1
            hit1 = report1.hits[0].hits[0]
            assert hit1.rule_name == "pwd"
            assert hit1.match_count == 1

            # 第二次扫描应命中缓存
            scanner2 = Scanner(rs, cache=cache)
            report2 = scanner2.scan(tmp_path)
            assert report2.stats.matched_files == 1
            hit2 = report2.hits[0].hits[0]
            assert hit2.rule_name == "pwd"
            assert hit2.match_count == hit1.match_count
            assert hit2.match_text == hit1.match_text
        finally:
            cache.close()

    def test_cache_miss_writes_result(self, tmp_path: Path) -> None:
        """扫描后缓存应包含结果记录。"""
        from fuscan.cache import CacheStore, compute_file_hash

        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner = Scanner(rs, cache=cache)
            scanner.scan(tmp_path)

            file_hash = compute_file_hash(tmp_path / "a.txt")
            rule_hashes = cache.get_rule_hashes()
            cached = cache.get_cached_hits(file_hash, list(rule_hashes.values()))
            assert len(cached) == 1
            cached_hit = next(iter(cached.values()))
            assert cached_hit is not None
            assert cached_hit.match_count == 1
        finally:
            cache.close()

    def test_file_change_triggers_rescan(self, tmp_path: Path) -> None:
        """文件内容变更后应重新扫描。"""
        from fuscan.cache import CacheStore

        path = tmp_path / "a.txt"
        path.write_text("password=old", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner1 = Scanner(rs, cache=cache)
            report1 = scanner1.scan(tmp_path)
            assert report1.stats.matched_files == 1
            assert report1.hits[0].hits[0].match_text == "password"

            # 修改文件内容（仍命中但 match_text 不同）
            path.write_text("password=new\npassword=again", encoding="utf-8")
            scanner2 = Scanner(rs, cache=cache)
            report2 = scanner2.scan(tmp_path)
            assert report2.stats.matched_files == 1
            # 新内容匹配 2 处
            assert report2.hits[0].hits[0].match_count == 2
        finally:
            cache.close()

    def test_path_change_still_hits(self, tmp_path: Path) -> None:
        """文件移动到新路径后，缓存仍命中（哈希不变）。"""
        from fuscan.cache import CacheStore

        (tmp_path / "sub").mkdir()
        path1 = tmp_path / "a.txt"
        path1.write_text("password=abc", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner1 = Scanner(rs, cache=cache)
            report1 = scanner1.scan(tmp_path)
            assert report1.stats.matched_files == 1

            # 移动文件到新路径
            path2 = tmp_path / "sub" / "renamed.txt"
            path1.rename(path2)
            scanner2 = Scanner(rs, cache=cache)
            report2 = scanner2.scan(tmp_path)
            assert report2.stats.matched_files == 1
            assert report2.hits[0].path.name == "renamed.txt"
            assert report2.hits[0].hits[0].rule_name == "pwd"
        finally:
            cache.close()

    def test_rule_change_triggers_rescan(self, tmp_path: Path) -> None:
        """规则变更（pattern 不同）后应重新扫描。"""
        from fuscan.cache import CacheStore

        (tmp_path / "a.txt").write_text("secret_key=abc", encoding="utf-8")
        rs1 = _build_ruleset(_content_rule("pwd", "secret"))
        rs2 = _build_ruleset(_content_rule("pwd", "key"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner1 = Scanner(rs1, cache=cache)
            report1 = scanner1.scan(tmp_path)
            assert report1.stats.matched_files == 1

            # 规则变更：pattern "secret" -> "key"
            scanner2 = Scanner(rs2, cache=cache)
            report2 = scanner2.scan(tmp_path)
            assert report2.stats.matched_files == 1
        finally:
            cache.close()

    def test_uncached_mode_unchanged(self, tmp_path: Path) -> None:
        """cache=None 时走原 _scan_entry_uncached 路径。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs)  # 不传 cache
        assert scanner._cache is None
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1

    def test_cache_concurrent_safe(self, tmp_path: Path) -> None:
        """多线程缓存扫描结果应与单线程一致。"""
        from fuscan.cache import CacheStore

        for i in range(20):
            (tmp_path / f"secret_{i}.txt").write_text(f"password_{i}", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner = Scanner(rs, cache=cache, max_workers=4)
            report = scanner.scan(tmp_path)
            assert report.stats.matched_files == 20
            assert report.stats.errors == 0
        finally:
            cache.close()

    def test_cache_none_hit_not_returned(self, tmp_path: Path) -> None:
        """未命中规则的文件二次扫描不产生命中。"""
        from fuscan.cache import CacheStore

        (tmp_path / "clean.txt").write_text("nothing suspicious", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner1 = Scanner(rs, cache=cache)
            report1 = scanner1.scan(tmp_path)
            assert report1.stats.matched_files == 0

            scanner2 = Scanner(rs, cache=cache)
            report2 = scanner2.scan(tmp_path)
            assert report2.stats.matched_files == 0
        finally:
            cache.close()

    def test_default_extract_content_with_hash(self, tmp_path: Path) -> None:
        """default_extract_content_with_hash 返回内容和哈希。"""
        import hashlib

        from fuscan.scanner.context import FileEntry
        from fuscan.scanner.scanner import default_extract_content_with_hash

        path = tmp_path / "a.txt"
        path.write_bytes(b"password content")
        entry = FileEntry.from_path(path)
        content, file_hash = default_extract_content_with_hash(entry)
        assert "password" in content
        expected = hashlib.sha256(b"password content").hexdigest()
        assert file_hash == expected

    def test_default_extract_content_with_hash_empty_for_dir(self, tmp_path: Path) -> None:
        """目录返回空内容和空哈希。"""
        import hashlib

        from fuscan.scanner.context import FileEntry
        from fuscan.scanner.scanner import default_extract_content_with_hash

        (tmp_path / "subdir").mkdir()
        entry = FileEntry.from_path(tmp_path / "subdir")
        content, file_hash = default_extract_content_with_hash(entry)
        assert content == ""
        assert file_hash == hashlib.sha256(b"").hexdigest()

    def test_default_extract_content_with_hash_single_io(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """default_extract_content_with_hash 只读一次磁盘（消除双重 I/O）。"""
        import hashlib

        from fuscan.scanner.context import FileEntry
        from fuscan.scanner.scanner import default_extract_content_with_hash

        path = tmp_path / "a.txt"
        path.write_bytes(b"password content")
        entry = FileEntry.from_path(path)

        call_count = 0
        original_read_bytes = Path.read_bytes

        def counting_read_bytes(self: Path) -> bytes:
            nonlocal call_count
            if self == path:
                call_count += 1
            return original_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)
        content, file_hash = default_extract_content_with_hash(entry)
        assert call_count == 1, "read_bytes 应只调用一次（消除双重 I/O）"
        assert "password" in content
        assert file_hash == hashlib.sha256(b"password content").hexdigest()

    def test_default_extract_content_with_hash_oversize_returns_empty(self, tmp_path: Path) -> None:
        """超过 100MB 的文件返回空内容和空哈希。"""
        import hashlib

        from fuscan.scanner.context import FileEntry
        from fuscan.scanner.scanner import default_extract_content_with_hash

        path = tmp_path / "big.txt"
        # 写入 100MB+1 字节
        path.write_bytes(b"x" * (100 * 1024 * 1024 + 1))
        entry = FileEntry.from_path(path)
        content, file_hash = default_extract_content_with_hash(entry)
        assert content == ""
        assert file_hash == hashlib.sha256(b"").hexdigest()

    def test_default_extract_content_with_hash_read_os_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """read_bytes 失败时返回空内容和空哈希。"""
        import hashlib

        from fuscan.scanner.context import FileEntry
        from fuscan.scanner.scanner import default_extract_content_with_hash

        path = tmp_path / "a.txt"
        path.write_bytes(b"content")
        entry = FileEntry.from_path(path)

        def mock_read_bytes(self: Path) -> bytes:
            if self == path:
                raise OSError("模拟读取失败")
            return b""

        monkeypatch.setattr(Path, "read_bytes", mock_read_bytes)
        content, file_hash = default_extract_content_with_hash(entry)
        assert content == ""
        assert file_hash == hashlib.sha256(b"").hexdigest()

    def test_default_extract_content_with_hash_extractor_error_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """提取器抛异常时回退到 UTF-8 解码。"""
        from fuscan.scanner.context import FileEntry
        from fuscan.scanner.scanner import default_extract_content_with_hash

        path = tmp_path / "a.txt"
        path.write_bytes(b"password content")
        entry = FileEntry.from_path(path)

        def mock_extract_from_bytes(data: bytes, extension: str) -> str:
            raise RuntimeError("模拟提取器失败")

        monkeypatch.setattr("fuscan.scanner.scanner.extract_content_from_bytes", mock_extract_from_bytes)
        content, file_hash = default_extract_content_with_hash(entry)
        assert "password content" in content  # 回退到 UTF-8 解码
        assert len(file_hash) == 64  # 哈希仍正确计算
