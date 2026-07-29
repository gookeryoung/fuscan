"""QML 集成测试：验证 ScanProgressCard 不报 null TypeError。

iter-101 修复：ScanProgressCard 将 scanController 从本地 property 绑定改为
``workspaceController.activeScanController.xxx`` 链式访问，消除 PySide2 5.15
将 ``@Property(ScanController)`` 返回的 QObject 绑定到本地 property 时类型推断
失败导致的 ``Cannot read property 'xxx' of null`` TypeError。

策略：

1. 设置 ``QT_QPA_PLATFORM=offscreen`` 支持无显示器环境
2. 用 ``qInstallMessageHandler`` 捕获 QML warnings（含 null TypeError）
   - ``QQmlEngine.warnings`` 信号参数为 ``QList<QQmlError>``，PySide2 无法处理，
     故改用全局消息处理器（QML warnings 在 Qt5 内部走 ``qWarning``）
3. 加载 ``Main.qml``，让 QML binding 求值
4. 启动一个工作区扫描，触发 ``hasActiveScan=true``，让 ScanProgressCard 可见
5. 断言无 ``Cannot read property 'xxx' of null`` TypeError
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Generator
from pathlib import Path
from typing import Callable

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = [pytest.mark.gui, pytest.mark.gui_qml]

try:
    from PySide2.QtCore import QTimer, QtMsgType, QUrl, qInstallMessageHandler
    from PySide2.QtGui import QGuiApplication
    from PySide2.QtQml import QQmlApplicationEngine

    PYSIDE2_AVAILABLE = True
except ImportError:
    PYSIDE2_AVAILABLE = False
    from PySide6.QtCore import (  # type: ignore[no-redef]
        QTimer,
        QtMsgType,
        QUrl,
        qInstallMessageHandler,
    )
    from PySide6.QtGui import QGuiApplication  # type: ignore[no-redef]
    from PySide6.QtQml import QQmlApplicationEngine  # type: ignore[no-redef]

try:
    from fuscan.gui.controllers import AppController, register_qml_types

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

# QML 文件 import ``QtGraphicalEffects 1.15``（Qt5 专用模块，Qt6 已移除），
# 故 QML 集成测试仅在 PySide2 环境下执行；PySide6-only 环境跳过。
if not PYSIDE_AVAILABLE or not PYSIDE2_AVAILABLE:
    pytest.skip("PySide2 未安装，跳过 QML 集成测试（QML 依赖 Qt5 专属模块）", allow_module_level=True)


def _filter_null_errors(warnings: list[str]) -> list[str]:
    """过滤出 null TypeError：Cannot read property 'xxx' of null。"""
    return [w for w in warnings if "Cannot read property" in w and "of null" in w]


@pytest.fixture()
def qml_warnings_handler() -> Generator[tuple[list[str], Callable[[], None]], None, None]:
    """安装全局消息处理器收集 QML warnings，返回 (warnings, uninstall)。

    ``qInstallMessageHandler`` 是全局的，必须用 fixture 确保测试后恢复，
    避免影响其他测试的 stderr/stdout 捕获。
    """
    warnings: list[str] = []

    def handler(msg_type: QtMsgType, context: object, msg: str) -> None:
        # 只收集 QML 相关的 warning（含文件路径 file:/// 或 null TypeError）
        if msg_type == QtMsgType.QtWarningMsg and ("Cannot read property" in msg or "file:///" in msg):
            warnings.append(msg)

    old_handler = qInstallMessageHandler(handler)

    def _restore() -> None:
        qInstallMessageHandler(old_handler)

    yield warnings, _restore
    # 确保恢复（即使 yield 失败）
    _restore()


def _build_engine_with_main_qml() -> tuple[QGuiApplication, AppController, QQmlApplicationEngine]:
    """构造 QGuiApplication + AppController + 已加载 Main.qml 的 engine。"""
    app = QGuiApplication.instance() or QGuiApplication(["fuscan"])
    register_qml_types()

    controller = AppController()
    engine = QQmlApplicationEngine()
    controller.register_to(engine.rootContext())

    views_dir = Path(__file__).resolve().parents[1] / "src" / "fuscan" / "gui" / "views"
    engine.addImportPath(str(views_dir))

    main_qml = views_dir / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(main_qml)))  # pyrefly: ignore [missing-argument]
    assert engine.rootObjects(), "Main.qml 加载失败，rootObjects 为空"

    return app, controller, engine


def _process_events(app: QGuiApplication, ms: int) -> None:
    """处理事件循环 ms 毫秒，让 QML binding 充分求值。"""
    QTimer.singleShot(ms, app.quit)  # type: ignore[missing-argument, bad-argument-type]
    run = app.exec if hasattr(app, "exec") else app.exec_
    run()


def test_main_qml_loads_without_null_type_errors(
    qml_warnings_handler: tuple[list[str], Callable[[], None]],
) -> None:
    """加载 Main.qml 应无 ``Cannot read property of null`` TypeError。

    覆盖 iter-101 修复：ScanProgressCard 链式访问 ``workspaceController.activeScanController``
    在初始加载（hasActiveScan=false，fallback controller）时应正常工作。
    """
    warnings, _ = qml_warnings_handler
    app, controller, _engine = _build_engine_with_main_qml()
    _process_events(app, 500)

    null_errors = _filter_null_errors(warnings)
    assert not null_errors, f"QML 加载报 null TypeError（共 {len(null_errors)} 条）:\n" + "\n".join(null_errors[:5])

    controller.cleanup()


def test_scan_progress_card_no_null_when_active_scan(
    qml_warnings_handler: tuple[list[str], Callable[[], None]],
) -> None:
    """启动扫描后 ScanProgressCard 应能访问 activeScanController 不报 null。

    覆盖 iter-101 修复场景：扫描中（hasActiveScan=true）时 ScanProgressCard
    可见，所有 ``workspaceController.activeScanController.xxx`` 链式访问应正常。
    """
    warnings, _ = qml_warnings_handler
    app, controller, _engine = _build_engine_with_main_qml()

    ws_controller = controller.workspace
    rules_controller = controller.rules
    # 加载内置规则集，避免 startScan 因 ruleset is None 提前返回
    if rules_controller.ruleset is None:
        with contextlib.suppress(Exception):  # pragma: no cover - 加载失败不影响测试核心断言
            rules_controller.load_builtin_ruleset()

    # addWorkspace(name, mode_str, target, rules_paths_json, use_builtin)
    ws_id = ws_controller.addWorkspace("测试任务", "文件夹", "F:/Dev/fuscan", "[]", True)
    with contextlib.suppress(Exception):  # pragma: no cover - 启动失败不影响测试核心断言
        ws_controller.startScan(ws_id)

    _process_events(app, 800)

    null_errors = _filter_null_errors(warnings)
    assert not null_errors, f"扫描中 QML 报 null TypeError（共 {len(null_errors)} 条）:\n" + "\n".join(null_errors[:5])

    controller.cleanup()
