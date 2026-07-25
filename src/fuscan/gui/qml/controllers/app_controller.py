"""主控制器工厂：构造并注册所有 controller 到 QML context。

单入口构造 :class:`ThemeController`/`ConfigController`/`RulesController`/
:class:`ScanController`/`AboutController`，供 ``app.py`` 调用
``engine.rootContext().setContextProperty`` 注册到 QML。

公共 API：

- :class:`AppController`：聚合所有 controller
- :func:`register_qml_types`：将 controller 类型注册到 QML 引擎（必须在 ``engine.load`` 前调用）
- :meth:`AppController.register_to`：注册到 QQmlContext
- :meth:`AppController.cleanup`：窗口关闭时统一清理

注册类型的必要性：``setContextProperty`` 注册的 QObject 实例，QML 编译器无法在
编译时推断其类型，导致绑定 ``Theme.isDark`` 在初始求值时把 ``Theme`` 当成 ``null``，
输出大量 ``Cannot read property 'xxx' of null`` TypeError。用 ``qmlRegisterType``
注册类型后，QML 文件可 ``import fuscan.controllers 1.0`` 并声明
``property ThemeController theme: Theme``，编译器据此生成正确的类型化访问代码，
消除 TypeError。
"""

from __future__ import annotations

import logging
from functools import lru_cache

try:
    from PySide2.QtCore import QObject
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QObject  # pyrefly: ignore [missing-import]

from fuscan.gui.qml.controllers.about_controller import AboutController
from fuscan.gui.qml.controllers.config_controller import ConfigController
from fuscan.gui.qml.controllers.rules_controller import RulesController
from fuscan.gui.qml.controllers.scan_controller import ScanController
from fuscan.gui.qml.models import ExtractorListModel, ResultListModel, RuleListModel
from fuscan.gui.qml.theme import ThemeController

__all__ = ["AppController", "register_qml_types"]

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def register_qml_types() -> None:
    """将所有 controller 与 model 类型注册到 QML 引擎。

    在 ``QQmlApplicationEngine`` 构造前调用一次，使 QML 文件能通过
    ``import fuscan.controllers 1.0`` / ``import fuscan.models 1.0`` /
    ``import fuscan.theme 1.0`` 导入类型，声明类型化 property 访问
    context property，消除 ``setContextProperty`` 导致的 TypeError。

    幂等：``lru_cache`` 保证多次调用只注册一次（多次注册会触发 Qt 警告）。
    """
    try:
        from PySide2.QtQml import qmlRegisterType
    except ImportError:  # pragma: no cover
        from PySide6.QtQml import qmlRegisterType  # pyrefly: ignore [missing-import]

    # URI=fuscan.theme，QML 用 `import fuscan.theme 1.0` 后用 ThemeController 类型
    # ThemeController 类型名与 context property 名 "Theme" 不同，无冲突
    # pyrefly stub 将 URI/typeName 参数标注为 bytes，实际运行时接受 str，故忽略类型检查
    qmlRegisterType(ThemeController, "fuscan.theme", 1, 0, "ThemeController")  # pyrefly: ignore [bad-argument-type]
    # URI=fuscan.controllers，类型名加 Type 后缀避免与同名 context property 冲突
    # （QML 编译器会把 property XxxController x: XxxController 右侧的 XxxController 解析为类型名而非 context property）
    qmlRegisterType(ConfigController, "fuscan.controllers", 1, 0, "ConfigControllerType")  # pyrefly: ignore [bad-argument-type]
    qmlRegisterType(RulesController, "fuscan.controllers", 1, 0, "RulesControllerType")  # pyrefly: ignore [bad-argument-type]
    qmlRegisterType(ScanController, "fuscan.controllers", 1, 0, "ScanControllerType")  # pyrefly: ignore [bad-argument-type]
    qmlRegisterType(AboutController, "fuscan.controllers", 1, 0, "AboutControllerType")  # pyrefly: ignore [bad-argument-type]
    # URI=fuscan.models，QML 用 `import fuscan.models 1.0` 后用各 model 类型
    qmlRegisterType(ExtractorListModel, "fuscan.models", 1, 0, "ExtractorListModel")  # pyrefly: ignore [bad-argument-type]
    qmlRegisterType(RuleListModel, "fuscan.models", 1, 0, "RuleListModel")  # pyrefly: ignore [bad-argument-type]
    qmlRegisterType(ResultListModel, "fuscan.models", 1, 0, "ResultListModel")  # pyrefly: ignore [bad-argument-type]


class AppController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """主控制器聚合：构造所有 controller 并注册到 QML context。

    :param parent: 父 QObject
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # 构造顺序：theme → config → rules → scan → about
        # theme 不依赖其他；config 不依赖其他；rules 依赖 config；scan 依赖 config+rules；about 独立
        self._theme = ThemeController(self)
        self._config = ConfigController(self)
        self._rules = RulesController(self._config, self)
        self._scan = ScanController(self._config, self._rules, self)
        self._about = AboutController(self)

    @property
    def theme(self) -> ThemeController:
        """主题控制器。"""
        return self._theme

    @property
    def config(self) -> ConfigController:
        """配置控制器。"""
        return self._config

    @property
    def rules(self) -> RulesController:
        """规则控制器。"""
        return self._rules

    @property
    def scan(self) -> ScanController:
        """扫描控制器。"""
        return self._scan

    @property
    def about(self) -> AboutController:
        """关于控制器。"""
        return self._about

    def register_to(self, context: object) -> None:
        """注册所有 controller 到 QQmlContext（以 QML 可见的名字）。

        QML 中通过 ``Theme`` / ``ConfigController`` / ``RulesController`` /
        ``ScanController`` / ``AboutController`` 直接访问。

        :param context: ``QQmlContext`` 实例
        """
        context.setContextProperty("Theme", self._theme)  # pyrefly: ignore [missing-attribute]
        context.setContextProperty("ConfigController", self._config)  # pyrefly: ignore [missing-attribute]
        context.setContextProperty("RulesController", self._rules)  # pyrefly: ignore [missing-attribute]
        context.setContextProperty("ScanController", self._scan)  # pyrefly: ignore [missing-attribute]
        context.setContextProperty("AboutController", self._about)  # pyrefly: ignore [missing-attribute]

    def cleanup(self) -> None:
        """窗口关闭时清理资源（扫描 worker + 缓存）。"""
        self._scan.cleanup()
