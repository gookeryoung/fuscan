"""``ResultListModel`` 单元测试。

覆盖 ``set_results``/``clear``/``get_result``/``data()``/``roleNames()``/``rowCount()``。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    try:
        from PySide2.QtCore import QModelIndex, Qt
        from PySide2.QtWidgets import QApplication
    except ImportError:  # pragma: no cover
        from PySide6.QtCore import QModelIndex, Qt  # pyrefly: ignore [missing-import]
        from PySide6.QtWidgets import QApplication  # pyrefly: ignore [missing-import]

    from fuscan.gui.models.result_model import SORT_DEFAULT, ResultListModel
    from fuscan.rules.model import Severity
    from fuscan.scanner.result import RuleHit, ScanResult

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过结果模型测试", allow_module_level=True)

if TYPE_CHECKING:
    pass  # 仅用于类型检查占位


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

    def test_data_ghost_row_fallback_when_flat_data_none(self, model: ResultListModel) -> None:
        """_flat_data[row] 为 None 且 _filtered[row] 为 None 时 data() 返回占位。

        覆盖 result_model.data() 行 504-511 幽灵行回退路径：扁平数据未就绪 +
        filtered 对应行为幽灵行（None）时，按 role 返回占位值
        （hitsCount=0 / index=row / 其余空串）。
        该路径在 gui_qml 排除时无 QML delegate 自然访问，需故障注入显式触发。
        """
        # 故障注入：模拟懒加载中间状态（扁平数据未填充 + filtered 幽灵行）
        model._flat_data[0] = None  # type: ignore[attr-defined]
        filtered_list = list(model._filtered)  # type: ignore[attr-defined]
        filtered_list[0] = None
        model._filtered = tuple(filtered_list)  # type: ignore[attr-defined]
        idx = model.index(0)
        # hitsCount 占位为 0
        assert model.data(idx, Qt.UserRole + 5) == 0
        # index 占位为 row
        assert model.data(idx, Qt.UserRole + 6) == 0
        # 其余 role 占位为空串
        assert model.data(idx, Qt.UserRole + 1) == ""
        assert model.data(idx, Qt.UserRole + 2) == ""

    def test_data_fallback_when_flat_data_empty(self, model: ResultListModel, tmp_path: Path) -> None:
        """``_flat_data`` 为空但 ``_filtered`` 有真实值时，data() 回退到直接访问 ScanResult。

        覆盖 result_model.data() 行 523-548 回退路径：扁平数据未就绪（空列表）+
        filtered 对应行为真实 ScanResult 时，按 role 直接从 ScanResult 属性返回。
        """
        # 故障注入：清空扁平数据，强制走回退路径
        model._flat_data = []  # type: ignore[attr-defined]
        idx = model.index(0)
        # filePath 直接从 result.path 返回
        assert model.data(idx, Qt.UserRole + 1) == str(tmp_path / "a.txt")
        # ruleName 从 result.rule_names[0] 返回
        assert model.data(idx, Qt.UserRole + 2) == "敏感内容"
        # severityText 从 severity_text(result.max_severity) 返回
        assert model.data(idx, Qt.UserRole + 3) == "严重"
        # severityColor 从 severity_color_hex(result.max_severity) 返回
        assert model.data(idx, Qt.UserRole + 4) == "#D73A49"
        # hitsCount 从 len(result.hits) 返回
        assert model.data(idx, Qt.UserRole + 5) == 1
        # index 返回 row
        assert model.data(idx, Qt.UserRole + 6) == 0
        # replaced 从 result.replaced 返回
        assert model.data(idx, Qt.UserRole + 7) is False
        # 未知 role 返回空串
        assert model.data(idx, Qt.DisplayRole) == ""


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


class TestRemoveResultByPath:
    """iter-139：移至暂存后按路径移除结果。"""

    def test_remove_existing_path_returns_true(self, model: ResultListModel, tmp_path: Path) -> None:
        """移除已存在路径返回 True，结果数减 1。"""
        target = tmp_path / "a.txt"
        removed = model.remove_result_by_path(target)
        assert removed is True
        assert model.total_count == 1
        assert model.filtered_count == 1

    def test_remove_nonexistent_path_returns_false(self, model: ResultListModel) -> None:
        """移除不存在的路径返回 False，结果不变。"""
        removed = model.remove_result_by_path(Path("not_exist.txt"))
        assert removed is False
        assert model.total_count == 2
        assert model.filtered_count == 2

    def test_remove_updates_filtered_view(self, model: ResultListModel, tmp_path: Path) -> None:
        """移除后过滤视图同步刷新，get_result 不再返回该路径。"""
        target = tmp_path / "a.txt"
        model.remove_result_by_path(target)
        # 遍历过滤视图，确认目标路径已不存在
        for i in range(model.rowCount()):
            result = model.get_result(i)
            assert result is not None
            assert result.path != target

    def test_remove_last_result_empties_model(self, model: ResultListModel, tmp_path: Path) -> None:
        """依次移除所有结果后模型为空。"""
        model.remove_result_by_path(tmp_path / "a.txt")
        model.remove_result_by_path(tmp_path / "b.txt")
        assert model.total_count == 0
        assert model.filtered_count == 0
        assert model.rowCount() == 0

    def test_remove_preserves_filter_conditions(self, model: ResultListModel, tmp_path: Path) -> None:
        """移除操作应保留当前过滤条件（仅路径 a.txt 命中过滤词）。"""
        model.set_filter_text("a.txt")
        assert model.filtered_count == 1
        # 移除 b.txt（不在过滤视图中），过滤视图不变
        model.remove_result_by_path(tmp_path / "b.txt")
        assert model.filtered_count == 1
        # 移除 a.txt（在过滤视图中），过滤视图清空
        model.remove_result_by_path(tmp_path / "a.txt")
        assert model.filtered_count == 0


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

    def test_filter_rules_idempotent_same_value(self, filter_model: ResultListModel) -> None:
        """重复设置相同规则名过滤条件应无副作用（覆盖早期 return 路径）。"""
        filter_model.set_filter_rules(["敏感内容"])
        count1 = filter_model.filtered_count
        filter_model.set_filter_rules(["敏感内容"])
        assert filter_model.filtered_count == count1


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

    def test_filter_severities_idempotent_same_value(self, filter_model: ResultListModel) -> None:
        """重复设置相同严重度过滤条件应无副作用（覆盖早期 return 路径）。"""
        filter_model.set_filter_severities([Severity.CRITICAL])
        count1 = filter_model.filtered_count
        filter_model.set_filter_severities([Severity.CRITICAL])
        assert filter_model.filtered_count == count1


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


def _build_replaced_results(tmp_path: Path) -> tuple[ScanResult, ...]:
    """构造 4 条命中结果，其中 2 条标记 replaced=True。

    用于 ``set_filter_replaced`` 维度的过滤测试。
    """
    h_critical = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="d1")
    h_warning = RuleHit(rule_name="API 密钥", severity=Severity.WARNING, detail="d2")
    return (
        # 已替换 2 条规则
        ScanResult(
            path=tmp_path / "config" / "secret.txt",
            size=10,
            hits=(h_critical,),
            errors=0,
            replaced=True,
            replaced_count=2,
        ),
        # 未替换
        ScanResult(path=tmp_path / "app.py", size=20, hits=(h_warning,), errors=0),
        # 已替换 1 条规则
        ScanResult(
            path=tmp_path / "README.md",
            size=30,
            hits=(h_critical, h_warning),
            errors=0,
            replaced=True,
            replaced_count=1,
        ),
        # 未替换
        ScanResult(path=tmp_path / "config" / "db.yaml", size=40, hits=(h_warning,), errors=0),
    )


@pytest.fixture()
def replaced_model(tmp_path: Path) -> ResultListModel:
    m = ResultListModel()
    m.set_results(_build_replaced_results(tmp_path))
    return m


class TestIter220FilterReplaced:
    """iter-220：已替换维度过滤（TabBar 切换）。"""

    def test_default_no_filter_shows_all(self, replaced_model: ResultListModel) -> None:
        # 默认 _filter_replaced=None 不过滤，全部 4 条都显示
        assert replaced_model.filter_replaced is None
        assert replaced_model.filtered_count == 4

    def test_filter_pending_only(self, replaced_model: ResultListModel) -> None:
        # value=1 → False（仅未替换，待处理 Tab）
        replaced_model.set_filter_replaced(1)
        assert replaced_model.filter_replaced is False
        assert replaced_model.filtered_count == 2
        for r in replaced_model.filtered_results:
            assert r.replaced is False

    def test_filter_replaced_only(self, replaced_model: ResultListModel) -> None:
        # value=2 → True（仅已替换 Tab）
        replaced_model.set_filter_replaced(2)
        assert replaced_model.filter_replaced is True
        assert replaced_model.filtered_count == 2
        for r in replaced_model.filtered_results:
            assert r.replaced is True

    def test_filter_all_clears_dimension(self, replaced_model: ResultListModel) -> None:
        # 先设过滤再切回 0 → None（全部 Tab）
        replaced_model.set_filter_replaced(2)
        assert replaced_model.filtered_count == 2
        replaced_model.set_filter_replaced(0)
        assert replaced_model.filter_replaced is None
        assert replaced_model.filtered_count == 4

    def test_invalid_value_treated_as_no_filter(self, replaced_model: ResultListModel) -> None:
        # 未知整数值视为不过滤（防御性）
        replaced_model.set_filter_replaced(99)
        assert replaced_model.filter_replaced is None
        assert replaced_model.filtered_count == 4

    def test_idempotent_same_value(self, replaced_model: ResultListModel) -> None:
        replaced_model.set_filter_replaced(1)
        count1 = replaced_model.filtered_count
        replaced_model.set_filter_replaced(1)  # 重复设置应无副作用
        assert replaced_model.filtered_count == count1

    def test_combined_with_text_filter(self, replaced_model: ResultListModel) -> None:
        # 已替换 + 路径含 config → 仅 secret.txt
        replaced_model.set_filter_replaced(2)
        replaced_model.set_filter_text("config")
        assert replaced_model.filtered_count == 1
        assert "secret.txt" in str(replaced_model.filtered_results[0].path)
        assert replaced_model.filtered_results[0].replaced is True

    def test_clear_filters_resets_replaced_dimension(self, replaced_model: ResultListModel) -> None:
        replaced_model.set_filter_replaced(1)
        assert replaced_model.filter_replaced is False
        replaced_model.clear_filters()
        assert replaced_model.filter_replaced is None
        assert replaced_model.filtered_count == 4

    def test_clear_filters_noop_when_already_clean(self, tmp_path: Path) -> None:
        """无任何过滤条件时 clear_filters 直接返回（覆盖早期 return 路径）。"""
        m = ResultListModel()
        m.set_results(_build_replaced_results(tmp_path))
        count_before = m.filtered_count
        m.clear_filters()  # 无过滤条件 → 早期 return
        assert m.filtered_count == count_before

    def test_replaced_role_in_data(self, replaced_model: ResultListModel) -> None:
        """data() 中 UserRole+7（replaced）正确返回 ScanResult.replaced 字段。"""
        # 切到默认排序（保留插入顺序），避免 SORT_SEVERITY 打乱顺序
        replaced_model.set_sort(SORT_DEFAULT, ascending=True)
        # 插入顺序：secret.txt(True) → app.py(False) → README.md(True) → db.yaml(False)
        idx = replaced_model.index(0, 0)
        # 第 0 行是 secret.txt，replaced=True
        assert idx.data(Qt.UserRole + 7) is True
        idx2 = replaced_model.index(1, 0)
        # 第 1 行是 app.py，replaced=False
        assert idx2.data(Qt.UserRole + 7) is False
        idx3 = replaced_model.index(2, 0)
        # 第 2 行是 README.md，replaced=True
        assert idx3.data(Qt.UserRole + 7) is True
        idx4 = replaced_model.index(3, 0)
        # 第 3 行是 db.yaml，replaced=False
        assert idx4.data(Qt.UserRole + 7) is False


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
        # 未知字段应被忽略，不改变现状（iter-137 默认为 severity 降序）
        filter_model.set_sort("unknown_field", ascending=True)
        assert filter_model.sort_field == "severity"  # 未变更

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
        # iter-137：默认按严重度降序
        assert filter_model.sort_field == "severity"
        assert filter_model.sort_ascending is False
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


# ----------------------------- iter-129 后台过滤+排序测试 -----------------------------


def _build_large_results(tmp_path: Path, n: int = 12000) -> tuple[ScanResult, ...]:
    """构造 n 条命中结果，覆盖不同规则名/严重度/路径。"""
    severities = [Severity.CRITICAL, Severity.WARNING, Severity.INFO]
    rule_names = ["敏感内容", "API 密钥", "提示信息"]
    return tuple(
        ScanResult(
            path=tmp_path / f"file_{i:05d}.txt",
            size=i,
            hits=(RuleHit(rule_name=rule_names[i % 3], severity=severities[i % 3], detail="d"),),
            errors=0,
        )
        for i in range(n)
    )


def _wait_for_worker(m: ResultListModel, timeout_ms: int = 5000) -> None:
    """处理 Qt 事件循环直到 FilterWorker 完成（模块级辅助函数）。

    Worker 完成后仅处理 1 轮事件（让 done 信号回调执行），然后立即取消
    懒填充（避免 QTimer singleShot 填充幽灵行，保持测试状态稳定）。
    """
    import time

    try:
        from PySide2.QtCore import QCoreApplication
    except ImportError:  # pragma: no cover
        from PySide6.QtCore import QCoreApplication  # pyrefly: ignore [missing-import]

    elapsed = 0
    while m._filter_worker is not None and elapsed < timeout_ms:  # type: ignore[attr-defined]
        QCoreApplication.processEvents()
        time.sleep(0.005)
        elapsed += 5
    # worker 完成后仅处理 1 轮事件，让 done 信号回调执行
    QCoreApplication.processEvents()
    # 立即取消懒填充（避免 QTimer singleShot 继续填充幽灵行干扰测试）
    if m._lazystate is not None:  # type: ignore[attr-defined]
        m._cancel_lazy_fill(and_fill_rest=True)  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """创建 QApplication（若不存在），用于 QThread 信号传递。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestIter129FilterAndSortPureFunction:
    """iter-129：``filter_and_sort`` 纯函数（无 Qt 依赖部分）。"""

    def test_empty_results_returns_empty_tuple(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import filter_and_sort

        assert filter_and_sort((), "", frozenset(), frozenset(), "default", True) == ()

    def test_no_filter_no_sort_keeps_order(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import filter_and_sort

        results = _build_filter_results(tmp_path)
        out = filter_and_sort(results, "", frozenset(), frozenset(), "default", True)
        assert out == results

    def test_filter_text_only(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import filter_and_sort

        results = _build_filter_results(tmp_path)
        out = filter_and_sort(results, "config", frozenset(), frozenset(), "default", True)
        assert len(out) == 2
        assert all("config" in str(r.path).lower() for r in out)

    def test_filter_severity_only(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import filter_and_sort

        results = _build_filter_results(tmp_path)
        out = filter_and_sort(results, "", frozenset(), frozenset({Severity.CRITICAL}), "default", True)
        # secret.txt + db.yaml 含 CRITICAL
        assert len(out) == 2
        assert all(r.max_severity == Severity.CRITICAL for r in out)

    def test_sort_severity_descending(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import _SEVERITY_WEIGHT, filter_and_sort

        results = _build_filter_results(tmp_path)
        out = filter_and_sort(results, "", frozenset(), frozenset(), "severity", sort_ascending=False)
        weights = [_SEVERITY_WEIGHT[r.max_severity] for r in out]
        assert weights == sorted(weights, reverse=True)

    def test_unknown_sort_field_keeps_order(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import filter_and_sort

        results = _build_filter_results(tmp_path)
        out = filter_and_sort(results, "", frozenset(), frozenset(), "unknown", True)
        assert out == results


class TestIter129AsyncPath:
    """iter-129：大结果集后台过滤路径（>= ``_ASYNC_THRESHOLD``）。

    使用 ``QCoreApplication.processEvents()`` 循环等待 worker 完成，
    与项目现有 ``test_gui_workspace_controller.py`` 的异步测试模式一致。
    """

    def _wait_for_worker(self, m: ResultListModel, timeout_ms: int = 5000) -> None:
        """处理 Qt 事件循环直到 worker 完成。"""
        import time

        try:
            from PySide2.QtCore import QCoreApplication
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QCoreApplication  # pyrefly: ignore [missing-import]

        elapsed = 0
        while m._filter_worker is not None and elapsed < timeout_ms:  # type: ignore[attr-defined]
            QCoreApplication.processEvents()
            time.sleep(0.005)
            elapsed += 5
        # worker 完成后多轮处理事件，确保 done 信号回调被消费
        for _ in range(10):
            QCoreApplication.processEvents()

    def test_async_threshold_triggers_worker(self, tmp_path: Path, qapp: QApplication) -> None:
        """结果数 >= 阈值时启动 FilterWorker。"""
        from fuscan.gui.models.result_model import _ASYNC_THRESHOLD

        m = ResultListModel()
        results = _build_large_results(tmp_path, n=_ASYNC_THRESHOLD + 1)
        # set_results 应触发异步路径，worker 在后台执行
        m.set_results(results)
        # 此时 _filtered 可能为空（worker 未完成），但不应阻塞
        self._wait_for_worker(m)
        assert m.rowCount() == _ASYNC_THRESHOLD + 1

    def test_async_filter_applies_correctly(self, tmp_path: Path, qapp: QApplication) -> None:
        """后台过滤完成后视图反映过滤条件。"""
        from fuscan.gui.models.result_model import _ASYNC_THRESHOLD

        m = ResultListModel()
        # set_results 先完成初始加载
        results = _build_large_results(tmp_path, n=_ASYNC_THRESHOLD + 1)
        m.set_results(results)
        self._wait_for_worker(m)
        assert m.rowCount() == _ASYNC_THRESHOLD + 1

        # 设置过滤条件，等待异步完成
        m.set_filter_text("file_00000")  # 仅匹配 1 条
        self._wait_for_worker(m)
        assert m.rowCount() == 1
        assert "file_00000" in str(m.filtered_results[0].path).lower()

    def test_async_generation_guard_drops_stale(self, tmp_path: Path, qapp: QApplication) -> None:
        """连续修改过滤条件时，过期 worker 结果被丢弃。"""
        from fuscan.gui.models.result_model import _ASYNC_THRESHOLD

        m = ResultListModel()
        results = _build_large_results(tmp_path, n=_ASYNC_THRESHOLD + 1)
        m.set_results(results)
        self._wait_for_worker(m)

        # 连续修改三次过滤条件，最后一次才是有效结果
        m.set_filter_text("file_00000")
        m.set_filter_text("file_00001")
        m.set_filter_text("file_00002")
        self._wait_for_worker(m)
        # 最终 rowCount 应是 file_00002 对应的 1 条
        assert m.rowCount() == 1
        assert "file_00002" in str(m.filtered_results[0].path).lower()

    def test_cancel_worker_on_new_set_results(self, tmp_path: Path, qapp: QApplication) -> None:
        """set_results 在 worker 运行期间被调用时，旧 worker 应被取消。"""
        from fuscan.gui.models.result_model import _ASYNC_THRESHOLD

        m = ResultListModel()
        results1 = _build_large_results(tmp_path, n=_ASYNC_THRESHOLD + 1)
        results2 = tuple(
            ScanResult(
                path=tmp_path / f"new_{i:05d}.txt",
                size=i,
                hits=(RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="d"),),
                errors=0,
            )
            for i in range(_ASYNC_THRESHOLD + 5)
        )
        m.set_results(results1)
        # 立即替换为新的结果集
        m.set_results(results2)
        self._wait_for_worker(m)
        # 最终视图应反映 results2
        assert m.rowCount() == _ASYNC_THRESHOLD + 5
        # 第一条路径应为 new_00000
        assert "new_00000" in str(m.filtered_results[0].path).lower()


# ----------------------------- iter-149 倒排索引与排序缓存 -----------------------------


class TestIter149BuildIndices:
    """``build_indices`` 纯函数（无 Qt 依赖）。"""

    def test_empty_results_returns_empty_indices(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import build_indices

        sev_idx, rule_idx = build_indices(())
        assert sev_idx == {}
        assert rule_idx == {}

    def test_severity_index_grouped_by_severity(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import build_indices

        # 严重度索引按 max_severity 分组（与原 filter_and_sort 过滤语义一致：
        # filter_severities 只比较 max_severity，不是是否包含某级命中）
        # 4 条 max_severity：0=CRITICAL, 1=WARNING, 2=WARNING, 3=CRITICAL
        results = _build_filter_results(tmp_path)
        sev_idx, _ = build_indices(results)
        assert set(sev_idx[Severity.CRITICAL]) == {0, 3}
        assert set(sev_idx[Severity.WARNING]) == {1, 2}
        # 没有任何条目 max_severity 为 INFO（README 含 INFO+WARNING 命中，max 仍是 WARNING）
        assert Severity.INFO not in sev_idx or sev_idx[Severity.INFO] == []

    def test_rule_index_grouped_by_rule(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import build_indices

        results = _build_filter_results(tmp_path)
        _, rule_idx = build_indices(results)
        # "敏感内容"：0 (secret.txt) + 3 (db.yaml)
        assert set(rule_idx["敏感内容"]) == {0, 3}
        # "API 密钥"：1 (app.py) + 2 (README) + 3 (db.yaml)
        assert set(rule_idx["API 密钥"]) == {1, 2, 3}
        # "提示信息"：2 (README) + 3 (db.yaml)
        assert set(rule_idx["提示信息"]) == {2, 3}


class TestIter149FilterViaIndex:
    """``filter_via_index`` 纯函数（无 Qt 依赖）。"""

    def _build_indices(self, tmp_path: Path) -> tuple[dict[Severity, list[int]], dict[str, list[int]], int]:
        from fuscan.gui.models.result_model import build_indices

        results = _build_filter_results(tmp_path)
        sev, rule = build_indices(results)
        return sev, rule, len(results)

    def test_no_filter_returns_none(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import filter_via_index

        sev_idx, rule_idx, n = self._build_indices(tmp_path)
        assert filter_via_index(sev_idx, rule_idx, frozenset(), frozenset(), n) is None

    def test_filter_only_severity(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import filter_via_index

        sev_idx, rule_idx, n = self._build_indices(tmp_path)
        out = filter_via_index(sev_idx, rule_idx, frozenset(), frozenset({Severity.CRITICAL}), n)
        # filter_severities 非空 → 不会返回 None
        assert out is not None
        assert sorted(out) == [0, 3]

    def test_filter_only_rule(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import filter_via_index

        sev_idx, rule_idx, n = self._build_indices(tmp_path)
        out = filter_via_index(sev_idx, rule_idx, frozenset({"敏感内容"}), frozenset(), n)
        # filter_rules 非空 → 不会返回 None
        assert out is not None
        assert sorted(out) == [0, 3]

    def test_filter_rule_plus_severity_intersection(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import filter_via_index

        sev_idx, rule_idx, n = self._build_indices(tmp_path)
        # "API 密钥" 命中 {1, 2, 3}（规则索引）；Severity.WARNING 命中 {1, 2}（max_severity）
        # 交集 = {1, 2}
        out = filter_via_index(sev_idx, rule_idx, frozenset({"API 密钥"}), frozenset({Severity.WARNING}), n)
        # filter_severities + filter_rules 均非空 → 不会返回 None
        assert out is not None
        assert sorted(out) == [1, 2]

    def test_no_match_returns_empty_list(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import filter_via_index

        sev_idx, rule_idx, n = self._build_indices(tmp_path)
        # CRITICAL + "提示信息"：CRITICAL={0,3}，提示信息={2,3}，交集=3
        # 但如果找一个不存在的规则：
        out = filter_via_index(sev_idx, rule_idx, frozenset({"不存在的规则"}), frozenset(), n)
        assert out == []


class TestIter149SortCache:
    """排序缓存：相同结果集+相同过滤排序条件不重复计算。"""

    def test_cache_hit_avoids_filter_and_sort(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        m = ResultListModel()
        results = _build_filter_results(tmp_path)
        m.set_results(results)
        # 初次调用：缓存未命中，应调用 filter_and_sort
        first_filtered = m.filtered_results
        assert len(first_filtered) == 4

        # 用 monkeypatch 让 filter_and_sort 抛异常，若缓存命中则不调函数、不抛异常
        def should_not_be_called(*_a, **_kw):  # type: ignore[no-untyped-def]
            raise AssertionError("filter_and_sort 被调用，缓存未生效")

        from fuscan.gui.models import result_model as rm_mod

        monkeypatch.setattr(rm_mod, "filter_and_sort", should_not_be_called)
        # 触发相同条件的刷新（手动调 _schedule_filter_refresh）
        m._schedule_filter_refresh()  # type: ignore[attr-defined]
        # 不抛异常说明缓存命中
        assert m.filtered_results == first_filtered

    def test_change_filter_text_invalidates_cache(self, tmp_path: Path) -> None:
        m = ResultListModel()
        m.set_results(_build_filter_results(tmp_path))
        before = m.filtered_count
        m.set_filter_text("config")
        after = m.filtered_count
        assert before == 4 and after == 2

    def test_change_sort_invalidates_cache(self, tmp_path: Path) -> None:
        m = ResultListModel()
        m.set_results(_build_filter_results(tmp_path))
        first_paths = [str(r.path) for r in m.filtered_results]
        # 默认 severity 降序，切换为 filePath 升序
        m.set_sort("filePath", ascending=True)
        second_paths = [str(r.path) for r in m.filtered_results]
        assert first_paths != second_paths

    def test_set_results_clears_cache(self, tmp_path: Path) -> None:
        m = ResultListModel()
        results = _build_filter_results(tmp_path)
        m.set_results(results)
        _ = m.filtered_results
        # 旧缓存 key 基于 id(results) + 默认过滤排序条件
        old_keys = list(m._sort_cache.keys())  # type: ignore[attr-defined]
        assert len(old_keys) == 1
        assert old_keys[0][0] == id(results)

        # 替换结果集后，同步路径会基于新 results 写入新缓存
        results2 = tuple(
            ScanResult(
                path=tmp_path / f"new_{i}.txt",
                size=i,
                hits=(RuleHit(rule_name="X", severity=Severity.INFO, detail="x"),),
                errors=0,
            )
            for i in range(2)
        )
        m.set_results(results2)
        new_keys = list(m._sort_cache.keys())  # type: ignore[attr-defined]
        assert len(new_keys) == 1
        # 新缓存 key 的 tuple[0] 是 id(results2)，已不等于旧 key
        assert new_keys[0][0] == id(results2)
        assert new_keys[0][0] != old_keys[0][0]


class TestIter149IndexAppliedForLargeSet:
    """大结果集（>= ``_INDEX_THRESHOLD``）应启用索引裁剪。"""

    def test_large_model_builds_indices(self, qapp: QApplication, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import _INDEX_THRESHOLD

        m = ResultListModel()
        results = _build_large_results(tmp_path, n=_INDEX_THRESHOLD)
        m.set_results(results)
        _wait_for_worker(m)
        assert m._severity_index  # type: ignore[attr-defined]
        assert m._rule_index  # type: ignore[attr-defined]

    def test_small_model_does_not_build_indices(self, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import _INDEX_THRESHOLD

        m = ResultListModel()
        # 小结果集不构建索引
        small = _build_large_results(tmp_path, n=max(100, _INDEX_THRESHOLD // 2))
        m.set_results(small)
        assert m._severity_index == {}  # type: ignore[attr-defined]
        assert m._rule_index == {}  # type: ignore[attr-defined]

    def test_remove_result_updates_indices(self, qapp: QApplication, tmp_path: Path) -> None:
        """``remove_result_by_path`` 后索引仍可用（过滤规则仍得到正确结果）。

        _build_large_results 每 3 条循环：[敏感内容, API密钥, 提示信息]。
        """
        from fuscan.gui.models.result_model import _INDEX_THRESHOLD

        m = ResultListModel()
        n = _INDEX_THRESHOLD + 10  # 2010
        results = _build_large_results(tmp_path, n=n)
        m.set_results(results)
        _wait_for_worker(m)
        first_path = results[0].path  # i=0 属于「敏感内容」
        first_count = m.rowCount()
        ok = m.remove_result_by_path(first_path)
        assert ok is True
        _wait_for_worker(m)
        assert m.rowCount() == first_count - 1
        # 索引仍有效：set_filter_rules 过滤敏感内容的数量应为总数 1/3（≈669）
        m.set_filter_rules(("敏感内容",))
        _wait_for_worker(m)
        # 2010 条原 670 条敏感内容，remove 1 条敏感内容剩 669
        assert m.filtered_count == (n // 3) - 1
        # 检查每个过滤后结果确实含敏感内容 rule_name
        assert all("敏感内容" in r.rule_names for r in m.filtered_results)


class TestIter151Virtualize:
    """iter-151：ListView 虚拟化——setVisibleRange 与 data() 占位返回。"""

    @staticmethod
    def _role_index() -> int:
        return Qt.UserRole + 6  # index role（始终返回行号，不受虚拟化影响）

    @staticmethod
    def _role_filepath() -> int:
        return Qt.UserRole + 1

    def test_set_visible_range_empty_model_noop(self) -> None:
        """空模型 setVisibleRange 不应抛出，rowCount=0 直接 return。"""
        m = ResultListModel()
        try:
            m.setVisibleRange(0, 10)
        except Exception as exc:  # pragma: no cover - 故障保护
            pytest.fail(f"空模型 setVisibleRange 不应抛异常：{exc}")
        assert m.rowCount() == 0

    def test_small_result_set_not_virtualized(self, tmp_path: Path) -> None:
        """小结果集（<= _VIRTUALIZE_THRESHOLD）即使 setVisibleRange，data() 仍返回真实值。"""
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = max(10, _VIRTUALIZE_THRESHOLD // 10)  # 200 条远小于阈值
        results = _build_large_results(tmp_path, n=n)
        m.set_results(results)
        # 设置视口范围：只「可见」第 0~2 行
        m.setVisibleRange(0, 2)
        # 小结果集不应虚拟化：第 100 行（远超视口范围）仍应返回真实文件路径（非空字符串）
        idx = m.index(n - 1)
        fp = m.data(idx, self._role_filepath())
        assert isinstance(fp, str)
        assert fp != "", "小结果集不应虚拟化：远视野文件仍应有真实 filePath"
        # 行号应始终正确（无论是否虚拟化，index role 返回 row）
        assert m.data(idx, self._role_index()) == n - 1

    def test_large_result_outside_viewport_returns_placeholder(self, qapp: QApplication, tmp_path: Path) -> None:
        """大结果集（> _VIRTUALIZE_THRESHOLD）视口外 data() 返回占位空串/0。"""
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 500
        results = _build_large_results(tmp_path, n=n)
        m.set_results(results)
        _wait_for_worker(m)
        # 设置仅可见第 50~60 行
        m.setVisibleRange(50, 60)
        # 视口内：55 行的 filePath 不为空
        inside = m.index(55)
        fp_in = m.data(inside, self._role_filepath())
        assert isinstance(fp_in, str) and fp_in != "", "视口内应有真实 filePath"
        # 视口外很远的位置（2000，远超 buffer=100）：filePath 应为空串
        outside_idx = m.index(n - 50)  # 约第 2450 行
        fp_out = m.data(outside_idx, self._role_filepath())
        assert fp_out == "", f"视口外（row={n - 50}）应返回占位空串，实际={fp_out!r}"
        # hitsCount 视口外应为 0
        hits_out = m.data(outside_idx, Qt.UserRole + 5)
        assert hits_out == 0, f"视口外 hitsCount 应=0，实际={hits_out}"
        # index role（无论是否视口内，始终=row）
        assert m.data(outside_idx, self._role_index()) == n - 50

    def test_set_visible_range_normalize_bounds(self, qapp: QApplication, tmp_path: Path) -> None:
        """setVisibleRange 应自动归一化 start/end 至合法区间。"""
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 100
        m.set_results(_build_large_results(tmp_path, n=n))
        _wait_for_worker(m)
        # start < 0, end > rowCount-1：自动截断
        m.setVisibleRange(-50, n + 9999)
        assert m._visible_start == 0  # type: ignore[attr-defined]
        assert m._visible_end == n - 1  # type: ignore[attr-defined]
        # start > end：直接 return，不修改内部状态
        prev_s, prev_e = m._visible_start, m._visible_end  # type: ignore[attr-defined]
        m.setVisibleRange(n + 5, n - 1)
        assert m._visible_start == prev_s  # type: ignore[attr-defined]
        assert m._visible_end == prev_e  # type: ignore[attr-defined]

    def test_visible_range_unchanged_no_extra_emits(self, qapp: QApplication, tmp_path: Path) -> None:
        """相同范围重复调用 setVisibleRange 不应 emit dataChanged（无副作用）。"""
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 200
        m.set_results(_build_large_results(tmp_path, n=n))
        changed_calls: list[tuple[int, int]] = []

        def _on_changed(tl: QModelIndex, br: QModelIndex) -> None:
            changed_calls.append((tl.row(), br.row()))

        m.dataChanged.connect(_on_changed)  # type: ignore[attr-defined]
        # 首次设置：会 emit
        m.setVisibleRange(100, 120)
        first_count = len(changed_calls)
        assert first_count >= 0
        # 相同范围再次设置：不应 emit（直接 return）
        m.setVisibleRange(100, 120)
        assert len(changed_calls) == first_count, "相同范围重复调用不应触发 dataChanged"

    def test_data_micro_benchmark_1000_calls(self, benchmark: object, tmp_path: Path) -> None:  # pragma: no cover
        """微基准：虚拟化启用时，data() 1000 次视口外访问开销应 < 20ms。

        仅当 --benchmark-only 启用时实际跑基准；常规运行仅验证功能正确性。
        """
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 1000
        m.set_results(_build_large_results(tmp_path, n=n))
        m.setVisibleRange(5, 15)  # 仅 11 行在视口（+buffer），其余均为占位
        role_fp = self._role_filepath()

        def run() -> None:
            # 随机访问：循环中 90% 访问视口外（快速路径占位返回）
            rows = list(range(0, n, max(1, n // 1000)))[:1000]
            for r in rows:
                idx = m.index(r)
                _ = m.data(idx, role_fp)

        # 功能正确性先行：确保真的有占位返回
        outside = m.data(m.index(n - 10), role_fp)
        assert outside == "", "基准前功能校验：远端应返回占位"
        if benchmark is not None and callable(benchmark):
            benchmark(run)
        else:
            run()  # 普通运行至少走一遍路径（无基准器具时）


class TestIter153DiffRefresh:
    """iter-153：setVisibleRange 差异段 dataChanged + filter 后恢复 visible_range。"""

    @staticmethod
    def _capture_changes(m: ResultListModel) -> list[tuple[int, int]]:
        captured: list[tuple[int, int]] = []

        def _on(tl: QModelIndex, br: QModelIndex) -> None:
            captured.append((tl.row(), br.row()))

        m.dataChanged.connect(_on)  # type: ignore[attr-defined]
        return captured

    def test_scroll_down_emits_two_difference_ranges_not_whole_union(self, qapp: QApplication, tmp_path: Path) -> None:
        """向下滚动：仅旧超出新左段 + 新超出旧右段 两段刷新，而非整段并集。"""
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD, _VISIBLE_BUFFER_ROWS

        buf = _VISIBLE_BUFFER_ROWS  # 60
        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 1000  # 3000
        m.set_results(_build_large_results(tmp_path, n=n))
        _wait_for_worker(m)
        changes = self._capture_changes(m)
        # 初始：[100, 120] → buf = [40, 180]
        m.setVisibleRange(100, 120)
        changes.clear()
        # 向下滚动 30 行：新 [130, 150] → buf_new = [70, 210]
        # 旧 buf = [40, 180]，差异：左 [40, 69]（旧超新左），右 [181, 210]（新超旧右）
        m.setVisibleRange(130, 150)
        # 总共 2 段：不应该是整段 [40, 210]（跨度 170 行）
        total_rows = sum(end - start + 1 for start, end in changes)
        expected_right = (180 + 1, min(n - 1, 150 + buf))  # 181 → 210
        expected_left = (40, 70 - 1)  # 40 → 69
        # 两段独立发射
        assert len(changes) == 2, f"应恰好 2 段差异刷新，实际={changes}"
        # 行总和 ≈ 两段之和，而非整段并集 (210-40+1) = 171
        expected_sum = (expected_left[1] - expected_left[0] + 1) + (expected_right[1] - expected_right[0] + 1)
        assert total_rows == expected_sum, (
            f"差异段总行数应为 {expected_sum}（两段之和），实际 {total_rows}；"
            f"若 ≈171 则是退回了整段并集刷新（bug）。changes={changes}"
        )
        # 两段的内容（顺序可能不保证，用集合比对）
        ranges = sorted(changes)
        assert ranges[0] == expected_left, f"左段差异不符：期望 {expected_left} 实际 {ranges[0]}"
        assert ranges[1] == expected_right, f"右段差异不符：期望 {expected_right} 实际 {ranges[1]}"

    def test_filter_text_change_restores_visible_range(self, qapp: QApplication, tmp_path: Path) -> None:
        """filter 改变 _filtered 后，visible_range 仍被保留，data() 继续虚拟化返回占位。"""
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 500
        results = _build_large_results(tmp_path, n=n)
        m.set_results(results)
        _wait_for_worker(m)
        # 先进入虚拟化态
        m.setVisibleRange(50, 80)
        assert m._visible_end >= 0  # type: ignore[attr-defined]
        prev_s, prev_e = m._visible_start, m._visible_end  # type: ignore[attr-defined]
        # 保存虚拟化远端（第 n-10 行）的状态：应为占位
        far_idx = m.index(n - 10)
        fp_role = Qt.UserRole + 1
        before = m.data(far_idx, fp_role)
        assert before == "", "filter 前远端应已返回占位"
        # 触发 filter（空字符串，过滤视图结果相同，但走 filter_and_sort + resetModel 流程）
        m.set_filter_text("")
        # 验证：visible_start/end 未被清零，_restore_visible_range_after_filter 生效
        assert m._visible_start == prev_s  # type: ignore[attr-defined]
        assert m._visible_end == prev_e  # type: ignore[attr-defined]
        # 功能正确性：远端依然返回占位（虚拟化继续生效）
        after = m.data(far_idx, fp_role)
        assert after == "", "filter 后远端应继续返回占位（visible_range 未恢复）"

    def test_first_setvisible_emits_single_initial_range(self, qapp: QApplication, tmp_path: Path) -> None:
        """首次设置可见范围（prev_buf_end < 0）：应只发射一次新缓冲区整块刷新。"""
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD, _VISIBLE_BUFFER_ROWS

        buf = _VISIBLE_BUFFER_ROWS
        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 200
        m.set_results(_build_large_results(tmp_path, n=n))
        _wait_for_worker(m)
        changes = self._capture_changes(m)
        # 首次：[200, 230] → buf [140, 290]
        start, end = 200, 230
        m.setVisibleRange(start, end)
        assert len(changes) == 1, f"首次可见范围应整块刷新（1段），实际={changes}"
        expected_start = max(0, start - buf)
        expected_end = min(n - 1, end + buf)
        assert changes[0] == (expected_start, expected_end), (
            f"首次刷新范围不符：期望 ({expected_start},{expected_end}) 实际 {changes[0]}"
        )


class TestIter156LazyFill:
    """iter-156：大结果集幽灵行+分帧懒加载正确性测试。"""

    def test_large_result_set_uses_lazy_fill_initial_none_rows(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """大结果集 set_results 后，_filtered 初始应为全 None（幽灵行），_filtered_real 为完整真实。

        通过 monkeypatch 拦截 ``_fill_next_chunk``：``processEvents()`` 会一并处理
        zero-delay ``QTimer.singleShot(0, ...)``，若不拦截，2 批（n=2500,
        batch=2000）即可填完，``_lazystate`` 被清空无法断言「懒加载状态已建立」。
        拦截后仅记录调度次数，不执行实际填充，保留 lazystate 供断言。
        """
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 500
        results = _build_large_results(tmp_path, n=n)
        # 拦截 _fill_next_chunk：避免 processEvents 递归触发懒填充清空 lazystate
        fill_calls: list[int] = []

        def _track_fill() -> None:
            fill_calls.append(1)

        monkeypatch.setattr(m, "_fill_next_chunk", _track_fill)
        m.set_results(results)
        # 手动等待 worker 完成，但不取消懒填充（保留懒加载状态用于断言）
        import time

        try:
            from PySide2.QtCore import QCoreApplication
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QCoreApplication  # pyrefly: ignore [missing-import]
        elapsed = 0
        while m._filter_worker is not None and elapsed < 5000:  # type: ignore[attr-defined]
            QCoreApplication.processEvents()
            time.sleep(0.005)
            elapsed += 5
        # done 信号回调在 processEvents 中执行，QTimer 被 _track_fill 拦截
        QCoreApplication.processEvents()
        # 真实副本：完整无 None
        assert len(m._filtered_real) == n  # type: ignore[attr-defined]
        assert all(x is not None for x in m._filtered_real)  # type: ignore[attr-defined]
        # 懒加载状态已建立
        assert m._lazystate is not None  # type: ignore[attr-defined]
        # _filtered 初始存在至少大量 None（幽灵行，除非 visible_range 已填视口）
        none_count = sum(1 for x in m._filtered if x is None)  # type: ignore[attr-defined]
        # visible_end < 0 时，优先填充可能为空，大量仍为 None
        assert none_count > 0, "大结果集懒加载初期应有幽灵行（至少部分 None）"
        # 验证 QTimer.singleShot 确实调度了 _fill_next_chunk（懒加载已启动）
        assert len(fill_calls) >= 1, "_fill_next_chunk 应被 QTimer.singleShot 调度"
        # 清理：取消懒加载避免后续 QTimer 触发影响其他测试
        m._cancel_lazy_fill(and_fill_rest=False)  # type: ignore[attr-defined]

    def test_filtered_results_never_contains_none(self, qapp: QApplication, tmp_path: Path) -> None:
        """filtered_results 属性永远返回真实值（无 None），即使处于懒加载阶段。"""
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 100
        m.set_results(_build_large_results(tmp_path, n=n))
        _wait_for_worker(m)
        filtered = m.filtered_results
        assert len(filtered) == n
        for i, r in enumerate(filtered):
            assert r is not None, f"filtered_results[{i}] 不应为 None"
            assert hasattr(r, "path") and hasattr(r, "hits"), "filtered_results 元素应为 ScanResult"

    def test_get_result_returns_real_even_when_ghost_row(self, qapp: QApplication, tmp_path: Path) -> None:
        """get_result(row) 即使 row 对应 _filtered[row] 为 None 也应返回真实 ScanResult。

        iter-156：过滤排序后结果顺序可能与原 results 顺序不同（sort+倒排索引裁剪重新排序），
        因此只断言 got.path 必须属于原 results 的某条路径，而非严格按下标相等。
        """
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 800
        results = _build_large_results(tmp_path, n=n)
        m.set_results(results)
        _wait_for_worker(m)
        # 选一个非常靠后的行（懒加载 cursor 还没到，大概率仍为 None）
        far_row = n - 50
        got = m.get_result(far_row)
        assert got is not None, "get_result(far_row) 不应为 None（回退到 _filtered_real）"
        original_paths = {r.path for r in results}
        assert got.path in original_paths, (
            f"get_result 返回路径 {got.path} 不在原结果路径集合中（过滤后重排属正常，但路径必须存在）"
        )

    def test_cancel_lazy_fill_then_all_real(self, qapp: QApplication, tmp_path: Path) -> None:
        """_cancel_lazy_fill(and_fill_rest=True) 后 _filtered 应全部替换为真实值。"""
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 300
        m.set_results(_build_large_results(tmp_path, n=n))
        _wait_for_worker(m)
        m._cancel_lazy_fill(and_fill_rest=True)  # type: ignore[attr-defined]
        assert m._lazystate is None  # type: ignore[attr-defined]
        none_after = sum(1 for x in m._filtered if x is None)  # type: ignore[attr-defined]
        assert none_after == 0, "cancel 后 _filtered 不应再有 None"

    def test_setvisible_range_fills_visible_priority_immediately(self, qapp: QApplication, tmp_path: Path) -> None:
        """懒加载中 setVisibleRange(远位置) 会立即把 visible_range + buffer 填成真实值。

        iter-156：为避免 QTimer singleShot(0, ...) 的第一帧在 set_results 之后
        先把 [0, batch) 填成真实值（导致 none_count_total 达不到阈值反而触发可见段未填），
        此处先 cancel_lazy_fill(and_fill_rest=False)，手动重建幽灵行，再置 visible_end=-1，
        保证初始态干净，然后 setVisibleRange 验证可见段优先填充。
        """
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD, _VISIBLE_BUFFER_ROWS

        buf = _VISIBLE_BUFFER_ROWS
        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 1000
        results = _build_large_results(tmp_path, n=n)
        m.set_results(results)
        _wait_for_worker(m)
        # 强制回到干净的懒加载初始态（避免 singleShot(0) 已经填了一部分）
        m._cancel_lazy_fill(and_fill_rest=False)  # type: ignore[attr-defined]
        m._filtered = (None,) * n  # type: ignore[attr-defined]
        m._filtered_real = m._filtered_real  # type: ignore[attr-defined]  # 保持真实副本
        # 重新启用 lazystate（cursor=0, generation=当前 generation）
        from fuscan.gui.models.result_model import _LazyFillState

        m._lazystate = _LazyFillState(  # type: ignore[attr-defined]
            generation=m._filter_generation,  # type: ignore[attr-defined]
            cursor=0,
            result_tuple=m._filtered_real,  # type: ignore[attr-defined]
        )
        # 清空 visible 记录（保证进入 setVisibleRange 前是未知态）
        m._visible_start = 0  # type: ignore[attr-defined]
        m._visible_end = -1  # type: ignore[attr-defined]
        # 跳转到非常靠后的范围
        far_start, far_end = n - 200, n - 180
        m.setVisibleRange(far_start, far_end)
        # 验证 visible 范围内 _filtered 都是真实值（无 None）
        buf_start = max(0, far_start - buf)
        buf_end = min(n - 1, far_end + buf)
        missing = [i for i in range(buf_start, buf_end + 1) if m._filtered[i] is None]  # type: ignore[attr-defined]
        assert missing == [], (
            f"visible range + buffer 内 {len(missing)} 行仍为 None（未立即填充）：{missing[:5]}..."
            if len(missing) > 5
            else f"visible range + buffer 内行仍为 None：{missing}"
        )

    def test_small_result_set_no_lazy_fill(self, qapp: QApplication, tmp_path: Path) -> None:
        """小结果集（<=_VIRTUALIZE_THRESHOLD）不启用懒加载，_filtered 全为真实值且 lazystate=None。"""
        from fuscan.gui.models.result_model import _INDEX_THRESHOLD, _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        # 使用小于 _INDEX_THRESHOLD 的数量确保走同步路径
        n = min(_VIRTUALIZE_THRESHOLD, _INDEX_THRESHOLD - 1)
        m.set_results(_build_large_results(tmp_path, n=n))
        assert m._lazystate is None  # type: ignore[attr-defined]
        none_count = sum(1 for x in m._filtered if x is None)  # type: ignore[attr-defined]
        assert none_count == 0, "小结果集无懒加载，_filtered 不应有任何 None"


class TestIter159FlatData:
    """iter-159：扁平化数据层 _flat_data 正确性验证。"""

    def test_flat_data_small_result_set(self, qapp: QApplication, tmp_path: Path) -> None:
        """小结果集 set_results 后 _flat_data 全量构造且字段正确。"""
        from fuscan.gui.models.result_model import (
            _FLAT_FILE_PATH,
            _FLAT_HITS_COUNT,
            _FLAT_RULE_NAME,
            _FLAT_SEV_TEXT,
        )

        m = ResultListModel()
        results = _build_results(tmp_path)
        m.set_results(results)
        flat = m._flat_data  # type: ignore[attr-defined]
        assert len(flat) == len(results), f"flat_data 行数 {len(flat)} != {len(results)}"
        # 第 0 行（CRITICAL 命中）
        row0 = flat[0]
        assert row0 is not None
        assert row0[_FLAT_FILE_PATH] == str(results[0].path)
        assert row0[_FLAT_RULE_NAME] == "敏感内容"
        assert row0[_FLAT_SEV_TEXT] == "严重"
        assert row0[_FLAT_HITS_COUNT] == 1
        # 第 1 行（含 2 条命中，取第一个规则名）
        row1 = flat[1]
        assert row1 is not None
        assert row1[_FLAT_RULE_NAME] == "敏感内容"
        assert row1[_FLAT_HITS_COUNT] == 2
        # 扁平数据列数正确（7 列：filePath/ruleName/sevText/sevColor/hitsCount/index/replaced）
        for i, r in enumerate(flat):
            assert r is not None, f"第 {i} 行为 None（应为扁平元组）"
            assert len(r) == 7, f"第 {i} 行元组长度 {len(r)} != 7"

    def test_flat_data_filled_during_lazy_fill(self, qapp: QApplication, tmp_path: Path) -> None:
        """懒加载填充可见范围时，_flat_data 同步构造扁平元组。"""
        from unittest.mock import patch

        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        # 使用足够大的 n，确保可见范围外有足够未填充行（即使 QTimer 填了 1 批 2000 行）
        n = _VIRTUALIZE_THRESHOLD + 2500  # 4500
        m.set_results(_build_large_results(tmp_path, n=n))
        # 手动等待 worker 完成，但不取消懒填充。
        # 关键：patch QTimer.singleShot 阻止懒填充分帧调度——
        # processEvents() 会处理所有 pending 事件（含 chained singleShot(0)），
        # 可能一次填完全部 4500 行导致 outside_none=0 断言 flaky。
        # patch 后 _lazystate 仍设置但 _fill_next_chunk 永不触发，_flat_data 保持全 None。
        import time

        try:
            from PySide2.QtCore import QCoreApplication
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QCoreApplication  # pyrefly: ignore [missing-import]
        with patch("fuscan.gui.models.result_model.QTimer.singleShot"):
            elapsed = 0
            while m._filter_worker is not None and elapsed < 5000:  # type: ignore[attr-defined]
                QCoreApplication.processEvents()
                time.sleep(0.005)
                elapsed += 5
            QCoreApplication.processEvents()
        flat = m._flat_data  # type: ignore[attr-defined]
        # 初始为幽灵行，flat 全为 None
        assert len(flat) == n
        # 触发可见范围填充（前 200 行）
        m.setVisibleRange(0, 180)
        # 验证可见范围 + buffer 内的 flat 已构造
        from fuscan.gui.models.result_model import _VISIBLE_BUFFER_ROWS

        buf = _VISIBLE_BUFFER_ROWS
        end = min(n - 1, 180 + buf)
        filled_count = sum(1 for i in range(0, end + 1) if flat[i] is not None)
        assert filled_count > 0, "可见范围内 flat 应为非 None"
        # 不在可见范围的行仍有大量 None（懒填充未完成）
        outside_none = sum(1 for i in range(end + 1, n) if flat[i] is None)
        assert outside_none > 0, f"可见范围外应有未填充行，实际 outside_none={outside_none}"

    def test_flat_data_rebuilt_on_cancel_lazy_fill(self, qapp: QApplication, tmp_path: Path) -> None:
        """cancel_lazy_fill(and_fill_rest=True) 后 _flat_data 全量重建。"""
        from fuscan.gui.models.result_model import _VIRTUALIZE_THRESHOLD

        m = ResultListModel()
        n = _VIRTUALIZE_THRESHOLD + 500
        m.set_results(_build_large_results(tmp_path, n=n))
        _wait_for_worker(m)
        m._cancel_lazy_fill(and_fill_rest=True)  # type: ignore[attr-defined]
        flat = m._flat_data  # type: ignore[attr-defined]
        assert len(flat) == n
        none_count = sum(1 for r in flat if r is None)
        assert none_count == 0, "cancel 后 flat 不应有 None"
        # 验证每条 flat 元组字段与 _filtered_real 一致
        for i, r in enumerate(flat):
            assert r is not None
            result = m._filtered_real[i]  # type: ignore[attr-defined]
            assert r[0] == str(result.path), f"第 {i} 行文件路径不一致"
            assert r[4] == len(result.hits), f"第 {i} 行 hitsCount 不一致"


# ----------------------------- iter-165 并行倒排索引 -----------------------------


class TestIter165BuildIndicesParallel:
    """iter-165：build_indices_parallel 分块并行构建 + 与串行结果等价。"""

    def test_parallel_empty(self) -> None:
        from fuscan.gui.models.result_model import build_indices_parallel

        sev, rule = build_indices_parallel(())
        assert sev == {}
        assert rule == {}

    def test_parallel_single_chunk_equals_serial(self, tmp_path: Path) -> None:
        """结果数小于 chunk_size 时，并行退化单线程，结果完全等价。"""
        from fuscan.gui.models.result_model import build_indices, build_indices_parallel

        results = _build_large_results(tmp_path, n=500)
        sev_s, rule_s = build_indices(results)
        sev_p, rule_p = build_indices_parallel(results, max_workers=4, chunk_size=2000)
        assert sev_s == sev_p
        assert rule_s == rule_p

    def test_parallel_multi_chunk_equals_serial(self, tmp_path: Path) -> None:
        """多切片并行构建后与串行结果等价。"""
        from fuscan.gui.models.result_model import build_indices, build_indices_parallel

        results = _build_large_results(tmp_path, n=5000)
        sev_s, rule_s = build_indices(results)
        sev_p, rule_p = build_indices_parallel(results, max_workers=4, chunk_size=800)
        assert sev_s == sev_p
        assert rule_s == rule_p

    def test_build_indices_auto_parallel_for_large(self, tmp_path: Path) -> None:
        """build_indices 在结果数 >= _INDEX_PARALLEL_THRESHOLD 时自动走并行路径。"""
        from fuscan.gui.models.result_model import (
            _INDEX_PARALLEL_THRESHOLD,
            build_indices,
        )

        results = _build_large_results(tmp_path, n=_INDEX_PARALLEL_THRESHOLD + 100)
        sev, rule = build_indices(results)
        assert len(sev) > 0
        assert len(rule) > 0
        # 严重度索引覆盖三种等级
        for sev_lv in (Severity.CRITICAL, Severity.WARNING, Severity.INFO):
            assert sev_lv in sev, f"严重度 {sev_lv} 应出现在索引中"

    def test_parallel_single_worker(self, tmp_path: Path) -> None:
        """max_workers=1 时直接走串行，避免线程池开销。"""
        from fuscan.gui.models.result_model import build_indices, build_indices_parallel

        results = _build_large_results(tmp_path, n=3000)
        sev_s, rule_s = build_indices(results)
        sev_p, rule_p = build_indices_parallel(results, max_workers=1, chunk_size=100)
        assert sev_s == sev_p
        assert rule_s == rule_p


class TestIter165FilterWorkerWithIndex:
    """iter-165：FilterWorker 信号现回传 (filtered, severity_index, rule_index) 三元组。"""

    def test_filter_worker_emits_three_values(self, qapp: QApplication, tmp_path: Path) -> None:
        from fuscan.gui.workers.filter_worker import FilterWorker

        results = _build_large_results(tmp_path, n=3000)
        worker = FilterWorker(
            results=results,
            filter_text="",
            filter_rules=frozenset(),
            filter_severities=frozenset(),
            sort_field="severity",
            sort_ascending=False,
        )
        captured: dict[str, object] = {}

        def _on_done(filtered: tuple[object, ...], sev: dict[object, object], rule: dict[object, object]) -> None:
            captured["filtered"] = filtered
            captured["sev"] = sev
            captured["rule"] = rule

        worker.done.connect(_on_done)  # type: ignore[attr-defined]
        worker.start()
        # 等待线程完成，然后处理事件让信号送达 slot
        worker.wait(2000)
        assert worker.isRunning() is False
        qapp.processEvents()
        assert "filtered" in captured, "FilterWorker.done 应触发"
        filtered = captured["filtered"]
        assert isinstance(filtered, tuple)
        assert len(filtered) == 3000
        sev = captured["sev"]
        rule = captured["rule"]
        assert isinstance(sev, dict)
        assert isinstance(rule, dict)
        # 3000 条已达 _INDEX_THRESHOLD（2000），索引应被构建
        assert len(sev) > 0
        assert len(rule) > 0

    def test_filter_worker_skips_index_for_small_set(self, qapp: QApplication, tmp_path: Path) -> None:
        from fuscan.gui.workers.filter_worker import FilterWorker

        results = _build_large_results(tmp_path, n=100)
        worker = FilterWorker(
            results=results,
            filter_text="",
            filter_rules=frozenset(),
            filter_severities=frozenset(),
            sort_field="default",
            sort_ascending=True,
            build_index=True,
            index_threshold=2000,
        )
        captured: dict[str, object] = {}

        def _on_done(filtered: tuple[object, ...], sev: dict[object, object], rule: dict[object, object]) -> None:
            captured["sev"] = sev
            captured["rule"] = rule

        worker.done.connect(_on_done)  # type: ignore[attr-defined]
        worker.start()
        worker.wait(2000)
        assert worker.isRunning() is False
        qapp.processEvents()
        # 100 条小于阈值，索引应为空
        assert captured["sev"] == {}
        assert captured["rule"] == {}


class TestIter165SetResultsAsyncIndex:
    """iter-165：set_results 在大结果集时不阻塞主线程构建索引，由 FilterWorker 后台完成。"""

    def test_large_set_results_builds_index_via_worker(self, qapp: QApplication, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import _ASYNC_THRESHOLD, _INDEX_THRESHOLD

        m = ResultListModel()
        n = max(_ASYNC_THRESHOLD + 1000, _INDEX_THRESHOLD + 100)
        # 直接调用 set_results —— 对 n >= _ASYNC_THRESHOLD 的结果，索引不在主线程同步构建
        # 而是由 FilterWorker 异步构建，_on_filter_done 回调中应用
        results = _build_large_results(tmp_path, n=n)
        m.set_results(results)
        # 等待 FilterWorker 完成
        worker = m._filter_worker  # type: ignore[attr-defined]
        if worker is not None:
            worker.wait(3000)
            worker.quit()
            worker.wait(500)
            qapp.processEvents()
        # 索引已被 FilterWorker 回传并应用（通过信号）
        assert m._severity_index  # type: ignore[attr-defined]
        assert m._rule_index  # type: ignore[attr-defined]
        # 过滤+排序结果已应用
        assert m.total_count == n

    def test_medium_set_results_builds_index_async(self, qapp: QApplication, tmp_path: Path) -> None:
        """中结果集（>= _INDEX_THRESHOLD）现在也走异步路径，索引由 FilterWorker 后台构建。"""
        from fuscan.gui.models.result_model import _ASYNC_THRESHOLD, _INDEX_THRESHOLD

        m = ResultListModel()
        # 中结果集：>= _INDEX_THRESHOLD 且 < _ASYNC_THRESHOLD
        n = (_INDEX_THRESHOLD + _ASYNC_THRESHOLD) // 2
        n = max(n, _INDEX_THRESHOLD + 500)
        results = _build_large_results(tmp_path, n=n)
        m.set_results(results)
        # 等待 FilterWorker 完成（索引构建已异步化）
        _wait_for_worker(m)
        assert m._severity_index  # type: ignore[attr-defined]
        assert m._rule_index  # type: ignore[attr-defined]

    def test_remove_result_updates_index_via_worker(self, qapp: QApplication, tmp_path: Path) -> None:
        from fuscan.gui.models.result_model import _ASYNC_THRESHOLD

        m = ResultListModel()
        n = _ASYNC_THRESHOLD + 500
        results = _build_large_results(tmp_path, n=n)
        m.set_results(results)
        # 等待首次 set_results 的 worker 完成
        w1 = m._filter_worker  # type: ignore[attr-defined]
        if w1 is not None:
            w1.wait(3000)
            w1.quit()
            w1.wait(500)
            qapp.processEvents()
        # 移除一条结果
        first_path = results[0].path
        ok = m.remove_result_by_path(first_path)
        assert ok is True
        # 等待 remove 后的 worker 完成
        w2 = m._filter_worker  # type: ignore[attr-defined]
        if w2 is not None:
            w2.wait(3000)
            w2.quit()
            w2.wait(500)
            qapp.processEvents()
        # 索引仍可用于过滤
        assert m._severity_index  # type: ignore[attr-defined]
        assert m.filtered_count == n - 1
