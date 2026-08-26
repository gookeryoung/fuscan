"""后台恢复扫描结果：避免主线程阻塞。

ResultRestoreWorker 在独立 QThread 中读取缓存文件并反序列化 ScanReport，
通过信号通知主线程恢复完成。10 万命中结果的反序列化（orjson）约 200ms，
移至后台线程后启动到可交互 < 1s。

信号：
- ``restore_done``：(ws_id, ScanReport) 加载成功
- ``restore_failed``：(ws_id, error_message) 加载失败
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide2.QtCore import QThread, Signal

from fuscan.scanner import ScanReport

__all__ = ["ResultRestoreWorker"]

logger = logging.getLogger(__name__)


class ResultRestoreWorker(QThread):  # pyrefly: ignore [invalid-inheritance]
    """后台加载缓存扫描结果的工作线程。

    在 :meth:`run` 中执行 ``read_bytes`` + ``ScanReport.from_json``，
    完成后通过 ``restore_done`` 信号将 :class:`ScanReport` 传回主线程。
    主线程在信号槽中调用 :meth:`ScanController.restoreFromReport` 恢复结果。

    :param ws_id: 工作区 ID（用于信号回传标识）
    :param cache_file: 缓存文件路径（``~/.fuscan/results/<ws_id>.json``）
    """

    restore_done = Signal(str, object)  # (ws_id, ScanReport)
    restore_failed = Signal(str, str)  # (ws_id, error_message)

    def __init__(self, ws_id: str, cache_file: Path) -> None:
        """初始化恢复线程。"""
        super().__init__()
        self._ws_id = ws_id
        self._cache_file = cache_file

    def run(self) -> None:
        """线程入口：读取并反序列化缓存文件。"""
        try:
            data = self._cache_file.read_bytes()
            report = ScanReport.from_json(data)
            self.restore_done.emit(self._ws_id, report)  # pyrefly: ignore [missing-attribute]
            logger.debug(
                "工作区 %s 扫描结果后台恢复完成（%d 条命中）",
                self._ws_id,
                len(report.hits),
            )
        except (OSError, ValueError, KeyError) as exc:
            self.restore_failed.emit(self._ws_id, str(exc))  # pyrefly: ignore [missing-attribute]
            logger.warning("工作区 %s 扫描结果后台恢复失败: %s", self._ws_id, exc)
