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
from fuscan.scanner.context import FileEntry

__all__ = [
    "MatchResult",
    "ProgressInfo",
    "RuleHit",
    "ScanReport",
    "ScanResult",
    "ScanStats",
    "WalkResult",
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
    # 当前扫描阶段：walk/scan/archive
    phase: str = "scan"
    # 用户标记跳过的文件数：区别于按扩展名/目录过滤的 skipped，
    # 此为用户在结果详情区主动「标记为跳过」后在本次扫描中跳过的文件数
    user_skipped: int = 0

    def summary(self) -> str:
        """返回实时进度状态栏文本（含速度计算）。

        根据 ``phase`` 返回不同文案（iter-79 四阶段命名）：
        walk 阶段（解析目录）突出已发现文件数与白名单跳过数，
        scan 阶段（文件解析）展示完整扫描指标，
        archive 阶段突出压缩包扫描进度。
        """
        if self.phase == "walk":
            return (
                f"解析目录 | 已发现 {self.total} 个文件 | 跳过 {self.skipped} | "
                f"用户跳过 {self.user_skipped} | 已用 {self.elapsed:.1f}s"
            )
        if self.phase == "archive":
            return (
                f"扫描压缩包 | 已扫描 {self.scanned} | 命中 {self.matched} | "
                f"错误 {self.errors} | 已用 {self.elapsed:.1f}s"
            )
        speed = self.scanned / self.elapsed if self.elapsed > 0 else 0.0
        return (
            f"已扫描 {self.scanned} | 跳过 {self.skipped} | 用户跳过 {self.user_skipped} | "
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
    """单个文件的扫描结果。

    ``user_skipped`` 标识该文件是否被用户在结果详情区「标记为跳过」（iter-77）。
    标记后本次扫描结果中仍保留该条目（带跳过标记），下一次扫描起扫描器在遍历
    阶段直接跳过该路径并计入 ``ScanStats.user_skipped`` 统计。

    ``archive_path`` 标识压缩包内部条目（iter-89）：非 None 时表示该结果来自
    压缩包内某个文件，``archive_path`` 指向压缩根本身（可 stat/打开），
    ``path`` 为 ``archive.zip!inner/file.txt`` 格式的展示路径。GUI 据此跳过
    内容预览（避免解压耗时）并展示"压缩包路径 / 内部条目路径"双字段。
    """

    path: Path
    size: int
    hits: tuple[RuleHit, ...] = field(default_factory=tuple)
    errors: int = 0
    # 用户标记跳过标识：True 表示用户已对该文件标记跳过
    user_skipped: bool = False
    # 压缩包根路径：非 None 时标识本结果为压缩包内部条目
    archive_path: Path | None = None

    @property
    def has_hit(self) -> bool:
        return bool(self.hits)

    @property
    def has_error(self) -> bool:
        return self.errors > 0

    @property
    def is_archive_entry(self) -> bool:
        """是否为压缩包内部条目（archive_path 非 None）。"""
        return self.archive_path is not None

    @property
    def inner_path(self) -> str:
        """压缩包内部条目路径（``!`` 后部分）。

        非压缩包内部条目时返回空字符串。压缩包内部条目的 ``path`` 形如
        ``archive.zip!dir/file.txt``，本属性返回 ``dir/file.txt``。

        路径分隔符统一为正斜杠 ``/``（与 ZIP/RAR/7Z 规范一致）：
        Windows 上 ``Path`` 构造会把 ``!`` 后部分的 ``/`` 转成 ``\\``，
        导出与 GUI 展示时需还原为 ``/``，避免 ``dir\\file.txt`` 跨平台不一致。
        """
        if self.archive_path is None:
            return ""
        path_str = str(self.path)
        sep_idx = path_str.find("!")
        if sep_idx < 0:
            return ""
        return path_str[sep_idx + 1 :].replace("\\", "/")

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
        """返回简洁摘要：``N 条规则 / M 处匹配``，已标记跳过时附加前缀。"""
        base = f"{len(self.hits)} 条规则 / {self.total_match_count} 处匹配"
        if self.user_skipped:
            return f"已标记跳过 | {base}"
        return base

    def file_info_html(self, extra: str = "") -> str:
        """返回文件元信息 HTML 片段（供 GUI 详情区/对话框共用）。

        含文件路径、大小、修改时间、命中规则数、匹配条数。
        ``extra`` 用于 GUI 追加自身状态相关的字段（如"可切换位置"数），
        为已格式化的 HTML 片段，将以 `` | `` 分隔附加在末尾。

        压缩包内部条目（``archive_path`` 非 None，iter-89）：跳过 ``stat`` 调用
        （内部条目路径在文件系统不存在），改为显示"压缩包路径 / 内部条目路径"
        双字段，修改时间显示"压缩包内部条目，无法获取"。
        """
        if self.archive_path is not None:
            # 压缩包内部条目：无法 stat，显示压缩包路径与内部条目路径
            info = (
                f"<b>压缩包路径:</b> {html.escape(str(self.archive_path))}<br>"
                f"<b>内部条目路径:</b> {html.escape(self.inner_path)}<br>"
                f"<b>文件大小:</b> {format_size(self.size)} ({self.size} 字节)<br>"
                f"<b>修改时间:</b> 压缩包内部条目，无法获取<br>"
                f"<b>命中规则数:</b> {len(self.hits)} | <b>匹配条数:</b> {self.total_match_count}"
            )
            if extra:
                info += f" | {extra}"
            return info

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
    # 用户标记跳过的文件数：扫描器在遍历阶段跳过用户已标记的路径，
    # 单独统计以区别于按扩展名/目录过滤的 skipped_files
    user_skipped: int = 0
    # 各阶段性能统计（PerfStats 始终启用）：
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
            f"跳过 {self.skipped_files} | 用户跳过 {self.user_skipped} | "
            f"命中 {self.matched_files} | 条数 {self.total_matches} | "
            f"错误 {self.errors} | 耗时 {self.duration_seconds:.2f}s"
        )


@dataclass(frozen=True)
class WalkResult:
    """walk 阶段产物：单根路径遍历收集的待扫描文件清单与统计。

    职责拆分（stats/scan worker 分离）后，``FileStatsWorker`` 执行 walk 阶段
    产出本对象，``ScanWorker`` 接收后跳过 walk 直接进入 scan/archive 阶段，
    使 UI 能更早展示确定的 ``total``，且两 worker 的取消/暂停各自独立。

    :param root: 本次 walk 的根路径
    :param entries: 通过过滤的待扫描文件清单（已剔除 ignored/user_skipped）
    :param total: walk 发现的文件总数（含跳过项，用于进度条总量）
    :param skipped: 按扩展名/目录过滤跳过的文件数
    :param user_skipped: 用户标记跳过的文件数（区别于 skipped）
    :param skipped_dirs: walk 阶段跳过的目录路径元组（最近条目，供 UI 展示）
    :param cancelled: walk 是否被取消
    """

    root: Path
    entries: tuple[FileEntry, ...] = field(default_factory=tuple)
    total: int = 0
    skipped: int = 0
    user_skipped: int = 0
    skipped_dirs: tuple[str, ...] = ()
    cancelled: bool = False


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
        """返回通知消息正文。

        形如 ``发现 3 个文件命中规则，共 7 处匹配``；无命中时返回 ``未发现命中``。
        """
        if not self.hits:
            return "未发现命中"
        return f"发现 {len(self.hits)} 个文件命中规则，共 {self.stats.total_matches} 处匹配"

    def to_format(self, fmt: str) -> str:
        """按格式名渲染报告（``json``/``csv``/``sarif``/``text``）。

        调用方无需维护 if-else 调度，未知格式回退到 ``text``。
        """
        if fmt == "json":
            return self.to_json()
        if fmt == "csv":
            return self.to_csv()
        if fmt == "sarif":
            return self.to_sarif()
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
                filtered.append(
                    ScanResult(
                        path=sr.path,
                        size=sr.size,
                        hits=matching_hits,
                        errors=sr.errors,
                        user_skipped=sr.user_skipped,
                        archive_path=sr.archive_path,
                    )
                )
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
                    "archive_path": str(r.archive_path) if r.archive_path is not None else None,
                    "inner_path": r.inner_path or None,
                    "size": r.size,
                    "max_severity": r.max_severity.value,
                    "match_count": r.total_match_count,
                    "user_skipped": r.user_skipped,
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
        # archive_path/inner_path 列标识压缩包内部条目
        writer.writerow(
            ["path", "archive_path", "inner_path", "size", "severity", "rule", "description", "match_count", "detail"]
        )
        for r in self.hits:
            archive_path_str = str(r.archive_path) if r.archive_path is not None else ""
            inner_path_str = r.inner_path or ""
            for hit in r.hits:
                writer.writerow(
                    [
                        str(r.path),
                        archive_path_str,
                        inner_path_str,
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
            # 压缩包内部条目附加压缩包路径标注
            if result.archive_path is not None:
                lines.append(
                    f"  {rel} [压缩包: {result.archive_path} » {result.inner_path}] "
                    f"(规则 {len(result.hits)} / 条数 {result.total_match_count})"
                )
            else:
                lines.append(f"  {rel} (规则 {len(result.hits)} / 条数 {result.total_match_count})")
            for hit in result.hits:
                # 描述非空时附加在规则名后，便于用户理解匹配规则含义
                rule_label = f"{hit.rule_name} - {hit.match_description}" if hit.match_description else hit.rule_name
                lines.append(f"    [{hit.severity.value}] {rule_label} (条数 {hit.match_count}): {hit.detail}")
        return "\n".join(lines)

    def to_sarif(self) -> str:
        """将扫描报告转换为 SARIF v2.1.0 JSON 字符串。

        SARIF（Static Analysis Results Interchange Format）是 OASIS 标准，
        GitHub Code Scanning 原生支持。每条 RuleHit 映射为一个 SARIF result：

        - ``ruleId``：规则名
        - ``level``：严重等级映射（CRITICAL→error, WARNING→warning, INFO→note）
        - ``message.text``：匹配描述或详情
        - ``locations[0].physicalLocation.artifactLocation.uri``：文件相对路径

        压缩包内部条目在 ``message.text`` 中附加 ``[压缩包: path » inner]`` 标注。
        """
        from fuscan import __version__

        severity_to_level: dict[Severity, str] = {
            Severity.CRITICAL: "error",
            Severity.WARNING: "warning",
            Severity.INFO: "note",
        }

        results: list[dict[str, object]] = []
        for sr in self.hits:
            try:
                uri = str(sr.path.relative_to(self.root))
            except ValueError:
                uri = str(sr.path)

            for hit in sr.hits:
                # 压缩包内部条目附加标注
                if sr.archive_path is not None:
                    msg_text = f"[压缩包: {sr.archive_path} » {sr.inner_path}] {hit.match_description or hit.detail}"
                else:
                    msg_text = hit.match_description or hit.detail

                result_entry: dict[str, object] = {
                    "ruleId": hit.rule_name,
                    "level": severity_to_level.get(hit.severity, "note"),
                    "message": {"text": msg_text},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {
                                    "uri": uri,
                                }
                            }
                        }
                    ],
                    "properties": {
                        "severity": hit.severity.value,
                        "matchCount": hit.match_count,
                        "target": hit.target,
                    },
                }
                results.append(result_entry)

        sarif: dict[str, object] = {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "fuscan",
                            "version": __version__,
                            "informationUri": "https://github.com/gookeryoung/fuscan",
                        }
                    },
                    "results": results,
                }
            ],
        }
        return json.dumps(sarif, ensure_ascii=False, indent=2)
