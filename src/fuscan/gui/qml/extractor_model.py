"""提取器勾选列表模型（QAbstractListModel）。

扁平化展示所有提取器（按 display_name 排序），每行包含类名、显示名、
扩展名、速度档次（文本/色值）与勾选状态。供 QML ``ListView`` 直接绑定。

替代旧版 widget 时期的 ``ExtractorTreeModel``（树形分组），扁平化更
适合 QML 列表渲染且简化父子联动逻辑。

公共 API：

- :class:`ExtractorListModel`：``QAbstractListModel`` 子类
- :meth:`ExtractorListModel.load_from_registry`：从默认注册表加载所有提取器
- :meth:`ExtractorListModel.set_disabled_extractors`：按 Config.disabled_extractors 配置勾选
- :meth:`ExtractorListModel.disabled_extractors`：返回当前未勾选的提取器类名列表
- :meth:`ExtractorListModel.enabled_extensions`：返回勾选提取器的扩展名集合
- :meth:`ExtractorListModel.set_extractor_enabled`：QML 勾选回调
- :meth:`ExtractorListModel.select_all` / :meth:`unselect_all`：全选/全不选
"""

from __future__ import annotations

import re

try:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt  # pyrefly: ignore [missing-import]

from fuscan.extractors.base import SpeedTier, default_registry
from fuscan.gui.qml._severity_utils import severity_color_hex  # noqa: F401 - 保持引用一致

__all__ = ["ExtractorListModel"]

# QML role 名称
_ROLE_CLASS_NAME = b"className"
_ROLE_DISPLAY_NAME = b"displayName"
_ROLE_EXTENSIONS = b"extensions"
_ROLE_SPEED_TIER_TEXT = b"speedTierText"
_ROLE_SPEED_TIER_COLOR = b"speedTierColor"
_ROLE_ENABLED = b"enabled"

_ROLES: dict[int, bytes] = {
    Qt.UserRole + 1: _ROLE_CLASS_NAME,
    Qt.UserRole + 2: _ROLE_DISPLAY_NAME,
    Qt.UserRole + 3: _ROLE_EXTENSIONS,
    Qt.UserRole + 4: _ROLE_SPEED_TIER_TEXT,
    Qt.UserRole + 5: _ROLE_SPEED_TIER_COLOR,
    Qt.UserRole + 6: _ROLE_ENABLED,
}

# 去掉 display_name 中的全角括号后缀（如 "Word（DOCX）" → "Word"）
_PAREN_RE = re.compile(r"（[^）]*）")

_SPEED_TIER_TEXT: dict[SpeedTier, str] = {
    SpeedTier.VERY_FAST: "T1 极速",
    SpeedTier.FAST: "T2 快速",
    SpeedTier.MEDIUM: "T3 中速",
    SpeedTier.SLOW: "T4 慢速",
    SpeedTier.VERY_SLOW: "T5 极慢",
}

_SPEED_TIER_COLOR: dict[SpeedTier, str] = {
    SpeedTier.VERY_FAST: "#28A745",
    SpeedTier.FAST: "#17A2B8",
    SpeedTier.MEDIUM: "#FFC107",
    SpeedTier.SLOW: "#FD7E14",
    SpeedTier.VERY_SLOW: "#DC3545",
}


class _ExtractorRow:
    """提取器行数据（内部可变容器）。"""

    __slots__ = ("class_name", "display_name", "enabled", "extensions", "speed_tier")

    def __init__(
        self,
        class_name: str,
        display_name: str,
        extensions: tuple[str, ...],
        speed_tier: SpeedTier,
        enabled: bool,
    ) -> None:
        self.class_name = class_name
        # 去掉全角括号后缀
        self.display_name = _PAREN_RE.sub("", display_name).strip()
        self.extensions = extensions
        self.speed_tier = speed_tier
        self.enabled = enabled


