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
        assert roles[Qt.UserRole + 8] == b"category"


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

    def test_data_returns_category_for_known_class(self, model: ExtractorListModel) -> None:
        """category role 应返回类别名字符串（如 DocxExtractor → "Office 文档"）。"""
        target_idx = -1
        for i in range(model.rowCount()):
            if model.data(model.index(i), Qt.UserRole + 1) == "DocxExtractor":
                target_idx = i
                break
        assert target_idx >= 0, "DocxExtractor 应在默认注册表中"
        assert model.data(model.index(target_idx), Qt.UserRole + 8) == "Office 文档"

    def test_data_returns_category_for_pdf(self, model: ExtractorListModel) -> None:
        """PdfExtractor 应归类到「PDF/RTF」。"""
        for i in range(model.rowCount()):
            if model.data(model.index(i), Qt.UserRole + 1) == "PdfExtractor":
                assert model.data(model.index(i), Qt.UserRole + 8) == "PDF/RTF"
                return
        pytest.fail("PdfExtractor 应在默认注册表中")

    def test_data_returns_category_non_empty_for_all(self, model: ExtractorListModel) -> None:
        """所有提取器都应返回非空 category。"""
        for i in range(model.rowCount()):
            cat = model.data(model.index(i), Qt.UserRole + 8)
            assert isinstance(cat, str)
            assert cat, f"行 {i} 的 category 为空"

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
        # 排序后 first_class 不一定在 index 0，需查找其所在行
        target_idx = -1
        for i in range(m.rowCount()):
            if m.data(m.index(i), Qt.UserRole + 1) == first_class:
                target_idx = i
                break
        assert target_idx >= 0, f"{first_class} 应在模型中"
        assert m.data(m.index(target_idx), Qt.UserRole + 6) is False

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
    def test_empty_model_returns_empty_tuple(self) -> None:
        """空模型（未加载注册表）时返回空 tuple，而非 None。

        ``all([])`` 为 ``True``，若无防御分支会误判为「全部勾选」返回 ``None``
        （扫描所有文件），与「无提取器可用」的实际语义矛盾。
        """
        m = ExtractorListModel()
        assert m.enabled_extensions() == ()

    def test_all_enabled_returns_none(self, model: ExtractorListModel) -> None:
        """全部勾选时返回 None（表示扫描所有文件，与 Scanner scan_extensions 语义一致）。"""
        assert model.enabled_extensions() is None

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
        """全部不选时返回空 tuple（不扫描任何文件，与 Scanner scan_extensions 语义一致）。"""
        model.unselect_all()
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


class TestCategorySort:
    """行按 (category_order, display_name) 排序，同类相邻。"""

    def test_rows_sorted_by_category_order(self, model: ExtractorListModel) -> None:
        """相邻行的 category 序号应单调非递减。"""
        from fuscan.gui.models.extractor_model import _category_sort_key

        prev_order = -1
        for i in range(model.rowCount()):
            cat = model.data(model.index(i), Qt.UserRole + 8)
            assert isinstance(cat, str)
            order = _category_sort_key(cat)
            assert order >= prev_order, f"行 {i} 的类别 {cat} 序号 {order} 小于前一行 {prev_order}"
            prev_order = order

    def test_same_category_rows_grouped(self, model: ExtractorListModel) -> None:
        """同一类别的行应连续排列（无穿插）。"""
        seen_categories: list[str] = []
        for i in range(model.rowCount()):
            cat = model.data(model.index(i), Qt.UserRole + 8)
            assert isinstance(cat, str)
            if cat not in seen_categories:
                seen_categories.append(cat)
            elif seen_categories[-1] != cat:
                # 类别已出现过但不是上一个 → 穿插，说明排序错误
                pytest.fail(f"类别 {cat} 在行 {i} 穿插出现，排序不连续")


