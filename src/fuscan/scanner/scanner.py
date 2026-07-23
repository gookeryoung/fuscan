"""扫描器：协调遍历器与匹配引擎，输出扫描报告。

两阶段扫描架构（iter-71）：

1. 单线程遍历目录树收集待扫描文件清单（按全局 ``scan_extensions`` 过滤）
2. ``max_workers > 1`` 时用 ThreadPoolExecutor 并发扫描清单，否则顺序扫描

压缩包扫描在 ``max_workers > 1`` 时按 archive 文件级别并行：不同 archive
用线程池并发扫描，单个 archive 内条目顺序执行（避免 reader 共享竞争）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

from fuscan.cache.hashes import hash_bytes
from fuscan.cache.store import BatchWriteItem
from fuscan.extractors import extract_content_from_bytes, extract_content_with_fallback
from fuscan.perf import PerfStats
from fuscan.rules.model import MatchSpec, MatchTarget, Rule, RuleSet
from fuscan.scanner.context import ContentProvider, FileEntry, MatchContext
from fuscan.scanner.matchers import Matcher, build_matcher
from fuscan.scanner.result import ProgressInfo, RuleHit, ScanReport, ScanResult, ScanStats
from fuscan.scanner.walker import FileWalker

if TYPE_CHECKING:
    from fuscan.archive import ArchiveScanner
    from fuscan.cache import CacheStore

__all__ = ["Scanner", "default_extract_content", "default_extract_content_with_hash"]

logger = logging.getLogger(__name__)

# 批量写入阈值：累积到该文件数后自动 flush 一次事务。
# 50 个文件 × 平均 2 条规则 = 100 行 scan_results + 50 行 scanned_files + 50 行 file_paths，
# 单次事务约 200 行写入，相比逐条 commit（200 次 fsync）减少 99% 提交开销。
_BATCH_THRESHOLD: int = 50

# 默认大文件跳过阈值（字节）：超过此值的文件不读取内容、不计哈希。
# 100MB 来自需求 req-13，避免大文件一次性读入内存导致卡死；
# 可通过 Config.max_file_size 与 Scanner(max_file_size=...) 覆盖，0 表示不限制。
_DEFAULT_MAX_FILE_SIZE: int = 100 * 1024 * 1024

# 进度收集列表上限：_skipped_dirs 与 _matched_files 使用 deque(maxlen=) 防止
# 大规模扫描（如全盘跳过 node_modules）时列表无界增长导致内存膨胀。
# _emit_progress 取该上限条 recent 条目，足够 GUI 展示近期跳过/命中情况。
# iter-59 由 200 下调到 50：每次进度回调需将 deque 转为 tuple 跨线程信号传递，
# 200 项 × 2 列表 = 400 元组拷贝，大规模扫描下高频回调会让主线程信号槽分发
# 占用可观时间片导致 UI 卡滞；50 项已足够用户感知"近期"上下文（最新命中/跳过）。
_PROGRESS_LIST_MAX: int = 50


def default_extract_content(entry: FileEntry) -> str:
    """默认内容提供器：通过提取器注册表按扩展名提取文本。

    无注册提取器时回退到纯文本读取；提取失败返回空字符串。
    """
    return extract_content_with_fallback(entry.path)


def default_extract_content_with_hash(entry: FileEntry) -> tuple[str, str]:
    """带哈希的内容提供器：读字节算 BLAKE2b，再从同一份字节提取内容。

    一次 ``read_bytes`` 既算哈希又提取内容，避免提取器内部重复读磁盘。
    缓存模式下，``Scanner`` 用此函数替代 :func:`default_extract_content`，
    使文件哈希计算与内容提取共享一次磁盘 I/O。

    哈希算法由 :func:`fuscan.cache.hashes.hash_bytes` 决定（BLAKE2b，
    ``digest_size=32``，64 字符 hex）。算法变更需递增
    :data:`fuscan.cache.schema.CACHE_COMPAT_VERSION` 触发旧缓存失效。

    超过 :data:`_DEFAULT_MAX_FILE_SIZE`（100MB）的文件跳过读取，
    返回空内容与空字节哈希；``Scanner`` 在缓存模式下走自己的
    :meth:`Scanner._extract_with_cache`，使用可配置的 ``max_file_size``。

    :param entry: 文件元信息
    :return: ``(content, file_hash)`` 元组；``file_hash`` 为 64 字符十六进制摘要
    """
    if entry.is_dir or entry.size > _DEFAULT_MAX_FILE_SIZE:
        return "", hash_bytes(b"")
    try:
        data = entry.path.read_bytes()
    except OSError:
        logger.debug("读取文件失败: %s", entry.path, exc_info=True)
        return "", hash_bytes(b"")
    file_hash = hash_bytes(data)
    try:
        content = extract_content_from_bytes(data, entry.extension)
    except Exception:
        logger.debug("提取器提取失败，回退到纯文本: %s", entry.path, exc_info=True)
        content = data.decode("utf-8", errors="ignore")
    return content, file_hash


def _spec_needs_content(spec: MatchSpec) -> bool:
    """递归检查 MatchSpec 是否包含 CONTENT 目标。

    用于缓存模式：若所有适用规则均不需要内容，可跳过文件 I/O。
    """
    from fuscan.rules.model import AndMatch, LeafMatch, NotMatch, OrMatch

    if isinstance(spec, LeafMatch):
        return spec.target == MatchTarget.CONTENT
    if isinstance(spec, AndMatch):
        return any(_spec_needs_content(c) for c in spec.children)
    if isinstance(spec, OrMatch):
        return any(_spec_needs_content(c) for c in spec.children)
    if isinstance(spec, NotMatch):
        return _spec_needs_content(spec.child)
    return False


def _cancel_all_futures(futures: Iterable[Future[Any]]) -> None:
    """对全部 future 调 ``cancel()``。

    已启动的 future 调 ``cancel()`` 返回 False（无法中断），未启动的会成功取消。
    用于扫描取消时跳过 ``as_completed`` 阻塞等待（需求 req-13 R1）。
    """
    for future in futures:
        future.cancel()


class Scanner:
    """扫描器：对目录或单文件应用规则集，产出扫描报告。

    - 构造时一次性编译规则集为 Matcher 列表，避免重复编译
    - 默认使用提取器注册表（extractors）提取文件内容，支持多格式
    - 支持自定义内容提供器覆盖默认提取逻辑
    - 两阶段架构（iter-71）：先单线程遍历收集文件清单（按全局 ``scan_extensions``
      过滤），再 ``max_workers > 1`` 时用线程池并发扫描清单
    - ``on_progress`` 回调在扫描过程中按时间节流（默认 150ms）反馈进度
    """

    def __init__(
        self,
        ruleset: RuleSet,
        content_provider: ContentProvider | None = None,
        max_depth: int | None = None,
        follow_symlinks: bool = False,
        scan_archives: bool = False,
        archive_password: str | None = None,
        max_workers: int | None = None,
        on_progress: Callable[[ProgressInfo], None] | None = None,
        progress_interval: float = 0.15,
        ignore_dirs: tuple[str, ...] = (),
        ignore_extensions: tuple[str, ...] = (),
        cache: CacheStore | None = None,
        source_files: Mapping[Path, str] | None = None,
        max_file_size: int | None = None,
        scan_extensions: tuple[str, ...] | None = None,
    ) -> None:
        self.ruleset = ruleset
        self._content_provider: ContentProvider = content_provider or default_extract_content
        # 大文件跳过阈值：None 或 0 表示不限制，否则超过此大小的文件不读取内容
        self._max_file_size: int = self._normalize_max_file_size(max_file_size)
        self._compiled: list[tuple[Rule, Matcher]] = [(rule, build_matcher(rule.match)) for rule in ruleset.rules]
        # 全局后缀过滤（iter-71）：None 或空表示扫描所有文件；
        # 非空 frozenset 表示只扫描指定后缀。替代原规则级 file_extensions 并集。
        self._scan_extensions: frozenset[str] | None = (
            frozenset(e.lower().lstrip(".") for e in scan_extensions) if scan_extensions else None
        )
        self._skipped_dirs: deque[str] = deque(maxlen=_PROGRESS_LIST_MAX)
        self._matched_files: deque[tuple[str, str]] = deque(maxlen=_PROGRESS_LIST_MAX)
        # scan_archives=True 时从 ignore_extensions 中剔除已注册的 archive 扩展名
        # （zip/rar/7z）：默认 ignore_extensions 含这些扩展名会阻止压缩包进入扫描队列，
        # 导致 ArchiveScanner 永远收不到压缩包（需求 req-13 R3 修复）
        # 同时保存 archive_exts 供 _should_scan 使用：scan_extensions 过滤时
        # archive 文件即使不在 scan_extensions 中也应收集（iter-71）
        effective_ignore_extensions = ignore_extensions
        self._archive_extensions: frozenset[str] = frozenset()
        if scan_archives:
            from fuscan.archive import default_factory as _archive_factory

            archive_exts = _archive_factory.registered_extensions
            self._archive_extensions = archive_exts
            effective_ignore_extensions = tuple(
                e for e in ignore_extensions if e.lower().lstrip(".") not in archive_exts
            )
        self._walker = FileWalker(
            ignore_dirs=ignore_dirs,
            ignore_extensions=effective_ignore_extensions,
            ignore_paths=ruleset.ignore_paths,
            max_depth=max_depth,
            follow_symlinks=follow_symlinks,
            on_skip_dir=self._on_skip_dir_internal,
        )
        self._scan_archives = scan_archives
        self._max_workers = max_workers
        # 预计算每个规则是否需要文件内容（含 CONTENT 目标），供缓存模式跳过 I/O
        self._content_rule_names: frozenset[str] = frozenset(
            rule.name for rule in ruleset.rules if _spec_needs_content(rule.match)
        )
        # 缓存模式：登记规则集并构造带哈希的编译列表
        self._cache: CacheStore | None = cache
        self._rule_hashes: dict[str, str] = {}
        self._compiled_with_hash: list[tuple[Rule, Matcher, str]] = []
        if cache is not None:
            self._rule_hashes = cache.register_ruleset(ruleset, source_files)
            self._compiled_with_hash = [
                (rule, matcher, self._rule_hashes[rule.name])
                for rule, matcher in self._compiled
                if rule.name in self._rule_hashes
            ]
        self._archive_scanner: ArchiveScanner | None = None
        if scan_archives:
            # 惰性导入避免与 archive.scanner 模块的循环依赖
            from fuscan.archive import ArchiveScanner

            self._archive_scanner = ArchiveScanner(
                ruleset=ruleset,
                password=archive_password,
                cache=cache,
                max_entry_size=self._max_file_size,
            )
        self._on_progress = on_progress
        self._progress_interval = progress_interval
        self._last_progress_time: float = 0.0
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancel_event = threading.Event()
        # 扫描进度上下文（scan() 期间设置，供 _emit_progress 使用）
        self._progress_start: float = 0.0
        self._progress_total: int = 0
        self._progress_skipped: int = 0
        self._base_scanned: int = 0
        self._base_matched: int = 0
        self._base_errors: int = 0
        self._base_matches: int = 0
        # 批量写入缓冲（iter-39 P2）：累积 BatchWriteItem，达到阈值后单次事务 flush。
        # _batch_lock 保护 _pending_batch 跨 worker 线程的并发累积与 flush。
        self._pending_batch: list[BatchWriteItem] = []
        self._batch_lock = threading.Lock()
        # 性能聚合统计（iter-65）：FUSCAN_PERF=1 时累计各阶段耗时，扫描末尾输出汇总。
        # PerfStats 始终启用（iter-66 起），仅做聚合统计无日志开销，不影响生产性能。
        self._perf: PerfStats = PerfStats()

    @staticmethod
    def _normalize_max_file_size(value: int | None) -> int:
        """规范化大文件跳过阈值：None 或负数退化为默认值，0 表示不限制。

        :param value: 调用方传入的原始值
        :return: 实际生效的阈值；0 表示不限制
        """
        if value is None or value < 0:
            return _DEFAULT_MAX_FILE_SIZE
        return value

    def pause(self) -> None:
        """暂停扫描，阻塞扫描线程直到 resume。"""
        self._pause_event.clear()

    def resume(self) -> None:
        """恢复暂停的扫描。"""
        self._pause_event.set()

    def cancel(self) -> None:
        """取消扫描，解除暂停以快速退出。"""
        self._cancel_event.set()
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        """扫描是否处于暂停状态。"""
        return not self._pause_event.is_set()

    @property
    def is_cancelled(self) -> bool:
        """扫描是否已被取消。"""
        return self._cancel_event.is_set()

    def _check_control(self) -> bool:
        """检查暂停与取消标志。

        暂停时阻塞当前线程直到 resume；取消时返回 True。
        """
        if self._cancel_event.is_set():
            return True
        self._pause_event.wait()
        return self._cancel_event.is_set()

    def _on_skip_dir_internal(self, dir_path: str) -> None:
        """FileWalker 跳过目录时的内部回调：收集到列表供 ProgressInfo 上报。"""
        self._skipped_dirs.append(dir_path)

    def scan(self, root: Path) -> ScanReport:
        """扫描根目录，返回完整报告。

        两阶段扫描架构（iter-71）：

        1. **阶段 1 - 收集**：单线程遍历目录树，按全局 ``scan_extensions`` 过滤
           生成待扫描文件清单。遍历为 I/O 轻量操作，单线程已足够。
        2. **阶段 2 - 扫描**：``max_workers > 1`` 时用 ThreadPoolExecutor 并发扫描
           文件清单，否则顺序扫描。先收集再扫描避免了 walk 与 scan 争抢磁盘 I/O
           导致的吞吐下降，且可对清单做全局后缀过滤减少无效提交。
        3. **阶段 3 - 压缩包**：顺序扫描压缩包内条目（避免 ArchiveScanner 线程安全问题）。

        ``on_progress`` 回调在遍历和扫描阶段按时间节流反馈进度。
        """
        self._progress_start = time.perf_counter()
        # 重置暂停状态（取消状态在 finally 中清除，保留"scan() 前取消"语义）
        self._pause_event.set()
        # 重置每次扫描的收集列表，避免跨多次 scan() 累积
        self._skipped_dirs.clear()
        self._matched_files.clear()
        # 重置性能统计，使每次 scan() 的汇总独立
        self._perf.reset()

        results: list[ScanResult] = []
        entries: list[FileEntry] = []
        total = 0
        skipped = 0
        scanned = 0
        matched = 0
        errors = 0
        matches = 0
        cancelled = self.is_cancelled

        try:
            if not cancelled:
                # 阶段 1：单线程遍历收集待扫描 entry（I/O 轻量，按全局后缀过滤）
                with self._perf.measure("walk"):
                    for entry in self._walker.walk(root):
                        if self._check_control():
                            break
                        total += 1
                        if not self._should_scan(entry):
                            skipped += 1
                            continue
                        entries.append(entry)
                        if total % 200 == 0:
                            self._emit_progress(str(entry.path), 0, 0, 0, phase="walk")
                self._progress_total = total
                self._progress_skipped = skipped
                # 阶段 2：并发扫描（max_workers > 1）或顺序扫描
                if self._max_workers and self._max_workers > 1:
                    scanned, matched, errors, matches = self._scan_concurrent(entries, results)
                else:
                    scanned, matched, errors, matches = self._scan_sequential(entries, results)

            # 阶段 3：顺序扫描压缩包内条目（避免 ArchiveScanner 线程安全问题）
            if self._scan_archives and self._archive_scanner is not None and not self.is_cancelled:
                # archive phase 内部直接调 CacheStore，不走 _pending_batch，需先 flush
                # 避免批量缓冲与 archive scanner 的写入交错
                self._flush_batch()
                self._base_scanned = scanned
                self._base_matched = matched
                self._base_errors = errors
                self._base_matches = matches
                d_scanned, d_matched, d_errors, d_matches = self._scan_archive_phase(entries, results)
                scanned += d_scanned
                matched += d_matched
                errors += d_errors
                matches += d_matches
        finally:
            # 异常路径（如 MemoryError、walker 未捕获错误）也 flush 已累积批次，
            # 避免最后一批（最多 _BATCH_THRESHOLD 个文件）缓存数据丢失
            self._flush_batch()
            # 记录取消状态后清除标志，使 Scanner 可在取消/异常后复用（C1 修复）：
            # 否则下次 scan() 的 is_cancelled 仍为 True，静默跳过全部扫描逻辑
            cancelled = self.is_cancelled
            self._cancel_event.clear()

        # 强制发送最终进度
        self._emit_progress("", scanned, matched, errors, matches, force=True)
        # 输出性能汇总到 DEBUG 日志（PerfStats 始终启用，但日志需配置 DEBUG 级别才可见）
        self._perf.report(logger)

        duration = time.perf_counter() - self._progress_start
        stats = ScanStats(
            total_files=total,
            scanned_files=scanned,
            matched_files=matched,
            skipped_files=skipped,
            errors=errors,
            duration_seconds=duration,
            total_matches=matches,
            # iter-66：PerfStats 始终启用，导出各阶段统计供 GUI/CLI 展示与持久化
            perf_summary=self._perf.to_dict(),
        )
        return ScanReport(root=root, results=tuple(results), stats=stats, cancelled=cancelled)

    def _emit_progress(
        self,
        current_file: str,
        scanned: int,
        matched: int,
        errors: int,
        matches: int = 0,
        force: bool = False,
        phase: str = "scan",
    ) -> None:
        """时间节流后调用 on_progress 回调。

        :param matches: 累计匹配文本条数（区别于 matched 的命中文件数）。
        :param force: 为 True 时跳过节流，强制发送（如最终进度）。
        :param phase: 当前扫描阶段（iter-75）：``"walk"``/``"scan"``/``"archive"``，
            GUI 据此显示不同提示文案，避免 walk 阶段 scanned=0 被误以为卡住。
        """
        if self._on_progress is None:
            return
        now = time.perf_counter()
        if not force and now - self._last_progress_time < self._progress_interval:
            return
        self._last_progress_time = now
        # deque(maxlen=_PROGRESS_LIST_MAX) 已自动截断到最近条目，直接转 tuple
        recent_skipped = tuple(self._skipped_dirs)
        recent_matched = tuple(self._matched_files)
        self._on_progress(
            ProgressInfo(
                current_file=current_file,
                scanned=scanned,
                total=self._progress_total,
                skipped=self._progress_skipped,
                matched=matched,
                errors=errors,
                elapsed=now - self._progress_start,
                matches=matches,
                skipped_dirs=recent_skipped,
                matched_files=recent_matched,
                phase=phase,
            )
        )

    def _scan_sequential(
        self,
        entries: list[FileEntry],
        results: list[ScanResult],
    ) -> tuple[int, int, int, int]:
        """单线程顺序扫描，返回 (scanned, matched, errors, matches)。"""
        scanned = 0
        matched = 0
        errors = 0
        matches = 0
        for entry in entries:
            if self._check_control():
                break
            try:
                result = self._scan_entry(entry)
                scanned += 1
                if result.has_hit:
                    matched += 1
                    matches += result.total_match_count
                    if self._on_progress is not None:
                        for hit in result.hits:
                            self._matched_files.append((str(entry.path), hit.rule_name))
                errors += result.errors
                results.append(result)
            except Exception:
                errors += 1
                scanned += 1
                logger.warning("扫描文件失败 %s", entry.path, exc_info=True)
            self._emit_progress(str(entry.path), scanned, matched, errors, matches)
        return scanned, matched, errors, matches

    def _scan_concurrent(
        self,
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

        命中结果同步收集到 ``_matched_files`` 供进度回调上报。
        """
        scanned = 0
        matched = 0
        errors = 0
        matches = 0
        future_to_entry: dict[Future[ScanResult], FileEntry] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            cancelled_in_submit = False
            # 一次性提交所有 entries：阶段 1 已完成遍历，entries 内存可见且可索引
            for entry in entries:
                if self._check_control():
                    cancelled_in_submit = True
                    break
                future = pool.submit(self._scan_entry, entry)
                future_to_entry[future] = entry
            if cancelled_in_submit:
                # 取消全部未启动 future，避免 with 退出时阻塞等待它们；
                # 已运行 future（最多 max_workers 个）由 with 退出时统一等待
                _cancel_all_futures(future_to_entry)
                return scanned, matched, errors, matches
            # 阻塞收集 future 结果（按完成顺序）
            for future in as_completed(future_to_entry):
                if self._check_control():
                    _cancel_all_futures(future_to_entry)
                    break
                entry = future_to_entry[future]
                scanned += 1
                try:
                    result = future.result()
                    if result.has_hit:
                        matched += 1
                        matches += result.total_match_count
                        if self._on_progress is not None:
                            for hit in result.hits:
                                self._matched_files.append((str(entry.path), hit.rule_name))
                    errors += result.errors
                    results.append(result)
                except Exception:
                    errors += 1
                    logger.warning("扫描文件失败 %s", entry.path, exc_info=True)
                self._emit_progress(str(entry.path), scanned, matched, errors, matches)
        return scanned, matched, errors, matches

    def _accumulate_archive_results(
        self,
        archive_results: tuple[ScanResult, ...],
        results: list[ScanResult],
    ) -> tuple[int, int, int, int]:
        """累积单个 archive 的扫描结果到 results，返回 (scanned, matched, errors, matches) 增量。

        命中结果同步收集到 ``_matched_files`` 供进度回调上报。单线程与多线程
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
                if self._on_progress is not None:
                    for hit in ar.hits:
                        self._matched_files.append((str(ar.path), hit.rule_name))
            errors += ar.errors
            results.append(ar)
        return scanned, matched, errors, matches

    def _scan_archive_phase(
        self,
        entries: list[FileEntry],
        results: list[ScanResult],
    ) -> tuple[int, int, int, int]:
        """扫描压缩包内条目，返回 (scanned, matched, errors, matches) 增量。

        archive 文件级别并行（iter-39 P3）：``max_workers > 1`` 时不同 archive
        文件用线程池并行扫描，单个 archive 内条目仍顺序执行（避免 reader
        共享导致的线程安全问题）。每个 archive 在 worker 内创建独立 reader，
        ArchiveScanner 自身状态（``_compiled`` 等）只读，CacheStore 内部
        用 RLock 串行化，跨 archive 并发安全。

        进度回调使用累计值（base + delta），按 archive 完成顺序触发。
        """
        from fuscan.archive import is_archive

        archive_entries = [e for e in entries if is_archive(e.path)]
        if not archive_entries:
            return 0, 0, 0, 0

        scanned = 0
        matched = 0
        errors = 0
        matches = 0

        if not (self._max_workers and self._max_workers > 1):
            # 单线程退化：顺序扫描
            for entry in archive_entries:
                if self._check_control():
                    break
                try:
                    archive_results = self._archive_scanner.scan_archive(entry.path)  # type: ignore[union-attr]
                except Exception:
                    errors += 1
                    logger.warning("压缩包扫描失败 %s", entry.path, exc_info=True)
                    continue
                d_scanned, d_matched, d_errors, d_matches = self._accumulate_archive_results(archive_results, results)
                scanned += d_scanned
                matched += d_matched
                errors += d_errors
                matches += d_matches
                self._emit_progress(
                    str(entry.path),
                    self._base_scanned + scanned,
                    self._base_matched + matched,
                    self._base_errors + errors,
                    self._base_matches + matches,
                    phase="archive",
                )
            return scanned, matched, errors, matches

        # 多线程：archive 文件级别并行
        # 取消加速（需求 req-13）：walk 循环检测到取消时立即对全部未启动 future
        # 调 ``f.cancel()`` 并 ``return``，**不进入** ``as_completed`` 阻塞等待。
        # ``ThreadPoolExecutor`` 上下文退出时仍会等待已运行 future（最多
        # ``max_workers`` 个）完成，配合 ``max_file_size`` 大文件跳过将单 worker
        # 阻塞上限控制在百毫秒级。
        future_to_entry: dict[Future[tuple[ScanResult, ...]], FileEntry] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            cancelled_in_walk = False
            for entry in archive_entries:
                if self._check_control():
                    cancelled_in_walk = True
                    break
                future = pool.submit(self._archive_scanner.scan_archive, entry.path)  # type: ignore[union-attr]
                future_to_entry[future] = entry
            if cancelled_in_walk:
                _cancel_all_futures(future_to_entry)
                return scanned, matched, errors, matches
            # 阻塞收集剩余 future
            d_scanned, d_matched, d_errors, d_matches = self._collect_archive_futures(future_to_entry, results)
            scanned += d_scanned
            matched += d_matched
            errors += d_errors
            matches += d_matches
        return scanned, matched, errors, matches

    def _collect_archive_futures(
        self,
        future_to_entry: dict[Future[tuple[ScanResult, ...]], FileEntry],
        results: list[ScanResult],
    ) -> tuple[int, int, int, int]:
        """阻塞收集压缩包扫描 future 结果，返回 ``(scanned, matched, errors, matches)`` 增量。

        取消时对剩余未启动 future 调 ``cancel()`` 并 ``break``。进度回调使用
        累计值（base + delta），按 archive 完成顺序触发。
        """
        scanned = matched = errors = matches = 0
        for future in as_completed(future_to_entry):
            if self._check_control():
                _cancel_all_futures(future_to_entry)
                break
            entry = future_to_entry[future]
            try:
                archive_results = future.result()
            except Exception:
                errors += 1
                logger.warning("压缩包扫描失败 %s", entry.path, exc_info=True)
                self._emit_progress(
                    str(entry.path),
                    self._base_scanned + scanned,
                    self._base_matched + matched,
                    self._base_errors + errors,
                    self._base_matches + matches,
                    phase="archive",
                )
                continue
            d_scanned, d_matched, d_errors, d_matches = self._accumulate_archive_results(archive_results, results)
            scanned += d_scanned
            matched += d_matched
            errors += d_errors
            matches += d_matches
            self._emit_progress(
                str(entry.path),
                self._base_scanned + scanned,
                self._base_matched + matched,
                self._base_errors + errors,
                self._base_matches + matches,
                phase="archive",
            )
        return scanned, matched, errors, matches

    def scan_file(self, path: Path) -> ScanResult:
        """扫描单个文件。"""
        entry = FileEntry.from_path(path)
        return self._scan_entry(entry)

    def scan_archive(self, path: Path) -> tuple[ScanResult, ...]:
        """扫描压缩包内所有条目。

        :raises RuntimeError: 未启用 scan_archives 选项
        """
        if self._archive_scanner is None:
            raise RuntimeError("未启用 scan_archives，无法扫描压缩包")
        return self._archive_scanner.scan_archive(path)

    def _should_scan(self, entry: FileEntry) -> bool:
        """根据全局 scan_extensions 过滤决定是否扫描该文件。

        ``scan_extensions`` 为 None 或空表示扫描所有文件；
        非空表示只扫描指定后缀（已规范化为小写、无点）。

        特例：``scan_archives=True`` 时，archive 文件（zip/rar/7z）即使不在
        ``scan_extensions`` 中也收集——压缩包内条目不再按 ``scan_extensions``
        二次过滤，由 ArchiveScanner 统一扫描全部条目（iter-71）。
        """
        if entry.is_dir:
            return False
        if not self._scan_extensions:
            return True
        if entry.extension in self._scan_extensions:
            return True
        # scan_archives=True 时 archive 文件即使不在 scan_extensions 中也收集
        return bool(self._archive_extensions) and entry.extension in self._archive_extensions

    def _scan_entry(self, entry: FileEntry) -> ScanResult:
        """对单个文件应用所有规则，返回扫描结果。

        缓存模式下委托 :meth:`_scan_entry_cached`，否则走 :meth:`_scan_entry_uncached`。
        """
        if self._cache is None:
            return self._scan_entry_uncached(entry)
        return self._scan_entry_cached(entry)

    def _scan_entry_uncached(self, entry: FileEntry) -> ScanResult:
        """对单个文件应用所有规则（无缓存）。

        超过 ``max_file_size`` 的文件跳过内容提取（与 :meth:`_extract_with_cache`
        行为对齐）：filename/path 规则仍可命中，CONTENT 规则因内容为空不命中。
        """
        if self._max_file_size > 0 and entry.size > self._max_file_size:
            # 大文件跳过内容读取，避免一次性读入内存导致卡死（需求 req-13 R2）
            def _skipped_provider(_fe: FileEntry) -> str:
                return ""

            context = MatchContext(entry, content_provider=_skipped_provider)
        else:
            context = MatchContext(entry, content_provider=self._content_provider)
        hits: list[RuleHit] = []
        rule_errors = 0

        # iter-71：全局 scan_extensions 已在 _should_scan 阶段统一过滤，
        # 此处无需再按 rule.file_extensions 二次过滤
        for rule, matcher in self._compiled:
            try:
                with self._perf.measure("match"):
                    result = matcher.matches(context)
            except Exception:
                rule_errors += 1
                logger.warning("规则 %s 求值失败 %s", rule.name, entry.path, exc_info=True)
                continue
            if result.matched:
                hits.append(
                    RuleHit(
                        rule_name=rule.name,
                        severity=rule.severity,
                        detail=result.detail,
                        match_text=result.match_text,
                        match_count=result.match_count,
                        target=result.target,
                        match_texts=result.match_texts,
                        match_description=result.match_description,
                    )
                )

        return ScanResult(path=entry.path, size=entry.size, hits=tuple(hits), errors=rule_errors)

    def _extract_with_cache(self, entry: FileEntry) -> tuple[str, str]:
        """缓存模式的提取+哈希：优先复用提取内容缓存（iter-39）。

        与 :func:`default_extract_content_with_hash` 的区别：

        - 一次 ``read_bytes`` 算哈希后，先查 :meth:`CacheStore.get_extracted_content`
        - 命中则跳过 ``extract_content_from_bytes``（docx/pptx 提取 5-8ms）
        - 未命中则提取并写入缓存（非空内容才写）
        - 大文件跳过阈值由 ``Scanner(max_file_size=...)`` 控制，0 表示不限制

        各阶段接入 ``PerfStats`` 计时（iter-66 起始终启用）：
        ``read_bytes`` / ``hash`` / ``cache_lookup_extract`` / ``extract`` /
        ``cache_put_extract``，便于定位 I/O 与 CPU 瓶颈。

        :param entry: 文件元信息
        :return: ``(content, file_hash)`` 元组
        """
        assert self._cache is not None  # 调用方已保证非 None
        if entry.is_dir or (self._max_file_size > 0 and entry.size > self._max_file_size):
            return "", hash_bytes(b"")
        try:
            with self._perf.measure("read_bytes"):
                data = entry.path.read_bytes()
        except OSError:
            logger.debug("读取文件失败: %s", entry.path, exc_info=True)
            return "", hash_bytes(b"")
        with self._perf.measure("hash"):
            file_hash = hash_bytes(data)
        # 查提取内容缓存
        with self._perf.measure("cache_lookup_extract"):
            cached_content = self._cache.get_extracted_content(file_hash)
        if cached_content is not None:
            return cached_content, file_hash
        # 未命中，执行提取
        try:
            with self._perf.measure("extract"):
                content = extract_content_from_bytes(data, entry.extension)
        except Exception:
            logger.debug("提取器提取失败，回退到纯文本: %s", entry.path, exc_info=True)
            content = data.decode("utf-8", errors="ignore")
        # 写入提取内容缓存（非空才写）
        if content:
            with self._perf.measure("cache_put_extract"):
                self._cache.put_extracted_content(file_hash, content, entry.extension)
        return content, file_hash

    def _scan_entry_cached(self, entry: FileEntry) -> ScanResult:
        """缓存模式扫描：先查缓存，命中直接复用，未命中走匹配器并写入缓存。

        优化路径：

        1. **filename/path 规则跳过 I/O**：若所有适用规则均不含 CONTENT 目标，
           走 :meth:`_scan_entry_uncached`，避免无谓的哈希计算
        2. **mtime 预筛跳过 read_bytes**：``CacheStore.lookup_file_hash`` 按
           ``(path, mtime, size)`` 查询已登记的 ``file_hash``。若所有适用规则
           都已缓存（命中或未命中），则**完全跳过文件读取**，仅复用缓存结果
        3. **提取内容缓存**（iter-39）：``CacheStore.get_extracted_content`` 按
           ``file_hash`` 查询提取器结果，命中则跳过 ``extract_content_from_bytes``；
           同内容不同路径（如 node_modules 重复依赖）可跳过 docx/pptx 提取开销
        4. **常规路径**：一次 I/O 同时取内容和文件哈希
           （:meth:`_extract_with_cache`），静态闭包包装内容
           传给 :class:`MatchContext`，避免改 MatchContext 接口
        """
        assert self._cache is not None  # 仅类型收窄，调用方已保证非 None
        # iter-71：全局 scan_extensions 已在 _should_scan 阶段统一过滤，
        # 此处无需再按 rule.file_extensions 二次过滤——所有规则对进入扫描队列的
        # 文件均适用
        has_content_rule = any(rule.name in self._content_rule_names for rule, _, _ in self._compiled_with_hash)
        if not has_content_rule:
            # 无内容规则：跳过文件 I/O，直接走匹配器（filename/path 不需读文件）
            return self._scan_entry_uncached(entry)

        applicable: list[tuple[Rule, Matcher, str]] = list(self._compiled_with_hash)
        rule_hashes = [rh for _, _, rh in applicable]

        # mtime 预筛：若 (path, mtime, size) 已登记且所有规则都已缓存，
        # 完全跳过 read_bytes，仅从缓存重建 ScanResult。
        cached: dict[str, RuleHit | None] | None = None
        with self._perf.measure("cache_lookup"):
            cached_file_hash = self._cache.lookup_file_hash(entry.path, entry.mtime, entry.size)
            if cached_file_hash is not None and rule_hashes:
                cached = self._cache.get_cached_hits(cached_file_hash, rule_hashes)
        if cached_file_hash is not None and cached is not None and all(rh in cached for rh in rule_hashes):
            # 全部规则已缓存命中（含未命中记录），无需读文件
            hits, rule_errors = self._build_hits_from_cache(applicable, cached)
            # 累积元数据刷新到批量缓冲（无新 scan_results 需写入，hits=()）
            self._add_to_batch(
                BatchWriteItem(
                    file_hash=cached_file_hash,
                    size=entry.size,
                    path=entry.path,
                    mtime=entry.mtime,
                    hits=(),
                )
            )
            return ScanResult(path=entry.path, size=entry.size, hits=tuple(hits), errors=rule_errors)

        # 常规路径：读文件 + 算哈希 + 查提取内容缓存 + 未命中执行提取
        content, file_hash = self._extract_with_cache(entry)

        def _static_provider(_fe: FileEntry) -> str:
            return content

        context = MatchContext(entry, content_provider=_static_provider)

        with self._perf.measure("cache_lookup_hits"):
            cached = self._cache.get_cached_hits(file_hash, rule_hashes) if rule_hashes else {}

        hits: list[RuleHit] = []
        rule_errors = 0
        batch_hits: list[tuple[str, RuleHit | None]] = []
        for rule, matcher, rule_hash in applicable:
            if rule_hash in cached:
                result = cached[rule_hash]
                if result is not None:
                    # 缓存命中（匹配）——填回 rule_name（缓存中为空字符串）
                    hits.append(
                        RuleHit(
                            rule_name=rule.name,
                            severity=result.severity,
                            detail=result.detail,
                            match_text=result.match_text,
                            match_count=result.match_count,
                            target=result.target,
                            match_texts=result.match_texts,
                            match_description=result.match_description,
                        )
                    )
                # else: 缓存记录为未命中，跳过
                continue
            # 未缓存——执行匹配器
            try:
                with self._perf.measure("match"):
                    match_result = matcher.matches(context)
            except Exception:
                rule_errors += 1
                logger.warning("规则 %s 求值失败 %s", rule.name, entry.path, exc_info=True)
                continue
            if match_result.matched:
                hit = RuleHit(
                    rule_name=rule.name,
                    severity=rule.severity,
                    detail=match_result.detail,
                    match_text=match_result.match_text,
                    match_count=match_result.match_count,
                    target=match_result.target,
                    match_texts=match_result.match_texts,
                    match_description=match_result.match_description,
                )
                hits.append(hit)
                batch_hits.append((rule_hash, hit))
            else:
                # 未命中也缓存，避免重复扫描
                batch_hits.append((rule_hash, None))

        # 累积到批量缓冲，达到阈值后由 _add_to_batch 自动 flush
        self._add_to_batch(
            BatchWriteItem(
                file_hash=file_hash,
                size=entry.size,
                path=entry.path,
                mtime=entry.mtime,
                hits=tuple(batch_hits),
            )
        )

        return ScanResult(path=entry.path, size=entry.size, hits=tuple(hits), errors=rule_errors)

    def _add_to_batch(self, item: BatchWriteItem) -> None:
        """累积写入请求到批量缓冲，达到阈值时自动 flush。

        线程安全：通过 ``_batch_lock`` 保护并发累积与 flush。
        无缓存模式下不应被调用（调用方 :meth:`_scan_entry_cached` 已保证）。
        """
        with self._batch_lock:
            self._pending_batch.append(item)
            if len(self._pending_batch) >= _BATCH_THRESHOLD:
                self._flush_batch_locked()

    def _flush_batch(self) -> None:
        """强制 flush 待写批次。

        在扫描阶段切换（如进入 archive phase）与 ``scan()`` 末尾调用，
        确保累积的数据不丢失。
        """
        with self._batch_lock:
            self._flush_batch_locked()

    def _flush_batch_locked(self) -> None:
        """执行批量写入（已持 ``_batch_lock``）。

        先取出并清空 ``_pending_batch``，再释放锁的"持有期间"调用
        :meth:`CacheStore.batch_put_results`。注意：``_batch_lock`` 仍持锁，
        但 ``CacheStore`` 内部的 ``RLock`` 是另一把锁，worker 线程在
        :meth:`_scan_entry_cached` 中查询（``get_cached_hits`` 等）不受影响。
        """
        if not self._pending_batch or self._cache is None:
            return
        items = self._pending_batch
        self._pending_batch = []
        with self._perf.measure("cache_write"):
            self._cache.batch_put_results(items)

    @staticmethod
    def _build_hits_from_cache(
        applicable: list[tuple[Rule, Matcher, str]],
        cached: dict[str, RuleHit | None],
    ) -> tuple[list[RuleHit], int]:
        """从缓存字典重建 ``RuleHit`` 列表（与主路径的填回逻辑一致）。

        :param applicable: 适用的 (Rule, Matcher, rule_hash) 列表，决定输出顺序
        :param cached: ``rule_hash -> RuleHit | None`` 字典
        :return: ``(hits, rule_errors)``；rule_errors 在纯缓存路径下恒为 0
        """
        hits: list[RuleHit] = []
        for rule, _, rule_hash in applicable:
            result = cached.get(rule_hash)
            if result is not None:
                hits.append(
                    RuleHit(
                        rule_name=rule.name,
                        severity=result.severity,
                        detail=result.detail,
                        match_text=result.match_text,
                        match_count=result.match_count,
                        target=result.target,
                        match_texts=result.match_texts,
                        match_description=result.match_description,
                    )
                )
        return hits, 0
