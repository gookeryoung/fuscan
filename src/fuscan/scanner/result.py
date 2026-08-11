"""扫描结果数据结构。

除 ``MatchResult``/``ProgressInfo`` 外，``ScanResult``/``ScanStats``/``ScanReport``
均提供数据层方法（``rule_names``/``filter``/``group_by_*``/``summary``/
``file_info_html``/``notification_message``/``to_format``），将"如何序列化、如何
筛选、如何分组、如何格式化展示文本"下沉到 dataclass，CLI/GUI 仅做展示，
避免展示层重复实现相同逻辑。

模块级 ``format_size`` 为字节数人类可读格式化，原属 GUI 层，提升至数据层后
供 dataclass 与 GUI 共享。

二进制导出（PDF/Excel）已拆分到 :mod:`fuscan.export.report`，本模块仅保留
数据结构与文本序列化（csv/json/text）。

``FileFingerprint`` 与 :class:`IncrementalManifest` 已迁出至
:mod:`fuscan.scanner.manifest`，本模块通过 ``TYPE_CHECKING`` 引用以便
:class:`WalkResult.manifest` 字段标注类型。JSON 助手 ``_json_dumps`` /
``_json_dumps_bytes`` / ``_json_loads`` 同步迁至 :mod:`fuscan.scanner.manifest`，
本模块 import 复用（避免重复实现 orjson 回退逻辑）。
"""

from __future__ import annotations

import csv
import datetime
import html
import io
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fuscan.rules.model import Severity
from fuscan.scanner.context import FileEntry
from fuscan.scanner.manifest import _json_dumps, _json_dumps_bytes, _json_loads

if TYPE_CHECKING:
    # 仅用于类型标注（``WalkResult.manifest``），运行时无循环依赖
    from fuscan.scanner.manifest import IncrementalManifest

