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
from fuscan.scanner._helpers import GIL_YIELD_THRESHOLD_S, cancel_all_futures
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
    距上次让步超过 ``GIL_YIELD_THRESHOLD_S``（5ms）才 ``sleep(0)`` 让步 GIL。
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

    与 ``_scan_concurrent`` 对齐，引入 ``_progress_emit_batch`` 批处理
    机制，每 N 个文件完成调用一次 ``_emit_progress``（内部仍有双门限节流），
    避免每文件都触发 ``time.perf_counter()`` 与 deque 转换开销。N 取 Scanner
    构造时的 ``_progress_emit_batch``（默认并发=10，顺序=1）。

    每文件调用 ``scanner._scan_entry``，命中时将 ``(path, rule_name)`` 追加到
    ``scanner._matched_files`` 供进度回调上报。距上次让步超过
    ``GIL_YIELD_THRESHOLD_S``（5ms）才 ``time.sleep(0)`` 让步 GIL，
    避免单线程长时间独占导致 UI 卡死。

    取消时（``_check_control`` 返回 True）立即 break，已扫描结果保留。
    """
    scanned = 0
    matched = 0
    errors = 0
    matches = 0
    emit_counter = 0
    _last_entry_path: str = ""
    # 命中结果批次缓冲，达 emit_batch 时一次性 extend 到共享列表
    batch_match_list: list[tuple[str, str]] = []
    for entry in entries:
        if scanner._check_control():
            break
        # 设置当前文件元信息缓存，供 _emit_progress 填充单文件字段
        scanner._current_file_path = str(entry.path)
        scanner._current_file_size = entry.size
        scanner._current_file_ext = entry.extension
        scanner._current_file_start_time = time.perf_counter()
        try:
            result = scanner._scan_entry(entry)
            scanned += 1
            _last_entry_path = str(entry.path)
            if result.has_hit:
                matched += 1
                matches += result.total_match_count
                if scanner._on_progress is not None:
                    for hit in result.hits:
                        batch_match_list.append((str(entry.path), hit.rule_name))
            errors += result.errors
            results.append(result)
        except Exception:
            errors += 1
            scanned += 1
            logger.warning("扫描文件失败 %s", entry.path, exc_info=True)
        # 批处理 emit，减少每文件调用 _emit_progress 的开销
        emit_counter += 1
        if emit_counter >= scanner._progress_emit_batch:
            if batch_match_list and scanner._on_progress is not None:
                scanner._matched_files.extend(batch_match_list)
                batch_match_list.clear()
            scanner._emit_progress(_last_entry_path, scanned, matched, errors, matches)
            emit_counter = 0
        # GIL 让步：单线程扫描时也定期让出 GIL，避免长时间独占导致 UI 卡死。
        # 时间式判断：距上次让步超过 5ms 才 sleep(0)，否则跳过避免无谓系统调用
        now = time.perf_counter()
        if now - scanner._last_yield_time >= GIL_YIELD_THRESHOLD_S:
            scanner._last_yield_time = now
            time.sleep(0)
    # 尾部补发
    if batch_match_list and scanner._on_progress is not None:
        scanner._matched_files.extend(batch_match_list)
    if emit_counter > 0 and scanner._on_progress is not None:
        scanner._emit_progress(_last_entry_path, scanned, matched, errors, matches)
    return scanned, matched, errors, matches


def _scan_concurrent(
    scanner: Scanner,
    entries: list[FileEntry],
    results: list[ScanResult],
) -> tuple[int, int, int, int]:
    """并发扫描文件清单，返回 ``(scanned, matched, errors, matches)``。

    两阶段架构：阶段 1 已单线程收集 ``entries``，本方法将所有
    entry 提交到 ``ThreadPoolExecutor`` 并用 :func:`as_completed` 收集结果。
    相比原流水线模式，先收集再扫描避免了 walk 线程与 worker 线程争抢
    磁盘 I/O，且可对完整清单做全局后缀过滤后再提交，减少无效 future。

    取消加速（需求 req-13）：提交或收集阶段检测到取消时，立即对全部未启动
    future 调 ``f.cancel()`` 并 ``break`` 跳出 ``as_completed`` 阻塞等待。
    ``ThreadPoolExecutor`` 上下文退出时仍会等待已运行 future（最多
    ``max_workers`` 个）完成，配合 ``max_file_size`` 大文件跳过可将单 worker
    阻塞上限控制在百毫秒级。

    命中结果同步收集到 ``scanner._matched_files`` 供进度回调上报。

    使用 :class:`DaemonThreadPoolExecutor` 并在 finally 中
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
    # 每个 future 的提交时间，供 _collect_concurrent_results 计算「提交到完成」耗时
    submit_times: dict[Future[ScanResult], float] = {}
    # 不使用 with 语句：取消时需要 shutdown(wait=False) 立即返回，
    # 避免某个 worker 卡在 read_bytes() 上导致 with 退出时无限阻塞。
    # 已运行 worker 在后台完成（_scan_entry 入口已检查取消标志会快速返回），
    # 不影响下次扫描（Scanner 每次扫描重新构造，不复用线程池）。
    pool = DaemonThreadPoolExecutor(max_workers=scanner._max_workers)
    try:
        cancelled_in_submit = False
        # 并发提交前按路径去重，避免同一文件被重复扫描
        seen_paths: set[str] = set()
        unique_entries: list[FileEntry] = []
        dup_skipped = 0
        for entry in entries:
            entry_path_str = str(entry.path)
            if entry_path_str in seen_paths:
                dup_skipped += 1
                continue
            seen_paths.add(entry_path_str)
            unique_entries.append(entry)
        if dup_skipped > 0:
            logger.info("并发扫描：去重 %d 个重复条目", dup_skipped)
        # 一次性提交所有 entries：阶段 1 已完成遍历，entries 内存可见且可索引
        for entry in unique_entries:
            if scanner._check_control():
                cancelled_in_submit = True
                break
            future = pool.submit(scanner._scan_entry, entry)
            future_to_entry[future] = entry
            submit_times[future] = time.perf_counter()
        if cancelled_in_submit:
            # 取消全部未启动 future，shutdown(wait=False) 不等待已运行 future
            cancel_all_futures(future_to_entry)
            pool.shutdown(wait=False)
            return scanned, matched, errors, matches
        scanned, matched, errors, matches = _collect_concurrent_results(
            scanner, future_to_entry, results, pool, submit_times
        )
    finally:
        # wait=False 不阻塞主线程。DaemonThreadPoolExecutor 的 worker
        # 为 daemon，进程退出时由 OS 回收；正常完成路径 as_completed 循环已退出，
        # 此时 worker 已空闲，shutdown 仅清理 pool 状态立即返回。
        pool.shutdown(wait=False)
    return scanned, matched, errors, matches


