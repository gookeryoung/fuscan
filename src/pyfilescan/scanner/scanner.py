"""扫描器：协调遍历器与匹配引擎，输出扫描报告。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple

from pyfilescan.rules.model import Rule, RuleSet
from pyfilescan.scanner.context import ContentProvider, FileEntry, MatchContext
from pyfilescan.scanner.matchers import Matcher, build_matcher
from pyfilescan.scanner.result import RuleHit, ScanReport, ScanResult, ScanStats
from pyfilescan.scanner.walker import FileWalker

__all__ = ["Scanner"]

logger = logging.getLogger(__name__)


class Scanner:
    """扫描器：对目录或单文件应用规则集，产出扫描报告。

    - 构造时一次性编译规则集为 Matcher 列表，避免重复编译
    - 支持自定义内容提供器（用于 GUI 阶段进度回调、P1 阶段格式解析）
    - 单线程实现，并发版可在 P5 阶段扩展
    """

    def __init__(
        self,
        ruleset: RuleSet,
        content_provider: Optional[ContentProvider] = None,
        max_depth: Optional[int] = None,
        follow_symlinks: bool = False,
    ) -> None:
        self.ruleset = ruleset
        self._content_provider = content_provider
        self._compiled: List[Tuple[Rule, Matcher]] = [(rule, build_matcher(rule.match)) for rule in ruleset.rules]
        self._walker = FileWalker(
            ignore_dirs=ruleset.ignore_dirs,
            ignore_extensions=ruleset.ignore_extensions,
            max_depth=max_depth,
            follow_symlinks=follow_symlinks,
        )

    def scan(self, root: Path) -> ScanReport:
        """扫描根目录，返回完整报告。"""
        start = time.perf_counter()
        results: List[ScanResult] = []
        total = 0
        scanned = 0
        matched = 0
        skipped = 0
        errors = 0

        for entry in self._walker.walk(root):
            total += 1
            if not self._should_scan(entry):
                skipped += 1
                continue
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

    def scan_file(self, path: Path) -> ScanResult:
        """扫描单个文件。"""
        entry = FileEntry.from_path(path)
        return self._scan_entry(entry)

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
