"""``ExtractorTreeModel`` 单元测试。

覆盖树形模型构造、index/parent/rowCount/data/flags/setData、父子勾选联动、
disabled_extractors/set_disabled_extractors、enabled_extensions、
checked_count/total_count 与 extractors_changed 信号。

测试不依赖 QApplication（QAbstractItemModel 的 data/setData 等方法在
无 QApplication 时也能工作，但 PySide 创建 QObject 时会尝试获取
QApplication 实例——若无则创建一个临时实例）。本测试文件标记 ``gui``
marker，CI 无 GUI 环境可通过 ``-m "not gui"`` 跳过。
"""

from __future__ import annotations

import os

import pytest
from typing_extensions import override

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    try:
        from PySide2.QtCore import Qt
    except ImportError:  # pragma: no cover
        from PySide6.QtCore import Qt  # pyrefly: ignore [missing-import]

    from fuscan.extractors.base import Extractor, ExtractorRegistry
    from fuscan.gui.extractor_model import ExtractorItem, ExtractorTreeModel

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过 ExtractorTreeModel 测试", allow_module_level=True)


class _StubExtractor(Extractor):
    """测试桩提取器基类：返回预设扩展名与显示名。

    子类通过 ``type().__name__`` 提供不同的 class_name，需匹配
    ``_EXTRACTOR_CATEGORIES`` 的键才能归入非默认分类。
    """

    _exts: tuple[str, ...] = ()
    _display_name: str = ""

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        return self._exts

    @property
    @override
    def display_name(self) -> str:
        return self._display_name

    @override
    def extract(self, path):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        raise NotImplementedError


# 类名匹配 _EXTRACTOR_CATEGORIES 键，使 stub 归入不同分类
# list_extractors() 按 display_name 排序：Excel（XLSX） < PDF < 纯文本
class PdfExtractor(_StubExtractor):  # → 文档
    _exts = ("pdf",)
    _display_name = "PDF"


class TextExtractor(_StubExtractor):  # → 文档
    _exts = ("txt", "md", "py", "log", "csv", "json")
    _display_name = "纯文本"


class XlsxExtractor(_StubExtractor):  # → 表格
    _exts = ("xlsx",)
    _display_name = "Excel（XLSX）"


def _build_registry() -> ExtractorRegistry:
    """构造含 3 个提取器的注册表（覆盖多分类、多扩展名合并、不同显示名）。

    list_extractors() 按 display_name 排序后：
    Excel（XLSX）/ PDF / 纯文本
    归类后：
    - 文档：[PdfExtractor, TextExtractor]（PDF 在前，纯文本在后）
    - 表格：[XlsxExtractor]
    - 演示/邮件：空
    """
    registry = ExtractorRegistry()
    registry.register(PdfExtractor())
    registry.register(TextExtractor())
    registry.register(XlsxExtractor())
    return registry


@pytest.fixture()
def model() -> ExtractorTreeModel:
    """构造测试用 ExtractorTreeModel（3 个提取器，默认全部勾选）。"""
    return ExtractorTreeModel(_build_registry())


def _cat_index(model: ExtractorTreeModel, cat_row: int) -> Qt.QModelIndex:  # type: ignore[name-defined]
    """辅助：返回分类节点索引。"""
    return model.index(cat_row, 0)


def _child_index(model: ExtractorTreeModel, cat_row: int, child_row: int) -> Qt.QModelIndex:  # type: ignore[name-defined]
    """辅助：返回子项节点索引。"""
    return model.index(child_row, 0, _cat_index(model, cat_row))


# ----------------------------- 构造与基础 -----------------------------


