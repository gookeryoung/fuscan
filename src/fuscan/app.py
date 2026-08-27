"""GUI 应用入口：构造 QApplication 与 QtWidgets 主窗口（视图 → Widgets 迁移）。"""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import sys
import threading
import warnings
from collections.abc import Sequence
from typing import Any

from PySide2.QtGui import QFont, QIcon
from PySide2.QtWidgets import QApplication, QSystemTrayIcon

from fuscan.config import migrate_config_to_rules
from fuscan.gui import resources_rc  # noqa: F401  注册 qrc 资源
from fuscan.gui.controllers import AppController, SplashController
from fuscan.gui.theme import detect_font_families
from fuscan.gui.widgets.main_window import MainWindow
from fuscan.paths import ICON_QRC_URL
from fuscan.perf import PerfReport, render_startup_summary, timed

__all__ = ["main"]

# 显式 import QtSvg：触发 fspack 打包 Qt5Svg.dll（qsvg imageformat plugin 依赖）。
# fspack 的 imageformats plugin 始终保留 qsvg.dll，但未标明其对 Svg 子模块的依赖，
# 故需代码侧显式 import 让 AST 分析发现 Svg。运行时若 Qt5Svg.dll 仍缺失（旧 dist 未重新打包），
# import 失败但不阻塞启动——仅 SVG 图标解码回退为空，应用仍可用，便于用户升级过渡。
with contextlib.suppress(ImportError):
    from PySide2 import QtSvg  # noqa: F401


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


