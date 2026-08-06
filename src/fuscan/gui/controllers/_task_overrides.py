"""任务级配置覆盖纯函数。

将 :class:`ScanController` 中 ``_effective_*`` 系列方法抽离为模块级纯函数，
便于独立测试与跨模块复用。

覆盖语义分两类：

- 扫描参数（``scan_archives``/``max_workers``/``max_file_size``/``max_depth``/
  ``ignore_dirs``）从 effective :class:`RuleSet` 读取，回退内置默认值。
  ``max_depth=0`` 归一化为 ``None``（无限深度）。这些参数**不再**支持任务级覆盖
  （任务级扫描设置功能已移除），统一由全局规则集决定。
- 规则路径与启用开关（``rules_paths``/``use_builtin``/``temp_rules_paths``/
  ``disabled_temp_rules_paths``）仍支持任务级覆盖：覆盖值优先于 :class:`Config`，
  未设置或类型不符时回退到 Config 对应字段。这些是「配置规则」对话框依赖的
  任务级临时规则机制。

公共 API：

- :func:`effective_scan_archives`：从 ruleset 读取的 scan_archives
- :func:`effective_max_workers`：从 ruleset 读取的 max_workers
- :func:`effective_max_file_size`：从 ruleset 读取的 max_file_size
- :func:`effective_max_depth`：从 ruleset 读取的 max_depth
- :func:`effective_ignore_dirs`：从 ruleset 读取的 ignore_dirs
- :func:`effective_rules_paths`：任务级覆盖优先的 rules_paths
- :func:`effective_use_builtin`：任务级覆盖优先的 use_builtin
- :func:`effective_temp_rules_paths`：任务级临时规则文件路径（叠加在全局规则之上）
- :func:`effective_disabled_temp_rules_paths`：任务级禁用的临时规则文件路径
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fuscan.config import Config
    from fuscan.rules.model import RuleSet

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


def effective_scan_archives(ruleset: RuleSet | None) -> bool:
    """从 effective ruleset 读取的 scan_archives。

    :param ruleset: effective RuleSet（可为 None）
    :return: ruleset.scan_params.scan_archives；None 回退到内置默认 True。
    """
    if ruleset is not None and ruleset.scan_params is not None and ruleset.scan_params.scan_archives is not None:
        return ruleset.scan_params.scan_archives
    return True  # builtin 默认


def effective_max_workers(ruleset: RuleSet | None) -> int:
    """从 effective ruleset 读取的 max_workers。

    :return: ruleset.scan_params.max_workers；None 回退到内置默认 5。
    """
    if ruleset is not None and ruleset.scan_params is not None and ruleset.scan_params.max_workers is not None:
        return ruleset.scan_params.max_workers
    return 5


def effective_max_file_size(ruleset: RuleSet | None) -> int:
    """从 effective ruleset 读取的 max_file_size。

    :return: ruleset.scan_params.max_file_size；None 回退到 :data:`DEFAULT_MAX_FILE_SIZE`。
    """
    from fuscan.config import DEFAULT_MAX_FILE_SIZE

    if ruleset is not None and ruleset.scan_params is not None and ruleset.scan_params.max_file_size is not None:
        return ruleset.scan_params.max_file_size
    return DEFAULT_MAX_FILE_SIZE


def effective_max_depth(ruleset: RuleSet | None) -> int | None:
    """从 effective ruleset 读取的 max_depth（None 表示不限深度）。

    ``0`` 归一化为 ``None``（无限深度），避免 walker 把 ``0`` 误解为「仅根目录直接子项」。
    """
    if ruleset is not None and ruleset.scan_params is not None and ruleset.scan_params.max_depth is not None:
        depth = ruleset.scan_params.max_depth
        return depth if depth > 0 else None
    return None


def effective_ignore_dirs(ruleset: RuleSet | None) -> tuple[str, ...]:
    """从 effective ruleset 读取的 ignore_dirs。

    :return: ruleset.ignore_dirs；ruleset 为 None 时返回空 tuple。
    """
    if ruleset is not None:
        return ruleset.ignore_dirs
    return ()


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
