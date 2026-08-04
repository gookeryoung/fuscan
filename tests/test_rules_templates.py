"""内置规则模板库单元测试（含模板继承）。

覆盖 ``fuscan.rules.templates`` 模块：

- ``get_template_names``：返回所有模板名（字母序）
- ``get_template_descriptions``：模板名 → 中文描述映射
- ``get_template_metadata``：模板完整元信息（含继承链与规则数）
- ``load_template``：按名称加载模板，解析继承链后返回 RuleSet

测试分组：

- :class:`TestTemplateListing`：模板列表与描述基础校验
- :class:`TestLoadLeafTemplate`：叶子模板加载与内容验证
- :class:`TestLoadCompositeTemplate`：组合模板继承解析验证
- :class:`TestInheritanceSemantics`：继承覆盖/合并语义验证
- :class:`TestCycleDetection`：循环继承检测
- :class:`TestTemplateMetadata`：元信息 API 验证
- :class:`TestTemplatePatternValidation`：正则模式有效性验证
"""

from __future__ import annotations

import re

import pytest

from fuscan.rules import (
    RuleSet,
    Severity,
    get_template_descriptions,
    get_template_metadata,
    get_template_names,
    load_template,
)
from fuscan.rules.errors import RuleParseError
from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget


class TestTemplateListing:
    def test_get_template_names_returns_sorted_list(self) -> None:
        names = get_template_names()
        assert names == sorted(names)
        # 至少 14 个模板：6 叶子 + 5 原有 + 3 组合
        assert len(names) >= 14

    def test_template_names_contains_expected(self) -> None:
        names = get_template_names()
        # 原有 5 个
        assert "aws_keys" in names
        assert "azure_keys" in names
        assert "gcp_keys" in names
        assert "privacy_data" in names
        assert "common_credentials" in names
        # 新增 6 个叶子模板
        assert "github_tokens" in names
        assert "slack_tokens" in names
        assert "stripe_keys" in names
        assert "jwt_tokens" in names
        assert "private_keys" in names
        assert "database_connection_strings" in names
        # 新增 3 个组合模板
        assert "cloud_keys" in names
        assert "saas_tokens" in names
        assert "compliance_scan" in names

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


