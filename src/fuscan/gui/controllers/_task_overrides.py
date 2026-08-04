"""任务级配置覆盖纯函数。

将 :class:`ScanController` 中 ``_effective_*`` 系列方法抽离为模块级纯函数，
便于独立测试与跨模块复用。

覆盖语义：任务级覆盖值优先于全局 :class:`Config`，未设置或类型不符时回退到
全局配置。``max_depth=0`` 归一化为 ``None``（无限深度），与
:meth:`ConfigController.setMaxDepth` 语义一致。

公共 API：

- :func:`effective_scan_archives`：任务级覆盖优先的 scan_archives
- :func:`effective_max_workers`：任务级覆盖优先的 max_workers
- :func:`effective_max_file_size`：任务级覆盖优先的 max_file_size
- :func:`effective_max_depth`：任务级覆盖优先的 max_depth
- :func:`effective_ignore_dirs`：任务级覆盖优先的 ignore_dirs
- :func:`effective_rules_paths`：任务级覆盖优先的 rules_paths
- :func:`effective_use_builtin`：任务级覆盖优先的 use_builtin
- :func:`effective_temp_rules_paths`：任务级临时规则文件路径（叠加在全局规则之上）
- :func:`effective_disabled_temp_rules_paths`：任务级禁用的临时规则文件路径
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fuscan.config import Config

__all__ = [
    "effective_disabled_temp_rules_paths",
    "effective_ignore_dirs",
    "effective_max_depth",
    "effective_max_file_size",
    "effective_max_workers",
    "effective_rules_paths",
    "effective_scan_archives",
    "effective_temp_rules_paths",
    "effective_use_builtin",
]


def effective_scan_archives(overrides: dict[str, object], config: Config) -> bool:
    """任务级覆盖优先的 scan_archives。"""
    value = overrides.get("scan_archives")
    if isinstance(value, bool):
        return value
    return config.scan_archives


def effective_max_workers(overrides: dict[str, object], config: Config) -> int:
    """任务级覆盖优先的 max_workers。"""
    value = overrides.get("max_workers")
    if isinstance(value, int):
        return value
    return config.max_workers


def effective_max_file_size(overrides: dict[str, object], config: Config) -> int:
    """任务级覆盖优先的 max_file_size。"""
    value = overrides.get("max_file_size")
    if isinstance(value, int):
        return value
    return config.max_file_size


def effective_max_depth(overrides: dict[str, object], config: Config) -> int | None:
    """任务级覆盖优先的 max_depth（None 表示不限深度）。

    与 :meth:`ConfigController.setMaxDepth` 保持语义一致：``0`` 归一化为
    ``None``（无限深度），避免 walker 把 ``0`` 误解为「仅根目录直接子项」。
    """
    value = overrides.get("max_depth")
    if isinstance(value, int):
        return value if value > 0 else None
    return config.max_depth


def effective_ignore_dirs(overrides: dict[str, object], config: Config) -> tuple[str, ...]:
    """任务级覆盖优先的 ignore_dirs。"""
    value = overrides.get("ignore_dirs")
    if isinstance(value, tuple):
        return value
    return tuple(config.ignore_dirs)


def effective_rules_paths(overrides: dict[str, object], config: Config) -> tuple[str, ...]:
    """任务级覆盖优先的 rules_paths。

    :return: ``tuple[str, ...]``，规则文件路径元组（按配置顺序）。
        任务级覆盖为 tuple 时直接返回；否则回退到 ``config.rules_paths``。
        与 :attr:`RulesController.rules_paths` 不同，此处**不**过滤不存在
        的文件——过滤逻辑由 :meth:`ScanController._effective_ruleset` 在
        加载阶段处理（避免此处与 RulesController 产生重复的 ``Path.exists()``
        调用语义分歧）。
    """
    value = overrides.get("rules_paths")
    if isinstance(value, tuple):
        return value
    return tuple(config.rules_paths)


def effective_use_builtin(overrides: dict[str, object], config: Config) -> bool:
    """任务级覆盖优先的 use_builtin。"""
    value = overrides.get("use_builtin")
    if isinstance(value, bool):
        return value
    return config.use_builtin


def effective_temp_rules_paths(overrides: dict[str, object]) -> tuple[str, ...]:
    """任务级临时规则文件路径（叠加在全局规则之上，不覆盖）。

    与 :func:`effective_rules_paths` 不同，临时规则不是对全局 rules_paths
    的覆盖，而是额外追加的规则文件——扫描时与全局启用的规则文件合并。

    :return: ``tuple[str, ...]``，临时规则文件路径元组。未设置时返回空元组。
        与 :func:`effective_rules_paths` 一致，此处不过滤不存在的文件——
        过滤逻辑由 :meth:`ScanController._compute_effective_ruleset` 处理。
    """
    value = overrides.get("temp_rules_paths")
    if isinstance(value, tuple):
        return value
    return ()


def effective_disabled_temp_rules_paths(overrides: dict[str, object]) -> tuple[str, ...]:
    """任务级禁用的临时规则文件路径（不参与扫描合并）。

    与全局 :attr:`Config.disabled_rules_paths` 同语义，仅作用于当前工作区
    的临时规则——禁用后 :meth:`ScanController._compute_effective_ruleset`
    在合并临时规则时跳过此列表中的路径。临时规则文件仍保留在
    ``temp_rules_paths`` 中以便重新启用。

    :return: ``tuple[str, ...]``，禁用的临时规则文件路径元组。未设置时返回空元组。
    """
    value = overrides.get("disabled_temp_rules_paths")
    if isinstance(value, tuple):
        return value
    return ()
