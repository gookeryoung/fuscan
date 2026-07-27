"""内置规则模板库单元测试（iter-122）。

覆盖 ``fuscan.rules.templates`` 模块：

- ``get_template_names``：返回所有模板名（字母序）
- ``get_template_descriptions``：模板名 → 中文描述映射
- ``load_template``：按名称加载模板，返回 RuleSet

模板内容验证：

- ``aws_keys``：AWS 访问密钥 ID + 秘密密钥
- ``azure_keys``：Azure 连接字符串 + 账户密钥
- ``gcp_keys``：GCP 服务账号私钥 + API 密钥
- ``privacy_data``：身份证号/手机号/邮箱
- ``common_credentials``：密码/API 密钥/Token 赋值语句
"""

from __future__ import annotations

import pytest

from fuscan.rules import (
    RuleSet,
    Severity,
    get_template_descriptions,
    get_template_names,
    load_template,
)
from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget


class TestTemplateListing:
    def test_get_template_names_returns_sorted_list(self) -> None:
        names = get_template_names()
        assert names == sorted(names)
        # 至少 5 个模板覆盖常见凭证场景（验收标准 #2）
        assert len(names) >= 5

    def test_template_names_contains_expected(self) -> None:
        names = get_template_names()
        assert "aws_keys" in names
        assert "azure_keys" in names
        assert "gcp_keys" in names
        assert "privacy_data" in names
        assert "common_credentials" in names

    def test_get_template_descriptions_returns_all(self) -> None:
        descriptions = get_template_descriptions()
        names = get_template_names()
        assert set(descriptions.keys()) == set(names)
        # 每个描述非空
        for name, desc in descriptions.items():
            assert isinstance(desc, str)
            assert len(desc) > 0, f"模板 {name!r} 描述为空"

    def test_descriptions_contains_chinese(self) -> None:
        """描述应为中文文本。"""
        descriptions = get_template_descriptions()
        assert "AWS" in descriptions["aws_keys"]
        assert "Azure" in descriptions["azure_keys"]
        assert "GCP" in descriptions["gcp_keys"]
        assert "隐私" in descriptions["privacy_data"] or "身份证" in descriptions["privacy_data"]
        assert "凭证" in descriptions["common_credentials"] or "密码" in descriptions["common_credentials"]


