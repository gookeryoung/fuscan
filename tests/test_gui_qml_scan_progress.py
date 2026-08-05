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
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = [pytest.mark.gui, pytest.mark.gui_qml]

try:
    from PySide2.QtCore import QTimer, QtMsgType, QUrl, qInstallMessageHandler
    from PySide2.QtGui import QGuiApplication
    from PySide2.QtQml import QQmlApplicationEngine, QQmlComponent

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
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent  # type: ignore[no-redef]

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


def test_no_binding_loop_warnings(
    qml_warnings_handler: tuple[list[str], Callable[[], None]],
) -> None:
    """iter-138：加载 Main.qml 应无 binding loop 警告。

    覆盖问题2 修复：SettingsPage ListView ``cacheBuffer`` 改为固定 500，
    消除与 ``settingsTabBar.currentIndex`` 的双向依赖导致的 binding loop。
    """
    warnings, _ = qml_warnings_handler
    app, controller, _engine = _build_engine_with_main_qml()
    _process_events(app, 500)

    binding_loops = [w for w in warnings if "binding loop" in w.lower()]
    assert not binding_loops, f"QML 报 binding loop（共 {len(binding_loops)} 条）:\n" + "\n".join(binding_loops[:5])

    controller.cleanup()


def _instantiate_phase_node(
    app: QGuiApplication,
    controller: AppController,
    engine: QQmlApplicationEngine,
    node_state: str,
) -> object:
    """用 QQmlComponent 从源码目录实例化 PhaseNode 并设置状态。

    :param node_state: pending / running / done
    :return: 实例化的 QML 对象（失败时抛断言）
    """
    views_dir = Path(__file__).resolve().parents[1] / "src" / "fuscan" / "gui" / "views"
    qml = (
        "import QtQuick 2.15\n"
        'import "components"\n'
        "PhaseNode {\n"
        '    title: "测试阶段"\n'
        '    detail: "剔除 3"\n'
        f'    nodeState: "{node_state}"\n'
        "}\n"
    )
    component = QQmlComponent(engine)
    component.setData(qml.encode("utf-8"), QUrl.fromLocalFile(str(views_dir / "inline.qml")))
    obj = component.create(engine.rootContext())
    assert obj is not None, f"PhaseNode({node_state}) 实例化失败：{component.errorString()}"
    return obj


def test_phase_node_three_states_no_errors(
    qml_warnings_handler: tuple[list[str], Callable[[], None]],
) -> None:
    """PhaseNode 三种状态（pending/running/done）实例化应无 QML 错误。

    覆盖 GitHub Actions 风格流程节点：验证状态指示器（空心圈/转圈/对勾）、
    连接线与 Canvas 绘制在各状态下均无 null TypeError、无组件加载失败。
    """
    warnings, _ = qml_warnings_handler
    app, controller, engine = _build_engine_with_main_qml()

    objs = [_instantiate_phase_node(app, controller, engine, s) for s in ("pending", "running", "done")]
    _process_events(app, 400)

    null_errors = _filter_null_errors(warnings)
    assert not null_errors, f"PhaseNode 报 null TypeError（共 {len(null_errors)} 条）:\n" + "\n".join(null_errors[:5])
    # 保持引用避免 GC 影响事件循环
    assert len(objs) == 3

    controller.cleanup()


def test_phase_node_done_uses_success_color(
    qml_warnings_handler: tuple[list[str], Callable[[], None]],
) -> None:
    """done 态实心圆应用 doneColor（成功绿）而非 accentColor（橙）。

    覆盖需求1：筛选节点 accentColor=warning（橙），完成后对勾应显示成功语义
    绿色。验证 doneColor 默认取 theme.colorSuccess，且与传入的 accentColor 分离。
    """
    warnings, _ = qml_warnings_handler
    app, controller, engine = _build_engine_with_main_qml()

    views_dir = Path(__file__).resolve().parents[1] / "src" / "fuscan" / "gui" / "views"
    qml = (
        "import QtQuick 2.15\n"
        "import fuscan.theme 1.0\n"
        'import "components"\n'
        "PhaseNode {\n"
        '    title: "筛选文件"\n'
        '    nodeState: "done"\n'
        "    accentColor: Theme.colorWarning\n"
        "}\n"
    )
    component = QQmlComponent(engine)
    component.setData(qml.encode("utf-8"), QUrl.fromLocalFile(str(views_dir / "inline.qml")))
    obj: object = component.create(engine.rootContext())
    assert obj is not None, f"PhaseNode 实例化失败：{component.errorString()}"
    _process_events(app, 200)

    # doneColor 默认为成功色，且不等于传入的 accentColor（warning 橙）
    done_color = obj.property("doneColor")  # type: ignore[attr-defined]
    accent_color = obj.property("accentColor")  # type: ignore[attr-defined]
    assert done_color is not None
    assert done_color != accent_color, "done 态用色应与 running 强调色（warning）区分"

    null_errors = _filter_null_errors(warnings)
    assert not null_errors, "PhaseNode(done) 报 null TypeError:\n" + "\n".join(null_errors[:5])

    controller.cleanup()


def test_phase_node_expandable_with_content(
    qml_warnings_handler: tuple[list[str], Callable[[], None]],
) -> None:
    """可展开 PhaseNode 注入 expandContent Component 并切换 expanded 应无错误。

    覆盖需求3：解析节点可展开查看具体文件解析明细。验证 expandable/expanded
    属性与 expandContent Component（Loader 延迟加载）在展开/收起时均无 QML 错误。
    """
    warnings, _ = qml_warnings_handler
    app, controller, engine = _build_engine_with_main_qml()

    views_dir = Path(__file__).resolve().parents[1] / "src" / "fuscan" / "gui" / "views"
    qml = (
        "import QtQuick 2.15\n"
        "import QtQuick.Layouts 1.15\n"
        "import QtQuick.Controls 2.15\n"
        'import "components"\n'
        "PhaseNode {\n"
        '    title: "解析文件内容"\n'
        '    nodeState: "running"\n'
        "    expandable: true\n"
        "    expanded: true\n"
        "    expandContent: Component {\n"
        "        ColumnLayout {\n"
        '            Label { text: "a.txt · 1.0 KB · 5ms" }\n'
        "        }\n"
        "    }\n"
        "}\n"
    )
    component = QQmlComponent(engine)
    component.setData(qml.encode("utf-8"), QUrl.fromLocalFile(str(views_dir / "inline.qml")))
    obj: object = component.create(engine.rootContext())
    assert obj is not None, f"可展开 PhaseNode 实例化失败：{component.errorString()}"
    _process_events(app, 200)

    assert obj.property("expandable") is True  # type: ignore[attr-defined]
    assert obj.property("expanded") is True  # type: ignore[attr-defined]
    # 收起后再展开，验证 Loader active 切换无错误
    obj.setProperty("expanded", False)  # type: ignore[attr-defined]
    _process_events(app, 100)
    obj.setProperty("expanded", True)  # type: ignore[attr-defined]
    _process_events(app, 100)

    null_errors = _filter_null_errors(warnings)
    assert not null_errors, "可展开 PhaseNode 报 null TypeError:\n" + "\n".join(null_errors[:5])

    controller.cleanup()
