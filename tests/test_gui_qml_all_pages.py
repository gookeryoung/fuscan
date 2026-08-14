"""QML 集成测试：全部页面/组件实例化应无属性绑定错误。

覆盖 PieChart.qml onChartDataChanged 误写作用域（Main.qml 加载失败）与
SettingsPage/FileMonitorPage binding loop/ReferenceError 等 QML 绑定问题。

背景：ContentArea 对 5 个非首页页面用 Loader 懒加载，Main.qml 启动加载
不会求值这些页面的绑定；仅加载 Main.qml 的测试抓不到懒加载页面的问题
（SettingsPage 的 binding loop 即漏网案例）。本测试逐个实例化全部
页面与组件，让编译期与初始绑定求值错误全部暴露。

策略：

1. ``QT_QPA_PLATFORM=offscreen`` + ``QT_QML_DISABLE_DISK_CACHE=1``
2. ``qInstallMessageHandler`` 收集 QML warnings，聚焦三类绑定问题：
   - ``Binding loop detected``（属性互写循环）
   - ``ReferenceError``（名字解析失败，如 Label 上裸 hovered）
   - ``Cannot read property 'xxx' of null``（null 链式访问）
3. 排除项：
   - HomePageDialogs：依赖 ``dialogsRoot.homePage`` 宿主引用，
     独立实例化必然报 null（Main.qml 链路已覆盖）
   - inline_preview：开发用预览文件，不在运行时链路
   - Splash：独立启动窗口，由 SplashController 单独驱动
4. qrc 图标经 ``resources_rc`` 注册，消除 ``QML Image: Cannot open`` 噪音
"""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错；禁用 QML 磁盘缓存避免环境污染
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QML_DISABLE_DISK_CACHE", "1")

pytestmark = [pytest.mark.gui, pytest.mark.gui_qml]

try:
    from PySide2.QtCore import QTimer, QtMsgType, QUrl, qInstallMessageHandler
    from PySide2.QtGui import QGuiApplication
    from PySide2.QtQml import QQmlApplicationEngine, QQmlComponent

    PYSIDE2_AVAILABLE = True
except ImportError:
    PYSIDE2_AVAILABLE = False

try:
    from fuscan.gui import resources_rc  # noqa: F401  注册 qrc 资源（消除图标噪音）
    from fuscan.gui.controllers import AppController, register_qml_types

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE or not PYSIDE2_AVAILABLE:
    pytest.skip("PySide2 未安装，跳过 QML 集成测试（QML 依赖 Qt5 专属模块）", allow_module_level=True)

# 依赖宿主引用或不在运行时链路的文件（详见模块 docstring）
_EXCLUDED = {"HomePageDialogs.qml", "inline_preview.qml", "Splash.qml"}

_BINDING_ERROR_MARKERS = (
    "Binding loop detected",
    "ReferenceError",
    "Cannot read property",  # 与 "of null" 联检见下方过滤函数
)


def _filter_binding_errors(warnings: list[str]) -> list[str]:
    """过滤出绑定类错误：binding loop / ReferenceError / null 链式访问。"""
    return [
        w
        for w in warnings
        if any(marker in w for marker in _BINDING_ERROR_MARKERS) and ("of null" not in w or "Cannot read property" in w)
    ]


@pytest.fixture()
def qml_warnings_handler() -> Generator[tuple[list[str], Callable[[], None]], None, None]:
    """安装全局消息处理器收集 QML warnings，返回 (warnings, uninstall)。"""
    warnings: list[str] = []

    def handler(msg_type: QtMsgType, context: object, msg: str) -> None:
        if msg_type == QtMsgType.QtWarningMsg and "file:///" in msg:
            warnings.append(msg)

    old_handler = qInstallMessageHandler(handler)

    def _restore() -> None:
        qInstallMessageHandler(old_handler)

    yield warnings, _restore
    _restore()


def _iter_qml_files() -> list[Path]:
    """枚举需要独立实例化的全部页面/组件 QML 文件。"""
    views_dir = Path(__file__).resolve().parents[1] / "src" / "fuscan" / "gui" / "views"
    files = [
        *sorted((views_dir / "pages").glob("*.qml")),
        *sorted((views_dir / "components").glob("*.qml")),
        views_dir / "Sidebar.qml",
        views_dir / "NavItem.qml",
        views_dir / "ContentArea.qml",
    ]
    return [f for f in files if f.name not in _EXCLUDED]


def test_all_pages_no_binding_errors(
    qml_warnings_handler: tuple[list[str], Callable[[], None]],
) -> None:
    """全部页面/组件实例化应无 binding loop / ReferenceError / null 访问。"""
    warnings, _ = qml_warnings_handler
    app = QGuiApplication.instance() or QGuiApplication(["fuscan"])
    register_qml_types()

    controller = AppController()
    engine = QQmlApplicationEngine()
    controller.register_to(engine.rootContext())

    views_dir = Path(__file__).resolve().parents[1] / "src" / "fuscan" / "gui" / "views"
    engine.addImportPath(str(views_dir))

    qml_files = _iter_qml_files()
    assert len(qml_files) >= 15, "QML 文件枚举异常，测试环境损坏"

    keep_alive: list[object] = []
    for qml_file in qml_files:
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_file)))
        assert component.status() != QQmlComponent.Error, f"{qml_file.name} 编译失败：{component.errorString()}"
        obj = component.create(engine.rootContext())
        assert obj is not None, f"{qml_file.name} 实例化失败：{component.errorString()}"
        keep_alive.append(obj)

    # 事件循环让 Loader/延迟绑定充分求值
    QTimer.singleShot(400, app.quit)  # type: ignore[missing-argument, bad-argument-type]
    run = app.exec if hasattr(app, "exec") else app.exec_
    run()
    assert len(keep_alive) == len(qml_files)

    binding_errors = _filter_binding_errors(warnings)
    assert not binding_errors, f"QML 绑定错误（共 {len(binding_errors)} 条，前 5 条）：\n" + "\n".join(
        binding_errors[:5]
    )

    controller.cleanup()
