"""扫描后处理子包：命中内容替换、跳过路径持久化、暂存/备份目录探测。

集中托管扫描完成后的用户操作相关逻辑：

- :mod:`fuscan.processing.replacer`：命中内容替换（备份 + 替换 + 撤销）
- :mod:`fuscan.processing.skip_store`：用户跳过路径 JSON 持久化
- :mod:`fuscan.processing.storage`：暂存区/备份区目录探测

公共 API：

- :class:`ReplaceResult` / :class:`BatchReplaceResult` / :class:`ReplaceStatus`：
  替换操作结果与状态（见 :mod:`fuscan.processing.replacer`）
- :func:`replace_in_file` / :func:`replace_batch` / :func:`restore_from_backup`：
  替换操作入口
- :class:`SkipStore` / :func:`default_skip_store_path`：跳过路径存储
- :func:`detect_default_staging_dir` / :func:`default_backup_dir`：目录探测
"""

from __future__ import annotations

from fuscan.processing.replacer import (
    BatchReplaceResult,
    ReplaceResult,
    ReplaceStatus,
    is_text_file,
    replace_batch,
    replace_in_file,
    restore_from_backup,
)
from fuscan.processing.skip_store import SkipStore, default_skip_store_path
from fuscan.processing.storage import default_backup_dir, detect_default_staging_dir

__all__ = [
    "BatchReplaceResult",
    "ReplaceResult",
    "ReplaceStatus",
    "SkipStore",
    "default_backup_dir",
    "default_skip_store_path",
    "detect_default_staging_dir",
    "is_text_file",
    "replace_batch",
    "replace_in_file",
    "restore_from_backup",
]