class TestExtractorTreeModelConstruction:
    """模型构造与 rowCount/data 基础行为。"""

    def test_category_count_includes_all_order_categories(self, model: ExtractorTreeModel) -> None:
        """分类数包含 _CATEGORY_ORDER 全部 4 类（即使为空也初始化）。"""
        assert model.category_count() == 4
        assert model.category_name(0) == "文档"
        assert model.category_name(1) == "表格"
        assert model.category_name(2) == "演示"
        assert model.category_name(3) == "邮件"

    def test_row_count_top_level_equals_category_count(self, model: ExtractorTreeModel) -> None:
        """顶层 rowCount 等于分类数（4）。"""
        assert model.rowCount() == 4

    def test_row_count_category_returns_children(self, model: ExtractorTreeModel) -> None:
        """分类节点 rowCount 返回子项数：文档=2，表格=1，演示=0，邮件=0。"""
        assert model.rowCount(_cat_index(model, 0)) == 2  # 文档
        assert model.rowCount(_cat_index(model, 1)) == 1  # 表格
        assert model.rowCount(_cat_index(model, 2)) == 0  # 演示
        assert model.rowCount(_cat_index(model, 3)) == 0  # 邮件

    def test_row_count_child_returns_zero(self, model: ExtractorTreeModel) -> None:
        """子项节点 rowCount 返回 0（无孙节点）。"""
        child = _child_index(model, 0, 0)
        assert model.rowCount(child) == 0

    def test_total_count_equals_extractor_count(self, model: ExtractorTreeModel) -> None:
        """total_count 等于提取器总数（3）。"""
        assert model.total_count() == 3
        assert model.row_count() == 3  # 测试别名

    def test_default_all_enabled(self, model: ExtractorTreeModel) -> None:
        """构造后默认全部勾选，disabled_extractors 为空。"""
        assert model.disabled_extractors() == []
        assert model.enabled_extensions() is None
        assert model.checked_count() == 3

    def test_column_count_always_one(self, model: ExtractorTreeModel) -> None:
        """columnCount 固定为 1（顶层与分类均如此）。"""
        assert model.columnCount() == 1
        assert model.columnCount(_cat_index(model, 0)) == 1

    def test_category_display_text_includes_count(self, model: ExtractorTreeModel) -> None:
        """分类节点 DisplayRole 返回 ``{类别名}（{子项数}）``。"""
        assert model.data(_cat_index(model, 0), Qt.DisplayRole) == "文档（2）"
        assert model.data(_cat_index(model, 1), Qt.DisplayRole) == "表格（1）"
        assert model.data(_cat_index(model, 2), Qt.DisplayRole) == "演示（0）"
        assert model.data(_cat_index(model, 3), Qt.DisplayRole) == "邮件（0）"

    def test_child_display_text_format(self, model: ExtractorTreeModel) -> None:
        """子项 DisplayRole 返回 ``{中文名}（{扩展名列表}）``格式。

        文档分类内子项顺序按 list_extractors() 的 display_name 排序：
        row 0 = PDF（pdf），row 1 = 纯文本（csv, json, log, md, py, txt）。
        """
        pdf_idx = _child_index(model, 0, 0)
        text_idx = _child_index(model, 0, 1)
        assert model.data(pdf_idx, Qt.DisplayRole) == "PDF（pdf）"
        assert model.data(text_idx, Qt.DisplayRole) == "纯文本（csv, json, log, md, py, txt）"

    def test_child_display_text_strips_paren_suffix(self, model: ExtractorTreeModel) -> None:
        """display_name 含全角括号后缀时去掉后缀再拼接扩展名。

        Excel（XLSX）→ 去掉后缀得 "Excel"，再拼接 "（xlsx）"。
        """
        xlsx_idx = _child_index(model, 1, 0)
        assert model.data(xlsx_idx, Qt.DisplayRole) == "Excel（xlsx）"

    def test_tooltip_lists_all_extensions(self, model: ExtractorTreeModel) -> None:
        """子项 ToolTipRole 返回所有扩展名（含 6 个的纯文本，按字母序排序）。"""
        text_idx = _child_index(model, 0, 1)
        assert model.data(text_idx, Qt.ToolTipRole) == "扩展名: csv, json, log, md, py, txt"

    def test_check_state_default_checked(self, model: ExtractorTreeModel) -> None:
        """子项 CheckStateRole 默认返回 Qt.Checked。"""
        for cat_row in range(model.category_count()):
            for child_row in range(model.rowCount(_cat_index(model, cat_row))):
                assert model.data(_child_index(model, cat_row, child_row), Qt.CheckStateRole) == Qt.Checked

    def test_category_check_state_default_checked(self, model: ExtractorTreeModel) -> None:
        """分类节点 CheckStateRole 在全部子项勾选时返回 Qt.Checked。"""
        assert model.data(_cat_index(model, 0), Qt.CheckStateRole) == Qt.Checked  # 文档 2/2
        assert model.data(_cat_index(model, 1), Qt.CheckStateRole) == Qt.Checked  # 表格 1/1

    def test_empty_category_check_state_unchecked(self, model: ExtractorTreeModel) -> None:
        """空分类节点 CheckStateRole 返回 Qt.Unchecked（无子项）。"""
        assert model.data(_cat_index(model, 2), Qt.CheckStateRole) == Qt.Unchecked  # 演示
        assert model.data(_cat_index(model, 3), Qt.CheckStateRole) == Qt.Unchecked  # 邮件

    def test_data_invalid_index_returns_none(self, model: ExtractorTreeModel) -> None:
        """无效 index 返回 None。"""
        invalid = model.index(-1, 0)
        assert model.data(invalid, Qt.DisplayRole) is None

    def test_data_unsupported_role_returns_none(self, model: ExtractorTreeModel) -> None:
        """未支持的角色返回 None。"""
        idx = _cat_index(model, 0)
        assert model.data(idx, Qt.FontRole) is None
        child = _child_index(model, 0, 0)
        assert model.data(child, Qt.FontRole) is None

    def test_item_at_returns_correct_item(self, model: ExtractorTreeModel) -> None:
        """item_at 返回指定分类与行的 ExtractorItem。"""
        item = model.item_at(0, 0)  # 文档 row 0 = PDF
        assert isinstance(item, ExtractorItem)
        assert item.display_name == "PDF"

    def test_parent_of_child_is_category(self, model: ExtractorTreeModel) -> None:
        """子项节点的 parent 返回所属分类节点。"""
        child = _child_index(model, 0, 0)
        parent = model.parent(child)
        assert parent.isValid()
        assert parent.internalId() == 1  # cat_index + 1 = 0 + 1

    def test_parent_of_category_is_invalid(self, model: ExtractorTreeModel) -> None:
        """分类节点的 parent 返回无效索引。"""
        cat = _cat_index(model, 0)
        assert not model.parent(cat).isValid()

    def test_index_invalid_column_returns_invalid(self, model: ExtractorTreeModel) -> None:
        """列号非 0 返回无效索引。"""
        assert not model.index(0, 1).isValid()


