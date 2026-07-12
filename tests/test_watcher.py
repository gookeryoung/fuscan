"""文件监控与增量扫描单元测试。"""

from __future__ import annotations

import time
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
from fuscan.watcher.ignore_dirs import default_ignore_dirs
from fuscan.watcher.incremental import IncrementalScanner
from fuscan.watcher.monitor import (
    FileEvent,
    FileEventType,
    FileMonitor,
    MonitorConfig,
)


def _build_ruleset(*rules: Rule) -> RuleSet:
    return RuleSet(version="1.0", rules=tuple(rules))


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
        import fuscan.watcher as watcher_pkg

        tray_cls = watcher_pkg.TrayApp
        assert tray_cls is not None
        assert tray_cls.__name__ == "TrayApp"

    def test_watcher_getattr_unknown_attribute_raises(self) -> None:
        """访问不存在的属性应抛出 AttributeError。"""
        import fuscan.watcher as watcher_pkg

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

        from fuscan.watcher.monitor import _EventHandler

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

        from fuscan.watcher.monitor import _EventHandler

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
    """无 cache 模式下 IncrementalScanner 委托 Scanner 全量扫描。"""

    def test_first_scan_scans_all(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        (tmp_path / "b.txt").write_text("normal", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        report = scanner.scan(tmp_path)
        assert report.stats.scanned_files == 2
        assert report.stats.matched_files == 1

    def test_new_file_is_scanned(self, tmp_path: Path) -> None:
        """无 cache 时每次 scan 都全量扫描，新增文件也被扫描。"""
        (tmp_path / "a.txt").write_text("normal", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        scanner.scan(tmp_path)

        # 新增文件
        (tmp_path / "b.txt").write_text("password", encoding="utf-8")
        report = scanner.scan(tmp_path)
        # 无 cache：两次都全量扫描
        assert report.stats.scanned_files == 2
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

    def test_scan_paths_handles_scan_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """scan_paths 中 scan_file 抛异常时应记录错误而非崩溃。"""
        f = tmp_path / "a.txt"
        f.write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)

        def raising_scan_file(path: Path) -> None:
            raise RuntimeError("scan error")

        monkeypatch.setattr(scanner._scanner, "scan_file", raising_scan_file)
        report = scanner.scan_paths([f])
        assert report.stats.scanned_files == 1
        assert report.stats.errors == 1

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

    def test_tracked_count_zero_without_cache(self, tmp_path: Path) -> None:
        """无 cache 时 tracked_count 始终为 0。"""
        (tmp_path / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        scanner.scan(tmp_path)
        assert scanner.tracked_count == 0

    def test_noop_methods_dont_raise(self, tmp_path: Path) -> None:
        """mark_scanned/remove_path/save_state/load_state 空操作不抛异常。"""
        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = IncrementalScanner(rs)
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        # 空操作：不应抛异常
        scanner.mark_scanned(f, f.stat().st_mtime)
        scanner.remove_path(f)
        scanner.save_state(tmp_path / "state.json")
        scanner.load_state(tmp_path / "nonexistent.json")


class TestIncrementalScannerCache:
    """有 cache 模式下 IncrementalScanner 的哈希缓存增量行为。

    cache.db 放在 tmp_path 根目录，扫描目标放在 scan 子目录，避免缓存文件被扫描。
    """

    def test_cache_hit_skips_second_scan(self, tmp_path: Path) -> None:
        """传 cache 后第二次扫描相同文件应复用缓存结果，命中信息一致。"""
        from fuscan.cache import CacheStore

        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        (scan_dir / "a.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        cache = CacheStore(tmp_path / "cache.db")
        try:
            scanner = IncrementalScanner(rs, cache=cache)

            # 首次扫描
            report1 = scanner.scan(scan_dir)
            assert report1.stats.scanned_files == 1
            assert report1.stats.matched_files == 1
            assert scanner.tracked_count == 1

            # 二次扫描，文件未变化 → 缓存命中，结果一致
            report2 = scanner.scan(scan_dir)
            assert report2.stats.matched_files == 1
            assert report2.hits[0].hits[0].rule_name == "r"
        finally:
            cache.close()

    def test_content_change_triggers_rescan(self, tmp_path: Path) -> None:
        """传 cache 后修改文件内容应触发重新扫描。"""
        from fuscan.cache import CacheStore

        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        f = scan_dir / "a.txt"
        f.write_text("normal", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        cache = CacheStore(tmp_path / "cache.db")
        try:
            scanner = IncrementalScanner(rs, cache=cache)

            # 首次扫描，无命中
            report1 = scanner.scan(scan_dir)
            assert report1.stats.matched_files == 0

            # 修改文件内容，加入 password
            f.write_text("password=123", encoding="utf-8")

            # 二次扫描，内容变化 → 重新扫描
            report2 = scanner.scan(scan_dir)
            assert report2.stats.scanned_files == 1
            assert report2.stats.matched_files == 1
        finally:
            cache.close()

    def test_rule_change_triggers_rescan(self, tmp_path: Path) -> None:
        """传 cache 后规则 pattern 变化应触发重新扫描。"""
        from fuscan.cache import CacheStore

        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        (scan_dir / "a.txt").write_text("secret123", encoding="utf-8")
        cache = CacheStore(tmp_path / "cache.db")
        try:
            # 首次用 "password" 规则扫描，无命中
            rs1 = _build_ruleset(_content_rule("r", "password"))
            scanner1 = IncrementalScanner(rs1, cache=cache)
            report1 = scanner1.scan(scan_dir)
            assert report1.stats.scanned_files == 1
            assert report1.stats.matched_files == 0

            # 换用 "secret" 规则扫描，规则哈希变化 → 重新扫描
            rs2 = _build_ruleset(_content_rule("r", "secret"))
            scanner2 = IncrementalScanner(rs2, cache=cache)
            report2 = scanner2.scan(scan_dir)
            assert report2.stats.scanned_files == 1
            assert report2.stats.matched_files == 1
        finally:
            cache.close()

    def test_tracked_count_reflects_cache(self, tmp_path: Path) -> None:
        """tracked_count 应反映 cache.stats().scanned_files。"""
        from fuscan.cache import CacheStore

        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        (scan_dir / "a.txt").write_text("x", encoding="utf-8")
        (scan_dir / "b.txt").write_text("y", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        cache = CacheStore(tmp_path / "cache.db")
        try:
            scanner = IncrementalScanner(rs, cache=cache)
            assert scanner.tracked_count == 0
            scanner.scan(scan_dir)
            assert scanner.tracked_count == 2
        finally:
            cache.close()

    def test_scan_paths_uses_cache(self, tmp_path: Path) -> None:
        """scan_paths 传 cache 后第二次调用相同文件应复用缓存结果。"""
        from fuscan.cache import CacheStore

        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        f = scan_dir / "a.txt"
        f.write_text("password", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "password"))
        cache = CacheStore(tmp_path / "cache.db")
        try:
            scanner = IncrementalScanner(rs, cache=cache)

            report1 = scanner.scan_paths([f])
            assert report1.stats.scanned_files == 1
            assert report1.stats.matched_files == 1

            # 二次 scan_paths 同一文件，缓存命中，结果一致
            report2 = scanner.scan_paths([f])
            assert report2.stats.matched_files == 1
            assert report2.hits[0].hits[0].rule_name == "r"
        finally:
            cache.close()

    def test_path_independence(self, tmp_path: Path) -> None:
        """同内容文件不同路径，缓存应命中（路径无关），结果一致。"""
        from fuscan.cache import CacheStore

        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        (dir1 / "a.txt").write_text("password", encoding="utf-8")
        (dir2 / "b.txt").write_text("password", encoding="utf-8")  # 同内容不同路径

        rs = _build_ruleset(_content_rule("r", "password"))
        cache = CacheStore(tmp_path / "cache.db")
        try:
            scanner = IncrementalScanner(rs, cache=cache)

            # 扫描 dir1
            report1 = scanner.scan(dir1)
            assert report1.stats.scanned_files == 1
            assert report1.stats.matched_files == 1

            # 扫描 dir2：文件哈希相同 → 缓存命中，结果一致
            report2 = scanner.scan(dir2)
            assert report2.stats.matched_files == 1
            assert report2.hits[0].hits[0].rule_name == "r"
        finally:
            cache.close()
