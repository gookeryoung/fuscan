"""扫描结果数据结构。

除 ``MatchResult``/``ProgressInfo`` 外，``ScanResult``/``ScanStats``/``ScanReport``
均提供数据层方法（``rule_names``/``filter``/``group_by_*``/``summary``/
``file_info_html``/``notification_message``/``to_format``），将"如何序列化、如何
筛选、如何分组、如何格式化展示文本"下沉到 dataclass，CLI/GUI 仅做展示，
避免展示层重复实现相同逻辑。

模块级 ``format_size`` 为字节数人类可读格式化，原属 GUI 层，提升至数据层后
供 dataclass 与 GUI 共享。

二进制导出（PDF/Excel）已拆分到 :mod:`fuscan.scanner.export`，本模块仅保留
数据结构与文本序列化（csv/json/text）。
"""

from __future__ import annotations

import csv
import datetime
import html
import io
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fuscan.rules.model import Severity

__all__ = [
    "MatchResult",
    "ProgressInfo",
    "RuleHit",
    "ScanReport",
    "ScanResult",
    "ScanStats",
    "format_size",
]


def format_size(size: int) -> str:
    """将字节数格式化为人类可读字符串（B/KB/MB/GB）。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


@dataclass(frozen=True)
class MatchResult:
    """单次匹配求值结果。

    ``match_text`` 存储匹配到的原始文本（regex 模式为首个 ``m.group(0)``，
    其他模式为 ``pattern``），供 GUI 高亮定位使用，避免 ``repr`` 转义导致的失真。
    ``match_count`` 为该次求值实际匹配到的文本条数（同一规则在同一文件中
    可能匹配多处，如多处密码、多处密钥），用于区分"命中规则数"与"匹配条数"。
    ``target`` 为匹配目标类型（"filename"/"content"/"path"），叶子匹配器设置，
    组合规则为空字符串。GUI 根据 ``target`` 判断是否在内容预览中搜索高亮位置。
    ``match_texts`` 为该次求值命中的所有文本（去重保序，含组合规则子匹配文本），
    用于 AND/OR 等组合规则标记每个命中的内容，避免仅标记单一匹配规则的内容。
    ``match_description`` 为该匹配项的可选描述（来自 MatchSpec.description），
    便于用户理解匹配规则含义，在 GUI 详情表与导出结果中展示。
    """

    matched: bool
    detail: str = ""
    match_text: str = ""
    match_count: int = 1
    target: str = ""
    match_texts: tuple[str, ...] = ()
    match_description: str = ""


@dataclass(frozen=True)
class ProgressInfo:
    """扫描进度信息（实时反馈给 UI）。

    ``phase`` 标识当前扫描阶段，GUI 据此显示不同提示文案，避免用户在
    walk 阶段（已扫描=0）误以为扫描卡住：

    - ``"walk"``：阶段 1，遍历目录树收集待扫描文件清单
    - ``"scan"``：阶段 2，并发/顺序解析文件内容
    - ``"archive"``：阶段 3，扫描压缩包内条目
    """

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
    # 当前扫描阶段（iter-75）：walk/scan/archive
    phase: str = "scan"

    def summary(self) -> str:
        """返回实时进度状态栏文本（含速度计算）。

        根据 ``phase`` 返回不同文案：walk 阶段突出"正在分析目录结构"，
        scan 阶段展示完整扫描指标，archive 阶段突出"正在扫描压缩包"。
        与 ``ScanStats.summary`` 不同，此处不含 ``total_files`` 与 ``cancelled``
        前缀，仅展示当前已扫描的实时指标，供 GUI 进度回调直接调用。
        """
        if self.phase == "walk":
            return f"正在分析目录结构 | 已发现 {self.total} 个文件 | 跳过 {self.skipped} | 已用 {self.elapsed:.1f}s"
        if self.phase == "archive":
            return (
                f"正在扫描压缩包 | 已扫描 {self.scanned} | 命中 {self.matched} | "
                f"错误 {self.errors} | 已用 {self.elapsed:.1f}s"
            )
        speed = self.scanned / self.elapsed if self.elapsed > 0 else 0.0
        return (
            f"已扫描 {self.scanned} | 跳过 {self.skipped} | "
            f"命中 {self.matched} | 条数 {self.matches} | 错误 {self.errors} | "
            f"已用 {self.elapsed:.1f}s | 速度 {speed:.0f} 文件/s"
        )


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
    ``match_texts`` 为该规则命中的所有文本（去重保序，含组合规则子匹配文本），
    用于 AND/OR 等组合规则标记每个命中的内容。
    ``match_description`` 为该匹配项的可选描述（来自 MatchSpec.description），
    便于用户理解匹配规则含义，在 GUI 详情表与导出结果中展示。
    """

    rule_name: str
    severity: Severity
    detail: str
    match_text: str = ""
    match_count: int = 1
    target: str = ""
    match_texts: tuple[str, ...] = ()
    match_description: str = ""


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

    @property
    def rule_names(self) -> tuple[str, ...]:
        """该文件命中的规则名（按出现顺序去重）。"""
        seen: set[str] = set()
        names: list[str] = []
        for h in self.hits:
            if h.rule_name not in seen:
                seen.add(h.rule_name)
                names.append(h.rule_name)
        return tuple(names)

    def summary(self) -> str:
        """返回简洁摘要：``N 条规则 / M 处匹配``。"""
        return f"{len(self.hits)} 条规则 / {self.total_match_count} 处匹配"

    def file_info_html(self, extra: str = "") -> str:
        """返回文件元信息 HTML 片段（供 GUI 详情区/对话框共用）。

        含文件路径、大小、修改时间、命中规则数、匹配条数。
        ``extra`` 用于 GUI 追加自身状态相关的字段（如"可切换位置"数），
        为已格式化的 HTML 片段，将以 `` | `` 分隔附加在末尾。

        修改时间通过 ``Path.stat`` 获取，失败时显示"无法获取"。
        """
        try:
            mtime = datetime.datetime.fromtimestamp(self.path.stat().st_mtime)
            mtime_str = mtime.strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            mtime_str = "无法获取"

        info = (
            f"<b>文件路径:</b> {html.escape(str(self.path))}<br>"
            f"<b>文件大小:</b> {format_size(self.size)} ({self.size} 字节)<br>"
            f"<b>修改时间:</b> {html.escape(mtime_str)}<br>"
            f"<b>命中规则数:</b> {len(self.hits)} | <b>匹配条数:</b> {self.total_match_count}"
        )
        if extra:
            info += f" | {extra}"
        return info


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
    # 各阶段性能统计（iter-66 起 PerfStats 始终启用）：
    # {stage_name: {"total_ms": float, "count": int, "max_ms": float}}
    # None 表示未采集（如测试构造的 ScanStats）；空 dict 表示扫描无数据
    perf_summary: dict[str, dict[str, float]] | None = None

    @property
    def speed(self) -> float:
        """扫描吞吐量（文件/秒），duration为0时返回0.0。"""
        return self.scanned_files / self.duration_seconds if self.duration_seconds > 0 else 0.0

    def summary(self, *, cancelled: bool = False) -> str:
        """返回状态栏摘要文本。

        :param cancelled: 是否在摘要前缀"已取消"，GUI/CLI 取消场景共用。
        """
        prefix = "已取消" if cancelled else "完成"
        return (
            f"{prefix}: 总计 {self.total_files} | 扫描 {self.scanned_files} | "
            f"跳过 {self.skipped_files} | 命中 {self.matched_files} | "
            f"条数 {self.total_matches} | 错误 {self.errors} | "
            f"耗时 {self.duration_seconds:.2f}s"
        )


