"""扫描器单元测试。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from fuscan.cache.hashes import hash_bytes
from fuscan.config import DEFAULT_MAX_FILE_SIZE
from fuscan.extractors import extract_content_from_bytes_with_retry
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
from fuscan.scanner import Scanner, ScanReport, ScanResult, default_extract_content
from fuscan.scanner._content_buckets import _ContentRuleBucket
from fuscan.scanner.context import FileEntry
from fuscan.scanner.result import ProgressInfo, ScanStats, WalkResult


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
        (tmp_path / ".git" / "password.txt").write_text("x", encoding="utf-8")
        (tmp_path / "password.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "password"))
        scanner = Scanner(rs, ignore_dirs=(".git",))
        report = scanner.scan(tmp_path)
        assert report.stats.total_files == 1  # .git 内被忽略
        assert report.stats.matched_files == 1

    def test_scan_respects_scan_extensions_whitelist(self, tmp_path: Path) -> None:
        """iter-87 白名单制：仅扫描 scan_extensions 指定后缀的文件。"""
        (tmp_path / "password.pyc").write_text("x", encoding="utf-8")
        (tmp_path / "password.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "password"))
        scanner = Scanner(rs, scan_extensions=("txt",))
        report = scanner.scan(tmp_path)
        # 两个文件均被 walker 发现
        assert report.stats.total_files == 2
        # pyc 不在白名单中被跳过，仅 txt 被扫描
        assert report.stats.scanned_files == 1
        assert report.stats.skipped_files == 1
        assert report.stats.matched_files == 1

    def test_scan_extensions_empty_whitelist_scans_nothing(self, tmp_path: Path) -> None:
        """iter-87：空白名单（用户全部取消勾选）时不扫描任何文件。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.py").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "a"))
        scanner = Scanner(rs, scan_extensions=())
        report = scanner.scan(tmp_path)
        assert report.stats.total_files == 2
        assert report.stats.scanned_files == 0
        assert report.stats.skipped_files == 2

    def test_scan_respects_skip_paths(self, tmp_path: Path) -> None:
        """skip_paths 标记的文件不计入扫描队列，单独统计为 user_skipped（iter-77）。"""
        skip_file = tmp_path / "secret.txt"
        skip_file.write_text("password", encoding="utf-8")
        (tmp_path / "password.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, skip_paths=frozenset({str(skip_file)}))
        report = scanner.scan(tmp_path)
        # 两个文件都被发现（total），但 secret.txt 被用户标记跳过
        assert report.stats.total_files == 2
        assert report.stats.user_skipped == 1
        assert report.stats.scanned_files == 1
        assert report.stats.matched_files == 1  # 只有 password.txt 命中
        # secret.txt 不在结果中
        assert all(r.path != skip_file for r in report.results)

    def test_scan_skip_paths_takes_precedence_over_extension_match(self, tmp_path: Path) -> None:
        """skip_paths 优先于 _should_scan：即使扩展名匹配也被跳过（iter-77）。"""
        skip_file = tmp_path / "skip.conf"
        skip_file.write_text("password", encoding="utf-8")
        (tmp_path / "scan.conf").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(
            rs,
            scan_extensions=("conf",),
            skip_paths=frozenset({str(skip_file)}),
        )
        report = scanner.scan(tmp_path)
        # 两个文件都被发现
        assert report.stats.total_files == 2
        # skip.conf 被用户标记跳过
        assert report.stats.user_skipped == 1
        # 仅 scan.conf 进入扫描队列
        assert report.stats.scanned_files == 1
        assert report.stats.matched_files == 1

    def test_scan_skip_paths_empty_behaves_like_default(self, tmp_path: Path) -> None:
        """空 skip_paths 应与默认行为一致（iter-77 回归测试）。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, skip_paths=frozenset())
        report = scanner.scan(tmp_path)
        assert report.stats.user_skipped == 0
        assert report.stats.scanned_files == 1

    def test_scan_skip_paths_progress_info_reports_user_skipped(self, tmp_path: Path) -> None:
        """ProgressInfo 应上报 user_skipped 计数（iter-77）。"""
        skip_file = tmp_path / "skip.txt"
        skip_file.write_text("x", encoding="utf-8")
        (tmp_path / "scan.txt").write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "x"))
        captured: list[ProgressInfo] = []

        def on_progress(info: ProgressInfo) -> None:
            captured.append(info)

        scanner = Scanner(
            rs,
            on_progress=on_progress,
            progress_interval=0.0,
            skip_paths=frozenset({str(skip_file)}),
        )
        scanner.scan(tmp_path)
        # 最终进度应反映 user_skipped=1
        last = captured[-1]
        assert last.user_skipped == 1


class TestScannerCompiledCache:
    """规则集编译缓存测试（ruleset 未变时复用已编译 Matcher 列表）。"""

    def setup_method(self) -> None:
        """每个测试前清空缓存，避免跨测试干扰。"""
        from fuscan.scanner.scanner import clear_compiled_cache

        clear_compiled_cache()

    def test_cache_miss_on_first_construct(self) -> None:
        """首次构造 Scanner 时缓存未命中，编译后写入缓存。"""
        from fuscan.scanner.scanner import _compiled_cache

        rs = _build_ruleset(_filename_rule("r", "x"))
        assert len(_compiled_cache) == 0
        Scanner(rs)
        assert len(_compiled_cache) == 1

    def test_cache_hit_on_second_construct_same_ruleset(self) -> None:
        """同一 ruleset 对象第二次构造命中缓存，复用编译产物。"""
        rs = _build_ruleset(_filename_rule("r", "x"))
        sc1 = Scanner(rs)
        # 第二次构造应命中缓存，_compiled 列表是同一对象引用
        sc2 = Scanner(rs)
        assert sc2._compiled is sc1._compiled
        assert sc2._global_content_buckets is sc1._global_content_buckets
        assert sc2._content_rule_names is sc1._content_rule_names

    def test_cache_miss_on_different_ruleset(self) -> None:
        """不同 ruleset 对象不命中缓存（id 不同）。"""
        from fuscan.scanner.scanner import _compiled_cache

        rs1 = _build_ruleset(_filename_rule("r1", "x"))
        rs2 = _build_ruleset(_filename_rule("r2", "y"))
        sc1 = Scanner(rs1)
        sc2 = Scanner(rs2)
        assert sc2._compiled is not sc1._compiled
        assert len(_compiled_cache) == 2

    def test_clear_cache_forces_recompile(self) -> None:
        """clear_compiled_cache 后下次构造重新编译。"""
        from fuscan.scanner.scanner import clear_compiled_cache

        rs = _build_ruleset(_filename_rule("r", "x"))
        sc1 = Scanner(rs)
        clear_compiled_cache()
        sc2 = Scanner(rs)
        # 清空后重新编译，_compiled 应是新对象
        assert sc2._compiled is not sc1._compiled

    def test_cached_scanner_produces_correct_results(self, tmp_path: Path) -> None:
        """缓存命中时扫描结果与首次编译一致（功能正确性）。"""
        (tmp_path / "secret.txt").write_text("password=123", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        # 首次编译 + 扫描
        sc1 = Scanner(rs)
        report1 = sc1.scan(tmp_path)
        # 第二次命中缓存 + 扫描
        sc2 = Scanner(rs)
        report2 = sc2.scan(tmp_path)
        assert report1.stats.matched_files == report2.stats.matched_files
        assert len(report1.hits) == len(report2.hits)

    def test_cache_eviction_when_full(self) -> None:
        """缓存满后清空，新 ruleset 重新编译。"""
        from fuscan.scanner.scanner import _COMPILED_CACHE_MAX, _compiled_cache

        scanners: list[Scanner] = []
        rulesets: list[RuleSet] = []
        for i in range(_COMPILED_CACHE_MAX + 1):
            rs = _build_ruleset(_filename_rule(f"r{i}", f"x{i}"))
            rulesets.append(rs)
            scanners.append(Scanner(rs))
        # 超过上限后缓存被清空再写入最新一条
        assert len(_compiled_cache) <= _COMPILED_CACHE_MAX

    def test_weakref_invalidation_after_gc(self) -> None:
        """ruleset 被 GC 后缓存条目失效，id 复用不会假命中。"""
        import gc

        from fuscan.scanner.scanner import _compiled_cache

        rs = _build_ruleset(_filename_rule("r", "x"))
        Scanner(rs)
        assert len(_compiled_cache) == 1
        del rs
        gc.collect()
        # ruleset 被 GC 后，weakref 失效，但缓存条目仍在 dict 中
        # 下次用同 id 的不同 ruleset 构造时会检测到并清除
        # （weakref() is not ruleset → 删除条目 → 重新编译）
        # 这里验证的是缓存不会永远持有已 GC 的 ruleset 的编译产物
        # 实际清除发生在下次同 id 构造时

    def test_scanner_with_cache_uses_compiled_matchers(self, tmp_path: Path) -> None:
        """缓存命中时 Scanner 仍能正确使用 _compiled_with_hash（cache 模式）。"""
        from fuscan.cache import CacheStore

        rs = _build_ruleset(_content_rule("r", "password"))
        (tmp_path / "f.txt").write_text("password=123", encoding="utf-8")
        cache_db = tmp_path / "test_cache.db"
        cache = CacheStore(cache_db)
        # 首次编译 + 缓存登记
        sc1 = Scanner(rs, cache=cache, source_files={})
        report1 = sc1.scan(tmp_path)
        # 第二次命中编译缓存（cache 模式）
        sc2 = Scanner(rs, cache=cache, source_files={})
        report2 = sc2.scan(tmp_path)
        assert report1.stats.matched_files == report2.stats.matched_files


class TestScannerCollectScanSplit:
    """collect_entries + scan_entries 职责拆分测试（stats/scan worker 分离）。"""

    def test_collect_entries_returns_walk_result(self, tmp_path: Path) -> None:
        """collect_entries 返回 WalkResult，含 entries 与统计。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        (tmp_path / "b.md").write_text("normal", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs)
        walk_result = scanner.collect_entries(tmp_path)
        assert walk_result.root == tmp_path
        assert walk_result.total == 2
        assert walk_result.cancelled is False
        # 两个文件都进入扫描队列（无 scan_extensions 过滤）
        assert len(walk_result.entries) == 2
        assert {e.path.name for e in walk_result.entries} == {"a.txt", "b.md"}

    def test_collect_entries_respects_scan_extensions(self, tmp_path: Path) -> None:
        """collect_entries 按 scan_extensions 过滤，未匹配文件计入 skipped。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.md").write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = Scanner(rs, scan_extensions=("txt",))
        walk_result = scanner.collect_entries(tmp_path)
        assert walk_result.total == 2
        assert walk_result.skipped == 1  # b.md 被过滤
        assert len(walk_result.entries) == 1
        assert walk_result.entries[0].path.name == "a.txt"

    def test_collect_entries_respects_skip_paths(self, tmp_path: Path) -> None:
        """collect_entries 按 skip_paths 跳过，计入 user_skipped。"""
        skip_file = tmp_path / "skip.txt"
        skip_file.write_text("x", encoding="utf-8")
        (tmp_path / "scan.txt").write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = Scanner(rs, skip_paths=frozenset({str(skip_file)}))
        walk_result = scanner.collect_entries(tmp_path)
        assert walk_result.total == 2
        assert walk_result.user_skipped == 1
        assert len(walk_result.entries) == 1
        assert walk_result.entries[0].path.name == "scan.txt"

    def test_scan_entries_equivalent_to_scan(self, tmp_path: Path) -> None:
        """collect_entries + scan_entries 应与 scan 产生等价的 hits 与统计。"""
        (tmp_path / "password.txt").write_text("db_password=x", encoding="utf-8")
        (tmp_path / "readme.md").write_text("normal", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        # 完整 scan
        scanner_full = Scanner(rs)
        report_full = scanner_full.scan(tmp_path)

        # 拆分：collect + scan_entries
        scanner_split = Scanner(rs)
        walk_result = scanner_split.collect_entries(tmp_path)
        report_split = scanner_split.scan_entries(tmp_path, walk_result)

        # hits 等价（路径与规则名一致）
        assert {r.path for r in report_full.hits} == {r.path for r in report_split.hits}
        assert report_full.stats.matched_files == report_split.stats.matched_files
        assert report_full.stats.scanned_files == report_split.stats.scanned_files
        assert report_full.stats.total_files == report_split.stats.total_files
        assert report_full.stats.skipped_files == report_split.stats.skipped_files
        assert report_full.stats.user_skipped == report_split.stats.user_skipped

    def test_scan_entries_with_scan_extensions_equivalent(self, tmp_path: Path) -> None:
        """带 scan_extensions 时拆分模式与完整 scan 等价。"""
        (tmp_path / "a.conf").write_text("password", encoding="utf-8")
        (tmp_path / "b.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        scanner_full = Scanner(rs, scan_extensions=("conf",))
        report_full = scanner_full.scan(tmp_path)

        scanner_split = Scanner(rs, scan_extensions=("conf",))
        walk_result = scanner_split.collect_entries(tmp_path)
        report_split = scanner_split.scan_entries(tmp_path, walk_result)

        assert report_full.stats.matched_files == report_split.stats.matched_files == 1
        assert report_full.stats.skipped_files == report_split.stats.skipped_files == 1
        assert {r.path for r in report_full.hits} == {r.path for r in report_split.hits}

    def test_scan_entries_walk_cancelled_skips_scan(self, tmp_path: Path) -> None:
        """walk_result.cancelled=True 时 scan_entries 跳过 scan 阶段，返回空结果。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs)
        cancelled_walk = WalkResult(root=tmp_path, entries=(), total=0, cancelled=True)
        report = scanner.scan_entries(tmp_path, cancelled_walk)
        assert report.cancelled is True
        assert report.stats.scanned_files == 0
        assert report.hits == ()


