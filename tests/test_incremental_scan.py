"""iter-124 增量扫描测试。

覆盖 ``IncrementalManifest`` 序列化、``Scanner`` 增量扫描行为与合并逻辑。
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any

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
from fuscan.scanner.manifest import FileFingerprint, IncrementalManifest
from fuscan.scanner.result import RuleHit


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


class TestIter150Sha1PrefixCompat:
    """FileFingerprint.sha1_prefix 字段与 JSON 前/向后兼容测试。"""

    def test_new_fingerprint_sha1_none_default(self) -> None:
        """新构造无 sha1_prefix 时默认 None，与旧二元组语义一致。"""
        fp = FileFingerprint(mtime=1000.0, size=10)
        assert fp.sha1_prefix is None
        assert FileFingerprint(mtime=1000.0, size=10) == FileFingerprint(mtime=1000.0, size=10, sha1_prefix=None)

    def test_sha1_none_omitted_in_json(self) -> None:
        """sha1_prefix 为 None 时 to_json 省略键，老版本 fuscan 可读。"""
        fps = {"a.txt": FileFingerprint(mtime=1000.0, size=10)}
        manifest = IncrementalManifest(root=Path("/root"), fingerprints=fps)
        json_str = manifest.to_json()
        # 不应包含 "sha1_prefix" 字样
        assert "sha1_prefix" not in json_str
        # 从 JSON 还原后 sha1 仍为 None
        restored = IncrementalManifest.from_json(json_str)
        assert restored.fingerprints["a.txt"].sha1_prefix is None

    def test_sha1_kept_roundtrip(self) -> None:
        """sha1_prefix 非 None 时 to_json 写入，from_json 可还原。"""
        fps = {
            "a.txt": FileFingerprint(mtime=1000.0, size=10, sha1_prefix="abcd1234ef567890"),
            "b.txt": FileFingerprint(mtime=2000.0, size=20),  # sha1=None
        }
        manifest = IncrementalManifest(root=Path("/root"), fingerprints=fps)
        json_str = manifest.to_json()
        assert '"sha1_prefix": "abcd1234ef567890"' in json_str
        restored = IncrementalManifest.from_json(json_str)
        assert restored.fingerprints["a.txt"].sha1_prefix == "abcd1234ef567890"
        assert restored.fingerprints["b.txt"].sha1_prefix is None

    def test_old_json_no_sha1_reads_none(self) -> None:
        """旧格式 JSON（无 sha1_prefix）经 from_json 读入后 sha1 为 None。"""
        import json as _json

        old_format = _json.dumps(
            {
                "root": "/root",
                "fingerprints": {
                    "a.txt": {"mtime": 1000.0, "size": 10},
                    "b.txt": {"mtime": 2000.0, "size": 20},
                },
            }
        )
        restored = IncrementalManifest.from_json(old_format)
        assert len(restored.fingerprints) == 2
        for fp in restored.fingerprints.values():
            assert fp.sha1_prefix is None
        # mtime/size 保持正确
        assert restored.fingerprints["a.txt"].mtime == 1000.0
        assert restored.fingerprints["b.txt"].size == 20

    def test_invalid_sha1_value_falls_back_to_none(self) -> None:
        """非法 sha1 值（非字符串/空串）读入后回退为 None，不抛异常。"""
        import json as _json

        bad = _json.dumps(
            {
                "root": "/r",
                "fingerprints": {
                    "a.txt": {"mtime": 1.0, "size": 1, "sha1_prefix": 123},  # 非字符串
                    "b.txt": {"mtime": 2.0, "size": 2, "sha1_prefix": ""},  # 空字符串
                },
            }
        )
        restored = IncrementalManifest.from_json(bad)
        assert restored.fingerprints["a.txt"].sha1_prefix is None
        assert restored.fingerprints["b.txt"].sha1_prefix is None


class TestIter150ScanStatsUnchanged:
    """ScanStats.unchanged_files 字段与增量扫描统计联动。"""

    def test_full_scan_unchanged_zero(self, tmp_path: Path) -> None:
        """全量扫描 unchanged_files == 0。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "a"))
        scanner = Scanner(rs, scan_extensions=("txt",))
        report = scanner.scan(tmp_path)
        assert report.stats.unchanged_files == 0

    def test_incremental_full_unchanged(self, tmp_path: Path) -> None:
        """100% 未变更增量扫描：unchanged_files == 文件总数，summary 显示复用。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "txt"))
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        manifest = scanner1.current_manifest
        assert manifest is not None

        scanner2 = Scanner(rs, scan_extensions=("txt",), incremental_manifest=manifest, prev_report=report1)
        walk_result = scanner2.collect_entries(tmp_path)
        report2 = scanner2.scan_entries(tmp_path, walk_result)
        # 两个 txt 文件全未变更
        assert report2.stats.unchanged_files == 2
        # summary 应含「复用 2」
        assert "复用 2" in report2.stats.summary()
        # speed 计算包含 unchanged_files（2 逻辑处理文件 / 耗时）
        # duration>0 时至少 > 0
        if report2.stats.duration_seconds > 0:
            assert report2.stats.speed > 0

    def test_partial_unchanged_counts_correctly(self, tmp_path: Path) -> None:
        """部分变更场景：1 改 + 1 未改 → unchanged_files == 1。"""
        file_a = tmp_path / "a.txt"
        file_b = tmp_path / "b.txt"
        file_a.write_text("x", encoding="utf-8")
        file_b.write_text("y", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "txt"))
        scanner1 = Scanner(rs, scan_extensions=("txt",))
        report1 = scanner1.scan(tmp_path)
        manifest = scanner1.current_manifest
        assert manifest is not None

        st = file_a.stat()
        os.utime(file_a, (st.st_mtime + 100, st.st_mtime + 100))

        scanner2 = Scanner(rs, scan_extensions=("txt",), incremental_manifest=manifest, prev_report=report1)
        walk_result = scanner2.collect_entries(tmp_path)
        report2 = scanner2.scan_entries(tmp_path, walk_result)
        assert report2.stats.unchanged_files == 1
        assert "复用 1" in report2.stats.summary()


class TestIter150Benchmark:
    """增量扫描基准：100% unchanged 场景 >= 1000 files/s。"""

    N = 3000  # 文件数；1000 files/s 目标意味着总耗时 <= 3s 即可达标

    @pytest.mark.benchmark(min_rounds=3, max_time=10.0, warmup=False)
    def test_incremental_throughput_ge_1000_files_per_sec(self, tmp_path: Path, benchmark: Any) -> None:
        """增量全量未变更（walk+manifest 比对 + 合并）：功能验证 + throughput >= 1000。

        ``--benchmark-disable`` 时 benchmark.stats 为 None，仅验证功能正确性；
        启用 benchmark 时才校验吞吐量数字，避免禁用模式下 AttributeError。
        """
        n = self.N
        for i in range(n):
            (tmp_path / f"f_{i:05d}.txt").write_text(f"c{i}", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "000000000"))
        s1 = Scanner(rs, scan_extensions=("txt",), max_workers=1)
        prev = s1.scan(tmp_path)
        manifest = s1.current_manifest
        assert manifest is not None
        assert prev.stats.total_files >= n

        def run_incremental() -> None:
            s = Scanner(
                rs,
                scan_extensions=("txt",),
                max_workers=1,
                incremental_manifest=manifest,
                prev_report=prev,
            )
            wr = s.collect_entries(tmp_path)
            rep = s.scan_entries(tmp_path, wr)
            assert len(wr.entries) == 0, "100% 未变更场景 entries 应为空"
            assert rep.stats.unchanged_files == n

        benchmark(run_incremental)
        # 仅在 benchmark 收集到统计时校验 throughput 数字
        stats = getattr(benchmark, "stats", None)
        if stats is not None and getattr(stats, "mean", None) is not None:
            avg_s = stats.mean  # 单位：秒
            throughput = n / avg_s if avg_s > 0 else float("inf")
            assert throughput >= 1000, (
                f"增量吞吐量 {throughput:.0f} files/s 未达 >= 1000 目标 (N={n}, mean={avg_s * 1000:.2f}ms)"
            )
