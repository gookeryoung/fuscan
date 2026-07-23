"""``ExtractorListModel`` 单元测试。

覆盖模型构造、rowCount/data/flags/setData、disabled_extractors/
set_disabled_extractors、enabled_extensions 与 extractors_changed 信号。

测试不依赖 QApplication（QAbstractListModel 的 data/setData 等方法在
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
    from fuscan.gui.extractor_model import ExtractorItem, ExtractorListModel

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过 ExtractorListModel 测试", allow_module_level=True)


class _StubExtractor(Extractor):
    """测试桩提取器基类：返回预设扩展名与显示名。

    子类通过 ``type().__name__`` 提供不同的 class_name（模拟真实提取器）。
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


class _TextStub(_StubExtractor):
    _exts = ("txt", "md", "py", "log", "csv", "json")
    _display_name = "纯文本"


class _PdfStub(_StubExtractor):
    _exts = ("pdf",)
    _display_name = "PDF"


class _WordStub(_StubExtractor):
    _exts = ("docx",)
    _display_name = "Word（DOCX）"


def _build_registry() -> ExtractorRegistry:
    """构造含 3 个提取器的注册表（覆盖多扩展名合并、不同显示名）。"""
    registry = ExtractorRegistry()
    registry.register(_TextStub())
    registry.register(_PdfStub())
    registry.register(_WordStub())
    return registry


@pytest.fixture()
def model() -> ExtractorListModel:
    """构造测试用 ExtractorListModel（3 个提取器，默认全部勾选）。"""
    return ExtractorListModel(_build_registry())


# ----------------------------- 构造与基础 -----------------------------


class TestExtractorListModelConstruction:
    """模型构造与 rowCount/data 基础行为。"""

    def test_row_count_matches_registry(self, model: ExtractorListModel) -> None:
        """rowCount 等于注册表中提取器数量（去重后）。"""
        assert model.row_count() == 3

    def test_row_count_with_invalid_parent(self, model: ExtractorListModel) -> None:
        """父索引有效时 rowCount 返回 0（列表模型无层级）。"""
        idx = model.index(0)
        assert model.rowCount(idx) == 0

    def test_default_all_enabled(self, model: ExtractorListModel) -> None:
        """构造后默认全部勾选，disabled_extractors 为空。"""
        assert model.disabled_extractors() == []
        assert model.enabled_extensions() is None

    def test_display_text_format(self, model: ExtractorListModel) -> None:
        """data(DisplayRole) 仅返回 display_name（紧凑展示，扩展名信息已在 display_name 中）。"""
        # 注册表按 display_name 排序：PDF / Word（DOCX） / 纯文本
        pdf_idx = model.index(0)
        word_idx = model.index(1)
        text_idx = model.index(2)
        assert model.data(pdf_idx, Qt.DisplayRole) == "PDF"
        assert model.data(word_idx, Qt.DisplayRole) == "Word（DOCX）"
        assert model.data(text_idx, Qt.DisplayRole) == "纯文本"

    def test_tooltip_lists_all_extensions(self, model: ExtractorListModel) -> None:
        """data(ToolTipRole) 返回所有扩展名（含 6 个的纯文本，按字母序排序）。"""
        tooltip = model.data(model.index(2), Qt.ToolTipRole)
        assert tooltip == "扩展名: csv, json, log, md, py, txt"

    def test_check_state_default_checked(self, model: ExtractorListModel) -> None:
        """data(CheckStateRole) 默认返回 Qt.Checked。"""
        for row in range(model.row_count()):
            assert model.data(model.index(row), Qt.CheckStateRole) == Qt.Checked

    def test_data_invalid_index_returns_none(self, model: ExtractorListModel) -> None:
        """无效 index 返回 None。"""
        invalid = model.index(-1)
        assert model.data(invalid, Qt.DisplayRole) is None
        out_of_range = model.index(99)
        assert model.data(out_of_range, Qt.DisplayRole) is None

    def test_data_unsupported_role_returns_none(self, model: ExtractorListModel) -> None:
        """未支持的角色返回 None。"""
        idx = model.index(0)
        assert model.data(idx, Qt.FontRole) is None

    def test_item_at_returns_correct_item(self, model: ExtractorListModel) -> None:
        """item_at 返回对应行的 ExtractorItem。"""
        item = model.item_at(0)
        assert isinstance(item, ExtractorItem)
        assert item.display_name == "PDF"


# ----------------------------- flags / setData -----------------------------


