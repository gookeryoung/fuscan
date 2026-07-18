"""``fuscan.gui.scan_path_history`` 单元测试。

覆盖去重、最近优先、限量、双控件同步、配置加载/保存等核心行为。
通过 ``QApplication`` 实例化真实 ``QComboBox`` / ``QListWidget`` 控件，
验证 ``ScanPathHistory`` 与 Qt 控件的集成正确性。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    try:
        from PySide2.QtWidgets import QApplication, QComboBox, QListWidget
    except ImportError:  # pragma: no cover
        from PySide6.QtWidgets import QApplication, QComboBox, QListWidget  # pyrefly: ignore [missing-import]

    from fuscan.config import MAX_HISTORY
    from fuscan.gui.scan_path_history import ScanPathHistory

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过 GUI 测试", allow_module_level=True)


@pytest.fixture
def qapp() -> QApplication:  # type: ignore[misc]
    """模块级 QApplication fixture。"""
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def history(qapp: QApplication) -> ScanPathHistory:  # type: ignore[misc]
    """构造绑定到真实控件的 ScanPathHistory 实例。"""
    combo = QComboBox()
    list_widget = QListWidget()
    return ScanPathHistory(combo, list_widget)


class TestAdd:
    """``ScanPathHistory.add`` 行为测试。"""

    def test_add_single_path_syncs_both_widgets(self, history: ScanPathHistory) -> None:
        """添加单个路径应同步 path_combo 与 history_list。"""
        history.add("/tmp/scan_dir")

        assert history._path_combo.count() == 1
        assert history._path_combo.itemText(0) == "/tmp/scan_dir"
        assert history._history_list.count() == 1
        assert history._history_list.item(0).text() == "/tmp/scan_dir"

    def test_add_deduplicates_existing_path(self, history: ScanPathHistory) -> None:
        """重复添加相同路径应去重，仅保留一项并置于顶部。"""
        history.add("/path/a")
        history.add("/path/b")
        history.add("/path/a")

        assert history._path_combo.count() == 2
        assert history._path_combo.itemText(0) == "/path/a"
        assert history._path_combo.itemText(1) == "/path/b"
        assert history._history_list.count() == 2

    def test_add_moves_existing_path_to_top(self, history: ScanPathHistory) -> None:
        """重复添加已存在路径应将其移至顶部（最近优先）。"""
        history.add("/first")
        history.add("/second")
        history.add("/third")
        history.add("/first")

        assert history._path_combo.itemText(0) == "/first"
        assert history._path_combo.itemText(1) == "/third"
        assert history._path_combo.itemText(2) == "/second"

    def test_add_enforces_max_history_limit(self, history: ScanPathHistory) -> None:
        """超过 ``MAX_HISTORY`` 上限应丢弃最旧项。"""
        for i in range(MAX_HISTORY + 5):
            history.add(f"/path/{i}")

        assert history._path_combo.count() == MAX_HISTORY
        assert history._history_list.count() == MAX_HISTORY
        # 最近添加的在最前
        assert history._path_combo.itemText(0) == f"/path/{MAX_HISTORY + 4}"

    def test_add_sets_current_index_to_top(self, history: ScanPathHistory) -> None:
        """添加后 path_combo 当前选中项应为最新路径（顶部）。"""
        history.add("/old")
        history.add("/new")

        assert history._path_combo.currentIndex() == 0
        assert history._path_combo.currentText() == "/new"


class TestLoadFromConfig:
    """``ScanPathHistory.load_from_config`` 行为测试。"""

    def test_load_from_config_populates_widgets(self, history: ScanPathHistory) -> None:
        """从配置加载路径应填充 path_combo 与 history_list。"""
        paths = ["/dir1", "/dir2", "/dir3"]
        history.load_from_config(paths)

        assert history._path_combo.count() == 3
        assert history._path_combo.itemText(0) == "/dir1"
        assert history._history_list.count() == 3
        assert history._history_list.item(2).text() == "/dir3"

    def test_load_from_config_replaces_existing(self, history: ScanPathHistory) -> None:
        """加载新配置应覆盖已有内容（不追加）。"""
        history.add("/old_path")
        history.load_from_config(["/new1", "/new2"])

        assert history._path_combo.count() == 2
        assert history._path_combo.itemText(0) == "/new1"
        assert "/old_path" not in [
            history._path_combo.itemText(i) for i in range(history._path_combo.count())
        ]

    def test_load_from_config_empty_list_clears_widgets(self, history: ScanPathHistory) -> None:
        """加载空列表应清空两个控件。"""
        history.add("/some_path")
        history.load_from_config([])

        assert history._path_combo.count() == 0
        assert history._history_list.count() == 0


class TestGetPaths:
    """``ScanPathHistory.get_paths`` 行为测试。"""

    def test_get_paths_returns_copy(self, history: ScanPathHistory) -> None:
        """``get_paths`` 应返回副本，修改返回值不影响内部状态。"""
        history.add("/path1")
        paths = history.get_paths()
        paths.append("/injected")

        assert history.get_paths() == ["/path1"]

    def test_get_paths_reflects_add_order(self, history: ScanPathHistory) -> None:
        """``get_paths`` 应反映最近优先顺序。"""
        history.add("/first")
        history.add("/second")
        history.add("/first")

        assert history.get_paths() == ["/first", "/second"]


class TestRefreshList:
    """``ScanPathHistory.refresh_list`` 行为测试。"""

    def test_refresh_list_rebuilds_history_widget(self, history: ScanPathHistory) -> None:
        """``refresh_list`` 应重建 history_list 内容（含 tooltip）。"""
        history.add("/with_tooltip")
        # 模拟外部修改 history_list 后强制刷新
        history._history_list.clear()
        assert history._history_list.count() == 0

        history.refresh_list()

        assert history._history_list.count() == 1
        item = history._history_list.item(0)
        assert item.text() == "/with_tooltip"
        assert item.toolTip() == "/with_tooltip"


class TestSignalBlocking:
    """``ScanPathHistory._sync_combo`` 信号阻塞行为测试。"""

    def test_sync_combo_does_not_emit_current_index_changed(
        self, qapp: QApplication  # type: ignore[misc]
    ) -> None:
        """``_sync_combo`` 应阻塞 ``currentIndexChanged`` 信号避免循环触发。"""
        combo = QComboBox()
        list_widget = QListWidget()
        history = ScanPathHistory(combo, list_widget)

        emitted: list[int] = []
        combo.currentIndexChanged.connect(lambda idx: emitted.append(idx))

        history.add("/path1")
        history.add("/path2")

        # add 内部 _sync_combo 应阻塞信号，emitted 应为空
        assert emitted == []
