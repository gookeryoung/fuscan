"""托盘驻守应用单元测试。

使用 ``gui`` marker 标记，CI 无 GUI 环境时可通过 ``-m "not gui"`` 跳过。
需要 QApplication 环境（offscreen 平台）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    try:
        from PySide2.QtWidgets import QApplication
    except ImportError:  # pragma: no cover
        from PySide6.QtWidgets import QApplication  # pyrefly: ignore [missing-import]

    from fuscan.rules.model import (
        LeafMatch,
        MatchMode,
        MatchTarget,
        Rule,
        RuleSet,
        Severity,
    )
    from fuscan.watcher.incremental import IncrementalScanner
    from fuscan.watcher.monitor import FileEvent, FileEventType
    from fuscan.watcher.tray import TrayApp

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过托盘测试", allow_module_level=True)


@pytest.fixture(scope="module")
def qapp() -> QApplication:  # type: ignore[misc]
    """模块级 QApplication fixture。"""
    app = QApplication.instance() or QApplication([])
    yield app


def _build_ruleset() -> RuleSet:
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


class TestTrayAppConstruction:
    def test_construct_with_defaults(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs)
        assert not app.is_monitoring
        assert app.tracked_count == 0

    def test_construct_with_watch_paths(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])
        assert not app.is_monitoring
        assert app.tracked_count == 0

    def test_construct_with_state_file(self, qapp: QApplication, tmp_path: Path) -> None:
        """传 state_file 时应调用 load_state（空操作）但不报错。"""
        rs = _build_ruleset()
        state_file = tmp_path / "state.json"
        app = TrayApp(ruleset=rs, state_file=state_file)
        assert not app.is_monitoring

    def test_construct_loads_state_file(self, qapp: QApplication, tmp_path: Path) -> None:
        """传 cache 后 TrayApp 应反映缓存中的已跟踪文件数。"""
        from fuscan.cache import CacheStore

        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        (scan_dir / "a.txt").write_text("x", encoding="utf-8")

        cache = CacheStore(tmp_path / "cache.db")
        try:
            scanner = IncrementalScanner(_build_ruleset(), cache=cache)
            scanner.scan(scan_dir)

            app = TrayApp(ruleset=_build_ruleset(), cache=cache)
            assert app.tracked_count == 1
        finally:
            cache.close()


class TestTrayAppMonitoring:
    def test_start_and_stop_monitoring(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])
        app._init_tray()
        app._init_main_window(show=False)

        app.start_monitoring()
        assert app.is_monitoring

        app.stop_monitoring()
        assert not app.is_monitoring

    def test_start_monitoring_twice_noop(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])
        app._init_tray()
        app._init_main_window(show=False)

        app.start_monitoring()
        first_monitor = app._monitor
        app.start_monitoring()  # 再次启动应无效果
        assert app._monitor is first_monitor

        app.stop_monitoring()

    def test_stop_monitoring_when_not_started(self, qapp: QApplication) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs)
        app.stop_monitoring()  # 不应抛异常
        assert not app.is_monitoring

    def test_toggle_monitoring(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])
        app._init_tray()
        app._init_main_window(show=False)

        assert not app.is_monitoring
        app._toggle_monitoring()
        assert app.is_monitoring
        app._toggle_monitoring()
        assert not app.is_monitoring


class TestTrayAppFileEvent:
    def test_created_event_enqueues_path(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])
        app._init_tray()
        app._init_main_window(show=False)

        f = tmp_path / "new.txt"
        event = FileEvent(event_type=FileEventType.CREATED, path=f)
        app._on_file_event(event)

        assert f in app._pending_paths

    def test_modified_event_enqueues_path(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])

        f = tmp_path / "mod.txt"
        event = FileEvent(event_type=FileEventType.MODIFIED, path=f)
        app._on_file_event(event)

        assert f in app._pending_paths

    def test_deleted_event_removes_path(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])

        f = tmp_path / "del.txt"
        app._scanner.mark_scanned(f, time.time())

        event = FileEvent(event_type=FileEventType.DELETED, path=f)
        app._on_file_event(event)

        assert app.tracked_count == 0
        assert f not in app._pending_paths

    def test_dir_event_ignored(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])

        d = tmp_path / "subdir"
        event = FileEvent(event_type=FileEventType.CREATED, path=d, is_dir=True)
        app._on_file_event(event)

        assert d not in app._pending_paths

    def test_duplicate_path_not_reenqueued(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])

        f = tmp_path / "dup.txt"
        event = FileEvent(event_type=FileEventType.CREATED, path=f)
        app._on_file_event(event)
        app._on_file_event(event)

        assert app._pending_paths.count(f) == 1


class TestTrayAppScanHandling:
    def test_flush_pending_scans_empty_noop(self, qapp: QApplication) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs)
        app._flush_pending_scans()  # 不应抛异常

    def test_flush_pending_scans_processes_paths(self, qapp: QApplication, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("password=123", encoding="utf-8")
        f2.write_text("normal", encoding="utf-8")

        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])
        app._init_tray()
        app._init_main_window(show=False)

        app._pending_paths = [f1, f2]

        reports: list[Any] = []
        app.scan_completed.connect(reports.append)  # pyrefly: ignore [missing-attribute]

        app._flush_pending_scans()

        assert len(reports) == 1
        report = reports[0]
        assert report.stats.scanned_files == 2
        assert report.stats.matched_files == 1
        assert app._pending_paths == []

    def test_handle_scan_result_emits_signals(self, qapp: QApplication, tmp_path: Path) -> None:
        from fuscan.scanner import ScanReport, ScanResult, ScanStats
        from fuscan.scanner.result import RuleHit

        rs = _build_ruleset()
        app = TrayApp(ruleset=rs)
        app._init_tray()
        app._init_main_window(show=False)

        completed_reports: list[Any] = []
        hit_paths: list[Any] = []
        app.scan_completed.connect(completed_reports.append)  # pyrefly: ignore [missing-attribute]
        app.file_hit.connect(lambda path, count: hit_paths.append((path, count)))  # pyrefly: ignore [missing-attribute]

        hit = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="命中 password")
        result = ScanResult(path=tmp_path / "a.txt", size=10, hits=(hit,), errors=0)
        report = ScanReport(
            root=tmp_path,
            results=(result,),
            stats=ScanStats(
                total_files=1,
                scanned_files=1,
                matched_files=1,
                skipped_files=0,
                errors=0,
                duration_seconds=0.01,
            ),
        )

        app._handle_scan_result(report)

        assert len(completed_reports) == 1
        assert len(hit_paths) == 1
        assert hit_paths[0] == (str(tmp_path / "a.txt"), 1)

    def test_handle_scan_result_no_hits_no_signal(self, qapp: QApplication, tmp_path: Path) -> None:
        from fuscan.scanner import ScanReport, ScanResult, ScanStats

        rs = _build_ruleset()
        app = TrayApp(ruleset=rs)

        hit_paths: list[Any] = []
        app.file_hit.connect(lambda path, count: hit_paths.append((path, count)))  # pyrefly: ignore [missing-attribute]

        result = ScanResult(path=tmp_path / "a.txt", size=10, hits=(), errors=0)
        report = ScanReport(
            root=tmp_path,
            results=(result,),
            stats=ScanStats(
                total_files=1,
                scanned_files=1,
                matched_files=0,
                skipped_files=0,
                errors=0,
                duration_seconds=0.01,
            ),
        )

        app._handle_scan_result(report)
        assert hit_paths == []

    def test_handle_scan_result_with_cache_no_error(self, qapp: QApplication, tmp_path: Path) -> None:
        """传 cache 后 _handle_scan_result 不应抛异常。"""
        from fuscan.cache import CacheStore
        from fuscan.scanner import ScanReport, ScanResult, ScanStats

        cache = CacheStore(tmp_path / "cache.db")
        try:
            rs = _build_ruleset()
            app = TrayApp(ruleset=rs, cache=cache)
            app._init_tray()
            app._init_main_window(show=False)

            result = ScanResult(path=tmp_path / "a.txt", size=10, hits=(), errors=0)
            report = ScanReport(
                root=tmp_path,
                results=(result,),
                stats=ScanStats(
                    total_files=1,
                    scanned_files=1,
                    matched_files=0,
                    skipped_files=0,
                    errors=0,
                    duration_seconds=0.01,
                ),
            )

            app._handle_scan_result(report)
        finally:
            cache.close()

    def test_handle_scan_result_with_state_file(self, qapp: QApplication, tmp_path: Path) -> None:
        """传 state_file 后 _handle_scan_result 应调用 save_state（空操作）但不报错。"""
        from fuscan.scanner import ScanReport, ScanResult, ScanStats

        rs = _build_ruleset()
        state_file = tmp_path / "state.json"
        app = TrayApp(ruleset=rs, state_file=state_file)
        app._init_tray()
        app._init_main_window(show=False)

        result = ScanResult(path=tmp_path / "a.txt", size=10, hits=(), errors=0)
        report = ScanReport(
            root=tmp_path,
            results=(result,),
            stats=ScanStats(
                total_files=1,
                scanned_files=1,
                matched_files=0,
                skipped_files=0,
                errors=0,
                duration_seconds=0.01,
            ),
        )
        # save_state 为空操作，不应抛异常
        app._handle_scan_result(report)


class TestTrayAppFullScan:
    def test_full_scan_no_paths_notifies(self, qapp: QApplication) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs)
        app._init_tray()
        app._init_main_window(show=False)

        # 无监控路径时调用 _full_scan 不应抛异常
        app._full_scan()

    def test_full_scan_with_paths_starts_worker(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])
        app._init_tray()
        app._init_main_window(show=False)

        # 用 fake 替换 ScanWorker，避免真实 QThread 导致测试崩溃
        class FakeSignal:
            def connect(self, _cb: object) -> None:
                pass

        class FakeWorker:
            def __init__(self, **kwargs: object) -> None:
                self.finished_report = FakeSignal()

            def start(self) -> None:
                pass

        import fuscan.workers as workers_mod

        monkeypatch.setattr(workers_mod, "ScanWorker", FakeWorker)

        app._full_scan()
        assert app._scan_worker is not None
        assert isinstance(app._scan_worker, FakeWorker)


class TestTrayAppQuit:
    def test_quit_stops_monitoring(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])
        app._init_tray()
        app._init_main_window(show=False)
        app.start_monitoring()
        assert app.is_monitoring

        app._quit()
        assert not app.is_monitoring

    def test_quit_closes_cache(self, qapp: QApplication, tmp_path: Path) -> None:
        """_quit 应关闭 cache，cache.db 文件应存在。"""
        from fuscan.cache import CacheStore

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, cache=cache)
        app._init_tray()
        app._init_main_window(show=False)

        app._quit()
        assert cache_path.exists()


class TestTrayAppShowWindow:
    def test_show_main_window(self, qapp: QApplication, tmp_path: Path) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs, watch_paths=[tmp_path])
        app._init_tray()
        app._init_main_window(show=False)

        app._show_main_window()
        assert app._main_window is not None
        assert app._main_window.isVisible()

    def test_show_main_window_when_none(self, qapp: QApplication) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs)
        # 未初始化主窗口时调用不应抛异常
        app._show_main_window()


class TestTrayAppInit:
    def test_init_tray_creates_icon(self, qapp: QApplication) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs)
        app._init_tray()
        assert app._tray is not None
        assert app._tray_menu is not None

    def test_init_main_window_hidden(self, qapp: QApplication) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs)
        app._init_main_window(show=False)
        assert app._main_window is not None
        assert not app._main_window.isVisible()

    def test_init_main_window_visible(self, qapp: QApplication) -> None:
        rs = _build_ruleset()
        app = TrayApp(ruleset=rs)
        app._init_main_window(show=True)
        assert app._main_window is not None
        assert app._main_window.isVisible()
        app._main_window.close()
