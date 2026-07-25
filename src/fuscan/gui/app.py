"""GUI 应用入口：构造 QGuiApplication 与 QQmlApplicationEngine。

提供 :func:`launch` 函数供 CLI ``gui`` 子命令调用，也可作为脚本直接运行。
按 rule-12-pyside-dev.md 要求，UI 全部在 ``.qml`` 文件定义，Python 侧仅
构造 controller 注册到 QML context。

参考实现：``ref/pyside2_qml_dashboard/main.py``
"""

from __future__ import annotations

import logging
import sys
import warnings
from pathlib import Path
from typing import Sequence

try:
    from PySide2.QtCore import QUrl
    from PySide2.QtGui import QGuiApplication
    from PySide2.QtQml import QQmlApplicationEngine
    from PySide2.QtQuickControls2 import QQuickStyle
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QUrl  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QGuiApplication  # pyrefly: ignore [missing-import]
    from PySide6.QtQml import QQmlApplicationEngine  # pyrefly: ignore [missing-import]
    from PySide6.QtQuickControls2 import QQuickStyle  # pyrefly: ignore [missing-import]

from fuscan.gui.qml import AppController
from fuscan.gui.qml.controllers import register_qml_types

__all__ = ["launch"]

logger = logging.getLogger(__name__)

# QML 文件目录（src/fuscan/gui/qml/views/）
# 按rule-12三层MVC分层，.qml视图文件全部在 views/ 子目录
_QML_DIR = Path(__file__).parent / "qml"
_VIEWS_DIR = _QML_DIR / "views"
_MAIN_QML = _VIEWS_DIR / "Main.qml"


def launch(argv: Sequence[str] | None = None) -> int:
    """启动 QML GUI 应用。

    :param argv: 命令行参数（默认从 sys.argv 读取）
    :return: 退出码
    """
    # 抑制 cryptography 对 Python 3.8 的弃用警告
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="cryptography")

    args = list(argv) if argv is not None else sys.argv
    # QML 使用 QGuiApplication（无需 QApplication 的 widgets 依赖）
    app = QGuiApplication.instance() or QGuiApplication(args)
    app.setApplicationName("fuscan")
    app.setOrganizationName("fuscan")

    # 设置 QtQuick Controls 2 风格为 Fusion（跨平台一致）
    QQuickStyle.setStyle("Fusion")

    # 注册 controller/model 类型到 QML 引擎（必须在 QQmlApplicationEngine 构造前）
    # 使 QML 文件能 import 类型并声明类型化 property，消除 setContextProperty 导致的 TypeError
    register_qml_types()

    # 构造主控制器并注册到 QML context
    controller = AppController()

    engine = QQmlApplicationEngine()
    controller.register_to(engine.rootContext())

    # 添加 views/ 到 QML import path（支持 Main.qml 同目录引用 Sidebar/ContentArea 等）
    engine.addImportPath(str(_VIEWS_DIR))

    # 加载主 QML
    engine.load(QUrl.fromLocalFile(str(_MAIN_QML)))  # pyrefly: ignore [missing-argument]

    if not engine.rootObjects():
        logger.error("QML 加载失败：%s", _MAIN_QML)
        return -1

    # 窗口关闭时清理 controller 资源
    app.aboutToQuit.connect(controller.cleanup)

    # PySide2 用 exec_，PySide6 推荐 exec
    run = app.exec if hasattr(app, "exec") else app.exec_
    return run()  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(launch())