class ExtractorListModel(QAbstractListModel):  # pyrefly: ignore [invalid-inheritance]
    """提取器勾选列表模型（扁平化，按 display_name 排序）。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._rows: list[_ExtractorRow] = []

    def rowCount(self, parent: QModelIndex = None) -> int:  # type: ignore[assignment]
        if parent is None:
            parent = QModelIndex()
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, bytes]:
        return _ROLES

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return ""
        row = self._rows[index.row()]
        if role == Qt.UserRole + 1:
            return row.class_name
        if role == Qt.UserRole + 2:
            return row.display_name
        if role == Qt.UserRole + 3:
            return ", ".join(row.extensions)
        if role == Qt.UserRole + 4:
            return _SPEED_TIER_TEXT[row.speed_tier]
        if role == Qt.UserRole + 5:
            return _SPEED_TIER_COLOR[row.speed_tier]
        if role == Qt.UserRole + 6:
            return row.enabled
        return ""

    # ----------------------------- 公共 API -----------------------------

    def load_from_registry(self, disabled_extractors: list[str] | None = None) -> None:
        """从默认注册表加载所有提取器，按 disabled_extractors 配置勾选。

        :param disabled_extractors: 未勾选的提取器类名列表（来自 ``Config.disabled_extractors``）
        """
        disabled_set = set(disabled_extractors or [])
        self.beginResetModel()
        self._rows = []
        for class_name, display_name, extensions, speed_tier in default_registry.list_extractors():
            self._rows.append(
                _ExtractorRow(
                    class_name=class_name,
                    display_name=display_name,
                    extensions=extensions,
                    speed_tier=speed_tier,
                    enabled=class_name not in disabled_set,
                )
            )
        self.endResetModel()

    def set_disabled_extractors(self, disabled: list[str]) -> None:
        """按 disabled 列表批量更新勾选状态。"""
        disabled_set = set(disabled)
        for i, row in enumerate(self._rows):
            new_enabled = row.class_name not in disabled_set
            if row.enabled != new_enabled:
                row.enabled = new_enabled
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, [Qt.UserRole + 6])

    def disabled_extractors(self) -> list[str]:
        """返回未勾选的提取器类名列表。"""
        return [row.class_name for row in self._rows if not row.enabled]

    def enabled_extensions(self) -> tuple[str, ...]:
        """返回勾选提取器的扩展名集合（用于扫描时白名单过滤）。

        全部勾选时返回空 tuple（表示扫描所有文件，与原 ``ContentTabPanel`` 行为一致）。
        """
        if all(row.enabled for row in self._rows):
            return ()
        exts: list[str] = []
        for row in self._rows:
            if row.enabled:
                exts.extend(row.extensions)
        return tuple(sorted(set(exts)))

    def set_extractor_enabled(self, class_name: str, enabled: bool) -> None:
        """QML 勾选回调：按类名更新勾选状态。"""
        for i, row in enumerate(self._rows):
            if row.class_name == class_name:
                if row.enabled != enabled:
                    row.enabled = enabled
                    idx = self.index(i)
                    self.dataChanged.emit(idx, idx, [Qt.UserRole + 6])
                return

    def select_all(self) -> None:
        """全选。"""
        self._set_all_enabled(True)

    def unselect_all(self) -> None:
        """全不选。"""
        self._set_all_enabled(False)

    @property
    def total_count(self) -> int:
        """提取器总数。"""
        return len(self._rows)

    @property
    def enabled_count(self) -> int:
        """已勾选提取器数。"""
        return sum(1 for row in self._rows if row.enabled)

    # ----------------------------- 内部方法 -----------------------------

    def _set_all_enabled(self, enabled: bool) -> None:
        """批量设置所有提取器勾选状态并 emit dataChanged。"""
        for row in self._rows:
            row.enabled = enabled
        if self._rows:
            top = self.index(0)
            bottom = self.index(len(self._rows) - 1)
            self.dataChanged.emit(top, bottom, [Qt.UserRole + 6])
