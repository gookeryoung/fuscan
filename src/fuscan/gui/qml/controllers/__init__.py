"""QML 控制层：``QObject`` 子类，通过 ``Property``/``Signal``/``Slot`` 暴露状态与操作给 QML。

按 rule-12-pyside-dev.md 三层 MVC 分层，控制器不持有 QML 控件引用，仅通过
信号槽与 QML 通信。子模块：

- :class:`AppController`：主控制器工厂，聚合所有 controller 并注册到 QML context
- :class:`ScanController`：扫描工作流（状态机 + 进度 + 结果模型）
- :class:`ConfigController`：配置持久化 + 盘符/路径历史/提取器勾选
- :class:`RulesController`：规则文件管理 + 规则列表模型
- :class:`AboutController`：关于页信息
"""

from __future__ import annotations

from fuscan.gui.qml.controllers.about_controller import AboutController
from fuscan.gui.qml.controllers.app_controller import AppController
from fuscan.gui.qml.controllers.config_controller import ConfigController
from fuscan.gui.qml.controllers.rules_controller import RulesController
from fuscan.gui.qml.controllers.scan_controller import ScanController

__all__ = [
    "AboutController",
    "AppController",
    "ConfigController",
    "RulesController",
    "ScanController",
]