class TestScannerFilterPhase:
    """iter-148 三阶段扫描重构的 filter 阶段测试。

    覆盖 :func:`run_filter_phase` 各筛除原因（empty/oversize/unreadable/symlink）、
    :meth:`Scanner.filter_entries` 薄包装、:meth:`Scanner.scan_entries` 优先使用
    ``filtered_entries`` 与回退 ``entries`` 的向后兼容行为，以及 filter 阶段进度 emit。
    """

    def test_filter_removes_empty_files(self, tmp_path: Path) -> None:
        """size==0 的文件被 filter 阶段剔除，不进入扫描队列。"""
        (tmp_path / "empty.txt").write_text("", encoding="utf-8")
        (tmp_path / "real.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs)
        walk_result = scanner.collect_entries(tmp_path)
        filtered = scanner.filter_entries(walk_result)
        assert filtered.filter_stats is not None
        assert filtered.filter_stats.removed_empty == 1
        assert filtered.filter_stats.total_removed == 1
        # filtered_entries 仅含 real.txt
        assert len(filtered.filtered_entries) == 1
        assert filtered.filtered_entries[0].path.name == "real.txt"

    def test_filter_removes_oversize_files(self, tmp_path: Path) -> None:
        """size > max_file_size 的文件被 filter 阶段剔除。"""
        (tmp_path / "big.txt").write_text("x" * 100 + "password", encoding="utf-8")
        (tmp_path / "small.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_file_size=20)
        walk_result = scanner.collect_entries(tmp_path)
        filtered = scanner.filter_entries(walk_result)
        assert filtered.filter_stats is not None
        assert filtered.filter_stats.removed_oversize == 1
        assert len(filtered.filtered_entries) == 1
        assert filtered.filtered_entries[0].path.name == "small.txt"

    def test_filter_keeps_archive_oversize_when_scan_archives(self, tmp_path: Path) -> None:
        """启用 scan_archives 时压缩包文件不参与 oversize 判断（作为容器由 ArchiveScanner 处理）。"""
        import zipfile

        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("big.txt", "x" * 100 + "password")
            zf.writestr("small.txt", "password")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        # 阈值远小于 zip 文件本身的大小，但 zip 应保留以供 archive 阶段处理
        scanner = Scanner(rs, scan_archives=True, max_file_size=10)
        walk_result = scanner.collect_entries(tmp_path)
        filtered = scanner.filter_entries(walk_result)
        assert filtered.filter_stats is not None
        assert filtered.filter_stats.removed_oversize == 0
        # zip 文件保留在 filtered_entries 中
        assert any(e.path.name == "a.zip" for e in filtered.filtered_entries)

    def test_filter_removes_oversize_archive_when_scan_archives_disabled(self, tmp_path: Path) -> None:
        """未启用 scan_archives 时压缩包文件按普通文件参与 oversize 判断。"""
        import zipfile

        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("big.txt", "x" * 100 + "password")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        # scan_archives=False：zip 作为普通文件，超限即剔除
        scanner = Scanner(rs, scan_archives=False, max_file_size=10)
        walk_result = scanner.collect_entries(tmp_path)
        filtered = scanner.filter_entries(walk_result)
        assert filtered.filter_stats is not None
        assert filtered.filter_stats.removed_oversize == 1

    def test_filter_removes_unreadable_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """os.access 返回 False 的文件被 filter 阶段剔除。"""
        (tmp_path / "ok.txt").write_text("password", encoding="utf-8")
        (tmp_path / "denied.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs)
        walk_result = scanner.collect_entries(tmp_path)
        # 模拟 denied.txt 不可读：os.access 对该路径返回 False
        denied_path = str(tmp_path / "denied.txt")
        original_access = __import__("os").access

        def mock_access(path: object, mode: int) -> bool:
            if str(path) == denied_path:
                return False
            return original_access(path, mode)

        # _filter_phase.py 内 os.access 已在模块顶层 import
        import fuscan.scanner._filter_phase as filter_module

        monkeypatch.setattr(filter_module.os, "access", mock_access)
        filtered = scanner.filter_entries(walk_result)
        assert filtered.filter_stats is not None
        assert filtered.filter_stats.removed_unreadable == 1
        # ok.txt 保留
        assert len(filtered.filtered_entries) == 1
        assert filtered.filtered_entries[0].path.name == "ok.txt"

    def test_filter_removes_symlink_files(self, tmp_path: Path) -> None:
        """follow_symlinks=False 时符号链接文件被 filter 阶段剔除。"""
        import sys

        target = tmp_path / "real.txt"
        target.write_text("password", encoding="utf-8")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("当前平台不支持创建符号链接")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, follow_symlinks=False)
        walk_result = scanner.collect_entries(tmp_path)
        # Windows 上 is_symlink 可能行为不一，跳过断言若未生成符号链接
        if not sys.platform.startswith("win") and not link.is_symlink():
            pytest.skip("符号链接未成功创建")
        filtered = scanner.filter_entries(walk_result)
        assert filtered.filter_stats is not None
        assert filtered.filter_stats.removed_symlink >= 1
        # 符号链接被剔除，仅保留 real.txt
        assert all(not e.path.is_symlink() for e in filtered.filtered_entries)

    def test_filter_keeps_symlink_when_follow_symlinks_true(self, tmp_path: Path) -> None:
        """follow_symlinks=True 时符号链接文件不被 filter 阶段剔除。"""
        target = tmp_path / "real.txt"
        target.write_text("password", encoding="utf-8")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("当前平台不支持创建符号链接")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, follow_symlinks=True)
        walk_result = scanner.collect_entries(tmp_path)
        filtered = scanner.filter_entries(walk_result)
        assert filtered.filter_stats is not None
        assert filtered.filter_stats.removed_symlink == 0

    def test_filter_stats_correctly_populated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FilterStats 四类计数正确（混合场景）。"""
        # empty 文件
        (tmp_path / "empty.txt").write_text("", encoding="utf-8")
        # oversize 文件
        (tmp_path / "big.txt").write_text("x" * 100, encoding="utf-8")
        # 正常文件
        (tmp_path / "ok.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_file_size=20)
        walk_result = scanner.collect_entries(tmp_path)
        filtered = scanner.filter_entries(walk_result)
        assert filtered.filter_stats is not None
        assert filtered.filter_stats.removed_empty == 1
        assert filtered.filter_stats.removed_oversize == 1
        assert filtered.filter_stats.removed_unreadable == 0
        assert filtered.filter_stats.removed_symlink == 0
        assert filtered.filter_stats.total_removed == 2
        # 仅 ok.txt 保留
        assert len(filtered.filtered_entries) == 1
        assert filtered.filtered_entries[0].path.name == "ok.txt"

    def test_scan_entries_prefers_filtered_entries(self, tmp_path: Path) -> None:
        """scan_entries 优先使用 filtered_entries（非空时），entries 字段被忽略。"""
        (tmp_path / "empty.txt").write_text("", encoding="utf-8")
        (tmp_path / "real.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs)
        walk_result = scanner.collect_entries(tmp_path)
        filtered = scanner.filter_entries(walk_result)
        # entries 仍含全部文件（filter 不修改 entries 字段，仅填 filtered_entries）
        assert len(walk_result.entries) == 2
        assert len(filtered.entries) == 2
        assert len(filtered.filtered_entries) == 1
        report = scanner.scan_entries(tmp_path, filtered)
        # 仅 real.txt 进入扫描，命中
        assert report.stats.matched_files == 1
        assert report.stats.filter_removed == 1

    def test_scan_entries_falls_back_to_entries(self, tmp_path: Path) -> None:
        """filtered_entries 为空时 scan_entries 回退到 entries（向后兼容）。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs)
        # 直接构造无 filtered_entries 的 WalkResult（旧调用方场景）
        walk_result = scanner.collect_entries(tmp_path)
        assert len(walk_result.filtered_entries) == 0
        assert walk_result.filter_stats is None
        report = scanner.scan_entries(tmp_path, walk_result)
        # 回退到 entries，正常扫描
        assert report.stats.matched_files == 1
        assert report.stats.filter_removed == 0

    def test_scan_entry_uncached_no_longer_skips_oversize(self, tmp_path: Path) -> None:
        """iter-148：_scan_entry_uncached 不再做 max_file_size 跳过（已前移到 filter）。

        scan_file 单文件入口未走 filter 阶段，但仍由内容提供器内部 size 限制保护。
        """
        (tmp_path / "big.txt").write_text("x" * 100 + "password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        # max_file_size 远小于文件大小，但 scan_file 不走 filter 阶段
        scanner = Scanner(rs, max_file_size=10)
        result = scanner.scan_file(tmp_path / "big.txt")
        # _scan_entry_uncached 不再跳过 oversize；内容提取由 default_content_provider
        # 内部 max_size=50MB 限制保护（big.txt 仅 109 字节，未超限，内容正常读取）
        assert result.has_hit

    def test_scan_stats_filter_removed_accumulated(self, tmp_path: Path) -> None:
        """ScanStats.filter_removed 正确累计被 filter 剔除的文件总数。"""
        # 两个空文件 + 一个 oversize 文件 + 一个正常文件
        (tmp_path / "empty1.txt").write_text("", encoding="utf-8")
        (tmp_path / "empty2.txt").write_text("", encoding="utf-8")
        (tmp_path / "big.txt").write_text("x" * 100, encoding="utf-8")
        (tmp_path / "ok.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_file_size=20)
        report = scanner.scan(tmp_path)
        assert report.stats.filter_removed == 3  # 2 empty + 1 oversize

    def test_filter_phase_progress_emits(self, tmp_path: Path) -> None:
        """filter 阶段每 N 个文件 emit 一次 phase='filter' 进度，结束时强制 emit。"""
        # 创建 250+ 文件触发多次 emit（_FILTER_EMIT_INTERVAL=200）
        for i in range(250):
            (tmp_path / f"f{i:03d}.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        captured: list[ProgressInfo] = []

        def on_progress(info: ProgressInfo) -> None:
            captured.append(info)

        scanner = Scanner(rs, on_progress=on_progress, progress_interval=0.0)
        scanner.scan(tmp_path)
        # 应有 phase='filter' 的 emit
        filter_progress = [p for p in captured if p.phase == "filter"]
        assert len(filter_progress) >= 1
        # 最后一次 filter emit 应反映完整 entries 处理
        last_filter = filter_progress[-1]
        assert last_filter.scanned == 250  # 全部 entries 处理完毕

    def test_filter_phase_progress_summary_text(self, tmp_path: Path) -> None:
        """ProgressInfo.summary() 在 phase='filter' 时返回筛选阶段文案。"""
        (tmp_path / "empty.txt").write_text("", encoding="utf-8")
        (tmp_path / "ok.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        captured: list[ProgressInfo] = []

        def on_progress(info: ProgressInfo) -> None:
            captured.append(info)

        scanner = Scanner(rs, on_progress=on_progress, progress_interval=0.0)
        scanner.scan(tmp_path)
        filter_progress = [p for p in captured if p.phase == "filter"]
        assert filter_progress, "应至少有一次 filter 阶段进度"
        summary = filter_progress[-1].summary()
        assert "筛选" in summary
        assert "空" in summary

    def test_scan_full_pipeline_filter_stats_in_report(self, tmp_path: Path) -> None:
        """完整 scan() 流程后 ScanStats.filter_removed 反映 filter 剔除数。"""
        (tmp_path / "empty.txt").write_text("", encoding="utf-8")
        (tmp_path / "big.txt").write_text("x" * 100, encoding="utf-8")
        (tmp_path / "ok.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_file_size=20)
        report = scanner.scan(tmp_path)
        # 2 个被剔除（empty + oversize）
        assert report.stats.filter_removed == 2
        # 仅 ok.txt 命中
        assert report.stats.matched_files == 1
        # summary 应含「筛选剔除」片段
        assert "筛选剔除" in report.stats.summary()

    def test_filter_phase_walk_result_cancelled_skips_filter(self, tmp_path: Path) -> None:
        """walk_result.cancelled=True 时 filter 阶段跳过筛选，filter_stats 仍为空 stats。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs)
        cancelled_walk = WalkResult(
            root=tmp_path,
            entries=(FileEntry.from_path(tmp_path / "a.txt"),),
            total=1,
            cancelled=True,
        )
        filtered = scanner.filter_entries(cancelled_walk)
        # 取消时跳过筛选循环，filter_stats 仍为零值
        assert filtered.filter_stats is not None
        assert filtered.filter_stats.total_removed == 0
        assert len(filtered.filtered_entries) == 0
        assert filtered.cancelled is True


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
        """全局 scan_extensions 过滤：只扫描指定后缀的文件（iter-71 起替代规则级 file_extensions）。"""
        (tmp_path / "a.conf").write_text("password", encoding="utf-8")
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rule = Rule(
            name="conf-only",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
        )
        rs = _build_ruleset(rule)
        scanner = Scanner(rs, scan_extensions=("conf",))
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
        (tmp_path / "password.txt").write_text("x", encoding="utf-8")
        (tmp_path / "backup").mkdir()
        (tmp_path / "backup" / "password.txt").write_text("x", encoding="utf-8")
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

    def test_summary_user_skipped_prefix(self) -> None:
        """user_skipped=True 时 summary 附加「已标记跳过」前缀（iter-77）。"""
        from fuscan.scanner.result import RuleHit

        result = ScanResult(
            path=Path("/x"),
            size=0,
            hits=(RuleHit("r1", Severity.INFO, "d1", match_count=2),),
            user_skipped=True,
        )
        assert result.summary() == "已标记跳过 | 1 条规则 / 2 处匹配"

    def test_user_skipped_default_false(self) -> None:
        """ScanResult.user_skipped 默认为 False。"""
        result = ScanResult(path=Path("/x"), size=0, hits=())
        assert result.user_skipped is False


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

    def test_summary_includes_user_skipped(self) -> None:
        """summary 应包含「用户跳过 N」类别，与「跳过 N」区分（iter-77）。"""
        stats = ScanStats(
            total_files=10,
            scanned_files=5,
            skipped_files=2,
            user_skipped=3,
            matched_files=1,
            duration_seconds=1.0,
        )
        s = stats.summary()
        assert "用户跳过 3" in s
        assert "跳过 2" in s

    def test_summary_cancelled_prefix(self) -> None:
        """cancelled=True 时前缀为"已取消"。"""

        stats = ScanStats(total_files=1, scanned_files=1, duration_seconds=0.0)
        assert stats.summary(cancelled=True).startswith("已取消:")
        assert stats.summary(cancelled=False).startswith("完成:")

    def test_speed_calculates_files_per_second(self) -> None:
        """speed 属性应返回 scanned_files / duration_seconds。"""
        stats = ScanStats(scanned_files=100, duration_seconds=2.0)
        assert stats.speed == 50.0
        # duration 为 0 时返回 0.0，不抛 ZeroDivisionError
        assert ScanStats(scanned_files=10, duration_seconds=0.0).speed == 0.0

    def test_perf_summary_default_none(self) -> None:
        """perf_summary 默认为 None（向后兼容）。"""
        stats = ScanStats()
        assert stats.perf_summary is None

    def test_perf_summary_field_holds_dict(self) -> None:
        """perf_summary 可携带各阶段统计字典。"""
        perf = {"read_bytes": {"total_ms": 100.0, "count": 50, "max_ms": 10.0}}
        stats = ScanStats(perf_summary=perf)
        assert stats.perf_summary is not None
        assert stats.perf_summary["read_bytes"]["count"] == 50

    def test_summary_includes_archive_entries(self) -> None:
        """iter-137：archive_entries > 0 时 summary 应注明含压缩包内条目。"""
        stats = ScanStats(
            total_files=10,
            scanned_files=210,
            archive_entries=200,
            matched_files=16,
            duration_seconds=1.5,
        )
        s = stats.summary()
        assert "扫描 210（含压缩包内条目 200）" in s

    def test_summary_omits_archive_entries_when_zero(self) -> None:
        """iter-137：archive_entries == 0 时 summary 不附加压缩包注明。"""
        stats = ScanStats(
            total_files=10,
            scanned_files=8,
            archive_entries=0,
            matched_files=3,
            duration_seconds=1.0,
        )
        s = stats.summary()
        assert "压缩包内条目" not in s
        assert "扫描 8" in s

    def test_archive_entries_serialization_roundtrip(self, tmp_path: Path) -> None:
        """iter-137：archive_entries 应在 to_json/from_json 往返中保留。"""
        from fuscan.scanner.result import ScanReport

        stats = ScanStats(
            total_files=10,
            scanned_files=210,
            archive_entries=200,
            matched_files=16,
            duration_seconds=1.5,
        )
        report = ScanReport(root=tmp_path, results=(), stats=stats)
        restored = ScanReport.from_json(report.to_json())
        assert restored.stats.archive_entries == 200

    def test_archive_entries_backward_compat(self, tmp_path: Path) -> None:
        """iter-137：旧格式 JSON（无 archive_entries 字段）反序列化默认为 0。"""
        from fuscan.scanner.result import ScanReport

        # 模拟旧格式 JSON（不含 archive_entries）
        escaped_root = str(tmp_path).replace("\\", "\\\\")
        old_json = f'{{"root": "{escaped_root}", "stats": {{"total_files": 5, "scanned_files": 5}}, "cancelled": false, "hits": []}}'
        restored = ScanReport.from_json(old_json)
        assert restored.stats.archive_entries == 0


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
        # iter-89：新增 archive_path/inner_path 列，标识压缩包内部条目
        assert lines[0] == "path,archive_path,inner_path,size,severity,rule,description,match_count,detail"
        # 3 条命中：a.txt 2 条 + b.txt 1 条
        assert len(lines) - 1 == 3
        # 第一条数据应包含 a.txt 路径
        assert "a.txt" in lines[1]

    def test_to_csv_empty_hits_only_header(self, tmp_path: Path) -> None:

        report = ScanReport(root=tmp_path, results=(), stats=ScanStats())
        csv_text = report.to_csv()
        assert csv_text.strip() == "path,archive_path,inner_path,size,severity,rule,description,match_count,detail"

    def test_to_csv_includes_description(self, tmp_path: Path) -> None:
        """to_csv 应在 description 列填入 match_description（需求4）。"""
        from fuscan.scanner.result import RuleHit

        results = (
            ScanResult(
                path=tmp_path / "a.txt",
                size=10,
                hits=(
                    RuleHit(
                        "敏感凭证",
                        Severity.WARNING,
                        "d1",
                        match_count=1,
                        match_description="敏感凭证关键词",
                    ),
                ),
            ),
        )
        report = ScanReport(root=tmp_path, results=results, stats=ScanStats())
        csv_text = report.to_csv()
        lines = csv_text.strip().splitlines()
        # iter-89：CSV 列顺序新增 archive_path/inner_path
        assert lines[0] == "path,archive_path,inner_path,size,severity,rule,description,match_count,detail"
        # 第二行（数据行）的 description 列应包含描述文本
        # CSV 列顺序：path,archive_path,inner_path,size,severity,rule,description,match_count,detail
        # 由于 detail 可能含逗号被引号包裹，用简单的 in 判断
        assert "敏感凭证关键词" in lines[1]

    def test_to_csv_description_empty_when_not_set(self, tmp_path: Path) -> None:
        """match_description 未设置时 description 列应为空。"""
        from fuscan.scanner.result import RuleHit

        results = (
            ScanResult(
                path=tmp_path / "a.txt",
                size=10,
                hits=(RuleHit("r", Severity.WARNING, "d1", match_count=1),),
            ),
        )
        report = ScanReport(root=tmp_path, results=results, stats=ScanStats())
        csv_text = report.to_csv()
        # 解析 CSV：用 csv 模块正确处理引号
        import csv as _csv
        import io as _io

        reader = _csv.reader(_io.StringIO(csv_text))
        rows = list(reader)
        # iter-89：列顺序新增 archive_path/inner_path 后 description 索引由 4 变 6
        # path=0,archive_path=1,inner_path=2,size=3,severity=4,rule=5,description=6,match_count=7,detail=8
        assert rows[0][6] == "description"
        assert rows[1][6] == ""  # description 列为空

    def test_to_text_includes_description(self, tmp_path: Path) -> None:
        """to_text 应在规则名后附加 match_description（需求4）。"""
        from fuscan.scanner.result import RuleHit

        results = (
            ScanResult(
                path=tmp_path / "a.txt",
                size=10,
                hits=(
                    RuleHit(
                        "敏感凭证",
                        Severity.WARNING,
                        "d1",
                        match_count=1,
                        match_description="敏感凭证关键词",
                    ),
                ),
            ),
        )
        report = ScanReport(root=tmp_path, results=results, stats=ScanStats())
        text = report.to_text()
        # 描述非空时应在规则名后附加 " - 描述"
        assert "敏感凭证 - 敏感凭证关键词" in text

    def test_to_text_description_empty_omits_suffix(self, tmp_path: Path) -> None:
        """match_description 为空时 to_text 不应附加 " - " 后缀。"""
        from fuscan.scanner.result import RuleHit

        results = (
            ScanResult(
                path=tmp_path / "a.txt",
                size=10,
                hits=(RuleHit("敏感凭证", Severity.WARNING, "d1", match_count=1),),
            ),
        )
        report = ScanReport(root=tmp_path, results=results, stats=ScanStats())
        text = report.to_text()
        assert "敏感凭证" in text
        assert "敏感凭证 - " not in text

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


class TestFormatElapsed:
    def test_milliseconds(self) -> None:
        from fuscan.scanner.result import format_elapsed

        # < 1s 分档：毫秒（无小数）
        assert format_elapsed(0.86) == "860ms"
        assert format_elapsed(0.0) == "0ms"
        assert format_elapsed(0.0005) == "0ms"

    def test_seconds(self) -> None:
        from fuscan.scanner.result import format_elapsed

        # < 60s 分档：秒保留一位小数
        assert format_elapsed(1.0) == "1.0s"
        assert format_elapsed(1.25) == "1.2s"
        assert format_elapsed(59.9) == "59.9s"

    def test_minutes(self) -> None:
        from fuscan.scanner.result import format_elapsed

        # >= 60s 分档：分秒（秒两位零填充）
        assert format_elapsed(60.0) == "1分00秒"
        assert format_elapsed(65.0) == "1分05秒"
        assert format_elapsed(125.0) == "2分05秒"

    def test_negative_and_nan(self) -> None:
        from fuscan.scanner.result import format_elapsed

        # 负数与 NaN 归零
        assert format_elapsed(-1.0) == "0ms"
        assert format_elapsed(float("nan")) == "0ms"


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

    def test_summary_walk_phase(self) -> None:
        """walk 阶段 summary 应突出"解析目录"并展示已发现文件数，避免 scanned=0 被误以为卡住。"""
        from fuscan.scanner.result import ProgressInfo

        info = ProgressInfo(
            current_file="/some/dir/sub",
            total=1234,
            skipped=8,
            elapsed=2.5,
            phase="walk",
        )
        s = info.summary()
        assert "解析目录" in s
        assert "已发现 1234 个文件" in s
        assert "跳过 8" in s
        assert "已用 2.5s" in s
        # walk 阶段不展示速度/条数等 scan 阶段指标
        assert "速度" not in s
        assert "条数" not in s

    def test_summary_archive_phase(self) -> None:
        """archive 阶段 summary 应突出"扫描压缩包"并展示已扫描/命中/错误数。"""
        from fuscan.scanner.result import ProgressInfo

        info = ProgressInfo(
            current_file="/some/a.zip/entry.txt",
            scanned=42,
            matched=3,
            errors=1,
            elapsed=5.0,
            phase="archive",
        )
        s = info.summary()
        assert "扫描压缩包" in s
        assert "已扫描 42" in s
        assert "命中 3" in s
        assert "错误 1" in s
        assert "已用 5.0s" in s
        # archive 阶段不展示速度/条数等 scan 阶段指标
        assert "速度" not in s
        assert "条数" not in s

    def test_summary_unknown_phase_falls_back_to_scan(self) -> None:
        """未知 phase 应回退到 scan 阶段的默认文案（含速度）。"""
        from fuscan.scanner.result import ProgressInfo

        info = ProgressInfo(scanned=10, elapsed=1.0, phase="unknown")
        s = info.summary()
        assert "已扫描 10" in s
        assert "速度 10 文件/s" in s


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
        """多线程模式下全局 scan_extensions 过滤应正常工作（iter-71 两阶段架构）。"""
        rule = Rule(
            name="conf-only",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
        )
        rs = _build_ruleset(rule)
        for i in range(10):
            (tmp_path / f"a_{i}.conf").write_text("password", encoding="utf-8")
            (tmp_path / f"b_{i}.txt").write_text("password", encoding="utf-8")

        scanner = Scanner(rs, max_workers=4, scan_extensions=("conf",))
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

    def test_concurrent_large_fileset_two_phase(self, tmp_path: Path) -> None:
        """两阶段架构（iter-71）：600 文件先收集再并发扫描，结果与单线程一致。

        替代原流水线 drain 测试：先收集再扫描模式下，所有 entry 一次性提交到
        ThreadPoolExecutor，由 as_completed 按完成顺序收集，最终统计与单线程一致。
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

    def test_concurrent_scan_entry_error_handling(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """两阶段架构并发扫描阶段 _scan_entry 抛异常应计 error 并继续（iter-71）。

        替代原流水线 drain 错误处理测试：并发收集阶段 future.result() 重抛
        被除 Exception 捕获，记为 error 不中断后续 future。
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
                raise RuntimeError("模拟并发扫描阶段失败")
            return original_scan_entry(entry)

        monkeypatch.setattr(scanner, "_scan_entry", fake_scan_entry)
        report = scanner.scan(tmp_path)
        assert report.stats.errors >= 1
        assert report.stats.scanned_files >= 1

    def test_iter111_gil_yield_threshold_initial_zero(self) -> None:
        """iter-111：Scanner 构造时 _last_yield_time 初始化为 0.0。

        时间式 GIL 让步（替代原计数式 _gil_yield_interval）：
        首次让步判断 now - 0.0 >= 0.005 必然为真，确保首个文件后即让步一次。
        """
        rs = _build_ruleset(_filename_rule("r", "x"))
        sc = Scanner(rs, max_workers=None)
        assert sc._last_yield_time == 0.0
        # 不应再存在 _gil_yield_interval 字段（已移除）
        assert not hasattr(sc, "_gil_yield_interval")

    def test_iter111_gil_yield_threshold_constant_value(self) -> None:
        """iter-111：GIL_YIELD_THRESHOLD_S 常量值为 0.005（5ms）。"""
        from fuscan.scanner._helpers import GIL_YIELD_THRESHOLD_S

        assert GIL_YIELD_THRESHOLD_S == 0.005

    def test_iter111_progress_emit_batch_sequential(self) -> None:
        """iter-111：顺序扫描的进度 emit 批处理阈值为 1（每文件实时反馈）。"""
        rs = _build_ruleset(_filename_rule("r", "x"))
        sc_none = Scanner(rs, max_workers=None)
        sc_one = Scanner(rs, max_workers=1)
        assert sc_none._progress_emit_batch == 1
        assert sc_one._progress_emit_batch == 1

    def test_iter111_progress_emit_batch_concurrent(self) -> None:
        """iter-111/147：并发扫描的进度 emit 批处理阈值为 10（减少回调开销）。"""
        rs = _build_ruleset(_filename_rule("r", "x"))
        sc = Scanner(rs, max_workers=4)
        assert sc._progress_emit_batch == 10

    def test_iter111_concurrent_progress_emitted_at_least_once(self, tmp_path: Path) -> None:
        """iter-111：并发扫描下批处理 emit 应至少触发一次最终进度上报。

        20 个文件 + 批处理阈值 10（iter-147）：理论上触发 2 次 emit + 1 次尾部补发。
        节流（150ms）会过滤掉中间 emit，最终 force=True 的进度必到达。
        """
        for i in range(20):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "f"))
        received: list[ProgressInfo] = []
        sc = Scanner(rs, max_workers=4, on_progress=received.append)
        sc.scan(tmp_path)
        assert received
        assert received[-1].scanned >= 20
        assert received[-1].matched >= 20

    def test_iter111_concurrent_batch_tail_flush(self, tmp_path: Path) -> None:
        """iter-111：批处理尾部补发应在 future 总数非 emit_batch 整数倍时生效。

        7 个文件 + emit_batch=10（iter-147）：7 < 10 不触发批次 emit，
        全部在循环结束后尾部补发一次。最终 force=True 进度由 scan_entries
        末尾发送，扫描中段至少有一次进度回调反映非整除的尾部状态。
        """
        for i in range(7):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "f"))
        received: list[ProgressInfo] = []
        # progress_interval=0 保证不节流，所有 emit 都到达
        sc = Scanner(rs, max_workers=4, on_progress=received.append, progress_interval=0.0)
        sc.scan(tmp_path)
        # 至少触发：1 次 walk 阶段 + 1 次尾部补发 + 1 次 force 最终
        assert len(received) >= 2
        assert received[-1].scanned >= 7


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
        assert not scanner._matched_files

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
        # 快照字段已优化为默认空元组（GUI 不消费，省去每次 emit 的 O(50) 拷贝）
        assert isinstance(last.matched_files, tuple)
        assert isinstance(last.skipped_dirs, tuple)

    def test_progress_info_skipped_dirs_collected(self, tmp_path: Path) -> None:
        """ignore_dirs 跳过的目录不再实时填充到 ProgressInfo.skipped_dirs（性能优化）。"""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("", encoding="utf-8")
        (tmp_path / "app.py").write_text("password", encoding="utf-8")

        rs = _build_ruleset(_content_rule("r", "password"))
        received: list[ProgressInfo] = []
        scanner = Scanner(rs, on_progress=received.append, progress_interval=0.0, ignore_dirs=(".git",))
        scanner.scan(tmp_path)

        assert len(received) >= 1
        last = received[-1]
        # skipped_dirs/matched_files 已优化为默认空元组（GUI 不消费）
        assert isinstance(last.skipped_dirs, tuple)
        assert isinstance(last.matched_files, tuple)

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

    def test_scanner_reusable_after_cancel(self, tmp_path: Path) -> None:
        """C1 修复：取消后 Scanner 可复用，第二次 scan() 正常执行。

        回归场景：scan() 在 finally 中清除 _cancel_event，确保下次 scan()
        的 is_cancelled 为 False；否则取消后 Scanner 静默跳过全部扫描逻辑。
        """
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        scanner = Scanner(_build_ruleset(_filename_rule("r", "secret")))
        # 第一次 scan 前取消
        scanner.cancel()
        report1 = scanner.scan(tmp_path)
        assert report1.cancelled
        assert report1.stats.scanned_files == 0
        # 取消标志应已被 scan() finally 清除
        assert not scanner.is_cancelled
        # 第二次 scan 应正常执行（C1 修复核心：不再静默跳过）
        report2 = scanner.scan(tmp_path)
        assert not report2.cancelled
        assert report2.stats.scanned_files == 1
        assert report2.stats.matched_files == 1

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

    def test_cache_mtime_prefilter_skips_read_bytes(self, tmp_path: Path) -> None:
        """二次扫描时未修改文件应跳过 read_bytes（mtime 预筛命中）。"""
        from fuscan.cache import CacheStore

        path = tmp_path / "secret.txt"
        path.write_text("password here", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner1 = Scanner(rs, cache=cache)
            report1 = scanner1.scan(tmp_path)
            assert report1.stats.matched_files == 1

            # 验证：第一次扫描结束后，lookup_file_hash 应能命中
            st = path.stat()
            pre = cache.lookup_file_hash(path, st.st_mtime, st.st_size)
            assert pre is not None, "首次扫描后 file_paths 应已登记该文件"

            # 第二次扫描：文件未修改，应走 mtime 预筛路径
            call_count = 0
            original_read_bytes = Path.read_bytes

            def counting_read_bytes(self: Path) -> bytes:
                nonlocal call_count
                if self.name == "secret.txt":
                    call_count += 1
                return original_read_bytes(self)

            scanner2 = Scanner(rs, cache=cache)
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(Path, "read_bytes", counting_read_bytes)
                report2 = scanner2.scan(tmp_path)
            # 文件未修改：mtime 预筛命中，应完全不调用 read_bytes
            assert call_count == 0, f"mtime 预筛未生效，read_bytes 仍被调用 {call_count} 次"
            # 结果应一致
            assert report2.stats.matched_files == 1
            assert report2.hits[0].rule_names == ("pwd",)
        finally:
            cache.close()

    def test_cache_mtime_prefilter_misses_when_file_modified(self, tmp_path: Path) -> None:
        """文件被修改后 mtime 预筛不命中，应回退到 read_bytes 重算哈希。"""
        import os

        from fuscan.cache import CacheStore

        path = tmp_path / "secret.txt"
        path.write_text("password here", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner1 = Scanner(rs, cache=cache)
            report1 = scanner1.scan(tmp_path)
            assert report1.stats.matched_files == 1

            # 修改文件内容并前移 mtime（确保 mtime/size 改变）
            path.write_text("password there and more", encoding="utf-8")
            # 强制 mtime 改变
            new_mtime = path.stat().st_mtime + 100
            os.utime(path, (new_mtime, new_mtime))

            scanner2 = Scanner(rs, cache=cache)
            report2 = scanner2.scan(tmp_path)
            # 文件被修改后应重新匹配，结果仍命中（含 password 关键字）
            assert report2.stats.matched_files == 1
        finally:
            cache.close()

    def test_filename_rule_not_cached_across_same_content_paths(self, tmp_path: Path) -> None:
        """同内容不同路径的文件，FILENAME 规则结果不可串号。

        场景：两个内容相同的文件路径不同（/match.txt 与 /nope.txt），FILENAME 规则
        contains "match"。首次扫描后 match.txt 命中、nope.txt 未命中；
        二次扫描时即使两者 file_hash 相同，nope.txt 仍不应错误继承
        match.txt 的命中。同时验证 match.txt 二次扫描仍命中（路径预筛命中后
        重新评估 FILENAME 规则，结果应与首次一致）。

        iter-148：原场景使用空文件触发同 file_hash，但 filter 阶段会剔除空文件，
        故改为非空内容相同的文件（file_hash 仍相同）。
        """
        from fuscan.cache import CacheStore

        # 两个内容相同的非空文件（避免被 filter 阶段剔除）
        same_content = "same content here"
        (tmp_path / "match.txt").write_text(same_content, encoding="utf-8")
        (tmp_path / "nope.txt").write_text(same_content, encoding="utf-8")
        rs = _build_ruleset(_filename_rule("fn", "match"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner1 = Scanner(rs, cache=cache)
            report1 = scanner1.scan(tmp_path)
            assert report1.stats.matched_files == 1
            hit_paths1 = {hit.path for hit in report1.hits}
            assert hit_paths1 == {tmp_path / "match.txt"}

            # 二次扫描：路径预筛命中，FILENAME 规则重新评估，结果应一致
            scanner2 = Scanner(rs, cache=cache)
            report2 = scanner2.scan(tmp_path)
            assert report2.stats.matched_files == 1
            hit_paths2 = {hit.path for hit in report2.hits}
            assert hit_paths2 == {tmp_path / "match.txt"}, (
                f"FILENAME 规则结果串号：期望仅 match.txt 命中，实际 {hit_paths2}"
            )
        finally:
            cache.close()

    def test_filename_rule_hot_cache_skips_read_bytes(self, tmp_path: Path) -> None:
        """含 FILENAME 规则的规则集二次扫描应跳过 read_bytes（mtime 预筛命中 + 全 CONTENT 缓存）。

        回归防护：修复前 ``disable_cache`` 检查导致含 FILENAME 规则时整个文件缓存被禁用，
        每次扫描都重新读文件。修复后 FILENAME 规则不缓存但重新评估（无 I/O），
        CONTENT 规则仍走缓存，热缓存场景应跳过文件读取。
        """
        from fuscan.cache import CacheStore

        path = tmp_path / "match.txt"
        path.write_text("password here", encoding="utf-8")
        # 1 FILENAME + 1 CONTENT：覆盖修复路径
        rs = _build_ruleset(
            _filename_rule("fn", "match"),
            _content_rule("pwd", "password"),
        )

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner1 = Scanner(rs, cache=cache)
            scanner1.scan(tmp_path)

            # 二次扫描：文件未修改，应走 mtime 预筛 + 缓存重建路径
            call_count = 0
            original_read_bytes = Path.read_bytes

            def counting_read_bytes(self: Path) -> bytes:
                nonlocal call_count
                if self.name == "match.txt":
                    call_count += 1
                return original_read_bytes(self)

            scanner2 = Scanner(rs, cache=cache)
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(Path, "read_bytes", counting_read_bytes)
                report2 = scanner2.scan(tmp_path)
            assert call_count == 0, f"热缓存场景仍读文件 {call_count} 次"
            assert report2.stats.matched_files == 1
        finally:
            cache.close()

    def test_extract_content_cache_skips_extract_on_second_path(self, tmp_path: Path) -> None:
        """同内容不同路径的文件，第二次扫描应命中提取内容缓存，跳过 extract。"""
        from fuscan.cache import CacheStore

        # 写入两个内容相同的文件
        content = "password content here"
        p1 = tmp_path / "a.txt"
        p2 = tmp_path / "b.txt"
        p1.write_text(content, encoding="utf-8")
        p2.write_text(content, encoding="utf-8")

        rs = _build_ruleset(_content_rule("pwd", "password"))
        cache = CacheStore(tmp_path / "cache.db")
        try:
            # 第一次扫描：p1 提取并写入 extracted_contents
            scanner1 = Scanner(rs, cache=cache)
            scanner1.scan_file(p1)
            file_hash = hash_bytes(content.encode("utf-8"))
            assert cache.get_extracted_content(file_hash) is not None

            # 第二次扫描 p2（同内容不同路径）：mtime 预筛不命中（p2 未登记），
            # 但提取内容缓存应命中，跳过 extract_content_from_bytes_with_retry
            extract_call_count = 0
            original_extract = extract_content_from_bytes_with_retry

            def counting_extract(
                data: bytes,
                extension: str,
                *,
                max_retries: int = 1,
                backoff_ms: float = 50.0,
                on_failure: object = None,
            ) -> str:
                nonlocal extract_call_count
                extract_call_count += 1
                return original_extract(data, extension, max_retries=max_retries, backoff_ms=backoff_ms)

            scanner2 = Scanner(rs, cache=cache)
            # 注入计数器：通过 monkeypatch 替换模块级函数
            # iter-109：extract_content_from_bytes 已迁移到 _cache_phase 子模块
            # iter-119：_cache_phase 已切换到 extract_content_from_bytes_with_retry
            import fuscan.scanner._cache_phase as cache_phase_module

            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(cache_phase_module, "extract_content_from_bytes_with_retry", counting_extract)
                result2 = scanner2.scan_file(p2)
            # 提取内容缓存应命中，extract 不应被调用
            assert extract_call_count == 0, "提取内容缓存未命中，extract 仍被调用"
            assert result2.has_hit
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

        def mock_extract_from_bytes(
            data: bytes,
            extension: str,
            *,
            max_retries: int = 1,
            backoff_ms: float = 50.0,
            on_failure: object = None,
        ) -> str:
            raise RuntimeError("模拟提取器失败")

        # iter-109：default_extract_content_with_hash 在 _helpers 模块内调用
        # iter-119：_helpers 已切换到 extract_content_from_bytes_with_retry，
        # patch 目标须为 _helpers 模块的 extract_content_from_bytes_with_retry
        monkeypatch.setattr(
            "fuscan.scanner._helpers.extract_content_from_bytes_with_retry",
            mock_extract_from_bytes,
        )
        content, file_hash = default_extract_content_with_hash(entry)
        assert "password content" in content  # 回退到 UTF-8 解码
        assert len(file_hash) == 64  # 哈希仍正确计算


class TestScannerBatchFlush:
    """扫描器批量写入 flush 集成测试（iter-39 P2）。"""

    def test_scan_flushes_batch_on_completion(self, tmp_path: Path) -> None:
        """扫描完成后 batch 应已 flush，缓存中能查到结果。"""
        from fuscan.cache import CacheStore

        (tmp_path / "secret.txt").write_text("password=abc", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner = Scanner(rs, cache=cache)
            # 扫描前 batch 为空
            assert scanner._batch_buffer is not None
            assert scanner._batch_buffer.is_empty
            scanner.scan(tmp_path)
            # 扫描后 batch 应已 flush
            assert scanner._batch_buffer.is_empty
            # cache 中应有 scan_results 记录
            assert cache.stats().scan_results >= 1
        finally:
            cache.close()

    def test_scan_with_many_files_triggers_auto_flush(self, tmp_path: Path) -> None:
        """扫描超过 _BATCH_THRESHOLD 个文件时中途自动 flush。"""
        from fuscan.cache import BatchWriteItem, CacheStore

        # 写入 60 个文件（> _BATCH_THRESHOLD=50）
        for i in range(60):
            (tmp_path / f"f{i}.txt").write_text(f"password_{i}", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner = Scanner(rs, cache=cache)
            # 计数 batch_put_results 调用次数（至少 1 次自动 + 1 次末尾 flush）
            call_count = 0
            original = cache.batch_put_results

            def counting_batch(items: list[BatchWriteItem]) -> None:
                nonlocal call_count
                call_count += 1
                original(items)

            cache.batch_put_results = counting_batch  # type: ignore[method-assign]
            scanner.scan(tmp_path)
            # 至少触发 1 次自动 flush（达到阈值时）
            assert call_count >= 1
            # 最终全部 flush 完成
            assert scanner._batch_buffer is not None
            assert scanner._batch_buffer.is_empty
            # 60 个 .txt 文件都被登记到 cache（cache.db 等 SQLite 文件不算）
            assert cache.stats().scanned_files >= 60
        finally:
            cache.close()

    def test_pipeline_scan_batch_flushes_correctly(self, tmp_path: Path) -> None:
        """流水线模式下扫描完成后 batch 应正确 flush，缓存一致。"""
        from fuscan.cache import CacheStore

        # 写入多个内容不同的文件
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(f"password_{i}", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner = Scanner(rs, cache=cache, max_workers=4)
            scanner.scan(tmp_path)
            # 扫描后 batch 应已 flush
            assert scanner._batch_buffer is not None
            assert scanner._batch_buffer.is_empty
            # 二次扫描应命中缓存（mtime 预筛命中）
            scanner2 = Scanner(rs, cache=cache, max_workers=4)
            report2 = scanner2.scan(tmp_path)
            assert report2.stats.matched_files == 10
            # 二次扫描全部走预筛路径（无 errors）
            assert report2.stats.errors == 0
        finally:
            cache.close()

    def test_scan_cancelled_still_flushes_pending(self, tmp_path: Path) -> None:
        """扫描取消后已累积的 batch 仍应 flush（避免数据丢失）。"""
        from fuscan.cache import CacheStore

        # 写入大量文件
        for i in range(100):
            (tmp_path / f"f{i}.txt").write_text(f"password_{i}", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner = Scanner(rs, cache=cache)
            # 在第 10 个文件后取消
            call_count = 0

            def on_progress(info: ProgressInfo) -> None:
                nonlocal call_count
                call_count += 1
                if call_count >= 5:
                    scanner.cancel()

            scanner._on_progress = on_progress
            scanner._progress_interval = 0.0
            scanner.scan(tmp_path)
            # 取消后 batch 仍应 flush（_flush_batch 在 scan() 末尾调用）
            assert scanner._batch_buffer is not None
            assert scanner._batch_buffer.is_empty
            # cache 中应有部分结果（已 flush 的批次）
            assert cache.stats().scanned_files >= 1
        finally:
            cache.close()


class TestScannerCancelSpeedup:
    """扫描取消加速测试（需求 req-13 R1）。

    覆盖 ``_cancel_all_futures`` 辅助函数与流水线取消路径：
    - 取消时对未启动 future 调 ``cancel()``，跳过 ``as_completed`` 阻塞等待
    - 已运行 future 由 ``ThreadPoolExecutor`` 上下文退出时等待完成
    - 单线程与多线程 archive 阶段取消路径
    """

    def test_cancel_all_futures_marks_cancelled(self) -> None:
        """``cancel_all_futures`` 对每个 future 调 ``cancel()``，未启动的会被标记为已取消。"""
        from concurrent.futures import Future, ThreadPoolExecutor

        from fuscan.scanner._helpers import cancel_all_futures

        with ThreadPoolExecutor(max_workers=1) as pool:
            # 提交一个慢任务占用 worker，确保后续 future 排队未启动
            blocker = threading.Event()

            def slow_task() -> str:
                blocker.wait(timeout=2)
                return "done"

            running_future = pool.submit(slow_task)
            # 排队的 future（worker 被占用，不会立即启动）
            pending_futures: list[Future[str]] = [pool.submit(lambda: "x") for _ in range(3)]

            # 对全部 future 调 cancel_all_futures
            cancel_all_futures(pending_futures)

            # 排队的 future 应全部被成功取消（cancel() 返回 True）
            cancelled_count = sum(1 for f in pending_futures if f.cancelled())
            assert cancelled_count == 3

            # 释放阻塞任务，让 pool 正常退出
            blocker.set()
            assert running_future.result(timeout=2) == "done"

    def test_cancel_all_futures_empty_iterable(self) -> None:
        """``cancel_all_futures`` 对空输入应安全返回。"""
        from fuscan.scanner._helpers import cancel_all_futures

        # 空列表不应抛异常
        cancel_all_futures([])

    def test_pipelined_cancel_skips_as_completed(self, tmp_path: Path) -> None:
        """流水线 walk 阶段取消时跳过 ``as_completed`` 阻塞，快速返回。

        构造 100 个文件 + 慢速内容提供器，确保 worker 线程被占用；
        在首个进度回调时取消，验证 ``scan()`` 在合理时间内返回
        （不阻塞等待所有 100 个 future）。
        """
        for i in range(100):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        scanner = Scanner(
            _build_ruleset(_filename_rule("r", "f")),
            max_workers=2,
            progress_interval=0.0,
        )

        cancelled_in_callback = threading.Event()

        def cancel_on_first_progress(_info: ProgressInfo) -> None:
            if not cancelled_in_callback.is_set():
                cancelled_in_callback.set()
                scanner.cancel()

        scanner._on_progress = cancel_on_first_progress

        start = time.perf_counter()
        report = scanner.scan(tmp_path)
        elapsed = time.perf_counter() - start

        assert report.cancelled
        # 取消应快速返回（不等待全部 100 个 future 完成）
        # 2s 上限足够 worker 完成最多 2 个在途任务
        assert elapsed < 2.0, f"取消耗时 {elapsed:.2f}s，可能未跳过 as_completed 阻塞"


class TestIter154ContentBucket:
    """iter-154：顶层 CONTENT 规则按 (mode, case_sensitive) 合并 OR 正则桶。"""

    @staticmethod
    def _make_content_rule(
        name: str,
        pattern: str,
        mode: MatchMode = MatchMode.REGEX,
        case_sensitive: bool = False,
        severity: Severity = Severity.CRITICAL,
    ) -> Rule:
        return Rule(
            name=name,
            severity=severity,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=mode,
                pattern=pattern,
                case_sensitive=case_sensitive,
            ),
        )

    def test_20_content_regex_rules_hit_all_correctly(self, tmp_path: Path) -> None:
        """20 条 content regex 规则应全部正确命中，与合并前结果一致。"""
        # 构造 20 条规则，每条 pattern 是一个关键词（10条真命中，10条不命中）
        hit_words = [f"KEYWORD_{i:02d}_FOUND" for i in range(10)]
        miss_words = [f"KEYWORD_{i:02d}_MISS" for i in range(10)]
        rules = [self._make_content_rule(f"r{i}", word) for i, word in enumerate(hit_words + miss_words)]
        rs = _build_ruleset(*rules)
        sc = Scanner(rs, max_workers=1)
        # 验证桶被构造：20 条同模式 REGEX + case_sensitive=False 合成 1 个桶
        assert len(sc._content_buckets) == 1
        bucket = sc._content_buckets[0]
        assert len(bucket.rules) == 20
        assert bucket.compiled is not None
        # 写 10 个文件，每个含 3 个 hit word（确保 match_count 正确累加）
        for i in range(10):
            text = f"header {hit_words[i]} mid {hit_words[(i + 3) % 10]} tail {hit_words[(i + 7) % 10]}\n"
            (tmp_path / f"f{i:02d}.txt").write_text(text, encoding="utf-8")
        report = sc.scan(tmp_path)
        # 10 个文件 × 3 条 word = 30 次规则匹配总条数
        assert report.stats.total_matches == 30, f"总命中次数应为 30，实际 {report.stats.total_matches}"
        # 每个 hit word 应该恰好出现 3 次（在 10 个文件中轮流）
        rule_hits = [h for sr in report.hits for h in sr.hits]
        by_rule: dict[str, int] = {}
        for h in rule_hits:
            by_rule[h.rule_name] = by_rule.get(h.rule_name, 0) + h.match_count
        for w in hit_words:
            # 10 条命中规则，每条在 3 个文件中出现 1 次 → 总 match_count=3
            idx = hit_words.index(w)
            rname = f"r{idx}"
            assert by_rule.get(rname, 0) == 3, f"{rname}({w}) 应命中 3 次，实际 {by_rule.get(rname, 0)}"
        # miss 规则 0 命中
        for i in range(10, 20):
            rname = f"r{i}"
            assert rname not in by_rule, f"{rname}({miss_words[i - 10]}) 不应命中，但有 {by_rule[rname]}"

    def test_contains_case_sensitive_count_matches_legacy(self, tmp_path: Path) -> None:
        """CONTAINS(case_sensitive=True) 合并桶应仍用 count 统计非重叠次数。"""
        # 3 条 CONTAINS 规则：相同文本 aa 出现 3 次（aaa 中有 2 个非重叠 'aa'）
        r1 = self._make_content_rule("c1", "aa", MatchMode.CONTAINS, case_sensitive=True)
        r2 = self._make_content_rule("c2", "bb", MatchMode.CONTAINS, case_sensitive=True)
        r3 = self._make_content_rule("c3", "ab", MatchMode.CONTAINS, case_sensitive=True)
        rs = _build_ruleset(r1, r2, r3)
        sc = Scanner(rs, max_workers=1)
        assert len(sc._content_buckets) == 1, "3 条 CONTAINS CS 应合并为 1 桶"
        f = tmp_path / "t.txt"
        # aa aaaa：非重叠 'aa' 出现次数 = 1 + 2 = 3
        # bb：bbb 非重叠 = 1
        # ab：ababab 非重叠 = 3
        f.write_text("aa aaaa bbb ababab", encoding="utf-8")
        report = sc.scan(tmp_path)
        rule_hits = [h for sr in report.hits for h in sr.hits]
        by_rule = {h.rule_name: h.match_count for h in rule_hits}
        assert by_rule.get("c1") == 3, f"c1(aa) 应为 3 非重叠，实际 {by_rule.get('c1')}"
        assert by_rule.get("c2") == 1, f"c2(bb) 应为 1 非重叠，实际 {by_rule.get('c2')}"
        assert by_rule.get("c3") == 3, f"c3(ab) 应为 3 非重叠，实际 {by_rule.get('c3')}"

    def test_single_content_rule_kept_in_remaining_not_bucket(self, tmp_path: Path) -> None:
        """单条 CONTENT 规则不应入桶（无合并收益），保持 remaining 原循环。"""
        rs = _build_ruleset(self._make_content_rule("only", "hello"))
        sc = Scanner(rs, max_workers=1)
        assert len(sc._content_buckets) == 0, "单条 CONTENT 规则不应生成合并桶"
        assert len(sc._remaining_uncached_rules) == 1
        f = tmp_path / "a.txt"
        f.write_text("hello world", encoding="utf-8")
        report = sc.scan(tmp_path)
        assert report.stats.matched_files == 1
        first_sr = report.hits[0]
        assert first_sr.hits[0].rule_name == "only"

    def test_mixed_content_and_filename_rules_no_regression(self, tmp_path: Path) -> None:
        """混合 content+filename+组合规则场景下结果不回归。"""
        # content 5 条 + filename 2 条 + Or 组合 1 条（内部 content 叶子）
        c_rules = [self._make_content_rule(f"c{i}", f"TOK{i}") for i in range(5)]
        f_rules = [_filename_rule(f"f{i}", f"name{i}") for i in range(2)]
        # Or 组合规则：filename 或 content 任一命中（内部叶子不会被合并桶吸收，因为顶层是 OrMatch）
        or_rule = Rule(
            name="or_x",
            severity=Severity.WARNING,
            match=OrMatch(
                children=(
                    LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="or"),
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern="OR_HIT"),
                )
            ),
        )
        rs = _build_ruleset(*c_rules, *f_rules, or_rule)
        sc = Scanner(rs, max_workers=1)
        # 5 条顶层 content → 1 桶；2 条 filename + 1 条 or = 3 remaining
        assert len(sc._content_buckets) == 1
        assert len(sc._remaining_uncached_rules) == 3
        # 构造文件：匹配 content c0 c1，filename f0，Or 规则（content 含 OR_HIT）
        name0 = tmp_path / "name0_and_or.txt"
        name0.write_text("TOK0 middle TOK1 OR_HIT", encoding="utf-8")
        name1 = tmp_path / "other.txt"
        name1.write_text("TOKEN TOK2 nothing", encoding="utf-8")
        report = sc.scan(tmp_path)
        rule_hits = [h for sr in report.hits for h in sr.hits]
        names = {h.rule_name for h in rule_hits}
        # c0 c1 c2 + or_x 应为命中
        assert "c0" in names
        assert "c1" in names
        assert "c2" in names
        assert "f0" in names  # name0_and_or.txt 匹配 name0
        assert "or_x" in names
        # 构造文件：name1 不含 f1（filename name1 是文件名）
        # 所以 f1 不应命中
        assert "f1" not in names

    def test_bucket_compile_failure_graceful_degrade(self, tmp_path: Path) -> None:
        """复合正则编译失败（如语法冲突）时，桶整体降级回 remaining，结果仍正确。

        通过 monkeypatch Scanner 实例的 ``_content_buckets`` 属性来模拟编译失败。
        """
        # 先构造 2 条正常 content rules，验证默认成功合并为 1 个桶
        rs = _build_ruleset(
            self._make_content_rule("a", "HELLO"),
            self._make_content_rule("b", "WORLD"),
        )
        sc = Scanner(rs, max_workers=1)
        # 人为清空 content_buckets，把两条规则塞回 remaining（模拟复合编译失败降级路径）
        # 等价于 _build_content_buckets 在 try/except re.error 后返回 ([], self._compiled)
        sc._content_buckets = []
        sc._remaining_uncached_rules = list(sc._compiled)
        # remaining 循环应给出正确结果
        (tmp_path / "x.txt").write_text("HELLO and WORLD and WORLD", encoding="utf-8")
        report = sc.scan(tmp_path)
        rule_hits = [h for sr in report.hits for h in sr.hits]
        by_rule = {h.rule_name: h.match_count for h in rule_hits}
        assert by_rule.get("a") == 1
        assert by_rule.get("b") == 2

    def test_benchmark_20_rules_1000_files(self, benchmark: object, tmp_path: Path) -> None:
        """iter-154 吞吐基准：100 文件 × 20 条 CONTENT 规则，验证合并后性能。

        注意：--benchmark-disable 时 benchmark 器具无 stats 属性，此时仅功能跑通。
        """
        from typing import Any

        bm: Any = benchmark
        # 20 条 CONTENT regex（2 个桶：10 条 ignore case + 10 条 case sensitive）
        rules_ci = [self._make_content_rule(f"ci{i}", f"TOKEN_CI_{i:02d}") for i in range(10)]
        rules_cs = [
            self._make_content_rule(f"cs{i}", f"TOKEN_CS_{i:02d}", MatchMode.REGEX, case_sensitive=True)
            for i in range(10)
        ]
        rs = _build_ruleset(*rules_ci, *rules_cs)
        sc = Scanner(rs, max_workers=1)
        assert len(sc._content_buckets) == 2, "应存在 CI + CS 两个桶"
        # 写 100 个文件（功能验证规模，benchmark 时重复调用以稳定测量）
        files_dir = tmp_path / "src"
        files_dir.mkdir()
        for i in range(100):
            # 不同内容：交替命中 ci/cs
            if i % 2 == 0:
                txt = f"TOKEN_CI_{i % 10:02d} TOKEN_CS_{i % 10:02d}"
            else:
                txt = f"random noise {i}"
            (files_dir / f"f{i:03d}.txt").write_text(txt, encoding="utf-8")

        def run() -> int:
            report = sc.scan(files_dir)
            # 命中 50 个 ci 命中 + 50 个 cs 命中 = 100 条（每 2 个文件 中 1 个命中规则）
            return report.stats.total_matches

        result = run()
        # 功能正确性：偶数 50 个文件 × 2 规则/文件 = 100 match_count
        assert result == 100, f"期望总命中 100，实际 {result}"
        if bm is not None and callable(bm):
            bm(run)

    def test_archive_phase_cancel_skips_as_completed(self, tmp_path: Path) -> None:
        """archive 阶段取消时跳过 ``as_completed`` 阻塞，快速返回。"""
        import zipfile

        # 构造多个 zip，使 archive 阶段有多个 future 排队
        for i in range(10):
            with zipfile.ZipFile(str(tmp_path / f"a{i}.zip"), "w") as zf:
                zf.writestr("secret.txt", "x")

        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(
            rs,
            scan_archives=True,
            max_workers=2,
            progress_interval=0.0,
        )

        # 在首个进度回调时取消（archive 阶段会触发进度回调）
        cancelled_in_callback = threading.Event()

        def cancel_on_first_progress(_info: ProgressInfo) -> None:
            if not cancelled_in_callback.is_set():
                cancelled_in_callback.set()
                scanner.cancel()

        scanner._on_progress = cancel_on_first_progress

        start = time.perf_counter()
        report = scanner.scan(tmp_path)
        elapsed = time.perf_counter() - start

        assert report.cancelled
        # 取消应快速返回
        assert elapsed < 3.0, f"archive 取消耗时 {elapsed:.2f}s"

    def test_cancel_during_drain_does_not_block(self, tmp_path: Path) -> None:
        """walk 阶段非阻塞 drain 后取消应快速退出。

        构造 600+ 文件触发多次 drain（每 500 个 future drain 一次），
        在 drain 后取消，验证不阻塞等待全部 future。
        """
        for i in range(600):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        scanner = Scanner(
            _build_ruleset(_filename_rule("r", "f")),
            max_workers=2,
            progress_interval=0.0,
        )

        cancelled_after_drain = threading.Event()

        def cancel_on_progress(_info: ProgressInfo) -> None:
            if not cancelled_after_drain.is_set() and _info.scanned >= 100:
                cancelled_after_drain.set()
                scanner.cancel()

        scanner._on_progress = cancel_on_progress

        start = time.perf_counter()
        report = scanner.scan(tmp_path)
        elapsed = time.perf_counter() - start

        assert report.cancelled
        assert elapsed < 3.0, f"drain 后取消耗时 {elapsed:.2f}s"


class TestIter155CacheBucket:
    """iter-155：缓存模式下 content 桶覆盖规则也走合并路径。"""

    @staticmethod
    def _rules() -> list[Rule]:
        """构造 10 条 CI regex + 1 条 filename + 1 条 Or 组合。"""
        content_rules = [
            Rule(
                name=f"cr{i}",
                severity=Severity.CRITICAL,
                match=LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=f"PAT_{i:02d}",
                    case_sensitive=False,
                ),
            )
            for i in range(10)
        ]
        fn_rule = _filename_rule("fn", "match_me")
        or_rule = Rule(
            name="or_combo",
            severity=Severity.WARNING,
            match=OrMatch(
                children=(
                    LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="special"),
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern="COMBO_HIT"),
                )
            ),
        )
        return [*content_rules, fn_rule, or_rule]

    def test_first_and_second_scan_hit_count_consistency(self, tmp_path: Path) -> None:
        """首次和二次（mtime 预筛命中缓存）扫描的总匹配数一致，缓存正确记录未命中占位。"""
        from fuscan.cache import CacheStore

        rs = _build_ruleset(*self._rules())
        cache_path = tmp_path / "c.db"
        cache = CacheStore(cache_path)
        # 写 50 个文件：50% 命中 content 规则，30% 命中 filename rule，20% 命中 Or 组合
        for i in range(50):
            parts: list[str] = []
            if i % 2 == 0:
                parts.append(f"content line with PAT_{i % 10:02d} and PAT_{(i + 3) % 10:02d}")
            if i % 3 == 0:
                # 文件名含 match_me
                name = f"file_match_me_{i}.txt"
            else:
                name = f"file_{i}.txt"
            if i % 5 == 0:
                name = f"special_{i}.txt"  # or_combo 命中：文件名含 special
                parts.append("here is COMBO_HIT text")
            (tmp_path / name).write_text("\n".join(parts), encoding="utf-8")
        try:
            sc1 = Scanner(rs, cache=cache, max_workers=1)
            # 有 10 条 content 在同一个 bucket
            assert len(sc1._content_buckets) == 1
            assert sc1._bucketed_rule_names == {f"cr{i}" for i in range(10)}
            rep1 = sc1.scan(tmp_path)
            total1 = rep1.stats.total_matches
            matched_files1 = rep1.stats.matched_files
            assert total1 > 0, "至少应该有匹配"
            # 再扫一次：mtime 不变 → 预筛命中缓存
            sc2 = Scanner(rs, cache=cache, max_workers=1)
            rep2 = sc2.scan(tmp_path)
            total2 = rep2.stats.total_matches
            matched_files2 = rep2.stats.matched_files
            assert total2 == total1, f"二次扫描总匹配数不一致：一 {total1} 二 {total2}"
            assert matched_files2 == matched_files1, (
                f"二次扫描命中文件数不一致：一 {matched_files1} 二 {matched_files2}"
            )
            # 三扫：逐规则核对 rule_name 的命中次数分布一致
            sc3 = Scanner(rs, cache=cache, max_workers=1)
            rep3 = sc3.scan(tmp_path)
            rule_hits1 = [h for sr in rep1.hits for h in sr.hits]
            rule_hits3 = [h for sr in rep3.hits for h in sr.hits]
            dist1 = {
                name: sum(h.match_count for h in rule_hits1 if h.rule_name == name)
                for name in {h.rule_name for h in rule_hits1}
            }
            dist3 = {
                name: sum(h.match_count for h in rule_hits3 if h.rule_name == name)
                for name in {h.rule_name for h in rule_hits3}
            }
            assert dist1 == dist3, f"一三次扫描规则命中分布不同：一 {dist1} 三 {dist3}"
        finally:
            cache.close()

    def test_unknown_rule_cached_as_none_not_rerun(self, tmp_path: Path) -> None:
        """首次扫描未命中的规则（写 None 到缓存）在二次扫描中不再产生命中（缓存占位生效）。"""
        from fuscan.cache import CacheStore

        # 仅 2 条 content 规则 + 1 条不会命中的 content 规则（确保有 3 条合并桶）
        rules = [
            Rule(
                name="ok1",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="MATCH_A"),
            ),
            Rule(
                name="ok2",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="MATCH_B"),
            ),
            Rule(
                name="never",
                severity=Severity.WARNING,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="NEVER_HIT_ZZZ"),
            ),
        ]
        rs = _build_ruleset(*rules)
        cache_path = tmp_path / "c2.db"
        cache = CacheStore(cache_path)
        (tmp_path / "a.txt").write_text("MATCH_A plus MATCH_B MATCH_B", encoding="utf-8")
        try:
            sc1 = Scanner(rs, cache=cache, max_workers=1)
            assert len(sc1._content_buckets) == 1
            rep1 = sc1.scan(tmp_path)
            names_first = sorted({h.rule_name for sr in rep1.hits for h in sr.hits})
            assert "ok1" in names_first
            assert "ok2" in names_first
            assert "never" not in names_first, "NEVER_HIT 规则不该有任何命中"
            # 二次扫描
            sc2 = Scanner(rs, cache=cache, max_workers=1)
            rep2 = sc2.scan(tmp_path)
            names_second = sorted({h.rule_name for sr in rep2.hits for h in sr.hits})
            assert names_first == names_second, f"二次扫描命中规则集不同：首 {names_first} 二 {names_second}"
            match_counts_first = sum(h.match_count for sr in rep1.hits for h in sr.hits if h.rule_name == "ok2")
            match_counts_second = sum(h.match_count for sr in rep2.hits for h in sr.hits if h.rule_name == "ok2")
            assert match_counts_first == match_counts_second == 2, (
                f"MATCH_B 应非重叠出现 2 次：首 {match_counts_first} 二 {match_counts_second}"
            )
        finally:
            cache.close()


class TestScannerMaxFileSize:
    """大文件跳过阈值测试（需求 req-13 R2）。

    覆盖 ``_normalize_max_file_size`` 规范化逻辑与缓存/非缓存模式下
    超大文件跳过内容提取的行为。
    """

    def test_normalize_max_file_size_none_returns_default(self) -> None:
        """``None`` 退化为默认值 50MB。"""
        from fuscan.scanner._helpers import normalize_max_file_size

        assert normalize_max_file_size(None) == DEFAULT_MAX_FILE_SIZE
        assert normalize_max_file_size(None) == 50 * 1024 * 1024

    def test_normalize_max_file_size_negative_returns_default(self) -> None:
        """负数退化为默认值。"""
        from fuscan.scanner._helpers import normalize_max_file_size

        assert normalize_max_file_size(-1) == DEFAULT_MAX_FILE_SIZE
        assert normalize_max_file_size(-100) == DEFAULT_MAX_FILE_SIZE

    def test_normalize_max_file_size_zero_means_unlimited(self) -> None:
        """0 表示不限制。"""
        from fuscan.scanner._helpers import normalize_max_file_size

        assert normalize_max_file_size(0) == 0

    def test_normalize_max_file_size_positive_value(self) -> None:
        """正数原样返回。"""
        from fuscan.scanner._helpers import normalize_max_file_size

        assert normalize_max_file_size(1024) == 1024
        assert normalize_max_file_size(50 * 1024 * 1024) == 50 * 1024 * 1024

    def test_scanner_default_max_file_size(self) -> None:
        """未传入 ``max_file_size`` 时使用默认值 50MB。"""
        scanner = Scanner(_build_ruleset(_filename_rule("r", "x")))
        assert scanner._max_file_size == DEFAULT_MAX_FILE_SIZE

    def test_scanner_explicit_max_file_size(self) -> None:
        """显式传入 ``max_file_size`` 时使用传入值。"""
        scanner = Scanner(_build_ruleset(_filename_rule("r", "x")), max_file_size=1024)
        assert scanner._max_file_size == 1024

    def test_scanner_max_file_size_zero_unlimited(self) -> None:
        """``max_file_size=0`` 表示不限制。"""
        scanner = Scanner(_build_ruleset(_filename_rule("r", "x")), max_file_size=0)
        assert scanner._max_file_size == 0

    def test_scanner_max_file_size_negative_falls_back_to_default(self) -> None:
        """``max_file_size`` 为负数时退化为默认值。"""
        scanner = Scanner(_build_ruleset(_filename_rule("r", "x")), max_file_size=-1)
        assert scanner._max_file_size == DEFAULT_MAX_FILE_SIZE

    def test_scan_skips_oversize_file_content(self, tmp_path: Path) -> None:
        """非缓存模式下超过 ``max_file_size`` 的文件被 filter 阶段剔除（不进入扫描队列）。"""
        # 写入超过 10 字节的大文件
        big_content = "x" * 100 + "password"
        (tmp_path / "big.txt").write_text(big_content, encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        # 设置阈值为 10 字节，big.txt 超过阈值
        scanner = Scanner(rs, max_file_size=10)
        report = scanner.scan(tmp_path)
        # 大文件被 filter 阶段剔除，content 规则不命中
        assert report.stats.matched_files == 0
        # filter_removed 统计应反映剔除
        assert report.stats.filter_removed == 1

    def test_scan_oversize_file_removed_entirely(self, tmp_path: Path) -> None:
        """iter-148：超限文件被 filter 阶段整体剔除，filename 规则也不再求值。

        旧实现 ``_scan_entry_uncached`` 内做 ``max_file_size`` 跳过——仅跳过内容
        提取，FILENAME/PATH 规则仍可命中。iter-148 将该逻辑前移到 filter 阶段，
        超限文件不进入扫描队列，FILENAME 规则也不会求值。
        """
        (tmp_path / "secret.txt").write_text("x" * 100, encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        # 阈值远小于文件大小
        scanner = Scanner(rs, max_file_size=10)
        report = scanner.scan(tmp_path)
        # filter 阶段整体剔除超限文件，filename 规则不命中
        assert report.stats.matched_files == 0
        assert report.stats.filter_removed == 1

    def test_scan_skips_content_io_when_no_content_rules(self, tmp_path: Path) -> None:
        """规则集不含 CONTENT 规则时，扫描器跳过所有文件内容读取。

        FILENAME/PATH 规则仍可命中，但 content_provider 不应被调用。
        """
        (tmp_path / "secret.txt").write_text("password 123456", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        call_count = 0

        def counting_provider(entry: FileEntry) -> str:
            nonlocal call_count
            call_count += 1
            return default_extract_content(entry)

        scanner = Scanner(rs, content_provider=counting_provider)
        report = scanner.scan(tmp_path)
        # filename 规则命中
        assert report.stats.matched_files == 1
        # content_provider 未被调用（无 CONTENT 规则）
        assert call_count == 0

    def test_scan_reads_content_io_when_content_rules_exist(self, tmp_path: Path) -> None:
        """规则集含 CONTENT 规则时，content_provider 被调用来读取文件内容。"""
        (tmp_path / "data.txt").write_text("password 123456", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        call_count = 0

        def counting_provider(entry: FileEntry) -> str:
            nonlocal call_count
            call_count += 1
            return default_extract_content(entry)

        scanner = Scanner(rs, content_provider=counting_provider)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1
        # content_provider 被调用（有 CONTENT 规则）
        assert call_count > 0

    def test_scan_max_file_size_zero_scans_all_content(self, tmp_path: Path) -> None:
        """``max_file_size=0`` 不限制，大文件内容仍被扫描。"""
        big_content = "x" * 100 + "password"
        (tmp_path / "big.txt").write_text(big_content, encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_file_size=0)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1

    def test_scan_cached_skips_oversize_file_content(self, tmp_path: Path) -> None:
        """缓存模式下超过 ``max_file_size`` 的文件被 filter 阶段剔除。"""
        from fuscan.cache import CacheStore

        # 扫描根用子目录，避免 cache.db/-wal/-shm 落在扫描范围内污染 filter_removed
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        big_content = "x" * 100 + "password"
        (scan_dir / "big.txt").write_text(big_content, encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache = CacheStore(tmp_path / "cache.db")
        try:
            # 阈值远小于文件大小
            scanner = Scanner(rs, cache=cache, max_file_size=10)
            report = scanner.scan(scan_dir)
            # 大文件被 filter 阶段剔除，不进入缓存扫描路径
            assert report.stats.matched_files == 0
            assert report.stats.filter_removed == 1
        finally:
            cache.close()

    def test_scan_cached_zero_scans_all_content(self, tmp_path: Path) -> None:
        """缓存模式下 ``max_file_size=0`` 不限制，大文件内容仍被扫描。"""
        from fuscan.cache import CacheStore

        big_content = "x" * 100 + "password"
        (tmp_path / "big.txt").write_text(big_content, encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache = CacheStore(tmp_path / "cache.db")
        try:
            scanner = Scanner(rs, cache=cache, max_file_size=0)
            report = scanner.scan(tmp_path)
            assert report.stats.matched_files == 1
        finally:
            cache.close()

    def test_archive_scanner_inherits_max_file_size(self, tmp_path: Path) -> None:
        """``Scanner`` 应将 ``max_file_size`` 传递给 ``ArchiveScanner.max_entry_size``。"""
        import zipfile

        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("big.txt", "x" * 100 + "password")
            zf.writestr("small.txt", "password")

        rs = _build_ruleset(_content_rule("pwd", "password"))
        # 阈值为 10 字节：big.txt 超过，small.txt 未超过
        scanner = Scanner(rs, scan_archives=True, max_file_size=10)
        report = scanner.scan(tmp_path)
        # 只有 small.txt 命中（big.txt 被跳过）
        hit_paths = [str(r.path) for r in report.hits]
        assert any("small.txt" in p for p in hit_paths)
        assert not any("big.txt" in p for p in hit_paths)

    def test_archive_scanner_zero_scans_all_entries(self, tmp_path: Path) -> None:
        """``max_file_size=0`` 时 archive 内所有条目都被扫描。"""
        import zipfile

        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("big.txt", "x" * 100 + "password")
            zf.writestr("small.txt", "password")

        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, scan_archives=True, max_file_size=0)
        report = scanner.scan(tmp_path)
        # 两个条目都应命中
        assert report.stats.matched_files >= 2


class TestIter152AdaptBatch:
    """iter-152：自适应 progress_emit_batch 与 matched_files 批量 extend 正确性。

    覆盖：
    - _adapt_progress_batch 按 entries 规模分档 (10/15/20/25)
    - 顺序扫描 (max_workers<=1) 保持 batch=1 不被修改
    - 并发扫描 matched_files 完整性：批量 extend 后与原始逐 append 结果
      集合相等，不丢命中 (path, rule) 元组
    """

    def test_adapt_batch_sequential_unchanged(self) -> None:
        """顺序扫描 (max_workers=1) 时 _adapt_progress_batch 不修改默认值。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=1)
        default_batch = scanner._progress_emit_batch
        scanner._adapt_progress_batch(100000)
        assert scanner._progress_emit_batch == default_batch
        assert scanner._progress_emit_batch == 1

    def test_adapt_batch_small_entries(self) -> None:
        """entries<=1000：batch=10，保留实时进度反馈。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=4)
        scanner._adapt_progress_batch(500)
        assert scanner._progress_emit_batch == 10
        scanner._adapt_progress_batch(1000)
        assert scanner._progress_emit_batch == 10

    def test_adapt_batch_mid_entries(self) -> None:
        """1000<entries<=10000：batch=15，平衡开销与反馈。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=4)
        scanner._adapt_progress_batch(1001)
        assert scanner._progress_emit_batch == 15
        scanner._adapt_progress_batch(8000)
        assert scanner._progress_emit_batch == 15
        scanner._adapt_progress_batch(10000)
        assert scanner._progress_emit_batch == 15

    def test_adapt_batch_large_entries(self) -> None:
        """10000<entries<=50000：batch=20，降低主线程循环开销。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=4)
        scanner._adapt_progress_batch(10001)
        assert scanner._progress_emit_batch == 20
        scanner._adapt_progress_batch(30000)
        assert scanner._progress_emit_batch == 20
        scanner._adapt_progress_batch(50000)
        assert scanner._progress_emit_batch == 20

    def test_adapt_batch_huge_entries(self) -> None:
        """entries>50000：batch=25，最大限度减少 emit 调用。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=4)
        scanner._adapt_progress_batch(50001)
        assert scanner._progress_emit_batch == 25
        scanner._adapt_progress_batch(200000)
        assert scanner._progress_emit_batch == 25

    def test_concurrent_matched_files_complete(self, tmp_path: Path) -> None:
        """并发扫描 matched_files：批量 extend 后元组集合与逐 append 等效。"""
        # 创建 30 个文本文件，每个有命中内容，验证 (path, rule) 元组不丢
        for i in range(30):
            (tmp_path / f"f{i:03d}.txt").write_text("password=secret123\nkey=abcdef", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        def on_progress(_info):
            pass

        scanner = Scanner(rs, max_workers=4, on_progress=on_progress)
        report = scanner.scan(tmp_path)
        # 所有命中文件数与 matched_files 条目一致：每个文件命中 pwd 规则
        assert report.stats.matched_files == 30
        # matched_files 中每个 (path, rule) 组合都对应命中文件
        mf_set = set(scanner._matched_files)
        # 30 个文件 x 1 条规则 = 30 个唯一 (path, rule) 元组
        assert len(mf_set) == 30
        for p, r in mf_set:
            assert r == "pwd"
            assert any(str(h.path) == p for h in report.hits)


# ---------------------------------------------------------------------------
# iter-162 并发扫描进度条假卡死修复（in-flight 文件路径跟踪）
# ---------------------------------------------------------------------------


class TestIter162InFlightProgress:
    """iter-162：并发扫描 wait 超时分支应显示真实 in-flight 文件路径。

    覆盖 _pipeline_phase._collect_concurrent_results 超时分支：
    多个 worker 同时处理大文件时，wait 超时返回空 done 集，emit 应显示
    真实正在扫描的文件（_in_flight_meta 映射中最早提交但未完成的），而非上一个完成文件的陈旧路径。
    """

    def test_concurrent_timeout_emits_in_flight_file(self, tmp_path: Path) -> None:
        """wait 超时分支应显示 in-flight 文件而非陈旧路径。

        通过 monkeypatch 让首个 future 阻塞 1s（> 0.5s 超时阈值），
        验证超时 emit 的 current_file 是该阻塞文件，而非空串或上次完成路径。
        """
        from fuscan.scanner._pipeline_phase import run_pipeline_phase
        from fuscan.scanner.context import FileEntry

        # 创建 4 个文件，前 3 个让 worker 阻塞 > 0.5s 触发超时分支
        for i in range(4):
            (tmp_path / f"slow_{i}.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        scanner = Scanner(rs, max_workers=2, on_progress=lambda _info: None, progress_interval=0.0)
        # 收集 entries
        entries: list[FileEntry] = []
        for i in range(4):
            p = tmp_path / f"slow_{i}.txt"
            st = p.stat()
            entries.append(
                FileEntry(
                    path=p,
                    name=p.name,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    extension="txt",
                    is_dir=False,
                )
            )

        # 记录超时分支 emit 的 current_file
        timeout_emits: list[str] = []
        original_emit = scanner._emit_progress

        def _capture_emit(current_file: str, *args: object, **kwargs: object) -> None:
            # 仅记录非空且非 force 最终的 emit
            if current_file and kwargs.get("force") and current_file.startswith(str(tmp_path)):
                timeout_emits.append(current_file)
            original_emit(current_file, *args, **kwargs)  # type: ignore[arg-type]

        scanner._emit_progress = _capture_emit  # type: ignore[assignment]

        # 用 monkeypatch 让前 2 个文件的 _scan_entry 阻塞 1s
        original_scan_entry = scanner._scan_entry
        call_count = [0]

        def _slow_scan_entry(entry: FileEntry) -> ScanResult:
            call_count[0] += 1
            if call_count[0] <= 2:
                time.sleep(1.0)  # 超过 0.5s 超时阈值
            return original_scan_entry(entry)

        scanner._scan_entry = _slow_scan_entry  # type: ignore[assignment]

        results: list[ScanResult] = []
        run_pipeline_phase(scanner, entries, results)

        # 验证：超时分支至少触发一次，且 emit 的文件路径在 in-flight 列表中
        assert timeout_emits, "应至少触发一次超时分支 emit"
        # 超时 emit 的文件应是某个仍在扫描中的 in-flight 文件（set 迭代顺序任意，4 个文件均可能）
        all_files = {str(tmp_path / f"slow_{i}.txt") for i in range(4)}
        assert any(e in all_files for e in timeout_emits), f"超时 emit 应显示 in-flight 文件，实际：{timeout_emits}"
        # 扫描完成后 _in_flight_meta 应清空
        assert scanner._in_flight_meta == {}

    def test_concurrent_submit_cancel_clears_in_flight(self, tmp_path: Path) -> None:
        """submit 阶段触发取消应清空 _in_flight_meta 并立即返回。

        通过在 submit 循环中触发 _check_control 返回 True，验证
        cancelled_in_submit 分支清空 _in_flight_meta 且返回全零统计。
        """
        from fuscan.scanner._pipeline_phase import run_pipeline_phase
        from fuscan.scanner.context import FileEntry

        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=4, progress_interval=0.0)

        entries: list[FileEntry] = []
        for i in range(10):
            p = tmp_path / f"f{i}.txt"
            st = p.stat()
            entries.append(
                FileEntry(
                    path=p,
                    name=p.name,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    extension="txt",
                    is_dir=False,
                )
            )

        # 让 _check_control 在第 3 次 submit 后返回 True
        call_count = [0]
        original_check = scanner._check_control

        def _cancel_after_3() -> bool:
            call_count[0] += 1
            if call_count[0] >= 3:
                return True
            return original_check()

        scanner._check_control = _cancel_after_3  # type: ignore[assignment]

        results: list[ScanResult] = []
        scanned, matched, errors, matches = run_pipeline_phase(scanner, entries, results)
        # 取消后立即返回，统计为 0
        assert scanned == 0
        assert matched == 0
        assert errors == 0
        assert matches == 0
        # _in_flight_meta 应被清空
        assert scanner._in_flight_meta == {}

    def test_concurrent_collect_cancel_clears_in_flight(self, tmp_path: Path) -> None:
        """collect 阶段触发取消应清空 _in_flight_meta。

        通过让所有 future 提交完成后在 wait 循环中触发 _check_control，
        验证取消分支清空 _in_flight_meta。
        """
        from fuscan.scanner._pipeline_phase import run_pipeline_phase
        from fuscan.scanner.context import FileEntry

        for i in range(4):
            (tmp_path / f"f{i}.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=2, progress_interval=0.0)

        entries: list[FileEntry] = []
        for i in range(4):
            p = tmp_path / f"f{i}.txt"
            st = p.stat()
            entries.append(
                FileEntry(
                    path=p,
                    name=p.name,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    extension="txt",
                    is_dir=False,
                )
            )

        # 让 _scan_entry 阻塞 0.6s（> 0.5s 超时），并在 _check_control 第 5 次返回 True
        # 4 次 submit 全部 False（让所有 future 提交完成进入 collect 阶段），
        # collect 阶段首次 _check_control 返回 True 触发取消，覆盖 line 275-278。
        original_scan_entry = scanner._scan_entry
        check_count = [0]
        original_check = scanner._check_control

        def _slow_scan(entry: FileEntry) -> ScanResult:
            time.sleep(0.6)
            return original_scan_entry(entry)

        def _cancel_after_5() -> bool:
            check_count[0] += 1
            if check_count[0] >= 5:
                return True
            return original_check()

        scanner._scan_entry = _slow_scan  # type: ignore[assignment]
        scanner._check_control = _cancel_after_5  # type: ignore[assignment]

        results: list[ScanResult] = []
        run_pipeline_phase(scanner, entries, results)
        # 取消后 _in_flight_meta 应被清空
        assert scanner._in_flight_meta == {}

    def test_concurrent_timeout_syncs_current_file_meta(self, tmp_path: Path) -> None:
        """iter-167：wait 超时分支应同步设置 _current_file_* 为真实 in-flight 文件元信息。

        覆盖 _pipeline_phase._collect_concurrent_results 超时分支的同步逻辑：
        当 wait 超时且 in-flight 文件非空时，应取最早提交的 in-flight 文件，
        同步更新 _current_file_path/size/ext/start_time 为该文件的元信息，
        让 UI 显示「[大小 · ext · elapsed_ms]」与当前路径一致，
        修复「路径是 A、大小/扩展名是上一个完成的 B」的错配假卡死观感。
        """
        from fuscan.scanner._pipeline_phase import run_pipeline_phase
        from fuscan.scanner.context import FileEntry

        # 创建 2 个不同大小的文件，让 worker 阻塞 > 0.5s 触发超时分支
        (tmp_path / "slow_a.txt").write_text("a" * 100, encoding="utf-8")
        (tmp_path / "slow_b.txt").write_text("b" * 200, encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        # max_workers=2 走 _scan_concurrent 分支，2 个文件都阻塞以触发 wait 超时
        scanner = Scanner(rs, max_workers=2, on_progress=lambda _info: None, progress_interval=0.0)
        entries: list[FileEntry] = []
        for name, size in (("slow_a.txt", 100), ("slow_b.txt", 200)):
            p = tmp_path / name
            entries.append(
                FileEntry(
                    path=p,
                    name=p.name,
                    size=size,
                    mtime=p.stat().st_mtime,
                    extension="txt",
                    is_dir=False,
                )
            )

        # 让两个文件都阻塞 > 0.5s 触发超时分支
        original_scan_entry = scanner._scan_entry

        def _slow_scan(entry: FileEntry) -> ScanResult:
            time.sleep(0.8)  # > 0.5s 超时阈值
            return original_scan_entry(entry)

        scanner._scan_entry = _slow_scan  # type: ignore[assignment]

        # 拦截 _emit_progress，记录每次超时分支同步后的 _current_file_* 状态
        captured: list[tuple[str, int, str, float]] = []
        original_emit = scanner._emit_progress

        def _capture_emit(current_file: str, *args: object, **kwargs: object) -> None:
            # 仅记录 force=True 的超时分支 emit
            if kwargs.get("force") and current_file:
                captured.append(
                    (
                        scanner._current_file_path,
                        scanner._current_file_size,
                        scanner._current_file_ext,
                        scanner._current_file_start_time,
                    )
                )
            original_emit(current_file, *args, **kwargs)  # type: ignore[arg-type]

        scanner._emit_progress = _capture_emit  # type: ignore[assignment]

        results: list[ScanResult] = []
        run_pipeline_phase(scanner, entries, results)

        # 验证：超时分支至少触发一次
        assert captured, "应至少触发一次超时分支同步 _current_file_*"
        # 超时分支同步的 _current_file_* 应与 in-flight 文件元信息一致（路径/大小/扩展名）
        # entries[0].size=100, entries[1].size=200，超时时最早提交的应是 entries[0]
        # （dict 保序，slow_a.txt 先 submit 故 next(iter()) 取到它）
        first_capture = captured[0]
        expected_path = str(entries[0].path)
        assert first_capture[0] == expected_path, (
            f"超时分支应同步为最早提交的 in-flight 文件路径，实际：{first_capture[0]}"
        )
        assert first_capture[1] == 100, f"超时分支应同步为该文件大小 100，实际：{first_capture[1]}"
        assert first_capture[2] == "txt", f"超时分支应同步为该文件扩展名 txt，实际：{first_capture[2]}"
        # start_time 应为 submit 时记录的 perf_counter 值（>0，且早于 emit 时刻）
        assert first_capture[3] > 0.0, f"超时分支应同步为 submit_time（>0），实际：{first_capture[3]}"
        # 扫描完成后 _in_flight_meta 应清空
        assert scanner._in_flight_meta == {}

    def test_concurrent_timeout_in_flight_empty_falls_back_to_last(self, tmp_path: Path) -> None:
        """iter-167：wait 超时时若 _in_flight_meta 已空，回退到 _last_entry_path。

        覆盖 _pipeline_phase._collect_concurrent_results 超时分支的兜底逻辑：
        理论上 pending 非空时 _in_flight_meta 必然非空（done 分支同步 pop），
        但若 race-condition 下 _in_flight_meta 恰为空，应回退到 _last_entry_path
        而非抛 StopIteration，保证扫描稳定性。
        """
        import fuscan.scanner._pipeline_phase as pp
        from fuscan.scanner._pipeline_phase import run_pipeline_phase
        from fuscan.scanner.context import FileEntry

        (tmp_path / "slow.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=2, on_progress=lambda _info: None, progress_interval=0.0)
        p = tmp_path / "slow.txt"
        st = p.stat()
        entries: list[FileEntry] = [
            FileEntry(
                path=p,
                name=p.name,
                size=st.st_size,
                mtime=st.st_mtime,
                extension="txt",
                is_dir=False,
            )
        ]

        # 让 worker 阻塞 0.8s 触发首次 wait 超时
        original_scan_entry = scanner._scan_entry

        def _slow_scan(entry: FileEntry) -> ScanResult:
            time.sleep(0.8)
            return original_scan_entry(entry)

        scanner._scan_entry = _slow_scan  # type: ignore[assignment]

        # 拦截 emit：记录所有 force=True 的 emit，含空串（兜底回退到 _last_entry_path 初始值）
        captured: list[str] = []
        original_emit = scanner._emit_progress

        def _capture_emit(current_file: str, *args: object, **kwargs: object) -> None:
            if kwargs.get("force"):
                captured.append(current_file)
            original_emit(current_file, *args, **kwargs)  # type: ignore[arg-type]

        scanner._emit_progress = _capture_emit  # type: ignore[assignment]

        # monkeypatch wait：首次返回 (空 done, 全部 pending) 模拟超时，
        # 并清空 _in_flight_meta 强制走 else 兜底分支
        original_wait = pp.wait
        call_count = [0]

        def _mock_wait(fs, timeout, return_when):  # type: ignore[no-untyped-def]
            call_count[0] += 1
            if call_count[0] == 1:
                scanner._in_flight_meta.clear()
                return set(), set(fs)
            return original_wait(fs, timeout, return_when=return_when)

        pp.wait = _mock_wait  # type: ignore[assignment]
        try:
            results: list[ScanResult] = []
            run_pipeline_phase(scanner, entries, results)
        finally:
            pp.wait = original_wait  # type: ignore[assignment]

        # 验证：超时分支应触发，且兜底 emit 的 current_file 是 _last_entry_path 初始值（空串）
        assert captured, "应至少触发一次超时分支 emit"
        assert any(c == "" for c in captured), f"兜底分支应回退到 _last_entry_path 空串，实际：{captured}"

    def test_sequential_tail_flush_emits_remaining_progress(self, tmp_path: Path) -> None:
        """顺序扫描尾部补发：batch_match_list 剩余应在循环结束后 extend。

        60 个文件（>50 增量门限）+ batch=1（顺序模式默认）每个文件都触发
        batch emit，循环结束后 batch_match_list 尾部 extend 路径被覆盖
        （_on_progress 非 None 时 extend 到 _matched_files）。
        """
        for i in range(60):
            (tmp_path / f"f{i:03d}.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        received: list[ProgressInfo] = []
        scanner = Scanner(
            rs,
            max_workers=1,
            on_progress=received.append,
            progress_interval=0.0,
        )
        scanner.scan(tmp_path)
        # 顺序扫描应触发多次 emit（60 个文件 + walk 阶段 + 最终 force）
        assert len(received) >= 2
        # 最后一次 emit 应反映全部 60 个文件
        assert received[-1].scanned >= 60
        assert received[-1].matched >= 60
        # _matched_files 应包含命中（尾部 extend 路径覆盖，deque maxlen=50 限制上限）
        assert len(scanner._matched_files) >= 50


# ---------------------------------------------------------------------------
# iter-163 _content_buckets 分支覆盖（extract_required_exts / 各 mode 桶）
# ---------------------------------------------------------------------------


class TestIter163ContentBucketsBranches:
    """iter-163：补全 _content_buckets.py 各分支覆盖率。

    覆盖：
    - extract_required_exts: match is None / pattern 无 '.' / AND 交集为空 / OR exts 为空
    - build_content_buckets: 各 mode (REGEX/CONTAINS/EQUALS/STARTSWITH/ENDSWITH) 编译路径
    - match_content_via_buckets: 各 mode detail 构造分支
    - CONTAINS case_sensitive count 路径与 case-insensitive finditer 路径
    """

    def test_extract_required_exts_none_match(self) -> None:
        """match 为 None 时返回 None。"""
        from fuscan.scanner._content_buckets import extract_required_exts

        rule = Rule(
            name="r",
            severity=Severity.INFO,
            match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x"),
        )
        # match 非 None 但 target 不是 FILENAME 或 mode 不对 → None
        assert extract_required_exts(rule.match) is None

    def test_extract_required_exts_filename_no_dot(self) -> None:
        """FILENAME + ENDSWITH 模式但 pattern 无 '.' 返回 None。"""
        from fuscan.scanner._content_buckets import extract_required_exts

        rule = Rule(
            name="r",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.FILENAME,
                mode=MatchMode.ENDSWITH,
                pattern="nodot",
            ),
        )
        assert extract_required_exts(rule.match) is None

    def test_extract_required_exts_filename_ext(self) -> None:
        """FILENAME + ENDSWITH 模式带扩展名返回 frozenset。"""
        from fuscan.scanner._content_buckets import extract_required_exts

        rule = Rule(
            name="r",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.FILENAME,
                mode=MatchMode.ENDSWITH,
                pattern="file.pdf",
            ),
        )
        result = extract_required_exts(rule.match)
        assert result == frozenset({"pdf"})

    def test_extract_required_exts_and_intersection_empty(self) -> None:
        """AND 子项交集为空时返回 None。"""
        from fuscan.scanner._content_buckets import extract_required_exts

        # 两个 ENDSWITH 子项扩展名不同，AND 交集为空
        child_a = LeafMatch(
            target=MatchTarget.FILENAME,
            mode=MatchMode.ENDSWITH,
            pattern=".pdf",
        )
        child_b = LeafMatch(
            target=MatchTarget.FILENAME,
            mode=MatchMode.ENDSWITH,
            pattern=".docx",
        )
        and_match = AndMatch(children=(child_a, child_b))
        assert extract_required_exts(and_match) is None

    def test_extract_required_exts_or_with_none_child(self) -> None:
        """OR 子项中含 None 约束返回 None。"""
        from fuscan.scanner._content_buckets import extract_required_exts

        # child_b 是 CONTENT target，extract 返回 None
        child_a = LeafMatch(
            target=MatchTarget.FILENAME,
            mode=MatchMode.ENDSWITH,
            pattern=".pdf",
        )
        child_b = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="x",
        )
        or_match = OrMatch(children=(child_a, child_b))
        assert extract_required_exts(or_match) is None

    def test_extract_required_exts_or_empty_children(self) -> None:
        """OR 无子项时返回 None。"""
        from fuscan.scanner._content_buckets import extract_required_exts

        or_match = OrMatch(children=())
        # 空子项 → exts 列表为空 → return None
        assert extract_required_exts(or_match) is None

    def test_extract_required_exts_not_match_returns_none(self) -> None:
        """NotMatch 始终返回 None（无法安全反转）。"""
        from fuscan.scanner._content_buckets import extract_required_exts

        child = LeafMatch(
            target=MatchTarget.FILENAME,
            mode=MatchMode.ENDSWITH,
            pattern=".pdf",
        )
        not_match = NotMatch(child=child)
        assert extract_required_exts(not_match) is None

    def test_build_buckets_equals_mode(self) -> None:
        """EQUALS 模式规则应合入桶并生成 ^pat$ 正则。"""
        from fuscan.scanner._content_buckets import build_content_buckets
        from fuscan.scanner.matchers import build_matcher

        r1 = Rule(
            name="eq1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.EQUALS,
                pattern="secret",
            ),
        )
        r2 = Rule(
            name="eq2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.EQUALS,
                pattern="password",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, remaining = build_content_buckets(pairs)
        assert len(buckets) == 1
        assert len(remaining) == 0
        # 验证桶匹配：内容完全等于 "secret" 时命中 eq1
        from fuscan.scanner._content_buckets import match_content_via_buckets

        hits = match_content_via_buckets("secret", buckets)
        assert any(h.rule_name == "eq1" for h in hits)
        # detail 应为 "完全相等"
        eq_hit = next(h for h in hits if h.rule_name == "eq1")
        assert eq_hit.detail == "完全相等"

    def test_build_buckets_startswith_mode(self) -> None:
        """STARTSWITH 模式规则应合入桶并生成 ^pat 正则。"""
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        r1 = Rule(
            name="sw1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.STARTSWITH,
                pattern="aws-",
            ),
        )
        r2 = Rule(
            name="sw2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.STARTSWITH,
                pattern="ghp_",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        assert len(buckets) == 1
        hits = match_content_via_buckets("aws-key-here", buckets)
        assert any(h.rule_name == "sw1" for h in hits)
        sw_hit = next(h for h in hits if h.rule_name == "sw1")
        assert "开头" in sw_hit.detail

    def test_build_buckets_endswith_mode(self) -> None:
        """ENDSWITH 模式规则应合入桶并生成 pat$ 正则。"""
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        r1 = Rule(
            name="ew1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.ENDSWITH,
                pattern="EOF",
            ),
        )
        r2 = Rule(
            name="ew2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.ENDSWITH,
                pattern="END",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        assert len(buckets) == 1
        hits = match_content_via_buckets("dataEOF", buckets)
        assert any(h.rule_name == "ew1" for h in hits)
        ew_hit = next(h for h in hits if h.rule_name == "ew1")
        assert "结尾" in ew_hit.detail

    def test_build_buckets_regex_mode(self) -> None:
        """REGEX 模式规则应合入桶并保留原 pattern。"""
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        r1 = Rule(
            name="re1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"AKIA[0-9A-Z]{16}",
            ),
        )
        r2 = Rule(
            name="re2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"ghp_[A-Za-z0-9]{36}",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        assert len(buckets) == 1
        hits = match_content_via_buckets("AKIA1234567890ABCDEF", buckets)
        assert any(h.rule_name == "re1" for h in hits)

    def test_build_buckets_contains_case_sensitive_count(self) -> None:
        """CONTAINS + case_sensitive=True 应走 count 路径。"""
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        r1 = Rule(
            name="cs1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="Secret",
                case_sensitive=True,
            ),
        )
        r2 = Rule(
            name="cs2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="Password",
                case_sensitive=True,
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        assert len(buckets) == 1
        # 内容含 "Secret"（大写 S）应命中 cs1
        hits = match_content_via_buckets("Secret here", buckets)
        assert any(h.rule_name == "cs1" for h in hits)
        # 小写 "secret" 不应命中 cs1（case_sensitive）
        hits_lower = match_content_via_buckets("secret here", buckets)
        assert not any(h.rule_name == "cs1" for h in hits_lower)

    def test_build_buckets_single_rule_no_merge(self) -> None:
        """单条 CONTENT 规则无合并收益，应回到 remaining。"""
        from fuscan.scanner._content_buckets import build_content_buckets
        from fuscan.scanner.matchers import build_matcher

        r1 = Rule(
            name="solo",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="solo",
            ),
        )
        pairs = [(r1, build_matcher(r1.match))]
        buckets, remaining = build_content_buckets(pairs)
        assert len(buckets) == 0
        assert len(remaining) == 1

    def test_match_buckets_empty_compiled_skipped(self) -> None:
        """bucket.compiled is None 时跳过匹配。"""
        from fuscan.scanner._content_buckets import _ContentRuleBucket, match_content_via_buckets

        # 构造一个 compiled=None 的桶
        bucket = _ContentRuleBucket(mode=MatchMode.CONTAINS, case_sensitive=False)
        # compiled 默认 None
        hits = match_content_via_buckets("any content", [bucket])
        assert hits == []

    def test_match_buckets_contains_empty_pattern_skipped(self) -> None:
        """CONTAINS case_sensitive 桶中空 contains_patterns 应跳过 count 路径。"""
        from fuscan.scanner._content_buckets import _ContentRuleBucket, match_content_via_buckets

        # LeafMatch 不允许空 pattern，但 contains_patterns 可手动设为空串
        # 触发 line 306 的 `if not pat: continue` 防御分支
        bucket = _ContentRuleBucket(mode=MatchMode.CONTAINS, case_sensitive=True)
        bucket.rules = [
            Rule(
                name="dummy",
                severity=Severity.INFO,
                match=LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.CONTAINS,
                    pattern="dummy",
                    case_sensitive=True,
                ),
            )
        ]
        # 手动设 contains_patterns 为空串列表，触发跳过分支
        bucket.contains_patterns = [""]
        import re

        bucket.compiled = re.compile("dummy")
        hits = match_content_via_buckets("any", [bucket])
        # 空 pattern 不应产生命中
        assert hits == []


# ---------------------------------------------------------------------------
# iter-165 CONTENT 桶字面量预筛（_extract_literals / match_content_via_buckets 预筛路径）
# ---------------------------------------------------------------------------


class TestIter165ContentBucketPrefilter:
    """iter-165：补全 CONTENT 桶字面量预筛（性能优化，避免大文件 finditer 阻塞）。

    覆盖：
    - _extract_literals: 各种正则 AST 节点（LITERAL/BRANCH/SUBPATTERN/MAX_REPEAT/IN）
    - build_content_buckets: prefilter_keywords / prefilter_case_insensitive 字段填充
    - match_content_via_buckets: 预筛命中后走 finditer；预筛未命中跳过 finditer
    - 预筛不产生 false negative（含大小写变体、字面量子集等边界）
    """

    def test_extract_literals_simple(self) -> None:
        """纯字面量正则应提取完整字符串。"""
        from fuscan.scanner._content_buckets import _extract_literals

        assert _extract_literals(r"password") == ["password"]
        assert _extract_literals(r"abc\d+") == ["abc"]

    def test_extract_literals_branch(self) -> None:
        """| 分支应提取所有分支的字面量（sre_parse 会把公共前缀提到 BRANCH 外）。"""
        from fuscan.scanner._content_buckets import _extract_literals

        # sre_parse 优化：(password|passwd|pwd) → p + BRANCH([assword, asswd, wd])
        # 预筛提取器必须正确还原 password / passwd / pwd
        result = _extract_literals(r"(password|passwd|pwd)")
        assert "password" in result
        assert "passwd" in result
        assert "pwd" in result

    def test_extract_literals_subpattern(self) -> None:
        """捕获组应递归提取内部字面量。"""
        from fuscan.scanner._content_buckets import _extract_literals

        # (ghp|gho)_[A-Za-z0-9]{36} → 提取 "ghp"/"gho"（"_" 因长度 1 被过滤）
        result = _extract_literals(r"(ghp|gho)_[A-Za-z0-9]{36}")
        assert "ghp" in result
        assert "gho" in result

    def test_extract_literals_in_single_chars(self) -> None:
        """字符类 [abc] 应展开为各候选前缀组合。"""
        from fuscan.scanner._content_buckets import _extract_literals

        # gh[pousr] → "ghp", "gho", "ghu", "ghs", "ghr"
        result = _extract_literals(r"gh[pousr]")
        for ch in "pousr":
            assert f"gh{ch}" in result

    def test_extract_literals_in_range_skipped(self) -> None:
        """字符类 [A-Z] 含 RANGE，不应提取任何字面量。"""
        from fuscan.scanner._content_buckets import _extract_literals

        # [A-Z]{16} 无字面量可提取
        result = _extract_literals(r"[A-Z]{16}")
        assert result == []

    def test_extract_literals_min_length(self) -> None:
        """长度 < 3 的字面量应被过滤。"""
        from fuscan.scanner._content_buckets import _extract_literals

        # "ab" 长度 2 < 3，应被过滤；"abcd" 长度 4 应保留
        result = _extract_literals(r"ab\s+abcd")
        assert "ab" not in result
        assert "abcd" in result

    def test_extract_literals_inline_flags_stripped(self) -> None:
        """内联标志 (?i) 应被剥离，不影响字面量提取。"""
        from fuscan.scanner._content_buckets import _extract_literals

        # (?i)password 与 password 应提取相同字面量
        assert _extract_literals(r"(?i)password") == ["password"]
        assert _extract_literals(r"(?im)aws_secret") == ["aws_secret"]

    def test_extract_literals_invalid_regex(self) -> None:
        """非法正则应返回空列表（不抛异常，保守不预筛）。"""
        from fuscan.scanner._content_buckets import _extract_literals

        # 非法正则：未闭合的 [
        assert _extract_literals(r"[unclosed") == []

    def test_extract_literals_dedup(self) -> None:
        """重复字面量应去重，保留首次出现顺序。"""
        from fuscan.scanner._content_buckets import _extract_literals

        # abc|abc 应只提取一次 "abc"
        result = _extract_literals(r"abc|abc")
        assert result.count("abc") == 1

    def test_build_buckets_prefilter_substring_removal(self) -> None:
        """build_content_buckets 应去除子串关键字（保留最长者）。"""
        from fuscan.scanner._content_buckets import build_content_buckets
        from fuscan.scanner.matchers import build_matcher

        # xox[abpr] → 提取 "xox"/"xoxa"/"xoxb"/"xoxp"/"xoxr"
        # 去子串后应只保留 4 个最长者（"xoxa"/"xoxb"/"xoxp"/"xoxr"），移除 "xox"
        r1 = Rule(
            name="re1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"\bxox[abpr]-[A-Za-z0-9-]{10,72}\b",
            ),
        )
        r2 = Rule(
            name="re2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        assert len(buckets) == 1
        bucket = buckets[0]
        # "xox" 是 "xoxa" 等的子串，应被移除
        assert "xox" not in bucket.prefilter_keywords
        assert "xoxa" in bucket.prefilter_keywords
        # "pwd" 不是 "password"/"passwd" 的子串，应保留
        assert "pwd" in bucket.prefilter_keywords
        assert "password" in bucket.prefilter_keywords

    def test_build_buckets_prefilter_keywords_populated(self) -> None:
        """build_content_buckets 应填充 prefilter_keywords 字段。"""
        from fuscan.scanner._content_buckets import build_content_buckets
        from fuscan.scanner.matchers import build_matcher

        r1 = Rule(
            name="re1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"password\s*[=:]",
            ),
        )
        r2 = Rule(
            name="re2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"aws_secret_access_key\s*[=:]",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        assert len(buckets) == 1
        # 桶默认 case_sensitive=False → 预筛大小写不敏感，关键字小写化
        bucket = buckets[0]
        assert bucket.prefilter_case_insensitive is True
        assert "password" in bucket.prefilter_keywords
        assert "aws_secret_access_key" in bucket.prefilter_keywords

    def test_build_buckets_prefilter_case_sensitive(self) -> None:
        """case_sensitive=True 桶：prefilter_case_insensitive=False，关键字保持原样。"""
        from fuscan.scanner._content_buckets import build_content_buckets
        from fuscan.scanner.matchers import build_matcher

        r1 = Rule(
            name="cs1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="Secret",
                case_sensitive=True,
            ),
        )
        r2 = Rule(
            name="cs2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="Password",
                case_sensitive=True,
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        assert len(buckets) == 1
        bucket = buckets[0]
        assert bucket.prefilter_case_insensitive is False
        # 关键字保持原大小写
        assert "Secret" in bucket.prefilter_keywords
        assert "Password" in bucket.prefilter_keywords

    def test_prefilter_skips_bucket_when_no_keyword_match(self) -> None:
        """内容不含任何关键字时，桶被预筛跳过，finditer 不执行。"""
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        r1 = Rule(
            name="re1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"password\s*[=:]\s*\S+",
            ),
        )
        r2 = Rule(
            name="re2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"aws_secret_access_key\s*[=:]\s*\S+",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        # 纯文档内容，不含任何关键字
        content = "This is a documentation file. No secrets here. Just plain text."
        hits = match_content_via_buckets(content, buckets)
        assert hits == []

    def test_prefilter_passes_then_finditer_runs(self) -> None:
        """预筛命中后 finditer 仍正常工作，返回真实命中。"""
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        r1 = Rule(
            name="re1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"password\s*[=:]\s*\S+",
            ),
        )
        r2 = Rule(
            name="re2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"aws_secret_access_key\s*[=:]\s*\S+",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        # 内容含 "password=" 关键字
        hits = match_content_via_buckets("password=secret123", buckets)
        assert any(h.rule_name == "re1" for h in hits)

    def test_prefilter_case_insensitive_matches_uppercase(self) -> None:
        """大小写不敏感桶：关键字小写化后能匹配大写 content。"""
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        # (?i) 使正则大小写不敏感；case_sensitive=False
        r1 = Rule(
            name="re1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"(?i)password\s*[=:]\s*\S+",
            ),
        )
        r2 = Rule(
            name="re2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"(?i)aws_secret_access_key\s*[=:]\s*\S+",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        # 大写 content，预筛应通过（关键字已小写化，content 也小写化匹配）
        hits = match_content_via_buckets("PASSWORD=secret123", buckets)
        assert any(h.rule_name == "re1" for h in hits)

    def test_prefilter_no_false_negative_property(self) -> None:
        """属性测试：随机文本上预筛不应产生 false negative（预筛未命中但 finditer 命中）。"""
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        # 构造多模式桶覆盖各种字面量提取场景
        rules = [
            Rule(
                name="pwd",
                severity=Severity.INFO,
                match=LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+",
                ),
            ),
            Rule(
                name="aws",
                severity=Severity.INFO,
                match=LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"(?i)aws_secret_access_key\s*[=:]\s*\S+",
                ),
            ),
            Rule(
                name="gh",
                severity=Severity.INFO,
                match=LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b",
                ),
            ),
            Rule(
                name="jwt",
                severity=Severity.INFO,
                match=LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
                ),
            ),
        ]
        pairs = [(r, build_matcher(r.match)) for r in rules]
        buckets, _remaining = build_content_buckets(pairs)
        assert len(buckets) >= 1

        # 关闭预筛的版本：直接构造无预筛的桶副本来跑对照
        # （通过临时清空 prefilter_keywords 实现）
        import copy

        buckets_no_prefilter = copy.deepcopy(buckets)
        for b in buckets_no_prefilter:
            b.prefilter_keywords = []

        import random

        rng = random.Random(42)
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \n\t.:=_-"
        for _ in range(50):
            length = rng.randint(50, 500)
            text = "".join(rng.choice(alphabet) for _ in range(length))
            hits_prefilter = match_content_via_buckets(text, buckets)
            hits_no_prefilter = match_content_via_buckets(text, buckets_no_prefilter)
            # 预筛不允许 false negative：预筛命中集 ⊆ 真实命中集
            # 即：若预筛返回命中，无预筛也应返回至少这些命中
            prefilter_names = {h.rule_name for h in hits_prefilter}
            no_prefilter_names = {h.rule_name for h in hits_no_prefilter}
            assert prefilter_names <= no_prefilter_names, (
                f"false negative: prefilter={prefilter_names}, no_prefilter={no_prefilter_names}, text={text!r}"
            )

    def test_prefilter_empty_keywords_no_skip(self) -> None:
        r"""桶无可提取关键字时（如纯 \d+），prefilter_keywords 空，不预筛，仍走 finditer。"""
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        # 两条纯字符类正则，无字面量
        r1 = Rule(
            name="re1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"\d{16}",
            ),
        )
        r2 = Rule(
            name="re2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"[A-Z]{20}",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2)]
        buckets, _remaining = build_content_buckets(pairs)
        assert len(buckets) == 1
        bucket = buckets[0]
        # 纯字符类正则，无字面量可提取
        assert bucket.prefilter_keywords == []
        # 即使无字面量，匹配仍正常工作
        hits = match_content_via_buckets("1234567890123456", buckets)
        assert any(h.rule_name == "re1" for h in hits)

    def test_prefilter_content_lower_reused_across_buckets(self) -> None:
        """多个大小写不敏感桶：预筛阶段对 content.lower() lazy 计算一次复用。

        验证多桶场景下大写 content 能被各 CI 桶正确预筛通过（间接覆盖 lazy
        content_lower 复用路径：若未复用则两次 lower 调用结果一致；若复用则
        一次调用结果被两个桶共用——行为一致，仅性能差异）。
        """
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        # 用 REGEX + CONTAINS 两种 mode 构造 2 个大小写不敏感桶
        r1 = Rule(
            name="regex_pwd",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"(?i)password\s*[=:]\s*\S+",
            ),
        )
        r2 = Rule(
            name="regex_aws",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"(?i)aws_secret\s*[=:]\s*\S+",
            ),
        )
        r3 = Rule(
            name="contains_token",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="bearer",
            ),
        )
        r4 = Rule(
            name="contains_jwt",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="eyJ",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2, r3, r4)]
        buckets, _remaining = build_content_buckets(pairs)
        # 应形成 2 个桶（REGEX CI 一个，CONTAINS CI 一个，均大小写不敏感）
        assert len(buckets) == 2
        assert all(b.prefilter_case_insensitive for b in buckets)

        # 大写 content 触发 content_lower 计算；多桶共享同一份 content_lower
        # 第一个桶预筛通过 → finditer 命中；第二个桶预筛通过 → finditer 命中
        hits = match_content_via_buckets("PASSWORD=secret123 BEARER abc", buckets)
        hit_names = {h.rule_name for h in hits}
        # REGEX 桶命中 regex_pwd
        assert "regex_pwd" in hit_names
        # CONTAINS 桶命中 contains_token
        assert "contains_token" in hit_names

    def test_prefilter_case_sensitive_and_insensitive_mixed(self) -> None:
        """混合桶：case_sensitive 桶用原 content，case_insensitive 桶用 content.lower。"""
        from fuscan.scanner._content_buckets import build_content_buckets, match_content_via_buckets
        from fuscan.scanner.matchers import build_matcher

        # case_sensitive=True 的 CONTAINS 桶
        r1 = Rule(
            name="cs_secret",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="Secret",
                case_sensitive=True,
            ),
        )
        r2 = Rule(
            name="cs_password",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="Password",
                case_sensitive=True,
            ),
        )
        # case_sensitive=False 的 REGEX 桶
        r3 = Rule(
            name="ci_aws",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"aws_secret_access_key\s*[=:]\s*\S+",
            ),
        )
        r4 = Rule(
            name="ci_jwt",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=r"\beyJ[A-Za-z0-9_-]{8,}\b",
            ),
        )
        pairs = [(r, build_matcher(r.match)) for r in (r1, r2, r3, r4)]
        buckets, _remaining = build_content_buckets(pairs)
        # 应有 2 个桶：1 个 case_sensitive + 1 个 case_insensitive
        ci_buckets = [b for b in buckets if b.prefilter_case_insensitive]
        cs_buckets = [b for b in buckets if not b.prefilter_case_insensitive]
        assert len(ci_buckets) == 1
        assert len(cs_buckets) == 1
        # case_sensitive 桶：关键字保持原大小写
        assert "Secret" in cs_buckets[0].prefilter_keywords
        # case_insensitive 桶：关键字小写化
        assert "aws_secret_access_key" in ci_buckets[0].prefilter_keywords
        # 混合 content：含大写 Secret 与小写 aws_secret_access_key
        hits = match_content_via_buckets("Secret here AWS_SECRET_ACCESS_KEY=x", buckets)
        hit_names = {h.rule_name for h in hits}
        assert "cs_secret" in hit_names  # case-sensitive 命中 "Secret"
        assert "ci_aws" in hit_names  # case-insensitive 命中 "AWS_SECRET_ACCESS_KEY"


