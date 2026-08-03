"""规则模型与解析子包。

公共 API：

- 数据模型： :class:`Rule`, :class:`RuleSet`, :class:`LeafMatch`, :class:`AndMatch`,
  :class:`OrMatch`, :class:`NotMatch`, :class:`MatchSpec`, :class:`MatchTarget`,
  :class:`MatchMode`, :class:`Severity`
- 解析函数： :func:`load_ruleset`, :func:`parse_ruleset`, :func:`parse_rule`,
  :func:`parse_match`
- 序列化函数： :func:`save_ruleset`, :func:`serialize_ruleset`,
  :func:`serialize_rule`, :func:`serialize_match`
- 模板库： :func:`get_template_names`, :func:`get_template_descriptions`,
  :func:`load_template`
- 异常： :class:`RuleError`, :class:`RuleParseError`, :class:`RuleLoadError`
"""

from __future__ import annotations

from fuscan.rules.builtin import BUILTIN_RULES_PATH, load_builtin_ruleset, load_with_builtin
from fuscan.rules.errors import RuleError, RuleLoadError, RuleParseError
from fuscan.rules.merge import merge_multiple_rulesets, merge_rulesets
from fuscan.rules.model import (
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
from fuscan.rules.parser import load_ruleset, parse_match, parse_rule, parse_ruleset
from fuscan.rules.serializer import save_ruleset, serialize_match, serialize_rule, serialize_ruleset
from fuscan.rules.templates import get_template_descriptions, get_template_names, load_template

__all__ = [
    "BUILTIN_RULES_PATH",
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
    "get_template_descriptions",
    "get_template_names",
    "load_builtin_ruleset",
    "load_ruleset",
    "load_template",
    "load_with_builtin",
    "merge_multiple_rulesets",
    "merge_rulesets",
    "parse_match",
    "parse_rule",
    "parse_ruleset",
    "save_ruleset",
    "serialize_match",
    "serialize_rule",
    "serialize_ruleset",
]
