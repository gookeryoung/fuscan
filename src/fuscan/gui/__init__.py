"""PySide2 + QML GUI 子包。

公共 API：

- :func:`launch`：启动 QML GUI 应用
- :class:`AppController`：主控制器聚合（构造并注册所有 controller 到 QML context）

GUI 采用 PySide2 + QML 范式（参考 ``ref/pyside2_qml_dashboard``），UI 全部
在 ``qml/*.qml`` 文件定义，Python 侧通过 ``QObject`` controller 桥接。
"""

from __future__ import annotations


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """惰性导入 launch 与 AppController，避免无 GUI 环境下 import 整个包失败。"""
    if name == "launch":
        from fuscan.gui.app import launch

        return launch
    if name == "AppController":
        from fuscan.gui.qml import AppController

        return AppController
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
