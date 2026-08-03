"""压缩包扫描阶段：archive 文件级别并行扫描逻辑。

从 :class:`fuscan.scanner.scanner.Scanner` 抽离的 archive phase 子流程，
封装"按 archive 文件级别并行 + 单 archive 内条目顺序执行"的扫描逻辑。
本模块仅依赖 :class:`Scanner` 的运行时状态（``_check_control``/``_emit_progress``/
``_archive_scanner`` 等），通过将 Scanner 实例作为参数传入访问。

公共 API：

- :func:`run_archive_phase`：执行 archive 阶段扫描，返回增量统计
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from fuscan.scanner._executor import DaemonThreadPoolExecutor
from fuscan.scanner._helpers import cancel_all_futures
from fuscan.scanner.result import ScanResult

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fuscan.scanner.context import FileEntry
    from fuscan.scanner.scanner import Scanner

__all__ = ["run_archive_phase"]

logger = logging.getLogger(__name__)


def _accumulate_archive_results(
    scanner: Scanner,
    archive_results: tuple[ScanResult, ...],
    results: list[ScanResult],
) -> tuple[int, int, int, int]:
    """累积单个 archive 的扫描结果到 results，返回 (scanned, matched, errors, matches) 增量。

    命中结果同步收集到 ``scanner._matched_files`` 供进度回调上报。单线程与多线程
    archive 路径共用此方法，避免结果累积逻辑重复。
    """
    scanned = 0
    matched = 0
    errors = 0
    matches = 0
    for ar in archive_results:
        scanned += 1
        if ar.has_hit:
            matched += 1
            matches += ar.total_match_count
            if scanner._on_progress is not None:
                for hit in ar.hits:
                    scanner._matched_files.append((str(ar.path), hit.rule_name))
        errors += ar.errors
        results.append(ar)
    return scanned, matched, errors, matches


def _emit_archive_progress(
    scanner: Scanner,
    entry: FileEntry,
    scanned: int,
    matched: int,
    errors: int,
    matches: int,
) -> None:
    """发射 archive 阶段进度回调（累计值 base + delta）。"""
    scanner._emit_progress(
        str(entry.path),
        scanner._base_scanned + scanned,
        scanner._base_matched + matched,
        scanner._base_errors + errors,
        scanner._base_matches + matches,
        phase="archive",
    )


def _collect_archive_futures(
    scanner: Scanner,
    future_to_entry: dict[Future[tuple[ScanResult, ...]], FileEntry],
    results: list[ScanResult],
    pool: ThreadPoolExecutor,
) -> tuple[int, int, int, int]:
    """阻塞收集压缩包扫描 future 结果，返回 ``(scanned, matched, errors, matches)`` 增量。

    取消时对剩余未启动 future 调 ``cancel()`` 并 ``shutdown(wait=False)`` 立即返回，
    避免大型压缩包卡住时 as_completed 无限阻塞。进度回调使用累计值（base + delta），
    按 archive 完成顺序触发。
    """
    scanned = matched = errors = matches = 0
    for future in as_completed(future_to_entry):
        if scanner._check_control():
            cancel_all_futures(future_to_entry)
            pool.shutdown(wait=False)
            break
        entry = future_to_entry[future]
        try:
            archive_results = future.result()
        except Exception:
            errors += 1
            logger.warning("压缩包扫描失败 %s", entry.path, exc_info=True)
            _emit_archive_progress(scanner, entry, scanned, matched, errors, matches)
            continue
        d_scanned, d_matched, d_errors, d_matches = _accumulate_archive_results(scanner, archive_results, results)
        scanned += d_scanned
        matched += d_matched
        errors += d_errors
        matches += d_matches
        _emit_archive_progress(scanner, entry, scanned, matched, errors, matches)
    return scanned, matched, errors, matches


def run_archive_phase(
    scanner: Scanner,
    entries: Iterable[FileEntry],
    results: list[ScanResult],
) -> tuple[int, int, int, int]:
    """扫描压缩包内条目，返回 (scanned, matched, errors, matches) 增量。

    archive 文件级别并行（P3）：``max_workers > 1`` 时不同 archive
    文件用线程池并行扫描，单个 archive 内条目仍顺序执行（避免 reader
    共享导致的线程安全问题）。每个 archive 在 worker 内创建独立 reader，
    ArchiveScanner 自身状态（``_compiled`` 等）只读，CacheStore 内部
    用 RLock 串行化，跨 archive 并发安全。

    进度回调使用累计值（base + delta），按 archive 完成顺序触发。

    :param scanner: 所属 Scanner 实例（提供控制状态、进度回调、base 累计值）
    :param entries: 待扫描的文件清单（含非 archive 文件，内部按 is_archive 过滤）
    :param results: 累积结果列表（本函数将 archive 扫描结果追加到此列表）
    :return: ``(scanned, matched, errors, matches)`` 增量统计
    """
    from fuscan.archive import is_archive

    archive_entries = [e for e in entries if is_archive(e.path)]
    if not archive_entries:
        return 0, 0, 0, 0

    scanned = 0
    matched = 0
    errors = 0
    matches = 0

    if not (scanner._max_workers and scanner._max_workers > 1):
        # 单线程退化：顺序扫描
        for entry in archive_entries:
            if scanner._check_control():
                break
            try:
                archive_results = scanner._archive_scanner.scan_archive(entry.path)  # type: ignore[union-attr]
            except Exception:
                errors += 1
                logger.warning("压缩包扫描失败 %s", entry.path, exc_info=True)
                continue
            d_scanned, d_matched, d_errors, d_matches = _accumulate_archive_results(scanner, archive_results, results)
            scanned += d_scanned
            matched += d_matched
            errors += d_errors
            matches += d_matches
            _emit_archive_progress(scanner, entry, scanned, matched, errors, matches)
        return scanned, matched, errors, matches

    # 多线程：archive 文件级别并行
    # 不使用 with 语句：取消时 shutdown(wait=False) 立即返回，避免大型压缩包
    # list_entries() 卡住时 with 退出无限阻塞。已运行 worker 在后台完成。
    # 使用 DaemonThreadPoolExecutor，finally 中 shutdown(wait=False)
    # 不阻塞主线程，避免取消路径下 worker 卡在慢 I/O 时 ScanWorker.run 不返回。
    future_to_entry: dict[Future[tuple[ScanResult, ...]], FileEntry] = {}
    pool = DaemonThreadPoolExecutor(max_workers=scanner._max_workers)
    try:
        cancelled_in_walk = False
        for entry in archive_entries:
            if scanner._check_control():
                cancelled_in_walk = True
                break
            future = pool.submit(scanner._archive_scanner.scan_archive, entry.path)  # type: ignore[union-attr]
            future_to_entry[future] = entry
        if cancelled_in_walk:
            cancel_all_futures(future_to_entry)
            pool.shutdown(wait=False)
            return scanned, matched, errors, matches
        # 阻塞收集剩余 future
        d_scanned, d_matched, d_errors, d_matches = _collect_archive_futures(scanner, future_to_entry, results, pool)
        scanned += d_scanned
        matched += d_matched
        errors += d_errors
        matches += d_matches
    finally:
        # wait=False 不阻塞主线程，daemon worker 由 OS 回收
        pool.shutdown(wait=False)
    return scanned, matched, errors, matches
