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
- :meth:`ConfigController.save`：Debounce 保存配置到 YAML（300ms 内合并多次写入）
- :meth:`ConfigController.flush_save`：立即 flush 待写入配置（供测试与关闭应用前调用）
- :meth:`ConfigController.add_scan_path`：添加扫描路径历史
"""

from __future__ import annotations

import logging

try:
    from PySide2.QtCore import Property, QObject, QTimer, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot  # pyrefly: ignore [missing-import]

from fuscan.config import Config, load_config, save_config

__all__ = ["ConfigController"]

logger = logging.getLogger(__name__)

# 配置 debounce 保存延迟（毫秒）：300ms 内多次 save 合并为一次磁盘写入，
# 避免设置页拖动滑块/输入框时每帧触发一次 YAML 序列化导致主线程卡顿。
# configChanged 信号仍每次 emit（QML 绑定立即更新），仅磁盘写入被合并
_SAVE_DEBOUNCE_MS: int = 300


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
        # Debounce 保存定时器：None 表示尚未创建（惰性创建），
        # 创建后以 singleShot + start() 重启方式合并 300ms 内多次 save 调用
        self._save_timer: QTimer | None = None
        # 盘符列表缓存：None 表示未缓存，首次访问 drives 时填充。
        # refresh_drives 清空缓存并 emit drivesChanged，下次访问重新枚举。
        # 避免每次 QML 访问 drives 属性都调用 list_drives（Windows 上较慢）
        self._drives_cache: list[str] | None = None

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
        """字体配置变更：debounce 持久化 + 发出信号。

        ThemeController 同步由 :class:`AppController` 监听 ``fontConfigChanged``
        信号后调用 ``setFontConfig`` 完成（含最小字号），避免在 ConfigController
        中反向依赖 GUI 层 ThemeController 实例。

        通过 :meth:`save` 走 debounce 路径，避免拖动字号滑块时每帧触发
        一次 YAML 写入。``fontConfigChanged`` 在 ``save`` 之前 emit，
        保持与原实现一致的信号顺序（fontConfigChanged → configChanged）。
        """
        self.fontConfigChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.save()

    # ----------------------------- 持久化 -----------------------------

    def save(self) -> None:
        """Debounce 保存配置到 YAML 文件。

        300ms 内多次 ``save`` 调用合并为一次磁盘写入，避免设置页拖动滑块、
        输入框逐字符输入时每帧触发 YAML 序列化导致主线程卡顿。

        ``configChanged`` 信号每次都立即 emit（反映内存中 Config 已变更），
        QML 绑定即时更新；仅磁盘写入被 debounce 到最后一次调用后 300ms 执行。
        """
        self.configChanged.emit()  # pyrefly: ignore [missing-attribute]
        if self._save_timer is None:
            self._save_timer = QTimer(self)
            self._save_timer.setSingleShot(True)
            self._save_timer.setInterval(_SAVE_DEBOUNCE_MS)
            self._save_timer.timeout.connect(self._do_save)  # pyrefly: ignore [missing-attribute]
        # start() 重启计时器：已在运行时取消旧超时，按本次调用重新计时
        self._save_timer.start()  # pyrefly: ignore [missing-attribute]

    def _do_save(self) -> None:
        """执行实际的磁盘写入（由 ``_save_timer`` 超时触发）。

        分离为独立方法便于测试中直接调用以跳过 timer 等待。
        """
        save_config(self._config)

    def flush_save(self) -> None:
        """立即 flush 待写入的配置（取消 debounce timer，同步写入磁盘）。

        供测试与关闭应用前调用，确保配置已持久化。无待写入时为 no-op。
        """
        if self._save_timer is not None and self._save_timer.isActive():
            self._save_timer.stop()
            save_config(self._config)

    @Slot()  # pyrefly: ignore [not-callable]
    def resetToDefaults(self) -> None:
        """重置字体设置为默认值。"""
        self._config.font_family = None
        self._config.font_size = 14
        self._config.font_bold = False
        self._config.min_font_size = 12
        self.fontConfigChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.save()
        logger.info("字体配置已重置为默认值")

    @Slot()  # pyrefly: ignore [not-callable]
    def refresh_drives(self) -> None:
        """刷新盘符列表（清空缓存并通知 QML 重新读取 ``drives`` 属性）。"""
        self._drives_cache = None
        self.drivesChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property("QVariantList", notify=drivesChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def drives(self) -> list[str]:
        """系统可用盘符列表（如 ``["C:\\", "D:\\"]``）。

        首次访问调用 :func:`list_drives` 并缓存结果，后续访问直接返回缓存，
        避免 QML 每次 binding 求值都触发 Windows 盘符枚举开销。
        :meth:`refresh_drives` 清空缓存触发重新枚举。
        """
        if self._drives_cache is None:
            from fuscan.scanner.walker import list_drives

            try:
                self._drives_cache = [str(d) for d in list_drives(include_network=self._config.include_network_drives)]
            except OSError:
                logger.warning("盘符枚举失败", exc_info=True)
                self._drives_cache = []
        return self._drives_cache
