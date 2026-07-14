"""扫描结果数据结构。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fuscan.rules.model import Severity

__all__ = ["MatchResult", "ProgressInfo", "RuleHit", "ScanReport", "ScanResult", "ScanStats"]


@dataclass(frozen=True)
class MatchResult:
    """单次匹配求值结果。

    ``match_text`` 存储匹配到的原始文本（regex 模式为首个 ``m.group(0)``，
    其他模式为 ``pattern``），供 GUI 高亮定位使用，避免 ``repr`` 转义导致的失真。
    ``match_count`` 为该次求值实际匹配到的文本条数（同一规则在同一文件中
    可能匹配多处，如多处密码、多处密钥），用于区分"命中规则数"与"匹配条数"。
    ``target`` 为匹配目标类型（"filename"/"content"/"path"），叶子匹配器设置，
    组合规则为空字符串。GUI 根据 ``target`` 判断是否在内容预览中搜索高亮位置。
    """

    matched: bool
    detail: str = ""
    match_text: str = ""
    match_count: int = 1
    target: str = ""


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
    # 匹配文本条数（同一规则在同一文件的多处匹配分别计数）
    matches: int = 0
    # 跳过的目录路径（最近 500 条，避免无限增长）
    skipped_dirs: tuple[str, ...] = ()
    # 命中的 (文件路径, 规则名) 列表（最近 500 条）
    matched_files: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RuleHit:
    """规则命中记录：一条规则对一个文件的命中信息。

    ``match_text`` 为匹配到的原始文本，供 GUI 高亮定位使用；
    对于组合规则（and/or/not）无单一匹配文本时为空字符串。
    ``match_count`` 为该规则在该文件实际匹配到的文本条数（如多处密码各算 1 条），
    用于区分"命中规则数"与"匹配条数"，避免两者不对等时产生歧义。
    ``target`` 为匹配目标类型（"filename"/"content"/"path"），叶子匹配器设置，
    组合规则为空字符串。GUI 据 ``target=="filename"`` 判断是否在内容预览中
    搜索高亮位置——文件名匹配不应在内容中搜索高亮，否则可能产生误导。
    """

    rule_name: str
    severity: Severity
    detail: str
    match_text: str = ""
    match_count: int = 1
    target: str = ""


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

    @property
    def total_match_count(self) -> int:
        """该文件所有命中规则的匹配文本条数之和。"""
        return sum(h.match_count for h in self.hits)


@dataclass(frozen=True)
class ScanStats:
    """扫描统计。"""

    total_files: int = 0
    scanned_files: int = 0
    matched_files: int = 0
    skipped_files: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    # 所有命中规则的匹配文本条数总和（区别于 matched_files 的命中文件数）
    total_matches: int = 0


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

    @classmethod
    def to_json(cls, report: ScanReport) -> str:
        """将扫描报告转换为 JSON 字符串。"""
        data = {
            "root": str(report.root),
            "stats": asdict(report.stats),
            "hits": [
                {
                    "path": str(r.path),
                    "size": r.size,
                    "max_severity": r.max_severity.value,
                    "rules": [asdict(h) for h in r.hits],
                }
                for r in report.hits
            ],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)
