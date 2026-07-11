"""GUI 烟雾测试。

使用 ``gui`` marker 标记，CI 无 GUI 环境时可通过 ``-m "not gui"`` 跳过。
需要 QApplication 环境（offscreen 平台）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import QApplication

    from uniscan.gui.detail_dialog import HitDetailDialog
    from uniscan.gui.main_window import MainWindow, ScanState
    from uniscan.gui.worker import ScanWorker
    from uniscan.rules import load_ruleset
    from uniscan.rules.model import (
        LeafMatch,
        MatchMode,
        MatchTarget,
        Rule,
        RuleSet,
        Severity,
    )
    from uniscan.scanner import ScanReport

    PYSIDE2_AVAILABLE = True
except ImportError:
    PYSIDE2_AVAILABLE = False

if not PYSIDE2_AVAILABLE:
    pytest.skip("PySide2 未安装，跳过 GUI 测试", allow_module_level=True)


@pytest.fixture(scope="module")
def qapp() -> QApplication:  # type: ignore[misc]
    """模块级 QApplication fixture。"""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离配置文件，避免测试读写用户主目录 ~/.uniscan/config.yaml。"""
    from uniscan.config import load_config as _load_impl
    from uniscan.config import save_config as _save_impl

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(
        "uniscan.gui.main_window.load_config",
        lambda path=None: _load_impl(config_path),
    )
    monkeypatch.setattr(
        "uniscan.gui.main_window.save_config",
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
        assert window.windowTitle().startswith("uniscan")
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
        from uniscan.scanner import Scanner

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
        from uniscan.scanner import Scanner

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
        from uniscan.scanner import Scanner

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
        window._use_builtin_checkbox.setChecked(False)

        # mock QFileDialog.getOpenFileName 返回规则文件路径
        monkeypatch.setattr(
            "uniscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(rules_yaml), ""),
        )

        window._on_load_rules()
        assert window._ruleset is not None
        assert window._rules_paths == [rules_yaml]
        assert "rules.yaml" in window._rules_label.text()
        assert window._rules_tree.topLevelItemCount() == 1
        window.close()

    def test_load_rules_cancelled(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """取消文件对话框不改变当前规则集。"""
        window = MainWindow()
        # 启动时已加载通用规则
        assert window._ruleset is not None
        monkeypatch.setattr(
            "uniscan.gui.main_window.QFileDialog.getOpenFileName",
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
        window._use_builtin_checkbox.setChecked(False)
        assert window._ruleset is None
        monkeypatch.setattr(
            "uniscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(bad_rules), ""),
        )
        warned = {"called": False}
        monkeypatch.setattr(
            "uniscan.gui.main_window.QMessageBox.warning",
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
            "uniscan.gui.main_window.QFileDialog.getExistingDirectory",
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
        assert window._use_builtin_checkbox.isChecked()
        # 规则树应非空
        assert window._rules_tree.topLevelItemCount() > 0
        window.close()

    def test_uncheck_builtin_clears_ruleset(self, qapp: QApplication) -> None:
        """取消勾选通用规则且无用户规则时 ruleset 为 None。"""
        window = MainWindow()
        window._use_builtin_checkbox.setChecked(False)
        assert window._use_builtin is False
        assert window._ruleset is None
        assert window._rules_tree.topLevelItemCount() == 0
        # 扫描按钮应禁用
        assert not window._scan_btn.isEnabled()
        window.close()

    def test_recheck_builtin_reloads_ruleset(self, qapp: QApplication) -> None:
        """重新勾选通用规则应重新加载规则集。"""
        window = MainWindow()
        window._use_builtin_checkbox.setChecked(False)
        assert window._ruleset is None
        window._use_builtin_checkbox.setChecked(True)
        assert window._ruleset is not None
        assert len(window._ruleset.rules) > 0
        window.close()

    def test_builtin_label_updated_on_toggle(self, qapp: QApplication) -> None:
        """切换通用规则开关时标签应更新。"""
        window = MainWindow()
        assert "通用规则" in window._rules_label.text()
        window._use_builtin_checkbox.setChecked(False)
        assert "未加载" in window._rules_label.text()
        window._use_builtin_checkbox.setChecked(True)
        assert "通用规则" in window._rules_label.text()
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
            "uniscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(rules_yaml), ""),
        )
        window._on_load_rules()
        # 合并后规则数应大于内置规则数
        assert window._rules_tree.topLevelItemCount() > builtin_count
        assert "通用规则" in window._rules_label.text()
        assert "rules.yaml" in window._rules_label.text()
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
        window._use_builtin_checkbox.setChecked(False)

        # 先返回 r1，再返回 r2
        paths_iter = iter([str(r1), str(r2)])
        monkeypatch.setattr(
            "uniscan.gui.main_window.QFileDialog.getOpenFileName",
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
        window._use_builtin_checkbox.setChecked(False)

        monkeypatch.setattr(
            "uniscan.gui.main_window.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(r1), ""),
        )
        # 抑制提示框
        monkeypatch.setattr(
            "uniscan.gui.main_window.QMessageBox.information",
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
        window._use_builtin_checkbox.setChecked(False)
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
        window._use_builtin_checkbox.setChecked(False)
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
        window._use_builtin_checkbox.setChecked(False)
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
        window._use_builtin_checkbox.setChecked(False)
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
        window._use_builtin_checkbox.setChecked(False)
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
        window._use_builtin_checkbox.setChecked(False)
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
        window._use_builtin_checkbox.setChecked(False)

        # [r1, r2] → r2 覆盖 r1
        window._rules_paths = [r1, r2]
        window._reload_ruleset()
        assert window._ruleset.rules[0].match.pattern == "from_r2"

        # [r2, r1] → r1 覆盖 r2
        window._rules_paths = [r2, r1]
        window._reload_ruleset()
        assert window._ruleset.rules[0].match.pattern == "from_r1"
        window.close()

    def test_label_shows_all_filenames(self, qapp: QApplication, tmp_path: Path) -> None:
        """规则标签应展示所有已加载文件名。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text('version: "1.0"\nrules: []\n', encoding="utf-8")
        r2 = tmp_path / "r2.yaml"
        r2.write_text('version: "1.0"\nrules: []\n', encoding="utf-8")

        window = MainWindow()
        window._rules_paths = [r1, r2]
        window._reload_ruleset()
        window._rules_label.setText(f"规则: {window._build_rules_label()}")

        text = window._rules_label.text()
        assert "r1.yaml" in text
        assert "r2.yaml" in text
        window.close()


class TestConfigPersistence:
    """配置持久化集成测试。"""

    def test_rules_paths_restored_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """启动时从配置恢复规则文件列表。"""
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

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
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

        config = Config(rules_paths=[str(tmp_path / "nonexistent.yaml")], use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert len(window._rules_paths) == 0
        window.close()

    def test_use_builtin_restored(self, qapp: QApplication, tmp_path: Path) -> None:
        """通用规则开关状态从配置恢复。"""
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

        config = Config(use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._use_builtin is False
        assert window._use_builtin_checkbox.isChecked() is False
        window.close()

    def test_scan_paths_history_restored(self, qapp: QApplication, tmp_path: Path) -> None:
        """扫描路径历史从配置恢复到下拉框。"""
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

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
        window._use_builtin_checkbox.setChecked(False)
        window.close()

        from uniscan.config import load_config as _load_impl

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
        window._use_builtin_checkbox.setChecked(False)
        window._rules_paths = [r1]
        window.close()

        from uniscan.config import load_config as _load_impl

        config = _load_impl(tmp_path / "config.yaml")
        assert str(r1) in config.rules_paths

    def test_close_saves_scan_paths_history(self, qapp: QApplication, tmp_path: Path) -> None:
        """关闭时扫描路径历史应被保存。"""
        (tmp_path / "scan_dir").mkdir()
        window = MainWindow()
        window._add_scan_path_history(str(tmp_path / "scan_dir"))
        window.close()

        from uniscan.config import load_config as _load_impl

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
        from uniscan.config import MAX_HISTORY

        window = MainWindow()
        for i in range(MAX_HISTORY + 5):
            window._add_scan_path_history(f"/path/{i}")
        assert window._path_combo.count() == MAX_HISTORY
        # 最近添加的应在最前
        assert window._path_combo.itemText(0) == f"/path/{MAX_HISTORY + 4}"
        window.close()

    def test_window_geometry_restored(self, qapp: QApplication, tmp_path: Path) -> None:
        """窗口几何从配置恢复。"""
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

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
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

        config = Config(window_geometry=[0, 0, 1000, 700], splitter_sizes=[400, 600])
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
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
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

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
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

        config = Config(scan_paths=[str(tmp_path / "nonexistent")], use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_root is None
        assert not window._scan_btn.isEnabled()
        window.close()

    def test_no_scan_path_disables_button_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """配置中无路径时启动后扫描按钮应禁用。"""
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

        config = Config(scan_paths=[], use_builtin=False)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_root is None
        assert not window._scan_btn.isEnabled()
        window.close()


class TestScanWorker:
    def test_worker_runs_scan(self, qapp: QApplication, tmp_path: Path) -> None:
        """ScanWorker 应在后台完成扫描。"""
        from PySide2.QtCore import QEventLoop, QTimer

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        results: list = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108
        worker.start()

        # 通过事件循环等待 finished 信号
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)  # 超时保护
        loop.exec_()

        worker.wait(2000)
        assert not worker.isRunning()
        assert len(results) == 1
        report = results[0]
        assert report.stats.matched_files >= 1

    def test_worker_handles_invalid_path(self, qapp: QApplication, tmp_path: Path) -> None:
        """无效路径应正常完成（Scanner 返回空报告）。"""
        from PySide2.QtCore import QEventLoop, QTimer

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path / "nonexistent"])

        results: list = []
        errors: list = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108
        worker.failed.connect(lambda msg: errors.append(msg))  # noqa: PLW0108
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
        loop.exec_()

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

    def test_stop_button_hidden_initially(self, qapp: QApplication) -> None:
        """启动时停止按钮应隐藏。"""
        window = MainWindow()
        assert not window._stop_btn.isVisible()
        window.close()

    def test_set_scan_controls_text_updates_both(self, qapp: QApplication) -> None:
        """_set_scan_controls_text 应同步更新按钮和 action 文本。"""
        window = MainWindow()
        window._set_scan_controls_text("暂停扫描")
        assert window._scan_btn.text() == "暂停扫描"
        assert window._scan_action.text() == "暂停扫描"
        window._set_scan_controls_text("继续扫描")
        assert window._scan_btn.text() == "继续扫描"
        assert window._scan_action.text() == "继续扫描"
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
        assert window._scan_btn.text() == "继续扫描"
        assert window._scan_action.text() == "继续扫描"
        assert "已暂停" in window._stats_label.text()
        window.close()

    def test_resume_scan_changes_state_and_text(self, qapp: QApplication) -> None:
        """_resume_scan 应设置 RUNNING 状态和"暂停扫描"文本。"""
        window = MainWindow()
        window._scan_state = ScanState.PAUSED
        window._resume_scan()
        assert window._scan_state == ScanState.RUNNING
        assert window._scan_btn.text() == "暂停扫描"
        assert window._scan_action.text() == "暂停扫描"
        window.close()

    def test_reset_scan_ui_resets_state(self, qapp: QApplication) -> None:
        """_reset_scan_ui 应重置到 IDLE 状态并恢复"开始扫描"文本。"""
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._set_scan_controls_text("暂停扫描")
        window._stop_btn.setVisible(True)
        window._reset_scan_ui()
        assert window._scan_state == ScanState.IDLE
        assert window._scan_btn.text() == "开始扫描"
        assert window._scan_action.text() == "开始扫描"
        assert not window._stop_btn.isVisible()
        assert not window._progress.isVisible()
        assert not window._current_file_label.isVisible()
        window.close()

    def test_on_scan_with_no_ruleset_does_nothing(self, qapp: QApplication) -> None:
        """IDLE 状态无规则集时点击扫描按钮不应启动。"""
        window = MainWindow()
        window._ruleset = None
        window._on_scan()
        assert window._scan_state == ScanState.IDLE
        assert window._worker is None
        window.close()

    def test_on_scan_running_triggers_pause(self, qapp: QApplication) -> None:
        """RUNNING 状态点击扫描按钮应触发暂停。"""
        window = MainWindow()
        window._scan_state = ScanState.RUNNING
        window._set_scan_controls_text("暂停扫描")
        window._on_scan()
        assert window._scan_state == ScanState.PAUSED
        assert window._scan_btn.text() == "继续扫描"
        window.close()

    def test_on_scan_paused_triggers_resume(self, qapp: QApplication) -> None:
        """PAUSED 状态点击扫描按钮应触发恢复。"""
        window = MainWindow()
        window._scan_state = ScanState.PAUSED
        window._set_scan_controls_text("继续扫描")
        window._on_scan()
        assert window._scan_state == ScanState.RUNNING
        assert window._scan_btn.text() == "暂停扫描"
        window.close()


class TestScanControlIntegration:
    """扫描控制集成测试：通过 MainWindow 运行完整扫描流程。"""

    def test_scan_completes_through_main_window(self, qapp: QApplication, tmp_path: Path) -> None:
        """通过 MainWindow 启动扫描应完成并填充结果树。"""
        from PySide2.QtCore import QEventLoop, QTimer

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        (tmp_path / "normal.txt").write_text("y", encoding="utf-8")

        window = MainWindow()
        window._ruleset = _build_ruleset()
        window._scan_root = tmp_path
        window._scan_mode = "folder"
        window._on_scan()

        assert window._scan_state == ScanState.RUNNING
        assert window._scan_btn.text() == "暂停扫描"
        assert window._stop_btn.isVisible()

        loop = QEventLoop()
        QTimer.singleShot(10000, loop.quit)
        window._worker.finished.connect(loop.quit) if window._worker is not None else None
        loop.exec_()

        if window._worker is not None:
            window._worker.wait(2000)

        assert window._scan_state == ScanState.IDLE
        assert window._scan_btn.text() == "开始扫描"
        assert window._result_tree.topLevelItemCount() >= 1
        assert window._worker is None
        window.close()

    def test_scan_cancel_through_main_window(self, qapp: QApplication, tmp_path: Path) -> None:
        """通过 MainWindow 取消扫描应显示已取消状态。"""
        from uniscan.scanner import ScanReport
        from uniscan.scanner.result import ScanStats

        window = MainWindow()
        window._ruleset = _build_ruleset()
        window._scan_root = tmp_path
        window._scan_mode = "folder"

        # 模拟扫描中状态
        window._scan_state = ScanState.RUNNING
        window._set_scan_controls_text("暂停扫描")
        window._stop_btn.setVisible(True)
        window._progress.setVisible(True)

        # 直接调用 _on_scan_cancelled 模拟取消回调
        report = ScanReport(
            root=tmp_path,
            results=(),
            stats=ScanStats(total_files=100, scanned_files=50, matched_files=10),
            cancelled=True,
        )
        window._on_scan_cancelled(report)

        assert window._scan_state == ScanState.IDLE
        assert window._scan_btn.text() == "开始扫描"
        assert not window._stop_btn.isVisible()
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
            "uniscan.gui.main_window.QMessageBox.warning",
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


class TestScanWorkerControl:
    """ScanWorker 暂停/取消控制信号测试。"""

    def test_worker_cancel_emits_cancelled_signal(self, qapp: QApplication, tmp_path: Path) -> None:
        """取消扫描应发射 cancelled 信号并携带部分结果。"""
        from PySide2.QtCore import QEventLoop, QTimer

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

        finished_reports: list = []
        cancelled_reports: list = []
        worker.finished_report.connect(lambda r: finished_reports.append(r))  # noqa: PLW0108
        worker.cancelled.connect(lambda r: cancelled_reports.append(r))  # noqa: PLW0108
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
        loop.exec_()

        worker.wait(2000)
        assert not worker.isRunning()
        assert len(finished_reports) == 0
        assert len(cancelled_reports) == 1
        report = cancelled_reports[0]
        assert report.cancelled

    def test_worker_pause_resume_delegates_to_scanner(self, qapp: QApplication, tmp_path: Path) -> None:
        """pause/resume 应委托给 Scanner，扫描仍正常完成。"""
        from PySide2.QtCore import QEventLoop, QTimer

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        results: list = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108
        worker.start()

        # 暂停后立即恢复
        QTimer.singleShot(50, worker.pause)
        QTimer.singleShot(100, worker.resume)

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
        loop.exec_()

        worker.wait(2000)
        assert not worker.isRunning()
        assert len(results) == 1
        report = results[0]
        assert not report.cancelled
        assert report.stats.matched_files >= 1


class TestLaunchApp:
    def test_launch_creates_window_and_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """launch 应创建 QApplication 与 MainWindow 并进入事件循环。"""
        from uniscan.gui import app as app_module

        created: list = []

        class FakeApp:
            def __init__(self, args):  # type: ignore[no-untyped-def]
                created.append(self)
                self._app_name = None

            def setApplicationName(self, name: str) -> None:
                self._app_name = name

            def setStyleSheet(self, sheet: str) -> None:
                pass

            def exec_(self) -> int:
                return 0

            @staticmethod
            def instance() -> None:
                return None

        shown: list = []

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

    def test_launch_reuses_existing_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """已有 QApplication 实例时复用，不创建新实例。"""
        from uniscan.gui import app as app_module

        existing_app = type(
            "ExistingApp",
            (),
            {
                "exec_": lambda self: 0,
                "setApplicationName": lambda self, n: None,
                "setStyleSheet": lambda self, s: None,
            },
        )()
        created: list = []

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

        shown: list = []

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


class TestHitDetailDialogHelpers:
    """详情对话框辅助函数测试。"""

    def test_format_size_bytes(self) -> None:
        from uniscan.gui.detail_dialog import _format_size

        assert _format_size(0) == "0 B"
        assert _format_size(512) == "512 B"
        assert _format_size(1023) == "1023 B"

    def test_format_size_kb(self) -> None:
        from uniscan.gui.detail_dialog import _format_size

        assert _format_size(1024) == "1.0 KB"
        assert _format_size(2048) == "2.0 KB"

    def test_format_size_mb(self) -> None:
        from uniscan.gui.detail_dialog import _format_size

        assert _format_size(1024 * 1024) == "1.0 MB"

    def test_format_size_gb(self) -> None:
        from uniscan.gui.detail_dialog import _format_size

        assert "GB" in _format_size(1024 * 1024 * 1024)

    def test_extract_keywords_contains(self) -> None:
        from uniscan.gui.detail_dialog import _extract_keywords
        from uniscan.scanner import RuleHit

        hits = (
            RuleHit("r1", Severity.WARNING, "包含 'password'"),
            RuleHit("r2", Severity.CRITICAL, "包含 'secret'"),
        )
        kws = _extract_keywords(hits)
        assert "password" in kws
        assert "secret" in kws

    def test_extract_keywords_regex(self) -> None:
        from uniscan.gui.detail_dialog import _extract_keywords
        from uniscan.scanner import RuleHit

        hits = (RuleHit("r", Severity.CRITICAL, "正则命中: 'AKIA1234'"),)
        kws = _extract_keywords(hits)
        assert "AKIA1234" in kws

    def test_extract_keywords_dedup(self) -> None:
        from uniscan.gui.detail_dialog import _extract_keywords
        from uniscan.scanner import RuleHit

        hits = (
            RuleHit("r1", Severity.WARNING, "包含 'password'"),
            RuleHit("r2", Severity.WARNING, "包含 'password'"),
        )
        kws = _extract_keywords(hits)
        assert kws.count("password") == 1

    def test_extract_keywords_no_match(self) -> None:
        from uniscan.gui.detail_dialog import _extract_keywords
        from uniscan.scanner import RuleHit

        hits = (RuleHit("r", Severity.INFO, "完全相等"),)
        kws = _extract_keywords(hits)
        assert kws == []

    def test_build_preview_html_no_keywords(self) -> None:
        from uniscan.gui.detail_dialog import _build_preview_html

        result = _build_preview_html("hello world", [])
        assert "hello" in result
        assert "<span" not in result

    def test_build_preview_html_with_keywords(self) -> None:
        from uniscan.gui.detail_dialog import _build_preview_html

        result = _build_preview_html("hello password world", ["password"])
        assert "span" in result
        assert "background-color: yellow" in result

    def test_build_preview_html_escapes_html(self) -> None:
        from uniscan.gui.detail_dialog import _build_preview_html

        result = _build_preview_html("<script>alert(1)</script>", [])
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_build_preview_html_case_insensitive(self) -> None:
        from uniscan.gui.detail_dialog import _build_preview_html

        result = _build_preview_html("PASSWORD password Password", ["password"])
        # 所有大小的 password 都应被高亮（3 次匹配 = 6 个 span 标签：开+关）
        assert result.count("<span") == 3


class TestScanWorkerMultiRoot:
    """ScanWorker 多根路径扫描测试。"""

    def test_worker_scans_multiple_roots(self, qapp: QApplication, tmp_path: Path) -> None:
        """ScanWorker 应依次扫描多个根路径并合并结果。"""
        from PySide2.QtCore import QEventLoop, QTimer

        (tmp_path / "dir_a").mkdir()
        (tmp_path / "dir_a" / "secret.txt").write_text("x", encoding="utf-8")
        (tmp_path / "dir_b").mkdir()
        (tmp_path / "dir_b" / "secret.txt").write_text("y", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path / "dir_a", tmp_path / "dir_b"])

        results: list = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
        loop.exec_()

        worker.wait(2000)
        assert not worker.isRunning()
        assert len(results) == 1
        report = results[0]
        # 两个目录各命中一个文件
        assert report.stats.matched_files >= 2
        assert report.stats.total_files >= 2

    def test_worker_merges_empty_and_nonempty(self, qapp: QApplication, tmp_path: Path) -> None:
        """有效路径与无效路径混合时应正常合并。"""
        from PySide2.QtCore import QEventLoop, QTimer

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(
            ruleset=rs,
            roots=[tmp_path / "nonexistent", tmp_path],
        )

        results: list = []
        worker.finished_report.connect(lambda r: results.append(r))  # noqa: PLW0108
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
        loop.exec_()

        worker.wait(2000)
        assert len(results) == 1
        report = results[0]
        assert report.stats.matched_files >= 1


class TestScanWorkerProgress:
    """ScanWorker progress_info 信号测试。"""

    def test_progress_info_emitted(self, qapp: QApplication, tmp_path: Path) -> None:
        """扫描过程中应 emit progress_info 信号。"""
        from PySide2.QtCore import QEventLoop, QTimer

        for i in range(5):
            (tmp_path / f"secret_{i}.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        progress_infos: list = []
        worker.progress_info.connect(progress_infos.append)
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
        loop.exec_()

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
        from PySide2.QtCore import QEventLoop, QTimer

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

        progress_infos: list = []
        worker.progress_info.connect(progress_infos.append)
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
        loop.exec_()

        worker.wait(2000)
        assert not worker.isRunning()
        # 最终进度的累计值应覆盖两个根路径的全部文件
        last = progress_infos[-1]
        assert last.total >= 7  # 3 + 4
        assert last.scanned >= 7
        assert last.matched >= 7

    def test_progress_info_fields_type(self, qapp: QApplication, tmp_path: Path) -> None:
        """progress_info 携带 ProgressInfo 对象且字段类型正确。"""
        from PySide2.QtCore import QEventLoop, QTimer

        from uniscan.scanner.result import ProgressInfo

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        progress_infos: list = []
        worker.progress_info.connect(progress_infos.append)
        worker.start()

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
        loop.exec_()

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

        reports: list = []
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

        reports: list = []
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

        reports: list = []
        worker.finished_report.connect(reports.append)
        worker.run()

        assert len(reports) == 1
        report = reports[0]
        assert report.stats.total_files == 0
        assert report.stats.matched_files == 0

    def test_on_progress_emits_cumulative(self, qapp: QApplication) -> None:
        """_on_progress 应累加 _cum_* 字段后 emit。"""
        from uniscan.scanner.result import ProgressInfo

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[Path("/tmp")])
        # 模拟前序根路径已扫描的累计值
        worker._cum_scanned = 10
        worker._cum_total = 15
        worker._cum_skipped = 3
        worker._cum_matched = 5
        worker._cum_errors = 1
        worker._start_time = time.monotonic() - 2.0  # 2 秒前开始

        emitted: list = []
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

    def test_run_emits_failed_on_exception(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scanner.scan 抛异常时 run() 应 emit failed 信号。"""
        from uniscan.scanner.scanner import Scanner

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        def boom(self: Scanner, root: Path) -> None:
            raise RuntimeError("扫描爆炸")

        monkeypatch.setattr(Scanner, "scan", boom)

        failures: list = []
        worker.failed.connect(failures.append)
        reports: list = []
        worker.finished_report.connect(reports.append)
        worker.run()

        assert len(failures) == 1
        assert "扫描爆炸" in failures[0]
        assert len(reports) == 0  # 不应 emit finished_report

    def test_on_progress_zero_cumulative(self, qapp: QApplication) -> None:
        """_cum_* 全为 0 时 _on_progress 应原样传递 info 值。"""
        from uniscan.scanner.result import ProgressInfo

        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[Path("/tmp")])
        worker._start_time = time.monotonic()

        emitted: list = []
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
        from uniscan.scanner.scanner import Scanner

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

        cancelled_reports: list = []
        worker.cancelled.connect(cancelled_reports.append)
        worker.run()

        assert cancel_called["n"] >= 1
        assert len(cancelled_reports) == 1

    def test_run_emits_cancelled_when_scanner_cancelled(
        self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scanner 被取消后 run() 应 emit cancelled 信号。"""
        from uniscan.scanner.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        worker = ScanWorker(ruleset=rs, roots=[tmp_path])

        original_scan = Scanner.scan

        def fake_scan(self: Scanner, root: Path):  # type: ignore[no-untyped-def]
            self.cancel()
            return original_scan(self, root)

        monkeypatch.setattr(Scanner, "scan", fake_scan)

        cancelled_reports: list = []
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
        assert window._folder_btn.isChecked()
        assert not window._full_btn.isChecked()
        assert not window._drive_btn.isChecked()
        window.close()

    def test_folder_mode_shows_path_row(self, qapp: QApplication) -> None:
        """folder 模式下路径行可见，盘符下拉隐藏。"""
        window = MainWindow()
        assert window._target_row.isVisible()
        assert not window._drive_combo.isVisible()
        assert not window._drive_label.isVisible()
        window.close()

    def test_full_mode_hides_target_selectors(self, qapp: QApplication) -> None:
        """full 模式下隐藏路径行与盘符下拉。"""
        window = MainWindow()
        window._full_btn.setChecked(True)
        window._on_scan_mode_changed(window._full_btn)
        assert window._scan_mode == "full"
        assert not window._target_row.isVisible()
        assert not window._drive_combo.isVisible()
        window.close()

    def test_drive_mode_shows_drive_combo(self, qapp: QApplication) -> None:
        """drive 模式下盘符下拉可见，路径行隐藏。"""
        window = MainWindow()
        window._drive_btn.setChecked(True)
        window._on_scan_mode_changed(window._drive_btn)
        assert window._scan_mode == "drive"
        assert window._drive_combo.isVisible()
        assert window._drive_label.isVisible()
        assert not window._target_row.isVisible()
        window.close()

    def test_full_mode_enables_scan_without_path(self, qapp: QApplication) -> None:
        """full 模式下有规则即可扫描，无需选择路径。"""
        window = MainWindow()
        assert window._ruleset is not None
        # folder 模式下未选路径，按钮禁用
        assert not window._scan_btn.isEnabled()
        # 切换到 full 模式
        window._full_btn.setChecked(True)
        window._on_scan_mode_changed(window._full_btn)
        assert window._scan_btn.isEnabled()
        window.close()

    def test_drive_mode_enables_scan_with_drive(self, qapp: QApplication) -> None:
        """drive 模式下有盘符即可扫描。"""
        window = MainWindow()
        window._drive_btn.setChecked(True)
        window._on_scan_mode_changed(window._drive_btn)
        # 盘符下拉在测试环境（Windows）通常有盘符
        if window._drive_combo.count() > 0:
            assert window._scan_btn.isEnabled()
        window.close()

    def test_build_scan_roots_full_mode(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """full 模式应返回所有盘符。"""
        from uniscan.gui import main_window as mw_mod

        fake_drives = [Path("C:\\"), Path("D:\\")]
        monkeypatch.setattr(mw_mod, "list_drives", lambda: fake_drives)

        window = MainWindow()
        window._full_btn.setChecked(True)
        window._on_scan_mode_changed(window._full_btn)
        roots = window._build_scan_roots()
        assert roots == fake_drives
        window.close()

    def test_build_scan_roots_drive_mode(self, qapp: QApplication) -> None:
        """drive 模式应返回选中的单个盘符。"""
        window = MainWindow()
        window._drive_btn.setChecked(True)
        window._on_scan_mode_changed(window._drive_btn)
        if window._drive_combo.count() > 0:
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
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

        config = Config(scan_mode="full")
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_mode == "full"
        assert window._full_btn.isChecked()
        window.close()

    def test_drive_mode_restored_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """启动时从配置恢复 drive 模式。"""
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

        config = Config(scan_mode="drive")
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_mode == "drive"
        assert window._drive_btn.isChecked()
        window.close()

    def test_close_saves_scan_mode(self, qapp: QApplication, tmp_path: Path) -> None:
        """关闭时扫描模式应被保存。"""
        window = MainWindow()
        window._full_btn.setChecked(True)
        window._on_scan_mode_changed(window._full_btn)
        window.close()

        from uniscan.config import load_config as _load_impl

        config = _load_impl(tmp_path / "config.yaml")
        assert config.scan_mode == "full"

    def test_last_drive_restored_on_startup(self, qapp: QApplication, tmp_path: Path) -> None:
        """启动时从配置恢复上次选择的盘符。"""
        from uniscan.config import Config
        from uniscan.config import save_config as _save_impl

        # 使用存在的盘符
        window = MainWindow()
        if window._drive_combo.count() == 0:
            window.close()
            pytest.skip("无可用盘符")
        first_drive = window._drive_combo.itemData(0)
        window.close()

        config = Config(scan_mode="drive", last_drive=first_drive)
        _save_impl(config, tmp_path / "config.yaml")

        window = MainWindow()
        assert window._scan_mode == "drive"
        assert window._drive_combo.currentData() == first_drive
        window.close()


class TestHitDetailDialog:
    """详情对话框测试。"""

    def test_dialog_shows_file_info(self, qapp: QApplication, tmp_path: Path) -> None:
        """对话框应展示文件路径、大小等信息。"""
        from uniscan.gui.detail_dialog import HitDetailDialog
        from uniscan.scanner import RuleHit, ScanResult

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
        from uniscan.gui.detail_dialog import HitDetailDialog
        from uniscan.scanner import RuleHit, ScanResult

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
        from uniscan.gui.detail_dialog import HitDetailDialog
        from uniscan.scanner import RuleHit, ScanResult

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
        from uniscan.gui.detail_dialog import HitDetailDialog
        from uniscan.scanner import RuleHit, ScanResult

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
        from uniscan.gui.detail_dialog import HitDetailDialog
        from uniscan.scanner import RuleHit, ScanResult

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
        from uniscan.gui import main_window as mw_module
        from uniscan.scanner import Scanner

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
        from PySide2.QtWidgets import QTreeWidgetItem

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
        from uniscan.gui.detail_dialog import HitDetailDialog
        from uniscan.scanner.result import RuleHit, ScanResult

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
        from uniscan.gui.detail_dialog import HitDetailDialog
        from uniscan.scanner.result import RuleHit, ScanResult

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
        from uniscan.gui.detail_dialog import HitDetailDialog
        from uniscan.scanner.result import RuleHit, ScanResult

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


def _build_multi_hit_report(tmp_path: Path) -> ScanReport:
    """构造多规则、多文件命中的测试报告。"""
    from uniscan.rules.model import (
        LeafMatch,
        MatchMode,
        MatchTarget,
        Rule,
        RuleSet,
        Severity,
    )
    from uniscan.scanner import Scanner

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
        """结果树应包含命中数列。"""
        window = MainWindow()
        assert window._result_tree.columnCount() == 5
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
        assert "warning" in severities
        assert "critical" in severities
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
        from uniscan.gui import detail_dialog as dd_module

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
        window._use_builtin_checkbox.setChecked(False)
        window._rules_paths = []
        called = {"count": 0}
        monkeypatch.setattr(
            "uniscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: called.update(count=called["count"] + 1),
        )
        window._on_edit_rules()
        assert called["count"] == 1
        window.close()

    def test_editor_dialog_opens_with_content(self, qapp: QApplication, tmp_path: Path) -> None:
        """编辑器应加载规则文件内容。"""
        from uniscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        assert dialog._file_combo.count() == 1
        assert dialog._file_combo.itemText(0) == "rules.yaml"
        content = dialog._editor.toPlainText()
        assert "测试规则" in content
        dialog.close()

    def test_editor_switch_files(self, qapp: QApplication, tmp_path: Path) -> None:
        """切换文件下拉应加载对应内容。"""
        from uniscan.gui.rule_editor import RuleEditorDialog

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
        from uniscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog._editor.setPlainText(
            'version: "1.0"\nrules:\n  - name: 新规则\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: test\n',
        )

        monkeypatch.setattr(
            "uniscan.gui.rule_editor.QMessageBox.information",
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
        from uniscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        original = rules_path.read_text(encoding="utf-8")
        dialog = RuleEditorDialog([rules_path])
        dialog._editor.setPlainText("invalid: yaml: content: [")

        warned = {"called": False}
        monkeypatch.setattr(
            "uniscan.gui.rule_editor.QMessageBox.warning",
            lambda *args, **kwargs: warned.update(called=True),
        )
        monkeypatch.setattr(
            "uniscan.gui.rule_editor.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        dialog._on_save()

        assert warned["called"]
        assert rules_path.read_text(encoding="utf-8") == original
        dialog.close()

    def test_reload_restores_content(self, qapp: QApplication, tmp_path: Path) -> None:
        """重新加载应恢复文件原始内容。"""
        from uniscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog._editor.setPlainText("modified content")
        dialog._on_reload()
        assert "测试规则" in dialog._editor.toPlainText()
        dialog.close()

    def test_empty_rules_paths(self, qapp: QApplication) -> None:
        """无规则文件时编辑器应显示提示。"""
        from uniscan.gui.rule_editor import RuleEditorDialog

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
        window._use_builtin_checkbox.setChecked(False)
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
            "uniscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window.close()

    def test_load_file_content_invalid_index(self, qapp: QApplication, tmp_path: Path) -> None:
        """无效索引应清空编辑器。"""
        from uniscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog._load_file_content(99)
        assert dialog._editor.toPlainText() == ""
        dialog.close()

    def test_load_file_content_read_error(self, qapp: QApplication, tmp_path: Path) -> None:
        """文件读取失败应显示错误信息并禁用编辑器。"""
        from uniscan.gui.rule_editor import RuleEditorDialog

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
        from uniscan.gui.rule_editor import RuleEditorDialog

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
        from uniscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        dialog._editor.setPlainText("version: '1.0'\nrules: []\n")

        warned = {"called": False}
        monkeypatch.setattr(
            "uniscan.gui.rule_editor.QMessageBox.warning",
            lambda *args, **kwargs: warned.update(called=True),
        )
        monkeypatch.setattr(
            "uniscan.gui.rule_editor.QMessageBox.information",
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
        from uniscan.gui.rule_editor import RuleEditorDialog

        rules_path = self._make_rules_file(tmp_path)
        dialog = RuleEditorDialog([rules_path])
        # 写入合法 YAML 但无效规则（缺少 match 字段）
        dialog._editor.setPlainText('version: "1.0"\nrules:\n  - name: bad\n    severity: warning\n')

        warned = {"called": False}
        monkeypatch.setattr(
            "uniscan.gui.rule_editor.QMessageBox.warning",
            lambda *args, **kwargs: warned.update(called=True),
        )
        monkeypatch.setattr(
            "uniscan.gui.rule_editor.QMessageBox.information",
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
        from uniscan.gui.main_window import _format_size

        assert _format_size(0) == "0 B"
        assert _format_size(512) == "512 B"
        assert _format_size(1024) == "1.0 KB"
        assert _format_size(1024 * 1024) == "1.0 MB"
        assert _format_size(1024 * 1024 * 1024) == "1.00 GB"

    def test_extract_keywords(self) -> None:
        """_extract_keywords 应从 detail 中提取单引号包裹的关键词。"""
        from uniscan.gui.main_window import _extract_keywords
        from uniscan.rules.model import Severity
        from uniscan.scanner.result import RuleHit

        hits = [
            RuleHit(rule_name="r1", severity=Severity.WARNING, detail="包含 'password'"),
            RuleHit(rule_name="r2", severity=Severity.CRITICAL, detail="正则命中: 'AKIA1234'"),
            RuleHit(rule_name="r3", severity=Severity.INFO, detail="无关键词"),
        ]
        keywords = _extract_keywords(hits)
        assert keywords == ["password", "AKIA1234"]

    def test_extract_keywords_dedup(self) -> None:
        """_extract_keywords 应去重相同关键词。"""
        from uniscan.gui.main_window import _extract_keywords
        from uniscan.rules.model import Severity
        from uniscan.scanner.result import RuleHit

        hits = [
            RuleHit(rule_name="r1", severity=Severity.WARNING, detail="包含 'secret'"),
            RuleHit(rule_name="r2", severity=Severity.WARNING, detail="包含 'secret'"),
        ]
        keywords = _extract_keywords(hits)
        assert keywords == ["secret"]

    def test_build_preview_html_no_keywords(self) -> None:
        """_build_preview_html 无关键词时只转义不高亮。"""
        from uniscan.gui.main_window import _build_preview_html

        result = _build_preview_html("hello & world", [])
        assert "hello &amp; world" in result
        assert "<span" not in result

    def test_build_preview_html_with_keywords(self) -> None:
        """_build_preview_html 有关键词时应高亮。"""
        from uniscan.gui.main_window import _build_preview_html

        result = _build_preview_html("hello password world", ["password"])
        assert "<span" in result
        assert "password" in result

    def test_build_preview_html_escapes_html(self) -> None:
        """_build_preview_html 应先转义再高亮，避免 XSS。"""
        from uniscan.gui.main_window import _build_preview_html

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
        window._detail_hit_positions = [(0, 1)]
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
        from uniscan.scanner import Scanner

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
        from uniscan.scanner import Scanner

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
        from uniscan.scanner import Scanner

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
        assert not window._detail_locate_btn.isEnabled()
        window.close()

    def test_detail_nav_label_with_hits(self, qapp: QApplication) -> None:
        """有命中时导航标签应显示进度且按钮启用。"""
        window = MainWindow()
        window._detail_hit_positions = [(0, 1), (5, 6)]
        window._detail_current_hit_index = 0
        window._update_detail_nav_label()
        assert "1 / 2" in window._detail_nav_label.text()
        assert window._detail_prev_btn.isEnabled()
        assert window._detail_next_btn.isEnabled()
        assert window._detail_locate_btn.isEnabled()
        window.close()

    def test_detail_prev_next_wrap_around(self, qapp: QApplication) -> None:
        """命中导航应在到达首尾时循环。"""
        window = MainWindow()
        window._detail_hit_positions = [(0, 1), (5, 6)]
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
        from uniscan.scanner import Scanner

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

    def test_detail_locate_hit_no_hits(self, qapp: QApplication) -> None:
        """无命中时定位不应崩溃。"""
        window = MainWindow()
        window._detail_hit_positions = []
        window._detail_current_hit_index = -1
        window._on_locate_hit()
        window.close()

    def test_detail_preview_empty_file(self, qapp: QApplication, tmp_path: Path) -> None:
        """空文件预览应显示提示文本。"""
        from uniscan.scanner import Scanner

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


class TestScanCallbacks:
    """扫描回调与 UI 状态测试。"""

    def test_on_scan_progress(self, qapp: QApplication) -> None:
        """_on_scan_progress 应更新进度条和统计标签。"""
        from uniscan.scanner.result import ProgressInfo

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
        from uniscan.scanner.result import ProgressInfo

        window = MainWindow()
        long_path = "/" + "a" * 200 + ".txt"
        info = ProgressInfo(total=10, scanned=1, skipped=0, matched=0, errors=0, current_file=long_path, elapsed=0.1)
        window._on_scan_progress(info)
        label_text = window._current_file_label.text()
        assert "..." in label_text
        window.close()

    def test_on_scan_failed(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """_on_scan_failed 应重置 UI 并弹出错误。"""
        window = MainWindow()
        warned = {"called": False}
        monkeypatch.setattr(
            "uniscan.gui.main_window.QMessageBox.critical",
            lambda *args, **kwargs: warned.update(called=True),
        )
        window._on_scan_failed("测试错误")
        assert warned["called"]
        assert "扫描失败" in window._stats_label.text()
        window.close()

    def test_on_scan_cancelled(self, qapp: QApplication, tmp_path: Path) -> None:
        """_on_scan_cancelled 应填充结果并显示取消统计。"""
        from uniscan.scanner import Scanner

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
        from uniscan.gui.worker import ScanWorker
        from uniscan.scanner import Scanner

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
        assert "继续" in window._scan_btn.text()

        window._resume_scan()
        assert window._scan_state == ScanState.RUNNING
        assert "暂停" in window._scan_btn.text()

        window._worker = None
        window.close()

    def test_on_stop_no_worker(self, qapp: QApplication) -> None:
        """无 worker 时停止不应崩溃。"""
        window = MainWindow()
        window._on_stop()
        window.close()


class TestExportAndMenu:
    """导出、菜单与工具栏操作测试。"""

    def test_export_menu_no_report(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """无报告时导出菜单应提示。"""
        window = MainWindow()
        informed = {"called": False}
        monkeypatch.setattr(
            "uniscan.gui.main_window.QMessageBox.information",
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
            "uniscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: informed.update(called=True),
        )
        window._on_export("csv")
        assert informed["called"]
        window.close()

    def test_export_csv_to_file(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """导出 CSV 应写入文件。"""
        from uniscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        out_path = tmp_path / "export.csv"
        window = MainWindow()
        window._last_report = report
        monkeypatch.setattr(
            "uniscan.gui.main_window.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: (str(out_path), ""),
        )
        monkeypatch.setattr(
            "uniscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: None,
        )
        window._on_export("csv")
        assert out_path.exists()
        assert "secret.txt" in out_path.read_text(encoding="utf-8")
        window.close()

    def test_export_cancelled(self, qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """取消导出对话框不应写文件。"""
        from uniscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset()
        scanner = Scanner(rs)
        report = scanner.scan(tmp_path)

        out_path = tmp_path / "export.csv"
        window = MainWindow()
        window._last_report = report
        monkeypatch.setattr(
            "uniscan.gui.main_window.QFileDialog.getSaveFileName",
            lambda *args, **kwargs: ("", ""),
        )
        window._on_export("csv")
        assert not out_path.exists()
        window.close()

    def test_about_dialog(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """关于对话框应弹出。"""
        window = MainWindow()
        about_called = {"called": False}
        monkeypatch.setattr(
            "uniscan.gui.main_window.QMessageBox.about",
            lambda *args, **kwargs: about_called.update(called=True),
        )
        window._on_about()
        assert about_called["called"]
        window.close()

    def test_batch_process_not_implemented(self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
        """批量处理应提示未实现。"""
        window = MainWindow()
        informed = {"called": False}
        monkeypatch.setattr(
            "uniscan.gui.main_window.QMessageBox.information",
            lambda *args, **kwargs: informed.update(called=True),
        )
        window._on_batch_process()
        assert informed["called"]
        window.close()

    def test_switch_tab(self, qapp: QApplication) -> None:
        """_switch_tab 应切换 Tab 页。"""
        window = MainWindow()
        window._switch_tab(1)
        assert window._tab_widget.currentIndex() == 1
        window._switch_tab(2)
        assert window._tab_widget.currentIndex() == 2
        window._switch_tab(0)
        assert window._tab_widget.currentIndex() == 0
        window.close()

    def test_on_view_history(self, qapp: QApplication) -> None:
        """_on_view_history 应切换到历史 Tab。"""
        window = MainWindow()
        window._on_view_history()
        assert window._tab_widget.currentIndex() == 2
        window.close()

    def test_history_item_double_clicked(self, qapp: QApplication, tmp_path: Path) -> None:
        """双击历史项应设置扫描路径。"""
        from PySide2.QtWidgets import QListWidgetItem

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
        from PySide2.QtGui import QCloseEvent

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
        window._use_builtin_checkbox.setChecked(False)
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
        window._use_builtin_checkbox.setChecked(False)
        informed = {"called": False}
        monkeypatch.setattr(
            "uniscan.gui.main_window.QMessageBox.information",
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
        window._use_builtin_checkbox.setChecked(False)
        window._rules_paths = [r1]
        window._refresh_rules_file_list()

        # mock exec_ 避免阻塞
        from uniscan.gui.rule_editor import RuleEditorDialog

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
        window._use_builtin_checkbox.setChecked(False)
        window._rules_paths = [r1]
        window._reload_and_refresh()
        # 应成功加载规则集
        assert window._ruleset is not None
        window.close()
