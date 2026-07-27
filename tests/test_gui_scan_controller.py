"""``ScanController`` 单元测试。

验证扫描状态机（setup/scanning/results）、扫描模式、进度属性、
结果模型与选中结果管理。耗时操作（真实 ScanWorker）通过 monkeypatch
替换为 FakeWorker，避免 QThread 导致测试崩溃。
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
    from fuscan.config import Config  # noqa: F401
    from fuscan.gui.controllers.config_controller import ConfigController
    from fuscan.gui.controllers.rules_controller import RulesController
    from fuscan.gui.controllers.scan_controller import ScanController
    from fuscan.gui.models.result_model import ResultListModel
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
    pytest.skip("PySide 未安装，跳过扫描控制器测试", allow_module_level=True)


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


class FakeSignal:
    """模拟 PySide2 Signal 对象：通过 ``connect`` 注册回调，``emit`` 触发。"""

    def __init__(self) -> None:
        self._callbacks: list[Any] = []

    def connect(self, cb: Any) -> None:
        self._callbacks.append(cb)

    def emit(self, payload: Any = None) -> None:
        for cb in self._callbacks:
            cb(payload)


class FakeStatsWorker:
    """模拟 FileStatsWorker，避免启动真实 QThread。

    记录构造参数供断言，``start`` 触发用户预设的信号回调。
    """

    instances: list[FakeStatsWorker] = []

    def __init__(self, **kwargs: Any) -> None:
        self.__class__.instances.append(self)
        self.kwargs = kwargs
        self.progress_info = FakeSignal()
        self.finished_stats = FakeSignal()
        self.failed = FakeSignal()
        self.cancelled = FakeSignal()
        self.cancel_called = False
        self.pause_called = False
        self.resume_called = False
        self.wait_called = False
        self._running = True

    def start(self) -> None:
        """start() 不自动触发回调，由测试显式调用 emit_* 方法。"""

    def cancel(self) -> None:
        self.cancel_called = True

    def pause(self) -> None:
        self.pause_called = True

    def resume(self) -> None:
        self.resume_called = True

    def wait(self, _msecs: int = 0) -> bool:
        self.wait_called = True
        self._running = False
        return True

    def deleteLater(self) -> None:
        """模拟 Qt deleteLater。"""

    def isRunning(self) -> bool:
        return self._running

    def emit_progress(self, info: ProgressInfo) -> None:
        self.progress_info.emit(info)

    def emit_finished(self, results: list[WalkResult]) -> None:
        self.finished_stats.emit(results)

    def emit_failed(self, error: str) -> None:
        self.failed.emit(error)

    def emit_cancelled(self, results: list[WalkResult]) -> None:
        self.cancelled.emit(results)


class FakeScanWorker:
    """模拟 ScanWorker，避免启动真实 QThread。"""

    instances: list[FakeScanWorker] = []

    def __init__(self, **kwargs: Any) -> None:
        self.__class__.instances.append(self)
        self.kwargs = kwargs
        self.progress_info = FakeSignal()
        self.finished_report = FakeSignal()
        self.failed = FakeSignal()
        self.cancelled = FakeSignal()
        self.cancel_called = False
        self.pause_called = False
        self.resume_called = False
        self.wait_called = False
        self._running = True

    def start(self) -> None:
        """start() 不自动触发回调。"""

    def cancel(self) -> None:
        self.cancel_called = True

    def pause(self) -> None:
        self.pause_called = True

    def resume(self) -> None:
        self.resume_called = True

    def wait(self, _msecs: int = 0) -> bool:
        self.wait_called = True
        self._running = False
        return True

    def deleteLater(self) -> None:
        """模拟 Qt deleteLater。"""

    def isRunning(self) -> bool:
        return self._running

    def emit_progress(self, info: ProgressInfo) -> None:
        self.progress_info.emit(info)

    def emit_finished(self, report: ScanReport) -> None:
        self.finished_report.emit(report)

    def emit_failed(self, error: str) -> None:
        self.failed.emit(error)

    def emit_cancelled(self, report: ScanReport) -> None:
        self.cancelled.emit(report)


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将 ~/.fuscan 重定向到 tmp_path，避免污染用户配置。"""
    fake_home = tmp_path / "fuscan_home"
    fake_home.mkdir()
    config_dir = fake_home / ".fuscan"
    config_dir.mkdir()
    monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_dir / "config.yaml")
    return config_dir


@pytest.fixture()
def config_controller(config_dir: Path) -> ConfigController:
    return ConfigController()


@pytest.fixture()
def rules_controller(config_controller: ConfigController) -> RulesController:
    return RulesController(config_controller)