def _collect_concurrent_results(
    scanner: Scanner,
    future_to_entry: dict[Future[ScanResult], FileEntry],
    results: list[ScanResult],
    pool: ThreadPoolExecutor,
    submit_times: dict[Future[ScanResult], float],
) -> tuple[int, int, int, int]:
    """阻塞收集 future 结果，返回 ``(scanned, matched, errors, matches)``。

    从 :func:`_scan_concurrent` 抽离的子流程，职责单一便于分支数控制。
    内含 GIL 让步（``GIL_YIELD_THRESHOLD_S``）与进度 emit 批处理
    （``_progress_emit_batch``）逻辑：

    - **GIL 让步**：并发模式下 PyO3 提取器在 Rust 层释放 GIL，worker I/O 期间
      主线程自然获得调度，按时间判断（5ms 阈值）让步避免无谓 sleep(0)。
    - **emit 批处理**：每 N 个 future 完成才调用一次 ``scanner._emit_progress``
      （内部仍有 150ms 节流），减少 ``time.perf_counter()`` 与 deque tuple
      拷贝开销；尾部不足一批的剩余进度补发一次。

    命中结果 (path, rule) 逐 tuple append → 批次内累积到
    ``_batch_match_list``，emit 时一次性 ``extend`` 到共享列表，减少
    list.append 的 C-level 调用次数与 Python 层循环（单文件多规则场景下
    可节省 30~50% 的命中聚合 overhead）。

    :param scanner: 所属 Scanner 实例（提供控制状态、进度回调、批处理参数）
    :param future_to_entry: future → entry 映射，由 :func:`_scan_concurrent` 提交
    :param results: 共享结果列表，本方法将 future 结果 append 到此列表
    :param pool: 所属线程池，取消时调 ``shutdown(wait=False)`` 立即返回
    :param submit_times: 每个 future 的提交时间（``time.perf_counter`` 基线），
        供 _emit_progress 计算「提交到完成」单文件耗时
    """
    scanned = 0
    matched = 0
    errors = 0
    matches = 0
    emit_counter = 0
    # 命中结果批次缓冲，达 emit_batch 时一次性 extend 到共享列表
    batch_match_list: list[tuple[str, str]] = []
    _last_entry_path: str = ""
    for future in as_completed(future_to_entry):
        if scanner._check_control():
            cancel_all_futures(future_to_entry)
            pool.shutdown(wait=False)
            break
        entry = future_to_entry[future]
        entry_path = str(entry.path)
        _last_entry_path = entry_path
        # 设置当前文件元信息缓存，供 _emit_progress 填充单文件字段
        scanner._current_file_path = entry_path
        scanner._current_file_size = entry.size
        scanner._current_file_ext = entry.extension
        scanner._current_file_start_time = submit_times.get(future, 0.0)
        scanned += 1
        try:
            result = future.result()
            if result.has_hit:
                matched += 1
                matches += result.total_match_count
                if scanner._on_progress is not None:
                    # 先累积到批次列表，后续 emit 时一次性 extend
                    for hit in result.hits:
                        batch_match_list.append((entry_path, hit.rule_name))
            errors += result.errors
            results.append(result)
        except Exception:
            errors += 1
            logger.warning("扫描文件失败 %s", entry_path, exc_info=True)
        # 批处理 emit，减少并发高吞吐场景下的进度回调开销
        emit_counter += 1
        if emit_counter >= scanner._progress_emit_batch:
            # flush 命中批次到共享列表（extend 比多次 append 快）
            if batch_match_list and scanner._on_progress is not None:
                scanner._matched_files.extend(batch_match_list)
                batch_match_list.clear()
            scanner._emit_progress(_last_entry_path, scanned, matched, errors, matches)
            emit_counter = 0
        # GIL 让步：并发模式下 worker 在 Rust/C 层释放 GIL，主线程本就能调度，
        # 但 as_completed 循环本身在 Python 层，仍需定期 sleep(0) 让出时间片。
        # 时间式判断：距上次让步超过 5ms 才 sleep(0)，避免高吞吐下无谓系统调用
        now = time.perf_counter()
        if now - scanner._last_yield_time >= GIL_YIELD_THRESHOLD_S:
            scanner._last_yield_time = now
            time.sleep(0)
    # 批处理尾部：剩余命中与未 emit 的进度补发一次（避免最后几个文件状态丢失）
    if batch_match_list and scanner._on_progress is not None:
        scanner._matched_files.extend(batch_match_list)
    if emit_counter > 0 and scanner._on_progress is not None:
        scanner._emit_progress(_last_entry_path, scanned, matched, errors, matches)
    return scanned, matched, errors, matches
