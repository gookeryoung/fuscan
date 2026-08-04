"""规则 YAML 解析器。

将字典形式（来自 YAML）转换为 :mod:`fuscan.rules.model` 中的不可变数据结构。

YAML 结构示例见 ``rules/example.yaml``。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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
    ScanParams,
    Severity,
)
from fuscan.rules.whitelist import WhitelistEntry

__all__ = ["load_ruleset", "parse_match", "parse_rule", "parse_ruleset"]

logger = logging.getLogger(__name__)


_LEAF_TYPES = {"filename", "content", "path"}
_COMPOSITE_TYPES = {"and", "or", "not"}

# 规则集版本兼容性检查
# 当前支持的规则集版本；导入时校验，不兼容版本抛出 RuleParseError
SUPPORTED_VERSIONS: frozenset[str] = frozenset({"1.0"})


def parse_match(data: Any, dedup: dict[int, MatchSpec] | None = None) -> MatchSpec:
    """从字典构造匹配条件。

    :param data: 匹配条件字典，必须包含 ``type`` 字段
    :param dedup: AST 共享节点去重。相同 (hashable_key) 结构的
        ``MatchSpec`` 在同一个 ``parse_ruleset`` 执行中共享同一对象，减少
        内存占用 + 加速 ``build_matcher`` 中 `is`/`id` 快速路径判断。为
        ``None`` 时不启用去重（等价于旧行为）。
    :return: 对应的 MatchSpec 实例
    :raises RuleParseError: 数据结构不合法或缺少必填字段
    """
    if not isinstance(data, Mapping):
        raise RuleParseError(f"匹配条件必须是字典，得到 {type(data).__name__}")

    match_type = data.get("type")
    if not match_type:
        raise RuleParseError("匹配条件缺少 type 字段")

    if match_type in _LEAF_TYPES:
        return _parse_leaf(match_type, data, dedup=dedup)
    if match_type in _COMPOSITE_TYPES:
        return _parse_composite(match_type, data, dedup=dedup)
    raise RuleParseError(f"未知匹配类型: {match_type!r}")


def _parse_leaf(
    match_type: str,
    data: Mapping[str, Any],
    *,
    dedup: dict[int, MatchSpec] | None,
) -> LeafMatch:
    """解析叶子匹配条件（filename/content/path）。启用 dedup 时，同键 LeafMatch 共享对象。"""
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
    spec = LeafMatch(
        target=target,
        mode=mode,
        pattern=str(pattern),
        case_sensitive=case_sensitive,
        description=description,
    )
    if dedup is None:
        return spec
    # hashable_key: LeafMatch 作为 MatchSpec 是 frozen dataclass，可直接 id()/hash()
    # 为了避免"不同 description 但其他字段相同"被错误合并（description 可能在 UI
    # 展示时会被用户看到），用完整 dataclass 的 hash 做 key 更安全。
    key = hash(
        (
            0,  # 0 = LeafMatch 类型哨兵，避免与组合器 key 碰撞
            target.value,
            mode.value,
            spec.pattern,
            case_sensitive,
            description,
        )
    )
    cached = dedup.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    dedup[key] = spec
    return spec


def _parse_composite(
    match_type: str,
    data: Mapping[str, Any],
    *,
    dedup: dict[int, MatchSpec] | None,
) -> MatchSpec:
    """解析逻辑组合匹配条件（and/or/not）。同结构组合器共享对象。"""
    description = str(data.get("description", ""))
    if match_type in ("and", "or"):
        children_raw = data.get("children")
        if not isinstance(children_raw, Sequence) or isinstance(children_raw, (str, bytes)):
            raise RuleParseError(f"{match_type} 匹配缺少 children 列表")
        children = tuple(parse_match(child, dedup=dedup) for child in children_raw)
        if not children:
            raise RuleParseError(f"{match_type} 匹配的 children 不能为空")
        type_tag = 1 if match_type == "and" else 2
        if match_type == "and":
            spec: MatchSpec = AndMatch(children=children, description=description)
        else:
            spec = OrMatch(children=children, description=description)
        if dedup is None:
            return spec
        # key: (type_tag, tuple[id(child) for child in children], description)
        key = hash((type_tag, tuple(id(c) for c in children), description))
        cached = dedup.get(key)
        if cached is not None:
            return cached
        dedup[key] = spec
        return spec

    # not
    child_raw = data.get("child")
    if child_raw is None:
        raise RuleParseError("not 匹配缺少 child 字段")
    child = parse_match(child_raw, dedup=dedup)
    spec = NotMatch(child=child, description=description)
    if dedup is None:
        return spec
    key = hash((3, id(child), description))
    cached = dedup.get(key)
    if cached is not None:
        return cached
    dedup[key] = spec
    return spec


def parse_rule(data: Any, *, dedup: dict[int, MatchSpec] | None = None) -> Rule:
    """从字典构造单条规则。

    :param data: 规则字典，必须包含 ``name`` 和 ``match``
    :param dedup: AST 共享节点去重。为 ``None`` 时不启用。
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
    match = parse_match(match_data, dedup=dedup)

    description = str(data.get("description", ""))
    severity_raw = data.get("severity", "info")
    try:
        severity = Severity(severity_raw)
    except ValueError as exc:
        valid = ", ".join(s.value for s in Severity)
        raise RuleParseError(f"规则 {name!r} 未知严重等级 {severity_raw!r}，合法值: {valid}") from exc

    # file_extensions 已移除：旧规则文件中的该字段被静默忽略，文件后缀过滤由全局 Config 统一管理。
    # replace/replace_with：可选字段，控制命中内容替换行为。
    # replace 为 True 时启用替换；replace_with 缺省为空字符串，触发替换时提示用户补充。
    replace = bool(data.get("replace", False))
    replace_with = str(data.get("replace_with", ""))
    return Rule(
        name=str(name),
        match=match,
        description=description,
        severity=severity,
        replace=replace,
        replace_with=replace_with,
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

    # 版本兼容性检查
    if version not in SUPPORTED_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_VERSIONS))
        raise RuleParseError(f"不支持的规则集版本 {version!r}，当前支持: {supported}。请升级 fuscan 或降级规则集格式。")

    # 兼容旧规则文件：ignore_extensions 字段已弃用，由顶层 scan_extensions 取代。
    # 旧字段被静默忽略（仅 debug 日志），不再影响实际行为。
    ignore_ext_raw = data.get("ignore_extensions", [])
    if ignore_ext_raw:
        logger.debug("规则文件中 ignore_extensions 已弃用，请改用 scan_extensions")

    ignore_paths_raw = data.get("ignore_paths", [])
    ignore_paths = _as_str_tuple(ignore_paths_raw, field="ignore_paths")

    # 顶层 ignore_dirs：目录名级忽略（任意层级、大小写不敏感），与 Config.ignore_dirs 同语义。
    # 保留原值（不强制小写），由 Scanner/FileWalker 在匹配时统一大小写处理。
    ignore_dirs_raw = data.get("ignore_dirs", [])
    ignore_dirs = _as_str_tuple(ignore_dirs_raw, field="ignore_dirs")

    # 顶层 scan_extensions：文件后缀白名单。
    # None（字段未出现）= 全选默认；空列表 = 都不扫描；非空 = 仅扫描指定后缀。
    scan_extensions_raw = data.get("scan_extensions")
    if scan_extensions_raw is None:
        scan_extensions: tuple[str, ...] | None = None
    else:
        scan_extensions = _as_str_tuple(scan_extensions_raw, field="scan_extensions", strip_dot=True)

    # 顶层 scan_params：扫描参数（线程/深度/大文件阈值/压缩包/缓存/性能日志）
    scan_params = _parse_scan_params(data.get("scan_params"))

    # 顶层 whitelist：误报白名单条目列表
    whitelist = _parse_whitelist(data.get("whitelist"))

    rules_raw = data.get("rules", [])
    if not isinstance(rules_raw, Sequence) or isinstance(rules_raw, (str, bytes)):
        raise RuleParseError("rules 必须是列表")
    # AST 共享节点去重（仅单次 parse_ruleset 生命周期，避免长期内存占用）
    dedup: dict[int, MatchSpec] = {}
    rules = tuple(parse_rule(item, dedup=dedup) for item in rules_raw)

    return RuleSet(
        version=version,
        rules=rules,
        ignore_paths=ignore_paths,
        ignore_dirs=ignore_dirs,
        scan_extensions=scan_extensions,
        scan_params=scan_params,
        whitelist=whitelist,
    )


