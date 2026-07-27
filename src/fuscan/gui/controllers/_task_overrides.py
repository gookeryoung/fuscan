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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fuscan.config import Config

__all__ = [
    "effective_ignore_dirs",
    "effective_max_depth",
    "effective_max_file_size",
    "effective_max_workers",
    "effective_scan_archives",
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