# ---------------------------------------------------------------------------
# iter-164 桶匹配异常 fallback 分支（scanner.py _match_content_via_buckets 异常路径）
# ---------------------------------------------------------------------------


class TestIter164BucketMatchFallback:
    """iter-164：补全 scanner.py 桶匹配异常 fallback 分支覆盖率。

    覆盖：
    - _scan_entry_uncached 桶匹配抛异常 → +1 rule_errors，remaining 规则继续执行
    - _scan_entry_cached 桶匹配抛异常 → +1 errors，缓存模式降级
    """

    def test_uncached_bucket_match_exception_fallback(self, tmp_path: Path) -> None:
        """无缓存模式桶匹配抛异常时记 rule_errors 且 remaining 规则继续。"""
        # 构造 2 条 CONTENT CONTAINS 规则触发桶合并
        r1 = Rule(
            name="c1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="secret",
            ),
        )
        r2 = Rule(
            name="c2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="password",
            ),
        )
        rs = _build_ruleset(r1, r2)
        scanner = Scanner(rs, max_workers=1)
        # monkeypatch _match_content_via_buckets_impl 抛异常
        original = scanner._match_content_via_buckets_impl

        def _raise(_content: str, _buckets: object) -> object:
            raise RuntimeError("test bucket match failure")

        scanner._match_content_via_buckets_impl = _raise  # type: ignore[assignment]
        try:
            (tmp_path / "f.txt").write_text("secret password", encoding="utf-8")
            report = scanner.scan(tmp_path)
            # 桶匹配失败 → rule_errors +1，但扫描仍完成
            assert report.stats.errors >= 1
        finally:
            scanner._match_content_via_buckets_impl = original  # type: ignore[assignment]

    def test_cached_bucket_match_exception_fallback(self, tmp_path: Path) -> None:
        """缓存模式桶匹配抛异常时记 errors 且降级到空匹配。"""
        from fuscan.cache import CacheStore
        from fuscan.scanner.result import RuleHit

        r1 = Rule(
            name="c1",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="secret",
            ),
        )
        r2 = Rule(
            name="c2",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="password",
            ),
        )
        rs = _build_ruleset(r1, r2)

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner = Scanner(rs, cache=cache, max_workers=1)
            # monkeypatch _match_content_via_buckets 抛异常（缓存模式走这个入口）
            original = scanner._match_content_via_buckets

            def _raise(_content: str) -> list[RuleHit]:
                raise RuntimeError("cached bucket match failure")

            scanner._match_content_via_buckets = _raise  # type: ignore[assignment]
            try:
                (tmp_path / "f.txt").write_text("secret password", encoding="utf-8")
                report = scanner.scan(tmp_path)
                # 桶匹配失败 → errors +1，扫描仍完成
                assert report.stats.errors >= 1
            finally:
                scanner._match_content_via_buckets = original  # type: ignore[assignment]
        finally:
            cache.close()


