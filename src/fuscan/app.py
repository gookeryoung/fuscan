"""GUI 应用入口：构造 QGuiApplication 与 QQmlApplicationEngine。"""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import sys
import warnings
from collections.abc import Sequence
from typing import Any

from fuscan.paths import ICON_QRC_URL, MAIN_QML_URL, QML_IMPORT_PATH, SPLASH_QML_URL
from fuscan.perf import PerfReport, render_startup_summary, timed

try:
    from PySide2.QtCore import QUrl
    from PySide2.QtGui import QFont, QGuiApplication, QIcon
    from PySide2.QtQml import QQmlApplicationEngine
    from PySide2.QtQuickControls2 import QQuickStyle
    from PySide2.QtWidgets import QSystemTrayIcon
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QUrl  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QFont, QGuiApplication, QIcon  # pyrefly: ignore [missing-import]
    from PySide6.QtQml import QQmlApplicationEngine  # pyrefly: ignore [missing-import]
    from PySide6.QtQuickControls2 import QQuickStyle  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import QSystemTrayIcon  # pyrefly: ignore [missing-import]

# 显式 import QtSvg：触发 fspack 打包 Qt5Svg.dll/Qt6Svg.dll（qsvg imageformat plugin 依赖）。
# fspack 的 imageformats plugin 始终保留 qsvg.dll，但未标明其对 Svg 子模块的依赖，
# 故需代码侧显式 import 让 AST 分析发现 Svg。运行时若 Qt5Svg.dll 仍缺失（旧 dist 未重新打包），
# import 失败但不阻塞启动——仅 SVG 图标解码回退为空，应用仍可用，便于用户升级过渡。
with contextlib.suppress(ImportError):
    from PySide2 import QtSvg
with contextlib.suppress(ImportError):
    from PySide6 import QtSvg  # noqa: F401  # pyrefly: ignore [missing-import]

from fuscan.config import migrate_config_to_rules
from fuscan.gui import resources_rc  # noqa: F401  注册 qrc 资源
from fuscan.gui.controllers import AppController, SplashController, register_qml_types
from fuscan.gui.theme import detect_font_families

__all__ = ["main"]


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _tune_gil_switch_interval(interval: float = 0.001) -> None:
    """下调 CPython GIL 线程切换间隔，缓解扫描期 GUI 卡死。

    默认切换间隔为 5ms（``sys.getswitchinterval()``）——扫描时多个 worker 线程
    执行纯 Python CPU 密集任务（正则匹配、charset-normalizer 解码），这些任务
    持 GIL 期间只在字节码边界让出。5ms 的间隔下，多个持 GIL 的 worker 线程会
    长时间独占 GIL，令 GUI 主线程极难抢到锁处理绘制/输入，表现为界面完全冻结。

    进程级一次性下调到 1ms，让持 GIL 的纯 Python 线程更频繁在字节码边界让出，
    主线程更易抢到 GIL。空闲主线程无额外开销（切换仅在多线程争抢时发生），
    对吞吐影响可忽略。注意：对**单次超长 C 调用**（大文本 ``re.finditer``、
    charset-normalizer 解码）无效——C 调用内部不检查切换间隔，故须配合
    worker 线程内的定时让步（见 :mod:`fuscan.scanner._content_buckets`）与
    CONTENT 正则密集场景的并发降档共同生效。

    :param interval: 目标切换间隔（秒），默认 0.001（1ms）
    """
    sys.setswitchinterval(interval)


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


def _load_splash(app: QGuiApplication, splash_controller: SplashController) -> QQmlApplicationEngine:
    """构造独立 QML 引擎加载 Splash.qml，让用户尽早看到启动反馈。

    Splash 用独立 :class:`QQmlApplicationEngine` 加载，仅注册 ``SplashController``
    一个 context property，避免依赖 :class:`AppController`（尚未构造）。加载后
    调用 :meth:`processEvents` 强制渲染一帧，确保 Splash 立即可见。

    :param app: 已构造的 QGuiApplication（用于 processEvents）
    :param splash_controller: Splash 阶段文本控制器
    :return: 加载完毕的 Splash QML 引擎（由调用者在主窗口显示后释放）
    """
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("SplashController", splash_controller)  # pyrefly: ignore [missing-argument]
    engine.load(QUrl(SPLASH_QML_URL))  # pyrefly: ignore [missing-argument]
    if not engine.rootObjects():
        logger.warning("Splash 加载失败：%s（继续无 Splash 启动）", SPLASH_QML_URL)
    # 强制处理一次事件循环，让 Splash 立即渲染
    app.processEvents()
    return engine