class TestLoadLeafTemplate:
    """叶子模板（无 extends）加载验证。"""

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

    def test_load_github_tokens_template(self) -> None:
        rs = load_template("github_tokens")
        assert rs.version == "1.0"
        assert len(rs.rules) == 3

        rule_names = [r.name for r in rs.rules]
        assert "GitHub PAT 令牌" in rule_names
        assert "GitHub OAuth 令牌" in rule_names
        assert "GitHub App 令牌" in rule_names

        for rule in rs.rules:
            assert rule.severity == Severity.CRITICAL
            assert isinstance(rule.match, LeafMatch)
            assert rule.match.target == MatchTarget.CONTENT
            assert rule.match.mode == MatchMode.REGEX

    def test_load_slack_tokens_template(self) -> None:
        rs = load_template("slack_tokens")
        assert rs.version == "1.0"
        assert len(rs.rules) == 3

        rule_names = [r.name for r in rs.rules]
        assert "Slack Bot 令牌" in rule_names
        assert "Slack User 令牌" in rule_names
        assert "Slack Webhook URL" in rule_names

        # Bot/User 令牌为 critical，Webhook 为 warning
        bot_rule = next(r for r in rs.rules if r.name == "Slack Bot 令牌")
        assert bot_rule.severity == Severity.CRITICAL
        webhook_rule = next(r for r in rs.rules if r.name == "Slack Webhook URL")
        assert webhook_rule.severity == Severity.WARNING

    def test_load_stripe_keys_template(self) -> None:
        rs = load_template("stripe_keys")
        assert rs.version == "1.0"
        assert len(rs.rules) == 3

        rule_names = [r.name for r in rs.rules]
        assert "Stripe Secret 密钥" in rule_names
        assert "Stripe Publishable 密钥" in rule_names
        assert "Stripe Restricted 密钥" in rule_names

        # Secret/Restricted 为 critical，Publishable 为 warning
        secret_rule = next(r for r in rs.rules if r.name == "Stripe Secret 密钥")
        assert secret_rule.severity == Severity.CRITICAL
        pub_rule = next(r for r in rs.rules if r.name == "Stripe Publishable 密钥")
        assert pub_rule.severity == Severity.WARNING

    def test_load_jwt_tokens_template(self) -> None:
        rs = load_template("jwt_tokens")
        assert rs.version == "1.0"
        assert len(rs.rules) == 1
        assert rs.rules[0].name == "JWT 令牌"
        assert rs.rules[0].severity == Severity.CRITICAL

    def test_load_private_keys_template(self) -> None:
        rs = load_template("private_keys")
        assert rs.version == "1.0"
        assert len(rs.rules) == 2

        rule_names = [r.name for r in rs.rules]
        assert "PEM 私钥文件头" in rule_names
        assert "PKCS#8 私钥文件头" in rule_names

        for rule in rs.rules:
            assert rule.severity == Severity.CRITICAL

    def test_load_database_connection_strings_template(self) -> None:
        rs = load_template("database_connection_strings")
        assert rs.version == "1.0"
        assert len(rs.rules) == 4

        rule_names = [r.name for r in rs.rules]
        assert "MySQL 连接字符串" in rule_names
        assert "PostgreSQL 连接字符串" in rule_names
        assert "MongoDB 连接字符串" in rule_names
        assert "Redis 连接字符串" in rule_names

        # MySQL/PostgreSQL/MongoDB 为 critical，Redis 为 warning
        redis_rule = next(r for r in rs.rules if r.name == "Redis 连接字符串")
        assert redis_rule.severity == Severity.WARNING
        mysql_rule = next(r for r in rs.rules if r.name == "MySQL 连接字符串")
        assert mysql_rule.severity == Severity.CRITICAL

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


class TestLoadCompositeTemplate:
    """组合模板（通过 extends 继承）加载验证。"""

    def test_cloud_keys_inherits_three_cloud_providers(self) -> None:
        """cloud_keys 应包含 AWS + Azure + GCP 全部 6 条规则。"""
        rs = load_template("cloud_keys")
        assert isinstance(rs, RuleSet)
        assert rs.version == "1.0"
        # AWS 2 + Azure 2 + GCP 2 = 6 条（无同名冲突）
        assert len(rs.rules) == 6

        rule_names = {r.name for r in rs.rules}
        # AWS
        assert "AWS 访问密钥 ID" in rule_names
        assert "AWS 秘密密钥" in rule_names
        # Azure
        assert "Azure 连接字符串" in rule_names
        assert "Azure 账户密钥" in rule_names
        # GCP
        assert "GCP 服务账号私钥" in rule_names
        assert "GCP API 密钥" in rule_names

    def test_saas_tokens_inherits_four_saas_templates(self) -> None:
        """saas_tokens 应包含 GitHub + Slack + Stripe + JWT 全部规则。"""
        rs = load_template("saas_tokens")
        assert rs.version == "1.0"
        # GitHub 3 + Slack 3 + Stripe 3 + JWT 1 = 10 条
        assert len(rs.rules) == 10

        rule_names = {r.name for r in rs.rules}
        # GitHub
        assert "GitHub PAT 令牌" in rule_names
        assert "GitHub OAuth 令牌" in rule_names
        assert "GitHub App 令牌" in rule_names
        # Slack
        assert "Slack Bot 令牌" in rule_names
        assert "Slack User 令牌" in rule_names
        assert "Slack Webhook URL" in rule_names
        # Stripe
        assert "Stripe Secret 密钥" in rule_names
        assert "Stripe Publishable 密钥" in rule_names
        assert "Stripe Restricted 密钥" in rule_names
        # JWT
        assert "JWT 令牌" in rule_names

    def test_compliance_scan_inherits_all_plus_own_rule(self) -> None:
        """compliance_scan 应包含所有叶子模板规则 + 自身「敏感配置文件名」规则。"""
        rs = load_template("compliance_scan")
        assert rs.version == "1.0"
        # cloud_keys(6) + saas_tokens(10) + privacy_data(3) + common_credentials(3)
        # + private_keys(2) + database_connection_strings(4) + 自身 1 = 29
        assert len(rs.rules) == 29

        rule_names = {r.name for r in rs.rules}
        # 自身规则
        assert "敏感配置文件名" in rule_names
        # 抽样验证各叶子模板规则
        assert "AWS 访问密钥 ID" in rule_names  # cloud_keys
        assert "GitHub PAT 令牌" in rule_names  # saas_tokens
        assert "身份证号" in rule_names  # privacy_data
        assert "密码赋值" in rule_names  # common_credentials
        assert "PEM 私钥文件头" in rule_names  # private_keys
        assert "MySQL 连接字符串" in rule_names  # database_connection_strings

    def test_compliance_scan_sensitive_filename_is_filename_match(self) -> None:
        """compliance_scan 自身规则应为 filename 匹配（非 content）。"""
        rs = load_template("compliance_scan")
        rule = next(r for r in rs.rules if r.name == "敏感配置文件名")
        assert isinstance(rule.match, LeafMatch)
        assert rule.match.target == MatchTarget.FILENAME
        assert rule.match.mode == MatchMode.REGEX
        assert rule.severity == Severity.INFO


