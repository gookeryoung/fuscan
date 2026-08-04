"""配置控制器：QML ↔ Config 持久化桥接。

暴露 :class:`Config` 字段为 ``@Property``，QML 控件 ``onCheckedChanged``/
``onValueChanged`` 直接调用 setter 保存配置。同时管理盘符列表与扫描路径历史。

扫描参数（scan_archives/max_workers/max_depth/max_file_size/cache_enabled/
perf_log_enabled/ignore_dirs/disabled_extractors）已迁移到 RuleSet 顶层，
由 :class:`RulesController.effectiveConfigPreview` 暴露给 QML 只读展示，
设置页「生效配置预览」区呈现。本控制器仅保留扫描模式、路径历史、字体等
"应用级"配置。

公共 API：

- :class:`ConfigController`：``QObject`` 子类
- :meth:`ConfigController.save`：保存配置到 YAML
- :meth:`ConfigController.add_scan_path`：添加扫描路径历史
"""

from __future__ import annotations

import logging

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]

from fuscan.config import Config, load_config, save_config

__all__ = ["ConfigController"]

logger = logging.getLogger(__name__)


class ConfigController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """配置控制器。

    :param parent: 父 QObject
    """

    configChanged = Signal()
    scanPathsChanged = Signal()
    drivesChanged = Signal()
    # 字体配置变更信号：AppController 监听此信号同步到 ThemeController
    fontConfigChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config: Config = load_config()

    @property
    def config(self) -> Config:
        """底层 :class:`Config` 实例（供 ScanController 读取）。"""
        return self._config

    def get_config_value(self, key: str) -> object:
        """按 task_override 字段名读取全局配置值。

        供 :meth:`WorkspaceController.clearTaskOverride` 在清除任务级覆盖后
        回填全局值到 ScanController。``rules_paths`` 返回 tuple；
        扫描参数字段（scan_archives/max_workers 等）已迁移到 RuleSet，
        此处返回 ``None``，由调用方从 ruleset 重新读取。

        :param key: ``TASK_OVERRIDE_KEYS`` 中的字段名
        :return: 全局配置值；未知字段或已迁移字段返回 ``None``
        """
        if key == "rules_paths":
            return tuple(self._config.rules_paths)
        if key == "use_builtin":
            return self._config.use_builtin
        return None

    # ----------------------------- 扫描路径历史 -----------------------------

    @Property("QVariantList", notify=scanPathsChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def scanPaths(self) -> list[str]:
        """扫描路径历史列表。"""
        return list(self._config.scan_paths)

    @Slot(str)  # pyrefly: ignore [not-callable]
    def add_scan_path(self, path_str: str) -> None:
        """添加扫描路径到历史（去重 + 最近优先 + 限制数量）。"""
        if not path_str:
            return
        paths = [p for p in self._config.scan_paths if p != path_str]
        paths.insert(0, path_str)
        # 限制数量（与 config.MAX_HISTORY 一致）
        from fuscan.config import MAX_HISTORY

        self._config.scan_paths = paths[:MAX_HISTORY]
        self.save()
        self.scanPathsChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def clearScanPaths(self) -> None:
        """清除扫描路径历史。"""
        if self._config.scan_paths:
            self._config.scan_paths = []
            self.save()
            self.scanPathsChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property(int, notify=configChanged)  # pyrefly: ignore [not-callable]
    def cpuCount(self) -> int:
        """当前机器 CPU 逻辑核心数（供 QML 显示「当前机器最大线程=…」备注）。"""
        import os

        return os.cpu_count() or 1

    # ----------------------------- 通用设置（字体） -----------------------------

    @Property(str, notify=fontConfigChanged)  # pyrefly: ignore [not-callable]
    def fontFamily(self) -> str:
        """字体族名（空串表示使用平台默认）。"""
        return self._config.font_family or ""

    @Slot(str)  # pyrefly: ignore [not-callable]
    def setFontFamily(self, value: str) -> None:
        """设置字体族名（空串表示平台默认）。"""
        new_value = value if value else None
        if new_value != self._config.font_family:
            self._config.font_family = new_value
            self._on_font_config_changed()

    @Property(int, notify=fontConfigChanged)  # pyrefly: ignore [not-callable]
    def fontSize(self) -> int:
        """基准字号（默认 14，其他字号基于此计算）。"""
        return self._config.font_size

    @Slot(int)  # pyrefly: ignore [not-callable]
    def setFontSize(self, value: int) -> None:
        """设置基准字号（钳制到 8-32 范围）。"""
        size = max(8, min(32, value))
        if size != self._config.font_size:
            self._config.font_size = size
            self._on_font_config_changed()

    @Property(bool, notify=fontConfigChanged)  # pyrefly: ignore [not-callable]
    def fontBold(self) -> bool:
        """是否全局加粗。"""
        return self._config.font_bold

    @Slot(bool)  # pyrefly: ignore [not-callable]
    def setFontBold(self, value: bool) -> None:
        """设置是否加粗。"""
        if value != self._config.font_bold:
            self._config.font_bold = value
            self._on_font_config_changed()

    @Property(int, notify=fontConfigChanged)  # pyrefly: ignore [not-callable]
    def minFontSize(self) -> int:
        """最小字号下限（caption/small 不低于此值，默认 12）。"""
        return self._config.min_font_size

    @Slot(int)  # pyrefly: ignore [not-callable]
    def setMinFontSize(self, value: int) -> None:
        """设置最小字号下限（钳制到 8-24 范围）。"""
        size = max(8, min(24, value))
        if size != self._config.min_font_size:
            self._config.min_font_size = size
            self._on_font_config_changed()

    def _on_font_config_changed(self) -> None:
        """字体配置变更：持久化 + 发出信号。

        ThemeController 同步由 :class:`AppController` 监听 ``fontConfigChanged``
        信号后调用 ``setFontConfig`` 完成（含最小字号），避免在 ConfigController
        中反向依赖 GUI 层 ThemeController 实例。
        """
        save_config(self._config)
        self.fontConfigChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.configChanged.emit()  # pyrefly: ignore [missing-attribute]

    # ----------------------------- 持久化 -----------------------------

    def save(self) -> None:
        """保存配置到 YAML 文件。"""
        save_config(self._config)
        self.configChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def resetToDefaults(self) -> None:
        """重置字体设置为默认值。"""
        self._config.font_family = None
        self._config.font_size = 14
        self._config.font_bold = False
        self._config.min_font_size = 12
        self.save()
        self.fontConfigChanged.emit()  # pyrefly: ignore [missing-attribute]
        logger.info("字体配置已重置为默认值")

    @Slot()  # pyrefly: ignore [not-callable]
    def refresh_drives(self) -> None:
        """刷新盘符列表（QML 通过 drives 属性读取）。"""
        self.drivesChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property("QVariantList", notify=drivesChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def drives(self) -> list[str]:
        """系统可用盘符列表（如 ``["C:\\", "D:\\"]``）。"""
        from fuscan.scanner.walker import list_drives

        try:
            return [str(d) for d in list_drives(include_network=self._config.include_network_drives)]
        except OSError:
            logger.warning("盘符枚举失败", exc_info=True)
            return []