@pytest.fixture()
def controller(config_controller: ConfigController, rules_controller: RulesController) -> ScanController:
    return ScanController(config_controller, rules_controller)


@pytest.fixture()
def fake_workers(monkeypatch: pytest.MonkeyPatch) -> tuple[list[FakeStatsWorker], list[FakeScanWorker]]:
    """替换 ScanController 中的 FileStatsWorker 与 ScanWorker 为 Fake。"""
    FakeStatsWorker.instances.clear()
    FakeScanWorker.instances.clear()
    monkeypatch.setattr("fuscan.gui.controllers.scan_controller.FileStatsWorker", FakeStatsWorker)
    monkeypatch.setattr("fuscan.gui.controllers.scan_controller.ScanWorker", FakeScanWorker)
    return FakeStatsWorker.instances, FakeScanWorker.instances


class TestInitialState:
    def test_scan_state_is_setup(self, controller: ScanController) -> None:
        assert controller.scanState == "setup"

    def test_is_paused_false(self, controller: ScanController) -> None:
        assert controller.isPaused is False

    def test_progress_zero(self, controller: ScanController) -> None:
        assert controller.progressScanned == 0
        assert controller.progressTotal == 0

    def test_status_text_default(self, controller: ScanController) -> None:
        assert controller.statusText == "就绪"

    def test_result_model_exposed(self, controller: ScanController) -> None:
        assert isinstance(controller.resultModel, ResultListModel)

    def test_selected_result_index_default_negative(self, controller: ScanController) -> None:
        assert controller.selectedResultIndex == -1


class TestScanMode:
    def test_scan_mode_default_folder(self, controller: ScanController) -> None:
        """默认扫描模式为 folder（索引 2）。"""
        assert controller.scanModeIndex == 2

    def test_set_scan_mode_index_emits_signal(self, controller: ScanController) -> None:
        emitted: list[None] = []
        controller.scanModeChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setScanModeIndex(0)
        assert controller.scanModeIndex == 0
        assert len(emitted) == 1

    def test_set_scan_mode_index_noop_when_same(self, controller: ScanController) -> None:
        controller.setScanModeIndex(0)
        emitted: list[None] = []
        controller.scanModeChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setScanModeIndex(0)
        assert len(emitted) == 0


class TestFolderRoot:
    def test_set_folder_root_emits_signal(self, controller: ScanController) -> None:
        """setFolderRoot 应 emit folderRootChanged 信号。"""
        emitted: list[None] = []
        controller.folderRootChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setFolderRoot("/tmp/test")
        assert len(emitted) == 1

    def test_set_folder_root_noop_when_same(self, controller: ScanController) -> None:
        """重复设置相同值不应 emit 信号。"""
        controller.setFolderRoot("/tmp/test")
        emitted: list[None] = []
        controller.folderRootChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setFolderRoot("/tmp/test")
        assert len(emitted) == 0

    def test_set_folder_root_empty_string_noop(self, controller: ScanController) -> None:
        """空字符串不应 emit 信号（视为无效路径）。"""
        emitted: list[None] = []
        controller.folderRootChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setFolderRoot("")
        assert len(emitted) == 0


