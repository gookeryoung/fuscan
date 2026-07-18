"""扫描路径历史管理。

维护去重、最近优先、限量的路径历史列表，同步 ``path_combo``（下拉选择）
与 ``history_list``（历史列表）两个控件。

抽离自 :class:`fuscan.gui.main_window.MainWindow`，承载独立的路径历史状态，
避免 :class:`MainWindow` 同时维护 ``_scan_history`` list 与两个控件的内容漂移。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from PySide2.QtWidgets import QComboBox, QListWidgetItem
except ImportError:  # pragma: no cover
    from PySide6.QtWidgets import QComboBox, QListWidgetItem  # pyrefly: ignore [missing-import]

from fuscan.config import MAX_HISTORY

if TYPE_CHECKING:
    from PySide2.QtWidgets import QListWidget

__all__ = ["ScanPathHistory"]


class ScanPathHistory:
    """扫描路径历史：去重、最近优先、限量、双控件同步。

    内部维护单一数据源 ``_paths``，``add`` / ``load_from_config`` 后通过
    ``_sync_combo`` / ``_sync_list`` 推送到两个控件，避免原实现中
    path_combo 与 history_list 内容漂移（原实现分别维护两份冗余列表）。

    线程模型：仅在主线程使用（QWidget 控件约束），无并发保护。
    """

    def __init__(self, path_combo: QComboBox, history_list: QListWidget) -> None:
        """绑定两个控件并初始化空历史。

        :param path_combo: 扫描路径下拉选择控件，历史顶项设为当前选中。
        :param history_list: 扫描历史列表控件，每项显示路径文本 + tooltip。
        """
        self._path_combo = path_combo
        self._history_list = history_list
        self._paths: list[str] = []

    def add(self, path_str: str) -> None:
        """添加路径到历史顶部（去重 + 最近优先 + 限量 + 同步控件）。

        :param path_str: 待添加的路径字符串。
        """
        if path_str in self._paths:
            self._paths.remove(path_str)
        self._paths.insert(0, path_str)
        while len(self._paths) > MAX_HISTORY:
            self._paths.pop()
        self._sync_combo()
        self._sync_list()

    def refresh_list(self) -> None:
        """强制刷新 history_list（供外部直接修改后调用，正常流程无需调用）。"""
        self._sync_list()

    def load_from_config(self, paths: list[str]) -> None:
        """从配置加载历史路径（启动时由 :meth:`MainWindow._apply_config` 调用）。

        :param paths: 配置中保存的路径列表，按历史顺序（最近优先）。
        """
        self._paths = list(paths)
        self._sync_combo()
        self._sync_list()

    def get_paths(self) -> list[str]:
        """返回当前历史路径列表的副本（用于保存到配置）。"""
        return list(self._paths)

    def _sync_combo(self) -> None:
        """同步 path_combo 内容到 _paths（blockSignals 避免触发 currentIndexChanged）。"""
        self._path_combo.blockSignals(True)
        self._path_combo.clear()
        for path_str in self._paths:
            self._path_combo.addItem(path_str)  # pyrefly: ignore [missing-argument]
        if self._paths:
            self._path_combo.setCurrentIndex(0)
        self._path_combo.blockSignals(False)

    def _sync_list(self) -> None:
        """同步 history_list 内容到 _paths（每项附 tooltip 显示完整路径）。"""
        self._history_list.clear()
        for path_str in self._paths:
            item = QListWidgetItem(path_str)
            item.setToolTip(path_str)
            self._history_list.addItem(item)  # pyrefly: ignore [missing-argument]