class TestExtractorListModelSetData:
    """flags 与 setData 行为。"""

    def test_flags_enable_and_checkable(self, model: ExtractorListModel) -> None:
        """flags 返回 Enabled | UserCheckable | Selectable。"""
        flags = model.flags(model.index(0))
        assert bool(flags & Qt.ItemIsEnabled)
        assert bool(flags & Qt.ItemIsUserCheckable)
        assert bool(flags & Qt.ItemIsSelectable)

    def test_flags_invalid_index_returns_no_flags(self, model: ExtractorListModel) -> None:
        """无效 index 的 flags 返回 NoItemFlags。"""
        assert model.flags(model.index(-1)) == Qt.NoItemFlags

    def test_set_data_unchecks_item(self, model: ExtractorListModel) -> None:
        """setData(CheckStateRole, Unchecked) 取消勾选。"""
        idx = model.index(0)
        assert model.setData(idx, Qt.Unchecked, Qt.CheckStateRole) is True
        assert model.data(idx, Qt.CheckStateRole) == Qt.Unchecked

    def test_set_data_ignored_for_unsupported_role(self, model: ExtractorListModel) -> None:
        """非 CheckStateRole 的 setData 被忽略，返回 False。"""
        idx = model.index(0)
        assert model.setData(idx, "new text", Qt.EditRole) is False

    def test_set_data_ignored_when_unchanged(self, model: ExtractorListModel) -> None:
        """setData 设置相同状态时返回 False 不发信号。"""
        idx = model.index(0)
        # 当前已是 Checked，再次设 Checked 不变化
        assert model.setData(idx, Qt.Checked, Qt.CheckStateRole) is False

    def test_set_data_invalid_index_returns_false(self, model: ExtractorListModel) -> None:
        """无效 index 的 setData 返回 False。"""
        invalid = model.index(-1)
        assert model.setData(invalid, Qt.Unchecked, Qt.CheckStateRole) is False

    def test_extractors_changed_emitted_on_toggle(self, model: ExtractorListModel) -> None:
        """勾选状态变化时发出 extractors_changed 信号。"""
        signals: list[None] = []
        model.extractors_changed.connect(lambda: signals.append(None))  # pyrefly: ignore [missing-attribute]
        model.setData(model.index(0), Qt.Unchecked, Qt.CheckStateRole)
        assert len(signals) == 1
        # 再次设为 Unchecked（无变化）不应发信号
        model.setData(model.index(0), Qt.Unchecked, Qt.CheckStateRole)
        assert len(signals) == 1
        # 重新勾选，发信号
        model.setData(model.index(0), Qt.Checked, Qt.CheckStateRole)
        assert len(signals) == 2


# ----------------------------- disabled_extractors / set_disabled_extractors -----------------------------


class TestExtractorListModelDisabled:
    """disabled_extractors / set_disabled_extractors 行为。"""

    def test_disabled_after_uncheck(self, model: ExtractorListModel) -> None:
        """取消勾选后 disabled_extractors 返回对应类名。"""
        model.setData(model.index(0), Qt.Unchecked, Qt.CheckStateRole)
        assert model.disabled_extractors() == ["_PdfStub"]

    def test_disabled_preserves_order(self, model: ExtractorListModel) -> None:
        """disabled_extractors 按 _items 顺序返回（与 display_name 排序后顺序一致）。"""
        # 取消第 0 行（PDF）与第 2 行（纯文本）
        model.setData(model.index(0), Qt.Unchecked, Qt.CheckStateRole)
        model.setData(model.index(2), Qt.Unchecked, Qt.CheckStateRole)
        # _items 顺序：PDF / Word（DOCX） / 纯文本
        assert model.disabled_extractors() == ["_PdfStub", "_TextStub"]

    def test_set_disabled_extractors_updates_state(self, model: ExtractorListModel) -> None:
        """set_disabled_extractors 批量更新勾选状态。"""
        model.set_disabled_extractors(["_PdfStub", "_WordStub"])
        assert model.disabled_extractors() == ["_PdfStub", "_WordStub"]
        # 仅纯文本启用，扩展名为 txt/md/py/log/csv/json
        assert model.enabled_extensions() == ("csv", "json", "log", "md", "py", "txt")

    def test_set_disabled_extractors_no_change_no_signal(self, model: ExtractorListModel) -> None:
        """无变化时不发信号。"""
        signals: list[None] = []
        model.extractors_changed.connect(lambda: signals.append(None))  # pyrefly: ignore [missing-attribute]
        # 默认全部勾选，传入空列表（无变化）
        model.set_disabled_extractors([])
        assert signals == []

    def test_set_disabled_extractors_ignores_unknown_names(self, model: ExtractorListModel) -> None:
        """未知类名被忽略（兼容旧版配置中已删除的提取器）。"""
        signals: list[None] = []
        model.extractors_changed.connect(lambda: signals.append(None))  # pyrefly: ignore [missing-attribute]
        model.set_disabled_extractors(["NonExistent"])
        # 无变化，无信号
        assert signals == []
        assert model.disabled_extractors() == []


# ----------------------------- enabled_extensions -----------------------------


class TestExtractorListModelEnabledExtensions:
    """enabled_extensions 行为。"""

    def test_all_enabled_returns_none(self, model: ExtractorListModel) -> None:
        """全部勾选时返回 None（Scanner 走快速路径）。"""
        assert model.enabled_extensions() is None

    def test_partial_enabled_returns_union(self, model: ExtractorListModel) -> None:
        """部分取消时返回启用扩展名的并集（小写、去重、排序）。"""
        # 取消 PDF（pdf）勾选：剩余 Word（docx） + 纯文本（txt/md/py/log/csv/json）
        model.setData(model.index(0), Qt.Unchecked, Qt.CheckStateRole)
        result = model.enabled_extensions()
        assert result is not None
        # 启用项：Word（docx） + 纯文本（txt, md, py, log, csv, json）
        assert result == ("csv", "docx", "json", "log", "md", "py", "txt")

    def test_all_disabled_returns_empty_tuple(self, model: ExtractorListModel) -> None:
        """全部禁用时返回空元组。"""
        model.set_disabled_extractors(["_PdfStub", "_WordStub", "_TextStub"])
        assert model.enabled_extensions() == ()
