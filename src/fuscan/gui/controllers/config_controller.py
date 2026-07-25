"""配置控制器：QML ↔ Config 持久化桥接。

暴露 :class:`Config` 字段为 ``@Property``，QML 控件 ``onCheckedChanged``/
``onValueChanged`` 直接调用 setter 保存配置。同时管理盘符列表、扫描路径
历史与提取器勾选模型。

公共 API：

- :class:`ConfigController`：``QObject`` 子类
- :meth:`ConfigController.save`：保存配置到 YAML
- :meth:`ConfigController.add_scan_path`：添加扫描路径历史
- :meth:`ConfigController.enabled_extensions`：返回勾选提取器的扩展名集合
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]

from fuscan.config import Config, load_config, save_config
from fuscan.gui.models.extractor_model import ExtractorListModel
from fuscan.perf import set_perf_enabled

if TYPE_CHECKING:
    pass

__all__ = ["ConfigController"]

logger = logging.getLogger(__name__)


class ConfigController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """配置控制器。

    :param parent: 父 QObject
    """

    configChanged = Signal()
    scanPathsChanged = Signal()
    drivesChanged = Signal()
    extractorCountChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config: Config = load_config()
        self._extractor_model: ExtractorListModel = ExtractorListModel(self)
        self._extractor_model.load_from_registry(self._config.disabled_extractors)
        # 性能日志开关同步
        set_perf_enabled(self._config.perf_log_enabled)

    @property
    def config(self) -> Config:
        """底层 :class:`Config` 实例（供 ScanController 读取）。"""
        return self._config

    # ----------------------------- 扫描设置 -----------------------------

    @Property(bool, notify=configChanged)  # pyrefly: ignore [not-callable]
    def scanArchives(self) -> bool:
        """是否扫描压缩包。"""
        return self._config.scan_archives

    @Slot(bool)  # pyrefly: ignore [not-callable]
    def setScanArchives(self, value: bool) -> None:
        """设置是否扫描压缩包。"""
        if value != self._config.scan_archives:
            self._config.scan_archives = value
            self.save()

    @Property(int, notify=configChanged)  # pyrefly: ignore [not-callable]
    def maxWorkers(self) -> int:
        """最大工作线程数。"""
        return self._config.max_workers

    @Slot(int)  # pyrefly: ignore [not-callable]
    def setMaxWorkers(self, value: int) -> None:
        """设置最大工作线程数。"""
        if value != self._config.max_workers and 1 <= value <= 16:
            self._config.max_workers = value
            self.save()

    @Property(int, notify=configChanged)  # pyrefly: ignore [not-callable]
    def maxFileSizeMB(self) -> int:
        """最大文件大小（MB）。"""
        return self._config.max_file_size // (1024 * 1024)

    @Slot(int)  # pyrefly: ignore [not-callable]
    def setMaxFileSizeMB(self, value: int) -> None:
        """设置最大文件大小（MB）。"""
        new_size = value * 1024 * 1024
        if new_size != self._config.max_file_size and 1 <= value <= 500:
            self._config.max_file_size = new_size
            self.save()

    @Property(int, notify=configChanged)  # pyrefly: ignore [not-callable]
    def maxDepth(self) -> int:
        """最大扫描深度（0=无限）。"""
        return self._config.max_depth or 0

    @Slot(int)  # pyrefly: ignore [not-callable]
    def setMaxDepth(self, value: int) -> None:
        """设置最大扫描深度（0=无限）。"""
        new_depth = value if value > 0 else None
        if new_depth != self._config.max_depth:
            self._config.max_depth = new_depth
            self.save()

    @Property(bool, notify=configChanged)  # pyrefly: ignore [not-callable]
    def cacheEnabled(self) -> bool:
        """是否启用扫描结果缓存。"""
        return self._config.cache_enabled

    @Slot(bool)  # pyrefly: ignore [not-callable]
    def setCacheEnabled(self, value: bool) -> None:
        """设置是否启用扫描结果缓存。"""
        if value != self._config.cache_enabled:
            self._config.cache_enabled = value
            self.save()

    @Property(bool, notify=configChanged)  # pyrefly: ignore [not-callable]
    def perfLogEnabled(self) -> bool:
        """是否启用性能详细日志。"""
        return self._config.perf_log_enabled

    @Slot(bool)  # pyrefly: ignore [not-callable]
    def setPerfLogEnabled(self, value: bool) -> None:
        """设置是否启用性能详细日志。"""
        if value != self._config.perf_log_enabled:
            self._config.perf_log_enabled = value
            set_perf_enabled(value)
            self.save()

    # ----------------------------- 忽略目录 -----------------------------

    @Property(str, notify=configChanged)  # pyrefly: ignore [not-callable]
    def ignoreDirsText(self) -> str:
        """忽略目录文本（一行一个目录名）。"""
        return "\n".join(self._config.ignore_dirs)

    @Slot(str)  # pyrefly: ignore [not-callable]
    def setIgnoreDirsText(self, text: str) -> None:
        """设置忽略目录文本。"""
        new_dirs = [line.strip() for line in text.split("\n") if line.strip()]
        if new_dirs != self._config.ignore_dirs:
            self._config.ignore_dirs = new_dirs
            self.save()

    # ----------------------------- 文件类型（提取器勾选） -----------------------------

    @Property(QObject, notify=configChanged)  # pyrefly: ignore [not-callable]
    def extractorModel(self) -> ExtractorListModel:
        """提取器勾选列表模型。

        用 ``QObject`` 作为 Property 类型，避免 PySide2 元类型系统对
        ``QAbstractListModel*`` 未注册导致的 ``QMetaObjectBuilder`` 警告。
        """
        return self._extractor_model

    @Property(str, notify=extractorCountChanged)  # pyrefly: ignore [not-callable]
    def extractorCountText(self) -> str:
        """提取器勾选数文本（如 ``已勾选 12/15``）。"""
        return f"已勾选 {self._extractor_model.enabled_count}/{self._extractor_model.total_count}"

    @Slot(str, bool)  # pyrefly: ignore [not-callable]
    def setExtractorEnabled(self, class_name: str, enabled: bool) -> None:
        """QML 勾选回调：更新提取器勾选状态并保存。"""
        self._extractor_model.set_extractor_enabled(class_name, enabled)
        self._config.disabled_extractors = self._extractor_model.disabled_extractors()
        self.extractorCountChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.save()

    @Slot()  # pyrefly: ignore [not-callable]
    def selectAllExtractors(self) -> None:
        """全选提取器。"""
        self._extractor_model.select_all()
        self._config.disabled_extractors = []
        self.extractorCountChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.save()

    @Slot()  # pyrefly: ignore [not-callable]
    def unselectAllExtractors(self) -> None:
        """全不选提取器。"""
        self._extractor_model.unselect_all()
        self._config.disabled_extractors = self._extractor_model.disabled_extractors()
        self.extractorCountChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.save()

    def enabled_extensions(self) -> tuple[str, ...] | None:
        """返回勾选提取器的扩展名集合（供 ScanController 使用）。

        :return: ``None`` 表示全部勾选（扫描所有文件）；空 tuple 表示全部取消勾选；
            非空 tuple 表示按白名单过滤。详见
            :meth:`ExtractorListModel.enabled_extensions`。
        """
        return self._extractor_model.enabled_extensions()

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

    # ----------------------------- 持久化 -----------------------------

    def save(self) -> None:
        """保存配置到 YAML 文件。"""
        save_config(self._config)
        self.configChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def resetToDefaults(self) -> None:
        """重置扫描相关配置为默认值（不影响扫描路径历史与禁用提取器列表）。"""
        self._config.scan_archives = True
        self._config.max_workers = 5
        self._config.max_file_size = 50 * 1024 * 1024
        self._config.max_depth = None
        self._config.cache_enabled = True
        self._config.perf_log_enabled = False
        self._config.ignore_dirs = list(Config.__dataclass_fields__["ignore_dirs"].default_factory())  # type: ignore[index]
        set_perf_enabled(False)
        self.save()
        logger.info("配置已重置为默认值")

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
