"""扫描器：协调遍历器与匹配引擎，输出扫描报告。

支持多线程并发扫描以提升 I/O 密集型场景的吞吐量：
``max_workers`` 控制线程池大小，``None`` 或 ``<=1`` 时退化为单线程。
压缩包内条目扫描始终顺序执行（避免 ArchiveScanner 的潜在线程安全问题）。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

from uniscan.extractors import extract_content
from uniscan.rules.model import Rule, RuleSet
from uniscan.scanner.context import ContentProvider, FileEntry, MatchContext
from uniscan.scanner.matchers import Matcher, build_matcher
from uniscan.scanner.result import ProgressInfo, RuleHit, ScanReport, ScanResult, ScanStats
from uniscan.scanner.walker import FileWalker

if TYPE_CHECKING:
    from uniscan.archive import ArchiveScanner

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
        content_provider: Optional[ContentProvider] = None,
        max_depth: Optional[int] = None,
        follow_symlinks: bool = False,
        scan_archives: bool = False,
        archive_password: Optional[str] = None,
        max_workers: Optional[int] = None,
        on_progress: Optional[Callable[[ProgressInfo], None]] = None,
        progress_interval: float = 0.15,
    ) -> None:
        self.ruleset = ruleset
        self._content_provider: ContentProvider = content_provider or default_extract_content
        self._compiled: List[Tuple[Rule, Matcher]] = [(rule, build_matcher(rule.match)) for rule in ruleset.rules]
        self._walker = FileWalker(
            ignore_dirs=ruleset.ignore_dirs,
            ignore_extensions=ruleset.ignore_extensions,
            ignore_paths=ruleset.ignore_paths,
            max_depth=max_depth,
            follow_symlinks=follow_symlinks,
        )
        self._scan_archives = scan_archives
        self._max_workers = max_workers
        self._archive_scanner: Optional[ArchiveScanner] = None
        if scan_archives:
            # 惰性导入避免与 archive.scanner 模块的循环依赖
            from uniscan.archive import ArchiveScanner

            self._archive_scanner = ArchiveScanner(
                ruleset=ruleset,
                password=archive_password,
            )
        self._on_progress = on_progress
        self._progress_interval = progress_interval
        self._last_progress_time: float = 0.0

    def scan(self, root: Path) -> ScanReport:
        """扫描根目录，返回完整报告。

        ``max_workers > 1`` 时用线程池并发扫描文件，压缩包内条目始终顺序扫描。
        ``on_progress`` 回调在遍历和扫描阶段按时间节流反馈进度。
        """
        start = time.perf_counter()

        # 阶段 1：遍历收集待扫描 entry（单线程，I/O 轻量）
        entries: List[FileEntry] = []
        total = 0
        skipped = 0
        for entry in self._walker.walk(root):
            total += 1
            if not self._should_scan(entry):
                skipped += 1
                continue
            entries.append(entry)
            if total % 200 == 0:
                self._emit_progress(start, str(entry.path), 0, total, skipped, 0, 0)

        # 阶段 2：扫描文件（单线程或并发）
        results: List[ScanResult] = []
        scanned = 0
        matched = 0
        errors = 0

        if self._max_workers and self._max_workers > 1:
            scanned, matched, errors = self._scan_concurrent(entries, results, start, total, skipped)
        else:
            scanned, matched, errors = self._scan_sequential(entries, results, start, total, skipped)

        # 阶段 3：顺序扫描压缩包内条目（避免 ArchiveScanner 线程安全问题）
        if self._scan_archives and self._archive_scanner is not None:
            from uniscan.archive import get_reader

            for entry in entries:
                if get_reader(entry.path) is not None:
                    try:
                        archive_results = self._archive_scanner.scan_archive(entry.path)
                    except Exception:
                        errors += 1
                        logger.warning("压缩包扫描失败 %s", entry.path, exc_info=True)
                        continue
                    for ar in archive_results:
                        scanned += 1
                        if ar.has_hit:
                            matched += 1
                        errors += ar.errors
                        results.append(ar)
                    self._emit_progress(start, str(entry.path), scanned, total, skipped, matched, errors)

        # 强制发送最终进度
        self._emit_progress(start, "", scanned, total, skipped, matched, errors, force=True)

        duration = time.perf_counter() - start
        stats = ScanStats(
            total_files=total,
            scanned_files=scanned,
            matched_files=matched,
            skipped_files=skipped,
            errors=errors,
            duration_seconds=duration,
        )
        return ScanReport(root=root, results=tuple(results), stats=stats)

    def _emit_progress(
        self,
        start: float,
        current_file: str,
        scanned: int,
        total: int,
        skipped: int,
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
        self._on_progress(
            ProgressInfo(
                current_file=current_file,
                scanned=scanned,
                total=total,
                skipped=skipped,
                matched=matched,
                errors=errors,
                elapsed=now - start,
            )
        )

    def _scan_sequential(
        self,
        entries: List[FileEntry],
        results: List[ScanResult],
        start: float,
        total: int,
        skipped: int,
    ) -> Tuple[int, int, int]:
        """单线程顺序扫描，返回 (scanned, matched, errors)。"""
        scanned = 0
        matched = 0
        errors = 0
        for entry in entries:
            try:
                result = self._scan_entry(entry)
                scanned += 1
                if result.has_hit:
                    matched += 1
                errors += result.errors
                results.append(result)
            except Exception:
                errors += 1
                scanned += 1
                logger.warning("扫描文件失败 %s", entry.path, exc_info=True)
            self._emit_progress(start, str(entry.path), scanned, total, skipped, matched, errors)
        return scanned, matched, errors

    def _scan_concurrent(
        self,
        entries: List[FileEntry],
        results: List[ScanResult],
        start: float,
        total: int,
        skipped: int,
    ) -> Tuple[int, int, int]:
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
                entry = future_to_entry[future]
                scanned += 1
                try:
                    result = future.result()
                    if result.has_hit:
                        matched += 1
                    errors += result.errors
                    results.append(result)
                except Exception:
                    errors += 1
                    logger.warning("扫描文件失败 %s", entry.path, exc_info=True)
                self._emit_progress(start, str(entry.path), scanned, total, skipped, matched, errors)
        return scanned, matched, errors

    def scan_file(self, path: Path) -> ScanResult:
        """扫描单个文件。"""
        entry = FileEntry.from_path(path)
        return self._scan_entry(entry)

    def scan_archive(self, path: Path) -> Tuple[ScanResult, ...]:
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
        if any(not rule.file_extensions for rule in self.ruleset.rules):
            return True
        all_extensions = {ext for rule in self.ruleset.rules for ext in rule.file_extensions}
        return entry.extension in all_extensions

    def _scan_entry(self, entry: FileEntry) -> ScanResult:
        """对单个文件应用所有规则，返回扫描结果。"""
        context = MatchContext(entry, content_provider=self._content_provider)
        hits: List[RuleHit] = []
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
