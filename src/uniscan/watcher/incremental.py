"""增量扫描器：仅扫描新增或修改的文件。

维护已扫描文件状态（路径 + mtime），每次扫描时跳过未变化的文件，
只对新增或修改的文件应用规则。适用于托盘驻守场景下的持续监控。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from uniscan.rules.model import Rule, RuleSet
from uniscan.scanner import ScanReport, ScanResult, ScanStats
from uniscan.scanner.context import FileEntry
from uniscan.scanner.matchers import Matcher, build_matcher
from uniscan.scanner.result import RuleHit
from uniscan.scanner.scanner import default_extract_content
from uniscan.scanner.walker import FileWalker

__all__ = ["FileState", "IncrementalScanner"]

logger = logging.getLogger(__name__)


@dataclass
class FileState:
    """文件扫描状态：路径 + 最后修改时间。"""

    path: Path
    mtime: float


class IncrementalScanner:
    """增量扫描器：跳过未变化文件，仅扫描新增/修改文件。

    使用方式：

    1. 首次调用 ``scan()`` 扫描全部文件并记录状态
    2. 后续调用仅扫描 mtime 变化的文件
    3. 可通过 ``scan_paths()`` 扫描指定路径列表（由文件监控触发）
    4. ``save_state()`` / ``load_state()`` 持久化状态
    """

    def __init__(
        self,
        ruleset: RuleSet,
        max_depth: int | None = None,
        scan_archives: bool = False,
    ) -> None:
        self._ruleset = ruleset
        self._max_depth = max_depth
        self._scan_archives = scan_archives
        self._compiled: list[tuple[Rule, Matcher]] = [(rule, build_matcher(rule.match)) for rule in ruleset.rules]
        self._walker = FileWalker(
            ignore_dirs=ruleset.ignore_dirs,
            ignore_extensions=ruleset.ignore_extensions,
            max_depth=max_depth,
        )
        self._file_states: dict[str, float] = {}  # path_str -> mtime

    @property
    def tracked_count(self) -> int:
        """已跟踪的文件数量。"""
        return len(self._file_states)

    def scan(self, root: Path) -> ScanReport:
        """增量扫描目录，跳过未变化文件。"""
        import time

        start = time.perf_counter()
        results: list[ScanResult] = []
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

            path_str = str(entry.path)

            # 增量判断：mtime 未变化则跳过
            if path_str in self._file_states and abs(entry.mtime - self._file_states[path_str]) < 0.001:
                skipped += 1
                continue

            try:
                result = self._scan_entry(entry)
                scanned += 1
                if result.has_hit:
                    matched += 1
                errors += result.errors
                results.append(result)
                # 更新状态
                self._file_states[path_str] = entry.mtime
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

    def scan_paths(self, paths: list[Path]) -> ScanReport:
        """扫描指定路径列表（由文件监控触发）。"""
        import time

        start = time.perf_counter()
        results: list[ScanResult] = []
        scanned = 0
        matched = 0
        errors = 0

        for path in paths:
            if not path.exists() or path.is_dir():
                continue
            entry = FileEntry.from_path(path)
            if not self._should_scan(entry):
                continue
            try:
                result = self._scan_entry(entry)
                scanned += 1
                if result.has_hit:
                    matched += 1
                errors += result.errors
                results.append(result)
                self._file_states[str(path)] = entry.mtime
            except Exception:
                errors += 1
                scanned += 1
                logger.warning("扫描文件失败 %s", path, exc_info=True)

        duration = time.perf_counter() - start
        stats = ScanStats(
            scanned_files=scanned,
            matched_files=matched,
            errors=errors,
            duration_seconds=duration,
        )
        return ScanReport(root=Path(), results=tuple(results), stats=stats)

    def mark_scanned(self, path: Path, mtime: float) -> None:
        """手动标记文件为已扫描。"""
        self._file_states[str(path)] = mtime

    def remove_path(self, path: Path) -> None:
        """从跟踪状态中移除路径（文件删除时调用）。"""
        self._file_states.pop(str(path), None)

    def save_state(self, path: Path) -> None:
        """持久化扫描状态到 JSON 文件。"""
        data = dict(self._file_states)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_state(self, path: Path) -> None:
        """从 JSON 文件加载扫描状态。"""
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._file_states = {k: float(v) for k, v in data.items()}
            logger.info("加载扫描状态: %d 个文件", len(self._file_states))
        except (json.JSONDecodeError, ValueError, OSError):
            logger.warning("扫描状态文件损坏，忽略", exc_info=True)

    def _should_scan(self, entry: FileEntry) -> bool:
        """根据规则集的 file_extensions 限制决定是否扫描。"""
        if entry.is_dir:
            return False
        if any(not rule.file_extensions for rule in self._ruleset.rules):
            return True
        all_extensions = {ext for rule in self._ruleset.rules for ext in rule.file_extensions}
        return entry.extension in all_extensions

    def _scan_entry(self, entry: FileEntry) -> ScanResult:
        """对单个文件应用所有规则。"""
        from uniscan.scanner.context import MatchContext

        context = MatchContext(entry, content_provider=default_extract_content)
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
