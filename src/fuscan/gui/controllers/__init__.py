"""GUI 控制层：``QObject`` 子类，通过 ``Signal``/``Slot`` 暴露状态与操作给 Widgets 页面。

按 rule-12-pyside-dev.md 三层 MVC 分层，控制器不持有页面控件引用，仅通过
信号槽与 Widgets 页面通信。子模块：

- :class:`AppController`：主控制器工厂，聚合所有 controller 供 ``app.py`` 构造 MainWindow
- :class:`SplashController`：启动画面阶段文本状态机（SplashWindow 绑定）
- :class:`ScanController`：扫描工作流（状态机 + 进度 + 结果模型）
- :class:`ConfigController`：配置持久化 + 盘符/路径历史/提取器勾选
- :class:`RulesController`：规则文件管理 + 规则列表模型
- :class:`WorkspaceController`：工作区管理（多任务）
- :class:`WhitelistController`：误报白名单管理（路径 glob + 规则名）
- :class:`AboutController`：关于页信息
- :class:`FileMonitorController`：文件监控（watchdog 事件驱动 + 实时命中推送）

子模块按需惰性加载（PEP 562 ``__getattr__``），避免 ``import fuscan.gui.controllers``
触发全量 controller 链（FileMonitorController 会拉起 scanner 链；ScanController 会
拉起 export.report 等）。与 :mod:`fuscan.gui` 顶层 ``__getattr__`` 同样的策略。
"""

from __future__ import annotations

import importlib

__all__ = [
    "AboutController",
    "AppController",
    "ConfigController",
    "FileMonitorController",
    "RulesController",
    "ScanController",
    "SplashController",
    "WhitelistController",
    "WorkspaceController",
]

# 符号名 → 所在子模块路径。按名访问时惰性加载对应子模块并缓存到模块全局。
_LAZY_MODULES: dict[str, str] = {
    "AboutController": "fuscan.gui.controllers.about_controller",
    "AppController": "fuscan.gui.controllers.app_controller",
    "ConfigController": "fuscan.gui.controllers.config_controller",
    "FileMonitorController": "fuscan.gui.controllers.file_monitor_controller",
    "RulesController": "fuscan.gui.controllers.rules_controller",
    "ScanController": "fuscan.gui.controllers.scan_controller",
    "SplashController": "fuscan.gui.controllers.splash_controller",
    "WhitelistController": "fuscan.gui.controllers.whitelist_controller",
    "WorkspaceController": "fuscan.gui.controllers.workspace_controller",
}


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """惰性加载 controller 类，避免 ``import fuscan.gui.controllers`` 触发全量加载。

    与 :mod:`fuscan.gui` 顶层 ``__getattr__`` 同样的 PEP 562 模式：仅在按名访问
    具体符号时才加载对应子模块。使 ``from fuscan.gui.controllers import AppController``
    不连带加载 FileMonitorController（拉起 scanner 链）等重型依赖，缩短 GUI 启动期
    与测试导入耗时。首次访问后缓存到模块全局，后续为直接字典命中。
    """
    module_path = _LAZY_MODULES.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value  # 缓存到模块全局，后续访问直接命中
    return value