def _parse_scan_params(data: Any) -> ScanParams | None:
    """解析顶层 ``scan_params`` 段为 :class:`ScanParams`。

    :param data: ``scan_params`` 字段值（字典或 None）
    :return: :class:`ScanParams` 实例；``data`` 为 None 时返回 None（未设置）
    :raises RuleParseError: 字段类型不合法
    """
    if data is None:
        return None
    if not isinstance(data, Mapping):
        raise RuleParseError(f"scan_params 必须是字典，得到 {type(data).__name__}")

    def _as_int(name: str) -> int | None:
        value = data.get(name)
        if value is None:
            return None
        if isinstance(value, bool):  # bool 是 int 的子类，先排除
            raise RuleParseError(f"scan_params.{name} 必须是整数，得到 bool")
        if not isinstance(value, int):
            raise RuleParseError(f"scan_params.{name} 必须是整数，得到 {type(value).__name__}")
        return value

    def _as_bool(name: str) -> bool | None:
        value = data.get(name)
        if value is None:
            return None
        if not isinstance(value, bool):
            raise RuleParseError(f"scan_params.{name} 必须是布尔，得到 {type(value).__name__}")
        return value

    return ScanParams(
        max_workers=_as_int("max_workers"),
        max_depth=_as_int("max_depth"),
        max_file_size=_as_int("max_file_size"),
        scan_archives=_as_bool("scan_archives"),
        cache_enabled=_as_bool("cache_enabled"),
        perf_log_enabled=_as_bool("perf_log_enabled"),
    )


