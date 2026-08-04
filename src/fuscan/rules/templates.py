"""内置规则模板库（支持模板继承）。

提供常见凭证与隐私数据的规则模板，用户可一键导入后自定义。

模板继承
========

模板可通过 ``extends`` 字段声明一个或多个父模板（按名称引用），
实现规则复用与组合：

- **多重继承**：``extends: ["aws_keys", "azure_keys"]`` 同时继承多个父模板
- **传递继承**：父模板本身也可继承其他模板，解析时递归展开
- **覆盖语义**：子模板的同名规则覆盖父模板；``ignore_paths``/
  ``ignore_dirs``/``whitelist`` 取并集；``scan_extensions``/``scan_params``
  由后者非 ``None`` 字段覆盖（复用 :func:`merge_multiple_rulesets`）
- **解析顺序**：``C extends [A, B]`` → ``merge_multiple_rulesets(load(A), load(B), parse(C.data))``
  （A 为基础，B 覆盖 A，C 覆盖 A、B）
- **循环检测**：解析链中发现重复模板名时抛 :class:`RuleParseError`

公共 API
========

- :func:`get_template_names`：返回所有模板名列表（字母序）
- :func:`get_template_descriptions`：返回模板名到描述的映射
- :func:`get_template_metadata`：返回模板完整元信息（含继承链与规则数）
- :func:`load_template`：按名称加载模板，解析继承链后返回 RuleSet

模板分类
========

- **叶子模板**（无 ``extends``）：直接定义规则
  - ``aws_keys`` / ``azure_keys`` / ``gcp_keys``：云平台密钥
  - ``github_tokens`` / ``slack_tokens`` / ``stripe_keys`` / ``jwt_tokens``：SaaS 令牌
  - ``private_keys`` / ``database_connection_strings``：私钥与连接字符串
  - ``privacy_data`` / ``common_credentials``：隐私数据与常见凭证
- **组合模板**（通过 ``extends`` 组合叶子模板）：
  - ``cloud_keys``：AWS + Azure + GCP 密钥集合
  - ``saas_tokens``：GitHub + Slack + Stripe + JWT 令牌集合
  - ``compliance_scan``：综合合规扫描（云密钥 + SaaS 令牌 + 隐私数据 + 凭证）
"""

from __future__ import annotations

from fuscan.rules.errors import RuleParseError
from fuscan.rules.merge import merge_multiple_rulesets
from fuscan.rules.model import RuleSet
from fuscan.rules.parser import parse_ruleset

__all__ = ["get_template_descriptions", "get_template_metadata", "get_template_names", "load_template"]


