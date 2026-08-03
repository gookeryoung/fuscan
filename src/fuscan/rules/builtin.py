"""内置规则加载便利函数。

将内置规则文件（``assets/rules/builtin.yaml``）与用户规则按顺序合并的便利函数。
从 :mod:`fuscan.config` 迁入本模块，使规则相关逻辑集中归 :mod:`fuscan.rules` 子包。

公共 API：

- :data:`BUILTIN_RULES_PATH`：内置规则文件路径（从 :mod:`fuscan.paths` 重导出）
- :func:`load_builtin_ruleset`：加载内置规则集（``lru_cache`` 缓存）
- :func:`load_with_builtin`：内置规则 + 用户规则合并
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from fuscan.paths import BUILTIN_RULES_PATH
from fuscan.rules.merge import merge_multiple_rulesets
from fuscan.rules.model import RuleSet
from fuscan.rules.parser import load_ruleset

__all__ = ["BUILTIN_RULES_PATH", "load_builtin_ruleset", "load_with_builtin"]

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_builtin_ruleset() -> RuleSet:
    """加载内置通用规则集。

    内置规则文件 ``builtin.yaml`` 在一次进程内不变，``lru_cache`` 缓存首次
    解析结果，避免启动时被 :func:`load_with_builtin` 重复调用 N+1 次
    （RulesController 1 次 + 每个工作区 N 次）导致的重复磁盘 I/O 与 YAML 解析。

    :return: 内置 RuleSet 实例
    :raises RuleError: 内置规则文件加载或解析失败
    """
    return load_ruleset(BUILTIN_RULES_PATH)


def load_with_builtin(user_paths: Sequence[Path] | None = None) -> RuleSet:
    """加载内置规则并与一个或多个用户规则按顺序合并。

    内置规则作为基础，用户规则按列表顺序依次合并覆盖（后面的覆盖前面的同名规则）。
    ignore_paths 取并集。
    若 ``user_paths`` 为 None 或空，仅返回内置规则集。

    :param user_paths: 用户规则文件路径列表（按优先级从低到高排列）
    :return: 合并后的 RuleSet
    :raises RuleError: 规则文件加载或解析失败
    """
    builtin = load_builtin_ruleset()
    if not user_paths:
        logger.debug("仅加载内置规则集")
        return builtin

    user_rulesets = [load_ruleset(p) for p in user_paths]
    logger.debug("合并规则: 内置 %d 条 + 用户 %d 个文件", len(builtin.rules), len(user_rulesets))
    return merge_multiple_rulesets(builtin, *user_rulesets)
