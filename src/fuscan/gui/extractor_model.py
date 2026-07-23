"""提取器勾选树形模型（Model/View 架构）。

将文件类型勾选区的数据与状态从 ``main_window.py`` 拆分到独立的
``ExtractorTreeModel``，遵循 rule-12「大数据量优先用 QAbstractItemModel」
约束。树形结构按父类别（文档/表格/演示/邮件/纯文本/源代码/配置文件/
标记与数据/样式表/压缩包）分组，支持父子勾选联动：
父节点勾选时全选/全取消子项，子项变化时父节点自动更新为全选/部分/全不选。

iter-88 起将原"纯文本"分类（57 个扩展名）拆分为 5 个独立分类：
纯文本/源代码/配置文件/标记与数据/样式表，各自对应一个子提取器。

公共 API：

- :class:`ExtractorItem`：单个提取器条目（frozen dataclass）
- :class:`ExtractorTreeModel`：``QAbstractItemModel`` 子类，树形存储提取器元数据
  与勾选状态，提供 ``disabled_extractors`` / ``set_disabled_extractors`` /
  ``enabled_extensions`` / ``checked_count`` API，勾选状态变化时发出
  ``extractors_changed`` 信号
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal  # pyrefly: ignore [missing-import]

if TYPE_CHECKING:
    from fuscan.extractors.base import ExtractorRegistry

__all__ = ["ExtractorItem", "ExtractorTreeModel"]

# 提取器类名 → 父类别名映射（iter-78：树形分组）
# iter-88：原 TextExtractor（57 扩展名）拆分为 5 个子提取器，各自独立分类
_EXTRACTOR_CATEGORIES: dict[str, str] = {
    # 文档
    "PdfExtractor": "文档",
    "DocxExtractor": "文档",
    "DocExtractor": "文档",
    "OdtExtractor": "文档",
    "RtfExtractor": "文档",
    "WpsExtractor": "文档",
    # 表格
    "XlsxExtractor": "表格",
    "XlsExtractor": "表格",
    "OdsExtractor": "表格",
    # 演示
    "PptxExtractor": "演示",
    "PptExtractor": "演示",
    # 邮件
    "EmlExtractor": "邮件",
    "MsgExtractor": "邮件",
    # 纯文本子提取器（iter-88 拆分）
    "PlainTextExtractor": "纯文本",
    "SourceCodeExtractor": "源代码",
    "ConfigFileExtractor": "配置文件",
    "MarkupDataExtractor": "标记与数据",
    "StylesheetExtractor": "样式表",
}

# 父类别显示顺序（iter-79：新增"压缩包"分类；iter-88：新增 5 个文本子分类）
_CATEGORY_ORDER: tuple[str, ...] = (
    "文档",
    "表格",
    "演示",
    "邮件",
    "纯文本",
    "源代码",
    "配置文件",
    "标记与数据",
    "样式表",
    "压缩包",
)

# 压缩包分类常量：虚拟项 class_name，用于在 disabled_extractors 中标识
_ARCHIVE_CLASS_NAME = "ArchiveFiles"
_ARCHIVE_CATEGORY = "压缩包"
_ARCHIVE_DISPLAY_NAME = "压缩文件"

# 去掉 display_name 中的全角括号后缀（如 "Word（DOCX）" → "Word"）
_PAREN_RE = re.compile(r"（[^）]*）")

# 子项节点 internalId 高位标记（PySide2 createIndex 要求 unsigned，不能用负数）
_CHILD_BIT = 0x10000


@dataclass(frozen=True)
class ExtractorItem:
    """提取器条目：类名 + 中文显示名 + 支持的扩展名集合。

    ``display_name`` 可能含全角括号后缀（如 "Word（DOCX）"），:attr:`tree_display_text`
    去掉括号后拼接小写扩展名列表，符合需求1格式 ``类别+扩展名``（如 "Word（docx）"）。
    """

    class_name: str
    display_name: str
    extensions: tuple[str, ...]

    @property
    def tree_display_text(self) -> str:
        """返回树形子项展示文本：``{中文名}（{扩展名列表}）``。

        去掉 display_name 中的全角括号后缀，再拼接小写扩展名列表，
        符合需求1格式 ``类别+扩展名``，如 ``Word（docx）`` / ``纯文本（txt, log, md）``。
        """
        name = _PAREN_RE.sub("", self.display_name).strip()
        return f"{name}（{', '.join(self.extensions)}）"

    @property
    def tooltip_text(self) -> str:
        """返回鼠标悬停提示文本：列出所有扩展名。"""
        return f"扩展名: {', '.join(self.extensions)}"


class ExtractorTreeModel(QAbstractItemModel):  # pyrefly: ignore [invalid-inheritance]
    """提取器勾选区树形模型：按父类别分组存储提取器元数据与勾选状态。

    树形结构：

    - 顶层节点：父类别（文档/表格/演示/邮件/纯文本/源代码/配置文件/标记与数据/样式表/压缩包）
    - 子节点：提取器条目

    父子勾选联动：

    - 父节点勾选 → 全选/全取消所有子项
    - 子项变化 → 父节点自动更新为 ``Checked``（全选）/ ``PartiallyChecked``（部分）/ ``Unchecked``（全不选）

    主窗口通过 :meth:`disabled_extractors` 读取禁用列表写入 Config，
    通过 :meth:`set_disabled_extractors` 在启动时恢复勾选状态，
    通过 :meth:`enabled_extensions` 在扫描时计算启用的扩展名集合
    （全部启用返回 ``None``，Scanner 走快速路径），
    通过 :meth:`checked_count` / :meth:`total_count` 获取已勾选/总数用于 UI 显示。

    节点编码（``QModelIndex.internalId``，PySide2 要求 unsigned，故用高位标记）：

    - 分类节点：``internalId = category_index + 1``（1-based，< ``_CHILD_BIT``）
    - 子项节点：``internalId = (category_index + 1) | _CHILD_BIT``（>= ``_CHILD_BIT``）
    - 无效节点：``internalId = 0``
    """

    # 勾选状态变化信号：主窗口连接此信号持久化 disabled_extractors 与更新勾选数量标签
    extractors_changed = Signal()

    def __init__(self, registry: ExtractorRegistry, parent=None) -> None:  # type: ignore[no-untyped-def]
        """初始化模型：从 registry.list_extractors() 加载提取器并按类别分组。

        压缩包分类（iter-79）独立于提取器体系：从 ``archive.default_factory``
        加载已注册的压缩文件扩展名（zip/rar/7z），创建虚拟
        :class:`ExtractorItem`（class_name=``"ArchiveFiles"``），归入"压缩包"
        分类。勾选状态通过 :meth:`archives_enabled` 读取，由主窗口同步到
        ``Config.scan_archives``。

        :param registry: 提取器注册表
        :param parent: 父 QObject
        """
        super().__init__(parent)
        # 树形数据：[(category_name, [ExtractorItem], [enabled_flags])]
        self._categories: list[tuple[str, list[ExtractorItem], list[bool]]] = []
        seen_cats: set[str] = set()
        # 先按 _CATEGORY_ORDER 顺序初始化类别节点
        for cat in _CATEGORY_ORDER:
            self._categories.append((cat, [], []))
            seen_cats.add(cat)
        # 加载提取器并归类
        for class_name, display_name, exts in registry.list_extractors():
            cat = _EXTRACTOR_CATEGORIES.get(class_name, "文档")
            if cat not in seen_cats:
                self._categories.append((cat, [], []))
                seen_cats.add(cat)
            item = ExtractorItem(class_name=class_name, display_name=display_name, extensions=exts)
            for _i, (cname, items, flags) in enumerate(self._categories):
                if cname == cat:
                    items.append(item)
                    flags.append(True)
                    break
        # 压缩包分类：从 archive.default_factory 加载已注册扩展名（iter-79）
        from fuscan.archive import default_factory as _archive_factory

        archive_exts = tuple(sorted(_archive_factory.registered_extensions))
        if archive_exts:
            archive_item = ExtractorItem(
                class_name=_ARCHIVE_CLASS_NAME,
                display_name=_ARCHIVE_DISPLAY_NAME,
                extensions=archive_exts,
            )
            for cname, items, flags in self._categories:
                if cname == _ARCHIVE_CATEGORY:
                    items.append(archive_item)
                    flags.append(True)
                    break

    # ----------------------------- QAbstractItemModel 必填 -----------------------------

    def index(self, row: int, column: int, parent: QModelIndex | None = None) -> QModelIndex:  # type: ignore[override]
        """返回指定行列与父节点的模型索引。

        :param row: 行号
        :param column: 列号（本模型固定单列，非 0 返回无效）
        :param parent: 父节点索引；无效时返回分类节点，分类节点时返回子项节点
        """
        if parent is None:
            parent = QModelIndex()
        if column != 0 or row < 0:
            return QModelIndex()
        if not parent.isValid():
            if row < len(self._categories):
                return self.createIndex(row, 0, row + 1)  # internalId = cat_index + 1
            return QModelIndex()
        parent_id = parent.internalId()
        if parent_id == 0 or parent_id >= _CHILD_BIT:
            # 父无效或是子项节点（无孙节点），返回无效
            return QModelIndex()
        # parent_id 为 1-4：分类节点，创建子项索引
        cat_index = parent_id - 1
        if cat_index >= len(self._categories):
            return QModelIndex()
        _cat_name, items, _flags = self._categories[cat_index]
        if row < len(items):
            return self.createIndex(row, 0, (cat_index + 1) | _CHILD_BIT)  # internalId = (cat_index+1) | _CHILD_BIT
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:  # type: ignore[override]
        """返回指定索引的父节点索引。分类节点无父，子项节点的父为所属分类。"""
        if not index.isValid():
            return QModelIndex()
        internal_id = index.internalId()
        if internal_id >= _CHILD_BIT:
            # 子项节点，父为分类
            cat_index = (internal_id & ~_CHILD_BIT) - 1
            return self.createIndex(cat_index, 0, cat_index + 1)
        return QModelIndex()

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        """返回指定父节点下的行数。无效父返回分类数，分类父返回子项数，子项父返回 0。"""
        if parent is None:
            parent = QModelIndex()
        if not parent.isValid():
            return len(self._categories)
        internal_id = parent.internalId()
        if internal_id >= _CHILD_BIT:
            # 子项节点无孙节点
            return 0
        if internal_id > 0:
            cat_index = internal_id - 1
            return len(self._categories[cat_index][1])
        return 0

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # type: ignore[override]  # noqa: ARG002
        """返回列数（固定单列）。"""
        return 1

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:  # type: ignore[override]
        """返回指定索引与 role 的数据。

        分类节点：

        - ``DisplayRole``：``{类别名}（{子项数}）``
        - ``CheckStateRole``：根据子项状态计算 Checked/PartiallyChecked/Unchecked

        子项节点：

        - ``DisplayRole``：``{中文名}（{扩展名列表}）``
        - ``ToolTipRole``：全部扩展名提示
        - ``CheckStateRole``：Checked/Unchecked
        """
        if not index.isValid():
            return None
        internal_id = index.internalId()
        if internal_id >= _CHILD_BIT:
            # 子项节点
            cat_index = (internal_id & ~_CHILD_BIT) - 1
            _cat_name, items, flags = self._categories[cat_index]
            row = index.row()
            if not (0 <= row < len(items)):
                return None
            item = items[row]
            if role == Qt.DisplayRole:
                return item.tree_display_text
            if role == Qt.ToolTipRole:
                return item.tooltip_text
            if role == Qt.CheckStateRole:
                return Qt.Checked if flags[row] else Qt.Unchecked
            return None
        if internal_id > 0:
            # 分类节点
            cat_index = internal_id - 1
            cat_name, items, flags = self._categories[cat_index]
            if role == Qt.DisplayRole:
                return f"{cat_name}（{len(items)}）"
            if role == Qt.CheckStateRole:
                return self._category_check_state(flags)
            return None
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:  # type: ignore[override]
        """返回 item 标志：启用 + 可勾选 + 可选择（分类与子项均适用）。"""
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable

    def setData(self, index: QModelIndex, value: object, role: int = Qt.EditRole) -> bool:  # type: ignore[override]
        """更新 item 数据。仅处理 ``Qt.CheckStateRole`` 的勾选切换。

        分类节点勾选时批量设置所有子项为新状态；子项勾选时更新自身并触发
        父分类节点状态刷新。两种情况均发出 ``dataChanged`` 与 ``extractors_changed``。

        :returns: 是否成功更新（未变化返回 False 不发信号）
        """
        if not index.isValid() or role != Qt.CheckStateRole:
            return False
        internal_id = index.internalId()
        new_checked = value == Qt.Checked
        if internal_id >= _CHILD_BIT:
            # 子项节点
            cat_index = (internal_id & ~_CHILD_BIT) - 1
            _cat_name, _items, flags = self._categories[cat_index]
            row = index.row()
            if not (0 <= row < len(flags)):
                return False
            if flags[row] == new_checked:
                return False
            flags[row] = new_checked
            self.dataChanged.emit(index, index, [role])
            parent_idx = self.parent(index)
            if parent_idx.isValid():
                self.dataChanged.emit(parent_idx, parent_idx, [role])
            self.extractors_changed.emit()  # pyrefly: ignore [missing-attribute]
            return True
        if internal_id > 0:
            # 分类节点：批量设置所有子项
            cat_index = internal_id - 1
            _cat_name, items, flags = self._categories[cat_index]
            if all(f == new_checked for f in flags):
                return False
            for i in range(len(flags)):
                flags[i] = new_checked
            self.dataChanged.emit(index, index, [role])
            child_count = len(items)
            if child_count > 0:
                top_child = self.index(0, 0, index)
                bottom_child = self.index(child_count - 1, 0, index)
                self.dataChanged.emit(top_child, bottom_child, [role])
            self.extractors_changed.emit()  # pyrefly: ignore [missing-attribute]
            return True
        return False

    # ----------------------------- 公共 API -----------------------------

    def disabled_extractors(self) -> list[str]:
        """返回当前禁用的提取器类名列表（用于持久化到 Config.disabled_extractors）。"""
        result: list[str] = []
        for _cat_name, items, flags in self._categories:
            for item, enabled in zip(items, flags):
                if not enabled:
                    result.append(item.class_name)
        return result

    def set_disabled_extractors(self, class_names: list[str]) -> None:
        """根据类名列表批量设置禁用状态（用于启动时恢复配置）。

        未在模型中的类名忽略（兼容旧版配置中已删除的提取器）。先更新内部状态
        再一次性发出 ``dataChanged`` 与 ``extractors_changed``，确保 emit 时
        视图读取到的已是最新数据。
        """
        disabled_set = set(class_names)
        changed = False
        for _cat_name, items, flags in self._categories:
            for i, item in enumerate(items):
                new_enabled = item.class_name not in disabled_set
                if new_enabled != flags[i]:
                    flags[i] = new_enabled
                    changed = True
        if not changed:
            return
        for cat_i in range(len(self._categories)):
            cat_idx = self.index(cat_i, 0)
            if cat_idx.isValid():
                self.dataChanged.emit(cat_idx, cat_idx, [Qt.CheckStateRole])
                n = self.rowCount(cat_idx)
                if n > 0:
                    top_child = self.index(0, 0, cat_idx)
                    bottom_child = self.index(n - 1, 0, cat_idx)
                    self.dataChanged.emit(top_child, bottom_child, [Qt.CheckStateRole])
        self.extractors_changed.emit()  # pyrefly: ignore [missing-attribute]

    def enabled_extensions(self) -> tuple[str, ...] | None:
        """根据勾选状态计算启用的扩展名白名单（含压缩包扩展名）。

        iter-87 起统一为白名单制：压缩包扩展名（zip/rar/7z）与其他扩展名
        统一进入白名单，不再由 ``Config.scan_archives`` 单独控制 walk 阶段过滤。
        ``scan_archives`` 字段保留作为 ArchiveScanner 构造开关，由勾选区
        ``archives_enabled`` 同步推导。

        三种返回值语义：

        - ``None``：所有分类全部勾选（含压缩包），Scanner 走快速路径扫所有文件
        - 空 tuple ``()``：用户全部取消勾选，Scanner 不扫任何文件（防御性边界）
        - 非空 tuple：部分勾选时返回启用扩展名并集
          （小写、去重、排序后元组，含压缩包扩展名）

        :returns: 全部勾选时返回 ``None``；部分/全部取消时返回扩展名并集元组
        """
        # 所有分类（含压缩包）全部勾选时走快速路径
        all_checked = all(all(flags) for _cat_name, _items, flags in self._categories)
        if all_checked:
            return None
        enabled: set[str] = set()
        for _cat_name, items, flags in self._categories:
            for item, enabled_flag in zip(items, flags):
                if enabled_flag:
                    enabled.update(item.extensions)
        return tuple(sorted(enabled))

    def archives_enabled(self) -> bool:
        """返回压缩包分类是否勾选（用于同步 ``Config.scan_archives``）。

        压缩包分类只有一个虚拟项 ``ArchiveFiles``，其勾选状态即为整个
        分类的启用状态。取消勾选后 walk 阶段不计入待解析数量，scan
        阶段不扫描压缩包内条目。
        """
        for cat_name, items, flags in self._categories:
            if cat_name == _ARCHIVE_CATEGORY:
                for _item, enabled in zip(items, flags):
                    return enabled
        return True

    def checked_count(self) -> int:
        """返回已勾选的子项数（用于 UI 显示「已勾选 N 项」）。"""
        return sum(sum(1 for f in flags if f) for _cat_name, _items, flags in self._categories)

    def total_count(self) -> int:
        """返回子项总数（用于 UI 显示「已勾选 N/M 项」）。"""
        return sum(len(items) for _cat_name, items, _flags in self._categories)

    # ----------------------------- 内部辅助 -----------------------------

    @staticmethod
    def _category_check_state(flags: list[bool]) -> Qt.CheckState:
        """根据子项启用标志计算分类节点的勾选状态。

        全选 → ``Checked``，全不选 → ``Unchecked``，部分 → ``PartiallyChecked``。
        """
        if not flags:
            return Qt.Unchecked
        checked = sum(1 for f in flags if f)
        if checked == len(flags):
            return Qt.Checked
        if checked == 0:
            return Qt.Unchecked
        return Qt.PartiallyChecked

    # ----------------------------- 测试与诊断辅助 -----------------------------

    def category_count(self) -> int:
        """返回分类数（仅用于测试与诊断）。"""
        return len(self._categories)

    def category_name(self, cat_index: int) -> str:
        """返回指定分类的名称（仅用于测试与诊断）。"""
        return self._categories[cat_index][0]

    def item_at(self, cat_index: int, row: int) -> ExtractorItem:
        """返回指定分类与行的条目（仅用于测试与诊断）。"""
        return self._categories[cat_index][1][row]

    def row_count(self) -> int:
        """返回子项总数（与 total_count 等价，仅用于测试与诊断）。"""
        return self.total_count()
