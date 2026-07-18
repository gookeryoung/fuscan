"""GUI 模块入口：支持 ``python -m fuscan.gui`` 直接启动 GUI 应用。

便于独立打包为可执行文件（PyInstaller 等），无需通过 CLI 子命令。
"""

from __future__ import annotations

import sys

from fuscan.gui.app import launch

if __name__ == "__main__":  # pragma: no cover
    sys.exit(launch())