class TestCategoryStates:
    """categoryStates 属性返回各类别的勾选状态。"""

    def test_all_selected_returns_all_state(self, model: ExtractorListModel) -> None:
        """全部勾选时，每个类别状态应为 "all"。"""
        states = model.categoryStates
        assert all(v == "all" for v in states.values()), states

    def test_unselect_all_returns_none_state(self, model: ExtractorListModel) -> None:
        """全部不选时，每个类别状态应为 "none"。"""
        model.unselect_all()
        states = model.categoryStates
        assert all(v == "none" for v in states.values()), states

    def test_partial_selection_returns_partial_state(self, model: ExtractorListModel) -> None:
        """部分勾选时，对应类别状态应为 "partial"。"""
        # 找到第一个类别的第一个提取器，取消勾选
        first_class = model.data(model.index(0), Qt.UserRole + 1)
        first_cat = model.data(model.index(0), Qt.UserRole + 8)
        assert isinstance(first_class, str)
        model.set_extractor_enabled(first_class, False)
        states = model.categoryStates
        # 该类别至少有一个取消勾选，且原本有多个提取器时为 partial
        # （若该类别只有一个提取器则为 "none"）
        cat_count = sum(1 for i in range(model.rowCount()) if model.data(model.index(i), Qt.UserRole + 8) == first_cat)
        expected = "none" if cat_count == 1 else "partial"
        assert states[first_cat] == expected, f"类别 {first_cat} 状态应为 {expected}，实际 {states[first_cat]}"

    def test_states_only_contains_present_categories(self, model: ExtractorListModel) -> None:
        """categoryStates 仅包含实际存在提取器的类别。"""
        states = model.categoryStates
        for cat, state in states.items():
            assert state in {"all", "none", "partial"}
            # 该类别至少有一个提取器
            count = sum(1 for i in range(model.rowCount()) if model.data(model.index(i), Qt.UserRole + 8) == cat)
            assert count > 0, f"类别 {cat} 在模型中无提取器"


class TestSetCategoryEnabled:
    """setCategoryEnabled Slot 批量切换类别内提取器勾选状态。"""

    def test_enable_category_all(self, model: ExtractorListModel) -> None:
        """先全不选，再启用某类别，该类别应全部勾选。"""
        model.unselect_all()
        first_cat = model.data(model.index(0), Qt.UserRole + 8)
        model.setCategoryEnabled(first_cat, True)
        # 该类别下所有提取器都应勾选
        for i in range(model.rowCount()):
            if model.data(model.index(i), Qt.UserRole + 8) == first_cat:
                assert model.data(model.index(i), Qt.UserRole + 6) is True

    def test_disable_category_all(self, model: ExtractorListModel) -> None:
        """先全选，再禁用某类别，该类别应全部不勾选。"""
        model.select_all()
        first_cat = model.data(model.index(0), Qt.UserRole + 8)
        model.setCategoryEnabled(first_cat, False)
        for i in range(model.rowCount()):
            if model.data(model.index(i), Qt.UserRole + 8) == first_cat:
                assert model.data(model.index(i), Qt.UserRole + 6) is False

    def test_does_not_affect_other_categories(self, model: ExtractorListModel) -> None:
        """禁用某类别不应影响其他类别的勾选状态。"""
        model.select_all()
        first_cat = model.data(model.index(0), Qt.UserRole + 8)
        model.setCategoryEnabled(first_cat, False)
        # 其他类别仍应全部勾选
        for i in range(model.rowCount()):
            cat = model.data(model.index(i), Qt.UserRole + 8)
            if cat != first_cat:
                assert model.data(model.index(i), Qt.UserRole + 6) is True, f"行 {i} 类别 {cat} 受到影响"

    def test_updates_category_states(self, model: ExtractorListModel) -> None:
        """禁用某类别后，categoryStates 中该类别应为 "none"。"""
        model.select_all()
        first_cat = model.data(model.index(0), Qt.UserRole + 8)
        model.setCategoryEnabled(first_cat, False)
        assert model.categoryStates[first_cat] == "none"

    def test_unknown_category_noop(self, model: ExtractorListModel) -> None:
        """未知类别名不应影响任何提取器状态。"""
        before = model.disabled_extractors()
        model.setCategoryEnabled("不存在的类别", True)
        assert model.disabled_extractors() == before

    def test_emits_signals_exactly_once(self, model: ExtractorListModel) -> None:
        """批量切换应只 emit 一次 dataChanged 与一次 categoryStatesChanged。"""
        data_changed_count: list[int] = []
        cat_changed_count: list[int] = []
        model.dataChanged.connect(lambda *_: data_changed_count.append(1))
        model.categoryStatesChanged.connect(lambda: cat_changed_count.append(1))  # pyrefly: ignore [missing-attribute]
        first_cat = model.data(model.index(0), Qt.UserRole + 8)
        assert isinstance(first_cat, str)
        model.setCategoryEnabled(first_cat, False)
        assert len(data_changed_count) == 1
        assert len(cat_changed_count) == 1

    def test_no_change_emits_nothing(self, model: ExtractorListModel) -> None:
        """类别状态已与目标一致时，不应 emit 任何信号。"""
        first_cat = model.data(model.index(0), Qt.UserRole + 8)
        assert isinstance(first_cat, str)
        model.select_all()  # 该类别已全部勾选
        emitted: list[int] = []
        model.categoryStatesChanged.connect(lambda: emitted.append(1))  # pyrefly: ignore [missing-attribute]
        model.setCategoryEnabled(first_cat, True)
        assert emitted == []
