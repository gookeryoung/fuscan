"""GUI 启动 smoke 测试。

验证 :func:`fuscan.gui.app.launch` 能成功加载 ``Main.qml`` 并构造
所有 controller。本次测试直接覆盖 iter-95 QML 迁移后未被发现的两类
加载错误（``ContentArea`` 缺少 ``import "pages"``、``NavItem`` 重复
声明 ``clicked()`` 信号、未安装的 ``QtQuick.Dialogs 1.3``）。

策略：

1. 设置 ``QT_QPA_PLATFORM=offscreen`` 支持无显示器环境
2. 在调用 :func:`launch` 前用 ``QTimer.singleShot`` 注册延迟 ``quit``，
   确保 ``app.exec_()`` 不阻塞测试进程
3. 断言 ``launch`` 返回 ``0``（QML 加载成功 + 正常退出）；
   若 QML 加载失败，``launch`` 内部检测到 ``rootObjects()`` 为空会
   立即返回 ``-1``，测试即可捕获 regression
"""

from __future__ import annotations

import os

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = [pytest.mark.gui, pytest.mark.gui_qml]

try:
    from PySide2.QtCore import QTimer
    from PySide2.QtGui import QGuiApplication

    from fuscan.gui.app import launch

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过 GUI launch 测试", allow_module_level=True)


def test_launch_loads_main_qml() -> None:
    """launch 应成功加载 Main.qml 并正常退出（退出码 0）。

    若 QML 加载失败（如 import 缺失、信号重名、依赖模块未安装），
    ``launch`` 返回 ``-1``，断言失败暴露 regression。
    """
    app = QGuiApplication.instance() or QGuiApplication(["fuscan"])
    QTimer.singleShot(1000, app.quit)  # type: ignore[missing-argument, bad-argument-type]
    code = launch(["fuscan"])
    assert code == 0, f"GUI 启动失败，launch 返回 {code}（QML 加载失败或异常退出）"
