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

    :param scan_mode_index: 扫描模式索引（0=drive / 1=folder / 2=file）
    :param selected_drive: 选中的盘符（drive 模式用）
    :param folder_root: 文件夹或文件根路径（folder/file 模式共用此字段）
    :return: 可构建返回 ``True``，否则 ``False``
    """
    if scan_mode_index == 0:  # drive
        return bool(selected_drive)
    return bool(folder_root)  # folder / file


def build_scan_roots(
    scan_mode_index: int,
    selected_drive: str,
    folder_root: str,
    config: Config,  # noqa: ARG001 保留签名兼容，当前无 full 模式不需要 config
) -> list[Path]:
    """构建扫描根路径列表。

    ``file`` 模式与 ``folder`` 模式共用 ``folder_root`` 字段——单文件场景下
    :class:`fuscan.scanner.walker.FileWalker.walk` 已支持根路径为文件（直接 yield
    单个 ``FileEntry``），故此处统一返回 ``[Path(folder_root)]``，由 walker/scanner
    在扫描时区分文件与目录。

    :param scan_mode_index: 扫描模式索引（0=drive / 1=folder / 2=file）
    :param selected_drive: 选中的盘符（drive 模式用）
    :param folder_root: 文件夹或文件根路径（folder/file 模式共用此字段）
    :param config: 全局配置（保留参数以兼容既有调用签名）
    :return: 扫描根路径列表，空列表表示无有效根
    """
    if scan_mode_index == 0:  # drive
        return [Path(selected_drive)] if selected_drive else []
    # folder / file：单文件根由 walker 内部 root.is_file() 分支处理
    return [Path(folder_root)] if folder_root else []
