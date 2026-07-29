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
        """加载默认注册表后行数应为注册表提取器数 + 压缩包虚拟行数（ZIP/RAR/7z）。"""
        # 压缩包类别为虚拟行（不对应实际提取器类），扩展名走白名单过滤
        archive_virtual_count = 3  # ZIP/RAR/7z
        assert model.rowCount() == len(default_registry.list_extractors()) + archive_virtual_count

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
        assert roles[Qt.UserRole + 9] == b"formatTags"


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

    def test_data_returns_format_tags_default_single(self, model: ExtractorListModel) -> None:
        """formatTags role 默认返回单元素列表 [formatLabel]（iter-103）。"""
        # PdfExtractor 扩展名仅 1 个，formatTags 应回退到 (format_label,)
        for i in range(model.rowCount()):
            if model.data(model.index(i), Qt.UserRole + 1) == "PdfExtractor":
                tags = model.data(model.index(i), Qt.UserRole + 9)
                assert tags == ["PDF"]
                return
        pytest.fail("PdfExtractor 应在默认注册表中")

    def test_data_returns_format_tags_for_source_code(self, model: ExtractorListModel) -> None:
        """SourceCodeExtractor 应返回代表性多标签 [HTML, XML, JSON, JS, C, CPP, PY, SH]（iter-136 扩展）。"""
        for i in range(model.rowCount()):
            if model.data(model.index(i), Qt.UserRole + 1) == "SourceCodeExtractor":
                tags = model.data(model.index(i), Qt.UserRole + 9)
                assert tags == ["HTML", "XML", "JSON", "JS", "C", "CPP", "PY", "SH"]
                return
        pytest.fail("SourceCodeExtractor 应在默认注册表中")

    def test_data_returns_format_tags_non_empty_for_all(self, model: ExtractorListModel) -> None:
        """所有提取器 formatTags 应为非空列表。"""
        for i in range(model.rowCount()):
            tags = model.data(model.index(i), Qt.UserRole + 9)
            assert isinstance(tags, list)
            assert len(tags) >= 1
            for tag in tags:
                assert isinstance(tag, str)
                assert tag
                # 应为大写
                assert tag == tag.upper()

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


class TestCategoryEnabled:
    """iter-104 父节点统一勾选：set_category_enabled 与 category_enabled_state 测试。"""

    def test_category_enabled_state_all_selected(self, model: ExtractorListModel) -> None:
        """默认全部勾选时，任意类别 state=1（全选）。"""
        for i in range(model.rowCount()):
            cat = model.data(model.index(i), Qt.UserRole + 8)
            assert isinstance(cat, str)
            assert model.category_enabled_state(cat) == 1

    def test_category_enabled_state_none_selected(self, model: ExtractorListModel) -> None:
        """全部取消勾选后，任意类别 state=0（全不选）。"""
        model.unselect_all()
        for i in range(model.rowCount()):
            cat = model.data(model.index(i), Qt.UserRole + 8)
            assert isinstance(cat, str)
            assert model.category_enabled_state(cat) == 0

    def test_category_enabled_state_partial(self, model: ExtractorListModel) -> None:
        """仅取消类别内一个提取器，该类别 state=2（部分选中）。"""
        # 找到「文本」类别下的第一个提取器并取消勾选
        target_idx = -1
        target_cat = ""
        for i in range(model.rowCount()):
            cat = model.data(model.index(i), Qt.UserRole + 8)
            if cat == "文本":
                assert isinstance(cat, str)
                target_idx = i
                target_cat = cat
                break
        assert target_idx >= 0, "文本类别应存在"
        class_name = model.data(model.index(target_idx), Qt.UserRole + 1)
        assert isinstance(class_name, str)
        model.set_extractor_enabled(class_name, False)
        # 该类别至少有 2 个提取器（PlainText + SourceCode），state 应为 2
        assert model.category_enabled_state(target_cat) == 2

    def test_set_category_enabled_uncheck_all(self, model: ExtractorListModel) -> None:
        """set_category_enabled(False) 应取消该类别下所有提取器勾选。"""
        # 选定「Office 文档」类别
        target_cat = "Office 文档"
        # 确保该类别有提取器
        cat_rows = [i for i in range(model.rowCount()) if model.data(model.index(i), Qt.UserRole + 8) == target_cat]
        assert len(cat_rows) > 0, "Office 文档类别应有提取器"
        model.set_category_enabled(target_cat, False)
        # 该类别下所有提取器应未勾选
        for i in cat_rows:
            assert model.data(model.index(i), Qt.UserRole + 6) is False
        # state 应为 0
        assert model.category_enabled_state(target_cat) == 0
        # 其他类别不应受影响
        for i in range(model.rowCount()):
            if model.data(model.index(i), Qt.UserRole + 8) != target_cat:
                assert model.data(model.index(i), Qt.UserRole + 6) is True

    def test_set_category_enabled_check_all(self, model: ExtractorListModel) -> None:
        """set_category_enabled(True) 应勾选该类别下所有提取器。"""
        target_cat = "文本"
        # 先全部取消
        model.unselect_all()
        assert model.category_enabled_state(target_cat) == 0
        # 再勾选文本类别
        model.set_category_enabled(target_cat, True)
        assert model.category_enabled_state(target_cat) == 1
        # 其他类别仍为 0
        for i in range(model.rowCount()):
            cat = model.data(model.index(i), Qt.UserRole + 8)
            if cat != target_cat:
                assert model.data(model.index(i), Qt.UserRole + 6) is False

    def test_set_category_enabled_unknown_category_noop(self, model: ExtractorListModel) -> None:
        """未知类别应返回 False 且不修改任何行。"""
        original_count = model.enabled_count
        assert model.set_category_enabled("不存在的类别", False) is False
        assert model.enabled_count == original_count

    def test_category_enabled_state_unknown_category_returns_zero(self, model: ExtractorListModel) -> None:
        """未知类别 state 应返回 0。"""
        assert model.category_enabled_state("不存在的类别") == 0


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


