"""GUI 应用入口：构造 QGuiApplication 与 QQmlApplicationEngine。"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from collections.abc import Sequence

from fuscan.paths import MAIN_QML_URL, QML_IMPORT_PATH
from fuscan.perf import PerfReport, render_startup_summary, timed

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

__all__ = ["main"]


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _apply_global_font(app: QGuiApplication) -> None:
    """设置全局默认字体（跨平台最佳实践 + 用户配置覆盖）。

    用 ``QFont.setFamilies()`` 设置优先级列表，Qt 自动选择首个可用字体：
    - 用户配置 font_family 优先（SettingsPage 通用设置）
    - 否则按平台默认：Windows → Microsoft YaHei UI；macOS → PingFang SC；Linux → Noto Sans CJK SC

    字号与加粗从用户配置读取（默认 14px、不加粗），
    QML 控件默认继承此全局字体，无需每个控件单独设置 ``font.family``。

    .. note::
        ``launch()`` 不再调用本函数；由 :meth:`AppController._apply_font_config_to_theme`
        在构造 ``ConfigController`` 后复用其已加载的 :class:`Config` 实例统一设置字体，
        避免 ``load_config()`` 重复读取 ``~/.fuscan/config.yaml``。本函数保留供
        需要独立设置字体的入口（如脚本测试）使用。
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


def main(argv: Sequence[str] | None = None) -> int:
    """启动 QML GUI 应用。

    各启动阶段通过 :class:`~fuscan.perf.timed` 分段计时并登记到 :class:`~fuscan.perf.PerfReport`；
    外层 ``timed("启动流程")`` 汇总总用时。启用性能测量时（``FUSCAN_PERF=1`` 环境变量或
    CLI ``--perf``），外层块退出后由 :func:`~fuscan.perf.render_startup_summary` 打印**单张**
    rich 汇总表（列：阶段 / 耗时 / 占比），一眼识别瓶颈；逐阶段细节降为 DEBUG（``-vv`` 才可见），
    避免刷屏。发布版默认关闭、零开销。
    """
    logger.info("启动 QML GUI 应用")

    # 抑制 cryptography 对 Python 3.8 的弃用警告
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="cryptography")

    # 抑制 Qt 在 Windows 上访问剪贴板时的 "Retrying to obtain clipboard"
    # 警告。该警告由其他应用锁住剪贴板时 Qt 内部重试产生，非代码 bug，仅日志噪音。
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.mime=false")

    # 收集各阶段耗时；外层块退出后渲染单张 rich 汇总表。逐阶段细节降为 DEBUG 避免刷屏。
    report = PerfReport()
    with timed("启动流程", level=logging.DEBUG, report=report):
        with timed("构造 QGuiApplication", level=logging.DEBUG, report=report):
            args = list(argv) if argv is not None else sys.argv
            app = QGuiApplication.instance() or QGuiApplication(args)
            app.setApplicationName("fuscan")
            app.setOrganizationName("fuscan")

            # 设置 QtQuick Controls 2 风格为 Fusion（跨平台一致）
            QQuickStyle.setStyle("Fusion")

        with timed("注册 QML 类型", level=logging.DEBUG, report=report):
            register_qml_types()

        with timed("构造主控制器", level=logging.DEBUG, report=report):
            controller = AppController()

        with timed("构造 QML 引擎并注册上下文", level=logging.DEBUG, report=report):
            engine = QQmlApplicationEngine()
            controller.register_to(engine.rootContext())
            logger.info("导入 QML 路径：%s", QML_IMPORT_PATH)
            engine.addImportPath(QML_IMPORT_PATH)

        with timed("加载主 QML", level=logging.DEBUG, report=report):
            logger.info("加载主 QML：%s", MAIN_QML_URL)
            engine.load(QUrl(MAIN_QML_URL))  # pyrefly: ignore [missing-argument]

        if not engine.rootObjects():
            logger.error("QML 加载失败：%s", MAIN_QML_URL)
            return -1

        # 窗口关闭时清理 controller 资源
        app.aboutToQuit.connect(controller.cleanup)

    # 启动成功后渲染单张 rich 汇总表（perf 未启用时内部即刻 return，零开销）
    render_startup_summary(report)

    # PySide2 用 exec_，PySide6 推荐 exec
    logger.info("启动应用")
    run = app.exec if hasattr(app, "exec") else app.exec_
    return run()  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
