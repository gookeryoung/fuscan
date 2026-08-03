"""``workers`` 子包三个 QThread Worker 的单元测试。

覆盖 :class:`ExportWorker` / :class:`ScanWorker` / :class:`FileStatsWorker`。
策略（参考 SKILL「QThread 测试崩溃」踩坑）：

- **不真实 start() QThread**：Windows 上真实 QThread + PySide2 会导致
  ``STATUS_STACK_BUFFER_OVERRUN`` 崩溃
- **直接调用 ``worker.run()`` 同步执行**：run() 是普通方法，内部
  ``self._scanner`` 由 ``monkeypatch`` 替换为 ``FakeScanner``，
  避免依赖文件系统与真实扫描逻辑
- **信号通过真实 PySide2 Signal emit**：用 ``worker.finished_report.connect``
  注册 lambda 收集 emit 的载荷，断言内容
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from fuscan.gui.models.result_model import SORT_FILE_PATH, SORT_SEVERITY
    from fuscan.gui.workers import ExportWorker, FileStatsWorker, ScanWorker
    from fuscan.gui.workers.filter_worker import FilterWorker
    from fuscan.gui.workers.restore_worker import ResultRestoreWorker
    from fuscan.rules.model import (
        LeafMatch,
        MatchMode,
        MatchTarget,
        Rule,
        RuleSet,
        Severity,
    )
    from fuscan.scanner import ScanReport, ScanResult, ScanStats
    from fuscan.scanner.result import ProgressInfo, RuleHit, WalkResult

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过 worker 测试", allow_module_level=True)


# ============================== 测试夹具与辅助 ==============================


def _build_ruleset() -> RuleSet:
    """构造最小 RuleSet 供 worker 测试使用。"""
    return RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="敏感内容",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            ),
        ),
    )


def _make_scan_result(path: Path, *, hits: tuple[RuleHit, ...] = (), errors: int = 0) -> ScanResult:
    """构造 ScanResult 测试数据。"""
    return ScanResult(path=path, size=100, hits=hits, errors=errors)


def _make_scan_report(
    root: Path,
    *,
    results: tuple[ScanResult, ...] = (),
    cancelled: bool = False,
    total: int = 0,
    scanned: int = 0,
    matched: int = 0,
    skipped: int = 0,
    errors: int = 0,
    matches: int = 0,
    user_skipped: int = 0,
) -> ScanReport:
    """构造 ScanReport 测试数据。"""
    return ScanReport(
        root=root,
        results=results,
        cancelled=cancelled,
        stats=ScanStats(
            total_files=total,
            scanned_files=scanned,
            matched_files=matched,
            skipped_files=skipped,
            errors=errors,
            total_matches=matches,
            user_skipped=user_skipped,
            perf_summary={},
        ),
    )


def _make_walk_result(
    root: Path,
    *,
    total: int = 0,
    skipped: int = 0,
    user_skipped: int = 0,
    cancelled: bool = False,
) -> WalkResult:
    """构造 WalkResult 测试数据。"""
    return WalkResult(
        root=root,
        entries=(),
        total=total,
        skipped=skipped,
        user_skipped=user_skipped,
        cancelled=cancelled,
    )


class FakeScanner:
    """模拟 :class:`fuscan.scanner.scanner.Scanner`，避免依赖文件系统。

    记录构造参数与各方法调用情况，``scan``/``collect_entries``/``scan_entries``
    返回预设的 ``ScanReport``/``WalkResult``，可配置取消行为。
    """

    instances: list[FakeScanner] = []
    # 类级配置：每次构造返回的 ScanReport / WalkResult
    next_scan_report: ScanReport | None = None
    next_walk_result: WalkResult | None = None
    # 多根路径模式：按调用顺序返回列表中的下一项
    scan_reports_queue: list[ScanReport] | None = None
    walk_results_queue: list[WalkResult] | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.__class__.instances.append(self)
        self.kwargs = kwargs
        self.pause_called = False
        self.resume_called = False
        self.cancel_called = False
        self.scan_calls: list[Path] = []
        self.collect_calls: list[Path] = []
        self.scan_entries_calls: list[tuple[Path, WalkResult]] = []

    def pause(self) -> None:
        self.pause_called = True

    def resume(self) -> None:
        self.resume_called = True

    def cancel(self) -> None:
        self.cancel_called = True

    def scan(self, root: Path) -> ScanReport:
        self.scan_calls.append(root)
        if self.__class__.scan_reports_queue:
            return self.__class__.scan_reports_queue.pop(0)
        return self.__class__.next_scan_report or _make_scan_report(root)

    def collect_entries(self, root: Path) -> WalkResult:
        self.collect_calls.append(root)
        if self.__class__.walk_results_queue:
            return self.__class__.walk_results_queue.pop(0)
        return self.__class__.next_walk_result or _make_walk_result(root)

    def scan_entries(self, root: Path, walk_result: WalkResult) -> ScanReport:
        self.scan_entries_calls.append((root, walk_result))
        if self.__class__.scan_reports_queue:
            return self.__class__.scan_reports_queue.pop(0)
        return self.__class__.next_scan_report or _make_scan_report(root)


@pytest.fixture()
def ruleset() -> RuleSet:
    """最小 RuleSet。"""
    return _build_ruleset()


@pytest.fixture(autouse=True)
def reset_fake_scanner() -> None:
    """每个测试前重置 FakeScanner 类级状态。"""
    FakeScanner.instances.clear()
    FakeScanner.next_scan_report = None
    FakeScanner.next_walk_result = None
    FakeScanner.scan_reports_queue = None
    FakeScanner.walk_results_queue = None


@pytest.fixture()
def patch_scanner(monkeypatch: pytest.MonkeyPatch) -> type[FakeScanner]:
    """替换 ScanWorker/FileStatsWorker 中的 Scanner 为 FakeScanner。"""
    monkeypatch.setattr("fuscan.gui.workers.scan_worker.Scanner", FakeScanner)
    monkeypatch.setattr("fuscan.gui.workers.stats_worker.Scanner", FakeScanner)
    return FakeScanner


# ============================== ExportWorker ==============================


class TestExportWorker:
    """``ExportWorker`` 测试：导出成功 / OSError / 通用异常。"""

    def test_run_success_emits_finished_ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run() 成功时 emit finished_ok 携带路径。"""
        saved: list[tuple[ScanReport, Path]] = []
        monkeypatch.setattr(
            "fuscan.gui.workers.export_worker.save_report",
            lambda report, path: saved.append((report, path)),
        )
        report = _make_scan_report(tmp_path)
        export_path = tmp_path / "report.json"
        worker = ExportWorker(report, export_path)

        finished_payloads: list[Path] = []
        failed_payloads: list[str] = []
        worker.finished_ok.connect(finished_payloads.append)  # pyrefly: ignore [missing-attribute]
        worker.failed.connect(failed_payloads.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert saved == [(report, export_path)]
        assert finished_payloads == [export_path]
        assert failed_payloads == []

    def test_run_oserror_emits_failed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run() 抛 OSError 时 emit failed 携带错误信息。"""
        monkeypatch.setattr(
            "fuscan.gui.workers.export_worker.save_report",
            lambda report, path: (_ for _ in ()).throw(OSError("磁盘已满")),
        )
        report = _make_scan_report(tmp_path)
        worker = ExportWorker(report, tmp_path / "report.json")

        finished_payloads: list[Path] = []
        failed_payloads: list[str] = []
        worker.finished_ok.connect(finished_payloads.append)  # pyrefly: ignore [missing-attribute]
        worker.failed.connect(failed_payloads.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert finished_payloads == []
        assert len(failed_payloads) == 1
        assert "磁盘已满" in failed_payloads[0]


# ============================== ScanWorker ==============================


class TestScanWorkerInit:
    """``ScanWorker`` 构造与初始状态。"""

    def test_init_records_params(self, ruleset: RuleSet, tmp_path: Path) -> None:
        """构造参数应记录到私有字段供 run() 使用。"""
        roots = [tmp_path]
        worker = ScanWorker(
            ruleset=ruleset,
            roots=roots,
            max_depth=5,
            scan_archives=True,
            max_workers=3,
            max_file_size=1024,
            ignore_dirs=(".git",),
            progress_interval=0.5,
            scan_extensions=(".txt",),
            skip_paths=frozenset({"/skip"}),
        )
        assert worker._ruleset is ruleset
        assert worker._roots == roots
        assert worker._max_depth == 5
        assert worker._scan_archives is True
        assert worker._max_workers == 3
        assert worker._max_file_size == 1024
        assert worker._ignore_dirs == (".git",)
        assert worker._progress_interval == 0.5
        assert worker._scan_extensions == (".txt",)
        assert worker._skip_paths == frozenset({"/skip"})
        assert worker._scanner is None
        assert worker._cancel_requested is False
        assert worker._precollected is None

    def test_init_defaults(self, ruleset: RuleSet, tmp_path: Path) -> None:
        """默认参数下可选字段应为空值。"""
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        assert worker._max_depth is None
        assert worker._scan_archives is False
        assert worker._max_workers is None
        assert worker._max_file_size is None
        assert worker._ignore_dirs == ()
        assert worker._source_files is None
        assert worker._scan_extensions is None
        assert worker._skip_paths == frozenset()
        assert worker._precollected is None


class TestScanWorkerControl:
    """``ScanWorker`` pause/resume/cancel 控制接口。"""

    def test_pause_noop_when_scanner_none(self, ruleset: RuleSet, tmp_path: Path) -> None:
        """_scanner 为 None 时 pause() 不抛异常。"""
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        worker.pause()  # 不应抛异常

    def test_resume_noop_when_scanner_none(self, ruleset: RuleSet, tmp_path: Path) -> None:
        """_scanner 为 None 时 resume() 不抛异常。"""
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        worker.resume()

    def test_cancel_sets_flag_when_scanner_none(self, ruleset: RuleSet, tmp_path: Path) -> None:
        """_scanner 为 None 时 cancel() 仅设置标志位。"""
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        worker.cancel()
        assert worker._cancel_requested is True
        assert worker._scanner is None

    def test_pause_delegates_to_scanner(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """_scanner 非 None 时 pause() 委托给 scanner.pause()。"""
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        scanner = FakeScanner()
        worker._scanner = scanner  # pyrefly: ignore [bad-assignment]
        worker.pause()
        assert scanner.pause_called is True

    def test_resume_delegates_to_scanner(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """_scanner 非 None 时 resume() 委托给 scanner.resume()。"""
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        scanner = FakeScanner()
        worker._scanner = scanner  # pyrefly: ignore [bad-assignment]
        worker.resume()
        assert scanner.resume_called is True

    def test_cancel_delegates_to_scanner(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """_scanner 非 None 时 cancel() 同时设置标志位与委托。"""
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        scanner = FakeScanner()
        worker._scanner = scanner  # pyrefly: ignore [bad-assignment]
        worker.cancel()
        assert worker._cancel_requested is True
        assert scanner.cancel_called is True

    def test_start_uses_low_priority(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """start() 默认以 QThread.LowPriority 启动，缓解与主线程的 GIL 争抢。

        不真实 spawn 线程（Windows 上会崩溃），仅拦截 QThread.start 捕获优先级。
        """
        from PySide2.QtCore import QThread

        captured: list[object] = []
        monkeypatch.setattr(QThread, "start", lambda self, priority: captured.append(priority))
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        worker.start()
        assert captured == [QThread.LowPriority]


class TestScanWorkerRun:
    """``ScanWorker.run()`` 主流程：成功 / 取消 / 异常 / precollected / 多根路径。"""

    def test_run_single_root_emits_finished_report(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """单根路径扫描成功 emit finished_report 携带合并后的 ScanReport。"""
        result = _make_scan_result(tmp_path / "a.txt")
        FakeScanner.next_scan_report = _make_scan_report(
            tmp_path,
            results=(result,),
            total=1,
            scanned=1,
        )
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])

        finished: list[ScanReport] = []
        cancelled: list[ScanReport] = []
        failed: list[str] = []
        worker.finished_report.connect(finished.append)  # pyrefly: ignore [missing-attribute]
        worker.cancelled.connect(cancelled.append)  # pyrefly: ignore [missing-attribute]
        worker.failed.connect(failed.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert len(finished) == 1
        assert cancelled == []
        assert failed == []
        report = finished[0]
        assert report.cancelled is False
        assert report.results == (result,)
        assert report.root == tmp_path
        # 累计统计已合并
        assert report.stats.total_files == 1
        assert report.stats.scanned_files == 1

    def test_run_multi_root_aggregates_stats(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """多根路径扫描累计统计字段，root 显示「（多路径）」。"""
        root1 = tmp_path / "r1"
        root2 = tmp_path / "r2"
        root1.mkdir()
        root2.mkdir()
        FakeScanner.scan_reports_queue = [
            _make_scan_report(root1, total=10, scanned=5, matched=2, errors=1, matches=3),
            _make_scan_report(root2, total=20, scanned=15, matched=4, errors=0, matches=5),
        ]
        worker = ScanWorker(ruleset=ruleset, roots=[root1, root2])

        finished: list[ScanReport] = []
        worker.finished_report.connect(finished.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert len(finished) == 1
        report = finished[0]
        assert report.root == Path("（多路径）")
        assert report.stats.total_files == 30  # 10 + 20
        assert report.stats.scanned_files == 20  # 5 + 15
        assert report.stats.matched_files == 6  # 2 + 4
        assert report.stats.errors == 1  # 1 + 0
        assert report.stats.total_matches == 8  # 3 + 5

    def test_run_cancelled_emits_cancelled_report(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """扫描被取消时 emit cancelled 携带部分结果。"""
        FakeScanner.next_scan_report = _make_scan_report(
            tmp_path,
            cancelled=True,
            total=5,
            scanned=2,
        )
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])

        finished: list[ScanReport] = []
        cancelled: list[ScanReport] = []
        worker.finished_report.connect(finished.append)  # pyrefly: ignore [missing-attribute]
        worker.cancelled.connect(cancelled.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert finished == []
        assert len(cancelled) == 1
        assert cancelled[0].cancelled is True

    def test_run_precollected_mode_calls_scan_entries(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """precollected 非 None 时跳过 walk，调用 scan_entries。"""
        walk_result = _make_walk_result(tmp_path, total=3)
        FakeScanner.next_scan_report = _make_scan_report(
            tmp_path,
            total=3,
            scanned=3,
        )
        worker = ScanWorker(
            ruleset=ruleset,
            roots=[tmp_path],
            precollected=[walk_result],
        )

        finished: list[ScanReport] = []
        worker.finished_report.connect(finished.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert len(finished) == 1
        assert len(FakeScanner.instances) == 1
        scanner = FakeScanner.instances[0]
        assert scanner.scan_calls == []
        assert scanner.scan_entries_calls == [(tmp_path, walk_result)]

    def test_run_precollected_cancelled_breaks_loop(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """precollected 模式下中途取消应中断循环 emit cancelled。"""
        wr1 = _make_walk_result(tmp_path / "r1")
        wr2 = _make_walk_result(tmp_path / "r2")
        FakeScanner.scan_reports_queue = [
            _make_scan_report(tmp_path / "r1", cancelled=True, total=1, scanned=1),
            _make_scan_report(tmp_path / "r2", total=2, scanned=2),
        ]
        worker = ScanWorker(
            ruleset=ruleset,
            roots=[tmp_path / "r1", tmp_path / "r2"],
            precollected=[wr1, wr2],
        )

        finished: list[ScanReport] = []
        cancelled: list[ScanReport] = []
        worker.finished_report.connect(finished.append)  # pyrefly: ignore [missing-attribute]
        worker.cancelled.connect(cancelled.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert finished == []
        assert len(cancelled) == 1
        # 第二个根路径不应被扫描
        assert len(FakeScanner.instances[0].scan_entries_calls) == 1

    def test_run_exception_emits_failed(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run() 内部抛异常时 emit failed 携带错误信息。"""

        class BoomScanner:
            def __init__(self, **kwargs: Any) -> None:
                pass

            def scan(self, root: Path) -> ScanReport:
                raise RuntimeError("scanner boom")

        monkeypatch.setattr("fuscan.gui.workers.scan_worker.Scanner", BoomScanner)
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])

        finished: list[ScanReport] = []
        failed: list[str] = []
        worker.finished_report.connect(finished.append)  # pyrefly: ignore [missing-attribute]
        worker.failed.connect(failed.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert finished == []
        assert len(failed) == 1
        assert "scanner boom" in failed[0]

    def test_run_propagates_cancel_request_to_scanner(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """构造前已 cancel_requested 时，run() 在构造 Scanner 后立即 cancel。"""
        FakeScanner.next_scan_report = _make_scan_report(tmp_path)
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        worker._cancel_requested = True  # 模拟扫描前用户已点取消

        worker.run()

        assert len(FakeScanner.instances) == 1
        assert FakeScanner.instances[0].cancel_called is True

    def test_on_progress_emits_accumulated_info(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """_on_progress 累加前序根路径的累计统计后 emit。"""
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        # 模拟前序根路径已扫描 5 个文件
        worker._cum_scanned = 5
        worker._cum_total = 10
        worker._cum_matched = 2
        worker._cum_errors = 1
        worker._cum_skipped = 3
        worker._cum_matches = 4
        worker._cum_user_skipped = 1
        worker._start_time = 0.0  # elapsed = monotonic() - 0

        payloads: list[ProgressInfo] = []
        worker.progress_info.connect(payloads.append)  # pyrefly: ignore [missing-attribute]

        worker._on_progress(
            ProgressInfo(
                current_file="x.txt",
                scanned=3,
                total=5,
                skipped=1,
                matched=1,
                errors=0,
                matches=2,
                user_skipped=0,
            )
        )

        assert len(payloads) == 1
        info = payloads[0]
        assert info.scanned == 8  # 5 + 3
        assert info.total == 15  # 10 + 5
        assert info.matched == 3  # 2 + 1
        assert info.errors == 1  # 1 + 0
        assert info.skipped == 4  # 3 + 1
        assert info.matches == 6  # 4 + 2
        assert info.user_skipped == 1  # 1 + 0
        assert info.current_file == "x.txt"

    def test_accumulate_report_merges_perf_summary(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
    ) -> None:
        """_accumulate_report 合并 perf_summary 到 _perf。"""
        worker = ScanWorker(ruleset=ruleset, roots=[tmp_path])
        report = _make_scan_report(tmp_path, total=5, scanned=5)
        # 注入 perf_summary
        object.__setattr__(
            report.stats,
            "perf_summary",
            {"extract": {"total_ms": 100.0, "count": 5, "max_ms": 30.0}},
        )
        worker._accumulate_report(report)
        assert worker._cum_scanned == 5
        assert worker._cum_total == 5
        assert "extract" in worker._perf.to_dict()


# ============================== FileStatsWorker ==============================


class TestStatsWorkerInit:
    """``FileStatsWorker`` 构造与初始状态。"""

    def test_init_records_params(self, ruleset: RuleSet, tmp_path: Path) -> None:
        """构造参数应记录到私有字段供 run() 使用。"""
        worker = FileStatsWorker(
            ruleset=ruleset,
            roots=[tmp_path],
            max_depth=3,
            scan_archives=True,
            ignore_dirs=("__pycache__",),
            progress_interval=0.2,
            scan_extensions=(".pdf",),
            skip_paths=frozenset({"/skip"}),
        )
        assert worker._ruleset is ruleset
        assert worker._roots == [tmp_path]
        assert worker._max_depth == 3
        assert worker._scan_archives is True
        assert worker._ignore_dirs == ("__pycache__",)
        assert worker._progress_interval == 0.2
        assert worker._scan_extensions == (".pdf",)
        assert worker._skip_paths == frozenset({"/skip"})
        assert worker._scanner is None
        assert worker._cancel_requested is False

    def test_init_defaults(self, ruleset: RuleSet, tmp_path: Path) -> None:
        """默认参数下可选字段应为空值。"""
        worker = FileStatsWorker(ruleset=ruleset, roots=[tmp_path])
        assert worker._max_depth is None
        assert worker._scan_archives is False
        assert worker._ignore_dirs == ()
        assert worker._scan_extensions is None
        assert worker._skip_paths == frozenset()


class TestStatsWorkerControl:
    """``FileStatsWorker`` pause/resume/cancel 控制接口。"""

    def test_pause_noop_when_scanner_none(self, ruleset: RuleSet, tmp_path: Path) -> None:
        worker = FileStatsWorker(ruleset=ruleset, roots=[tmp_path])
        worker.pause()

    def test_resume_noop_when_scanner_none(self, ruleset: RuleSet, tmp_path: Path) -> None:
        worker = FileStatsWorker(ruleset=ruleset, roots=[tmp_path])
        worker.resume()

    def test_cancel_sets_flag_when_scanner_none(self, ruleset: RuleSet, tmp_path: Path) -> None:
        worker = FileStatsWorker(ruleset=ruleset, roots=[tmp_path])
        worker.cancel()
        assert worker._cancel_requested is True

    def test_control_delegates_to_scanner(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        worker = FileStatsWorker(ruleset=ruleset, roots=[tmp_path])
        scanner = FakeScanner()
        worker._scanner = scanner  # pyrefly: ignore [bad-assignment]
        worker.pause()
        worker.resume()
        worker.cancel()
        assert scanner.pause_called is True
        assert scanner.resume_called is True
        assert scanner.cancel_called is True

    def test_start_uses_low_priority(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """start() 默认以 QThread.LowPriority 启动，与 ScanWorker 保持一致。"""
        from PySide2.QtCore import QThread

        captured: list[object] = []
        monkeypatch.setattr(QThread, "start", lambda self, priority: captured.append(priority))
        worker = FileStatsWorker(ruleset=ruleset, roots=[tmp_path])
        worker.start()
        assert captured == [QThread.LowPriority]


class TestStatsWorkerRun:
    """``FileStatsWorker.run()`` 主流程。"""

    def test_run_single_root_emits_finished_stats(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """单根路径统计成功 emit finished_stats。"""
        wr = _make_walk_result(tmp_path, total=5, skipped=1, user_skipped=0)
        FakeScanner.next_walk_result = wr
        worker = FileStatsWorker(ruleset=ruleset, roots=[tmp_path])

        finished: list[list[WalkResult]] = []
        cancelled: list[list[WalkResult]] = []
        failed: list[str] = []
        worker.finished_stats.connect(finished.append)  # pyrefly: ignore [missing-attribute]
        worker.cancelled.connect(cancelled.append)  # pyrefly: ignore [missing-attribute]
        worker.failed.connect(failed.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert len(finished) == 1
        assert cancelled == []
        assert failed == []
        assert finished[0] == [wr]

    def test_run_multi_root_aggregates(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """多根路径统计累计 total/skipped/user_skipped。"""
        root1 = tmp_path / "r1"
        root2 = tmp_path / "r2"
        root1.mkdir()
        root2.mkdir()
        FakeScanner.walk_results_queue = [
            _make_walk_result(root1, total=10, skipped=2, user_skipped=1),
            _make_walk_result(root2, total=20, skipped=3, user_skipped=0),
        ]
        worker = FileStatsWorker(ruleset=ruleset, roots=[root1, root2])

        finished: list[list[WalkResult]] = []
        worker.finished_stats.connect(finished.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert len(finished) == 1
        results = finished[0]
        assert len(results) == 2
        # 累计值用于 _on_progress，验证已合并
        assert worker._cum_total == 30  # 10 + 20
        assert worker._cum_skipped == 5  # 2 + 3
        assert worker._cum_user_skipped == 1  # 1 + 0

    def test_run_cancelled_breaks_loop(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """中途取消应中断循环 emit cancelled 携带部分结果。"""
        root1 = tmp_path / "r1"
        root2 = tmp_path / "r2"
        root1.mkdir()
        root2.mkdir()
        FakeScanner.walk_results_queue = [
            _make_walk_result(root1, cancelled=True, total=1),
            _make_walk_result(root2, total=2),
        ]
        worker = FileStatsWorker(ruleset=ruleset, roots=[root1, root2])

        finished: list[list[WalkResult]] = []
        cancelled: list[list[WalkResult]] = []
        worker.finished_stats.connect(finished.append)  # pyrefly: ignore [missing-attribute]
        worker.cancelled.connect(cancelled.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert finished == []
        assert len(cancelled) == 1
        # 第二个根路径不应被 collect
        assert len(FakeScanner.instances[0].collect_calls) == 1

    def test_run_exception_emits_failed(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run() 内部抛异常时 emit failed。"""

        class BoomScanner:
            def __init__(self, **kwargs: Any) -> None:
                pass

            def collect_entries(self, root: Path) -> WalkResult:
                raise RuntimeError("stats boom")

        monkeypatch.setattr("fuscan.gui.workers.stats_worker.Scanner", BoomScanner)
        worker = FileStatsWorker(ruleset=ruleset, roots=[tmp_path])

        finished: list[list[WalkResult]] = []
        failed: list[str] = []
        worker.finished_stats.connect(finished.append)  # pyrefly: ignore [missing-attribute]
        worker.failed.connect(failed.append)  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert finished == []
        assert len(failed) == 1
        assert "stats boom" in failed[0]

    def test_run_propagates_cancel_request_to_scanner(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """构造前已 cancel_requested 时，run() 立即取消 scanner。"""
        FakeScanner.next_walk_result = _make_walk_result(tmp_path)
        worker = FileStatsWorker(ruleset=ruleset, roots=[tmp_path])
        worker._cancel_requested = True

        worker.run()

        assert FakeScanner.instances[0].cancel_called is True

    def test_on_progress_emits_accumulated_info(
        self,
        ruleset: RuleSet,
        tmp_path: Path,
        patch_scanner: type[FakeScanner],
    ) -> None:
        """_on_progress 累加前序根路径的 total/skipped/user_skipped 后 emit。"""
        worker = FileStatsWorker(ruleset=ruleset, roots=[tmp_path])
        worker._cum_total = 5
        worker._cum_skipped = 2
        worker._cum_user_skipped = 1
        worker._start_time = 0.0

        payloads: list[ProgressInfo] = []
        worker.progress_info.connect(payloads.append)  # pyrefly: ignore [missing-attribute]

        worker._on_progress(
            ProgressInfo(
                current_file="y.txt",
                scanned=0,
                total=3,
                skipped=1,
                user_skipped=0,
            )
        )

        assert len(payloads) == 1
        info = payloads[0]
        # walk 阶段 scanned/matched/errors/matches 不累计，恒为传入值
        assert info.scanned == 0
        assert info.total == 8  # 5 + 3
        assert info.skipped == 3  # 2 + 1
        assert info.user_skipped == 1  # 1 + 0
        assert info.current_file == "y.txt"


# ============================== ResultRestoreWorker ==============================


class TestResultRestoreWorker:
    """``ResultRestoreWorker`` 测试：成功恢复 / 文件不存在 / JSON 格式错误。

    覆盖 :meth:`run` 的成功与失败路径，确保 ``restore_done``/``restore_failed``
    信号正确发射。
    """

    def test_run_success_emits_restore_done(self, tmp_path: Path) -> None:
        """缓存文件有效时 emit restore_done 携带反序列化的 ScanReport。"""
        hit = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="d1")
        report = _make_scan_report(
            tmp_path,
            results=(_make_scan_result(tmp_path / "a.txt", hits=(hit,)),),
            total=1,
            scanned=1,
            matched=1,
            matches=1,
        )
        cache_file = tmp_path / "cache.json"
        cache_file.write_bytes(report.to_json_bytes())

        worker = ResultRestoreWorker("ws-1", cache_file)

        done_payloads: list[tuple[str, ScanReport]] = []
        failed_payloads: list[tuple[str, str]] = []
        worker.restore_done.connect(  # pyrefly: ignore [missing-attribute]
            lambda ws_id, report: done_payloads.append((ws_id, report))
        )
        worker.restore_failed.connect(  # pyrefly: ignore [missing-attribute]
            lambda ws_id, err: failed_payloads.append((ws_id, err))
        )

        worker.run()

        assert len(done_payloads) == 1
        assert failed_payloads == []
        ws_id, restored = done_payloads[0]
        assert ws_id == "ws-1"
        assert restored.root == tmp_path
        assert len(restored.hits) == 1
        assert restored.hits[0].path == tmp_path / "a.txt"

    def test_run_missing_file_emits_restore_failed(self, tmp_path: Path) -> None:
        """缓存文件不存在时 emit restore_failed 携带错误信息。"""
        missing = tmp_path / "nonexistent.json"
        worker = ResultRestoreWorker("ws-2", missing)

        done_payloads: list[tuple[str, ScanReport]] = []
        failed_payloads: list[tuple[str, str]] = []
        worker.restore_done.connect(  # pyrefly: ignore [missing-attribute]
            lambda ws_id, report: done_payloads.append((ws_id, report))
        )
        worker.restore_failed.connect(  # pyrefly: ignore [missing-attribute]
            lambda ws_id, err: failed_payloads.append((ws_id, err))
        )

        worker.run()

        assert done_payloads == []
        assert len(failed_payloads) == 1
        ws_id, err_msg = failed_payloads[0]
        assert ws_id == "ws-2"
        # 错误信息应包含文件名或系统错误
        assert err_msg

    def test_run_invalid_json_emits_restore_failed(self, tmp_path: Path) -> None:
        """缓存文件 JSON 格式错误时 emit restore_failed。"""
        cache_file = tmp_path / "broken.json"
        cache_file.write_bytes(b"{not valid json")
        worker = ResultRestoreWorker("ws-3", cache_file)

        done_payloads: list[tuple[str, ScanReport]] = []
        failed_payloads: list[tuple[str, str]] = []
        worker.restore_done.connect(  # pyrefly: ignore [missing-attribute]
            lambda ws_id, report: done_payloads.append((ws_id, report))
        )
        worker.restore_failed.connect(  # pyrefly: ignore [missing-attribute]
            lambda ws_id, err: failed_payloads.append((ws_id, err))
        )

        worker.run()

        assert done_payloads == []
        assert len(failed_payloads) == 1
        ws_id, err_msg = failed_payloads[0]
        assert ws_id == "ws-3"
        assert err_msg


# ============================== FilterWorker ==============================


class TestFilterWorker:
    """``FilterWorker`` 测试：过滤+排序后 emit done 携带结果元组。"""

    def test_run_emits_filtered_sorted_results(self, tmp_path: Path) -> None:
        """run() 调用 filter_and_sort 并通过 done 信号回传结果。

        iter-165：done 信号现回传三元组 (filtered, severity_index, rule_index)。
        """
        h_critical = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="d1")
        h_warning = RuleHit(rule_name="API 密钥", severity=Severity.WARNING, detail="d2")
        results = (
            ScanResult(path=tmp_path / "b.txt", size=20, hits=(h_warning,), errors=0),
            ScanResult(path=tmp_path / "a.txt", size=10, hits=(h_critical,), errors=0),
        )
        worker = FilterWorker(
            results=results,
            filter_text="",
            filter_rules=frozenset(),
            filter_severities=frozenset(),
            sort_field=SORT_FILE_PATH,
            sort_ascending=True,
        )

        done_payloads: list[tuple[object, ...]] = []
        worker.done.connect(lambda *args: done_payloads.append(args))  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert len(done_payloads) == 1
        payload = done_payloads[0]
        # iter-165：信号现回传 (filtered, severity_index, rule_index) 三元组
        assert len(payload) == 3
        filtered = payload[0]
        assert isinstance(filtered, tuple)
        assert len(filtered) == 2
        # 按文件路径升序：a.txt 在前
        assert filtered[0].path == tmp_path / "a.txt"
        assert filtered[1].path == tmp_path / "b.txt"

    def test_run_applies_severity_filter(self, tmp_path: Path) -> None:
        """filter_severities 非空时仅保留匹配严重度的结果。"""
        h_critical = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="d1")
        h_info = RuleHit(rule_name="提示信息", severity=Severity.INFO, detail="d2")
        results = (
            ScanResult(path=tmp_path / "a.txt", size=10, hits=(h_critical,), errors=0),
            ScanResult(path=tmp_path / "b.txt", size=20, hits=(h_info,), errors=0),
        )
        worker = FilterWorker(
            results=results,
            filter_text="",
            filter_rules=frozenset(),
            filter_severities=frozenset({Severity.CRITICAL}),
            sort_field=SORT_SEVERITY,
            sort_ascending=False,
        )

        done_payloads: list[tuple[object, ...]] = []
        worker.done.connect(lambda *args: done_payloads.append(args))  # pyrefly: ignore [missing-attribute]

        worker.run()

        assert len(done_payloads) == 1
        payload = done_payloads[0]
        filtered = payload[0]
        assert isinstance(filtered, tuple)
        assert len(filtered) == 1
        assert filtered[0].path == tmp_path / "a.txt"
