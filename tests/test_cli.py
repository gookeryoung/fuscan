"""CLI 单元测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from fuscan import __version__
from fuscan.cli import build_parser, main

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def rules_file(tmp_path: Path) -> Path:
    """创建测试用规则文件。"""
    content = """
version: "1.0"
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
    (root / "doc.txt").write_text("password=AKIA1234567890ABCDEF", encoding="utf-8")
    (root / "readme.md").write_text("hello world", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "password.txt").write_text("ignored", encoding="utf-8")
    return root


class TestBuildParser:
    def test_parser_has_subcommands(self) -> None:
        parser = build_parser()
        actions = {a for a in parser._subparsers._group_actions if hasattr(a, "choices")}  # pyrefly: ignore [missing-attribute]
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
        assert "doc.txt" in out

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
        # iter-89：CSV 列新增 archive_path/inner_path 标识压缩包内部条目
        assert lines[0] == "path,archive_path,inner_path,size,severity,rule,description,match_count,detail"
        assert len(lines) >= 3  # 表头 + 2 条命中

    def test_scan_text_output_includes_match_count(
        self,
        scan_root: Path,
        rules_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """文本报告统计行应包含条数。"""
        rc = main(["scan", str(scan_root), "-r", str(rules_file)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "条数" in out

    def test_scan_json_output_includes_match_count(
        self,
        scan_root: Path,
        rules_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """JSON 报告 stats 应包含 total_matches，hits 应包含 match_count。"""
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "-o", "json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "total_matches" in data["stats"]
        for hit in data["hits"]:
            assert "match_count" in hit
            for rule in hit["rules"]:
                assert "match_count" in rule

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

    def test_scan_pdf_output_to_file(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
    ) -> None:
        """-o pdf -f 应生成 PDF 二进制文件，以 %PDF- 开头。"""
        out_file = tmp_path / "report.pdf"
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "-o", "pdf", "-f", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        data = out_file.read_bytes()
        assert data[:5] == b"%PDF-"

    def test_scan_excel_output_to_file(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
    ) -> None:
        """-o excel -f 应生成 xlsx 二进制文件（zip 格式，PK 开头）。"""
        out_file = tmp_path / "report.xlsx"
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "-o", "excel", "-f", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        data = out_file.read_bytes()
        assert data[:2] == b"PK"

    def test_scan_pdf_without_output_file_logs_error(
        self,
        scan_root: Path,
        rules_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """-o pdf 不带 -f 应记错误日志但不崩溃，退出码仍为 0。"""
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "-o", "pdf"])
        assert rc == 0

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

    def test_scan_perf_save_writes_json(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--perf-save 应将性能统计写入 JSON 文件。"""
        perf_path = tmp_path / "perf_stats.json"
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "--perf-save", str(perf_path)])
        assert rc == 0
        assert perf_path.exists()
        data = json.loads(perf_path.read_text(encoding="utf-8"))
        assert "timestamp" in data
        assert "stages" in data
        assert "meta" in data
        # PerfStats 始终启用，应有阶段数据
        assert len(data["stages"]) > 0

    def test_scan_perf_flag_enables_detailed_logging(
        self,
        scan_root: Path,
        rules_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--perf 应启用 PerfTimer 详细日志。"""
        from fuscan import perf as perf_mod

        original = perf_mod._PerfState.enabled
        try:
            rc = main(["-vv", "scan", str(scan_root), "-r", str(rules_file), "--perf"])
            assert rc == 0
            assert perf_mod._PerfState.enabled is True
        finally:
            perf_mod._PerfState.enabled = original


class TestBuiltinRules:
    """内置通用规则集成测试。"""

    def test_scan_with_builtin_only(self, scan_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """不带 -r 时默认使用内置通用规则。"""
        rc = main(["scan", str(scan_root)])
        assert rc == 0
        out = capsys.readouterr().out
        # 内置规则包含通用密码赋值检测，doc.txt 含 password= 应命中
        assert "doc.txt" in out

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
        assert "doc.txt" in out  # 内置规则命中

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

    def test_rules_command_displays_ignore_paths(self, rules_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """rules 子命令应显示 ignore_paths 信息。"""
        rc = main(["rules", "-r", str(rules_file)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "忽略路径" in out


class TestGuiCommand:
    def test_gui_launches_when_pyside_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PySide 可用时调用 fuscan.app.main 启动 GUI。"""
        called = {"launched": False}

        def fake_main() -> int:
            called["launched"] = True
            return 0

        # 注入 fake main 到 fuscan.app 命名空间
        import sys
        import types

        fake_app = types.ModuleType("fuscan.app")
        fake_app.main = fake_main  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "fuscan.app", fake_app)

        rc = main(["gui"])
        assert rc == 0
        assert called["launched"] is True

    def test_gui_returns_error_when_pyside_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """PySide 不可用时返回错误码 3。"""
        import builtins

        original_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "fuscan.app":
                raise ImportError("No module named 'PySide2'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        rc = main(["gui"])
        assert rc == 3
        err = capsys.readouterr().err
        assert "GUI 启动失败" in err


class TestMainErrorHandling:
    def test_no_command_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main([])  # argparse required 子命令会 SystemExit

    def test_invalid_command_exits(self) -> None:
        with pytest.raises(SystemExit):
            main(["invalid-command"])


class TestCliErrorPaths:
    """CLI 异常路径覆盖。"""

    def test_keyboard_interrupt_returns_130(
        self,
        scan_root: Path,
        rules_file: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """KeyboardInterrupt 时返回 130。"""
        import fuscan.cli as cli_mod

        def raise_keyboard_interrupt(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise KeyboardInterrupt

        monkeypatch.setattr(cli_mod, "load_with_builtin", raise_keyboard_interrupt)
        rc = main(["scan", str(scan_root), "-r", str(rules_file)])
        assert rc == 130
        err = capsys.readouterr().err
        assert "已中断" in err

    def test_generic_exception_returns_1(
        self,
        scan_root: Path,
        rules_file: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未预期异常时返回 1。"""
        import fuscan.cli as cli_mod

        def raise_exception(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("模拟未知错误")

        monkeypatch.setattr(cli_mod, "load_with_builtin", raise_exception)
        rc = main(["scan", str(scan_root), "-r", str(rules_file)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "错误" in err

    def test_rule_with_description_displayed(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """rules 子命令应显示规则描述。"""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            'version: "1.0"\n'
            "rules:\n"
            "  - name: 带描述的规则\n"
            "    severity: warning\n"
            "    description: 这是一个描述信息\n"
            "    match:\n"
            "      type: filename\n"
            "      mode: contains\n"
            "      pattern: password\n",
            encoding="utf-8",
        )
        rc = main(["rules", "-r", str(rules_file)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "这是一个描述信息" in out

    def test_format_text_path_not_relative_to_root(self) -> None:
        """命中路径不在扫描根下时 to_text 回退到绝对路径。"""
        from fuscan.rules.model import Severity
        from fuscan.scanner.result import RuleHit, ScanReport, ScanResult, ScanStats

        report = ScanReport(
            root=Path("C:/scan"),
            results=(
                ScanResult(
                    path=Path("D:/other/path/file.txt"),
                    size=10,
                    hits=(RuleHit(rule_name="r", severity=Severity.WARNING, detail="命中"),),
                ),
            ),
            stats=ScanStats(total_files=1, scanned_files=1, matched_files=1),
        )
        text = report.to_text()
        assert "D:/other/path/file.txt" in text or "D:\\other\\path\\file.txt" in text

    def test_configure_logging_verbose_levels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_configure_logging 不同 verbose 级别传入不同日志级别到 basicConfig。"""
        import logging

        from fuscan.cli import _configure_logging

        calls: list[dict[str, Any]] = []

        def mock_basic_config(**kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)

        monkeypatch.setattr(logging, "basicConfig", mock_basic_config)

        _configure_logging(0)
        assert calls[-1]["level"] == logging.WARNING

        _configure_logging(1)
        assert calls[-1]["level"] == logging.INFO

        _configure_logging(2)
        assert calls[-1]["level"] == logging.DEBUG


class TestCacheCommand:
    """缓存子命令测试。"""

    def test_cache_stats_shows_statistics(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cache stats 应输出缓存统计信息。"""
        cache_path = tmp_path / "cache.db"
        # 先扫描以创建缓存
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "--cache-path", str(cache_path)])
        assert rc == 0
        assert cache_path.exists()
        capsys.readouterr()

        rc = main(["cache", "stats", "--cache-path", str(cache_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "缓存数据库" in out
        assert "已扫描文件" in out

    def test_cache_stats_nonexistent_db(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cache stats 在缓存不存在时应提示。"""
        cache_path = tmp_path / "nonexistent.db"
        rc = main(["cache", "stats", "--cache-path", str(cache_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "不存在" in out

    def test_cache_clear_deletes_db(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cache clear 应删除缓存数据库文件。"""
        cache_path = tmp_path / "cache.db"
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "--cache-path", str(cache_path)])
        assert rc == 0
        assert cache_path.exists()
        capsys.readouterr()

        rc = main(["cache", "clear", "--cache-path", str(cache_path)])
        assert rc == 0
        assert not cache_path.exists()
        out = capsys.readouterr().out
        assert "已清空" in out

    def test_cache_clear_nonexistent_db(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cache clear 在缓存不存在时应提示。"""
        cache_path = tmp_path / "nonexistent.db"
        rc = main(["cache", "clear", "--cache-path", str(cache_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "不存在" in out

    def test_cache_prune_removes_stale(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cache prune --max-age-days 0 应清理所有文件缓存。"""
        cache_path = tmp_path / "cache.db"
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "--cache-path", str(cache_path)])
        assert rc == 0
        capsys.readouterr()

        rc = main(["cache", "prune", "--cache-path", str(cache_path), "--max-age-days", "0"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "已清理" in out

    def test_scan_no_cache_flag_disables_cache(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
    ) -> None:
        """--no-cache 时不创建 cache.db。"""
        cache_path = tmp_path / "cache.db"
        rc = main(
            [
                "scan",
                str(scan_root),
                "-r",
                str(rules_file),
                "--no-cache",
                "--cache-path",
                str(cache_path),
            ]
        )
        assert rc == 0
        assert not cache_path.exists()

    def test_scan_cache_path_uses_custom_path(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
    ) -> None:
        """--cache-path 应使用自定义路径创建缓存。"""
        cache_path = tmp_path / "subdir" / "my_cache.db"
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "--cache-path", str(cache_path)])
        assert rc == 0
        assert cache_path.exists()

    def test_scan_creates_cache_db_by_default(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """默认 scan 应创建 cache.db。"""
        cache_path = tmp_path / "cache.db"
        monkeypatch.setenv("HOME", str(tmp_path))
        rc = main(["scan", str(scan_root), "-r", str(rules_file), "--cache-path", str(cache_path)])
        assert rc == 0
        assert cache_path.exists()


class TestBenchmarkCommand:
    """benchmark 子命令测试。"""

    def test_parse_benchmark_command(self) -> None:
        """benchmark 子命令参数解析。"""
        parser = build_parser()
        args = parser.parse_args(["benchmark", "some_path", "-r", "r.yaml", "--rounds", "3", "--warmup", "2"])
        assert args.command == "benchmark"
        assert str(args.path) == "some_path"
        assert args.rounds == 3
        assert args.warmup == 2

    def test_bench_alias(self) -> None:
        """bench 是 benchmark 的别名。"""
        parser = build_parser()
        args = parser.parse_args(["bench", "p"])
        assert args.command == "bench"

    def test_benchmark_table_output(
        self,
        scan_root: Path,
        rules_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """table 输出应含阶段表头与测量信息。"""
        rc = main(["benchmark", str(scan_root), "-r", str(rules_file), "--rounds", "1", "--warmup", "0", "--no-cache"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "基准测量" in out
        assert "均值(ms)" in out
        assert "占比" in out

    def test_benchmark_json_output(
        self,
        scan_root: Path,
        rules_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """json 输出应为合法 JSON，含 stages 数组。"""
        rc = main(
            [
                "benchmark",
                str(scan_root),
                "-r",
                str(rules_file),
                "--rounds",
                "1",
                "--warmup",
                "0",
                "--no-cache",
                "-o",
                "json",
            ]
        )
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["rounds"] == 1
        assert "stages" in data
        assert isinstance(data["stages"], list)

    def test_benchmark_save_baseline(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--save-baseline 应写出基准线 JSON 文件。"""
        baseline = tmp_path / "baseline.json"
        rc = main(
            [
                "benchmark",
                str(scan_root),
                "-r",
                str(rules_file),
                "--rounds",
                "1",
                "--warmup",
                "0",
                "--no-cache",
                "--save-baseline",
                str(baseline),
            ]
        )
        assert rc == 0
        assert baseline.exists()
        data = json.loads(baseline.read_text(encoding="utf-8"))
        assert "stages" in data
        assert "meta" in data
        err = capsys.readouterr().err
        assert "基准线已保存" in err

    def test_benchmark_compare_baseline(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--baseline 应加载历史基准线并输出对比表。"""
        baseline = tmp_path / "baseline.json"
        rc = main(
            [
                "benchmark",
                str(scan_root),
                "-r",
                str(rules_file),
                "--rounds",
                "1",
                "--warmup",
                "0",
                "--no-cache",
                "--save-baseline",
                str(baseline),
            ]
        )
        assert rc == 0
        capsys.readouterr()
        # 用刚保存的基准线对比自身
        rc = main(
            [
                "benchmark",
                str(scan_root),
                "-r",
                str(rules_file),
                "--rounds",
                "1",
                "--warmup",
                "0",
                "--no-cache",
                "--baseline",
                str(baseline),
            ]
        )
        # 对比自身可能因抖动判回归（退出码 0 或 1 均合法），只校验输出含对比表
        assert rc in (0, 1)
        out = capsys.readouterr().out
        assert "对比基准线" in out

    def test_benchmark_regression_exit_code(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """本次耗时远超基准线时以退出码 1 提示回归。"""
        # 手工写一个各阶段极小的基准线，确保本次必然回归
        baseline = tmp_path / "tiny.json"
        baseline.write_text(
            json.dumps(
                {
                    "timestamp": "2026-01-01T00:00:00",
                    "stages": {"match": {"mean_ms": 0.0001}, "walk": {"mean_ms": 0.0001}},
                    "meta": {},
                }
            ),
            encoding="utf-8",
        )
        rc = main(
            [
                "benchmark",
                str(scan_root),
                "-r",
                str(rules_file),
                "--rounds",
                "1",
                "--warmup",
                "0",
                "--no-cache",
                "--baseline",
                str(baseline),
            ]
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "回归" in out

    def test_benchmark_nonexistent_path(self, tmp_path: Path, rules_file: Path) -> None:
        """路径不存在返回 1。"""
        rc = main(["benchmark", str(tmp_path / "missing"), "-r", str(rules_file), "--no-cache"])
        assert rc == 1

    def test_benchmark_invalid_rounds(
        self, scan_root: Path, rules_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--rounds 0 返回 1 并提示。"""
        rc = main(["benchmark", str(scan_root), "-r", str(rules_file), "--no-cache", "--rounds", "0"])
        assert rc == 1
        assert "rounds" in capsys.readouterr().err

    def test_benchmark_invalid_warmup(
        self, scan_root: Path, rules_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--warmup -1 返回 1 并提示。"""
        rc = main(["benchmark", str(scan_root), "-r", str(rules_file), "--no-cache", "--warmup", "-1"])
        assert rc == 1
        assert "warmup" in capsys.readouterr().err

    def test_benchmark_missing_baseline_file(
        self, scan_root: Path, rules_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--baseline 指向不存在文件时返回 1 并提示。"""
        rc = main(
            [
                "benchmark",
                str(scan_root),
                "-r",
                str(rules_file),
                "--no-cache",
                "--baseline",
                str(tmp_path / "nope.json"),
            ]
        )
        assert rc == 1
        assert "加载基准线失败" in capsys.readouterr().err

    def test_benchmark_bad_ruleset(self, scan_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--no-builtin 无 -r 返回 1。"""
        rc = main(["benchmark", str(scan_root), "--no-cache", "--no-builtin"])
        assert rc == 1

    def test_benchmark_with_cache(
        self,
        scan_root: Path,
        rules_file: Path,
        tmp_path: Path,
    ) -> None:
        """启用缓存路径分支：--cache-path 创建缓存后 benchmark 正常完成。"""
        cache_path = tmp_path / "cache.db"
        rc = main(
            [
                "benchmark",
                str(scan_root),
                "-r",
                str(rules_file),
                "--rounds",
                "1",
                "--warmup",
                "0",
                "--cache-path",
                str(cache_path),
            ]
        )
        assert rc == 0
        assert cache_path.exists()


class TestBpCommand:
    """bp 子命令测试：验证并发扫描假卡死修复。"""

    def test_parse_bp_command(self) -> None:
        """bp 子命令参数解析与默认值。"""
        parser = build_parser()
        args = parser.parse_args(["bp"])
        assert args.command == "bp"
        assert args.fast_files == 20
        assert args.slow_files == 4
        assert args.slow_duration == 1.5
        assert args.workers == 8
        assert args.progress_interval == 0.1

    def test_parse_bp_custom_args(self) -> None:
        """bp 子命令自定义参数。"""
        parser = build_parser()
        args = parser.parse_args(
            ["bp", "--fast-files", "5", "--slow-files", "2", "--slow-duration", "0.3", "--workers", "4"]
        )
        assert args.fast_files == 5
        assert args.slow_files == 2
        assert args.slow_duration == 0.3
        assert args.workers == 4

    def test_bp_rejects_zero_slow_files(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--slow-files 0 应报错退出 1。"""
        rc = main(["bp", "--slow-files", "0"])
        assert rc == 1
        assert "至少为 1" in capsys.readouterr().err

    def test_bp_rejects_zero_workers(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--workers 0 应报错退出 1。"""
        rc = main(["bp", "--workers", "0"])
        assert rc == 1
        assert ">= 1" in capsys.readouterr().err

    def test_bp_rejects_nonpositive_duration(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--slow-duration 0 应报错退出 1。"""
        rc = main(["bp", "--slow-duration", "0"])
        assert rc == 1
        assert "> 0" in capsys.readouterr().err

    def test_bp_rejects_negative_fast_files(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--fast-files 负数应报错退出 1。"""
        rc = main(["bp", "--fast-files", "-1"])
        assert rc == 1
        assert "不能为负" in capsys.readouterr().err

    def test_bp_passes_with_minimal_scenario(self, capsys: pytest.CaptureFixture[str]) -> None:
        """最小场景（2 快 + 2 慢 1.2s + 2 worker）应验证通过返回 0。

        slow_duration 需 > 1.0s 以确保 ``wait`` 超时分支（固定 0.5s 阈值）
        至少触发 2 次，从而验证 elapsed_ms 单调递增。
        """
        rc = main(
            [
                "bp",
                "--fast-files",
                "2",
                "--slow-files",
                "2",
                "--slow-duration",
                "1.2",
                "--workers",
                "2",
                "--progress-interval",
                "0.05",
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "[PASS]" in out
        assert "超时分支触发" in out
        assert "slow_00.txt" in out
