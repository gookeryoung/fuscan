"""扫描器：协调遍历器与匹配引擎，输出扫描报告。

支持多线程并发扫描以提升 I/O 密集型场景的吞吐量：
``max_workers`` 控制线程池大小，``None`` 或 ``<=1`` 时退化为单线程。
压缩包内条目扫描始终顺序执行（避免 ArchiveScanner 的潜在线程安全问题）。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from fuscan.extractors import extract_content
from fuscan.rules.model import Rule, RuleSet
from fuscan.scanner.context import ContentProvider, FileEntry, MatchContext
from fuscan.scanner.matchers import Matcher, build_matcher
from fuscan.scanner.result import ProgressInfo, RuleHit, ScanReport, ScanResult, ScanStats
from fuscan.scanner.walker import FileWalker

if TYPE_CHECKING:
    from fuscan.archive import ArchiveScanner

__all__ = ["Scanner", "default_extract_content"]

logger = logging.getLogger(__name__)


def default_extract_content(entry: FileEntry) -> str:
    """默认内容提供器：通过提取器注册表按扩展名提取文本。

    无注册提取器时回退到纯文本读取；提取失败返回空字符串。
    """
    try:
        return extract_content(entry.path)
    except Exception:
        logger.debug("提取器提取失败，回退到纯文本: %s", entry.path, exc_info=True)
        return entry.path.read_text(encoding="utf-8", errors="ignore")


class Scanner:
    """扫描器：对目录或单文件应用规则集，产出扫描报告。

    - 构造时一次性编译规则集为 Matcher 列表，避免重复编译
    - 默认使用提取器注册表（extractors）提取文件内容，支持多格式
    - 支持自定义内容提供器覆盖默认提取逻辑
    - ``max_workers > 1`` 时用线程池并发扫描，提升 I/O 密集型场景吞吐量
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
    ) -> None:
        self.ruleset = ruleset
        self._content_provider: ContentProvider = content_provider or default_extract_content
        self._compiled: list[tuple[Rule, Matcher]] = [(rule, build_matcher(rule.match)) for rule in ruleset.rules]
        # 预计算规则集扩展名并集，避免 _should_scan 对每个文件重算
        self._has_unrestricted_rule: bool = any(not rule.file_extensions for rule in ruleset.rules)
        self._all_extensions: frozenset[str] = frozenset(ext for rule in ruleset.rules for ext in rule.file_extensions)
        self._skipped_dirs: list[str] = []
        self._matched_files: list[tuple[str, str]] = []
        self._walker = FileWalker(
            ignore_dirs=ignore_dirs,
            ignore_extensions=ignore_extensions,
            ignore_paths=ruleset.ignore_paths,
            max_depth=max_depth,
            follow_symlinks=follow_symlinks,
            on_skip_dir=self._on_skip_dir_internal,
        )
        self._scan_archives = scan_archives
        self._max_workers = max_workers
        self._archive_scanner: ArchiveScanner | None = None
        if scan_archives:
            # 惰性导入避免与 archive.scanner 模块的循环依赖
            from fuscan.archive import ArchiveScanner

            self._archive_scanner = ArchiveScanner(
                ruleset=ruleset,
                password=archive_password,
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

        ``max_workers > 1`` 时用线程池并发扫描文件，压缩包内条目始终顺序扫描。
        ``on_progress`` 回调在遍历和扫描阶段按时间节流反馈进度。
        """
        self._progress_start = time.perf_counter()
        # 重置每次扫描的收集列表，避免跨多次 scan() 累积
        self._skipped_dirs = []
        self._matched_files = []

        # 阶段 1：遍历收集待扫描 entry（单线程，I/O 轻量）
        entries: list[FileEntry] = []
        total = 0
        skipped = 0
        for entry in self._walker.walk(root):
            if self._check_control():
                break
            total += 1
            if not self._should_scan(entry):
                skipped += 1
                continue
            entries.append(entry)
            if total % 200 == 0:
                self._emit_progress(str(entry.path), 0, 0, 0)

        self._progress_total = total
        self._progress_skipped = skipped

        # 阶段 2：扫描文件（单线程或并发）
        results: list[ScanResult] = []
        scanned = 0
        matched = 0
        errors = 0

        if not self.is_cancelled:
            if self._max_workers and self._max_workers > 1:
                scanned, matched, errors = self._scan_concurrent(entries, results)
            else:
                scanned, matched, errors = self._scan_sequential(entries, results)

        # 阶段 3：顺序扫描压缩包内条目（避免 ArchiveScanner 线程安全问题）
        if self._scan_archives and self._archive_scanner is not None and not self.is_cancelled:
            self._base_scanned = scanned
            self._base_matched = matched
            self._base_errors = errors
            d_scanned, d_matched, d_errors = self._scan_archive_phase(entries, results)
            scanned += d_scanned
            matched += d_matched
            errors += d_errors

        # 强制发送最终进度
        self._emit_progress("", scanned, matched, errors, force=True)

        duration = time.perf_counter() - self._progress_start
        stats = ScanStats(
            total_files=total,
            scanned_files=scanned,
            matched_files=matched,
            skipped_files=skipped,
            errors=errors,
            duration_seconds=duration,
        )
        return ScanReport(root=root, results=tuple(results), stats=stats, cancelled=self.is_cancelled)

    def _emit_progress(
        self,
        current_file: str,
        scanned: int,
        matched: int,
        errors: int,
        force: bool = False,
    ) -> None:
        """时间节流后调用 on_progress 回调。

        :param force: 为 True 时跳过节流，强制发送（如最终进度）。
        """
        if self._on_progress is None:
            return
        now = time.perf_counter()
        if not force and now - self._last_progress_time < self._progress_interval:
            return
        self._last_progress_time = now
        # 截断到最近 500 条，避免大扫描量时 ProgressInfo 过大
        recent_skipped = tuple(self._skipped_dirs[-500:])
        recent_matched = tuple(self._matched_files[-500:])
        self._on_progress(
            ProgressInfo(
                current_file=current_file,
                scanned=scanned,
                total=self._progress_total,
                skipped=self._progress_skipped,
                matched=matched,
                errors=errors,
                elapsed=now - self._progress_start,
                skipped_dirs=recent_skipped,
                matched_files=recent_matched,
            )
        )

    def _scan_sequential(
        self,
        entries: list[FileEntry],
        results: list[ScanResult],
    ) -> tuple[int, int, int]:
        """单线程顺序扫描，返回 (scanned, matched, errors)。"""
        scanned = 0
        matched = 0
        errors = 0
        for entry in entries:
            if self._check_control():
                break
            try:
                result = self._scan_entry(entry)
                scanned += 1
                if result.has_hit:
                    matched += 1
                    for hit in result.hits:
                        self._matched_files.append((str(entry.path), hit.rule_name))
                errors += result.errors
                results.append(result)
            except Exception:
                errors += 1
                scanned += 1
                logger.warning("扫描文件失败 %s", entry.path, exc_info=True)
            self._emit_progress(str(entry.path), scanned, matched, errors)
        return scanned, matched, errors

    def _scan_concurrent(
        self,
        entries: list[FileEntry],
        results: list[ScanResult],
    ) -> tuple[int, int, int]:
        """多线程并发扫描，返回 (scanned, matched, errors)。

        每个文件的提取+匹配作为独立任务提交到线程池，
        通过 as_completed 收集结果。_scan_entry 无共享可变状态，线程安全。
        """
        scanned = 0
        matched = 0
        errors = 0
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            future_to_entry = {pool.submit(self._scan_entry, entry): entry for entry in entries}
            for future in as_completed(future_to_entry):
                if self._check_control():
                    for f in future_to_entry:
                        f.cancel()
                    break
                entry = future_to_entry[future]
                scanned += 1
                try:
                    result = future.result()
                    if result.has_hit:
                        matched += 1
                        for hit in result.hits:
                            self._matched_files.append((str(entry.path), hit.rule_name))
                    errors += result.errors
                    results.append(result)
                except Exception:
                    errors += 1
                    logger.warning("扫描文件失败 %s", entry.path, exc_info=True)
                self._emit_progress(str(entry.path), scanned, matched, errors)
        return scanned, matched, errors

    def _scan_archive_phase(
        self,
        entries: list[FileEntry],
        results: list[ScanResult],
    ) -> tuple[int, int, int]:
        """顺序扫描压缩包内条目，返回 (scanned, matched, errors) 增量。

        压缩包扫描始终顺序执行以避免 ArchiveScanner 的线程安全问题。
        进度回调使用累计值（base + delta）。
        """
        from fuscan.archive import get_reader

        scanned = 0
        matched = 0
        errors = 0
        for entry in entries:
            if self._check_control():
                break
            if get_reader(entry.path) is None:
                continue
            try:
                archive_results = self._archive_scanner.scan_archive(entry.path)  # type: ignore[union-attr]
            except Exception:
                errors += 1
                logger.warning("压缩包扫描失败 %s", entry.path, exc_info=True)
                continue
            for ar in archive_results:
                scanned += 1
                if ar.has_hit:
                    matched += 1
                    for hit in ar.hits:
                        self._matched_files.append((str(ar.path), hit.rule_name))
                errors += ar.errors
                results.append(ar)
            self._emit_progress(
                str(entry.path),
                self._base_scanned + scanned,
                self._base_matched + matched,
                self._base_errors + errors,
            )
        return scanned, matched, errors

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
        """根据规则集的 file_extensions 限制决定是否扫描该文件。

        若任一规则未限定扩展名，则扫描所有文件；
        否则只扫描规则限定扩展名的并集。
        """
        if entry.is_dir:
            return False
        if self._has_unrestricted_rule:
            return True
        return entry.extension in self._all_extensions

    def _scan_entry(self, entry: FileEntry) -> ScanResult:
        """对单个文件应用所有规则，返回扫描结果。"""
        context = MatchContext(entry, content_provider=self._content_provider)
        hits: list[RuleHit] = []
        rule_errors = 0

        for rule, matcher in self._compiled:
            if rule.file_extensions and entry.extension not in rule.file_extensions:
                continue
            try:
                result = matcher.matches(context)
            except Exception:
                rule_errors += 1
                logger.warning("规则 %s 求值失败 %s", rule.name, entry.path, exc_info=True)
                continue
            if result.matched:
                hits.append(RuleHit(rule_name=rule.name, severity=rule.severity, detail=result.detail))

        return ScanResult(path=entry.path, size=entry.size, hits=tuple(hits), errors=rule_errors)
