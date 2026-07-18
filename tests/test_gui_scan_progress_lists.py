"""``fuscan.gui.scan_progress_lists`` 单元测试。

``ScanListUpdater`` 的节流、增量 append、全量重建、reset 行为测试。
需要 QApplication 环境（``QListWidget``）。
"""

from __future__ import annotations

import os
import time

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    try:
        from PySide2.QtWidgets import QApplication, QListWidget
    except ImportError:  # pragma: no cover
        from PySide6.QtWidgets import (  # pyrefly: ignore [missing-import]
            QApplication,
            QListWidget,
        )

    from fuscan.gui.scan_progress_lists import ScanListUpdater

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过 GUI 测试", allow_module_level=True)


@pytest.fixture(scope="module")
def qapp() -> QApplication:  # type: ignore[misc]
    """模块级 QApplication fixture。"""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def updater(qapp: QApplication) -> ScanListUpdater:
    """创建绑定两个 ``QListWidget`` 的 ``ScanListUpdater`` 实例。"""
    skipped = QListWidget()
    matched = QListWidget()
    return ScanListUpdater(skipped, matched)


def _items_text(list_widget: QListWidget) -> list[str]:
    """获取 ``QListWidget`` 中所有项的文本。"""
    return [list_widget.item(i).text() for i in range(list_widget.count())]


class TestScanListUpdaterReset:
    """``reset`` 方法行为测试。"""

    def test_reset_clears_both_lists(self, updater: ScanListUpdater) -> None:
        """``reset`` 应清空两个列表。"""
        updater._skipped_list.addItems(["a", "b"])
        updater._matched_files_list.addItems(["x → r1"])
        assert updater._skipped_list.count() == 2
        assert updater._matched_files_list.count() == 1

        updater.reset()

        assert updater._skipped_list.count() == 0
        assert updater._matched_files_list.count() == 0

    def test_reset_clears_internal_state(self, updater: ScanListUpdater) -> None:
        """``reset`` 应清空增量更新内部快照，使下次 ``try_update`` 视为全新数据。"""
        updater._last_skipped_dirs = ("/old",)
        updater._last_matched_files = (("/old", "rule"),)
        updater._last_update_time = 100.0

        updater.reset()

        assert updater._last_skipped_dirs == ()
        assert updater._last_matched_files == ()
        assert updater._last_update_time == -1.0

    def test_reset_allows_first_update_without_throttle(
        self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``reset`` 后首次 ``try_update`` 不应被节流（``_last_update_time`` 重置为 -1.0）。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])
        # 模拟之前已更新过一次（_last_update_time 非 -1.0）
        updater._last_update_time = 99.9

        updater.reset()

        # 距离 reset 前的时间戳 0.1 秒，但 reset 已重置 _last_update_time=-1.0，
        # 首次 try_update 不应被节流
        t[0] = 100.0
        result = updater.try_update(("/dir1",), ())
        assert result is True
        assert updater._skipped_list.count() == 1