# ----------------------------- flags / setData -----------------------------


class TestExtractorTreeModelSetData:
    """flags 与 setData 行为。"""

    def test_flags_enable_and_checkable(self, model: ExtractorTreeModel) -> None:
        """flags 返回 Enabled | UserCheckable | Selectable（分类与子项均如此）。"""
        cat_flags = model.flags(_cat_index(model, 0))
        assert bool(cat_flags & Qt.ItemIsEnabled)
        assert bool(cat_flags & Qt.ItemIsUserCheckable)
        assert bool(cat_flags & Qt.ItemIsSelectable)

        child_flags = model.flags(_child_index(model, 0, 0))
        assert bool(child_flags & Qt.ItemIsEnabled)
        assert bool(child_flags & Qt.ItemIsUserCheckable)
        assert bool(child_flags & Qt.ItemIsSelectable)

    def test_flags_invalid_index_returns_no_flags(self, model: ExtractorTreeModel) -> None:
        """无效 index 的 flags 返回 NoItemFlags。"""
        assert model.flags(model.index(-1, 0)) == Qt.NoItemFlags

    def test_set_data_unchecks_child(self, model: ExtractorTreeModel) -> None:
        """setData(CheckStateRole, Unchecked) 取消子项勾选。"""
        idx = _child_index(model, 0, 0)
        assert model.setData(idx, Qt.Unchecked, Qt.CheckStateRole) is True
        assert model.data(idx, Qt.CheckStateRole) == Qt.Unchecked

    def test_set_data_ignored_for_unsupported_role(self, model: ExtractorTreeModel) -> None:
        """非 CheckStateRole 的 setData 被忽略，返回 False。"""
        idx = _child_index(model, 0, 0)
        assert model.setData(idx, "new text", Qt.EditRole) is False

    def test_set_data_ignored_when_unchanged(self, model: ExtractorTreeModel) -> None:
        """setData 设置相同状态时返回 False 不发信号。"""
        idx = _child_index(model, 0, 0)
        assert model.setData(idx, Qt.Checked, Qt.CheckStateRole) is False

    def test_set_data_invalid_index_returns_false(self, model: ExtractorTreeModel) -> None:
        """无效 index 的 setData 返回 False。"""
        invalid = model.index(-1, 0)
        assert model.setData(invalid, Qt.Unchecked, Qt.CheckStateRole) is False

    def test_extractors_changed_emitted_on_child_toggle(self, model: ExtractorTreeModel) -> None:
        """子项勾选状态变化时发出 extractors_changed 信号。"""
        signals: list[None] = []
        model.extractors_changed.connect(lambda: signals.append(None))  # pyrefly: ignore [missing-attribute]
        idx = _child_index(model, 0, 0)
        model.setData(idx, Qt.Unchecked, Qt.CheckStateRole)
        assert len(signals) == 1
        model.setData(idx, Qt.Unchecked, Qt.CheckStateRole)  # 无变化
        assert len(signals) == 1
        model.setData(idx, Qt.Checked, Qt.CheckStateRole)
        assert len(signals) == 2


