"""QML 控制器与数据模型子包。

公共 API：

- :class:`ThemeController`：主题令牌双向绑定（暗色模式 + 色彩/排版/间距）
- :class:`AppController`：主控制器工厂，构造并注册所有 controller 到 QML context
- :class:`ScanController`：扫描工作流控制器（状态机 + 进度 + 结果模型）
- :class:`ConfigController`：配置持久化 + 盘符/路径历史/提取器勾选
- :class:`RulesController`：规则文件管理 + 规则列表模型
- :class:`ResultListModel`：扫描结果 QAbstractListModel
- :class:`RuleListModel`：规则列表 QAbstractListModel
- :class:`ExtractorListModel`：提取器勾选 QAbstractListModel
- :class:`AboutController`：关于页信息
"""

from __future__ import annotations

from fuscan.gui.qml.about_controller import AboutController
from fuscan.gui.qml.app_controller import AppController
from fuscan.gui.qml.config_controller import ConfigController
from fuscan.gui.qml.extractor_model import ExtractorListModel
from fuscan.gui.qml.result_model import ResultListModel
from fuscan.gui.qml.rule_model import RuleListModel
from fuscan.gui.qml.rules_controller import RulesController
from fuscan.gui.qml.scan_controller import ScanController
from fuscan.gui.qml.theme import ThemeController

__all__ = [
    "AboutController",
    "AppController",
    "ConfigController",
    "ExtractorListModel",
    "ResultListModel",
    "RuleListModel",
    "RulesController",
    "ScanController",
    "ThemeController",
]
