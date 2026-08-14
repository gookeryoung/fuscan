"""规则编辑纯函数：对 RuleSet 的 rules 元组执行定位/替换/追加/删除。

供 GUI「规则编辑」面板调用：所有操作返回新的不可变 :class:`RuleSet`，不涉及
文件 I/O，便于在 pytest 下覆盖。与 :mod:`fuscan.rules.sandbox` 同为无 Qt 依赖
的纯函数模块。

公共 API：

- :func:`find_rule`：按 name 查找规则
- :func:`is_leaf_rule`：判断规则是否为叶子匹配
- :func:`make_leaf_rule`：构造并校验叶子规则
- :func:`replace_rule`：按 original_name 替换规则（支持重命名）
- :func:`append_rule`：追加规则（重名报错）
- :func:`remove_rule`：按 name 删除规则
- :func:`serialize_rule_for_editor`：规则扁平化为编辑器友好字典
"""

from __future__ import annotations

import re
from dataclasses import replace

from fuscan.rules.errors import RuleError
from fuscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)

__all__ = [
    "append_rule",
    "find_rule",
    "is_leaf_rule",
    "make_leaf_rule",
    "remove_rule",
    "replace_rule",
    "serialize_rule_for_editor",
]


def find_rule(ruleset: RuleSet, name: str) -> Rule | None:
    """按 ``name`` 在 ``ruleset.rules`` 中查找规则，未找到返回 None。"""
    return next((r for r in ruleset.rules if r.name == name), None)


def is_leaf_rule(rule: Rule) -> bool:
    """规则是否为叶子匹配（LeafMatch）；组合规则（AND/OR/NOT）返回 False。"""
    return isinstance(rule.match, LeafMatch)


def make_leaf_rule(
    *,
    name: str,
    severity: str,
    target: str,
    mode: str,
    pattern: str,
    case_sensitive: bool = False,
    description: str = "",
    replace: bool = False,
    replace_with: str = "",
) -> Rule:
    """构造叶子规则并校验字段合法性。

    :raises RuleError: ``name``/``pattern`` 为空、枚举值非法、regex 模式编译失败
    """
    name = name.strip()
    if not name:
        raise RuleError("规则 name 不能为空")
    pattern = pattern.strip() if pattern else ""
    if not pattern:
        raise RuleError("匹配模式 pattern 不能为空")
    try:
        target_enum = MatchTarget(target)
        mode_enum = MatchMode(mode)
        severity_enum = Severity(severity)
    except ValueError as exc:
        raise RuleError(f"枚举值非法: {exc}") from exc
    # regex 模式预编译校验，避免保存后扫描时才暴露语法错误
    if mode_enum is MatchMode.REGEX:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RuleError(f"正则编译失败: {exc}") from exc
    leaf = LeafMatch(
        target=target_enum,
        mode=mode_enum,
        pattern=pattern,
        case_sensitive=case_sensitive,
        # 编辑器只管理规则级 description，叶子描述留空避免与规则级描述重复展示
        description="",
    )
    return Rule(
        name=name,
        match=leaf,
        description=description.strip(),
        severity=severity_enum,
        replace=replace,
        replace_with=replace_with,
    )


def replace_rule(ruleset: RuleSet, original_name: str, new_rule: Rule) -> RuleSet:
    """按 ``original_name`` 定位并替换为 ``new_rule``（支持重命名）。

    :raises RuleError: ``original_name`` 未找到，或 ``new_rule.name`` 与其他
        规则（非 ``original_name`` 自身）重名
    """
    if find_rule(ruleset, original_name) is None:
        raise RuleError(f"规则未找到: {original_name}")
    # 重名检查：新名与其他规则（非 original_name）冲突
    for r in ruleset.rules:
        if r.name == new_rule.name and r.name != original_name:
            raise RuleError(f"规则名已存在: {new_rule.name}")
    new_rules = tuple(new_rule if r.name == original_name else r for r in ruleset.rules)
    return replace(ruleset, rules=new_rules)


def append_rule(ruleset: RuleSet, rule: Rule) -> RuleSet:
    """追加规则到末尾。

    :raises RuleError: 规则名已存在
    """
    if find_rule(ruleset, rule.name) is not None:
        raise RuleError(f"规则名已存在: {rule.name}")
    return replace(ruleset, rules=(*ruleset.rules, rule))


def remove_rule(ruleset: RuleSet, name: str) -> RuleSet:
    """按 ``name`` 删除规则。

    :raises RuleError: 规则未找到
    """
    if find_rule(ruleset, name) is None:
        raise RuleError(f"规则未找到: {name}")
    new_rules = tuple(r for r in ruleset.rules if r.name != name)
    return replace(ruleset, rules=new_rules)


def serialize_rule_for_editor(rule: Rule) -> dict[str, object]:
    """将规则序列化为编辑器友好的扁平字典。

    叶子规则展开 ``target``/``mode``/``pattern``/``caseSensitive`` 字段；
    组合规则这些字段留空且 ``isLeaf=False``，编辑器据此显示只读提示。

    :return: 含 ``name``/``description``/``severity``/``target``/``mode``/
        ``pattern``/``caseSensitive``/``replace``/``replaceWith``/``isLeaf`` 的字典
    """
    if isinstance(rule.match, LeafMatch):
        return {
            "name": rule.name,
            "description": rule.description,
            "severity": rule.severity.value,
            "target": rule.match.target.value,
            "mode": rule.match.mode.value,
            "pattern": rule.match.pattern,
            "caseSensitive": rule.match.case_sensitive,
            "replace": rule.replace,
            "replaceWith": rule.replace_with,
            "isLeaf": True,
        }
    return {
        "name": rule.name,
        "description": rule.description,
        "severity": rule.severity.value,
        "target": "",
        "mode": "",
        "pattern": "",
        "caseSensitive": False,
        "replace": rule.replace,
        "replaceWith": rule.replace_with,
        "isLeaf": False,
    }