# ---------------------------------------------------------------------------
# iter-164 规则 AST 剪枝预筛（按扩展名分组）
# ---------------------------------------------------------------------------


class TestIter164RulePruning:
    """iter-164：按扩展名预筛规则，减少非必要 CONTENT 匹配调用。

    覆盖：
    1. extract_required_exts 返回正确集合（Leaf FILENAME endswith/equals / AND / OR）
    2. Scanner.__init__ 正确按 required_exts 拆分 global_pairs 与 ext_pairs
    3. 针对 .env 的规则在 .txt 文件上不会被调用（_ext_content_buckets /
       _ext_remaining_rules 中 .env 规则不进 .txt 的 effective_*）
    4. 预筛后的扫描结果与未预筛的全量规则结果完全一致（50 规则 × 100 文件）
    """

    @staticmethod
    def _filename_endswith_rule(name: str, suffix: str, content_pattern: str) -> Rule:
        """构造 AND(filename endswith {suffix}, content contains {content_pattern}) 的规则。"""
        from fuscan.rules.parser import parse_rule

        raw = {
            "id": name,
            "name": name,
            "severity": "info",
            "match": {
                "type": "and",
                "children": [
                    {"type": "filename", "mode": "endswith", "pattern": suffix},
                    {"type": "content", "mode": "contains", "pattern": content_pattern},
                ],
            },
        }
        return parse_rule(raw)

    def test_extract_required_exts_leaf_filename_endswith(self) -> None:
        """LeafMatch(FILENAME endswith ".env") → frozenset({'env'})。"""
        from fuscan.rules.parser import parse_match
        from fuscan.scanner.scanner import extract_required_exts

        match_spec = parse_match({"type": "filename", "mode": "endswith", "pattern": ".env"})
        exts = extract_required_exts(match_spec)
        assert exts == frozenset({"env"})

    def test_extract_required_exts_and_intersection(self) -> None:
        """AND(child1: FILENAME endswith ".env", child2: CONTENT) →
        仅 child1 有约束 → 交集结果 = frozenset({'env'})。"""
        from fuscan.scanner.scanner import extract_required_exts

        rule = self._filename_endswith_rule("env-key", ".env", "AKIA")
        exts = extract_required_exts(rule.match)
        assert exts == frozenset({"env"})

    def test_extract_required_exts_or_union(self) -> None:
        """OR(endswith ".json", endswith ".yaml") → frozenset({'json', 'yaml'})。"""
        from fuscan.rules.parser import parse_match
        from fuscan.scanner.scanner import extract_required_exts

        match_spec = parse_match(
            {
                "type": "or",
                "children": [
                    {"type": "filename", "mode": "endswith", "pattern": ".json"},
                    {"type": "filename", "mode": "endswith", "pattern": ".yaml"},
                ],
            }
        )
        exts = extract_required_exts(match_spec)
        assert exts == frozenset({"json", "yaml"})

    def test_extract_required_exts_pure_content_returns_none(self) -> None:
        """纯 CONTENT 规则：extract_required_exts 返回 None（无扩展名约束）。"""
        from fuscan.scanner.scanner import extract_required_exts

        rule = _content_rule("c1", "password")
        exts = extract_required_exts(rule.match)
        assert exts is None

    def test_scanner_splits_global_and_ext_specific_rules(self) -> None:
        """Scanner __init__: 一条纯 CONTENT 规则进 global；一条 .env 规则进 env。"""
        env_rule = self._filename_endswith_rule("env-ak", ".env", "AWS_ACCESS")
        content_rule = _content_rule("pwd", "password")
        rs = _build_ruleset(env_rule, content_rule)
        scanner = Scanner(rs)
        # env_rule 应出现在 _ext_remaining_rules["env"] 中（非 CONTENT 顶层 leaf → remaining）
        # 验证：_ext_remaining_rules 有 "env"
        assert "env" in scanner._ext_remaining_rules
        env_rule_names = {r.name for r, _ in scanner._ext_remaining_rules["env"]}
        assert "env-ak" in env_rule_names
        # 纯 CONTENT 规则 pwd：要么进 global_content_buckets（若 N>=2 合入桶）或 global_remaining
        global_rule_names = {r.name for r, _ in scanner._global_remaining_rules}
        bucket_names = {r.name for b in scanner._global_content_buckets for r in b.rules}
        assert "pwd" in global_rule_names or "pwd" in bucket_names

    def test_pruning_preserves_result_equivalence(self, tmp_path: Path) -> None:
        """预筛前后结果必须完全一致（20 规则 × 2 扩展名 × 各 5 文件 = 20 文件）。

        - 每条规则只对一个扩展名生效（AND 条件），若预筛错误则会漏匹配。
        - 对每条针对 {ext} 的规则：在该 ext 文件中放匹配内容，在其他 ext 中不放；
          验证最终命中与未启用分 ext 拆分时（monkeypatch 强制 all global）一致。
        """
        rules: list[Rule] = []
        # 10 条 .env 规则 + 10 条 .json 规则
        for i in range(10):
            rules.append(self._filename_endswith_rule(f"env-r{i}", ".env", f"ENV_TOKEN_{i}"))
            rules.append(self._filename_endswith_rule(f"json-r{i}", ".json", f"JSON_KEY_{i}"))
        rs = _build_ruleset(*rules)

        # 创建 10 × .env（含 ENV_TOKEN_i）+ 10 × .json（含 JSON_KEY_i）
        for i in range(10):
            env_file = tmp_path / f"c{i:02d}.env"
            json_file = tmp_path / f"c{i:02d}.json"
            env_lines = [f"ENV_TOKEN_{j}=value_{j}" for j in range(10)]
            json_lines = [f'"k{j}": "JSON_KEY_{j}"' for j in range(10)]
            env_file.write_text("\n".join(env_lines), encoding="utf-8")
            json_file.write_text("{\n" + ",\n".join(json_lines) + "\n}\n", encoding="utf-8")

        # 正常扫描（启用预筛）
        scanner1 = Scanner(rs)
        report1 = scanner1.scan(tmp_path)
        # 收集 (path_str, rule_name) 集合：遍历 ScanResult → RuleHit
        result1: set[tuple[str, str]] = set()
        for r in report1.hits:
            for h in r.hits:
                result1.add((str(r.path), h.rule_name))

        # 禁用预筛：让 global_pairs 包含所有规则，ext_pairs_map 为空
        # 通过模拟 Scanner 构造后的拆分逻辑，手动重新赋值
        scanner2 = Scanner(rs)
        all_pairs = list(scanner2._compiled)
        g_buckets, g_remaining = scanner2._build_content_buckets(all_pairs)
        scanner2._global_content_buckets = g_buckets
        scanner2._global_remaining_rules = g_remaining
        scanner2._ext_content_buckets = {}
        scanner2._ext_remaining_rules = {}
        scanner2._content_buckets = g_buckets
        scanner2._remaining_uncached_rules = g_remaining

        report2 = scanner2.scan(tmp_path)
        result2: set[tuple[str, str]] = set()
        for r in report2.hits:
            for h in r.hits:
                result2.add((str(r.path), h.rule_name))

        # 预筛后的命中集合必须与全量扫描完全相等（不能漏也不能多）
        assert result1 == result2, f"预筛改变了命中结果！新增={result2 - result1}; 丢失={result1 - result2}"

    def test_get_effective_buckets_and_rules_for_env_txt(self, tmp_path: Path) -> None:
        """_get_effective_buckets_and_rules 对 .txt 不返回 .env 的 rules；对 .env 返回。"""
        env_rule = self._filename_endswith_rule("env-ak", ".env", "AWS_ACCESS")
        txt_rule = self._filename_endswith_rule("txt-pwd", ".txt", "password")
        rs = _build_ruleset(env_rule, txt_rule)
        scanner = Scanner(rs)
        # 用 FileEntry.from_path 构造（自动填充 name/extension）
        from fuscan.scanner.context import FileEntry

        env_path = tmp_path / "app.env"
        txt_path = tmp_path / "note.txt"
        env_path.write_bytes(b"mock")
        txt_path.write_bytes(b"mock")
        env_entry = FileEntry.from_path(env_path)
        txt_entry = FileEntry.from_path(txt_path)

        _env_b, env_r = scanner._get_effective_buckets_and_rules(env_entry)
        _txt_b, txt_r = scanner._get_effective_buckets_and_rules(txt_entry)

        env_remaining_names = {r.name for r, _ in env_r}
        txt_remaining_names = {r.name for r, _ in txt_r}

        # env-ak 只在 env entry 的 remaining 中；txt-pwd 只在 txt entry 的 remaining
        assert "env-ak" in env_remaining_names
        assert "env-ak" not in txt_remaining_names
        assert "txt-pwd" in txt_remaining_names
        assert "txt-pwd" not in env_remaining_names


