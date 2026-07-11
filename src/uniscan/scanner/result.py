"""扫描结果数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from uniscan.rules.model import Severity

__all__ = ["MatchResult", "ProgressInfo", "RuleHit", "ScanReport", "ScanResult", "ScanStats"]


@dataclass(frozen=True)
class MatchResult:
    """单次匹配求值结果。"""

    matched: bool
    detail: str = ""


@dataclass(frozen=True)
class ProgressInfo:
    """扫描进度信息（实时反馈给 UI）。"""

    current_file: str = ""
    scanned: int = 0
    total: int = 0
    skipped: int = 0
    matched: int = 0
    errors: int = 0
    elapsed: float = 0.0


@dataclass(frozen=True)
class RuleHit:
    """规则命中记录：一条规则对一个文件的命中信息。"""

    rule_name: str
    severity: Severity
    detail: str


@dataclass(frozen=True)
class ScanResult:
    """单个文件的扫描结果。"""

    path: Path
    size: int
    hits: tuple[RuleHit, ...] = field(default_factory=tuple)
    errors: int = 0

    @property
    def has_hit(self) -> bool:
        return bool(self.hits)

    @property
    def has_error(self) -> bool:
        return self.errors > 0

    @property
    def max_severity(self) -> Severity:
        """该文件命中规则中的最高严重等级。"""
        if not self.hits:
            return Severity.INFO
        order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}
        return max(self.hits, key=lambda h: order[h.severity]).severity


@dataclass(frozen=True)
class ScanStats:
    """扫描统计。"""

    total_files: int = 0
    scanned_files: int = 0
    matched_files: int = 0
    skipped_files: int = 0
    errors: int = 0
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class ScanReport:
    """完整扫描报告。"""

    root: Path
    results: tuple[ScanResult, ...] = field(default_factory=tuple)
    stats: ScanStats = field(default_factory=ScanStats)
    cancelled: bool = False

    @property
    def hits(self) -> tuple[ScanResult, ...]:
        """仅返回有命中的结果。"""
        return tuple(r for r in self.results if r.has_hit)
