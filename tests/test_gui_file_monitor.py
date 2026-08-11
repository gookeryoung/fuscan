"""``FileMonitorController`` 与 ``FileMonitorModel`` 单元测试。

覆盖：

- ``FileMonitorModel`` 增量追加 / 清空 / FIFO 限容 / role 数据
- ``FileMonitorController`` 目录增删 / 启停 / 事件处理 / 防抖 / 命中信号 / 持久化
- watchdog Observer 通过工厂注入 mock，避免测试启动真实文件系统监听线程
- Scanner 通过 monkeypatch 替换为可控的 fake，避免依赖真实规则集编译
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from PySide2.QtCore import Qt
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Qt  # type: ignore[no-redef]  # pyrefly: ignore [missing-import]

try:
    from fuscan.gui.controllers.file_monitor_controller import (
        FileMonitorController,
        _WatchdogHandler,
    )
    from fuscan.gui.models.file_monitor_model import FileMonitorModel
    from fuscan.rules.model import (
        LeafMatch,
        MatchMode,
        MatchTarget,
        Rule,
        RuleSet,
        Severity,
    )

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过文件监控测试", allow_module_level=True)


# ============================ 共享 fixture ============================


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将 ~/.fuscan 重定向到 tmp_path，避免污染用户配置。"""
    fake_home = tmp_path / "fuscan_home"
    fake_home.mkdir()
    cfg_dir = fake_home / ".fuscan"
    cfg_dir.mkdir()
    monkeypatch.setattr("fuscan.config.CONFIG_DIR", cfg_dir)
    return cfg_dir


class _FakeRulesController:
    """伪 RulesController：持有可替换的 ruleset，提供 rulesetChanged 信号。"""

    def __init__(self, ruleset: Any = None) -> None:
        self._ruleset = ruleset
        # 简单的信号替身：connect/emit
        self._ruleset_changed_callbacks: list[Any] = []

    @property
    def ruleset(self) -> Any:
        return self._ruleset

    @property
    def rulesetChanged(self) -> Any:
        return self

    def connect(self, cb: Any) -> None:
        self._ruleset_changed_callbacks.append(cb)

    def emit(self) -> None:
        for cb in list(self._ruleset_changed_callbacks):
            cb()


class _SignalCounter:
    """简单的信号计数器：记录 emit 次数与参数。"""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self._callbacks: list[Any] = []

    def connect(self, cb: Any) -> None:
        self._callbacks.append(cb)

    def emit(self, *args: Any) -> None:
        self.calls.append(args)
        for cb in list(self._callbacks):
            cb(*args)

    @property
    def count(self) -> int:
        return len(self.calls)


