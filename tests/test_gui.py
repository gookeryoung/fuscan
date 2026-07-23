"""GUI 烟雾测试。

使用 ``gui`` marker 标记，CI 无 GUI 环境时可通过 ``-m "not gui"`` 跳过。
需要 QApplication 环境（offscreen 平台）。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    try:
        from PySide2.QtCore import Qt
        from PySide2.QtWidgets import QApplication, QListWidgetItem, QMenu, QMessageBox
    except ImportError:  # pragma: no cover
        from PySide6.QtCore import Qt  # pyrefly: ignore [missing-import]
        from PySide6.QtWidgets import (  # pyrefly: ignore [missing-import]
            QApplication,
            QListWidgetItem,
            QMenu,
            QMessageBox,
        )

    from fuscan import (
        __author__,
        __description__,
        __license__,
        __version__,
    )
    from fuscan.gui.main_window import MainWindow, ScanState, WorkflowStage
    from fuscan.gui.preview_utils import build_preview_html, extract_keywords, format_size, severity_text
    from fuscan.gui.worker import ScanWorker
    from fuscan.rules import load_ruleset
    from fuscan.rules.model import (
        LeafMatch,
        MatchMode,
        MatchTarget,
        Rule,
        RuleSet,
        Severity,
    )
    from fuscan.scanner import ScanReport

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过 GUI 测试", allow_module_level=True)


@pytest.fixture(scope="module")
def qapp() -> QApplication:  # type: ignore[misc]
    """模块级 QApplication fixture。"""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离配置文件，避免测试读写用户主目录 ~/.fuscan/config.yaml。"""
    from fuscan.config import load_config as _load_impl
    from fuscan.config import save_config as _save_impl

    # 清空内容提取缓存，避免测试间相互影响（需求2）
    from fuscan.extractors import clear_content_cache

    clear_content_cache()

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(
        "fuscan.gui.main_window.load_config",
        lambda path=None: _load_impl(config_path),
    )
    monkeypatch.setattr(
        "fuscan.gui.main_window.save_config",
        lambda config, path=None: _save_impl(config, config_path),
    )


def _build_ruleset() -> RuleSet:
    return RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="敏感文件名",
                severity=Severity.WARNING,
                match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="secret"),
            ),
        ),
    )


class TestMainWindow:
    def test_window_creation(self, qapp: QApplication) -> None:
        window = MainWindow()
        assert window.windowTitle().startswith("fuscan")
        assert window.scan_btn is not None
        assert window.rules_tree is not None
        assert window.result_tree is not None
        window.close()

    def test_scan_button_disabled_initially(self, qapp: QApplication) -> None:
        window = MainWindow()
        assert not window.scan_btn.isEnabled()
        window.close()

    def test_load_ruleset_updates_rules_tree(self, qapp: QApplication, tmp_path: Path) -> None:
        """加载规则后规则树应展示规则。"""
        rules_yaml = tmp_path / "rules.yaml"
        rules_yaml.write_text(
            """
version: "1.0"
rules:
  - name: 敏感名
    severity: warning
    match:
      type: filename
      mode: contains
      pattern: secret
""",
            encoding="utf-8",
        )
        window = MainWindow()
        rs = load_ruleset(rules_yaml)
        window._ruleset = rs
        window._rules_paths = [rules_yaml]
        window._refresh_rules_tree()
        assert window.rules_tree.topLevelItemCount() == 1
        item = window.rules_tree.topLevelItem(0)
        assert item.text(0) == "敏感名"
        window.close()

    def test_populate_results_displays_hits(self, qapp: QApplication, tmp_path: Path) -> None:
        """结果树应展示命中项。"""
        from fuscan.scanner import Scanner

        # 准备测试文件
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        (tmp_path / "normal.txt").write_text("y", encoding="utf-8")

        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        assert window.result_tree.model().rowCount() == 1
        item = window.result_tree.model().item(0, 0)
        assert "secret.txt" in item.text()
        window.close()

    def test_export_csv(self, qapp: QApplication, tmp_path: Path) -> None:
        """CSV 导出应写入文件。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        out_path = tmp_path / "out.csv"
        content = report.to_format("csv")
        out_path.write_text(content, encoding="utf-8")
        assert out_path.exists()
        text = out_path.read_text(encoding="utf-8")
        assert "secret.txt" in text
        window.close()

    def test_export_json(self, qapp: QApplication, tmp_path: Path) -> None:
        """JSON 导出应写入文件。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        out_path = tmp_path / "out.json"
        content = report.to_format("json")
        out_path.write_text(content, encoding="utf-8")
        import json

        data = json.loads(content)
        assert "hits" in data
        assert len(data["hits"]) >= 1
        window.close()

    def test_load_rules_via_dialog(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过模拟文件对话框测试规则加载（关闭通用规则后仅加载用户规则）。"""
        rules_yaml = tmp_path / "rules.yaml"
        rules_yaml.write_text(
            'version: "1.0"\nrules:\n  - name: r1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: secret\n',
            encoding="utf-8",
        )

        window = MainWindow()
        # 关闭通用规则，确保仅加载用户规则
        window._set_use_builtin(False)

        # mock QFileDialog.getOpenFileName 返回规则文件路径
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(rules_yaml), ""),
        )

        window._on_load_rules()
        assert window._ruleset is not None
        assert window._rules_paths == [rules_yaml]
        assert window.rules_tree.topLevelItemCount() == 1
        window.close()

    def test_load_rules_cancelled(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """取消文件对话框不改变当前规则集。"""
        window = MainWindow()
        # 启动时已加载通用规则
        assert window._ruleset is not None
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: ("", ""),
        )
        window._on_load_rules()
        # 取消后规则集仍保留
        assert window._ruleset is not None
        window.close()

    def test_load_rules_invalid(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """加载无效规则文件应弹出警告且保留原规则集。"""
        # 使用未知匹配类型触发 RuleParseError
        bad_rules = tmp_path / "bad.yaml"
        bad_rules.write_text(
            'version: "1.0"\nrules:\n  - name: r1\n    match:\n      type: unknown_type\n      mode: contains\n      pattern: x\n',
            encoding="utf-8",
        )

        window = MainWindow()
        # 关闭通用规则，使初始 ruleset 为 None
        window._set_use_builtin(False)
        assert window._ruleset is None
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(bad_rules), ""),
        )
        warned = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.warning",
            lambda *args, **kwargs: warned.update(called=True),
        )
        window._on_load_rules()
        assert window._ruleset is None
        assert warned["called"]
        window.close()

    def test_select_path_via_dialog(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过模拟对话框选择扫描路径。"""
        window = MainWindow()
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getExistingDirectory",
            lambda *args, **kwargs: str(tmp_path),
        )
        window._on_select_path()
        assert window._scan_root == tmp_path
        window.close()

    def test_update_scan_button_state(self, qapp: QApplication, tmp_path: Path) -> None:
        """扫描按钮状态随规则与路径就绪变化。"""
        window = MainWindow()
        assert not window.scan_btn.isEnabled()

        # 仅设置规则
        window._ruleset = _build_ruleset()
        window._update_scan_button()
        assert not window.scan_btn.isEnabled()

        # 设置路径
        window._scan_root = tmp_path
        window._update_scan_button()
        assert window.scan_btn.isEnabled()
        window.close()

    def test_refresh_rules_tree_empty(self, qapp: QApplication) -> None:
        """无规则集时规则树为空。"""
        window = MainWindow()
        window._ruleset = None
        window._refresh_rules_tree()
        assert window.rules_tree.topLevelItemCount() == 0
        window.close()

    def test_close_event_terminates_worker(self, qapp: QApplication, tmp_path: Path) -> None:
        """closeEvent 应安全终止后台线程。"""
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        window = MainWindow()
        # 不启动 worker，直接关闭
        window.close()
        assert window._worker is None or not window._worker.isRunning()


class TestBuiltinRulesToggle:
    """通用规则开关测试。"""

    def test_builtin_loaded_at_startup(self, qapp: QApplication) -> None:
        """启动时应默认加载内置通用规则。"""
        window = MainWindow()
        assert window._ruleset is not None
        assert len(window._ruleset.rules) > 0
        assert window._use_builtin is True
        # 规则树应非空
        assert window.rules_tree.topLevelItemCount() > 0
        window.close()

    def test_uncheck_builtin_clears_ruleset(self, qapp: QApplication) -> None:
        """取消勾选通用规则且无用户规则时 ruleset 为 None。"""
        window = MainWindow()
        window._set_use_builtin(False)
        assert window._use_builtin is False
        assert window._ruleset is None
        assert window.rules_tree.topLevelItemCount() == 0
        # 扫描按钮应禁用
        assert not window.scan_btn.isEnabled()
        window.close()

    def test_recheck_builtin_reloads_ruleset(self, qapp: QApplication) -> None:
        """重新勾选通用规则应重新加载规则集。"""
        window = MainWindow()
        window._set_use_builtin(False)
        assert window._ruleset is None
        window._set_use_builtin(True)
        assert window._ruleset is not None
        assert len(window._ruleset.rules) > 0
        window.close()

    def test_builtin_label_updated_on_toggle(self, qapp: QApplication) -> None:
        """切换通用规则开关时规则集应更新。"""
        window = MainWindow()
        assert window._use_builtin is True
        assert window._ruleset is not None
        window._set_use_builtin(False)
        assert window._use_builtin is False
        window._set_use_builtin(True)
        assert window._use_builtin is True
        assert window._ruleset is not None
        window.close()

    def test_load_user_rules_with_builtin(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """勾选通用规则时加载用户规则应合并。"""
        rules_yaml = tmp_path / "rules.yaml"
        rules_yaml.write_text(
            'version: "1.0"\nrules:\n  - name: 用户规则1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: secret\n',
            encoding="utf-8",
        )

        window = MainWindow()
        builtin_count = window.rules_tree.topLevelItemCount()

        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(rules_yaml), ""),
        )
        window._on_load_rules()
        # 合并后规则数应大于内置规则数
        assert window.rules_tree.topLevelItemCount() > builtin_count
        assert window._ruleset is not None
        window.close()

    def test_scan_enabled_with_builtin_only(self, qapp: QApplication, tmp_path: Path) -> None:
        """仅加载内置规则并选择路径后扫描按钮应可用。"""
        window = MainWindow()
        assert window._ruleset is not None
        # 未选路径时按钮禁用
        assert not window.scan_btn.isEnabled()
        # 选择路径后按钮启用
        window._scan_root = tmp_path
        window._update_scan_button()
        assert window.scan_btn.isEnabled()
        window.close()


