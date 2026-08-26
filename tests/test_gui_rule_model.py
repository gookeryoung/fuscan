"""``RuleListModel`` 单元测试。

覆盖 ``set_ruleset``/``clear``/``data()``/``roleNames()``/``rowCount()`` 接口。
"""

from __future__ import annotations

import os

import pytest
from PySide2.QtCore import QModelIndex, Qt

from fuscan.gui.models.rule_model import RuleListModel
from fuscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui


def _build_ruleset() -> RuleSet:
    return RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="敏感内容",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
                description="检测密码关键词",
            ),
            Rule(
                name="API 密钥",
                severity=Severity.WARNING,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="api_key"),
            ),
        ),
    )


@pytest.fixture()
def model() -> RuleListModel:
    m = RuleListModel()
    m.set_ruleset(_build_ruleset())
    return m


class TestRowCount:
    def test_empty_model_has_zero_rows(self) -> None:
        m = RuleListModel()
        assert m.rowCount() == 0

    def test_set_ruleset_populates_rows(self, model: RuleListModel) -> None:
        assert model.rowCount() == 2

    def test_rowcount_with_parent_index_returns_zero(self, model: RuleListModel) -> None:
        parent = model.index(0)
        assert model.rowCount(parent) == 0

    def test_set_none_ruleset_clears_rows(self, model: RuleListModel) -> None:
        model.set_ruleset(None)
        assert model.rowCount() == 0


class TestRoleNames:
    def test_role_names(self, model: RuleListModel) -> None:
        roles = model.roleNames()
        assert roles[Qt.UserRole + 1] == b"name"
        assert roles[Qt.UserRole + 2] == b"severityText"
        assert roles[Qt.UserRole + 3] == b"severityColor"
        assert roles[Qt.UserRole + 4] == b"description"


class TestData:
    def test_data_invalid_index_returns_empty(self, model: RuleListModel) -> None:
        invalid = QModelIndex()
        assert model.data(invalid, Qt.UserRole + 1) == ""

    def test_data_returns_name(self, model: RuleListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.UserRole + 1) == "敏感内容"

    def test_data_returns_severity_text(self, model: RuleListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.UserRole + 2) == "严重"

    def test_data_returns_severity_color(self, model: RuleListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.UserRole + 3) == "#D73A49"

    def test_data_returns_description(self, model: RuleListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.UserRole + 4) == "检测密码关键词"

    def test_data_unknown_role_returns_empty(self, model: RuleListModel) -> None:
        idx = model.index(0)
        assert model.data(idx, Qt.DisplayRole) == ""

    def test_data_warning_severity(self, model: RuleListModel) -> None:
        idx = model.index(1)
        assert model.data(idx, Qt.UserRole + 1) == "API 密钥"
        assert model.data(idx, Qt.UserRole + 2) == "警告"
        assert model.data(idx, Qt.UserRole + 3) == "#F0883E"


class TestClear:
    def test_clear_empties_rows(self, model: RuleListModel) -> None:
        model.clear()
        assert model.rowCount() == 0
        assert model.rules == ()


class TestRulesProperty:
    def test_rules_returns_tuple(self, model: RuleListModel) -> None:
        rules = model.rules
        assert isinstance(rules, tuple)
        assert len(rules) == 2

    def test_empty_model_rules_tuple(self) -> None:
        m = RuleListModel()
        assert m.rules == ()
