"""扫描后台线程：避免阻塞 UI。

ScanWorker 在独立 QThread 中运行 Scanner.scan，通过信号通知 UI
进度、完成与错误。支持多根路径扫描（如全盘扫描时扫描多个盘符），
完成后合并为单一 ScanReport。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide2.QtCore import QThread, Signal

from uniscan.rules.model import RuleSet
from uniscan.scanner import ScanReport
from uniscan.scanner.result import ProgressInfo, ScanResult, ScanStats
from uniscan.scanner.scanner import Scanner

__all__ = ["ScanWorker"]

logger = logging.getLogger(__name__)


class ScanWorker(QThread):
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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._ruleset = ruleset
        self._roots = roots
        self._max_depth = max_depth
        self._scan_archives = scan_archives
        self._max_workers = max_workers
        self._scanner: Scanner | None = None
        self._cancel_requested: bool = False
        # 多根路径累计统计
        self._cum_scanned = 0
        self._cum_total = 0
        self._cum_skipped = 0
        self._cum_matched = 0
        self._cum_errors = 0
        self._start_time: float = 0.0

    def pause(self) -> None:
        """暂停扫描。"""
        if self._scanner is not None:
            self._scanner.pause()

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
        """Scanner 进度回调：累加前序根路径的统计后 emit。"""
        elapsed = time.monotonic() - self._start_time
        self.progress_info.emit(
            ProgressInfo(
                current_file=info.current_file,
                scanned=info.scanned + self._cum_scanned,
                total=info.total + self._cum_total,
                skipped=info.skipped + self._cum_skipped,
                matched=info.matched + self._cum_matched,
                errors=info.errors + self._cum_errors,
                elapsed=elapsed,
            )
        )

    def run(self) -> None:
        """线程入口：依次扫描所有根路径并合并结果。"""
        try:
            self._start_time = time.monotonic()
            self._scanner = Scanner(
                ruleset=self._ruleset,
                max_depth=self._max_depth,
                scan_archives=self._scan_archives,
                max_workers=self._max_workers,
                on_progress=self._on_progress,
            )
            if self._cancel_requested:
                self._scanner.cancel()
            all_results: list[ScanResult] = []
            total_scanned = 0
            total_files = 0
            total_matched = 0
            total_skipped = 0
            total_errors = 0

            for root in self._roots:
                if self._scanner is not None and self._scanner.is_cancelled:
                    break
                report: ScanReport = self._scanner.scan(root)
                all_results.extend(report.results)
                total_scanned += report.stats.scanned_files
                total_files += report.stats.total_files
                total_matched += report.stats.matched_files
                total_skipped += report.stats.skipped_files
                total_errors += report.stats.errors
                # 更新累计值，供下一个根路径的进度回调使用
                self._cum_scanned = total_scanned
                self._cum_total = total_files
                self._cum_skipped = total_skipped
                self._cum_matched = total_matched
                self._cum_errors = total_errors

            was_cancelled = self._scanner is not None and self._scanner.is_cancelled
            elapsed = time.monotonic() - self._start_time
            merged = ScanReport(
                root=self._roots[0] if len(self._roots) == 1 else Path("（多路径）"),
                results=tuple(all_results),
                stats=ScanStats(
                    total_files=total_files,
                    scanned_files=total_scanned,
                    matched_files=total_matched,
                    skipped_files=total_skipped,
                    errors=total_errors,
                    duration_seconds=elapsed,
                ),
                cancelled=was_cancelled,
            )
            if was_cancelled:
                self.cancelled.emit(merged)
            else:
                self.finished_report.emit(merged)
        except Exception as exc:
            logger.exception("后台扫描失败")
            self.failed.emit(str(exc))