class TestInheritanceSemantics:
    """继承合并语义验证。"""

    def test_composite_rule_count_equals_sum_of_parents(self) -> None:
        """组合模板规则数 = 各父模板规则数之和（无同名冲突时）。"""
        aws = load_template("aws_keys")
        azure = load_template("azure_keys")
        gcp = load_template("gcp_keys")
        cloud = load_template("cloud_keys")

        assert len(cloud.rules) == len(aws.rules) + len(azure.rules) + len(gcp.rules)

    def test_composite_no_duplicate_rule_names(self) -> None:
        """组合模板解析后不应有重复规则名。"""
        for name in ["cloud_keys", "saas_tokens", "compliance_scan"]:
            rs = load_template(name)
            rule_names = [r.name for r in rs.rules]
            assert len(rule_names) == len(set(rule_names)), f"模板 {name!r} 存在重复规则名"

    def test_child_rule_overrides_parent_same_name(self) -> None:
        """子模板同名规则应覆盖父模板。

        通过 compliance_scan 验证：它自身定义了「敏感配置文件名」规则，
        若某父模板也有同名规则，子模板版本应胜出。这里通过构造性验证——
        compliance_scan 的「敏感配置文件名」规则 severity=info 应保留。
        """
        rs = load_template("compliance_scan")
        rule = next(r for r in rs.rules if r.name == "敏感配置文件名")
        # 子模板定义的 severity=info 应保留（未被父模板覆盖）
        assert rule.severity == Severity.INFO

    def test_transitive_inheritance_resolved(self) -> None:
        """传递继承：compliance_scan extends cloud_keys，cloud_keys extends aws_keys。

        compliance_scan 应包含 aws_keys 的规则（传递继承展开）。
        """
        rs = load_template("compliance_scan")
        aws = load_template("aws_keys")
        aws_names = {r.name for r in aws.rules}
        compliance_names = {r.name for r in rs.rules}
        # AWS 的所有规则都应出现在 compliance_scan 中
        assert aws_names.issubset(compliance_names), "传递继承失败：AWS 规则未出现在 compliance_scan 中"

    def test_merge_order_left_to_right(self) -> None:
        """父模板按 extends 列表顺序合并（左→右，后者覆盖前者同名规则）。

        由于现有模板无同名规则冲突，这里验证规则总数符合预期顺序合并结果。
        """
        cloud = load_template("cloud_keys")
        # 验证所有三个父模板的规则都在结果中
        names = {r.name for r in cloud.rules}
        assert "AWS 访问密钥 ID" in names  # aws_keys（第一个）
        assert "Azure 连接字符串" in names  # azure_keys（第二个）
        assert "GCP 服务账号私钥" in names  # gcp_keys（第三个）

    def test_leaf_template_no_inheritance(self) -> None:
        """叶子模板（extends 为空）直接返回自身规则，不发生合并。"""
        aws = load_template("aws_keys")
        # 叶子模板规则数应等于自身 data 中定义的规则数
        assert len(aws.rules) == 2

    def test_composite_inherits_ignore_dirs_union(self) -> None:
        """组合模板的 ignore_dirs 取并集。

        构造性验证：在父模板 data 中添加 ignore_dirs 后子模板应继承。
        由于内置模板未定义 ignore_dirs，这里验证默认空并集不报错。
        """
        rs = load_template("cloud_keys")
        # 内置模板均未定义 ignore_dirs，合并后仍为空
        assert rs.ignore_dirs == ()

    def test_composite_inherits_whitelist_union(self) -> None:
        """组合模板的 whitelist 取并集。"""
        rs = load_template("compliance_scan")
        # 内置模板均未定义 whitelist，合并后仍为空
        assert rs.whitelist == ()


