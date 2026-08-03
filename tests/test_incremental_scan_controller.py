"""``ScanController`` 增量扫描测试（iter-124）。

覆盖 :meth:`ScanController.startIncrementalScan` / :meth:`_load_manifest` /
:meth:`_save_manifest` 与增量上下文（``_pending_manifest`` /
``_pending_prev_report`` / ``_pending_ws_id``）的传递与持久化。

耗时操作（真实 ``ScanWorker`` / ``FileStatsWorker``）通过替换为 FakeWorker
避免启动 QThread。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from typing_extensions import override

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from fuscan.config import Config  # noqa: F401
    from fuscan.gui.controllers.config_controller import ConfigController
    from fuscan.gui.controllers.rules_controller import RulesController
    from fuscan.gui.controllers.scan_controller import ScanController
    from fuscan.rules.model import Severity
    from fuscan.scanner import ScanReport, ScanResult, ScanStats
    from fuscan.scanner.manifest import FileFingerprint, IncrementalManifest
    from fuscan.scanner.result import (
        ProgressInfo,
        RuleHit,
        WalkResult,
    )

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过增量扫描控制器测试", allow_module_level=True)


# ---------------------------- Fake 对象（与 test_gui_scan_controller 同构） ----------------------------


class FakeSignal:
    """模拟 PySide2 Signal：通过 ``connect`` 注册回调，``emit`` 触发。"""

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

    记录构造参数供断言，``manifest`` 属性模拟 FileStatsWorker 构建的增量清单。
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
        # 默认 manifest=None；测试用例可通过设置该属性模拟清单构建结果
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


# ---------------------------- fixtures ----------------------------


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将 ``~/.fuscan`` 重定向到 tmp_path，避免污染用户配置。"""
    fake_home = tmp_path / "fuscan_home"
    fake_home.mkdir()
    config_dir = fake_home / ".fuscan"
    config_dir.mkdir()
    monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_dir / "config.yaml")
    # 同步重定向 _MANIFESTS_DIR（模块级常量，在 scan_controller 顶层已求值）
    manifests_dir = config_dir / "manifests"
    monkeypatch.setattr(
        "fuscan.gui.controllers.scan_controller._MANIFESTS_DIR",
        manifests_dir,
    )
    return config_dir


@pytest.fixture()
def config_controller(config_dir: Path) -> ConfigController:
    return ConfigController()


@pytest.fixture()
def rules_controller(config_controller: ConfigController) -> RulesController:
    return RulesController(config_controller)


@pytest.fixture()
def controller(
    config_controller: ConfigController,
    rules_controller: RulesController,
) -> ScanController:
    return ScanController(config_controller, rules_controller)


@pytest.fixture()
def fake_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[FakeStatsWorker], list[FakeScanWorker]]:
    """替换 ScanController 中的 FileStatsWorker 与 ScanWorker 为 Fake。"""
    FakeStatsWorker.instances.clear()
    FakeScanWorker.instances.clear()
    monkeypatch.setattr(
        "fuscan.gui.controllers.scan_controller.FileStatsWorker",
        FakeStatsWorker,
    )
    monkeypatch.setattr(
        "fuscan.gui.controllers.scan_controller.ScanWorker",
        FakeScanWorker,
    )
    return FakeStatsWorker.instances, FakeScanWorker.instances


# ---------------------------- 辅助构造 ----------------------------


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


def _make_manifest(root: Path = Path("/tmp")) -> IncrementalManifest:
    """构造测试用 IncrementalManifest，含 2 项指纹。"""
    return IncrementalManifest(
        root=root,
        fingerprints={
            "a.txt": FileFingerprint(mtime=1000.0, size=10),
            "b.txt": FileFingerprint(mtime=2000.0, size=20),
        },
    )


# ---------------------------- 测试用例 ----------------------------


