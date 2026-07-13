"""GUI 烟雾测试。

使用 ``gui`` marker 标记，CI 无 GUI 环境时可通过 ``-m "not gui"`` 跳过。
需要 QApplication 环境（offscreen 平台）。
"""

from __future__ import annotations

import os
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
        from PySide2.QtWidgets import QApplication, QMenu
    except ImportError:  # pragma: no cover
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QMenu

    from fuscan.gui.detail_dialog import HitDetailDialog
    from fuscan.gui.main_window import MainWindow, ScanState, WorkflowStage, _severity_text
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
        assert window._scan_btn is not None
        assert window._rules_tree is not None
        assert window._result_tree is not None
        window.close()

    def test_scan_button_disabled_initially(self, qapp: QApplication) -> None:
        window = MainWindow()
        assert not window._scan_btn.isEnabled()
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
        assert window._rules_tree.topLevelItemCount() == 1
        item = window._rules_tree.topLevelItem(0)
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
        assert window._result_tree.topLevelItemCount() == 1
        item = window._result_tree.topLevelItem(0)
        assert "secret.txt" in item.text(0)
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
        content = MainWindow._format_report(report, "csv")
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
        content = MainWindow._format_report(report, "json")
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
        assert window._rules_tree.topLevelItemCount() == 1
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
        assert not window._scan_btn.isEnabled()

        # 仅设置规则
        window._ruleset = _build_ruleset()
        window._update_scan_button()
        assert not window._scan_btn.isEnabled()

        # 设置路径
        window._scan_root = tmp_path
        window._update_scan_button()
        assert window._scan_btn.isEnabled()
        window.close()

    def test_refresh_rules_tree_empty(self, qapp: QApplication) -> None:
        """无规则集时规则树为空。"""
        window = MainWindow()
        window._ruleset = None
        window._refresh_rules_tree()
        assert window._rules_tree.topLevelItemCount() == 0
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
        assert window._rules_tree.topLevelItemCount() > 0
        window.close()

    def test_uncheck_builtin_clears_ruleset(self, qapp: QApplication) -> None:
        """取消勾选通用规则且无用户规则时 ruleset 为 None。"""
        window = MainWindow()
        window._set_use_builtin(False)
        assert window._use_builtin is False
        assert window._ruleset is None
        assert window._rules_tree.topLevelItemCount() == 0
        # 扫描按钮应禁用
        assert not window._scan_btn.isEnabled()
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
        builtin_count = window._rules_tree.topLevelItemCount()

        monkeypatch.setattr(
            "fuscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(rules_yaml), ""),
        )
        window._on_load_rules()
        # 合并后规则数应大于内置规则数
        assert window._rules_tree.topLevelItemCount() > builtin_count
        assert window._ruleset is not None
        window.close()

    def test_scan_enabled_with_builtin_only(self, qapp: QApplication, tmp_path: Path) -> None:
        """仅加载内置规则并选择路径后扫描按钮应可用。"""
        window = MainWindow()
        assert window._ruleset is not None
        # 未选路径时按钮禁用
        assert not window._scan_btn.isEnabled()
        # 选择路径后按钮启用
        window._scan_root = tmp_path
        window._update_scan_button()
        assert window._scan_btn.isEnabled()
        window.close()


