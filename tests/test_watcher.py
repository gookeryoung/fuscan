"""文件监控与增量扫描单元测试。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from uniscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)
from uniscan.watcher.ignore_dirs import default_ignore_dirs
from uniscan.watcher.incremental import IncrementalScanner
from uniscan.watcher.monitor import (
    FileEvent,
    FileEventType,
    FileMonitor,
    MonitorConfig,
)


def _build_ruleset(*rules: Rule) -> RuleSet:
    return RuleSet(version="1.0", rules=tuple(rules))


def _filename_rule(name: str, pattern: str) -> Rule:
    return Rule(
        name=name,
        severity=Severity.WARNING,
        match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern=pattern),
    )


def _content_rule(name: str, pattern: str) -> Rule:
    return Rule(
        name=name,
        severity=Severity.CRITICAL,
        match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern=pattern),
    )


# ----------------------------- IgnoreDirs -----------------------------


class TestIgnoreDirs:
    def test_default_ignore_dirs_returns_list(self) -> None:
        dirs = default_ignore_dirs()
        assert isinstance(dirs, list)
        assert ".git" in dirs
        assert "__pycache__" in dirs

    def test_default_ignore_dirs_includes_windows_on_win32(self) -> None:
        import sys

        dirs = default_ignore_dirs()
        if sys.platform == "win32":
            assert "Windows" in dirs
            assert "Program Files" in dirs


class TestWatcherLazyImport:
    def test_trayapp_lazy_import(self) -> None:
        """TrayApp 通过 __getattr__ 懒加载，避免无 GUI 环境 import 失败。"""
        import uniscan.watcher as watcher_pkg

        tray_cls = watcher_pkg.TrayApp
        assert tray_cls is not None
        assert tray_cls.__name__ == "TrayApp"

    def test_watcher_getattr_unknown_attribute_raises(self) -> None:
        """访问不存在的属性应抛出 AttributeError。"""
        import uniscan.watcher as watcher_pkg

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = watcher_pkg.NonExistent  # type: ignore[attr-defined]


# ----------------------------- FileMonitor -----------------------------


class TestFileMonitor:
    def test_monitor_starts_and_stops(self, tmp_path: Path) -> None:
        config = MonitorConfig(watch_paths=[tmp_path])
        monitor = FileMonitor(config)
        events: list[FileEvent] = []
        monitor.start(events.append)
        assert monitor.is_running

        # 触发文件创建
        (tmp_path / "test.txt").write_text("hello", encoding="utf-8")
        time.sleep(0.5)  # 等待 watchdog 处理

        monitor.stop()
        assert not monitor.is_running
        assert len(events) > 0
        assert any(e.path.name == "test.txt" for e in events)

    def test_monitor_ignores_dirs(self, tmp_path: Path) -> None:
        config = MonitorConfig(
            watch_paths=[tmp_path],
            ignore_dirs=["ignored"],
        )
        monitor = FileMonitor(config)
        events: list[FileEvent] = []
        monitor.start(events.append)

        # 创建忽略目录内的文件
        ignored_dir = tmp_path / "ignored"
        ignored_dir.mkdir()
        (ignored_dir / "secret.txt").write_text("x", encoding="utf-8")
        time.sleep(0.5)

        # 创建正常文件
        (tmp_path / "normal.txt").write_text("x", encoding="utf-8")
        time.sleep(0.5)

        monitor.stop()
        paths = [e.path for e in events]
        assert any(p.name == "normal.txt" for p in paths)
        assert not any("ignored" in str(p) for p in paths)

    def test_monitor_ignores_extensions(self, tmp_path: Path) -> None:
        config = MonitorConfig(
            watch_paths=[tmp_path],
            ignore_extensions=["pyc", "log"],
        )
        monitor = FileMonitor(config)
        events: list[FileEvent] = []
        monitor.start(events.append)

        (tmp_path / "a.pyc").write_text("x", encoding="utf-8")
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        time.sleep(0.5)

        monitor.stop()
        names = [e.path.name for e in events]
        assert "a.txt" in names
        assert "a.pyc" not in names

    def test_monitor_start_twice_raises(self, tmp_path: Path) -> None:
        config = MonitorConfig(watch_paths=[tmp_path])
        monitor = FileMonitor(config)
        monitor.start(lambda e: None)
        try:
            with pytest.raises(RuntimeError, match="已在运行"):
                monitor.start(lambda e: None)
        finally:
            monitor.stop()

    def test_monitor_no_watch_paths_does_not_start(self) -> None:
        config = MonitorConfig(watch_paths=[])
        monitor = FileMonitor(config)
        monitor.start(lambda e: None)
        assert not monitor.is_running

    def test_monitor_add_watch(self, tmp_path: Path) -> None:
        config = MonitorConfig(watch_paths=[tmp_path])
        monitor = FileMonitor(config)
        events: list[FileEvent] = []
        monitor.start(events.append)

        new_dir = tmp_path / "newdir"
        new_dir.mkdir()
        monitor.add_watch(new_dir)
        (new_dir / "added.txt").write_text("x", encoding="utf-8")
        time.sleep(0.5)

        monitor.stop()
        assert any(e.path.name == "added.txt" for e in events)

    def test_monitor_context_manager(self, tmp_path: Path) -> None:
        config = MonitorConfig(watch_paths=[tmp_path])
        with FileMonitor(config) as monitor:
            monitor.start(lambda e: None)
            assert monitor.is_running
        assert not monitor.is_running

    def test_monitor_deleted_event_removes_from_tracking(self, tmp_path: Path) -> None:
        """删除事件路径不触发扫描回调（由上层处理）。"""
        config = MonitorConfig(watch_paths=[tmp_path], dedup_interval_seconds=0.0)
        monitor = FileMonitor(config)
        events: list[FileEvent] = []
        monitor.start(events.append)

        f = tmp_path / "temp.txt"
        f.write_text("x", encoding="utf-8")
        time.sleep(0.3)
        f.unlink()
        time.sleep(0.3)

        monitor.stop()
        types = [e.event_type for e in events]
        assert FileEventType.CREATED in types
        assert FileEventType.DELETED in types


class TestFileMonitorEdgeCases:
    """FileMonitor 边界条件覆盖。"""

    def test_monitor_watch_paths_property(self, tmp_path: Path) -> None:
        """watch_paths 属性应返回配置的路径列表副本。"""
        config = MonitorConfig(watch_paths=[tmp_path])
        monitor = FileMonitor(config)
        paths = monitor.watch_paths
        assert paths == [tmp_path]
        # 修改返回值不影响内部状态
        paths.append(Path("/other"))
        assert monitor.watch_paths == [tmp_path]

    def test_monitor_stop_when_not_running(self) -> None:
        """未启动时调用 stop 不应出错。"""
        config = MonitorConfig(watch_paths=[])
        monitor = FileMonitor(config)
        monitor.stop()  # 不应抛异常
        assert not monitor.is_running

    def test_monitor_start_with_nonexistent_path(self, tmp_path: Path) -> None:
        """start 时路径不存在应跳过该路径但不报错。"""
        nonexistent = tmp_path / "nonexistent"
        config = MonitorConfig(watch_paths=[nonexistent])
        monitor = FileMonitor(config)
        events: list[FileEvent] = []
        monitor.start(events.append)
        # 无有效路径，监控器仍标记为运行（observer 已 start）
        assert monitor.is_running
        monitor.stop()

    def test_monitor_add_watch_nonexistent(self, tmp_path: Path) -> None:
        """add_watch 传入不存在路径应跳过。"""
        config = MonitorConfig(watch_paths=[tmp_path])
        monitor = FileMonitor(config)
        monitor.start(lambda e: None)
        nonexistent = tmp_path / "nonexistent"
        monitor.add_watch(nonexistent)
        # 不应崩溃
        monitor.stop()

    def test_event_handler_unknown_event_type(self, tmp_path: Path) -> None:
        """未知事件类型应被跳过（返回 None 映射）。"""
        from watchdog.events import FileSystemEvent

        from uniscan.watcher.monitor import _EventHandler

        events: list[FileEvent] = []
        handler = _EventHandler(
            callback=events.append,
            ignore_dirs=set(),
            ignore_extensions=set(),
            dedup_interval=0.0,
        )
        # 构造一个未知事件类型
        event = FileSystemEvent("unknown_event_type")
        event.src_path = str(tmp_path / "test.txt")
        event.is_directory = False
        handler.on_any_event(event)
        assert len(events) == 0

    def test_event_handler_callback_exception_does_not_propagate(self, tmp_path: Path) -> None:
        """回调抛异常时不应传播，仅记录日志。"""
        from watchdog.events import FileSystemEvent

        from uniscan.watcher.monitor import _EventHandler

        def faulty_callback(event: FileEvent) -> None:
            raise RuntimeError("回调异常")

        handler = _EventHandler(
            callback=faulty_callback,
            ignore_dirs=set(),
            ignore_extensions=set(),
            dedup_interval=0.0,
        )
        event = FileSystemEvent("created")
        event.src_path = str(tmp_path / "test.txt")
        event.is_directory = False
        # 不应抛异常
        handler.on_any_event(event)


# ----------------------------- IncrementalScanner -----------------------------


class TestIncrementalScanner:
    def test_first_scan_scans_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        (tmp_path / "b.txt").write_text("normal", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.scanned_files == 2
        assert report.stats.matched_files == 1

    def test_second_scan_skips_unchanged(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)

        # 首次扫描
        scanner.scan(tmp_path)
        assert scanner.tracked_count == 1

        # 二次扫描，文件未变化
        report = scanner.scan(tmp_path)
        assert report.stats.scanned_files == 0
        assert report.stats.skipped_files == 1

    def test_modified_file_is_rescanned(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("normal", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)

        # 首次扫描，无命中
        report1 = scanner.scan(tmp_path)
        assert report1.stats.matched_files == 0

        # 修改文件，加入 password
        time.sleep(0.01)  # 确保 mtime 变化
        f.write_text("password=123", encoding="utf-8")

        # 二次扫描，应重新扫描
        report2 = scanner.scan(tmp_path)
        assert report2.stats.scanned_files == 1
        assert report2.stats.matched_files == 1

    def test_new_file_is_scanned(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("normal", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        scanner.scan(tmp_path)

        # 新增文件
        (tmp_path / "b.txt").write_text("password", encoding="utf-8")
        report = scanner.scan(tmp_path)
        assert report.stats.scanned_files == 1
        assert report.stats.matched_files == 1

    def test_scan_paths_scans_specific_files(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("password", encoding="utf-8")
        f2.write_text("normal", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)

        report = scanner.scan_paths([f1, f2])
        assert report.stats.scanned_files == 2
        assert report.stats.matched_files == 1

    def test_scan_paths_skips_nonexistent(self, tmp_path: Path) -> None:
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        report = scanner.scan_paths([tmp_path / "nonexistent.txt"])
        assert report.stats.scanned_files == 0

    def test_mark_scanned_skips_on_next_scan(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        scanner.mark_scanned(f, f.stat().st_mtime)

        report = scanner.scan(tmp_path)
        assert report.stats.scanned_files == 0
        assert report.stats.skipped_files == 1

    def test_remove_path_clears_state(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        scanner.scan(tmp_path)
        assert scanner.tracked_count == 1

        scanner.remove_path(f)
        assert scanner.tracked_count == 0

        report = scanner.scan(tmp_path)
        assert report.stats.scanned_files == 1

    def test_save_and_load_state(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        scanner.scan(tmp_path)

        # 状态文件保存到扫描目录外，避免被当作新文件扫描
        state_file = tmp_path.parent / f"state_{tmp_path.name}.json"
        scanner.save_state(state_file)
        assert state_file.exists()

        # 新建扫描器，加载状态
        scanner2 = IncrementalScanner(rs)
        scanner2.load_state(state_file)
        assert scanner2.tracked_count == 1

        # 加载状态后，文件未变化应跳过
        report = scanner2.scan(tmp_path)
        assert report.stats.scanned_files == 0
        state_file.unlink()

    def test_load_state_nonexistent_file(self, tmp_path: Path) -> None:
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        scanner.load_state(tmp_path / "nonexistent.json")
        assert scanner.tracked_count == 0

    def test_load_state_corrupted_file(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text("not json {{{", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        scanner.load_state(state_file)
        assert scanner.tracked_count == 0

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
        scanner = IncrementalScanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.scanned_files == 1
        assert report.stats.matched_files == 1


class TestIncrementalScannerErrorPaths:
    """增量扫描器异常路径与边界条件覆盖。"""

    def test_scan_entry_exception_counts_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """scan() 中 _scan_entry 抛异常时应计 error 并继续。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        (tmp_path / "b.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)

        original = scanner._scan_entry

        def faulty(entry):
            if entry.path.name == "a.txt":
                raise RuntimeError("模拟扫描失败")
            return original(entry)

        monkeypatch.setattr(scanner, "_scan_entry", faulty)
        report = scanner.scan(tmp_path)
        assert report.stats.errors >= 1
        assert report.stats.scanned_files >= 1

    def test_scan_paths_skips_directory(self, tmp_path: Path) -> None:
        """scan_paths 传入目录应跳过。"""
        d = tmp_path / "subdir"
        d.mkdir()
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        report = scanner.scan_paths([d])
        assert report.stats.scanned_files == 0

    def test_scan_paths_skips_non_matching_extension(self, tmp_path: Path) -> None:
        """scan_paths 传入不匹配扩展名的文件应跳过。"""
        f = tmp_path / "a.txt"
        f.write_text("password", encoding="utf-8")
        rule = Rule(
            name="conf-only",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            file_extensions=("conf",),
        )
        rs = _build_ruleset(rule)
        scanner = IncrementalScanner(rs)
        report = scanner.scan_paths([f])
        assert report.stats.scanned_files == 0

    def test_scan_paths_entry_exception_counts_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """scan_paths 中 _scan_entry 抛异常时应计 error。"""
        f = tmp_path / "a.txt"
        f.write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)

        def faulty(entry):
            raise RuntimeError("模拟扫描失败")

        monkeypatch.setattr(scanner, "_scan_entry", faulty)
        report = scanner.scan_paths([f])
        assert report.stats.errors >= 1
        assert report.stats.scanned_files >= 1

    def test_should_scan_dir_returns_false(self, tmp_path: Path) -> None:
        """_should_scan 对目录返回 False。"""
        from uniscan.scanner.context import FileEntry

        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        d = tmp_path / "subdir"
        d.mkdir()
        entry = FileEntry(
            path=d,
            name=d.name,
            size=0,
            mtime=d.stat().st_mtime,
            extension="",
            is_dir=True,
        )
        assert scanner._should_scan(entry) is False

    def test_scan_entry_rule_exception_counts_rule_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_scan_entry 中 matcher 抛异常时应计 rule_errors 并继续。"""
        from uniscan.scanner.context import FileEntry
        from uniscan.scanner.matchers import FileNameMatcher

        f = tmp_path / "a.txt"
        f.write_text("password", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "password"))
        scanner = IncrementalScanner(rs)

        # 替换 compiled 中的 matcher 为抛异常的 mock
        rule = scanner._compiled[0][0]

        class FailingMatcher(FileNameMatcher):
            def matches(self, context):
                raise RuntimeError("匹配器异常")

        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="password")
        scanner._compiled = [(rule, FailingMatcher(spec))]

        entry = FileEntry.from_path(f)
        result = scanner._scan_entry(entry)
        assert result.errors >= 1
        assert not result.has_hit
