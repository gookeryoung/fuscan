"""主控制器工厂：构造并聚合所有 controller。

单入口构造 :class:`ThemeController`/`ConfigController`/`RulesController`/
:class:`WorkspaceController`/`AboutController`/`WhitelistController`/
`FileMonitorController`，供 ``app.py`` 构造 ``MainWindow`` 使用。

公共 API：

- :class:`AppController`：聚合所有 controller
- :meth:`AppController.cleanup`：窗口关闭时统一清理
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide2.QtCore import QObject

if TYPE_CHECKING:
    # 仅用于属性返回类型注解的 controller 类型（``from __future__ import annotations``
    # 使注解为字符串，运行时不求值）。运行时在 __init__ 内延迟导入，
    # 使 ``from fuscan.gui.controllers import AppController`` 不触发任何 controller 模块加载：
    # ScanController 顶层拉起 scanner 链（``from fuscan.scanner import ScanReport``），
    # WorkspaceController 间接拉起（导入 scan_controller），FileMonitorController 拉起
    # ``fuscan.scanner.scanner``。
    from fuscan.gui.controllers.about_controller import AboutController
    from fuscan.gui.controllers.config_controller import ConfigController
    from fuscan.gui.controllers.file_monitor_controller import FileMonitorController
    from fuscan.gui.controllers.rules_controller import RulesController
    from fuscan.gui.controllers.whitelist_controller import WhitelistController
    from fuscan.gui.controllers.workspace_controller import WorkspaceController
    from fuscan.gui.theme import ThemeController

__all__ = ["AppController"]

logger = logging.getLogger(__name__)


class AppController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """主控制器聚合：构造所有 controller 并接管生命周期。

    :param parent: 父 QObject
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # 延迟导入：构造时才加载 controller 模块（含 scanner 链），避免类定义期触发。
        # ScanController/WorkspaceController/FileMonitorController 均会拉起 scanner 链。
        from fuscan.gui.controllers.about_controller import AboutController
        from fuscan.gui.controllers.config_controller import ConfigController
        from fuscan.gui.controllers.file_monitor_controller import FileMonitorController
        from fuscan.gui.controllers.rules_controller import RulesController
        from fuscan.gui.controllers.whitelist_controller import WhitelistController
        from fuscan.gui.controllers.workspace_controller import WorkspaceController
        from fuscan.gui.theme import ThemeController

        # 构造顺序：theme → config → rules → whitelist → workspace → about
        # theme 不依赖其他；config 不依赖其他；rules 依赖 config；
        # whitelist 独立（仅持有 WhitelistStore）；
        # workspace 依赖 config+rules+whitelist（内部按需构造 ScanController）；about 独立
        self._theme = ThemeController(self)
        self._config = ConfigController(self)
        self._rules = RulesController(self._config, self)
        self._whitelist = WhitelistController(self)
        # 延迟注入 RulesController：WhitelistController.addEntry 委托
        # RulesController.appendWhitelistEntry 写入 user-scan.yaml 的 whitelist 段
        self._whitelist.set_rules_controller(self._rules)
        self._workspace = WorkspaceController(self._config, self._rules, self, whitelist_controller=self._whitelist)
        # 延迟注入 WorkspaceController：RulesController 在 WorkspaceController 之前构造，
        # 通过 set_workspace_controller 注入后即可访问当前工作区以管理临时规则
        self._rules.set_workspace_controller(self._workspace)
        self._about = AboutController(self)
        # FileMonitorController 依赖 RulesController（构造期读取当前 ruleset，
        # 连接 rulesetChanged 信号），在 about 之后构造（不依赖 workspace）。
        # scan_async=True：监控扫描在单 worker 守护线程池后台执行，
        # 防止大文件（PDF/OCR 等）同步扫描阻塞 GUI 主线程导致界面卡死
        self._file_monitor = FileMonitorController(self._rules, self, scan_async=True)
        # 从用户配置注入字体设置到 ThemeController（Widgets 各页读 theme.fontSize* 刷新）
        self._apply_font_config_to_theme()
        # 监听 ConfigController 字体变更信号，实时同步到 ThemeController
        self._config.fontConfigChanged.connect(self._apply_font_config_to_theme)  # pyrefly: ignore [missing-attribute]

    def _apply_font_config_to_theme(self) -> None:
        """将 ConfigController 的字体配置同步到 ThemeController。"""
        cfg = self._config.config
        self._theme.setFontConfig(
            cfg.font_family or "",
            cfg.font_size,
            cfg.font_bold,
            cfg.min_font_size,
        )
        # 同步全局 QApplication 字体（Widgets 控件默认继承）
        from PySide2.QtGui import QFont, QGuiApplication

        app = QGuiApplication.instance()
        if app is not None:
            from fuscan.gui.theme import detect_font_families

            font = QFont()
            if cfg.font_family:
                font.setFamily(cfg.font_family)
            else:
                font.setFamilies(list(detect_font_families()))
            font.setPixelSize(cfg.font_size)
            if cfg.font_bold:
                font.setBold(True)
            app.setFont(font)

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
    def workspace(self) -> WorkspaceController:
        """工作区控制器。"""
        return self._workspace

    @property
    def whitelist(self) -> WhitelistController:
        """白名单控制器。"""
        return self._whitelist

    @property
    def about(self) -> AboutController:
        """关于控制器。"""
        return self._about

    @property
    def file_monitor(self) -> FileMonitorController:
        """文件监控控制器。"""
        return self._file_monitor

    def cleanup(self) -> None:
        """窗口关闭时清理资源（工作区 ScanController + 缓存 + 文件监控 Observer）。"""
        self._file_monitor.cleanup()
        self._workspace.cleanup()
