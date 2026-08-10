"""内置匹配规则单元测试。

验证 ``builtin-patterns.yaml`` 中 3 条规则正确匹配对应敏感信息样本，
且不误匹配无关文本。覆盖：

- P0101-通用密码赋值（content regex）：``password``/``passwd``/``pwd`` 赋值语句
- P0102-敏感配置文件名（filename regex）：``.env``/``.pem``/``.key`` 等敏感文件名
- P0103-邮件信息包含敏感词（AND: filename ``.eml`` + content 敏感词）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fuscan.rules import load_builtin_ruleset
from fuscan.rules.model import AndMatch, LeafMatch, MatchTarget, Severity
from fuscan.scanner.context import FileEntry, MatchContext
from fuscan.scanner.matchers import Matcher, build_matcher


def _make_context(name: str, content: str = "") -> MatchContext:
    """构造匹配上下文（路径不存在，仅用于 filename/content 匹配）。

    :param name: 文件名（用于 filename 匹配与扩展名推导）
    :param content: 文件内容（用于 content 匹配）
    """
    path = Path("/tmp") / name
    entry = FileEntry(
        path=path,
        name=name,
        size=len(content),
        mtime=0.0,
        extension=_extract_extension(name),
    )
    return MatchContext(entry, content_provider=lambda e: content)


def _extract_extension(name: str) -> str:
    """与 :func:`fuscan.scanner.context._extract_extension` 一致的扩展名推导。"""
    suffix = Path(name).suffix
    if suffix:
        return suffix.lower().lstrip(".")
    if name.startswith(".") and len(name) > 1:
        return name[1:].lower()
    return ""


def _build_matcher_by_name(rule_name: str) -> tuple[str, Severity, Matcher]:
    """按规则名从内置规则集构造 (name, severity, matcher)。"""
    rs = load_builtin_ruleset()
    rule = next(r for r in rs.rules if r.name == rule_name)
    return rule.name, rule.severity, build_matcher(rule.match)


class TestBuiltinRulesetStructure:
    """内置规则集结构验证。"""

    def test_builtin_ruleset_has_3_rules(self) -> None:
        """内置规则集应包含 P0101/P0102/P0103 三条规则。"""
        rs = load_builtin_ruleset()
        names = [r.name for r in rs.rules]
        assert names == [
            "P0101-通用密码赋值",
            "P0102-敏感配置文件名",
            "P0103-邮件信息包含敏感词",
        ]

    def test_builtin_includes_password_filename_email_categories(self) -> None:
        """应覆盖密码赋值/敏感文件名/邮件敏感词三类。"""
        rs = load_builtin_ruleset()
        names = " ".join(r.name for r in rs.rules)
        assert "密码赋值" in names
        assert "配置文件名" in names
        assert "邮件" in names


class TestP0101PasswordAssignment:
    """通用密码赋值模式（P0101，content regex）。"""

    @pytest.mark.parametrize(
        "content",
        [
            "password=S3cr3t!",
            "passwd: abc123",
            "pwd = xxx",
            "PASSWORD=test",
            "db_password = hardcoded123",
            "user_passwd=secret",
        ],
    )
    def test_matches_password_assignments(self, content: str) -> None:
        """password/passwd/pwd 赋值语句应命中（大小写不敏感）。"""
        ctx = _make_context("config.txt", content)
        _name, _sev, matcher = _build_matcher_by_name("P0101-通用密码赋值")
        assert matcher.matches(ctx).matched

    def test_matches_password_in_yaml_like_content(self) -> None:
        """YAML 风格的 password: value 应命中。"""
        content = "development:\n  database_password: S3cr3t!\n"
        ctx = _make_context("app.yaml", content)
        _name, _sev, matcher = _build_matcher_by_name("P0101-通用密码赋值")
        assert matcher.matches(ctx).matched

    def test_does_not_match_plain_text(self) -> None:
        """普通文本不应命中。"""
        ctx = _make_context("readme.txt", "The quick brown fox jumps over the lazy dog.")
        _name, _sev, matcher = _build_matcher_by_name("P0101-通用密码赋值")
        assert not matcher.matches(ctx).matched

    def test_does_not_match_password_word_alone(self) -> None:
        """仅出现 password 单词而无赋值符不应命中。"""
        ctx = _make_context("doc.txt", "Please reset your password regularly.")
        _name, _sev, matcher = _build_matcher_by_name("P0101-通用密码赋值")
        assert not matcher.matches(ctx).matched


class TestP0102SensitiveFilename:
    """敏感配置文件名模式（P0102，filename regex）。"""

    @pytest.mark.parametrize(
        "filename",
        [
            ".env",
            ".env.local",
            ".env.production",
            "credentials",
            "secrets.yaml",
            "server.pem",
            "private.key",
            "cert.pfx",
            "app.keystore",
        ],
    )
    def test_matches_sensitive_filenames(self, filename: str) -> None:
        """敏感文件名应命中。"""
        ctx = _make_context(filename)
        _name, _sev, matcher = _build_matcher_by_name("P0102-敏感配置文件名")
        assert matcher.matches(ctx).matched

    @pytest.mark.parametrize(
        "filename",
        [
            "readme.txt",
            "app.py",
            "config.yaml",
            "environment_variable.md",
            "Makefile",
        ],
    )
    def test_does_not_match_normal_filenames(self, filename: str) -> None:
        """普通文件名不应命中。"""
        ctx = _make_context(filename)
        _name, _sev, matcher = _build_matcher_by_name("P0102-敏感配置文件名")
        assert not matcher.matches(ctx).matched

    def test_matches_case_insensitive(self) -> None:
        """文件名匹配应大小写不敏感（regex 含 (?i) 标志）。"""
        ctx = _make_context("SERVER.PEM")
        _name, _sev, matcher = _build_matcher_by_name("P0102-敏感配置文件名")
        assert matcher.matches(ctx).matched


class TestP0103EmailSensitiveWords:
    """邮件敏感词组合模式（P0103，AND: filename .eml + content 敏感词）。

    P0103 的 filename 正则为 ``^\\.eml$``，仅匹配文件名恰好为 ``.eml`` 的 dotfile
    （与 ``.env`` 同语义：隐藏邮件文件），不匹配 ``message.eml`` 等普通 .eml 文件。
    """

    @pytest.mark.parametrize("keyword", ["价格", "内部", "商业", "薪酬"])
    def test_matches_eml_with_sensitive_word(self, keyword: str) -> None:
        """文件名为 .eml 且内容含敏感词应命中。"""
        ctx = _make_context(".eml", f"邮件正文包含{keyword}信息")
        _name, _sev, matcher = _build_matcher_by_name("P0103-邮件信息包含敏感词")
        assert matcher.matches(ctx).matched

    def test_does_not_match_eml_without_sensitive_word(self) -> None:
        """文件名为 .eml 但内容不含敏感词不应命中。"""
        ctx = _make_context(".eml", "这是一封普通的工作邮件，没有敏感内容。")
        _name, _sev, matcher = _build_matcher_by_name("P0103-邮件信息包含敏感词")
        assert not matcher.matches(ctx).matched

    def test_does_not_match_txt_with_sensitive_word(self) -> None:
        """非 .eml 文件即使包含敏感词也不应命中（filename 子条件不满足）。"""
        ctx = _make_context("notes.txt", "价格 内部 商业 薪酬")
        _name, _sev, matcher = _build_matcher_by_name("P0103-邮件信息包含敏感词")
        assert not matcher.matches(ctx).matched

    def test_does_not_match_message_eml(self) -> None:
        """文件名为 message.eml 不应命中（P0103 仅匹配文件名恰好为 .eml 的 dotfile）。"""
        ctx = _make_context("message.eml", "价格 内部 商业 薪酬")
        _name, _sev, matcher = _build_matcher_by_name("P0103-邮件信息包含敏感词")
        assert not matcher.matches(ctx).matched

    def test_rule_uses_and_combination(self) -> None:
        """P0103 应使用 AndMatch 组合 filename + content 两个叶子条件。"""
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0103-邮件信息包含敏感词")
        assert isinstance(rule.match, AndMatch)
        assert len(rule.match.children) == 2
        filename_child, content_child = rule.match.children
        assert isinstance(filename_child, LeafMatch)
        assert filename_child.target == MatchTarget.FILENAME
        assert isinstance(content_child, LeafMatch)
        assert content_child.target == MatchTarget.CONTENT


class TestNoFalsePositivesOnNaturalText:
    """P0101 内容规则不应误匹配自然语言文本。"""

    def test_natural_text_no_matches(self) -> None:
        """自然语言文本不应触发 P0101 通用密码赋值规则。"""
        natural_samples = [
            "The quick brown fox jumps over the lazy dog.",
            "Hello world, this is a test message.",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
            "https://example.com/api/v1/users/12345/profile",
            "server.port=8080\nserver.host=localhost",
            "def hello():\n    print('Hello, World!')",
            "SELECT * FROM users WHERE active = true",
            '{\n  "name": "test",\n  "version": "1.0.0"\n}',
            "import os\nfrom pathlib import Path",
            "# This is a comment\n# Another comment\n",
        ]
        _name, _sev, matcher = _build_matcher_by_name("P0101-通用密码赋值")
        for sample in natural_samples:
            ctx = _make_context("sample.txt", sample)
            result = matcher.matches(ctx)
            assert not result.matched, f"P0101 误匹配自然文本: {sample[:50]!r}"
