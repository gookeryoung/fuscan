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
    from fuscan.gui.controllers._result_detail import build_detail_hits_full
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
        ScanParams,
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

    def disconnect(self, cb: Any) -> None:
        """移除已注册回调；未注册时抛 RuntimeError（与 PySide2 一致）。"""
        try:
            self._callbacks.remove(cb)
        except ValueError as exc:
            raise RuntimeError from exc

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
        # iter-124：模拟 FileStatsWorker.manifest 属性（None 表示未构建清单）
        self.manifest: Any = None

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


class FakeDetailSignal:
    """模拟 :class:`DetailWorker` 的双参数 ``done`` 信号 ``Signal(list, int)``。

    :class:`FakeSignal` 仅支持单参数 emit，命中详情信号需回传
    ``(model, generation)`` 两参数，故单列一个双参数版本。
    """

    def __init__(self) -> None:
        self._callbacks: list[Any] = []

    def connect(self, cb: Any) -> None:
        self._callbacks.append(cb)

    def disconnect(self, cb: Any) -> None:
        """移除已注册回调；未注册时抛 RuntimeError（与 PySide2 一致）。"""
        try:
            self._callbacks.remove(cb)
        except ValueError as exc:
            raise RuntimeError from exc

    def emit(self, model: Any, generation: int) -> None:
        for cb in list(self._callbacks):
            cb(model, generation)


class FakeDetailWorker:
    """模拟 :class:`DetailWorker`，避免启动真实 QThread。

    构造时记录 ``result``/``generation`` 供断言，``start`` 不自动 emit，
    由测试显式调用 :meth:`emit_done` 模拟后台补齐上下文完成。
    """

    instances: list[FakeDetailWorker] = []

    def __init__(self, result: ScanResult | None, generation: int) -> None:
        self.__class__.instances.append(self)
        self.result = result
        self.generation = generation
        self.done = FakeDetailSignal()
        self.start_called = False
        self.quit_called = False
        self.wait_called = False
        self.terminate_called = False
        self._running = True

    def start(self) -> None:
        """start() 不自动触发回调，由测试显式调用 emit_done。"""
        self.start_called = True

    def quit(self) -> None:
        self.quit_called = True

    def wait(self, _msecs: int = 0) -> bool:
        self.wait_called = True
        self._running = False
        return True

    def terminate(self) -> None:
        self.terminate_called = True

    def deleteLater(self) -> None:
        """模拟 Qt deleteLater。"""

    def isRunning(self) -> bool:
        return self._running

    def emit_done(self, model: list[dict[str, object]], generation: int) -> None:
        self.done.emit(model, generation)


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


@pytest.fixture()
def fake_detail_workers(monkeypatch: pytest.MonkeyPatch) -> list[FakeDetailWorker]:
    """替换 ScanController 中的 DetailWorker 为 Fake，返回构造实例列表供断言。"""
    FakeDetailWorker.instances.clear()
    monkeypatch.setattr("fuscan.gui.controllers.scan_controller.DetailWorker", FakeDetailWorker)
    return FakeDetailWorker.instances


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
        """默认扫描模式为 folder（索引 1）。"""
        assert controller.scanModeIndex == 1

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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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

    def test_ruleset_changed_signal_updates_cache(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
    ) -> None:
        """iter-139：rulesetChanged 信号应更新 ScanController 缓存的 ruleset。"""
        controller = ScanController(config_controller, rules_controller)
        # 触发 rulesetChanged 信号，模拟规则文件变更
        rules_controller.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]
        # 缓存应已更新（引用 rules_controller 最新的 ruleset）
        assert controller._ruleset is rules_controller.ruleset
        # 若 ruleset 内容未变，引用应与原对象相同；若变则不同
        assert controller._ruleset is not None

    def test_can_start_scan_reads_latest_ruleset(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """iter-139：canStartScan 应读取 rules_controller 最新 ruleset，而非陈旧缓存。"""
        # 准备一个扫描根目录（folder 模式）
        scan_root = tmp_path / "scan"
        scan_root.mkdir()

        controller = ScanController(config_controller, rules_controller)
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(scan_root))
        # 默认启用内置规则，可启动
        assert controller.canStartScan is True
        assert controller.rulesCount > 0

        # 模拟规则集被清空：直接置空 rules_controller._ruleset 并触发信号
        rules_controller._ruleset = None
        rules_controller.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

        # canStartScan 应反映最新状态：无规则集时为 False
        assert controller.canStartScan is False
        assert controller.rulesCount == 0


