"""规则集合合并：将多个规则集按顺序合并。

合并语义：

- ``rules``：后一个规则集中同名规则覆盖前一个，未被覆盖的保留
- ``ignore_paths``：取并集（去重保序）
- ``ignore_dirs``：取并集（去重保序）
- ``scan_extensions``：后者非 ``None`` 覆盖前者（``None`` 表未设置，
  由内置规则集提供默认；空 tuple 表都不扫描；非空 tuple 表白名单）
- ``scan_params``：字段级覆盖（后者非 ``None`` 字段覆盖前者）
- ``whitelist``：取并集（按 ``path_glob`` + ``rule_name`` 去重保序）
- ``version``：采用最后一个规则集的版本号
"""

from __future__ import annotations

from fuscan.rules.model import Rule, RuleSet, ScanParams
from fuscan.rules.whitelist import WhitelistEntry

__all__ = ["merge_multiple_rulesets", "merge_rulesets"]


def merge_rulesets(base: RuleSet, override: RuleSet) -> RuleSet:
    """将 override 规则集合并到 base 之上，override 中同名规则覆盖 base。

    :param base: 基础规则集（如内置通用规则）
    :param override: 覆盖规则集（如用户自定义规则）
    :return: 合并后的新 RuleSet
    """
    override_names = {r.name for r in override.rules}

    # 保留 base 中未被覆盖的规则，再追加 override 的全部规则
    merged_rules: list[Rule] = [r for r in base.rules if r.name not in override_names]
    merged_rules.extend(override.rules)

    return RuleSet(
        version=override.version,
        rules=tuple(merged_rules),
        ignore_paths=_union(base.ignore_paths, override.ignore_paths),
        ignore_dirs=_union(base.ignore_dirs, override.ignore_dirs),
        scan_extensions=_merge_scan_extensions(base.scan_extensions, override.scan_extensions),
        scan_params=_merge_scan_params(base.scan_params, override.scan_params),
        whitelist=_merge_whitelist(base.whitelist, override.whitelist),
    )


def merge_multiple_rulesets(*rulesets: RuleSet) -> RuleSet:
    """按顺序合并多个规则集，后面的覆盖前面的同名规则。

    传入的第一个规则集作为基础，后续每个规则集依次合并覆盖。
    若无参数，返回空规则集。

    :param rulesets: 按优先级从低到高排列的规则集
    :return: 合并后的 RuleSet
    """
    if not rulesets:
        return RuleSet(version="1.0")
    merged = rulesets[0]
    for rs in rulesets[1:]:
        merged = merge_rulesets(merged, rs)
    return merged


def _union(*tuples: tuple[str, ...]) -> tuple[str, ...]:
    """合并多个元组，去重并保持插入顺序。"""
    seen: dict[str, None] = {}
    for t in tuples:
        for item in t:
            if item not in seen:
                seen[item] = None
    return tuple(seen.keys())


def _merge_scan_extensions(
    base: tuple[str, ...] | None,
    override: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    """合并 scan_extensions：后者非 None 覆盖前者。

    - 两者都 None → None（全选默认）
    - 后者非 None → 直接返回后者（含空 tuple，空表都不扫描）
    - 后者 None、前者非 None → 返回前者
    """
    if override is not None:
        return override
    return base


def _merge_scan_params(base: ScanParams | None, override: ScanParams | None) -> ScanParams | None:
    """合并 scan_params：字段级覆盖（后者非 None 字段覆盖前者）。

    - 两者都 None → None
    - 后者 None、前者非 None → 返回前者
    - 后者非 None → 逐字段取后者非 None 值，回退前者
    """
    if override is None:
        return base
    if base is None:
        return override
    return ScanParams(
        max_workers=override.max_workers if override.max_workers is not None else base.max_workers,
        max_depth=override.max_depth if override.max_depth is not None else base.max_depth,
        max_file_size=override.max_file_size if override.max_file_size is not None else base.max_file_size,
        scan_archives=override.scan_archives if override.scan_archives is not None else base.scan_archives,
        cache_enabled=override.cache_enabled if override.cache_enabled is not None else base.cache_enabled,
        perf_log_enabled=override.perf_log_enabled if override.perf_log_enabled is not None else base.perf_log_enabled,
    )


def _merge_whitelist(
    base: tuple[WhitelistEntry, ...],
    override: tuple[WhitelistEntry, ...],
) -> tuple[WhitelistEntry, ...]:
    """合并 whitelist：取并集（按 path_glob + rule_name 去重保序）。

    override 中的条目优先级高（相同 key 时覆盖 base 的同 key 条目，
    以便用户规则文件中的备注/创建时间能覆盖内置预定义）。
    """
    seen: dict[tuple[str, str], WhitelistEntry] = {}
    for entry in base:
        key = (entry.path_glob, entry.rule_name)
        if key not in seen:
            seen[key] = entry
    for entry in override:
        # override 覆盖 base 的同 key 条目
        seen[(entry.path_glob, entry.rule_name)] = entry
    return tuple(seen.values())
