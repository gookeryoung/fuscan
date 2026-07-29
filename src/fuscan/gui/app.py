"""GUI 应用入口：构造 QGuiApplication 与 QQmlApplicationEngine。

提供 :func:`launch` 函数供 CLI ``gui`` 子命令调用，也可作为脚本直接运行。
按 rule-12-pyside-dev.md 要求，UI 全部在 ``.qml`` 文件定义，Python 侧仅
构造 controller 注册到 QML context。

参考实现：``ref/pyside2_qml_dashboard/main.py``

QML 加载策略（iter-108 启动加速）：

- 所有 ``.qml`` 与 ``.svg`` 资源由 ``scripts/build_qrc.py`` 编译进
  ``resources_rc.py``，运行时通过 ``qrc:/`` 路径访问，避免 Win7 等老系统
  磁盘 I/O 阻塞。
- 修改 QML/SVG 后须重新运行 ``uv run python scripts/build_qrc.py`` 重建。
"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from typing import Sequence

try:
    from PySide2.QtCore import QUrl
    from PySide2.QtGui import QFont, QGuiApplication
    from PySide2.QtQml import QQmlApplicationEngine
    from PySide2.QtQuickControls2 import QQuickStyle
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QUrl  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QFont, QGuiApplication  # pyrefly: ignore [missing-import]
    from PySide6.QtQml import QQmlApplicationEngine  # pyrefly: ignore [missing-import]
    from PySide6.QtQuickControls2 import QQuickStyle  # pyrefly: ignore [missing-import]

from fuscan.gui import resources_rc  # noqa: F401  注册 qrc 资源
from fuscan.gui.controllers import AppController, register_qml_types
from fuscan.gui.theme import detect_font_families

__all__ = ["launch"]

logger = logging.getLogger(__name__)

# QML 资源 qrc 路径前缀（resources.qrc 中 alias=qml/Main.qml）
# QML 间相对 import（如 import "pages"）在 qrc 内自动解析
_QML_IMPORT_PATH = "qrc:/qml"
_MAIN_QML_URL = "qrc:/qml/Main.qml"


def _apply_global_font(app: QGuiApplication) -> None:
    """设置全局默认字体（跨平台最佳实践 + 用户配置覆盖）。

    用 ``QFont.setFamilies()`` 设置优先级列表，Qt 自动选择首个可用字体：
    - 用户配置 font_family 优先（SettingsPage 通用设置）
    - 否则按平台默认：Windows → Microsoft YaHei UI；macOS → PingFang SC；Linux → Noto Sans CJK SC

    字号与加粗从用户配置读取（默认 14px、不加粗），
    QML 控件默认继承此全局字体，无需每个控件单独设置 ``font.family``。
    """
    from fuscan.config import load_config

    cfg = load_config()
    font = QFont()
    if cfg.font_family:
        font.setFamily(cfg.font_family)
    else:
        font.setFamilies(list(detect_font_families()))
    font.setPixelSize(cfg.font_size)
    if cfg.font_bold:
        font.setBold(True)
    app.setFont(font)


def launch(argv: Sequence[str] | None = None) -> int:
    """启动 QML GUI 应用。

    :param argv: 命令行参数（默认从 sys.argv 读取）
    :return: 退出码
    """
    # 抑制 cryptography 对 Python 3.8 的弃用警告
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="cryptography")

    # iter-132：抑制 Qt 在 Windows 上访问剪贴板时的 "Retrying to obtain clipboard"
    # 警告。该警告由其他应用锁住剪贴板时 Qt 内部重试产生，非代码 bug，仅日志噪音。
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.mime=false")

    args = list(argv) if argv is not None else sys.argv
    # QML 使用 QGuiApplication（无需 QApplication 的 widgets 依赖）
    app = QGuiApplication.instance() or QGuiApplication(args)
    app.setApplicationName("fuscan")
    app.setOrganizationName("fuscan")

    # 设置全局跨平台字体（必须在 QML 引擎加载前，确保控件继承）
    _apply_global_font(app)

    # 设置 QtQuick Controls 2 风格为 Fusion（跨平台一致）
    QQuickStyle.setStyle("Fusion")

    # 注册 controller/model 类型到 QML 引擎（必须在 QQmlApplicationEngine 构造前）
    # 使 QML 文件能 import 类型并声明类型化 property，消除 setContextProperty 导致的 TypeError
    register_qml_types()

    # 构造主控制器并注册到 QML context
    controller = AppController()

    engine = QQmlApplicationEngine()
    controller.register_to(engine.rootContext())

    # 添加 qrc:/qml 到 QML import path（支持 Main.qml 同目录引用 Sidebar/ContentArea 等）
    engine.addImportPath(_QML_IMPORT_PATH)

    # 加载主 QML（从 qrc 资源，避免文件系统 I/O）
    engine.load(QUrl(_MAIN_QML_URL))  # pyrefly: ignore [missing-argument]

    if not engine.rootObjects():
        logger.error("QML 加载失败：%s", _MAIN_QML_URL)
        return -1

    # 窗口关闭时清理 controller 资源
    app.aboutToQuit.connect(controller.cleanup)

    # PySide2 用 exec_，PySide6 推荐 exec
    run = app.exec if hasattr(app, "exec") else app.exec_
    return run()  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(launch())