class TestCycleDetection:
    """循环继承检测。"""

    def test_cycle_detection_raises(self) -> None:
        """构造 A extends B、B extends A 的循环，应抛 RuleParseError。"""
        from fuscan.rules import templates as templates_mod

        # 备份原始 _TEMPLATES
        original = templates_mod._TEMPLATES.copy()
        try:
            # 构造循环：cycle_a extends [cycle_b], cycle_b extends [cycle_a]
            # rules 键省略——parse_ruleset 默认为空列表
            templates_mod._TEMPLATES["cycle_a"] = {
                "name": "循环 A",
                "description": "测试循环 A",
                "extends": ["cycle_b"],
                "data": {"version": "1.0"},
            }
            templates_mod._TEMPLATES["cycle_b"] = {
                "name": "循环 B",
                "description": "测试循环 B",
                "extends": ["cycle_a"],
                "data": {"version": "1.0"},
            }
            with pytest.raises(RuleParseError, match="循环继承"):
                templates_mod.load_template("cycle_a")
        finally:
            templates_mod._TEMPLATES.clear()
            templates_mod._TEMPLATES.update(original)

    def test_self_cycle_detection_raises(self) -> None:
        """构造 A extends A 的自循环，应抛 RuleParseError。"""
        from fuscan.rules import templates as templates_mod

        original = templates_mod._TEMPLATES.copy()
        try:
            templates_mod._TEMPLATES["self_cycle"] = {
                "name": "自循环",
                "description": "测试自循环",
                "extends": ["self_cycle"],
                "data": {"version": "1.0"},
            }
            with pytest.raises(RuleParseError, match="循环继承"):
                templates_mod.load_template("self_cycle")
        finally:
            templates_mod._TEMPLATES.clear()
            templates_mod._TEMPLATES.update(original)

    def test_missing_parent_raises(self) -> None:
        """引用不存在的父模板应抛 RuleParseError。"""
        from fuscan.rules import templates as templates_mod

        original = templates_mod._TEMPLATES.copy()
        try:
            templates_mod._TEMPLATES["bad_parent_ref"] = {
                "name": "引用不存在父模板",
                "description": "测试引用不存在的父模板",
                "extends": ["nonexistent_parent"],
                "data": {"version": "1.0"},
            }
            with pytest.raises(RuleParseError, match="不存在的父模板"):
                templates_mod.load_template("bad_parent_ref")
        finally:
            templates_mod._TEMPLATES.clear()
            templates_mod._TEMPLATES.update(original)

    def test_no_cycle_for_normal_templates(self) -> None:
        """正常模板不应触发循环检测。"""
        # 所有内置模板都应能正常加载（无循环）
        for name in get_template_names():
            rs = load_template(name)
            assert isinstance(rs, RuleSet)