class TestComputeEffectiveRulesetTempRules:
    """``_compute_effective_ruleset`` 临时规则叠加测试（iter-138）。"""

    def test_temp_rules_added_on_top_of_global(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """临时规则文件应叠加在全局规则集之上。"""
        # 写入临时规则文件（包含一条全局没有的规则）
        temp_file = tmp_path / "temp.yaml"
        temp_file.write_text(
            'version: "1.0"\n'
            "rules:\n"
            '  - name: "临时规则-unique_temp_marker"\n'
            "    severity: warning\n"
            "    match:\n"
            "      type: content\n"
            "      target: content\n"
            "      mode: contains\n"
            '      pattern: "unique_temp_marker_xyz"\n',
            encoding="utf-8",
        )
        controller = ScanController(config_controller, rules_controller)
        # 初始规则数（仅内置规则）
        initial_count = controller.rulesCount
        # 设置临时规则覆盖
        controller.setTaskOverride("temp_rules_paths", (str(temp_file),))
        # 规则数应增加（临时规则叠加）
        assert controller.rulesCount > initial_count
        # 验证临时规则确实被加载
        assert controller._ruleset is not None
        rule_names = [r.name for r in controller._ruleset.rules]
        assert "临时规则-unique_temp_marker" in rule_names

    def test_temp_rules_without_override_uses_global(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
    ) -> None:
        """无任务级覆盖且无临时规则时直接取全局 ruleset。"""
        controller = ScanController(config_controller, rules_controller)
        # 无 temp_rules_paths 时 ruleset 应等同于全局
        assert controller._ruleset is rules_controller.ruleset

    def test_temp_rules_nonexistent_file_filtered(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """不存在的临时规则文件应被过滤，不影响 ruleset。"""
        controller = ScanController(config_controller, rules_controller)
        initial_count = controller.rulesCount
        # 设置不存在的临时规则文件
        controller.setTaskOverride("temp_rules_paths", (str(tmp_path / "missing.yaml"),))
        # 规则数应不变（不存在的文件被过滤）
        assert controller.rulesCount == initial_count

    def test_temp_rules_cleared(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """清除临时规则后 ruleset 应回退到全局。"""
        temp_file = tmp_path / "temp.yaml"
        temp_file.write_text(
            'version: "1.0"\n'
            "rules:\n"
            '  - name: "临时规则"\n'
            "    severity: warning\n"
            "    match:\n"
            "      type: content\n"
            "      target: content\n"
            "      mode: contains\n"
            '      pattern: "temp_pattern"\n',
            encoding="utf-8",
        )
        controller = ScanController(config_controller, rules_controller)
        # 添加临时规则
        controller.setTaskOverride("temp_rules_paths", (str(temp_file),))
        count_with_temp = controller.rulesCount
        assert count_with_temp > 0
        # 清除临时规则（设为空元组）
        controller.setTaskOverride("temp_rules_paths", ())
        # 规则数应回退
        assert controller.rulesCount < count_with_temp
        # 等同于全局 ruleset
        assert controller._ruleset is rules_controller.ruleset

    def test_temp_rules_with_rules_paths_override(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """同时设置 rules_paths 覆盖和 temp_rules_paths 时两者都生效。"""
        # 任务级 rules_paths 覆盖文件
        override_file = tmp_path / "override.yaml"
        override_file.write_text(
            'version: "1.0"\n'
            "rules:\n"
            '  - name: "覆盖规则"\n'
            "    severity: critical\n"
            "    match:\n"
            "      type: content\n"
            "      target: content\n"
            "      mode: contains\n"
            '      pattern: "override_pattern"\n',
            encoding="utf-8",
        )
        # 临时规则文件
        temp_file = tmp_path / "temp.yaml"
        temp_file.write_text(
            'version: "1.0"\n'
            "rules:\n"
            '  - name: "临时规则"\n'
            "    severity: warning\n"
            "    match:\n"
            "      type: content\n"
            "      target: content\n"
            "      mode: contains\n"
            '      pattern: "temp_pattern"\n',
            encoding="utf-8",
        )
        controller = ScanController(config_controller, rules_controller)
        # 同时设置任务级 rules_paths 覆盖（禁用内置）和临时规则
        controller.setTaskOverride("use_builtin", False)
        controller.setTaskOverride("rules_paths", (str(override_file),))
        controller.setTaskOverride("temp_rules_paths", (str(temp_file),))
        # 验证两种规则都被加载
        assert controller._ruleset is not None
        rule_names = [r.name for r in controller._ruleset.rules]
        assert "覆盖规则" in rule_names
        assert "临时规则" in rule_names
        # 内置规则应被禁用（use_builtin=False）
        # 检查规则数 = 1（覆盖）+ 1（临时）= 2
        assert len(controller._ruleset.rules) == 2

    def test_disabled_temp_rule_filtered_from_ruleset(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """禁用的临时规则应被过滤，不参与 ruleset 合并（iter-140）。"""
        temp_file = tmp_path / "temp.yaml"
        temp_file.write_text(
            'version: "1.0"\n'
            "rules:\n"
            '  - name: "临时规则-应被过滤"\n'
            "    severity: warning\n"
            "    match:\n"
            "      type: content\n"
            "      target: content\n"
            "      mode: contains\n"
            '      pattern: "filtered_marker"\n',
            encoding="utf-8",
        )
        controller = ScanController(config_controller, rules_controller)
        initial_count = controller.rulesCount
        # 加载临时规则
        controller.setTaskOverride("temp_rules_paths", (str(temp_file),))
        count_with_temp = controller.rulesCount
        assert count_with_temp > initial_count
        # 禁用该临时规则
        controller.setTaskOverride("disabled_temp_rules_paths", (str(temp_file),))
        # 规则数应回退到初始值（临时规则被过滤）
        assert controller.rulesCount == initial_count
        # ruleset 中不应包含被禁用的临时规则
        assert controller._ruleset is not None
        rule_names = [r.name for r in controller._ruleset.rules]
        assert "临时规则-应被过滤" not in rule_names

    def test_re_enable_temp_rule_restores_ruleset(
        self,
        config_controller: ConfigController,
        rules_controller: RulesController,
        tmp_path: Path,
    ) -> None:
        """启用已禁用的临时规则后 ruleset 应重新包含该规则（iter-140）。"""
        temp_file = tmp_path / "temp.yaml"
        temp_file.write_text(
            'version: "1.0"\n'
            "rules:\n"
            '  - name: "临时规则-可恢复"\n'
            "    severity: warning\n"
            "    match:\n"
            "      type: content\n"
            "      target: content\n"
            "      mode: contains\n"
            '      pattern: "restorable_marker"\n',
            encoding="utf-8",
        )
        controller = ScanController(config_controller, rules_controller)
        controller.setTaskOverride("temp_rules_paths", (str(temp_file),))
        count_with_temp = controller.rulesCount
        # 禁用后再启用
        controller.setTaskOverride("disabled_temp_rules_paths", (str(temp_file),))
        controller.setTaskOverride("disabled_temp_rules_paths", ())
        # 规则数应恢复
        assert controller.rulesCount == count_with_temp
        assert controller._ruleset is not None
        rule_names = [r.name for r in controller._ruleset.rules]
        assert "临时规则-可恢复" in rule_names


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
        controller.setScanModeIndex(1)  # folder 模式
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
        controller.setScanModeIndex(1)
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
        # iter-137：通过全局配置清空规则集（禁用内置 + 无规则文件）
        rules_controller.setUseBuiltin(False)
        assert rules_controller.ruleset is None
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        # 复用/变更文件数 Property（gui_qml 排除时需显式访问覆盖 getter）
        assert controller.reusedFiles == 0
        assert controller.changedFiles == 10  # scanned=10, archive_entries=0

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
        controller.setScanModeIndex(1)
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
            current_file_size=12345,
            current_file_ext="txt",
            current_file_elapsed_ms=500.0,
        )
        stats_instances[0].emit_progress(info)

        assert controller.progressScanned == 5
        assert controller.progressTotal == 10
        assert controller.matchedCount == 2
        assert controller.skippedCount == 1
        assert controller.passedCount == 3  # 5 - 2 - 0
        assert controller.progressIndeterminate is False
        assert "test.txt" in controller.currentFile
        # 单文件进度字段（iter-148 新增；显式访问覆盖 getter）
        assert controller.currentFileSize == 12345
        assert controller.currentFileExt == "txt"
        assert controller.currentFileElapsedMs == 500.0
        # 平均速度：scanned=5 / elapsed=1.0 = 5.0 文件/s
        assert controller.scanSpeed == 5.0
        # 最近解析文件明细：size>0 且有路径时记录一条
        recent = controller.recentParsedFiles
        assert len(recent) == 1
        assert recent[0]["path"] == "/tmp/test.txt"
        assert recent[0]["size"] == 12345
        assert recent[0]["ext"] == "txt"
        assert recent[0]["elapsedMs"] == 500.0

    def test_progress_truncates_long_file_path(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """超长文件路径应被截断显示。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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

    def test_filter_phase_activates_spinner_and_populates_removed(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """filter 阶段：转圈 indeterminate=True，四类剔除计数透传 QML 属性。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        # walk 阶段
        walk_info = ProgressInfo(current_file="/tmp/x.txt", total=50, skipped=10, phase="walk")
        stats_instances[0].emit_progress(walk_info)

        # 切换到 filter 阶段
        filter_info = ProgressInfo(
            current_file="",
            scanned=30,
            total=50,
            phase="filter",
            filter_removed_empty=5,
            filter_removed_oversize=3,
            filter_removed_unreadable=1,
            filter_removed_symlink=2,
        )
        stats_instances[0].emit_progress(filter_info)

        assert controller.scanPhase == "filter"
        assert controller.filterActive is True
        assert controller.progressIndeterminate is True
        assert controller.filterRemovedEmpty == 5
        assert controller.filterRemovedOversize == 3
        assert controller.filterRemovedUnreadable == 1
        assert controller.filterRemovedSymlink == 2

    def test_filter_to_scan_phase_deactivates_spinner(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """filter→scan 切换：filterActive 降为 False，indeterminate 关闭，恢复扫描进度。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        # walk → filter
        stats_instances[0].emit_progress(ProgressInfo(total=50, skipped=10, phase="walk"))
        stats_instances[0].emit_progress(
            ProgressInfo(
                scanned=30,
                total=50,
                phase="filter",
                filter_removed_empty=5,
                filter_removed_oversize=3,
                filter_removed_unreadable=1,
                filter_removed_symlink=2,
            )
        )
        assert controller.filterActive is True

        # filter → scan
        scan_info = ProgressInfo(
            scanned=10,
            total=40,
            matched=2,
            phase="scan",
        )
        stats_instances[0].emit_progress(scan_info)

        assert controller.scanPhase == "scan"
        assert controller.filterActive is False
        assert controller.progressIndeterminate is False
        assert controller.progressScanned == 10
        assert controller.progressTotal == 40

    def test_walk_progress_zero_when_discovered_zero(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """walkDiscovered=0 时 walkProgress 应返回 0（避免除零）。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])

        report = _make_scan_report(results=(), cancelled=True)
        scan_instances[0].emit_cancelled(report)

        assert controller.scanPhase == "done"
        assert controller.scanDone is True

    def test_walk_progress_returns_100_when_walk_done(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """iter-125：walkDone=True 时 walkProgress 固定返回 100，与进度条对应。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        # 模拟 walk 阶段：发现 100，跳过 80（classified=20，占比 20%）
        info = ProgressInfo(current_file="", total=100, skipped=80, phase="walk")
        stats_instances[0].emit_progress(info)
        assert controller.walkDone is False
        assert controller.walkProgress == 20.0  # 进行中按占比
        # walk 完成
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])
        assert controller.walkDone is True
        # 完成后 walkProgress 固定 100，即使 classified < discovered
        assert controller.walkProgress == 100.0

    def test_progress_returns_100_when_scan_done(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """iter-125：scanDone=True 时 progress 固定返回 100，与进度条对应。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])
        # 模拟 scan 阶段：scanned=30, total=40（75%）
        info = ProgressInfo(current_file="/tmp/x", scanned=30, total=40, phase="scan")
        scan_instances[0].emit_progress(info)
        assert controller.scanDone is False
        assert controller.progress == 75.0
        # scan 完成
        report = _make_scan_report(results=())
        scan_instances[0].emit_finished(report)
        assert controller.scanDone is True
        # 完成后 progress 固定 100，即使 scanned < total
        assert controller.progress == 100.0


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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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

    def test_export_results_writes_pdf(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """有命中时应写入 PDF 二进制导出文件（iter-136）。"""
        result = _make_scan_result(tmp_path / "test.txt")
        controller._last_report = _make_scan_report(results=(result,))

        export_path = tmp_path / "export.pdf"
        controller.exportResults("pdf", str(export_path))

        assert export_path.exists()
        # PDF 文件以 %PDF- 魔数开头
        assert export_path.read_bytes()[:5] == b"%PDF-"

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

    def test_open_location_archive_entry_targets_archive(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """iter-133：压缩包内部条目 openLocation 应定位到压缩包文件本身。"""
        archive_path = Path("/tmp/a.zip")
        result = ScanResult(
            path=Path("/tmp/a.zip!inner/file.txt"),
            size=100,
            hits=(RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="匹配"),),
            archive_path=archive_path,
        )
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)

        called: list[Path] = []
        monkeypatch.setattr(
            "fuscan.gui.controllers.scan_controller.open_path_in_explorer",
            called.append,
        )
        controller.openLocation()
        # 应定位到压缩包文件本身，而非内部条目路径
        assert called == [archive_path]

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


class TestIter113BatchReplaceUndo:
    """iter-113：ScanController 批量替换与撤销 Slot 测试。"""

    def _populate_replaceable_results(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> tuple[Path, ...]:
        """构造 2 条可替换的结果填入 resultModel。

        规则集需包含 replace=True 规则，使得 canReplaceAllFiltered 为 True。
        """
        # 注入含 replace 规则的 ruleset 到 controller
        from fuscan.rules.model import (
            LeafMatch,
            MatchMode,
            MatchTarget,
            Rule,
            RuleSet,
        )

        rule = Rule(
            name="可替换规则",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            replace=True,
            replace_with="***",
        )
        controller._ruleset = RuleSet(version="1.0", rules=(rule,))

        # 写入两个真实文件（含 password 关键词）
        src1 = tmp_path / "scan" / "a.txt"
        src1.parent.mkdir(parents=True)
        src1.write_text("password=abc\n", encoding="utf-8")
        src2 = tmp_path / "scan" / "b.txt"
        src2.write_text("password=def\n", encoding="utf-8")

        # 构造 ScanResult
        hit = RuleHit(
            rule_name="可替换规则",
            severity=Severity.WARNING,
            detail="匹配",
            match_texts=("password",),
        )
        results = (
            ScanResult(path=src1, size=src1.stat().st_size, hits=(hit,)),
            ScanResult(path=src2, size=src2.stat().st_size, hits=(hit,)),
        )
        controller._result_model.set_results(results)
        # 设置 last_report.root 以便 resolve_scan_root 计算
        controller._last_report = ScanReport(
            root=tmp_path / "scan",
            results=results,
            stats=ScanStats(),
        )
        return (src1, src2)

    def test_replace_all_filtered_results_success(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """replaceAllFilteredResults：成功批量替换返回聚合消息。"""
        src1, src2 = self._populate_replaceable_results(controller, tmp_path)

        msg = controller.replaceAllFilteredResults()

        assert "成功 2/2" in msg
        # 源文件应被替换
        assert src1.read_text(encoding="utf-8") == "***=abc\n"
        assert src2.read_text(encoding="utf-8") == "***=def\n"
        # canUndoLastBatchReplace 应为 True
        assert controller.canUndoLastBatchReplace is True

    def test_replace_all_filtered_results_no_ruleset(
        self,
        controller: ScanController,
    ) -> None:
        """未加载规则集 → 返回提示消息。"""
        controller._ruleset = None
        msg = controller.replaceAllFilteredResults()
        assert msg == "规则集未加载"

    def test_replace_all_filtered_results_no_results(
        self,
        controller: ScanController,
    ) -> None:
        """无过滤后结果 → 返回提示消息。"""
        # ruleset 非 None 但 result_model 为空
        from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet

        controller._ruleset = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="r",
                    severity=Severity.WARNING,
                    match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="x"),
                ),
            ),
        )
        msg = controller.replaceAllFilteredResults()
        assert msg == "无待替换的结果"

    def test_undo_last_batch_replace_success(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """undoLastBatchReplace：从 .bak 恢复所有文件。"""
        src1, src2 = self._populate_replaceable_results(controller, tmp_path)
        # 先批量替换
        controller.replaceAllFilteredResults()
        assert src1.read_text(encoding="utf-8") == "***=abc\n"
        # 撤销
        msg = controller.undoLastBatchReplace()

        assert "恢复 2" in msg
        # 源文件应恢复为原始内容
        assert src1.read_text(encoding="utf-8") == "password=abc\n"
        assert src2.read_text(encoding="utf-8") == "password=def\n"
        # 撤销记录已清除
        assert controller.canUndoLastBatchReplace is False

    def test_undo_last_batch_replace_no_record(
        self,
        controller: ScanController,
    ) -> None:
        """无可撤销记录 → 返回提示消息。"""
        msg = controller.undoLastBatchReplace()
        assert msg == "无可撤销的批量替换"

    def test_undo_last_batch_replace_partial_failure(
        self,
        controller: ScanController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """撤销时备份文件丢失 → 部分失败消息。"""
        self._populate_replaceable_results(controller, tmp_path)
        controller.replaceAllFilteredResults()
        # 删除其中一个备份文件，模拟撤销失败
        # 找到第一个备份并删除
        for _src_path, backup_path in controller._last_batch_backup_paths:
            backup_path.unlink()
            break
        msg = controller.undoLastBatchReplace()

        assert "恢复 1" in msg
        assert "1 个失败" in msg
        # 撤销记录已清除
        assert controller.canUndoLastBatchReplace is False

    def test_undo_selected_replace_no_selection(
        self,
        controller: ScanController,
    ) -> None:
        """未选中结果 → 返回提示消息。"""
        msg = controller.undoSelectedReplace()
        assert msg == "未选中结果"

    def test_undo_selected_replace_success(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """undoSelectedReplace：成功从 .bak 恢复当前选中结果。"""
        src1, _src2 = self._populate_replaceable_results(controller, tmp_path)
        # 先单文件替换（构造备份）
        controller.setSelectedResultIndex(0)
        controller.replaceSelectedResult()
        assert src1.read_text(encoding="utf-8") == "***=abc\n"
        # 撤销当前选中
        msg = controller.undoSelectedReplace()

        assert msg.startswith("已从备份恢复")
        # 源文件应恢复为原始内容
        assert src1.read_text(encoding="utf-8") == "password=abc\n"

    def test_can_replace_all_filtered_false_when_no_ruleset(
        self,
        controller: ScanController,
    ) -> None:
        """无规则集 → canReplaceAllFiltered=False。"""
        controller._ruleset = None
        assert controller.canReplaceAllFiltered is False

    def test_can_replace_all_filtered_false_when_no_results(
        self,
        controller: ScanController,
    ) -> None:
        """有规则集但无结果 → canReplaceAllFiltered=False。"""
        from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet

        controller._ruleset = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="r",
                    severity=Severity.WARNING,
                    match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="x"),
                ),
            ),
        )
        assert controller.canReplaceAllFiltered is False

    def test_can_replace_all_filtered_true(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """有规则集 + 可替换结果 → canReplaceAllFiltered=True。"""
        self._populate_replaceable_results(controller, tmp_path)
        assert controller.canReplaceAllFiltered is True

    def test_can_undo_last_batch_replace_default_false(
        self,
        controller: ScanController,
    ) -> None:
        """初始状态 canUndoLastBatchReplace=False。"""
        assert controller.canUndoLastBatchReplace is False


class TestIter124CustomReplaceWith:
    """iter-124：ScanController 自定义替换文本 replace_with 参数测试。"""

    def _populate_no_replace_results(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> Path:
        """构造命中规则无 replace=True 的结果（验证 override 模式不要求 replace=True）。"""
        # 注入无 replace 标志的规则集
        from fuscan.rules.model import (
            LeafMatch,
            MatchMode,
            MatchTarget,
            Rule,
            RuleSet,
        )

        rule = Rule(
            name="只检测规则",
            severity=Severity.CRITICAL,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            # replace 默认 False
        )
        controller._ruleset = RuleSet(version="1.0", rules=(rule,))

        # 写入真实文件（含 password 关键词）
        src = tmp_path / "scan" / "secret.txt"
        src.parent.mkdir(parents=True)
        src.write_text("password=sensitive\n", encoding="utf-8")

        # 构造 ScanResult（命中含 match_texts）
        hit = RuleHit(
            rule_name="只检测规则",
            severity=Severity.CRITICAL,
            detail="匹配",
            match_texts=("password",),
        )
        results = (ScanResult(path=src, size=src.stat().st_size, hits=(hit,)),)
        controller._result_model.set_results(results)
        controller._last_report = ScanReport(
            root=tmp_path / "scan",
            results=results,
            stats=ScanStats(),
        )
        return src

    def test_replace_selected_with_custom_text(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """replaceSelectedResult(replace_with) 用自定义文本替换，不要求规则 replace=True。"""
        src = self._populate_no_replace_results(controller, tmp_path)
        controller.setSelectedResultIndex(0)

        # 用自定义文本 [REDACTED] 替换
        msg = controller.replaceSelectedResult("[REDACTED]")

        assert "替换成功" in msg
        assert src.read_text(encoding="utf-8") == "[REDACTED]=sensitive\n"

    def test_replace_selected_with_ellipsis_default(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """replaceSelectedResult("...") 用默认省略号替换命中内容。"""
        src = self._populate_no_replace_results(controller, tmp_path)
        controller.setSelectedResultIndex(0)

        msg = controller.replaceSelectedResult("...")

        assert "替换成功" in msg
        assert src.read_text(encoding="utf-8") == "...=sensitive\n"

    def test_replace_selected_empty_string_uses_rule_driven(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """replaceSelectedResult("") 走规则驱动模式（无 replace=True 规则 → 提示消息）。"""
        self._populate_no_replace_results(controller, tmp_path)
        controller.setSelectedResultIndex(0)

        # 空字符串走规则驱动模式，规则无 replace=True → 返回 NO_REPLACE_RULES 消息
        msg = controller.replaceSelectedResult("")

        assert "未启用替换" in msg or "无匹配文本" in msg

    def test_replace_all_filtered_with_custom_text(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """replaceAllFilteredResults(replace_with) 用自定义文本批量替换。"""
        src = self._populate_no_replace_results(controller, tmp_path)
        # 再加一个文件
        src2 = tmp_path / "scan" / "another.txt"
        src2.write_text("password=another\n", encoding="utf-8")
        hit = RuleHit(
            rule_name="只检测规则",
            severity=Severity.CRITICAL,
            detail="匹配",
            match_texts=("password",),
        )
        results = (
            ScanResult(path=src, size=src.stat().st_size, hits=(hit,)),
            ScanResult(path=src2, size=src2.stat().st_size, hits=(hit,)),
        )
        controller._result_model.set_results(results)
        controller._last_report = ScanReport(
            root=tmp_path / "scan",
            results=results,
            stats=ScanStats(),
        )

        msg = controller.replaceAllFilteredResults("***")

        assert "成功 2/2" in msg
        assert src.read_text(encoding="utf-8") == "***=sensitive\n"
        assert src2.read_text(encoding="utf-8") == "***=another\n"
        # 撤销记录应可用
        assert controller.canUndoLastBatchReplace is True

    def test_replace_all_filtered_no_ruleset_with_override_succeeds(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """replaceAllFilteredResults(override) 在 ruleset=None 时仍可执行（override 模式不依赖规则集）。"""
        # 不注入任何规则集
        controller._ruleset = None

        # 写入文件与命中
        src = tmp_path / "scan" / "a.txt"
        src.parent.mkdir(parents=True)
        src.write_text("password=abc\n", encoding="utf-8")
        hit = RuleHit(
            rule_name="任意规则",
            severity=Severity.CRITICAL,
            detail="匹配",
            match_texts=("password",),
        )
        results = (ScanResult(path=src, size=src.stat().st_size, hits=(hit,)),)
        controller._result_model.set_results(results)
        controller._last_report = ScanReport(
            root=tmp_path / "scan",
            results=results,
            stats=ScanStats(),
        )

        msg = controller.replaceAllFilteredResults("...")

        assert "成功 1/1" in msg
        assert src.read_text(encoding="utf-8") == "...=abc\n"

    def test_can_replace_selected_with_match_texts_no_ruleset(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """canReplaceSelected：规则集为 None 但命中含 match_texts → True（用户自定义模式）。"""
        controller._ruleset = None
        # 写入文件
        src = tmp_path / "scan" / "a.txt"
        src.parent.mkdir(parents=True)
        src.write_text("password=abc\n", encoding="utf-8")
        hit = RuleHit(
            rule_name="任意规则",
            severity=Severity.CRITICAL,
            detail="匹配",
            match_texts=("password",),
        )
        results = (ScanResult(path=src, size=src.stat().st_size, hits=(hit,)),)
        controller._result_model.set_results(results)
        controller.setSelectedResultIndex(0)

        assert controller.canReplaceSelected is True


class TestIter220AutoReplaceHits:
    """iter-220：扫描完成后 ScanController._auto_replace_hits 自动替换流程。"""

    def _setup_replaceable_ruleset(self, controller: ScanController) -> Rule:
        """注入含 replace=True 规则的 ruleset 到 controller，返回规则实例。"""
        rule = Rule(
            name="可替换规则",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            replace=True,
            replace_with="***",
        )
        controller._ruleset = RuleSet(version="1.0", rules=(rule,))
        return rule

    def _make_hit(self) -> RuleHit:
        return RuleHit(
            rule_name="可替换规则",
            severity=Severity.WARNING,
            detail="匹配",
            match_texts=("password",),
        )

    def test_empty_hits_returns_empty(self, controller: ScanController) -> None:
        """空结果元组直接返回，配对为空。"""
        new_hits, pairs = controller._auto_replace_hits((), Path("/tmp"))
        assert new_hits == ()
        assert pairs == ()

    def test_no_ruleset_returns_original(self, controller: ScanController, tmp_path: Path) -> None:
        """ruleset 为 None 时返回原元组，不执行替换。"""
        controller._ruleset = None
        src = tmp_path / "a.txt"
        src.write_text("password=abc\n", encoding="utf-8")
        sr = ScanResult(path=src, size=src.stat().st_size, hits=(self._make_hit(),))
        new_hits, pairs = controller._auto_replace_hits((sr,), tmp_path)
        assert new_hits == (sr,)
        assert pairs == ()
        # 文件未被修改
        assert src.read_text(encoding="utf-8") == "password=abc\n"

    def test_replace_success_marks_replaced(self, controller: ScanController, tmp_path: Path) -> None:
        """含 replace=True 规则且替换成功：新 ScanResult 标记 replaced=True。"""
        self._setup_replaceable_ruleset(controller)
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        src = scan_root / "a.txt"
        src.write_text("password=abc\n", encoding="utf-8")
        sr = ScanResult(path=src, size=src.stat().st_size, hits=(self._make_hit(),))
        new_hits, pairs = controller._auto_replace_hits((sr,), scan_root)
        assert len(new_hits) == 1
        assert new_hits[0].replaced is True
        assert new_hits[0].replaced_count >= 1
        # 配对非空：包含 (src, backup_path)
        assert len(pairs) == 1
        assert pairs[0][0] == src
        # 源文件应已被替换
        assert "password" not in src.read_text(encoding="utf-8")

    def test_no_replace_rule_preserved(self, controller: ScanController, tmp_path: Path) -> None:
        """命中规则不含 replace=True：保留原 ScanResult（replaced=False）。"""
        # ruleset 中规则 replace=False
        rule = Rule(
            name="只检测规则",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            replace=False,
            replace_with="***",
        )
        controller._ruleset = RuleSet(version="1.0", rules=(rule,))
        src = tmp_path / "a.txt"
        src.write_text("password=abc\n", encoding="utf-8")
        hit = RuleHit(
            rule_name="只检测规则",
            severity=Severity.WARNING,
            detail="匹配",
            match_texts=("password",),
        )
        sr = ScanResult(path=src, size=src.stat().st_size, hits=(hit,))
        new_hits, pairs = controller._auto_replace_hits((sr,), tmp_path)
        assert new_hits[0].replaced is False
        assert pairs == ()
        # 文件未被修改
        assert src.read_text(encoding="utf-8") == "password=abc\n"

    def test_archive_entry_skipped(self, controller: ScanController, tmp_path: Path) -> None:
        """压缩包内部条目跳过自动替换。"""
        self._setup_replaceable_ruleset(controller)
        archive = tmp_path / "bundle.zip"
        archive.write_bytes(b"fake zip")
        sr = ScanResult(
            path=tmp_path / "bundle.zip!inner.txt",
            size=10,
            hits=(self._make_hit(),),
            archive_path=archive,
        )
        new_hits, pairs = controller._auto_replace_hits((sr,), tmp_path)
        assert new_hits[0].replaced is False
        assert pairs == ()

    def test_mixed_results_partial_replaced(self, controller: ScanController, tmp_path: Path) -> None:
        """混合结果：部分可替换 + 部分无可替换规则 → 仅前者标记 replaced。"""
        self._setup_replaceable_ruleset(controller)
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        # 可替换文件
        src1 = scan_root / "a.txt"
        src1.write_text("password=abc\n", encoding="utf-8")
        sr1 = ScanResult(path=src1, size=src1.stat().st_size, hits=(self._make_hit(),))
        # 不可替换文件（规则集中无对应规则）
        src2 = scan_root / "b.txt"
        src2.write_text("secret=xyz\n", encoding="utf-8")
        hit2 = RuleHit(
            rule_name="未注册规则",
            severity=Severity.WARNING,
            detail="匹配",
            match_texts=("secret",),
        )
        sr2 = ScanResult(path=src2, size=src2.stat().st_size, hits=(hit2,))
        new_hits, pairs = controller._auto_replace_hits((sr1, sr2), scan_root)
        assert len(new_hits) == 2
        assert new_hits[0].replaced is True
        assert new_hits[1].replaced is False
        assert len(pairs) == 1
        assert pairs[0][0] == src1


class TestIter220SetResultFilterReplaced:
    """iter-220：ScanController.setResultFilterReplaced 槽函数覆盖。"""

    def _build_mixed_results(self, tmp_path: Path) -> tuple[ScanResult, ...]:
        """构造 2 条已替换 + 2 条未替换的结果元组。"""
        h_critical = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="d1")
        h_warning = RuleHit(rule_name="API 密钥", severity=Severity.WARNING, detail="d2")
        return (
            ScanResult(
                path=tmp_path / "a.txt",
                size=10,
                hits=(h_critical,),
                errors=0,
                replaced=True,
                replaced_count=1,
            ),
            ScanResult(path=tmp_path / "b.txt", size=20, hits=(h_warning,), errors=0),
            ScanResult(
                path=tmp_path / "c.txt",
                size=30,
                hits=(h_critical, h_warning),
                errors=0,
                replaced=True,
                replaced_count=2,
            ),
            ScanResult(path=tmp_path / "d.txt", size=40, hits=(h_warning,), errors=0),
        )

    def test_filter_pending_reduces_count(self, controller: ScanController, tmp_path: Path) -> None:
        """value=1（仅未替换）过滤后结果数减少为 2。"""
        controller._result_model.set_results(self._build_mixed_results(tmp_path))
        assert controller._result_model.filtered_count == 4
        controller.setResultFilterReplaced(1)
        assert controller._result_model.filtered_count == 2
        for r in controller._result_model.filtered_results:
            assert r.replaced is False

    def test_filter_replaced_only(self, controller: ScanController, tmp_path: Path) -> None:
        """value=2（仅已替换）过滤后结果数为 2。"""
        controller._result_model.set_results(self._build_mixed_results(tmp_path))
        controller.setResultFilterReplaced(2)
        assert controller._result_model.filtered_count == 2
        for r in controller._result_model.filtered_results:
            assert r.replaced is True

    def test_filter_all_clears(self, controller: ScanController, tmp_path: Path) -> None:
        """value=0（全部）清除过滤维度，结果数恢复为 4。"""
        controller._result_model.set_results(self._build_mixed_results(tmp_path))
        controller.setResultFilterReplaced(2)
        assert controller._result_model.filtered_count == 2
        controller.setResultFilterReplaced(0)
        assert controller._result_model.filtered_count == 4

    def test_filter_resets_out_of_range_selection(self, controller: ScanController, tmp_path: Path) -> None:
        """过滤后选中索引越界时重置为 -1。"""
        controller._result_model.set_results(self._build_mixed_results(tmp_path))
        controller.setSelectedResultIndex(3)  # 选中第 4 行
        # 过滤为仅未替换（2 条），索引 3 越界 → 重置为 -1
        controller.setResultFilterReplaced(1)
        assert controller._selected_result_index == -1

    def test_filter_preserves_valid_selection(self, controller: ScanController, tmp_path: Path) -> None:
        """过滤后选中索引仍有效时保留。"""
        controller._result_model.set_results(self._build_mixed_results(tmp_path))
        controller.setSelectedResultIndex(0)
        controller.setResultFilterReplaced(1)
        # 过滤后仍有 2 条，索引 0 有效 → 保留
        assert controller._selected_result_index == 0


class TestBuildScanRoots:
    """测试 _build_scan_roots 构建扫描根路径。"""

    def test_build_roots_folder_mode(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """folder 模式应返回 folder_root 列表。"""
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        roots = controller._build_scan_roots()
        assert roots == [tmp_path]

    def test_build_roots_drive_mode(self, controller: ScanController) -> None:
        """drive 模式应返回选中盘符列表。"""
        controller.setScanModeIndex(0)
        controller.setSelectedDrive("C:")
        roots = controller._build_scan_roots()
        assert roots == [Path("C:")]

    def test_build_roots_drive_mode_no_selection(self, controller: ScanController) -> None:
        """drive 模式无选中盘符应返回空列表。"""
        controller.setScanModeIndex(0)
        controller.setSelectedDrive("")
        roots = controller._build_scan_roots()
        assert roots == []

    def test_build_roots_folder_mode_empty(self, controller: ScanController) -> None:
        """folder 模式空路径应返回空列表。"""
        controller.setScanModeIndex(1)
        controller.setFolderRoot("")
        roots = controller._build_scan_roots()
        assert roots == []


class TestBuildCacheContext:
    """测试 _build_cache_context 构造缓存上下文。"""

    def test_build_cache_context_disabled(self, controller: ScanController) -> None:
        """cache_enabled=False 时应返回 (None, None)。"""
        controller._ruleset = RuleSet(version="1.0", scan_params=ScanParams(cache_enabled=False))
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
        controller._ruleset = RuleSet(version="1.0", scan_params=ScanParams(cache_enabled=True))
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
        controller._ruleset = RuleSet(version="1.0", scan_params=ScanParams(cache_enabled=True))
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
        controller.setScanModeIndex(1)
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
        controller.setScanModeIndex(1)
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

    def test_quick_cancel_closes_cache_async(
        self,
        controller: ScanController,
    ) -> None:
        """iter-147：quick_cancel 异步关闭 cache（消除 quick_cancel/cleanup 不一致）。

        原 quick_cancel 不关 cache（注释说"进程退出由 OS 回收"），但
        workspace_controller.cleanup 用 quick_cancel 而非 cleanup，导致
        cache.close() 永不被调用，WAL 文件无限膨胀（iter-145 cache.db 15.7GB）。
        修复后 quick_cancel 末尾启动 daemon thread 异步关闭 cache。
        """
        import threading

        closed_event = threading.Event()

        class FakeCacheStore:
            def close(self) -> None:
                closed_event.set()

        cache = FakeCacheStore()
        controller._cache = cache  # type: ignore[bad-assignment]
        controller.quick_cancel()
        # _cache 立即设为 None（同步），避免重复关闭
        assert controller._cache is None
        # daemon thread 异步关闭 cache，等待最多 2s
        assert closed_event.wait(timeout=2.0), "cache.close() 未在 daemon thread 中被调用"

    def test_quick_cancel_no_cache_noop(self, controller: ScanController) -> None:
        """iter-147：quick_cancel 在 _cache 为 None 时不抛异常。"""
        controller._cache = None
        controller.quick_cancel()
        assert controller._cache is None

    def test_quick_cancel_sets_worker_none(
        self,
        controller: ScanController,
    ) -> None:
        """iter-147：quick_cancel 后 _worker/_stats_worker 设为 None（消除残留）。"""

        class StubbornWorker:
            def __init__(self) -> None:
                self.terminated = False

            def cancel(self) -> None:
                pass

            def wait(self, _msecs: int = 0) -> bool:
                return False

            def terminate(self) -> None:
                self.terminated = True

            def isRunning(self) -> bool:
                return True

            def deleteLater(self) -> None:
                pass

        worker = StubbornWorker()
        stats = StubbornWorker()
        controller._worker = worker  # type: ignore[bad-assignment]
        controller._stats_worker = stats  # type: ignore[bad-assignment]
        controller.quick_cancel()
        assert controller._worker is None
        assert controller._stats_worker is None
        assert worker.terminated is True
        assert stats.terminated is True


class TestIter143CoverageGaps:
    """iter-143：补充 scan_controller.py 未覆盖分支。

    覆盖目标：Property getters（restoring/archiveEntryCount/effectiveMax*）、
    filterSeverities/setResultSort 选中索引越界、moveSelectedToStaging 同步
    last_report、markAsFalsePositive 三分支、_on_scan_progress phase 切换、
    _on_stats_finished stats_worker is None、_on_scan_finished speed > 0、
    _set_restoring noop、_set_status 无 summary、quick_cancel 各分支。
    """

    def test_restoring_property_default_false(self, controller: ScanController) -> None:
        """restoring Property 默认 False（iter-143 覆盖行 271）。"""
        assert controller.restoring is False

    def test_archive_entry_count_default_zero(self, controller: ScanController) -> None:
        """archiveEntryCount Property 默认 0（iter-143 覆盖行 377）。"""
        assert controller.archiveEntryCount == 0

    def test_effective_max_workers_property(self, controller: ScanController) -> None:
        """effectiveMaxWorkers Property 返回 config 值（iter-143 覆盖行 575）。"""
        assert controller.effectiveMaxWorkers == controller._effective_max_workers()

    def test_effective_max_file_size_mb_property(self, controller: ScanController) -> None:
        """effectiveMaxFileSizeMB Property 返回 MB 单位值（iter-143 覆盖行 583）。"""
        expected = controller._effective_max_file_size() // (1024 * 1024)
        assert controller.effectiveMaxFileSizeMB == expected

    def test_effective_max_depth_property_default_zero(self, controller: ScanController) -> None:
        """effectiveMaxDepth 默认 None 归一化为 0（iter-143 覆盖行 592-593）。"""
        # Config 默认 max_depth=0（无限），effective_max_depth 返回 None，Property 归一化为 0
        assert controller.effectiveMaxDepth == 0

    def test_effective_max_depth_property_with_value(
        self,
        controller: ScanController,
    ) -> None:
        """effectiveMaxDepth 设非零值时返回该值（iter-143 覆盖行 592-593 depth or 0 分支）。"""
        # max_depth 已迁移到 RuleSet.scan_params，从规则集读取
        controller._ruleset = RuleSet(version="1.0", scan_params=ScanParams(max_depth=5))
        assert controller.effectiveMaxDepth == 5

    def test_filter_severities_resets_selected_index_when_out_of_range(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """filterSeverities 过滤后选中索引越界应重置为 -1（iter-143 覆盖行 734）。"""
        h_critical = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="d1")
        h_warning = RuleHit(rule_name="API 密钥", severity=Severity.WARNING, detail="d2")
        results = (
            ScanResult(path=tmp_path / "a.txt", size=10, hits=(h_critical,)),
            ScanResult(path=tmp_path / "b.txt", size=20, hits=(h_warning,)),
        )
        controller._result_model.set_results(results)
        controller.setSelectedResultIndex(1)  # 选中第 2 条（WARNING）
        # 过滤仅保留 CRITICAL，第 2 条被过滤，选中索引越界
        controller.setResultFilterSeverities(["严重"])
        assert controller.selectedResultIndex == -1

    def test_set_result_sort_resets_selected_index_when_out_of_range(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """setResultSort 后选中索引越界应重置为 -1（iter-143 覆盖行 746）。"""
        results = (
            ScanResult(
                path=tmp_path / "a.txt",
                size=10,
                hits=(RuleHit(rule_name="r", severity=Severity.CRITICAL, detail="d"),),
            ),
            ScanResult(
                path=tmp_path / "b.txt",
                size=20,
                hits=(RuleHit(rule_name="r", severity=Severity.CRITICAL, detail="d"),),
            ),
        )
        controller._result_model.set_results(results)
        controller.setSelectedResultIndex(1)
        # 过滤后仅 1 条，排序时选中索引 1 越界
        controller.setResultFilterText("a.txt")
        controller.setResultSort("filePath", True)
        assert controller.selectedResultIndex == -1

    def test_move_to_staging_syncs_last_report(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """moveSelectedToStaging 成功后 _last_report.hits 同步移除该条目（iter-143 覆盖 926->937）。"""
        result = _make_scan_result(tmp_path / "test.txt")
        controller._result_model.set_results((result,))
        controller._last_report = _make_scan_report(results=(result,))
        controller.setSelectedResultIndex(0)

        # mock move_to_staging 返回成功前缀
        monkeypatch.setattr(
            "fuscan.gui.controllers.scan_controller.move_to_staging",
            lambda **kwargs: f"已移至暂存: {tmp_path}/quarantine/test.txt",
        )

        msg = controller.moveSelectedToStaging()
        assert msg.startswith("已移至暂存")
        # _last_report.hits 中已移除该条目
        assert all(str(h.path) != str(result.path) for h in controller._last_report.hits)
        # 选中索引重置
        assert controller.selectedResultIndex == -1

    def test_move_to_staging_failure_skips_sync(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """moveSelectedToStaging 失败时不修改 _last_report（iter-143 覆盖 924->939）。"""
        result = _make_scan_result(tmp_path / "test.txt")
        controller._result_model.set_results((result,))
        controller._last_report = _make_scan_report(results=(result,))
        controller.setSelectedResultIndex(0)

        # mock move_to_staging 返回失败消息
        monkeypatch.setattr(
            "fuscan.gui.controllers.scan_controller.move_to_staging",
            lambda **kwargs: "移至暂存失败: 模拟错误",
        )

        msg = controller.moveSelectedToStaging()
        assert msg.startswith("移至暂存失败")
        # _last_report.hits 未修改
        assert len(controller._last_report.hits) == 1
        # 选中索引未重置
        assert controller.selectedResultIndex == 0

    def test_move_to_staging_no_last_report_skips_sync(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """moveSelectedToStaging 成功但 _last_report is None 时跳过同步（iter-143 覆盖 926->937 False 分支）。"""
        result = _make_scan_result(tmp_path / "test.txt")
        controller._result_model.set_results((result,))
        controller._last_report = None
        controller.setSelectedResultIndex(0)

        # mock remove_result_by_path 返回 False（removed=False 走 if False 分支）
        monkeypatch.setattr(
            "fuscan.gui.controllers.scan_controller.move_to_staging",
            lambda **kwargs: f"已移至暂存: {tmp_path}/quarantine/test.txt",
        )
        # 让 remove_result_by_path 返回 False 触发 926 if False 分支
        monkeypatch.setattr(controller._result_model, "remove_result_by_path", lambda _path: False)

        msg = controller.moveSelectedToStaging()
        assert msg.startswith("已移至暂存")
        # _last_report 仍为 None
        assert controller._last_report is None
        # 选中索引仍重置（937 行在 if 块外）
        assert controller.selectedResultIndex == -1

    def test_mark_as_false_positive_no_selection(self, controller: ScanController) -> None:
        """markAsFalsePositive 未选中结果返回 '未选中结果'（iter-143 覆盖 961-966）。"""
        msg = controller.markAsFalsePositive()
        assert msg == "未选中结果"

    def test_mark_as_false_positive_archive_entry(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """markAsFalsePositive 压缩包内部条目返回错误（iter-143 覆盖 961-966）。"""
        archive_path = tmp_path / "a.zip"
        result = ScanResult(
            path=tmp_path / "a.zip!inner/file.txt",
            size=100,
            hits=(RuleHit(rule_name="r", severity=Severity.CRITICAL, detail="d"),),
            archive_path=archive_path,
        )
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)

        msg = controller.markAsFalsePositive()
        assert msg == "压缩包内部条目不支持标记误报"

    def test_mark_as_false_positive_success_with_pending_ws(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """markAsFalsePositive 成功且 _pending_ws_id 设置时调用 invalidate_manifest（iter-143 覆盖 967-971）。"""
        result = _make_scan_result(tmp_path / "test.txt")
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)
        controller._pending_ws_id = "ws-test"

        # mock addEntry 返回成功消息
        monkeypatch.setattr(
            controller._whitelist_controller,
            "addEntry",
            lambda path_glob, rule_name, note: f"已标记为误报: {path_glob} ({rule_name})",
        )
        # mock invalidate_manifest 捕获调用
        invalidated: list[str] = []
        monkeypatch.setattr(controller, "invalidate_manifest", invalidated.append)

        msg = controller.markAsFalsePositive(rule_filter="敏感内容")
        assert msg.startswith("已标记为误报")
        assert invalidated == ["ws-test"]

    def test_mark_as_false_positive_success_without_pending_ws(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """markAsFalsePositive 成功但 _pending_ws_id 为空时不调用 invalidate_manifest（iter-143 覆盖 969->971）。"""
        result = _make_scan_result(tmp_path / "test.txt")
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)
        # _pending_ws_id 为空字符串（falsy）
        controller._pending_ws_id = ""

        monkeypatch.setattr(
            controller._whitelist_controller,
            "addEntry",
            lambda path_glob, rule_name, note: f"已标记为误报: {path_glob} ({rule_name})",
        )
        invalidated: list[str] = []
        monkeypatch.setattr(controller, "invalidate_manifest", invalidated.append)

        msg = controller.markAsFalsePositive()
        assert msg.startswith("已标记为误报")
        # _pending_ws_id 为空，不调用 invalidate_manifest
        assert invalidated == []

    def test_on_scan_progress_phase_switch_to_scan(
        self,
        controller: ScanController,
    ) -> None:
        """_on_scan_progress phase 从 walk 切到 scan 应标记 walk_done（iter-143 覆盖 1236->1240）。"""
        # 初始 phase=setup，先发 walk 进度
        walk_info = ProgressInfo(phase="walk", total=10, scanned=0)
        controller._on_scan_progress(walk_info)
        assert controller._walk_done is False
        # 切到 scan 阶段
        scan_info = ProgressInfo(phase="scan", total=10, scanned=5, matched=1)
        controller._on_scan_progress(scan_info)
        assert controller._walk_done is True
        assert controller._walk_indeterminate is False

    def test_scan_speed_zero_when_no_elapsed(self, controller: ScanController) -> None:
        """scanSpeed 在 elapsed<=0 时返回 0.0（避免除零）。"""
        assert controller.scanSpeed == 0.0
        # elapsed=0 的 scan 进度不应触发除零
        controller._on_scan_progress(ProgressInfo(phase="scan", scanned=5, total=10, elapsed=0.0))
        assert controller.scanSpeed == 0.0

    def test_recent_parsed_files_skips_zero_size(self, controller: ScanController) -> None:
        """current_file_size<=0 的回调不应记入解析明细（避免 walk/archive 汇总污染）。"""
        # 无文件大小：不记录
        controller._on_scan_progress(
            ProgressInfo(phase="scan", scanned=1, total=1, current_file="/a.txt", current_file_size=0)
        )
        assert controller.recentParsedFiles == []
        # 有文件大小：记录一条
        controller._on_scan_progress(
            ProgressInfo(phase="scan", scanned=2, total=2, current_file="/b.txt", current_file_size=100)
        )
        assert len(controller.recentParsedFiles) == 1

    def test_recent_parsed_files_newest_first(self, controller: ScanController) -> None:
        """recentParsedFiles 最新解析的文件排在列表首位。"""
        for i in range(3):
            controller._on_scan_progress(
                ProgressInfo(
                    phase="scan",
                    scanned=i + 1,
                    total=3,
                    current_file=f"/f{i}.txt",
                    current_file_size=10,
                )
            )
        recent = controller.recentParsedFiles
        assert [item["path"] for item in recent] == ["/f2.txt", "/f1.txt", "/f0.txt"]

    def test_recent_parsed_files_maxlen(self, controller: ScanController) -> None:
        """解析明细超出上限时 deque 自动丢弃最旧条目。"""
        from fuscan.gui.controllers.scan_controller import _RECENT_FILES_MAX

        for i in range(_RECENT_FILES_MAX + 10):
            controller._on_scan_progress(
                ProgressInfo(
                    phase="scan",
                    scanned=i + 1,
                    total=_RECENT_FILES_MAX + 10,
                    current_file=f"/f{i}.txt",
                    current_file_size=10,
                )
            )
        assert len(controller.recentParsedFiles) == _RECENT_FILES_MAX

    def test_recent_parsed_files_preformatted_fields(self, controller: ScanController) -> None:
        """recentParsedFiles 每项含后端预格式化的 name/sizeText/elapsedText/engine 字段。"""
        controller._on_scan_progress(
            ProgressInfo(
                phase="scan",
                scanned=1,
                total=1,
                current_file="C:\\dir\\report.pdf",
                current_file_size=2048,
                current_file_ext="pdf",
                current_file_elapsed_ms=1500.0,
                current_file_engine="pypdfium2",
            )
        )
        item = controller.recentParsedFiles[0]
        # name 取路径末段（兼容反斜杠）
        assert item["name"] == "report.pdf"
        assert item["path"] == "C:\\dir\\report.pdf"
        assert item["size"] == 2048
        assert item["ext"] == "pdf"
        assert item["elapsedMs"] == 1500.0
        # engine 透传 ProgressInfo.current_file_engine，供明细行标注
        assert item["engine"] == "pypdfium2"
        # sizeText 复用 format_size，elapsedText 复用 format_elapsed(elapsedMs/1000)
        assert item["sizeText"] == "2.0 KB"
        assert item["elapsedText"] == "1.5s"

    def test_recent_parsed_files_name_posix_path(self, controller: ScanController) -> None:
        """recentParsedFiles name 兼容正斜杠路径末段提取。"""
        controller._on_scan_progress(
            ProgressInfo(
                phase="scan",
                scanned=1,
                total=1,
                current_file="/home/user/a.txt",
                current_file_size=100,
            )
        )
        assert controller.recentParsedFiles[0]["name"] == "a.txt"

    def test_recent_same_file_updates_not_duplicates(self, controller: ScanController) -> None:
        """同一文件多次进度回调应更新末条而非反复新增条目。

        大文件解析期间 150ms 节流会多次回调同一 current_file，原实现每次 append
        导致同一文件在明细列表中反复出现。改为按路径去重：末条路径相同时就地
        更新 elapsedMs/size/engine，status 保持 "scanning"。
        """
        path = "/big/report.pdf"
        for ms in (100.0, 300.0, 600.0):
            controller._on_scan_progress(
                ProgressInfo(
                    phase="scan",
                    scanned=1,
                    total=1,
                    current_file=path,
                    current_file_size=5_000_000,
                    current_file_ext="pdf",
                    current_file_elapsed_ms=ms,
                    current_file_engine="pypdfium2",
                )
            )
        recent = controller.recentParsedFiles
        # 仅一条条目（不重复）
        assert len(recent) == 1
        assert recent[0]["path"] == path
        # elapsedMs 为最后一次回调的值（实时增长）
        assert recent[0]["elapsedMs"] == 600.0

    def test_recent_status_scanning_for_current_file(self, controller: ScanController) -> None:
        """当前正在解析的文件 status 为 "scanning"（QML 据此显示转圈）。"""
        controller._on_scan_progress(
            ProgressInfo(
                phase="scan",
                scanned=1,
                total=1,
                current_file="/a.txt",
                current_file_size=100,
            )
        )
        assert controller.recentParsedFiles[0]["status"] == "scanning"

    def test_recent_status_done_when_new_file_arrives(self, controller: ScanController) -> None:
        """新文件到来时前一条从 "scanning" 切换为 "done"（QML 据此显示勾选）。"""
        controller._on_scan_progress(
            ProgressInfo(phase="scan", scanned=1, total=2, current_file="/a.txt", current_file_size=100)
        )
        controller._on_scan_progress(
            ProgressInfo(phase="scan", scanned=2, total=2, current_file="/b.txt", current_file_size=200)
        )
        recent = controller.recentParsedFiles
        # 最新在前：b（scanning）→ a（done）
        assert recent[0]["path"] == "/b.txt"
        assert recent[0]["status"] == "scanning"
        assert recent[1]["path"] == "/a.txt"
        assert recent[1]["status"] == "done"

    def test_recent_finalize_marks_last_done(self, controller: ScanController) -> None:
        """_finalize_last_recent_file 将末条 scanning 标记为 done（扫描完成/暂停/取消时调用）。"""
        controller._on_scan_progress(
            ProgressInfo(phase="scan", scanned=1, total=1, current_file="/a.txt", current_file_size=100)
        )
        assert controller.recentParsedFiles[0]["status"] == "scanning"
        controller._finalize_last_recent_file()
        assert controller.recentParsedFiles[0]["status"] == "done"
        # 无条目或已 done 时不重复操作
        controller._finalize_last_recent_file()
        assert controller.recentParsedFiles[0]["status"] == "done"

    def test_recent_emit_throttled_below_thresholds(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """未达时间/批量阈值时高频进度回调不 emit recentParsedFilesChanged。

        进度条/计数走 scanProgressChanged 照常刷新，明细列表低频节流避免全表重建。
        """
        import fuscan.gui.controllers.scan_controller as sc_mod

        # 冻结时钟：所有回调发生在同一瞬间（时间阈值不满足），
        # 且回调数少于批量阈值，则明细刷新应被完全节流掉。
        monkeypatch.setattr(sc_mod.time, "perf_counter", lambda: 100.0)
        recent_emits: list[None] = []
        controller.recentParsedFilesChanged.connect(lambda: recent_emits.append(None))  # pyrefly: ignore [missing-attribute]
        # 预置 last=当前冻结时刻，避免初值 -inf 导致首个回调即满足时间阈值。
        controller._recent_emit_last = 100.0
        # 同一时刻且 pending<批量阈值 → 不 emit
        n = sc_mod._RECENT_EMIT_BATCH - 1
        for i in range(n):
            controller._on_scan_progress(
                ProgressInfo(phase="scan", scanned=i + 1, total=n, current_file=f"/f{i}.txt", current_file_size=10)
            )
        # 全部被节流：pending 累计但未达阈值、时间未推进
        assert recent_emits == []
        assert controller._recent_pending == n

    def test_recent_emit_triggered_by_batch(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """累计新增达到批量阈值时立即 emit recentParsedFilesChanged（不等时间）。"""
        import fuscan.gui.controllers.scan_controller as sc_mod

        monkeypatch.setattr(sc_mod.time, "perf_counter", lambda: 100.0)
        recent_emits: list[None] = []
        controller.recentParsedFilesChanged.connect(lambda: recent_emits.append(None))  # pyrefly: ignore [missing-attribute]
        # 预置 last=当前冻结时刻，隔离时间阈值，专测批量阈值触发。
        controller._recent_emit_last = 100.0
        for i in range(sc_mod._RECENT_EMIT_BATCH):
            controller._on_scan_progress(
                ProgressInfo(
                    phase="scan",
                    scanned=i + 1,
                    total=sc_mod._RECENT_EMIT_BATCH,
                    current_file=f"/f{i}.txt",
                    current_file_size=10,
                )
            )
        # 第 _RECENT_EMIT_BATCH 条使 pending 达阈值触发一次 emit，pending 归零
        assert len(recent_emits) == 1
        assert controller._recent_pending == 0

    def test_recent_emit_triggered_by_time(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """时间跨过 _RECENT_EMIT_INTERVAL 时即使 pending 未达批量阈值也 emit。"""
        import fuscan.gui.controllers.scan_controller as sc_mod

        clock = {"t": 100.0}
        monkeypatch.setattr(sc_mod.time, "perf_counter", lambda: clock["t"])
        recent_emits: list[None] = []
        controller.recentParsedFilesChanged.connect(lambda: recent_emits.append(None))  # pyrefly: ignore [missing-attribute]
        # 第一条：last 初值 -inf，时间阈值必满足 → emit（last=100.0）
        controller._on_scan_progress(
            ProgressInfo(phase="scan", scanned=1, total=2, current_file="/a.txt", current_file_size=10)
        )
        assert len(recent_emits) == 1
        # 时钟推进超过间隔阈值：下一条虽 pending=1（<批量）也应 emit
        clock["t"] = 100.0 + sc_mod._RECENT_EMIT_INTERVAL + 0.01
        controller._on_scan_progress(
            ProgressInfo(phase="scan", scanned=2, total=2, current_file="/b.txt", current_file_size=10)
        )
        assert len(recent_emits) == 2
        assert controller._recent_pending == 0

    def test_recent_emit_force_ignores_thresholds(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """force=True 无条件 emit 并归零 pending（不受时间/批量阈值约束）。"""
        import fuscan.gui.controllers.scan_controller as sc_mod

        monkeypatch.setattr(sc_mod.time, "perf_counter", lambda: 100.0)
        recent_emits: list[None] = []
        controller.recentParsedFilesChanged.connect(lambda: recent_emits.append(None))  # pyrefly: ignore [missing-attribute]
        controller._recent_pending = 3
        controller._maybe_emit_recent(force=True)
        assert len(recent_emits) == 1
        assert controller._recent_pending == 0

    def test_recent_emit_skipped_when_no_pending(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """无待刷新条目（pending==0）时非 force 调用早退不 emit。"""
        import fuscan.gui.controllers.scan_controller as sc_mod

        monkeypatch.setattr(sc_mod.time, "perf_counter", lambda: 100.0)
        recent_emits: list[None] = []
        controller.recentParsedFilesChanged.connect(lambda: recent_emits.append(None))  # pyrefly: ignore [missing-attribute]
        controller._recent_pending = 0
        controller._maybe_emit_recent()
        assert recent_emits == []

    def test_recent_emit_force_on_scan_finished(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """扫描完成时强制刷新明细列表，即使有未达阈值的 pending 也定格最新。"""
        recent_emits: list[None] = []
        controller.recentParsedFilesChanged.connect(lambda: recent_emits.append(None))  # pyrefly: ignore [missing-attribute]
        controller._recent_pending = 2
        controller._on_scan_finished(_make_scan_report())
        # 完成收尾 force emit，pending 归零
        assert len(recent_emits) >= 1
        assert controller._recent_pending == 0

    def test_recent_emit_force_on_scan_cancelled(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """扫描取消时强制刷新明细列表定格取消瞬间状态。"""
        recent_emits: list[None] = []
        controller.recentParsedFilesChanged.connect(lambda: recent_emits.append(None))  # pyrefly: ignore [missing-attribute]
        controller._recent_pending = 2
        controller._on_scan_cancelled(_make_scan_report(cancelled=True))
        assert len(recent_emits) >= 1
        assert controller._recent_pending == 0

    def test_recent_emit_force_on_pause(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """暂停扫描时强制刷新明细列表，让用户看到暂停瞬间的完整明细。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])
        recent_emits: list[None] = []
        controller.recentParsedFilesChanged.connect(lambda: recent_emits.append(None))  # pyrefly: ignore [missing-attribute]
        controller._recent_pending = 2
        controller.togglePause()
        assert controller.isPaused is True
        assert len(recent_emits) >= 1
        assert controller._recent_pending == 0

    def test_recent_emit_reset_on_start_scan(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """startScan 归零节流状态并 force emit 清空明细列表。"""
        # 制造残留节流状态
        controller._recent_pending = 5
        controller._recent_emit_last = 999.0
        recent_emits: list[None] = []
        controller.recentParsedFilesChanged.connect(lambda: recent_emits.append(None))  # pyrefly: ignore [missing-attribute]
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        # reset 归零并 force emit
        assert controller._recent_pending == 0
        assert controller.recentParsedFiles == []
        assert len(recent_emits) >= 1

    def test_walk_elapsed_text_empty_before_start(self, controller: ScanController) -> None:
        """walk 未开始（_walk_start_time==0）时 walkElapsedText 返回空串。"""
        assert controller.walkElapsedText == ""

    def test_walk_elapsed_text_running_realtime(self, controller: ScanController) -> None:
        """walk 进行中 walkElapsedText 实时返回 now-start 的格式化文案（非空）。"""
        import time as _time

        controller._walk_start_time = _time.perf_counter() - 0.5
        controller._walk_done = False
        # 进行中：返回非空实时用时文案（约 0.5s，落在秒档）
        assert controller.walkElapsedText != ""
        assert controller.walkElapsedText.endswith("s")

    def test_walk_elapsed_text_frozen_after_done(self, controller: ScanController) -> None:
        """walk 完成后 walkElapsedText 返回定格的 _walk_elapsed 格式化值。"""
        controller._walk_done = True
        controller._walk_elapsed = 0.86
        assert controller.walkElapsedText == "860ms"

    def test_scan_elapsed_text_empty_when_no_elapsed(self, controller: ScanController) -> None:
        """未进入解析阶段（_scan_elapsed<=0）时 scanElapsedText 返回空串。"""
        assert controller.scanElapsedText == ""

    def test_scan_elapsed_text_formatted(self, controller: ScanController) -> None:
        """scanElapsedText 复用 _scan_elapsed 并格式化。"""
        controller._scan_elapsed = 1.25
        assert controller.scanElapsedText == "1.2s"

    def test_walk_elapsed_settled_on_stats_finished(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """_on_stats_finished 结算 _walk_elapsed 并使 walkElapsedText 定格非空。"""
        import time as _time

        controller._ruleset = _build_ruleset()
        controller._walk_start_time = _time.perf_counter() - 0.3
        controller._on_stats_finished([_make_walk_result(tmp_path)])
        assert controller._walk_done is True
        assert controller._walk_elapsed > 0.0
        assert controller.walkElapsedText != ""

    def test_walk_elapsed_zero_when_no_start_time(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """_walk_start_time 未打点（==0）时 _on_stats_finished 结算 _walk_elapsed 归零。"""
        controller._ruleset = _build_ruleset()
        controller._walk_start_time = 0.0
        controller._on_stats_finished([_make_walk_result(tmp_path)])
        assert controller._walk_elapsed == 0.0

    def test_start_scan_resets_speed_and_recent_files(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """startScan 应重置累计耗时与解析明细，避免上次扫描残留。"""
        # 先制造残留状态
        controller._scan_elapsed = 5.0
        controller._recent_files.append({"path": "/old.txt", "size": 1, "ext": "txt", "elapsedMs": 1.0})
        # 启动新扫描
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        assert controller.scanSpeed == 0.0
        assert controller.recentParsedFiles == []

    def test_on_stats_finished_with_none_stats_worker(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """_on_stats_finished 时 _stats_worker 为 None 跳过 manifest 读取（iter-143 覆盖 1267->1269）。"""
        # 不通过 startScan，直接设 ruleset 后调用 _on_stats_finished
        controller._ruleset = _build_ruleset()
        controller._stats_worker = None  # type: ignore[bad-assignment]
        controller._pending_manifest = None

        controller._on_stats_finished([_make_walk_result(tmp_path)])

        # _pending_manifest 仍为 None（未读取 stats_worker.manifest）
        assert controller._pending_manifest is None

    def test_on_scan_finished_with_speed(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """_on_scan_finished 时 speed > 0 状态摘要应含速度（iter-143 覆盖 1364->1366）。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])
        scan_worker = scan_instances[0]

        result = _make_scan_result(tmp_path / "test.txt")
        report = ScanReport(
            root=tmp_path,
            results=(result,),
            stats=ScanStats(
                total_files=10,
                scanned_files=10,
                matched_files=1,
                skipped_files=0,
                errors=0,
                duration_seconds=0.5,  # speed = 10/0.5 = 20 > 0
                total_matches=1,
            ),
            cancelled=False,
        )
        scan_worker.emit_finished(report)
        assert "速度" in controller.statusSummary

    def test_set_restoring_noop_when_same(self, controller: ScanController) -> None:
        """_set_restoring 重复设置相同值不 emit 信号（iter-143 覆盖 1501->exit）。"""
        emitted: list[None] = []
        controller.restoringChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller._set_restoring(True)
        assert len(emitted) == 1
        # 重复设置 True 不 emit
        controller._set_restoring(True)
        assert len(emitted) == 1

    def test_set_status_without_summary_keeps_existing(
        self,
        controller: ScanController,
    ) -> None:
        """_set_status 不传 summary 时保留既有 _status_summary（iter-143 覆盖 1548->1550）。"""
        controller._set_status("初始", "初始摘要")
        assert controller.statusSummary == "初始摘要"
        # 不传 summary，_status_summary 应保持不变
        controller._set_status("新文本")
        assert controller.statusText == "新文本"
        assert controller.statusSummary == "初始摘要"

    def test_quick_cancel_with_running_scan_worker(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """quick_cancel 时 _worker.isRunning() True 应 cancel+wait（iter-143 覆盖 1614-1615）。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        # 完成 stats 阶段以创建 scan worker
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])
        scan_worker = scan_instances[0]

        controller.quick_cancel()
        assert scan_worker.cancel_called is True
        assert scan_worker.wait_called is True

    def test_quick_cancel_with_running_stats_worker(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """quick_cancel 时 _stats_worker.isRunning() True 应 cancel+wait（iter-143 覆盖 1619-1621）。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        stats_worker = stats_instances[0]

        controller.quick_cancel()
        assert stats_worker.cancel_called is True
        assert stats_worker.wait_called is True

    def test_quick_cancel_terminates_stubborn_scan_worker(
        self,
        controller: ScanController,
    ) -> None:
        """quick_cancel 时 _worker wait 后仍 isRunning 应 terminate（iter-143 覆盖 1616-1618）。"""

        class StubbornScanWorker:
            """wait 后仍 isRunning=True 的 scan worker 桩。"""

            def __init__(self) -> None:
                self.cancel_called = False
                self.wait_called = False
                self.terminate_called = False

            def cancel(self) -> None:
                self.cancel_called = True

            def wait(self, _msecs: int = 0) -> bool:
                self.wait_called = True
                return False  # 仍 running

            def terminate(self) -> None:
                self.terminate_called = True

            def isRunning(self) -> bool:
                return True  # 始终 running

            def deleteLater(self) -> None:
                pass

        stubborn = StubbornScanWorker()
        controller._worker = stubborn  # type: ignore[bad-assignment]
        controller.quick_cancel()
        assert stubborn.cancel_called is True
        assert stubborn.wait_called is True
        assert stubborn.terminate_called is True

    def test_quick_cancel_terminates_stubborn_stats_worker(
        self,
        controller: ScanController,
    ) -> None:
        """quick_cancel 时 _stats_worker wait 后仍 isRunning 应 terminate（iter-143 覆盖 1622-1624）。"""

        class StubbornStatsWorker:
            """wait 后仍 isRunning=True 的 stats worker 桩。"""

            def __init__(self) -> None:
                self.cancel_called = False
                self.wait_called = False
                self.terminate_called = False

            def cancel(self) -> None:
                self.cancel_called = True

            def wait(self, _msecs: int = 0) -> bool:
                self.wait_called = True
                return False

            def terminate(self) -> None:
                self.terminate_called = True

            def isRunning(self) -> bool:
                return True

            def deleteLater(self) -> None:
                pass

        stubborn = StubbornStatsWorker()
        controller._stats_worker = stubborn  # type: ignore[bad-assignment]
        controller.quick_cancel()
        assert stubborn.cancel_called is True
        assert stubborn.wait_called is True
        assert stubborn.terminate_called is True


class TestDetailHitsModel:
    """选中结果命中详情异步构建：轻量占位即时可读 + 后台 worker 补齐上下文。

    覆盖 :meth:`ScanController._refresh_detail_hits` /
    :meth:`_on_detail_done` / :meth:`_cancel_detail_worker` /
    :meth:`_cleanup_detail_worker` 及 cleanup/quick_cancel 清理路径。
    """

    @staticmethod
    def _populate(controller: ScanController, tmp_path: Path) -> Path:
        """写入一个含 ``password`` 的真实文件并设为唯一结果，返回文件路径。"""
        src = tmp_path / "secret.txt"
        src.write_text("line1\nlogin password=secret\nline3\n", encoding="utf-8")
        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="占位详情",
            match_text="password=secret",
            match_texts=("password=secret",),
        )
        result = ScanResult(path=src, size=src.stat().st_size, hits=(hit,))
        controller._result_model.set_results((result,))
        return src

    def test_light_model_immediately_readable(
        self,
        controller: ScanController,
        fake_detail_workers: list[FakeDetailWorker],
        tmp_path: Path,
    ) -> None:
        """选中变化后 detailHitsModel 立即返回轻量占位（context=detail），且启动 worker。"""
        self._populate(controller, tmp_path)
        controller.setSelectedResultIndex(0)

        model = controller.detailHitsModel
        assert len(model) == 1
        # 轻量占位：context 取 hit.detail，尚未读文件补上下文
        assert model[0]["context"] == "占位详情"
        assert model[0]["matchText"] == "password=secret"
        # 已启动一个 DetailWorker
        assert len(fake_detail_workers) == 1
        assert fake_detail_workers[0].start_called is True
        assert fake_detail_workers[0].generation == controller._detail_generation

    def test_worker_done_replaces_with_full_context(
        self,
        controller: ScanController,
        fake_detail_workers: list[FakeDetailWorker],
        tmp_path: Path,
    ) -> None:
        """worker done 回调（世代号匹配）替换缓存为完整上下文并 emit。"""
        self._populate(controller, tmp_path)
        controller.setSelectedResultIndex(0)
        worker = fake_detail_workers[0]

        changed: list[int] = []
        controller.detailHitsModelChanged.connect(lambda: changed.append(1))  # pyrefly: ignore [missing-attribute]

        # 模拟后台构建完整模型（真实调用 build_detail_hits_full 读文件补上下文）
        full = build_detail_hits_full(controller._get_selected_result())
        worker.emit_done(full, worker.generation)

        model = controller.detailHitsModel
        assert ">>> login password=secret" in str(model[0]["context"])
        assert changed  # detailHitsModelChanged 已 emit

    def test_stale_generation_discarded(
        self,
        controller: ScanController,
        fake_detail_workers: list[FakeDetailWorker],
        tmp_path: Path,
    ) -> None:
        """旧世代号的 worker done 回调被丢弃，不覆盖新缓存。"""
        self._populate(controller, tmp_path)
        controller.setSelectedResultIndex(0)
        stale_worker = fake_detail_workers[0]
        stale_gen = stale_worker.generation

        # 再次刷新（选中未变则不触发，故直接调 _refresh_detail_hits 模拟过滤/排序刷新）
        controller._refresh_detail_hits()
        assert controller._detail_generation != stale_gen

        current_model = controller.detailHitsModel
        # 旧 worker 用过期世代号 emit，应被丢弃
        stale_worker.emit_done([{"ruleName": "过期"}], stale_gen)
        assert controller.detailHitsModel == current_model

    def test_archive_entry_synchronous_no_worker(
        self,
        controller: ScanController,
        fake_detail_workers: list[FakeDetailWorker],
        tmp_path: Path,
    ) -> None:
        """压缩包内部条目主线程同步构建（context=detail），不启动 worker。"""
        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="压缩包内命中",
            match_text="password",
        )
        result = ScanResult(
            path=Path("inner/secret.txt"),
            size=100,
            hits=(hit,),
            archive_path=tmp_path / "bundle.zip",
        )
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)

        model = controller.detailHitsModel
        assert model[0]["context"] == "压缩包内命中"
        # 压缩包条目不读文件，不建 worker
        assert fake_detail_workers == []

    def test_none_selection_no_worker(
        self,
        controller: ScanController,
        fake_detail_workers: list[FakeDetailWorker],
        tmp_path: Path,
    ) -> None:
        """无选中结果时缓存清空为空列表，不启动 worker。"""
        self._populate(controller, tmp_path)
        controller.setSelectedResultIndex(0)
        assert len(fake_detail_workers) == 1

        # 取消选中：缓存清空、不新建 worker（仅取消旧 worker）
        controller.setSelectedResultIndex(-1)
        assert controller.detailHitsModel == []
        # None 分支不新建 worker，故仍是之前那 1 个（已取消）
        assert len(fake_detail_workers) == 1

    def test_rapid_switch_cancels_previous_worker(
        self,
        controller: ScanController,
        fake_detail_workers: list[FakeDetailWorker],
        tmp_path: Path,
    ) -> None:
        """快速切换选中时取消旧 worker（quit+wait+deleteLater）并建新 worker。"""
        src1 = tmp_path / "a.txt"
        src1.write_text("password=1\n", encoding="utf-8")
        src2 = tmp_path / "b.txt"
        src2.write_text("password=2\n", encoding="utf-8")
        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="d",
            match_text="password",
            match_texts=("password",),
        )
        results = (
            ScanResult(path=src1, size=src1.stat().st_size, hits=(hit,)),
            ScanResult(path=src2, size=src2.stat().st_size, hits=(hit,)),
        )
        controller._result_model.set_results(results)

        controller.setSelectedResultIndex(0)
        first_worker = fake_detail_workers[0]
        controller.setSelectedResultIndex(1)

        # 第一个 worker 被取消
        assert first_worker.wait_called is True
        # 新建了第二个 worker
        assert len(fake_detail_workers) == 2
        assert controller._detail_worker is fake_detail_workers[1]

    def test_on_detail_done_cleans_up_worker(
        self,
        controller: ScanController,
        fake_detail_workers: list[FakeDetailWorker],
        tmp_path: Path,
    ) -> None:
        """worker done 后 _detail_worker 被清理为 None（_cleanup_detail_worker）。"""
        self._populate(controller, tmp_path)
        controller.setSelectedResultIndex(0)
        worker = fake_detail_workers[0]

        worker.emit_done([], worker.generation)
        assert controller._detail_worker is None

    def test_on_detail_done_matching_generation_replaces_model(
        self,
        controller: ScanController,
    ) -> None:
        """_on_detail_done 世代号匹配时替换缓存并 emit（直接调用精确覆盖）。"""
        controller._detail_generation = 5
        changed: list[int] = []
        controller.detailHitsModelChanged.connect(lambda: changed.append(1))  # pyrefly: ignore [missing-attribute]

        full_model: list[dict[str, object]] = [{"ruleName": "r", "context": "完整上下文"}]
        controller._on_detail_done(full_model, 5)

        assert controller._detail_hits_model == full_model
        assert controller.detailHitsModel == full_model
        assert changed  # detailHitsModelChanged 已 emit

    def test_on_detail_done_stale_generation_discarded(
        self,
        controller: ScanController,
    ) -> None:
        """_on_detail_done 世代号不匹配时丢弃，不替换缓存（直接调用精确覆盖）。"""
        controller._detail_generation = 9
        controller._detail_hits_model = [{"ruleName": "旧"}]

        controller._on_detail_done([{"ruleName": "过期"}], 3)

        assert controller._detail_hits_model == [{"ruleName": "旧"}]

    def test_cleanup_workers_cleans_detail_worker(
        self,
        controller: ScanController,
        fake_detail_workers: list[FakeDetailWorker],
        tmp_path: Path,
    ) -> None:
        """_cleanup_workers 非阻塞清理 detail worker（deleteLater + 置 None）。"""
        self._populate(controller, tmp_path)
        controller.setSelectedResultIndex(0)
        assert controller._detail_worker is not None

        controller._cleanup_workers()
        assert controller._detail_worker is None

    def test_quick_cancel_terminates_stubborn_detail_worker(
        self,
        controller: ScanController,
    ) -> None:
        """quick_cancel 时 detail worker wait 后仍 isRunning 应 terminate。"""

        class StubbornDetailWorker:
            """wait 后仍 isRunning=True 的 detail worker 桩。"""

            def __init__(self) -> None:
                self.quit_called = False
                self.wait_called = False
                self.terminate_called = False

            def quit(self) -> None:
                self.quit_called = True

            def wait(self, _msecs: int = 0) -> bool:
                self.wait_called = True
                return False  # 仍 running

            def terminate(self) -> None:
                self.terminate_called = True

            def isRunning(self) -> bool:
                return True

            def deleteLater(self) -> None:
                pass

        stubborn = StubbornDetailWorker()
        controller._detail_worker = stubborn  # type: ignore[bad-assignment]
        controller.quick_cancel()
        assert stubborn.quit_called is True
        assert stubborn.wait_called is True
        assert stubborn.terminate_called is True

    def test_cleanup_cancels_detail_worker(
        self,
        controller: ScanController,
        fake_detail_workers: list[FakeDetailWorker],
        tmp_path: Path,
    ) -> None:
        """cleanup 阻塞取消 detail worker（quit+wait+deleteLater 置 None）。"""
        self._populate(controller, tmp_path)
        controller.setSelectedResultIndex(0)
        worker = fake_detail_workers[0]

        controller.cleanup()
        assert worker.wait_called is True
        assert controller._detail_worker is None


class TestStatsChartData:
    """统计页图表数据 Property 测试（severityChartData/topRulesChartData/extensionChartData）。

    覆盖：空态、严重度分组与顺序、Top 规则排序与色值、扩展名分组与「其他」归并、
    restoreFromReport 后数据刷新。图表数据由 :meth:`ScanReport.group_by_severity`/
    :meth:`group_by_rule` 派生，本测试不重测 group_by 本身（已在 test_scanner 覆盖），
    仅验证 controller 层聚合/排序/截断/色值映射逻辑。
    """

    def test_empty_when_no_report(self, controller: ScanController) -> None:
        """未扫描时三个图表数据均为空列表。"""
        assert controller.severityChartData == []
        assert controller.topRulesChartData == []
        assert controller.extensionChartData == []

    def test_empty_when_no_hits(self, controller: ScanController) -> None:
        """扫描完成但无命中时图表数据为空。"""
        controller.restoreFromReport(_make_scan_report(results=()))
        assert controller.severityChartData == []
        assert controller.topRulesChartData == []
        assert controller.extensionChartData == []

    def test_severity_chart_orders_info_warning_critical(self, controller: ScanController) -> None:
        """严重度图表按 INFO/WARNING/CRITICAL 固定顺序返回有命中的档位。"""
        results = (
            ScanResult(
                path=Path("/tmp/c.txt"),
                size=10,
                hits=(RuleHit(rule_name="r3", severity=Severity.CRITICAL, detail=""),),
            ),
            ScanResult(
                path=Path("/tmp/a.txt"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
            ScanResult(
                path=Path("/tmp/b.txt"),
                size=10,
                hits=(RuleHit(rule_name="r2", severity=Severity.WARNING, detail=""),),
            ),
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.severityChartData
        # 顺序固定 INFO → WARNING → CRITICAL，不受结果顺序影响
        assert [d["label"] for d in data] == ["信息", "警告", "严重"]
        assert [d["value"] for d in data] == [1, 1, 1]
        # 色值与 severity_utils 一致
        assert data[0]["color"] == "#0366D6"  # INFO 蓝
        assert data[1]["color"] == "#F0883E"  # WARNING 橙
        assert data[2]["color"] == "#D73A49"  # CRITICAL 红

    def test_severity_chart_skips_empty_severity(self, controller: ScanController) -> None:
        """仅有 INFO 命中时图表只返回 INFO 档位（跳过空档）。"""
        results = (
            ScanResult(
                path=Path("/tmp/a.txt"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.severityChartData
        assert len(data) == 1
        assert data[0]["label"] == "信息"
        assert data[0]["value"] == 1

    def test_top_rules_chart_sorted_by_file_count_desc(self, controller: ScanController) -> None:
        """Top 规则图表按命中文件数降序排列。"""
        results = (
            ScanResult(
                path=Path("/tmp/a.txt"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
            ScanResult(
                path=Path("/tmp/b.txt"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
            ScanResult(
                path=Path("/tmp/c.txt"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
            ScanResult(
                path=Path("/tmp/d.txt"),
                size=10,
                hits=(RuleHit(rule_name="r2", severity=Severity.CRITICAL, detail=""),),
            ),
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.topRulesChartData
        assert len(data) == 2
        assert data[0]["label"] == "r1"
        assert data[0]["value"] == 3
        assert data[1]["label"] == "r2"
        assert data[1]["value"] == 1

    def test_top_rules_chart_color_by_max_severity(self, controller: ScanController) -> None:
        """Top 规则颜色取该规则在所有命中中的最高严重度档位色值。"""
        # 同一规则 r1 在不同文件有不同严重度（理论不会，但验证 max 聚合）
        results = (
            ScanResult(
                path=Path("/tmp/a.txt"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
            ScanResult(
                path=Path("/tmp/b.txt"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.CRITICAL, detail=""),),
            ),
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.topRulesChartData
        assert len(data) == 1
        assert data[0]["value"] == 2  # 去重文件数
        assert data[0]["color"] == "#D73A49"  # CRITICAL 红（最高档）

    def test_top_rules_chart_dedup_same_file_multiple_hits(self, controller: ScanController) -> None:
        """同一文件被同一规则多次命中，Top 规则计数仅计 1（去重文件数）。"""
        results = (
            ScanResult(
                path=Path("/tmp/a.txt"),
                size=10,
                hits=(
                    RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),
                    RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),
                ),
            ),
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.topRulesChartData
        assert len(data) == 1
        assert data[0]["value"] == 1  # 同文件去重，计 1

    def test_top_rules_chart_limit_10(self, controller: ScanController) -> None:
        """Top 规则图表最多返回 10 条。"""
        results = tuple(
            ScanResult(
                path=Path(f"/tmp/{i}.txt"),
                size=10,
                hits=(RuleHit(rule_name=f"rule_{i}", severity=Severity.INFO, detail=""),),
            )
            for i in range(15)
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.topRulesChartData
        assert len(data) == 10

    def test_extension_chart_groups_by_extension(self, controller: ScanController) -> None:
        """扩展名图表按扩展名分组并按命中文件数降序排列。"""
        results = (
            ScanResult(
                path=Path("/tmp/a.py"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
            ScanResult(
                path=Path("/tmp/b.py"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
            ScanResult(
                path=Path("/tmp/c.txt"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.extensionChartData
        assert data[0]["label"] == ".py"
        assert data[0]["value"] == 2
        assert data[1]["label"] == ".txt"
        assert data[1]["value"] == 1

    def test_extension_chart_other_bucket(self, controller: ScanController) -> None:
        """超过 8 个扩展名时多余项归入「其他」桶。"""
        results = tuple(
            ScanResult(
                path=Path(f"/tmp/file_{i}.ext{i}"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            )
            for i in range(10)
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.extensionChartData
        # 8 个 Top + 1 个「其他」
        assert len(data) == 9
        assert data[-1]["label"] == "其他"
        assert data[-1]["value"] == 2  # 10 - 8 = 2

    def test_extension_chart_case_insensitive(self, controller: ScanController) -> None:
        """扩展名大小写归一（.PY 与 .py 合并）。"""
        results = (
            ScanResult(
                path=Path("/tmp/a.PY"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
            ScanResult(
                path=Path("/tmp/b.py"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.extensionChartData
        assert len(data) == 1
        assert data[0]["label"] == ".py"
        assert data[0]["value"] == 2

    def test_extension_chart_no_extension_bucket(self, controller: ScanController) -> None:
        """无扩展名文件归入「(无扩展名)」档位。"""
        results = (
            ScanResult(
                path=Path("/tmp/Makefile"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.INFO, detail=""),),
            ),
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.extensionChartData
        assert len(data) == 1
        assert data[0]["label"] == "(无扩展名)"

    def test_restore_from_report_refreshes_chart_data(self, controller: ScanController) -> None:
        """restoreFromReport 后图表数据应反映最新报告（覆盖旧数据）。"""
        # 先恢复空报告
        controller.restoreFromReport(_make_scan_report(results=()))
        assert controller.severityChartData == []
        # 再恢复有命中的报告
        results = (
            ScanResult(
                path=Path("/tmp/a.txt"),
                size=10,
                hits=(RuleHit(rule_name="r1", severity=Severity.WARNING, detail=""),),
            ),
        )
        controller.restoreFromReport(_make_scan_report(results=results))
        data = controller.severityChartData
        assert len(data) == 1
        assert data[0]["label"] == "警告"


class TestPerfSummary:
    """性能剖析 Property 测试（perfSummary）。

    覆盖：空态（无报告/无 perf_summary）、按 total_ms 降序、stage 中英文映射、
    percent 占比计算、count 字段、未知 stage 保留英文、恢复报告无 perf 数据。
    perf_summary 不持久化（from_json 置 None），故恢复的历史报告无性能数据。
    """

    def test_empty_when_no_report(self, controller: ScanController) -> None:
        """未扫描时 perfSummary 为空列表。"""
        assert controller.perfSummary == []

    def test_empty_when_no_perf_data(self, controller: ScanController) -> None:
        """ScanStats.perf_summary 为 None 时返回空列表。"""
        controller.restoreFromReport(_make_scan_report(results=()))
        assert controller.perfSummary == []

    def test_sorted_by_total_ms_desc(self, controller: ScanController) -> None:
        """perfSummary 按 total_ms 降序排列。"""
        report = ScanReport(
            root=Path("/tmp"),
            results=(),
            stats=ScanStats(
                perf_summary={
                    "walk": {"total_ms": 100.0, "count": 1, "max_ms": 100.0},
                    "match": {"total_ms": 300.0, "count": 50, "max_ms": 10.0},
                    "read_bytes": {"total_ms": 200.0, "count": 50, "max_ms": 5.0},
                },
            ),
        )
        controller.restoreFromReport(report)
        data = controller.perfSummary
        assert len(data) == 3
        # 降序：match(300) > read_bytes(200) > walk(100)
        assert data[0]["label"] == "规则匹配"
        assert data[0]["value"] == 300.0
        assert data[1]["label"] == "读取文件"
        assert data[1]["value"] == 200.0
        assert data[2]["label"] == "遍历文件"
        assert data[2]["value"] == 100.0

    def test_percent_calculation(self, controller: ScanController) -> None:
        """percent 为该阶段占总耗时的百分比。"""
        report = ScanReport(
            root=Path("/tmp"),
            results=(),
            stats=ScanStats(
                perf_summary={
                    "match": {"total_ms": 300.0, "count": 50, "max_ms": 10.0},
                    "walk": {"total_ms": 100.0, "count": 1, "max_ms": 100.0},
                },
            ),
        )
        controller.restoreFromReport(report)
        data = controller.perfSummary
        # 总耗时 400ms：match=75%, walk=25%
        assert data[0]["percent"] == 75.0
        assert data[1]["percent"] == 25.0

    def test_count_field(self, controller: ScanController) -> None:
        """count 字段反映该阶段调用次数。"""
        report = ScanReport(
            root=Path("/tmp"),
            results=(),
            stats=ScanStats(
                perf_summary={
                    "match": {"total_ms": 300.0, "count": 50, "max_ms": 10.0},
                },
            ),
        )
        controller.restoreFromReport(report)
        data = controller.perfSummary
        assert data[0]["count"] == 50

    def test_unknown_stage_keeps_english(self, controller: ScanController) -> None:
        """未知 stage 名保留英文原名（向前兼容未来新增 stage）。"""
        report = ScanReport(
            root=Path("/tmp"),
            results=(),
            stats=ScanStats(
                perf_summary={
                    "unknown_stage": {"total_ms": 50.0, "count": 1, "max_ms": 50.0},
                },
            ),
        )
        controller.restoreFromReport(report)
        data = controller.perfSummary
        assert data[0]["label"] == "unknown_stage"

    def test_restore_from_persisted_report_has_no_perf(self, controller: ScanController) -> None:
        """恢复的历史报告无 perf_summary（from_json 置 None），返回空列表。"""
        # _make_scan_report 默认不传 perf_summary（None）
        controller.restoreFromReport(_make_scan_report(results=()))
        assert controller.perfSummary == []

    def test_value_rounded_to_one_decimal(self, controller: ScanController) -> None:
        """value 保留 1 位小数。"""
        report = ScanReport(
            root=Path("/tmp"),
            results=(),
            stats=ScanStats(
                perf_summary={
                    "match": {"total_ms": 123.456, "count": 1, "max_ms": 123.456},
                },
            ),
        )
        controller.restoreFromReport(report)
        data = controller.perfSummary
        assert data[0]["value"] == 123.5
