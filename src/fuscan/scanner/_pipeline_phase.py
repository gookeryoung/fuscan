"""扫描流水线阶段：顺序/并发扫描 entries 的子流程。

从 :class:`fuscan.scanner.scanner.Scanner` 抽离的 scan phase 主体逻辑，
封装"对预收集的 entries 执行内容扫描"的两种执行模式：

- ``max_workers <= 1``：单线程顺序扫描（:func:`_scan_sequential`）
- ``max_workers > 1``：ThreadPoolExecutor 并发扫描（:func:`_scan_concurrent`）

本模块仅依赖 :class:`Scanner` 的运行时状态（``_check_control``/``_emit_progress``/
``_scan_entry``/``_matched_files``/``_on_progress`` 等），通过将 Scanner
实例作为参数传入访问，与 :mod:`fuscan.scanner._archive_phase` 抽离模式一致。

公共 API：

- :func:`run_pipeline_phase`：执行 scan 阶段扫描，按 ``_max_workers`` 分派
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from fuscan.scanner._executor import DaemonThreadPoolExecutor
from fuscan.scanner._helpers import cancel_all_futures
from fuscan.scanner.result import ScanResult

if TYPE_CHECKING:
    from fuscan.scanner.context import FileEntry
    from fuscan.scanner.scanner import Scanner

__all__ = ["run_pipeline_phase"]

logger = logging.getLogger(__name__)


def run_pipeline_phase(
    scanner: Scanner,
    entries: list[FileEntry],
    results: list[ScanResult],
) -> tuple[int, int, int, int]:
    """执行 scan 阶段扫描，返回 ``(scanned, matched, errors, matches)``。

    按 ``scanner._max_workers`` 分派到顺序或并发扫描子流程。
    顺序扫描：单线程逐文件调用 ``scanner._scan_entry``，每文件 emit 进度，
    每 ``_gil_yield_interval`` 个文件 ``sleep(0)`` 让步 GIL。
    并发扫描：ThreadPoolExecutor 一次性提交所有 entries，``as_completed``
    收集结果；含取消加速（取消时 ``cancel()`` 未启动 future 并 ``shutdown(wait=False)``）。

    :param scanner: 所属 Scanner 实例（提供控制状态、进度回调、扫描入口）
    :param entries: 待扫描的文件清单（walk 阶段已按白名单过滤）
    :param results: 共享结果列表，本函数将扫描结果追加到此列表
    :return: ``(scanned, matched, errors, matches)`` 统计
    """
    if scanner._max_workers and scanner._max_workers > 1:
        return _scan_concurrent(scanner, entries, results)
    return _scan_sequential(scanner, entries, results)


def _scan_sequential(
    scanner: Scanner,
    entries: list[FileEntry],
    results: list[ScanResult],
) -> tuple[int, int, int, int]:
    """单线程顺序扫描，返回 (scanned, matched, errors, matches)。

    每文件调用 ``scanner._scan_entry``，命中时将 ``(path, rule_name)`` 追加到
    ``scanner._matched_files`` 供进度回调上报。每 ``_gil_yield_interval``
    个文件 ``time.sleep(0)`` 让步 GIL，避免单线程长时间独占导致 UI 卡死。

    取消时（``_check_control`` 返回 True）立即 break，已扫描结果保留。
    """
    scanned = 0
    matched = 0
    errors = 0
    matches = 0
    yield_counter = 0
    for entry in entries:
        if scanner._check_control():
            break
        try:
            result = scanner._scan_entry(entry)
            scanned += 1
            if result.has_hit:
                matched += 1
                matches += result.total_match_count
                if scanner._on_progress is not None:
                    for hit in result.hits:
                        scanner._matched_files.append((str(entry.path), hit.rule_name))
            errors += result.errors
            results.append(result)
        except Exception:
            errors += 1
            scanned += 1
            logger.warning("扫描文件失败 %s", entry.path, exc_info=True)
        scanner._emit_progress(str(entry.path), scanned, matched, errors, matches)
        # GIL 让步：单线程扫描时也定期让出 GIL，避免长时间独占导致 UI 卡死
        # iter-111：使用实例级 _gil_yield_interval（顺序扫描为 20）
        yield_counter += 1
        if yield_counter >= scanner._gil_yield_interval:
            yield_counter = 0
            time.sleep(0)
    return scanned, matched, errors, matches


def _scan_concurrent(
    scanner: Scanner,
    entries: list[FileEntry],
    results: list[ScanResult],
) -> tuple[int, int, int, int]:
    """并发扫描文件清单，返回 ``(scanned, matched, errors, matches)``。

    iter-71 两阶段架构：阶段 1 已单线程收集 ``entries``，本方法将所有
    entry 提交到 ``ThreadPoolExecutor`` 并用 :func:`as_completed` 收集结果。
    相比原流水线模式，先收集再扫描避免了 walk 线程与 worker 线程争抢
    磁盘 I/O，且可对完整清单做全局后缀过滤后再提交，减少无效 future。

    取消加速（需求 req-13）：提交或收集阶段检测到取消时，立即对全部未启动
    future 调 ``f.cancel()`` 并 ``break`` 跳出 ``as_completed`` 阻塞等待。
    ``ThreadPoolExecutor`` 上下文退出时仍会等待已运行 future（最多
    ``max_workers`` 个）完成，配合 ``max_file_size`` 大文件跳过可将单 worker
    阻塞上限控制在百毫秒级。

    命中结果同步收集到 ``scanner._matched_files`` 供进度回调上报。

    iter-139：使用 :class:`DaemonThreadPoolExecutor` 并在 finally 中
    ``shutdown(wait=False)`` 不阻塞主线程，依赖 daemon worker 在进程退出时
    由 OS 回收。原实现 ``shutdown(wait=True)`` 在取消路径下因
    ``_collect_concurrent_results`` 内已 ``shutdown(wait=False)`` 仍会重新
    ``t.join()`` 阻塞，导致 ``ScanWorker.run`` 不返回、进程不退。
    """
    scanned = 0
    matched = 0
    errors = 0
    matches = 0
    future_to_entry: dict[Future[ScanResult], FileEntry] = {}
    # 不使用 with 语句：取消时需要 shutdown(wait=False) 立即返回，
    # 避免某个 worker 卡在 read_bytes() 上导致 with 退出时无限阻塞。
    # 已运行 worker 在后台完成（_scan_entry 入口已检查取消标志会快速返回），
    # 不影响下次扫描（Scanner 每次扫描重新构造，不复用线程池）。
    pool = DaemonThreadPoolExecutor(max_workers=scanner._max_workers)
    try:
        cancelled_in_submit = False
        # 一次性提交所有 entries：阶段 1 已完成遍历，entries 内存可见且可索引
        for entry in entries:
            if scanner._check_control():
                cancelled_in_submit = True
                break
            future = pool.submit(scanner._scan_entry, entry)
            future_to_entry[future] = entry
        if cancelled_in_submit:
            # 取消全部未启动 future，shutdown(wait=False) 不等待已运行 future
            cancel_all_futures(future_to_entry)
            pool.shutdown(wait=False)
            return scanned, matched, errors, matches
        scanned, matched, errors, matches = _collect_concurrent_results(scanner, future_to_entry, results, pool)
    finally:
        # iter-139：wait=False 不阻塞主线程。DaemonThreadPoolExecutor 的 worker
        # 为 daemon，进程退出时由 OS 回收；正常完成路径 as_completed 循环已退出，
        # 此时 worker 已空闲，shutdown 仅清理 pool 状态立即返回。
        pool.shutdown(wait=False)
    return scanned, matched, errors, matches


def _collect_concurrent_results(
    scanner: Scanner,
    future_to_entry: dict[Future[ScanResult], FileEntry],
    results: list[ScanResult],
    pool: ThreadPoolExecutor,
) -> tuple[int, int, int, int]:
    """阻塞收集 future 结果，返回 ``(scanned, matched, errors, matches)``。

    iter-111 从 :func:`_scan_concurrent` 抽离的子流程，职责单一便于分支数控制。
    内含 GIL 让步（``_gil_yield_interval``）与进度 emit 批处理
    （``_progress_emit_batch``）逻辑：

    - **GIL 让步**：并发模式下 PyO3 提取器在 Rust 层释放 GIL，worker I/O 期间
      主线程自然获得调度，让步间隔提高到 50 减少 sleep(0) 调用开销。
    - **emit 批处理**：每 N 个 future 完成才调用一次 ``scanner._emit_progress``
      （内部仍有 150ms 节流），减少 ``time.perf_counter()`` 与 deque tuple
      拷贝开销；尾部不足一批的剩余进度补发一次。

    :param scanner: 所属 Scanner 实例（提供控制状态、进度回调、批处理参数）
    :param future_to_entry: future → entry 映射，由 :func:`_scan_concurrent` 提交
    :param results: 共享结果列表，本方法将 future 结果 append 到此列表
    :param pool: 所属线程池，取消时调 ``shutdown(wait=False)`` 立即返回
    """
    scanned = 0
    matched = 0
    errors = 0
    matches = 0
    yield_counter = 0
    emit_counter = 0
    for future in as_completed(future_to_entry):
        if scanner._check_control():
            cancel_all_futures(future_to_entry)
            pool.shutdown(wait=False)
            break
        entry = future_to_entry[future]
        scanned += 1
        try:
            result = future.result()
            if result.has_hit:
                matched += 1
                matches += result.total_match_count
                if scanner._on_progress is not None:
                    for hit in result.hits:
                        scanner._matched_files.append((str(entry.path), hit.rule_name))
            errors += result.errors
            results.append(result)
        except Exception:
            errors += 1
            logger.warning("扫描文件失败 %s", entry.path, exc_info=True)
        # iter-111：批处理 emit，减少并发高吞吐场景下的进度回调开销
        emit_counter += 1
        if emit_counter >= scanner._progress_emit_batch:
            scanner._emit_progress(str(entry.path), scanned, matched, errors, matches)
            emit_counter = 0
        yield_counter += 1
        if yield_counter >= scanner._gil_yield_interval:
            yield_counter = 0
            time.sleep(0)
    # 批处理尾部：剩余未 emit 的进度补发一次（避免最后几个文件状态丢失）
    if emit_counter > 0 and scanner._on_progress is not None:
        scanner._emit_progress("", scanned, matched, errors, matches)
    return scanned, matched, errors, matches