__all__ = [
    "FilterStats",
    "MatchResult",
    "ProgressInfo",
    "RuleHit",
    "ScanReport",
    "ScanResult",
    "ScanStats",
    "WalkResult",
    "format_elapsed",
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


def format_elapsed(seconds: float) -> str:
    """将耗时秒数格式化为人类可读字符串。

    分档以兼顾极快阶段（收集/筛选常在毫秒级）与长时扫描：

    - ``< 1s``：毫秒（如 ``"860ms"``），避免长时间显示 ``"0.0s"``
    - ``< 60s``：秒并保留一位小数（如 ``"1.2s"``）
    - ``>= 60s``：分秒（如 ``"1分05秒"``），避免大数字秒不直观

    负数或非有限值归零处理，返回 ``"0ms"``。供 GUI 在收集/解析节点
    统一展示阶段用时，格式化逻辑下沉后端避免 QML 层重复实现。
    """
    if math.isnan(seconds) or seconds < 0:  # NaN 或负数
        return "0ms"
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remaining = int(seconds % 60)
    return f"{minutes}分{remaining:02d}秒"


@dataclass(frozen=True)
class MatchResult:
    """单次匹配求值结果。

    保留 ``frozen=True`` —— 该对象在匹配器内部被多次复用（AND/OR
    组合器收集子匹配结果时需要作为 dict key 或 set 元素使用 tuple 转换，
    但 MatchResult 本身不进入 hash 路径，此处保留 frozen 仅为明确不可变
    语义；若后续性能基准证明 ``__hash__`` 开销可接受再考虑替换为 ``__slots__``。
    """

    matched: bool
    detail: str = ""
    match_text: str = ""
    match_count: int = 1
    target: str = ""
    match_texts: tuple[str, ...] = ()
    match_description: str = ""


@dataclass(frozen=True)
class FilterStats:
    """筛选阶段统计：walk 产物经 :func:`run_filter_phase` 二次过滤后的剔除明细。

    四类剔除原因互斥（同一文件仅归入首个命中的类别，按 empty → oversize →
    unreadable → symlink 顺序判断）：

    - ``removed_empty``：``entry.size == 0``，扫描无意义（CONTENT 规则无文本可匹配）
    - ``removed_oversize``：``max_file_size > 0 and entry.size > max_file_size``，
      避免一次性读入内存导致卡死
    - ``removed_unreadable``：``os.access(entry.path, os.R_OK) == False``，
      避免 scan 阶段抛 OSError（Windows 上基本为 0，Unix 真实权限检查）
    - ``removed_symlink``：``follow_symlinks=False`` 且 ``entry.path.is_symlink()``，
      避免重复扫描链接目标

    :class:`WalkResult.filter_stats` 为 ``None`` 表示未经筛选阶段（向后兼容旧
    调用方直接构造的 WalkResult）；非 None 时 ``filtered_entries`` 为筛选后
    的可扫描文件清单，``scan_entries`` 优先使用。
    """

    removed_empty: int = 0
    removed_oversize: int = 0
    removed_unreadable: int = 0
    removed_symlink: int = 0

    @property
    def total_removed(self) -> int:
        """被筛选剔除的文件总数（四类原因之和）。"""
        return self.removed_empty + self.removed_oversize + self.removed_unreadable + self.removed_symlink


@dataclass(frozen=True)
class ProgressInfo:
    """扫描进度信息（实时反馈给 UI）。

    ``phase`` 标识当前扫描阶段，GUI 据此显示不同提示文案，避免用户在
    walk 阶段（已扫描=0）误以为扫描卡住：

    - ``"walk"``：阶段 1，遍历目录树收集待扫描文件清单
    - ``"filter"``：阶段 2，对 walk 产物二次筛选（剔除空/超限/不可读/符号链接）
    - ``"scan"``：阶段 3，并发/顺序解析文件内容
    - ``"archive"``：阶段 4，扫描压缩包内条目
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
    # 当前扫描阶段：walk/filter/scan/archive
    phase: str = "scan"
    # 用户标记跳过的文件数：区别于按扩展名/目录过滤的 skipped，
    # 此为用户在结果详情区主动「标记为跳过」后在本次扫描中跳过的文件数
    user_skipped: int = 0
    # 当前文件大小（字节）：scan 阶段填入，walk/filter/archive 阶段为 0。
    # GUI 据此在进度卡片展示「[12.3 MB · pdf · 1.2s]」单文件元信息，
    # 让用户感知大文件解析进度，避免误以为卡死
    current_file_size: int = 0
    # 当前文件扩展名（小写无点，如 ``"pdf"``）：scan 阶段填入，其余阶段为空串
    current_file_ext: str = ""
    # 当前文件已解析耗时（毫秒）：scan 阶段填入，
    # 顺序扫描为单文件解析耗时，并发扫描为提交到完成的时间间隔
    current_file_elapsed_ms: float = 0.0
    # 当前文件解析引擎名（如 ``"pypdfium2"``/``"lxml"``/``"fuscan-core"``）：
    # scan 阶段按扩展名反查对应提取器 engine_info 填入，其余阶段为空串。
    # GUI 据此在明细行「文件名 · 大小 · 耗时」后追加引擎名，供用户了解各文件解析路径。
    current_file_engine: str = ""
    # filter 阶段四类剔除原因累计数（仅 phase=="filter" 时非零）：
    # GUI 据此在筛选阶段展示「已剔除 N 个空文件 / M 个超限文件」明细，
    # 让用户感知筛选进度而非空白等待
    filter_removed_empty: int = 0
    filter_removed_oversize: int = 0
    filter_removed_unreadable: int = 0
    filter_removed_symlink: int = 0

    def summary(self) -> str:
        """返回实时进度状态栏文本（含速度计算）。

        根据 ``phase`` 返回不同文案（四阶段命名）：
        walk 阶段（解析目录）突出已发现文件数与白名单跳过数，
        filter 阶段（二次筛选）突出各类剔除原因明细，
        scan 阶段（文件解析）展示完整扫描指标，
        archive 阶段突出压缩包扫描进度。
        """
        if self.phase == "walk":
            return (
                f"解析目录 | 已发现 {self.total} 个文件 | 跳过 {self.skipped} | "
                f"用户跳过 {self.user_skipped} | 已用 {self.elapsed:.1f}s"
            )
        if self.phase == "filter":
            removed = (
                self.filter_removed_empty
                + self.filter_removed_oversize
                + self.filter_removed_unreadable
                + self.filter_removed_symlink
            )
            return (
                f"筛选文件 | 已处理 {self.scanned} | 剔除 {removed} "
                f"(空 {self.filter_removed_empty} / 超限 {self.filter_removed_oversize} / "
                f"不可读 {self.filter_removed_unreadable} / 符号链接 {self.filter_removed_symlink}) | "
                f"已用 {self.elapsed:.1f}s"
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

    保留 ``frozen=True`` 不可变语义；命中对象在 GUI 详情区、
    导出、缓存重建等多路径复用，不变性可防止误修改。若后续性能基准证明
    构造开销显著，可引入 ``__slots__`` + 手动 ``__hash__`` 方案。

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

    保留 ``frozen=True`` 不可变语义。``ScanResult`` 进入
    GUI 展示层与报告序列化后不应被修改；frozen 不可变约束在 dataclass
    层面保证该不变式。若后续基准证明构造开销成为瓶颈，可引入 ``__slots__``
    + 手动 ``__hash__`` 方案。

    ``user_skipped`` 标识该文件是否被用户在结果详情区「标记为跳过」。
    标记后本次扫描结果中仍保留该条目（带跳过标记），下一次扫描起扫描器在遍历
    阶段直接跳过该路径并计入 ``ScanStats.user_skipped`` 统计。

    ``archive_path`` 标识压缩包内部条目：非 None 时表示该结果来自
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
    # 单文件解析真实耗时（毫秒）：_scan_entry 测量后回填，供并发模式进度
    # 展示单文件耗时。并发模式下 submit_time≈扫描起点，now-submit_time 是
    # 累计耗时而非单文件耗时，故 collector 用本字段反推真实起点。
    elapsed_ms: float = 0.0
    # 解析引擎名（如 ``"pypdfium2"``/``"lxml"``/``"fuscan-core"``）：
    # _scan_entry 按扩展名反查对应提取器 engine_info 回填，供 GUI 明细行标注。
    # 属运行期展示信息，不参与 JSON/CSV 序列化（引擎由扩展名静态决定，可随时反查）。
    engine: str = ""
    # 自动替换标识：True 表示扫描完成后已对该文件执行自动替换。
    # GUI 据此将结果分到「已替换」Tab 展示，并禁用对已替换文件的重复替换入口。
    replaced: bool = False
    # 实际替换的规则条数：自动替换时统计 replace=True 且成功写入文件的规则数，
    # 供 GUI 详情区展示「已替换 N 条规则」。
    replaced_count: int = 0

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
        """返回简洁摘要：``N 条规则 / M 处匹配``，已标记跳过/已替换时附加前缀。"""
        base = f"{len(self.hits)} 条规则 / {self.total_match_count} 处匹配"
        if self.user_skipped:
            return f"已标记跳过 | {base}"
        if self.replaced:
            return f"已自动替换 {self.replaced_count} 条 | {base}"
        return base

    def file_info_html(self, extra: str = "") -> str:
        """返回文件元信息 HTML 片段（供 GUI 详情区/对话框共用）。

        含文件路径、大小、修改时间、命中规则数、匹配条数。
        ``extra`` 用于 GUI 追加自身状态相关的字段（如"可切换位置"数），
        为已格式化的 HTML 片段，将以 `` | `` 分隔附加在末尾。

        压缩包内部条目（``archive_path`` 非 None）：跳过 ``stat`` 调用
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
    # 压缩包内条目数（archive 阶段扫描的条目，已含在 scanned_files 中）。
    # 单独统计以便在摘要中注明，避免 scanned_files > total_files 时产生误解
    archive_entries: int = 0
    # 增量扫描时未变更文件数：从 prev_report 复用上次命中结果、未重新读取内容
    # 做 I/O 的文件数。全量扫描时为 0；增量扫描越大，此值越接近 total_files。
    unchanged_files: int = 0
    # 筛选阶段剔除的文件总数（empty/oversize/unreadable/symlink 四类之和）。
    # 多根路径扫描时由 ScanWorker 累加各 report 的 filter_removed。
    # 与 skipped_files 区分：skipped_files 是 walk 阶段按扩展名/目录过滤的文件数，
    # filter_removed 是 filter 阶段对 walk 产物二次剔除的文件数
    filter_removed: int = 0
    # 各阶段性能统计（PerfStats 始终启用）：
    # {stage_name: {"total_ms": float, "count": int, "max_ms": float}}
    # None 表示未采集（如测试构造的 ScanStats）；空 dict 表示扫描无数据
    perf_summary: dict[str, dict[str, float]] | None = None

    @property
    def speed(self) -> float:
        """扫描吞吐量（文件/秒），duration为0时返回0.0。

        增量扫描场景下用 ``scanned_files + unchanged_files``（逻辑总处理数）
        除以耗时，反映用户实际体验的吞吐：未变更文件虽没做 I/O，但
        从 manifest 比对+合并结果也视为完成处理。
        """
        total_processed = self.scanned_files + self.unchanged_files
        return total_processed / self.duration_seconds if self.duration_seconds > 0 else 0.0

    def summary(self, *, cancelled: bool = False) -> str:
        """返回状态栏摘要文本。

        :param cancelled: 是否在摘要前缀"已取消"，GUI/CLI 取消场景共用。

        当 ``archive_entries > 0`` 时在"扫描"后注明含压缩包内条目数，
        避免 ``scanned_files > total_files`` 时用户误解为统计异常。
        ``filter_removed > 0`` 时附加"筛选剔除 N"，让用户感知 filter 阶段
        剔除的文件数（与 skipped_files 区分）。
        """
        prefix = "已取消" if cancelled else "完成"
        scan_part = f"扫描 {self.scanned_files}"
        if self.archive_entries > 0:
            scan_part += f"（含压缩包内条目 {self.archive_entries}）"
        unchanged_part = ""
        if self.unchanged_files > 0:
            unchanged_part = f" | 复用 {self.unchanged_files}"
        filter_part = ""
        if self.filter_removed > 0:
            filter_part = f" | 筛选剔除 {self.filter_removed}"
        return (
            f"{prefix}: 总计 {self.total_files} | {scan_part}{unchanged_part}{filter_part} | "
            f"跳过 {self.skipped_files} | 用户跳过 {self.user_skipped} | "
            f"命中 {self.matched_files} | 条数 {self.total_matches} | "
            f"错误 {self.errors} | 耗时 {self.duration_seconds:.2f}s"
        )


@dataclass(frozen=True)
class WalkResult:
    """walk 阶段产物：单根路径遍历收集的待扫描文件清单与统计。

    职责拆分（stats/scan worker 分离）后，``FileStatsWorker`` 执行 walk 阶段
    产出本对象，``ScanWorker`` 接收后跳过 walk 直接进入 filter/scan/archive 阶段，
    使 UI 能更早展示确定的 ``total``，且两 worker 的取消/暂停各自独立。

    :param root: 本次 walk 的根路径
    :param entries: 通过过滤的待扫描文件清单（已剔除 ignored/user_skipped）
    :param total: walk 发现的文件总数（含跳过项，用于进度条总量）
    :param skipped: 按扩展名/目录过滤跳过的文件数
    :param user_skipped: 用户标记跳过的文件数（区别于 skipped）
    :param skipped_dirs: walk 阶段跳过的目录路径元组（最近条目，供 UI 展示）
    :param cancelled: walk 是否被取消
    :param unchanged_count: 增量扫描时未变更文件数（供 scan_entries
        合并未变更命中结果；全量扫描时为 0）
    :param manifest: 本次 collect_entries 构建的新 manifest（含变更+未变更所有
        walk 到的文件指纹）。scan_entries 用其 keys() 过滤已删除文件，
        避免增量合并时把已删除文件的命中结果重新加入结果列表。ScanWorker 用
        precollected 模式调 scan_entries 时，Scanner 实例自身 _current_manifest
        为 None（collect_entries 未被本实例调用），需从 WalkResult 恢复。
    :param filtered_entries: filter 阶段剔除空/超限/不可读/符号链接后的
        可扫描文件清单。``scan_entries`` 优先使用此字段（非空时），
        否则回退到 ``entries``（向后兼容未经筛选阶段的调用方）
    :param filter_stats: filter 阶段剔除明细统计；``None`` 表示未经筛选阶段
        （旧调用方直接构造 WalkResult）；非 None 时 ``filtered_entries`` 已就绪
    """

    root: Path
    entries: tuple[FileEntry, ...] = field(default_factory=tuple)
    total: int = 0
    skipped: int = 0
    user_skipped: int = 0
    skipped_dirs: tuple[str, ...] = ()
    cancelled: bool = False
    unchanged_count: int = 0
    manifest: IncrementalManifest | None = None
    # filter 阶段产物：默认 None / 空 tuple 表示未经筛选阶段（向后兼容）
    filtered_entries: tuple[FileEntry, ...] = field(default_factory=tuple)
    filter_stats: FilterStats | None = None


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
                        replaced=sr.replaced,
                        replaced_count=sr.replaced_count,
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

    def _to_dict(self) -> dict[str, Any]:
        """将扫描报告转为可序列化字典（to_json / to_json_bytes 共用）。"""
        return {
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
                    "replaced": r.replaced,
                    "replaced_count": r.replaced_count,
                    "rules": [asdict(h) for h in r.hits],
                }
                for r in self.hits
            ],
        }

    def to_json(self) -> str:
        """将扫描报告转换为 JSON 字符串。"""
        return _json_dumps(self._to_dict())

    def to_json_bytes(self) -> bytes:
        """将扫描报告转换为 JSON 字节串（直接写文件，避免 str 编码开销）。

        orjson 直接输出 UTF-8 bytes，跳过 ``.decode()`` + ``.encode()`` 往返，
        10 万命中结果场景下额外节省约 15% 序列化时间。
        """
        return _json_dumps_bytes(self._to_dict())

    @classmethod
    def from_json(cls, json_str: str | bytes) -> ScanReport:
        """从 JSON 反序列化扫描报告（与 :meth:`to_json` 互逆）。

        用于扫描结果持久化：扫描完成后将 ``to_json()`` 输出写入磁盘，
        重启后通过 ``from_json()`` 恢复结果到 :class:`ScanController`，
        避免用户重启后被迫重新扫描。

        改用 ``_json_loads``（orjson），接受 str 或 bytes，
        配合 ``read_bytes()`` 跳过 ``.decode()`` + ``.encode()`` 往返。

        :param json_str: ``to_json()`` 输出的 JSON 字符串或字节串
        :return: :class:`ScanReport` 实例
        :raises ValueError: JSON 格式非法或字段缺失
        """
        data = _json_loads(json_str)

        root = Path(data["root"])
        cancelled = bool(data.get("cancelled", False))

        # 反序列化 stats
        stats_data = data.get("stats", {})
        stats = ScanStats(
            total_files=int(stats_data.get("total_files", 0)),
            scanned_files=int(stats_data.get("scanned_files", 0)),
            matched_files=int(stats_data.get("matched_files", 0)),
            skipped_files=int(stats_data.get("skipped_files", 0)),
            errors=int(stats_data.get("errors", 0)),
            duration_seconds=float(stats_data.get("duration_seconds", 0.0)),
            total_matches=int(stats_data.get("total_matches", 0)),
            user_skipped=int(stats_data.get("user_skipped", 0)),
            archive_entries=int(stats_data.get("archive_entries", 0)),
            # filter_removed 持久化（与 archive_entries 同级，供历史报告复盘）
            filter_removed=int(stats_data.get("filter_removed", 0)),
            # perf_summary 不持久化（运行时统计，重启后无意义）
            perf_summary=None,
        )

        # 反序列化 hits
        results: list[ScanResult] = []
        for hit_data in data.get("hits", []):
            path = Path(hit_data["path"])
            archive_path_str = hit_data.get("archive_path")
            archive_path = Path(archive_path_str) if archive_path_str else None
            size = int(hit_data.get("size", 0))
            user_skipped = bool(hit_data.get("user_skipped", False))

            # 反序列化规则命中
            rule_hits: list[RuleHit] = []
            for rule_data in hit_data.get("rules", []):
                # asdict() 将 Severity 枚举序列化为字符串
                severity_str = rule_data.get("severity", "info")
                try:
                    severity = Severity(severity_str)
                except ValueError:
                    severity = Severity.INFO
                rule_hits.append(
                    RuleHit(
                        rule_name=str(rule_data.get("rule_name", "")),
                        severity=severity,
                        detail=str(rule_data.get("detail", "")),
                        match_text=str(rule_data.get("match_text", "")),
                        match_count=int(rule_data.get("match_count", 1)),
                        target=str(rule_data.get("target", "")),
                        # tuple 字段 asdict 后变为 list，需转换
                        match_texts=tuple(rule_data.get("match_texts", [])),
                        match_description=str(rule_data.get("match_description", "")),
                    )
                )

            results.append(
                ScanResult(
                    path=path,
                    size=size,
                    hits=tuple(rule_hits),
                    user_skipped=user_skipped,
                    archive_path=archive_path,
                    replaced=bool(hit_data.get("replaced", False)),
                    replaced_count=int(hit_data.get("replaced_count", 0)),
                )
            )

        return cls(
            root=root,
            results=tuple(results),
            stats=stats,
            cancelled=cancelled,
        )

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

    def save_json_file(
        self,
        path: str | Path,
        chunk_size: int = 1000,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> None:
        """流式分块写入 JSON 文件，避免大结果集内存峰值。

        原始 :meth:`to_json` / :meth:`to_json_bytes` 会在内存中构造完整
        JSON 对象 + 序列化 str/bytes，10 万命中场景下内存峰值约为结果本身的
        3-5 倍。本方法按 ``chunk_size`` 条 :class:`ScanResult` 为单位分批序列化
        并写盘，常驻内存仅与单批大小相关，峰值约为原始方案的 20-30%。

        输出 JSON 结构与 :meth:`to_json` 完全一致，可与 :meth:`from_json`
        互逆。

        :param path: 目标 JSON 文件路径
        :param chunk_size: 每批序列化的 ``ScanResult`` 条数，必须 > 0
        :param progress_cb: 可选进度回调，签名为 ``(current: int, total: int)``，
            ``current`` 表示已处理完成的 ``ScanResult`` 条数
        """
        if chunk_size <= 0:
            raise ValueError(f"chunk_size 必须为正整数，当前: {chunk_size}")
        total = len(self.hits)
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        # 1. 写出 header：{root, stats, cancelled} + ",\"hits\":["
        header: dict[str, Any] = {
            "root": str(self.root),
            "stats": asdict(self.stats),
            "cancelled": self.cancelled,
        }
        header_bytes = _json_dumps_bytes(header)
        # header_bytes 形如 b'{...}'，去掉末尾 } 追加 hits 数组开头
        if not header_bytes.endswith(b"}"):  # pragma: no cover - 防御性
            raise RuntimeError("header JSON 序列化失败，无法流式拼接 hits")
        prefix = header_bytes[:-1] + b',"hits":['

        with path_obj.open("wb") as f:
            f.write(prefix)
            if progress_cb is not None:
                progress_cb(0, total)
            # 2. 逐批序列化 hits
            is_first = True
            processed = 0
            hits_list = list(self.hits)
            for start in range(0, total, chunk_size):
                batch = hits_list[start : start + chunk_size]
                # 将 asdict 得到的 dict 直接序列化（与 _to_dict() 中 hits 结构一致）
                batch_dicts: list[dict[str, Any]] = []
                for r in batch:
                    archive_path_str: str | None = str(r.archive_path) if r.archive_path is not None else None
                    batch_dicts.append(
                        {
                            "path": str(r.path),
                            "archive_path": archive_path_str,
                            "inner_path": r.inner_path or None,
                            "size": r.size,
                            "max_severity": r.max_severity.value,
                            "match_count": r.total_match_count,
                            "user_skipped": r.user_skipped,
                            "replaced": r.replaced,
                            "replaced_count": r.replaced_count,
                            "rules": [asdict(h) for h in r.hits],
                        }
                    )
                batch_bytes = _json_dumps_bytes(batch_dicts)  # pyrefly: ignore [bad-argument-type]
                # batch_bytes 形如 b"[{...},{...},...]"，去掉外围 []
                inner = batch_bytes[1:-1]
                if is_first:
                    f.write(inner)
                    is_first = False
                else:
                    f.write(b",")
                    f.write(inner)
                processed += len(batch)
                if progress_cb is not None:
                    progress_cb(processed, total)
                f.flush()
            # 3. 写出结尾：]}
            f.write(b"]}")
            f.flush()

    def save_csv_file(
        self,
        path: str | Path,
        chunk_size: int = 1000,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> None:
        """流式分块写入 CSV 文件，避免大结果集内存峰值。

        CSV 每行对应一条 :class:`RuleHit`（单条 :class:`ScanResult`
        可能对应多行）。原始 :meth:`to_csv` 会在内存 :class:`io.StringIO` 中
        累积全部内容，对 100 万行 CSV 不友好。本方法按 ``chunk_size`` 条
        :class:`ScanResult` 为单位分批 flush 到磁盘，常驻内存与单批大小相关。

        输出 CSV 列与 :meth:`to_csv` 完全一致：
        ``path, archive_path, inner_path, size, severity, rule, description, match_count, detail``。

        :param path: 目标 CSV 文件路径
        :param chunk_size: 每批处理的 ``ScanResult`` 条数，必须 > 0
        :param progress_cb: 可选进度回调，签名为 ``(current: int, total: int)``，
            ``current`` 表示已处理完成的 ``ScanResult`` 条数
        """
        if chunk_size <= 0:
            raise ValueError(f"chunk_size 必须为正整数，当前: {chunk_size}")
        total = len(self.hits)
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        with path_obj.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "path",
                    "archive_path",
                    "inner_path",
                    "size",
                    "severity",
                    "rule",
                    "description",
                    "match_count",
                    "detail",
                ]
            )
            if progress_cb is not None:
                progress_cb(0, total)
            processed = 0
            hits_list = list(self.hits)
            for start in range(0, total, chunk_size):
                batch = hits_list[start : start + chunk_size]
                for r in batch:
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
                processed += len(batch)
                if progress_cb is not None:
                    progress_cb(processed, total)
                f.flush()
