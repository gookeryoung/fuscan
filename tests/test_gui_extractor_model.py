"""``ExtractorListModel`` 单元测试。

覆盖从默认注册表加载、勾选状态管理、扩展名集合计算、批量全选/全不选、
``data()``/``roleNames()``/``rowCount()`` 等 ``QAbstractListModel`` 接口。
"""

from __future__ import annotations

import os

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    try:
        from PySide2.QtCore import QModelIndex, Qt
    except ImportError:  # pragma: no cover
        from PySide6.QtCore import QModelIndex, Qt  # pyrefly: ignore [missing-import]

    from fuscan.extractors.base import SpeedTier, default_registry  # noqa: F401
    from fuscan.gui.models.extractor_model import ExtractorListModel

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过提取器模型测试", allow_module_level=True)


@pytest.fixture()
def model() -> ExtractorListModel:
    """每个测试独立 ExtractorListModel 实例（默认全部勾选）。"""
    m = ExtractorListModel()
    m.load_from_registry()
    return m


class TestRowCount:
    def test_empty_model_has_zero_rows(self) -> None:
        m = ExtractorListModel()
        assert m.rowCount() == 0

    def test_load_from_registry_populates_rows(self, model: ExtractorListModel) -> None:
        """加载默认注册表后行数应与 default_registry.list_extractors() 一致。"""
        assert model.rowCount() == len(default_registry.list_extractors())

    def test_rowcount_with_parent_index_returns_zero(self, model: ExtractorListModel) -> None:
        """传有效 parent index 应返回 0（扁平模型无子节点）。"""
        parent = model.index(0)
        assert model.rowCount(parent) == 0


class TestRoleNames:
    def test_role_names_contains_expected_roles(self, model: ExtractorListModel) -> None:
        roles = model.roleNames()
        assert roles[Qt.UserRole + 1] == b"className"
        assert roles[Qt.UserRole + 2] == b"displayName"
        assert roles[Qt.UserRole + 3] == b"extensions"
        assert roles[Qt.UserRole + 4] == b"speedTierText"
        assert roles[Qt.UserRole + 5] == b"speedTierColor"
        assert roles[Qt.UserRole + 6] == b"enabled"
        assert roles[Qt.UserRole + 7] == b"formatLabel"


class TestData:
    def test_data_invalid_index_returns_empty(self, model: ExtractorListModel) -> None:
        invalid = QModelIndex()
        assert model.data(invalid, Qt.UserRole + 1) == ""

    def test_data_out_of_range_returns_empty(self, model: ExtractorListModel) -> None:
        idx = model.index(model.rowCount() + 10)
        assert model.data(idx, Qt.UserRole + 1) == ""

    def test_data_returns_class_name(self, model: ExtractorListModel) -> None:
        idx = model.index(0)
        class_name = model.data(idx, Qt.UserRole + 1)
        assert isinstance(class_name, str)
        assert class_name

    def test_data_returns_display_name(self, model: ExtractorListModel) -> None:
        idx = model.index(0)
        display_name = model.data(idx, Qt.UserRole + 2)
        assert isinstance(display_name, str)
        assert display_name

    def test_data_returns_extensions(self, model: ExtractorListModel) -> None:
        idx = model.index(0)
        exts = model.data(idx, Qt.UserRole + 3)
        assert isinstance(exts, str)
        assert exts

    def test_data_returns_speed_tier_text(self, model: ExtractorListModel) -> None:
        idx = model.index(0)
        text = model.data(idx, Qt.UserRole + 4)
        # 5 档之一
        assert text in {"T1 极速", "T2 快速", "T3 中速", "T4 慢速", "T5 极慢"}

    def test_data_returns_speed_tier_color(self, model: ExtractorListModel) -> None:
        idx = model.index(0)
        color = model.data(idx, Qt.UserRole + 5)
        assert color in {"#28A745", "#17A2B8", "#FFC107", "#FD7E14", "#DC3545"}

    def test_data_returns_enabled_default_true(self, model: ExtractorListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.UserRole + 6) is True

    def test_data_returns_format_label_non_empty(self, model: ExtractorListModel) -> None:
        """formatLabel role 应返回非空字符串（格式 tag 文本）。"""
        for i in range(model.rowCount()):
            idx = model.index(i)
            label = model.data(idx, Qt.UserRole + 7)
            assert isinstance(label, str)
            assert label
            # 应为大写（DOCX/PDF/XLSX 等）
            assert label == label.upper()

    def test_data_returns_format_label_from_paren(self, model: ExtractorListModel) -> None:
        """带括号的 display_name（如 Word（DOCX））应提取括号内文本为 formatLabel。"""
        # DocxExtractor.display_name = "Word（DOCX）" → formatLabel = "DOCX"
        target_idx = -1
        for i in range(model.rowCount()):
            if model.data(model.index(i), Qt.UserRole + 1) == "DocxExtractor":
                target_idx = i
                break
        assert target_idx >= 0, "DocxExtractor 应在默认注册表中"
        assert model.data(model.index(target_idx), Qt.UserRole + 7) == "DOCX"

    def test_data_returns_format_label_fallback_to_extension(self, model: ExtractorListModel) -> None:
        """无括号的 display_name（如 PDF）应回退到首扩展名大写为 formatLabel。"""
        target_idx = -1
        for i in range(model.rowCount()):
            if model.data(model.index(i), Qt.UserRole + 1) == "PdfExtractor":
                target_idx = i
                break
        assert target_idx >= 0, "PdfExtractor 应在默认注册表中"
        # PdfExtractor.display_name = "PDF"（无括号），supported_extensions = ("pdf",)
        assert model.data(model.index(target_idx), Qt.UserRole + 7) == "PDF"

    def test_data_unknown_role_returns_empty(self, model: ExtractorListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.DisplayRole) == ""


