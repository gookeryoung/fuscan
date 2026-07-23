"""PySide2 GUI 子包。

公共 API：

- :class:`MainWindow`：主窗口
- :func:`launch`：启动 GUI 应用
"""

from __future__ import annotations

from fuscan.gui.main_window import MainWindow

__all__ = ["MainWindow", "launch"]


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """惰性导入 launch，避免无 GUI 环境下 import 整个包失败。"""
    if name == "launch":
        from fuscan.gui.app import launch

        return launch
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