class TestArchiveVirtualRows:
    """压缩包虚拟行：ZIP/RAR/7z 不对应实际提取器类，仅用于文件类型树勾选。"""

    _EXPECTED_ARCHIVES = {
        "ZipArchiveExtractor": ("ZIP 压缩包", "zip"),
        "RarArchiveExtractor": ("RAR 压缩包", "rar"),
        "SevenZArchiveExtractor": ("7z 压缩包", "7z"),
    }

    def test_archive_rows_present(self, model: ExtractorListModel) -> None:
        """加载注册表后应包含三个压缩包虚拟行。"""
        class_names = {model.data(model.index(i), Qt.UserRole + 1) for i in range(model.rowCount())}
        for cls in self._EXPECTED_ARCHIVES:
            assert cls in class_names, f"压缩包虚拟行 {cls} 未出现在模型中"

    def test_archive_rows_category(self, model: ExtractorListModel) -> None:
        """压缩包虚拟行的 category 应为「压缩包」。"""
        for i in range(model.rowCount()):
            class_name = model.data(model.index(i), Qt.UserRole + 1)
            if class_name in self._EXPECTED_ARCHIVES:
                category = model.data(model.index(i), Qt.UserRole + 8)
                assert category == "压缩包", f"{class_name} 类别应为「压缩包」，实际 {category}"

    def test_archive_rows_extensions(self, model: ExtractorListModel) -> None:
        """压缩包虚拟行的扩展名应与定义一致。"""
        for i in range(model.rowCount()):
            class_name = model.data(model.index(i), Qt.UserRole + 1)
            if class_name in self._EXPECTED_ARCHIVES:
                _, expected_ext = self._EXPECTED_ARCHIVES[class_name]
                exts_text = model.data(model.index(i), Qt.UserRole + 3)
                assert exts_text == expected_ext, f"{class_name} 扩展名应为 {expected_ext}，实际 {exts_text}"

    def test_archive_rows_enabled_by_default(self, model: ExtractorListModel) -> None:
        """默认加载（无 disabled_extractors）时压缩包虚拟行应勾选。"""
        for i in range(model.rowCount()):
            class_name = model.data(model.index(i), Qt.UserRole + 1)
            if class_name in self._EXPECTED_ARCHIVES:
                enabled = model.data(model.index(i), Qt.UserRole + 6)
                assert enabled is True, f"{class_name} 默认应勾选"

    def test_archive_rows_disabled_via_config(self) -> None:
        """通过 disabled_extractors 参数应能取消压缩包勾选。"""
        m = ExtractorListModel()
        m.load_from_registry(
            disabled_extractors=["ZipArchiveExtractor", "RarArchiveExtractor", "SevenZArchiveExtractor"]
        )
        disabled = m.disabled_extractors()
        assert "ZipArchiveExtractor" in disabled
        assert "RarArchiveExtractor" in disabled
        assert "SevenZArchiveExtractor" in disabled

    def test_archive_rows_toggle_enabled(self, model: ExtractorListModel) -> None:
        """set_extractor_enabled 应能切换压缩包虚拟行勾选状态。"""
        model.set_extractor_enabled("ZipArchiveExtractor", False)
        assert "ZipArchiveExtractor" in model.disabled_extractors()
        model.set_extractor_enabled("ZipArchiveExtractor", True)
        assert "ZipArchiveExtractor" not in model.disabled_extractors()

    def test_archive_extensions_in_enabled_extensions(self, model: ExtractorListModel) -> None:
        """勾选压缩包虚拟行后，enabled_extensions 应包含 zip/rar/7z 扩展名。"""
        # 取消勾选一个非压缩包提取器，触发非 None 路径
        first_class = model.data(model.index(0), Qt.UserRole + 1)
        assert isinstance(first_class, str)
        model.set_extractor_enabled(first_class, False)
        exts = model.enabled_extensions()
        assert exts is not None
        assert "zip" in exts
        assert "rar" in exts
        assert "7z" in exts

    def test_archive_extensions_excluded_when_disabled(self, model: ExtractorListModel) -> None:
        """取消压缩包勾选后，enabled_extensions 不应包含对应扩展名。"""
        model.set_extractor_enabled("ZipArchiveExtractor", False)
        # 取消勾选一个非压缩包提取器，触发非 None 路径
        first_class = model.data(model.index(0), Qt.UserRole + 1)
        assert isinstance(first_class, str)
        model.set_extractor_enabled(first_class, False)
        exts = model.enabled_extensions()
        assert exts is not None
        assert "zip" not in exts
        assert "rar" in exts
        assert "7z" in exts

    def test_archive_format_label(self, model: ExtractorListModel) -> None:
        """压缩包虚拟行的 formatLabel 应为扩展名大写。"""
        expected_labels = {
            "ZipArchiveExtractor": "ZIP",
            "RarArchiveExtractor": "RAR",
            "SevenZArchiveExtractor": "7Z",
        }
        for i in range(model.rowCount()):
            class_name = model.data(model.index(i), Qt.UserRole + 1)
            if class_name in expected_labels:
                label = model.data(model.index(i), Qt.UserRole + 7)
                assert label == expected_labels[class_name], (
                    f"{class_name} formatLabel 应为 {expected_labels[class_name]}，实际 {label}"
                )
