"""规则 YAML 解析器。

将字典形式（来自 YAML）转换为 :mod:`fuscan.rules.model` 中的不可变数据结构。

YAML 结构示例见 ``rules/example.yaml``。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from fuscan.rules.errors import RuleLoadError, RuleParseError
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

__all__ = ["load_ruleset", "parse_match", "parse_rule", "parse_ruleset"]

logger = logging.getLogger(__name__)


_LEAF_TYPES = {"filename", "content", "path"}
_COMPOSITE_TYPES = {"and", "or", "not"}


def parse_match(data: Any) -> MatchSpec:
    """从字典构造匹配条件。

    :param data: 匹配条件字典，必须包含 ``type`` 字段
    :return: 对应的 MatchSpec 实例
    :raises RuleParseError: 数据结构不合法或缺少必填字段
    """
    if not isinstance(data, Mapping):
        raise RuleParseError(f"匹配条件必须是字典，得到 {type(data).__name__}")

    match_type = data.get("type")
    if not match_type:
        raise RuleParseError("匹配条件缺少 type 字段")

    if match_type in _LEAF_TYPES:
        return _parse_leaf(match_type, data)
    if match_type in _COMPOSITE_TYPES:
        return _parse_composite(match_type, data)
    raise RuleParseError(f"未知匹配类型: {match_type!r}")


def _parse_leaf(match_type: str, data: Mapping[str, Any]) -> LeafMatch:
    """解析叶子匹配条件（filename/content/path）。"""
    target = MatchTarget(match_type)
    mode_raw = data.get("mode")
    if not mode_raw:
        raise RuleParseError(f"叶子匹配 ({match_type}) 缺少 mode 字段")
    try:
        mode = MatchMode(mode_raw)
    except ValueError as exc:
        valid = ", ".join(m.value for m in MatchMode)
        raise RuleParseError(f"未知匹配模式 {mode_raw!r}，合法值: {valid}") from exc

    pattern = data.get("pattern")
    if not pattern:
        raise RuleParseError(f"叶子匹配 ({match_type}) 缺少 pattern 字段")

    case_sensitive = bool(data.get("case_sensitive", False))
    description = str(data.get("description", ""))
    return LeafMatch(
        target=target,
        mode=mode,
        pattern=str(pattern),
        case_sensitive=case_sensitive,
        description=description,
    )


def _parse_composite(match_type: str, data: Mapping[str, Any]) -> MatchSpec:
    """解析逻辑组合匹配条件（and/or/not）。"""
    description = str(data.get("description", ""))
    if match_type in ("and", "or"):
        children_raw = data.get("children")
        if not isinstance(children_raw, Sequence) or isinstance(children_raw, (str, bytes)):
            raise RuleParseError(f"{match_type} 匹配缺少 children 列表")
        children = tuple(parse_match(child) for child in children_raw)
        if not children:
            raise RuleParseError(f"{match_type} 匹配的 children 不能为空")
        if match_type == "and":
            return AndMatch(children=children, description=description)
        return OrMatch(children=children, description=description)

    # not
    child_raw = data.get("child")
    if child_raw is None:
        raise RuleParseError("not 匹配缺少 child 字段")
    return NotMatch(child=parse_match(child_raw), description=description)


def parse_rule(data: Any) -> Rule:
    """从字典构造单条规则。

    :param data: 规则字典，必须包含 ``name`` 和 ``match``
    :return: Rule 实例
    :raises RuleParseError: 数据结构不合法
    """
    if not isinstance(data, Mapping):
        raise RuleParseError(f"规则必须是字典，得到 {type(data).__name__}")

    name = data.get("name")
    if not name:
        raise RuleParseError("规则缺少 name 字段")

    match_data = data.get("match")
    if match_data is None:
        raise RuleParseError(f"规则 {name!r} 缺少 match 字段")
    match = parse_match(match_data)

    description = str(data.get("description", ""))
    severity_raw = data.get("severity", "info")
    try:
        severity = Severity(severity_raw)
    except ValueError as exc:
        valid = ", ".join(s.value for s in Severity)
        raise RuleParseError(f"规则 {name!r} 未知严重等级 {severity_raw!r}，合法值: {valid}") from exc

    # file_extensions 已移除（iter-86）：旧规则文件中的该字段被静默忽略，
    # 文件后缀过滤由全局 Config.extractors 统一管理（iter-71 起 Scanner 不再读取此字段）。
    return Rule(
        name=str(name),
        match=match,
        description=description,
        severity=severity,
    )


def parse_ruleset(data: Any) -> RuleSet:
    """从字典构造规则集合。

    :param data: 规则集字典（YAML 顶层结构）
    :return: RuleSet 实例
    :raises RuleParseError: 数据结构不合法
    """
    if not isinstance(data, Mapping):
        raise RuleParseError(f"规则集必须是字典，得到 {type(data).__name__}")

    version = str(data.get("version", "1.0"))

    # ignore_dirs 已迁移至全局 Config.ignore_dirs；ignore_extensions 已由全局
    # 文件类型白名单（Config.scan_extensions）替代。规则文件中这两个字段被静默忽略。
    ignore_dirs_raw = data.get("ignore_dirs", [])
    if ignore_dirs_raw:
        logger.debug("规则文件中 ignore_dirs 已弃用，请改用全局配置")
    ignore_ext_raw = data.get("ignore_extensions", [])
    if ignore_ext_raw:
        logger.debug("规则文件中 ignore_extensions 已弃用，请改用全局文件类型勾选")

    ignore_paths_raw = data.get("ignore_paths", [])
    ignore_paths = _as_str_tuple(ignore_paths_raw, field="ignore_paths")

    rules_raw = data.get("rules", [])
    if not isinstance(rules_raw, Sequence) or isinstance(rules_raw, (str, bytes)):
        raise RuleParseError("rules 必须是列表")
    rules = tuple(parse_rule(item) for item in rules_raw)

    return RuleSet(
        version=version,
        rules=rules,
        ignore_paths=ignore_paths,
    )


def _as_str_tuple(value: Any, *, field: str, strip_dot: bool = False) -> tuple[str, ...]:
    """将列表规范化为字符串元组，可选去除前导点号。"""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RuleParseError(f"{field} 必须是列表")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise RuleParseError(f"{field} 中的元素必须是字符串，得到 {type(item).__name__}")
        normalized = item.lower() if strip_dot else item
        if strip_dot and normalized.startswith("."):
            normalized = normalized.lstrip(".")
        items.append(normalized)
    return tuple(items)


def load_ruleset(path: Path) -> RuleSet:
    """从 YAML 文件加载规则集。

    :param path: YAML 规则文件路径
    :return: RuleSet 实例
    :raises RuleLoadError: 文件读取或 YAML 解析失败
    :raises RuleParseError: 数据结构不合法
    """
    if not path.exists():
        raise RuleLoadError(f"规则文件不存在: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise RuleLoadError(f"YAML 解析失败: {path}: {exc}") from exc
    except OSError as exc:
        raise RuleLoadError(f"规则文件读取失败: {path}: {exc}") from exc

    if data is None:
        raise RuleParseError(f"规则文件为空: {path}")

    return parse_ruleset(data)