class TestLoadTemplate:
    def test_load_aws_keys_template(self) -> None:
        rs = load_template("aws_keys")
        assert isinstance(rs, RuleSet)
        assert rs.version == "1.0"
        assert len(rs.rules) == 2

        rule_names = [r.name for r in rs.rules]
        assert "AWS 访问密钥 ID" in rule_names
        assert "AWS 秘密密钥" in rule_names

        # 所有 AWS 规则严重等级为 critical
        for rule in rs.rules:
            assert rule.severity == Severity.CRITICAL
            # 内容匹配（regex 模式）
            assert isinstance(rule.match, LeafMatch)
            assert rule.match.target == MatchTarget.CONTENT
            assert rule.match.mode == MatchMode.REGEX

    def test_load_aws_key_pattern_detects_akia(self) -> None:
        """AWS 模板应能匹配典型的 AKIA 开头密钥。"""
        import re

        rs = load_template("aws_keys")
        akia_rule = next(r for r in rs.rules if r.name == "AWS 访问密钥 ID")
        assert isinstance(akia_rule.match, LeafMatch)
        pattern = akia_rule.match.pattern
        # 典型 AWS Key ID：AKIA + 16 位大写字母数字
        assert re.search(pattern, "AKIAIOSFODNN7EXAMPLE")
        assert re.search(pattern, "aws_key = AKIAIOSFODNN7EXAMPLE")

    def test_load_azure_keys_template(self) -> None:
        rs = load_template("azure_keys")
        assert rs.version == "1.0"
        assert len(rs.rules) == 2

        rule_names = [r.name for r in rs.rules]
        assert "Azure 连接字符串" in rule_names
        assert "Azure 账户密钥" in rule_names

        for rule in rs.rules:
            assert rule.severity == Severity.CRITICAL

    def test_load_gcp_keys_template(self) -> None:
        rs = load_template("gcp_keys")
        assert rs.version == "1.0"
        assert len(rs.rules) == 2

        rule_names = [r.name for r in rs.rules]
        assert "GCP 服务账号私钥" in rule_names
        assert "GCP API 密钥" in rule_names

        for rule in rs.rules:
            assert rule.severity == Severity.CRITICAL

    def test_load_privacy_data_template(self) -> None:
        rs = load_template("privacy_data")
        assert rs.version == "1.0"
        assert len(rs.rules) == 3

        rule_names = [r.name for r in rs.rules]
        assert "身份证号" in rule_names
        assert "手机号" in rule_names
        assert "邮箱地址" in rule_names

        # 身份证/手机号为 warning，邮箱为 info
        id_rule = next(r for r in rs.rules if r.name == "身份证号")
        assert id_rule.severity == Severity.WARNING
        phone_rule = next(r for r in rs.rules if r.name == "手机号")
        assert phone_rule.severity == Severity.WARNING
        email_rule = next(r for r in rs.rules if r.name == "邮箱地址")
        assert email_rule.severity == Severity.INFO

    def test_load_common_credentials_template(self) -> None:
        rs = load_template("common_credentials")
        assert rs.version == "1.0"
        assert len(rs.rules) == 3

        rule_names = [r.name for r in rs.rules]
        assert "密码赋值" in rule_names
        assert "API 密钥赋值" in rule_names
        assert "Token 赋值" in rule_names

        for rule in rs.rules:
            assert rule.severity == Severity.WARNING

    def test_load_template_unknown_name_raises(self) -> None:
        with pytest.raises(KeyError, match="不存在"):
            load_template("nonexistent_template")

    def test_load_template_all_names_loadable(self) -> None:
        """所有 get_template_names 返回的模板名都能成功加载。"""
        for name in get_template_names():
            rs = load_template(name)
            assert isinstance(rs, RuleSet)
            assert rs.version == "1.0"
            assert len(rs.rules) > 0, f"模板 {name!r} 规则数为 0"

    def test_template_rules_have_descriptions(self) -> None:
        """模板规则应包含描述字段，便于用户理解。"""
        for name in get_template_names():
            rs = load_template(name)
            for rule in rs.rules:
                assert rule.description, f"模板 {name!r} 中规则 {rule.name!r} 描述为空"
                # 叶子匹配也应有描述
                if isinstance(rule.match, LeafMatch):
                    assert rule.match.description, f"模板 {name!r} 中规则 {rule.name!r} 的匹配描述为空"

    def test_template_rules_are_content_regex(self) -> None:
        """所有模板规则均为 content + regex 模式（凭证/隐私数据扫描的典型场景）。"""
        for name in get_template_names():
            rs = load_template(name)
            for rule in rs.rules:
                assert isinstance(rule.match, LeafMatch), f"模板 {name!r} 中规则 {rule.name!r} 应为叶子匹配"
                assert rule.match.target == MatchTarget.CONTENT
                assert rule.match.mode == MatchMode.REGEX


class TestTemplatePatternValidation:
    """模板正则模式的有效性测试（避免无效正则导致扫描器异常）。"""

    def test_all_template_patterns_are_valid_regex(self) -> None:
        """所有模板的正则模式应可被 re.compile 编译。"""
        import re

        for name in get_template_names():
            rs = load_template(name)
            for rule in rs.rules:
                assert isinstance(rule.match, LeafMatch)
                # 编译验证（无效正则会抛 re.error）
                re.compile(rule.match.pattern)

    def test_aws_template_detects_typical_key(self) -> None:
        """AWS 模板能检测典型密钥字符串。"""
        import re

        rs = load_template("aws_keys")
        akia_rule = next(r for r in rs.rules if r.name == "AWS 访问密钥 ID")
        assert isinstance(akia_rule.match, LeafMatch)
        # 模拟日志中的 AWS Key ID
        text = "2024-01-01 INFO Using AWS access key AKIAIOSFODNN7EXAMPLE for production"
        assert re.search(akia_rule.match.pattern, text)

    def test_privacy_template_detects_phone(self) -> None:
        """隐私数据模板能检测中国手机号。"""
        import re

        rs = load_template("privacy_data")
        phone_rule = next(r for r in rs.rules if r.name == "手机号")
        assert isinstance(phone_rule.match, LeafMatch)
        text = "联系方式：13912345678，请勿泄漏"
        assert re.search(phone_rule.match.pattern, text)

    def test_privacy_template_detects_id_number(self) -> None:
        """隐私数据模板能检测 18 位身份证号。"""
        import re

        rs = load_template("privacy_data")
        id_rule = next(r for r in rs.rules if r.name == "身份证号")
        assert isinstance(id_rule.match, LeafMatch)
        text = "身份证号：110101199003078888"
        assert re.search(id_rule.match.pattern, text)

    def test_common_credentials_detects_password_assignment(self) -> None:
        """常见凭证模板能检测密码赋值语句。"""
        import re

        rs = load_template("common_credentials")
        pwd_rule = next(r for r in rs.rules if r.name == "密码赋值")
        assert isinstance(pwd_rule.match, LeafMatch)
        text = "password = 'admin12345'"
        assert re.search(pwd_rule.match.pattern, text)