# ----------------------------- 父子勾选联动 -----------------------------


class TestExtractorTreeModelParentChildLinkage:
    """父节点批量勾选与子项变化时父节点状态联动。"""

    def test_category_check_all_children(self, model: ExtractorTreeModel) -> None:
        """分类节点设为 Unchecked 时批量取消所有子项。"""
        cat_idx = _cat_index(model, 0)  # 文档（2 个子项）
        assert model.setData(cat_idx, Qt.Unchecked, Qt.CheckStateRole) is True
        # 子项全部 Unchecked
        assert model.data(_child_index(model, 0, 0), Qt.CheckStateRole) == Qt.Unchecked
        assert model.data(_child_index(model, 0, 1), Qt.CheckStateRole) == Qt.Unchecked
        # 分类节点 CheckState 也为 Unchecked
        assert model.data(cat_idx, Qt.CheckStateRole) == Qt.Unchecked
        assert model.checked_count() == 1  # 仅表格的 XlsxExtractor 仍勾选

    def test_category_uncheck_all_then_recheck(self, model: ExtractorTreeModel) -> None:
        """分类节点批量取消后再批量勾选。"""
        cat_idx = _cat_index(model, 0)
        model.setData(cat_idx, Qt.Unchecked, Qt.CheckStateRole)
        assert model.data(cat_idx, Qt.CheckStateRole) == Qt.Unchecked
        model.setData(cat_idx, Qt.Checked, Qt.CheckStateRole)
        assert model.data(cat_idx, Qt.CheckStateRole) == Qt.Checked
        assert model.data(_child_index(model, 0, 0), Qt.CheckStateRole) == Qt.Checked

    def test_category_set_data_no_change_returns_false(self, model: ExtractorTreeModel) -> None:
        """分类节点所有子项已是目标状态时返回 False 不发信号。"""
        cat_idx = _cat_index(model, 0)  # 默认全选
        assert model.setData(cat_idx, Qt.Checked, Qt.CheckStateRole) is False

    def test_partial_check_shows_partially_checked(self, model: ExtractorTreeModel) -> None:
        """部分子项取消时分类节点显示 PartiallyChecked。"""
        # 文档分类有 2 个子项，取消其中 1 个
        model.setData(_child_index(model, 0, 0), Qt.Unchecked, Qt.CheckStateRole)
        assert model.data(_cat_index(model, 0), Qt.CheckStateRole) == Qt.PartiallyChecked

    def test_last_uncheck_makes_category_unchecked(self, model: ExtractorTreeModel) -> None:
        """最后一个子项取消后分类节点变为 Unchecked。"""
        model.setData(_child_index(model, 0, 0), Qt.Unchecked, Qt.CheckStateRole)
        model.setData(_child_index(model, 0, 1), Qt.Unchecked, Qt.CheckStateRole)
        assert model.data(_cat_index(model, 0), Qt.CheckStateRole) == Qt.Unchecked

    def test_last_recheck_makes_category_checked(self, model: ExtractorTreeModel) -> None:
        """全部取消后重新勾选最后一个子项使分类变为 PartiallyChecked。"""
        cat_idx = _cat_index(model, 0)
        model.setData(cat_idx, Qt.Unchecked, Qt.CheckStateRole)
        # 勾选 1 个 → PartiallyChecked（1/2）
        model.setData(_child_index(model, 0, 0), Qt.Checked, Qt.CheckStateRole)
        assert model.data(cat_idx, Qt.CheckStateRole) == Qt.PartiallyChecked
        # 再勾选另 1 个 → Checked（2/2）
        model.setData(_child_index(model, 0, 1), Qt.Checked, Qt.CheckStateRole)
        assert model.data(cat_idx, Qt.CheckStateRole) == Qt.Checked

    def test_category_toggle_emits_signal_once(self, model: ExtractorTreeModel) -> None:
        """分类节点批量勾选时只发一次 extractors_changed 信号。"""
        signals: list[None] = []
        model.extractors_changed.connect(lambda: signals.append(None))  # pyrefly: ignore [missing-attribute]
        model.setData(_cat_index(model, 0), Qt.Unchecked, Qt.CheckStateRole)
        assert len(signals) == 1