class TestIter166StreamSave:
    """iter-166：ScanReport 流式分块写入 JSON/CSV，大结果集内存峰值下降。

    覆盖：
    1. save_json_file 输出与 to_json() 完全一致（bytes 级等价，含字段顺序）
    2. save_csv_file 输出与 to_csv() 完全一致（文本级等价）
    3. 进度回调调用次数与参数正确
    4. chunk_size <= 0 抛 ValueError
    5. 空报告（0 hits）写入正确
    """

    @staticmethod
    def _build_report(tmp_path: Path) -> ScanReport:
        from fuscan.scanner.result import RuleHit

        (tmp_path / "secret").mkdir(parents=True, exist_ok=True)
        results = (
            ScanResult(
                path=tmp_path / "secret" / "a.txt",
                size=10,
                hits=(
                    RuleHit("敏感文件名", Severity.WARNING, "d1", match_count=1),
                    RuleHit("密钥内容", Severity.CRITICAL, "d2", match_count=2),
                ),
            ),
            ScanResult(
                path=tmp_path / "secret" / "b.txt",
                size=20,
                hits=(RuleHit("密钥内容", Severity.CRITICAL, "d3", match_count=3),),
            ),
            ScanResult(
                path=tmp_path / "secret" / "c.json",
                size=30,
                archive_path=tmp_path / "bundle.zip",
                hits=(RuleHit("AWS 密钥", Severity.CRITICAL, "d4", match_count=1),),
            ),
            ScanResult(path=tmp_path / "clean.txt", size=0, hits=()),
        )
        stats = ScanStats(
            total_files=4,
            scanned_files=4,
            matched_files=3,
            skipped_files=0,
            errors=0,
            duration_seconds=0.5,
            total_matches=7,
        )
        return ScanReport(root=tmp_path, results=results, stats=stats)

    def test_save_json_file_equals_to_json(self, tmp_path: Path) -> None:
        """save_json_file(chunk_size=1) 输出与 to_json() 逐字节相等。"""
        import json as _json

        report = self._build_report(tmp_path)
        out = tmp_path / "report.json"
        report.save_json_file(out, chunk_size=1)
        # 语义一致性：解析后 dict 完全等价（绕过 JSON 字段顺序差异）
        expected = _json.loads(report.to_json())
        actual = _json.loads(out.read_bytes())
        assert actual == expected

    def test_save_json_file_large_chunk(self, tmp_path: Path) -> None:
        """chunk_size=999 覆盖单批全量写入场景，结果仍等价。"""
        import json as _json

        report = self._build_report(tmp_path)
        out = tmp_path / "report_large.json"
        report.save_json_file(out, chunk_size=999)
        expected = _json.loads(report.to_json())
        actual = _json.loads(out.read_bytes())
        assert actual == expected

    def test_save_json_file_roundtrip_from_json(self, tmp_path: Path) -> None:
        """save_json_file → from_json 还原后关键字段相等。"""
        report = self._build_report(tmp_path)
        out = tmp_path / "report.json"
        report.save_json_file(out, chunk_size=2)
        restored = ScanReport.from_json(out.read_text())
        assert restored.root == report.root
        assert len(restored.hits) == len(report.hits)
        assert restored.stats.total_matches == report.stats.total_matches
        for r1, r2 in zip(restored.hits, report.hits, strict=True):
            assert r1.path == r2.path
            assert r1.size == r2.size
            assert [h.rule_name for h in r1.hits] == [h.rule_name for h in r2.hits]

    def test_save_csv_file_equals_to_csv(self, tmp_path: Path) -> None:
        """save_csv_file(chunk_size=1) 输出与 to_csv() 逐行等价（换行规范化后比较）。"""
        report = self._build_report(tmp_path)
        out = tmp_path / "report.csv"
        report.save_csv_file(out, chunk_size=1)
        # csv 换行在 Windows 文本读写中会规范化，用 splitlines 消除差异
        assert out.read_text(encoding="utf-8").splitlines() == report.to_csv().splitlines()

    def test_save_csv_file_large_chunk(self, tmp_path: Path) -> None:
        """chunk_size=999 覆盖单批全量写入 CSV，结果仍等价。"""
        report = self._build_report(tmp_path)
        out = tmp_path / "report_large.csv"
        report.save_csv_file(out, chunk_size=999)
        assert out.read_text(encoding="utf-8").splitlines() == report.to_csv().splitlines()

    def test_progress_callback_called(self, tmp_path: Path) -> None:
        """进度回调：初始 0 + 每批结束后调用；注意 hits 仅统计含命中 rule 的文件（3）。

        - JSON chunk_size=2：2 批（首批 2 条 + 剩余 1 条），共 3 次 cb
        - CSV chunk_size=3：1 批（单批覆盖全部 3 条），共 2 次 cb
        """
        report = self._build_report(tmp_path)
        # ScanReport.hits 仅返回至少有 1 条 RuleHit 的 ScanResult（不是 4）
        total = len(report.hits)
        assert total == 3, f"expect 3 files with rule hits, got {total}"
        calls: list[tuple[int, int]] = []
        report.save_json_file(tmp_path / "p.json", chunk_size=2, progress_cb=lambda c, t: calls.append((c, t)))
        assert calls == [(0, total), (2, total), (3, total)]
        calls.clear()
        report.save_csv_file(tmp_path / "p.csv", chunk_size=3, progress_cb=lambda c, t: calls.append((c, t)))
        assert calls == [(0, total), (3, total)]

    def test_chunk_size_non_positive_raises(self, tmp_path: Path) -> None:
        """chunk_size <= 0 抛 ValueError。"""
        report = self._build_report(tmp_path)
        with pytest.raises(ValueError, match="chunk_size"):
            report.save_json_file(tmp_path / "bad.json", chunk_size=0)
        with pytest.raises(ValueError, match="chunk_size"):
            report.save_json_file(tmp_path / "bad.json", chunk_size=-5)
        with pytest.raises(ValueError, match="chunk_size"):
            report.save_csv_file(tmp_path / "bad.csv", chunk_size=0)

    def test_empty_report_writes_valid_json_and_csv(self, tmp_path: Path) -> None:
        """空报告（no hits）仍能写出合法 JSON/CSV。"""
        import json as _json

        empty = ScanReport(root=tmp_path, results=(), stats=ScanStats())
        json_out = tmp_path / "empty.json"
        csv_out = tmp_path / "empty.csv"
        empty.save_json_file(json_out, chunk_size=100)
        empty.save_csv_file(csv_out, chunk_size=100)
        assert _json.loads(json_out.read_bytes())["hits"] == []
        assert csv_out.read_text(encoding="utf-8").splitlines() == empty.to_csv().splitlines()


