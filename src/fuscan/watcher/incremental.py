"""增量扫描器：委托 Scanner + CacheStore 实现基于内容哈希的增量扫描。

cache 不为 None 时，Scanner 内部使用哈希缓存跳过未变化文件（文件哈希 + 规则哈希
均未变则直接复用结果）；cache 为 None 时退化为全量扫描。
状态持久化由 SQLite 缓存处理，``save_state``/``load_state`` 为空操作（保留签名兼容 TrayApp）。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fuscan.rules.model import RuleSet
from fuscan.scanner import ScanReport, ScanResult, ScanStats
from fuscan.scanner.context import FileEntry
from fuscan.scanner.scanner import Scanner

if TYPE_CHECKING:
    from fuscan.cache import CacheStore

__all__ = ["IncrementalScanner"]

logger = logging.getLogger(__name__)


class IncrementalScanner:
    """增量扫描器：委托 Scanner + CacheStore 实现哈希缓存增量扫描。

    使用方式：

    1. 构造时传入 ``cache`` 启用哈希缓存（推荐）
    2. ``scan(root)`` 扫描目录，``scan_paths(paths)`` 扫描指定文件列表
    3. ``save_state``/``load_state`` 为空操作，缓存由 SQLite 持久化
    """

    def __init__(  # noqa: PLR0913
        self,
        ruleset: RuleSet,
        max_depth: int | None = None,
        scan_archives: bool = False,
        ignore_dirs: tuple[str, ...] = (),
        ignore_extensions: tuple[str, ...] = (),
        cache: CacheStore | None = None,
    ) -> None:
        self._cache: CacheStore | None = cache
        self._scanner = Scanner(
            ruleset,
            max_depth=max_depth,
            scan_archives=scan_archives,
            ignore_dirs=ignore_dirs,
            ignore_extensions=ignore_extensions,
            cache=cache,
        )

    @property
    def tracked_count(self) -> int:
        """已跟踪的文件数量（基于缓存统计，无缓存时返回 0）。"""
        if self._cache is None:
            return 0
        return self._cache.stats().scanned_files

    def scan(self, root: Path) -> ScanReport:
        """扫描目录，委托 Scanner 处理缓存命中与规则匹配。"""
        return self._scanner.scan(root)

    def scan_paths(self, paths: list[Path]) -> ScanReport:
        """扫描指定路径列表（由文件监控触发）。

        跳过不存在、目录、扩展名不匹配的文件。
        """
        start = time.perf_counter()
        results: list[ScanResult] = []
        scanned = 0
        matched = 0
        errors = 0
        matches = 0

        for path in paths:
            if not path.exists() or path.is_dir():
                continue
            entry = FileEntry.from_path(path)
            if not self._scanner._should_scan(entry):
                continue
            try:
                result = self._scanner.scan_file(path)
                scanned += 1
                if result.has_hit:
                    matched += 1
                    matches += result.total_match_count
                errors += result.errors
                results.append(result)
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
            total_matches=matches,
        )
        return ScanReport(root=Path(), results=tuple(results), stats=stats)

    def mark_scanned(self, _path: Path, _mtime: float) -> None:
        """空操作：缓存模式下由 CacheStore 自动登记文件。"""

    def remove_path(self, _path: Path) -> None:
        """空操作：缓存基于内容哈希，与路径无关。"""

    def save_state(self, _path: Path) -> None:
        """空操作：缓存由 SQLite 持久化，无需额外状态文件。"""

    def load_state(self, _path: Path) -> None:
        """空操作：缓存由 SQLite 持久化，无需额外状态文件。"""