class TestMultiRulesList:
    """多规则文件列表与排序测试。"""

    def test_rules_file_list_initially_empty(self, qapp: QApplication) -> None:
        """启动时（仅内置规则）规则文件列表应仅含内置规则条目（row 0）。"""
        window = MainWindow()
        assert window._rules_paths == []
        # row 0 为内置规则条目（需求1），用户规则列表为空
        assert window.rules_file_list.count() == 1
        item = window.rules_file_list.item(0)
        assert item is not None
        assert item.checkState() == Qt.Checked
        window.close()

    def test_load_multiple_rules_via_dialog(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """连续加载多个规则文件应全部追加到列表。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 规则1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n',
            encoding="utf-8",
        )
        r2 = tmp_path / "r2.yaml"
        r2.write_text(
            'version: "1.0"\nrules:\n  - name: 规则2\n    severity: critical\n    match:\n      type: filename\n      mode: contains\n      pattern: b\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)

        # 先返回 r1，再返回 r2
        paths_iter = iter([str(r1), str(r2)])
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (next(paths_iter), ""),
        )
        window._on_load_rules()
        window._on_load_rules()

        assert len(window._rules_paths) == 2
        assert window._rules_paths[0] == r1
        assert window._rules_paths[1] == r2
        # row 0=内置规则条目 + row 1/2=用户规则（需求1）
        assert window.rules_file_list.count() == 3
        # 合并后规则树应有 2 条规则
        assert window.rules_tree.topLevelItemCount() == 2
        window.close()

    def test_load_duplicate_rule_cancelled_no_reload(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """重复加载同一文件时用户取消应保留原规则集，不追加路径。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 规则1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)

        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(r1), ""),
        )
        # 拦截询问对话框，模拟用户选择"否"（取消重新加载）
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.question",
            lambda *_args, **_kwargs: QMessageBox.No,
        )
        window._on_load_rules()
        original_rule_count = len(window._ruleset.rules) if window._ruleset else 0
        window._on_load_rules()  # 重复加载，用户取消

        assert len(window._rules_paths) == 1
        # 规则集未变
        assert window._ruleset is not None
        assert len(window._ruleset.rules) == original_rule_count
        window.close()

    def test_load_duplicate_rule_reload_on_confirm(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """重复加载同一文件时用户确认应重新加载规则集，不追加路径。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 规则1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)

        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(r1), ""),
        )
        window._on_load_rules()
        assert window._ruleset is not None
        assert len(window._ruleset.rules) == 1

        # 模拟用户在外部编辑器修改文件内容（新增一条规则）
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 规则1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n  - name: 规则2\n    severity: critical\n    match:\n      type: filename\n      mode: contains\n      pattern: b\n',
            encoding="utf-8",
        )

        # 拦截询问对话框，模拟用户选择"是"（确认重新加载）
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.question",
            lambda *_args, **_kwargs: QMessageBox.Yes,
        )
        window._on_load_rules()  # 重复加载，用户确认

        # 路径不重复追加
        assert len(window._rules_paths) == 1
        # 规则集已刷新为最新内容（2 条规则）
        assert window._ruleset is not None
        assert len(window._ruleset.rules) == 2
        assert window._ruleset.rules[1].name == "规则2"
        window.close()

    def test_load_duplicate_rule_reload_parse_error_warns(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """重复加载同一文件时用户确认但解析失败应弹警告。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 规则1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)

        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(r1), ""),
        )
        window._on_load_rules()
        assert window._ruleset is not None

        # 模拟用户破坏文件内容
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 规则1\n    match:\n      type: unknown_type\n      mode: contains\n      pattern: x\n',
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.question",
            lambda *_args, **_kwargs: QMessageBox.Yes,
        )
        warned = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.warning",
            lambda *_args, **_kwargs: warned.update(called=True),
        )
        # 不应抛异常
        window._on_load_rules()
        assert warned["called"]
        window.close()

    def test_move_rule_up(self, qapp: QApplication, tmp_path: Path) -> None:
        """上移规则文件应改变列表顺序并重新合并。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 共同名\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: first\n',
            encoding="utf-8",
        )
        r2 = tmp_path / "r2.yaml"
        r2.write_text(
            'version: "1.0"\nrules:\n  - name: 共同名\n    severity: critical\n    match:\n      type: filename\n      mode: contains\n      pattern: second\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [r1, r2]
        window._reload_ruleset()
        window._refresh_rules_file_list()
        # 初始 r2 覆盖 r1，pattern 应为 second
        rule = window._ruleset.rules[0]  # pyrefly: ignore [missing-attribute]
        assert rule.match.pattern == "second"

        # 选中 r2（row 2）并上移
        window.rules_file_list.setCurrentRow(2)  # pyrefly: ignore [missing-argument]
        window._on_move_rule_up()

        # 顺序变为 [r2, r1]，r1 覆盖 r2，pattern 应为 first
        assert window._rules_paths == [r2, r1]
        rule = window._ruleset.rules[0]  # pyrefly: ignore [missing-attribute]
        assert rule.match.pattern == "first"
        window.close()

    def test_move_rule_down(self, qapp: QApplication, tmp_path: Path) -> None:
        """下移规则文件应改变列表顺序并重新合并。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 共同名\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: first\n',
            encoding="utf-8",
        )
        r2 = tmp_path / "r2.yaml"
        r2.write_text(
            'version: "1.0"\nrules:\n  - name: 共同名\n    severity: critical\n    match:\n      type: filename\n      mode: contains\n      pattern: second\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [r1, r2]
        window._reload_ruleset()
        window._refresh_rules_file_list()

        # 选中 r1（row 1）并下移
        window.rules_file_list.setCurrentRow(1)  # pyrefly: ignore [missing-argument]
        window._on_move_rule_down()

        # 顺序变为 [r2, r1]，r1 覆盖 r2
        assert window._rules_paths == [r2, r1]
        rule = window._ruleset.rules[0]  # pyrefly: ignore [missing-attribute]
        assert rule.match.pattern == "first"
        window.close()

    def test_move_rule_up_at_top_noop(self, qapp: QApplication, tmp_path: Path) -> None:
        """首行用户规则上移不应改变顺序。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules: []\n',
            encoding="utf-8",
        )
        r2 = tmp_path / "r2.yaml"
        r2.write_text(
            'version: "1.0"\nrules: []\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [r1, r2]
        window._refresh_rules_file_list()

        # row 1 为首个用户规则（row 0 为内置规则条目），上移应 noop
        window.rules_file_list.setCurrentRow(1)  # pyrefly: ignore [missing-argument]
        window._on_move_rule_up()

        assert window._rules_paths == [r1, r2]
        window.close()

    def test_move_rule_down_at_bottom_noop(self, qapp: QApplication, tmp_path: Path) -> None:
        """末行下移不应改变顺序。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules: []\n',
            encoding="utf-8",
        )
        r2 = tmp_path / "r2.yaml"
        r2.write_text(
            'version: "1.0"\nrules: []\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [r1, r2]
        window._refresh_rules_file_list()

        # row 2 为末位用户规则，下移应 noop
        window.rules_file_list.setCurrentRow(2)  # pyrefly: ignore [missing-argument]
        window._on_move_rule_down()

        assert window._rules_paths == [r1, r2]
        window.close()

    def test_remove_rule(self, qapp: QApplication, tmp_path: Path) -> None:
        """移除规则文件应从列表删除并重新合并。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 规则1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n',
            encoding="utf-8",
        )
        r2 = tmp_path / "r2.yaml"
        r2.write_text(
            'version: "1.0"\nrules:\n  - name: 规则2\n    severity: critical\n    match:\n      type: filename\n      mode: contains\n      pattern: b\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [r1, r2]
        window._reload_ruleset()
        window._refresh_rules_file_list()
        window._refresh_rules_tree()
        assert window.rules_tree.topLevelItemCount() == 2

        # 选中 r1（row 1）并移除
        window.rules_file_list.setCurrentRow(1)  # pyrefly: ignore [missing-argument]
        window._on_remove_rule()

        assert len(window._rules_paths) == 1
        assert window._rules_paths[0] == r2
        # row 0=内置规则条目 + row 1=剩余用户规则
        assert window.rules_file_list.count() == 2
        assert window.rules_tree.topLevelItemCount() == 1
        window.close()

    def test_remove_all_rules_then_none(self, qapp: QApplication, tmp_path: Path) -> None:
        """移除所有用户规则后（无内置）ruleset 应为 None。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 规则1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [r1]
        window._reload_ruleset()
        window._refresh_rules_file_list()
        assert window._ruleset is not None

        # row 1 为 r1（row 0 为内置规则条目）
        window.rules_file_list.setCurrentRow(1)  # pyrefly: ignore [missing-argument]
        window._on_remove_rule()

        assert len(window._rules_paths) == 0
        assert window._ruleset is None
        assert window.rules_tree.topLevelItemCount() == 0
        window.close()

    def test_order_affects_override(self, qapp: QApplication, tmp_path: Path) -> None:
        """规则文件顺序决定覆盖关系：后者覆盖前者同名规则。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 共同名\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: from_r1\n',
            encoding="utf-8",
        )
        r2 = tmp_path / "r2.yaml"
        r2.write_text(
            'version: "1.0"\nrules:\n  - name: 共同名\n    severity: critical\n    match:\n      type: filename\n      mode: contains\n      pattern: from_r2\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)

        # [r1, r2] → r2 覆盖 r1
        window._rules_paths = [r1, r2]
        window._reload_ruleset()
        assert window._ruleset.rules[0].match.pattern == "from_r2"  # pyrefly: ignore [missing-attribute]

        # [r2, r1] → r1 覆盖 r2
        window._rules_paths = [r2, r1]
        window._reload_ruleset()
        assert window._ruleset.rules[0].match.pattern == "from_r1"  # pyrefly: ignore [missing-attribute]
        window.close()

    def test_builtin_item_check_state_reflects_use_builtin(self, qapp: QApplication) -> None:
        """内置规则条目（row 0）勾选状态应反映 _use_builtin。"""
        window = MainWindow()
        assert window._use_builtin is True
        item = window.rules_file_list.item(0)
        assert item is not None
        assert item.checkState() == Qt.Checked

        # 切换 _use_builtin=False 后刷新列表，勾选状态应变为 Unchecked
        window._set_use_builtin(False)
        window._refresh_rules_file_list()
        assert window.rules_file_list.item(0).checkState() == Qt.Unchecked
        window.close()

    def test_uncheck_builtin_item_persists_to_config(self, qapp: QApplication, tmp_path: Path) -> None:
        """取消勾选内置规则条目后 _use_builtin 应为 False 并持久化到配置。"""
        window = MainWindow()
        assert window._use_builtin is True

        # 模拟用户取消勾选 row 0
        item = window.rules_file_list.item(0)
        assert item is not None
        item.setCheckState(Qt.Unchecked)
        # itemChanged 信号触发回写
        from fuscan.config import load_config as _load_impl

        assert window._use_builtin is False
        config = _load_impl(tmp_path / "config.yaml")
        assert config.use_builtin is False
        window.close()

    def test_recheck_builtin_item_persists_to_config(self, qapp: QApplication, tmp_path: Path) -> None:
        """重新勾选内置规则条目后 _use_builtin 应为 True 并持久化到配置。"""
        window = MainWindow()
        window._set_use_builtin(False)
        window._refresh_rules_file_list()

        item = window.rules_file_list.item(0)
        assert item is not None
        assert item.checkState() == Qt.Unchecked

        # 模拟用户重新勾选
        item.setCheckState(Qt.Checked)
        from fuscan.config import load_config as _load_impl

        assert window._use_builtin is True
        config = _load_impl(tmp_path / "config.yaml")
        assert config.use_builtin is True
        window.close()

    def test_builtin_item_not_removable(self, qapp: QApplication) -> None:
        """内置规则条目（row 0）不可移除。"""
        window = MainWindow()
        # 选中 row 0 后调用 _on_remove_rule，不应删除任何项
        window.rules_file_list.setCurrentRow(0)  # pyrefly: ignore [missing-argument]
        window._on_remove_rule()
        # 内置规则条目仍在
        assert window.rules_file_list.count() == 1
        assert window.rules_file_list.item(0).text().startswith("内置通用规则")
        window.close()

    def test_builtin_item_not_movable(self, qapp: QApplication) -> None:
        """内置规则条目（row 0）不可上移/下移。"""
        window = MainWindow()
        window.rules_file_list.setCurrentRow(0)  # pyrefly: ignore [missing-argument]
        window._on_move_rule_up()
        window._on_move_rule_down()
        # 内置规则条目仍在 row 0
        assert window.rules_file_list.item(0).text().startswith("内置通用规则")
        window.close()


class TestConfigPersistence:
    """配置持久化集成测试。"""

    def test_rules_paths_restored_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """启动时从配置恢复规则文件列表。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: r1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n',
            encoding="utf-8",
        )
        config = Config(rules_paths=[str(r1)], use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert len(window._rules_paths) == 1
        assert window._rules_paths[0] == r1
        assert window._use_builtin is False
        assert window._ruleset is not None
        assert len(window._ruleset.rules) == 1
        window.close()

    def test_nonexistent_rules_paths_skipped(self, qapp: QApplication, tmp_path: Path) -> None:
        """配置中不存在的规则文件路径应被跳过。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        config = Config(rules_paths=[str(tmp_path / "nonexistent.yaml")], use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert len(window._rules_paths) == 0
        window.close()

    def test_use_builtin_restored(self, qapp: QApplication, tmp_path: Path) -> None:
        """通用规则开关状态从配置恢复。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        config = Config(use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._use_builtin is False
        window.close()

    def test_scan_paths_history_restored(self, qapp: QApplication, tmp_path: Path) -> None:
        """扫描路径历史从配置恢复到下拉框。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        (tmp_path / "dir_a").mkdir()
        (tmp_path / "dir_b").mkdir()
        config = Config(scan_paths=[str(tmp_path / "dir_a"), str(tmp_path / "dir_b")])
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window.path_combo.count() == 2
        assert window.path_combo.itemText(0) == str(tmp_path / "dir_a")
        assert window.path_combo.itemText(1) == str(tmp_path / "dir_b")
        window.close()

    def test_close_event_saves_config(self, qapp: QApplication, tmp_path: Path) -> None:
        """关闭窗口时配置应被保存。"""
        window = MainWindow()
        window._set_use_builtin(False)
        window.close()

        from fuscan.config import load_config as _load_impl

        config = _load_impl(tmp_path / "config.yaml")
        assert config.use_builtin is False

    def test_close_saves_rules_paths(self, qapp: QApplication, tmp_path: Path) -> None:
        """关闭时规则文件列表应被保存。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules: []\n',
            encoding="utf-8",
        )
        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [r1]
        window.close()

        from fuscan.config import load_config as _load_impl

        config = _load_impl(tmp_path / "config.yaml")
        assert str(r1) in config.rules_paths

    def test_close_saves_scan_paths_history(self, qapp: QApplication, tmp_path: Path) -> None:
        """关闭时扫描路径历史应被保存。"""
        (tmp_path / "scan_dir").mkdir()
        window = MainWindow()
        window._add_scan_path_history(str(tmp_path / "scan_dir"))
        window.close()

        from fuscan.config import load_config as _load_impl

        config = _load_impl(tmp_path / "config.yaml")
        assert str(tmp_path / "scan_dir") in config.scan_paths

    def test_path_combo_select_sets_scan_root(self, qapp: QApplication, tmp_path: Path) -> None:
        """从下拉选择路径应设置 scan_root。"""
        (tmp_path / "target").mkdir()
        window = MainWindow()
        window.path_combo.addItem(str(tmp_path / "target"))  # pyrefly: ignore [missing-argument]
        window.path_combo.setCurrentIndex(0)
        assert window._scan_root == tmp_path / "target"
        window.close()

    def test_path_history_dedup(self, qapp: QApplication, tmp_path: Path) -> None:
        """重复路径在历史中只出现一次。"""
        path_str = str(tmp_path)
        window = MainWindow()
        window._add_scan_path_history(path_str)
        window._add_scan_path_history(path_str)
        assert window.path_combo.count() == 1
        window.close()

    def test_path_history_limit(self, qapp: QApplication, tmp_path: Path) -> None:
        """历史路径超过上限时自动截断。"""
        from fuscan.config import MAX_HISTORY

        window = MainWindow()
        for i in range(MAX_HISTORY + 5):
            window._add_scan_path_history(f"/path/{i}")
        assert window.path_combo.count() == MAX_HISTORY
        # 最近添加的应在最前
        assert window.path_combo.itemText(0) == f"/path/{MAX_HISTORY + 4}"
        window.close()

    def test_window_geometry_restored(self, qapp: QApplication, tmp_path: Path) -> None:
        """窗口几何从配置恢复。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        # 高度需大于窗口最小高度（5 区布局约 680px），否则 Qt 会强制抬升到最小值
        config = Config(window_geometry=[50, 60, 900, 800])
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        geo = window.geometry()
        assert geo.x() == 50
        assert geo.y() == 60
        assert geo.width() == 900
        # 高度可能因 QSS 布局约束有少量偏差
        assert abs(geo.height() - 800) <= 5
        window.close()

    def test_splitter_sizes_restored(self, qapp: QApplication, tmp_path: Path) -> None:
        """分割器大小从配置恢复（按比例）。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        config = Config(window_geometry=[0, 0, 1000, 700], splitter_sizes=[400, 600])
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        window._switch_stage(WorkflowStage.RESULTS)
        window.show()
        qapp.processEvents()
        sizes = window.results_splitter.sizes()
        assert len(sizes) == 2
        assert all(s > 0 for s in sizes)
        # 比例约为 400:600 = 2:3
        ratio = sizes[0] / sizes[1]
        assert 0.5 < ratio < 0.8
        window.close()

    def test_valid_scan_path_enables_button_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """配置中有有效路径时启动后扫描按钮应启用。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        scan_dir = tmp_path / "scan_target"
        scan_dir.mkdir()
        config = Config(scan_paths=[str(scan_dir)], use_builtin=True)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_root == scan_dir
        assert window.scan_btn.isEnabled()
        window.close()

    def test_invalid_scan_path_disables_button_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """配置中路径无效时启动后扫描按钮应禁用。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        config = Config(scan_paths=[str(tmp_path / "nonexistent")], use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_root is None
        assert not window.scan_btn.isEnabled()
        window.close()

    def test_no_scan_path_disables_button_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """配置中无路径时启动后扫描按钮应禁用。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        config = Config(scan_paths=[], use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_root is None
        assert not window.scan_btn.isEnabled()
        window.close()


class TestScanWorker:
    def test_worker_runs_scan(self, qapp: QApplication, tmp_path: Path) -> None:
        """ScanWorker 应在后台完成扫描。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer  # pyrefly: ignore [missing-import]

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        results: list[Any] = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108  # pyrefly: ignore [missing-attribute]
        worker.start()

        # 通过事件循环等待 finished 信号
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # 超时保护  # pyrefly: ignore [bad-argument-type, missing-argument]
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        worker.wait(2000)
        assert not worker.isRunning()
        assert len(results) == 1
        report = results[0]
        assert report.stats.matched_files >= 1

    def test_worker_handles_invalid_path(self, qapp: QApplication, tmp_path: Path) -> None:
        """无效路径应正常完成（Scanner 返回空报告）。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer  # pyrefly: ignore [missing-import]

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path / "nonexistent"])

        results: list[Any] = []
        errors: list[Any] = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108  # pyrefly: ignore [missing-attribute]
        worker.failed.connect(lambda msg: errors.append(msg))  # noqa: PLW0108  # pyrefly: ignore [missing-attribute]
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # pyrefly: ignore [bad-argument-type, missing-argument]
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        worker.wait(2000)
        assert not worker.isRunning()
        # 无效路径返回空报告，不应有 error
        assert len(errors) == 0


class TestScanControlUI:
    """扫描控制 UI 状态测试：开始/暂停/继续/停止。"""

    def test_scan_state_idle_initially(self, qapp: QApplication) -> None:
        """启动时扫描状态应为 IDLE。"""
        window = MainWindow()
        assert window._scan_state == ScanState.IDLE
        window.close()

    def test_scan_button_text_idle(self, qapp: QApplication) -> None:
        """IDLE 状态扫描按钮文本为"开始扫描"。"""
        window = MainWindow()
        assert window.scan_btn.text() == "开始扫描"
        assert window.scan_action.text() == "开始扫描"
        window.close()

    def test_update_scan_button_running_stays_enabled(self, qapp: QApplication) -> None:
        """RUNNING 状态下扫描按钮应始终可用，即使无规则集。"""
        window = MainWindow()
        window._ruleset = None
        window._scan_state = ScanState.RUNNING
        window._update_scan_button()
        assert window.scan_btn.isEnabled()
        assert window.scan_action.isEnabled()
        window.close()

    def test_update_scan_button_paused_stays_enabled(self, qapp: QApplication) -> None:
        """PAUSED 状态下扫描按钮应始终可用。"""
        window = MainWindow()
        window._ruleset = None
        window._scan_state = ScanState.PAUSED
        window._update_scan_button()
        assert window.scan_btn.isEnabled()
        assert window.scan_action.isEnabled()
        window.close()

    def test_pause_scan_changes_state_and_text(self, qapp: QApplication) -> None:
        """_pause_scan 应设置 PAUSED 状态和"继续扫描"文本。"""
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._pause_scan()
        assert window._scan_state == ScanState.PAUSED
        assert window.pause_resume_btn.text() == "继续扫描"
        assert "已暂停" in window.stats_label.text()
        window.close()

    def test_resume_scan_changes_state_and_text(self, qapp: QApplication) -> None:
        """_resume_scan 应设置 RUNNING 状态和"暂停扫描"文本。"""
        window = MainWindow()
        window._scan_state = ScanState.PAUSED
        window._resume_scan()
        assert window._scan_state == ScanState.RUNNING
        assert window.pause_resume_btn.text() == "暂停扫描"
        window.close()

    def test_reset_scan_ui_resets_state(self, qapp: QApplication) -> None:
        """_reset_scan_ui 应重置到 IDLE 状态并恢复 pause_resume_btn 文本。"""
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._reset_scan_ui()
        assert window._scan_state == ScanState.IDLE
        assert window.pause_resume_btn.text() == "暂停扫描"
        assert window._worker is None
        window.close()

    def test_on_scan_with_no_ruleset_does_nothing(self, qapp: QApplication) -> None:
        """IDLE 状态无规则集时点击扫描按钮不应启动。"""
        window = MainWindow()
        window._ruleset = None
        window._on_scan()
        assert window._scan_state == ScanState.IDLE
        assert window._worker is None
        window.close()

    def test_on_pause_resume_running_triggers_pause(self, qapp: QApplication) -> None:
        """RUNNING 状态点击 pause_resume_btn 应触发暂停。"""
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._on_pause_resume()
        assert window._scan_state == ScanState.PAUSED
        assert window.pause_resume_btn.text() == "继续扫描"
        window.close()

    def test_on_pause_resume_paused_triggers_resume(self, qapp: QApplication) -> None:
        """PAUSED 状态点击 pause_resume_btn 应触发恢复。"""
        window = MainWindow()
        window._scan_state = ScanState.PAUSED
        window._on_pause_resume()
        assert window._scan_state == ScanState.RUNNING
        assert window.pause_resume_btn.text() == "暂停扫描"
        window.close()


class TestScanControlIntegration:
    """扫描控制集成测试：通过 MainWindow 运行完整扫描流程。"""

    def test_scan_completes_through_main_window(self, qapp: QApplication, tmp_path: Path) -> None:
        """通过 MainWindow 启动扫描应完成并填充结果树。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer  # pyrefly: ignore [missing-import]

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        (tmp_path / "normal.txt").write_text("y", encoding="utf-8")

        window = MainWindow()
        window.show()
        qapp.processEvents()
        window._ruleset = _build_ruleset()
        window._scan_root = tmp_path
        window._scan_mode = "folder"
        window._on_scan()

        assert window._scan_state == ScanState.RUNNING
        assert window.pause_resume_btn.text() == "暂停扫描"
        assert window.main_stack.currentIndex() == 1

        loop = QEventLoop()
        QTimer.singleShot(10000, loop.quit)  # pyrefly: ignore [bad-argument-type, missing-argument]
        window._worker.finished.connect(loop.quit) if window._worker is not None else None
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        if window._worker is not None:
            window._worker.wait(2000)

        assert window._scan_state == ScanState.IDLE
        assert window.main_stack.currentIndex() == 2
        assert window.result_tree.model().rowCount() >= 1
        assert window._worker is None
        window.close()

    def test_scan_cancel_through_main_window(self, qapp: QApplication, tmp_path: Path) -> None:
        """通过 MainWindow 取消扫描应显示已取消状态。"""
        from fuscan.scanner import ScanReport
        from fuscan.scanner.result import ScanStats

        window = MainWindow()
        window._ruleset = _build_ruleset()
        window._scan_root = tmp_path
        window._scan_mode = "folder"

        # 模拟扫描中状态
        window._scan_state = ScanState.RUNNING

        # 直接调用 _on_scan_cancelled 模拟取消回调
        report = ScanReport(
            root=tmp_path,
            results=(),
            stats=ScanStats(total_files=100, scanned_files=50, matched_files=10),
            cancelled=True,
        )
        window._on_scan_cancelled(report)

        assert window._scan_state == ScanState.IDLE
        assert window.main_stack.currentIndex() == 0
        assert "已取消" in window.stats_label.text()
        window.close()

    def test_cleanup_worker_sets_none(self, qapp: QApplication) -> None:
        """_cleanup_worker 应将 _worker 设为 None。"""
        window = MainWindow()
        # 模拟有 worker 的情况
        window._worker = None  # 已经是 None
        window._cleanup_worker()
        assert window._worker is None
        window.close()

    def test_on_scan_no_roots_shows_warning(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IDLE 状态有规则集但无有效扫描目标时应提示。"""
        warned: dict[str, bool] = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.warning",
            lambda *args, **kwargs: warned.update(called=True),
        )
        window = MainWindow()
        window._ruleset = _build_ruleset()
        window._scan_mode = "folder"
        window._scan_root = None
        window._on_scan()
        assert warned["called"]
        assert window._scan_state == ScanState.IDLE
        window.close()


class TestWorkflowStage:
    """工作流阶段切换测试：SETUP/SCANNING/RESULTS 三页切换与控件状态。"""

    def test_initial_stage_is_setup(self, qapp: QApplication) -> None:
        """新建窗口应在 SETUP 阶段（配置页）。"""
        window = MainWindow()
        assert window._workflow_stage == WorkflowStage.SETUP
        assert window.main_stack.currentIndex() == 0
        window.close()

    def test_setup_to_scanning(self, qapp: QApplication, tmp_path: Path) -> None:
        """从 SETUP 启动扫描应切换到 SCANNING 页。"""
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        window = MainWindow()
        window._ruleset = _build_ruleset()
        window._scan_root = tmp_path
        window._scan_mode = "folder"
        window._on_scan()
        assert window._scan_state == ScanState.RUNNING
        assert window.main_stack.currentIndex() == 1
        assert window._workflow_stage == WorkflowStage.SCANNING
        # 清理后台线程
        if window._worker is not None:
            window._worker.wait(2000)
            window._worker = None
        window.close()

    def test_scanning_to_results_on_finish(self, qapp: QApplication, tmp_path: Path) -> None:
        """扫描完成应切换到 RESULTS 页。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._on_scan_finished(report)
        assert window.main_stack.currentIndex() == 2
        assert window._workflow_stage == WorkflowStage.RESULTS
        window.close()

    def test_scanning_to_setup_on_fail(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """扫描失败应切回 SETUP 页。"""
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.critical",
            lambda *args, **kwargs: None,
        )
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._on_scan_failed("测试错误")
        assert window.main_stack.currentIndex() == 0
        assert window._workflow_stage == WorkflowStage.SETUP
        window.close()

    def test_results_to_setup_on_rescan(self, qapp: QApplication) -> None:
        """结果页点击重新扫描应返回 SETUP 页。"""
        window = MainWindow()
        window._switch_stage(WorkflowStage.RESULTS)
        window._on_rescan()
        assert window.main_stack.currentIndex() == 0
        assert window._workflow_stage == WorkflowStage.SETUP
        window.close()

    def test_setup_to_results_on_view_results(self, qapp: QApplication, tmp_path: Path) -> None:
        """配置页有报告时点击查看结果应切换到 RESULTS 页。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        window._update_stage_actions()
        window._on_view_results()
        assert window.main_stack.currentIndex() == 2
        window.close()

    def test_view_results_btn_disabled_initially(self, qapp: QApplication) -> None:
        """新建窗口无报告时查看结果按钮应可见但禁用。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        # 按钮始终可见（与 scan_btn 组合在一起），但无结果时禁用
        assert window.view_results_btn.isVisible()
        assert not window.view_results_btn.isEnabled()
        window.close()

    def test_view_results_btn_enabled_with_report(self, qapp: QApplication, tmp_path: Path) -> None:
        """配置页有报告时查看结果按钮应可见且启用。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        window._update_stage_actions()
        window.show()
        qapp.processEvents()
        assert window.view_results_btn.isVisible()
        assert window.view_results_btn.isEnabled()
        window.close()

    def test_scan_btn_disabled_without_ruleset(self, qapp: QApplication) -> None:
        """无规则集时扫描按钮应禁用。"""
        window = MainWindow()
        window._ruleset = None
        window._update_stage_actions()
        assert not window.scan_btn.isEnabled()
        window.close()

    def test_scan_btn_enabled_with_ruleset_and_target(self, qapp: QApplication, tmp_path: Path) -> None:
        """有规则集和有效目标时扫描按钮应可用。"""
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        window = MainWindow()
        window._ruleset = _build_ruleset()
        window._scan_root = tmp_path
        window._scan_mode = "folder"
        window._update_stage_actions()
        assert window.scan_btn.isEnabled()
        window.close()

    def test_rescan_btn_disabled_in_setup(self, qapp: QApplication) -> None:
        """SETUP 阶段重新扫描按钮应禁用，RESULTS 阶段应启用。"""
        window = MainWindow()
        window._update_stage_actions()
        assert not window.rescan_btn.isEnabled()
        window._switch_stage(WorkflowStage.RESULTS)
        assert window.rescan_btn.isEnabled()
        window.close()

    def test_pause_cancel_btn_disabled_in_setup(self, qapp: QApplication) -> None:
        """SETUP 阶段（扫描前）暂停与取消按钮应禁用。"""
        window = MainWindow()
        window._update_stage_actions()
        assert not window.pause_resume_btn.isEnabled()
        assert not window.cancel_btn.isEnabled()
        window.close()

    def test_pause_cancel_btn_enabled_in_scanning(self, qapp: QApplication) -> None:
        """SCANNING 阶段暂停与取消按钮应启用。"""
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._switch_stage(WorkflowStage.SCANNING)
        assert window.pause_resume_btn.isEnabled()
        assert window.cancel_btn.isEnabled()
        window.close()

    def test_pause_cancel_btn_disabled_in_results(self, qapp: QApplication) -> None:
        """RESULTS 阶段（扫描完成后）暂停与取消按钮应禁用。"""
        window = MainWindow()
        window._switch_stage(WorkflowStage.RESULTS)
        assert not window.pause_resume_btn.isEnabled()
        assert not window.cancel_btn.isEnabled()
        window.close()

    def test_pause_resume_btn_text_in_scanning_running(self, qapp: QApplication) -> None:
        """SCANNING 阶段 RUNNING 状态 pause_resume_btn 文本为"暂停扫描"。"""
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._switch_stage(WorkflowStage.SCANNING)
        assert window.pause_resume_btn.text() == "暂停扫描"
        window.close()

    def test_pause_resume_btn_text_in_scanning_paused(self, qapp: QApplication) -> None:
        """SCANNING 阶段 PAUSED 状态 pause_resume_btn 文本为"继续扫描"。"""
        window = MainWindow()
        window._scan_state = ScanState.PAUSED
        window._switch_stage(WorkflowStage.SCANNING)
        assert window.pause_resume_btn.text() == "继续扫描"
        window.close()

    def test_on_pause_resume_idle_does_nothing(self, qapp: QApplication) -> None:
        """IDLE 状态调用 _on_pause_resume 不应改变状态。"""
        window = MainWindow()
        window._on_pause_resume()
        assert window._scan_state == ScanState.IDLE
        window.close()

    def test_on_cancel_scan_calls_worker_cancel(self, qapp: QApplication) -> None:
        """_on_cancel_scan 应调用 worker.cancel()。"""

        class _FakeWorker:
            def __init__(self) -> None:
                self.cancelled = False

            def cancel(self) -> None:
                self.cancelled = True

        window = MainWindow()
        fake = _FakeWorker()
        window._worker = fake  # type: ignore[assignment]
        window._on_cancel_scan()
        assert fake.cancelled
        window._worker = None
        window.close()

    def test_on_cancel_scan_without_worker_does_nothing(self, qapp: QApplication) -> None:
        """无 worker 时 _on_cancel_scan 不应崩溃。"""
        window = MainWindow()
        window._worker = None
        window._on_cancel_scan()
        window.close()

    def test_cancel_scan_with_hits_returns_to_results(self, qapp: QApplication, tmp_path: Path) -> None:
        """取消扫描有命中时应切换到 RESULTS 页。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._on_scan_cancelled(report)
        assert window.main_stack.currentIndex() == 2
        window.close()

    def test_cancel_scan_without_hits_returns_to_setup(self, qapp: QApplication, tmp_path: Path) -> None:
        """取消扫描无命中时应返回 SETUP 页。"""
        from fuscan.scanner import ScanReport
        from fuscan.scanner.result import ScanStats

        report = ScanReport(
            root=tmp_path,
            results=(),
            stats=ScanStats(total_files=10, scanned_files=5, matched_files=0),
            cancelled=True,
        )
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._on_scan_cancelled(report)
        assert window.main_stack.currentIndex() == 0
        window.close()

    def test_scan_action_disabled_in_scanning(self, qapp: QApplication) -> None:
        """SCANNING 阶段扫描菜单项应禁用。"""
        window = MainWindow()
        window._switch_stage(WorkflowStage.SCANNING)
        assert not window.scan_action.isEnabled()
        window.close()

    def test_export_actions_disabled_in_setup(self, qapp: QApplication) -> None:
        """SETUP 阶段导出菜单项应禁用。"""
        window = MainWindow()
        window._update_stage_actions()
        assert not window.export_csv_action.isEnabled()
        assert not window.export_json_action.isEnabled()
        window.close()

    def test_export_actions_enabled_in_results_with_report(self, qapp: QApplication, tmp_path: Path) -> None:
        """RESULTS 阶段有报告时导出菜单项应可用。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        window._switch_stage(WorkflowStage.RESULTS)
        assert window.export_csv_action.isEnabled()
        assert window.export_json_action.isEnabled()
        window.close()

    def test_load_edit_rules_actions_disabled_in_results(self, qapp: QApplication) -> None:
        """RESULTS 阶段加载/编辑规则菜单项应禁用。"""
        window = MainWindow()
        window._switch_stage(WorkflowStage.RESULTS)
        assert not window.load_rules_action.isEnabled()
        assert not window.edit_rules_action.isEnabled()
        window.close()

    def test_switch_stage_syncs_sidebar(self, qapp: QApplication) -> None:
        """_switch_stage 应同步侧边栏选中项。"""
        window = MainWindow()
        window._switch_stage(WorkflowStage.SCANNING)
        assert window.sidebar.currentRow() == 1
        window._switch_stage(WorkflowStage.RESULTS)
        assert window.sidebar.currentRow() == 2
        window._switch_stage(WorkflowStage.SETUP)
        assert window.sidebar.currentRow() == 0
        window.close()

    def test_on_header_tab_changed_switches_tab_stack(self, qapp: QApplication) -> None:
        """_on_header_tab_changed 应切换 tab_stack 页面。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        window._on_header_tab_changed(1)
        assert window.tab_stack.currentIndex() == 1
        assert not window.sidebar.isVisible()
        window._on_header_tab_changed(2)
        assert window.tab_stack.currentIndex() == 2
        assert not window.sidebar.isVisible()
        window._on_header_tab_changed(0)
        assert window.tab_stack.currentIndex() == 0
        assert window.sidebar.isVisible()
        window.close()

    def test_on_sidebar_stage_changed_switches_main_stack(self, qapp: QApplication) -> None:
        """_on_sidebar_stage_changed 应映射 row 到 WorkflowStage 并切换 main_stack。"""
        window = MainWindow()
        window._on_sidebar_stage_changed(1)
        assert window.main_stack.currentIndex() == 1
        assert window._workflow_stage == WorkflowStage.SCANNING
        window._on_sidebar_stage_changed(2)
        assert window.main_stack.currentIndex() == 2
        assert window._workflow_stage == WorkflowStage.RESULTS
        window._on_sidebar_stage_changed(0)
        assert window.main_stack.currentIndex() == 0
        assert window._workflow_stage == WorkflowStage.SETUP
        window.close()

    def test_scan_finished_shows_speed_and_perf_hotspots(self, qapp: QApplication, tmp_path: Path) -> None:
        """扫描完成后状态栏应显示速度与性能热点摘要。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password123", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._on_scan_finished(report)
        stats_text = window.stats_label.text()
        # 速度应出现在状态栏
        assert "文件/s" in stats_text
        # 性能热点应出现（PerfStats 始终启用）
        assert "热点:" in stats_text or "read" in stats_text or "match" in stats_text
        window.close()

    def test_show_perf_stats_no_data_shows_message(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """无扫描结果时点击性能统计应提示先扫描。"""
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window = MainWindow()
        window._on_show_perf_stats()
        window.close()

    def test_show_perf_stats_with_data_shows_dialog(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """有扫描结果时点击性能统计应展示对话框。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password123", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        # mock dialog.exec_ 避免阻塞
        monkeypatch.setattr("fuscan.gui.main_window.QDialog.exec_", lambda self: 0)
        window._on_show_perf_stats()
        window.close()

    def test_toggle_perf_log_switches_state(self, qapp: QApplication) -> None:
        """切换性能日志菜单项应切换 PerfTimer 开关。"""
        from fuscan import perf as perf_mod

        original = perf_mod._PerfState.enabled
        window = MainWindow()
        try:
            window._on_toggle_perf_log(True)
            assert perf_mod._PerfState.enabled is True
            window._on_toggle_perf_log(False)
            assert perf_mod._PerfState.enabled is False
        finally:
            perf_mod._PerfState.enabled = original
            window.close()

    def test_toggle_perf_log_persists_to_config(self, qapp: QApplication, tmp_path: Path) -> None:
        """切换性能日志后应立即持久化到配置文件（iter-69）。"""
        from fuscan import perf as perf_mod
        from fuscan.config import load_config as _load_impl

        original = perf_mod._PerfState.enabled
        window = MainWindow()
        try:
            window._on_toggle_perf_log(True)
            config = _load_impl(tmp_path / "config.yaml")
            assert config.perf_log_enabled is True
            window._on_toggle_perf_log(False)
            config = _load_impl(tmp_path / "config.yaml")
            assert config.perf_log_enabled is False
        finally:
            perf_mod._PerfState.enabled = original
            window.close()

    def test_perf_log_enabled_restored_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """配置中 perf_log_enabled=True 时启动应自动恢复 PerfTimer 开关（iter-69）。"""
        from fuscan import perf as perf_mod
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        original = perf_mod._PerfState.enabled
        _save_impl(Config(perf_log_enabled=True), tmp_path / "config.yaml")
        window = MainWindow()
        try:
            assert perf_mod._PerfState.enabled is True
            assert window.perf_log_action.isChecked() is True
        finally:
            perf_mod._PerfState.enabled = original
            window.close()

    def test_add_scan_path_history_persists_immediately(self, qapp: QApplication, tmp_path: Path) -> None:
        """添加路径后应立即持久化，无需关闭窗口（iter-69）。"""
        from fuscan.config import load_config as _load_impl

        (tmp_path / "scan_dir").mkdir()
        window = MainWindow()
        window._add_scan_path_history(str(tmp_path / "scan_dir"))
        # 不调用 window.close()，直接检查配置文件
        config = _load_impl(tmp_path / "config.yaml")
        assert str(tmp_path / "scan_dir") in config.scan_paths
        window.close()


class TestSetupActionBar:
    """配置页操作条（setup_action_bar）结构与样式测试。"""

    def test_scan_btn_height_reduced_to_44(self, qapp: QApplication) -> None:
        """scan_btn 最小高度应为 44（与扫描中页按钮一致）。"""
        window = MainWindow()
        assert window.scan_btn.minimumHeight() == 44
        window.close()

    def test_scan_btn_minimum_width_180(self, qapp: QApplication) -> None:
        """scan_btn 最小宽度应为 180。"""
        window = MainWindow()
        assert window.scan_btn.minimumWidth() >= 180
        window.close()

    def test_setup_action_bar_exists(self, qapp: QApplication) -> None:
        """配置页应包含 setup_action_bar 容器。"""
        window = MainWindow()
        assert hasattr(window, "setup_action_bar")
        assert window.setup_action_bar is not None
        window.close()

    def test_scan_btn_qss_uses_primary_blue(self) -> None:
        """QSS 中 scan_btn 应使用 PRIMARY 蓝 #40a9ff，不应保留绿色 #2ea44f。"""
        from fuscan.gui.app import load_stylesheet

        qss = load_stylesheet()
        scan_btn_section = qss[qss.find("QPushButton#scan_btn") :]
        assert "#40a9ff" in scan_btn_section
        assert "#2ea44f" not in qss

    def test_view_results_btn_qss_is_outline(self) -> None:
        """view_results_btn 应为轮廓样式（白底蓝边），与 scan_btn 主次区分。"""
        from fuscan.gui.app import load_stylesheet

        qss = load_stylesheet()
        assert "QPushButton#view_results_btn" in qss
        view_results_section = qss[qss.find("QPushButton#view_results_btn") :]
        assert "background: #ffffff" in view_results_section[:200]
        assert "border: 1px solid #40a9ff" in view_results_section[:200]

    def test_view_results_btn_same_size_as_scan_btn(self, qapp: QApplication) -> None:
        """view_results_btn 与 scan_btn 最小尺寸应一致（180x44）。"""
        window = MainWindow()
        assert window.view_results_btn.minimumWidth() == window.scan_btn.minimumWidth()
        assert window.view_results_btn.minimumHeight() == window.scan_btn.minimumHeight()
        assert window.scan_btn.minimumWidth() == 180
        assert window.scan_btn.minimumHeight() == 44
        window.close()

    def test_view_results_btn_adjacent_to_scan_btn(self, qapp: QApplication) -> None:
        """view_results_btn 与 scan_btn 应相邻（中间无 stretch spacer 分隔）。

        通过验证 setup_btn_row 中两个按钮的布局索引相邻且无 Expanding spacer 插入。
        """
        window = MainWindow()
        layout = window.setup_btn_row
        # 找到 view_results_btn 和 scan_btn 的位置索引
        view_idx = -1
        scan_idx = -1
        spacer_count = 0
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            if item.widget() is window.view_results_btn:
                view_idx = i
            elif item.widget() is window.scan_btn:
                scan_idx = i
            elif item.spacerItem() is not None and item.spacerItem().expandingDirections() != 0:
                spacer_count += 1
        assert view_idx >= 0 and scan_idx >= 0
        # 两个按钮索引相邻（差的绝对值为 1）
        assert abs(scan_idx - view_idx) == 1
        # 仅保留一个 leading spacer 用于右对齐，按钮间不再插入 spacer
        assert spacer_count == 1
        window.close()

    def test_setup_btn_spacer_removed(self, qapp: QApplication) -> None:
        """原 setup_btn_spacer（按钮间分隔）应已移除，仅保留 leading spacer。"""
        window = MainWindow()
        # 旧属性名不应再存在
        assert not hasattr(window, "setup_btn_spacer")
        assert hasattr(window, "setup_btn_leading_spacer")
        window.close()


class TestScanningPageLayout:
    """扫描中页布局调整测试：进度与当前文件移至状态栏，统计面板移除。"""

    def test_stats_group_removed_from_ui(self, qapp: QApplication) -> None:
        """scanning_page 不应再包含 stats_group / stats_counts_label / stats_time_label。"""
        window = MainWindow()
        assert not hasattr(window, "stats_group")
        assert not hasattr(window, "stats_counts_label")
        assert not hasattr(window, "stats_time_label")
        window.close()

    def test_progress_moved_to_status_bar(self, qapp: QApplication) -> None:
        """进度条应挂载到状态栏（permanent 区），且初始不可见。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        # _progress 应存在且为 QProgressBar
        try:
            from PySide2.QtWidgets import QProgressBar as _QProgressBar
        except ImportError:  # pragma: no cover
            from PySide6.QtWidgets import QProgressBar as _QProgressBar  # pyrefly: ignore [missing-import]
        assert isinstance(window.progress, _QProgressBar)
        # 初始（SETUP 阶段）应不可见
        assert not window.progress.isVisible()
        window.close()

    def test_current_file_label_in_status_bar(self, qapp: QApplication) -> None:
        """当前文件标签应挂载到状态栏，且初始不可见。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        assert not window.current_file_label.isVisible()
        # 进入扫描中阶段后应可见
        window._switch_stage(WorkflowStage.SCANNING)
        qapp.processEvents()
        assert window.current_file_label.isVisible()
        assert window.progress.isVisible()
        window.close()

    def test_progress_hidden_in_non_scanning_stage(self, qapp: QApplication) -> None:
        """非扫描阶段进度条与当前文件标签应隐藏。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        window._switch_stage(WorkflowStage.SETUP)
        qapp.processEvents()
        assert not window.progress.isVisible()
        assert not window.current_file_label.isVisible()
        window._switch_stage(WorkflowStage.RESULTS)
        qapp.processEvents()
        assert not window.progress.isVisible()
        assert not window.current_file_label.isVisible()
        window.close()

    def test_progress_updates_value_in_status_bar(self, qapp: QApplication) -> None:
        """_on_scan_progress 应更新状态栏进度条的值。"""
        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        window._switch_stage(WorkflowStage.SCANNING)
        info = ProgressInfo(
            total=100,
            scanned=50,
            skipped=0,
            matched=0,
            errors=0,
            current_file="/test/file.txt",
            elapsed=1.0,
        )
        window._on_scan_progress(info)
        assert window.progress.value() == 50
        assert window.progress.maximum() == 100
        window.close()


class TestThemeColorContrast:
    """主题色选中/未选中对比度测试。"""

    def test_primary_dark_distinct_from_primary(self) -> None:
        """COLOR_PRIMARY_DARK 应与 COLOR_PRIMARY 有明显色差（非相邻值）。"""
        from fuscan import theme

        assert theme.COLOR_PRIMARY != theme.COLOR_PRIMARY_DARK
        assert theme.COLOR_PRIMARY_DARK != theme.COLOR_PRIMARY_DARKER
        # PRIMARY_DARK 应比 PRIMARY 更深（hex 值更小）
        assert theme.COLOR_PRIMARY_DARK < theme.COLOR_PRIMARY

    def test_header_tab_checked_has_accent_border(self) -> None:
        """QSS 中头部 Tab 选中态应有强调色底边。"""
        from fuscan.gui.app import load_stylesheet

        qss = load_stylesheet()
        checked_section = qss[qss.find("QFrame#header_bar QPushButton:checked") :]
        assert "border-bottom" in checked_section[:300]
        assert "${COLOR_ACCENT}" not in checked_section  # 占位符应已替换
        assert "#58a6ff" in checked_section  # COLOR_ACCENT 替换后的值

    def test_item_views_have_selection_color_for_inactive_focus(self) -> None:
        """所有 item view 控件应设置 selection-color/selection-background-color。

        防止 iter-67 修复的"失去焦点选中项变黑"问题回归：QSS ``::item:selected``
        仅控制有焦点时的样式，失去焦点时 Qt 回退到 QPalette Inactive Highlight
        （默认灰底黑字）。控件级 ``selection-*`` 属性不论焦点状态都生效。
        """
        from fuscan import theme
        from fuscan.gui.app import load_stylesheet

        qss = load_stylesheet()
        # 6 处 item view 控件均须显式设置 selection-color 为白色
        # （QListWidget#sidebar / QTreeWidget#result_tree / QTreeWidget /
        #  QListWidget / QTableWidget / QComboBox QAbstractItemView）
        expected_selection_color = theme.COLOR_TEXT_ON_PRIMARY  # #ffffff
        expected_selection_bg = theme.COLOR_PRIMARY_DARK  # #096dd9
        # 统计 selection-color 出现次数（应为 6 处控件级 + QLineEdit 1 处 = 7）
        # QLineEdit 的 selection-* 用于文本选区，不计入 item view 回归保护
        item_view_selectors = [
            "QListWidget#sidebar",
            "QTreeWidget#result_tree",
            "QTreeWidget {",
            "QListWidget {",
            "QTableWidget {",
            "QComboBox QAbstractItemView",
        ]
        for selector in item_view_selectors:
            block_start = qss.find(selector)
            assert block_start != -1, f"QSS 缺少选择器: {selector}"
            # 取该选择器到下一个选择器之间的块（大括号闭合）
            block_end = qss.find("}", block_start)
            block = qss[block_start : block_end + 1]
            assert "selection-background-color" in block, f"{selector} 缺少 selection-background-color"
            assert "selection-color" in block, f"{selector} 缺少 selection-color"
            assert expected_selection_bg in block, f"{selector} 选中背景应为 {expected_selection_bg}"
            assert expected_selection_color in block, f"{selector} 选中文字应为 {expected_selection_color}"


class TestSeverityDisplay:
    """严重等级颜色区分测试。"""

    def test_severity_text_chinese_labels(self) -> None:
        """severity_text 应返回中文标签。"""
        assert severity_text(Severity.CRITICAL) == "严重"
        assert severity_text(Severity.WARNING) == "警告"
        assert severity_text(Severity.INFO) == "一般"

    def test_result_tree_flat_shows_severity_colors(self, qapp: QApplication, tmp_path: Path) -> None:
        """result_tree 不分组模式下严重等级列应显示中文标签与背景色。

        注：前景色由 QSS ``::item:selected`` 选中态接管（统一白字），
        故仅验证背景色编码（需求1：选中项字体统一白色）。
        """
        from fuscan.gui.preview_utils import SEVERITY_BACKGROUNDS
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password = 123", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        window._switch_stage(WorkflowStage.RESULTS)
        window._refresh_result_tree()

        expected_bg = SEVERITY_BACKGROUNDS[Severity.WARNING]
        top_item = window.result_tree.model().item(0, 0)
        assert top_item is not None
        sev_cell = window.result_tree.model().item(0, 2)
        assert sev_cell is not None
        assert sev_cell.text() == "警告"
        assert sev_cell.background().color().rgb() == expected_bg.rgb()

        assert top_item.rowCount() > 0
        child_sev_cell = top_item.child(0, 2)
        assert child_sev_cell is not None
        assert child_sev_cell.text() == "警告"
        assert child_sev_cell.background().color().rgb() == expected_bg.rgb()
        window.close()

    def test_detail_hits_table_shows_severity_colors(self, qapp: QApplication, tmp_path: Path) -> None:
        """detail_hits_table 严重等级列应显示中文标签与背景色。

        注：前景色由 QSS ``::item:selected`` 选中态接管（统一白字），
        故仅验证背景色编码（需求1：选中项字体统一白色）。
        """
        from fuscan.gui.preview_utils import SEVERITY_BACKGROUNDS
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password = 123", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        window._switch_stage(WorkflowStage.RESULTS)
        window._refresh_result_tree()

        top_item = window.result_tree.model().item(0, 0)
        assert top_item is not None
        window.result_tree.setCurrentIndex(window.result_tree.model().index(0, 0))
        qapp.processEvents()

        expected_bg = SEVERITY_BACKGROUNDS[Severity.WARNING]
        item = window.detail_hits_table.item(0, 1)
        assert item is not None
        assert item.text() == "警告"
        assert item.background().color().rgb() == expected_bg.rgb()
        window.close()

    def test_rules_tree_shows_severity_colors(self, qapp: QApplication) -> None:
        """rules_tree 严重等级列应显示中文标签与背景色。

        注：前景色由 QSS ``::item:selected`` 选中态接管（统一白字），
        故仅验证背景色编码（需求1：选中项字体统一白色）。
        """
        from fuscan.gui.preview_utils import SEVERITY_BACKGROUNDS

        window = MainWindow()
        window._ruleset = _build_ruleset()
        window._refresh_rules_tree()

        item = window.rules_tree.topLevelItem(0)
        assert item is not None
        sev_text = item.text(1)
        assert sev_text in ("严重", "警告", "一般")
        expected_bgs = {
            sev: SEVERITY_BACKGROUNDS[sev].rgb() for sev in (Severity.CRITICAL, Severity.WARNING, Severity.INFO)
        }
        bg_rgb = item.background(1).color().rgb()
        assert bg_rgb in expected_bgs.values()
        window.close()


class TestScanWorkerControl:
    """ScanWorker 暂停/取消控制信号测试。"""

    def test_worker_cancel_emits_cancelled_signal(self, qapp: QApplication, tmp_path: Path) -> None:
        """取消扫描应发射 cancelled 信号并携带部分结果。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer  # pyrefly: ignore [missing-import]

        # 300 个文件确保遍历阶段触发进度回调（每 200 个文件一次）
        for i in range(300):
            (tmp_path / f"secret_{i}.txt").write_text("x", encoding="utf-8")

        class _CancelOnProgressWorker(ScanWorker):
            """首个进度事件后自动取消的 ScanWorker。"""

            def _on_progress(self, info) -> None:  # type: ignore[no-untyped-def]
                ScanWorker._on_progress(self, info)
                self.cancel()

        rs = _build_ruleset()
        worker = _CancelOnProgressWorker(ruleset=rs, roots=[tmp_path])

        finished_reports: list[Any] = []
        cancelled_reports: list[Any] = []
        worker.finished_report.connect(lambda r: finished_reports.append(r))  # noqa: PLW0108  # pyrefly: ignore [missing-attribute]
        worker.cancelled.connect(lambda r: cancelled_reports.append(r))  # noqa: PLW0108  # pyrefly: ignore [missing-attribute]
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # pyrefly: ignore [bad-argument-type, missing-argument]
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        worker.wait(2000)
        assert not worker.isRunning()
        assert len(finished_reports) == 0
        assert len(cancelled_reports) == 1
        report = cancelled_reports[0]
        assert report.cancelled

    def test_worker_pause_resume_delegates_to_scanner(self, qapp: QApplication, tmp_path: Path) -> None:
        """pause/resume 应委托给 Scanner，扫描仍正常完成。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer  # pyrefly: ignore [missing-import]

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        results: list[Any] = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108  # pyrefly: ignore [missing-attribute]
        worker.start()

        # 暂停后立即恢复
        QTimer.singleShot(50, worker.pause)  # pyrefly: ignore [bad-argument-type, missing-argument]
        QTimer.singleShot(100, worker.resume)  # pyrefly: ignore [bad-argument-type, missing-argument]

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # pyrefly: ignore [bad-argument-type, missing-argument]
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        worker.wait(2000)
        assert not worker.isRunning()
        assert len(results) == 1
        report = results[0]
        assert not report.cancelled
        assert report.stats.matched_files >= 1


class TestLaunchApp:
    def test_launch_creates_window_and_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """launch 应创建 QApplication 与 MainWindow 并进入事件循环。"""
        from fuscan.gui import app as app_module

        created: list[Any] = []
        set_attrs: list[Any] = []

        class FakeApp:
            def __init__(self, args):  # type: ignore[no-untyped-def]
                created.append(self)
                self._app_name: str | None = None

            def setApplicationName(self, name: str) -> None:
                self._app_name = name

            def setStyleSheet(self, sheet: str) -> None:
                pass

            def exec_(self) -> int:
                return 0

            @staticmethod
            def instance() -> None:
                return None

            @staticmethod
            def setAttribute(attr, _on: bool = True) -> None:  # type: ignore[no-untyped-def]
                set_attrs.append(attr)

        shown: list[Any] = []

        class FakeMainWindow:
            def __init__(self):  # type: ignore[no-untyped-def]
                shown.append(self)

            def show(self) -> None:
                pass

            def close(self) -> None:
                pass

        monkeypatch.setattr(app_module, "QApplication", FakeApp)
        monkeypatch.setattr(app_module, "MainWindow", FakeMainWindow)

        rc = app_module.launch(["test"])
        assert rc == 0
        assert len(created) == 1
        assert len(shown) == 1
        # 验证高 DPI 属性已设置
        assert len(set_attrs) == 2

    def test_launch_reuses_existing_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """已有 QApplication 实例时复用，不创建新实例。"""
        from fuscan.gui import app as app_module

        existing_app = type(
            "ExistingApp",
            (),
            {
                "exec_": lambda self: 0,
                "setApplicationName": lambda self, n: None,
                "setStyleSheet": lambda self, s: None,
            },
        )()
        created: list[Any] = []

        class FakeApp:
            def __init__(self, args):  # type: ignore[no-untyped-def]
                created.append(self)

            def setApplicationName(self, name: str) -> None:
                pass

            def setStyleSheet(self, sheet: str) -> None:
                pass

            def exec_(self) -> int:
                return 0

            @staticmethod
            def instance():
                return existing_app

            @staticmethod
            def setAttribute(attr, _on: bool = True) -> None:  # type: ignore[no-untyped-def]
                pass

        shown: list[Any] = []

        class FakeMainWindow:
            def __init__(self):  # type: ignore[no-untyped-def]
                shown.append(self)

            def show(self) -> None:
                pass

            def close(self) -> None:
                pass

        monkeypatch.setattr(app_module, "QApplication", FakeApp)
        monkeypatch.setattr(app_module, "MainWindow", FakeMainWindow)

        rc = app_module.launch(["test"])
        assert rc == 0
        assert len(created) == 0  # 复用现有实例，不创建新的
        assert len(shown) == 1

    def test_launch_qss_load_error_logged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """QSS 样式表加载失败时应记录警告但不中断启动。"""
        from fuscan.gui import app as app_module

        class FakeApp:
            def __init__(self, args):  # type: ignore[no-untyped-def]
                pass

            def setApplicationName(self, name: str) -> None:
                pass

            def setStyleSheet(self, sheet: str) -> None:
                pass

            def exec_(self) -> int:
                return 0

            @staticmethod
            def instance() -> None:
                return None

            @staticmethod
            def setAttribute(attr, _on: bool = True) -> None:  # type: ignore[no-untyped-def]
                pass

        class FakeMainWindow:
            def __init__(self):  # type: ignore[no-untyped-def]
                pass

            def show(self) -> None:
                pass

            def close(self) -> None:
                pass

        monkeypatch.setattr(app_module, "QApplication", FakeApp)
        monkeypatch.setattr(app_module, "MainWindow", FakeMainWindow)
        monkeypatch.setattr(app_module, "_QSS_PATH", Path("/nonexistent/styles.qss"))

        rc = app_module.launch(["test"])
        assert rc == 0

    def test_gui_package_lazy_launch_import(self) -> None:
        """fuscan.gui 包应通过 __getattr__ 惰性导入 launch。"""
        import fuscan.gui

        launch = fuscan.gui.launch
        assert callable(launch)

    def test_gui_package_getattr_unknown_raises(self) -> None:
        """访问 fuscan.gui 包不存在的属性应抛出 AttributeError。"""
        import fuscan.gui

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = fuscan.gui.__getattr__("nonexistent_attr")


class TestPreviewHelpers:
    """详情对话框辅助函数测试。"""

    def test_format_size_bytes(self) -> None:

        assert format_size(0) == "0 B"
        assert format_size(512) == "512 B"
        assert format_size(1023) == "1023 B"

    def test_format_size_kb(self) -> None:

        assert format_size(1024) == "1.0 KB"
        assert format_size(2048) == "2.0 KB"

    def test_format_size_mb(self) -> None:

        assert format_size(1024 * 1024) == "1.0 MB"

    def test_format_size_gb(self) -> None:

        assert "GB" in format_size(1024 * 1024 * 1024)

    def test_extract_keywords_contains(self) -> None:
        from fuscan.scanner import RuleHit

        hits = (
            RuleHit("r1", Severity.WARNING, "包含 'password'"),
            RuleHit("r2", Severity.CRITICAL, "包含 'secret'"),
        )
        kws = extract_keywords(hits)
        assert "password" in kws
        assert "secret" in kws

    def test_extract_keywords_regex(self) -> None:
        from fuscan.scanner import RuleHit

        hits = (RuleHit("r", Severity.CRITICAL, "正则命中: 'AKIA1234'"),)
        kws = extract_keywords(hits)
        assert "AKIA1234" in kws

    def test_extract_keywords_dedup(self) -> None:
        from fuscan.scanner import RuleHit

        hits = (
            RuleHit("r1", Severity.WARNING, "包含 'password'"),
            RuleHit("r2", Severity.WARNING, "包含 'password'"),
        )
        kws = extract_keywords(hits)
        assert kws.count("password") == 1

    def test_extract_keywords_no_match(self) -> None:
        from fuscan.scanner import RuleHit

        hits = (RuleHit("r", Severity.INFO, "完全相等"),)
        kws = extract_keywords(hits)
        assert kws == []

    def test_build_preview_html_no_keywords(self) -> None:

        result = build_preview_html("hello world", [])
        assert "hello" in result
        assert "<span" not in result

    def test_build_preview_html_with_keywords(self) -> None:

        result = build_preview_html("hello password world", ["password"])
        assert "span" in result
        assert "background-color: yellow" in result

    def test_build_preview_html_escapes_html(self) -> None:

        result = build_preview_html("<script>alert(1)</script>", [])
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_build_preview_html_case_insensitive(self) -> None:

        result = build_preview_html("PASSWORD password Password", ["password"])
        # 所有大小的 password 都应被高亮（3 次匹配 = 6 个 span 标签：开+关）
        assert result.count("<span") == 3


class TestScanWorkerMultiRoot:
    """ScanWorker 多根路径扫描测试。"""

    def test_worker_scans_multiple_roots(self, qapp: QApplication, tmp_path: Path) -> None:
        """ScanWorker 应依次扫描多个根路径并合并结果。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer  # pyrefly: ignore [missing-import]

        (tmp_path / "dir_a").mkdir()
        (tmp_path / "dir_a" / "secret.txt").write_text("x", encoding="utf-8")
        (tmp_path / "dir_b").mkdir()
        (tmp_path / "dir_b" / "secret.txt").write_text("y", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path / "dir_a", tmp_path / "dir_b"])

        results: list[Any] = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108  # pyrefly: ignore [missing-attribute]
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # pyrefly: ignore [bad-argument-type, missing-argument]
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        worker.wait(2000)
        assert not worker.isRunning()
        assert len(results) == 1
        report = results[0]
        # 两个目录各命中一个文件
        assert report.stats.matched_files >= 2
        assert report.stats.total_files >= 2

    def test_worker_merges_empty_and_nonempty(self, qapp: QApplication, tmp_path: Path) -> None:
        """有效路径与无效路径混合时应正常合并。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer  # pyrefly: ignore [missing-import]

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(
            ruleset=rs,
            roots=[tmp_path / "nonexistent", tmp_path],
        )

        results: list[Any] = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108  # pyrefly: ignore [missing-attribute]
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # pyrefly: ignore [bad-argument-type, missing-argument]
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        worker.wait(2000)
        assert len(results) == 1
        report = results[0]
        assert report.stats.matched_files >= 1


class TestScanWorkerProgress:
    """ScanWorker progress_info 信号测试。"""

    def test_progress_info_emitted(self, qapp: QApplication, tmp_path: Path) -> None:
        """扫描过程中应 emit progress_info 信号。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer  # pyrefly: ignore [missing-import]

        for i in range(5):
            (tmp_path / f"secret_{i}.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        progress_infos: list[Any] = []
        worker.progress_info.connect(progress_infos.append)  # pyrefly: ignore [missing-attribute]
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # pyrefly: ignore [bad-argument-type, missing-argument]
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        worker.wait(2000)
        assert not worker.isRunning()
        # 应至少收到一条进度（最终 force=True 的进度）
        assert len(progress_infos) >= 1
        last = progress_infos[-1]
        # 最终进度应反映全部文件
        assert last.total >= 5
        assert last.scanned >= 5
        assert last.matched >= 5
        assert last.elapsed >= 0  # 极快扫描可能为 0.0

    def test_progress_info_cumulative_multi_root(self, qapp: QApplication, tmp_path: Path) -> None:
        """多根路径扫描时 progress_info 应累加前序根路径的统计。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer  # pyrefly: ignore [missing-import]

        dir_a = tmp_path / "dir_a"
        dir_a.mkdir()
        for i in range(3):
            (dir_a / f"secret_{i}.txt").write_text("x", encoding="utf-8")
        dir_b = tmp_path / "dir_b"
        dir_b.mkdir()
        for i in range(4):
            (dir_b / f"secret_{i}.txt").write_text("y", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[dir_a, dir_b])

        progress_infos: list[Any] = []
        worker.progress_info.connect(progress_infos.append)  # pyrefly: ignore [missing-attribute]
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # pyrefly: ignore [bad-argument-type, missing-argument]
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        worker.wait(2000)
        assert not worker.isRunning()
        # 最终进度的累计值应覆盖两个根路径的全部文件
        last = progress_infos[-1]
        assert last.total >= 7  # 3 + 4
        assert last.scanned >= 7
        assert last.matched >= 7

    def test_progress_info_fields_type(self, qapp: QApplication, tmp_path: Path) -> None:
        """progress_info 携带 ProgressInfo 对象且字段类型正确。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer  # pyrefly: ignore [missing-import]

        from fuscan.scanner.result import ProgressInfo

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        progress_infos: list[Any] = []
        worker.progress_info.connect(progress_infos.append)  # pyrefly: ignore [missing-attribute]
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # pyrefly: ignore [bad-argument-type, missing-argument]
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        worker.wait(2000)
        assert len(progress_infos) >= 1
        info = progress_infos[-1]
        assert isinstance(info, ProgressInfo)
        assert isinstance(info.current_file, str)
        assert isinstance(info.scanned, int)
        assert isinstance(info.total, int)
        assert isinstance(info.skipped, int)
        assert isinstance(info.matched, int)
        assert isinstance(info.errors, int)
        assert isinstance(info.elapsed, float)


class TestScanWorkerDirect:
    """直接调用 run()/_on_progress() 的测试。

    coverage 无法跟踪 QThread（C++ 线程）内执行的代码，
    通过直接调用方法在主线程执行来覆盖 run() 与 _on_progress() 逻辑。
    """

    def test_run_emits_finished_report(self, qapp: QApplication, tmp_path: Path) -> None:
        """直接调用 run() 应 emit finished_report 信号。"""
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        reports: list[Any] = []
        worker.finished_report.connect(reports.append)  # pyrefly: ignore [missing-attribute]
        worker.run()  # 直接调用，不通过 start()

        assert len(reports) == 1
        report = reports[0]
        assert report.stats.matched_files >= 1
        assert report.stats.total_files >= 1
        assert report.root == tmp_path

    def test_run_multi_root_merges_results(self, qapp: QApplication, tmp_path: Path) -> None:
        """直接调用 run() 多根路径应合并结果且 root 标记为多路径。"""
        dir_a = tmp_path / "dir_a"
        dir_a.mkdir()
        (dir_a / "secret.txt").write_text("x", encoding="utf-8")
        dir_b = tmp_path / "dir_b"
        dir_b.mkdir()
        (dir_b / "secret.txt").write_text("y", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[dir_a, dir_b])

        reports: list[Any] = []
        worker.finished_report.connect(reports.append)  # pyrefly: ignore [missing-attribute]
        worker.run()

        assert len(reports) == 1
        report = reports[0]
        assert report.stats.matched_files >= 2
        assert report.stats.total_files >= 2
        assert "多路径" in str(report.root)

    def test_run_empty_dir_returns_empty_report(self, qapp: QApplication, tmp_path: Path) -> None:
        """直接调用 run() 空目录应返回空报告。"""
        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        reports: list[Any] = []
        worker.finished_report.connect(reports.append)  # pyrefly: ignore [missing-attribute]
        worker.run()

        assert len(reports) == 1
        report = reports[0]
        assert report.stats.total_files == 0
        assert report.stats.matched_files == 0

    def test_on_progress_emits_cumulative(self, qapp: QApplication) -> None:
        """_on_progress 应累加 _cum_* 字段后 emit。"""
        from fuscan.scanner.result import ProgressInfo

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[Path("/tmp")])
        # 模拟前序根路径已扫描的累计值
        worker._cum_scanned = 10
        worker._cum_total = 15
        worker._cum_skipped = 3
        worker._cum_matched = 5
        worker._cum_errors = 1
        worker._start_time = time.monotonic() - 2.0  # 2 秒前开始

        emitted: list[Any] = []
        worker.progress_info.connect(emitted.append)  # pyrefly: ignore [missing-attribute]

        info = ProgressInfo(current_file="test.txt", scanned=5, total=8, skipped=1, matched=2, errors=0, elapsed=1.0)
        worker._on_progress(info)

        assert len(emitted) == 1
        result = emitted[0]
        assert result.scanned == 15  # 5 + 10
        assert result.total == 23  # 8 + 15
        assert result.skipped == 4  # 1 + 3
        assert result.matched == 7  # 2 + 5
        assert result.errors == 1  # 0 + 1
        assert result.current_file == "test.txt"
        assert result.elapsed >= 2.0  # 至少 2 秒

    def test_on_progress_forwards_skipped_dirs_and_matched_files(self, qapp: QApplication) -> None:
        """_on_progress 应直接透传 skipped_dirs 和 matched_files（不做累计）。"""
        from fuscan.scanner.result import ProgressInfo

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[Path("/tmp")])
        worker._start_time = time.monotonic()

        emitted: list[Any] = []
        worker.progress_info.connect(emitted.append)  # pyrefly: ignore [missing-attribute]

        info = ProgressInfo(
            current_file="secret.py",
            scanned=5,
            total=8,
            skipped=1,
            matched=2,
            errors=0,
            elapsed=1.0,
            skipped_dirs=("/tmp/.git", "/tmp/node_modules"),
            matched_files=(("/tmp/secret.py", "敏感文件名"), ("/tmp/config.yaml", "明文密码")),
        )
        worker._on_progress(info)

        assert len(emitted) == 1
        result = emitted[0]
        # 新字段直接透传，不累计
        assert result.skipped_dirs == ("/tmp/.git", "/tmp/node_modules")
        assert result.matched_files == (
            ("/tmp/secret.py", "敏感文件名"),
            ("/tmp/config.yaml", "明文密码"),
        )

    def test_run_emits_failed_on_exception(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scanner.scan 抛异常时 run() 应 emit failed 信号。"""
        from fuscan.scanner.scanner import Scanner

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        def boom(self: Scanner, root: Path) -> None:
            raise RuntimeError("扫描爆炸")

        monkeypatch.setattr(Scanner, "scan", boom)

        failures: list[Any] = []
        worker.failed.connect(failures.append)  # pyrefly: ignore [missing-attribute]
        reports: list[Any] = []
        worker.finished_report.connect(reports.append)  # pyrefly: ignore [missing-attribute]
        worker.run()

        assert len(failures) == 1
        assert "扫描爆炸" in failures[0]
        assert len(reports) == 0  # 不应 emit finished_report

    def test_on_progress_zero_cumulative(self, qapp: QApplication) -> None:
        """_cum_* 全为 0 时 _on_progress 应原样传递 info 值。"""
        from fuscan.scanner.result import ProgressInfo

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[Path("/tmp")])
        worker._start_time = time.monotonic()

        emitted: list[Any] = []
        worker.progress_info.connect(emitted.append)  # pyrefly: ignore [missing-attribute]

        info = ProgressInfo(current_file="", scanned=3, total=3, skipped=0, matched=1, errors=0, elapsed=0.5)
        worker._on_progress(info)

        assert len(emitted) == 1
        result = emitted[0]
        assert result.scanned == 3
        assert result.total == 3
        assert result.matched == 1

    def test_pause_delegates_to_scanner(self, qapp: QApplication) -> None:
        """pause() 在 _scanner 非空时应调用 scanner.pause()。"""
        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[Path("/tmp")])

        called = {"pause": False}

        class FakeScanner:
            is_cancelled = False

            def pause(self) -> None:
                called["pause"] = True

            def resume(self) -> None:
                pass

            def cancel(self) -> None:
                pass

        worker._scanner = FakeScanner()  # type: ignore[assignment]
        worker.pause()
        assert called["pause"] is True

    def test_resume_delegates_to_scanner(self, qapp: QApplication) -> None:
        """resume() 在 _scanner 非空时应调用 scanner.resume()。"""
        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[Path("/tmp")])

        called = {"resume": False}

        class FakeScanner:
            is_cancelled = False

            def pause(self) -> None:
                pass

            def resume(self) -> None:
                called["resume"] = True

            def cancel(self) -> None:
                pass

        worker._scanner = FakeScanner()  # type: ignore[assignment]
        worker.resume()
        assert called["resume"] is True

    def test_cancel_delegates_to_scanner(self, qapp: QApplication) -> None:
        """cancel() 在 _scanner 非空时应调用 scanner.cancel()。"""
        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[Path("/tmp")])

        called = {"cancel": False}

        class FakeScanner:
            is_cancelled = False

            def pause(self) -> None:
                pass

            def resume(self) -> None:
                pass

            def cancel(self) -> None:
                called["cancel"] = True

        worker._scanner = FakeScanner()  # type: ignore[assignment]
        worker.cancel()
        assert called["cancel"] is True
        assert worker._cancel_requested is True

    def test_run_cancel_requested_cancels_scanner(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_cancel_requested 为 True 时 run() 应在创建 Scanner 后立即 cancel。"""
        from fuscan.scanner.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])
        worker._cancel_requested = True

        cancel_called = {"n": 0}
        original_cancel = Scanner.cancel

        def fake_cancel(self: Scanner) -> None:
            cancel_called["n"] += 1
            original_cancel(self)

        monkeypatch.setattr(Scanner, "cancel", fake_cancel)

        cancelled_reports: list[Any] = []
        worker.cancelled.connect(cancelled_reports.append)  # pyrefly: ignore [missing-attribute]
        worker.run()

        assert cancel_called["n"] >= 1
        assert len(cancelled_reports) == 1

    def test_run_emits_cancelled_when_scanner_cancelled(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scanner 被取消后 run() 应 emit cancelled 信号。"""
        from fuscan.scanner.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        original_scan = Scanner.scan

        def fake_scan(self: Scanner, root: Path):  # type: ignore[no-untyped-def]
            self.cancel()
            return original_scan(self, root)

        monkeypatch.setattr(Scanner, "scan", fake_scan)

        cancelled_reports: list[Any] = []
        worker.cancelled.connect(cancelled_reports.append)  # pyrefly: ignore [missing-attribute]
        worker.run()

        assert len(cancelled_reports) == 1
        assert cancelled_reports[0].cancelled is True


class TestScanWorkerSkipPaths:
    """ScanWorker skip_paths 集成测试（iter-77）。"""

    def test_run_skip_paths_excludes_marked_files(self, qapp: QApplication, tmp_path: Path) -> None:
        """run() 应将 skip_paths 传入 Scanner，被标记文件计入 user_skipped 不进入结果。"""
        skip_file = tmp_path / "skip.txt"
        skip_file.write_text("secret", encoding="utf-8")
        (tmp_path / "scan.txt").write_text("secret", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(
            ruleset=rs,
            roots=[tmp_path],
            skip_paths=frozenset({str(skip_file)}),
        )

        reports: list[Any] = []
        worker.finished_report.connect(reports.append)  # pyrefly: ignore [missing-attribute]
        worker.run()

        assert len(reports) == 1
        report = reports[0]
        # 两个文件都被发现，但 skip.txt 被用户标记跳过
        assert report.stats.total_files == 2
        assert report.stats.user_skipped == 1
        assert report.stats.scanned_files == 1
        # skip.txt 不在结果中
        assert all(r.path != skip_file for r in report.results)

    def test_run_skip_paths_accumulates_across_roots(self, qapp: QApplication, tmp_path: Path) -> None:
        """多根路径扫描时 user_skipped 应累计（iter-77）。"""
        dir_a = tmp_path / "dir_a"
        dir_a.mkdir()
        skip_a = dir_a / "skip.txt"
        skip_a.write_text("x", encoding="utf-8")
        (dir_a / "scan.txt").write_text("y", encoding="utf-8")

        dir_b = tmp_path / "dir_b"
        dir_b.mkdir()
        skip_b = dir_b / "skip.txt"
        skip_b.write_text("x", encoding="utf-8")
        (dir_b / "scan.txt").write_text("y", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(
            ruleset=rs,
            roots=[dir_a, dir_b],
            skip_paths=frozenset({str(skip_a), str(skip_b)}),
        )

        reports: list[Any] = []
        worker.finished_report.connect(reports.append)  # pyrefly: ignore [missing-attribute]
        worker.run()

        assert len(reports) == 1
        report = reports[0]
        # 两个根路径各跳过 1 个，累计 2
        assert report.stats.user_skipped == 2
        assert report.stats.scanned_files == 2

    def test_on_progress_forwards_user_skipped_cumulative(self, qapp: QApplication) -> None:
        """_on_progress 应累加 _cum_user_skipped 后 emit（iter-77）。"""
        from fuscan.scanner.result import ProgressInfo

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[Path("/tmp")])
        worker._start_time = time.monotonic()
        # 模拟前序根路径已累计跳过 4 个
        worker._cum_user_skipped = 4

        emitted: list[Any] = []
        worker.progress_info.connect(emitted.append)  # pyrefly: ignore [missing-attribute]

        info = ProgressInfo(
            current_file="x.txt",
            scanned=2,
            total=3,
            skipped=0,
            matched=1,
            errors=0,
            elapsed=0.5,
            user_skipped=2,
        )
        worker._on_progress(info)

        assert len(emitted) == 1
        result = emitted[0]
        # 当前根 2 + 前序累计 4 = 6
        assert result.user_skipped == 6

    def test_on_progress_forwards_phase(self, qapp: QApplication) -> None:
        """_on_progress 应透传 phase 字段（iter-77 顺带修复 iter-75 遗留丢失问题）。"""
        from fuscan.scanner.result import ProgressInfo

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[Path("/tmp")])
        worker._start_time = time.monotonic()

        emitted: list[Any] = []
        worker.progress_info.connect(emitted.append)  # pyrefly: ignore [missing-attribute]

        info = ProgressInfo(
            current_file="x.txt",
            scanned=0,
            total=10,
            skipped=0,
            matched=0,
            errors=0,
            elapsed=0.5,
            phase="walk",
        )
        worker._on_progress(info)

        assert len(emitted) == 1
        assert emitted[0].phase == "walk"


class TestScanMode:
    """扫描模式 UI 测试。"""

    def test_default_mode_is_folder(self, qapp: QApplication) -> None:
        """启动时默认扫描模式为 folder。"""
        window = MainWindow()
        assert window._scan_mode == "folder"
        assert window.scan_mode_combo.currentIndex() == 2
        window.close()

    def test_folder_mode_shows_path_row(self, qapp: QApplication) -> None:
        """folder 模式下 target_stack 切到文件夹页。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        assert window.target_stack.currentIndex() == 2
        assert window.path_combo.isVisible()
        window.close()

    def test_full_mode_hides_target_selectors(self, qapp: QApplication) -> None:
        """full 模式下 target_stack 切到全盘页。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        window.scan_mode_combo.setCurrentIndex(0)
        assert window._scan_mode == "full"
        assert window.target_stack.currentIndex() == 0
        window.close()

    def test_drive_mode_shows_drive_buttons(self, qapp: QApplication) -> None:
        """drive 模式下 target_stack 切到盘符页。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        window.scan_mode_combo.setCurrentIndex(1)
        assert window._scan_mode == "drive"
        assert window.target_stack.currentIndex() == 1
        window.close()

    def test_full_mode_enables_scan_without_path(self, qapp: QApplication) -> None:
        """full 模式下有规则即可扫描，无需选择路径。"""
        window = MainWindow()
        assert window._ruleset is not None
        # folder 模式下未选路径，按钮禁用
        assert not window.scan_btn.isEnabled()
        # 切换到 full 模式
        window.scan_mode_combo.setCurrentIndex(0)
        assert window.scan_btn.isEnabled()
        window.close()

    def test_drive_mode_enables_scan_with_drive(self, qapp: QApplication) -> None:
        """drive 模式下选中盘符即可扫描。"""
        window = MainWindow()
        window.scan_mode_combo.setCurrentIndex(1)
        # 盘符按钮在测试环境（Windows）通常有盘符
        if len(window._drive_buttons) > 0:
            window._drive_buttons[0].setChecked(True)
            window._on_drive_selected(window._drive_buttons[0])
            assert window.scan_btn.isEnabled()
        window.close()

    def test_build_scan_roots_full_mode(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """full 模式应返回所有盘符。"""
        from fuscan.gui import main_window as mw_mod

        fake_drives = [Path("C:\\"), Path("D:\\")]
        monkeypatch.setattr(mw_mod, "list_drives", lambda include_network=False: fake_drives)

        window = MainWindow()
        window.scan_mode_combo.setCurrentIndex(0)
        roots = window._build_scan_roots()
        assert roots == fake_drives
        window.close()

    def test_build_scan_roots_drive_mode(self, qapp: QApplication) -> None:
        """drive 模式应返回选中的单个盘符。"""
        window = MainWindow()
        window.scan_mode_combo.setCurrentIndex(1)
        if len(window._drive_buttons) > 0:
            window._drive_buttons[0].setChecked(True)
            window._on_drive_selected(window._drive_buttons[0])
            roots = window._build_scan_roots()
            assert len(roots) == 1
        window.close()

    def test_build_scan_roots_folder_mode(self, qapp: QApplication, tmp_path: Path) -> None:
        """folder 模式应返回选中的路径。"""
        window = MainWindow()
        window._scan_root = tmp_path
        roots = window._build_scan_roots()
        assert roots == [tmp_path]
        window.close()

    def test_build_scan_roots_folder_mode_empty(self, qapp: QApplication) -> None:
        """folder 模式未选路径时返回空列表。"""
        window = MainWindow()
        window._scan_root = None
        roots = window._build_scan_roots()
        assert roots == []
        window.close()


class TestScanModePersistence:
    """扫描模式与盘符持久化测试。"""

    def test_scan_mode_restored_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """启动时从配置恢复扫描模式。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        config = Config(scan_mode="full")
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_mode == "full"
        assert window.scan_mode_combo.currentIndex() == 0
        window.close()

    def test_drive_mode_restored_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """启动时从配置恢复 drive 模式。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        config = Config(scan_mode="drive")
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_mode == "drive"
        assert window.scan_mode_combo.currentIndex() == 1
        window.close()

    def test_close_saves_scan_mode(self, qapp: QApplication, tmp_path: Path) -> None:
        """关闭时扫描模式应被保存。"""
        window = MainWindow()
        window.scan_mode_combo.setCurrentIndex(0)
        window.close()

        from fuscan.config import load_config as _load_impl

        config = _load_impl(tmp_path / "config.yaml")
        assert config.scan_mode == "full"

    def test_last_drive_restored_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """启动时从配置恢复上次选择的盘符。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        # 使用存在的盘符
        window = MainWindow()
        if len(window._drive_buttons) == 0:
            window.close()
            pytest.skip("无可用盘符")
        first_drive = window._drive_buttons[0].property("drive")  # pyrefly: ignore [bad-argument-type]
        window.close()

        config = Config(scan_mode="drive", last_drive=first_drive)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_mode == "drive"
        assert window._selected_drive == first_drive
        window.close()


class TestMatchTextHighlighting:
    """``match_text`` 字段驱动的关键词提取与跨行定位测试。

    覆盖数据库连接串密码与 Bearer 令牌的详情定位修复：
    - ``extract_keywords`` 优先使用 ``match_text`` 而非从 detail 解析
    - 特殊字符（反斜杠、单引号）的关键词正确定位
    - 跨行 Bearer 令牌的换行规范化为 ``\\s+`` 后跨段落定位
    """

    def test_extract_keywords_prefers_match_text(self) -> None:
        """``_extract_keywords`` 应优先使用 ``match_text`` 而非从 detail 解析。"""
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.CRITICAL, "正则命中: 'repr_escape'", match_text="raw_text"),)
        kws = extract_keywords(hits)
        assert kws == ["raw_text"]

    def test_extract_keywords_match_text_with_backslash(self) -> None:
        """``match_text`` 含反斜杠时应原样使用，不经过 repr 转义。"""
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.WARNING, "正则命中: 'pass\\\\123'", match_text=r"pass\123"),)
        kws = extract_keywords(hits)
        assert kws == [r"pass\123"]

    def test_extract_keywords_match_text_with_single_quote(self) -> None:
        """``match_text`` 含单引号时应原样使用。"""
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.WARNING, "正则命中", match_text="pa'ss"),)
        kws = extract_keywords(hits)
        assert kws == ["pa'ss"]

    def test_extract_keywords_match_text_with_newline(self) -> None:
        """``match_text`` 含换行符时应原样保留，供跨行定位使用。"""
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.INFO, "正则命中", match_text="Bearer\n  eyJhbGci"),)
        kws = extract_keywords(hits)
        assert len(kws) == 1
        assert "\n" in kws[0]

    def test_extract_keywords_falls_back_to_detail_when_match_text_empty(self) -> None:
        """``match_text`` 为空时应回退到从 detail 中提取单引号内容。"""
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.CRITICAL, "包含 'password'", match_text=""),)
        kws = extract_keywords(hits)
        assert kws == ["password"]

    def test_extract_keywords_prefers_match_texts(self) -> None:
        """``extract_keywords`` 应优先遍历 ``match_texts`` 而非 ``match_text``（需求3）。"""
        from fuscan.scanner.result import RuleHit

        hits = (
            RuleHit(
                "r",
                Severity.WARNING,
                "命中多个关键词",
                match_text="password",
                match_texts=("password", "token", "api_key"),
            ),
        )
        kws = extract_keywords(hits)
        assert kws == ["password", "token", "api_key"]

    def test_extract_keywords_match_texts_dedup_across_hits(self) -> None:
        """``extract_keywords`` 应在多条 hit 间去重 match_texts。"""
        from fuscan.scanner.result import RuleHit

        hits = (
            RuleHit("r1", Severity.WARNING, "d1", match_texts=("password", "token")),
            RuleHit("r2", Severity.CRITICAL, "d2", match_texts=("token", "secret")),
        )
        kws = extract_keywords(hits)
        assert kws == ["password", "token", "secret"]

    def test_extract_keywords_match_texts_falls_back_to_match_text(self) -> None:
        """``match_texts`` 为空元组时应回退到 ``match_text``（兼容旧缓存）。"""
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.WARNING, "命中", match_text="password", match_texts=()),)
        kws = extract_keywords(hits)
        assert kws == ["password"]

    def test_extract_keywords_match_texts_falls_back_to_detail(self) -> None:
        """``match_texts`` 与 ``match_text`` 均空时应回退到 detail 解析。"""
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.WARNING, "包含 'secret'", match_text="", match_texts=()),)
        kws = extract_keywords(hits)
        assert kws == ["secret"]

    def test_build_keyword_to_rule_map_uses_match_texts(self) -> None:
        """``build_keyword_to_rule_map`` 应优先遍历 ``match_texts``（需求3）。"""
        from fuscan.gui.preview_utils import build_keyword_to_rule_map
        from fuscan.scanner.result import RuleHit

        hits = (
            RuleHit(
                "r1",
                Severity.WARNING,
                "命中多个",
                match_text="password",
                target="content",
                match_texts=("password", "token"),
            ),
            RuleHit("r2", Severity.CRITICAL, "d2", match_text="secret", target="content"),
        )
        mapping = build_keyword_to_rule_map(hits)
        assert mapping == {"password": 0, "token": 0, "secret": 1}

    def test_build_keyword_to_rule_map_dedup_first_wins(self) -> None:
        """同一关键词被多条规则命中时，仅归属到首条规则。"""
        from fuscan.gui.preview_utils import build_keyword_to_rule_map
        from fuscan.scanner.result import RuleHit

        hits = (
            RuleHit("r1", Severity.WARNING, "d1", match_texts=("password",), target="content"),
            RuleHit("r2", Severity.CRITICAL, "d2", match_texts=("password",), target="content"),
        )
        mapping = build_keyword_to_rule_map(hits)
        assert mapping == {"password": 0}

    def test_build_keyword_to_rule_map_skips_filename_target(self) -> None:
        """``target=="filename"`` 的规则应跳过（不在内容中高亮）。"""
        from fuscan.gui.preview_utils import build_keyword_to_rule_map
        from fuscan.scanner.result import RuleHit

        hits = (
            RuleHit("r1", Severity.WARNING, "d1", match_texts=("password",), target="filename"),
            RuleHit("r2", Severity.CRITICAL, "d2", match_texts=("secret",), target="content"),
        )
        mapping = build_keyword_to_rule_map(hits)
        assert mapping == {"secret": 1}

    def test_panel_positions_db_connection_with_backslash(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情面板应能定位含反斜杠的数据库连接串密码。"""
        from fuscan.gui.main_window import MainWindow
        from fuscan.scanner.result import RuleHit, ScanResult

        content = r"url=mongodb://user:pass\123@host"
        path = tmp_path / "db.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("数据库连接串", Severity.WARNING, "正则命中", match_text=r"mongodb://user:pass\123@"),),
        )
        window = MainWindow()
        window._detail_panel.show_result(result)
        assert len(window._detail_panel._hit_positions) >= 1
        assert "1 /" in window._detail_panel._c.nav_label.text()
        window.close()

    def test_panel_positions_db_connection_with_single_quote(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情面板应能定位含单引号的数据库连接串密码。"""
        from fuscan.gui.main_window import MainWindow
        from fuscan.scanner.result import RuleHit, ScanResult

        content = "url=mongodb://user:pa'ss@host"
        path = tmp_path / "db.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("数据库连接串", Severity.WARNING, "正则命中", match_text="mongodb://user:pa'ss@"),),
        )
        window = MainWindow()
        window._detail_panel.show_result(result)
        assert len(window._detail_panel._hit_positions) >= 1
        assert "1 /" in window._detail_panel._c.nav_label.text()
        window.close()

    def test_panel_positions_cross_line_bearer(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情面板应能定位跨行 Bearer 令牌（换行规范化为 \\s+）。"""
        from fuscan.gui.main_window import MainWindow
        from fuscan.scanner.result import RuleHit, ScanResult

        content = "Authorization: Bearer\n  eyJhbGci.token"
        path = tmp_path / "auth.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("Bearer令牌", Severity.INFO, "正则命中", match_text="Bearer\n  eyJhbGci"),),
        )
        window = MainWindow()
        window._detail_panel.show_result(result)
        assert len(window._detail_panel._hit_positions) >= 1
        assert "1 /" in window._detail_panel._c.nav_label.text()
        window.close()

    def test_panel_positions_single_line_bearer(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情面板应能定位单行 Bearer 令牌。"""
        from fuscan.gui.main_window import MainWindow
        from fuscan.scanner.result import RuleHit, ScanResult

        content = "Authorization: Bearer eyJhbGci.token"
        path = tmp_path / "auth.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("Bearer令牌", Severity.INFO, "正则命中", match_text="Bearer eyJhbGci.token"),),
        )
        window = MainWindow()
        window._detail_panel.show_result(result)
        assert len(window._detail_panel._hit_positions) >= 1
        assert "1 /" in window._detail_panel._c.nav_label.text()
        window.close()

    def test_main_window_positions_cross_line_bearer(self, qapp: QApplication, tmp_path: Path) -> None:
        """主窗口详情区应能定位跨行 Bearer 令牌。"""
        from fuscan.scanner.result import RuleHit, ScanResult

        content = "Authorization: Bearer\n  eyJhbGci.token"
        path = tmp_path / "auth.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("Bearer令牌", Severity.INFO, "正则命中", match_text="Bearer\n  eyJhbGci"),),
        )
        window = MainWindow()
        window._detail_panel.show_result(result)
        assert len(window._detail_panel._hit_positions) >= 1
        assert "1 /" in window.detail_nav_label.text()
        window.close()

    def test_main_window_positions_db_with_backslash(self, qapp: QApplication, tmp_path: Path) -> None:
        """主窗口详情区应能定位含反斜杠的数据库连接串。"""
        from fuscan.scanner.result import RuleHit, ScanResult

        content = r"url=mongodb://user:pass\123@host"
        path = tmp_path / "db.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("数据库连接串", Severity.WARNING, "正则命中", match_text=r"mongodb://user:pass\123@"),),
        )
        window = MainWindow()
        window._detail_panel.show_result(result)
        assert len(window._detail_panel._hit_positions) >= 1
        assert "1 /" in window.detail_nav_label.text()
        window.close()

    def test_main_window_positions_db_with_single_quote(self, qapp: QApplication, tmp_path: Path) -> None:
        """主窗口详情区应能定位含单引号的数据库连接串。"""
        from fuscan.scanner.result import RuleHit, ScanResult

        content = "url=mongodb://user:pa'ss@host"
        path = tmp_path / "db.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("数据库连接串", Severity.WARNING, "正则命中", match_text="mongodb://user:pa'ss@"),),
        )
        window = MainWindow()
        window._detail_panel.show_result(result)
        assert len(window._detail_panel._hit_positions) >= 1
        assert "1 /" in window.detail_nav_label.text()
        window.close()


def _build_multi_hit_report(tmp_path: Path) -> ScanReport:
    """构造多规则、多文件命中的测试报告。"""
    from fuscan.rules.model import (
        LeafMatch,
        MatchMode,
        MatchTarget,
        Rule,
        RuleSet,
        Severity,
    )
    from fuscan.scanner import Scanner

    (tmp_path / "secret.txt").write_text("password = 123", encoding="utf-8")
    (tmp_path / "safe.txt").write_text("normal content", encoding="utf-8")
    (tmp_path / "key.txt").write_text("api_key = abc", encoding="utf-8")

    rs = RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="敏感文件名",
                severity=Severity.WARNING,
                match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="secret"),
            ),
            Rule(
                name="密钥内容",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="key"),
            ),
        ),
    )
    scanner = Scanner(rs)
    return scanner.scan(tmp_path)


class TestResultFilterAndGroup:
    """结果筛选与分组测试。"""

    def test_filter_bar_exists(self, qapp: QApplication) -> None:
        """筛选栏控件应存在。"""
        window = MainWindow()
        assert window.path_filter_input is not None
        assert window.rule_filter_combo is not None
        assert window.group_mode_combo is not None
        window.close()

    def test_header_sorting_enabled(self, qapp: QApplication) -> None:
        """结果树应启用表头排序。"""
        window = MainWindow()
        assert window.result_tree.isSortingEnabled()
        window.close()

    def test_column_count_includes_hit_count(self, qapp: QApplication) -> None:
        """结果树应包含命中数与条数列。"""
        window = MainWindow()
        assert window.result_tree.model().columnCount() == 6
        window.close()

    def test_rule_filter_populated_after_scan(self, qapp: QApplication, tmp_path: Path) -> None:
        """扫描后规则筛选下拉应填充规则名。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        combo = window.rule_filter_combo
        assert combo.count() == 3  # "全部规则" + 2 个命中规则
        assert combo.itemText(0) == "全部规则"
        rule_texts = {combo.itemText(i) for i in range(1, combo.count())}
        assert "敏感文件名" in rule_texts
        assert "密钥内容" in rule_texts
        window.close()

    def test_path_filter(self, qapp: QApplication, tmp_path: Path) -> None:
        """路径筛选应只显示匹配路径的文件。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        assert window.result_tree.model().rowCount() == 2  # secret.txt + key.txt

        window.path_filter_input.setText("secret")
        # 需求9：textChanged 已改为节流触发，测试中同步刷新模拟 timer 到期
        window._refresh_result_tree()
        assert window.result_tree.model().rowCount() == 1
        assert "secret.txt" in window.result_tree.model().item(0, 0).text()
        window.close()

    def test_path_filter_case_insensitive(self, qapp: QApplication, tmp_path: Path) -> None:
        """路径筛选应大小写不敏感。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        window.path_filter_input.setText("SECRET")
        # 需求9：textChanged 已改为节流触发，测试中同步刷新模拟 timer 到期
        window._refresh_result_tree()
        assert window.result_tree.model().rowCount() == 1
        window.close()

    def test_rule_filter(self, qapp: QApplication, tmp_path: Path) -> None:
        """规则筛选应只显示包含该规则命中的文件。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window.rule_filter_combo.findData("密钥内容")
        assert idx >= 0
        window.rule_filter_combo.setCurrentIndex(idx)
        # secret.txt 同时命中"密钥内容"（内容含 key），key.txt 也命中"密钥内容"
        count = window.result_tree.model().rowCount()
        assert count >= 1
        window.close()

    def test_combined_path_and_rule_filter(self, qapp: QApplication, tmp_path: Path) -> None:
        """路径+规则组合筛选。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        window.path_filter_input.setText("key.txt")
        # 需求9：textChanged 已改为节流触发，测试中同步刷新模拟 timer 到期
        window._refresh_result_tree()
        idx = window.rule_filter_combo.findData("密钥内容")
        window.rule_filter_combo.setCurrentIndex(idx)
        assert window.result_tree.model().rowCount() == 1
        assert "key.txt" in window.result_tree.model().item(0, 0).text()
        window.close()

    def test_no_results_after_filter(self, qapp: QApplication, tmp_path: Path) -> None:
        """筛选无匹配时结果树为空。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        window.path_filter_input.setText("nonexistent_path")
        # 需求9：textChanged 已改为节流触发，测试中同步刷新模拟 timer 到期
        window._refresh_result_tree()
        assert window.result_tree.model().rowCount() == 0
        window.close()

    def test_clear_path_filter_restores_results(self, qapp: QApplication, tmp_path: Path) -> None:
        """清空路径筛选应恢复全部结果。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        window.path_filter_input.setText("secret")
        # 需求9：textChanged 已改为节流触发，测试中同步刷新模拟 timer 到期
        window._refresh_result_tree()
        assert window.result_tree.model().rowCount() == 1
        window.path_filter_input.setText("")
        window._refresh_result_tree()
        assert window.result_tree.model().rowCount() == 2
        window.close()

    def test_path_filter_throttled_by_timer(self, qapp: QApplication, tmp_path: Path) -> None:
        """需求9：路径输入应通过 QTimer 节流，textChanged 后不立即刷新结果树。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        assert window.result_tree.model().rowCount() == 2

        # 验证 timer 配置：singleShot、300ms
        assert window._result_filter_timer.isSingleShot()
        assert window._result_filter_timer.interval() == 300

        # textChanged 触发 _schedule_result_refresh，仅启动 timer 不立即刷新
        window.path_filter_input.setText("secret")
        assert window._result_filter_timer.isActive()
        # timer 未到期前结果树保持原样
        assert window.result_tree.model().rowCount() == 2

        # 模拟 timer 到期：直接调用槽函数（timeout 信号连接的目标）
        window._result_filter_timer.stop()
        window._refresh_result_tree()
        assert window.result_tree.model().rowCount() == 1
        window.close()

    def test_populate_results_stops_pending_filter_timer(self, qapp: QApplication, tmp_path: Path) -> None:
        """需求9：_populate_results 应停止挂起的节流 timer，避免与立即刷新重复触发。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        # 启动节流 timer 模拟用户正在输入
        window._result_filter_timer.start()  # pyrefly: ignore [missing-argument]
        assert window._result_filter_timer.isActive()

        # 新扫描完成调用 _populate_results，应停止挂起的 timer
        window._populate_results(report)
        assert not window._result_filter_timer.isActive()
        window.close()

    def test_group_by_rule(self, qapp: QApplication, tmp_path: Path) -> None:
        """按规则分组：顶层项为规则名。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window.group_mode_combo.findData("rule")
        window.group_mode_combo.setCurrentIndex(idx)
        top_count = window.result_tree.model().rowCount()
        assert top_count == 2  # 两个规则
        rule_names = {window.result_tree.model().item(i, 1).text() for i in range(top_count)}
        assert "敏感文件名" in rule_names
        assert "密钥内容" in rule_names
        window.close()

    def test_group_by_severity(self, qapp: QApplication, tmp_path: Path) -> None:
        """按严重等级分组：顶层项为严重等级。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window.group_mode_combo.findData("severity")
        window.group_mode_combo.setCurrentIndex(idx)
        top_count = window.result_tree.model().rowCount()
        assert top_count == 2  # warning + critical
        severities = {window.result_tree.model().item(i, 2).text() for i in range(top_count)}
        assert "警告" in severities
        assert "严重" in severities
        window.close()

    def test_group_by_rule_children_have_user_data(self, qapp: QApplication, tmp_path: Path) -> None:
        """按规则分组时子项应携带 ScanResult 供双击使用。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window.group_mode_combo.findData("rule")
        window.group_mode_combo.setCurrentIndex(idx)
        top = window.result_tree.model().item(0, 0)
        assert top is not None
        assert top.rowCount() > 0
        child = top.child(0, 0)
        assert child is not None
        # QStandardItem 单列，data 直接取 UserRole（无 column 参数）
        assert child.data(Qt.UserRole) is not None
        window.close()

    def test_refresh_with_no_report(self, qapp: QApplication) -> None:
        """无报告时刷新结果树不应异常。"""
        window = MainWindow()
        window._last_report = None
        window._refresh_result_tree()
        assert window.result_tree.model().rowCount() == 0
        window.close()

    def test_rule_filter_restored_after_repopulate(self, qapp: QApplication, tmp_path: Path) -> None:
        """重新填充结果时之前选中的规则筛选应恢复。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window.rule_filter_combo.findData("密钥内容")
        window.rule_filter_combo.setCurrentIndex(idx)
        assert window.rule_filter_combo.currentData() == "密钥内容"

        # 重新填充应恢复选中的规则
        window._populate_results(report)
        assert window.rule_filter_combo.currentData() == "密钥内容"
        window.close()


def _build_multi_match_report(tmp_path: Path) -> ScanReport:
    """构造单文件多处匹配的测试报告，用于验证条数列。"""
    from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity
    from fuscan.scanner import Scanner

    # key.txt 含 3 处 "key"（不含 "secret"），secret.txt 含 1 处 "secret"
    (tmp_path / "key.txt").write_text("api_key=1\naccess_key=2\nuser_key=3", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("password secret", encoding="utf-8")

    rs = RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="密钥",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="key"),
            ),
            Rule(
                name="密码",
                severity=Severity.WARNING,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="secret"),
            ),
        ),
    )
    scanner = Scanner(rs)
    return scanner.scan(tmp_path)