def _parse_whitelist(data: Any) -> tuple[WhitelistEntry, ...]:
    """解析顶层 ``whitelist`` 段为 :class:`WhitelistEntry` 元组。

    每项为字典，含 ``path_glob`` / ``rule_name`` / ``created_at`` / ``note``
    / ``source`` 字段。``source`` 缺省为 ``"rules"``（来自规则文件预定义）。

    :param data: ``whitelist`` 字段值（列表或 None）
    :return: :class:`WhitelistEntry` 元组；空或 None 返回空元组
    :raises RuleParseError: 数据结构不合法或条目缺少必填字段
    """
    if data is None:
        return ()
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        raise RuleParseError("whitelist 必须是列表")
    entries: list[WhitelistEntry] = []
    for idx, item in enumerate(data):
        if not isinstance(item, Mapping):
            raise RuleParseError(f"whitelist[{idx}] 必须是字典，得到 {type(item).__name__}")
        path_glob = item.get("path_glob")
        if not path_glob or not isinstance(path_glob, str):
            raise RuleParseError(f"whitelist[{idx}] 缺少 path_glob 字段或类型不合法")
        rule_name = item.get("rule_name", "*")
        if not isinstance(rule_name, str):
            raise RuleParseError(f"whitelist[{idx}].rule_name 必须是字符串")
        created_at = item.get("created_at", "")
        if not isinstance(created_at, str):
            raise RuleParseError(f"whitelist[{idx}].created_at 必须是字符串")
        note = item.get("note", "")
        if not isinstance(note, str):
            raise RuleParseError(f"whitelist[{idx}].note 必须是字符串")
        source = item.get("source", "rules")
        if not isinstance(source, str) or source not in ("rules", "runtime"):
            raise RuleParseError(f"whitelist[{idx}].source 必须是 'rules' 或 'runtime'")
        try:
            entries.append(
                WhitelistEntry(
                    path_glob=path_glob,
                    rule_name=rule_name,
                    created_at=created_at,
                    note=note,
                    source=source,
                )
            )
        except ValueError as exc:
            raise RuleParseError(f"whitelist[{idx}] 无效: {exc}") from exc
    return tuple(entries)


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