# 文件监控命中时的声音参数（仅 Windows winsound.Beep 可用）
# 严重度越高频率越高、时长越长，便于用户从声音区分等级
_HIT_SOUND_PARAMS: dict[str, tuple[int, int]] = {
    "info": (800, 200),
    "warning": (1000, 300),
    "critical": (1200, 500),
}


def _play_hit_sound(severity: str) -> None:
    """播放监控命中提示音（仅 Windows；非 Windows 静默跳过）。

    :param severity: 严重度值（``"info"``/``"warning"``/``"critical"``）
    """
    if sys.platform != "win32":
        return
    try:
        # 动态导入避免 pyrefly 在 Linux CI 上报 missing-import
        # winsound 是 Windows 专有标准库模块
        winsound = importlib.import_module("winsound")
        freq, duration = _HIT_SOUND_PARAMS.get(severity, (800, 200))
        winsound.Beep(freq, duration)
    except (OSError, RuntimeError, ImportError) as exc:
        # 蜂鸣器不可用（部分虚拟机/无音频设备）不阻塞流程
        logger.debug("监控命中提示音播放失败: %s", exc)


def _setup_file_monitor_tray(app: QGuiApplication, controller: object) -> QSystemTrayIcon | None:
    """构造系统托盘图标，连接文件监控命中信号触发托盘通知 + 声音。

    托盘在系统通知区显示 fuscan 图标，命中规则时弹出消息框并播放提示音。
    无系统托盘环境（如部分 Linux 无 tray）时静默跳过，不影响主功能。

    :param app: QGuiApplication 实例（提供图标）
    :param controller: AppController 实例（读取 ``file_monitor`` 属性）
    :return: 构造的 :class:`QSystemTrayIcon`；不可用时返回 ``None``
    """
    if not QSystemTrayIcon.isSystemTrayAvailable():
        logger.info("系统托盘不可用，跳过托盘通知")
        return None
    tray = QSystemTrayIcon(QIcon(ICON_QRC_URL), app)
    tray.setToolTip("fuscan 文件监控")

    file_monitor = controller.file_monitor  # pyrefly: ignore [missing-attribute]

    def _on_hit(hit: dict[str, Any]) -> None:
        severity = hit.get("severity", "info")
        severity_text = hit.get("severity_text", "")
        rule_name = hit.get("rule_name", "")
        path = hit.get("path", "")
        # 文件名取路径最后一段（避免托盘消息过长）
        file_name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else ""
        title = f"fuscan 监控命中 · {severity_text}"
        body = f"{rule_name} · {file_name}"
        # 图标按严重度选择：critical→Critical，warning→Warning，info→Information
        icon_flag = {
            "critical": QSystemTrayIcon.Critical,
            "warning": QSystemTrayIcon.Warning,
        }.get(severity, QSystemTrayIcon.Information)
        # showMessage 在部分平台（macOS）不显示，已通过界面内提醒面板兜底
        tray.showMessage(title, body, icon_flag, 5000)  # pyrefly: ignore [bad-argument-type]
        _play_hit_sound(severity)

    file_monitor.hitFound.connect(_on_hit)
    tray.show()
    return tray