class TestScanListUpdaterThrottle:
    """``try_update`` 节流行为测试。"""

    def test_first_call_not_throttled(self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch) -> None:
        """首次调用 ``try_update`` 应直接刷新列表。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        result = updater.try_update(("/dir1",), ())

        assert result is True
        assert updater._skipped_list.count() == 1

    def test_call_within_throttle_window_skipped(
        self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """节流窗口内（<0.5 秒）的调用应被跳过，不刷新列表。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        updater.try_update(("/dir1",), ())
        assert updater._skipped_list.count() == 1

        # 推进 0.1 秒（节流窗口内），新增一个目录但应被跳过
        t[0] = 100.1
        result = updater.try_update(("/dir1", "/dir2"), ())

        assert result is False
        assert updater._skipped_list.count() == 1  # 仍为 1

    def test_call_after_throttle_window_refreshes(
        self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """节流窗口到期（>=0.5 秒）的调用应刷新列表。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        updater.try_update(("/dir1",), ())

        t[0] = 100.5
        result = updater.try_update(("/dir1", "/dir2"), ())

        assert result is True
        assert updater._skipped_list.count() == 2

    def test_custom_throttle_seconds(self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch) -> None:
        """``throttle_seconds`` 参数应允许调用方自定义节流窗口。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        updater.try_update(("/dir1",), ())

        # 推进 0.2 秒，默认节流 0.5 秒应跳过；但自定义节流 0.1 秒应放行
        t[0] = 100.2
        result = updater.try_update(("/dir1", "/dir2"), (), throttle_seconds=0.1)

        assert result is True
        assert updater._skipped_list.count() == 2


class TestScanListUpdaterSkippedDirs:
    """``ScanListUpdater`` 跳过目录列表增量更新算法测试。"""

    def test_incremental_append_when_old_is_prefix(
        self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """旧列表是新列表前缀时应只 append 新增尾部，不 clear 重建。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        updater.try_update(("/dir1", "/dir2"), ())
        assert _items_text(updater._skipped_list) == ["/dir1", "/dir2"]

        # 推进 0.6 秒，新增一个目录（旧列表是新列表前缀）
        t[0] = 100.6
        updater.try_update(("/dir1", "/dir2", "/dir3"), ())

        # 前两项应保持不变（未 clear 重建）
        assert _items_text(updater._skipped_list) == ["/dir1", "/dir2", "/dir3"]
        # 增量 append：仅添加 1 项（而非 clear + 3 项 addItems）
        assert updater._skipped_list.count() == 3

    def test_full_rebuild_on_truncation(self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch) -> None:
        """滚动截断（新列表非旧列表前缀）时应全量重建。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        updater.try_update(("/dir1", "/dir2"), ())

        # 推进 0.6 秒，模拟滚动截断：旧前缀被丢弃，新列表完全不同
        t[0] = 100.6
        updater.try_update(("/dir3", "/dir4"), ())

        assert _items_text(updater._skipped_list) == ["/dir3", "/dir4"]

    def test_same_input_no_update(self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch) -> None:
        """新旧列表相同时不应调用 ``addItems``，但 ``try_update`` 仍返回 ``True``。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        updater.try_update(("/dir1",), ())
        initial_count = updater._skipped_list.count()

        t[0] = 100.6
        updater.try_update(("/dir1",), ())

        assert updater._skipped_list.count() == initial_count
        assert _items_text(updater._skipped_list) == ["/dir1"]

    def test_empty_input_no_update(self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch) -> None:
        """空 ``skipped_dirs`` 元组应直接返回，不修改列表。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        # 预填充一些数据
        updater._skipped_list.addItems(["existing"])
        updater._last_skipped_dirs = ("/existing",)

        result = updater.try_update((), ())

        # try_update 返回 True（节流放行），但内部 _update_skipped_list 因 new_dirs 为空跳过
        assert result is True
        assert updater._skipped_list.count() == 1  # 原有项保留


class TestScanListUpdaterMatchedFiles:
    """``ScanListUpdater`` 命中文件列表增量更新与格式化测试。"""

    def test_matched_files_format_path_arrow_rule(
        self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """命中文件列表项格式应为 ``"路径 → 规则名"``。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        updater.try_update(
            (),
            (("/proj/a.py", "规则A"), ("/proj/b.py", "规则B")),
        )

        assert _items_text(updater._matched_files_list) == [
            "/proj/a.py → 规则A",
            "/proj/b.py → 规则B",
        ]

    def test_matched_files_incremental_append(self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch) -> None:
        """命中文件列表增量 append：只添加新增项。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        updater.try_update((), (("/proj/a.py", "规则A"),))
        assert updater._matched_files_list.count() == 1

        t[0] = 100.6
        updater.try_update(
            (),
            (("/proj/a.py", "规则A"), ("/proj/b.py", "规则B")),
        )

        assert updater._matched_files_list.count() == 2
        assert updater._matched_files_list.item(0).text() == "/proj/a.py → 规则A"
        assert updater._matched_files_list.item(1).text() == "/proj/b.py → 规则B"

    def test_matched_files_full_rebuild_on_change(
        self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """命中文件列表内容变化时应全量重建。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        updater.try_update((), (("/proj/a.py", "规则A"),))

        t[0] = 100.6
        updater.try_update((), (("/proj/c.py", "规则C"),))

        assert updater._matched_files_list.count() == 1
        assert updater._matched_files_list.item(0).text() == "/proj/c.py → 规则C"


class TestScanListUpdaterCombinedUpdate:
    """``ScanListUpdater`` 同时更新两个列表的协同行为测试。"""

    def test_both_lists_updated_together(self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch) -> None:
        """``try_update`` 应同时刷新两个列表。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        result = updater.try_update(
            ("/dir1",),
            (("/file1.py", "规则1"),),
        )

        assert result is True
        assert updater._skipped_list.count() == 1
        assert updater._matched_files_list.count() == 1

    def test_throttle_affects_both_lists(self, updater: ScanListUpdater, monkeypatch: pytest.MonkeyPatch) -> None:
        """节流期间两个列表都不应被刷新。"""
        t = [100.0]
        monkeypatch.setattr(time, "perf_counter", lambda: t[0])

        updater.try_update(("/dir1",), (("/file1.py", "规则1"),))

        # 推进 0.1 秒（节流窗口内），两个列表都有新数据但都应被跳过
        t[0] = 100.1
        result = updater.try_update(
            ("/dir1", "/dir2"),
            (("/file1.py", "规则1"), ("/file2.py", "规则2")),
        )

        assert result is False
        assert updater._skipped_list.count() == 1
        assert updater._matched_files_list.count() == 1