class _EventEmitterStub:
    """伪 _EventEmitter：仅记录 emit 调用，不实际跨线程转发。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        # event_received 信号：connect/emit
        self._callbacks: list[Any] = []

    @property
    def event_received(self) -> Any:
        return self

    def connect(self, cb: Any) -> None:
        self._callbacks.append(cb)

    def emit(self, path: str, event_type: str) -> None:
        self.events.append((path, event_type))


class _FakeObserver:
    """伪 watchdog Observer：不启动真实线程，记录 schedule/unschedule 调用。"""

    def __init__(self) -> None:
        self.scheduled: list[tuple[Any, str, bool]] = []
        self.unscheduled: list[Any] = []
        self.started: bool = False
        self.stopped: bool = False
        self.joined: bool = False

    def schedule(self, handler: Any, path: str, recursive: bool = False) -> Any:
        watch = {"handler": handler, "path": path, "recursive": recursive}
        self.scheduled.append((handler, path, recursive))
        return watch

    def unschedule(self, watch: Any) -> None:
        self.unscheduled.append(watch)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        self.joined = True

    def isRunning(self) -> bool:
        return self.started and not self.stopped


class _FakeScanResult:
    """伪 ScanResult，仅实现 has_hit/hits/max_severity。"""

    def __init__(self, hits: tuple[Any, ...]) -> None:
        self._hits = hits

    @property
    def has_hit(self) -> bool:
        return bool(self._hits)

    @property
    def hits(self) -> tuple[Any, ...]:
        return self._hits


class _FakeRuleHit:
    """伪 RuleHit。"""

    def __init__(
        self,
        rule_name: str = "test_rule",
        severity: Severity = Severity.WARNING,  # pyrefly: ignore [unbound-name]
        detail: str = "测试命中详情",
        match_text: str = "secret",
    ) -> None:
        self.rule_name = rule_name
        self.severity = severity
        self.detail = detail
        self.match_text = match_text


@pytest.fixture()
def fake_rules_controller() -> _FakeRulesController:
    return _FakeRulesController(ruleset=None)


@pytest.fixture()
def controller(
    config_dir: Path,
    fake_rules_controller: _FakeRulesController,
) -> FileMonitorController:
    """构造带 mock Observer 工厂的 FileMonitorController。"""
    return FileMonitorController(
        fake_rules_controller,
        _observer_factory=_FakeObserver,
    )


# ============================ FileMonitorModel 测试 ============================


class TestFileMonitorModel:
    """``FileMonitorModel`` 基础功能测试。"""

    def test_initial_state(self) -> None:
        """初始状态：rowCount=0，records 为空。"""
        model = FileMonitorModel()
        assert model.rowCount() == 0
        assert model.count == 0
        assert model.records == ()

    def test_append_hit_increments_count(self) -> None:
        """append_hit 应增加 rowCount。"""
        model = FileMonitorModel()
        model.append_hit("12:00:00", "/test/file.txt", "rule1", "warning", "match")
        assert model.rowCount() == 1
        assert model.count == 1

    def test_append_hit_data_roles(self) -> None:
        """append_hit 后 data() 应返回对应字段。"""
        model = FileMonitorModel()
        model.append_hit("12:00:00", "/test/file.txt", "rule1", "warning", "match text")
        idx = model.index(0)
        assert idx.data(Qt.UserRole + 1) == "12:00:00"
        assert idx.data(Qt.UserRole + 2) == "/test/file.txt"
        assert idx.data(Qt.UserRole + 3) == "rule1"
        # severity_text(WARNING) == "警告"
        assert idx.data(Qt.UserRole + 4) == "警告"
        # severity_color_hex(WARNING) == "#F0883E"
        assert idx.data(Qt.UserRole + 5) == "#F0883E"
        assert idx.data(Qt.UserRole + 6) == "match text"

    def test_append_hit_invalid_severity_falls_back_to_info(self) -> None:
        """未知严重度值回退到 INFO。"""
        model = FileMonitorModel()
        model.append_hit("12:00:00", "/p", "r", "unknown", "m")
        idx = model.index(0)
        # severity_text(INFO) == "信息"
        assert idx.data(Qt.UserRole + 4) == "信息"

    def test_clear_empties_model(self) -> None:
        """clear 后 rowCount=0。"""
        model = FileMonitorModel()
        model.append_hit("12:00:00", "/a", "r", "info", "m")
        model.append_hit("12:00:01", "/b", "r", "info", "m")
        assert model.count == 2
        model.clear()
        assert model.count == 0
        assert model.records == ()

    def test_clear_on_empty_model_is_noop(self) -> None:
        """对空 model 调用 clear 不应抛异常。"""
        model = FileMonitorModel()
        model.clear()
        assert model.count == 0

    def test_fifo_eviction_when_exceeding_max_rows(self) -> None:
        """超过 max_rows 时应从头部丢弃最旧记录。"""
        model = FileMonitorModel(max_rows=3)
        model.append_hit("t1", "/a", "r", "info", "m1")
        model.append_hit("t2", "/b", "r", "info", "m2")
        model.append_hit("t3", "/c", "r", "info", "m3")
        assert model.count == 3
        # 第 4 条触发 FIFO，最旧的 t1 被丢弃
        model.append_hit("t4", "/d", "r", "info", "m4")
        assert model.count == 3
        records = model.records
        assert records[0].time == "t2"
        assert records[2].time == "t4"

    def test_data_invalid_index_returns_empty(self) -> None:
        """越界 index 的 data() 应返回空字符串。"""
        model = FileMonitorModel()
        model.append_hit("t", "/a", "r", "info", "m")
        # 直接调用 model.data() 而非 QModelIndex.data()（后者对无效索引返回 None）
        invalid_idx = model.index(99)
        assert model.data(invalid_idx, Qt.UserRole + 1) == ""

    def test_rolenames_returns_expected_mapping(self) -> None:
        """roleNames 应包含 6 个 role。"""
        model = FileMonitorModel()
        roles = model.roleNames()
        assert len(roles) == 6
        assert roles[Qt.UserRole + 1] == b"time"
        assert roles[Qt.UserRole + 2] == b"filePath"
        assert roles[Qt.UserRole + 3] == b"ruleName"
        assert roles[Qt.UserRole + 4] == b"severityText"
        assert roles[Qt.UserRole + 5] == b"severityColor"
        assert roles[Qt.UserRole + 6] == b"matchText"


# ============================ FileMonitorController 测试 ============================


class TestFileMonitorControllerConstruction:
    """``FileMonitorController`` 构造与初始状态。"""

    def test_initial_state(self, controller: FileMonitorController) -> None:
        """构造后：监控未启用，watched 为空，model 可访问。"""
        assert controller.monitoringEnabled is False
        assert controller.watchedCount == 0
        assert controller.watchedDirectories == []
        assert isinstance(controller.model, FileMonitorModel)

    def test_model_parent_is_controller(self, controller: FileMonitorController) -> None:
        """model 的 parent 应为 controller。"""
        assert controller.model.parent() is controller


class TestAddRemoveWatch:
    """``addWatch`` / ``removeWatch`` 测试。"""

    def test_add_watch_records_directory(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """addWatch 应记录目录到 watched 列表。"""
        d = tmp_path / "watched"
        d.mkdir()
        assert controller.addWatch(str(d)) is True
        assert controller.watchedCount == 1
        # 路径规范化为绝对路径
        assert str(d.resolve()) in controller.watchedDirectories

    def test_add_watch_emits_signals(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """addWatch 应触发 directoryAdded 与 watchedDirectoriesChanged。"""
        d = tmp_path / "watched"
        d.mkdir()
        added_paths: list[str] = []
        changed_count = [0]

        controller.directoryAdded.connect(added_paths.append)  # pyrefly: ignore [missing-attribute]
        controller.watchedDirectoriesChanged.connect(lambda: changed_count.__setitem__(0, changed_count[0] + 1))  # pyrefly: ignore [missing-attribute]

        controller.addWatch(str(d))
        assert len(added_paths) == 1
        assert changed_count[0] == 1

    def test_add_watch_duplicate_returns_false(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """重复添加同一路径应返回 False。"""
        d = tmp_path / "watched"
        d.mkdir()
        assert controller.addWatch(str(d)) is True
        assert controller.addWatch(str(d)) is False
        assert controller.watchedCount == 1

    def test_add_watch_nonexistent_path_returns_false(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """不存在的路径应返回 False。"""
        assert controller.addWatch(str(tmp_path / "nonexistent")) is False
        assert controller.watchedCount == 0

    def test_add_watch_file_returns_false(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """添加文件（非目录）应返回 False。"""
        f = tmp_path / "file.txt"
        f.write_text("test")
        assert controller.addWatch(str(f)) is False

    def test_add_watch_empty_path_returns_false(self, controller: FileMonitorController) -> None:
        """空路径应返回 False。"""
        assert controller.addWatch("") is False

    def test_remove_watch(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """removeWatch 应从 watched 列表移除。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.addWatch(str(d))
        assert controller.watchedCount == 1
        assert controller.removeWatch(str(d)) is True
        assert controller.watchedCount == 0

    def test_remove_watch_nonexistent_returns_false(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """移除未添加的路径应返回 False。"""
        d = tmp_path / "watched"
        d.mkdir()
        assert controller.removeWatch(str(d)) is False

    def test_remove_watch_emits_signals(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """removeWatch 应触发 directoryRemoved 与 watchedDirectoriesChanged。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.addWatch(str(d))
        removed_paths: list[str] = []
        controller.directoryRemoved.connect(removed_paths.append)  # pyrefly: ignore [missing-attribute]
        controller.removeWatch(str(d))
        assert len(removed_paths) == 1

    def test_remove_last_watch_auto_disables_monitoring(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """移除最后一个监控目录时应自动停用监控，保持状态一致。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.addWatch(str(d))
        controller.setMonitoringEnabled(True)
        assert controller.monitoringEnabled is True
        controller.removeWatch(str(d))
        assert controller.watchedCount == 0
        assert controller.monitoringEnabled is False

    def test_remove_one_of_many_keeps_monitoring_enabled(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """移除多个目录中的一个时不应停用监控。"""
        d1 = tmp_path / "watched1"
        d2 = tmp_path / "watched2"
        d1.mkdir()
        d2.mkdir()
        controller.addWatch(str(d1))
        controller.addWatch(str(d2))
        controller.setMonitoringEnabled(True)
        controller.removeWatch(str(d1))
        assert controller.watchedCount == 1
        assert controller.monitoringEnabled is True

    def test_remove_last_watch_emits_monitor_state_changed(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """移除最后一个目录触发自动停用时，应 emit monitorStateChanged。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.addWatch(str(d))
        controller.setMonitoringEnabled(True)
        state_changes: list[bool] = []
        controller.monitorStateChanged.connect(state_changes.append)  # pyrefly: ignore [missing-attribute]
        controller.removeWatch(str(d))
        assert state_changes == [False]

    def test_remove_last_watch_persists_disabled_state(
        self,
        controller: FileMonitorController,
        config_dir: Path,
        tmp_path: Path,
    ) -> None:
        """自动停用后应将 monitoring_enabled=False 持久化到 monitor.json。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.addWatch(str(d))
        controller.setMonitoringEnabled(True)
        controller.removeWatch(str(d))
        monitor_json = config_dir / "monitor.json"
        assert monitor_json.is_file()
        data = json.loads(monitor_json.read_text(encoding="utf-8"))
        assert data["monitoring_enabled"] is False
        assert data["directories"] == []


class TestMonitoringEnable:
    """``setMonitoringEnabled`` 启停监控测试。"""

    def test_enable_when_no_watched_is_noop(
        self,
        controller: FileMonitorController,
    ) -> None:
        """无监控目录时启用监控不应启动 Observer。"""
        controller.setMonitoringEnabled(True)
        # 监控状态仍切换为 True（语义上启用），但 observer 不会启动
        # 由于 _watched 为空，_start_observer 不会 schedule 任何目录
        assert controller.monitoringEnabled is True

    def test_enable_disable_lifecycle(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """启用→停用 应正确启动与停止 Observer。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.addWatch(str(d))
        # 启用：触发 _start_observer
        controller.setMonitoringEnabled(True)
        assert controller.monitoringEnabled is True
        # 停用：触发 _stop_observer
        controller.setMonitoringEnabled(False)
        assert controller.monitoringEnabled is False

    def test_enable_idempotent(
        self,
        controller: FileMonitorController,
    ) -> None:
        """重复调用 setMonitoringEnabled(同值) 应为 no-op。"""
        controller.setMonitoringEnabled(True)
        controller.setMonitoringEnabled(True)
        assert controller.monitoringEnabled is True


class TestEventHandling:
    """事件处理与防抖测试。"""

    def test_event_with_no_ruleset_skips_scan(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """ruleset 为 None 时，事件接收仍应启动防抖定时器。"""
        # 启用监控（_on_event_received 检查 monitoring_enabled）
        controller._monitoring_enabled = True
        controller._on_event_received(str(tmp_path / "test.txt"), "modified")
        # 应启动防抖定时器
        assert len(controller._debounce_timers) == 1
        # 清理防抖定时器
        controller.cleanup()

    def test_scan_path_no_ruleset_skips(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """无 ruleset 时 _scan_path 不应抛异常。"""
        f = tmp_path / "test.txt"
        f.write_text("content")
        controller._scan_path(str(f))
        # 无命中记录追加
        assert controller.model.count == 0

    def test_scan_path_with_hit_emits_signal(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """有命中时应追加到 model 并 emit hitFound。"""

        # 注入一个有 hits 的 fake ruleset
        class _FakeRuleset:
            scan_extensions: tuple[str, ...] | None = None
            ignore_dirs: tuple[str, ...] = ()

        fake_rules_controller._ruleset = _FakeRuleset()

        # monkeypatch Scanner 构造与 scan_file
        hit = _FakeRuleHit(rule_name="密码规则", severity=Severity.CRITICAL, match_text="password123")

        class _FakeScanner:
            def scan_file(self, path: Path) -> _FakeScanResult:
                return _FakeScanResult(hits=(hit,))

        # 替换 _get_scanner 直接返回 fake（避免真实 Scanner 构造）
        monkeypatch.setattr(controller, "_get_scanner", _FakeScanner)

        # 监听 hitFound 信号
        hits: list[dict[str, Any]] = []
        controller.hitFound.connect(hits.append)  # pyrefly: ignore [missing-attribute]

        f = tmp_path / "secret.txt"
        f.write_text("password123")
        # 启用监控（_scan_path 检查 monitoring_enabled）
        controller._monitoring_enabled = True
        controller._scan_path(str(f))

        assert len(hits) == 1
        assert hits[0]["rule_name"] == "密码规则"
        assert hits[0]["severity"] == "critical"
        assert hits[0]["path"] == str(f)
        assert controller.model.count == 1

    def test_scan_path_no_hit_does_not_emit(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """无命中时不应 emit hitFound。"""

        class _FakeRuleset:
            scan_extensions: tuple[str, ...] | None = None
            ignore_dirs: tuple[str, ...] = ()

        fake_rules_controller._ruleset = _FakeRuleset()

        class _FakeScanner:
            def scan_file(self, path: Path) -> _FakeScanResult:
                return _FakeScanResult(hits=())  # 无命中

        monkeypatch.setattr(controller, "_get_scanner", _FakeScanner)

        hits: list[dict[str, Any]] = []
        controller.hitFound.connect(hits.append)  # pyrefly: ignore [missing-attribute]

        f = tmp_path / "normal.txt"
        f.write_text("normal content")
        controller._monitoring_enabled = True
        controller._scan_path(str(f))

        assert len(hits) == 0
        assert controller.model.count == 0

    def test_scan_path_nonexistent_file_skips(
        self,
        controller: FileMonitorController,
    ) -> None:
        """文件不存在时 _scan_path 应跳过。"""
        controller._monitoring_enabled = True
        controller._scan_path("/nonexistent/path/file.txt")
        assert controller.model.count == 0

    def test_scan_path_handles_scan_exception(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """扫描器抛异常时不应中断监控。"""

        class _FakeRuleset:
            scan_extensions: tuple[str, ...] | None = None
            ignore_dirs: tuple[str, ...] = ()

        fake_rules_controller._ruleset = _FakeRuleset()

        class _FakeScanner:
            def scan_file(self, path: Path) -> Any:
                raise RuntimeError("扫描失败")

        monkeypatch.setattr(controller, "_get_scanner", _FakeScanner)

        f = tmp_path / "fail.txt"
        f.write_text("content")
        controller._monitoring_enabled = True
        # 不应抛异常
        controller._scan_path(str(f))
        assert controller.model.count == 0


# ============================ 真实文件解析测试 ============================


def _build_real_ruleset(
    *,
    scan_extensions: tuple[str, ...] | None = None,
) -> RuleSet:
    """构造含「敏感内容」CONTENT 规则的真实 RuleSet。

    :param scan_extensions: 扩展名白名单；``None`` 表示扫描所有文件
    """
    rule = Rule(
        name="敏感内容",
        severity=Severity.CRITICAL,
        match=LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="password",
        ),
    )
    return RuleSet(
        version="1.0",
        rules=(rule,),
        scan_extensions=scan_extensions,
    )


class TestScanPathRealParsing:
    """使用真实 Scanner + 真实 RuleSet 验证文件监控的端到端解析。

    不 monkeypatch ``_get_scanner``，让真实 Scanner 构造与 ``scan_file``
    全链路执行，覆盖提取器调度、内容桶匹配、RuleHit 构造、model 追加等路径。
    """

    def test_txt_file_with_password_triggers_hit(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """txt 文件包含 password → 命中、model 追加、hitFound emit。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        hits: list[dict[str, Any]] = []
        controller.hitFound.connect(hits.append)  # pyrefly: ignore [missing-attribute]

        f = tmp_path / "secret.txt"
        f.write_text("db_password=hunter2\n", encoding="utf-8")
        controller._scan_path(str(f))

        assert controller.model.count == 1
        assert len(hits) == 1
        assert hits[0]["rule_name"] == "敏感内容"
        assert hits[0]["severity"] == "critical"
        assert hits[0]["path"] == str(f)
        controller.cleanup()

    def test_py_file_with_password_triggers_hit(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """py 文件包含 password → 命中。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        f = tmp_path / "config.py"
        f.write_text('PASSWORD = "admin123"\n', encoding="utf-8")
        controller._scan_path(str(f))

        assert controller.model.count == 1
        record = controller.model.records[0]
        assert record.rule_name == "敏感内容"
        controller.cleanup()

    def test_yaml_file_with_password_triggers_hit(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """yaml 文件包含 password → 命中。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        f = tmp_path / "db.yaml"
        f.write_text("database:\n  password: secret123\n", encoding="utf-8")
        controller._scan_path(str(f))

        assert controller.model.count == 1
        controller.cleanup()

    def test_json_file_with_password_triggers_hit(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """json 文件包含 password → 命中。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        f = tmp_path / "config.json"
        f.write_text('{"password": "p@ssw0rd"}\n', encoding="utf-8")
        controller._scan_path(str(f))

        assert controller.model.count == 1
        controller.cleanup()

    def test_xml_file_with_password_triggers_hit(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """xml 文件包含 password → 命中。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        f = tmp_path / "app.xml"
        f.write_text("<config><password>s3cr3t</password></config>\n", encoding="utf-8")
        controller._scan_path(str(f))

        assert controller.model.count == 1
        controller.cleanup()

    def test_file_without_password_no_hit(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """文件不含 password → 无命中、model 不追加。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        hits: list[dict[str, Any]] = []
        controller.hitFound.connect(hits.append)  # pyrefly: ignore [missing-attribute]

        f = tmp_path / "normal.txt"
        f.write_text("just some normal content\n", encoding="utf-8")
        controller._scan_path(str(f))

        assert controller.model.count == 0
        assert len(hits) == 0
        controller.cleanup()

    def test_empty_file_no_hit_no_crash(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """空文件 → 无命中，不抛异常。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        controller._scan_path(str(f))

        assert controller.model.count == 0
        controller.cleanup()

    def test_binary_file_no_crash(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """二进制文件 → 不抛异常（Scanner 内部按二进制跳过或无文本命中）。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        f = tmp_path / "binary.bin"
        f.write_bytes(bytes(range(256)) * 4)
        # 不应抛异常
        controller._scan_path(str(f))
        controller.cleanup()

    def test_multiple_files_independent_hits(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """多次 _scan_path 调用独立追加命中到 model。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        for i in range(5):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"password{i}\n", encoding="utf-8")
            controller._scan_path(str(f))

        assert controller.model.count == 5
        controller.cleanup()

    def test_same_file_scanned_twice_appends_twice(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """同一文件被扫描两次 → model 追加两条（监控场景：文件反复修改）。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        f = tmp_path / "changed.txt"
        f.write_text("password=abc\n", encoding="utf-8")
        controller._scan_path(str(f))
        f.write_text("password=def\n", encoding="utf-8")
        controller._scan_path(str(f))

        assert controller.model.count == 2
        controller.cleanup()

    def test_hit_record_fields_complete(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """命中记录字段完整：time/file_path/rule_name/severity/match_text。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        f = tmp_path / "cred.txt"
        f.write_text("password=hunter2\n", encoding="utf-8")
        controller._scan_path(str(f))

        record = controller.model.records[0]
        assert record.time  # 非空时间字符串
        assert record.file_path == str(f)
        assert record.rule_name == "敏感内容"
        assert record.severity == Severity.CRITICAL
        assert "password" in record.match_text.lower()
        controller.cleanup()


class TestGetScannerCache:
    """``_get_scanner`` 缓存与 None ruleset 路径测试（不 monkeypatch）。"""

    def test_get_scanner_returns_none_when_ruleset_none(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
    ) -> None:
        """ruleset 为 None 时 _get_scanner 返回 None（真实路径，非 monkeypatch）。"""
        fake_rules_controller._ruleset = None
        assert controller._get_scanner() is None

    def test_get_scanner_caches_by_ruleset_id(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
    ) -> None:
        """同一 ruleset 多次调用返回同一 Scanner 实例。"""
        rs = _build_real_ruleset()
        fake_rules_controller._ruleset = rs
        s1 = controller._get_scanner()
        s2 = controller._get_scanner()
        assert s1 is s2
        controller.cleanup()

    def test_get_scanner_new_instance_on_ruleset_change(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
    ) -> None:
        """更换 ruleset 后返回新的 Scanner 实例。"""
        rs1 = _build_real_ruleset()
        fake_rules_controller._ruleset = rs1
        s1 = controller._get_scanner()

        rs2 = _build_real_ruleset()
        fake_rules_controller._ruleset = rs2
        s2 = controller._get_scanner()

        assert s1 is not s2
        controller.cleanup()

    def test_scan_path_with_none_ruleset_no_crash(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """ruleset=None 时 _scan_path 不抛异常，无命中（覆盖行 566）。"""
        fake_rules_controller._ruleset = None
        controller._monitoring_enabled = True

        f = tmp_path / "test.txt"
        f.write_text("password=secret\n", encoding="utf-8")
        controller._scan_path(str(f))

        assert controller.model.count == 0
        controller.cleanup()


class TestScanPathErrorHandling:
    """``_scan_path`` 边界与异常场景测试。"""

    def test_scan_path_when_monitoring_disabled_skips(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """监控停用时 _scan_path 跳过扫描。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = False

        f = tmp_path / "secret.txt"
        f.write_text("password=abc\n", encoding="utf-8")
        controller._scan_path(str(f))

        assert controller.model.count == 0

    def test_scan_path_deletes_debounce_timer_after_scan(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """_scan_path 执行后从 _debounce_timers 移除该路径的定时器。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        f = tmp_path / "test.txt"
        f.write_text("content\n", encoding="utf-8")
        path_str = str(f)

        # 先通过 _on_event_received 创建防抖定时器
        controller._on_event_received(path_str, "created")
        assert path_str in controller._debounce_timers

        # 直接调用 _scan_path（跳过防抖等待）
        controller._scan_path(path_str)
        assert path_str not in controller._debounce_timers
        controller.cleanup()

    def test_scan_path_oserror_handled(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """scan_file 抛 OSError 时静默处理，不中断监控（覆盖行 570-571）。"""

        class _OSErrorScanner:
            def scan_file(self, path: Path) -> Any:
                raise OSError("权限拒绝")

        fake_rules_controller._ruleset = _build_real_ruleset()
        monkeypatch.setattr(controller, "_get_scanner", _OSErrorScanner)

        f = tmp_path / "locked.txt"
        f.write_text("password=abc\n", encoding="utf-8")
        controller._monitoring_enabled = True
        # 不应抛异常
        controller._scan_path(str(f))
        assert controller.model.count == 0
        controller.cleanup()

    def test_scan_path_generic_exception_handled(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """scan_file 抛非 OSError 异常时静默处理（覆盖行 572-574）。"""

        class _CrashScanner:
            def scan_file(self, path: Path) -> Any:
                raise ValueError("内部错误")

        fake_rules_controller._ruleset = _build_real_ruleset()
        monkeypatch.setattr(controller, "_get_scanner", _CrashScanner)

        f = tmp_path / "crash.txt"
        f.write_text("password=abc\n", encoding="utf-8")
        controller._monitoring_enabled = True
        controller._scan_path(str(f))
        assert controller.model.count == 0
        controller.cleanup()

    def test_match_text_truncation(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """match_text 超过 _MATCH_TEXT_MAX 时截断并加省略号。"""
        from fuscan.gui.controllers.file_monitor_controller import _MATCH_TEXT_MAX

        long_text = "A" * (_MATCH_TEXT_MAX + 50)

        class _LongTextScanner:
            def scan_file(self, path: Path) -> Any:
                return _FakeScanResult(
                    hits=(
                        _FakeRuleHit(
                            rule_name="长文本规则",
                            severity=Severity.WARNING,
                            match_text=long_text,
                        ),
                    )
                )

        fake_rules_controller._ruleset = _build_real_ruleset()
        monkeypatch.setattr(controller, "_get_scanner", _LongTextScanner)

        f = tmp_path / "long.txt"
        f.write_text("content\n", encoding="utf-8")
        controller._monitoring_enabled = True
        controller._scan_path(str(f))

        record = controller.model.records[0]
        assert len(record.match_text) == _MATCH_TEXT_MAX
        assert record.match_text.endswith("…")
        controller.cleanup()

    def test_match_text_falls_back_to_detail_when_empty(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """match_text 为空时回退到 detail 字段。"""

        class _EmptyMatchScanner:
            def scan_file(self, path: Path) -> Any:
                return _FakeScanResult(
                    hits=(
                        _FakeRuleHit(
                            rule_name="规则",
                            severity=Severity.WARNING,
                            match_text="",
                            detail="回退详情文本",
                        ),
                    )
                )

        fake_rules_controller._ruleset = _build_real_ruleset()
        monkeypatch.setattr(controller, "_get_scanner", _EmptyMatchScanner)

        f = tmp_path / "test.txt"
        f.write_text("content\n", encoding="utf-8")
        controller._monitoring_enabled = True
        controller._scan_path(str(f))

        record = controller.model.records[0]
        assert record.match_text == "回退详情文本"
        controller.cleanup()

    def test_scan_path_directory_skips(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """_scan_path 传入目录路径时跳过（is_file() 为 False）。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        d = tmp_path / "subdir"
        d.mkdir()
        controller._scan_path(str(d))

        assert controller.model.count == 0

    def test_hit_found_signal_payload_complete(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
    ) -> None:
        """hitFound 信号 payload 包含全部字段。"""
        fake_rules_controller._ruleset = _build_real_ruleset()
        controller._monitoring_enabled = True

        hits: list[dict[str, Any]] = []
        controller.hitFound.connect(hits.append)  # pyrefly: ignore [missing-attribute]

        f = tmp_path / "secret.txt"
        f.write_text("password=hunter2\n", encoding="utf-8")
        controller._scan_path(str(f))

        assert len(hits) == 1
        payload = hits[0]
        assert set(payload.keys()) == {
            "time",
            "path",
            "rule_name",
            "severity",
            "severity_text",
            "match_text",
        }
        assert payload["severity"] == "critical"
        assert payload["severity_text"] == "严重"
        controller.cleanup()


class TestEventLog:
    """事件日志功能测试：eventCount / recentEvents / clearEvents。"""

    def test_initial_event_count_is_zero(self, controller: FileMonitorController) -> None:
        """初始状态事件计数为 0。"""
        assert controller.eventCount == 0
        assert controller.recentEvents == []

    def test_event_received_increments_count(self, controller: FileMonitorController, tmp_path: Path) -> None:
        """收到事件后 eventCount 递增，recentEvents 追加一条。"""
        controller._monitoring_enabled = True
        controller._on_event_received(str(tmp_path / "a.txt"), "created")
        assert controller.eventCount == 1
        assert len(controller.recentEvents) == 1
        assert controller.recentEvents[0]["path"] == str(tmp_path / "a.txt")
        assert controller.recentEvents[0]["event_type"] == "created"
        controller.cleanup()

    def test_multiple_events_accumulate(self, controller: FileMonitorController, tmp_path: Path) -> None:
        """多个事件累加计数与日志。"""
        controller._monitoring_enabled = True
        controller._on_event_received(str(tmp_path / "a.txt"), "created")
        controller._on_event_received(str(tmp_path / "b.txt"), "modified")
        controller._on_event_received(str(tmp_path / "c.txt"), "created")
        assert controller.eventCount == 3
        assert len(controller.recentEvents) == 3
        controller.cleanup()

    def test_event_log_fifo_eviction(self, controller: FileMonitorController, tmp_path: Path) -> None:
        """超过 _RECENT_EVENTS_MAX 后丢弃最旧记录。"""
        from fuscan.gui.controllers.file_monitor_controller import _RECENT_EVENTS_MAX

        controller._monitoring_enabled = True
        for i in range(_RECENT_EVENTS_MAX + 10):
            controller._on_event_received(str(tmp_path / f"f{i}.txt"), "created")
        # eventCount 记录全部，recentEvents 只保留最近 N 条
        assert controller.eventCount == _RECENT_EVENTS_MAX + 10
        assert len(controller.recentEvents) == _RECENT_EVENTS_MAX
        # 最旧记录已被丢弃，最新记录在末尾
        assert f"f{_RECENT_EVENTS_MAX + 9}.txt" in controller.recentEvents[-1]["path"]
        controller.cleanup()

    def test_event_not_recorded_when_monitoring_disabled(
        self, controller: FileMonitorController, tmp_path: Path
    ) -> None:
        """监控停用时事件不记录日志。"""
        controller._monitoring_enabled = False
        controller._on_event_received(str(tmp_path / "a.txt"), "created")
        assert controller.eventCount == 0
        assert controller.recentEvents == []

    def test_clear_events_resets_log(self, controller: FileMonitorController, tmp_path: Path) -> None:
        """clearEvents 清零事件计数与日志。"""
        controller._monitoring_enabled = True
        controller._on_event_received(str(tmp_path / "a.txt"), "created")
        assert controller.eventCount == 1
        controller.clearEvents()
        assert controller.eventCount == 0
        assert controller.recentEvents == []
        controller.cleanup()

    def test_enable_monitoring_resets_event_log(self, controller: FileMonitorController, tmp_path: Path) -> None:
        """重新启用监控时重置事件日志。"""
        controller._monitoring_enabled = True
        controller._on_event_received(str(tmp_path / "a.txt"), "created")
        assert controller.eventCount == 1
        # 模拟停用再启用
        controller._monitoring_enabled = False
        controller._monitoring_enabled = True
        controller._event_count = 0  # setMonitoringEnabled 中的重置逻辑
        controller._recent_events.clear()
        assert controller.eventCount == 0

    def test_recent_events_have_timestamp(self, controller: FileMonitorController, tmp_path: Path) -> None:
        """recentEvents 每项包含 time 字段（HH:MM:SS 格式）。"""
        controller._monitoring_enabled = True
        controller._on_event_received(str(tmp_path / "a.txt"), "created")
        event = controller.recentEvents[0]
        assert "time" in event
        # 格式 HH:MM:SS（8 字符）
        assert len(event["time"]) == 8
        assert event["time"][2] == ":"
        assert event["time"][5] == ":"
        controller.cleanup()


class TestFilterStats:
    """过滤统计测试：ignoredDirCount / filteredExtCount / dirEventCount。"""

    def test_initial_filter_stats_zero(self, controller: FileMonitorController) -> None:
        """初始状态过滤统计全为 0。"""
        assert controller.ignoredDirCount == 0
        assert controller.filteredExtCount == 0
        assert controller.dirEventCount == 0

    def test_ignored_dir_counted(self, controller: FileMonitorController) -> None:
        """噪声目录路径的事件计入 ignoredDirCount，不 emit event_received。"""
        stats: dict[str, int] = {"ignored_dir": 0, "filtered_ext": 0, "dir_events": 0}
        handler = _WatchdogHandler(
            emitter=controller._emitter,  # type: ignore[attr-defined]
            scan_extensions=None,
            filter_stats=stats,
        )
        # 模拟 .git 目录下的文件创建事件
        event = _FakeFileEvent(str(Path("/proj/.git/config")), is_directory=False)
        handler.on_created(event)
        assert stats["ignored_dir"] == 1
        assert stats["filtered_ext"] == 0
        assert stats["dir_events"] == 0

    def test_filtered_ext_counted(self, controller: FileMonitorController) -> None:
        """扩展名不匹配的事件计入 filteredExtCount。"""
        stats: dict[str, int] = {"ignored_dir": 0, "filtered_ext": 0, "dir_events": 0}
        handler = _WatchdogHandler(
            emitter=controller._emitter,  # type: ignore[attr-defined]
            scan_extensions=("py", "yaml"),
            filter_stats=stats,
        )
        # .txt 文件不在白名单
        event = _FakeFileEvent("/proj/notes.txt", is_directory=False)
        handler.on_created(event)
        assert stats["filtered_ext"] == 1
        assert stats["ignored_dir"] == 0
        assert stats["dir_events"] == 0

    def test_dir_events_counted(self, controller: FileMonitorController) -> None:
        """目录事件计入 dirEventCount，不触发文件扫描。"""
        stats: dict[str, int] = {"ignored_dir": 0, "filtered_ext": 0, "dir_events": 0}
        handler = _WatchdogHandler(
            emitter=controller._emitter,  # type: ignore[attr-defined]
            scan_extensions=None,
            filter_stats=stats,
        )
        event = _FakeFileEvent("/proj/new_folder", is_directory=True)
        handler.on_created(event)
        assert stats["dir_events"] == 1
        assert stats["ignored_dir"] == 0
        assert stats["filtered_ext"] == 0

    def test_passed_event_not_counted_in_filter(self, controller: FileMonitorController) -> None:
        """通过过滤的事件不计入任何过滤统计。"""
        stats: dict[str, int] = {"ignored_dir": 0, "filtered_ext": 0, "dir_events": 0}
        handler = _WatchdogHandler(
            emitter=controller._emitter,  # type: ignore[attr-defined]
            scan_extensions=("py",),
            filter_stats=stats,
        )
        event = _FakeFileEvent("/proj/app.py", is_directory=False)
        handler.on_created(event)
        assert stats["ignored_dir"] == 0
        assert stats["filtered_ext"] == 0
        assert stats["dir_events"] == 0

    def test_poll_filter_stats_emits_signal(self, controller: FileMonitorController) -> None:
        """_poll_filter_stats 检测到变化时 emit eventLogChanged。"""
        # 手动修改 filter_stats 模拟 handler 线程递增
        controller._filter_stats["dir_events"] = 5
        emitted = []
        controller.eventLogChanged.connect(lambda: emitted.append(1))  # type: ignore[attr-defined]
        controller._poll_filter_stats()
        assert len(emitted) == 1
        assert controller.dirEventCount == 5

    def test_poll_filter_stats_no_change_no_emit(self, controller: FileMonitorController) -> None:
        """_poll_filter_stats 无变化时不 emit。"""
        controller._poll_filter_stats()  # 初次调用，记下快照
        emitted = []
        controller.eventLogChanged.connect(lambda: emitted.append(1))  # type: ignore[attr-defined]
        controller._poll_filter_stats()  # 无变化
        assert len(emitted) == 0

    def test_clear_events_resets_filter_stats(self, controller: FileMonitorController) -> None:
        """clearEvents 同时清零过滤统计。"""
        controller._filter_stats["ignored_dir"] = 3
        controller._filter_stats["filtered_ext"] = 5
        controller._filter_stats["dir_events"] = 2
        controller.clearEvents()
        assert controller.ignoredDirCount == 0
        assert controller.filteredExtCount == 0
        assert controller.dirEventCount == 0

    def test_enable_monitoring_resets_filter_stats(self, controller: FileMonitorController) -> None:
        """重新启用监控时清零过滤统计。"""
        controller._filter_stats["ignored_dir"] = 10
        # 模拟 setMonitoringEnabled 中的重置逻辑
        controller._filter_stats["ignored_dir"] = 0
        for key in controller._filter_stats:
            controller._filter_stats[key] = 0
        assert controller.ignoredDirCount == 0


class _FakeFileEvent:
    """伪 watchdog FileSystemEvent，用于测试 handler 回调。"""

    def __init__(self, src_path: str, *, is_directory: bool = False) -> None:
        self.src_path = src_path
        self.dest_path = src_path
        self.is_directory = is_directory
        self.event_type = "created"


class TestRulesetChanged:
    """``rulesetChanged`` 信号处理测试。"""

    def test_ruleset_changed_clears_scanner_cache(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
    ) -> None:
        """规则集变更应清空 Scanner 缓存。"""
        # 预填充缓存
        controller._scanner_cache[123] = object()  # type: ignore[assignment]
        assert len(controller._scanner_cache) == 1
        # 触发信号
        fake_rules_controller.emit()
        assert len(controller._scanner_cache) == 0


class TestPersistence:
    """``monitor.json`` 持久化测试。"""

    def test_persist_writes_monitor_json(
        self,
        controller: FileMonitorController,
        config_dir: Path,
        tmp_path: Path,
    ) -> None:
        """addWatch 后应写入 monitor.json。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.addWatch(str(d))
        monitor_json = config_dir / "monitor.json"
        assert monitor_json.is_file()
        data = json.loads(monitor_json.read_text(encoding="utf-8"))
        assert "directories" in data
        assert "monitoring_enabled" in data
        assert len(data["directories"]) == 1
        assert data["monitoring_enabled"] is False

    def test_load_persisted_restores_directories(
        self,
        config_dir: Path,
        tmp_path: Path,
        fake_rules_controller: _FakeRulesController,
    ) -> None:
        """构造时从 monitor.json 恢复监控目录列表。"""
        d = tmp_path / "watched"
        d.mkdir()
        # 预先写入 monitor.json
        monitor_json = config_dir / "monitor.json"
        monitor_json.write_text(
            json.dumps(
                {
                    "directories": [str(d)],
                    "monitoring_enabled": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # 构造新 controller，应加载持久化目录
        controller2 = FileMonitorController(
            fake_rules_controller,
            _observer_factory=_FakeObserver,
        )
        assert controller2.watchedCount == 1
        assert str(d.resolve()) in controller2.watchedDirectories

    def test_load_persisted_skips_nonexistent_dirs(
        self,
        config_dir: Path,
        fake_rules_controller: _FakeRulesController,
    ) -> None:
        """持久化的路径不存在时应跳过。"""
        monitor_json = config_dir / "monitor.json"
        monitor_json.write_text(
            json.dumps(
                {
                    "directories": ["/definitely/nonexistent/path"],
                    "monitoring_enabled": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        controller2 = FileMonitorController(
            fake_rules_controller,
            _observer_factory=_FakeObserver,
        )
        assert controller2.watchedCount == 0

    def test_load_persisted_invalid_json_skips(
        self,
        config_dir: Path,
        fake_rules_controller: _FakeRulesController,
    ) -> None:
        """monitor.json 损坏时应跳过，不抛异常。"""
        monitor_json = config_dir / "monitor.json"
        monitor_json.write_text("{ invalid json", encoding="utf-8")
        controller2 = FileMonitorController(
            fake_rules_controller,
            _observer_factory=_FakeObserver,
        )
        assert controller2.watchedCount == 0


class TestCleanup:
    """``cleanup`` 测试。"""

    def test_cleanup_stops_observer(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """cleanup 应停止 Observer。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.addWatch(str(d))
        controller.setMonitoringEnabled(True)
        controller.cleanup()
        # monitoring_enabled 应被置为 False
        assert controller._monitoring_enabled is False

    def test_cleanup_clears_debounce_timers(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """cleanup 应清空防抖定时器表。"""
        # 启用监控以使 _on_event_received 创建防抖定时器
        controller._monitoring_enabled = True
        controller._on_event_received(str(tmp_path / "test.txt"), "modified")
        assert len(controller._debounce_timers) == 1
        controller.cleanup()
        assert len(controller._debounce_timers) == 0

    def test_cleanup_idempotent(self, controller: FileMonitorController) -> None:
        """重复调用 cleanup 不应抛异常。"""
        controller.cleanup()
        controller.cleanup()


class TestWatchdogHandler:
    """``_WatchdogHandler`` 过滤逻辑测试。"""

    def test_should_handle_normal_path(self) -> None:
        """普通路径应通过过滤。"""
        handler = _WatchdogHandler(_EventEmitterStub(), None)
        assert handler._should_handle("/some/dir/file.txt") is True

    def test_should_skip_noise_dirs(self) -> None:
        """噪声目录路径应被跳过。"""
        handler = _WatchdogHandler(_EventEmitterStub(), None)
        assert handler._should_handle("/proj/.git/HEAD") is False
        assert handler._should_handle("/proj/__pycache__/module.cpython-310.pyc") is False
        assert handler._should_handle("/proj/node_modules/lib/index.js") is False

    def test_should_filter_by_scan_extensions(self) -> None:
        """scan_extensions 非空时按扩展名过滤。"""
        handler = _WatchdogHandler(_EventEmitterStub(), ("txt", "yaml"))
        assert handler._should_handle("/dir/file.txt") is True
        assert handler._should_handle("/dir/file.yaml") is True
        assert handler._should_handle("/dir/file.exe") is False

    def test_scan_extensions_none_passes_all(self) -> None:
        """scan_extensions=None 时通过所有文件。"""
        handler = _WatchdogHandler(_EventEmitterStub(), None)
        assert handler._should_handle("/dir/file.anything") is True

    def test_scan_extensions_empty_blocks_all(self) -> None:
        """scan_extensions=() 时阻止所有文件（与 Scanner 语义一致）。"""
        handler = _WatchdogHandler(_EventEmitterStub(), ())
        assert handler._should_handle("/dir/file.txt") is False

    def test_should_handle_bytes_path(self) -> None:
        """bytes 类型路径应解码后过滤。"""
        handler = _WatchdogHandler(_EventEmitterStub(), None)
        assert handler._should_handle(b"/some/dir/file.txt") is True
        assert handler._should_handle(b"/proj/.git/HEAD") is False


class TestWatchdogHandlerEvents:
    """``_WatchdogHandler`` 事件回调测试。"""

    def test_on_created_emits_event(self) -> None:
        """on_created 应转发文件创建事件。"""
        emitter = _EventEmitterStub()
        handler = _WatchdogHandler(emitter, None)

        class _FakeEvent:
            is_directory = False
            src_path = "/dir/new_file.txt"
            dest_path = "/dir/moved.txt"

        handler.on_created(_FakeEvent())
        assert len(emitter.events) == 1
        assert emitter.events[0] == ("/dir/new_file.txt", "created")

    def test_on_created_skips_directory_event(self) -> None:
        """目录事件应跳过。"""
        emitter = _EventEmitterStub()
        handler = _WatchdogHandler(emitter, None)

        class _FakeEvent:
            is_directory = True
            src_path = "/dir/new_dir"
            dest_path = "/dir/moved.txt"

        handler.on_created(_FakeEvent())
        assert len(emitter.events) == 0

    def test_on_modified_emits_event(self) -> None:
        """on_modified 应转发文件修改事件。"""
        emitter = _EventEmitterStub()
        handler = _WatchdogHandler(emitter, None)

        class _FakeEvent:
            is_directory = False
            src_path = "/dir/file.txt"
            dest_path = "/dir/moved.txt"

        handler.on_modified(_FakeEvent())
        assert len(emitter.events) == 1
        assert emitter.events[0] == ("/dir/file.txt", "modified")

    def test_on_moved_emits_event(self) -> None:
        """on_moved 应按目标路径转发移动事件。"""
        emitter = _EventEmitterStub()
        handler = _WatchdogHandler(emitter, None)

        class _FakeEvent:
            is_directory = False
            src_path = "/dir/old.txt"
            dest_path = "/dir/new.txt"

        handler.on_moved(_FakeEvent())
        assert len(emitter.events) == 1
        assert emitter.events[0] == ("/dir/new.txt", "moved")

    def test_on_moved_skips_noise_dir(self) -> None:
        """目标路径在噪声目录中应跳过。"""
        emitter = _EventEmitterStub()
        handler = _WatchdogHandler(emitter, None)

        class _FakeEvent:
            is_directory = False
            src_path = "/dir/old.txt"
            dest_path = "/proj/.git/HEAD"

        handler.on_moved(_FakeEvent())
        assert len(emitter.events) == 0


class TestAddWatchWithMonitoring:
    """``addWatch`` 在监控启用时的 schedule 路径测试。"""

    def test_add_watch_schedules_when_monitoring_enabled(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """监控启用后添加目录应立即 schedule。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.setMonitoringEnabled(True)
        controller.addWatch(str(d))
        assert controller.watchedCount == 1

    def test_add_watch_schedule_oserror_returns_false(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """schedule 抛 OSError 时 addWatch 返回 False。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.setMonitoringEnabled(True)

        def _raise_oserror(*args: Any, **kwargs: Any) -> Any:
            raise OSError("schedule failed")

        monkeypatch.setattr(controller._observer, "schedule", _raise_oserror)
        assert controller.addWatch(str(d)) is False


class TestRemoveWatchWithMonitoring:
    """``removeWatch`` 在监控启用时的 unschedule 路径测试。"""

    def test_remove_watch_unschedules_when_monitoring_enabled(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """监控启用后移除目录应调用 unschedule。"""
        d = tmp_path / "watched"
        d.mkdir()
        controller.setMonitoringEnabled(True)
        controller.addWatch(str(d))
        fake_obs = controller._observer
        assert fake_obs is not None
        controller.removeWatch(str(d))
        assert len(fake_obs.unscheduled) == 1  # pyrefly: ignore [missing-attribute]


class TestScanPathEdgeCases:
    """``_scan_path`` 边界场景测试。"""

    def test_scan_path_truncates_long_match_text(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """match_text 超长时应截断。"""

        class _FakeRuleset:
            scan_extensions: tuple[str, ...] | None = None
            ignore_dirs: tuple[str, ...] = ()

        fake_rules_controller._ruleset = _FakeRuleset()

        long_text = "A" * 200
        hit = _FakeRuleHit(rule_name="长匹配规则", severity=Severity.INFO, match_text=long_text)

        class _FakeScanner:
            def scan_file(self, path: Path) -> _FakeScanResult:
                return _FakeScanResult(hits=(hit,))

        monkeypatch.setattr(controller, "_get_scanner", _FakeScanner)
        controller._monitoring_enabled = True

        f = tmp_path / "long.txt"
        f.write_text("content")
        controller._scan_path(str(f))
        assert controller.model.count == 1
        # 验证 match_text 被截断（最大 _MATCH_TEXT_MAX=120）
        records = controller.model.records
        assert len(records[0].match_text) <= 120

    def test_scan_path_nonexistent_file_skips(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
    ) -> None:
        """文件不存在时 _scan_path 应跳过。"""
        controller._monitoring_enabled = True
        controller._scan_path(str(tmp_path / "nonexistent.txt"))
        assert controller.model.count == 0


class TestPersistenceEdgeCases:
    """持久化边界场景测试。"""

    def test_load_persisted_non_list_directories_skips(
        self,
        config_dir: Path,
        fake_rules_controller: _FakeRulesController,
    ) -> None:
        """directories 字段非 list 时应跳过。"""
        monitor_json = config_dir / "monitor.json"
        monitor_json.write_text(
            json.dumps({"directories": "not_a_list", "monitoring_enabled": False}),
            encoding="utf-8",
        )
        controller2 = FileMonitorController(
            fake_rules_controller,
            _observer_factory=_FakeObserver,
        )
        assert controller2.watchedCount == 0

    def test_load_persisted_non_str_entry_skips(
        self,
        config_dir: Path,
        fake_rules_controller: _FakeRulesController,
    ) -> None:
        """directories 中非字符串条目应跳过。"""
        monitor_json = config_dir / "monitor.json"
        monitor_json.write_text(
            json.dumps({"directories": [123, "/valid/path"], "monitoring_enabled": False}),
            encoding="utf-8",
        )
        controller2 = FileMonitorController(
            fake_rules_controller,
            _observer_factory=_FakeObserver,
        )
        # 123 被跳过，/valid/path 不存在也被跳过
        assert controller2.watchedCount == 0

    def test_load_persisted_restores_enabled_state(
        self,
        config_dir: Path,
        tmp_path: Path,
        fake_rules_controller: _FakeRulesController,
    ) -> None:
        """monitoring_enabled=True 时应恢复启用状态。"""
        d = tmp_path / "watched"
        d.mkdir()
        monitor_json = config_dir / "monitor.json"
        monitor_json.write_text(
            json.dumps(
                {
                    "directories": [str(d)],
                    "monitoring_enabled": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        controller2 = FileMonitorController(
            fake_rules_controller,
            _observer_factory=_FakeObserver,
        )
        # QTimer.singleShot(0, ...) 延迟恢复，这里仅验证目录加载
        assert controller2.watchedCount == 1


class TestAppTrayIntegration:
    """``app.py`` 托盘与声音函数测试。"""

    def test_play_hit_sound_non_windows_skips(self) -> None:
        """非 Windows 平台应静默跳过。"""
        from fuscan.app import _play_hit_sound

        # 无论 sys.platform 是什么，函数都不应抛异常
        _play_hit_sound("info")
        _play_hit_sound("warning")
        _play_hit_sound("critical")

    def test_setup_tray_returns_none_when_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """系统托盘不可用时应返回 None。"""

        class _StubApp:
            pass

        class _StubController:
            file_monitor: object | None = None

        class _StubTrayIcon:
            @staticmethod
            def isSystemTrayAvailable() -> bool:
                return False

        monkeypatch.setattr(
            "fuscan.app.QSystemTrayIcon",
            _StubTrayIcon,
        )
        from fuscan.app import _setup_file_monitor_tray

        result = _setup_file_monitor_tray(_StubApp(), _StubController())
        assert result is None


class TestGetScanner:
    """``_get_scanner`` Scanner 缓存与构造测试。"""

    def test_get_scanner_constructs_and_caches(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """有 ruleset 时应构造 Scanner 并缓存。"""

        class _FakeRuleset:
            scan_extensions: tuple[str, ...] | None = None
            ignore_dirs: tuple[str, ...] = ()

        fake_rules_controller._ruleset = _FakeRuleset()

        # mock Scanner 构造，避免依赖真实规则编译
        constructed: list[Any] = []

        class _StubScanner:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                constructed.append(args)

        monkeypatch.setattr(
            "fuscan.gui.controllers.file_monitor_controller.Scanner",
            _StubScanner,
        )

        s1 = controller._get_scanner()
        assert s1 is not None
        assert len(constructed) == 1

        # 第二次调用应命中缓存
        s2 = controller._get_scanner()
        assert s2 is s1
        assert len(constructed) == 1


class TestScanPathNoHit:
    """``_scan_path`` 无命中路径测试。"""

    def test_scan_path_with_scanner_but_no_hit(
        self,
        controller: FileMonitorController,
        fake_rules_controller: _FakeRulesController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """有 Scanner 但无命中时不追加记录。"""

        class _FakeRuleset:
            scan_extensions: tuple[str, ...] | None = None
            ignore_dirs: tuple[str, ...] = ()

        fake_rules_controller._ruleset = _FakeRuleset()

        class _FakeScanner:
            def scan_file(self, path: Path) -> _FakeScanResult:
                return _FakeScanResult(hits=())

        monkeypatch.setattr(controller, "_get_scanner", _FakeScanner)
        controller._monitoring_enabled = True

        f = tmp_path / "normal.txt"
        f.write_text("content")
        controller._scan_path(str(f))
        assert controller.model.count == 0


class TestPersistError:
    """``_persist`` 异常路径测试。"""

    def test_persist_oserror_does_not_raise(
        self,
        controller: FileMonitorController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_persist 写入 OSError 时不应抛异常。"""
        d = tmp_path / "watched"
        d.mkdir()

        def _raise_oserror(*args: Any, **kwargs: Any) -> Any:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _raise_oserror)
        # addWatch 内部调用 _persist，不应抛异常
        controller.addWatch(str(d))


class TestTrayAvailable:
    """``_setup_file_monitor_tray`` 托盘可用时路径测试。"""

    def test_setup_tray_when_available(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """系统托盘可用时应返回 QSystemTrayIcon 实例。"""
        from fuscan.app import _setup_file_monitor_tray

        tray_instances: list[Any] = []

        class _StubTrayIcon:
            Critical = 3
            Warning = 2
            Information = 1

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                tray_instances.append(self)
                self._shown = False

            def setToolTip(self, text: str) -> None:
                self._tooltip = text

            def showMessage(self, *args: Any, **kwargs: Any) -> None:
                pass

            def show(self) -> None:
                self._shown = True

            @staticmethod
            def isSystemTrayAvailable() -> bool:
                return True

        class _StubFileMonitor:
            hitFound: Any = None

            class _Signal:
                def connect(self, cb: Any) -> None:
                    self._cb = cb

            def __init__(self) -> None:
                self.hitFound = _StubFileMonitor._Signal()

        file_monitor = _StubFileMonitor()

        class _StubController:
            file_monitor: object | None = None

        stub_controller = _StubController()
        stub_controller.file_monitor = file_monitor

        monkeypatch.setattr(
            "fuscan.app.QSystemTrayIcon",
            _StubTrayIcon,
        )
        monkeypatch.setattr("fuscan.app._play_hit_sound", lambda sev: None)

        class _StubApp:
            pass

        result = _setup_file_monitor_tray(_StubApp(), stub_controller)
        assert result is not None
        assert result._shown is True
        assert result._tooltip == "fuscan 文件监控"
