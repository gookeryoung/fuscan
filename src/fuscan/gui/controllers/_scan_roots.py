"""扫描根构建纯函数。

将 :class:`ScanController` 中与扫描根路径构建相关的纯逻辑抽离到模块级，
便于独立测试与复用。``_can_build_roots`` 判断当前选择是否可构建有效根路径，
``_build_scan_roots`` 实际构建根路径列表。

公共 API：

- :func:`can_build_roots`：判断当前是否可构建扫描根路径列表
- :func:`build_scan_roots`：构建扫描根路径列表
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fuscan.config import Config

__all__ = ["build_scan_roots", "can_build_roots"]


def can_build_roots(scan_mode_index: int, selected_drive: str, folder_root: str) -> bool:
    """判断当前是否可构建扫描根路径列表。

    :param scan_mode_index: 扫描模式索引（0=full / 1=drive / 2=folder）
    :param selected_drive: 选中的盘符（drive 模式用）
    :param folder_root: 文件夹根路径（folder 模式用）
    :return: 可构建返回 ``True``，否则 ``False``
    """
    if scan_mode_index == 0:  # full
        return True
    if scan_mode_index == 1:  # drive
        return bool(selected_drive)
    return bool(folder_root)  # folder


def build_scan_roots(
    scan_mode_index: int,
    selected_drive: str,
    folder_root: str,
    config: Config,
) -> list[Path]:
    """构建扫描根路径列表。

    :param scan_mode_index: 扫描模式索引（0=full / 1=drive / 2=folder）
    :param selected_drive: 选中的盘符（drive 模式用）
    :param folder_root: 文件夹根路径（folder 模式用）
    :param config: 全局配置（full 模式用 ``include_network_drives``）
    :return: 扫描根路径列表，空列表表示无有效根
    """
    if scan_mode_index == 0:  # full
        from fuscan.scanner.walker import list_drives

        return list_drives(include_network=config.include_network_drives)
    if scan_mode_index == 1:  # drive
        return [Path(selected_drive)] if selected_drive else []
    # folder
    return [Path(folder_root)] if folder_root else []
