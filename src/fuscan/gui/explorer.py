"""跨平台文件管理器集成。

将 ``explorer /select,`` / ``open -R`` / ``xdg-open`` 的平台分派逻辑从
``main_window.py`` 拆分到本模块，使主窗口仅负责异常捕获与用户提示，
系统集成命令构造与启动内聚到本模块。

公共 API：

- :func:`open_path_in_explorer`：在系统文件管理器中打开指定文件所在目录并选中该文件
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

__all__ = ["open_path_in_explorer"]


def open_path_in_explorer(path: Path) -> None:
    """在系统文件管理器中打开指定文件所在目录并选中该文件。

    跨平台实现：

    - Windows：``explorer /select, <path>``
    - macOS：``open -R <path>``
    - 其他：``xdg-open <parent>``（仅打开父目录，无法选中具体文件）

    :param path: 待定位的文件路径
    :raises OSError: 启动文件管理器进程失败（如可执行文件不存在或权限不足）
    """
    if sys.platform == "win32":
        cmd: list[str] = ["explorer", "/select,", str(path)]
    elif sys.platform == "darwin":
        cmd = ["open", "-R", str(path)]
    else:
        cmd = ["xdg-open", str(path.parent)]
    subprocess.Popen(cmd)
