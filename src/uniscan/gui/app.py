"""GUI 应用入口：构造 QApplication 与主窗口。

提供 :func:`launch` 函数供 CLI ``gui`` 子命令调用，也可作为脚本直接运行。
"""

from __future__ import annotations

import logging
import sys
from typing import Optional, Sequence

from PySide2.QtWidgets import QApplication

from uniscan.gui.main_window import MainWindow

__all__ = ["launch"]

logger = logging.getLogger(__name__)


def launch(argv: Optional[Sequence[str]] = None) -> int:
    """启动 GUI 应用。

    :param argv: 命令行参数（默认从 sys.argv 读取）
    :return: 退出码
    """
    args = list(argv) if argv is not None else sys.argv
    app = QApplication.instance() or QApplication(args)
    app.setApplicationName("uniscan")

    window = MainWindow()
    window.show()

    return app.exec_()


if __name__ == "__main__":
    sys.exit(launch())
