"""QML 子包：控制器、模型与主题令牌。

按 rule-12-pyside-dev.md 三层 MVC 分层：

- :mod:`fuscan.gui.qml.controllers`：控制层（``QObject`` 子类）
- :mod:`fuscan.gui.qml.models`：模型层（``QAbstractListModel`` 子类）
- :mod:`fuscan.gui.qml.theme`：主题令牌（``ThemeController``，rule-12 指定位置）
- :mod:`fuscan.gui.qml.severity_utils`：严重度文本/色值工具（跨层共享）

公共 API（顶层导出，保持向后兼容）：

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

from fuscan.gui.qml.controllers import (
    AboutController,
    AppController,
    ConfigController,
    RulesController,
    ScanController,
)
from fuscan.gui.qml.models import ExtractorListModel, ResultListModel, RuleListModel
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