@dataclass(frozen=True)
class ScanReport:
    """完整扫描报告。

    提供数据层操作（``filter``/``group_by_*``/``to_format``/``rule_names``/``summary``），
    将序列化、筛选、分组逻辑下沉到 dataclass，CLI/GUI 仅做展示，
    避免展示层重复实现相同逻辑。
    """

    root: Path
    results: tuple[ScanResult, ...] = field(default_factory=tuple)
    stats: ScanStats = field(default_factory=ScanStats)
    cancelled: bool = False

    @property
    def hits(self) -> tuple[ScanResult, ...]:
        """仅返回有命中的结果。"""
        return tuple(r for r in self.results if r.has_hit)

    @property
    def rule_names(self) -> tuple[str, ...]:
        """所有命中结果涉及的规则名（按首次出现顺序去重）。"""
        seen: set[str] = set()
        names: list[str] = []
        for r in self.hits:
            for name in r.rule_names:
                if name not in seen:
                    seen.add(name)
                    names.append(name)
        return tuple(names)

    def summary(self) -> str:
        """返回状态栏摘要文本（自动识别 ``cancelled`` 标志）。"""
        return self.stats.summary(cancelled=self.cancelled)

    def notification_message(self) -> str:
        """返回托盘/通知消息正文。

        形如 ``发现 3 个文件命中规则，共 7 处匹配``；无命中时返回 ``未发现命中``。
        """
        if not self.hits:
            return "未发现命中"
        return f"发现 {len(self.hits)} 个文件命中规则，共 {self.stats.total_matches} 处匹配"

    def to_format(self, fmt: str) -> str:
        """按格式名渲染报告（``json``/``csv``/``text``）。

        调用方无需维护 if-else 调度，未知格式回退到 ``text``。
        """
        if fmt == "json":
            return self.to_json()
        if fmt == "csv":
            return self.to_csv()
        return self.to_text()

    def filter(self, path_query: str = "", rule_name: str = "") -> ScanReport:
        """按路径子串与规则名筛选，返回新的 ScanReport（不修改原对象）。

        - ``path_query``：大小写不敏感的路径子串，空字符串跳过路径过滤
        - ``rule_name``：规则名精确匹配；非空时仅保留该规则命中，
          且每个 ScanResult 的 hits 被过滤为仅该规则的命中

        stats 不变（仍代表整体扫描统计），仅 results 被过滤。
        """
        query = path_query.strip().lower()
        if not query and not rule_name:
            return self
        filtered: list[ScanResult] = []
        for sr in self.hits:
            if query and query not in str(sr.path).lower():
                continue
            if rule_name:
                matching_hits = tuple(h for h in sr.hits if h.rule_name == rule_name)
                if not matching_hits:
                    continue
                filtered.append(ScanResult(path=sr.path, size=sr.size, hits=matching_hits, errors=sr.errors))
            else:
                filtered.append(sr)
        return ScanReport(
            root=self.root,
            results=tuple(filtered),
            stats=self.stats,
            cancelled=self.cancelled,
        )

    def group_by_rule(self) -> dict[str, list[tuple[ScanResult, RuleHit]]]:
        """按规则名分组：``{规则名: [(ScanResult, RuleHit), ...]}``。

        同一文件若被同一规则多次命中（理论上不会，但保留兼容）会重复出现；
        同一规则在不同文件的命中分别作为列表项。
        """
        groups: dict[str, list[tuple[ScanResult, RuleHit]]] = {}
        for sr in self.hits:
            for hit in sr.hits:
                groups.setdefault(hit.rule_name, []).append((sr, hit))
        return groups

    def group_by_severity(self) -> dict[Severity, list[ScanResult]]:
        """按文件最高严重等级分组：``{Severity: [ScanResult, ...]}``。"""
        groups: dict[Severity, list[ScanResult]] = {}
        for sr in self.hits:
            groups.setdefault(sr.max_severity, []).append(sr)
        return groups

    def to_json(self) -> str:
        """将扫描报告转换为 JSON 字符串。"""
        data = {
            "root": str(self.root),
            "stats": asdict(self.stats),
            "cancelled": self.cancelled,
            "hits": [
                {
                    "path": str(r.path),
                    "size": r.size,
                    "max_severity": r.max_severity.value,
                    "match_count": r.total_match_count,
                    "rules": [asdict(h) for h in r.hits],
                }
                for r in self.hits
            ],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def to_csv(self) -> str:
        """将扫描报告转换为 CSV 字符串（每行一条规则命中）。"""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["path", "size", "severity", "rule", "description", "match_count", "detail"])
        for r in self.hits:
            for hit in r.hits:
                writer.writerow(
                    [
                        str(r.path),
                        r.size,
                        hit.severity.value,
                        hit.rule_name,
                        hit.match_description,
                        hit.match_count,
                        hit.detail,
                    ]
                )
        return buf.getvalue()

    def to_text(self) -> str:
        """将扫描报告转换为可读文本（含路径、统计与逐文件命中详情）。"""
        lines: list[str] = []
        lines.append(f"扫描路径: {self.root}")
        lines.append(f"统计: {self.stats.summary(cancelled=self.cancelled)}")
        lines.append("")
        if not self.hits:
            lines.append("未发现命中项。")
            return "\n".join(lines)
        lines.append(f"命中项 ({len(self.hits)}):")
        for result in self.hits:
            try:
                rel = result.path.relative_to(self.root)
            except ValueError:
                rel = result.path
            lines.append(f"  {rel} (规则 {len(result.hits)} / 条数 {result.total_match_count})")
            for hit in result.hits:
                # 描述非空时附加在规则名后，便于用户理解匹配规则含义
                rule_label = f"{hit.rule_name} - {hit.match_description}" if hit.match_description else hit.rule_name
                lines.append(f"    [{hit.severity.value}] {rule_label} (条数 {hit.match_count}): {hit.detail}")
        return "\n".join(lines)
