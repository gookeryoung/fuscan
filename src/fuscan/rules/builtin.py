"""内置规则加载便利函数。

将内置规则文件（``assets/rules/builtin.yaml`` + ``assets/rules/builtin-patterns.yaml``）
与用户规则按顺序合并的便利函数。从 :mod:`fuscan.config` 迁入本模块，
使规则相关逻辑集中归 :mod:`fuscan.rules` 子包。

内置规则由两个文件组成，职责分离：

- ``builtin.yaml``：``ignore_paths`` / ``ignore_dirs`` / ``scan_params`` / ``whitelist``
- ``builtin-patterns.yaml``：``scan_extensions`` / ``rules``
  （文件后缀白名单与匹配规则）

加载时两者按顺序合并为单一 :class:`RuleSet`，并按 CPU 核数动态计算
``max_workers``（覆盖 ``scan_params.max_workers=None``）。

公共 API：

- :data:`BUILTIN_RULES_PATH`：内置规则文件路径（从 :mod:`fuscan.paths` 重导出）
- :data:`BUILTIN_PATTERNS_PATH`：内置匹配规则文件路径（从 :mod:`fuscan.paths` 重导出）
- :func:`load_builtin_ruleset`：加载内置规则集（``lru_cache`` 缓存）
- :func:`load_with_builtin`：内置规则 + 用户规则合并
- :func:`recommended_max_workers`：按 CPU 核数计算推荐工作线程数
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from fuscan.paths import BUILTIN_PATTERNS_PATH, BUILTIN_RULES_PATH
from fuscan.rules.merge import merge_multiple_rulesets
from fuscan.rules.model import RuleSet, ScanParams
from fuscan.rules.parser import load_ruleset

__all__ = [
    "BUILTIN_PATTERNS_PATH",
    "BUILTIN_RULES_PATH",
    "load_builtin_ruleset",
    "load_with_builtin",
    "recommended_max_workers",
]

logger = logging.getLogger(__name__)

# 推荐工作线程数计算的硬上下界：
# - 下限 4：小机型仍保证 I/O 并行度，避免过少线程导致 PDF/Excel 解析串行
# - 上限 16：CPU 核数多时不再增加线程，避免缓存争用与内存压力
# - 保留 2 个核心给系统与 GUI（OS 调度、QML 渲染、用户后台任务）
_RECOMMENDED_MIN_WORKERS: int = 4
_RECOMMENDED_MAX_WORKERS: int = 16
_RECOMMENDED_RESERVED_CORES: int = 2


def recommended_max_workers(cpu_count: int | None = None) -> int:
    """按 CPU 核数计算推荐工作线程数。

    推荐最佳实践：留 2 个核心给系统与 GUI（OS 调度、QML 渲染、用户后台任务），
    下限 4、上限 16，避免影响机器正常使用。

    - ``cpu_count`` 为 None 时调用 :func:`os.cpu_count`，仍为 None 时回退到 4
    - 4 核以下机器：返回 4（小机型也保证 I/O 并行度）
    - 6 核机器：返回 4（6 - 2 = 4）
    - 8 核机器：返回 6
    - 16 核及以上机器：返回 16（上限）

    :param cpu_count: CPU 逻辑核心数；None 表示自动探测
    :return: 推荐工作线程数（4~16）
    """
    if cpu_count is None:
        cpu_count = os.cpu_count() or _RECOMMENDED_MIN_WORKERS
    workers = cpu_count - _RECOMMENDED_RESERVED_CORES
    return max(_RECOMMENDED_MIN_WORKERS, min(workers, _RECOMMENDED_MAX_WORKERS))


@lru_cache(maxsize=1)
def load_builtin_ruleset() -> RuleSet:
    """加载内置通用规则集（合并 builtin.yaml + builtin-patterns.yaml）。

    内置规则文件在一次进程内不变，``lru_cache`` 缓存首次解析结果，避免启动时
    被 :func:`load_with_builtin` 重复调用 N+1 次（RulesController 1 次 + 每个工作区
    N 次）导致的重复磁盘 I/O 与 YAML 解析。

    加载步骤：

    1. 加载 ``builtin.yaml``（ignore_paths/ignore_dirs/scan_params/whitelist）
    2. 加载 ``builtin-patterns.yaml``（scan_extensions/rules）并合并
    3. 若合并后 ``scan_params.max_workers`` 为 None，按 CPU 核数动态计算并覆盖

    :return: 内置 RuleSet 实例（``max_workers`` 已填充推荐值）
    :raises RuleError: 内置规则文件加载或解析失败
    """
    base = load_ruleset(BUILTIN_RULES_PATH)
    patterns = load_ruleset(BUILTIN_PATTERNS_PATH)
    merged = merge_multiple_rulesets(base, patterns)

    # max_workers 动态计算：用户未显式设置（None）时按 CPU 核数推荐
    sp = merged.scan_params
    if sp is None or sp.max_workers is None:
        recommended = recommended_max_workers()
        new_sp = ScanParams(
            max_workers=recommended,
            max_depth=sp.max_depth if sp is not None else None,
            max_file_size=sp.max_file_size if sp is not None else None,
            scan_archives=sp.scan_archives if sp is not None else None,
            cache_enabled=sp.cache_enabled if sp is not None else None,
            perf_log_enabled=sp.perf_log_enabled if sp is not None else None,
        )
        from dataclasses import replace

        merged = replace(merged, scan_params=new_sp)
        logger.info("内置规则 max_workers 按机器 CPU 核数自动计算为 %d", recommended)

    return merged


def load_with_builtin(user_paths: Sequence[Path] | None = None) -> RuleSet:
    """加载内置规则并与一个或多个用户规则按顺序合并。

    内置规则作为基础，用户规则按列表顺序依次合并覆盖（后面的覆盖前面的同名规则）。
    ignore_paths 取并集。
    若 ``user_paths`` 为 None 或空，仅返回内置规则集。

    用户规则中 ``scan_params.max_workers`` 非 None 时覆盖内置推荐值，
    为 None 时保留内置推荐值（按 CPU 核数自动计算）。

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