class TestLoadFromRegistry:
    def test_load_with_disabled_extractors(self) -> None:
        """加载时传入 disabled 列表应反映在 enabled 状态上。"""
        m = ExtractorListModel()
        extractors = default_registry.list_extractors()
        first_class = extractors[0][0]
        m.load_from_registry(disabled_extractors=[first_class])
        idx = m.index(0)
        assert m.data(idx, Qt.UserRole + 6) is False

    def test_load_resets_previous_state(self) -> None:
        """重复加载应重置行数据。"""
        m = ExtractorListModel()
        m.load_from_registry(disabled_extractors=["PlainTextExtractor"])
        m.load_from_registry()
        # 重新加载后无 disabled
        assert m.disabled_extractors() == []


class TestDisabledExtractors:
    def test_default_no_disabled(self, model: ExtractorListModel) -> None:
        assert model.disabled_extractors() == []

    def test_set_disabled_extractors_updates_state(self, model: ExtractorListModel) -> None:
        first_class = model.data(model.index(0), Qt.UserRole + 1)
        assert isinstance(first_class, str)
        model.set_disabled_extractors([first_class])
        assert first_class in model.disabled_extractors()

    def test_set_extractor_enabled_unchecks(self, model: ExtractorListModel) -> None:
        first_class = model.data(model.index(0), Qt.UserRole + 1)
        assert isinstance(first_class, str)
        model.set_extractor_enabled(first_class, False)
        assert first_class in model.disabled_extractors()

    def test_set_extractor_enabled_unknown_class_noop(self, model: ExtractorListModel) -> None:
        """未知类名不应影响现有状态。"""
        before = model.disabled_extractors()
        model.set_extractor_enabled("NonExistentClass", False)
        assert model.disabled_extractors() == before

    def test_set_extractor_enabled_same_value_noop(self, model: ExtractorListModel) -> None:
        first_class = model.data(model.index(0), Qt.UserRole + 1)
        assert isinstance(first_class, str)
        # 已默认勾选，再设 True 应无效果
        model.set_extractor_enabled(first_class, True)
        assert model.disabled_extractors() == []


class TestEnabledExtensions:
    def test_all_enabled_returns_empty_tuple(self, model: ExtractorListModel) -> None:
        """全部勾选时返回空 tuple（表示扫描所有文件）。"""
        assert model.enabled_extensions() == ()

    def test_partial_enabled_returns_sorted_extensions(self, model: ExtractorListModel) -> None:
        first_class = model.data(model.index(0), Qt.UserRole + 1)
        assert isinstance(first_class, str)
        model.set_extractor_enabled(first_class, False)
        exts = model.enabled_extensions()
        # 非空 tuple，已排序
        assert isinstance(exts, tuple)
        assert len(exts) > 0
        assert list(exts) == sorted(exts)

    def test_all_disabled_returns_empty_tuple(self, model: ExtractorListModel) -> None:
        """全部不选时返回空 tuple（无扩展名被勾选）。"""
        model.unselect_all()
        # 当所有提取器都不勾选时，没有扩展名加入列表
        assert model.enabled_extensions() == ()


class TestSelectAll:
    def test_select_all(self, model: ExtractorListModel) -> None:
        model.unselect_all()
        assert model.enabled_count == 0
        model.select_all()
        assert model.enabled_count == model.total_count
        assert model.disabled_extractors() == []

    def test_unselect_all(self, model: ExtractorListModel) -> None:
        model.unselect_all()
        assert model.enabled_count == 0
        assert model.total_count > 0
        assert len(model.disabled_extractors()) == model.total_count


class TestCounts:
    def test_total_count_matches_row_count(self, model: ExtractorListModel) -> None:
        assert model.total_count == model.rowCount()

    def test_enabled_count_default_all(self, model: ExtractorListModel) -> None:
        assert model.enabled_count == model.total_count

    def test_enabled_count_after_unselect_one(self, model: ExtractorListModel) -> None:
        first_class = model.data(model.index(0), Qt.UserRole + 1)
        assert isinstance(first_class, str)
        model.set_extractor_enabled(first_class, False)
        assert model.enabled_count == model.total_count - 1