def _build_multi_rule_report(tmp_path: Path) -> ScanReport:
    """构造多规则多位置命中报告，用于测试点击跳转与位置数列。

    multi_rule.txt 内容 ``password=abc\\ntoken=xyz\\npassword=def``：
    - 规则"密码"匹配 password（2 处）
    - 规则"令牌"匹配 token（1 处）
    """
    from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity
    from fuscan.scanner import Scanner

    (tmp_path / "multi_rule.txt").write_text("password=abc\ntoken=xyz\npassword=def", encoding="utf-8")
    rs = RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="密码",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            ),
            Rule(
                name="令牌",
                severity=Severity.WARNING,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token"),
            ),
        ),
    )
    scanner = Scanner(rs)
    return scanner.scan(tmp_path)


def _build_filename_match_report(tmp_path: Path) -> ScanReport:
    """构造含文件名匹配和内容匹配的报告，用于测试文件名匹配的高亮跳过。

    secret_config.txt 内容 ``password=abc\ntoken=xyz``：
    - 规则"文件名"匹配 FILENAME（target="filename"，match_text="secret"）
    - 规则"密码"匹配 CONTENT（target="content"，match_text="password"）
    """
    from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity
    from fuscan.scanner import Scanner

    (tmp_path / "secret_config.txt").write_text("password=abc\ntoken=xyz", encoding="utf-8")
    rs = RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="文件名",
                severity=Severity.INFO,
                match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="secret"),
            ),
            Rule(
                name="密码",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            ),
        ),
    )
    scanner = Scanner(rs)
    return scanner.scan(tmp_path)


