"""CLI 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyfilescan import __version__
from pyfilescan.cli import build_parser, main


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
    (root / "doc.conf").write_text("key=AKIA1234", encoding="utf-8")
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
        assert str(args.rules) == "rules.yaml"
        assert args.output_format == "text"

    def test_parse_scan_with_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
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
            ]
        )
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

        rc = main(
            [
                "scan",
                str(scan_root),
                "-r",
                str(rules_file),
                "--ignore-dir",
                "exclude_me",
            ]
        )
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


class TestGuiCommand:
    def test_gui_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["gui"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "P3" in err


class TestMainErrorHandling:
    def test_no_command_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main([])  # argparse required 子命令会 SystemExit

    def test_invalid_command_exits(self) -> None:
        with pytest.raises(SystemExit):
            main(["invalid-command"])