class TestCanStartScan:
    def test_can_start_scan_false_when_no_ruleset(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
    ) -> None:
        """禁用内置规则且无自定义规则时 canStartScan 为 False。"""
        config_controller.config.use_builtin = False
        rules_controller.setUseBuiltin(False)
        controller = ScanController(config_controller, rules_controller)
        assert controller.canStartScan is False

    def test_can_start_scan_false_when_no_target(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
    ) -> None:
        """有规则集但无扫描目标时 canStartScan 为 False。"""
        controller = ScanController(config_controller, rules_controller)
        # folder 模式但 folder_root 为空
        controller.setScanModeIndex(2)
        controller.setFolderRoot("")
        assert controller.canStartScan is False

    def test_can_start_scan_true_when_ruleset_and_target(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """有规则集且有扫描目标时 canStartScan 为 True。"""
        controller = ScanController(config_controller, rules_controller)
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        assert controller.canStartScan is True


class TestSelectedResult:
    def test_set_selected_result_index_emits_signal(self, controller: ScanController) -> None:
        emitted: list[None] = []
        controller.selectedResultChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setSelectedResultIndex(5)
        assert controller.selectedResultIndex == 5
        assert len(emitted) == 1

    def test_set_selected_result_index_noop_when_same(self, controller: ScanController) -> None:
        controller.setSelectedResultIndex(3)
        emitted: list[None] = []
        controller.selectedResultChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setSelectedResultIndex(3)
        assert len(emitted) == 0

    def test_detail_file_path_empty_when_no_selection(self, controller: ScanController) -> None:
        """未选中结果时 detailFilePath 为空字符串。"""
        assert controller.detailFilePath == ""

    def test_detail_hits_count_zero_when_no_selection(self, controller: ScanController) -> None:
        """未选中结果时 detailHitsCount 为 0。"""
        assert controller.detailHitsCount == 0

    def test_detail_hits_model_empty_when_no_selection(self, controller: ScanController) -> None:
        """未选中结果时 detailHitsModel 为空列表。"""
        assert controller.detailHitsModel == []


class TestRulesetChange:
    def test_ruleset_loaded_on_construction(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
    ) -> None:
        """构造时应从 rules_controller 加载初始 ruleset（一次性快照）。"""
        controller = ScanController(config_controller, rules_controller)
        # 默认启用内置规则，ruleset 应非 None
        assert controller._ruleset is not None

    def test_set_workspace_ruleset_updates_controller(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
    ) -> None:
        """iter-107：setWorkspaceRuleset 注入新 ruleset 后 controller 应更新。"""
        controller = ScanController(config_controller, rules_controller)
        assert controller._ruleset is not None
        # 注入 None 规则集（模拟工作区无规则）
        controller.setWorkspaceRuleset(None, [], False)
        # ruleset 应变为 None
        assert controller._ruleset is None
        # 工作区规则路径与内置开关也应同步
        assert controller._workspace_rules_paths == ()
        assert controller._workspace_use_builtin is False


class TestOpenLocation:
    def test_open_location_invalid_index_noop(self, controller: ScanController) -> None:
        """无效索引时 openLocation 不应抛异常。"""
        # 未设置结果，调用不应抛异常
        controller.openLocation()

    def test_copy_path_invalid_index_noop(self, controller: ScanController) -> None:
        """无效索引时 copyPath 不应抛异常。"""
        controller.copyPath()


class TestCleanup:
    def test_cleanup_no_workers_noop(self, controller: ScanController) -> None:
        """无 worker 时 cleanup 不应抛异常。"""
        controller.cleanup()


def _make_scan_result(path: Path = Path("/tmp/test.txt"), hits: int = 1) -> ScanResult:
    """构造测试用 ScanResult。"""
    rule_hits = tuple(
        RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail=f"命中 {i}") for i in range(hits)
    )
    return ScanResult(path=path, size=100, hits=rule_hits)


def _make_scan_report(
    results: tuple[ScanResult, ...] = (),
    cancelled: bool = False,
    duration: float = 1.0,
) -> ScanReport:
    """构造测试用 ScanReport。"""
    return ScanReport(
        root=Path("/tmp"),
        results=results,
        stats=ScanStats(
            total_files=10,
            scanned_files=10,
            matched_files=len(results),
            skipped_files=0,
            errors=0,
            duration_seconds=duration,
            total_matches=len(results),
        ),
        cancelled=cancelled,
    )


def _make_walk_result(root: Path = Path("/tmp")) -> WalkResult:
    """构造测试用 WalkResult。"""
    return WalkResult(root=root, entries=(), total=5, skipped=1)


