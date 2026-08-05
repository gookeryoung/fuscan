"""筛选阶段：对 walk 产物二次过滤的子流程。

从 :class:`fuscan.scanner.scanner.Scanner` 抽离的 filter phase 主体逻辑，
封装"对预收集的 entries 按可扫描性二次筛选"的过程。介于 collect 与 scan
之间，剔除本不应进入扫描队列的条目，使 scan 阶段分母准确、进度反馈更真实。

四类剔除原因（互斥，按 empty → oversize → unreadable → symlink 顺序判断）：

- **empty**：``entry.size == 0``——CONTENT 规则无文本可匹配，FILENAME/PATH
  规则在 walk 阶段已评估（不进入 entries），故空文件扫描无意义
- **oversize**：``max_file_size > 0 and entry.size > max_file_size``——避免
  一次性读入大文件导致内存卡死（原散落在 ``_scan_entry_uncached`` 内的跳过逻辑
  前移至此，使被剔除文件数可统计、可展示）
- **unreadable**：``os.access(entry.path, os.R_OK) == False``——避免 scan
  阶段抛 OSError（Windows 上基本为 0，Unix 真实权限检查）
- **symlink**：``follow_symlinks=False`` 且 ``entry.path.is_symlink()``——避免
  重复扫描链接目标（链接目标若也在 walk 范围内会被独立扫描，符号链接本身
  会产生重复结果）

本模块仅依赖 :class:`Scanner` 的运行时状态（``_max_file_size``/
``_walker._follow_symlinks``/``_emit_progress``/``_check_control``），
通过将 Scanner 实例作为参数传入访问，与 :mod:`fuscan.scanner._pipeline_phase`
/:mod:`fuscan.scanner._archive_phase` 抽离模式一致。

公共 API：

- :func:`run_filter_phase`：对 WalkResult 执行筛选，返回带 ``filtered_entries``
  与 ``filter_stats`` 的新 WalkResult（原对象不可变，返回新实例）
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from fuscan.archive.base import is_archive
from fuscan.scanner.context import FileEntry
from fuscan.scanner.result import FilterStats, WalkResult

if TYPE_CHECKING:
    from fuscan.scanner.scanner import Scanner

__all__ = ["run_filter_phase"]

logger = logging.getLogger(__name__)

# filter 阶段进度 emit 频率：每处理 N 个文件 emit 一次。
# filter 是快速阶段（仅 size 比较 + os.access 系统调用），emit 过频会增加主线程
# 回调开销；过疏则用户感知不到进度。200 与 walk 阶段的 emit 频率对齐。
_FILTER_EMIT_INTERVAL: int = 200


def run_filter_phase(scanner: Scanner, walk_result: WalkResult) -> WalkResult:
    """对 ``walk_result.entries`` 二次筛选，返回带 ``filtered_entries`` 的新 WalkResult。

    按 empty → oversize → unreadable → symlink 顺序判断（同一文件仅归入首个
    命中的类别），剔除的文件计入 :class:`FilterStats` 对应字段。筛选过程中
    每 :data:`_FILTER_EMIT_INTERVAL` 个文件 emit 一次 ``phase="filter"`` 进度，
    结束时再 emit 一次最终统计。

    取消时（``scanner._check_control`` 返回 True）立即 break，已筛选结果保留，
    返回的 WalkResult ``cancelled`` 字段沿用原值。

    :param scanner: 所属 Scanner 实例（提供 max_file_size、follow_symlinks、
        控制状态、进度回调入口）
    :param walk_result: walk 阶段产物
    :return: 新 WalkResult，``entries`` 字段保留原值（向后兼容），``filtered_entries``
        为筛选后清单，``filter_stats`` 为剔除明细；原 ``cancelled`` 等字段透传
    """
    # 缓存 Scanner 状态到局部变量，避免循环内重复属性访问
    max_file_size = scanner._max_file_size
    follow_symlinks = scanner._walker._follow_symlinks
    # 启用 scan_archives 时，压缩包文件作为容器交由 ArchiveScanner 处理，
    # 不参与 oversize 判断（ArchiveScanner 内部按 max_entry_size 过滤条目）。
    # 否则压缩包自身会被 max_file_size 误剔除，archive 阶段无包可扫。
    scan_archives = scanner._scan_archives

    removed_empty = 0
    removed_oversize = 0
    removed_unreadable = 0
    removed_symlink = 0
    filtered: list[FileEntry] = []
    processed = 0
    cancelled = walk_result.cancelled

    # 取消时跳过筛选，直接返回原 WalkResult 的 filter_stats=None（保持向后兼容）
    if not cancelled:
        for entry in walk_result.entries:
            # 取消检查：filter 是快速阶段，每条都检查开销可接受
            if scanner._check_control():
                cancelled = True
                break
            processed += 1
            # 顺序判断：empty → oversize → unreadable → symlink（互斥）
            if entry.size == 0:
                removed_empty += 1
            elif (
                max_file_size > 0
                and entry.size > max_file_size
                # 压缩包文件跳过 oversize 判断：作为容器由 ArchiveScanner 处理，
                # 不一次性读入内存；其内部条目由 max_entry_size 单独过滤
                and not (scan_archives and is_archive(entry.path))
            ):
                removed_oversize += 1
            else:
                # os.access 在 Windows 上始终返回 True（无真实权限模型），
                # 仅 Unix 系统会做权限检查；此处统一调用以保持跨平台一致语义
                try:
                    if not os.access(entry.path, os.R_OK):
                        removed_unreadable += 1
                        continue
                except OSError:
                    # 路径无效或符号链接断裂：视为不可读
                    removed_unreadable += 1
                    continue
                if not follow_symlinks and entry.path.is_symlink():
                    removed_symlink += 1
                else:
                    filtered.append(entry)
            # 每 N 个文件 emit 一次进度，让用户感知筛选阶段仍在推进
            if processed % _FILTER_EMIT_INTERVAL == 0:
                _emit_filter_progress(
                    scanner,
                    processed=processed,
                    total=len(walk_result.entries),
                    removed_empty=removed_empty,
                    removed_oversize=removed_oversize,
                    removed_unreadable=removed_unreadable,
                    removed_symlink=removed_symlink,
                )

    filter_stats = FilterStats(
        removed_empty=removed_empty,
        removed_oversize=removed_oversize,
        removed_unreadable=removed_unreadable,
        removed_symlink=removed_symlink,
    )

    # 结束时强制 emit 一次最终统计（force=True 绕过节流）
    if not walk_result.cancelled and scanner._on_progress is not None:
        _emit_filter_progress(
            scanner,
            processed=processed,
            total=len(walk_result.entries),
            removed_empty=removed_empty,
            removed_oversize=removed_oversize,
            removed_unreadable=removed_unreadable,
            removed_symlink=removed_symlink,
            force=True,
        )

    # 返回新 WalkResult 实例：entries 保留原值（向后兼容，部分调用方可能仍读 entries），
    # filtered_entries 为筛选后清单，filter_stats 为剔除明细
    return WalkResult(
        root=walk_result.root,
        entries=walk_result.entries,
        total=walk_result.total,
        skipped=walk_result.skipped,
        user_skipped=walk_result.user_skipped,
        skipped_dirs=walk_result.skipped_dirs,
        cancelled=cancelled,
        unchanged_count=walk_result.unchanged_count,
        manifest=walk_result.manifest,
        filtered_entries=tuple(filtered),
        filter_stats=filter_stats,
    )


def _emit_filter_progress(
    scanner: Scanner,
    *,
    processed: int,
    total: int,
    removed_empty: int,
    removed_oversize: int,
    removed_unreadable: int,
    removed_symlink: int,
    force: bool = False,
) -> None:
    """发射 filter 阶段进度回调。

    复用 ``scanner._emit_progress`` 但填充 filter 专属字段。``scanned`` 字段
    复用为「已处理文件数」（filter 阶段无"扫描"语义），``total`` 为待筛选
    文件总数（walk_result.entries 长度），其余字段恒为 0（filter 阶段无
    matched/errors/matches 概念）。

    :param scanner: 所属 Scanner 实例
    :param processed: 已处理的 entries 数（含剔除的）
    :param total: 待筛选 entries 总数
    :param removed_empty/oversize/unreadable/symlink: 四类剔除原因累计数
    :param force: 是否绕过节流强制发送（最终统计时为 True）
    """
    if scanner._on_progress is None:
        return
    # 直接调用 _emit_progress 让其处理节流；filter 专属字段通过 _emit_progress
    # 的 filter_stats 参数注入（scanner._emit_progress 已扩展支持）
    scanner._emit_progress(
        "",
        scanned=processed,
        matched=0,
        errors=0,
        matches=0,
        force=force,
        phase="filter",
        filter_stats=FilterStats(
            removed_empty=removed_empty,
            removed_oversize=removed_oversize,
            removed_unreadable=removed_unreadable,
            removed_symlink=removed_symlink,
        ),
        filter_total=total,
    )