# 模板原始定义（YAML 字典形式，复用 parser 解析确保与文件加载行为一致）
# 每个模板含以下字段：
# - name：展示名（中文）
# - description：描述文本
# - extends：父模板名列表（空列表表示叶子模板）
# - data：RuleSet 字典（自身规则；组合模板可为仅含 version 的空规则集）
_TEMPLATES: dict[str, dict[str, object]] = {
    # ------------------- 云平台密钥（叶子模板） -------------------
    "aws_keys": {
        "name": "AWS 密钥检测",
        "description": "AWS 访问密钥 ID（AKIA 开头）与秘密密钥模式",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "AWS 访问密钥 ID",
                    "description": "AKIA 开头的 20 位 AWS 访问密钥 ID",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "AKIA[0-9A-Z]{16}",
                        "description": "AWS Access Key ID",
                    },
                },
                {
                    "name": "AWS 秘密密钥",
                    "description": "40 位 AWS 秘密访问密钥（高熵 Base64）",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)aws.{0,20}secret.{0,20}[A-Za-z0-9/+=]{40}",
                        "description": "AWS Secret Access Key",
                    },
                },
            ],
        },
    },
    "azure_keys": {
        "name": "Azure 密钥检测",
        "description": "Azure 账户密钥与连接字符串",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "Azure 连接字符串",
                    "description": "Azure Storage 连接字符串含 AccountKey",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)DefaultEndpointsProtocol=https?;AccountName=[^;]+;AccountKey=[A-Za-z0-9+/=]+",
                        "description": "Azure Storage Connection String",
                    },
                },
                {
                    "name": "Azure 账户密钥",
                    "description": "88 位 Azure Base64 账户密钥",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)azure.{0,20}(?:key|secret).{0,20}[A-Za-z0-9+/=]{88}",
                        "description": "Azure Account Key",
                    },
                },
            ],
        },
    },
    "gcp_keys": {
        "name": "GCP 密钥检测",
        "description": "GCP 服务账号 private_key 与 API key",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "GCP 服务账号私钥",
                    "description": "服务账号 JSON 中的 private_key 字段",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)private_key.{0,50}BEGIN PRIVATE KEY",
                        "description": "GCP Service Account Private Key",
                    },
                },
                {
                    "name": "GCP API 密钥",
                    "description": "39 位的 Google API 密钥（AIza 开头）",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "AIza[0-9A-Za-z_\\-]{35}",
                        "description": "Google API Key",
                    },
                },
            ],
        },
    },
    # ------------------- SaaS 令牌（叶子模板） -------------------
    "github_tokens": {
        "name": "GitHub 令牌检测",
        "description": "GitHub Personal Access Token（ghp_/gho_/ghs_/ghu_/ghr_ 前缀）",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "GitHub PAT 令牌",
                    "description": "ghp_ 开头的 36 位 GitHub 个人访问令牌",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\bghp_[A-Za-z0-9]{36}\\b",
                        "description": "GitHub Personal Access Token (classic)",
                    },
                },
                {
                    "name": "GitHub OAuth 令牌",
                    "description": "gho_ 开头的 GitHub OAuth 令牌",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\bgho_[A-Za-z0-9]{36}\\b",
                        "description": "GitHub OAuth Access Token",
                    },
                },
                {
                    "name": "GitHub App 令牌",
                    "description": "ghs_ 开头的 GitHub App 安装令牌",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\bghs_[A-Za-z0-9]{36}\\b",
                        "description": "GitHub App Installation Token",
                    },
                },
            ],
        },
    },
    "slack_tokens": {
        "name": "Slack 令牌检测",
        "description": "Slack Bot/User/Refresh/Webhook 令牌（xox[bpoar]- 前缀）",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "Slack Bot 令牌",
                    "description": "xoxb- 开头的 Slack Bot User OAuth 令牌",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\bxoxb-[0-9]{10,13}-[0-9]{10,13}[A-Za-z0-9]*\\b",
                        "description": "Slack Bot User OAuth Token",
                    },
                },
                {
                    "name": "Slack User 令牌",
                    "description": "xoxp- 开头的 Slack User OAuth 令牌",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\bxoxp-[0-9]{10,13}-[0-9]{10,13}-[0-9]{10,13}[A-Za-z0-9]*\\b",
                        "description": "Slack User OAuth Token",
                    },
                },
                {
                    "name": "Slack Webhook URL",
                    "description": "hooks.slack.com/services/ 形式的 Slack Incoming Webhook",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "https://hooks\\.slack\\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]+",
                        "description": "Slack Incoming Webhook URL",
                    },
                },
            ],
        },
    },
    "stripe_keys": {
        "name": "Stripe 密钥检测",
        "description": "Stripe Secret/Publishable/Restricted 密钥（sk_live_/pk_live_/rk_live_ 前缀）",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "Stripe Secret 密钥",
                    "description": "sk_live_ 开头的 Stripe Secret API 密钥",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\bsk_live_[A-Za-z0-9]{24,}\\b",
                        "description": "Stripe Live Secret Key",
                    },
                },
                {
                    "name": "Stripe Publishable 密钥",
                    "description": "pk_live_ 开头的 Stripe Publishable 密钥",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\bpk_live_[A-Za-z0-9]{24,}\\b",
                        "description": "Stripe Live Publishable Key",
                    },
                },
                {
                    "name": "Stripe Restricted 密钥",
                    "description": "rk_live_ 开头的 Stripe Restricted 密钥",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\brk_live_[A-Za-z0-9]{24,}\\b",
                        "description": "Stripe Live Restricted Key",
                    },
                },
            ],
        },
    },
    "jwt_tokens": {
        "name": "JWT 令牌检测",
        "description": "JSON Web Token（eyJ 三段式 Base64URL 编码）",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "JWT 令牌",
                    "description": "三段式 JWT（header.payload.signature，均以 eyJ 开头）",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\beyJ[A-Za-z0-9_-]+\\.eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\b",
                        "description": "JSON Web Token (JWT)",
                    },
                },
            ],
        },
    },
    # ------------------- 私钥与连接字符串（叶子模板） -------------------
    "private_keys": {
        "name": "私钥文件检测",
        "description": "PEM 格式私钥文件头（RSA/EC/DSA/OPENSSH/PGP）",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "PEM 私钥文件头",
                    "description": "PEM 格式私钥文件头（RSA/EC/DSA/OPENSSH/PGP）",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "-----BEGIN\\s+(RSA\\s+|EC\\s+|DSA\\s+|OPENSSH\\s+|PGP\\s+)?PRIVATE\\s+KEY-----",
                        "description": "PEM Private Key Header",
                    },
                },
                {
                    "name": "PKCS#8 私钥文件头",
                    "description": "ENCRYPTED PRIVATE KEY 块头",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "-----BEGIN\\s+ENCRYPTED\\s+PRIVATE\\s+KEY-----",
                        "description": "PKCS#8 Encrypted Private Key Header",
                    },
                },
            ],
        },
    },
    "database_connection_strings": {
        "name": "数据库连接字符串检测",
        "description": "MySQL/PostgreSQL/MongoDB/Redis/MSSQL 连接字符串含凭证",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "MySQL 连接字符串",
                    "description": "mysql:// user:password@host 形式的连接字符串",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)mysql://[^:\\s]+:[^@\\s]+@[A-Za-z0-9.:-]+",
                        "description": "MySQL Connection String",
                    },
                },
                {
                    "name": "PostgreSQL 连接字符串",
                    "description": "postgresql:// 或 postgres:// user:password@host 形式",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)postgres(?:ql)?://[^:\\s]+:[^@\\s]+@[A-Za-z0-9.:-]+",
                        "description": "PostgreSQL Connection String",
                    },
                },
                {
                    "name": "MongoDB 连接字符串",
                    "description": "mongodb:// 或 mongodb+srv:// user:password@host 形式",
                    "severity": "critical",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)mongodb(?:\\+srv)?://[^:\\s]+:[^@\\s]+@[A-Za-z0-9.:-]+",
                        "description": "MongoDB Connection String",
                    },
                },
                {
                    "name": "Redis 连接字符串",
                    "description": "redis:// :password@host 形式的连接字符串",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)redis://:[^@\\s]+@[A-Za-z0-9.:-]+",
                        "description": "Redis Connection String",
                    },
                },
            ],
        },
    },
    # ------------------- 隐私数据与常见凭证（叶子模板） -------------------
    "privacy_data": {
        "name": "隐私数据检测",
        "description": "中国身份证号、手机号、邮箱地址",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "身份证号",
                    "description": "18 位中国居民身份证号",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\b[1-9]\\d{5}(?:19|20)\\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\\d|3[01])\\d{3}[0-9Xx]\\b",
                        "description": "18 位身份证号",
                    },
                },
                {
                    "name": "手机号",
                    "description": "11 位中国手机号",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\b1[3-9]\\d{9}\\b",
                        "description": "11 位手机号",
                    },
                },
                {
                    "name": "邮箱地址",
                    "description": "电子邮件地址",
                    "severity": "info",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b",
                        "description": "邮箱地址",
                    },
                },
            ],
        },
    },
    "common_credentials": {
        "name": "常见凭证检测",
        "description": "password/secret/token/api_key 等凭证关键词",
        "extends": [],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "密码赋值",
                    "description": "password = / pwd = 等赋值语句",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)(?:password|passwd|pwd)\\s*[:=]\\s*['\"]?[^'\"\\s]{4,}",
                        "description": "密码赋值语句",
                    },
                },
                {
                    "name": "API 密钥赋值",
                    "description": "api_key = / apikey = 等赋值语句",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)api[_-]?key\\s*[:=]\\s*['\"]?[^'\"\\s]{8,}",
                        "description": "API 密钥赋值",
                    },
                },
                {
                    "name": "Token 赋值",
                    "description": "token = / secret = 等赋值语句",
                    "severity": "warning",
                    "match": {
                        "type": "content",
                        "mode": "regex",
                        "pattern": "(?i)(?:token|secret|access_token)\\s*[:=]\\s*['\"]?[^'\"\\s]{8,}",
                        "description": "Token 赋值语句",
                    },
                },
            ],
        },
    },
    # ------------------- 组合模板（通过 extends 继承） -------------------
    "cloud_keys": {
        "name": "云平台密钥集合",
        "description": "AWS + Azure + GCP 三大云平台密钥检测（继承自 aws_keys/azure_keys/gcp_keys）",
        "extends": ["aws_keys", "azure_keys", "gcp_keys"],
        "data": {
            "version": "1.0",
            "rules": [],
        },
    },
    "saas_tokens": {
        "name": "SaaS 令牌集合",
        "description": "GitHub + Slack + Stripe + JWT 令牌检测（继承自 github_tokens/slack_tokens/stripe_keys/jwt_tokens）",
        "extends": ["github_tokens", "slack_tokens", "stripe_keys", "jwt_tokens"],
        "data": {
            "version": "1.0",
            "rules": [],
        },
    },
    "compliance_scan": {
        "name": "合规综合扫描",
        "description": "云密钥 + SaaS 令牌 + 隐私数据 + 常见凭证 + 私钥 + 数据库连接字符串的综合合规扫描模板",
        "extends": [
            "cloud_keys",
            "saas_tokens",
            "privacy_data",
            "common_credentials",
            "private_keys",
            "database_connection_strings",
        ],
        "data": {
            "version": "1.0",
            "rules": [
                {
                    "name": "敏感配置文件名",
                    "description": "检测常见敏感配置文件名（.env/credentials/secrets/.pem/.key 等）",
                    "severity": "info",
                    "match": {
                        "type": "filename",
                        "mode": "regex",
                        "pattern": "(?i)(^\\.env$|^\\.env\\.|^credentials|^secrets\\.|\\.pem$|\\.key$|\\.pfx$|\\.keystore$)",
                        "description": "敏感配置文件名",
                    },
                },
            ],
        },
    },
}


