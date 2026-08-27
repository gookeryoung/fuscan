"""PySide2 QtWidgets GUI 子包。

按 rule-12-pyside-dev.md 三层 MVC 分层：

- :mod:`fuscan.gui.controllers`：控制层（``QObject`` 子类）
- :mod:`fuscan.gui.models`：模型层（``QAbstractListModel`` 子类）
- :mod:`fuscan.gui.theme`：主题令牌（``ThemeController``，rule-12 指定位置）
- :mod:`fuscan.gui.widgets`：视图层（页面与组件）
- :mod:`fuscan.gui.severity_utils`：严重度文本/色值工具（跨层共享）

公共 API（惰性导出，避免无 GUI 环境下 import 整个包失败）：

- :class:`AppController`：主控制器工厂，聚合所有 controller 供 MainWindow 使用
- :class:`ScanController`：扫描工作流控制器（状态机 + 进度 + 结果模型）
- :class:`ConfigController`：配置持久化 + 盘符/路径历史/提取器勾选
- :class:`RulesController`：规则文件管理 + 规则列表模型
- :class:`ResultListModel`：扫描结果 QAbstractListModel
- :class:`RuleListModel`：规则列表 QAbstractListModel
- :class:`ExtractorListModel`：提取器勾选 QAbstractListModel
- :class:`AboutController`：关于页信息

.. note::
    GUI 应用入口 :func:`fuscan.app.main` 已迁出本子包（原 ``fuscan.gui.app.launch``），
    请从 :mod:`fuscan.app` 导入。
"""

from __future__ import annotations


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """惰性导入 controller/model 类，避免无 GUI 环境下 import 整个包失败。"""
    if name == "AppController":
        from fuscan.gui.controllers import AppController

        return AppController
    if name == "ScanController":
        from fuscan.gui.controllers import ScanController

        return ScanController
    if name == "ConfigController":
        from fuscan.gui.controllers import ConfigController

        return ConfigController
    if name == "RulesController":
        from fuscan.gui.controllers import RulesController

        return RulesController
    if name == "AboutController":
        from fuscan.gui.controllers import AboutController

        return AboutController
    if name == "ThemeController":
        from fuscan.gui.theme import ThemeController

        return ThemeController
    if name in {"ResultListModel", "RuleListModel", "ExtractorListModel"}:
        from fuscan.gui import models

        return getattr(models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
