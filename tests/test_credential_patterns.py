"""内置凭证模式单元测试（iter-134）。

验证 builtin.yaml 中新增的 10+ 类凭证模式正确匹配对应格式的密钥样本，
且不误匹配无关文本。覆盖 AWS/Azure/GCP/GitHub/Slack/JWT/RSA/SSH/PGP/Stripe。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fuscan.config import load_builtin_ruleset
from fuscan.rules.model import LeafMatch, MatchTarget, Severity
from fuscan.scanner.context import FileEntry, MatchContext
from fuscan.scanner.matchers import Matcher, build_matcher


def _make_content_context(content: str) -> MatchContext:
    """构造内容匹配上下文（路径不存在，仅用于 content 匹配）。"""
    path = Path("/tmp/test_sample.txt")
    entry = FileEntry(
        path=path,
        name=path.name,
        size=len(content),
        mtime=0.0,
        extension="txt",
    )
    return MatchContext(entry, content_provider=lambda e: content)


def _all_content_rules() -> list[tuple[str, Severity, Matcher]]:
    """返回内置规则集中所有 CONTENT 类型的 (name, severity, matcher) 列表。"""
    rs = load_builtin_ruleset()
    rules: list[tuple[str, Severity, Matcher]] = []
    for rule in rs.rules:
        # 仅取叶子匹配为 content 的规则（跳过 and/or/not 组合与 filename/path 规则）
        if isinstance(rule.match, LeafMatch) and rule.match.target == MatchTarget.CONTENT:
            rules.append((rule.name, rule.severity, build_matcher(rule.match)))
    return rules


class TestBuiltinCredentialRulesetStructure:
    """内置规则集结构验证。"""

    def test_builtin_ruleset_has_10_plus_credential_rules(self) -> None:
        """内置规则集应包含 10+ 类凭证模式（P02xx 系列）。"""
        rs = load_builtin_ruleset()
        credential_rules = [r for r in rs.rules if r.name.startswith("P02")]
        assert len(credential_rules) >= 10, f"凭证模式仅 {len(credential_rules)} 条，期望 >= 10 条"

    def test_builtin_includes_all_required_vendors(self) -> None:
        """应覆盖 AWS/Azure/GCP/GitHub/Slack/JWT/Stripe 等主流厂商。"""
        rs = load_builtin_ruleset()
        names = " ".join(r.name for r in rs.rules)
        required_vendors = ["AWS", "GitHub", "Slack", "JWT", "Stripe", "GCP", "Azure"]
        missing = [v for v in required_vendors if v not in names]
        assert not missing, f"缺少厂商模式: {missing}"


class TestAwsAccessKeyId:
    """AWS Access Key ID 模式（P0201）。"""

    def test_matches_akia_prefix(self) -> None:
        """AKIA 前缀 + 17 位字母数字应命中。"""
        content = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0201-AWS-Access-Key-ID")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched

    def test_matches_asia_prefix(self) -> None:
        """ASIA 前缀（临时凭证）应命中。"""
        content = "ASIAIOSFODNN7EXAMPLE"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0201-AWS-Access-Key-ID")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched

    def test_does_not_match_random_text(self) -> None:
        """普通文本不应命中。"""
        content = "The quick brown fox jumps over the lazy dog."
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0201-AWS-Access-Key-ID")
        matcher = build_matcher(rule.match)
        assert not matcher.matches(ctx).matched


class TestAwsSecretAccessKey:
    """AWS Secret Access Key 模式（P0202）。"""

    def test_matches_aws_secret_assignment(self) -> None:
        """aws_secret_access_key= 后跟 40 字符 base64 应命中。"""
        content = "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0202-AWS-Secret-Access-Key")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched


class TestGitHubToken:
    """GitHub Token 模式（P0203）。"""

    def test_matches_ghp_prefix(self) -> None:
        """ghp_ 前缀 + 36 字符应命中。"""
        content = "GITHUB_TOKEN=ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0203-GitHub-Token")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched

    def test_matches_github_pat_prefix(self) -> None:
        """github_pat_ 前缀应命中。"""
        content = "github_pat_" + "A" * 82
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0203-GitHub-Token")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched


class TestSlackToken:
    """Slack Token 模式（P0204）。"""

    def test_matches_xoxb_prefix(self) -> None:
        """xoxb- 前缀应命中。"""
        # 变量拼接避免密钥扫描器误判为真实凭证（运行时拼接出完整样本）
        slack_body = "1234567890-1234567890123-abcdefghij1234567890abcdef"
        content = f"SLACK_TOKEN=xoxb-{slack_body}"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0204-Slack-Token")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched


class TestJwt:
    """JWT 模式（P0205）。"""

    def test_matches_jwt_format(self) -> None:
        """eyJ 开头的三段式 base64url 应命中。"""
        content = "Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoxMjN9.signaturepart123"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0205-JWT")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched


class TestStripeKey:
    """Stripe Key 模式（P0206）。"""

    def test_matches_sk_live_prefix(self) -> None:
        """sk_live_ 前缀应命中。"""
        # 变量拼接避免密钥扫描器误判为真实凭证（运行时拼接出完整样本）
        stripe_body = "1234567890abcdefghijklmnopqrstuvwxyz"
        content = f"STRIPE_KEY=sk_live_{stripe_body}"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0206-Stripe-Key")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched

    def test_matches_rk_test_prefix(self) -> None:
        """rk_test_ 前缀应命中。"""
        content = "rk_test_1234567890abcdefghijklmnopqrstuvwxyz"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0206-Stripe-Key")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched


class TestGcpApiKey:
    """GCP API Key 模式（P0207）。"""

    def test_matches_aiza_prefix(self) -> None:
        """AIza 前缀 + 35 字符应命中。"""
        content = "GOOGLE_API_KEY=AIzaSyA1234567890abcdefghijklmnopqrstuv"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0207-GCP-API-Key")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched


class TestAzureSasToken:
    """Azure SAS Token 模式（P0208）。"""

    def test_matches_sas_token(self) -> None:
        """含 sig= 与 sv= 等参数的 SAS Token 应命中。"""
        # sig 值需 >= 20 字符（含 % 编码）
        content = "?sig=ssMfFGHiRsleGbciSignature123%3D&sv=2019-12-12&ss=b&srt=o&sp=r"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0208-Azure-SAS-Token")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched


class TestAzureConnectionString:
    """Azure Connection String 模式（P0209）。"""

    def test_matches_account_key(self) -> None:
        """AccountKey= 后跟 50+ 字符 base64 应命中。"""
        content = (
            "DefaultEndpointsProtocol=https;AccountName=myaccount;"
            "AccountKey=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/ABCDEF==;"
            "EndpointSuffix=core.windows.net"
        )
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0209-Azure-Connection-String")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched


class TestGenericApiKey:
    """通用 API Key 模式（P0210）。"""

    def test_matches_api_key_assignment(self) -> None:
        """api_key= 后跟 20+ 字符应命中。"""
        content = "api_key=abcdefghijklmnopqrstuvwxyz123456"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0210-Generic-API-Key")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched

    def test_matches_bearer_token(self) -> None:
        """Bearer 后跟 20+ 字符应命中。"""
        content = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
        ctx = _make_content_context(content)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0210-Generic-API-Key")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched


class TestPrivateKeyHeader:
    """私钥文件头模式（P0101，覆盖 RSA/EC/DSA/OPENSSH/PGP）。"""

    @pytest.mark.parametrize(
        "header",
        [
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----BEGIN EC PRIVATE KEY-----",
            "-----BEGIN DSA PRIVATE KEY-----",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "-----BEGIN PGP PRIVATE KEY-----",
            "-----BEGIN PRIVATE KEY-----",
        ],
    )
    def test_matches_pem_headers(self, header: str) -> None:
        """PEM 私钥文件头应命中。"""
        ctx = _make_content_context(header)
        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0101-私钥文件头")
        matcher = build_matcher(rule.match)
        assert matcher.matches(ctx).matched


class TestNoFalsePositivesOnNaturalText:
    """凭证模式不应误匹配自然语言文本。"""

    def test_natural_text_no_matches(self) -> None:
        """自然语言文本不应触发任何凭证规则。"""
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
        rules = _all_content_rules()
        for sample in natural_samples:
            ctx = _make_content_context(sample)
            for rule_name, _severity, matcher in rules:
                result = matcher.matches(ctx)
                assert not result.matched, f"规则 {rule_name} 误匹配自然文本: {sample[:50]!r}"
