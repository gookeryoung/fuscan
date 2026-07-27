"""规则集序列化：将不可变数据结构转换为字典/YAML/JSON（iter-122）。

与 :mod:`fuscan.rules.parser` 互为逆操作：

- ``parser``：字典 → RuleSet（YAML/JSON 导入）
- ``serializer``：RuleSet → 字典（YAML/JSON 导出）

公共 API：

- :func:`serialize_match`：MatchSpec → dict
- :func:`serialize_rule`：Rule → dict
- :func:`serialize_ruleset`：RuleSet → dict
- :func:`save_ruleset`：RuleSet 写入文件（YAML/JSON）

导出的 YAML/JSON 可被 :func:`parser.load_ruleset` 重新加载，行为一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchSpec,
    NotMatch,
    OrMatch,
    Rule,
    RuleSet,
)

__all__ = ["save_ruleset", "serialize_match", "serialize_rule", "serialize_ruleset"]


def serialize_match(match: MatchSpec) -> dict[str, Any]:
    """将匹配条件转换为字典。

    :param match: 匹配条件（LeafMatch/AndMatch/OrMatch/NotMatch）
    :return: 可序列化为 YAML/JSON 的字典
    """
    if isinstance(match, LeafMatch):
        result: dict[str, Any] = {
            "type": match.target.value,
            "mode": match.mode.value,
            "pattern": match.pattern,
        }
        if match.case_sensitive:
            result["case_sensitive"] = True
        if match.description:
            result["description"] = match.description
        return result

    if isinstance(match, AndMatch):
        result = {
            "type": "and",
            "children": [serialize_match(child) for child in match.children],
        }
        if match.description:
            result["description"] = match.description
        return result

    if isinstance(match, OrMatch):
        result = {
            "type": "or",
            "children": [serialize_match(child) for child in match.children],
        }
        if match.description:
            result["description"] = match.description
        return result

    if isinstance(match, NotMatch):
        result = {
            "type": "not",
            "child": serialize_match(match.child),
        }
        if match.description:
            result["description"] = match.description
        return result

    # 理论上不可达：MatchSpec 联合类型已穷尽
    raise TypeError(f"未知匹配类型: {type(match).__name__}")


def serialize_rule(rule: Rule) -> dict[str, Any]:
    """将单条规则转换为字典。

    :param rule: 规则实例
    :return: 可序列化的字典
    """
    result: dict[str, Any] = {
        "name": rule.name,
        "match": serialize_match(rule.match),
        "severity": rule.severity.value,
    }
    if rule.description:
        result["description"] = rule.description
    if rule.replace:
        result["replace"] = True
        result["replace_with"] = rule.replace_with
    return result


def serialize_ruleset(ruleset: RuleSet) -> dict[str, Any]:
    """将规则集转换为字典。

    :param ruleset: 规则集实例
    :return: 可序列化为 YAML/JSON 的字典
    """
    result: dict[str, Any] = {
        "version": ruleset.version,
        "rules": [serialize_rule(rule) for rule in ruleset.rules],
    }
    if ruleset.ignore_paths:
        result["ignore_paths"] = list(ruleset.ignore_paths)
    return result


def save_ruleset(ruleset: RuleSet, path: Path, fmt: str | None = None) -> None:
    """将规则集保存到文件（YAML/JSON）。

    :param ruleset: 规则集实例
    :param path: 目标文件路径
    :param fmt: 显式指定格式（``yaml``/``json``）；为 None 时根据扩展名推断
    :raises ValueError: 不支持的格式
    """
    data = serialize_ruleset(ruleset)
    ext = path.suffix.lower()
    if fmt == "yaml" or (fmt is None and ext in (".yaml", ".yml")):
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)
        return
    if fmt == "json" or (fmt is None and ext == ".json"):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    raise ValueError(f"不支持的规则集格式: fmt={fmt!r}, ext={ext!r}（支持 yaml/json）")