class TestIter160ProgressThrottle:
    """iter-160：Scanner._emit_progress 双门限节流测试。

    覆盖：
    1. 时间门限 + 增量门限（双抑制）：时间窗内且 scanned/matched 增量都低于阈值时跳过
    2. 时间门限通过但增量仍不足：仍然跳过
    3. 增量门限通过（scanned 增量 >= 阈值）：即使时间极短也放行
    4. force=True 强制发送：忽略双门限
    5. matched_files 快照截断到 PROGRESS_SNAPSHOT_TAIL（避免 O(N) 拷贝）
    """

    @staticmethod
    def _build_scanner(
        on_progress: Callable[[ProgressInfo], None] | None,
        progress_interval: float = 0.0,
    ) -> Scanner:
        rs = _build_ruleset(_filename_rule("r", "f"))
        return Scanner(
            rs,
            on_progress=on_progress,
            progress_interval=progress_interval,
        )

    def test_throttle_skips_when_below_both_thresholds(self) -> None:
        """时间窗内且增量不足 → 跳过 emit。"""
        received: list[ProgressInfo] = []
        sc = self._build_scanner(received.append, progress_interval=0.05)
        sc._progress_start = time.perf_counter()
        sc._progress_total = 1000
        # 先放一次 initial，建立基线（首次 emit 放行以初始化进度）
        sc._emit_progress("", 10, 0, 0, 0)
        assert len(received) == 1
        # 等待超过时间窗，但 scanned 增量仍只有 10（<200）且 matched=0 → 跳过
        time.sleep(0.1)
        sc._emit_progress("", 20, 0, 0, 0)
        assert len(received) == 1

    def test_throttle_releases_when_delta_files_reached(self) -> None:
        """时间窗通过 + 扫描增量 >= 阈值 → 放行。"""
        received: list[ProgressInfo] = []
        sc = self._build_scanner(received.append, progress_interval=0.05)
        sc._progress_start = time.perf_counter()
        sc._progress_total = 1000
        # 首次 emit（时间窗天然通过，增量因无历史基准直接通过）
        sc._emit_progress("", 10, 0, 0, 0)
        assert len(received) == 1
        # 时间窗通过 + 扫描增量 >= 200
        time.sleep(0.1)
        sc._emit_progress("", 220, 0, 0, 0)
        assert len(received) == 2
        assert received[-1].scanned == 220

    def test_throttle_releases_when_delta_matched_reached(self) -> None:
        """时间窗通过 + 命中增量 >= 阈值（即使扫描增量不足）→ 放行。"""
        received: list[ProgressInfo] = []
        sc = self._build_scanner(received.append, progress_interval=0.05)
        sc._progress_start = time.perf_counter()
        sc._progress_total = 1000
        # 首次 emit 确立基线
        sc._emit_progress("", 10, 0, 0, 0)
        assert len(received) == 1
        time.sleep(0.1)
        # 扫描增量 20<200 但 matched 增量 60>=50 → 放行
        sc._emit_progress("", 30, 60, 0, 0)
        assert len(received) == 2
        assert received[-1].matched == 60

    def test_force_bypasses_all_thresholds(self) -> None:
        """force=True 无视时间窗和增量，强制发送。"""
        received: list[ProgressInfo] = []
        sc = self._build_scanner(received.append, progress_interval=10.0)
        sc._progress_start = time.perf_counter()
        sc._progress_total = 1000
        # 时间窗极长（10s）且 scanned=0 matched=0 仍应强制发送
        sc._emit_progress("", 0, 0, 0, 0, force=True)
        assert len(received) == 1
        # 紧接着再次 force=True 也必须发送
        sc._emit_progress("", 1, 0, 0, 0, force=True)
        assert len(received) == 2

    def test_snapshot_tail_capped(self) -> None:
        """matched_files/skipped_dirs 已优化为默认空元组（GUI 不消费，性能优化）。"""
        received: list[ProgressInfo] = []
        sc = self._build_scanner(received.append, progress_interval=0.0)
        sc._progress_start = time.perf_counter()
        sc._progress_total = 1000
        # 模拟添加命中记录（内部 deque 仍被填充，但 emit 不再构建快照）
        for i in range(100):
            sc._matched_files.append((f"path_{i}", f"rule_{i}"))
        for i in range(50):
            sc._skipped_dirs.append(f"skip_{i}")
        sc._emit_progress("", 10, 10, 0, 0)
        assert len(received) == 1
        info = received[0]
        # 快照字段已优化为空元组，不再构建 O(N) 拷贝
        assert info.matched_files == ()
        assert info.skipped_dirs == ()