class TestStartIncrementalScanFallback:
    """``startIncrementalScan`` 回退场景。"""

    def test_fallback_no_last_report(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """无上次 ScanReport（_last_report=None）时应回退到 startScan。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)  # folder 模式
        controller.setFolderRoot(str(tmp_path))
        assert controller._last_report is None

        controller.startIncrementalScan("ws-1")

        # 回退到 startScan：应创建 stats worker，状态切换为 scanning
        assert len(stats_instances) == 1
        assert controller.scanState == "scanning"
        # _pending_ws_id 应已被设置（_on_scan_finished 据此持久化 manifest）
        assert controller._pending_ws_id == "ws-1"

    def test_fallback_no_manifest_file(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """有 _last_report 但 manifest 文件不存在时应回退到 startScan。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller._last_report = _make_scan_report()

        controller.startIncrementalScan("ws-no-manifest")

        # 回退到全量扫描
        assert len(stats_instances) == 1
        assert controller.scanState == "scanning"
        # iter-135：回退时保留 _pending_prev_report，供 _on_scan_finished
        # 在本次无命中时合并旧 hits（不再清零）
        assert controller._pending_prev_report is not None
        # _pending_ws_id 仍应被设置，以便 _on_scan_finished 持久化新 manifest
        assert controller._pending_ws_id == "ws-no-manifest"


class TestStartIncrementalScanWithManifest:
    """``startIncrementalScan`` 正常增量场景。"""

    def test_with_manifest_passes_incremental_context(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """有 _last_report 与 manifest 文件时应启用增量模式。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))

        # 设置 _last_report（非 None）
        prev_report = _make_scan_report(results=(_make_scan_result(),))
        controller._last_report = prev_report

        # 创建 manifest 文件（_MANIFESTS_DIR 已被 config_dir fixture 重定向到 tmp 下）
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        manifests_dir.mkdir(parents=True, exist_ok=True)
        manifest = _make_manifest()
        (manifests_dir / "ws-inc.json").write_text(manifest.to_json(), encoding="utf-8")

        controller.startIncrementalScan("ws-inc")

        # 应创建 stats worker 并切换到 scanning
        assert len(stats_instances) == 1
        assert controller.scanState == "scanning"
        # FakeStatsWorker 收到 incremental_manifest 参数（_load_manifest 通过 from_json
        # 反序列化生成新对象，故只能验证内容一致而非同一实例）
        worker_kwargs = stats_instances[0].kwargs
        passed_manifest = worker_kwargs.get("incremental_manifest")
        assert isinstance(passed_manifest, IncrementalManifest)
        assert passed_manifest.fingerprints["a.txt"].size == 10
        assert passed_manifest.fingerprints["b.txt"].mtime == 2000.0
        # _pending_prev_report 被设置为 prev_report（_on_stats_finished 传给 ScanWorker）
        assert controller._pending_prev_report is prev_report
        # _pending_ws_id 被设置
        assert controller._pending_ws_id == "ws-inc"

    def test_noop_when_scanning(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """scanning 态重复调用应被忽略。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller._last_report = _make_scan_report()
        # 先创建 manifest 文件
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        manifests_dir.mkdir(parents=True, exist_ok=True)
        (manifests_dir / "ws-scan.json").write_text(_make_manifest().to_json(), encoding="utf-8")

        controller.startIncrementalScan("ws-scan")
        assert len(stats_instances) == 1
        # 再次调用：scanning 态应被忽略
        controller.startIncrementalScan("ws-scan")
        assert len(stats_instances) == 1

    def test_noop_when_no_ruleset(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """无规则集时应被忽略（已有 manifest 文件，但 ruleset=None）。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller._last_report = _make_scan_report()
        # iter-137：通过全局 RulesController 清空规则集
        controller._rules_controller.setUseBuiltin(False)
        assert controller._rules_controller.ruleset is None
        # 创建 manifest 文件
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        manifests_dir.mkdir(parents=True, exist_ok=True)
        (manifests_dir / "ws-norule.json").write_text(_make_manifest().to_json(), encoding="utf-8")

        controller.startIncrementalScan("ws-norule")
        # 无规则集时应被忽略
        assert len(stats_instances) == 0
        assert controller.scanState == "setup"

    def test_noop_when_no_target(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """无扫描目标时应被忽略。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot("")  # 空目标
        controller._last_report = _make_scan_report()
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        manifests_dir.mkdir(parents=True, exist_ok=True)
        (manifests_dir / "ws-notarget.json").write_text(_make_manifest().to_json(), encoding="utf-8")

        controller.startIncrementalScan("ws-notarget")
        assert len(stats_instances) == 0


class TestLoadManifest:
    """``_load_manifest`` 测试。"""

    def test_load_manifest_returns_instance(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """文件存在时应返回 IncrementalManifest 实例。"""
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        manifests_dir.mkdir(parents=True, exist_ok=True)
        manifest = _make_manifest(root=tmp_path)
        (manifests_dir / "load-ok.json").write_text(manifest.to_json(), encoding="utf-8")

        loaded = controller._load_manifest("load-ok")
        assert loaded is not None
        assert isinstance(loaded, IncrementalManifest)
        assert loaded.fingerprints["a.txt"].mtime == 1000.0
        assert loaded.fingerprints["a.txt"].size == 10
        assert loaded.fingerprints["b.txt"].size == 20

    def test_load_manifest_missing_returns_none(self, controller: ScanController) -> None:
        """文件不存在时应返回 None。"""
        result = controller._load_manifest("nonexistent-ws")
        assert result is None

    def test_load_manifest_invalid_json_returns_none(
        self,
        controller: ScanController,
    ) -> None:
        """JSON 解析失败时应返回 None（不抛异常）。"""
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        manifests_dir.mkdir(parents=True, exist_ok=True)
        # 写入非法 JSON
        (manifests_dir / "bad.json").write_text("{not valid json", encoding="utf-8")

        result = controller._load_manifest("bad")
        assert result is None


class TestSaveManifest:
    """``_save_manifest`` 测试。"""

    def test_save_manifest_writes_file(
        self,
        controller: ScanController,
    ) -> None:
        """调用 _save_manifest 后应创建 JSON 文件且内容可被 _load_manifest 读回。"""
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        # 此时 manifests_dir 可能不存在，_save_manifest 应自动创建

        manifest = _make_manifest()
        controller._save_manifest("save-ok", manifest)

        manifest_file = manifests_dir / "save-ok.json"
        assert manifest_file.exists()
        # 内容应为合法 JSON，且包含 fingerprints
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert "fingerprints" in data
        assert "a.txt" in data["fingerprints"]
        assert data["fingerprints"]["a.txt"]["size"] == 10

        # 回读校验
        loaded = controller._load_manifest("save-ok")
        assert loaded is not None
        assert loaded.fingerprints["a.txt"].size == 10

    def test_save_manifest_handles_oserror(
        self,
        controller: ScanController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OSError 时应记录 warning 不抛异常。"""
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR

        class _BadPath(type(manifests_dir)):  # type: ignore[misc]
            @override
            def mkdir(self, *args: Any, **kwargs: Any) -> None:
                raise OSError("模拟写入失败")

        # 用 monkeypatch 替换 _MANIFESTS_DIR 为 _BadPath 实例
        bad_dir = _BadPath(manifests_dir)
        monkeypatch.setattr(sc_module, "_MANIFESTS_DIR", bad_dir)

        # 不应抛异常
        controller._save_manifest("fail", _make_manifest())


class TestOnStatsFinishedReadsManifest:
    """``_on_stats_finished`` 读取 stats worker 构建的 manifest。"""

    def test_reads_manifest_into_pending(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """stats 完成时 _pending_manifest 应被设置为 stats_worker.manifest。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()

        # 模拟 stats worker 构建了 manifest
        built_manifest = _make_manifest()
        stats_instances[0].manifest = built_manifest

        assert controller._pending_manifest is None
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])

        assert controller._pending_manifest is built_manifest


class TestOnScanFinishedSavesManifest:
    """``_on_scan_finished`` 持久化 manifest 测试。"""

    def test_saves_manifest_when_pending_ws_id_set(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """_pending_manifest 非 None 且 _pending_ws_id 非空时应持久化到文件。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))

        # 模拟 startIncrementalScan 设置的增量上下文
        controller._pending_ws_id = "ws-persist"
        built_manifest = _make_manifest()
        controller._pending_manifest = built_manifest

        # startScan → stats finished → scan finished
        controller.startScan()
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])

        # 验证 _pending_ws_id 未被 startScan 重置
        assert controller._pending_ws_id == "ws-persist"
        # stats 完成会读取 _pending_manifest（这里 stats_worker.manifest=None，会清空）
        # 重新设置 _pending_manifest
        controller._pending_manifest = built_manifest

        # 触发 scan 完成
        report = _make_scan_report(results=(_make_scan_result(tmp_path / "x.txt"),))
        scan_instances[0].emit_finished(report)

        # 验证 manifest 已持久化到文件
        from fuscan.gui.controllers import scan_controller as sc_module

        manifest_file = sc_module._MANIFESTS_DIR / "ws-persist.json"
        assert manifest_file.exists()
        # 内容应包含 built_manifest 的指纹
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert "a.txt" in data["fingerprints"]
        # _pending_manifest 在持久化后不应被清除（仅 _pending_ws_id 标识保留）
        # 注意：_on_scan_finished 不清除 _pending_manifest，下次扫描 startScan/startIncrementalScan 会重置

    def test_not_saved_when_ws_id_empty(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """_pending_ws_id 为空时（纯全量扫描）不应持久化 manifest。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))

        # 模拟 stats worker 构建了 manifest，但 _pending_ws_id 为空
        controller.startScan()
        built_manifest = _make_manifest()
        stats_instances[0].manifest = built_manifest
        # _pending_ws_id 默认为 ""（startScan 不设置）
        assert controller._pending_ws_id == ""

        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])
        # _pending_manifest 应被设置为 stats_worker.manifest
        assert controller._pending_manifest is built_manifest

        # 触发 scan 完成
        report = _make_scan_report(results=())
        scan_instances[0].emit_finished(report)

        # _pending_ws_id 为空，不应持久化
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        # manifests_dir 可能不存在，或存在但无任何 .json 文件
        if manifests_dir.exists():
            assert not list(manifests_dir.glob("*.json"))

    def test_not_saved_when_manifest_none(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """_pending_manifest 为 None 时不应持久化（即使 _pending_ws_id 非空）。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))

        controller._pending_ws_id = "ws-none-manifest"
        # _pending_manifest 保持 None
        assert controller._pending_manifest is None

        controller.startScan()
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])
        # stats_worker.manifest 为 None，_pending_manifest 仍为 None
        assert controller._pending_manifest is None

        report = _make_scan_report(results=())
        scan_instances[0].emit_finished(report)

        # 不应持久化任何文件
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        if manifests_dir.exists():
            assert not list(manifests_dir.glob("*.json"))


class TestOnScanFinishedMergesOldHits:
    """iter-135：增量扫描回退全量时，``_on_scan_finished`` 合并旧 hits 测试。"""

    def test_merges_old_hits_when_new_report_empty(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """增量回退全量后本次无命中时，应合并 _pending_prev_report 中的旧 hits。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))

        # 设置 _last_report 有 hits，但不提供 manifest 文件 → startIncrementalScan 回退为全量
        old_result = _make_scan_result(tmp_path / "old.txt", hits=2)
        old_report = _make_scan_report(results=(old_result,))
        controller._last_report = old_report

        # startIncrementalScan 会因 manifest 不存在而回退为 startScan，
        # 并保留 _pending_prev_report = old_report
        controller.startIncrementalScan("ws-merge-test")
        assert controller._pending_prev_report is not None

        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])

        # 本次扫描无命中
        empty_report = _make_scan_report(results=())
        scan_instances[0].emit_finished(empty_report)

        # _last_report 应包含合并后的旧 hits
        assert controller._last_report is not None
        assert len(controller._last_report.hits) == 1
        assert controller._last_report.hits[0].path == old_result.path
        # 状态应切到 results（有命中）
        assert controller.scanState == "results"

    def test_no_merge_when_prev_report_none(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """正常全量扫描（_pending_prev_report=None）无命中时不合并。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))

        controller.startScan()
        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])

        # 本次扫描无命中，_pending_prev_report 为 None
        empty_report = _make_scan_report(results=())
        scan_instances[0].emit_finished(empty_report)

        # 不合并，hits 为空
        assert controller._last_report is not None
        assert len(controller._last_report.hits) == 0
        # 状态切到 setup（无命中）
        assert controller.scanState == "setup"

    def test_no_merge_when_new_report_has_hits(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """增量回退全量后本次有命中时，不合并旧 hits。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))

        # 设置 _last_report 有 hits，manifest 不存在 → 回退为全量
        old_result = _make_scan_result(tmp_path / "old.txt", hits=1)
        old_report = _make_scan_report(results=(old_result,))
        controller._last_report = old_report

        controller.startIncrementalScan("ws-merge-test2")
        assert controller._pending_prev_report is not None

        stats_instances[0].emit_finished([_make_walk_result(tmp_path)])

        # 本次扫描有命中
        new_result = _make_scan_result(tmp_path / "new.txt", hits=1)
        new_report = _make_scan_report(results=(new_result,))
        scan_instances[0].emit_finished(new_report)

        # 不合并旧 hits，只有本次的 1 条命中
        assert len(controller._last_report.hits) == 1
        assert controller._last_report.hits[0].path == new_result.path


