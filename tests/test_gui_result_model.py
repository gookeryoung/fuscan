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

    from fuscan.gui.qml.models.result_model import ResultListModel
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