class TestTemplateMetadata:
    """get_template_metadata API 验证。"""

    def test_metadata_for_leaf_template(self) -> None:
        """叶子模板元信息：extends 为空，isComposite 为 False。"""
        meta = get_template_metadata("aws_keys")
        assert meta["name"] == "AWS 密钥检测"
        assert meta["description"]
        assert meta["extends"] == []
        assert meta["isComposite"] is False
        assert meta["ruleCount"] == 2

    def test_metadata_for_composite_template(self) -> None:
        """组合模板元信息：extends 非空，isComposite 为 True。"""
        meta = get_template_metadata("cloud_keys")
        assert meta["name"] == "云平台密钥集合"
        assert meta["description"]
        assert meta["extends"] == ["aws_keys", "azure_keys", "gcp_keys"]
        assert meta["isComposite"] is True
        assert meta["ruleCount"] == 6

    def test_metadata_for_transitive_composite(self) -> None:
        """传递继承的组合模板元信息。"""
        meta = get_template_metadata("compliance_scan")
        assert meta["isComposite"] is True
        # isinstance 收窄 object → list，便于 in 成员判断
        extends = meta["extends"]
        assert isinstance(extends, list)
        assert "cloud_keys" in extends
        assert "saas_tokens" in extends
        # ruleCount 应为 29（所有叶子 + 自身 1）
        assert meta["ruleCount"] == 29

    def test_metadata_rule_count_matches_load_template(self) -> None:
        """元信息中的 ruleCount 应与 load_template 返回的规则数一致。"""
        for name in get_template_names():
            meta = get_template_metadata(name)
            rs = load_template(name)
            assert meta["ruleCount"] == len(rs.rules), f"模板 {name!r} ruleCount 不一致"

    def test_metadata_all_templates_have_metadata(self) -> None:
        """所有模板都能获取元信息。"""
        for name in get_template_names():
            meta = get_template_metadata(name)
            assert "name" in meta
            assert "description" in meta
            assert "extends" in meta
            assert "isComposite" in meta
            assert "ruleCount" in meta

    def test_metadata_unknown_name_raises(self) -> None:
        with pytest.raises(KeyError, match="不存在"):
            get_template_metadata("nonexistent_template")


