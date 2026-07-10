"""规则模型与解析子包。

公共 API：

- 数据模型： :class:`Rule`, :class:`RuleSet`, :class:`LeafMatch`, :class:`AndMatch`,
  :class:`OrMatch`, :class:`NotMatch`, :class:`MatchSpec`, :class:`MatchTarget`,
  :class:`MatchMode`, :class:`Severity`
- 解析函数： :func:`load_ruleset`, :func:`parse_ruleset`, :func:`parse_rule`,
  :func:`parse_match`
- 异常： :class:`RuleError`, :class:`RuleParseError`, :class:`RuleLoadError`
"""

from __future__ import annotations

from uniscan.rules.errors import RuleError, RuleLoadError, RuleParseError
from uniscan.rules.merge import merge_multiple_rulesets, merge_rulesets
from uniscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchSpec,
    MatchTarget,
    NotMatch,
    OrMatch,
    Rule,
    RuleSet,
    Severity,
)
from uniscan.rules.parser import load_ruleset, parse_match, parse_rule, parse_ruleset

__all__ = [
    "AndMatch",
    "LeafMatch",
    "MatchMode",
    "MatchSpec",
    "MatchTarget",
    "NotMatch",
    "OrMatch",
    "Rule",
    "RuleError",
    "RuleLoadError",
    "RuleParseError",
    "RuleSet",
    "Severity",
    "load_ruleset",
    "merge_multiple_rulesets",
    "merge_rulesets",
    "parse_match",
    "parse_rule",
    "parse_ruleset",
]
