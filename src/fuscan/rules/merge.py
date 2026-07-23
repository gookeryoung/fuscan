"""规则集合合并：将多个规则集按顺序合并。

合并语义：

- ``rules``：后一个规则集中同名规则覆盖前一个，未被覆盖的保留
- ``ignore_paths``：取并集（去重保序）
- ``version``：采用最后一个规则集的版本号

``ignore_dirs`` 已迁移至全局 :class:`~fuscan.config.Config`，``ignore_extensions``
已由全局文件类型白名单（``Config.scan_extensions``）替代，均不在规则集合并中处理。
"""

from __future__ import annotations

from fuscan.rules.model import Rule, RuleSet

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
