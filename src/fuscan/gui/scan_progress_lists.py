"""扫描中页跳过目录与命中文件列表的增量更新器。

将扫描中页两个 ``QListWidget`` 的增量 append / 全量重建算法与 0.5 秒节流
逻辑从 ``main_window.py`` 拆分到独立的 :class:`ScanListUpdater` 类，
使主窗口 ``_on_scan_progress`` 仅负责进度条、状态栏文本与列表更新的协调，
列表填充算法内聚到本模块。

公共 API：

- :class:`ScanListUpdater`：增量更新器，主窗口持有实例

增量算法：

若新列表是旧列表的前缀扩展（旧列表是新列表前缀），只 append 新增尾部；
否则（滚动截断或内容变化）全量重建用 ``addItems`` 批量添加。
"""

from __future__ import annotations

import time

try:
    from PySide2.QtWidgets import QListWidget
except ImportError:  # pragma: no cover
    from PySide6.QtWidgets import QListWidget  # pyrefly: ignore [missing-import]

__all__ = ["ScanListUpdater"]


class ScanListUpdater:
    """扫描中页跳过目录与命中文件列表的增量更新器。

    主窗口持有实例，在 ``_on_scan_progress`` 中调用 :meth:`try_update`，
    由本类负责 0.5 秒节流与两个列表的同步刷新。节流期间所有更新被丢弃，
    避免高频回调（每次扫描一个文件触发一次）阻塞主线程。

    :ivar _skipped_list: 跳过目录 ``QListWidget``
    :ivar _matched_files_list: 命中文件 ``QListWidget``，项格式 ``"路径 → 规则名"``
    :ivar _last_skipped_dirs: 上次已渲染的跳过目录元组（增量 append 基线）
    :ivar _last_matched_files: 上次已渲染的命中文件元组
    :ivar _last_update_time: 上次实际更新的 ``time.perf_counter()`` 时间戳；
        初始 ``-1.0`` 确保首次调用不被节流（新进程 ``perf_counter`` 可能返回小值）
    """

    def __init__(self, skipped_list: QListWidget, matched_files_list: QListWidget) -> None:
        """初始化更新器：绑定两个列表控件并重置增量状态。

        :param skipped_list: 跳过目录列表控件（由主窗口 ``setupUi`` 创建）
        :param matched_files_list: 命中文件列表控件（同上）
        """
        self._skipped_list = skipped_list
        self._matched_files_list = matched_files_list
        self._last_skipped_dirs: tuple[str, ...] = ()
        self._last_matched_files: tuple[tuple[str, str], ...] = ()
        self._last_update_time: float = -1.0

    def reset(self) -> None:
        """重置更新器状态：清空两个列表与节流时间戳。

        在新扫描启动时调用，避免上次扫描的快照干扰本次增量对比。
        """
        self._skipped_list.clear()
        self._matched_files_list.clear()
        self._last_skipped_dirs = ()
        self._last_matched_files = ()
        self._last_update_time = -1.0

    def try_update(
        self,
        skipped_dirs: tuple[str, ...],
        matched_files: tuple[tuple[str, str], ...],
        throttle_seconds: float = 0.5,
    ) -> bool:
        """按 0.5 秒节流增量更新两个列表。

        节流期间返回 ``False`` 且不修改任何状态；节流窗口到期时调用两个
        ``_update_*`` 方法实际刷新列表，并返回 ``True``。调用方据此判断
        是否需要触发依赖列表的副作用（如分类统计面板刷新）。

        :param skipped_dirs: 当前跳过目录全量元组
        :param matched_files: 当前命中文件全量元组，元素为 ``(路径, 规则名)``
        :param throttle_seconds: 节流窗口，默认 0.5 秒
        :returns: ``True`` 表示本次实际刷新了列表；``False`` 表示被节流跳过
        """
        now = time.perf_counter()
        if now - self._last_update_time < throttle_seconds:
            return False
        self._last_update_time = now
        self._update_skipped_list(skipped_dirs)
        self._update_matched_files_list(matched_files)
        return True

    def _update_skipped_list(self, new_dirs: tuple[str, ...]) -> None:
        """增量更新跳过目录列表。

        若新列表是旧列表的扩展（旧列表是新列表前缀），只 append 新增尾部条目；
        否则（滚动截断或内容变化）全量重建用 ``addItems`` 批量添加。
        """
        old_dirs = self._last_skipped_dirs
        if not new_dirs:
            return
        if new_dirs == old_dirs:
            return
        # 关闭更新以避免逐项 addItems 触发重绘，批量完成后统一刷新
        self._skipped_list.setUpdatesEnabled(False)
        try:
            if len(new_dirs) > len(old_dirs) and new_dirs[: len(old_dirs)] == old_dirs:
                # 增量 append：旧列表是新列表前缀，只添加新增尾部
                self._skipped_list.addItems(new_dirs[len(old_dirs) :])
            else:
                # 全量重建（滚动截断或内容变化）
                self._skipped_list.clear()
                self._skipped_list.addItems(new_dirs)
        finally:
            self._skipped_list.setUpdatesEnabled(True)
        self._skipped_list.scrollToBottom()
        self._last_skipped_dirs = new_dirs

    def _update_matched_files_list(self, new_files: tuple[tuple[str, str], ...]) -> None:
        """增量更新命中文件列表，逻辑同 :meth:`_update_skipped_list`。

        列表项格式为 ``"路径 → 规则名"``。
        """
        old_files = self._last_matched_files
        if not new_files:
            return
        if new_files == old_files:
            return
        # 关闭更新以避免逐项 addItems 触发重绘，批量完成后统一刷新
        self._matched_files_list.setUpdatesEnabled(False)
        try:
            if len(new_files) > len(old_files) and new_files[: len(old_files)] == old_files:
                # 增量 append：格式 "路径 → 规则名"
                items = [f"{fp} → {rn}" for fp, rn in new_files[len(old_files) :]]
                self._matched_files_list.addItems(items)
            else:
                # 全量重建
                self._matched_files_list.clear()
                items = [f"{fp} → {rn}" for fp, rn in new_files]
                self._matched_files_list.addItems(items)
        finally:
            self._matched_files_list.setUpdatesEnabled(True)
        self._matched_files_list.scrollToBottom()
        self._last_matched_files = new_files
