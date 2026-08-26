"""扫描后台线程：避免阻塞 UI。

ScanWorker 在独立 QThread 中运行 Scanner.scan，通过信号通知 UI
进度、完成与错误。支持多根路径扫描（如全盘扫描时扫描多个盘符），
完成后合并为单一 ScanReport。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from PySide2.QtCore import QObject, QThread, Signal

from fuscan.perf import PerfStats
from fuscan.rules.model import RuleSet
from fuscan.rules.whitelist import Whitelist
from fuscan.scanner import ScanReport
from fuscan.scanner.result import ProgressInfo, ScanResult, ScanStats, WalkResult
from fuscan.scanner.scanner import Scanner

if TYPE_CHECKING:
    from fuscan.cache import CacheStore

__all__ = ["ScanWorker"]

logger = logging.getLogger(__name__)


class ScanWorker(QThread):  # pyrefly: ignore [invalid-inheritance]
    """后台扫描线程。

    信号：

    - ``progress_info``：实时进度信息（ProgressInfo，含当前文件、已扫描/跳过/命中数等）
    - ``finished_report``：扫描完成，携带合并后的 ScanReport
    - ``failed``：扫描异常，携带错误信息
    - ``cancelled``：扫描被用户取消，携带已扫描的部分结果
    """

    progress_info = Signal(object)
    finished_report = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(
        self,
        ruleset: RuleSet,
        roots: list[Path],
        max_depth: int | None = None,
        scan_archives: bool = False,
        max_workers: int | None = None,
        max_file_size: int | None = None,
        ignore_dirs: tuple[str, ...] = (),
        cache: CacheStore | None = None,
        source_files: Mapping[Path, str] | None = None,
        progress_interval: float = 0.1,
        scan_extensions: tuple[str, ...] | None = None,
        skip_paths: frozenset[str] | None = None,
        precollected: list[WalkResult] | None = None,
        prev_report: ScanReport | None = None,
        whitelist: Whitelist | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ruleset = ruleset
        self._roots = roots
        self._max_depth = max_depth
        self._scan_archives = scan_archives
        self._max_workers = max_workers
        self._max_file_size = max_file_size
        self._ignore_dirs = ignore_dirs
        self._cache: CacheStore | None = cache
        self._source_files: Mapping[Path, str] | None = source_files
        self._progress_interval: float = progress_interval
        # 全局后缀白名单：None 表示扫描所有文件；
        # 非空 tuple 按白名单过滤；空 tuple 不扫描任何文件（用户全部取消勾选的边界）
        self._scan_extensions: tuple[str, ...] | None = scan_extensions
        # 用户标记跳过的路径集合：传给 Scanner 在 walk 阶段跳过
        self._skip_paths: frozenset[str] = skip_paths or frozenset()
        # 预收集的 walk 产物（stats/scan worker 分离）：非 None 时 run() 跳过 walk，
        # 直接调 Scanner.scan_entries。由 FileStatsWorker.finished_stats 提供，
        # 与 roots 一一对应（WalkResult.root == roots[i]）
        self._precollected: list[WalkResult] | None = precollected
        # 上次扫描报告，提供未变更文件的命中结果供 Scanner 合并。
        # 与 incremental_manifest 配合启用增量扫描（FileStatsWorker 侧传入 manifest）。
        # ScanWorker 用 precollected 模式调 scan_entries，Scanner 在 __init__ 时
        # 根据 prev_report 预索引未变更命中结果（_unchanged_hits），scan_entries 合并。
        self._prev_report: ScanReport | None = prev_report
        # 误报白名单快照，传给 Scanner 在命中聚合阶段过滤
        self._whitelist: Whitelist | None = whitelist
        self._scanner: Scanner | None = None
        self._cancel_requested: bool = False
        # 多根路径累计性能统计：每次 scan() 后合并 perf_summary
        self._perf: PerfStats = PerfStats()
        # 多根路径累计统计
        self._cum_scanned = 0
        self._cum_total = 0
        self._cum_skipped = 0
        self._cum_matched = 0
        self._cum_errors = 0
        self._cum_matches = 0
        # 多根路径累计用户跳过数
        self._cum_user_skipped = 0
        # 多根路径累计压缩包内条目数
        self._cum_archive_entries = 0
        # 多根路径累计 filter 阶段剔除文件数（empty/oversize/unreadable/symlink 之和）
        self._cum_filter_removed = 0
        self._start_time: float = 0.0

    def pause(self) -> None:
        """暂停扫描。"""
        if self._scanner is not None:
            self._scanner.pause()

    def start(self, priority: QThread.Priority = QThread.LowPriority) -> None:
        """以低优先级启动线程，缓解与 GUI 主线程的 GIL 争抢。

        扫描 worker 内嵌套多个纯 Python CPU 密集任务（正则匹配、
        olefile/email 回退路径），这些任务持 GIL 期间会与 GUI 主线程争抢同一把锁。
        默认 ``QThread.LowPriority`` 让 OS 调度天平偏向主线程，主线程更易抢到
        GIL 处理绘制/输入，显著改善解析大文档时的界面卡滞。
        """
        super().start(priority)

    def resume(self) -> None:
        """恢复扫描。"""
        if self._scanner is not None:
            self._scanner.resume()

    def cancel(self) -> None:
        """取消扫描，即使 Scanner 尚未创建也能生效。"""
        self._cancel_requested = True
        if self._scanner is not None:
            self._scanner.cancel()

    def _on_progress(self, info: ProgressInfo) -> None:
        """Scanner 进度回调：累加前序根路径的统计后 emit。

        filter 阶段四类剔除字段（filter_removed_*）直接透传不累计——
        filter 是单线程顺序阶段，单次 emit 的累计值已包含前序 entries 的剔除数，
        无需 worker 层再叠加。其他阶段（walk/scan/archive）这些字段恒为 0，
        透传也不会引入误差。
        """
        elapsed = time.monotonic() - self._start_time
        self.progress_info.emit(  # pyrefly: ignore [missing-attribute]
            ProgressInfo(
                current_file=info.current_file,
                scanned=info.scanned + self._cum_scanned,
                total=info.total + self._cum_total,
                skipped=info.skipped + self._cum_skipped,
                matched=info.matched + self._cum_matched,
                errors=info.errors + self._cum_errors,
                elapsed=elapsed,
                matches=info.matches + self._cum_matches,
                # skipped_dirs/matched_files 不累计，仅反映最近一次 scan() 的快照
                skipped_dirs=info.skipped_dirs,
                matched_files=info.matched_files,
                phase=info.phase,
                user_skipped=info.user_skipped + self._cum_user_skipped,
                # 单文件元信息透传：size/ext/elapsed_ms 仅反映最近一次 emit 的文件
                current_file_size=info.current_file_size,
                current_file_ext=info.current_file_ext,
                current_file_elapsed_ms=info.current_file_elapsed_ms,
                # filter 阶段四类剔除原因累计数：Scanner 内已累计本次 walk_result 的全部剔除，
                # worker 无需再叠加（与 scanned/matched 的多根累计语义不同）
                filter_removed_empty=info.filter_removed_empty,
                filter_removed_oversize=info.filter_removed_oversize,
                filter_removed_unreadable=info.filter_removed_unreadable,
                filter_removed_symlink=info.filter_removed_symlink,
            )
        )

    def run(self) -> None:
        """线程入口：依次扫描所有根路径并合并结果。

        ``precollected`` 非 None 时跳过 walk 阶段，直接对预收集的
        :class:`WalkResult` 调 :meth:`Scanner.scan_entries`，与
        :class:`FileStatsWorker` 配合实现 stats/scan 职责拆分。

        取消保护——多根路径扫描时每个根之前检查 ``_cancel_requested``，
        避免取消后仍启动下一个根的扫描。单根扫描的取消由 Scanner 内部
        ``_check_control``/``_cancel_event`` 保证（``_scan_entry`` 入口 +
        ``as_completed`` 循环顶部双重检查）。ThreadPool worker 由
        :class:`DaemonThreadPoolExecutor` 提供进程退出保护，``shutdown(wait=False)``
        不阻塞主线程。
        """
        try:
            self._start_time = time.monotonic()
            self._scanner = Scanner(
                ruleset=self._ruleset,
                max_depth=self._max_depth,
                scan_archives=self._scan_archives,
                max_workers=self._max_workers,
                max_file_size=self._max_file_size,
                on_progress=self._on_progress,
                ignore_dirs=self._ignore_dirs,
                cache=self._cache,
                source_files=self._source_files,
                progress_interval=self._progress_interval,
                scan_extensions=self._scan_extensions,
                skip_paths=self._skip_paths,
                prev_report=self._prev_report,
                whitelist=self._whitelist,
            )
            if self._cancel_requested:
                self._scanner.cancel()
            all_results: list[ScanResult] = []
            # 基于 report.cancelled 判断取消状态：C1 修复后 scan()/scan_entries() 在
            # finally 中清除 _cancel_event，返回后 self._scanner.is_cancelled 恒为 False，
            # 必须用 report.cancelled 累积取消标志，否则取消的扫描会被误判为正常完成
            was_cancelled = False

            # precollected 模式：跳过 walk，对每个 wr 先 filter 再 scan_entries；
            # 否则遍历 roots 调 scan（walk + filter + scan 串联，向后兼容）
            if self._precollected is not None:
                reports = (
                    self._scanner.scan_entries(wr.root, self._scanner.filter_entries(wr)) for wr in self._precollected
                )
            else:
                reports = (self._scanner.scan(root) for root in self._roots)

            # 手动迭代 reports 生成器，每根之前检查 _cancel_requested。
            # 原因：scan()/scan_entries() 在 finally 中清除 _cancel_event，
            # 若用 `for report in reports:` 隐式 next()，下一根 scan 时 _cancel_event
            # 已清空会正常执行，导致取消后仍扫描后续根路径。
            reports_iter = iter(reports)
            while not was_cancelled:
                # 取消标志在 next() 之前检查，避免取消后仍启动下一根扫描
                if self._cancel_requested:
                    was_cancelled = True
                    break
                try:
                    report = next(reports_iter)
                except StopIteration:
                    break
                all_results.extend(report.results)
                self._accumulate_report(report)
                if report.cancelled:
                    was_cancelled = True
                    break

            elapsed = time.monotonic() - self._start_time
            merged = ScanReport(
                root=self._roots[0] if len(self._roots) == 1 else Path("（多路径）"),
                results=tuple(all_results),
                stats=ScanStats(
                    total_files=self._cum_total,
                    scanned_files=self._cum_scanned,
                    matched_files=self._cum_matched,
                    skipped_files=self._cum_skipped,
                    errors=self._cum_errors,
                    duration_seconds=elapsed,
                    total_matches=self._cum_matches,
                    user_skipped=self._cum_user_skipped,
                    archive_entries=self._cum_archive_entries,
                    # filter 阶段剔除的文件总数（多根路径累加）
                    filter_removed=self._cum_filter_removed,
                    perf_summary=self._perf.to_dict(),
                ),
                cancelled=was_cancelled,
            )
            if was_cancelled:
                self.cancelled.emit(merged)  # pyrefly: ignore [missing-attribute]
            else:
                self.finished_report.emit(merged)  # pyrefly: ignore [missing-attribute]
        except Exception as exc:
            logger.exception("后台扫描失败")
            self.failed.emit(str(exc))  # pyrefly: ignore [missing-attribute]

    def _accumulate_report(self, report: ScanReport) -> None:
        """累加单个根路径的扫描结果到累计统计字段。

        将 report 的统计合并到 ``self._cum_*`` 与 ``self._perf``，
        供后续根路径的进度回调与最终 ScanReport 使用。
        """
        self._cum_scanned += report.stats.scanned_files
        self._cum_total += report.stats.total_files
        self._cum_matched += report.stats.matched_files
        self._cum_skipped += report.stats.skipped_files
        self._cum_errors += report.stats.errors
        self._cum_matches += report.stats.total_matches
        self._cum_user_skipped += report.stats.user_skipped
        self._cum_archive_entries += report.stats.archive_entries
        self._cum_filter_removed += report.stats.filter_removed
        if report.stats.perf_summary:
            self._perf.merge_dict(report.stats.perf_summary)