def _apply_global_font(app: QApplication) -> None:
    """设置全局默认字体（跨平台最佳实践 + 用户配置覆盖）。

    用 ``QFont.setFamilies()`` 设置优先级列表，Qt 自动选择首个可用字体：
    - 用户配置 font_family 优先（SettingsPage 通用设置）
    - 否则按平台默认：Windows → Microsoft YaHei UI；macOS → PingFang SC；Linux → Noto Sans CJK SC

    字号与加粗从用户配置读取（默认 14px、不加粗），
    QtWidgets 控件默认继承此全局字体，无需每个控件单独设置 ``font.family``。

    .. note::
        ``main()`` 不再调用本函数；由 :meth:`AppController._apply_font_config_to_theme`
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


def _load_splash(app: QApplication, splash_controller: SplashController):
    """构造启动画面，让用户尽早看到启动反馈。

    仅依赖 :class:`SplashController` 的阶段/进度信号，
    不依赖尚未构造的 :class:`AppController`。显示后调用 :meth:`processEvents`
    强制渲染一帧确保立即可见。

    :param app: 已构造的 QApplication（用于 processEvents）
    :param splash_controller: Splash 阶段文本控制器
    :return: 加载完毕的 :class:`~fuscan.gui.widgets.splash.SplashWindow`
        （由调用者在主窗口显示后释放）
    """
    from fuscan.gui.widgets.splash import SplashWindow

    splash = SplashWindow(splash_controller)
    splash.show()
    app.processEvents()
    return splash


# 文件监控命中时的声音参数（仅 Windows winsound.Beep 可用）
# 严重度越高频率越高、时长越长，便于用户从声音区分等级
_HIT_SOUND_PARAMS: dict[str, tuple[int, int]] = {
    "info": (800, 200),
    "warning": (1000, 300),
    "critical": (1200, 500),
}


def _play_hit_sound(severity: str) -> None:
    """播放监控命中提示音（仅 Windows；非 Windows 静默跳过）。

    ``winsound.Beep`` 同步阻塞（critical 长达 500ms），在独立守护线程
    播放避免阻塞 GUI 主线程。

    :param severity: 严重度值（``"info"``/``"warning"``/``"critical"``）
    """
    if sys.platform != "win32":
        return

    def _beep() -> None:
        try:
            # 动态导入避免 pyrefly 在 Linux CI 上报 missing-import
            # winsound 是 Windows 专有标准库模块
            winsound = importlib.import_module("winsound")
            freq, duration = _HIT_SOUND_PARAMS.get(severity, (800, 200))
            winsound.Beep(freq, duration)
        except (OSError, RuntimeError, ImportError) as exc:
            # 蜂鸣器不可用（部分虚拟机/无音频设备）不阻塞流程
            logger.debug("监控命中提示音播放失败: %s", exc)

    threading.Thread(target=_beep, daemon=True, name="fuscan-monitor-beep").start()


def _setup_file_monitor_tray(app: QApplication, controller: object) -> QSystemTrayIcon | None:
    """构造系统托盘图标，连接文件监控命中信号触发托盘通知 + 声音。

    托盘在系统通知区显示 fuscan 图标，命中规则时弹出消息框并播放提示音。
    无系统托盘环境（如部分 Linux 无 tray）时静默跳过，不影响主功能。

    :param app: QApplication 实例（提供图标）
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
    """启动 Widgets GUI 应用。

    启动流程采用**渐进式 Splash 反馈**：在 QApplication 构造后立即显示
    :class:`~fuscan.gui.widgets.splash.SplashWindow`（无边框圆角卡片 + logo +
    阶段文本 + 确定性进度条），让用户在数百毫秒内看到反馈；后续各阶段
    （迁移配置 / 构造主控制器 / 构造主窗口）通过 :meth:`SplashController.setStage`
    更新文本与单调递增的进度值，并调用 :meth:`QApplication.processEvents`
    让 Splash 重绘。主窗口构造完成后关闭 Splash 并进入事件循环。

    各启动阶段通过 :class:`~fuscan.perf.timed` 分段计时并登记到 :class:`~fuscan.perf.PerfReport`；
    启用性能测量时（``FUSCAN_PERF=1`` 或 CLI ``--perf``）退出后由
    :func:`~fuscan.perf.render_startup_summary` 打印 rich 汇总表；默认零开销。
    """
    logger.info("启动 Widgets GUI 应用")

    # 进程级下调 GIL 切换间隔，缓解扫描期多 worker 线程持 GIL 导致的 GUI 冻结。
    _tune_gil_switch_interval()

    # 抑制 cryptography 对 Python 3.8 的弃用警告
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="cryptography")

    # 抑制 Qt 在 Windows 上访问剪贴板时的 "Retrying to obtain clipboard" 警告噪音
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.mime=false")

    report = PerfReport()
    with timed("启动流程", level=logging.DEBUG, report=report):
        with timed("构造 QApplication", level=logging.DEBUG, report=report):
            args = list(argv) if argv is not None else sys.argv
            app = QApplication.instance() or QApplication(args)
            app.setApplicationName("fuscan")
            app.setOrganizationName("fuscan")
            app.setStyle("Fusion")

            # 应用图标：影响 Windows 任务栏与窗口标题栏图标。
            # favicon.ico 已编译进 qrc（resources_rc.py），必须先 import resources_rc。
            app.setWindowIcon(QIcon(ICON_QRC_URL))

        # Widgets 版启动画面：仅依赖 SplashController，不依赖 AppController。
        with timed("构造 Splash 启动画面", level=logging.DEBUG, report=report):
            splash_controller = SplashController()
            splash = _load_splash(app, splash_controller)
            splash.set_dark(False)

        with timed("迁移旧配置字段到规则集", level=logging.DEBUG, report=report):
            splash_controller.setStage("迁移配置...", 0.15)
            app.processEvents()
            migrate_config_to_rules()

        with timed("构造主控制器", level=logging.DEBUG, report=report):
            splash_controller.setStage("加载规则与工作区...", 0.35)
            app.processEvents()
            controller = AppController()

        with timed("应用全局字体与主题色板", level=logging.DEBUG, report=report):
            # 复用 ConfigController 已加载的 Config：字体注入 ThemeController 后
            # 读取计算值设置全局 QFont 与 QSS 字号，避免重复读配置文件。
            # 注意 fontFamily/fontSizeBase/fontBold 为 QProperty，须按属性访问
            theme = controller.theme  # pyrefly: ignore [missing-attribute]
            font = QFont()
            font.setFamily(theme.fontFamily)
            font.setPixelSize(theme.fontSizeBase)
            if theme.fontBold:
                font.setBold(True)
            app.setFont(font)
            from fuscan.gui.widgets.qss import build_app_qss

            app.setStyleSheet(
                build_app_qss(dark=False, font_family=theme.fontFamily, body_font_size=theme.fontSizeBase)
            )
            splash_controller.setStage("加载主界面...", 0.65)

        with timed("构造主窗口", level=logging.DEBUG, report=report):
            window = MainWindow(controller)

        # 窗口关闭时清理 controller 资源（closeEvent 内驱动）
        app.aboutToQuit.connect(controller.cleanup)

        with timed("构造文件监控托盘", level=logging.DEBUG, report=report):
            _setup_file_monitor_tray(app, controller)

        # 主窗口已就绪，关闭并释放 Splash
        splash_controller.setStage("就绪", 1.0)
        window.show()
        app.processEvents()
        splash.deleteLater()

    render_startup_summary(report)

    logger.info("启动应用")
    return app.exec_()  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