def get_template_names() -> list[str]:
    """返回所有模板名列表（按字母序）。"""
    return sorted(_TEMPLATES.keys())


def get_template_descriptions() -> dict[str, str]:
    """返回模板名到中文描述的映射。"""
    return {name: str(meta["description"]) for name, meta in _TEMPLATES.items()}


def get_template_metadata(name: str) -> dict[str, object]:
    """返回模板完整元信息（含继承链与解析后规则数）。

    :param name: 模板名
    :return: 元信息字典，字段：

        - ``name``：模板展示名（中文）
        - ``description``：描述文本
        - ``extends``：父模板名列表（叶子模板为空列表）
        - ``isComposite``：是否为组合模板（extends 非空）
        - ``ruleCount``：解析继承链后的规则总数
    :raises KeyError: 模板名不存在
    """
    if name not in _TEMPLATES:
        available = ", ".join(get_template_names())
        raise KeyError(f"模板 {name!r} 不存在，可用模板: {available}")
    meta = _TEMPLATES[name]
    extends = _extract_extends(meta)
    # 解析后的规则数（触发继承解析）
    ruleset = load_template(name)
    return {
        "name": str(meta["name"]),
        "description": str(meta["description"]),
        "extends": extends,
        "isComposite": len(extends) > 0,
        "ruleCount": len(ruleset.rules),
    }