class TestMultiRulesList:
    """多规则文件列表与排序测试。"""

    def test_rules_file_list_initially_empty(self, qapp: QApplication) -> None:
        """启动时（仅内置规则）规则文件列表应为空。"""
        window = MainWindow()
        assert window._rules_paths == []
        assert window._rules_file_list.count() == 0
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
        assert window._rules_file_list.count() == 2
        # 合并后规则树应有 2 条规则
        assert window._rules_tree.topLevelItemCount() == 2
        window.close()

    def test_load_duplicate_rule_ignored(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """重复加载同一文件不应追加。"""
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
        # 抑制提示框
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window._on_load_rules()
        window._on_load_rules()  # 重复加载

        assert len(window._rules_paths) == 1
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
        rule = window._ruleset.rules[0]
        assert rule.match.pattern == "second"

        # 选中第二行并上移
        window._rules_file_list.setCurrentRow(1)
        window._on_move_rule_up()

        # 顺序变为 [r2, r1]，r1 覆盖 r2，pattern 应为 first
        assert window._rules_paths == [r2, r1]
        rule = window._ruleset.rules[0]
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

        # 选中第一行并下移
        window._rules_file_list.setCurrentRow(0)
        window._on_move_rule_down()

        # 顺序变为 [r2, r1]，r1 覆盖 r2
        assert window._rules_paths == [r2, r1]
        rule = window._ruleset.rules[0]
        assert rule.match.pattern == "first"
        window.close()

    def test_move_rule_up_at_top_noop(self, qapp: QApplication, tmp_path: Path) -> None:
        """首行上移不应改变顺序。"""
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

        window._rules_file_list.setCurrentRow(0)
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

        window._rules_file_list.setCurrentRow(1)
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
        assert window._rules_tree.topLevelItemCount() == 2

        # 选中第一行并移除
        window._rules_file_list.setCurrentRow(0)
        window._on_remove_rule()

        assert len(window._rules_paths) == 1
        assert window._rules_paths[0] == r2
        assert window._rules_file_list.count() == 1
        assert window._rules_tree.topLevelItemCount() == 1
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

        window._rules_file_list.setCurrentRow(0)
        window._on_remove_rule()

        assert len(window._rules_paths) == 0
        assert window._ruleset is None
        assert window._rules_tree.topLevelItemCount() == 0
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
        assert window._ruleset.rules[0].match.pattern == "from_r2"

        # [r2, r1] → r1 覆盖 r2
        window._rules_paths = [r2, r1]
        window._reload_ruleset()
        assert window._ruleset.rules[0].match.pattern == "from_r1"
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
        assert window._path_combo.count() == 2
        assert window._path_combo.itemText(0) == str(tmp_path / "dir_a")
        assert window._path_combo.itemText(1) == str(tmp_path / "dir_b")
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
        window._path_combo.addItem(str(tmp_path / "target"))
        window._path_combo.setCurrentIndex(0)
        assert window._scan_root == tmp_path / "target"
        window.close()

    def test_path_history_dedup(self, qapp: QApplication, tmp_path: Path) -> None:
        """重复路径在历史中只出现一次。"""
        path_str = str(tmp_path)
        window = MainWindow()
        window._add_scan_path_history(path_str)
        window._add_scan_path_history(path_str)
        assert window._path_combo.count() == 1
        window.close()

    def test_path_history_limit(self, qapp: QApplication, tmp_path: Path) -> None:
        """历史路径超过上限时自动截断。"""
        from fuscan.config import MAX_HISTORY

        window = MainWindow()
        for i in range(MAX_HISTORY + 5):
            window._add_scan_path_history(f"/path/{i}")
        assert window._path_combo.count() == MAX_HISTORY
        # 最近添加的应在最前
        assert window._path_combo.itemText(0) == f"/path/{MAX_HISTORY + 4}"
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
        sizes = window._splitter.sizes()
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
        assert window._scan_btn.isEnabled()
        window.close()

    def test_invalid_scan_path_disables_button_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """配置中路径无效时启动后扫描按钮应禁用。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        config = Config(scan_paths=[str(tmp_path / "nonexistent")], use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_root is None
        assert not window._scan_btn.isEnabled()
        window.close()

    def test_no_scan_path_disables_button_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """配置中无路径时启动后扫描按钮应禁用。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        config = Config(scan_paths=[], use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_root is None
        assert not window._scan_btn.isEnabled()
        window.close()


class TestScanWorker:
    def test_worker_runs_scan(self, qapp: QApplication, tmp_path: Path) -> None:
        """ScanWorker 应在后台完成扫描。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        results: list[Any] = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108
        worker.start()

        # 通过事件循环等待 finished 信号
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # 超时保护
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
            from PySide6.QtCore import QEventLoop, QTimer

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path / "nonexistent"])

        results: list[Any] = []
        errors: list[Any] = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108
        worker.failed.connect(lambda msg: errors.append(msg))  # noqa: PLW0108
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
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
        assert window._scan_btn.text() == "开始扫描"
        assert window._scan_action.text() == "开始扫描"
        window.close()

    def test_update_scan_button_running_stays_enabled(self, qapp: QApplication) -> None:
        """RUNNING 状态下扫描按钮应始终可用，即使无规则集。"""
        window = MainWindow()
        window._ruleset = None
        window._scan_state = ScanState.RUNNING
        window._update_scan_button()
        assert window._scan_btn.isEnabled()
        assert window._scan_action.isEnabled()
        window.close()

    def test_update_scan_button_paused_stays_enabled(self, qapp: QApplication) -> None:
        """PAUSED 状态下扫描按钮应始终可用。"""
        window = MainWindow()
        window._ruleset = None
        window._scan_state = ScanState.PAUSED
        window._update_scan_button()
        assert window._scan_btn.isEnabled()
        assert window._scan_action.isEnabled()
        window.close()

    def test_pause_scan_changes_state_and_text(self, qapp: QApplication) -> None:
        """_pause_scan 应设置 PAUSED 状态和"继续扫描"文本。"""
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._pause_scan()
        assert window._scan_state == ScanState.PAUSED
        assert window._pause_resume_btn.text() == "继续扫描"
        assert "已暂停" in window._stats_label.text()
        window.close()

    def test_resume_scan_changes_state_and_text(self, qapp: QApplication) -> None:
        """_resume_scan 应设置 RUNNING 状态和"暂停扫描"文本。"""
        window = MainWindow()
        window._scan_state = ScanState.PAUSED
        window._resume_scan()
        assert window._scan_state == ScanState.RUNNING
        assert window._pause_resume_btn.text() == "暂停扫描"
        window.close()

    def test_reset_scan_ui_resets_state(self, qapp: QApplication) -> None:
        """_reset_scan_ui 应重置到 IDLE 状态并恢复 pause_resume_btn 文本。"""
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._reset_scan_ui()
        assert window._scan_state == ScanState.IDLE
        assert window._pause_resume_btn.text() == "暂停扫描"
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
        assert window._pause_resume_btn.text() == "继续扫描"
        window.close()

    def test_on_pause_resume_paused_triggers_resume(self, qapp: QApplication) -> None:
        """PAUSED 状态点击 pause_resume_btn 应触发恢复。"""
        window = MainWindow()
        window._scan_state = ScanState.PAUSED
        window._on_pause_resume()
        assert window._scan_state == ScanState.RUNNING
        assert window._pause_resume_btn.text() == "暂停扫描"
        window.close()


class TestScanControlIntegration:
    """扫描控制集成测试：通过 MainWindow 运行完整扫描流程。"""

    def test_scan_completes_through_main_window(self, qapp: QApplication, tmp_path: Path) -> None:
        """通过 MainWindow 启动扫描应完成并填充结果树。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer

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
        assert window._pause_resume_btn.text() == "暂停扫描"
        assert window._main_stack.currentIndex() == 1

        loop = QEventLoop()
        QTimer.singleShot(10000, loop.quit)
        window._worker.finished.connect(loop.quit) if window._worker is not None else None
        (loop.exec if hasattr(loop, "exec") else loop.exec_)()

        if window._worker is not None:
            window._worker.wait(2000)

        assert window._scan_state == ScanState.IDLE
        assert window._main_stack.currentIndex() == 2
        assert window._result_tree.topLevelItemCount() >= 1
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
        assert window._main_stack.currentIndex() == 0
        assert "已取消" in window._stats_label.text()
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
        assert window._main_stack.currentIndex() == 0
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
        assert window._main_stack.currentIndex() == 1
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
        assert window._main_stack.currentIndex() == 2
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
        assert window._main_stack.currentIndex() == 0
        assert window._workflow_stage == WorkflowStage.SETUP
        window.close()

    def test_results_to_setup_on_rescan(self, qapp: QApplication) -> None:
        """结果页点击重新扫描应返回 SETUP 页。"""
        window = MainWindow()
        window._switch_stage(WorkflowStage.RESULTS)
        window._on_rescan()
        assert window._main_stack.currentIndex() == 0
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
        assert window._main_stack.currentIndex() == 2
        window.close()

    def test_view_results_btn_disabled_initially(self, qapp: QApplication) -> None:
        """新建窗口无报告时查看结果按钮应可见但禁用。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        # 按钮始终可见（与 scan_btn 组合在一起），但无结果时禁用
        assert window._view_results_btn.isVisible()
        assert not window._view_results_btn.isEnabled()
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
        assert window._view_results_btn.isVisible()
        assert window._view_results_btn.isEnabled()
        window.close()

    def test_scan_btn_disabled_without_ruleset(self, qapp: QApplication) -> None:
        """无规则集时扫描按钮应禁用。"""
        window = MainWindow()
        window._ruleset = None
        window._update_stage_actions()
        assert not window._scan_btn.isEnabled()
        window.close()

    def test_scan_btn_enabled_with_ruleset_and_target(self, qapp: QApplication, tmp_path: Path) -> None:
        """有规则集和有效目标时扫描按钮应可用。"""
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        window = MainWindow()
        window._ruleset = _build_ruleset()
        window._scan_root = tmp_path
        window._scan_mode = "folder"
        window._update_stage_actions()
        assert window._scan_btn.isEnabled()
        window.close()

    def test_rescan_btn_disabled_in_setup(self, qapp: QApplication) -> None:
        """SETUP 阶段重新扫描按钮应禁用，RESULTS 阶段应启用。"""
        window = MainWindow()
        window._update_stage_actions()
        assert not window._rescan_btn.isEnabled()
        window._switch_stage(WorkflowStage.RESULTS)
        assert window._rescan_btn.isEnabled()
        window.close()

    def test_pause_resume_btn_text_in_scanning_running(self, qapp: QApplication) -> None:
        """SCANNING 阶段 RUNNING 状态 pause_resume_btn 文本为"暂停扫描"。"""
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._switch_stage(WorkflowStage.SCANNING)
        assert window._pause_resume_btn.text() == "暂停扫描"
        window.close()

    def test_pause_resume_btn_text_in_scanning_paused(self, qapp: QApplication) -> None:
        """SCANNING 阶段 PAUSED 状态 pause_resume_btn 文本为"继续扫描"。"""
        window = MainWindow()
        window._scan_state = ScanState.PAUSED
        window._switch_stage(WorkflowStage.SCANNING)
        assert window._pause_resume_btn.text() == "继续扫描"
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
        assert window._main_stack.currentIndex() == 2
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
        assert window._main_stack.currentIndex() == 0
        window.close()

    def test_scan_action_disabled_in_scanning(self, qapp: QApplication) -> None:
        """SCANNING 阶段扫描菜单项应禁用。"""
        window = MainWindow()
        window._switch_stage(WorkflowStage.SCANNING)
        assert not window._scan_action.isEnabled()
        window.close()

    def test_export_actions_disabled_in_setup(self, qapp: QApplication) -> None:
        """SETUP 阶段导出菜单项应禁用。"""
        window = MainWindow()
        window._update_stage_actions()
        assert not window._export_csv_action.isEnabled()
        assert not window._export_json_action.isEnabled()
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
        assert window._export_csv_action.isEnabled()
        assert window._export_json_action.isEnabled()
        window.close()

    def test_load_edit_rules_actions_disabled_in_results(self, qapp: QApplication) -> None:
        """RESULTS 阶段加载/编辑规则菜单项应禁用。"""
        window = MainWindow()
        window._switch_stage(WorkflowStage.RESULTS)
        assert not window._load_rules_action.isEnabled()
        assert not window._edit_rules_action.isEnabled()
        window.close()

    def test_switch_stage_syncs_sidebar(self, qapp: QApplication) -> None:
        """_switch_stage 应同步侧边栏选中项。"""
        window = MainWindow()
        window._switch_stage(WorkflowStage.SCANNING)
        assert window._sidebar.currentRow() == 1
        window._switch_stage(WorkflowStage.RESULTS)
        assert window._sidebar.currentRow() == 2
        window._switch_stage(WorkflowStage.SETUP)
        assert window._sidebar.currentRow() == 0
        window.close()

    def test_on_header_tab_changed_switches_tab_stack(self, qapp: QApplication) -> None:
        """_on_header_tab_changed 应切换 tab_stack 页面。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        window._on_header_tab_changed(1)
        assert window._tab_stack.currentIndex() == 1
        assert not window._sidebar.isVisible()
        window._on_header_tab_changed(2)
        assert window._tab_stack.currentIndex() == 2
        assert not window._sidebar.isVisible()
        window._on_header_tab_changed(0)
        assert window._tab_stack.currentIndex() == 0
        assert window._sidebar.isVisible()
        window.close()

    def test_on_sidebar_stage_changed_switches_main_stack(self, qapp: QApplication) -> None:
        """_on_sidebar_stage_changed 应映射 row 到 WorkflowStage 并切换 main_stack。"""
        window = MainWindow()
        window._on_sidebar_stage_changed(1)
        assert window._main_stack.currentIndex() == 1
        assert window._workflow_stage == WorkflowStage.SCANNING
        window._on_sidebar_stage_changed(2)
        assert window._main_stack.currentIndex() == 2
        assert window._workflow_stage == WorkflowStage.RESULTS
        window._on_sidebar_stage_changed(0)
        assert window._main_stack.currentIndex() == 0
        assert window._workflow_stage == WorkflowStage.SETUP
        window.close()


class TestSetupActionBar:
    """配置页操作条（setup_action_bar）结构与样式测试。"""

    def test_scan_btn_height_reduced_to_44(self, qapp: QApplication) -> None:
        """scan_btn 最小高度应为 44（与扫描中页按钮一致）。"""
        window = MainWindow()
        assert window._scan_btn.minimumHeight() == 44
        window.close()

    def test_scan_btn_minimum_width_180(self, qapp: QApplication) -> None:
        """scan_btn 最小宽度应为 180。"""
        window = MainWindow()
        assert window._scan_btn.minimumWidth() >= 180
        window.close()

    def test_setup_action_bar_exists(self, qapp: QApplication) -> None:
        """配置页应包含 setup_action_bar 容器。"""
        window = MainWindow()
        assert hasattr(window._ui, "setup_action_bar")
        assert window._ui.setup_action_bar is not None
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
        assert window._view_results_btn.minimumWidth() == window._scan_btn.minimumWidth()
        assert window._view_results_btn.minimumHeight() == window._scan_btn.minimumHeight()
        assert window._scan_btn.minimumWidth() == 180
        assert window._scan_btn.minimumHeight() == 44
        window.close()

    def test_view_results_btn_adjacent_to_scan_btn(self, qapp: QApplication) -> None:
        """view_results_btn 与 scan_btn 应相邻（中间无 stretch spacer 分隔）。

        通过验证 setup_btn_row 中两个按钮的布局索引相邻且无 Expanding spacer 插入。
        """
        window = MainWindow()
        layout = window._ui.setup_btn_row
        # 找到 view_results_btn 和 scan_btn 的位置索引
        view_idx = -1
        scan_idx = -1
        spacer_count = 0
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            if item.widget() is window._view_results_btn:
                view_idx = i
            elif item.widget() is window._scan_btn:
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
        assert not hasattr(window._ui, "setup_btn_spacer")
        assert hasattr(window._ui, "setup_btn_leading_spacer")
        window.close()


class TestScanningPageLayout:
    """扫描中页布局调整测试：进度与当前文件移至状态栏，统计面板移除。"""

    def test_stats_group_removed_from_ui(self, qapp: QApplication) -> None:
        """scanning_page 不应再包含 stats_group / stats_counts_label / stats_time_label。"""
        window = MainWindow()
        assert not hasattr(window._ui, "stats_group")
        assert not hasattr(window._ui, "stats_counts_label")
        assert not hasattr(window._ui, "stats_time_label")
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
            from PySide6.QtWidgets import QProgressBar as _QProgressBar
        assert isinstance(window._progress, _QProgressBar)
        # 初始（SETUP 阶段）应不可见
        assert not window._progress.isVisible()
        window.close()

    def test_current_file_label_in_status_bar(self, qapp: QApplication) -> None:
        """当前文件标签应挂载到状态栏，且初始不可见。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        assert not window._current_file_label.isVisible()
        # 进入扫描中阶段后应可见
        window._switch_stage(WorkflowStage.SCANNING)
        qapp.processEvents()
        assert window._current_file_label.isVisible()
        assert window._progress.isVisible()
        window.close()

    def test_progress_hidden_in_non_scanning_stage(self, qapp: QApplication) -> None:
        """非扫描阶段进度条与当前文件标签应隐藏。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        window._switch_stage(WorkflowStage.SETUP)
        qapp.processEvents()
        assert not window._progress.isVisible()
        assert not window._current_file_label.isVisible()
        window._switch_stage(WorkflowStage.RESULTS)
        qapp.processEvents()
        assert not window._progress.isVisible()
        assert not window._current_file_label.isVisible()
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
        assert window._progress.value() == 50
        assert window._progress.maximum() == 100
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


class TestSeverityDisplay:
    """严重等级颜色区分测试。"""

    def test_severity_text_chinese_labels(self) -> None:
        """_severity_text 应返回中文标签。"""
        assert _severity_text(Severity.CRITICAL) == "严重"
        assert _severity_text(Severity.WARNING) == "警告"
        assert _severity_text(Severity.INFO) == "一般"

    def test_result_tree_flat_shows_severity_colors(self, qapp: QApplication, tmp_path: Path) -> None:
        """result_tree 不分组模式下严重等级列应显示中文标签和颜色。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password = 123", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        window._switch_stage(WorkflowStage.RESULTS)
        window._refresh_result_tree()

        top_item = window._result_tree.topLevelItem(0)
        assert top_item is not None
        assert top_item.text(2) == "警告"
        assert top_item.foreground(2).color().name() == "#f0883e"

        child = top_item.child(0)
        assert child is not None
        assert child.text(2) == "警告"
        assert child.foreground(2).color().name() == "#f0883e"
        window.close()

    def test_detail_hits_table_shows_severity_colors(self, qapp: QApplication, tmp_path: Path) -> None:
        """detail_hits_table 严重等级列应显示中文标签和颜色。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password = 123", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        window._switch_stage(WorkflowStage.RESULTS)
        window._refresh_result_tree()

        top_item = window._result_tree.topLevelItem(0)
        top_item.setSelected(True)
        qapp.processEvents()

        item = window._detail_hits_table.item(0, 1)
        assert item is not None
        assert item.text() == "警告"
        assert item.foreground().color().name() == "#f0883e"
        window.close()

    def test_rules_tree_shows_severity_colors(self, qapp: QApplication) -> None:
        """rules_tree 严重等级列应显示中文标签和颜色。"""
        window = MainWindow()
        window._ruleset = _build_ruleset()
        window._refresh_rules_tree()

        item = window._rules_tree.topLevelItem(0)
        assert item is not None
        sev_text = item.text(1)
        assert sev_text in ("严重", "警告", "一般")
        color_name = item.foreground(1).color().name()
        assert color_name in ("#d73a49", "#f0883e", "#0366d6")
        window.close()

    def test_detail_dialog_shows_severity_colors(self, qapp: QApplication, tmp_path: Path) -> None:
        """HitDetailDialog 的 hits_table 严重等级列应显示中文标签和颜色。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password = 123", encoding="utf-8")
        rs = _build_ruleset()
        report = Scanner(rs).scan(tmp_path)
        result = report.results[0]

        dialog = HitDetailDialog(result, None)
        item = dialog._hits_table.item(0, 1)
        assert item is not None
        assert item.text() == "警告"
        assert item.foreground().color().name() == "#f0883e"
        dialog.close()


class TestScanWorkerControl:
    """ScanWorker 暂停/取消控制信号测试。"""

    def test_worker_cancel_emits_cancelled_signal(self, qapp: QApplication, tmp_path: Path) -> None:
        """取消扫描应发射 cancelled 信号并携带部分结果。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer

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
        worker.finished_report.connect(lambda r: finished_reports.append(r))  # noqa: PLW0108
        worker.cancelled.connect(lambda r: cancelled_reports.append(r))  # noqa: PLW0108
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
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
            from PySide6.QtCore import QEventLoop, QTimer

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        results: list[Any] = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108
        worker.start()

        # 暂停后立即恢复
        QTimer.singleShot(50, worker.pause)
        QTimer.singleShot(100, worker.resume)

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
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


class TestHitDetailDialogHelpers:
    """详情对话框辅助函数测试。"""

    def test_format_size_bytes(self) -> None:
        from fuscan.gui.detail_dialog import _format_size

        assert _format_size(0) == "0 B"
        assert _format_size(512) == "512 B"
        assert _format_size(1023) == "1023 B"

    def test_format_size_kb(self) -> None:
        from fuscan.gui.detail_dialog import _format_size

        assert _format_size(1024) == "1.0 KB"
        assert _format_size(2048) == "2.0 KB"

    def test_format_size_mb(self) -> None:
        from fuscan.gui.detail_dialog import _format_size

        assert _format_size(1024 * 1024) == "1.0 MB"

    def test_format_size_gb(self) -> None:
        from fuscan.gui.detail_dialog import _format_size

        assert "GB" in _format_size(1024 * 1024 * 1024)

    def test_extract_keywords_contains(self) -> None:
        from fuscan.gui.detail_dialog import _extract_keywords
        from fuscan.scanner import RuleHit

        hits = (
            RuleHit("r1", Severity.WARNING, "包含 'password'"),
            RuleHit("r2", Severity.CRITICAL, "包含 'secret'"),
        )
        kws = _extract_keywords(hits)
        assert "password" in kws
        assert "secret" in kws

    def test_extract_keywords_regex(self) -> None:
        from fuscan.gui.detail_dialog import _extract_keywords
        from fuscan.scanner import RuleHit

        hits = (RuleHit("r", Severity.CRITICAL, "正则命中: 'AKIA1234'"),)
        kws = _extract_keywords(hits)
        assert "AKIA1234" in kws

    def test_extract_keywords_dedup(self) -> None:
        from fuscan.gui.detail_dialog import _extract_keywords
        from fuscan.scanner import RuleHit

        hits = (
            RuleHit("r1", Severity.WARNING, "包含 'password'"),
            RuleHit("r2", Severity.WARNING, "包含 'password'"),
        )
        kws = _extract_keywords(hits)
        assert kws.count("password") == 1

    def test_extract_keywords_no_match(self) -> None:
        from fuscan.gui.detail_dialog import _extract_keywords
        from fuscan.scanner import RuleHit

        hits = (RuleHit("r", Severity.INFO, "完全相等"),)
        kws = _extract_keywords(hits)
        assert kws == []

    def test_build_preview_html_no_keywords(self) -> None:
        from fuscan.gui.detail_dialog import _build_preview_html

        result = _build_preview_html("hello world", [])
        assert "hello" in result
        assert "<span" not in result

    def test_build_preview_html_with_keywords(self) -> None:
        from fuscan.gui.detail_dialog import _build_preview_html

        result = _build_preview_html("hello password world", ["password"])
        assert "span" in result
        assert "background-color: yellow" in result

    def test_build_preview_html_escapes_html(self) -> None:
        from fuscan.gui.detail_dialog import _build_preview_html

        result = _build_preview_html("<script>alert(1)</script>", [])
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_build_preview_html_case_insensitive(self) -> None:
        from fuscan.gui.detail_dialog import _build_preview_html

        result = _build_preview_html("PASSWORD password Password", ["password"])
        # 所有大小的 password 都应被高亮（3 次匹配 = 6 个 span 标签：开+关）
        assert result.count("<span") == 3


class TestScanWorkerMultiRoot:
    """ScanWorker 多根路径扫描测试。"""

    def test_worker_scans_multiple_roots(self, qapp: QApplication, tmp_path: Path) -> None:
        """ScanWorker 应依次扫描多个根路径并合并结果。"""
        try:
            from PySide2.QtCore import QEventLoop, QTimer
        except ImportError:  # pragma: no cover
            from PySide6.QtCore import QEventLoop, QTimer

        (tmp_path / "dir_a").mkdir()
        (tmp_path / "dir_a" / "secret.txt").write_text("x", encoding="utf-8")
        (tmp_path / "dir_b").mkdir()
        (tmp_path / "dir_b" / "secret.txt").write_text("y", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path / "dir_a", tmp_path / "dir_b"])

        results: list[Any] = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
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
            from PySide6.QtCore import QEventLoop, QTimer

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(
            ruleset=rs,
            roots=[tmp_path / "nonexistent", tmp_path],
        )

        results: list[Any] = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
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
            from PySide6.QtCore import QEventLoop, QTimer

        for i in range(5):
            (tmp_path / f"secret_{i}.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        progress_infos: list[Any] = []
        worker.progress_info.connect(progress_infos.append)
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
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
            from PySide6.QtCore import QEventLoop, QTimer

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
        worker.progress_info.connect(progress_infos.append)
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
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
            from PySide6.QtCore import QEventLoop, QTimer

        from fuscan.scanner.result import ProgressInfo

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        progress_infos: list[Any] = []
        worker.progress_info.connect(progress_infos.append)
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
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
        worker.finished_report.connect(reports.append)
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
        worker.finished_report.connect(reports.append)
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
        worker.finished_report.connect(reports.append)
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
        worker.progress_info.connect(emitted.append)

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
        worker.progress_info.connect(emitted.append)

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
        worker.failed.connect(failures.append)
        reports: list[Any] = []
        worker.finished_report.connect(reports.append)
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
        worker.progress_info.connect(emitted.append)

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
        worker.cancelled.connect(cancelled_reports.append)
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
        worker.cancelled.connect(cancelled_reports.append)
        worker.run()

        assert len(cancelled_reports) == 1
        assert cancelled_reports[0].cancelled is True


class TestScanMode:
    """扫描模式 UI 测试。"""

    def test_default_mode_is_folder(self, qapp: QApplication) -> None:
        """启动时默认扫描模式为 folder。"""
        window = MainWindow()
        assert window._scan_mode == "folder"
        assert window._scan_mode_combo.currentIndex() == 2
        window.close()

    def test_folder_mode_shows_path_row(self, qapp: QApplication) -> None:
        """folder 模式下 target_stack 切到文件夹页。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        assert window._target_stack.currentIndex() == 2
        assert window._path_combo.isVisible()
        window.close()

    def test_full_mode_hides_target_selectors(self, qapp: QApplication) -> None:
        """full 模式下 target_stack 切到全盘页。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        window._scan_mode_combo.setCurrentIndex(0)
        assert window._scan_mode == "full"
        assert window._target_stack.currentIndex() == 0
        window.close()

    def test_drive_mode_shows_drive_buttons(self, qapp: QApplication) -> None:
        """drive 模式下 target_stack 切到盘符页。"""
        window = MainWindow()
        window.show()
        qapp.processEvents()
        window._scan_mode_combo.setCurrentIndex(1)
        assert window._scan_mode == "drive"
        assert window._target_stack.currentIndex() == 1
        window.close()

    def test_full_mode_enables_scan_without_path(self, qapp: QApplication) -> None:
        """full 模式下有规则即可扫描，无需选择路径。"""
        window = MainWindow()
        assert window._ruleset is not None
        # folder 模式下未选路径，按钮禁用
        assert not window._scan_btn.isEnabled()
        # 切换到 full 模式
        window._scan_mode_combo.setCurrentIndex(0)
        assert window._scan_btn.isEnabled()
        window.close()

    def test_drive_mode_enables_scan_with_drive(self, qapp: QApplication) -> None:
        """drive 模式下选中盘符即可扫描。"""
        window = MainWindow()
        window._scan_mode_combo.setCurrentIndex(1)
        # 盘符按钮在测试环境（Windows）通常有盘符
        if len(window._drive_buttons) > 0:
            window._drive_buttons[0].setChecked(True)
            window._on_drive_selected(window._drive_buttons[0])
            assert window._scan_btn.isEnabled()
        window.close()

    def test_build_scan_roots_full_mode(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """full 模式应返回所有盘符。"""
        from fuscan.gui import main_window as mw_mod

        fake_drives = [Path("C:\\"), Path("D:\\")]
        monkeypatch.setattr(mw_mod, "list_drives", lambda include_network=False: fake_drives)

        window = MainWindow()
        window._scan_mode_combo.setCurrentIndex(0)
        roots = window._build_scan_roots()
        assert roots == fake_drives
        window.close()

    def test_build_scan_roots_drive_mode(self, qapp: QApplication) -> None:
        """drive 模式应返回选中的单个盘符。"""
        window = MainWindow()
        window._scan_mode_combo.setCurrentIndex(1)
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
        assert window._scan_mode_combo.currentIndex() == 0
        window.close()

    def test_drive_mode_restored_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """启动时从配置恢复 drive 模式。"""
        from fuscan.config import Config
        from fuscan.config import save_config as _save_impl

        config = Config(scan_mode="drive")
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_mode == "drive"
        assert window._scan_mode_combo.currentIndex() == 1
        window.close()

    def test_close_saves_scan_mode(self, qapp: QApplication, tmp_path: Path) -> None:
        """关闭时扫描模式应被保存。"""
        window = MainWindow()
        window._scan_mode_combo.setCurrentIndex(0)
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
        first_drive = window._drive_buttons[0].property("drive")
        window.close()

        config = Config(scan_mode="drive", last_drive=first_drive)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_mode == "drive"
        assert window._selected_drive == first_drive
        window.close()


class TestHitDetailDialog:
    """详情对话框测试。"""

    def test_dialog_shows_file_info(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框应展示文件路径、大小等信息。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner import RuleHit, ScanResult

        path = tmp_path / "secret.txt"
        path.write_text("my password here", encoding="utf-8")

        result = ScanResult(
            path=path,
            size=path.stat().st_size,
            hits=(RuleHit("r1", Severity.WARNING, "包含 'password'"),),
        )
        dialog = HitDetailDialog(result)
        info_text = dialog._info_label.text()
        assert "secret.txt" in info_text
        assert "命中规则数" in info_text
        dialog.close()

    def test_dialog_hits_table(self, qapp: QApplication, tmp_path: Path) -> None:
        """命中规则表应正确显示。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner import RuleHit, ScanResult

        path = tmp_path / "test.txt"
        path.write_text("content", encoding="utf-8")
        result = ScanResult(
            path=path,
            size=7,
            hits=(
                RuleHit("rule-a", Severity.WARNING, "包含 'a'"),
                RuleHit("rule-b", Severity.CRITICAL, "包含 'b'"),
            ),
        )
        dialog = HitDetailDialog(result)
        assert dialog._hits_table.rowCount() == 2
        assert dialog._hits_table.item(0, 0).text() == "rule-a"
        assert dialog._hits_table.item(1, 0).text() == "rule-b"
        dialog.close()

    def test_dialog_preview_highlights_keywords(self, qapp: QApplication, tmp_path: Path) -> None:
        """内容预览应高亮关键词。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner import RuleHit, ScanResult

        path = tmp_path / "data.txt"
        path.write_text("the password is secret123", encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len("the password is secret123"),
            hits=(
                RuleHit("r1", Severity.WARNING, "包含 'password'"),
                RuleHit("r2", Severity.CRITICAL, "正则命中: 'secret123'"),
            ),
        )
        dialog = HitDetailDialog(result)
        html = dialog._preview.toHtml()
        assert "password" in html
        assert "span" in html  # 有高亮标签
        dialog.close()

    def test_dialog_preview_empty_file(self, qapp: QApplication, tmp_path: Path) -> None:
        """空文件预览应显示提示。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner import RuleHit, ScanResult

        path = tmp_path / "empty.txt"
        path.write_text("", encoding="utf-8")
        result = ScanResult(
            path=path,
            size=0,
            hits=(RuleHit("r", Severity.WARNING, "包含 'x'"),),
        )
        dialog = HitDetailDialog(result)
        text = dialog._preview.toPlainText()
        assert "为空" in text or "二进制" in text
        dialog.close()

    def test_dialog_preview_nonexistent_file(self, qapp: QApplication, tmp_path: Path) -> None:
        """文件不存在时预览应显示错误提示。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner import RuleHit, ScanResult

        result = ScanResult(
            path=tmp_path / "nonexistent.txt",
            size=0,
            hits=(RuleHit("r", Severity.WARNING, "包含 'x'"),),
        )
        dialog = HitDetailDialog(result)
        assert "无法读取" in dialog._preview.toPlainText()
        dialog.close()

    def test_double_click_opens_dialog(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """双击结果项应弹出详情对话框。"""
        from fuscan.gui import main_window as mw_module
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)

        # 模拟 exec_ 避免模态阻塞
        called = {"count": 0}

        def fake_exec(self) -> int:  # type: ignore[no-untyped-def]
            called["count"] += 1
            return 1

        monkeypatch.setattr(mw_module.HitDetailDialog, "exec_", fake_exec)

        # 双击顶层项
        top_item = window._result_tree.topLevelItem(0)
        window._on_result_double_clicked(top_item, 0)
        assert called["count"] == 1

        # 双击子项也应触发
        if top_item.childCount() > 0:
            child_item = top_item.child(0)
            window._on_result_double_clicked(child_item, 0)
            assert called["count"] == 2

        window.close()

    def test_double_click_no_data_does_nothing(self, qapp: QApplication) -> None:
        """无双击数据时不弹对话框。"""
        try:
            from PySide2.QtWidgets import QTreeWidgetItem
        except ImportError:  # pragma: no cover
            from PySide6.QtWidgets import QTreeWidgetItem

        window = MainWindow()
        # 创建一个没有 UserRole 数据的项
        item = QTreeWidgetItem(["test", "", "", ""])
        window._result_tree.addTopLevelItem(item)
        # 不应抛异常
        window._on_result_double_clicked(item, 0)
        window.close()


class TestHitDetailDialogNavigation:
    """详情对话框命中位置导航测试。"""

    def _make_dialog_with_content(
        self, qapp: QApplication, tmp_path: Path, content: str, keyword: str
    ) -> HitDetailDialog:
        """构造带内容的详情对话框。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner.result import RuleHit, ScanResult

        path = tmp_path / "test.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("r1", Severity.WARNING, f"包含 '{keyword}'"),),
        )
        return HitDetailDialog(result)

    def test_nav_buttons_exist(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框应包含导航按钮和标签。"""
        dialog = self._make_dialog_with_content(qapp, tmp_path, "hello", "hello")
        assert dialog._prev_btn is not None
        assert dialog._next_btn is not None
        assert dialog._nav_label is not None
        dialog.close()

    def test_hit_positions_found(self, qapp: QApplication, tmp_path: Path) -> None:
        """应在内容中找到关键词位置。"""
        dialog = self._make_dialog_with_content(qapp, tmp_path, "password and password again", "password")
        assert len(dialog._hit_positions) == 2
        dialog.close()

    def test_first_hit_selected_on_open(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框打开时应定位到首个命中。"""
        dialog = self._make_dialog_with_content(qapp, tmp_path, "first password and second password", "password")
        assert dialog._current_hit_index == 0
        assert "1 / 2" in dialog._nav_label.text()
        dialog.close()

    def test_next_hit_advances(self, qapp: QApplication, tmp_path: Path) -> None:
        """下一个按钮应前进到下一个命中。"""
        dialog = self._make_dialog_with_content(qapp, tmp_path, "password and password again", "password")
        assert dialog._current_hit_index == 0
        dialog._on_next_hit()
        assert dialog._current_hit_index == 1
        assert "2 / 2" in dialog._nav_label.text()
        dialog.close()

    def test_next_wraps_around(self, qapp: QApplication, tmp_path: Path) -> None:
        """到达最后一个命中后再下一个应回到首个。"""
        dialog = self._make_dialog_with_content(qapp, tmp_path, "password and password again", "password")
        dialog._on_next_hit()
        assert dialog._current_hit_index == 1
        dialog._on_next_hit()
        assert dialog._current_hit_index == 0
        dialog.close()

    def test_prev_wraps_around(self, qapp: QApplication, tmp_path: Path) -> None:
        """在首个命中时上一个应跳转到最后一个。"""
        dialog = self._make_dialog_with_content(qapp, tmp_path, "password and password again", "password")
        assert dialog._current_hit_index == 0
        dialog._on_prev_hit()
        assert dialog._current_hit_index == 1
        dialog.close()

    def test_no_hits_disables_buttons(self, qapp: QApplication, tmp_path: Path) -> None:
        """无关键词命中时按钮应禁用。"""
        dialog = self._make_dialog_with_content(qapp, tmp_path, "nothing here", "missing")
        assert len(dialog._hit_positions) == 0
        assert not dialog._prev_btn.isEnabled()
        assert not dialog._next_btn.isEnabled()
        assert "无命中" in dialog._nav_label.text()
        dialog.close()

    def test_empty_file_no_crash(self, qapp: QApplication, tmp_path: Path) -> None:
        """空文件不应导致导航异常。"""
        dialog = self._make_dialog_with_content(qapp, tmp_path, "", "keyword")
        assert len(dialog._hit_positions) == 0
        dialog.close()

    def test_nonexistent_file_no_crash(self, qapp: QApplication, tmp_path: Path) -> None:
        """文件不存在时不应导致导航异常。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner.result import RuleHit, ScanResult

        result = ScanResult(
            path=tmp_path / "nonexistent.txt",
            size=0,
            hits=(RuleHit("r", Severity.WARNING, "包含 'keyword'"),),
        )
        dialog = HitDetailDialog(result)
        assert len(dialog._hit_positions) == 0
        dialog.close()

    def test_multiple_keywords(self, qapp: QApplication, tmp_path: Path) -> None:
        """多个不同关键词的命中都应被找到。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner.result import RuleHit, ScanResult

        content = "password here and api_key there"
        path = tmp_path / "multi.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(
                RuleHit("r1", Severity.WARNING, "包含 'password'"),
                RuleHit("r2", Severity.CRITICAL, "包含 'api_key'"),
            ),
        )
        dialog = HitDetailDialog(result)
        assert len(dialog._hit_positions) == 2
        assert "1 / 2" in dialog._nav_label.text()
        dialog.close()

    def _make_dialog_multi_rule(self, qapp: QApplication, tmp_path: Path) -> HitDetailDialog:
        """构造多规则多位置命中对话框，用于测试点击跳转与位置数列。

        复用 ``_build_multi_rule_report`` 的扫描产物：
        - 规则"密码"匹配 password（2 处）
        - 规则"令牌"匹配 token（1 处）
        """
        report = _build_multi_rule_report(tmp_path)
        return HitDetailDialog(report.hits[0])

    def test_dialog_hits_table_has_position_count_column(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框命中规则表应包含5列，第4列为'位置数'。"""
        dialog = self._make_dialog_multi_rule(qapp, tmp_path)
        assert dialog._hits_table.columnCount() == 5
        assert dialog._hits_table.horizontalHeaderItem(3).text() == "位置数"
        dialog.close()

    def test_dialog_hits_table_position_count_values(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框位置数列应显示每条规则在预览中的高亮位置数。"""
        dialog = self._make_dialog_multi_rule(qapp, tmp_path)
        # 规则0(密码): 2处password, 规则1(令牌): 1处token
        assert dialog._hits_table.item(0, 3).text() == "2"
        assert dialog._hits_table.item(1, 3).text() == "1"
        dialog.close()

    def test_dialog_click_hits_row_jumps_to_rule_highlight(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框中点击规则表行应跳转到该规则对应的高亮位置。"""
        dialog = self._make_dialog_multi_rule(qapp, tmp_path)
        # 位置排序: [(0,8,0), (13,18,1), (23,31,0)]
        # 初始定位到位置0(规则0的首个password)
        assert dialog._current_hit_index == 0
        # 点击规则1(令牌) → 跳到位置1(token)
        dialog._on_hits_row_clicked(1, 0)
        assert dialog._current_hit_index == 1
        dialog.close()

    def test_dialog_click_hits_row_cycles_within_rule(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框中重复点击同一规则行应在该规则的位置间循环。"""
        dialog = self._make_dialog_multi_rule(qapp, tmp_path)
        # 初始在位置0(规则0)
        assert dialog._current_hit_index == 0
        # 再次点击规则0 → 跳到位置2(规则0的第二个password)
        dialog._on_hits_row_clicked(0, 0)
        assert dialog._current_hit_index == 2
        # 再次点击规则0 → 回到位置0(循环)
        dialog._on_hits_row_clicked(0, 0)
        assert dialog._current_hit_index == 0
        dialog.close()

    def test_dialog_click_hits_row_no_positions_no_crash(self, qapp: QApplication) -> None:
        """对话框无高亮位置时点击规则表行不应崩溃。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner.result import RuleHit, ScanResult

        result = ScanResult(
            path=Path("/nonexistent.txt"),
            size=0,
            hits=(RuleHit("r", Severity.WARNING, "包含 'keyword'"),),
        )
        dialog = HitDetailDialog(result)
        assert len(dialog._hit_positions) == 0
        dialog._on_hits_row_clicked(0, 0)
        assert dialog._current_hit_index == -1
        dialog.close()

    def test_dialog_filename_match_shows_dash(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框中文件名匹配规则的位置数列应显示'-'。"""
        report = _build_filename_match_report(tmp_path)
        dialog = HitDetailDialog(report.hits[0])
        assert dialog._hits_table.item(0, 3).text() == "-"
        assert dialog._hits_table.item(1, 3).text() == "1"
        dialog.close()

    def test_dialog_filename_match_detail_label(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框中文件名匹配规则的详情应追加'（仅文件名）'。"""
        report = _build_filename_match_report(tmp_path)
        dialog = HitDetailDialog(report.hits[0])
        assert "（仅文件名）" in dialog._hits_table.item(0, 4).text()
        assert "（仅文件名）" not in dialog._hits_table.item(1, 4).text()
        dialog.close()

    def test_dialog_info_label_shows_switchable_count(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框信息标签应显示'可切换位置'字段。"""
        report = _build_multi_rule_report(tmp_path)
        dialog = HitDetailDialog(report.hits[0])
        info_text = dialog._info_label.text()
        assert "可切换位置" in info_text
        dialog.close()

    def test_dialog_highlight_skips_out_of_range(self, qapp: QApplication) -> None:
        """对话框高亮位置超出文档长度时应跳过高亮，不调用 setPosition 越界。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner.result import RuleHit, ScanResult

        path = Path("/nonexistent.txt")
        result = ScanResult(
            path=path,
            size=0,
            hits=(RuleHit("r", Severity.WARNING, "包含 'keyword'"),),
        )
        dialog = HitDetailDialog(result)
        dialog._preview.setPlainText("short")
        dialog._hit_positions = [(0, 3, 0), (100, 200, 0)]
        dialog._current_hit_index = 1
        dialog._highlight_current_hit()
        dialog._scroll_to_current_hit()
        dialog.close()


class TestMatchTextHighlighting:
    """``match_text`` 字段驱动的关键词提取与跨行定位测试。

    覆盖数据库连接串密码与 Bearer 令牌的详情定位修复：
    - ``_extract_keywords`` 优先使用 ``match_text`` 而非从 detail 解析
    - 特殊字符（反斜杠、单引号）的关键词正确定位
    - 跨行 Bearer 令牌的换行规范化为 ``\\s+`` 后跨段落定位
    """

    def test_extract_keywords_prefers_match_text(self) -> None:
        """``_extract_keywords`` 应优先使用 ``match_text`` 而非从 detail 解析。"""
        from fuscan.gui.detail_dialog import _extract_keywords
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.CRITICAL, "正则命中: 'repr_escape'", match_text="raw_text"),)
        kws = _extract_keywords(hits)
        assert kws == ["raw_text"]

    def test_extract_keywords_match_text_with_backslash(self) -> None:
        """``match_text`` 含反斜杠时应原样使用，不经过 repr 转义。"""
        from fuscan.gui.detail_dialog import _extract_keywords
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.WARNING, "正则命中: 'pass\\\\123'", match_text=r"pass\123"),)
        kws = _extract_keywords(hits)
        assert kws == [r"pass\123"]

    def test_extract_keywords_match_text_with_single_quote(self) -> None:
        """``match_text`` 含单引号时应原样使用。"""
        from fuscan.gui.detail_dialog import _extract_keywords
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.WARNING, "正则命中", match_text="pa'ss"),)
        kws = _extract_keywords(hits)
        assert kws == ["pa'ss"]

    def test_extract_keywords_match_text_with_newline(self) -> None:
        """``match_text`` 含换行符时应原样保留，供跨行定位使用。"""
        from fuscan.gui.detail_dialog import _extract_keywords
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.INFO, "正则命中", match_text="Bearer\n  eyJhbGci"),)
        kws = _extract_keywords(hits)
        assert len(kws) == 1
        assert "\n" in kws[0]

    def test_extract_keywords_falls_back_to_detail_when_match_text_empty(self) -> None:
        """``match_text`` 为空时应回退到从 detail 中提取单引号内容。"""
        from fuscan.gui.detail_dialog import _extract_keywords
        from fuscan.scanner.result import RuleHit

        hits = (RuleHit("r", Severity.CRITICAL, "包含 'password'", match_text=""),)
        kws = _extract_keywords(hits)
        assert kws == ["password"]

    def test_dialog_positions_db_connection_with_backslash(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情对话框应能定位含反斜杠的数据库连接串密码。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner.result import RuleHit, ScanResult

        content = r"url=mongodb://user:pass\123@host"
        path = tmp_path / "db.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("数据库连接串", Severity.WARNING, "正则命中", match_text=r"mongodb://user:pass\123@"),),
        )
        dialog = HitDetailDialog(result)
        assert len(dialog._hit_positions) >= 1
        assert "1 /" in dialog._nav_label.text()
        dialog.close()

    def test_dialog_positions_db_connection_with_single_quote(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情对话框应能定位含单引号的数据库连接串密码。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner.result import RuleHit, ScanResult

        content = "url=mongodb://user:pa'ss@host"
        path = tmp_path / "db.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("数据库连接串", Severity.WARNING, "正则命中", match_text="mongodb://user:pa'ss@"),),
        )
        dialog = HitDetailDialog(result)
        assert len(dialog._hit_positions) >= 1
        assert "1 /" in dialog._nav_label.text()
        dialog.close()

    def test_dialog_positions_cross_line_bearer(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情对话框应能定位跨行 Bearer 令牌（换行规范化为 \\s+）。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner.result import RuleHit, ScanResult

        content = "Authorization: Bearer\n  eyJhbGci.token"
        path = tmp_path / "auth.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("Bearer令牌", Severity.INFO, "正则命中", match_text="Bearer\n  eyJhbGci"),),
        )
        dialog = HitDetailDialog(result)
        assert len(dialog._hit_positions) >= 1
        assert "1 /" in dialog._nav_label.text()
        dialog.close()

    def test_dialog_positions_single_line_bearer(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情对话框应能定位单行 Bearer 令牌。"""
        from fuscan.gui.detail_dialog import HitDetailDialog
        from fuscan.scanner.result import RuleHit, ScanResult

        content = "Authorization: Bearer eyJhbGci.token"
        path = tmp_path / "auth.txt"
        path.write_text(content, encoding="utf-8")
        result = ScanResult(
            path=path,
            size=len(content),
            hits=(RuleHit("Bearer令牌", Severity.INFO, "正则命中", match_text="Bearer eyJhbGci.token"),),
        )
        dialog = HitDetailDialog(result)
        assert len(dialog._hit_positions) >= 1
        assert "1 /" in dialog._nav_label.text()
        dialog.close()

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
        window._detail_show_result(result)
        assert len(window._detail_hit_positions) >= 1
        assert "1 /" in window._detail_nav_label.text()
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
        window._detail_show_result(result)
        assert len(window._detail_hit_positions) >= 1
        assert "1 /" in window._detail_nav_label.text()
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
        window._detail_show_result(result)
        assert len(window._detail_hit_positions) >= 1
        assert "1 /" in window._detail_nav_label.text()
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
        assert window._path_filter_input is not None
        assert window._rule_filter_combo is not None
        assert window._group_mode_combo is not None
        window.close()

    def test_header_sorting_enabled(self, qapp: QApplication) -> None:
        """结果树应启用表头排序。"""
        window = MainWindow()
        assert window._result_tree.isSortingEnabled()
        window.close()

    def test_column_count_includes_hit_count(self, qapp: QApplication) -> None:
        """结果树应包含命中数与条数列。"""
        window = MainWindow()
        assert window._result_tree.columnCount() == 6
        window.close()

    def test_rule_filter_populated_after_scan(self, qapp: QApplication, tmp_path: Path) -> None:
        """扫描后规则筛选下拉应填充规则名。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        combo = window._rule_filter_combo
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
        assert window._result_tree.topLevelItemCount() == 2  # secret.txt + key.txt

        window._path_filter_input.setText("secret")
        assert window._result_tree.topLevelItemCount() == 1
        assert "secret.txt" in window._result_tree.topLevelItem(0).text(0)
        window.close()

    def test_path_filter_case_insensitive(self, qapp: QApplication, tmp_path: Path) -> None:
        """路径筛选应大小写不敏感。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        window._path_filter_input.setText("SECRET")
        assert window._result_tree.topLevelItemCount() == 1
        window.close()

    def test_rule_filter(self, qapp: QApplication, tmp_path: Path) -> None:
        """规则筛选应只显示包含该规则命中的文件。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window._rule_filter_combo.findData("密钥内容")
        assert idx >= 0
        window._rule_filter_combo.setCurrentIndex(idx)
        # secret.txt 同时命中"密钥内容"（内容含 key），key.txt 也命中"密钥内容"
        count = window._result_tree.topLevelItemCount()
        assert count >= 1
        window.close()

    def test_combined_path_and_rule_filter(self, qapp: QApplication, tmp_path: Path) -> None:
        """路径+规则组合筛选。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        window._path_filter_input.setText("key.txt")
        idx = window._rule_filter_combo.findData("密钥内容")
        window._rule_filter_combo.setCurrentIndex(idx)
        assert window._result_tree.topLevelItemCount() == 1
        assert "key.txt" in window._result_tree.topLevelItem(0).text(0)
        window.close()

    def test_no_results_after_filter(self, qapp: QApplication, tmp_path: Path) -> None:
        """筛选无匹配时结果树为空。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        window._path_filter_input.setText("nonexistent_path")
        assert window._result_tree.topLevelItemCount() == 0
        window.close()

    def test_clear_path_filter_restores_results(self, qapp: QApplication, tmp_path: Path) -> None:
        """清空路径筛选应恢复全部结果。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)
        window._path_filter_input.setText("secret")
        assert window._result_tree.topLevelItemCount() == 1
        window._path_filter_input.setText("")
        assert window._result_tree.topLevelItemCount() == 2
        window.close()

    def test_group_by_rule(self, qapp: QApplication, tmp_path: Path) -> None:
        """按规则分组：顶层项为规则名。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window._group_mode_combo.findData("rule")
        window._group_mode_combo.setCurrentIndex(idx)
        top_count = window._result_tree.topLevelItemCount()
        assert top_count == 2  # 两个规则
        rule_names = {window._result_tree.topLevelItem(i).text(1) for i in range(top_count)}
        assert "敏感文件名" in rule_names
        assert "密钥内容" in rule_names
        window.close()

    def test_group_by_severity(self, qapp: QApplication, tmp_path: Path) -> None:
        """按严重等级分组：顶层项为严重等级。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window._group_mode_combo.findData("severity")
        window._group_mode_combo.setCurrentIndex(idx)
        top_count = window._result_tree.topLevelItemCount()
        assert top_count == 2  # warning + critical
        severities = {window._result_tree.topLevelItem(i).text(2) for i in range(top_count)}
        assert "警告" in severities
        assert "严重" in severities
        window.close()

    def test_group_by_rule_children_have_user_data(self, qapp: QApplication, tmp_path: Path) -> None:
        """按规则分组时子项应携带 ScanResult 供双击使用。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window._group_mode_combo.findData("rule")
        window._group_mode_combo.setCurrentIndex(idx)
        top = window._result_tree.topLevelItem(0)
        assert top.childCount() > 0
        child = top.child(0)
        assert child.data(0, Qt.UserRole) is not None
        window.close()

    def test_double_click_grouped_child_opens_dialog(self, qapp: QApplication, tmp_path: Path) -> None:
        """分组模式下双击子项应打开详情对话框。"""
        from fuscan.gui import detail_dialog as dd_module

        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window._group_mode_combo.findData("rule")
        window._group_mode_combo.setCurrentIndex(idx)

        called = {"count": 0}

        def fake_exec(self) -> int:  # type: ignore[no-untyped-def]
            called["count"] += 1
            return 1

        monkeypatch_obj = pytest.MonkeyPatch()
        monkeypatch_obj.setattr(dd_module.HitDetailDialog, "exec_", fake_exec)
        top = window._result_tree.topLevelItem(0)
        child = top.child(0)
        window._on_result_double_clicked(child, 0)
        assert called["count"] == 1
        monkeypatch_obj.undo()
        window.close()

    def test_refresh_with_no_report(self, qapp: QApplication) -> None:
        """无报告时刷新结果树不应异常。"""
        window = MainWindow()
        window._last_report = None
        window._refresh_result_tree()
        assert window._result_tree.topLevelItemCount() == 0
        window.close()

    def test_rule_filter_restored_after_repopulate(self, qapp: QApplication, tmp_path: Path) -> None:
        """重新填充结果时之前选中的规则筛选应恢复。"""
        window = MainWindow()
        report = _build_multi_hit_report(tmp_path)
        window._populate_results(report)

        idx = window._rule_filter_combo.findData("密钥内容")
        window._rule_filter_combo.setCurrentIndex(idx)
        assert window._rule_filter_combo.currentData() == "密钥内容"

        # 重新填充应恢复选中的规则
        window._populate_results(report)
        assert window._rule_filter_combo.currentData() == "密钥内容"
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
        for i in range(window._result_tree.topLevelItemCount()):
            item = window._result_tree.topLevelItem(i)
            if item.text(0).endswith("key.txt"):
                key_item = item
                break
        assert key_item is not None
        # 列 3=命中数（规则数），列 4=条数（匹配处数）
        assert key_item.text(3) == "1"
        assert key_item.text(4) == "3"
        window.close()

    def test_flat_child_item_shows_rule_match_count(self, qapp: QApplication, tmp_path: Path) -> None:
        """不分组模式下子项（规则命中）的条数列应显示该规则的匹配条数。"""
        window = MainWindow()
        report = _build_multi_match_report(tmp_path)
        window._populate_results(report)

        # 找到 key.txt 顶层项，检查其子项
        key_item = None
        for i in range(window._result_tree.topLevelItemCount()):
            item = window._result_tree.topLevelItem(i)
            if item.text(0).endswith("key.txt"):
                key_item = item
                break
        assert key_item is not None
        assert key_item.childCount() == 1  # 仅"密钥"规则命中
        child = key_item.child(0)
        # 子项列 1=规则名，列 4=条数
        assert child.text(1) == "密钥"
        assert child.text(4) == "3"
        window.close()

    def test_group_by_rule_shows_match_sum(self, qapp: QApplication, tmp_path: Path) -> None:
        """按规则分组时顶层项的条数列应为该规则所有文件匹配条数之和。"""
        window = MainWindow()
        report = _build_multi_match_report(tmp_path)
        window._populate_results(report)

        idx = window._group_mode_combo.findData("rule")
        window._group_mode_combo.setCurrentIndex(idx)

        # "密钥"规则在 key.txt 命中 3 处
        rule_item = None
        for i in range(window._result_tree.topLevelItemCount()):
            item = window._result_tree.topLevelItem(i)
            if item.text(1) == "密钥":
                rule_item = item
                break
        assert rule_item is not None
        assert rule_item.text(4) == "3"
        window.close()

    def test_detail_hits_table_shows_match_count(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情区命中规则表应显示条数列。"""
        window = MainWindow()
        report = _build_multi_match_report(tmp_path)
        window._populate_results(report)

        # 选中 key.txt 触发详情区更新
        for i in range(window._result_tree.topLevelItemCount()):
            item = window._result_tree.topLevelItem(i)
            if item.text(0).endswith("key.txt"):
                item.setSelected(True)
                break

        # 命中表列 2=条数
        assert window._detail_hits_table.rowCount() == 1
        assert window._detail_hits_table.item(0, 2).text() == "3"
        window.close()

    def test_detail_info_label_shows_match_count(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情区文件信息应显示匹配条数。"""
        window = MainWindow()
        report = _build_multi_match_report(tmp_path)
        window._populate_results(report)

        for i in range(window._result_tree.topLevelItemCount()):
            item = window._result_tree.topLevelItem(i)
            if item.text(0).endswith("key.txt"):
                item.setSelected(True)
                break

        info_text = window._detail_info_label.text()
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
        assert window._edit_rule_btn is not None
        assert window._edit_rule_btn.text() == "编辑"
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
        assert dialog._file_combo.count() == 1
        assert dialog._file_combo.itemText(0) == "rules.yaml"
        content = dialog._editor.toPlainText()
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
        assert "测试规则" in dialog._editor.toPlainText()
        dialog._file_combo.setCurrentIndex(1)
        assert "规则二" in dialog._editor.toPlainText()
        dialog.close()

    def test_save_writes_file(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """保存应写入文件。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog._editor.setPlainText(
            'version: "1.0"\nrules:\n  - name: 新规则\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: test\n',
        )

        monkeypatch.setattr(
            "fuscan.gui.rule_editor.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        saved_paths: list[str] = []
        dialog.rules_saved.connect(saved_paths.append)
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
        dialog._editor.setPlainText("invalid: yaml: content: [")

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
        dialog._editor.setPlainText("modified content")
        dialog._on_reload()
        assert "测试规则" in dialog._editor.toPlainText()
        dialog.close()

    def test_empty_rules_paths(self, qapp: QApplication) -> None:
        """无规则文件时编辑器应显示提示。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        dialog = RuleEditorDialog([])
        assert dialog._file_combo.count() == 0
        assert not dialog._editor.isEnabled()
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
        assert dialog._editor.toPlainText() == ""
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

        assert "读取文件失败" in dialog._editor.toPlainText()
        assert not dialog._editor.isEnabled()
        dialog.close()

    def test_save_invalid_index_does_nothing(self, qapp: QApplication, tmp_path: Path) -> None:
        """无效索引时保存不应执行任何操作。"""
        from fuscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        saved_paths: list[str] = []
        dialog.rules_saved.connect(saved_paths.append)

        # 模拟无效索引
        dialog._file_combo.setCurrentIndex(-1)
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
        dialog._editor.setPlainText("version: '1.0'\nrules: []\n")

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
        dialog.rules_saved.connect(saved_paths.append)
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
        dialog._editor.setPlainText('version: "1.0"\nrules:\n  - name: bad\n    severity: warning\n')

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
        dialog.rules_saved.connect(saved_paths.append)
        dialog._on_save()

        assert warned["called"]
        assert len(saved_paths) == 1
        dialog.close()


class TestMainWindowHelpers:
    """main_window.py 模块级辅助函数测试。"""

    def test_format_size_bytes(self) -> None:
        """_format_size 应正确格式化字节数。"""
        from fuscan.gui.main_window import _format_size

        assert _format_size(0) == "0 B"
        assert _format_size(512) == "512 B"
        assert _format_size(1024) == "1.0 KB"
        assert _format_size(1024 * 1024) == "1.0 MB"
        assert _format_size(1024 * 1024 * 1024) == "1.00 GB"

    def test_extract_keywords(self) -> None:
        """_extract_keywords 应从 detail 中提取单引号包裹的关键词。"""
        from fuscan.gui.main_window import _extract_keywords
        from fuscan.rules.model import Severity
        from fuscan.scanner.result import RuleHit

        hits = [
            RuleHit(rule_name="r1", severity=Severity.WARNING, detail="包含 'password'"),
            RuleHit(rule_name="r2", severity=Severity.CRITICAL, detail="正则命中: 'AKIA1234'"),
            RuleHit(rule_name="r3", severity=Severity.INFO, detail="无关键词"),
        ]
        keywords = _extract_keywords(hits)
        assert keywords == ["password", "AKIA1234"]

    def test_extract_keywords_dedup(self) -> None:
        """_extract_keywords 应去重相同关键词。"""
        from fuscan.gui.main_window import _extract_keywords
        from fuscan.rules.model import Severity
        from fuscan.scanner.result import RuleHit

        hits = [
            RuleHit(rule_name="r1", severity=Severity.WARNING, detail="包含 'secret'"),
            RuleHit(rule_name="r2", severity=Severity.WARNING, detail="包含 'secret'"),
        ]
        keywords = _extract_keywords(hits)
        assert keywords == ["secret"]

    def test_build_preview_html_no_keywords(self) -> None:
        """_build_preview_html 无关键词时只转义不高亮。"""
        from fuscan.gui.main_window import _build_preview_html

        result = _build_preview_html("hello & world", [])
        assert "hello &amp; world" in result
        assert "<span" not in result

    def test_build_preview_html_with_keywords(self) -> None:
        """_build_preview_html 有关键词时应高亮。"""
        from fuscan.gui.main_window import _build_preview_html

        result = _build_preview_html("hello password world", ["password"])
        assert "<span" in result
        assert "password" in result

    def test_build_preview_html_escapes_html(self) -> None:
        """_build_preview_html 应先转义再高亮，避免 XSS。"""
        from fuscan.gui.main_window import _build_preview_html

        result = _build_preview_html("<script>alert(1)</script>", ["script"])
        # 原始 <script> 标签不应原样出现（已转义）
        assert "<script>" not in result
        assert "&lt;" in result
        assert "&gt;" in result


class TestDetailArea:
    """详情区两态切换与命中导航测试。"""

    def test_detail_empty_state_initially(self, qapp: QApplication) -> None:
        """启动时详情区应在空态。"""
        window = MainWindow()
        assert window._detail_action_stack.currentIndex() == 0
        assert window._detail_main_stack.currentIndex() == 0
        window.close()

    def test_detail_clear(self, qapp: QApplication) -> None:
        """_detail_clear 应切换到空态并清空内容。"""
        window = MainWindow()
        window._detail_action_stack.setCurrentIndex(1)
        window._detail_main_stack.setCurrentIndex(1)
        window._detail_current_result = object()  # type: ignore[assignment]
        window._detail_hit_positions = [(0, 1, 0)]
        window._detail_current_hit_index = 0
        window._detail_clear()
        assert window._detail_action_stack.currentIndex() == 0
        assert window._detail_main_stack.currentIndex() == 0
        assert window._detail_current_result is None
        assert window._detail_hit_positions == []
        assert window._detail_current_hit_index == -1
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
        item = window._result_tree.topLevelItem(0)
        window._result_tree.setCurrentItem(item)
        # 详情区应切换到非空态
        assert window._detail_action_stack.currentIndex() == 1
        assert window._detail_main_stack.currentIndex() == 1
        assert window._detail_current_result is not None
        # 命中表应有行
        assert window._detail_hits_table.rowCount() > 0
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
        window._group_mode_combo.setCurrentText("按规则分组")
        window._populate_results(report)
        # 选中第一个子项
        top = window._result_tree.topLevelItem(0)
        if top.childCount() > 0:
            child = top.child(0)
            window._result_tree.setCurrentItem(child)
            assert window._detail_action_stack.currentIndex() == 1
        window.close()

    def test_detail_selection_no_items(self, qapp: QApplication) -> None:
        """无选中项时详情区应清空。"""
        window = MainWindow()
        window._detail_action_stack.setCurrentIndex(1)
        window._detail_main_stack.setCurrentIndex(1)
        window._on_result_selection_changed()
        assert window._detail_action_stack.currentIndex() == 0
        assert window._detail_main_stack.currentIndex() == 0
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
        item = window._result_tree.topLevelItem(0)
        window._result_tree.setCurrentItem(item)

        total = len(window._detail_hit_positions)
        if total > 1:
            # 下一个命中
            window._on_next_detail_hit()
            assert window._detail_current_hit_index == 1
            # 上一个命中（回到 0）
            window._on_prev_detail_hit()
            assert window._detail_current_hit_index == 0
            # 导航标签应显示 "1 / total"
            assert "1" in window._detail_nav_label.text()
            assert str(total) in window._detail_nav_label.text()
        window.close()

    def test_detail_nav_label_no_hits(self, qapp: QApplication) -> None:
        """无命中时导航标签应显示"无命中"且按钮禁用。"""
        window = MainWindow()
        window._detail_hit_positions = []
        window._detail_current_hit_index = -1
        window._update_detail_nav_label()
        assert "无命中" in window._detail_nav_label.text()
        assert not window._detail_prev_btn.isEnabled()
        assert not window._detail_next_btn.isEnabled()
        window.close()

    def test_detail_nav_label_with_hits(self, qapp: QApplication) -> None:
        """有命中时导航标签应显示进度且按钮启用。"""
        window = MainWindow()
        window._detail_hit_positions = [(0, 1, 0), (5, 6, 0)]
        window._detail_current_hit_index = 0
        window._update_detail_nav_label()
        assert "1 / 2" in window._detail_nav_label.text()
        assert window._detail_prev_btn.isEnabled()
        assert window._detail_next_btn.isEnabled()
        window.close()

    def test_detail_prev_next_wrap_around(self, qapp: QApplication) -> None:
        """命中导航应在到达首尾时循环。"""
        window = MainWindow()
        window._detail_hit_positions = [(0, 1, 0), (5, 6, 0)]
        window._detail_current_hit_index = 0
        # 在索引 0 时上一个应循环到最后
        window._on_prev_detail_hit()
        assert window._detail_current_hit_index == 1
        # 在索引 1 时下一个应循环到第一个
        window._on_next_detail_hit()
        assert window._detail_current_hit_index == 0
        window.close()

    def test_detail_prev_next_no_hits(self, qapp: QApplication) -> None:
        """无命中时上一个/下一个不应崩溃。"""
        window = MainWindow()
        window._detail_hit_positions = []
        window._detail_current_hit_index = -1
        window._on_prev_detail_hit()
        window._on_next_detail_hit()
        assert window._detail_current_hit_index == -1
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
        item = window._result_tree.topLevelItem(0)
        window._result_tree.setCurrentItem(item)
        window._on_copy_path()
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        assert "secret.txt" in clipboard.text()
        window.close()

    def test_detail_copy_path_no_result(self, qapp: QApplication) -> None:
        """无选中结果时复制路径不应崩溃。"""
        window = MainWindow()
        window._on_copy_path()
        window.close()

    def test_detail_open_in_window_no_result(self, qapp: QApplication) -> None:
        """无选中结果时打开窗口不应崩溃。"""
        window = MainWindow()
        window._on_open_in_window()
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
        item = window._result_tree.topLevelItem(0)
        window._result_tree.setCurrentItem(item)
        # 空文件应显示提示
        text = window._detail_preview.toPlainText()
        assert "空" in text or "二进制" in text
        window.close()

    def test_result_tree_context_menu_actions(self, qapp: QApplication, tmp_path: Path) -> None:
        """结果树右键菜单应包含复制路径/新窗口打开/打开文件位置三个动作。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password=123", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        item = window._result_tree.topLevelItem(0)
        window._result_tree.setCurrentItem(item)
        assert window._detail_current_result is not None

        captured: list[Any] = []
        from fuscan.gui import main_window as mw_module

        original_qmenu = mw_module.QMenu

        class FakeQMenu(QMenu):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.exec_ = lambda *a, **kw: None
                captured.append(self)

        mw_module.QMenu = FakeQMenu
        try:
            window._on_result_tree_context_menu(window._result_tree.viewport().rect().center())
        finally:
            mw_module.QMenu = original_qmenu

        assert len(captured) == 1
        actions = captured[0].actions()
        assert len(actions) == 3
        texts = [a.text() for a in actions]
        assert "复制路径" in texts
        assert "在新窗口打开" in texts
        assert "打开文件位置" in texts
        window.close()

    def test_result_tree_context_menu_no_selection(self, qapp: QApplication) -> None:
        """无选中结果时右键菜单不应弹出。"""
        window = MainWindow()
        assert window._detail_current_result is None

        from fuscan.gui import main_window as mw_module

        original_qmenu = mw_module.QMenu
        call_count = {"n": 0}

        class FakeQMenu(QMenu):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                call_count["n"] += 1
                self.exec_ = lambda *a, **kw: None

        mw_module.QMenu = FakeQMenu
        try:
            window._on_result_tree_context_menu(window._result_tree.viewport().rect().center())
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
        window._rules_file_list.setCurrentRow(0)

        captured: list[Any] = []
        from fuscan.gui import main_window as mw_module

        original_qmenu = mw_module.QMenu

        class FakeQMenu(QMenu):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.exec_ = lambda *a, **kw: None
                captured.append(self)

        mw_module.QMenu = FakeQMenu
        try:
            window._on_rules_file_list_context_menu(window._rules_file_list.viewport().rect().center())
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
            from PySide6.QtGui import QKeySequence

        window = MainWindow()
        assert window._shortcut_next.key().toString() == QKeySequence("F3").toString()
        assert window._shortcut_prev.key().toString() == QKeySequence("Shift+F3").toString()
        assert window._shortcut_remove_rule.key().toString() == QKeySequence(QKeySequence.Delete).toString()
        window.close()

    def test_shortcut_next_triggers_nav(self, qapp: QApplication) -> None:
        """F3 快捷键的 activated 信号应触发下一条命中导航。"""
        window = MainWindow()
        window._detail_hit_positions = [(0, 1, 0), (5, 6, 0)]
        window._detail_current_hit_index = 0
        window._shortcut_next.activated.emit()
        assert window._detail_current_hit_index == 1
        window.close()

    def test_shortcut_prev_triggers_nav(self, qapp: QApplication) -> None:
        """Shift+F3 快捷键的 activated 信号应触发上一条命中导航。"""
        window = MainWindow()
        window._detail_hit_positions = [(0, 1, 0), (5, 6, 0)]
        window._detail_current_hit_index = 1
        window._shortcut_prev.activated.emit()
        assert window._detail_current_hit_index == 0
        window.close()

    def test_detail_hits_table_has_position_count_column(self, qapp: QApplication, tmp_path: Path) -> None:
        """命中规则表应包含5列，第4列为'位置数'。"""
        report = _build_multi_rule_report(tmp_path)
        window = MainWindow()
        window._detail_show_result(report.hits[0])
        assert window._detail_hits_table.columnCount() == 5
        assert window._detail_hits_table.horizontalHeaderItem(3).text() == "位置数"
        window.close()

    def test_detail_hits_table_position_count_values(self, qapp: QApplication, tmp_path: Path) -> None:
        """位置数列应显示每条规则在预览中的高亮位置数。"""
        report = _build_multi_rule_report(tmp_path)
        window = MainWindow()
        window._detail_show_result(report.hits[0])
        # 规则0(密码): 2处password, 规则1(令牌): 1处token
        assert window._detail_hits_table.item(0, 3).text() == "2"
        assert window._detail_hits_table.item(1, 3).text() == "1"
        window.close()

    def test_click_hits_row_jumps_to_rule_highlight(self, qapp: QApplication, tmp_path: Path) -> None:
        """点击规则表行应跳转到该规则对应的高亮位置。"""
        report = _build_multi_rule_report(tmp_path)
        window = MainWindow()
        window._detail_show_result(report.hits[0])
        # 位置排序: [(0,8,0), (13,18,1), (23,31,0)]
        # 初始定位到位置0(规则0的首个password)
        assert window._detail_current_hit_index == 0
        # 点击规则1(令牌) → 跳到位置1(token)
        window._on_detail_hits_row_clicked(1, 0)
        assert window._detail_current_hit_index == 1
        window.close()

    def test_click_hits_row_cycles_within_rule(self, qapp: QApplication, tmp_path: Path) -> None:
        """重复点击同一规则行应在该规则的位置间循环。"""
        report = _build_multi_rule_report(tmp_path)
        window = MainWindow()
        window._detail_show_result(report.hits[0])
        # 初始在位置0(规则0)
        assert window._detail_current_hit_index == 0
        # 再次点击规则0 → 跳到位置2(规则0的第二个password)
        window._on_detail_hits_row_clicked(0, 0)
        assert window._detail_current_hit_index == 2
        # 再次点击规则0 → 回到位置0(循环)
        window._on_detail_hits_row_clicked(0, 0)
        assert window._detail_current_hit_index == 0
        window.close()

    def test_click_hits_row_no_positions_no_crash(self, qapp: QApplication) -> None:
        """无高亮位置时点击规则表行不应崩溃。"""
        window = MainWindow()
        window._detail_hit_positions = []
        window._detail_current_hit_index = -1
        window._on_detail_hits_row_clicked(0, 0)
        assert window._detail_current_hit_index == -1
        window.close()

    def test_filename_match_position_count_shows_dash(self, qapp: QApplication, tmp_path: Path) -> None:
        """文件名匹配规则的位置数列应显示'-'而非数字。"""
        report = _build_filename_match_report(tmp_path)
        window = MainWindow()
        window._detail_show_result(report.hits[0])
        # 规则0(文件名): target="filename" → 位置数列显示"-"
        assert window._detail_hits_table.item(0, 3).text() == "-"
        # 规则1(密码): target="content" → 位置数列显示"1"（password在预览中出现1次）
        assert window._detail_hits_table.item(1, 3).text() == "1"
        window.close()

    def test_filename_match_detail_shows_filename_label(self, qapp: QApplication, tmp_path: Path) -> None:
        """文件名匹配规则的详情列应追加'（仅文件名）'提示。"""
        report = _build_filename_match_report(tmp_path)
        window = MainWindow()
        window._detail_show_result(report.hits[0])
        detail_text = window._detail_hits_table.item(0, 4).text()
        assert "（仅文件名）" in detail_text
        # 内容匹配规则不应有此标记
        content_detail = window._detail_hits_table.item(1, 4).text()
        assert "（仅文件名）" not in content_detail
        window.close()

    def test_filename_match_skips_content_highlight(self, qapp: QApplication, tmp_path: Path) -> None:
        """文件名匹配规则的关键词不应在内容预览中搜索高亮位置。"""
        report = _build_filename_match_report(tmp_path)
        window = MainWindow()
        window._detail_show_result(report.hits[0])
        # 文件名"secret_config.txt"含"secret"，但内容"password=abc\ntoken=xyz"不含"secret"
        # 若错误搜索内容，"secret"不在内容中，不会产生位置；但若文件名恰好也在内容中则会产生误导
        # 此处验证：所有高亮位置都不归属到规则0(文件名)
        for _, _, rule_idx in window._detail_hit_positions:
            assert rule_idx != 0, "文件名匹配规则不应有内容高亮位置"
        window.close()

    def test_detail_info_label_shows_switchable_count(self, qapp: QApplication, tmp_path: Path) -> None:
        """详情信息标签应显示'可切换位置'字段。"""
        report = _build_multi_rule_report(tmp_path)
        window = MainWindow()
        window._detail_show_result(report.hits[0])
        info_text = window._detail_info_label.text()
        assert "可切换位置" in info_text
        # multi_rule.txt: password×2 + token×1 = 3个位置
        assert "3" in info_text
        window.close()

    def test_highlight_skips_out_of_range_position(self, qapp: QApplication) -> None:
        """高亮位置超出文档长度时应跳过高亮，不调用 setPosition 越界。"""
        window = MainWindow()
        window._detail_preview.setPlainText("short")
        # 设置一个超出文档长度的位置
        window._detail_hit_positions = [(0, 3, 0), (100, 200, 0)]
        window._detail_current_hit_index = 1
        # 不应抛出异常
        window._highlight_current_detail_hit()
        window._scroll_to_current_detail_hit()
        # 第一个位置（在范围内）应正常高亮
        window._detail_current_hit_index = 0
        window._highlight_current_detail_hit()
        window._scroll_to_current_detail_hit()
        window.close()

    def test_rules_file_list_context_menu_no_selection(self, qapp: QApplication) -> None:
        """规则文件列表无选中项时右键菜单不应弹出。"""
        window = MainWindow()
        assert window._rules_file_list.currentRow() < 0

        from fuscan.gui import main_window as mw_module

        original_qmenu = mw_module.QMenu
        call_count = {"n": 0}

        class FakeQMenu(QMenu):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                call_count["n"] += 1
                self.exec_ = lambda *a, **kw: None

        mw_module.QMenu = FakeQMenu
        try:
            window._on_rules_file_list_context_menu(window._rules_file_list.viewport().rect().center())
        finally:
            mw_module.QMenu = original_qmenu

        assert call_count["n"] == 0
        window.close()

    def test_on_open_file_location_win32(self, qapp: QApplication, tmp_path: Path) -> None:
        """_on_open_file_location 在 Windows 应调用 explorer 命令。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password=123", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        window = MainWindow()
        window._populate_results(report)
        item = window._result_tree.topLevelItem(0)
        window._result_tree.setCurrentItem(item)
        assert window._detail_current_result is not None

        popen_calls: list[Any] = []
        import subprocess as subprocess_mod

        original_popen = subprocess_mod.Popen
        subprocess_mod.Popen = lambda *args, **kwargs: popen_calls.append(args)  # type: ignore[assignment]
        try:
            window._on_open_file_location()
        finally:
            subprocess_mod.Popen = original_popen

        assert len(popen_calls) == 1
        window.close()

    def test_on_open_file_location_no_result(self, qapp: QApplication) -> None:
        """无选中结果时打开文件位置不应崩溃。"""
        window = MainWindow()
        window._on_open_file_location()
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
        item = window._result_tree.topLevelItem(0)
        window._result_tree.setCurrentItem(item)
        assert window._detail_current_result is not None

        window._on_copy_path()
        assert "已复制" in window._stats_label.text()
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
        window._progress.setVisible(True)
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
        assert window._progress.value() == 50
        assert window._progress.maximum() == 100
        assert "50" in window._stats_label.text()
        window.close()

    def test_on_scan_progress_long_path(self, qapp: QApplication) -> None:
        """_on_scan_progress 应截断过长的文件路径。"""
        from fuscan.scanner.result import ProgressInfo

        window = MainWindow()
        long_path = "/" + "a" * 200 + ".txt"
        info = ProgressInfo(total=10, scanned=1, skipped=0, matched=0, errors=0, current_file=long_path, elapsed=0.1)
        window._on_scan_progress(info)
        label_text = window._current_file_label.text()
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
        assert window._skipped_dirs_list.count() == 2
        items = [window._skipped_dirs_list.item(i).text() for i in range(window._skipped_dirs_list.count())]
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
        assert window._matched_files_list.count() == 2
        text0 = window._matched_files_list.item(0).text()
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
        stats_text = window._stats_label.text()
        # 计数与时间应汇总到状态栏文本
        assert "100" in stats_text
        assert "50" in stats_text
        assert "30" in stats_text
        assert "5" in stats_text
        assert "10.0s" in stats_text
        # 速度 = 100 / 10.0 = 10 文件/s
        assert "10" in stats_text
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
        assert window._skipped_dirs_list.count() == 1

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
        assert window._skipped_dirs_list.count() == 1  # 仍为 1，节流生效
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
        assert window._skipped_dirs_list.count() == 2

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
        assert window._skipped_dirs_list.count() == 3
        # 前两项应保持不变（未 clear 重建）
        assert window._skipped_dirs_list.item(0).text() == "/dir1"
        assert window._skipped_dirs_list.item(1).text() == "/dir2"
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
        assert window._skipped_dirs_list.count() == 2
        assert window._skipped_dirs_list.item(0).text() == "/dir3"
        assert window._skipped_dirs_list.item(1).text() == "/dir4"
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
        assert window._matched_files_list.count() == 1

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
        assert window._matched_files_list.count() == 2
        assert window._matched_files_list.item(0).text() == "/p/a.py → 规则A"
        assert window._matched_files_list.item(1).text() == "/p/b.py → 规则B"
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
        assert "扫描失败" in window._stats_label.text()
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
        assert "已取消" in window._stats_label.text()
        assert window._result_tree.topLevelItemCount() > 0
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
        window._worker = ScanWorker(scanner, tmp_path)
        window._scan_state = ScanState.RUNNING

        window._pause_scan()
        assert window._scan_state == ScanState.PAUSED
        assert "继续" in window._pause_resume_btn.text()

        window._resume_scan()
        assert window._scan_state == ScanState.RUNNING
        assert "暂停" in window._pause_resume_btn.text()

        window._worker = None
        window.close()


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

    def test_about_dialog(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """关于对话框应弹出。"""
        window = MainWindow()
        about_called = {"called": False}
        monkeypatch.setattr(
            "fuscan.gui.main_window.QMessageBox.about",
            lambda *args, **kwargs: about_called.update(called=True),
        )
        window._on_about()
        assert about_called["called"]
        window.close()

    def test_history_item_double_clicked(self, qapp: QApplication, tmp_path: Path) -> None:
        """双击历史项应设置扫描路径。"""
        try:
            from PySide2.QtWidgets import QListWidgetItem
        except ImportError:  # pragma: no cover
            from PySide6.QtWidgets import QListWidgetItem

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
            from PySide6.QtGui import QCloseEvent

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
        assert window._rules_file_list.count() == 1

        window._rules_file_list.setCurrentRow(0)
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
            from PySide6.QtWidgets import QDialog

        window = MainWindow()
        # settings_action 应存在并连接到 _on_settings
        assert window._settings_action is not None
        assert window._settings_action.text() == "设置..."
        # 设置 action 应在文件菜单中
        file_actions = window._ui.file_menu.actions()
        settings_texts = [a.text() for a in file_actions]
        assert "设置..." in settings_texts
        # 帮助菜单不应包含设置
        help_actions = window._ui.help_menu.actions()
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
        """_save_config 应将控件值保存到配置，get_config 应返回当前配置。"""
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
        dialog._ui.max_workers_spin.setValue(8)
        dialog._ui.max_depth_spin.setValue(20)
        dialog._ui.scan_archives_check.setChecked(True)
        dialog._ui.include_network_check.setChecked(False)
        dialog._ui.use_builtin_check.setChecked(True)

        dialog._save_config()

        assert config.max_workers == 8
        assert config.max_depth == 20
        assert config.scan_archives is True
        assert config.include_network_drives is False
        assert config.use_builtin is True

        # get_config 返回同一配置对象
        assert dialog.get_config() is config
        dialog.close()

    def test_settings_dialog_save_config_depth_zero(self, qapp: QApplication) -> None:
        """max_depth 为 0 时应保存为 None。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.max_depth = 5

        dialog = SettingsDialog(config)
        dialog._ui.max_depth_spin.setValue(0)
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
        dialog._ui.max_workers_spin.setValue(16)

        accepted_called: list[bool] = []
        monkeypatch.setattr(dialog, "accept", lambda: accepted_called.append(True))

        dialog._on_accept()

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
        assert dialog._ui.cache_enabled_check.isChecked() is False
        assert dialog._ui.cache_path_edit.text() == "/tmp/custom_cache.db"
        dialog.close()

    def test_settings_dialog_saves_cache_config(self, qapp: QApplication) -> None:
        """_save_config 应将缓存控件值保存到配置。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        config.cache_enabled = True
        config.cache_path = None

        dialog = SettingsDialog(config)
        dialog._ui.cache_enabled_check.setChecked(False)
        dialog._ui.cache_path_edit.setText("/tmp/new_cache.db")
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
        dialog._ui.cache_path_edit.setText("   ")
        dialog._save_config()

        assert config.cache_path is None
        dialog.close()


class TestSettingsDialogIgnore:
    """设置对话框忽略项控件测试。"""

    def test_ignore_widgets_exist_with_placeholders(self, qapp: QApplication) -> None:
        """对话框应包含忽略目录与忽略扩展名编辑控件，并设置占位提示。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(Config())
        assert dialog._ui.ignore_dirs_edit is not None
        assert dialog._ui.ignore_extensions_edit is not None
        assert "目录名" in dialog._ui.ignore_dirs_edit.placeholderText()
        assert "扩展名" in dialog._ui.ignore_extensions_edit.placeholderText()
        dialog.close()

    def test_default_ignore_dirs_loaded(self, qapp: QApplication) -> None:
        """默认 Config 的 ignore_dirs 应加载到编辑器。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(Config())
        lines = dialog._ui.ignore_dirs_edit.toPlainText().splitlines()
        assert ".git" in lines
        assert "node_modules" in lines
        assert "__pycache__" in lines
        dialog.close()

    def test_default_ignore_extensions_loaded(self, qapp: QApplication) -> None:
        """默认 Config 的 ignore_extensions 应加载到编辑器。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(Config())
        lines = dialog._ui.ignore_extensions_edit.toPlainText().splitlines()
        assert "pyc" in lines
        assert "exe" in lines
        assert "zip" in lines
        dialog.close()

    def test_custom_ignore_dirs_loaded(self, qapp: QApplication) -> None:
        """自定义 ignore_dirs 应按行加载到编辑器。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config(ignore_dirs=["custom_dir", ".git"])
        dialog = SettingsDialog(config)
        assert dialog._ui.ignore_dirs_edit.toPlainText().splitlines() == ["custom_dir", ".git"]
        dialog.close()

    def test_save_config_writes_ignore_dirs(self, qapp: QApplication) -> None:
        """_save_config 应将编辑器文本按行写入 config.ignore_dirs，过滤空行。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        dialog = SettingsDialog(config)
        dialog._ui.ignore_dirs_edit.setPlainText("new_dir\n.git\n\n  \n")
        dialog._save_config()
        assert config.ignore_dirs == ["new_dir", ".git"]
        dialog.close()

    def test_save_config_writes_ignore_extensions(self, qapp: QApplication) -> None:
        """_save_config 应将扩展名编辑器文本按行写入 config.ignore_extensions。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        dialog = SettingsDialog(config)
        dialog._ui.ignore_extensions_edit.setPlainText("pyc\nexe\n\n")
        dialog._save_config()
        assert config.ignore_extensions == ["pyc", "exe"]
        dialog.close()

    def test_save_config_strips_whitespace(self, qapp: QApplication) -> None:
        """保存时应 strip 每行首尾空白。"""
        from fuscan.config import Config
        from fuscan.gui.settings_dialog import SettingsDialog

        config = Config()
        dialog = SettingsDialog(config)
        dialog._ui.ignore_dirs_edit.setPlainText("  .git  \n  node_modules  \n")
        dialog._save_config()
        assert config.ignore_dirs == [".git", "node_modules"]
        dialog.close()


class TestIcons:
    """按钮与菜单动作图标接入测试。"""

    def test_all_action_buttons_have_icons(self, qapp: QApplication) -> None:
        """所有操作按钮应设置图标。"""
        window = MainWindow()
        assert not window._edit_rule_btn.icon().isNull()
        assert not window._export_btn.icon().isNull()
        assert not window._rescan_btn.icon().isNull()
        assert not window._cancel_btn.icon().isNull()
        assert not window._pause_resume_btn.icon().isNull()
        window.close()

    def test_all_menu_actions_have_icons(self, qapp: QApplication) -> None:
        """所有菜单动作应设置图标。"""
        window = MainWindow()
        assert not window._edit_rules_action.icon().isNull()
        assert not window._export_csv_action.icon().isNull()
        assert not window._export_json_action.icon().isNull()
        assert not window._settings_action.icon().isNull()
        assert not window._ui.about_action.icon().isNull()
        window.close()


class TestSeverityBackground:
    """严重等级背景色与分组项可选性测试。"""

    def test_critical_tree_item_has_background(self, qapp: QApplication, tmp_path: Path) -> None:
        """critical 等级文件项各列应有浅红背景色。"""
        from fuscan.gui.main_window import _SEVERITY_BACKGROUNDS
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

        top_item = window._result_tree.topLevelItem(0)
        assert top_item is not None
        expected_bg = _SEVERITY_BACKGROUNDS[Severity.CRITICAL]
        for col in range(top_item.columnCount()):
            bg = top_item.background(col)
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
        idx = window._group_mode_combo.findData("severity")
        window._group_mode_combo.setCurrentIndex(idx)
        window._refresh_result_tree()

        top_item = window._result_tree.topLevelItem(0)
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
        window._detail_show_result(result)
        text = window._detail_preview.toPlainText()
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
            from PySide6.QtGui import QCloseEvent

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
            from PySide6.QtGui import QCloseEvent

        window = MainWindow()
        window._config.cache_enabled = True
        window._config.cache_path = str(tmp_path / "cache.db")
        window._build_cache_context()
        assert window._cache is not None

        def raising_close() -> None:
            raise RuntimeError("close error")

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
            self._config.cache_enabled = False
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
            self._config.cache_path = str(tmp_path / "new_cache.db")
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
            raise RuntimeError("close error")

        monkeypatch.setattr(window._cache, "close", raising_close)

        from fuscan.gui import settings_dialog as sd_module

        def fake_exec(self: sd_module.SettingsDialog) -> int:
            self._config.cache_enabled = False
            return 1  # QDialog.Accepted

        monkeypatch.setattr(sd_module.SettingsDialog, "exec_", fake_exec)
        # 不应抛异常
        window._on_settings()
        assert window._cache is None
        window.close()
