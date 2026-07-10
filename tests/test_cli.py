"""CLI 单元测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from uniscan import __version__
from uniscan.cli import build_parser, main

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def rules_file(tmp_path: Path) -> Path:
    """创建测试用规则文件。"""
    content = """
version: "1.0"
ignore_dirs:
  - .git
rules:
  - name: 敏感文件名
    severity: warning
    match:
      type: filename
      mode: contains
      pattern: password
  - name: 密钥内容
    severity: critical
    match:
      type: content
      mode: regex
      pattern: 'AKIA[0-9A-Z]{4}'
"""
    path = tmp_path / "rules.yaml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture()
def scan_root(tmp_path: Path) -> Path:
    """创建测试扫描目录。"""
    root = tmp_path / "scan_root"
    root.mkdir()
    (root / "password.txt").write_text("normal", encoding="utf-8")
    (root / "doc.conf").write_text("key=AKIA1234567890ABCDEF", encoding="utf-8")
    (root / "readme.md").write_text("hello world", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "password.txt").write_text("ignored", encoding="utf-8")
    return root


class TestBuildParser:
    def test_parser_has_subcommands(self) -> None:
        parser = build_parser()
        actions = {a for a in parser._subparsers._group_actions if hasattr(a, "choices")}
        # 至少存在子命令解析
        assert actions

    def test_parse_scan_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["scan", "scan_path", "-r", "rules.yaml"])
        assert args.command == "scan"
        assert str(args.path) == "scan_path"
        assert args.rules == [Path("rules.yaml")]
        assert args.output_format == "text"

    def test_parse_scan_multiple_rules(self) -> None:
        """-r 可重复指定多个规则文件。"""
        parser = build_parser()
        args = parser.parse_args(["scan", "p", "-r", "r1.yaml", "-r", "r2.yaml"])
        assert args.rules == [Path("r1.yaml"), Path("r2.yaml")]

    def test_parse_scan_with_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "scan",
            "scan_path",
            "-r",
            "r.yaml",
            "-o",
            "json",
            "-f",
            "out.json",
            "--max-depth",
            "3",
        ])
        assert args.output_format == "json"
        assert args.max_depth == 3

    def test_parse_rules_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["rules", "-r", "rules.yaml"])
        assert args.command == "rules"

    def test_parse_gui_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["gui"])
        assert args.command == "gui"

    def test_parse_version_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["version"])
        assert args.command == "version"


class TestVersionCommand:
    def test_version_prints(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["version"])
        assert rc == 0
        out = capsys.readouterr().out
        assert __version__ in out


class TestRulesCommand:
    def test_rules_valid_file(self, rules_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["rules", "-r", str(rules_file)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "校验通过" in out
        assert "敏感文件名" in out
        assert "密钥内容" in out

    def test_rules_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["rules", "-r", str(tmp_path / "missing.yaml")])
        assert rc == 1
        err = capsys.readouterr().err
        assert "不存在" in err


class TestScanCommand:
    def test_scan_text_output(
        self,
        scan_root: Path,
        rules_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(["scan", str(scan_root), "-r", str(rules_file)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "扫描路径" in out
        assert "命中项" in out
        assert "password.txt" in out
        assert "doc.conf" in out

    def test_scan_json_output(
        self,
        scan_root: Path,
        rules_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "-o", "json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "root" in data
        assert "stats" in data
        assert "hits" in data
        assert len(data["hits"]) == 2

    def test_scan_csv_output(
        self,
        scan_root: Path,
        rules_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "-o", "csv"])
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert lines[0] == "path,size,severity,rule,detail"
        assert len(lines) >= 3  # 表头 + 2 条命中

    def test_scan_output_to_file(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
    ) -> None:
        out_file = tmp_path / "report.json"
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "-o", "json", "-f", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert len(data["hits"]) == 2

    def test_scan_nonexistent_path(self, tmp_path: Path, rules_file: Path) -> None:
        rc = main(["scan", str(tmp_path / "missing"), "-r", str(rules_file)])
        assert rc == 1

    def test_scan_nonexistent_rules(self, scan_root: Path, tmp_path: Path) -> None:
        rc = main(["scan", str(scan_root), "-r", str(tmp_path / "missing.yaml")])
        assert rc == 1

    def test_scan_with_extra_ignore_dir(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # 在 scan_root 下新建一个特殊目录，通过 --ignore-dir 排除
        extra_dir = scan_root / "exclude_me"
        extra_dir.mkdir()
        (extra_dir / "password.txt").write_text("", encoding="utf-8")

        rc = main([
            "scan",
            str(scan_root),
            "-r",
            str(rules_file),
            "--ignore-dir",
            "exclude_me",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        # exclude_me 内的 password.txt 应被忽略
        assert "exclude_me" not in out

    def test_scan_invalid_rules_returns_2(self, scan_root: Path, tmp_path: Path) -> None:
        bad_rules = tmp_path / "bad.yaml"
        bad_rules.write_text(
            "version: '1.0'\nrules:\n  - name: bad\n    match:\n      type: unknown\n", encoding="utf-8"
        )
        rc = main(["scan", str(scan_root), "-r", str(bad_rules)])
        assert rc == 2


class TestBuiltinRules:
    """内置通用规则集成测试。"""

    def test_scan_with_builtin_only(self, scan_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """不带 -r 时默认使用内置通用规则。"""
        rc = main(["scan", str(scan_root)])
        assert rc == 0
        out = capsys.readouterr().out
        # 内置规则包含 AWS 密钥检测，doc.conf 含 AKIA1234 应命中
        assert "doc.conf" in out

    def test_scan_no_builtin_without_rules_errors(self, scan_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--no-builtin 但无 -r 应报错。"""
        rc = main(["scan", str(scan_root), "--no-builtin"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "--no-builtin" in err

    def test_scan_no_builtin_with_rules(
        self, scan_root: Path, rules_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--no-builtin + -r 仅使用用户规则。"""
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "--no-builtin"])
        assert rc == 0
        out = capsys.readouterr().out
        # 用户规则应生效
        assert "password.txt" in out

    def test_scan_merge_builtin_and_user(
        self, scan_root: Path, rules_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """默认合并内置规则与用户规则。"""
        rc = main(["scan", str(scan_root), "-r", str(rules_file)])
        assert rc == 0
        out = capsys.readouterr().out
        # 内置规则与用户规则同时生效
        assert "password.txt" in out  # 用户规则命中
        assert "doc.conf" in out  # 内置规则命中

    def test_scan_multiple_user_rules_merged(
        self, scan_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """多个 -r 文件按顺序合并，后者覆盖前者同名规则。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 共同名\n    severity: info\n    match:\n      type: filename\n      mode: contains\n      pattern: password\n',
            encoding="utf-8",
        )
        r2 = tmp_path / "r2.yaml"
        r2.write_text(
            'version: "1.0"\nrules:\n  - name: 共同名\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: nonexistent\n',
            encoding="utf-8",
        )
        # r2 覆盖 r1：pattern 变为 nonexistent，password.txt 不应命中
        rc = main(["scan", str(scan_root), "--no-builtin", "-r", str(r1), "-r", str(r2)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "password.txt" not in out

    def test_scan_multiple_user_rules_order_matters(
        self, scan_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """交换 -r 顺序后覆盖关系反转。"""
        r1 = tmp_path / "r1.yaml"
        r1.write_text(
            'version: "1.0"\nrules:\n  - name: 共同名\n    severity: info\n    match:\n      type: filename\n      mode: contains\n      pattern: nonexistent\n',
            encoding="utf-8",
        )
        r2 = tmp_path / "r2.yaml"
        r2.write_text(
            'version: "1.0"\nrules:\n  - name: 共同名\n    severity: warning\n    match:\n      type: filename\n      mode: contains\n      pattern: password\n',
            encoding="utf-8",
        )
        # r2 在后，覆盖 r1：pattern 为 password，password.txt 应命中
        rc = main(["scan", str(scan_root), "--no-builtin", "-r", str(r1), "-r", str(r2)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "password.txt" in out

    def test_scan_no_builtin_nonexistent_rules_errors(
        self, scan_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--no-builtin + 不存在的规则文件应报错。"""
        rc = main(["scan", str(scan_root), "--no-builtin", "-r", str(tmp_path / "missing.yaml")])
        assert rc == 1
        err = capsys.readouterr().err
        assert "不存在" in err

    def test_tray_no_builtin_without_rules_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        """tray --no-builtin 但无 -r 应报错。"""
        rc = main(["tray", "--no-builtin"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "--no-builtin" in err

    def test_rules_command_displays_ignore_paths(self, rules_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """rules 子命令应显示 ignore_paths 信息。"""
        rc = main(["rules", "-r", str(rules_file)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "忽略路径" in out


class TestGuiCommand:
    def test_gui_launches_when_pyside2_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PySide2 可用时调用 launch 启动 GUI。"""
        called = {"launch": False}

        def fake_launch() -> int:
            called["launch"] = True
            return 0

        # 注入 fake launch 到 uniscan.gui 命名空间
        import sys
        import types

        fake_gui = types.ModuleType("uniscan.gui")
        fake_gui.launch = fake_launch  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "uniscan.gui", fake_gui)

        rc = main(["gui"])
        assert rc == 0
        assert called["launch"] is True

    def test_gui_returns_error_when_pyside2_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """PySide2 不可用时返回错误码 3。"""
        import builtins

        original_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "uniscan.gui":
                raise ImportError("No module named 'PySide2'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        rc = main(["gui"])
        assert rc == 3
        err = capsys.readouterr().err
        assert "GUI 启动失败" in err


class TestTrayCommand:
    def test_tray_missing_rules_file_returns_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """规则文件不存在时返回错误码 1。"""
        rc = main(["tray", "-r", str(tmp_path / "nonexistent.yaml")])
        assert rc == 1
        err = capsys.readouterr().err
        assert "规则文件不存在" in err

    def test_tray_pyside2_missing_returns_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        rules_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """PySide2 不可用时返回错误码 3。"""
        import builtins

        original_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "PySide2.QtWidgets":
                raise ImportError("No module named 'PySide2'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        rc = main(["tray", "-r", str(rules_file)])
        assert rc == 3
        err = capsys.readouterr().err
        assert "托盘启动失败" in err

    def test_tray_invokes_trayapp(
        self,
        monkeypatch: pytest.MonkeyPatch,
        rules_file: Path,
        tmp_path: Path,
    ) -> None:
        """正常调用时创建 TrayApp 并启动。"""
        called: dict = {}

        class FakeTrayApp:
            def __init__(self, **kwargs: object) -> None:
                called["kwargs"] = kwargs

            def start(self, show_window: bool = True) -> int:
                called["show_window"] = show_window
                return 0

        import uniscan.watcher.tray as tray_mod

        monkeypatch.setattr(tray_mod, "TrayApp", FakeTrayApp)

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        state_file = tmp_path / "state.json"

        rc = main([
            "tray",
            "-r",
            str(rules_file),
            "-w",
            str(watch_dir),
            "--state",
            str(state_file),
        ])
        assert rc == 0
        assert called["show_window"] is False
        kwargs = called["kwargs"]
        assert kwargs["watch_paths"] == [watch_dir]
        assert kwargs["state_file"] == state_file


class TestMainErrorHandling:
    def test_no_command_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main([])  # argparse required 子命令会 SystemExit

    def test_invalid_command_exits(self) -> None:
        with pytest.raises(SystemExit):
            main(["invalid-command"])


class TestMainModuleImport:
    def test_main_module_importable(self) -> None:
        """``python -m uniscan`` 入口模块可被导入。"""
        import uniscan.__main__ as main_mod

        assert hasattr(main_mod, "main")
