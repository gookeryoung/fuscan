"""多格式文件与多规则类型扫描测试。

覆盖矩阵：
- 文件格式：JSON/XML/CSV/YAML/Python/HTML/SQL/Markdown
- MatchTarget × MatchMode：FILENAME/CONTENT/PATH × CONTAINS/EQUALS/STARTSWITH/ENDSWITH/REGEX
- 组合规则：AND/OR/NOT 及嵌套
- 边界场景：空文件、大文件、二进制、GBK 编码、嵌套目录
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    NotMatch,
    OrMatch,
    Rule,
    RuleSet,
    Severity,
)
from fuscan.scanner import Scanner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ============================ Helper 函数 ============================


def _rs(*rules: Rule) -> RuleSet:
    return RuleSet(version="1.0", rules=tuple(rules))


def _leaf(
    target: MatchTarget,
    mode: MatchMode,
    pattern: str,
    name: str = "r",
    severity: Severity = Severity.WARNING,
    exts: tuple[str, ...] = (),
) -> Rule:
    return Rule(
        name=name,
        severity=severity,
        match=LeafMatch(target=target, mode=mode, pattern=pattern),
        file_extensions=exts,
    )


# ============================ 多格式文件 Fixtures ============================


@pytest.fixture()
def multi_format_root(tmp_path: Path) -> Path:
    """创建包含 8 种格式的测试目录，每种格式含敏感/干净文件对。"""
    root = tmp_path / "multi"
    root.mkdir()

    # JSON：含 AWS 密钥
    (root / "config.json").write_text(
        json.dumps({"access_key": "AKIAIOSFODNN7EXAMPLE", "region": "us-east-1"}), encoding="utf-8"
    )
    (root / "clean.json").write_text(json.dumps({"name": "hello"}), encoding="utf-8")

    # XML：属性值含密钥
    (root / "data.xml").write_text('<root><user token="AKIAIOSFODNN7EXAMPLE">alice</user></root>', encoding="utf-8")
    (root / "clean.xml").write_text("<root><user>bob</user></root>", encoding="utf-8")

    # CSV：列数据含密钥
    (root / "data.csv").write_text("id,key,note\n1,AKIAIOSFODNN7EXAMPLE,secret\n2,normal,fine\n", encoding="utf-8")
    (root / "clean.csv").write_text("id,note\n1,hello\n", encoding="utf-8")

    # YAML：值含密钥
    (root / "config.yaml").write_text(
        "aws:\n  access_key: AKIAIOSFODNN7EXAMPLE\n  region: us-east-1\n", encoding="utf-8"
    )
    (root / "clean.yaml").write_text("app:\n  name: hello\n", encoding="utf-8")

    # Python：字符串字面量含密钥
    (root / "settings.py").write_text('ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\nREGION = "us-east-1"\n', encoding="utf-8")
    (root / "clean.py").write_text('APP_NAME = "hello"\n', encoding="utf-8")

    # HTML：script 标签含密钥
    (root / "page.html").write_text('<html><script>var key = "AKIAIOSFODNN7EXAMPLE";</script></html>', encoding="utf-8")
    (root / "clean.html").write_text("<html><body>hello</body></html>", encoding="utf-8")

    # SQL：INSERT VALUES 含密钥
    (root / "seed.sql").write_text("INSERT INTO keys VALUES (1, 'AKIAIOSFODNN7EXAMPLE');\n", encoding="utf-8")
    (root / "clean.sql").write_text("INSERT INTO users VALUES (1, 'alice');\n", encoding="utf-8")

    # Markdown：代码块含密钥
    (root / "readme.md").write_text("```python\nkey = 'AKIAIOSFODNN7EXAMPLE'\n```\n", encoding="utf-8")
    (root / "notes.md").write_text("# Notes\n\nJust a normal doc.\n", encoding="utf-8")

    return root


# ============================ 多格式内容扫描测试 ============================


class TestMultiFormatContentScan:
    """不同格式文件的 content 规则扫描。"""

    @pytest.mark.parametrize(
        "filename",
        ["config.json", "data.xml", "data.csv", "config.yaml", "settings.py", "page.html", "seed.sql", "readme.md"],
    )
    def test_content_regex_finds_akia_in_each_format(self, multi_format_root: Path, filename: str) -> None:
        """正则规则 AKIA[0-9A-Z]{16} 应在每种格式中命中。"""
        rule = _leaf(MatchTarget.CONTENT, MatchMode.REGEX, r"AKIA[0-9A-Z]{16}", name="aws_key")
        scanner = Scanner(_rs(rule))
        report = scanner.scan(multi_format_root)
        hit_files = {h.path.name for h in report.hits}
        assert filename in hit_files

    def test_content_contains_scans_all_formats(self, multi_format_root: Path) -> None:
        """contains 规则应命中所有含 AKIA 的文件，不命中 clean 文件。"""
        rule = _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "AKIAIOSFODNN7EXAMPLE", name="akia")
        scanner = Scanner(_rs(rule))
        report = scanner.scan(multi_format_root)
        hit_files = {h.path.name for h in report.hits}
        expected = {
            "config.json",
            "data.xml",
            "data.csv",
            "config.yaml",
            "settings.py",
            "page.html",
            "seed.sql",
            "readme.md",
        }
        assert hit_files == expected

    def test_content_contains_case_insensitive(self, multi_format_root: Path) -> None:
        """大小写不敏感的 contains 应命中。"""
        rule = Rule(
            name="ci",
            severity=Severity.INFO,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="akia",
                case_sensitive=False,
            ),
        )
        scanner = Scanner(_rs(rule))
        report = scanner.scan(multi_format_root)
        assert report.stats.matched_files == 8

    def test_content_regex_python_specific(self, multi_format_root: Path) -> None:
        """全局 scan_extensions 过滤仅扫描 .py 文件（iter-71）。"""
        rule = _leaf(
            MatchTarget.CONTENT,
            MatchMode.REGEX,
            r"AKIA[A-Z0-9]{16}",
            name="py_only",
        )
        scanner = Scanner(_rs(rule), scan_extensions=("py",))
        report = scanner.scan(multi_format_root)
        hit_files = {h.path.name for h in report.hits}
        assert hit_files == {"settings.py"}


# ============================ MatchTarget × MatchMode 覆盖矩阵 ============================


class TestMatchTargetModeMatrix:
    """所有 MatchTarget × MatchMode 组合测试。"""

    @pytest.fixture()
    def matrix_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "matrix"
        root.mkdir()
        (root / "secret_config.txt").write_text("password=AKIA1234\nhello world", encoding="utf-8")
        (root / "normal.txt").write_text("just text\n", encoding="utf-8")
        return root

    # --- FILENAME ---

    def test_filename_contains(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.FILENAME, MatchMode.CONTAINS, "secret", name="fc")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    def test_filename_equals(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.FILENAME, MatchMode.EQUALS, "secret_config.txt", name="fe")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    def test_filename_startswith(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.FILENAME, MatchMode.STARTSWITH, "secret", name="fs")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    def test_filename_endswith(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.FILENAME, MatchMode.ENDSWITH, ".txt", name="fend")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt", "normal.txt"}

    def test_filename_regex(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.FILENAME, MatchMode.REGEX, r"^secret_\w+\.txt$", name="fr")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    # --- CONTENT ---

    def test_content_contains(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "AKIA", name="cc")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    def test_content_equals(self, matrix_root: Path) -> None:
        # equals 比较完整内容
        full_content = "password=AKIA1234\nhello world"
        scanner = Scanner(_rs(_leaf(MatchTarget.CONTENT, MatchMode.EQUALS, full_content, name="ce")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    def test_content_startswith(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.CONTENT, MatchMode.STARTSWITH, "password", name="cs")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    def test_content_endswith(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.CONTENT, MatchMode.ENDSWITH, "hello world", name="cend")))
        # secret_config.txt 以 "hello world" 结尾
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    def test_content_regex(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.CONTENT, MatchMode.REGEX, r"AKIA\d+", name="cr")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    # --- PATH ---

    def test_path_contains(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.PATH, MatchMode.CONTAINS, "matrix", name="pc")))
        report = scanner.scan(matrix_root)
        assert report.stats.matched_files == 2

    def test_path_equals(self, matrix_root: Path) -> None:
        target = str(matrix_root / "secret_config.txt")
        scanner = Scanner(_rs(_leaf(MatchTarget.PATH, MatchMode.EQUALS, target, name="pe")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    def test_path_startswith(self, matrix_root: Path) -> None:
        prefix = str(matrix_root)
        scanner = Scanner(_rs(_leaf(MatchTarget.PATH, MatchMode.STARTSWITH, prefix, name="ps")))
        report = scanner.scan(matrix_root)
        assert report.stats.matched_files == 2

    def test_path_endswith(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.PATH, MatchMode.ENDSWITH, "secret_config.txt", name="pend")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}

    def test_path_regex(self, matrix_root: Path) -> None:
        scanner = Scanner(_rs(_leaf(MatchTarget.PATH, MatchMode.REGEX, r"secret_\w+\.txt$", name="pr")))
        report = scanner.scan(matrix_root)
        assert {h.path.name for h in report.hits} == {"secret_config.txt"}


# ============================ 组合规则测试 ============================


class TestCompositeRules:
    """AND/OR/NOT 及嵌套组合规则。"""

    @pytest.fixture()
    def composite_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "composite"
        root.mkdir()
        (root / "secret_config.json").write_text('{"key": "AKIA1234"}', encoding="utf-8")
        (root / "secret_readme.md").write_text("# Readme\nAKIA1234\n", encoding="utf-8")
        (root / "normal_config.json").write_text('{"key": "normal"}', encoding="utf-8")
        (root / "secret_notes.txt").write_text("just notes\n", encoding="utf-8")
        return root

    def test_and_filename_and_content(self, composite_root: Path) -> None:
        """AND: filename contains 'secret' AND content contains 'AKIA'。"""
        rule = Rule(
            name="and_rule",
            severity=Severity.CRITICAL,
            match=AndMatch(
                children=(
                    LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="secret"),
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="AKIA"),
                )
            ),
        )
        scanner = Scanner(_rs(rule))
        report = scanner.scan(composite_root)
        hit_files = {h.path.name for h in report.hits}
        # secret_config.json 和 secret_readme.md 同时满足两个条件
        assert hit_files == {"secret_config.json", "secret_readme.md"}

    def test_or_filename_or_content(self, composite_root: Path) -> None:
        """OR: filename equals 'normal_config.json' OR content startswith 'just'。"""
        rule = Rule(
            name="or_rule",
            severity=Severity.WARNING,
            match=OrMatch(
                children=(
                    LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="normal_config.json"),
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.STARTSWITH, pattern="just"),
                )
            ),
        )
        scanner = Scanner(_rs(rule))
        report = scanner.scan(composite_root)
        hit_files = {h.path.name for h in report.hits}
        # normal_config.json 命中 filename，secret_notes.txt 命中 content startswith
        assert hit_files == {"normal_config.json", "secret_notes.txt"}

    def test_not_content(self, composite_root: Path) -> None:
        """NOT: content does NOT contain 'AKIA'。"""
        rule = Rule(
            name="not_rule",
            severity=Severity.INFO,
            match=NotMatch(
                child=LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.CONTAINS,
                    pattern="AKIA",
                )
            ),
        )
        scanner = Scanner(_rs(rule))
        report = scanner.scan(composite_root)
        hit_files = {h.path.name for h in report.hits}
        # 不含 AKIA 的文件命中
        assert hit_files == {"normal_config.json", "secret_notes.txt"}

    def test_nested_and_or_not(self, composite_root: Path) -> None:
        """嵌套: AND(OR(filename contains 'secret', content contains 'AKIA'), NOT(path endswith '.json'))。"""
        rule = Rule(
            name="nested",
            severity=Severity.CRITICAL,
            match=AndMatch(
                children=(
                    OrMatch(
                        children=(
                            LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="secret"),
                            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="AKIA"),
                        )
                    ),
                    NotMatch(
                        child=LeafMatch(
                            target=MatchTarget.PATH,
                            mode=MatchMode.ENDSWITH,
                            pattern=".json",
                        )
                    ),
                )
            ),
        )
        scanner = Scanner(_rs(rule))
        report = scanner.scan(composite_root)
        hit_files = {h.path.name for h in report.hits}
        # secret_readme.md (secret + AKIA, not .json) 和 secret_notes.txt (secret, not .json)
        # secret_config.json 排除 (.json)
        assert hit_files == {"secret_readme.md", "secret_notes.txt"}

    def test_extension_filter_with_content_regex(self, composite_root: Path) -> None:
        """全局 scan_extensions 过滤 + content regex（iter-71）。"""
        rule = Rule(
            name="ext_regex",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"AKIA\d+"),
        )
        scanner = Scanner(_rs(rule), scan_extensions=("json", "md"))
        report = scanner.scan(composite_root)
        hit_files = {h.path.name for h in report.hits}
        # 仅扫描 .json 和 .md 文件
        assert hit_files == {"secret_config.json", "secret_readme.md"}

    def test_multiple_rules_different_severity(self, composite_root: Path) -> None:
        """多条规则不同 severity 同时扫描。"""
        rules = (
            _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "AKIA", name="critical_key", severity=Severity.CRITICAL),
            _leaf(MatchTarget.FILENAME, MatchMode.CONTAINS, "secret", name="warning_name", severity=Severity.WARNING),
            _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "normal", name="info_normal", severity=Severity.INFO),
        )
        scanner = Scanner(_rs(*rules))
        report = scanner.scan(composite_root)

        # secret_config.json: AKIA (critical) + secret (warning)
        config_rule_hits: dict[str, Severity] = {}
        for sr in report.hits:
            if sr.path.name == "secret_config.json":
                for rh in sr.hits:
                    config_rule_hits[rh.rule_name] = rh.severity
        assert "critical_key" in config_rule_hits
        assert config_rule_hits["critical_key"] == Severity.CRITICAL
        assert "warning_name" in config_rule_hits

        # normal_config.json: normal (info)
        normal_rule_names: set[str] = set()
        for sr in report.hits:
            if sr.path.name == "normal_config.json":
                normal_rule_names.update(rh.rule_name for rh in sr.hits)
        assert "info_normal" in normal_rule_names


# ============================ 边界场景测试 ============================


class TestEdgeCases:
    """边界场景：空文件、大文件、二进制、GBK 编码、嵌套目录。"""

    def test_empty_file_not_error(self, tmp_path: Path) -> None:
        """空文件不应报错，content 规则不命中。"""
        (tmp_path / "empty.txt").write_text("", encoding="utf-8")
        rule = _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "anything", name="r")
        scanner = Scanner(_rs(rule))
        report = scanner.scan(tmp_path)
        assert report.stats.errors == 0
        assert report.stats.matched_files == 0
        assert report.stats.scanned_files == 1

    def test_large_file_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """超过 max_size 的文件应跳过内容读取（返回空内容）。"""
        from fuscan.extractors import default_registry
        from fuscan.extractors.text import TextExtractor

        # 临时降低 TextExtractor 的 max_size 到 1MB，避免写 100MB 文件
        extractor = default_registry.get("txt")
        assert isinstance(extractor, TextExtractor)
        original_max_size = extractor._max_size
        extractor._max_size = 1024 * 1024  # 1MB

        large = tmp_path / "large.txt"
        # 写 2MB 文本（填充 'A'），超过自定义 max_size
        large.write_text("A" * (2 * 1024 * 1024), encoding="utf-8")
        rule = _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "A", name="r")
        scanner = Scanner(_rs(rule))
        report = scanner.scan(tmp_path)
        # 大文件内容为空，不命中
        assert report.stats.matched_files == 0
        assert report.stats.scanned_files == 1
        assert report.stats.errors == 0
        extractor._max_size = original_max_size

    def test_binary_file_scanned_without_error(self, tmp_path: Path) -> None:
        """二进制文件应被扫描，content 规则不命中（errors='ignore'）。"""
        (tmp_path / "data.bin").write_bytes(bytes(range(256)) * 4)
        rule = _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "password", name="r")
        scanner = Scanner(_rs(rule))
        report = scanner.scan(tmp_path)
        assert report.stats.errors == 0
        assert report.stats.scanned_files == 1

    def test_gbk_encoded_file(self, tmp_path: Path) -> None:
        """GBK 编码文件应被 charset-normalizer 正确检测。"""
        gbk_content = "密钥=AKIA1234\n说明=测试".encode("gbk")
        (tmp_path / "gbk.txt").write_bytes(gbk_content)
        rule = _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "AKIA", name="r")
        scanner = Scanner(_rs(rule))
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files == 1
        assert {h.path.name for h in report.hits} == {"gbk.txt"}

    def test_nested_directories(self, tmp_path: Path) -> None:
        """嵌套目录应递归扫描。"""
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "secret.txt").write_text("AKIA1234", encoding="utf-8")
        (tmp_path / "a" / "normal.txt").write_text("hello", encoding="utf-8")
        rule = _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "AKIA", name="r")
        scanner = Scanner(_rs(rule))
        report = scanner.scan(tmp_path)
        assert report.stats.scanned_files == 2
        assert report.stats.matched_files == 1
        assert {h.path.name for h in report.hits} == {"secret.txt"}

    def test_ignore_dirs_excludes_files(self, tmp_path: Path) -> None:
        """ignore_dirs 应排除目录内文件。"""
        (tmp_path / "secret.txt").write_text("AKIA1234", encoding="utf-8")
        ignored = tmp_path / ".git"
        ignored.mkdir()
        (ignored / "secret.txt").write_text("AKIA1234", encoding="utf-8")
        rule = _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "AKIA", name="r")
        scanner = Scanner(_rs(rule), ignore_dirs=(".git",))
        report = scanner.scan(tmp_path)
        assert report.stats.scanned_files == 1
        assert {h.path.name for h in report.hits} == {"secret.txt"}

    def test_max_depth_limit(self, tmp_path: Path) -> None:
        """max_depth 应限制递归深度。"""
        deep = tmp_path / "l1" / "l2" / "l3"
        deep.mkdir(parents=True)
        (deep / "secret.txt").write_text("AKIA1234", encoding="utf-8")
        (tmp_path / "l1" / "top.txt").write_text("AKIA1234", encoding="utf-8")
        rule = _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "AKIA", name="r")
        scanner = Scanner(_rs(rule), max_depth=1)
        report = scanner.scan(tmp_path)
        # max_depth=1: 只扫描 l1/ 下的文件，不递归到 l2/l3
        hit_files = {h.path.name for h in report.hits}
        assert "top.txt" in hit_files
        assert "secret.txt" not in hit_files

    def test_concurrent_scan_matches_sequential(self, tmp_path: Path) -> None:
        """多线程扫描结果应与单线程一致。"""
        for i in range(20):
            (tmp_path / f"file_{i}.txt").write_text(f"content {i} {'AKIA1234' if i % 3 == 0 else ''}", encoding="utf-8")
        rule = _leaf(MatchTarget.CONTENT, MatchMode.CONTAINS, "AKIA", name="r")

        seq_scanner = Scanner(_rs(rule), max_workers=1)
        seq_report = seq_scanner.scan(tmp_path)

        conc_scanner = Scanner(_rs(rule), max_workers=4)
        conc_report = conc_scanner.scan(tmp_path)

        assert seq_report.stats.matched_files == conc_report.stats.matched_files
        assert seq_report.stats.scanned_files == conc_report.stats.scanned_files
        assert seq_report.stats.errors == conc_report.stats.errors
