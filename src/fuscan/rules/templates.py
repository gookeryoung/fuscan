"""内置规则模板库（iter-122）。

提供常见凭证与隐私数据的规则模板，用户可一键导入后自定义。

公共 API：

- :func:`get_template_names`：返回所有模板名列表
- :func:`get_template_descriptions`：返回模板名到描述的映射
- :func:`load_template`：按名称加载模板，返回 RuleSet

模板内容：

- ``aws_keys``：AWS 访问密钥（AKIA 开头）与秘密密钥
- ``azure_keys``：Azure 账户密钥与连接字符串
- ``gcp_keys``：GCP 服务账号 private_key 与 API key
- ``privacy_data``：隐私数据（身份证号/手机号/邮箱）
- ``common_credentials``：常见凭证关键词（password/secret/token/api_key）
"""

from __future__ import annotations

from fuscan.rules.model import (
    RuleSet,
)
from fuscan.rules.parser import parse_ruleset

__all__ = ["get_template_descriptions", "get_template_names", "load_template"]


# 模板原始定义（YAML 字典形式，复用 parser 解析确保与文件加载行为一致）
_TEMPLATES: dict[str, dict[str, object]] = {
    "aws_keys": {
        "name": "AWS 密钥检测",
        "description": "AWS 访问密钥 ID（AKIA 开头）与秘密密钥模式",
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
    "privacy_data": {
        "name": "隐私数据检测",
        "description": "中国身份证号、手机号、邮箱地址",
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
}


def get_template_names() -> list[str]:
    """返回所有模板名列表（按字母序）。"""
    return sorted(_TEMPLATES.keys())


def get_template_descriptions() -> dict[str, str]:
    """返回模板名到中文描述的映射。"""
    return {name: str(meta["description"]) for name, meta in _TEMPLATES.items()}


def load_template(name: str) -> RuleSet:
    """按名称加载模板，返回 RuleSet。

    :param name: 模板名（如 ``aws_keys``）
    :return: 对应的 RuleSet 实例
    :raises KeyError: 模板名不存在
    """
    if name not in _TEMPLATES:
        available = ", ".join(get_template_names())
        raise KeyError(f"模板 {name!r} 不存在，可用模板: {available}")
    data = _TEMPLATES[name]["data"]
    return parse_ruleset(data)