class TestStartScan:
    """测试 startScan 工作流：stats worker 启动与状态切换。"""

    def test_start_scan_creates_stats_worker(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """startScan 应创建 stats worker 并切换到 scanning 状态。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(2)  # folder 模式
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        assert controller.scanState == "scanning"
        assert len(stats_instances) == 1
        assert len(scan_instances) == 0
        assert controller.progressIndeterminate is True

    def test_start_scan_noop_when_scanning(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """scanning 态重复 startScan 应被忽略。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        assert len(stats_instances) == 1
        controller.startScan()  # 重复调用
        assert len(stats_instances) == 1  # 不应创建新 worker

    def test_start_scan_noop_when_no_ruleset(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """无规则集时 startScan 应被忽略。"""
        stats_instances, _ = fake_workers
        # iter-107：通过 setWorkspaceRuleset 注入空规则集（取代旧 setUseBuiltin 监听）
        controller.setWorkspaceRuleset(None, [], False)
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        assert len(stats_instances) == 0
        assert controller.scanState == "setup"

    def test_start_scan_noop_when_no_target(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
    ) -> None:
        """无扫描目标时 startScan 应被忽略。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot("")
        controller.startScan()
        assert len(stats_instances) == 0


class TestStatsWorkerCallbacks:
    """测试 stats worker 信号回调。"""

    def test_on_stats_finished_creates_scan_worker(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """stats 完成 应创建 scan worker。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        stats_worker = stats_instances[0]
        walk_results = [_make_walk_result(tmp_path)]
        stats_worker.emit_finished(walk_results)

        assert len(scan_instances) == 1
        assert scan_instances[0].kwargs.get("precollected") == walk_results

    def test_on_stats_failed_resets_to_setup(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """stats 失败应重置到 setup 状态。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        stats_worker = stats_instances[0]
        stats_worker.emit_failed("统计失败")

        assert controller.scanState == "setup"
        assert "统计失败" in controller.statusText

    def test_on_stats_cancelled_resets_to_setup(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """stats 取消应重置到 setup 状态。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        stats_worker = stats_instances[0]
        stats_worker.emit_cancelled([])

        assert controller.scanState == "setup"
        assert controller.statusText == "已取消"


class TestScanWorkerCallbacks:
    """测试 scan worker 信号回调。"""

    def _start_and_finish_stats(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> FakeScanWorker:
        """辅助：启动扫描并完成 stats 阶段，返回 scan worker。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        stats_worker = stats_instances[0]
        stats_worker.emit_finished([_make_walk_result(tmp_path)])
        return scan_instances[0]

    def test_on_scan_finished_with_results(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """扫描完成且有结果应切换到 results 状态。"""
        scan_worker = self._start_and_finish_stats(controller, fake_workers, tmp_path)
        result = _make_scan_result(tmp_path / "test.txt")
        report = _make_scan_report(results=(result,))
        scan_worker.emit_finished(report)

        assert controller.scanState == "results"
        assert controller.matchedCount == 1
        assert controller.resultModel.rowCount() == 1

    def test_on_scan_finished_no_results(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """扫描完成无结果应回到 setup 状态。"""
        scan_worker = self._start_and_finish_stats(controller, fake_workers, tmp_path)
        report = _make_scan_report(results=())
        scan_worker.emit_finished(report)

        assert controller.scanState == "setup"

    def test_on_scan_failed_resets_to_setup(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """扫描失败应重置到 setup 状态。"""
        scan_worker = self._start_and_finish_stats(controller, fake_workers, tmp_path)
        scan_worker.emit_failed("扫描异常")

        assert controller.scanState == "setup"
        assert controller.statusText == "扫描失败"
        assert controller.statusSummary == "扫描异常"

    def test_on_scan_cancelled_with_results(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """扫描取消但有结果应切换到 results 状态。"""
        scan_worker = self._start_and_finish_stats(controller, fake_workers, tmp_path)
        result = _make_scan_result(tmp_path / "test.txt")
        report = _make_scan_report(results=(result,), cancelled=True)
        scan_worker.emit_cancelled(report)

        assert controller.scanState == "results"
        assert controller.statusText == "已完成[用户取消]"

    def test_on_scan_cancelled_no_results(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """扫描取消无结果应回到 setup 状态。"""
        scan_worker = self._start_and_finish_stats(controller, fake_workers, tmp_path)
        report = _make_scan_report(results=(), cancelled=True)
        scan_worker.emit_cancelled(report)

        assert controller.scanState == "setup"


class TestProgressCallback:
    """测试 _on_scan_progress 进度回调。"""

    def test_progress_updates_properties(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """进度回调应更新 controller 的进度属性。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        info = ProgressInfo(
            current_file="/tmp/test.txt",
            scanned=5,
            total=10,
            skipped=1,
            matched=2,
            errors=0,
            elapsed=1.0,
            matches=3,
            phase="scan",
        )
        stats_instances[0].emit_progress(info)

        assert controller.progressScanned == 5
        assert controller.progressTotal == 10
        assert controller.matchedCount == 2
        assert controller.skippedCount == 1
        assert controller.passedCount == 3  # 5 - 2 - 0
        assert controller.progressIndeterminate is False
        assert "test.txt" in controller.currentFile

    def test_progress_truncates_long_file_path(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """超长文件路径应被截断显示。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        long_path = "/tmp/" + "a" * 200 + "/test.txt"
        info = ProgressInfo(current_file=long_path, scanned=1, total=1, phase="scan")
        stats_instances[0].emit_progress(info)

        assert len(controller.currentFile) <= 100
        assert controller.currentFile.startswith("...")

    def test_progress_ignored_when_cancelling(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """取消中状态下进度回调应被忽略。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        # 模拟取消中状态
        controller._cancelling = True
        info = ProgressInfo(current_file="/tmp/test.txt", scanned=99, total=99, phase="scan")
        stats_instances[0].emit_progress(info)

        # 进度不应更新
        assert controller.progressScanned == 0


class TestScanPhaseProgress:
    """iter-105 双进度条：测试扫描阶段独立进度字段与切换。"""

    def test_initial_phase_is_setup(self, controller: ScanController) -> None:
        """初始 scanPhase 应为 setup，所有阶段计数为零。"""
        assert controller.scanPhase == "setup"
        assert controller.walkDiscovered == 0
        assert controller.walkSkipped == 0
        assert controller.walkUserSkipped == 0
        assert controller.walkDone is False
        assert controller.scanDone is False
        assert controller.walkProgress == 0.0

    def test_start_scan_enters_walk_phase(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """startScan 应将 scanPhase 切换为 walk，并标记 walk 阶段为 indeterminate。"""
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        assert controller.scanPhase == "walk"
        assert controller.walkIndeterminate is True
        assert controller.walkDone is False
        assert controller.scanDone is False

    def test_walk_progress_updates_walk_fields(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """walk 阶段进度回调应仅更新 walk_* 字段，scan 字段保持零。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        # walk 阶段进度：发现 100，跳过 30，用户跳过 5
        info = ProgressInfo(
            current_file="/tmp/x.txt",
            scanned=0,
            total=100,
            skipped=30,
            matched=0,
            errors=0,
            elapsed=1.0,
            matches=0,
            phase="walk",
            user_skipped=5,
        )
        stats_instances[0].emit_progress(info)

        assert controller.scanPhase == "walk"
        assert controller.walkIndeterminate is False
        assert controller.walkDiscovered == 100
        assert controller.walkSkipped == 30
        assert controller.walkUserSkipped == 5
        # walk 阶段 scan 字段不更新
        assert controller.progressScanned == 0
        assert controller.progressTotal == 0
        assert controller.matchedCount == 0
        # walkProgress = (100 - 30 - 5) / 100 = 65%
        assert controller.walkProgress == 65.0

    def test_walk_phase_to_scan_phase_marks_walk_done(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """phase 从 walk 切换到 scan 时应标记 walk_done=True。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        # walk 进度
        walk_info = ProgressInfo(current_file="/tmp/x.txt", total=50, skipped=10, phase="walk")
        stats_instances[0].emit_progress(walk_info)
        assert controller.walkDone is False

        # 切换到 scan 阶段
        scan_info = ProgressInfo(
            current_file="/tmp/y.txt",
            scanned=5,
            total=40,
            skipped=10,
            matched=2,
            errors=0,
            matches=2,
            phase="scan",
        )
        stats_instances[0].emit_progress(scan_info)

        assert controller.scanPhase == "scan"
        assert controller.walkDone is True
        assert controller.walkIndeterminate is False
        # walk 字段保留 walk 阶段最终值
        assert controller.walkDiscovered == 50
        assert controller.walkSkipped == 10
        # scan 字段被更新
        assert controller.progressScanned == 5
        assert controller.progressTotal == 40
        assert controller.matchedCount == 2

    def test_walk_progress_zero_when_discovered_zero(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """walkDiscovered=0 时 walkProgress 应返回 0（避免除零）。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        info = ProgressInfo(current_file="", total=0, skipped=0, phase="walk")
        stats_instances[0].emit_progress(info)

        assert controller.walkDiscovered == 0
        assert controller.walkProgress == 0.0

    def test_stats_finished_syncs_walk_totals(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """stats 完成时应从 WalkResult 同步 walk 阶段最终统计。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        walk_results = [
            WalkResult(
                root=tmp_path,
                entries=(),
                total=200,
                skipped=50,
                user_skipped=10,
            )
        ]
        stats_instances[0].emit_finished(walk_results)

        # walk 阶段最终统计应同步
        assert controller.walkDiscovered == 200
        assert controller.walkSkipped == 50
        assert controller.walkUserSkipped == 10
        assert controller.walkDone is True
        assert controller.walkIndeterminate is False
        # scan 阶段切换
        assert controller.scanPhase == "scan"
        # scan 总数为 entries 总数（这里 entries 为空）
        assert controller.progressTotal == 0
        assert len(scan_instances) == 1

    def test_walk_classified_initial_zero(self, controller: ScanController) -> None:
        """初始 walkClassified 应为 0（无扫描数据）。"""
        assert controller.walkClassified == 0

    def test_walk_classified_calc(self, controller: ScanController) -> None:
        """walkClassified = walkDiscovered - walkSkipped - walkUserSkipped，下界 0。"""
        # 直接通过内部字段设置验证 Property 计算
        controller._walk_discovered = 100
        controller._walk_skipped = 30
        controller._walk_user_skipped = 5
        assert controller.walkClassified == 65

    def test_walk_classified_clamped_to_zero(self, controller: ScanController) -> None:
        """当 skipped + user_skipped > discovered 时应下界为 0（避免负数）。"""
        controller._walk_discovered = 10
        controller._walk_skipped = 30
        controller._walk_user_skipped = 5
        assert controller.walkClassified == 0

    def test_walk_classified_after_walk_progress(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """walk 进度回调后 walkClassified 应反映 (discovered - skipped - user_skipped)。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        info = ProgressInfo(
            current_file="/tmp/x.txt",
            scanned=0,
            total=100,
            skipped=30,
            matched=0,
            errors=0,
            elapsed=1.0,
            matches=0,
            phase="walk",
            user_skipped=5,
        )
        stats_instances[0].emit_progress(info)
        # 100 - 30 - 5 = 65
        assert controller.walkClassified == 65

    def test_scan_finished_marks_scan_done(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """scan 完成 应标记 scanDone=True 且 scanPhase=done。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])

        report = _make_scan_report(results=())
        scan_instances[0].emit_finished(report)

        assert controller.scanPhase == "done"
        assert controller.scanDone is True
        assert controller.walkDone is True

    def test_scan_cancelled_marks_scan_done(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """scan 取消 也应标记 scanDone=True 避免进度条卡住。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])

        report = _make_scan_report(results=(), cancelled=True)
        scan_instances[0].emit_cancelled(report)

        assert controller.scanPhase == "done"
        assert controller.scanDone is True


class TestTogglePause:
    """测试 togglePause 暂停/继续。"""

    def test_toggle_pause_pauses_workers(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """togglePause 应调用 stats worker.pause。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        controller.togglePause()
        assert controller.isPaused is True
        assert stats_instances[0].pause_called is True
        assert controller.statusText == "已暂停"

    def test_toggle_pause_resumes_workers(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """二次 togglePause 应调用 stats worker.resume。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        controller.togglePause()  # 暂停
        controller.togglePause()  # 继续
        assert controller.isPaused is False
        assert stats_instances[0].resume_called is True


class TestCancelScan:
    """测试 cancelScan 取消扫描。"""

    def test_cancel_scan_calls_worker_cancel(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """cancelScan 应调用 stats worker.cancel。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        controller.cancelScan()
        assert stats_instances[0].cancel_called is True
        assert controller.statusText == "取消中..."

    def test_cancel_scan_noop_when_no_workers(self, controller: ScanController) -> None:
        """无 worker 时 cancelScan 应被忽略。"""
        controller.cancelScan()  # 不应抛异常


class TestSelectedResultDetails:
    """测试选中结果详情属性。"""

    def test_detail_properties_with_selected_result(
        self,
        controller: ScanController,
    ) -> None:
        """选中结果后 detail 属性应返回正确值。"""
        result = _make_scan_result(Path("/tmp/test.txt"), hits=2)
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)

        assert controller.detailFilePath == str(Path("/tmp/test.txt"))
        assert controller.detailHitsCount == 2
        hits_model = controller.detailHitsModel
        assert len(hits_model) == 2
        assert hits_model[0]["ruleName"] == "敏感内容"
        assert "severityText" in hits_model[0]
        assert "severityColor" in hits_model[0]
        assert "context" in hits_model[0]


class TestExportResults:
    """测试 exportResults 导出。"""

    def test_export_results_noop_when_no_report(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """无报告时 exportResults 应被忽略。"""
        # 不设置 _last_report，直接调用应不抛异常
        controller.exportResults("csv", str(tmp_path / "export.csv"))

    def test_export_results_noop_when_no_hits(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """报告无命中时 exportResults 应被忽略。"""
        controller._last_report = _make_scan_report(results=())
        controller.exportResults("csv", str(tmp_path / "export.csv"))

    def test_export_results_writes_file(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """有命中时应写入导出文件。"""
        result = _make_scan_result(tmp_path / "test.txt")
        controller._last_report = _make_scan_report(results=(result,))

        export_path = tmp_path / "export.csv"
        controller.exportResults("csv", str(export_path))

        assert export_path.exists()
        content = export_path.read_text(encoding="utf-8")
        assert "test.txt" in content or "敏感内容" in content

    def test_export_results_empty_path_noop(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """空路径时不导出（对应 QML FileDialog 取消）。"""
        result = _make_scan_result(tmp_path / "test.txt")
        controller._last_report = _make_scan_report(results=(result,))

        controller.exportResults("csv", "")  # 不应抛异常

    def test_export_results_handles_oserror(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """导出失败时应记录 warning 不抛异常。"""
        result = _make_scan_result(tmp_path / "test.txt")
        controller._last_report = _make_scan_report(results=(result,))

        # 路径指向无效位置触发 OSError
        controller.exportResults("csv", "/nonexistent/path/export.csv")
        assert "导出失败" in controller.statusText


class TestOpenLocationWithResult:
    """测试有选中结果时的 openLocation/copyPath。"""

    def test_open_location_calls_explorer(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """openLocation 应调用 open_path_in_explorer。"""
        result = _make_scan_result(Path("/tmp/test.txt"))
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)

        called: list[Path] = []
        monkeypatch.setattr(
            "fuscan.gui.controllers.scan_controller.open_path_in_explorer",
            called.append,
        )
        controller.openLocation()
        assert called == [Path("/tmp/test.txt")]

    def test_open_location_handles_oserror(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """openLocation 异常时应记录 warning 不抛异常。"""

        def _raise(_: Path) -> None:
            raise OSError("无法打开")

        result = _make_scan_result(Path("/tmp/test.txt"))
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)
        monkeypatch.setattr("fuscan.gui.controllers.scan_controller.open_path_in_explorer", _raise)
        controller.openLocation()  # 不应抛异常

    def test_copy_path_sets_clipboard(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """copyPath 应将路径设置到剪贴板。"""
        result = _make_scan_result(Path("/tmp/test.txt"))
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)

        clipboard_texts: list[str] = []

        class FakeClipboard:
            def setText(self, text: str) -> None:
                clipboard_texts.append(text)

        class FakeGuiApp:
            @staticmethod
            def clipboard() -> FakeClipboard:
                return FakeClipboard()

        # copyPath 内部 from PySide2.QtGui import QGuiApplication，需 patch 源模块
        import PySide2.QtGui as qt_gui_module

        monkeypatch.setattr(qt_gui_module, "QGuiApplication", FakeGuiApp)
        controller.copyPath()
        assert clipboard_texts == [str(Path("/tmp/test.txt"))]
        assert "已复制" in controller.statusText


class TestIter112ResultFilterSort:
    """iter-112：ScanController 过滤+排序 Slot 测试。"""

    def _populate_results(self, controller: ScanController, tmp_path: Path) -> None:
        """构造 3 条命中结果填入 resultModel。"""
        h_critical = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="d1")
        h_warning = RuleHit(rule_name="API 密钥", severity=Severity.WARNING, detail="d2")
        results = (
            ScanResult(path=tmp_path / "config" / "secret.txt", size=10, hits=(h_critical,), errors=0),
            ScanResult(path=tmp_path / "app.py", size=20, hits=(h_warning,), errors=0),
            ScanResult(
                path=tmp_path / "db.yaml",
                size=30,
                hits=(h_critical, h_warning),
                errors=0,
            ),
        )
        controller._result_model.set_results(results)

    def test_set_result_filter_text(self, controller: ScanController, tmp_path: Path) -> None:
        """setResultFilterText 应触发 model 过滤。"""
        self._populate_results(controller, tmp_path)
        assert controller.resultModel.rowCount() == 3
        controller.setResultFilterText("config")
        assert controller.resultModel.rowCount() == 1
        assert controller.resultFilteredCount == 1
        assert controller.resultTotalCount == 3

    def test_set_result_filter_rules(self, controller: ScanController, tmp_path: Path) -> None:
        """setResultFilterRules 应触发规则多选过滤。"""
        self._populate_results(controller, tmp_path)
        controller.setResultFilterRules(["API 密钥"])
        # app.py 与 db.yaml 含 API 密钥
        assert controller.resultFilteredCount == 2

    def test_set_result_filter_severities(self, controller: ScanController, tmp_path: Path) -> None:
        """setResultFilterSeverities 接收中文文本列表并触发过滤。"""
        self._populate_results(controller, tmp_path)
        controller.setResultFilterSeverities(["严重"])
        # secret.txt 与 db.yaml 的 max_severity 为 CRITICAL
        assert controller.resultFilteredCount == 2

    def test_set_result_sort(self, controller: ScanController, tmp_path: Path) -> None:
        """setResultSort 应触发排序。"""
        self._populate_results(controller, tmp_path)
        controller.setResultSort("hitsCount", False)
        # 降序：db.yaml (2 hits) → secret.txt (1) → app.py (1)
        first = controller.resultModel.get_result(0)
        assert first is not None
        assert len(first.hits) == 2

    def test_clear_result_filters(self, controller: ScanController, tmp_path: Path) -> None:
        """clearResultFilters 应清除所有过滤条件。"""
        self._populate_results(controller, tmp_path)
        controller.setResultFilterText("config")
        assert controller.resultFilteredCount == 1
        controller.clearResultFilters()
        assert controller.resultFilteredCount == 3

    def test_filter_resets_selected_index_when_out_of_range(self, controller: ScanController, tmp_path: Path) -> None:
        """过滤后选中索引越界应重置为 -1。"""
        self._populate_results(controller, tmp_path)
        controller.setSelectedResultIndex(2)  # 选中第 3 条
        controller.setResultFilterText("config")  # 过滤后只剩 1 条
        assert controller.selectedResultIndex == -1

    def test_result_rule_names_property(self, controller: ScanController, tmp_path: Path) -> None:
        """resultRuleNames 应返回所有结果中的规则名列表（去重保序）。"""
        self._populate_results(controller, tmp_path)
        names = controller.resultRuleNames
        assert "敏感内容" in names
        assert "API 密钥" in names
        assert len(names) == 2  # 去重


class TestBuildScanRoots:
    """测试 _build_scan_roots 构建扫描根路径。"""

    def test_build_roots_folder_mode(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """folder 模式应返回 folder_root 列表。"""
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        roots = controller._build_scan_roots()
        assert roots == [tmp_path]

    def test_build_roots_drive_mode(self, controller: ScanController) -> None:
        """drive 模式应返回选中盘符列表。"""
        controller.setScanModeIndex(1)
        controller.setSelectedDrive("C:")
        roots = controller._build_scan_roots()
        assert roots == [Path("C:")]

    def test_build_roots_drive_mode_no_selection(self, controller: ScanController) -> None:
        """drive 模式无选中盘符应返回空列表。"""
        controller.setScanModeIndex(1)
        controller.setSelectedDrive("")
        roots = controller._build_scan_roots()
        assert roots == []

    def test_build_roots_folder_mode_empty(self, controller: ScanController) -> None:
        """folder 模式空路径应返回空列表。"""
        controller.setScanModeIndex(2)
        controller.setFolderRoot("")
        roots = controller._build_scan_roots()
        assert roots == []

    def test_build_roots_full_mode(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """full 模式应调用 list_drives。"""
        controller.setScanModeIndex(0)
        monkeypatch.setattr(
            "fuscan.scanner.walker.list_drives",
            lambda include_network=False: [Path("C:"), Path("D:")],
        )
        roots = controller._build_scan_roots()
        assert roots == [Path("C:"), Path("D:")]


class TestBuildCacheContext:
    """测试 _build_cache_context 构造缓存上下文。"""

    def test_build_cache_context_disabled(self, controller: ScanController) -> None:
        """cache_enabled=False 时应返回 (None, None)。"""
        controller._config.cache_enabled = False
        cache, source_files = controller._build_cache_context()
        assert cache is None
        assert source_files is None

    def test_build_cache_context_enabled(
        self,
        controller: ScanController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cache_enabled=True 时应构造 CacheStore。"""

        class FakeCacheStore:
            def __init__(self, path: Path) -> None:
                self.path = path

            def close(self) -> None:
                pass

        cache_path = tmp_path / "cache.db"
        monkeypatch.setattr("fuscan.cache.CacheStore", FakeCacheStore)
        monkeypatch.setattr("fuscan.cache.compute_source_files", lambda paths, use_builtin: {})
        controller._config.cache_enabled = True
        controller._config.cache_path = str(cache_path)
        controller._cache = None  # 重置缓存

        cache, source_files = controller._build_cache_context()
        assert isinstance(cache, FakeCacheStore)
        assert source_files == {}

    def test_build_cache_context_reuses_existing(
        self,
        controller: ScanController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """已有 _cache 时应复用。"""

        class FakeCacheStore:
            def __init__(self, path: Path) -> None:
                self.path = path

            def close(self) -> None:
                pass

        monkeypatch.setattr("fuscan.cache.compute_source_files", lambda paths, use_builtin: {})
        controller._config.cache_enabled = True
        existing_cache = FakeCacheStore(tmp_path / "existing.db")
        controller._cache = existing_cache  # type: ignore[bad-assignment]

        cache, _ = controller._build_cache_context()
        assert cache is existing_cache


class TestCleanupWithWorkers:
    """测试有 worker 时的 cleanup。"""

    def test_cleanup_with_stats_worker(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """有 stats worker 时 cleanup 应取消并等待。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        stats_worker = stats_instances[0]

        controller.cleanup()
        assert stats_worker.cancel_called is True
        assert stats_worker.wait_called is True

    def test_cleanup_with_scan_worker(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """有 scan worker 时 cleanup 应取消并等待。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(2)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])
        scan_worker = scan_instances[0]

        controller.cleanup()
        assert scan_worker.cancel_called is True
        assert scan_worker.wait_called is True

    def test_cleanup_closes_cache(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """有 cache 时 cleanup 应关闭。"""

        class FakeCacheStore:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        cache = FakeCacheStore()
        controller._cache = cache  # type: ignore[bad-assignment]
        controller.cleanup()
        assert cache.closed is True
        assert controller._cache is None