class TestBuildHistoryEntry:
    """``build_history_entry`` 测试（iter-115）。

    ``build_history_entry`` 在扫描完成后由 ``WorkspaceController`` 调用，
    从 ``_last_report`` 构建归档条目。``_last_report`` 也是 ``startIncrementalScan``
    判断是否可启用增量的依据，故与增量上下文紧密相关。
    """

    def test_returns_none_when_no_last_report(self, controller: ScanController) -> None:
        """无 _last_report 时应返回 None。"""
        assert controller._last_report is None
        entry = controller.build_history_entry("ws-1", "工作区A")
        assert entry is None

    def test_returns_entry_from_completed_report(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """有 _last_report 且未取消时应构建 STATUS_COMPLETED 条目。"""
        result = _make_scan_result(tmp_path / "secret.txt", hits=2)
        controller._last_report = _make_scan_report(results=(result,), cancelled=False)

        entry = controller.build_history_entry("ws-2", "工作区B")
        assert entry is not None
        assert entry.workspace_id == "ws-2"
        assert entry.workspace_name == "工作区B"
        # 状态应为 COMPLETED
        from fuscan.history import STATUS_COMPLETED

        assert entry.status == STATUS_COMPLETED
        assert entry.total_files == 10
        assert entry.matched_files == 1
        # hit_paths 为排序后的路径元组
        assert entry.hit_paths == (str(tmp_path / "secret.txt"),)
        # rule_names 为排序后的规则名元组
        assert entry.rule_names == ("敏感内容",)

    def test_returns_cancelled_status_when_report_cancelled(
        self,
        controller: ScanController,
    ) -> None:
        """取消的 report 应构建 STATUS_CANCELLED 条目。"""
        controller._last_report = _make_scan_report(results=(), cancelled=True)

        entry = controller.build_history_entry("ws-3", "工作区C")
        assert entry is not None
        from fuscan.history import STATUS_CANCELLED

        assert entry.status == STATUS_CANCELLED
        assert entry.hit_paths == ()
        assert entry.rule_names == ()


class TestRestoreFromReport:
    """``restoreFromReport`` 测试（iter-123）。

    ``restoreFromReport`` 从持久化的 ScanReport 恢复 ``_last_report``，
    使 ``startIncrementalScan`` 在重启后仍可启用增量。
    """

    def test_restores_results_with_hits(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """恢复有命中的 report 应切到 results 状态。"""
        result = _make_scan_result(tmp_path / "x.txt", hits=1)
        report = _make_scan_report(results=(result,), cancelled=False)
        assert controller.scanState == "setup"

        controller.restoreFromReport(report)

        # _last_report 应被设置（startIncrementalScan 据此判断可否启用增量）
        assert controller._last_report is report
        # 有命中 → 切到 results
        assert controller.scanState == "results"
        # resultModel 应包含恢复的命中
        assert controller.resultModel.rowCount() == 1
        # scanPhase 标记为 done
        assert controller.scanPhase == "done"
        assert controller.scanDone is True
        # statusText 应为「已完成」
        assert controller.statusText == "已完成"

    def test_restores_results_no_hits(
        self,
        controller: ScanController,
    ) -> None:
        """恢复无命中的 report 应保持 setup 状态。"""
        report = _make_scan_report(results=(), cancelled=False)
        controller.restoreFromReport(report)

        assert controller._last_report is report
        assert controller.scanState == "setup"
        assert controller.statusText == "已完成"

    def test_restores_cancelled_report(
        self,
        controller: ScanController,
    ) -> None:
        """恢复取消的 report 应将 statusText 设为「已完成[用户取消]」。"""
        report = _make_scan_report(results=(), cancelled=True)
        controller.restoreFromReport(report)

        assert controller._last_report is report
        assert controller.statusText == "已完成[用户取消]"

    def test_restores_report_speed_zero(
        self,
        controller: ScanController,
    ) -> None:
        """report.stats.speed=0 时 summary 不附加速度信息（duration=0 分支）。"""
        # duration_seconds=0 → speed=0.0
        report = ScanReport(
            root=Path("/tmp"),
            results=(),
            stats=ScanStats(
                total_files=10,
                scanned_files=10,
                matched_files=0,
                skipped_files=0,
                errors=0,
                duration_seconds=0.0,
                total_matches=0,
            ),
            cancelled=False,
        )
        controller.restoreFromReport(report)

        # statusSummary 不应包含速度信息
        assert "速度" not in controller.statusSummary

    def test_restores_report_with_speed(
        self,
        controller: ScanController,
    ) -> None:
        """report.stats.speed > 0 时 summary 应附加速度信息。"""
        # duration=2.0，scanned=10 → speed=5.0 文件/s
        report = _make_scan_report(results=(), cancelled=False, duration=2.0)
        controller.restoreFromReport(report)

        # statusSummary 应包含速度信息
        assert "速度" in controller.statusSummary


class TestIncrementalContextAfterRestore:
    """``restoreFromReport`` 与 ``startIncrementalScan`` 协作测试。

    恢复后的 ``_last_report`` 应使 ``startIncrementalScan`` 启用增量模式
    （而非回退到全量扫描）。
    """

    def test_restore_then_incremental_uses_report(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """restoreFromReport 设置 _last_report 后，startIncrementalScan 应使用增量。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))

        # 先通过 restoreFromReport 设置 _last_report
        prev_report = _make_scan_report(results=(_make_scan_result(),))
        controller.restoreFromReport(prev_report)
        assert controller._last_report is prev_report

        # 创建 manifest 文件
        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        manifests_dir.mkdir(parents=True, exist_ok=True)
        (manifests_dir / "ws-restore.json").write_text(_make_manifest().to_json(), encoding="utf-8")

        # 调用 startIncrementalScan：_last_report 非 None + manifest 存在 → 启用增量
        controller.startIncrementalScan("ws-restore")

        assert len(stats_instances) == 1
        assert controller.scanState == "scanning"
        # _pending_prev_report 应被设置为恢复的 prev_report
        assert controller._pending_prev_report is prev_report
        # stats worker 应收到 incremental_manifest 参数
        assert stats_instances[0].kwargs.get("incremental_manifest") is not None


class TestIncrementalScanPauseCancel:
    """增量扫描流程中的暂停/取消测试。

    覆盖 ``togglePause`` / ``cancelScan`` 在有 scan worker（增量扫描进入 scan 阶段后）
    的分支，确保增量上下文下暂停/取消能正确传递到 worker。
    """

    def _start_incremental_to_scan_phase(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> tuple[FakeStatsWorker, FakeScanWorker]:
        """辅助：启动增量扫描并推进到 scan 阶段（stats 完成 → scan worker 创建）。"""
        stats_instances, scan_instances = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller._last_report = _make_scan_report()

        from fuscan.gui.controllers import scan_controller as sc_module

        manifests_dir = sc_module._MANIFESTS_DIR
        manifests_dir.mkdir(parents=True, exist_ok=True)
        (manifests_dir / "ws-pause.json").write_text(_make_manifest().to_json(), encoding="utf-8")

        controller.startIncrementalScan("ws-pause")
        stats_worker = stats_instances[0]
        # stats 完成 → 创建 scan worker
        stats_worker.emit_finished([_make_walk_result(tmp_path)])
        return stats_worker, scan_instances[0]

    def test_toggle_pause_pauses_both_workers(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """scan 阶段 togglePause 应同时 pause stats worker 与 scan worker。"""
        _stats_worker, scan_worker = self._start_incremental_to_scan_phase(controller, fake_workers, tmp_path)

        controller.togglePause()

        assert controller.isPaused is True
        # stats worker 已在 _on_stats_finished 中 cleanup，pause 不会调用
        # scan worker 应被 pause
        assert scan_worker.pause_called is True

    def test_toggle_pause_then_resume_both_workers(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """scan 阶段二次 togglePause 应 resume scan worker。"""
        _stats_worker, scan_worker = self._start_incremental_to_scan_phase(controller, fake_workers, tmp_path)

        controller.togglePause()  # 暂停
        controller.togglePause()  # 继续

        assert controller.isPaused is False
        assert scan_worker.resume_called is True

    def test_cancel_scan_cancels_scan_worker(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """scan 阶段 cancelScan 应 cancel scan worker。"""
        _stats_worker, scan_worker = self._start_incremental_to_scan_phase(controller, fake_workers, tmp_path)

        controller.cancelScan()

        assert scan_worker.cancel_called is True
        assert controller.statusText == "取消中..."


class TestCleanupCacheEdgeCase:
    """``cleanup`` 关闭缓存异常分支测试。"""

    def test_cleanup_handles_cache_close_error(
        self,
        controller: ScanController,
    ) -> None:
        """cache.close() 抛异常时应记录 warning 不抛异常。"""

        class _BadCache:
            def close(self) -> None:
                raise OSError("模拟关闭失败")

        controller._cache = _BadCache()  # type: ignore[bad-assignment]
        # 不应抛异常
        controller.cleanup()
        assert controller._cache is None


class TestResolveScanRootFallback:
    """``_resolve_scan_root`` 回退分支测试。

    当 ``_last_report`` 为 None 时回退到选中结果的父目录，
    此分支影响 ``replaceAllFilteredResults`` 的相对路径计算，
    也影响增量扫描上下文缺失时的回退行为。
    """

    def test_fallback_to_selected_result_parent(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """_last_report=None 但有选中结果时应回退到选中结果父目录。"""
        # _last_report 保持 None
        assert controller._last_report is None
        result = _make_scan_result(tmp_path / "sub" / "x.txt")
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)

        root = controller._resolve_scan_root()
        assert root == (tmp_path / "sub")

    def test_fallback_to_cwd_when_no_result(self, controller: ScanController) -> None:
        """_last_report=None 且无选中结果时应回退到 Path.cwd()。"""
        assert controller._last_report is None
        # 无选中结果
        root = controller._resolve_scan_root()
        assert root == Path.cwd()


class TestScanControllerPropertiesCoverage:
    """补充 Property getter 覆盖测试。

    这些 Property 在增量扫描上下文中也可能被 QML 读取（如扫描中读取 cancelling、
    canStartScan 等），确保各 getter 分支被覆盖。
    """

    def test_cancelling_property_default_false(self, controller: ScanController) -> None:
        """初始 cancelling 应为 False。"""
        assert controller.cancelling is False

    def test_can_start_scan_false_when_scanning(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """scanning 态 canStartScan 应为 False。"""
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))
        controller.startScan()
        assert controller.canStartScan is False

    def test_progress_zero_when_total_zero(self, controller: ScanController) -> None:
        """_progress_total=0 时 progress 应返回 0。"""
        controller._progress_total = 0
        controller._progress_scanned = 5
        controller._scan_done = False
        assert controller.progress == 0.0

    def test_progress_returns_value_when_scan_not_done(
        self,
        controller: ScanController,
    ) -> None:
        """_scan_done=False 且 total>0 时 progress 应按比例计算。"""
        controller._progress_total = 10
        controller._progress_scanned = 5
        controller._scan_done = False
        assert controller.progress == 50.0

    def test_error_count_property(self, controller: ScanController) -> None:
        """errorCount Property 应返回 _error_count。"""
        controller._error_count = 3
        assert controller.errorCount == 3

    def test_selected_drive_property(self, controller: ScanController) -> None:
        """selectedDrive Property 应返回 _selected_drive。"""
        controller.setSelectedDrive("E:")
        assert controller.selectedDrive == "E:"

    def test_folder_root_property(self, controller: ScanController) -> None:
        """folderRoot Property 应返回 _folder_root。"""
        controller.setFolderRoot("/some/path")
        assert controller.folderRoot == "/some/path"

    def test_set_task_override(self, controller: ScanController) -> None:
        """setTaskOverride 应写入 _task_overrides。"""
        controller.setTaskOverride("scan_archives", False)
        assert controller._task_overrides["scan_archives"] is False

    def test_rules_count_property(self, controller: ScanController) -> None:
        """rulesCount Property 应返回规则数。"""
        # 默认加载内置规则集
        assert controller.rulesCount > 0

    def test_rules_count_zero_when_no_ruleset(self, controller: ScanController) -> None:
        """ruleset=None 时 rulesCount 应为 0。"""
        # iter-137：通过全局 RulesController 清空规则集
        controller._rules_controller.setUseBuiltin(False)
        assert controller._rules_controller.ruleset is None
        # 刷新 ScanController 持有的 ruleset 快照
        controller._ruleset = controller._rules_controller.ruleset
        assert controller.rulesCount == 0

    def test_select_next_result(self, controller: ScanController, tmp_path: Path) -> None:
        """selectNextResult 应选中下一条结果。"""
        results = (
            _make_scan_result(tmp_path / "a.txt"),
            _make_scan_result(tmp_path / "b.txt"),
        )
        controller._result_model.set_results(results)
        controller.setSelectedResultIndex(0)
        controller.selectNextResult()
        assert controller.selectedResultIndex == 1

    def test_select_next_result_noop_at_end(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """selectNextResult 在末尾应被忽略。"""
        results = (_make_scan_result(tmp_path / "a.txt"),)
        controller._result_model.set_results(results)
        controller.setSelectedResultIndex(0)
        controller.selectNextResult()
        assert controller.selectedResultIndex == 0

    def test_select_prev_result(self, controller: ScanController, tmp_path: Path) -> None:
        """selectPrevResult 应选中上一条结果。"""
        results = (
            _make_scan_result(tmp_path / "a.txt"),
            _make_scan_result(tmp_path / "b.txt"),
        )
        controller._result_model.set_results(results)
        controller.setSelectedResultIndex(1)
        controller.selectPrevResult()
        assert controller.selectedResultIndex == 0

    def test_select_prev_result_noop_at_start(self, controller: ScanController) -> None:
        """selectPrevResult 在开头应被忽略。"""
        controller.setSelectedResultIndex(0)
        controller.selectPrevResult()
        assert controller.selectedResultIndex == 0

    def test_detail_file_size_with_selection(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """选中结果后 detailFileSize 应返回人类可读大小。"""
        result = _make_scan_result(tmp_path / "x.txt")
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)
        # _make_scan_result size=100 → "100 B"
        assert controller.detailFileSize == "100 B"

    def test_detail_file_size_empty_when_no_selection(self, controller: ScanController) -> None:
        """未选中结果时 detailFileSize 应为空字符串。"""
        assert controller.detailFileSize == ""

    def test_set_result_filter_rules_resets_out_of_range_selection(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """setResultFilterRules 过滤后选中索引越界应重置为 -1。"""
        h1 = RuleHit(rule_name="r1", severity=Severity.CRITICAL, detail="d1")
        h2 = RuleHit(rule_name="r2", severity=Severity.WARNING, detail="d2")
        results = (
            ScanResult(path=tmp_path / "a.txt", size=10, hits=(h1,)),
            ScanResult(path=tmp_path / "b.txt", size=10, hits=(h2,)),
        )
        controller._result_model.set_results(results)
        controller.setSelectedResultIndex(1)
        controller.setResultFilterRules(["r1"])
        # 过滤后只剩 1 条，索引 1 越界 → 重置为 -1
        assert controller.selectedResultIndex == -1


class TestStatsWorkerManifestProperty:
    """``FileStatsWorker.manifest`` 属性在 ``_scanner`` 未创建时返回 None（iter-124）。"""

    def test_manifest_none_before_run(self, tmp_path: Path) -> None:
        """构造 FileStatsWorker 但未调 run() 时 manifest 属性返回 None。"""
        from fuscan.gui.workers.stats_worker import FileStatsWorker
        from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet

        match = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="test")
        rule = Rule(name="test", match=match, severity=Severity.INFO)
        ruleset = RuleSet(rules=(rule,), version="1.0")
        worker = FileStatsWorker(
            ruleset=ruleset,
            roots=[tmp_path],
            scan_extensions=("txt",),
        )
        # _scanner 在 run() 中创建，构造后尚未创建
        assert worker.manifest is None
        worker.deleteLater()


class TestInvalidateManifest:
    """``invalidate_manifest`` 删除 manifest 文件测试（iter-136）。

    规则变更后调用此方法清除 manifest，使下次增量扫描回退为全量扫描，
    确保新规则被实际执行。
    """

    def test_invalidate_deletes_manifest_file(
        self,
        controller: ScanController,
        tmp_path: Path,
    ) -> None:
        """invalidate_manifest 应删除已存在的 manifest 文件。"""
        from fuscan.gui.controllers import scan_controller as sc_module

        ws_id = "ws-invalidate"
        manifest = IncrementalManifest(root=tmp_path, fingerprints={})
        controller._save_manifest(ws_id, manifest)
        manifest_file = sc_module._MANIFESTS_DIR / f"{ws_id}.json"
        assert manifest_file.exists()

        controller.invalidate_manifest(ws_id)

        assert not manifest_file.exists()

    def test_invalidate_noop_when_no_manifest(
        self,
        controller: ScanController,
    ) -> None:
        """manifest 文件不存在时 invalidate_manifest 不报错。"""
        from fuscan.gui.controllers import scan_controller as sc_module

        ws_id = "ws-no-manifest-to-invalidate"
        manifest_file = sc_module._MANIFESTS_DIR / f"{ws_id}.json"
        assert not manifest_file.exists()

        # 不应抛异常
        controller.invalidate_manifest(ws_id)
        assert not manifest_file.exists()

    def test_invalidate_forces_fallback_to_full_scan(
        self,
        controller: ScanController,
        fake_workers: tuple[list[FakeStatsWorker], list[FakeScanWorker]],
        tmp_path: Path,
    ) -> None:
        """规则变更后 invalidate_manifest 使增量扫描回退为全量扫描。"""
        stats_instances, _ = fake_workers
        controller.setScanModeIndex(1)
        controller.setFolderRoot(str(tmp_path))

        # 先保存 manifest 和 _last_report
        manifest = IncrementalManifest(root=tmp_path, fingerprints={})
        controller._save_manifest("ws-rule-change", manifest)
        old_result = _make_scan_result(tmp_path / "old.txt", hits=1)
        controller._last_report = _make_scan_report(results=(old_result,))

        # 模拟规则变更：invalidate_manifest
        controller.invalidate_manifest("ws-rule-change")

        # startIncrementalScan 应因 manifest 不存在而回退为全量
        controller.startIncrementalScan("ws-rule-change")
        assert controller.scanState == "scanning"
        # 应创建了 stats worker（全量扫描模式，无 incremental_manifest 参数）
        assert len(stats_instances) == 1
        assert stats_instances[0].kwargs.get("incremental_manifest") is None
