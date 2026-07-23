"""提取器勾选模型（Model/View 架构）。

将文件类型勾选区的数据与状态从 ``main_window.py`` 拆分到独立的
``ExtractorListModel``，遵循 rule-12「大数据量优先用 QAbstractItemModel」
约束。主窗口仅负责模型构造、信号路由与配置持久化，勾选区视图逻辑
内聚到本模块。

公共 API：

- :class:`ExtractorItem`：单个提取器条目（frozen dataclass）
- :class:`ExtractorListModel`：``QAbstractListModel`` 子类，存储提取器元数据
  与勾选状态，提供 ``disabled_extractors`` / ``set_disabled_extractors`` /
  ``enabled_extensions`` API，勾选状态变化时发出 ``extractors_changed`` 信号
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt, Signal
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal  # pyrefly: ignore [missing-import]

if TYPE_CHECKING:
    from fuscan.extractors.base import ExtractorRegistry

__all__ = ["ExtractorItem", "ExtractorListModel"]


@dataclass(frozen=True)
class ExtractorItem:
    """提取器条目：类名 + 中文显示名 + 支持的扩展名集合。

    ``display_name`` 已包含主要扩展名信息（如 "Word（DOCX）"），
    故 :attr:`display_text` 仅展示 ``display_name`` 保持视觉紧凑；
    完整扩展名列表通过 :attr:`tooltip_text` 在鼠标悬停时展示。
    """

    class_name: str
    display_name: str
    extensions: tuple[str, ...]

    @property
    def display_text(self) -> str:
        """返回 QListView 中展示的文本：仅 ``display_name``（扩展名信息已在 display_name 中体现）。"""
        return self.display_name

    @property
    def tooltip_text(self) -> str:
        """返回鼠标悬停提示文本：列出所有扩展名。"""
        return f"扩展名: {', '.join(self.extensions)}"


class ExtractorListModel(QAbstractListModel):  # pyrefly: ignore [invalid-inheritance]
    """提取器勾选区模型：存储提取器元数据与勾选状态。

    模型从 :class:`ExtractorRegistry` 加载所有已注册提取器，每个 item
    默认勾选（``Qt.Checked``）。用户在 QListView 中点击 checkbox 时，
    :meth:`setData` 更新内部状态并发出 ``dataChanged`` 与
    ``extractors_changed`` 信号，主窗口连接后者持久化到配置文件。

    主窗口通过 :meth:`disabled_extractors` 读取禁用列表写入 Config，
    通过 :meth:`set_disabled_extractors` 在启动时恢复勾选状态，
    通过 :meth:`enabled_extensions` 在扫描时计算启用的扩展名集合
    （全部启用返回 ``None``，Scanner 走快速路径）。
    """

    # 勾选状态变化信号：主窗口连接此信号持久化 disabled_extractors
    extractors_changed = Signal()

    def __init__(self, registry: ExtractorRegistry, parent=None) -> None:  # type: ignore[no-untyped-def]
        """初始化模型：从 registry.list_extractors() 加载提取器条目。

        :param registry: 提取器注册表
        :param parent: 父 QObject
        """
        super().__init__(parent)
        self._items: list[ExtractorItem] = [
            ExtractorItem(class_name=cn, display_name=dn, extensions=exts)
            for cn, dn, exts in registry.list_extractors()
        ]
        # 勾选状态列表与 _items 一一对应；True=启用，False=禁用
        self._enabled_flags: list[bool] = [True] * len(self._items)

    # ----------------------------- QAbstractListModel 必填 -----------------------------

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        """返回条目数。父索引有效时返回 0（列表模型无层级）。"""
        if parent is not None and parent.isValid():
            return 0
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:
        """返回指定 index 与 role 的数据。

        支持的角色：

        - ``Qt.DisplayRole``：展示文本 ``{display_name} ({ext_hint})``
        - ``Qt.ToolTipRole``：全部扩展名提示
        - ``Qt.CheckStateRole``：勾选状态（Checked/Unchecked）
        """
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        item = self._items[index.row()]
        if role == Qt.DisplayRole:
            return item.display_text
        if role == Qt.ToolTipRole:
            return item.tooltip_text
        if role == Qt.CheckStateRole:
            return Qt.Checked if self._enabled_flags[index.row()] else Qt.Unchecked
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        """返回 item 标志：启用 + 可勾选 + 可选择。"""
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable

    def setData(self, index: QModelIndex, value: object, role: int = Qt.EditRole) -> bool:
        """更新 item 数据。仅处理 ``Qt.CheckStateRole`` 的勾选切换。

        :returns: 是否成功更新（未变化返回 False 不发信号）
        """
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return False
        if role != Qt.CheckStateRole:
            return False
        # value 可能是 Qt.CheckState 枚举或 int（PySide2/6 兼容），统一用 == 比较
        new_enabled = value == Qt.Checked
        if new_enabled == self._enabled_flags[index.row()]:
            return False
        self._enabled_flags[index.row()] = new_enabled
        self.dataChanged.emit(index, index, [role])
        self.extractors_changed.emit()  # pyrefly: ignore [missing-attribute]
        return True

    # ----------------------------- 公共 API -----------------------------

    def disabled_extractors(self) -> list[str]:
        """返回当前禁用的提取器类名列表（用于持久化到 Config.disabled_extractors）。"""
        return [item.class_name for item, enabled in zip(self._items, self._enabled_flags) if not enabled]

    def set_disabled_extractors(self, class_names: list[str]) -> None:
        """根据类名列表批量设置禁用状态（用于启动时恢复配置）。

        未在模型中的类名忽略（兼容旧版配置中已删除的提取器）。
        先更新内部状态再一次性发出 ``dataChanged`` 与 ``extractors_changed``，
        确保 emit 时视图读取到的已是最新数据。
        """
        disabled_set = set(class_names)
        changed = False
        new_flags = []
        for item, prev in zip(self._items, self._enabled_flags):
            new_enabled = item.class_name not in disabled_set
            if new_enabled != prev:
                changed = True
            new_flags.append(new_enabled)
        if not changed:
            return
        # 先更新数据，再 emit 信号（Qt Model/View 规范：emit 时数据须已最新）
        self._enabled_flags = new_flags
        if self._items:
            top = self.index(0)
            bottom = self.index(len(self._items) - 1)
            self.dataChanged.emit(top, bottom, [Qt.CheckStateRole])
        self.extractors_changed.emit()  # pyrefly: ignore [missing-attribute]

    def enabled_extensions(self) -> tuple[str, ...] | None:
        """根据勾选状态计算启用的扩展名集合。

        :returns: 全部启用时返回 ``None``（Scanner 走快速路径）；
                  部分取消时返回启用扩展名并集（小写、去重、排序后元组）
        """
        if all(self._enabled_flags):
            return None
        enabled: set[str] = set()
        for item, enabled_flag in zip(self._items, self._enabled_flags):
            if enabled_flag:
                enabled.update(item.extensions)
        return tuple(sorted(enabled))

    # ----------------------------- 测试与诊断辅助 -----------------------------

    def item_at(self, row: int) -> ExtractorItem:
        """返回指定行的条目（仅用于测试与诊断）。"""
        return self._items[row]

    def row_count(self) -> int:
        """返回条目数（仅用于测试与诊断，与 rowCount 等价但无需 QModelIndex）。"""
        return len(self._items)