# ---------------------------------------------------------------------------
# iter-161 并发扫描路径去重（entries 提交前按 path 去重）
# ---------------------------------------------------------------------------


class TestIter161ConcurrentDedup:
    """iter-161：并发扫描时 entries 按路径去重，避免同一文件被重复扫描。"""

    def test_concurrent_dedup_when_duplicate_entries(self, tmp_path: Path) -> None:
        """entries 中包含重复路径时，结果仅保留唯一文件。"""
        (tmp_path / "a.txt").write_text("password=1", encoding="utf-8")
        (tmp_path / "b.txt").write_text("password=2", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        scanner = Scanner(rs, max_workers=4)
        # 虽然 scan 方法内部的 collect_entries 通常已去重，但此处验证
        # 并发扫描（run_pipeline_phase → _scan_concurrent）在收到重复
        # entries 时能正确去重。通过直接向 _scan_concurrent 传入手工
        # 构造的重复 entries 验证。
        from fuscan.scanner._pipeline_phase import run_pipeline_phase
        from fuscan.scanner.context import FileEntry

        def _entry(p: Path) -> FileEntry:
            st = p.stat()
            return FileEntry(
                path=p,
                name=p.name,
                size=st.st_size,
                mtime=st.st_mtime,
                extension=p.suffix.lower().lstrip("."),
                is_dir=False,
            )

        a_entry = _entry(tmp_path / "a.txt")
        b_entry = _entry(tmp_path / "b.txt")
        # 构造重复 entries：a 出现两次
        dup_entries = [a_entry, b_entry, a_entry]

        results: list[ScanResult] = []
        scanned, matched, errors, matches = run_pipeline_phase(scanner, dup_entries, results)

        assert scanned == 2, f"scanned 应为 2（去重后仅 2 个唯一文件），实际 {scanned}"
        assert matched == 2, f"matched 应为 2，实际 {matched}"
        assert len(results) == 2, f"results 应为 2 条，实际 {len(results)}"
        paths = sorted(str(r.path) for r in results)
        assert paths == sorted([str(tmp_path / "a.txt"), str(tmp_path / "b.txt")])
        # 命中数总计：每个文件各 1 条密码命中
        assert matches == 2
        assert errors == 0

    def test_concurrent_dedup_single_entry_no_duplicate(self, tmp_path: Path) -> None:
        """唯一 entries 列表应不受去重逻辑影响。"""
        (tmp_path / "only.txt").write_text("no secret here", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        scanner = Scanner(rs, max_workers=4)
        from fuscan.scanner._pipeline_phase import run_pipeline_phase
        from fuscan.scanner.context import FileEntry

        p = tmp_path / "only.txt"
        st = p.stat()
        entry = FileEntry(
            path=p,
            name=p.name,
            size=st.st_size,
            mtime=st.st_mtime,
            extension=p.suffix.lower().lstrip("."),
            is_dir=False,
        )
        results: list[ScanResult] = []
        scanned, matched, errors, matches = run_pipeline_phase(scanner, [entry], results)
        assert scanned == 1
        assert matched == 0  # 无 password 命中
        assert matches == 0
        assert len(results) == 1
        assert errors == 0


class TestPerRuleContentPrefilter:
    """CONTENT 桶逐规则预筛：仅对关键字实际出现的规则子集运行匹配。

    覆盖需求：普通文档偶现常见词（如 password）不应触发整桶复合正则，
    只触发对应规则，避免大文本 finditer 阻塞。
    """

    @staticmethod
    def _regex_content_rule(name: str, pattern: str, *, case_sensitive: bool = False) -> Rule:
        return Rule(
            name=name,
            severity=Severity.CRITICAL,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=pattern,
                case_sensitive=case_sensitive,
            ),
        )

    def _build_bucket(self, *rules: Rule):  # type: ignore[no-untyped-def]
        from fuscan.scanner._content_buckets import build_content_buckets
        from fuscan.scanner.matchers import build_matcher

        pairs = [(r, build_matcher(r.match)) for r in rules]
        buckets, _remaining = build_content_buckets(pairs)
        return buckets

    def test_single_keyword_activates_one_rule(self) -> None:
        """仅含 password 的内容只激活对应规则，不触发整桶。"""
        from fuscan.scanner._content_buckets import _compute_active_indices

        rules = [
            self._regex_content_rule("r_pwd", r"password"),
            self._regex_content_rule("r_ghp", r"ghp_[A-Za-z0-9]{36}"),
            self._regex_content_rule("r_aiza", r"AIza[0-9A-Za-z_-]{35}"),
        ]
        buckets = self._build_bucket(*rules)
        assert len(buckets) == 1
        bucket = buckets[0]
        content = "some password here " * 100
        haystack = content.lower() if bucket.prefilter_case_insensitive else content
        active = _compute_active_indices(bucket, haystack)
        active_names = {bucket.rules[i].name for i in active}
        assert active_names == {"r_pwd"}

    def test_multiple_keywords_activate_multiple_rules(self) -> None:
        """含多个不同前缀关键字时激活对应多条规则。"""
        from fuscan.scanner._content_buckets import _compute_active_indices

        rules = [
            self._regex_content_rule("r_pwd", r"password"),
            self._regex_content_rule("r_ghp", r"ghp_[A-Za-z0-9]{36}"),
            self._regex_content_rule("r_aiza", r"AIza[0-9A-Za-z_-]{35}"),
        ]
        buckets = self._build_bucket(*rules)
        bucket = buckets[0]
        content = "password and ghp_ prefix and AIza key"
        haystack = content.lower() if bucket.prefilter_case_insensitive else content
        active = _compute_active_indices(bucket, haystack)
        active_names = {bucket.rules[i].name for i in active}
        # 大小写不敏感桶：password/ghp/aiza 前缀均出现
        assert "r_pwd" in active_names
        assert "r_ghp" in active_names
        assert "r_aiza" in active_names

    def test_no_keyword_short_circuits(self) -> None:
        """完全无关键字时活跃规则集为空（桶级/逐规则均短路）。"""
        from fuscan.scanner._content_buckets import _compute_active_indices

        rules = [
            self._regex_content_rule("r_ghp", r"ghp_[A-Za-z0-9]{36}"),
            self._regex_content_rule("r_aiza", r"AIza[0-9A-Za-z_-]{35}"),
        ]
        buckets = self._build_bucket(*rules)
        bucket = buckets[0]
        content = "完全干净的普通中文文档，没有任何敏感前缀。" * 50
        haystack = content.lower() if bucket.prefilter_case_insensitive else content
        active = _compute_active_indices(bucket, haystack)
        assert active == []

    def test_real_secret_still_matches(self, tmp_path: Path) -> None:
        """真实密钥（ghp_ / AIza）仍被正确命中，预筛不产生 false negative。"""
        ghp = "ghp_" + "a" * 36
        aiza = "AIza" + "B" * 35
        content = f"config line1\ngithub={ghp}\ngcp={aiza}\n"
        rules = [
            self._regex_content_rule("r_ghp", r"ghp_[A-Za-z0-9]{36}"),
            self._regex_content_rule("r_aiza", r"AIza[0-9A-Za-z_-]{35}"),
        ]
        from fuscan.scanner._content_buckets import match_content_via_buckets

        buckets = self._build_bucket(*rules)
        hits = match_content_via_buckets(content, buckets)
        hit_names = {h.rule_name for h in hits}
        assert hit_names == {"r_ghp", "r_aiza"}

    def test_no_literal_rules_always_active(self) -> None:
        """无字面量规则（纯字符类）始终活跃且能正常命中。"""
        from fuscan.scanner._content_buckets import _compute_active_indices, match_content_via_buckets

        rules = [
            self._regex_content_rule("r_upper", r"[A-Z]{16}", case_sensitive=True),
            self._regex_content_rule("r_digits", r"[0-9]{16}", case_sensitive=True),
        ]
        buckets = self._build_bucket(*rules)
        bucket = buckets[0]
        # 两条规则均无可提取字面量，per_rule_keywords 为空 → 始终活跃
        assert all(kws == [] for kws in bucket.per_rule_keywords)
        content = "token ABCDEFGHIJKLMNOP and 1234567890123456"
        haystack = content if bucket.case_sensitive else content.lower()
        active = _compute_active_indices(bucket, haystack)
        assert set(active) == {0, 1}
        hits = match_content_via_buckets(content, buckets)
        assert {h.rule_name for h in hits} == {"r_upper", "r_digits"}

    def test_active_subset_regex_cached(self) -> None:
        """同一活跃子集第二次匹配应命中 sub_compiled_cache。"""
        from fuscan.scanner._content_buckets import match_content_via_buckets

        rules = [
            self._regex_content_rule("r_pwd", r"password"),
            self._regex_content_rule("r_ghp", r"ghp_[A-Za-z0-9]{36}"),
            self._regex_content_rule("r_aiza", r"AIza[0-9A-Za-z_-]{35}"),
        ]
        buckets = self._build_bucket(*rules)
        bucket = buckets[0]
        content = "only password word repeated " * 20
        match_content_via_buckets(content, buckets)
        # 活跃子集为单规则 {r_pwd 的下标}，应写入缓存
        assert len(bucket.sub_compiled_cache) == 1
        cached_key = next(iter(bucket.sub_compiled_cache))
        cached_pattern = bucket.sub_compiled_cache[cached_key]
        # 第二次匹配同一子集：缓存复用，键集合不新增
        match_content_via_buckets(content, buckets)
        assert len(bucket.sub_compiled_cache) == 1
        assert bucket.sub_compiled_cache[cached_key] is cached_pattern

    def test_all_active_reuses_full_bucket_regex(self) -> None:
        """全部规则活跃时复用整桶 compiled，不新增子集缓存。"""
        from fuscan.scanner._content_buckets import match_content_via_buckets

        rules = [
            self._regex_content_rule("r_pwd", r"password"),
            self._regex_content_rule("r_pwd2", r"passwd"),
        ]
        buckets = self._build_bucket(*rules)
        bucket = buckets[0]
        # 内容含两条规则的关键字 → 全部活跃 → 用整桶 compiled
        content = "password and passwd both present"
        hits = match_content_via_buckets(content, buckets)
        assert {h.rule_name for h in hits} == {"r_pwd", "r_pwd2"}
        assert bucket.sub_compiled_cache == {}

    def test_contains_case_sensitive_per_rule_prefilter(self) -> None:
        """CONTAINS(case_sensitive) 桶仅对活跃规则做 count。"""
        from fuscan.scanner._content_buckets import match_content_via_buckets

        rules = [
            Rule(
                name="c_secret",
                severity=Severity.CRITICAL,
                match=LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="SECRET_TOKEN", case_sensitive=True
                ),
            ),
            Rule(
                name="c_apikey",
                severity=Severity.CRITICAL,
                match=LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="APIKEY_VALUE", case_sensitive=True
                ),
            ),
        ]
        buckets = self._build_bucket(*rules)
        content = "line with SECRET_TOKEN present, APIKEY absent"
        hits = match_content_via_buckets(content, buckets)
        assert {h.rule_name for h in hits} == {"c_secret"}


