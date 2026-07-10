"""系统托盘驻守应用。

集成文件监控、增量扫描与系统托盘，实现长期驻守的动态扫描：

- 系统托盘图标（QSystemTrayIcon）
- 右键菜单：显示窗口、启动/停止监控、立即扫描、退出
- 文件监控触发增量扫描（仅扫描新增/修改文件）
- 命中规则时托盘通知
- 扫描状态持久化

用法：

.. code-block:: python

    app = TrayApp(ruleset, watch_paths=[Path("D:/data")])
    app.start()  # 进入 Qt 事件循环
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide2.QtCore import QObject, QTimer, Signal
from PySide2.QtGui import QIcon
from PySide2.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon

from uniscan.rules.model import RuleSet
from uniscan.scanner import ScanReport
from uniscan.watcher.ignore_dirs import default_ignore_dirs
from uniscan.watcher.incremental import IncrementalScanner
from uniscan.watcher.monitor import FileEvent, FileEventType, FileMonitor, MonitorConfig

__all__ = ["TrayApp"]

logger = logging.getLogger(__name__)


class TrayApp(QObject):
    """托盘驻守应用。

    信号：

    - ``scan_completed``：扫描完成，携带 ScanReport
    - ``file_hit``：发现命中文件，携带路径与规则数
    """

    scan_completed = Signal(object)
    file_hit = Signal(str, int)

    def __init__(
        self,
        ruleset: RuleSet,
        watch_paths: Optional[List[Path]] = None,
        state_file: Optional[Path] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._ruleset = ruleset
        self._watch_paths = watch_paths or []
        self._state_file = state_file

        ignore_dirs = list(default_ignore_dirs())
        ignore_dirs.extend(ruleset.ignore_dirs)

        self._monitor_config = MonitorConfig(
            watch_paths=list(self._watch_paths),
            ignore_dirs=ignore_dirs,
            ignore_extensions=list(ruleset.ignore_extensions),
        )
        self._monitor: Optional[FileMonitor] = None
        self._scanner = IncrementalScanner(ruleset=ruleset)

        self._tray: Optional[QSystemTrayIcon] = None
        self._tray_menu: Optional[QMenu] = None
        self._main_window = None
        self._scan_worker = None

        # 增量扫描队列与定时器（批量处理文件事件，避免频繁扫描）
        self._pending_paths: List[Path] = []
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(2000)  # 2 秒内的事件批量处理
        self._scan_timer.timeout.connect(self._flush_pending_scans)

        # 加载持久化状态
        if self._state_file is not None:
            self._scanner.load_state(self._state_file)

    @property
    def is_monitoring(self) -> bool:
        """是否正在监控。"""
        return self._monitor is not None and self._monitor.is_running

    @property
    def tracked_count(self) -> int:
        """已跟踪文件数。"""
        return self._scanner.tracked_count

    def start(self, show_window: bool = True) -> int:
        """启动托盘应用并进入事件循环。

        :param show_window: 是否显示主窗口
        :return: 退出码
        """
        app = QApplication.instance() or QApplication([])
        app.setApplicationName("uniscan")
        app.setQuitOnLastWindowClosed(False)  # 关闭窗口不退出，驻留托盘

        self._init_tray()
        self._init_main_window(show_window)

        if self._watch_paths:
            self.start_monitoring()

        return app.exec_()

    def _init_tray(self) -> None:
        """初始化系统托盘。"""
        # 使用内置图标（无资源文件时）
        icon = QIcon.fromTheme("document-search", QIcon())
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("uniscan 文件扫描器")

        self._tray_menu = QMenu()

        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self._show_main_window)
        self._tray_menu.addAction(show_action)

        self._monitor_action = QAction("启动监控", self)
        self._monitor_action.triggered.connect(self._toggle_monitoring)
        self._tray_menu.addAction(self._monitor_action)

        scan_now_action = QAction("立即全量扫描", self)
        scan_now_action.triggered.connect(self._full_scan)
        self._tray_menu.addAction(scan_now_action)

        self._tray_menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit)
        self._tray_menu.addAction(quit_action)

        self._tray.setContextMenu(self._tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _init_main_window(self, show: bool) -> None:
        """初始化主窗口（复用 MainWindow）。"""
        from uniscan.gui.main_window import MainWindow

        self._main_window = MainWindow()
        self._main_window._ruleset = self._ruleset
        if self._ruleset is not None:
            self._main_window._refresh_rules_tree()
        if show:
            self._main_window.show()
        else:
            # MainWindow._apply_config 可能调用 showMaximized，需显式隐藏
            self._main_window.hide()

    def start_monitoring(self) -> None:
        """启动文件监控。"""
        if self._monitor is not None and self._monitor.is_running:
            return
        self._monitor = FileMonitor(self._monitor_config)
        self._monitor.start(self._on_file_event)
        if self._tray is not None:
            self._monitor_action.setText("停止监控")
        logger.info("文件监控已启动")

    def stop_monitoring(self) -> None:
        """停止文件监控。"""
        if self._monitor is None:
            return
        self._monitor.stop()
        self._monitor = None
        if self._tray is not None:
            self._monitor_action.setText("启动监控")
        logger.info("文件监控已停止")

    def _toggle_monitoring(self) -> None:
        """切换监控状态。"""
        if self.is_monitoring:
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def _on_file_event(self, event: FileEvent) -> None:
        """文件事件回调：加入待扫描队列。"""
        if event.is_dir:
            return
        if event.event_type == FileEventType.DELETED:
            self._scanner.remove_path(event.path)
            return
        # CREATED 或 MODIFIED：加入队列，由定时器批量扫描
        if event.path not in self._pending_paths:
            self._pending_paths.append(event.path)
        self._scan_timer.start()

    def _flush_pending_scans(self) -> None:
        """批量扫描待处理文件。"""
        if not self._pending_paths:
            return
        paths = self._pending_paths[:]
        self._pending_paths.clear()

        logger.info("增量扫描 %d 个文件", len(paths))
        report = self._scanner.scan_paths(paths)
        self._handle_scan_result(report)

    def _full_scan(self) -> None:
        """立即全量扫描所有监控路径。"""
        if not self._watch_paths:
            self._notify("无可扫描路径", "请先设置监控路径")
            return
        logger.info("启动全量扫描")
        # 全量扫描在后台线程执行，避免阻塞 UI
        from uniscan.gui.worker import ScanWorker

        self._scan_worker = ScanWorker(ruleset=self._ruleset, root=self._watch_paths[0])
        self._scan_worker.finished_report.connect(self._handle_scan_result)
        self._scan_worker.start()

    def _handle_scan_result(self, report: ScanReport) -> None:
        """处理扫描结果。"""
        self.scan_completed.emit(report)

        # 持久化状态（无论是否有命中）
        if self._state_file is not None:
            try:
                self._scanner.save_state(self._state_file)
            except OSError:
                logger.warning("扫描状态持久化失败", exc_info=True)

        if not report.hits:
            return

        # 通知每个命中文件
        for result in report.hits:
            self.file_hit.emit(str(result.path), len(result.hits))

        # 托盘通知
        if self._tray is not None and self._tray.isVisible():
            count = len(report.hits)
            self._tray.showMessage(
                "uniscan 发现命中",
                f"发现 {count} 个文件命中规则",
                QSystemTrayIcon.Information,
                3000,
            )

        # 更新主窗口结果树
        if self._main_window is not None:
            self._main_window._last_report = report
            self._main_window._populate_results(report)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """托盘图标激活回调：双击显示窗口。"""
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_main_window()

    def _show_main_window(self) -> None:
        """显示主窗口。"""
        if self._main_window is None:
            return
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

    def _notify(self, title: str, message: str) -> None:
        """显示托盘通知。"""
        if self._tray is not None and self._tray.isVisible():
            self._tray.showMessage(title, message, QSystemTrayIcon.Information, 3000)

    def _quit(self) -> None:
        """退出应用。"""
        self.stop_monitoring()
        if self._state_file is not None:
            try:
                self._scanner.save_state(self._state_file)
            except OSError:
                logger.warning("扫描状态持久化失败", exc_info=True)
        if self._main_window is not None:
            self._main_window.close()
        QApplication.quit()