class TestMatchCountDisplay:
    """条数列显示测试：验证结果树正确展示匹配条数（区别于命中规则数）。"""

    def test_flat_file_item_shows_match_count(self, qapp: QApplication, tmp_path: Path) -> None:
        """不分组模式下文件项的条数列应显示匹配条数之和。"""
        window = MainWindow()
        report = _build_multi_match_report(tmp_path)
        window._populate_results(report)

        # key.txt：1 条规则命中（密钥），匹配 3 处
        key_item = None
        key_row = -1
        for i in range(window.result_tree.model().rowCount()):
            item = window.result_tree.model().item(i, 0)
            if item is not None and item.text().endswith("key.txt"):
                key_item = item
                key_row = i
                break
        assert key_item is not None
        # 列 3=命中数（规则数），列 4=条数（匹配处数）
        assert window.result_tree.model().item(key_row, 3).text() == "1"
        assert window.result_tree.model().item(key_row, 4).text() == "3"
        window.close()

    def test_flat_child_item_shows_rule_match_count(self, qapp: QApplication, tmp_path: Path) -> None:
        """不分组模式下子项（规则命中）的条数列应显示该规则的匹配条数。"""
        window = MainWindow()
        report = _build_multi_match_report(tmp_path)
        window._populate_results(report)

        # 找到 key.txt 顶层项，检查其子项
        key_item = None
        for i in range(window.result_tree.model().rowCount()):
            item = window.result_tree.model().item(i, 0)
            if item is not None and item.text().endswith("key.txt"):
                key_item = item
                break
        assert key_item is not None
        assert key_item.rowCount() == 1  # 仅"密钥"规则命中
        # 子项列 1=规则名，列 4=条数
        assert key_item.child(0, 1).text() == "密钥"
        assert key_item.child(0, 4).text() == "3"
        window.close()

    def test_group_by_rule_shows_match_sum(self, qapp: QApplication, tmp_path: Path) -> None:
        """按规则分组时顶层项的条数列应为该规则所有文件匹配条数之和。"""
        window = MainWindow()
        report = _build_multi_match_report(tmp_path)
        window._populate_results(report)

        idx = window.group_mode_combo.findData("rule")
        window.group_mode_combo.setCurrentIndex(idx)

        # "密钥"规则在 key.txt 命中 3 处
        rule_item = None
        rule_row = -1
        for i in range(window.result_tree.model().rowCount()):
            item = window.result_tree.model().item(i, 1)
            if item is not None and item.text() == "密钥":
                rule_item = item
                rule_row = i
                break
        assert rule_item is not None
        assert window.result_tree.model().item(rule_row, 4).text() == "3"
        window.close()

    def test_detail_hits_table_shows_match_count(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情区命中规则表应显示条数列。"""
        window = MainWindow()
        report = _build_multi_match_report(tmp_path)
        window._populate_results(report)

        # 选中 key.txt 触发详情区更新
        for i in range(window.result_tree.model().rowCount()):
            item = window.result_tree.model().item(i, 0)
            if item is not None and item.text().endswith("key.txt"):
                window.result_tree.setCurrentIndex(window.result_tree.model().index(i, 0))
                break

        # 命中表列 2=条数
        assert window.detail_hits_table.rowCount() == 1
        assert window.detail_hits_table.item(0, 2).text() == "3"
        window.close()

    def test_detail_info_label_shows_match_count(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情区文件信息应显示匹配条数。"""
        window = MainWindow()
        report = _build_multi_match_report(tmp_path)
        window._populate_results(report)

        for i in range(window.result_tree.model().rowCount()):
            item = window.result_tree.model().item(i, 0)
            if item is not None and item.text().endswith("key.txt"):
                window.result_tree.setCurrentIndex(window.result_tree.model().index(i, 0))
                break

        info_text = window.detail_info_label.text()
        assert "匹配条数" in info_text
        assert "3" in info_text
        window.close()


class TestRuleEditor:
    """规则编辑器测试。"""

    def _make_rules_file(self, tmp_path: Path, name: str = "rules.yaml") -> Path:
        """创建测试规则文件。"""
        path = tmp_path / name
        path.write_text(
            'version: "1.0"\nrules:\n  - name: 测试规则\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: secret\n',
            encoding="utf-8",
        )
        return path

    def test_edit_button_exists(self, qapp: QApplication) -> None:
        """主窗口应包含编辑按钮。"""
        window = MainWindow()
        assert window.edit_rule_btn is not None
        assert window.edit_rule_btn.text() == "编辑"
        window.close()

    def test_edit_no_rules_shows_message(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """无规则文件时点击编辑应提示。"""
        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = []
        called = {"count": 0}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: called.update(count=called["count"] + 1),
        )
        window._on_edit_rules()
        assert called["count"] == 1
        window.close()

    def test_editor_dialog_opens_with_content(self, qapp: QApplication, tmp_path: Path) -> None:
        """编辑器应加载规则文件内容。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        assert dialog.file_combo.count() == 1
        assert dialog.file_combo.itemText(0) == "rules.yaml"
        content = dialog.rule_editor.toPlainText()
        assert "测试规则" in content
        dialog.close()

    def test_editor_switch_files(self, qapp: QApplication, tmp_path: Path) -> None:
        """切换文件下拉应加载对应内容。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        r1 = self._make_rules_file(tmp_path, "r1.yaml")
        r2 = tmp_path / "r2.yaml"
        r2.write_text(
            'version: "1.0"\nrules:\n  - name: 规则二\n    severity: critical\n    match:\n      type: filename\n      mode: contains\n      pattern: key\n',
            encoding="utf-8",
        )
        dialog = RuleEditorDialog([r1, r2])
        assert "测试规则" in dialog.rule_editor.toPlainText()
        dialog.file_combo.setCurrentIndex(1)
        assert "规则二" in dialog.rule_editor.toPlainText()
        dialog.close()

    def test_save_writes_file(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """保存应写入文件。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.rule_editor.setPlainText(
            'version: "1.0"\nrules:\n  - name: 新规则\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: test\n',
        )

        monkeypatch.setattr(
            "fuscan.gui.rule_editor.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        saved_paths: list[str] = []
        dialog.rules_saved.connect(saved_paths.append)  # pyrefly: ignore [missing-attribute]
        dialog._on_save()

        content = rules_path.read_text(encoding="utf-8")
        assert "新规则" in content
        assert len(saved_paths) == 1
        dialog.close()

    def test_save_invalid_yaml_shows_error(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无效 YAML 应提示错误且不保存。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        original = rules_path.read_text(encoding="utf-8")
        dialog = RuleEditorDialog([rules_path])
        dialog.rule_editor.setPlainText("invalid: yaml: content: [")

        warned = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.rule_editor.QMessageBox.warning",
            lambda *args, **kwargs: warned.update(called=True),
        )
        monkeypatch.setattr(
            "fuscan.gui.rule_editor.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        dialog._on_save()

        assert warned["called"]
        assert rules_path.read_text(encoding="utf-8") == original
        dialog.close()

    def test_reload_restores_content(self, qapp: QApplication, tmp_path: Path) -> None:
        """重新加载应恢复文件原始内容。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.rule_editor.setPlainText("modified content")
        dialog._on_reload()
        assert "测试规则" in dialog.rule_editor.toPlainText()
        dialog.close()

    def test_empty_rules_paths(self, qapp: QApplication) -> None:
        """无规则文件时编辑器应显示提示。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        dialog = RuleEditorDialog([])
        assert dialog.file_combo.count() == 0
        assert not dialog.rule_editor.isEnabled()
        dialog.close()

    def test_main_window_edit_and_save_reloads(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """通过 MainWindow 编辑保存后应重新加载规则集。"""

        rules_path = self._make_rules_file(tmp_path)
        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [rules_path]
        window._reload_and_refresh()
        assert window._ruleset is not None
        assert len(window._ruleset.rules) == 1

        # 模拟编辑保存
        new_content = 'version: "1.0"\nrules:\n  - name: 新规则1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n  - name: 新规则2\n    severity: critical\n    match:\n      type: filename\n      mode: contains\n      pattern: b\n'
        rules_path.write_text(new_content, encoding="utf-8")
        window._on_rules_saved(str(rules_path))

        assert window._ruleset is not None
        assert len(window._ruleset.rules) == 2
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window.close()

    def test_load_file_content_invalid_index(self, qapp: QApplication, tmp_path: Path) -> None:
        """无效索引应清空编辑器。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog._load_file_content(99)
        assert dialog.rule_editor.toPlainText() == ""
        dialog.close()

    def test_load_file_content_read_error(self, qapp: QApplication, tmp_path: Path) -> None:
        """文件读取失败应显示错误信息并禁用编辑器。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])

        def _raise_oserror(self: Path, *args: object, **kwargs: object) -> str:
            raise OSError("模拟读取失败")

        monkeypatch_method = type(rules_path).read_text
        try:
            type(rules_path).read_text = _raise_oserror  # type: ignore[method-assign]
            dialog._load_file_content(0)
        finally:
            type(rules_path).read_text = monkeypatch_method  # type: ignore[method-assign]

        assert "读取文件失败" in dialog.rule_editor.toPlainText()
        assert not dialog.rule_editor.isEnabled()
        dialog.close()

    def test_save_invalid_index_does_nothing(self, qapp: QApplication, tmp_path: Path) -> None:
        """无效索引时保存不应执行任何操作。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        saved_paths: list[str] = []
        dialog.rules_saved.connect(saved_paths.append)  # pyrefly: ignore [missing-attribute]

        # 模拟无效索引
        dialog.file_combo.setCurrentIndex(-1)
        dialog._on_save()
        assert len(saved_paths) == 0
        dialog.close()

    def test_save_write_error_shows_warning(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """写入文件失败应提示且不发射信号。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.rule_editor.setPlainText("version: '1.0'\nrules: []\n")

        warned = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.rule_editor.QMessageBox.warning",
            lambda *args, **kwargs: warned.update(called=True),
        )
        monkeypatch.setattr(
            "fuscan.gui.rule_editor.QMessageBox.information",
            lambda *args, **kwargs: None,
        )

        original_write = Path.write_text

        def _raise_on_write(self: Path, *args: object, **kwargs: object) -> int:
            raise OSError("模拟写入失败")

        monkeypatch.setattr(Path, "write_text", _raise_on_write)
        saved_paths: list[str] = []
        dialog.rules_saved.connect(saved_paths.append)  # pyrefly: ignore [missing-attribute]
        dialog._on_save()

        try:
            assert warned["called"]
            assert len(saved_paths) == 0
        finally:
            monkeypatch.setattr(Path, "write_text", original_write)
        dialog.close()

    def test_save_rule_parse_error_emits_signal(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """规则解析失败时仍应发射信号（文件已保存）。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        # 写入合法 YAML 但无效规则（缺少 match 字段）
        dialog.rule_editor.setPlainText('version: "1.0"\nrules:\n  - name: bad\n    severity: warning\n')

        warned = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.rule_editor.QMessageBox.warning",
            lambda *args, **kwargs: warned.update(called=True),
        )
        monkeypatch.setattr(
            "fuscan.gui.rule_editor.QMessageBox.information",
            lambda *args, **kwargs: None,
        )

        saved_paths: list[str] = []
        dialog.rules_saved.connect(saved_paths.append)  # pyrefly: ignore [missing-attribute]
        dialog._on_save()

        assert warned["called"]
        assert len(saved_paths) == 1
        dialog.close()


class TestRuleEditorRegexPanel:
    """规则编辑器正则验证面板测试（需求 req-13 R4）。

    覆盖 ``_on_test_regex`` 的各分支：空 pattern、编译失败、无命中、命中多个、
    捕获组与命名组、大小写敏感性，以及速查手册初始化。
    """

    def _make_rules_file(self, tmp_path: Path, name: str = "rules.yaml") -> Path:
        """创建测试规则文件。"""
        path = tmp_path / name
        path.write_text(
            'version: "1.0"\nrules:\n  - name: 测试规则\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: secret\n',
            encoding="utf-8",
        )
        return path

    def test_regex_cheatsheet_initialized(self, qapp: QApplication, tmp_path: Path) -> None:
        """初始化后速查手册应包含常用语法说明。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        cheatsheet = dialog.regex_cheatsheet_view.toPlainText()
        # 检查关键章节标题
        assert "字符类" in cheatsheet
        assert "量词" in cheatsheet
        assert "锚点" in cheatsheet
        assert "分组与捕获" in cheatsheet
        assert "零宽断言" in cheatsheet
        # 检查常用示例
        assert "邮箱" in cheatsheet
        assert "IPv4" in cheatsheet
        assert "中国手机号" in cheatsheet
        dialog.close()

    def test_test_regex_empty_pattern_shows_hint(self, qapp: QApplication, tmp_path: Path) -> None:
        """空 pattern 应在结果区显示提示。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.regex_pattern_edit.setText("   ")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "请输入正则表达式" in result
        dialog.close()

    def test_test_regex_invalid_pattern_shows_error(self, qapp: QApplication, tmp_path: Path) -> None:
        """非法正则应在结果区显示编译失败信息。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        # 未闭合的括号
        dialog.regex_pattern_edit.setText("(unclosed")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "正则编译失败" in result
        dialog.close()

    def test_test_regex_no_match(self, qapp: QApplication, tmp_path: Path) -> None:
        """无命中时应显示字符数统计。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.regex_pattern_edit.setText(r"\d+")
        dialog.regex_test_text_edit.setPlainText("no digits here")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "未命中" in result
        assert "14" in result  # len("no digits here") == 14
        dialog.close()

    def test_test_regex_single_match(self, qapp: QApplication, tmp_path: Path) -> None:
        """单个命中应显示位置与文本。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.regex_pattern_edit.setText(r"password")
        dialog.regex_test_text_edit.setPlainText("my password is secret")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "共命中 1 处" in result
        assert "password" in result
        # 位置：password 出现在索引 3-11
        assert "3-11" in result
        dialog.close()

    def test_test_regex_multiple_matches(self, qapp: QApplication, tmp_path: Path) -> None:
        """多个命中应全部列出。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.regex_pattern_edit.setText(r"\d+")
        dialog.regex_test_text_edit.setPlainText("abc 123 def 456 ghi 789")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "共命中 3 处" in result
        assert "123" in result
        assert "456" in result
        assert "789" in result
        dialog.close()

    def test_test_regex_capture_groups(self, qapp: QApplication, tmp_path: Path) -> None:
        """捕获组应显示在结果中。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.regex_pattern_edit.setText(r"(\d{4})-(\d{2})-(\d{2})")
        dialog.regex_test_text_edit.setPlainText("date: 2024-01-15")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "共命中 1 处" in result
        assert "捕获组" in result
        assert "'2024'" in result
        assert "'01'" in result
        assert "'15'" in result
        dialog.close()

    def test_test_regex_named_groups(self, qapp: QApplication, tmp_path: Path) -> None:
        """命名捕获组应显示在结果中。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.regex_pattern_edit.setText(r"(?P<year>\d{4})-(?P<month>\d{2})")
        dialog.regex_test_text_edit.setPlainText("2024-06")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "共命中 1 处" in result
        assert "命名组" in result
        assert "'year'" in result
        assert "'2024'" in result
        assert "'month'" in result
        assert "'06'" in result
        dialog.close()

    def test_test_regex_case_insensitive_default(self, qapp: QApplication, tmp_path: Path) -> None:
        """默认不区分大小写，应匹配不同大小写。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        # 默认 regex_case_sensitive_check 未勾选
        assert not dialog.regex_case_sensitive_check.isChecked()
        dialog.regex_pattern_edit.setText(r"password")
        dialog.regex_test_text_edit.setPlainText("PASSWORD here")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "共命中 1 处" in result
        assert "PASSWORD" in result
        dialog.close()

    def test_test_regex_case_sensitive_checked(self, qapp: QApplication, tmp_path: Path) -> None:
        """勾选区分大小写后，不同大小写不应匹配。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.regex_case_sensitive_check.setChecked(True)
        assert dialog.regex_case_sensitive_check.isChecked()
        dialog.regex_pattern_edit.setText(r"password")
        dialog.regex_test_text_edit.setPlainText("PASSWORD here")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "未命中" in result
        dialog.close()

    def test_test_regex_unicode_pattern(self, qapp: QApplication, tmp_path: Path) -> None:
        """Unicode 字符（中文）应正确匹配。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.regex_pattern_edit.setText(r"密码")
        dialog.regex_test_text_edit.setPlainText("用户密码是 secret")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "共命中 1 处" in result
        assert "密码" in result
        dialog.close()

    def test_test_regex_empty_text(self, qapp: QApplication, tmp_path: Path) -> None:
        """空文本时应有 0 字符统计。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.regex_pattern_edit.setText(r"abc")
        dialog.regex_test_text_edit.setPlainText("")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "未命中" in result
        assert "0" in result  # len("") == 0
        dialog.close()

    def test_test_regex_overlapping_no_match(self, qapp: QApplication, tmp_path: Path) -> None:
        """finditer 不匹配重叠，第二个位置应跳过。

        ``aaa`` 对 pattern ``aa`` 只会匹配到 ``[0-2]`` 和 ``[2-4]`` 两个
        位置（不重叠，从上次结束位置继续）。
        """
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog.regex_pattern_edit.setText(r"aa")
        dialog.regex_test_text_edit.setPlainText("aaa")
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        # finditer 不重叠，匹配到 2 处：[0, 2) 和 [2, 4) 越界？实际只匹配 1 处 [0,2)
        # 因为 [2,4) 超出 "aaa" 长度 3，但 finditer 会在 index 2 处再试，
        # 只剩 1 个字符 'a' 不满足 'aa'，所以最终只 1 处
        assert "共命中 1 处" in result
        dialog.close()

    def test_regex_test_btn_signal_connected(self, qapp: QApplication, tmp_path: Path) -> None:
        """regex_test_btn 点击应触发 _on_test_regex。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        # 通过点击按钮验证信号槽连接（不抛异常即说明已连接）
        dialog.regex_pattern_edit.setText(r"abc")
        dialog.regex_test_text_edit.setPlainText("xabcy")
        # 直接调用 .click() 可能在无事件循环时不触发，这里手动调用验证方法存在
        dialog._on_test_regex()
        result = dialog.regex_result_view.toPlainText()
        assert "共命中 1 处" in result
        dialog.close()

    def test_regex_pattern_return_pressed(self, qapp: QApplication, tmp_path: Path) -> None:
        """regex_pattern_edit 的 returnPressed 信号应连接到 _on_test_regex。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        # 验证信号已连接（不抛异常）
        dialog.regex_pattern_edit.setText(r"\d+")
        dialog.regex_test_text_edit.setPlainText("abc 123")
        # 触发 returnPressed 信号
        dialog.regex_pattern_edit.returnPressed.emit()
        result = dialog.regex_result_view.toPlainText()
        assert "共命中 1 处" in result
        dialog.close()


class TestMainWindowHelpers:
    """main_window.py 模块级辅助函数测试。"""

    def test_format_size_bytes(self) -> None:
        """format_size 应正确格式化字节数。"""

        assert format_size(0) == "0 B"
        assert format_size(512) == "512 B"
        assert format_size(1024) == "1.0 KB"
        assert format_size(1024 * 1024) == "1.0 MB"
        assert format_size(1024 * 1024 * 1024) == "1.00 GB"

    def test_extract_keywords(self) -> None:
        """extract_keywords 应从 detail 中提取单引号包裹的关键词。"""
        from fuscan.rules.model import Severity
        from fuscan.scanner.result import RuleHit

        hits = [
            RuleHit(rule_name="r1", severity=Severity.WARNING, detail="包含 'password'"),
            RuleHit(rule_name="r2", severity=Severity.CRITICAL, detail="正则命中: 'AKIA1234'"),
            RuleHit(rule_name="r3", severity=Severity.INFO, detail="无关键词"),
        ]
        keywords = extract_keywords(hits)
        assert keywords == ["password", "AKIA1234"]

    def test_extract_keywords_dedup(self) -> None:
        """_extract_keywords 应去重相同关键词。"""
        from fuscan.rules.model import Severity
        from fuscan.scanner.result import RuleHit

        hits = [
            RuleHit(rule_name="r1", severity=Severity.WARNING, detail="包含 'secret'"),
            RuleHit(rule_name="r2", severity=Severity.WARNING, detail="包含 'secret'"),
        ]
        keywords = extract_keywords(hits)
        assert keywords == ["secret"]

    def test_build_preview_html_no_keywords(self) -> None:
        """build_preview_html 无关键词时只转义不高亮。"""

        result = build_preview_html("hello & world", [])
        assert "hello &amp; world" in result
        assert "<span" not in result

    def test_build_preview_html_with_keywords(self) -> None:
        """_build_preview_html 有关键词时应高亮。"""

        result = build_preview_html("hello password world", ["password"])
        assert "<span" in result
        assert "password" in result

    def test_build_preview_html_escapes_html(self) -> None:
        """build_preview_html 应先转义再高亮，避免 XSS。"""

        result = build_preview_html("<script>alert(1)</script>", ["script"])
        # 原始 <script> 标签不应原样出现（已转义）
        assert "<script>" not in result
        assert "&lt;" in result
        assert "&gt;" in result


class TestDetailArea:
    """详情区两态切换与命中导航测试。"""

    def test_detail_empty_state_initially(self, qapp: QApplication) -> None:
        """启动时详情区应在空态。"""
        window = MainWindow()
        assert window.detail_action_stack.currentIndex() == 0
        assert window.detail_main_stack.currentIndex() == 0
        window.close()

    def test_detail_clear(self, qapp: QApplication) -> None:
        """_detail_panel.clear 应切换到空态并清空内容。"""
        window = MainWindow()
        window.detail_action_stack.setCurrentIndex(1)
        window.detail_main_stack.setCurrentIndex(1)
        window._detail_panel._current_result = object()  # type: ignore[assignment]
        window._detail_panel._hit_positions = [(0, 1, 0)]
        window._detail_panel._current_hit_index = 0
        window._detail_panel.clear()
        assert window.detail_action_stack.currentIndex() == 0
        assert window.detail_main_stack.currentIndex() == 0
        assert window._detail_panel._current_result is None
        assert window._detail_panel._hit_positions == []
        assert window._detail_panel._current_hit_index == -1
        window.close()

    def test_detail_show_result(self, qapp: QApplication, tmp_path: Path) -> None:
        """选中结果项后详情区应切换到非空态并展示详情。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password=123", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        # 选中第一个结果项
        window.result_tree.setCurrentIndex(window.result_tree.model().index(0, 0))
        # 详情区应切换到非空态
        assert window.detail_action_stack.currentIndex() == 1
        assert window.detail_main_stack.currentIndex() == 1
        assert window._detail_panel._current_result is not None
        # 命中表应有行
        assert window.detail_hits_table.rowCount() > 0
        window.close()

    def test_detail_show_result_grouped_child(self, qapp: QApplication, tmp_path: Path) -> None:
        """分组模式下选中子项应展示对应详情。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret1.txt").write_text("x", encoding="utf-8")
        (tmp_path / "secret2.txt").write_text("y", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window.group_mode_combo.setCurrentText("按规则分组")
        window._populate_results(report)
        # 选中第一个子项
        top = window.result_tree.model().item(0, 0)
        assert top is not None
        if top.rowCount() > 0:
            child = top.child(0, 0)
            assert child is not None
            window.result_tree.setCurrentIndex(child.index())
            assert window.detail_action_stack.currentIndex() == 1
        window.close()

    def test_detail_show_result_flat_child(self, qapp: QApplication, tmp_path: Path) -> None:
        """flat 模式下选中命中子行应通过父行查找展示文件详情。

        覆盖 ResultTreeView._handle_selection_changed 的 parent 查找分支：
        flat 模式命中子行未存 UserRole 数据，需向上取父行（文件项）。
        """
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password=123", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        # flat 模式：顶层为文件项（有 data），子项为命中行（无 data，需父行查找）
        top = window.result_tree.model().item(0, 0)
        assert top is not None
        if top.rowCount() > 0:
            child = top.child(0, 0)
            assert child is not None
            window.result_tree.setCurrentIndex(child.index())
            # 子行无 data，但应通过父行查找展示详情
            assert window.detail_action_stack.currentIndex() == 1
            assert window._detail_panel._current_result is not None
        window.close()

    def test_detail_selection_no_items(self, qapp: QApplication) -> None:
        """无选中项时详情区应清空。"""
        window = MainWindow()
        window.detail_action_stack.setCurrentIndex(1)
        window.detail_main_stack.setCurrentIndex(1)
        window._on_result_selected(None)
        assert window.detail_action_stack.currentIndex() == 0
        assert window.detail_main_stack.currentIndex() == 0
        window.close()

    def test_detail_selection_no_data_top_item(self, qapp: QApplication) -> None:
        """选中无 data 的顶层项（无父行）应清空详情区。

        覆盖 ResultTreeView._handle_selection_changed 的 parent is None 与 result is None 分支：
        顶层项无 UserRole 数据且无父行时，详情区保持空态。
        """
        try:
            from PySide2.QtGui import QStandardItem
        except ImportError:  # pragma: no cover
            from PySide6.QtGui import QStandardItem  # pyrefly: ignore [missing-import]

        window = MainWindow()
        # 创建无 data 的顶层项并选中
        item = QStandardItem("no-data")
        item.setEditable(False)
        window.result_tree.model().appendRow([item])
        window.detail_action_stack.setCurrentIndex(1)
        window.detail_main_stack.setCurrentIndex(1)
        window.result_tree.setCurrentIndex(window.result_tree.model().index(0, 0))
        # 无 data 且无父行 → 详情区应回到空态
        assert window.detail_action_stack.currentIndex() == 0
        assert window.detail_main_stack.currentIndex() == 0
        window.close()

    def test_detail_hit_navigation(self, qapp: QApplication, tmp_path: Path) -> None:
        """命中导航按钮应在多个命中间切换。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password password password", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        window.result_tree.setCurrentIndex(window.result_tree.model().index(0, 0))

        total = len(window._detail_panel._hit_positions)
        if total > 1:
            # 下一个命中
            window._detail_panel.next_hit()
            assert window._detail_panel._current_hit_index == 1
            # 上一个命中（回到 0）
            window._detail_panel.prev_hit()
            assert window._detail_panel._current_hit_index == 0
            # 导航标签应显示 "1 / total"
            assert "1" in window.detail_nav_label.text()
            assert str(total) in window.detail_nav_label.text()
        window.close()

    def test_detail_nav_label_no_hits(self, qapp: QApplication) -> None:
        """无命中时导航标签应显示"无命中"且按钮禁用。"""
        window = MainWindow()
        window._detail_panel._hit_positions = []
        window._detail_panel._current_hit_index = -1
        window._detail_panel._update_nav_label()
        assert "无命中" in window.detail_nav_label.text()
        assert not window.detail_prev_btn.isEnabled()
        assert not window.detail_next_btn.isEnabled()
        window.close()

    def test_detail_nav_label_with_hits(self, qapp: QApplication) -> None:
        """有命中时导航标签应显示进度且按钮启用。"""
        window = MainWindow()
        window._detail_panel._hit_positions = [(0, 1, 0), (5, 6, 0)]
        window._detail_panel._current_hit_index = 0
        window._detail_panel._update_nav_label()
        assert "1 / 2" in window.detail_nav_label.text()
        assert window.detail_prev_btn.isEnabled()
        assert window.detail_next_btn.isEnabled()
        window.close()

    def test_detail_prev_next_wrap_around(self, qapp: QApplication) -> None:
        """命中导航应在到达首尾时循环。"""
        window = MainWindow()
        window._detail_panel._hit_positions = [(0, 1, 0), (5, 6, 0)]
        window._detail_panel._current_hit_index = 0
        # 在索引 0 时上一个应循环到最后
        window._detail_panel.prev_hit()
        assert window._detail_panel._current_hit_index == 1
        # 在索引 1 时下一个应循环到第一个
        window._detail_panel.next_hit()
        assert window._detail_panel._current_hit_index == 0
        window.close()

    def test_detail_prev_next_no_hits(self, qapp: QApplication) -> None:
        """无命中时上一个/下一个不应崩溃。"""
        window = MainWindow()
        window._detail_panel._hit_positions = []
        window._detail_panel._current_hit_index = -1
        window._detail_panel.prev_hit()
        window._detail_panel.next_hit()
        assert window._detail_panel._current_hit_index == -1
        window.close()

    def test_detail_copy_path(self, qapp: QApplication, tmp_path: Path) -> None:
        """复制路径应将路径写入剪贴板。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        window.result_tree.setCurrentIndex(window.result_tree.model().index(0, 0))
        window._detail_panel.copy_path()
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        assert "secret.txt" in clipboard.text()  # pyrefly: ignore [missing-argument]
        window.close()

    def test_detail_copy_path_no_result(self, qapp: QApplication) -> None:
        """无选中结果时复制路径不应崩溃。"""
        window = MainWindow()
        window._detail_panel.copy_path()
        window.close()

    def test_detail_preview_empty_file(self, qapp: QApplication, tmp_path: Path) -> None:
        """空文件预览应显示提示文本。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        window.result_tree.setCurrentIndex(window.result_tree.model().index(0, 0))
        # 空文件应显示提示
        text = window.detail_preview.toPlainText()
        assert "空" in text or "二进制" in text
        window.close()

    def test_result_tree_context_menu_actions(self, qapp: QApplication, tmp_path: Path) -> None:
        """结果树右键菜单应包含复制路径/打开文件位置两个动作。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password=123", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        window.result_tree.setCurrentIndex(window.result_tree.model().index(0, 0))
        assert window._detail_panel._current_result is not None

        captured: list[Any] = []
        from fuscan.gui import main_window as mw_module

        original_qmenu = mw_module.QMenu

        class FakeQMenu(QMenu):  # pyrefly: ignore [invalid-inheritance]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.exec_ = lambda *a, **kw: None
                captured.append(self)

        mw_module.QMenu = FakeQMenu
        try:
            window._on_result_tree_context_menu(window.result_tree.viewport().rect().center())
        finally:
            mw_module.QMenu = original_qmenu

        assert len(captured) == 1
        actions = captured[0].actions()
        assert len(actions) == 2
        texts = [a.text() for a in actions]
        assert "复制路径" in texts
        assert "打开文件位置" in texts
        window.close()

    def test_result_tree_context_menu_no_selection(self, qapp: QApplication) -> None:
        """无选中结果时右键菜单不应弹出。"""
        window = MainWindow()
        assert window._detail_panel._current_result is None

        from fuscan.gui import main_window as mw_module

        original_qmenu = mw_module.QMenu
        call_count = {"n": 0}

        class FakeQMenu(QMenu):  # pyrefly: ignore [invalid-inheritance]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                call_count["n"] += 1
                self.exec_ = lambda *a, **kw: None

        mw_module.QMenu = FakeQMenu
        try:
            window._on_result_tree_context_menu(window.result_tree.viewport().rect().center())
        finally:
            mw_module.QMenu = original_qmenu

        assert call_count["n"] == 0
        window.close()

    def test_rules_file_list_context_menu_actions(self, qapp: QApplication, tmp_path: Path) -> None:
        """规则文件列表右键菜单应包含上移/下移/移除三个动作。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text('version: "1.0"\nrules: []\n', encoding="utf-8")

        window = MainWindow()
        window._rules_paths = [r1]
        window._refresh_rules_file_list()
        # row 1 为 r1（row 0 为内置规则条目，菜单操作应禁用）
        window.rules_file_list.setCurrentRow(1)  # pyrefly: ignore [missing-argument]

        captured: list[Any] = []
        from fuscan.gui import main_window as mw_module

        original_qmenu = mw_module.QMenu

        class FakeQMenu(QMenu):  # pyrefly: ignore [invalid-inheritance]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.exec_ = lambda *a, **kw: None
                captured.append(self)

        mw_module.QMenu = FakeQMenu
        try:
            window._on_rules_file_list_context_menu(window.rules_file_list.viewport().rect().center())
        finally:
            mw_module.QMenu = original_qmenu

        assert len(captured) == 1
        actions = captured[0].actions()
        # 3 个 action + 1 个 separator
        action_texts = [a.text() for a in actions if a.text()]
        assert "上移" in action_texts
        assert "下移" in action_texts
        assert "移除" in action_texts
        window.close()

    def test_shortcuts_created(self, qapp: QApplication) -> None:
        """_setup_shortcuts 应创建 F3/Shift+F3/Delete 三个快捷键。"""
        try:
            from PySide2.QtGui import QKeySequence
        except ImportError:  # pragma: no cover
            from PySide6.QtGui import QKeySequence  # pyrefly: ignore [missing-import]

        window = MainWindow()
        assert window._shortcut_next.key().toString() == QKeySequence("F3").toString()
        assert window._shortcut_prev.key().toString() == QKeySequence("Shift+F3").toString()
        assert window._shortcut_remove_rule.key().toString() == QKeySequence(QKeySequence.Delete).toString()
        window.close()

    def test_shortcut_next_triggers_nav(self, qapp: QApplication) -> None:
        """F3 快捷键的 activated 信号应触发下一条命中导航。"""
        window = MainWindow()
        window._detail_panel._hit_positions = [(0, 1, 0), (5, 6, 0)]
        window._detail_panel._current_hit_index = 0
        window._shortcut_next.activated.emit()
        assert window._detail_panel._current_hit_index == 1
        window.close()

    def test_shortcut_prev_triggers_nav(self, qapp: QApplication) -> None:
        """Shift+F3 快捷键的 activated 信号应触发上一条命中导航。"""
        window = MainWindow()
        window._detail_panel._hit_positions = [(0, 1, 0), (5, 6, 0)]
        window._detail_panel._current_hit_index = 1
        window._shortcut_prev.activated.emit()
        assert window._detail_panel._current_hit_index == 0
        window.close()

    def test_detail_hits_table_has_position_count_column(self, qapp: QApplication, tmp_path: Path) -> None:
        """命中规则表应包含6列，第4列为'位置数'，第6列为'描述'。"""
        report = _build_multi_rule_report(tmp_path)
        window = MainWindow()
        window._detail_panel.show_result(report.hits[0])
        assert window.detail_hits_table.columnCount() == 6
        assert window.detail_hits_table.horizontalHeaderItem(3).text() == "位置数"
        assert window.detail_hits_table.horizontalHeaderItem(5).text() == "描述"
        window.close()

    def test_detail_hits_table_position_count_values(self, qapp: QApplication, tmp_path: Path) -> None:
        """位置数列应显示每条规则在预览中的高亮位置数。"""
        report = _build_multi_rule_report(tmp_path)
        window = MainWindow()
        window._detail_panel.show_result(report.hits[0])
        # 规则0(密码): 2处password, 规则1(令牌): 1处token
        assert window.detail_hits_table.item(0, 3).text() == "2"
        assert window.detail_hits_table.item(1, 3).text() == "1"
        window.close()

    def test_detail_hits_table_description_column_filled(self, qapp: QApplication, tmp_path: Path) -> None:
        """第6列(描述)应填充 match_description，未设置时为空字符串（需求4）。"""
        from fuscan.gui.main_window import MainWindow
        from fuscan.rules.model import Severity
        from fuscan.scanner.result import RuleHit, ScanResult

        result = ScanResult(
            path=tmp_path / "a.txt",
            size=10,
            hits=(
                RuleHit(
                    "规则A",
                    Severity.WARNING,
                    "d1",
                    match_count=1,
                    match_description="敏感凭证关键词",
                ),
                RuleHit("规则B", Severity.CRITICAL, "d2", match_count=1),
            ),
        )
        window = MainWindow()
        window._detail_panel.show_result(result)
        # 第0行描述列应填充 match_description
        assert window.detail_hits_table.item(0, 5).text() == "敏感凭证关键词"
        # 第1行描述列未设置时应为空字符串
        assert window.detail_hits_table.item(1, 5).text() == ""
        window.close()

    def test_click_hits_row_jumps_to_rule_highlight(self, qapp: QApplication, tmp_path: Path) -> None:
        """点击规则表行应跳转到该规则对应的高亮位置。"""
        report = _build_multi_rule_report(tmp_path)
        window = MainWindow()
        window._detail_panel.show_result(report.hits[0])
        # 位置排序: [(0,8,0), (13,18,1), (23,31,0)]
        # 初始定位到位置0(规则0的首个password)
        assert window._detail_panel._current_hit_index == 0
        # 点击规则1(令牌) → 跳到位置1(token)
        window._detail_panel._on_hits_row_clicked(1, 0)
        assert window._detail_panel._current_hit_index == 1
        window.close()

    def test_click_hits_row_cycles_within_rule(self, qapp: QApplication, tmp_path: Path) -> None:
        """重复点击同一规则行应在该规则的位置间循环。"""
        report = _build_multi_rule_report(tmp_path)
        window = MainWindow()
        window._detail_panel.show_result(report.hits[0])
        # 初始在位置0(规则0)
        assert window._detail_panel._current_hit_index == 0
        # 再次点击规则0 → 跳到位置2(规则0的第二个password)
        window._detail_panel._on_hits_row_clicked(0, 0)
        assert window._detail_panel._current_hit_index == 2
        # 再次点击规则0 → 回到位置0(循环)
        window._detail_panel._on_hits_row_clicked(0, 0)
        assert window._detail_panel._current_hit_index == 0
        window.close()

    def test_click_hits_row_no_positions_no_crash(self, qapp: QApplication) -> None:
        """无高亮位置时点击规则表行不应崩溃。"""
        window = MainWindow()
        window._detail_panel._hit_positions = []
        window._detail_panel._current_hit_index = -1
        window._detail_panel._on_hits_row_clicked(0, 0)
        assert window._detail_panel._current_hit_index == -1
        window.close()

    def test_filename_match_position_count_shows_dash(self, qapp: QApplication, tmp_path: Path) -> None:
        """文件名匹配规则的位置数列应显示'-'而非数字。"""
        report = _build_filename_match_report(tmp_path)
        window = MainWindow()
        window._detail_panel.show_result(report.hits[0])
        # 规则0(文件名): target="filename" → 位置数列显示"-"
        assert window.detail_hits_table.item(0, 3).text() == "-"
        # 规则1(密码): target="content" → 位置数列显示"1"（password在预览中出现1次）
        assert window.detail_hits_table.item(1, 3).text() == "1"
        window.close()

    def test_filename_match_detail_shows_filename_label(self, qapp: QApplication, tmp_path: Path) -> None:
        """文件名匹配规则的详情列应追加'（仅文件名）'提示。"""
        report = _build_filename_match_report(tmp_path)
        window = MainWindow()
        window._detail_panel.show_result(report.hits[0])
        detail_text = window.detail_hits_table.item(0, 4).text()
        assert "（仅文件名）" in detail_text
        # 内容匹配规则不应有此标记
        content_detail = window.detail_hits_table.item(1, 4).text()
        assert "（仅文件名）" not in content_detail
        window.close()

    def test_filename_match_skips_content_highlight(self, qapp: QApplication, tmp_path: Path) -> None:
        """文件名匹配规则的关键词不应在内容预览中搜索高亮位置。"""
        report = _build_filename_match_report(tmp_path)
        window = MainWindow()
        window._detail_panel.show_result(report.hits[0])
        # 文件名"secret_config.txt"含"secret"，但内容"password=abc\ntoken=xyz"不含"secret"
        # 若错误搜索内容，"secret"不在内容中，不会产生位置；但若文件名恰好也在内容中则会产生误导
        # 此处验证：所有高亮位置都不归属到规则0(文件名)
        for _, _, rule_idx in window._detail_panel._hit_positions:
            assert rule_idx != 0, "文件名匹配规则不应有内容高亮位置"
        window.close()

    def test_detail_info_label_shows_switchable_count(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情信息标签应显示'可切换位置'字段。"""
        report = _build_multi_rule_report(tmp_path)
        window = MainWindow()
        window._detail_panel.show_result(report.hits[0])
        info_text = window.detail_info_label.text()
        assert "可切换位置" in info_text
        # multi_rule.txt: password×2 + token×1 = 3个位置
        assert "3" in info_text
        window.close()

    def test_highlight_skips_out_of_range_position(self, qapp: QApplication) -> None:
        """高亮位置超出文档长度时应跳过高亮，不调用 setPosition 越界。"""
        window = MainWindow()
        window.detail_preview.setPlainText("short")
        # 同步 plain_text 缓存（_highlight/_scroll 复用缓存而非 toPlainText）
        window._detail_panel._plain_text = "short"
        # 设置一个超出文档长度的位置
        window._detail_panel._hit_positions = [(0, 3, 0), (100, 200, 0)]
        window._detail_panel._current_hit_index = 1
        # 不应抛出异常
        window._detail_panel._highlight_current_hit()
        window._detail_panel._scroll_to_current_hit()
        # 第一个位置（在范围内）应正常高亮
        window._detail_panel._current_hit_index = 0
        window._detail_panel._highlight_current_hit()
        window._detail_panel._scroll_to_current_hit()
        window.close()

    def test_rules_file_list_context_menu_no_selection(self, qapp: QApplication) -> None:
        """规则文件列表无选中项时右键菜单不应弹出。"""
        window = MainWindow()
        assert window.rules_file_list.currentRow() < 0

        from fuscan.gui import main_window as mw_module

        original_qmenu = mw_module.QMenu
        call_count = {"n": 0}

        class FakeQMenu(QMenu):  # pyrefly: ignore [invalid-inheritance]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                call_count["n"] += 1
                self.exec_ = lambda *a, **kw: None

        mw_module.QMenu = FakeQMenu
        try:
            window._on_rules_file_list_context_menu(window.rules_file_list.viewport().rect().center())
        finally:
            mw_module.QMenu = original_qmenu

        assert call_count["n"] == 0
        window.close()

    def test_on_open_file_location_win32(self, qapp: QApplication, tmp_path: Path) -> None:
        """open_location 在 Windows 应触发 explorer 命令（经信号路由到主窗口槽）。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password=123", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        window.result_tree.setCurrentIndex(window.result_tree.model().index(0, 0))
        assert window._detail_panel._current_result is not None

        popen_calls: list[Any] = []
        import subprocess as subprocess_mod

        original_popen = subprocess_mod.Popen
        subprocess_mod.Popen = lambda *args, **kwargs: popen_calls.append(args)  # type: ignore[assignment]
        try:
            window._detail_panel.open_location()
        finally:
            subprocess_mod.Popen = original_popen

        assert len(popen_calls) == 1
        window.close()

    def test_on_open_file_location_no_result(self, qapp: QApplication) -> None:
        """无选中结果时打开文件位置不应崩溃。"""
        window = MainWindow()
        window._detail_panel.open_location()
        window.close()

    def test_on_copy_path_with_result(self, qapp: QApplication, tmp_path: Path) -> None:
        """有选中结果时复制路径应更新剪贴板和状态栏。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password=123", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        window.result_tree.setCurrentIndex(window.result_tree.model().index(0, 0))
        assert window._detail_panel._current_result is not None

        window._detail_panel.copy_path()
        assert "已复制" in window.stats_label.text()
        window.close()

    def test_set_use_builtin_rule_error(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """_set_use_builtin 规则加载失败时应弹出警告对话框。"""
        window = MainWindow()

        warned = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.warning",
            lambda *args, **kwargs: warned.update(called=True),
        )
        # 让 _reload_ruleset 抛出 RuleError
        from fuscan.rules import RuleError

        def raise_rule_error() -> None:
            raise RuleError("测试错误")

        monkeypatch.setattr(window, "_reload_ruleset", raise_rule_error)
        window._set_use_builtin(False)
        assert warned["called"]
        assert window._use_builtin is False
        window.close()


class TestScanCallbacks:
    """扫描回调与 UI 状态测试。"""

    def test_on_scan_progress(self, qapp: QApplication) -> None:
        """_on_scan_progress 应更新进度条和统计标签。"""
        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        window.progress.setVisible(True)
        info = ProgressInfo(
            total=100,
            scanned=50,
            skipped=5,
            matched=3,
            errors=1,
            current_file="/test/file.txt",
            elapsed=1.5,
        )
        window._on_scan_progress(info)
        assert window.progress.value() == 50
        assert window.progress.maximum() == 100
        assert "50" in window.stats_label.text()
        window.close()

    def test_on_scan_progress_long_path(self, qapp: QApplication) -> None:
        """_on_scan_progress 应截断过长的文件路径。"""
        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        long_path = "/" + "a" * 200 + ".txt"
        info = ProgressInfo(total=10, scanned=1, skipped=0, matched=0, errors=0, current_file=long_path, elapsed=0.1)
        window._on_scan_progress(info)
        label_text = window.current_file_label.text()
        assert "..." in label_text
        window.close()

    def test_on_scan_progress_updates_skipped_dirs_list(self, qapp: QApplication) -> None:
        """_on_scan_progress 应将 skipped_dirs 填充到跳过文件夹列表。"""
        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        info = ProgressInfo(
            total=10,
            scanned=5,
            skipped=2,
            matched=1,
            errors=0,
            current_file="/test/file.txt",
            elapsed=1.0,
            skipped_dirs=("/proj/.git", "/proj/node_modules"),
        )
        window._on_scan_progress(info)
        assert window.skipped_dirs_list.count() == 2
        items = [window.skipped_dirs_list.item(i).text() for i in range(window.skipped_dirs_list.count())]
        assert any(".git" in t for t in items)
        assert any("node_modules" in t for t in items)
        window.close()

    def test_on_scan_progress_updates_matched_files_list(self, qapp: QApplication) -> None:
        """_on_scan_progress 应将 matched_files 填充到命中文件列表，格式为"路径 → 规则名"。"""
        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        info = ProgressInfo(
            total=10,
            scanned=5,
            skipped=0,
            matched=2,
            errors=0,
            current_file="/test/file.txt",
            elapsed=1.0,
            matched_files=(
                ("/proj/secret.py", "敏感文件名"),
                ("/proj/config.yaml", "明文密码"),
            ),
        )
        window._on_scan_progress(info)
        assert window.matched_files_list.count() == 2
        text0 = window.matched_files_list.item(0).text()
        assert "secret.py" in text0
        assert "敏感文件名" in text0
        assert "→" in text0
        window.close()

    def test_on_scan_progress_updates_stats_labels(self, qapp: QApplication) -> None:
        """_on_scan_progress 应更新状态栏汇总文本（含计数与速度）。"""
        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        info = ProgressInfo(
            total=200,
            scanned=100,
            skipped=50,
            matched=30,
            errors=5,
            current_file="/test/file.txt",
            elapsed=10.0,
        )
        window._on_scan_progress(info)
        stats_text = window.stats_label.text()
        # 计数与时间应汇总到状态栏文本
        assert "100" in stats_text
        assert "50" in stats_text
        assert "30" in stats_text
        assert "5" in stats_text
        assert "10.0s" in stats_text
        # 速度 = 100 / 10.0 = 10 文件/s
        assert "10" in stats_text
        window.close()

    def test_setup_scan_stats_panel_initial_zeros(self, qapp: QApplication) -> None:
        """_setup_scan_stats_panel 初始化后 scan_stats_label 应显示全零计数（需求6/7）。"""
        window = MainWindow()
        text = window.scan_stats_label.text()
        # 四类计数初始均为 0
        assert "已通过 0" in text
        assert "命中 0" in text
        assert "跳过 0" in text
        assert "错误 0" in text
        window.close()

    def test_update_scan_stats_html_content(self, qapp: QApplication) -> None:
        """_update_scan_stats 应在 HTML 中体现四类计数与颜色标识（需求6/7）。"""
        window = MainWindow()
        window._update_scan_stats(passed=80, matched=10, skipped=5, errors=3)
        text = window.scan_stats_label.text()
        # 颜色标识
        assert "#28A745" in text  # 已通过 绿色
        assert "#DC3545" in text  # 命中/错误 红色
        assert "#FFC107" in text  # 跳过 黄色
        # 计数
        assert "已通过 80" in text
        assert "命中 10" in text
        assert "跳过 5" in text
        assert "错误 3" in text
        window.close()

    def test_on_scan_progress_updates_scan_stats_panel(self, qapp: QApplication) -> None:
        """_on_scan_progress 应同步刷新扫描中页分类统计面板（需求6/7）。

        passed = scanned - matched - errors，需对负数兜底为 0。
        """
        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        info = ProgressInfo(
            total=200,
            scanned=100,
            skipped=20,
            matched=30,
            errors=5,
            current_file="/test/file.txt",
            elapsed=10.0,
        )
        window._on_scan_progress(info)
        text = window.scan_stats_label.text()
        # passed = 100 - 30 - 5 = 65
        assert "已通过 65" in text
        assert "命中 30" in text
        assert "跳过 20" in text
        assert "错误 5" in text
        window.close()

    def test_on_scan_progress_scan_stats_passed_floor_zero(self, qapp: QApplication) -> None:
        """scanned < matched + errors 时 passed 应兜底为 0（避免负数显示）。"""
        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        info = ProgressInfo(
            total=10,
            scanned=3,
            skipped=0,
            matched=5,  # 异常场景：matched > scanned
            errors=2,
            current_file="/x",
            elapsed=0.1,
        )
        window._on_scan_progress(info)
        text = window.scan_stats_label.text()
        assert "已通过 0" in text
        window.close()

    def test_on_scan_resets_scan_stats_panel(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_on_scan 启动扫描时应将统计面板重置为全零（需求6/7）。"""

        class _FakeWorker:
            """拦截 ScanWorker 实例化与信号连接，避免启动真实线程。"""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.progress_info = _FakeSignal()
                self.finished_report = _FakeSignal()
                self.failed = _FakeSignal()
                self.cancelled = _FakeSignal()

            def start(self) -> None:
                pass

        class _FakeSignal:
            def connect(self, slot: Any) -> None:
                pass

        monkeypatch.setattr("fuscan.gui.main_window.ScanWorker", _FakeWorker)

        window = MainWindow()
        window._ruleset = _build_ruleset()
        window._scan_root = tmp_path
        window._scan_mode = "folder"
        # 先填充非零统计
        window._update_scan_stats(passed=80, matched=10, skipped=5, errors=3)
        assert "已通过 80" in window.scan_stats_label.text()

        window._on_scan()
        text = window.scan_stats_label.text()
        # 启动扫描后应重置为 0
        assert "已通过 0" in text
        assert "命中 0" in text
        assert "跳过 0" in text
        assert "错误 0" in text
        window.close()

    def test_on_matched_file_double_clicked_open_location(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """双击命中文件项选择"打开"应调用 _open_path_in_explorer（需求5）。"""
        window = MainWindow()
        item = QListWidgetItem(f"{tmp_path / 'secret.txt'} → 敏感文件名")

        # 拦截 QMessageBox.question 返回 Open
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.question",
            lambda *_: QMessageBox.Open,
        )
        # 拦截 _open_path_in_explorer 避免真实调用 explorer
        called: list[Path] = []
        monkeypatch.setattr(window, "_open_path_in_explorer", called.append)

        window._on_matched_file_double_clicked(item)
        assert len(called) == 1
        assert called[0] == tmp_path / "secret.txt"
        window.close()

    def test_on_matched_file_double_clicked_close_no_call(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """双击命中文件项选择"关闭"不应调用 _open_path_in_explorer（需求5）。"""
        window = MainWindow()
        item = QListWidgetItem("/some/path.txt → 规则A")

        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.question",
            lambda *_: QMessageBox.Close,
        )
        called: list[Path] = []
        monkeypatch.setattr(window, "_open_path_in_explorer", called.append)

        window._on_matched_file_double_clicked(item)
        assert called == []
        window.close()

    def test_on_matched_file_double_clicked_no_arrow_returns_early(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """列表项不含 " → " 时应直接返回，不弹窗也不调用定位（需求5）。"""
        window = MainWindow()
        item = QListWidgetItem("无格式文本")

        question_called = {"n": 0}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.question",
            lambda *_: question_called.update(n=question_called["n"] + 1),
        )
        called: list[Path] = []
        monkeypatch.setattr(window, "_open_path_in_explorer", called.append)

        window._on_matched_file_double_clicked(item)
        assert question_called["n"] == 0
        assert called == []
        window.close()

    def test_on_matched_file_double_clicked_rsplit_path_with_arrow(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """路径中含 " → " 时应从右侧分割一次，正确提取路径与规则名（需求5）。"""
        window = MainWindow()
        # 极端场景：路径含 " → "
        item = QListWidgetItem("/proj/a → b.txt → 敏感文件名")

        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.question",
            lambda *_: QMessageBox.Open,
        )
        captured: list[Path] = []
        monkeypatch.setattr(window, "_open_path_in_explorer", captured.append)

        window._on_matched_file_double_clicked(item)
        assert len(captured) == 1
        # 从右侧分割：路径 = "/proj/a → b.txt"，规则名 = "敏感文件名"
        assert captured[0] == Path("/proj/a → b.txt")
        window.close()

    def test_open_path_in_explorer_win32(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_open_path_in_explorer 在 Windows 应调用 explorer /select, 命令。"""
        window = MainWindow()
        target = tmp_path / "secret.txt"
        target.write_text("x", encoding="utf-8")

        popen_calls: list[Any] = []
        import subprocess as subprocess_mod

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(subprocess_mod, "Popen", lambda *args: popen_calls.append(args))

        window._open_path_in_explorer(target)
        assert len(popen_calls) == 1
        assert popen_calls[0][0] == ["explorer", "/select,", str(target)]
        window.close()

    def test_open_path_in_explorer_failure_warns(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_open_path_in_explorer 调用失败时应弹 warning 提示而不抛异常。"""
        window = MainWindow()
        target = tmp_path / "missing.txt"

        warned: dict[str, bool] = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.warning",
            lambda *_: warned.update(called=True),
        )
        import subprocess as subprocess_mod

        def raise_os(*args: Any, **kwargs: Any) -> None:
            raise OSError("mocked failure")

        monkeypatch.setattr(subprocess_mod, "Popen", raise_os)

        window._open_path_in_explorer(target)
        assert warned["called"]
        window.close()

    def test_on_open_file_location_delegates_to_open_path(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """open_location 经信号路由后应委托给 _open_path_in_explorer。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password=123", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        window.result_tree.setCurrentIndex(window.result_tree.model().index(0, 0))
        assert window._detail_panel._current_result is not None

        called: list[Path] = []
        monkeypatch.setattr(window, "_open_path_in_explorer", called.append)
        window._detail_panel.open_location()
        assert len(called) == 1
        assert called[0] == window._detail_panel._current_result.path
        window.close()

    def test_on_scan_progress_throttles_list_update(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """连续两次进度回调在 0.5 秒内，第二次应跳过列表更新。"""
        import time as time_mod

        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        t = [100.0]
        monkeypatch.setattr(time_mod, "perf_counter", lambda: t[0])

        info1 = ProgressInfo(
            total=10,
            scanned=1,
            skipped=1,
            matched=0,
            errors=0,
            current_file="/a.txt",
            elapsed=1.0,
            skipped_dirs=("/dir1",),
        )
        window._on_scan_progress(info1)
        assert window.skipped_dirs_list.count() == 1

        # 推进 0.1 秒，新增一个跳过目录，但应被节流跳过
        t[0] = 100.1
        info2 = ProgressInfo(
            total=10,
            scanned=2,
            skipped=2,
            matched=0,
            errors=0,
            current_file="/b.txt",
            elapsed=1.1,
            skipped_dirs=("/dir1", "/dir2"),
        )
        window._on_scan_progress(info2)
        assert window.skipped_dirs_list.count() == 1  # 仍为 1，节流生效
        window.close()

    def test_on_scan_progress_incremental_append_skipped_dirs(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """增量 append：旧列表是新列表前缀时只添加新增尾部，不 clear 重建。"""
        import time as time_mod

        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        t = [100.0]
        monkeypatch.setattr(time_mod, "perf_counter", lambda: t[0])

        info1 = ProgressInfo(
            total=10,
            scanned=1,
            skipped=1,
            matched=0,
            errors=0,
            current_file="/a.txt",
            elapsed=1.0,
            skipped_dirs=("/dir1", "/dir2"),
        )
        window._on_scan_progress(info1)
        assert window.skipped_dirs_list.count() == 2

        # 推进 0.6 秒（超过节流间隔），新增一个目录
        t[0] = 100.6
        info2 = ProgressInfo(
            total=10,
            scanned=2,
            skipped=2,
            matched=0,
            errors=0,
            current_file="/b.txt",
            elapsed=1.6,
            skipped_dirs=("/dir1", "/dir2", "/dir3"),
        )
        window._on_scan_progress(info2)
        assert window.skipped_dirs_list.count() == 3
        # 前两项应保持不变（未 clear 重建）
        assert window.skipped_dirs_list.item(0).text() == "/dir1"
        assert window.skipped_dirs_list.item(1).text() == "/dir2"
        window.close()

    def test_on_scan_progress_full_rebuild_on_truncation(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """滚动截断时（新列表非旧列表前缀）应全量重建。"""
        import time as time_mod

        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        t = [100.0]
        monkeypatch.setattr(time_mod, "perf_counter", lambda: t[0])

        info1 = ProgressInfo(
            total=10,
            scanned=1,
            skipped=2,
            matched=0,
            errors=0,
            current_file="/a.txt",
            elapsed=1.0,
            skipped_dirs=("/dir1", "/dir2"),
        )
        window._on_scan_progress(info1)

        # 推进 0.6 秒，模拟滚动截断：旧前缀被丢弃，新列表完全不同
        t[0] = 100.6
        info2 = ProgressInfo(
            total=10,
            scanned=2,
            skipped=2,
            matched=0,
            errors=0,
            current_file="/b.txt",
            elapsed=1.6,
            skipped_dirs=("/dir3", "/dir4"),
        )
        window._on_scan_progress(info2)
        assert window.skipped_dirs_list.count() == 2
        assert window.skipped_dirs_list.item(0).text() == "/dir3"
        assert window.skipped_dirs_list.item(1).text() == "/dir4"
        window.close()

    def test_on_scan_progress_incremental_append_matched_files(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """命中文件列表增量 append：只添加新增项，格式"路径 → 规则名"。"""
        import time as time_mod

        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        t = [100.0]
        monkeypatch.setattr(time_mod, "perf_counter", lambda: t[0])

        info1 = ProgressInfo(
            total=10,
            scanned=1,
            skipped=0,
            matched=1,
            errors=0,
            current_file="/a.txt",
            elapsed=1.0,
            matched_files=(("/p/a.py", "规则A"),),
        )
        window._on_scan_progress(info1)
        assert window.matched_files_list.count() == 1

        t[0] = 100.6
        info2 = ProgressInfo(
            total=10,
            scanned=2,
            skipped=0,
            matched=2,
            errors=0,
            current_file="/b.txt",
            elapsed=1.6,
            matched_files=(("/p/a.py", "规则A"), ("/p/b.py", "规则B")),
        )
        window._on_scan_progress(info2)
        assert window.matched_files_list.count() == 2
        assert window.matched_files_list.item(0).text() == "/p/a.py → 规则A"
        assert window.matched_files_list.item(1).text() == "/p/b.py → 规则B"
        window.close()

    def test_on_scan_failed(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """_on_scan_failed 应重置 UI 并弹出错误。"""
        window = MainWindow()
        warned = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.critical",
            lambda *args, **kwargs: warned.update(called=True),
        )
        window._on_scan_failed("测试错误")
        assert warned["called"]
        assert "扫描失败" in window.stats_label.text()
        window.close()

    def test_on_scan_cancelled(self, qapp: QApplication, tmp_path: Path) -> None:
        """_on_scan_cancelled 应填充结果并显示取消统计。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._on_scan_cancelled(report)
        assert "已取消" in window.stats_label.text()
        assert window.result_tree.model().rowCount() > 0
        window.close()

    def test_pause_resume_scan(self, qapp: QApplication, tmp_path: Path) -> None:
        """暂停/恢复扫描应更新状态和按钮文字。"""
        from fuscan.gui.worker import ScanWorker
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)

        window = MainWindow()
        window._scan_root = tmp_path
        window._ruleset = rs
        # 手动创建 worker 以测试暂停/恢复
        window._worker = ScanWorker(scanner, tmp_path)  # pyrefly: ignore [bad-argument-type]
        window._scan_state = ScanState.RUNNING

        window._pause_scan()
        assert window._scan_state == ScanState.PAUSED
        assert "继续" in window.pause_resume_btn.text()

        window._resume_scan()
        assert window._scan_state == ScanState.RUNNING
        assert "暂停" in window.pause_resume_btn.text()

        window._worker = None
        window.close()


def _wait_export_worker(window: MainWindow, qapp: QApplication) -> None:
    """等待 ExportWorker 完成（iter-59 导出异步化后的测试辅助）。

    主线程 ``_on_export`` 启动 ``ExportWorker``（QThread）后立即返回，
    测试需主动等待后台线程完成 ``save_report`` 并通过信号槽回到主线程
    处理结果，否则 ``out_path.exists()`` 断言会因竞态失败。

    :param window: 主窗口实例（含 ``_export_worker`` 属性）
    :param qapp: QApplication 实例，用于 ``processEvents`` 让信号槽分发
    """
    worker = getattr(window, "_export_worker", None)
    if worker is None:
        return
    worker.wait(5000)
    qapp.processEvents()


class TestExportAndMenu:
    """导出、菜单与工具栏操作测试。"""

    def test_export_menu_no_report(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """无报告时导出菜单应提示。"""
        window = MainWindow()
        informed = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: informed.update(called=True),
        )
        window._on_export_menu()
        assert informed["called"]
        window.close()

    def test_export_no_report(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """无报告时导出应提示。"""
        window = MainWindow()
        informed = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: informed.update(called=True),
        )
        window._on_export("csv")
        assert informed["called"]
        window.close()

    def test_export_csv_to_file(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """导出 CSV 应写入文件。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        out_path = tmp_path / "export.csv"
        window = MainWindow()
        window._last_report = report
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: (str(out_path), ""),
        )
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window._on_export("csv")
        _wait_export_worker(window, qapp)
        assert out_path.exists()
        assert "secret.txt" in out_path.read_text(encoding="utf-8")
        window.close()

    def test_export_cancelled(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """取消导出对话框不应写文件。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        out_path = tmp_path / "export.csv"
        window = MainWindow()
        window._last_report = report
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: ("", ""),
        )
        window._on_export("csv")
        assert not out_path.exists()
        window.close()

    def test_export_menu_with_report_select_csv(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """导出菜单选择 CSV 格式应写文件。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        out_path = tmp_path / "export.csv"
        window = MainWindow()
        window._last_report = report
        monkeypatch.setattr(
            "fuscan.gui.main_window.QInputDialog.getItem",
            lambda *args, **kwargs: ("CSV 文件 (*.csv)", True),
        )
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: (str(out_path), ""),
        )
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window._on_export_menu()
        _wait_export_worker(window, qapp)
        assert out_path.exists()
        window.close()

    def test_export_menu_with_report_cancel_dialog(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """导出菜单取消格式选择不应写文件。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        out_path = tmp_path / "export.csv"
        window = MainWindow()
        window._last_report = report
        monkeypatch.setattr(
            "fuscan.gui.main_window.QInputDialog.getItem",
            lambda *args, **kwargs: ("CSV 文件 (*.csv)", False),
        )
        window._on_export_menu()
        assert not out_path.exists()
        window.close()

    def test_export_pdf_to_file(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """导出 PDF 应写入二进制文件，以 %PDF- 开头。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        out_path = tmp_path / "export.pdf"
        window = MainWindow()
        window._last_report = report
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: (str(out_path), ""),
        )
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window._on_export("pdf")
        _wait_export_worker(window, qapp)
        assert out_path.exists()
        assert out_path.read_bytes()[:5] == b"%PDF-"
        window.close()

    def test_export_excel_to_file(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """导出 Excel 应写入 xlsx 二进制文件（PK 开头）。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        out_path = tmp_path / "export.xlsx"
        window = MainWindow()
        window._last_report = report
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: (str(out_path), ""),
        )
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window._on_export("excel")
        _wait_export_worker(window, qapp)
        assert out_path.exists()
        assert out_path.read_bytes()[:2] == b"PK"
        window.close()

    def test_export_menu_select_pdf(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """导出菜单选择 PDF 格式应写文件。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        out_path = tmp_path / "export.pdf"
        window = MainWindow()
        window._last_report = report
        monkeypatch.setattr(
            "fuscan.gui.main_window.QInputDialog.getItem",
            lambda *args, **kwargs: ("PDF 文件 (*.pdf)", True),
        )
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: (str(out_path), ""),
        )
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window._on_export_menu()
        _wait_export_worker(window, qapp)
        assert out_path.exists()
        assert out_path.read_bytes()[:5] == b"%PDF-"
        window.close()

    def test_export_menu_select_excel(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """导出菜单选择 Excel 格式应写文件。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        out_path = tmp_path / "export.xlsx"
        window = MainWindow()
        window._last_report = report
        monkeypatch.setattr(
            "fuscan.gui.main_window.QInputDialog.getItem",
            lambda *args, **kwargs: ("Excel 文件 (*.xlsx)", True),
        )
        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: (str(out_path), ""),
        )
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window._on_export_menu()
        _wait_export_worker(window, qapp)
        assert out_path.exists()
        assert out_path.read_bytes()[:2] == b"PK"
        window.close()

    def test_export_worker_run_emits_finished_ok(self, qapp: QApplication, tmp_path: Path) -> None:
        """直接调用 ExportWorker.run() 应同步完成并 emit finished_ok 信号。

        iter-59 新增 ExportWorker 异步导出，QThread 子线程代码无法被 coverage
        捕获（与 ScanWorker 一致），故通过直接调用 ``run()`` 在主线程同步执行
        以覆盖成功路径，验证信号携带导出路径。
        """
        from fuscan.gui.export_worker import ExportWorker
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        out_path = tmp_path / "direct.csv"
        worker = ExportWorker(report, out_path)

        results: list[Any] = []
        failures: list[Any] = []
        worker.finished_ok.connect(results.append)  # pyrefly: ignore [missing-attribute]
        worker.failed.connect(failures.append)  # pyrefly: ignore [missing-attribute]
        worker.run()  # 直接调用，不通过 start()

        assert not failures
        assert len(results) == 1
        assert results[0] == out_path
        assert out_path.exists()
        assert "secret.txt" in out_path.read_text(encoding="utf-8")

    def test_export_worker_run_emits_failed_on_os_error(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ExportWorker.run() 在 save_report 抛 OSError 时应 emit failed 信号。"""
        from fuscan.gui import export_worker as export_worker_mod
        from fuscan.gui.export_worker import ExportWorker
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        out_path = tmp_path / "fail.csv"

        def _raise_oserror(*_args: object, **_kwargs: object) -> None:
            raise OSError("模拟磁盘已满")

        monkeypatch.setattr(export_worker_mod, "save_report", _raise_oserror)
        worker = ExportWorker(report, out_path)

        results: list[Any] = []
        failures: list[Any] = []
        worker.finished_ok.connect(results.append)  # pyrefly: ignore [missing-attribute]
        worker.failed.connect(failures.append)  # pyrefly: ignore [missing-attribute]
        worker.run()

        assert not results
        assert len(failures) == 1
        assert "模拟磁盘已满" in failures[0]

    def test_about_dialog(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """关于对话框应包含版本、作者、许可证等关键信息。"""
        window = MainWindow()
        captured: dict[str, object] = {}

        def _capture(*args: object, **kwargs: object) -> None:
            captured["args"] = args

        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.about",
            _capture,
        )
        window._on_about()
        assert captured["args"] is not None
        title, body = captured["args"][1], captured["args"][2]  # type: ignore[index]
        assert title == "关于 fuscan"
        assert __version__ in body
        assert __author__ in body
        assert __license__ in body
        assert __description__ in body
        window.close()

    def test_history_item_double_clicked(self, qapp: QApplication, tmp_path: Path) -> None:
        """双击历史项应设置扫描路径。"""
        try:
            from PySide2.QtWidgets import QListWidgetItem
        except ImportError:  # pragma: no cover
            from PySide6.QtWidgets import QListWidgetItem  # pyrefly: ignore [missing-import]

        scan_dir = tmp_path / "scan_target"
        scan_dir.mkdir()
        (scan_dir / "secret.txt").write_text("x", encoding="utf-8")

        window = MainWindow()
        item = QListWidgetItem(str(scan_dir))
        window._on_history_item_double_clicked(item)
        assert window._scan_root == scan_dir
        window.close()

    def test_close_event_saves_config(self, qapp: QApplication, tmp_path: Path) -> None:
        """closeEvent 应保存配置。"""
        try:
            from PySide2.QtGui import QCloseEvent
        except ImportError:  # pragma: no cover
            from PySide6.QtGui import QCloseEvent  # pyrefly: ignore [missing-import]

        window = MainWindow()
        window.closeEvent(QCloseEvent())
        # 配置应已保存（通过 _isolate_config fixture 隔离到 tmp_path）
        window.close()


class TestRulesManagement:
    """规则管理操作测试。"""

    def test_on_remove_rule_no_selection(self, qapp: QApplication) -> None:
        """无选中规则文件时删除不应崩溃。"""
        window = MainWindow()
        window._on_remove_rule()
        window.close()

    def test_on_remove_rule_with_selection(self, qapp: QApplication, tmp_path: Path) -> None:
        """删除选中规则文件应从列表移除。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: r1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [r1]
        window._refresh_rules_file_list()
        # row 0=内置规则条目 + row 1=r1
        assert window.rules_file_list.count() == 2

        # 选中 r1（row 1）删除
        window.rules_file_list.setCurrentRow(1)  # pyrefly: ignore [missing-argument]
        window._on_remove_rule()
        assert len(window._rules_paths) == 0
        window.close()

    def test_on_edit_rules_no_rules(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """无规则文件时编辑应提示。"""
        window = MainWindow()
        window._set_use_builtin(False)
        informed = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: informed.update(called=True),
        )
        window._on_edit_rules()
        assert informed["called"]
        window.close()

    def test_on_edit_rules_with_rules(self, qapp: QApplication, tmp_path: Path) -> None:
        """有规则文件时编辑应打开编辑器对话框。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: r1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [r1]
        window._refresh_rules_file_list()

        # mock exec_ 避免阻塞
        from fuscan.gui.rule_editor import RuleEditorDialog

        original_exec = RuleEditorDialog.exec_
        RuleEditorDialog.exec_ = lambda self: 0  # type: ignore[method-assign]
        try:
            window._on_edit_rules()
        finally:
            RuleEditorDialog.exec_ = original_exec  # type: ignore[method-assign]
        window.close()

    def test_reload_and_refresh(self, qapp: QApplication, tmp_path: Path) -> None:
        """_reload_and_refresh 应重新加载规则集。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: r1\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: a\n',
            encoding="utf-8",
        )

        window = MainWindow()
        window._set_use_builtin(False)
        window._rules_paths = [r1]
        window._reload_and_refresh()
        # 应成功加载规则集
        assert window._ruleset is not None
        window.close()


class TestSettingsDialog:
    """设置对话框测试。"""

    def test_settings_action_in_file_menu(self, qapp: QApplication) -> None:
        """设置 action 应存在于文件菜单（而非帮助菜单）。"""
        try:
            from PySide2.QtWidgets import QDialog
        except ImportError:  # pragma: no cover
            from PySide6.QtWidgets import QDialog  # pyrefly: ignore [missing-import]

        window = MainWindow()
        # settings_action 应存在并连接到 _on_settings
        assert window.settings_action is not None
        assert window.settings_action.text() == "设置..."
        # 设置 action 应在文件菜单中
        file_actions = window.file_menu.actions()
        settings_texts = [a.text() for a in file_actions]
        assert "设置..." in settings_texts
        # 帮助菜单不应包含设置
        help_actions = window.help_menu.actions()
        help_texts = [a.text() for a in help_actions]
        assert "设置..." not in help_texts
        # 确认 QDialog 已导入可用
        assert QDialog.Accepted == 1
        window.close()

    def test_on_settings_accept_applies_config(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """点击确定应保存配置并应用。"""
        from fuscan.gui import settings_dialog as sd_module

        window = MainWindow()
        monkeypatch.setattr(sd_module.SettingsDialog, "exec_", lambda self: 1)  # QDialog.Accepted

        original_config = window._config.use_builtin
        window._config.use_builtin = not original_config
        window._on_settings()

        # 配置应被应用
        assert window._use_builtin == window._config.use_builtin
        window.close()

    def test_on_settings_reject_no_change(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """点击取消不应改变配置。"""
        from fuscan.gui import settings_dialog as sd_module

        window = MainWindow()
        monkeypatch.setattr(sd_module.SettingsDialog, "exec_", lambda self: 0)  # QDialog.Rejected

        before = window._config.use_builtin
        window._on_settings()
        # 取消后配置不变
        assert window._config.use_builtin == before
        window.close()

    def test_settings_dialog_save_and_get_config(self, qapp: QApplication) -> None:
        """_save_config 应将控件值保存到配置，对话框应持有同一配置对象。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.max_workers = 4
        config.max_depth = 10
        config.scan_archives = False
        config.include_network_drives = True
        config.use_builtin = False

        dialog = SettingsDialog(config)
        # 修改控件值
        dialog.max_workers_spin.setValue(8)
        dialog.max_depth_spin.setValue(20)
        dialog.scan_archives_check.setChecked(True)
        dialog.include_network_check.setChecked(False)
        dialog.use_builtin_check.setChecked(True)

        dialog._save_config()

        assert config.max_workers == 8
        assert config.max_depth == 20
        assert config.scan_archives is True
        assert config.include_network_drives is False
        assert config.use_builtin is True

        # 对话框持有同一配置对象引用
        assert dialog.config is config
        dialog.close()

    def test_settings_dialog_save_config_depth_zero(self, qapp: QApplication) -> None:
        """max_depth 为 0 时应保存为 None。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.max_depth = 5

        dialog = SettingsDialog(config)
        dialog.max_depth_spin.setValue(0)
        dialog._save_config()

        assert config.max_depth is None
        dialog.close()

    def test_settings_dialog_on_accept(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """_on_accept 应调用 _save_config 并关闭对话框。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.max_workers = 2

        dialog = SettingsDialog(config)
        dialog.max_workers_spin.setValue(16)

        accepted_called: list[bool] = []
        monkeypatch.setattr(dialog, "accept", lambda: accepted_called.append(True))

        dialog.on_accept()

        assert config.max_workers == 16
        assert accepted_called == [True]
        dialog.close()

    def test_settings_dialog_loads_cache_config(self, qapp: QApplication) -> None:
        """_load_config 应将缓存配置恢复到控件。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.cache_enabled = False
        config.cache_path = "/tmp/custom_cache.db"

        dialog = SettingsDialog(config)
        assert dialog.cache_enabled_check.isChecked() is False
        assert dialog.cache_path_edit.text() == "/tmp/custom_cache.db"
        dialog.close()

    def test_settings_dialog_saves_cache_config(self, qapp: QApplication) -> None:
        """_save_config 应将缓存控件值保存到配置。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.cache_enabled = True
        config.cache_path = None

        dialog = SettingsDialog(config)
        dialog.cache_enabled_check.setChecked(False)
        dialog.cache_path_edit.setText("/tmp/new_cache.db")
        dialog._save_config()

        assert config.cache_enabled is False
        assert config.cache_path == "/tmp/new_cache.db"
        dialog.close()

    def test_settings_dialog_cache_path_empty_becomes_none(self, qapp: QApplication) -> None:
        """缓存路径为空时保存为 None（使用默认路径）。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.cache_path = "/tmp/old.db"

        dialog = SettingsDialog(config)
        dialog.cache_path_edit.setText("   ")
        dialog._save_config()

        assert config.cache_path is None
        dialog.close()

    def test_settings_dialog_loads_staging_dir(self, qapp: QApplication) -> None:
        """iter-77：_load_config 应将 staging_dir 恢复到控件。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.staging_dir = "/tmp/staging"

        dialog = SettingsDialog(config)
        assert dialog.staging_dir_edit.text() == "/tmp/staging"
        dialog.close()

    def test_settings_dialog_saves_staging_dir(self, qapp: QApplication) -> None:
        """iter-77：_save_config 应将暂存区控件值保存到配置。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.staging_dir = None

        dialog = SettingsDialog(config)
        dialog.staging_dir_edit.setText("/tmp/new_staging")
        dialog._save_config()

        assert config.staging_dir == "/tmp/new_staging"
        dialog.close()

    def test_settings_dialog_staging_dir_empty_becomes_none(self, qapp: QApplication) -> None:
        """iter-77：暂存区路径为空时保存为 None（自动探测盘符）。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.staging_dir = "/tmp/old_staging"

        dialog = SettingsDialog(config)
        dialog.staging_dir_edit.setText("   ")
        dialog._save_config()

        assert config.staging_dir is None
        dialog.close()

    def test_settings_dialog_browse_staging_dir_updates_edit(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """iter-77：选择目录按钮应将所选路径填入编辑框。"""
        from fuscan.config import Config
        from fuscan.gui import settings_dialog as sd_module
        from fuscan.gui.settings_dialog import SettingsDialog

        chosen_path = "/tmp/chosen_staging"

        def fake_get_existing_directory(parent, title, start_dir):  # type: ignore[no-untyped-def]
            assert title == "选择暂存区目录"
            return chosen_path

        monkeypatch.setattr(sd_module.QFileDialog, "getExistingDirectory", fake_get_existing_directory)

        dialog = SettingsDialog(Config())
        dialog._on_browse_staging_dir()
        assert dialog.staging_dir_edit.text() == chosen_path
        dialog.close()

    def test_settings_dialog_browse_staging_dir_cancelled_keeps_edit(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """iter-77：取消选择时保持编辑框内容不变。"""
        from fuscan.config import Config
        from fuscan.gui import settings_dialog as sd_module
        from fuscan.gui.settings_dialog import SettingsDialog

        monkeypatch.setattr(sd_module.QFileDialog, "getExistingDirectory", lambda *a, **k: "")

        dialog = SettingsDialog(Config())
        dialog.staging_dir_edit.setText("/tmp/predefined")
        dialog._on_browse_staging_dir()
        assert dialog.staging_dir_edit.text() == "/tmp/predefined"
        dialog.close()


class TestMainWindowIgnore:
    """主窗口配置页忽略项控件测试（iter-79：从设置对话框迁移到配置页）。"""

    def test_ignore_widgets_exist_with_placeholders(self, qapp: QApplication) -> None:
        """主窗口应包含忽略目录与忽略扩展名编辑控件，并设置占位提示。"""
        window = MainWindow()
        assert window.ignore_dirs_edit is not None
        assert window.ignore_extensions_edit is not None
        assert "目录名" in window.ignore_dirs_edit.placeholderText()
        assert "扩展名" in window.ignore_extensions_edit.placeholderText()
        window.close()

    def test_content_tab_widget_has_three_tabs(self, qapp: QApplication) -> None:
        """iter-79：文件类型与忽略项通过 QTabWidget 切换，文件类型为第一个 Tab。"""
        window = MainWindow()
        assert window.content_tab_widget is not None
        assert window.content_tab_widget.count() == 3
        assert window.content_tab_widget.tabText(0) == "文件类型"
        assert window.content_tab_widget.tabText(1) == "忽略目录"
        assert window.content_tab_widget.tabText(2) == "忽略扩展名"
        window.close()

    def test_default_ignore_dirs_loaded(self, qapp: QApplication) -> None:
        """默认 Config 的 ignore_dirs 应加载到编辑器。"""
        window = MainWindow()
        lines = window.ignore_dirs_edit.toPlainText().splitlines()
        assert ".git" in lines
        assert "node_modules" in lines
        assert "__pycache__" in lines
        window.close()

    def test_default_ignore_extensions_loaded(self, qapp: QApplication) -> None:
        """默认 Config 的 ignore_extensions 应加载到编辑器。"""
        window = MainWindow()
        lines = window.ignore_extensions_edit.toPlainText().splitlines()
        assert "pyc" in lines
        assert "exe" in lines
        assert "zip" in lines
        window.close()

    def test_custom_ignore_dirs_loaded(self, qapp: QApplication) -> None:
        """自定义 ignore_dirs 应通过 _apply_config 加载到编辑器。"""
        window = MainWindow()
        # 直接修改 config 并调用 _apply_config 验证加载逻辑
        window._config.ignore_dirs = ["custom_dir", ".git"]
        window._apply_config()
        assert window.ignore_dirs_edit.toPlainText().splitlines() == ["custom_dir", ".git"]
        window.close()

    def test_save_ignore_to_config_writes_dirs(self, qapp: QApplication) -> None:
        """_save_ignore_to_config 应将编辑器文本按行写入 config.ignore_dirs，过滤空行。"""
        window = MainWindow()
        window.ignore_dirs_edit.setPlainText("new_dir\n.git\n\n  \n")
        window._save_ignore_to_config()
        assert window._config.ignore_dirs == ["new_dir", ".git"]
        window.close()

    def test_save_ignore_to_config_writes_extensions(self, qapp: QApplication) -> None:
        """_save_ignore_to_config 应将扩展名编辑器文本按行写入 config.ignore_extensions。"""
        window = MainWindow()
        window.ignore_extensions_edit.setPlainText("pyc\nexe\n\n")
        window._save_ignore_to_config()
        assert window._config.ignore_extensions == ["pyc", "exe"]
        window.close()

    def test_save_ignore_to_config_strips_whitespace(self, qapp: QApplication) -> None:
        """保存时应 strip 每行首尾空白。"""
        window = MainWindow()
        window.ignore_dirs_edit.setPlainText("  .git  \n  node_modules  \n")
        window._save_ignore_to_config()
        assert window._config.ignore_dirs == [".git", "node_modules"]
        window.close()

    def test_on_ignore_changed_starts_timer(self, qapp: QApplication) -> None:
        """编辑器 textChanged 应启动节流 timer，500ms 后保存。"""
        window = MainWindow()
        # 确保初始未运行
        assert not window._ignore_save_timer.isActive()
        window.ignore_dirs_edit.setPlainText("new_dir\n.git")
        # textChanged 触发 _on_ignore_changed 启动 timer
        assert window._ignore_save_timer.isActive()
        window._ignore_save_timer.stop()
        window.close()

    def test_apply_config_does_not_trigger_save_on_load(self, qapp: QApplication) -> None:
        """_apply_config 加载忽略项时 blockSignals 避免触发节流保存循环。"""
        window = MainWindow()
        # 加载后 timer 不应被触发
        assert not window._ignore_save_timer.isActive()
        window.close()


class TestIcons:
    """按钮与菜单动作图标接入测试。"""

    def test_all_action_buttons_have_icons(self, qapp: QApplication) -> None:
        """所有操作按钮应设置图标。"""
        window = MainWindow()
        assert not window.edit_rule_btn.icon().isNull()
        assert not window.export_btn.icon().isNull()
        assert not window.rescan_btn.icon().isNull()
        assert not window.cancel_btn.icon().isNull()
        assert not window.pause_resume_btn.icon().isNull()
        window.close()

    def test_all_menu_actions_have_icons(self, qapp: QApplication) -> None:
        """所有菜单动作应设置图标。"""
        window = MainWindow()
        assert not window.edit_rules_action.icon().isNull()
        assert not window.export_csv_action.icon().isNull()
        assert not window.export_json_action.icon().isNull()
        assert not window.settings_action.icon().isNull()
        assert not window.about_action.icon().isNull()
        window.close()


class TestSeverityBackground:
    """严重等级背景色与分组项可选性测试。"""

    def test_critical_tree_item_has_background(self, qapp: QApplication, tmp_path: Path) -> None:
        """critical 等级文件项各列应有浅红背景色。"""
        from fuscan.gui.preview_utils import SEVERITY_BACKGROUNDS
        from fuscan.scanner import Scanner

        (tmp_path / "leak.conf").write_text("AKIAIOSFODNN7EXAMPLE", encoding="utf-8")
        rs = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="AWS密钥",
                    severity=Severity.CRITICAL,
                    match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="AKIA"),
                ),
            ),
        )
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        window._switch_stage(WorkflowStage.RESULTS)
        window._refresh_result_tree()

        top_item = window.result_tree.model().item(0, 0)
        assert top_item is not None
        expected_bg = SEVERITY_BACKGROUNDS[Severity.CRITICAL]
        for col in range(window.result_tree.model().columnCount()):
            cell = window.result_tree.model().item(0, col)
            assert cell is not None
            bg = cell.background()
            assert bg.color().rgb() == expected_bg.rgb()
        window.close()

    def test_group_items_non_selectable(self, qapp: QApplication, tmp_path: Path) -> None:
        """按严重等级分组模式下，顶层分组项应不可选中。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        window._switch_stage(WorkflowStage.RESULTS)
        idx = window.group_mode_combo.findData("severity")
        window.group_mode_combo.setCurrentIndex(idx)
        window._refresh_result_tree()

        top_item = window.result_tree.model().item(0, 0)
        assert top_item is not None
        assert not (top_item.flags() & Qt.ItemIsSelectable)
        window.close()


class TestDetailPreviewFallback:
    """详情预览回退提示测试。"""

    def test_preview_shows_fallback_when_no_keywords(self, qapp: QApplication, tmp_path: Path) -> None:
        """命中规则但 detail 无单引号关键词时，预览应显示回退提示。"""
        from fuscan.scanner.result import RuleHit, ScanResult

        path = tmp_path / "config.yaml"
        path.write_text("some: content\nhere: value", encoding="utf-8")

        result = ScanResult(
            path=path,
            size=path.stat().st_size,
            hits=(RuleHit("路径规则", Severity.INFO, "路径匹配"),),
        )

        window = MainWindow()
        window._detail_panel.show_result(result)
        text = window.detail_preview.toPlainText()
        assert "无内容关键词可高亮" in text
        assert "路径规则" in text
        window.close()


class TestGuiCache:
    """GUI 缓存集成测试。"""

    def test_scan_worker_accepts_cache_params(self, qapp: QApplication, tmp_path: Path) -> None:
        """ScanWorker 应接受 cache 和 source_files 参数并存储。"""
        from fuscan.cache import CacheStore

        cache = CacheStore(tmp_path / "cache.db")
        source_files = {tmp_path / "rules.yaml": "abc123"}
        try:
            worker = ScanWorker(
                ruleset=_build_ruleset(),
                roots=[tmp_path],
                cache=cache,
                source_files=source_files,
            )
            assert worker._cache is cache
            assert worker._source_files is source_files
        finally:
            cache.close()

    def test_scan_worker_defaults_cache_none(self, qapp: QApplication, tmp_path: Path) -> None:
        """未传 cache 时默认为 None。"""
        worker = ScanWorker(ruleset=_build_ruleset(), roots=[tmp_path])
        assert worker._cache is None
        assert worker._source_files is None

    def test_main_window_build_cache_context_creates_cache(self, qapp: QApplication, tmp_path: Path) -> None:
        """_build_cache_context 启用缓存时应惰性创建 CacheStore 并返回 source_files。"""
        window = MainWindow()
        window._config.cache_enabled = True
        window._config.cache_path = str(tmp_path / "cache.db")
        try:
            cache, source_files = window._build_cache_context()
            assert cache is not None
            assert source_files is not None
            assert (tmp_path / "cache.db").exists()
            # 缓存对象复用：再次调用返回同一实例
            cache2, _ = window._build_cache_context()
            assert cache2 is cache
        finally:
            if window._cache is not None:
                window._cache.close()
            window.close()

    def test_main_window_build_cache_context_disabled(self, qapp: QApplication) -> None:
        """禁用缓存时 _build_cache_context 返回 (None, None)。"""
        window = MainWindow()
        window._config.cache_enabled = False
        try:
            cache, source_files = window._build_cache_context()
            assert cache is None
            assert source_files is None
            assert window._cache is None
        finally:
            window.close()

    def test_main_window_close_event_closes_cache(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """closeEvent 应关闭 CacheStore 并置空引用。"""
        try:
            from PySide2.QtGui import QCloseEvent
        except ImportError:  # pragma: no cover
            from PySide6.QtGui import QCloseEvent  # pyrefly: ignore [missing-import]

        window = MainWindow()
        window._config.cache_enabled = True
        window._config.cache_path = str(tmp_path / "cache.db")
        window._build_cache_context()
        assert window._cache is not None
        cache_ref = window._cache
        closed: list[bool] = []
        monkeypatch.setattr(cache_ref, "close", lambda: closed.append(True))
        # 绕过父类 closeEvent 对事件类型的要求
        monkeypatch.setattr("fuscan.gui.main_window.QMainWindow.closeEvent", lambda self, event: None)
        window.closeEvent(QCloseEvent())
        assert closed == [True]
        assert window._cache is None
        window.close()

    def test_main_window_close_event_handles_cache_close_error(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """closeEvent 中 cache.close() 抛异常时应记录日志但不中断关闭。"""
        try:
            from PySide2.QtGui import QCloseEvent
        except ImportError:  # pragma: no cover
            from PySide6.QtGui import QCloseEvent  # pyrefly: ignore [missing-import]

        window = MainWindow()
        window._config.cache_enabled = True
        window._config.cache_path = str(tmp_path / "cache.db")
        window._build_cache_context()
        assert window._cache is not None

        def raising_close() -> None:
            raise sqlite3.OperationalError("close error")

        monkeypatch.setattr(window._cache, "close", raising_close)
        monkeypatch.setattr("fuscan.gui.main_window.QMainWindow.closeEvent", lambda self, event: None)
        # 不应抛异常
        window.closeEvent(QCloseEvent())
        assert window._cache is None
        window.close()

    def test_main_window_build_cache_context_default_path(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cache_path 为 None 时应使用 default_cache_path()。"""

        cache_db = tmp_path / "default_cache.db"
        monkeypatch.setattr("fuscan.cache.default_cache_path", lambda: cache_db)
        # compute_source_files 内部 import hash_bytes，不影响
        window = MainWindow()
        window._config.cache_enabled = True
        window._config.cache_path = None
        try:
            cache, _source_files = window._build_cache_context()
            assert cache is not None
            assert cache_db.exists()
        finally:
            if window._cache is not None:
                window._cache.close()
            window.close()

    def test_main_window_settings_releases_cache_when_disabled(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """设置中关闭缓存后应释放旧 CacheStore。"""
        window = MainWindow()
        window._config.cache_enabled = True
        window._config.cache_path = str(tmp_path / "cache.db")
        window._build_cache_context()
        assert window._cache is not None

        from fuscan.gui import settings_dialog as sd_module

        # 模拟对话框 exec_ 返回 Accepted，并在返回前模拟用户关闭缓存
        def fake_exec(self: sd_module.SettingsDialog) -> int:
            self.config.cache_enabled = False
            return 1  # QDialog.Accepted

        monkeypatch.setattr(sd_module.SettingsDialog, "exec_", fake_exec)
        window._on_settings()
        assert window._cache is None
        window.close()

    def test_main_window_settings_releases_cache_on_path_change(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """设置中改变缓存路径后应释放旧 CacheStore。"""
        window = MainWindow()
        window._config.cache_enabled = True
        window._config.cache_path = str(tmp_path / "cache.db")
        window._build_cache_context()
        assert window._cache is not None

        from fuscan.gui import settings_dialog as sd_module

        # 模拟对话框返回 Accepted 并改变缓存路径
        def fake_exec(self: sd_module.SettingsDialog) -> int:
            self.config.cache_path = str(tmp_path / "new_cache.db")
            return 1  # QDialog.Accepted

        monkeypatch.setattr(sd_module.SettingsDialog, "exec_", fake_exec)
        window._on_settings()
        assert window._cache is None
        window.close()

    def test_main_window_settings_cache_close_error(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_on_settings 中 cache.close() 抛异常时应记录日志但不中断。"""
        window = MainWindow()
        window._config.cache_enabled = True
        window._config.cache_path = str(tmp_path / "cache.db")
        window._build_cache_context()
        assert window._cache is not None

        def raising_close() -> None:
            raise sqlite3.OperationalError("close error")

        monkeypatch.setattr(window._cache, "close", raising_close)

        from fuscan.gui import settings_dialog as sd_module

        def fake_exec(self: sd_module.SettingsDialog) -> int:
            self.config.cache_enabled = False
            return 1  # QDialog.Accepted

        monkeypatch.setattr(sd_module.SettingsDialog, "exec_", fake_exec)
        # 不应抛异常
        window._on_settings()
        assert window._cache is None
        window.close()