def _extract_extends(meta: dict[str, object]) -> list[str]:
    """从模板定义中提取 extends 列表（类型安全）。

    :param meta: 模板元数据字典
    :return: 父模板名列表（叶子模板返回空列表）
    """
    extends_raw = meta.get("extends")
    if not isinstance(extends_raw, list):
        return []
    return [str(p) for p in extends_raw]


def load_template(name: str, _chain: tuple[str, ...] | None = None) -> RuleSet:
    """按名称加载模板，解析继承链后返回 RuleSet。

    解析算法：

    1. 读取模板定义中的 ``extends`` 列表（父模板名）
    2. 递归加载每个父模板（父模板本身可能继续继承）
    3. 按顺序合并父模板 RuleSet（后者覆盖前者同名规则）
    4. 将本模板自身 ``data`` 合并到父模板合并结果之上（本模板覆盖父模板）
    5. 无父模板时直接解析 ``data`` 返回

    循环检测：用 ``_chain`` 元组跟踪当前解析链（有序），发现重复模板名时
    抛 :class:`RuleParseError`，错误消息包含完整解析路径。

    :param name: 模板名（如 ``aws_keys``）
    :param _chain: 内部递归用解析链元组，外部调用不应传入
    :return: 对应的 RuleSet 实例（已合并所有父模板）
    :raises KeyError: 模板名不存在
    :raises RuleParseError: 检测到循环继承或引用了不存在的父模板
    """
    if name not in _TEMPLATES:
        available = ", ".join(get_template_names())
        raise KeyError(f"模板 {name!r} 不存在，可用模板: {available}")

    # 循环检测：当前模板已在解析链中 → 形成环
    if _chain is None:
        _chain = ()
    if name in _chain:
        chain = " -> ".join([*_chain, name])
        raise RuleParseError(f"检测到模板循环继承: {chain}")

    meta = _TEMPLATES[name]
    extends_list = _extract_extends(meta)

    # 解析本模板自身 data
    own_ruleset = parse_ruleset(meta["data"])

    # 无父模板：直接返回自身解析结果
    if not extends_list:
        return own_ruleset

    # 有父模板：递归加载并按顺序合并
    new_chain = (*_chain, name)
    parent_rulesets: list[RuleSet] = []
    for parent_name in extends_list:
        if parent_name not in _TEMPLATES:
            raise RuleParseError(
                f"模板 {name!r} 引用了不存在的父模板 {parent_name!r}，可用模板: {', '.join(get_template_names())}"
            )
        parent_rulesets.append(load_template(parent_name, _chain=new_chain))

    # 合并顺序：父模板先（按 extends 列表顺序）→ 本模板最后（覆盖父模板）
    return merge_multiple_rulesets(*parent_rulesets, own_ruleset)