# ----------------------------- disabled_extractors / set_disabled_extractors -----------------------------


class TestExtractorTreeModelDisabled:
    """disabled_extractors / set_disabled_extractors 行为。"""

    def test_disabled_after_uncheck(self, model: ExtractorTreeModel) -> None:
        """取消勾选后 disabled_extractors 返回对应类名。"""
        model.setData(_child_index(model, 0, 0), Qt.Unchecked, Qt.CheckStateRole)
        assert model.disabled_extractors() == ["PdfExtractor"]

    def test_disabled_preserves_category_order(self, model: ExtractorTreeModel) -> None:
        """disabled_extractors 按分类顺序返回（文档在前，表格在后）。"""
        # 取消文档第 0 项（PdfExtractor）与表格第 0 项（XlsxExtractor）
        model.setData(_child_index(model, 0, 0), Qt.Unchecked, Qt.CheckStateRole)
        model.setData(_child_index(model, 1, 0), Qt.Unchecked, Qt.CheckStateRole)
        assert model.disabled_extractors() == ["PdfExtractor", "XlsxExtractor"]

    def test_set_disabled_extractors_updates_state(self, model: ExtractorTreeModel) -> None:
        """set_disabled_extractors 批量更新勾选状态。"""
        model.set_disabled_extractors(["PdfExtractor", "XlsxExtractor"])
        assert model.disabled_extractors() == ["PdfExtractor", "XlsxExtractor"]
        # 仅纯文本启用，扩展名为 txt/md/py/log/csv/json
        assert model.enabled_extensions() == ("csv", "json", "log", "md", "py", "txt")

    def test_set_disabled_extractors_updates_category_state(self, model: ExtractorTreeModel) -> None:
        """set_disabled_extractors 后分类节点状态正确联动。"""
        model.set_disabled_extractors(["PdfExtractor"])  # 文档 1/2
        assert model.data(_cat_index(model, 0), Qt.CheckStateRole) == Qt.PartiallyChecked
        assert model.data(_cat_index(model, 1), Qt.CheckStateRole) == Qt.Checked  # 表格全选

    def test_set_disabled_extractors_no_change_no_signal(self, model: ExtractorTreeModel) -> None:
        """无变化时不发信号。"""
        signals: list[None] = []
        model.extractors_changed.connect(lambda: signals.append(None))  # pyrefly: ignore [missing-attribute]
        model.set_disabled_extractors([])
        assert signals == []

    def test_set_disabled_extractors_ignores_unknown_names(self, model: ExtractorTreeModel) -> None:
        """未知类名被忽略（兼容旧版配置中已删除的提取器）。"""
        signals: list[None] = []
        model.extractors_changed.connect(lambda: signals.append(None))  # pyrefly: ignore [missing-attribute]
        model.set_disabled_extractors(["NonExistent"])
        assert signals == []
        assert model.disabled_extractors() == []

    def test_set_disabled_all_makes_category_unchecked(self, model: ExtractorTreeModel) -> None:
        """禁用某分类全部子项后分类节点变为 Unchecked。"""
        model.set_disabled_extractors(["PdfExtractor", "TextExtractor"])
        assert model.data(_cat_index(model, 0), Qt.CheckStateRole) == Qt.Unchecked
        assert model.data(_cat_index(model, 1), Qt.CheckStateRole) == Qt.Checked


