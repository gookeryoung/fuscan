"""GUI 模块入口：支持 ``python -m fuscan.gui`` 直接启动 GUI 应用。

便于独立打包为可执行文件（fspack 等），无需通过 CLI 子命令。
"""

from __future__ import annotations

import sys

# GUI 标记：供 fspack 识别应用类型为 GUI（多入口模式下按入口脚本 import 推断）
try:
    import PySide2  # noqa: F401
except ImportError:  # pragma: no cover
    import PySide6  # noqa: F401

from fuscan.gui.app import launch

if __name__ == "__main__":  # pragma: no cover
    sys.exit(launch())