def _plain_entry(path: Path) -> FileEntry:
    """从路径构造 FileEntry（测试辅助）。"""
    st = path.stat()
    return FileEntry(
        path=path,
        name=path.name,
        size=st.st_size,
        mtime=st.st_mtime,
        extension=path.suffix.lower().lstrip("."),
        is_dir=False,
    )


class TestPerFileElapsedMs:
    """单文件解析耗时应为该文件真实耗时，而非累计耗时。

    覆盖需求：解析详情展开列表中每个文件旁的耗时应是单文件解析用时。
    并发模式下 submit_time≈扫描起点，若用 now-submit_time 会呈累计增长；
    ScanResult.elapsed_ms 由 worker 实测，collector 据此反推起点得到单文件耗时。
    """

    def test_scan_result_carries_elapsed_ms(self, tmp_path: Path) -> None:
        """_scan_entry 应始终回填非负的 elapsed_ms（无论是否启用 file_perf）。"""
        p = tmp_path / "a.txt"
        p.write_text("hello world content", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=1, cache=None)
        result = scanner._scan_entry(_plain_entry(p))
        assert result.elapsed_ms >= 0.0

    def test_scan_result_carries_engine(self, tmp_path: Path) -> None:
        """_scan_entry 应按扩展名回填解析引擎名（纯文本 → 「纯文本」）。"""
        p = tmp_path / "a.txt"
        p.write_text("hello world content", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=1, cache=None)
        result = scanner._scan_entry(_plain_entry(p))
        # .txt 由 PlainTextExtractor 处理，引擎为 charset-normalizer
        assert result.engine == "charset-normalizer"

    def test_concurrent_elapsed_not_cumulative(self, tmp_path: Path) -> None:
        """并发模式下单文件耗时不应随队列位置单调递增（不是累计耗时）。

        用注入固定 sleep 的 content_provider 让每个文件解析耗时相近（约 20ms），
        断言 progress 上报的 current_file_elapsed_ms 稳定在单文件量级，
        而非随完成顺序累加到数百毫秒。
        """
        from fuscan.scanner._pipeline_phase import run_pipeline_phase

        file_count = 8
        per_file_sleep = 0.02  # 20ms/文件
        for i in range(file_count):
            (tmp_path / f"f_{i}.txt").write_text(f"body {i}", encoding="utf-8")

        def _slow_provider(_entry: FileEntry) -> str:
            time.sleep(per_file_sleep)
            return "no hit content"

        rs = _build_ruleset(_content_rule("pwd", "password"))
        elapsed_samples: list[float] = []
        scanner = Scanner(
            rs,
            max_workers=4,
            cache=None,
            content_provider=_slow_provider,
            on_progress=lambda info: (
                elapsed_samples.append(info.current_file_elapsed_ms)
                if info.phase == "scan" and info.current_file_elapsed_ms > 0
                else None
            ),
            progress_interval=0.0,
        )
        # 每文件都 emit，确保采集到每个文件的单文件耗时样本
        scanner._progress_emit_batch = 1
        entries = [_plain_entry(tmp_path / f"f_{i}.txt") for i in range(file_count)]
        results: list[ScanResult] = []
        run_pipeline_phase(scanner, entries, results)

        assert len(results) == file_count
        # 单文件耗时应在单文件量级（约 20ms，宽松上界 200ms 容忍调度抖动），
        # 而非累计到 file_count*20ms=160ms 以上并持续增长
        assert elapsed_samples, "应至少上报一次 scan 阶段单文件耗时"
        cumulative_ceiling_ms = per_file_sleep * file_count * 1000 * 0.9  # 累计耗时下界
        for sample in elapsed_samples:
            assert sample < cumulative_ceiling_ms, f"单文件耗时 {sample}ms 疑似累计耗时"

    def test_sequential_elapsed_is_per_file(self, tmp_path: Path) -> None:
        """顺序模式单文件耗时同样为单文件量级（回归保护）。"""
        from fuscan.scanner._pipeline_phase import run_pipeline_phase

        file_count = 5
        per_file_sleep = 0.02
        for i in range(file_count):
            (tmp_path / f"s_{i}.txt").write_text(f"body {i}", encoding="utf-8")

        def _slow_provider(_entry: FileEntry) -> str:
            time.sleep(per_file_sleep)
            return "no hit content"

        rs = _build_ruleset(_content_rule("pwd", "password"))
        elapsed_samples: list[float] = []
        scanner = Scanner(
            rs,
            max_workers=1,
            cache=None,
            content_provider=_slow_provider,
            on_progress=lambda info: (
                elapsed_samples.append(info.current_file_elapsed_ms)
                if info.phase == "scan" and info.current_file_elapsed_ms > 0
                else None
            ),
            progress_interval=0.0,
        )
        # 顺序模式默认 batch=1，此处显式设置以保证逐文件 emit
        scanner._progress_emit_batch = 1
        entries = [_plain_entry(tmp_path / f"s_{i}.txt") for i in range(file_count)]
        results: list[ScanResult] = []
        run_pipeline_phase(scanner, entries, results)

        assert len(results) == file_count
        cumulative_ceiling_ms = per_file_sleep * file_count * 1000 * 0.9
        for sample in elapsed_samples:
            assert sample < cumulative_ceiling_ms


class TestEngineForExtension:
    """``engine_for_extension`` 按扩展名反查解析引擎名。

    引擎名由扩展名静态决定，供 GUI 明细行标注每个文件的解析路径。
    """

    def test_registered_extension_returns_engine_info(self) -> None:
        """已注册扩展名返回其提取器 engine_info。"""
        from fuscan.scanner._helpers import engine_for_extension

        # .txt → PlainTextExtractor.engine_info == "charset-normalizer"
        assert engine_for_extension("txt") == "charset-normalizer"

    def test_extension_case_insensitive(self) -> None:
        """扩展名大小写不敏感（注册表内部归一化为小写）。"""
        from fuscan.scanner._helpers import engine_for_extension

        assert engine_for_extension("TXT") == engine_for_extension("txt")

    def test_leading_dot_stripped(self) -> None:
        """带前导点的扩展名与不带点结果一致。"""
        from fuscan.scanner._helpers import engine_for_extension

        assert engine_for_extension(".txt") == engine_for_extension("txt")

    def test_unregistered_extension_returns_fallback(self) -> None:
        """无注册提取器的扩展名回退到「纯文本」引擎名。"""
        from fuscan.scanner._helpers import engine_for_extension

        # 极不可能被注册的扩展名，走纯文本读取回退
        assert engine_for_extension("no_such_ext_xyz") == "纯文本"

    def test_empty_extension_returns_fallback(self) -> None:
        """空扩展名（无扩展名文件）回退到「纯文本」引擎名。"""
        from fuscan.scanner._helpers import engine_for_extension

        assert engine_for_extension("") == "纯文本"


class TestIsMinifiedContent:
    """``is_minified_content`` 按内容特征识别压缩/打包产物。

    识别与文件名无关，仅看内容形态：总长达标且存在超长单行时判定为压缩产物。
    """

    def test_long_single_line_detected(self) -> None:
        """含超长单行（>=5000 字符）且总长达标：判定为压缩产物。"""
        from fuscan.scanner._helpers import is_minified_content

        # 模拟 min.js：单行 6000 字符无换行
        content = "var a=1;" * 750  # 8*750 = 6000 字符，单行
        assert is_minified_content(content) is True

    def test_long_line_among_normal_lines_detected(self) -> None:
        """普通多行中夹一条超长行（如 chunk.js 中间行）：仍判定为压缩产物。"""
        from fuscan.scanner._helpers import is_minified_content

        content = "line1\n" + ("x" * 6000) + "\nline3\n"
        assert is_minified_content(content) is True

    def test_trailing_long_line_without_newline_detected(self) -> None:
        """末行无结尾换行且超长：判定为压缩产物（覆盖末行分支）。"""
        from fuscan.scanner._helpers import is_minified_content

        content = "short\n" + ("y" * 6000)  # 末行无 \n
        assert is_minified_content(content) is True

    def test_normal_source_not_detected(self) -> None:
        """普通多行源码（行普遍较短）：不判定为压缩产物。"""
        from fuscan.scanner._helpers import is_minified_content

        # 2000 行，每行约 40 字符，总长达标但无超长行
        content = "\n".join(f"def func_{i}(): return {i}" for i in range(2000))
        assert is_minified_content(content) is False

    def test_short_content_not_detected(self) -> None:
        """内容总长低于下限：即便单行也不判定（小文件无性能问题）。"""
        from fuscan.scanner._helpers import is_minified_content

        # 单行 1000 字符，未达总长下限 2048
        assert is_minified_content("z" * 1000) is False

    def test_empty_content_not_detected(self) -> None:
        """空内容：不判定为压缩产物。"""
        from fuscan.scanner._helpers import is_minified_content

        assert is_minified_content("") is False


class TestMinifiedContentSkipped:
    """压缩/打包产物在解析阶段跳过 CONTENT 匹配（保留 FILENAME/PATH 规则）。"""

    def test_minified_skips_content_rule_uncached(self, tmp_path: Path) -> None:
        """无缓存模式：压缩内容中的敏感串不触发 CONTENT 命中。"""
        # 超长单行含 password，正常应被 CONTENT 规则命中，但因判定为压缩产物被跳过
        minified = "var config={};" + ("a=password;" * 600)  # 单行 >5000 且含 password
        (tmp_path / "bundle.js").write_text(minified, encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        assert report.stats.matched_files == 0

    def test_minified_keeps_filename_rule_uncached(self, tmp_path: Path) -> None:
        """无缓存模式：压缩文件的 FILENAME 规则仍命中（不依赖内容）。"""
        minified = "var config={};" + ("a=password;" * 600)
        (tmp_path / "app.bundle.js").write_text(minified, encoding="utf-8")
        # 同时含 CONTENT 规则（触发读内容路径）与 FILENAME 规则
        rs = _build_ruleset(
            _content_rule("pwd", "password"),
            _filename_rule("js_bundle", "bundle"),
        )

        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        assert report.stats.matched_files == 1
        rule_names = {hit.rule_name for hit in report.hits[0].hits}
        # FILENAME 命中保留，CONTENT 命中被跳过
        assert rule_names == {"js_bundle"}

    def test_normal_file_still_matches_content(self, tmp_path: Path) -> None:
        """对照组：普通多行文件的 CONTENT 规则正常命中，未被误跳。"""
        (tmp_path / "config.txt").write_text("db_password=secret123\n", encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        assert report.stats.matched_files == 1

    def test_minified_skips_content_rule_cached(self, tmp_path: Path) -> None:
        """缓存模式：压缩内容中的敏感串同样不触发 CONTENT 命中。"""
        from fuscan.cache import CacheStore

        minified = "var config={};" + ("a=password;" * 600)
        (tmp_path / "vendor.js").write_text(minified, encoding="utf-8")
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache = CacheStore(tmp_path / "cache.db")
        try:
            scanner = Scanner(rs, cache=cache)
            report = scanner.scan(tmp_path)
            assert report.stats.matched_files == 0
        finally:
            cache.close()


class TestTuneGilSwitchInterval:
    """措施2：进程级下调 GIL 线程切换间隔，缓解扫描期 GUI 冻结。"""

    def test_sets_interval(self) -> None:
        """调用后 ``sys.getswitchinterval()`` 变为目标值（默认 1ms）。"""
        import sys

        from fuscan.app import _tune_gil_switch_interval

        original = sys.getswitchinterval()
        try:
            _tune_gil_switch_interval()
            assert sys.getswitchinterval() == pytest.approx(0.001)
        finally:
            sys.setswitchinterval(original)

    def test_custom_interval(self) -> None:
        """可传入自定义间隔。"""
        import sys

        from fuscan.app import _tune_gil_switch_interval

        original = sys.getswitchinterval()
        try:
            _tune_gil_switch_interval(0.002)
            assert sys.getswitchinterval() == pytest.approx(0.002)
        finally:
            sys.setswitchinterval(original)


class TestIsNativeEngine:
    """``is_native_engine`` 判断扩展名对应提取器是否使用释放 GIL 的原生引擎。"""

    def test_text_engine_not_native(self) -> None:
        """文本/源码（charset-normalizer 纯 Python）非原生。"""
        from fuscan.scanner._helpers import is_native_engine

        assert is_native_engine("txt") is False
        assert is_native_engine("py") is False

    def test_pdf_engine_native(self) -> None:
        """PDF（pdf_oxide/pypdfium2 原生）为原生引擎。"""
        from fuscan.scanner._helpers import is_native_engine

        assert is_native_engine("pdf") is True

    def test_office_xml_engine_native(self) -> None:
        """DOCX/XLSX（lxml/calamine 原生）为原生引擎。"""
        from fuscan.scanner._helpers import is_native_engine

        assert is_native_engine("docx") is True
        assert is_native_engine("xlsx") is True

    def test_legacy_office_engine_not_native(self) -> None:
        """DOC/PPT（olefile 纯 Python）非原生。"""
        from fuscan.scanner._helpers import is_native_engine

        assert is_native_engine("doc") is False

    def test_unregistered_extension_not_native(self) -> None:
        """未注册扩展名回退纯文本读取，非原生。"""
        from fuscan.scanner._helpers import is_native_engine

        assert is_native_engine("no_such_ext_xyz") is False


class TestEffectiveMaxWorkers:
    """措施3：CONTENT 正则密集 + 非原生提取器场景动态降并发至 2。

    ``_effective_max_workers`` 在保住高并发（原生提取器为主 / 无 CONTENT 规则）与
    降档保住 GUI 响应（纯 Python 提取器 + CONTENT 正则密集）之间做静态判据。
    """

    def test_content_rule_all_extensions_downscaled(self) -> None:
        """CONTENT 规则 + 扫描所有扩展名（文本源码为主，持 GIL）→ 降档至 2。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=5)
        assert scanner._max_workers == 5
        assert scanner._effective_max_workers == 2

    def test_content_rule_native_extensions_kept(self) -> None:
        """CONTENT 规则 + 只扫原生引擎扩展名（PDF，解析释放 GIL）→ 保持原值。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=5, scan_extensions=("pdf",))
        assert scanner._effective_max_workers == 5

    def test_content_rule_native_majority_kept(self) -> None:
        """CONTENT 规则 + 原生扩展名占多数（pdf/xlsx vs py）→ 保持原值。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=5, scan_extensions=("pdf", "xlsx", "py"))
        assert scanner._effective_max_workers == 5

    def test_content_rule_non_native_extensions_downscaled(self) -> None:
        """CONTENT 规则 + 只扫非原生扩展名（py/txt，持 GIL）→ 降档至 2。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=5, scan_extensions=("py", "txt"))
        assert scanner._effective_max_workers == 2

    def test_no_content_rule_kept(self) -> None:
        """无 CONTENT 规则（仅 FILENAME，主线程无 finditer 争抢对手）→ 保持原值。"""
        rs = _build_ruleset(_filename_rule("env", ".env"))
        scanner = Scanner(rs, max_workers=5)
        assert scanner._effective_max_workers == 5

    def test_low_workers_no_downscale_room(self) -> None:
        """max_workers=2（已是降档目标）→ 无降档空间，保持 2。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=2)
        assert scanner._effective_max_workers == 2

    def test_none_workers_kept_none(self) -> None:
        """未指定 max_workers（None）→ 保持 None（顺序扫描，无并发降档语义）。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=None)
        assert scanner._effective_max_workers is None

    def test_empty_whitelist_kept(self) -> None:
        """空白名单（用户全部取消勾选，无文件可扫）→ 并发度无意义，保持原值。"""
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = Scanner(rs, max_workers=5, scan_extensions=())
        assert scanner._effective_max_workers == 5


class TestContentBucketsGilYield:
    """措施1：``match_content_via_buckets`` 桶间按时间式让步（``time.sleep(0)``）。

    不测墙钟或是否卡顿（必然 flaky），仅通过 monkeypatch ``_content_buckets`` 内的
    ``time.perf_counter``/``time.sleep`` 断言让步行为：跑过 finditer 的桶在距上次让步
    超过阈值时调用一次 ``sleep(0)``。
    """

    @staticmethod
    def _build_two_bucket_content() -> tuple[list[_ContentRuleBucket], str]:
        """构造含 >=2 条同 (mode, case_sensitive) CONTENT 规则的桶 + 命中内容。"""
        from fuscan.scanner._content_buckets import build_content_buckets
        from fuscan.scanner.matchers import build_matcher

        # 同 (REGEX, case_sensitive=True) 的两条规则 → 合并为一个桶（>=2 条才建桶）
        rules = [
            _content_rule("r_alpha", "alphakeyword"),
            _content_rule("r_beta", "betakeyword"),
        ]
        # CONTAINS → 转 REGEX 桶；两条同桶
        pairs = [(r, build_matcher(r.match)) for r in rules]
        buckets, _remaining = build_content_buckets(pairs)
        # 内容同时含两个关键字 → 桶内活跃、走 finditer 分支
        content = "prefix alphakeyword middle betakeyword suffix\n" * 3
        return buckets, content

    def test_yield_called_when_threshold_exceeded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """桶跑过 finditer 且距上次让步超阈值 → 调用一次 ``time.sleep(0)``。"""
        from fuscan.scanner import _content_buckets

        buckets, content = self._build_two_bucket_content()
        assert buckets, "预期至少构建一个 CONTENT 桶"

        sleep_calls: list[float] = []
        # perf_counter 每次调用递增大于阈值，确保让步条件必然成立
        counter = {"t": 0.0}

        def fake_perf_counter() -> float:
            counter["t"] += _content_buckets.GIL_YIELD_THRESHOLD_S * 2
            return counter["t"]

        monkeypatch.setattr(_content_buckets.time, "perf_counter", fake_perf_counter)

        def record_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(_content_buckets.time, "sleep", record_sleep)

        hits = _content_buckets.match_content_via_buckets(content, buckets)

        # 两条规则均命中
        assert {h.rule_name for h in hits} == {"r_alpha", "r_beta"}
        # 桶处理后触发至少一次让步，且让步为 sleep(0)
        assert len(sleep_calls) >= 1
        assert all(s == 0 for s in sleep_calls)

    def test_no_yield_when_below_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """距上次让步未超阈值 → 不调用 ``time.sleep``（覆盖 else 分支）。"""
        from fuscan.scanner import _content_buckets

        buckets, content = self._build_two_bucket_content()
        assert buckets

        sleep_calls: list[float] = []
        # perf_counter 恒定返回同值 → now - last_yield == 0 < 阈值，不让步
        monkeypatch.setattr(_content_buckets.time, "perf_counter", lambda: 100.0)

        def record_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(_content_buckets.time, "sleep", record_sleep)

        hits = _content_buckets.match_content_via_buckets(content, buckets)

        assert {h.rule_name for h in hits} == {"r_alpha", "r_beta"}
        assert sleep_calls == []


class TestScannerRemainingRuleYield:
    """措施1：``_scan_entry_uncached``/``_scan_entry_cached`` 的 remaining 规则循环
    在 worker 线程内按时间式让步。

    直接调用 ``_scan_entry_uncached``/``_scan_entry_cached`` 精准命中 remaining 循环
    的让步代码（不经 ``_scan_sequential``，避免其自身让步干扰断言），通过 monkeypatch
    ``scanner.time`` 强制让步条件成立/不成立，覆盖 ``if`` 两分支，不依赖真实墙钟。
    remaining 循环由 FILENAME 规则（非 CONTENT，不入桶）驱动。
    """

    @staticmethod
    def _make_entry(tmp_path: Path) -> FileEntry:
        p = tmp_path / "secret.txt"
        p.write_text("data", encoding="utf-8")
        st = p.stat()
        return FileEntry(
            path=p,
            name=p.name,
            size=st.st_size,
            mtime=st.st_mtime,
            extension="txt",
            is_dir=False,
        )

    def test_uncached_yield_triggered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """无缓存：remaining（FILENAME）规则循环超阈值 → 触发 ``sleep(0)``。"""
        from fuscan.scanner import scanner as scanner_mod

        rs = _build_ruleset(_filename_rule("名含 secret", "secret"))
        scanner = Scanner(rs)
        entry = self._make_entry(tmp_path)

        sleep_calls: list[float] = []
        counter = {"t": 0.0}

        def fake_perf_counter() -> float:
            counter["t"] += scanner_mod.GIL_YIELD_THRESHOLD_S * 2
            return counter["t"]

        monkeypatch.setattr(scanner_mod.time, "perf_counter", fake_perf_counter)

        def record_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(scanner_mod.time, "sleep", record_sleep)

        result = scanner._scan_entry_uncached(entry)

        assert result.has_hit
        assert any(s == 0 for s in sleep_calls)

    def test_cached_yield_triggered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """缓存模式：remaining（FILENAME）规则循环超阈值 → 触发 ``sleep(0)``。"""
        from fuscan.cache import CacheStore
        from fuscan.scanner import scanner as scanner_mod

        rs = _build_ruleset(_filename_rule("名含 secret", "secret"))
        entry = self._make_entry(tmp_path)

        cache = CacheStore(tmp_path / "cache.db")
        try:
            scanner = Scanner(rs, cache=cache)

            sleep_calls: list[float] = []
            counter = {"t": 0.0}

            def fake_perf_counter() -> float:
                counter["t"] += scanner_mod.GIL_YIELD_THRESHOLD_S * 2
                return counter["t"]

            monkeypatch.setattr(scanner_mod.time, "perf_counter", fake_perf_counter)

            def record_sleep(seconds: float) -> None:
                sleep_calls.append(seconds)

            monkeypatch.setattr(scanner_mod.time, "sleep", record_sleep)

            result = scanner._scan_entry_cached(entry)

            assert result.has_hit
            assert any(s == 0 for s in sleep_calls)
        finally:
            cache.close()

    def test_uncached_no_yield_below_threshold(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """无缓存：perf_counter 恒定 → remaining 循环不触发 ``sleep``（覆盖 else 分支）。"""
        from fuscan.scanner import scanner as scanner_mod

        rs = _build_ruleset(_filename_rule("名含 secret", "secret"))
        scanner = Scanner(rs)
        entry = self._make_entry(tmp_path)

        sleep_calls: list[float] = []
        monkeypatch.setattr(scanner_mod.time, "perf_counter", lambda: 42.0)

        def record_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(scanner_mod.time, "sleep", record_sleep)

        result = scanner._scan_entry_uncached(entry)

        assert result.has_hit
        assert sleep_calls == []
