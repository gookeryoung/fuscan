"""忽略目录预设配置。

提供常见系统目录与临时目录的预设列表，避免监控这些目录产生无效事件。
用户可在 MonitorConfig.ignore_dirs 中追加自定义目录。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["common_ignore_dirs", "default_ignore_dirs", "windows_system_dirs"]


common_ignore_dirs: tuple[str, ...] = (
    # 版本控制
    ".git",
    ".hg",
    ".svn",
    # Python
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    # Node
    "node_modules",
    ".npm",
    ".yarn",
    # IDE
    ".idea",
    ".vscode",
    ".vs",
    # 构建产物
    "build",
    "dist",
    "target",
    "bin",
    "obj",
    # 临时文件
    "tmp",
    "temp",
    ".tmp",
    "cache",
    ".cache",
    # 系统回收站
    "$Recycle.Bin",
    ".Trash",
)


windows_system_dirs: tuple[str, ...] = (
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "System Volume Information",
    "WinSxS",
    "Drivers",
    "DriverStore",
)


def default_ignore_dirs() -> list[str]:
    """返回当前平台的默认忽略目录列表。

    Windows 上追加系统目录，其他平台只返回通用忽略目录。
    """
    dirs: list[str] = list(common_ignore_dirs)
    if sys.platform == "win32":
        dirs.extend(windows_system_dirs)
        # 用户临时目录
        temp = os.environ.get("TEMP", "")
        if temp:
            dirs.append(Path(temp).name)
    return dirs
