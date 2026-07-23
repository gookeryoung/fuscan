"""后台导出工作线程：避免阻塞 UI。

``ExportWorker`` 在独立 QThread 中运行 :func:`fuscan.scanner.export.save_report`，
通过信号通知 UI 完成或失败。PDF/Excel 渲染可能耗时数秒，主线程同步执行会
导致界面完全无响应（菜单/按钮/进度条均无法刷新），iter-59 将其移至后台。

设计要点：

- 继承 ``QThread`` 与 ``ScanWorker`` 模式一致，便于复用信号槽
- 信号 ``finished_ok`` 携带导出路径，主窗口据此弹出"导出成功"对话框
- 信号 ``failed`` 携带错误信息，主窗口弹出 warning
- 不需要取消接口（导出通常 < 5 秒，用户不会主动取消）
"""

from __future__ import annotations

import logging
from pathlib import Path

try:
    from PySide2.QtCore import QObject, QThread, Signal
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QObject, QThread, Signal  # pyrefly: ignore [missing-import]

from fuscan.perf import PerfTimer
from fuscan.scanner import ScanReport
from fuscan.scanner.export import save_report

__all__ = ["ExportWorker"]

logger = logging.getLogger(__name__)


class ExportWorker(QThread):  # pyrefly: ignore [invalid-inheritance]
    """后台导出线程。

    信号：

    - ``finished_ok``：导出成功，携带文件路径（``Path``）
    - ``failed``：导出失败，携带错误信息（``str``）
    """

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, report: ScanReport, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._report = report
        self._path = path

    def run(self) -> None:
        """线程入口：调用 save_report 并通过信号通知 UI 结果。"""
        try:
            with PerfTimer("ExportWorker.save_report"):
                save_report(self._report, self._path)
            logger.info("导出成功: %s", self._path)
            self.finished_ok.emit(self._path)  # pyrefly: ignore [missing-attribute]
        except OSError as exc:
            logger.warning("导出失败: %s", exc, exc_info=True)
            self.failed.emit(str(exc))  # pyrefly: ignore [missing-attribute]
        except Exception as exc:  # pragma: no cover - reportlab/openpyxl 内部异常防御
            logger.exception("导出异常")
            self.failed.emit(str(exc))  # pyrefly: ignore [missing-attribute]
