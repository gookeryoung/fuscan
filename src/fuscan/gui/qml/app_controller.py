"""主控制器工厂：构造并注册所有 controller 到 QML context。

单入口构造 :class:`ThemeController`/`ConfigController`/`RulesController`/
:class:`ScanController`/`AboutController`，供 ``app.py`` 调用
``engine.rootContext().setContextProperty`` 注册到 QML。

公共 API：

- :class:`AppController`：聚合所有 controller
- :meth:`AppController.register_to`：注册到 QQmlContext
- :meth:`AppController.cleanup`：窗口关闭时统一清理
"""

from __future__ import annotations

import logging

try:
    from PySide2.QtCore import QObject
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QObject  # pyrefly: ignore [missing-import]

from fuscan.gui.qml.about_controller import AboutController
from fuscan.gui.qml.config_controller import ConfigController
from fuscan.gui.qml.rules_controller import RulesController
from fuscan.gui.qml.scan_controller import ScanController
from fuscan.gui.qml.theme import ThemeController

__all__ = ["AppController"]

logger = logging.getLogger(__name__)


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
