"""pytest 共享 fixture。"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


class _SyncDetailSignal:
    """同步版 ``DetailWorker.done`` 信号：``emit(model, generation)`` 直接回调。"""

    def __init__(self) -> None:
        self._callbacks: list[Any] = []

    def connect(self, cb: Any) -> None:
        self._callbacks.append(cb)

    def disconnect(self, cb: Any) -> None:
        try:
            self._callbacks.remove(cb)
        except ValueError as exc:
            raise RuntimeError from exc

    def emit(self, model: Any, generation: int) -> None:
        for cb in list(self._callbacks):
            cb(model, generation)


class _SyncDetailWorker:
    """同步替身 :class:`DetailWorker`，避免测试环境真实 QThread 启动崩溃。

    Windows 上 PySide2 真实 ``QThread.start()`` 会触发
    ``STATUS_STACK_BUFFER_OVERRUN`` 崩溃。GUI 测试默认用本替身：``start()``
    同步调用 :func:`build_detail_hits_full` 后立即经 ``done`` 信号回传，
    行为等价异步补齐但不启线程。需要精确断言 worker 生命周期的测试可用
    专门的 fake fixture 再次覆盖。
    """

    def __init__(self, result: Any, generation: int) -> None:
        self._result = result
        self._generation = generation
        self.done = _SyncDetailSignal()
        self._running = False

    def start(self) -> None:
        from fuscan.gui.controllers._result_detail import build_detail_hits_full

        self.done.emit(build_detail_hits_full(self._result), self._generation)

    def quit(self) -> None:
        """同步替身无事件循环，quit 无操作。"""

    def wait(self, _msecs: int = 0) -> bool:
        return True

    def terminate(self) -> None:
        """同步替身无线程，terminate 无操作。"""

    def deleteLater(self) -> None:
        """模拟 Qt deleteLater。"""

    def isRunning(self) -> bool:
        return self._running


@pytest.fixture(autouse=True)
def _sync_detail_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """全局将 ScanController 中的 DetailWorker 替换为同步替身。

    避免任意 GUI 测试选中结果时启动真实 QThread 导致 Windows 崩溃。
    scan_controller 模块未导入（非 GUI 测试）时静默跳过。
    """
    try:
        import fuscan.gui.controllers.scan_controller as scan_controller_mod
    except ImportError:
        return
    monkeypatch.setattr(scan_controller_mod, "DetailWorker", _SyncDetailWorker)


@pytest.fixture()
def tmp_scan_root(tmp_path: Path) -> Path:
    """创建一个临时扫描根目录，含若干示例文件。"""
    root = tmp_path / "scan_root"
    root.mkdir()
    return root


@pytest.fixture()
def sample_text_file(tmp_scan_root: Path) -> Path:
    """创建一个含示例文本的 .txt 文件。"""
    path = tmp_scan_root / "sample.txt"
    path.write_text("这是一份测试文档，包含敏感词: SECRET-12345。\n", encoding="utf-8")
    return path


@pytest.fixture()
def chdir_tmp(tmp_path: Path) -> Iterator[Path]:
    """临时切换工作目录，避免影响真实文件系统。"""
    original = Path.cwd()
    try:
        sys.path.insert(0, str(tmp_path))
        yield tmp_path
    finally:
        sys.path.remove(str(tmp_path))
        sys.chdir(original)  # pyrefly: ignore [missing-attribute]
