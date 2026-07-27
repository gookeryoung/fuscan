"""``ResultListModel`` 单元测试。

覆盖 ``set_results``/``clear``/``get_result``/``data()``/``roleNames()``/``rowCount()``。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    try:
        from PySide2.QtCore import QModelIndex, Qt
    except ImportError:  # pragma: no cover
        from PySide6.QtCore import QModelIndex, Qt  # pyrefly: ignore [missing-import]

    from fuscan.gui.models.result_model import ResultListModel
    from fuscan.rules.model import Severity
    from fuscan.scanner.result import RuleHit, ScanResult

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过结果模型测试", allow_module_level=True)


def _build_results(tmp_path: Path) -> tuple[ScanResult, ...]:
    """构造 2 条命中结果。"""
    hit1 = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="命中 password")
    hit2 = RuleHit(rule_name="API 密钥", severity=Severity.WARNING, detail="命中 api_key")
    return (
        ScanResult(path=tmp_path / "a.txt", size=10, hits=(hit1,), errors=0),
        ScanResult(path=tmp_path / "b.txt", size=20, hits=(hit1, hit2), errors=0),
    )


@pytest.fixture()
def model(tmp_path: Path) -> ResultListModel:
    m = ResultListModel()
    m.set_results(_build_results(tmp_path))
    return m


class TestRowCount:
    def test_empty_model_has_zero_rows(self) -> None:
        m = ResultListModel()
        assert m.rowCount() == 0

    def test_set_results_populates_rows(self, model: ResultListModel) -> None:
        assert model.rowCount() == 2

    def test_rowcount_with_parent_index_returns_zero(self, model: ResultListModel) -> None:
        parent = model.index(0)
        assert model.rowCount(parent) == 0


class TestRoleNames:
    def test_role_names(self, model: ResultListModel) -> None:
        roles = model.roleNames()
        assert roles[Qt.UserRole + 1] == b"filePath"
        assert roles[Qt.UserRole + 2] == b"ruleName"
        assert roles[Qt.UserRole + 3] == b"severityText"
        assert roles[Qt.UserRole + 4] == b"severityColor"
        assert roles[Qt.UserRole + 5] == b"hitsCount"
        assert roles[Qt.UserRole + 6] == b"index"


class TestData:
    def test_data_invalid_index_returns_empty(self, model: ResultListModel) -> None:
        invalid = QModelIndex()
        assert model.data(invalid, Qt.UserRole + 1) == ""

    def test_data_returns_file_path(self, model: ResultListModel, tmp_path: Path) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.UserRole + 1) == str(tmp_path / "a.txt")

    def test_data_returns_rule_name(self, model: ResultListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.UserRole + 2) == "敏感内容"

    def test_data_returns_severity_text(self, model: ResultListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.UserRole + 3) == "严重"

    def test_data_returns_severity_color(self, model: ResultListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.UserRole + 4) == "#D73A49"

    def test_data_returns_hits_count(self, model: ResultListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.UserRole + 5) == 1
        idx2 = model.index(1)
        assert model.data(idx2, Qt.UserRole + 5) == 2

    def test_data_returns_index(self, model: ResultListModel) -> None:
        idx = model.index(1)
        assert model.data(idx, Qt.UserRole + 6) == 1

    def test_data_unknown_role_returns_empty(self, model: ResultListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.DisplayRole) == ""

    def test_data_multi_hits_returns_first_rule_name(self, model: ResultListModel) -> None:
        """多规则命中时取第一个规则名作为主要展示。"""
        idx = model.index(1)
        assert model.data(idx, Qt.UserRole + 2) == "敏感内容"


class TestClear:
    def test_clear_empties_rows(self, model: ResultListModel) -> None:
        model.clear()
        assert model.rowCount() == 0
        assert model.results == ()


class TestGetResult:
    def test_get_result_valid_index(self, model: ResultListModel, tmp_path: Path) -> None:
        result = model.get_result(0)
        assert result is not None
        assert result.path == tmp_path / "a.txt"

    def test_get_result_invalid_negative(self, model: ResultListModel) -> None:
        assert model.get_result(-1) is None

    def test_get_result_invalid_out_of_range(self, model: ResultListModel) -> None:
        assert model.get_result(100) is None


class TestResultsProperty:
    def test_results_returns_tuple(self, model: ResultListModel) -> None:
        results = model.results
        assert isinstance(results, tuple)
        assert len(results) == 2

    def test_empty_model_results_tuple(self) -> None:
        m = ResultListModel()
        assert m.results == ()


# ----------------------------- iter-112 过滤+排序测试 -----------------------------


def _build_filter_results(tmp_path: Path) -> tuple[ScanResult, ...]:
    """构造 4 条命中结果，覆盖不同规则名/严重度/路径/命中数。"""
    h_critical = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="d1")
    h_warning = RuleHit(rule_name="API 密钥", severity=Severity.WARNING, detail="d2")
    h_info = RuleHit(rule_name="提示信息", severity=Severity.INFO, detail="d3")
    return (
        ScanResult(path=tmp_path / "config" / "secret.txt", size=10, hits=(h_critical,), errors=0),
        ScanResult(path=tmp_path / "app.py", size=20, hits=(h_warning,), errors=0),
        ScanResult(path=tmp_path / "README.md", size=30, hits=(h_info, h_warning), errors=0),
        ScanResult(path=tmp_path / "config" / "db.yaml", size=40, hits=(h_critical, h_warning, h_info), errors=0),
    )


@pytest.fixture()
def filter_model(tmp_path: Path) -> ResultListModel:
    m = ResultListModel()
    m.set_results(_build_filter_results(tmp_path))
    return m


class TestIter112FilterText:
    """iter-112：文件路径模糊过滤。"""

    def test_filter_text_case_insensitive(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_text("CONFIG")
        assert filter_model.filtered_count == 2
        paths = [str(r.path) for r in filter_model.filtered_results]
        assert all("config" in p.lower() for p in paths)

    def test_filter_text_partial_match(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_text("secret")
        assert filter_model.filtered_count == 1
        assert "secret" in str(filter_model.filtered_results[0].path).lower()

    def test_filter_text_no_match(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_text("nonexistent_zzz")
        assert filter_model.filtered_count == 0
        assert filter_model.rowCount() == 0

    def test_filter_text_empty_clears_dimension(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_text("config")
        assert filter_model.filtered_count == 2
        filter_model.set_filter_text("")
        assert filter_model.filtered_count == 4

    def test_filter_text_whitespace_normalized(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_text("  config  ")
        assert filter_model.filtered_count == 2

    def test_filter_text_idempotent_same_value(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_text("config")
        count1 = filter_model.filtered_count
        filter_model.set_filter_text("config")  # 重复设置应无副作用
        assert filter_model.filtered_count == count1


class TestIter112FilterRules:
    """iter-112：规则名多选过滤。"""

    def test_filter_single_rule(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_rules(["敏感内容"])
        # 命中敏感内容的结果：config/secret.txt 与 config/db.yaml
        assert filter_model.filtered_count == 2

    def test_filter_multiple_rules(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_rules(["敏感内容", "API 密钥"])
        # 四个结果均含敏感内容或 API 密钥（README 含 API 密钥，db.yaml 含两者）
        assert filter_model.filtered_count == 4

    def test_filter_rule_no_match(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_rules(["不存在的规则"])
        assert filter_model.filtered_count == 0

    def test_filter_rules_empty_clears(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_rules(["敏感内容"])
        assert filter_model.filtered_count == 2
        filter_model.set_filter_rules([])
        assert filter_model.filtered_count == 4

    def test_filter_rules_none_clears(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_rules(["敏感内容"])
        filter_model.set_filter_rules(None)
        assert filter_model.filtered_count == 4


class TestIter112FilterSeverities:
    """iter-112：严重度多选过滤。"""

    def test_filter_single_severity(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_severities([Severity.CRITICAL])
        # 含 CRITICAL 的结果：secret.txt 与 db.yaml
        assert filter_model.filtered_count == 2

    def test_filter_multiple_severities(self, filter_model: ResultListModel) -> None:
        # max_severity 为 WARNING 的结果：app.py 与 README.md
        filter_model.set_filter_severities([Severity.WARNING])
        assert filter_model.filtered_count == 2

    def test_filter_severity_empty_clears(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_severities([Severity.CRITICAL])
        filter_model.set_filter_severities([])
        assert filter_model.filtered_count == 4


class TestIter112CombinedFilter:
    """iter-112：多维度过滤组合。"""

    def test_text_plus_severity(self, filter_model: ResultListModel) -> None:
        # 路径含 config 且严重度为 CRITICAL
        filter_model.set_filter_text("config")
        filter_model.set_filter_severities([Severity.CRITICAL])
        # 命中：config/secret.txt 与 config/db.yaml 均为 CRITICAL
        assert filter_model.filtered_count == 2

    def test_text_plus_rule_plus_severity(self, filter_model: ResultListModel) -> None:
        # 路径含 config + 规则含 API 密钥 + 严重度 CRITICAL
        # 仅 config/db.yaml 同时满足（路径含 config + 含 API 密钥规则 + max_severity=CRITICAL）
        filter_model.set_filter_text("config")
        filter_model.set_filter_rules(["API 密钥"])
        filter_model.set_filter_severities([Severity.CRITICAL])
        assert filter_model.filtered_count == 1
        assert "db.yaml" in str(filter_model.filtered_results[0].path)

    def test_clear_filters_preserves_sort(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_text("config")
        filter_model.set_sort("filePath", ascending=False)
        assert filter_model.filtered_count == 2
        filter_model.clear_filters()
        # 过滤清除后排序保留
        assert filter_model.filtered_count == 4
        assert filter_model.sort_field == "filePath"
        assert filter_model.sort_ascending is False


class TestIter112Sort:
    """iter-112：排序功能。"""

    def test_sort_file_path_ascending(self, filter_model: ResultListModel) -> None:
        filter_model.set_sort("filePath", ascending=True)
        paths = [str(r.path) for r in filter_model.filtered_results]
        assert paths == sorted(paths, key=str.lower)

    def test_sort_file_path_descending(self, filter_model: ResultListModel) -> None:
        filter_model.set_sort("filePath", ascending=False)
        paths = [str(r.path) for r in filter_model.filtered_results]
        assert paths == sorted(paths, key=str.lower, reverse=True)

    def test_sort_hits_count_ascending(self, filter_model: ResultListModel) -> None:
        filter_model.set_sort("hitsCount", ascending=True)
        counts = [len(r.hits) for r in filter_model.filtered_results]
        assert counts == sorted(counts)

    def test_sort_hits_count_descending(self, filter_model: ResultListModel) -> None:
        filter_model.set_sort("hitsCount", ascending=False)
        counts = [len(r.hits) for r in filter_model.filtered_results]
        assert counts == sorted(counts, reverse=True)

    def test_sort_severity_ascending(self, filter_model: ResultListModel) -> None:
        # 升序：INFO(1) → WARNING(2) → CRITICAL(3)
        from fuscan.gui.models.result_model import _SEVERITY_WEIGHT

        filter_model.set_sort("severity", ascending=True)
        weights = [_SEVERITY_WEIGHT[r.max_severity] for r in filter_model.filtered_results]
        assert weights == sorted(weights)

    def test_sort_severity_descending(self, filter_model: ResultListModel) -> None:
        from fuscan.gui.models.result_model import _SEVERITY_WEIGHT

        filter_model.set_sort("severity", ascending=False)
        weights = [_SEVERITY_WEIGHT[r.max_severity] for r in filter_model.filtered_results]
        assert weights == sorted(weights, reverse=True)

    def test_sort_default_keeps_original_order(self, filter_model: ResultListModel, tmp_path: Path) -> None:
        filter_model.set_sort("filePath", ascending=True)
        filter_model.set_sort("default", ascending=True)
        # default 应保持 set_results 时的原始顺序
        first_path = str(filter_model.filtered_results[0].path)
        assert first_path.endswith(str(tmp_path / "config" / "secret.txt"))

    def test_sort_unknown_field_ignored(self, filter_model: ResultListModel) -> None:
        # 未知字段应被忽略，不改变现状
        filter_model.set_sort("unknown_field", ascending=True)
        assert filter_model.sort_field == "default"  # 未变更

    def test_sort_idempotent_same_value(self, filter_model: ResultListModel) -> None:
        filter_model.set_sort("filePath", ascending=True)
        first_paths = [str(r.path) for r in filter_model.filtered_results]
        filter_model.set_sort("filePath", ascending=True)  # 重复设置
        second_paths = [str(r.path) for r in filter_model.filtered_results]
        assert first_paths == second_paths


class TestIter112Properties:
    """iter-112：property 暴露。"""

    def test_total_count_property(self, filter_model: ResultListModel) -> None:
        assert filter_model.total_count == 4

    def test_filtered_count_property(self, filter_model: ResultListModel) -> None:
        assert filter_model.filtered_count == 4
        filter_model.set_filter_text("config")
        assert filter_model.filtered_count == 2
        assert filter_model.total_count == 4  # 原始总数不变

    def test_filter_text_property(self, filter_model: ResultListModel) -> None:
        assert filter_model.filter_text == ""
        filter_model.set_filter_text("config")
        assert filter_model.filter_text == "config"

    def test_filter_rules_property(self, filter_model: ResultListModel) -> None:
        assert filter_model.filter_rules == frozenset()
        filter_model.set_filter_rules(["敏感内容", "API 密钥"])
        assert filter_model.filter_rules == frozenset({"敏感内容", "API 密钥"})

    def test_filter_severities_property(self, filter_model: ResultListModel) -> None:
        assert filter_model.filter_severities == frozenset()
        filter_model.set_filter_severities([Severity.CRITICAL])
        assert filter_model.filter_severities == frozenset({Severity.CRITICAL})

    def test_sort_field_and_ascending_properties(self, filter_model: ResultListModel) -> None:
        assert filter_model.sort_field == "default"
        assert filter_model.sort_ascending is True
        filter_model.set_sort("filePath", ascending=False)
        assert filter_model.sort_field == "filePath"
        assert filter_model.sort_ascending is False


class TestIter112GetResultOnFilteredView:
    """iter-112：get_result 基于过滤后视图。"""

    def test_get_result_after_filter(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_text("secret")
        assert filter_model.filtered_count == 1
        result = filter_model.get_result(0)
        assert result is not None
        assert "secret" in str(result.path).lower()

    def test_get_result_out_of_range_after_filter(self, filter_model: ResultListModel) -> None:
        filter_model.set_filter_text("secret")
        # 过滤后只有 1 条，索引 1 应返回 None
        assert filter_model.get_result(1) is None
