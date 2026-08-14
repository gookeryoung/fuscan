"""暂存区与备份区目录探测。

从 :mod:`fuscan.config` 迁入本模块，与 :mod:`fuscan.processing.replacer`（替换时
备份源文件）职责内聚：暂存/备份目录的探测逻辑供替换引擎与 GUI 配置控制器共用。

公共 API：

- :func:`detect_default_staging_dir`：探测剩余空间最大的盘符下 ``.fuscan-cache``
- :func:`default_backup_dir`：返回 ``~/.fuscan/backup`` 默认备份区目录
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fuscan import config as config_module

__all__ = ["default_backup_dir", "detect_default_staging_dir"]

logger = logging.getLogger(__name__)


def detect_default_staging_dir() -> Path:
    """探测默认暂存区目录：剩余空间最大的盘符下 ``.fuscan-cache``。

    遍历本机所有本地盘符（不含网络映射盘），选择 ``shutil.disk_usage().free``
    最大的盘符，返回 ``<drive>/.fuscan-cache``。盘符枚举失败或无可用盘符时
    回退到用户主目录下的 ``~/.fuscan-cache``。

    :return: 默认暂存区目录路径（路径可能尚不存在，调用方按需 ``mkdir``）
    """
    # 延迟导入避免顶层依赖：walker 依赖 scanner.context，与 config 无循环依赖，
    # 但保留惰性导入使本模块在无 scanner 包时仍可独立用于目录探测测试。
    from fuscan.scanner.walker import list_drives

    fallback = Path.home() / ".fuscan-cache"
    try:
        drives = list_drives(include_network=False)
    except OSError:
        logger.warning("盘符枚举失败，暂存区回退到主目录", exc_info=True)
        return fallback
    if not drives:
        return fallback

    best_drive = drives[0]
    best_free = -1
    for drive in drives:
        try:
            free = shutil.disk_usage(drive).free
        except OSError:
            continue
        if free > best_free:
            best_free = free
            best_drive = drive
    return best_drive / ".fuscan-cache"


def default_backup_dir() -> Path:
    """返回默认备份区目录：``~/.fuscan/backup``。

    与暂存区不同，备份区存放的是「替换内容」前的源文件副本（``.bak`` 后缀），
    体量较小且用户事后可手动清理，故无需探测剩余空间最大的盘符，
    直接放在用户主目录下的 ``.fuscan`` 配置目录中，便于统一管理。

    :return: 默认备份区目录路径（路径可能尚不存在，调用方按需 ``mkdir``）
    """
    # 运行时读取 ``config_module.CONFIG_DIR`` 当前值，支持测试 monkeypatch
    return config_module.CONFIG_DIR / "backup"