# ----------------------------- enabled_extensions -----------------------------


class TestExtractorTreeModelEnabledExtensions:
    """enabled_extensions 行为。"""

    def test_all_enabled_returns_none(self, model: ExtractorTreeModel) -> None:
        """全部勾选时返回 None（Scanner 走快速路径）。"""
        assert model.enabled_extensions() is None

    def test_partial_enabled_returns_union(self, model: ExtractorTreeModel) -> None:
        """部分取消时返回启用扩展名的并集（小写、去重、排序）。"""
        # 取消 PDF 勾选：剩余 Word 类无 + 纯文本（txt/md/py/log/csv/json）+ Excel（xlsx）
        model.setData(_child_index(model, 0, 0), Qt.Unchecked, Qt.CheckStateRole)
        result = model.enabled_extensions()
        assert result is not None
        assert result == ("csv", "json", "log", "md", "py", "txt", "xlsx")

    def test_all_disabled_returns_empty_tuple(self, model: ExtractorTreeModel) -> None:
        """全部禁用时返回空元组。"""
        model.set_disabled_extractors(["PdfExtractor", "TextExtractor", "XlsxExtractor"])
        assert model.enabled_extensions() == ()

    def test_all_disabled_via_category_toggle(self, model: ExtractorTreeModel) -> None:
        """通过分类节点批量取消所有子项后 enabled_extensions 为空元组。"""
        model.setData(_cat_index(model, 0), Qt.Unchecked, Qt.CheckStateRole)
        model.setData(_cat_index(model, 1), Qt.Unchecked, Qt.CheckStateRole)
        assert model.enabled_extensions() == ()


# ----------------------------- checked_count / total_count -----------------------------


class TestExtractorTreeModelCount:
    """checked_count / total_count 行为。"""

    def test_initial_counts(self, model: ExtractorTreeModel) -> None:
        """初始状态：3/3 全部勾选。"""
        assert model.total_count() == 3
        assert model.checked_count() == 3

    def test_count_after_uncheck_one(self, model: ExtractorTreeModel) -> None:
        """取消 1 项后 checked_count 减 1。"""
        model.setData(_child_index(model, 0, 0), Qt.Unchecked, Qt.CheckStateRole)
        assert model.checked_count() == 2
        assert model.total_count() == 3  # 总数不变

    def test_count_after_category_uncheck(self, model: ExtractorTreeModel) -> None:
        """批量取消文档分类（2 项）后 checked_count 减 2。"""
        model.setData(_cat_index(model, 0), Qt.Unchecked, Qt.CheckStateRole)
        assert model.checked_count() == 1  # 仅表格 1 项

    def test_count_after_recheck(self, model: ExtractorTreeModel) -> None:
        """取消后重新勾选，checked_count 恢复。"""
        model.setData(_cat_index(model, 0), Qt.Unchecked, Qt.CheckStateRole)
        model.setData(_cat_index(model, 0), Qt.Checked, Qt.CheckStateRole)
        assert model.checked_count() == 3
