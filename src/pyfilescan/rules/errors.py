"""规则解析相关异常定义。"""

from __future__ import annotations

__all__ = ["RuleError", "RuleLoadError", "RuleParseError"]


class RuleError(Exception):
    """规则相关错误基类。"""


class RuleParseError(RuleError):
    """规则字典/数据结构解析失败时抛出。"""


class RuleLoadError(RuleError):
    """规则文件读取或 YAML 解析失败时抛出。"""
