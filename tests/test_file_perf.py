"""FilePerfRecorder 单文件性能基线记录器测试。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from fuscan.perf import FilePerfDiff, FilePerfRecorder
from fuscan.rules import load_builtin_ruleset
from fuscan.scanner import Scanner


class TestFilePerfRecorderBasic:
    """基础功能：record / records / count。"""

    def test_empty_recorder(self) -> None:
        recorder = FilePerfRecorder()
        assert recorder.count == 0
        assert recorder.records == []

    def test_record_and_count(self) -> None:
        recorder = FilePerfRecorder()
        recorder.record("a.txt", "txt", 100, 1.5, 2)
        recorder.record("b.log", "log", 200, 3.0, 0)
        assert recorder.count == 2
        assert len(recorder.records) == 2

    def test_records_are_copies(self) -> None:
        """records 属性返回副本，修改不影响内部状态。"""
        recorder = FilePerfRecorder()
        recorder.record("a.txt", "txt", 100, 1.0, 0)
        records = recorder.records
        records.clear()
        assert recorder.count == 1


class TestFilePerfRecorderSummary:
    """summary 生成。"""

    def test_empty_summary(self) -> None:
        recorder = FilePerfRecorder()
        s = recorder.summary()
        assert s.total_files == 0
        assert s.total_ms == 0.0
        assert s.slowest == []

    def test_summary_with_records(self) -> None:
        recorder = FilePerfRecorder()
        recorder.record("fast.txt", "txt", 100, 1.0, 0)
        recorder.record("slow.pdf", "pdf", 5000, 50.0, 3)
        recorder.record("mid.txt", "txt", 200, 5.0, 1)
        s = recorder.summary(top=2)
        assert s.total_files == 3
        assert s.total_ms == 56.0
        assert s.avg_ms > 0
        assert s.max_ms == 50.0
        assert s.max_path == "slow.pdf"
        assert len(s.slowest) == 2
        assert s.slowest[0].path == "slow.pdf"
        # 按扩展名分组
        assert "txt" in s.by_extension
        assert "pdf" in s.by_extension
        assert s.by_extension["txt"]["count"] == 2
        assert s.by_extension["pdf"]["count"] == 1


class TestFilePerfRecorderSaveLoad:
    """save_to_json / load_from_json 持久化。"""

    def test_roundtrip(self, tmp_path: Path) -> None:
        recorder = FilePerfRecorder()
        recorder.record("a.txt", "txt", 100, 1.5, 2)
        recorder.record("b.pdf", "pdf", 5000, 30.0, 1)
        json_path = tmp_path / "baseline.json"
        recorder.save_to_json(json_path, meta={"root": "/test"})

        assert json_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert "timestamp" in data
        assert "records" in data
        assert data["meta"]["root"] == "/test"
        assert len(data["records"]) == 2

        loaded = FilePerfRecorder.load_from_json(json_path)
        assert loaded.count == 2
        records = loaded.records
        assert records[0].path == "a.txt"
        assert records[0].total_ms == 1.5
        assert records[1].extension == "pdf"

    def test_save_creates_parent_dir(self, tmp_path: Path) -> None:
        recorder = FilePerfRecorder()
        recorder.record("a.txt", "txt", 10, 0.5, 0)
        nested = tmp_path / "sub" / "dir" / "baseline.json"
        recorder.save_to_json(nested)
        assert nested.exists()


class TestFilePerfRecorderCompare:
    """compare 基线对比。"""

    def test_no_diff_under_threshold(self) -> None:
        baseline = FilePerfRecorder()
        baseline.record("a.txt", "txt", 100, 10.0, 0)
        current = FilePerfRecorder()
        current.record("a.txt", "txt", 100, 10.5, 0)  # +5%，低于 20% 阈值
        diffs = current.compare(baseline, threshold_pct=20.0)
        assert diffs == []

    def test_regression_detected(self) -> None:
        baseline = FilePerfRecorder()
        baseline.record("a.txt", "txt", 100, 10.0, 0)
        current = FilePerfRecorder()
        current.record("a.txt", "txt", 100, 15.0, 0)  # +50%，回归
        diffs = current.compare(baseline, threshold_pct=20.0)
        assert len(diffs) == 1
        assert diffs[0].delta_pct == 50.0
        assert diffs[0].delta_ms == 5.0

    def test_improvement_detected(self) -> None:
        baseline = FilePerfRecorder()
        baseline.record("a.txt", "txt", 100, 10.0, 0)
        current = FilePerfRecorder()
        current.record("a.txt", "txt", 100, 5.0, 0)  # -50%，改善
        diffs = current.compare(baseline, threshold_pct=20.0)
        assert len(diffs) == 1
        assert diffs[0].delta_pct == -50.0

    def test_only_common_files_compared(self) -> None:
        baseline = FilePerfRecorder()
        baseline.record("a.txt", "txt", 100, 10.0, 0)
        current = FilePerfRecorder()
        current.record("a.txt", "txt", 100, 15.0, 0)
        current.record("b.txt", "txt", 100, 5.0, 0)  # 仅在 current 中
        diffs = current.compare(baseline, threshold_pct=20.0)
        assert len(diffs) == 1
        assert diffs[0].path == "a.txt"

    def test_sorted_by_delta_desc(self) -> None:
        baseline = FilePerfRecorder()
        baseline.record("a.txt", "txt", 100, 10.0, 0)
        baseline.record("b.txt", "txt", 100, 10.0, 0)
        current = FilePerfRecorder()
        current.record("a.txt", "txt", 100, 20.0, 0)  # +100%
        current.record("b.txt", "txt", 100, 13.0, 0)  # +30%
        diffs = current.compare(baseline, threshold_pct=20.0)
        assert len(diffs) == 2
        assert diffs[0].delta_pct > diffs[1].delta_pct


class TestFilePerfScannerIntegration:
    """Scanner 集成：file_perf 参数端到端测试。"""

    def test_scanner_records_file_perf(self, tmp_path: Path) -> None:
        """Scanner 在 file_perf 启用时记录每个文件的扫描耗时。"""
        (tmp_path / "a.txt").write_text("password=secret123", encoding="utf-8")
        (tmp_path / "b.txt").write_text("normal content", encoding="utf-8")

        ruleset = load_builtin_ruleset()
        recorder = FilePerfRecorder()
        scanner = Scanner(
            ruleset,
            scan_extensions=ruleset.scan_extensions,
            file_perf=recorder,
        )
        scanner.scan(tmp_path)

        assert recorder.count == 2
        records = {r.path: r for r in recorder.records}
        assert all(r.total_ms >= 0 for r in records.values())
        assert all(r.extension == "txt" for r in records.values())

    def test_scanner_without_file_perf_is_fast_path(self, tmp_path: Path) -> None:
        """file_perf=None 时走快速路径，无记录。"""
        (tmp_path / "a.txt").write_text("test", encoding="utf-8")
        ruleset = load_builtin_ruleset()
        scanner = Scanner(ruleset, scan_extensions=ruleset.scan_extensions)
        scanner.scan(tmp_path)
        # 无异常即通过

    def test_file_perf_save_and_compare(self, tmp_path: Path) -> None:
        """完整流程：扫描 → 保存基线 → 二次扫描 → 对比。"""
        (tmp_path / "a.txt").write_text("password=secret", encoding="utf-8")
        ruleset = load_builtin_ruleset()
        baseline_path = tmp_path / "baseline.json"

        # 第一次扫描：保存基线
        recorder1 = FilePerfRecorder()
        scanner1 = Scanner(
            ruleset,
            scan_extensions=ruleset.scan_extensions,
            file_perf=recorder1,
        )
        scanner1.scan(tmp_path)
        recorder1.save_to_json(baseline_path)

        # 第二次扫描：对比基线
        recorder2 = FilePerfRecorder()
        scanner2 = Scanner(
            ruleset,
            scan_extensions=ruleset.scan_extensions,
            file_perf=recorder2,
        )
        scanner2.scan(tmp_path)

        baseline = FilePerfRecorder.load_from_json(baseline_path)
        # 同样文件、同样规则，差异应在合理范围内（不 assert 具体值）
        diffs = recorder2.compare(baseline, threshold_pct=1000.0)  # 高阈值确保捕获
        # 两次扫描同一文件，可能有些差异
        for d in diffs:
            assert isinstance(d, FilePerfDiff)
            assert d.path.endswith("a.txt")


class TestFilePerfPrintSummary:
    """print_summary 日志输出。"""

    def test_print_empty(self, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[name-defined]
        caplog.set_level(logging.INFO, logger="fuscan.perf")
        recorder = FilePerfRecorder()
        recorder.print_summary()
        assert any("无记录" in r.message for r in caplog.records)

    def test_print_with_records(self, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[name-defined]
        caplog.set_level(logging.INFO, logger="fuscan.perf")
        recorder = FilePerfRecorder()
        recorder.record("a.txt", "txt", 100, 5.0, 2)
        recorder.record("b.pdf", "pdf", 5000, 50.0, 1)
        recorder.print_summary(top=5)
        messages = [r.message for r in caplog.records]
        assert any("单文件性能汇总" in m for m in messages)
        assert any("a.txt" in m for m in messages)
        assert any("b.pdf" in m for m in messages)