class TestTemplatePatternValidation:
    """模板正则模式的有效性测试（避免无效正则导致扫描器异常）。"""

    def test_all_template_patterns_are_valid_regex(self) -> None:
        """所有模板的正则模式应可被 re.compile 编译。"""
        for name in get_template_names():
            rs = load_template(name)
            for rule in rs.rules:
                if isinstance(rule.match, LeafMatch) and rule.match.mode == MatchMode.REGEX:
                    # 编译验证（无效正则会抛 re.error）
                    re.compile(rule.match.pattern)

    def test_aws_template_detects_typical_key(self) -> None:
        """AWS 模板能检测典型密钥字符串。"""
        rs = load_template("aws_keys")
        akia_rule = next(r for r in rs.rules if r.name == "AWS 访问密钥 ID")
        assert isinstance(akia_rule.match, LeafMatch)
        # 模拟日志中的 AWS Key ID
        text = "2024-01-01 INFO Using AWS access key AKIAIOSFODNN7EXAMPLE for production"
        assert re.search(akia_rule.match.pattern, text)

    def test_privacy_template_detects_phone(self) -> None:
        """隐私数据模板能检测中国手机号。"""
        rs = load_template("privacy_data")
        phone_rule = next(r for r in rs.rules if r.name == "手机号")
        assert isinstance(phone_rule.match, LeafMatch)
        text = "联系方式：13912345678，请勿泄漏"
        assert re.search(phone_rule.match.pattern, text)

    def test_privacy_template_detects_id_number(self) -> None:
        """隐私数据模板能检测 18 位身份证号。"""
        rs = load_template("privacy_data")
        id_rule = next(r for r in rs.rules if r.name == "身份证号")
        assert isinstance(id_rule.match, LeafMatch)
        text = "身份证号：110101199003078888"
        assert re.search(id_rule.match.pattern, text)

    def test_common_credentials_detects_password_assignment(self) -> None:
        """常见凭证模板能检测密码赋值语句。"""
        rs = load_template("common_credentials")
        pwd_rule = next(r for r in rs.rules if r.name == "密码赋值")
        assert isinstance(pwd_rule.match, LeafMatch)
        text = "password = 'admin12345'"
        assert re.search(pwd_rule.match.pattern, text)

    def test_github_template_detects_pat(self) -> None:
        """GitHub 模板能检测 ghp_ 开头的 PAT。"""
        rs = load_template("github_tokens")
        pat_rule = next(r for r in rs.rules if r.name == "GitHub PAT 令牌")
        assert isinstance(pat_rule.match, LeafMatch)
        # ghp_ + 36 位字母数字（10 数字 + 26 字母 = 36）
        text = "GITHUB_TOKEN=ghp_0123456789abcdefghijklmnopqrstuvwxyz"
        assert re.search(pat_rule.match.pattern, text)

    def test_slack_template_detects_bot_token(self) -> None:
        """Slack 模板能检测 xoxb- 开头的 Bot 令牌。"""
        rs = load_template("slack_tokens")
        bot_rule = next(r for r in rs.rules if r.name == "Slack Bot 令牌")
        assert isinstance(bot_rule.match, LeafMatch)
        # 拼接构造测试 token，避免 secret scanner 误判为真实密钥
        slack_prefix = "xoxb"
        text = f"SLACK_BOT_TOKEN={slack_prefix}-0000000000-0000000000fake"
        assert re.search(bot_rule.match.pattern, text)

    def test_stripe_template_detects_secret_key(self) -> None:
        """Stripe 模板能检测 sk_live_ 开头的密钥。"""
        rs = load_template("stripe_keys")
        secret_rule = next(r for r in rs.rules if r.name == "Stripe Secret 密钥")
        assert isinstance(secret_rule.match, LeafMatch)
        # 拼接构造测试 key，避免 secret scanner 误判为真实密钥
        stripe_prefix = "sk_live"
        text = f"STRIPE_KEY={stripe_prefix}_000000000000000000000000EXAMPLE"
        assert re.search(secret_rule.match.pattern, text)

    def test_jwt_template_detects_jwt_token(self) -> None:
        """JWT 模板能检测三段式 JWT。"""
        rs = load_template("jwt_tokens")
        jwt_rule = next(r for r in rs.rules if r.name == "JWT 令牌")
        assert isinstance(jwt_rule.match, LeafMatch)
        # 典型 JWT 格式
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        assert re.search(jwt_rule.match.pattern, text)

    def test_private_keys_template_detects_pem_header(self) -> None:
        """私钥模板能检测 PEM 文件头。"""
        rs = load_template("private_keys")
        pem_rule = next(r for r in rs.rules if r.name == "PEM 私钥文件头")
        assert isinstance(pem_rule.match, LeafMatch)
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."
        assert re.search(pem_rule.match.pattern, text)

    def test_database_template_detects_mysql_connection(self) -> None:
        """数据库模板能检测 MySQL 连接字符串。"""
        rs = load_template("database_connection_strings")
        mysql_rule = next(r for r in rs.rules if r.name == "MySQL 连接字符串")
        assert isinstance(mysql_rule.match, LeafMatch)
        text = "DATABASE_URL=mysql://root:password123@localhost:3306/mydb"
        assert re.search(mysql_rule.match.pattern, text)

    def test_compliance_scan_detects_sensitive_filename(self) -> None:
        """compliance_scan 能检测敏感配置文件名。"""
        rs = load_template("compliance_scan")
        rule = next(r for r in rs.rules if r.name == "敏感配置文件名")
        assert isinstance(rule.match, LeafMatch)
        assert rule.match.target == MatchTarget.FILENAME
        # .env 文件名应匹配
        assert re.search(rule.match.pattern, ".env")
        assert re.search(rule.match.pattern, "credentials")
        assert re.search(rule.match.pattern, "server.pem")
