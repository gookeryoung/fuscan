"""iter-124 增量扫描测试。

覆盖 ``IncrementalManifest`` 序列化、``Scanner`` 增量扫描行为与合并逻辑。
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

from fuscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)
from fuscan.scanner import Scanner, ScanReport, ScanResult
from fuscan.scanner.result import (
    FileFingerprint,
    IncrementalManifest,
    RuleHit,
)


def _build_ruleset(*rules: Rule) -> RuleSet:
    return RuleSet(version="1.0", rules=tuple(rules))


def _filename_rule(name: str, pattern: str, severity: Severity = Severity.WARNING) -> Rule:
    return Rule(
        name=name,
        severity=severity,
        match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern=pattern),
    )


class TestIncrementalManifest:
    """IncrementalManifest 序列化与 rel_key 测试。"""

    def test_manifest_roundtrip(self) -> None:
        """to_json/from_json 互逆（含多个指纹）。"""
        fps = {
            "a.txt": FileFingerprint(mtime=1000.0, size=10),
            "sub/b.txt": FileFingerprint(mtime=2000.5, size=20),
        }
        manifest = IncrementalManifest(root=Path("/scan/root"), fingerprints=fps)
        json_str = manifest.to_json()
        restored = IncrementalManifest.from_json(json_str)
        assert restored.root == manifest.root
        assert restored.fingerprints == manifest.fingerprints

    def test_manifest_empty_roundtrip(self) -> None:
        """空清单序列化应保持空。"""
        manifest = IncrementalManifest(root=Path("/empty"))
        json_str = manifest.to_json()
        restored = IncrementalManifest.from_json(json_str)
        assert restored.root == manifest.root
        assert restored.fingerprints == {}

    def test_manifest_invalid_json(self) -> None:
        """非法 JSON 应抛 ValueError。"""
        with pytest.raises(ValueError):
            IncrementalManifest.from_json("not a json string")

    def test_manifest_rel_key_windows_separators(self, tmp_path: Path) -> None:
        """rel_key 应将 Windows 反斜杠分隔符统一为正斜杠。"""
        root = tmp_path
        nested = root / "subdir" / "file.txt"
        nested.parent.mkdir(parents=True)
        nested.write_text("x", encoding="utf-8")
        key = IncrementalManifest.rel_key(nested, root)
        assert "\\" not in key
        assert key == "subdir/file.txt"

    def test_manifest_rel_key_outside_root(self, tmp_path: Path) -> None:
        """路径不在 root 下时 rel_key 应回退为绝对路径（容错）。"""
        root = tmp_path
        outside = Path("/some/other/place/file.txt") if os.name != "nt" else Path("C:\\other\\file.txt")
        key = IncrementalManifest.rel_key(outside, root)
        # 回退为绝对路径，分隔符统一为正斜杠
        assert "\\" not in key
        assert "file.txt" in key


class TestScannerIncremental:
    """Scanner 增量扫描行为测试。"""

    def test_full_scan_builds_manifest(self, tmp_path: Path) -> None:
        """全量扫描后 current_manifest 非 None，含所有通过过滤的文件指纹。"""
        (tmp_path / "secret_a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "secret_b.txt").write_text("y", encoding="utf-8")
        (tmp_path / "readme.md").write_text("z", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_extensions=("txt",))
        scanner.scan(tmp_path)

        manifest = scanner.current_manifest
        assert manifest is not None
        assert IncrementalManifest.rel_key(tmp_path / "secret_a.txt", tmp_path) in manifest.fingerprints
        assert IncrementalManifest.rel_key(tmp_path / "secret_b.txt", tmp_path) in manifest.fingerprints
        # .md 不在白名单，不进入 manifest
        assert IncrementalManifest.rel_key(tmp_path / "readme.md", tmp_path) not in manifest.fingerprints

    def test_incremental_skips_unchanged_files(self, tmp_path: Path) -> None:
        """全量+增量（无变更）：WalkResult.entries 为空，结果与全量一致。"""
        (tmp_path / "secret_a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "secret_b.txt").write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))

        # 全量扫描
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        manifest = scanner1.current_manifest
        assert manifest is not None
        assert report1.stats.matched_files == 2

        # 增量扫描（无变更）
        scanner2 = Scanner(
            rs,
            scan_extensions=("txt",),
            incremental_manifest=manifest,
            prev_report=report1,
        )
        walk_result = scanner2.collect_entries(tmp_path)
        assert len(walk_result.entries) == 0  # 所有文件未变更

        report2 = scanner2.scan_entries(tmp_path, walk_result)
        # 命中结果从 prev_report 合并，与全量一致
        assert report2.stats.matched_files == report1.stats.matched_files
        assert {r.path for r in report2.hits} == {r.path for r in report1.hits}

    def test_incremental_scans_modified_file(self, tmp_path: Path) -> None:
        """修改文件 mtime 后增量扫描：仅扫描修改文件，合并未变更文件命中。"""
        file_a = tmp_path / "secret_a.txt"
        file_b = tmp_path / "secret_b.txt"
        file_a.write_text("x", encoding="utf-8")
        file_b.write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))

        # 全量扫描
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        manifest = scanner1.current_manifest
        assert manifest is not None

        # 修改 file_a 的 mtime（模拟文件变更）
        st = file_a.stat()
        new_mtime = st.st_mtime + 100
        os.utime(file_a, (new_mtime, new_mtime))

        # 增量扫描
        scanner2 = Scanner(
            rs,
            scan_extensions=("txt",),
            incremental_manifest=manifest,
            prev_report=report1,
        )
        walk_result = scanner2.collect_entries(tmp_path)
        assert {e.path.name for e in walk_result.entries} == {"secret_a.txt"}

        report2 = scanner2.scan_entries(tmp_path, walk_result)
        # 修改文件最新命中 + 未变更文件合并命中
        assert report2.stats.matched_files == 2
        assert {r.path.name for r in report2.hits} == {"secret_a.txt", "secret_b.txt"}

    def test_incremental_scans_new_file(self, tmp_path: Path) -> None:
        """新增文件后增量扫描：WalkResult.entries 仅含新文件。"""
        (tmp_path / "secret_a.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))

        # 全量扫描
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        manifest = scanner1.current_manifest
        assert manifest is not None

        # 新增文件
        (tmp_path / "secret_b.txt").write_text("y", encoding="utf-8")

        # 增量扫描
        scanner2 = Scanner(
            rs,
            scan_extensions=("txt",),
            incremental_manifest=manifest,
            prev_report=report1,
        )
        walk_result = scanner2.collect_entries(tmp_path)
        assert {e.path.name for e in walk_result.entries} == {"secret_b.txt"}

        report2 = scanner2.scan_entries(tmp_path, walk_result)
        # 新文件命中 + 旧文件合并命中
        assert report2.stats.matched_files == 2
        assert {r.path.name for r in report2.hits} == {"secret_a.txt", "secret_b.txt"}

    def test_incremental_no_manifest_fallback(self, tmp_path: Path) -> None:
        """无 manifest 时全量扫描，current_manifest 仍构建。"""
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_extensions=("txt",))  # 不传 incremental_manifest
        report = scanner.scan(tmp_path)

        assert scanner.current_manifest is not None
        assert IncrementalManifest.rel_key(tmp_path / "secret.txt", tmp_path) in scanner.current_manifest.fingerprints
        assert report.stats.matched_files == 1

    def test_incremental_deleted_file_not_in_results(self, tmp_path: Path) -> None:
        """删除文件后增量扫描：删除文件不在 walk 结果与新 manifest 中。"""
        file_a = tmp_path / "secret_a.txt"
        file_b = tmp_path / "secret_b.txt"
        file_a.write_text("x", encoding="utf-8")
        file_b.write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))

        # 全量扫描
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        manifest = scanner1.current_manifest
        assert manifest is not None

        # 删除 file_a
        file_a.unlink()

        # 增量扫描
        scanner2 = Scanner(
            rs,
            scan_extensions=("txt",),
            incremental_manifest=manifest,
            prev_report=report1,
        )
        walk_result = scanner2.collect_entries(tmp_path)

        # 删除文件不在 walk 结果中
        assert all(e.path != file_a for e in walk_result.entries)
        # 删除文件不在新 manifest 中
        manifest2 = scanner2.current_manifest
        assert manifest2 is not None
        rel_a = IncrementalManifest.rel_key(file_a, tmp_path)
        assert rel_a not in manifest2.fingerprints
        # 未删除文件仍在 manifest 中
        rel_b = IncrementalManifest.rel_key(file_b, tmp_path)
        assert rel_b in manifest2.fingerprints

    def test_incremental_manifest_merges_old_and_new_fingerprints(self, tmp_path: Path) -> None:
        """增量扫描后 current_manifest 含所有文件（未变更旧指纹 + 变更/新文件新指纹）。"""
        file_a = tmp_path / "secret_a.txt"  # 将修改
        file_b = tmp_path / "secret_b.txt"  # 保持不变
        file_a.write_text("x", encoding="utf-8")
        file_b.write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))

        # 全量扫描
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        scanner1.scan(tmp_path)
        manifest1 = scanner1.current_manifest
        assert manifest1 is not None

        # 修改 file_a 的 mtime + 新增 file_c
        st = file_a.stat()
        os.utime(file_a, (st.st_mtime + 100, st.st_mtime + 100))
        file_c = tmp_path / "secret_c.txt"
        file_c.write_text("z", encoding="utf-8")

        # 增量扫描
        scanner2 = Scanner(rs, scan_extensions=("txt",), incremental_manifest=manifest1)
        scanner2.collect_entries(tmp_path)
        manifest2 = scanner2.current_manifest
        assert manifest2 is not None

        rel_a = IncrementalManifest.rel_key(file_a, tmp_path)
        rel_b = IncrementalManifest.rel_key(file_b, tmp_path)
        rel_c = IncrementalManifest.rel_key(file_c, tmp_path)

        # 三个文件都在新 manifest 中
        assert rel_a in manifest2.fingerprints
        assert rel_b in manifest2.fingerprints
        assert rel_c in manifest2.fingerprints

        # 未变更文件指纹与旧 manifest 一致（复用）
        assert manifest2.fingerprints[rel_b] == manifest1.fingerprints[rel_b]
        # 变更文件指纹与旧 manifest 不同（mtime 改变）
        assert manifest2.fingerprints[rel_a] != manifest1.fingerprints[rel_a]


class TestIncrementalMerge:
    """增量扫描合并逻辑测试。"""

    def test_merge_preserves_unchanged_hits(self, tmp_path: Path) -> None:
        """未变更文件有命中时，合并后仍保留。"""
        (tmp_path / "secret_a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "secret_b.txt").write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))

        # 全量扫描：两个文件都命中
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        assert report1.stats.matched_files == 2
        manifest = scanner1.current_manifest
        assert manifest is not None

        # 增量扫描（无变更）：命中结果应从 prev_report 合并
        scanner2 = Scanner(
            rs,
            scan_extensions=("txt",),
            incremental_manifest=manifest,
            prev_report=report1,
        )
        walk_result = scanner2.collect_entries(tmp_path)
        assert len(walk_result.entries) == 0  # 全部未变更

        report2 = scanner2.scan_entries(tmp_path, walk_result)
        # 合并后命中数与全量一致
        assert report2.stats.matched_files == 2
        hit_paths = {r.path.name for r in report2.hits}
        assert hit_paths == {"secret_a.txt", "secret_b.txt"}

    def test_merge_no_duplicate_for_changed_hit(self, tmp_path: Path) -> None:
        """变更文件本次也有命中时，不重复合并旧命中。"""
        file_a = tmp_path / "secret_a.txt"  # 将修改
        file_b = tmp_path / "secret_b.txt"  # 保持不变
        file_a.write_text("x", encoding="utf-8")
        file_b.write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))

        # 全量扫描：两个文件都命中
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        manifest = scanner1.current_manifest
        assert manifest is not None

        # 修改 file_a 的 mtime
        st = file_a.stat()
        os.utime(file_a, (st.st_mtime + 100, st.st_mtime + 100))

        # 增量扫描
        scanner2 = Scanner(
            rs,
            scan_extensions=("txt",),
            incremental_manifest=manifest,
            prev_report=report1,
        )
        walk_result = scanner2.collect_entries(tmp_path)
        # 仅 file_a 进入扫描队列
        assert {e.path.name for e in walk_result.entries} == {"secret_a.txt"}

        report2 = scanner2.scan_entries(tmp_path, walk_result)
        # 两个文件各出现一次，无重复
        path_counts = Counter(r.path for r in report2.results)
        assert path_counts[file_a] == 1, "变更文件不应被重复合并"
        assert path_counts[file_b] == 1, "未变更文件应被合并一次"
        assert report2.stats.matched_files == 2

    def test_merge_archive_entries_not_merged(self, tmp_path: Path) -> None:
        """archive_path 非 None 的结果不参与合并（每次重新扫描压缩包）。"""
        file_normal = tmp_path / "secret_normal.txt"
        file_normal.write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))

        # 全量扫描获取正常文件的命中与 manifest
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        manifest = scanner1.current_manifest
        assert manifest is not None
        assert report1.stats.matched_files == 1

        normal_hit = report1.hits[0]

        # 构造含压缩包内部条目的 prev_report（archive_path 非 None）
        archive_hit = ScanResult(
            path=Path("archive.zip!inner/secret.txt"),
            size=10,
            hits=(RuleHit("r", Severity.WARNING, "detail"),),
            archive_path=Path("archive.zip"),
        )
        prev_report = ScanReport(
            root=tmp_path,
            results=(normal_hit, archive_hit),
            stats=report1.stats,
        )

        # 增量扫描：正常文件未变更，压缩包条目不应被合并
        scanner2 = Scanner(
            rs,
            scan_extensions=("txt",),
            incremental_manifest=manifest,
            prev_report=prev_report,
        )
        walk_result = scanner2.collect_entries(tmp_path)
        assert len(walk_result.entries) == 0  # 正常文件未变更

        report2 = scanner2.scan_entries(tmp_path, walk_result)
        # 正常文件命中被合并
        assert any(r.path == file_normal for r in report2.results)
        # 压缩包内部条目不被合并
        assert not any(r.archive_path is not None for r in report2.results)
        assert all("archive.zip" not in str(r.path) for r in report2.results)

    def test_merge_excludes_deleted_file_hits(self, tmp_path: Path) -> None:
        """iter-135：删除文件后增量扫描，删除文件的命中结果不应出现在最终 report。"""
        file_a = tmp_path / "secret_a.txt"  # 将删除
        file_b = tmp_path / "secret_b.txt"  # 保持不变
        file_a.write_text("x", encoding="utf-8")
        file_b.write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))

        # 全量扫描：两个文件都命中
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        assert report1.stats.matched_files == 2
        manifest = scanner1.current_manifest
        assert manifest is not None

        # 删除 file_a
        file_a.unlink()

        # 增量扫描
        scanner2 = Scanner(
            rs,
            scan_extensions=("txt",),
            incremental_manifest=manifest,
            prev_report=report1,
        )
        walk_result = scanner2.collect_entries(tmp_path)
        # file_a 已删除，不在 walk 结果中
        assert all(e.path != file_a for e in walk_result.entries)

        report2 = scanner2.scan_entries(tmp_path, walk_result)
        # iter-135 关键断言：删除文件的命中结果不应出现在最终 report
        assert not any(r.path == file_a for r in report2.results), "删除文件的命中结果不应被合并"
        # 未删除文件的命中结果仍保留
        assert any(r.path == file_b for r in report2.results)
        # matched_files 只计未删除文件
        assert report2.stats.matched_files == 1
        # 结果路径集合不包含 file_a
        result_paths = {r.path for r in report2.results}
        assert file_a not in result_paths

    def test_merge_excludes_deleted_file_hits_via_walk_result_manifest(self, tmp_path: Path) -> None:
        """iter-135：ScanWorker precollected 模式下 manifest 经 WalkResult 传递，删除文件被过滤。

        模拟 ScanWorker 用新 Scanner 实例调 scan_entries 的场景：
        collect_entries 在 stats Scanner 实例构建 manifest 并放入 WalkResult，
        scan_entries 在 scan Scanner 实例从 WalkResult 恢复 manifest 用于过滤。
        """
        file_a = tmp_path / "secret_a.txt"  # 将删除
        file_b = tmp_path / "secret_b.txt"  # 保持不变
        file_a.write_text("x", encoding="utf-8")
        file_b.write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "secret"))

        # 全量扫描
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        manifest1 = scanner1.current_manifest
        assert manifest1 is not None
        assert report1.stats.matched_files == 2

        # 删除 file_a
        file_a.unlink()

        # 模拟 FileStatsWorker：用 stats Scanner 跑 collect_entries，产出 WalkResult
        stats_scanner = Scanner(
            rs,
            scan_extensions=("txt",),
            incremental_manifest=manifest1,
        )
        walk_result = stats_scanner.collect_entries(tmp_path)
        # WalkResult.manifest 应非 None（iter-135 新增字段传递）
        assert walk_result.manifest is not None
        # file_a 已删除，不在 manifest.fingerprints 中
        rel_a = IncrementalManifest.rel_key(file_a, tmp_path)
        assert rel_a not in walk_result.manifest.fingerprints

        # 模拟 ScanWorker：用新 scan Scanner 实例调 scan_entries，传 prev_report
        scan_scanner = Scanner(
            rs,
            scan_extensions=("txt",),
            prev_report=report1,
        )
        # scan_scanner 自身 _current_manifest 为 None（未调 collect_entries）
        assert scan_scanner._current_manifest is None

        report2 = scan_scanner.scan_entries(tmp_path, walk_result)
        # scan_entries 从 WalkResult 恢复 manifest，用于过滤已删除文件
        assert scan_scanner._current_manifest is not None
        # 删除文件的命中结果不应出现
        assert not any(r.path == file_a for r in report2.results), "删除文件命中不应经 WalkResult.manifest 过滤后仍出现"
        # 未删除文件命中保留
        assert any(r.path == file_b for r in report2.results)
        assert report2.stats.matched_files == 1
