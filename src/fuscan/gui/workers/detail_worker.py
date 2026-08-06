"""后台构建选中结果命中详情：避免主线程读文件补上下文时阻塞 UI。

选中结果的命中详情需读文件提取匹配文本上下文（``build_detail_hits_full``）。
海量命中或超大文件时同步在主线程执行会冻结界面，故移至独立 QThread：
主线程先用 ``build_detail_hits_light`` 即时展示占位详情，DetailWorker 在
后台补齐完整上下文后经 ``done`` 信号回传主线程替换。

``done`` 携带世代号，主线程回调按世代号丢弃过期结果（快速切换选中时
只保留最新一次构建结果）。

信号：
- ``done``：(list[dict[str, object]], int) 完整命中详情列表 + 世代号
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QThread, Signal
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QThread, Signal  # pyrefly: ignore [missing-import]

from fuscan.gui.controllers._result_detail import build_detail_hits_full

if TYPE_CHECKING:
    from fuscan.scanner.result import ScanResult

__all__ = ["DetailWorker"]

logger = logging.getLogger(__name__)


class DetailWorker(QThread):  # pyrefly: ignore [invalid-inheritance]
    """后台构建命中详情工作线程。

    在独立 QThread 中调用 :func:`build_detail_hits_full` 读文件补齐上下文，
    通过 ``done`` 信号将完整详情列表与世代号回传主线程。

    :param result: 选中结果；``None`` 产出空列表
    :param generation: 世代号，随信号回传供主线程丢弃过期结果
    """

    done = Signal(list, int)

    def __init__(self, result: ScanResult | None, generation: int) -> None:
        super().__init__()
        self._result = result
        self._generation = generation

    def run(self) -> None:
        """线程入口：读文件构建完整命中详情后 emit。"""
        model = build_detail_hits_full(self._result)
        self.done.emit(model, self._generation)  # pyrefly: ignore [missing-attribute]