def main(argv: Sequence[str] | None = None) -> int:
    """启动 QML GUI 应用。

    启动流程采用**渐进式 Splash 反馈**：在 QGuiApplication 构造后立即加载独立
    的 :file:`Splash.qml`（无边框圆角窗口 + logo + 阶段文本 + 确定性进度条），让用户
    在数百毫秒内看到反馈；后续各阶段（迁移配置 / 构造主控制器 / 加载主 QML）
    通过 :meth:`SplashController.setStage` 更新文本与单调递增的进度值，并调用
    :meth:`QGuiApplication.processEvents` 让 Splash 重绘，缓解"应用启动卡顿"的观感。
    进度条按阶段比例填充且只增不减，避免 indeterminate 往返动画造成的"反复前进后退"。
    主窗口 QML 加载完成后关闭 Splash 并进入事件循环。

    各启动阶段通过 :class:`~fuscan.perf.timed` 分段计时并登记到 :class:`~fuscan.perf.PerfReport`；
    外层 ``timed("启动流程")`` 汇总总用时。启用性能测量时（``FUSCAN_PERF=1`` 环境变量或
    CLI ``--perf``），外层块退出后由 :func:`~fuscan.perf.render_startup_summary` 打印**单张**
    rich 汇总表（列：阶段 / 耗时 / 占比），一眼识别瓶颈；逐阶段细节降为 DEBUG（``-vv`` 才可见），
    避免刷屏。发布版默认关闭、零开销。
    """
    logger.info("启动 QML GUI 应用")

    # 进程级下调 GIL 切换间隔，缓解扫描期多 worker 线程持 GIL 导致的 GUI 冻结。
    # 越早设置越好：影响后续所有线程（扫描/导出/统计/筛选/恢复 worker）。
    _tune_gil_switch_interval()

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

            # 设置应用图标：影响 Windows 任务栏与窗口标题栏图标。
            # favicon.ico 已编译进 qrc（resources_rc.py），通过 qrc 路径加载。
            # 必须在 QGuiApplication 构造后、首次显示窗口前设置。
            app.setWindowIcon(QIcon(ICON_QRC_URL))

            # 设置 QtQuick Controls 2 风格为 Fusion（跨平台一致）
            QQuickStyle.setStyle("Fusion")

        with timed("注册 QML 类型", level=logging.DEBUG, report=report):
            register_qml_types()

        # 构造 Splash：在 QGuiApplication 与 QML 类型注册后立即加载，让用户尽早看到反馈。
        # Splash 用独立 engine + 仅 SplashController context property，不依赖 AppController。
        with timed("构造 Splash 启动画面", level=logging.DEBUG, report=report):
            splash_controller = SplashController()
            splash_engine = _load_splash(app, splash_controller)

        with timed("迁移旧配置字段到规则集", level=logging.DEBUG, report=report):
            # 在 ConfigController 构造前执行迁移：将旧版 config.yaml 中的
            # scan_archives/max_workers/ignore_dirs/disabled_extractors 等字段
            # 搬到 ~/.fuscan/rules/user-scan.yaml，并从 config.yaml 中清除。
            # 幂等：无迁移字段时 no-op。
            splash_controller.setStage("迁移配置...", 0.15)
            app.processEvents()  # 让 Splash 立即刷新阶段文本与进度
            migrate_config_to_rules()

        with timed("构造主控制器", level=logging.DEBUG, report=report):
            splash_controller.setStage("加载规则与工作区...", 0.35)
            app.processEvents()
            controller = AppController()

        with timed("构造 QML 引擎并注册上下文", level=logging.DEBUG, report=report):
            engine = QQmlApplicationEngine()
            controller.register_to(engine.rootContext())
            logger.info("导入 QML 路径：%s", QML_IMPORT_PATH)
            engine.addImportPath(QML_IMPORT_PATH)

        with timed("加载主 QML", level=logging.DEBUG, report=report):
            splash_controller.setStage("加载主界面...", 0.65)
            app.processEvents()
            logger.info("加载主 QML：%s", MAIN_QML_URL)
            engine.load(QUrl(MAIN_QML_URL))  # pyrefly: ignore [missing-argument]

        if not engine.rootObjects():
            logger.error("QML 加载失败：%s", MAIN_QML_URL)
            return -1

        # 窗口关闭时清理 controller 资源
        app.aboutToQuit.connect(controller.cleanup)

        # 构造系统托盘并连接文件监控命中信号（无系统托盘环境静默跳过）
        with timed("构造文件监控托盘", level=logging.DEBUG, report=report):
            _setup_file_monitor_tray(app, controller)

        # 主窗口已加载显示，关闭并释放 Splash 资源
        splash_controller.setStage("就绪", 1.0)
        app.processEvents()
        splash_engine.deleteLater()

    # 启动成功后渲染单张 rich 汇总表（perf 未启用时内部即刻 return，零开销）
    render_startup_summary(report)

    # PySide2 用 exec_，PySide6 推荐 exec
    logger.info("启动应用")
    run = app.exec if hasattr(app, "exec") else app.exec_
    return run()  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
