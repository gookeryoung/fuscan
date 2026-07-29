"""扫描器：协调遍历器与匹配引擎，输出扫描报告。

两阶段扫描架构（iter-71）：

1. 单线程遍历目录树收集待扫描文件清单（按全局 ``scan_extensions`` 过滤）
2. ``max_workers > 1`` 时用 ThreadPoolExecutor 并发扫描清单，否则顺序扫描

压缩包扫描在 ``max_workers > 1`` 时按 archive 文件级别并行：不同 archive
用线程池并发扫描，单个 archive 内条目顺序执行（避免 reader 共享竞争）。

模块结构：

- :mod:`fuscan.scanner._helpers`：纯函数与模块级常量（内容提供器、规则求值辅助等）
- :mod:`fuscan.scanner._archive_phase`：archive 阶段并行扫描子流程
- :mod:`fuscan.scanner._pipeline_phase`：scan 阶段顺序/并发扫描子流程
- :mod:`fuscan.scanner._cache_phase`：缓存模式扫描辅助（BatchBuffer/缓存命中重建）
- 本模块：:class:`Scanner` 主类，串联 walk → scan → archive 三阶段
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping

from fuscan.cache.store import BatchWriteItem
from fuscan.perf import PerfStats
from fuscan.rules.model import Rule, RuleSet
from fuscan.scanner._archive_phase import run_archive_phase
from fuscan.scanner._cache_phase import (
    BatchBuffer,
    build_hits_from_cache,
    extract_with_cache,
)
from fuscan.scanner._helpers import (
    GIL_YIELD_INTERVAL,
    PROGRESS_LIST_MAX,
    default_extract_content,
    default_extract_content_with_hash,
    empty_content_provider,
    normalize_max_file_size,
    spec_needs_content,
)
from fuscan.scanner._pipeline_phase import run_pipeline_phase
from fuscan.scanner.context import ContentProvider, FileEntry, MatchContext
from fuscan.scanner.matchers import Matcher, build_matcher
from fuscan.scanner.result import (
    FileFingerprint,
    IncrementalManifest,
    ProgressInfo,
    RuleHit,
    ScanReport,
    ScanResult,
    ScanStats,
    WalkResult,
)
from fuscan.scanner.walker import FileWalker

if TYPE_CHECKING:
    from fuscan.archive import ArchiveScanner
    from fuscan.cache import CacheStore

__all__ = ["Scanner", "default_extract_content", "default_extract_content_with_hash"]

logger = logging.getLogger(__name__)


class Scanner:
    """扫描器：对目录或单文件应用规则集，产出扫描报告。

    - 构造时一次性编译规则集为 Matcher 列表，避免重复编译
    - 默认使用提取器注册表（extractors）提取文件内容，支持多格式
    - 支持自定义内容提供器覆盖默认提取逻辑
    - 两阶段架构（iter-71）：先单线程遍历收集文件清单（按全局 ``scan_extensions``
      过滤），再 ``max_workers > 1`` 时用线程池并发扫描清单
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
        cache: CacheStore | None = None,
        source_files: Mapping[Path, str] | None = None,
        max_file_size: int | None = None,
        scan_extensions: tuple[str, ...] | None = None,
        skip_paths: frozenset[str] | None = None,
        incremental_manifest: IncrementalManifest | None = None,
        prev_report: ScanReport | None = None,
    ) -> None:
        self.ruleset = ruleset
        self._content_provider: ContentProvider = content_provider or default_extract_content
        # 大文件跳过阈值：None 或 0 表示不限制，否则超过此大小的文件不读取内容
        self._max_file_size: int = normalize_max_file_size(max_file_size)
        self._compiled: list[tuple[Rule, Matcher]] = [(rule, build_matcher(rule.match)) for rule in ruleset.rules]
        # 全局后缀白名单：
        #   - None：扫描所有文件（全选快速路径）
        #   - 空 frozenset：不扫描任何文件（用户全部取消勾选的防御性边界）
        #   - 非空 frozenset：只扫描指定后缀（已规范化为小写、无点，含压缩包扩展名）
        # 压缩包扩展名与其他扩展名统一走白名单，
        # walker 收集到压缩包文件后由 ArchiveScanner 按同一白名单过滤内部条目。
        if scan_extensions is None:
            self._scan_extensions: frozenset[str] | None = None
        else:
            self._scan_extensions = frozenset(e.lower().lstrip(".") for e in scan_extensions)
        # 用户标记跳过的路径集合：walk 阶段命中即跳过并计入 user_skipped，
        # 与按扩展名/目录过滤的 skipped 区分。键为 str(Path)，与 SkipStore 存储格式一致。
        self._skip_paths: frozenset[str] = skip_paths or frozenset()
        self._skipped_dirs: deque[str] = deque(maxlen=PROGRESS_LIST_MAX)
        self._matched_files: deque[tuple[str, str]] = deque(maxlen=PROGRESS_LIST_MAX)
        self._walker = FileWalker(
            ignore_dirs=ignore_dirs,
            ignore_paths=ruleset.ignore_paths,
            max_depth=max_depth,
            follow_symlinks=follow_symlinks,
            on_skip_dir=self._on_skip_dir_internal,
        )
        self._scan_archives = scan_archives
        self._max_workers = max_workers
        # 预计算每个规则是否需要文件内容（含 CONTENT 目标），供缓存模式跳过 I/O
        self._content_rule_names: frozenset[str] = frozenset(
            rule.name for rule in ruleset.rules if spec_needs_content(rule.match)
        )
        # 缓存模式：登记规则集并构造带哈希的编译列表
        self._cache: CacheStore | None = cache
        self._rule_hashes: dict[str, str] = {}
        self._compiled_with_hash: list[tuple[Rule, Matcher, str]] = []
        if cache is not None:
            self._rule_hashes = cache.register_ruleset(ruleset, source_files)
            self._compiled_with_hash = [
                (rule, matcher, self._rule_hashes[rule.name])
                for rule, matcher in self._compiled
                if rule.name in self._rule_hashes
            ]
        self._archive_scanner: ArchiveScanner | None = None
        if scan_archives:
            # 惰性导入避免与 archive.scanner 模块的循环依赖
            from fuscan.archive import ArchiveScanner

            self._archive_scanner = ArchiveScanner(
                ruleset=ruleset,
                password=archive_password,
                cache=cache,
                max_entry_size=self._max_file_size,
                # 压缩包内条目同样按白名单过滤：None 表示全选快速路径，
                # 非 frozenset 表示按白名单过滤内部条目（如压缩包内 .txt 在白名单不含 txt 时跳过）
                scan_extensions=self._scan_extensions,
            )
        self._on_progress = on_progress
        self._progress_interval = progress_interval
        self._last_progress_time: float = 0.0
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancel_event = threading.Event()
        # iter-111：自适应 GIL 让步间隔。
        # 顺序扫描（max_workers<=1）：主线程独占 GIL，需每 20 个文件让步一次避免 UI 卡死。
        # 并发扫描（max_workers>1）：PyO3 提取器（pdf_oxide/calamine）在 Rust 层释放 GIL，
        # worker 线程在 I/O 与提取期间不持 GIL，主线程自然获得调度机会；
        # 让步间隔提高到 50，减少 sleep(0) 开销（10万文件节省约 3ms）。
        self._gil_yield_interval: int = (
            GIL_YIELD_INTERVAL if not max_workers or max_workers <= 1 else GIL_YIELD_INTERVAL * 5 // 2
        )
        # iter-111：进度 emit 批处理阈值。
        # 并发扫描时每 N 个 future 完成才调用一次 _emit_progress（内部仍有 150ms 节流），
        # 减少 time.perf_counter() + 比较的函数调用开销。
        # 顺序扫描保持每文件 emit（用户期望实时反馈）。
        self._progress_emit_batch: int = 5 if (max_workers and max_workers > 1) else 1
        # 扫描进度上下文（scan() 期间设置，供 _emit_progress 使用）
        self._progress_start: float = 0.0
        self._progress_total: int = 0
        self._progress_skipped: int = 0
        # walk 阶段累计的用户跳过数，scan/archive 阶段复用此值上报
        self._progress_user_skipped: int = 0
        self._base_scanned: int = 0
        self._base_matched: int = 0
        self._base_errors: int = 0
        self._base_matches: int = 0
        # 批量写入缓冲：缓存模式下累积 BatchWriteItem，达阈值后单次事务 flush。
        # iter-109：抽取为 :class:`BatchBuffer` 子模块，消除 scanner.py 内的锁与
        # 缓冲管理细节；无缓存模式下 :attr:`_cache` 为 None，BatchBuffer 不创建。
        self._batch_buffer: BatchBuffer | None = None
        # 性能聚合统计：PerfStats 始终启用，仅做聚合统计无日志开销，不影响生产性能。
        self._perf: PerfStats = PerfStats()
        if cache is not None:
            self._batch_buffer = BatchBuffer(cache, self._perf)
        # 增量扫描上下文（iter-124）：
        # - _incremental_manifest 非 None 时启用增量模式，walk 阶段对比指纹跳过未变更文件
        # - _prev_report 提供未变更文件的命中结果，scan 阶段合并到本次报告
        # - _unchanged_hits 缓存未变更文件中仍有命中的结果（按相对路径索引），供合并
        # - _unchanged_count 统计未变更文件数，用于进度与统计合并
        self._incremental_manifest: IncrementalManifest | None = incremental_manifest
        self._prev_report: ScanReport | None = prev_report
        self._unchanged_hits: dict[str, ScanResult] = {}
        self._unchanged_count: int = 0
        # 本次 collect_entries 构建的新 manifest（供调用方持久化，下次增量扫描用）
        self._current_manifest: IncrementalManifest | None = None
        # iter-133：_unchanged_hits 只依赖 prev_report 预索引上次命中结果，
        # 与 incremental_manifest 无关（manifest 仅用于 walk 阶段对比指纹）。
        # 此前条件为 `incremental_manifest is not None and prev_report is not None`，
        # 但 ScanWorker 构造 Scanner 时不传 incremental_manifest（manifest 在
        # FileStatsWorker 侧），导致 _unchanged_hits 永远为空，增量扫描合并
        # 无数据可合并，结果清零。
        if prev_report is not None:
            for sr in prev_report.hits:
                if sr.archive_path is not None:
                    continue  # 压缩包内部条目不参与增量合并（每次重新扫描压缩包）
                rel = IncrementalManifest.rel_key(sr.path, prev_report.root)
                self._unchanged_hits[rel] = sr

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

        暂停时阻塞当前线程直到 resume 或取消；取消时返回 True。
        使用 0.5 秒超时循环等待，避免暂停期间无法响应取消请求导致死锁。
        """
        if self._cancel_event.is_set():
            return True
        # 超时等待：每 0.5 秒醒来重检取消标志，确保 cancel() 能及时解除暂停阻塞
        while not self._pause_event.wait(timeout=0.5):
            if self._cancel_event.is_set():
                return True
        return self._cancel_event.is_set()

    def _on_skip_dir_internal(self, dir_path: str) -> None:
        """FileWalker 跳过目录时的内部回调：收集到列表供 ProgressInfo 上报。"""
        self._skipped_dirs.append(dir_path)

    def scan(self, root: Path) -> ScanReport:
        """扫描根目录，返回完整报告（``collect_entries`` + ``scan_entries`` 串联）。

        两阶段扫描架构（iter-71）：

        1. **阶段 1 - 收集**：:meth:`collect_entries` 单线程遍历目录树，按全局
           ``scan_extensions`` 过滤生成待扫描文件清单。遍历为 I/O 轻量操作，单线程已足够。
        2. **阶段 2 - 扫描**：:meth:`scan_entries` 在 ``max_workers > 1`` 时用
           ThreadPoolExecutor 并发扫描文件清单，否则顺序扫描。先收集再扫描避免了 walk
           与 scan 争抢磁盘 I/O 导致的吞吐下降，且可对清单做全局后缀过滤减少无效提交。
        3. **阶段 3 - 压缩包**：顺序扫描压缩包内条目（避免 ArchiveScanner 线程安全问题）。

        ``on_progress`` 回调在遍历和扫描阶段按时间节流反馈进度。
        职责拆分后，``FileStatsWorker`` 可独立调用 :meth:`collect_entries`，
        ``ScanWorker`` 接收 :class:`WalkResult` 后调用 :meth:`scan_entries` 跳过 walk。
        """
        walk_result = self.collect_entries(root)
        return self.scan_entries(root, walk_result)

    def collect_entries(self, root: Path) -> WalkResult:
        """walk 阶段：单线程遍历目录树收集待扫描文件清单，按过滤规则筛选。

        独立调用（如 ``FileStatsWorker``）时仅执行 walk 阶段；与 :meth:`scan_entries`
        配合时由 :meth:`scan` 串联。本方法重置进度上下文与收集列表，并在结束时
        清除 ``_cancel_event``，使 Scanner 可在取消/异常后复用（C1 修复语义保持）。

        过滤规则：

        - ``skip_paths``：用户标记跳过的文件计入 ``user_skipped``，不进入清单
        - ``scan_extensions``：不在白名单的文件计入 ``skipped``，不进入清单
          （iter-87 起统一白名单制：None 全选，空集合都不扫，非空按白名单过滤）
        - ``ignore_dirs``：在 ``FileWalker`` 内部过滤，
          跳过的目录收集到 ``skipped_dirs`` 供 UI 展示

        增量模式（iter-124）：构造时传入 ``incremental_manifest`` 启用。walk 阶段
        对比 ``(mtime, size)`` 指纹，未变更文件跳过（不加入 entries），仅变更/
        新增文件进入扫描队列。未变更文件数累计到 ``_unchanged_count``，供
        :meth:`scan_entries` 合并统计。

        :param root: 待遍历的根路径
        :return: walk 产物 :class:`WalkResult`，含 entries 与统计
        """
        self._progress_start = time.perf_counter()
        # 重置暂停状态（取消状态在末尾清除，保留"collect 前取消"语义）
        self._pause_event.set()
        # 重置每次扫描的收集列表，避免跨多次调用累积
        self._skipped_dirs.clear()
        self._matched_files.clear()
        # 重置性能统计，使每次调用的汇总独立
        self._perf.reset()
        # 重置增量扫描统计
        self._unchanged_count = 0
        # 构建本次扫描的新 manifest（供下次增量扫描用）
        # 增量模式合并旧 manifest 未变更指纹 + 新文件指纹；全量模式从零构建
        new_fingerprints: dict[str, FileFingerprint] = {}

        # 增量模式指纹映射（空字典表示全量扫描）
        prev_fps: dict[str, FileFingerprint] = (
            self._incremental_manifest.fingerprints if self._incremental_manifest else {}
        )

        entries: list[FileEntry] = []
        total = 0
        skipped = 0
        user_skipped = 0
        cancelled = self.is_cancelled

        if not cancelled:
            # 阶段 1：单线程遍历收集待扫描 entry（I/O 轻量，按全局后缀过滤）
            with self._perf.measure("walk"):
                for entry in self._walker.walk(root):
                    if self._check_control():
                        break
                    total += 1
                    # 用户标记跳过的文件计入 user_skipped（区别于
                    # 按扩展名/目录过滤的 skipped），不进入扫描队列
                    if str(entry.path) in self._skip_paths:
                        user_skipped += 1
                        continue
                    if not self._should_scan(entry):
                        skipped += 1
                        continue
                    # 增量模式：指纹匹配的未变更文件跳过（不加入扫描队列），
                    # 仅累计 _unchanged_count 供 scan_entries 合并统计
                    if prev_fps:
                        rel = IncrementalManifest.rel_key(entry.path, root)
                        prev_fp = prev_fps.get(rel)
                        if prev_fp is not None and prev_fp.mtime == entry.mtime and prev_fp.size == entry.size:
                            self._unchanged_count += 1
                            # 未变更文件指纹直接复用（mtime/size 未变）
                            new_fingerprints[rel] = prev_fp
                            continue
                    # 变更/新文件/全量模式：记录当前指纹供下次增量扫描
                    new_fingerprints[IncrementalManifest.rel_key(entry.path, root)] = FileFingerprint(
                        mtime=entry.mtime, size=entry.size
                    )
                    entries.append(entry)
                    if total % 200 == 0:
                        # 实时同步进度上下文，使 _emit_progress 反映 walk 阶段累计值。
                        # 旧实现在此处未同步 self._progress_*，导致 ProgressInfo 中
                        # total/skipped/user_skipped 始终为旧值（0 或上次扫描值），
                        # UI 的 walkDiscovered/walkSkipped 不增长，进度条不动。
                        self._progress_total = total
                        self._progress_skipped = skipped
                        self._progress_user_skipped = user_skipped
                        self._emit_progress(str(entry.path), 0, 0, 0, phase="walk")

        # walk 结束后同步最终统计并强制发送进度，确保 UI 收到完整 walk 统计。
        # 文件数 < 200 时循环内不触发 emit，此处 force=True 是唯一的进度上报点。
        self._progress_total = total
        self._progress_skipped = skipped
        self._progress_user_skipped = user_skipped
        self._emit_progress(str(root), 0, 0, 0, phase="walk", force=True)
        # 记录取消状态后清除标志，使 Scanner 可在取消/异常后复用（C1 修复）：
        # 否则下次 collect_entries 的 is_cancelled 仍为 True，静默跳过全部逻辑
        cancelled = self.is_cancelled
        self._cancel_event.clear()

        # 构建本次扫描的 manifest（全量+增量模式均构建，供下次增量扫描用）
        self._current_manifest = IncrementalManifest(root=root, fingerprints=new_fingerprints)

        return WalkResult(
            root=root,
            entries=tuple(entries),
            total=total,
            skipped=skipped,
            user_skipped=user_skipped,
            skipped_dirs=tuple(self._skipped_dirs),
            cancelled=cancelled,
            # iter-133：传递未变更文件数到 scan_entries，供合并未变更命中结果
            unchanged_count=self._unchanged_count,
        )

    @property
    def current_manifest(self) -> IncrementalManifest | None:
        """本次 collect_entries 构建的新 manifest（供下次增量扫描持久化）。

        全量模式也构建 manifest（记录所有通过过滤的文件指纹），使下次可启用
        增量扫描。增量模式合并未变更文件旧指纹 + 变更文件新指纹。
        """
        return self._current_manifest

    def scan_entries(self, root: Path, walk_result: WalkResult) -> ScanReport:
        """scan + archive 阶段：对预收集的 entries 执行内容扫描。

        接收 :meth:`collect_entries` 的产物，跳过 walk 直接进入阶段 2/3。
        与 :meth:`collect_entries` 配合实现 stats/scan worker 职责拆分：
        ``FileStatsWorker`` 跑完 walk 产出 ``WalkResult``，``ScanWorker`` 接收后
        调本方法，使 UI 从 scan 阶段开始即展示确定的 ``total``。

        本方法重置 ``_progress_start``，``duration_seconds`` 仅反映 scan/archive
        阶段耗时（walk 已由 ``FileStatsWorker`` 独立计时）。

        :param root: 扫描根路径（用于 ScanReport.root）
        :param walk_result: :meth:`collect_entries` 的产物
        :return: 完整扫描报告
        """
        # 重置 scan 阶段计时起点，使 duration 仅反映 scan/archive 耗时
        self._progress_start = time.perf_counter()
        self._pause_event.set()

        entries = list(walk_result.entries)
        total = walk_result.total
        skipped = walk_result.skipped
        user_skipped = walk_result.user_skipped
        # iter-133：从 WalkResult 恢复未变更文件数——collect_entries 在
        # FileStatsWorker 的 Scanner 实例中累加 _unchanged_count，但 ScanWorker
        # 使用新 Scanner 实例调 scan_entries，_unchanged_count 初始为 0。
        # 若不从 WalkResult 恢复，合并条件 _unchanged_count > 0 永远为 False，
        # 未变更命中结果不会被合并，导致增量扫描结果清零。
        self._unchanged_count = walk_result.unchanged_count
        # walk_result.cancelled 来自 collect_entries 末尾的 is_cancelled 快照，
        # 此处沿用：walk 被取消则跳过 scan/archive 阶段
        cancelled = walk_result.cancelled

        results: list[ScanResult] = []
        scanned = 0
        matched = 0
        errors = 0
        matches = 0
        # 复位 walk 累积的进度上下文，供 _emit_progress 在 scan 阶段使用。
        # scan 阶段 total 必须为实际待扫描文件数 len(entries)（符合类型的文件），
        # 而非 walk 阶段的 walk_result.total（含白名单/用户标记跳过的文件）。
        # 否则 progress = scanned / walk_total * 100 会偏低，与"已扫描 N / M 个文件"
        # 数值不匹配（如 1000 个发现 / 300 跳过 → entries=700，但 total=1000 导致
        # 进度条 350/1000=35% 而非正确的 350/700=50%）。
        # iter-133：total 须纳入未变更文件数（_unchanged_count），因为 scanned
        # 在合并阶段会累加 _unchanged_count（未变更文件视为已扫描），若 total 不含
        # 此部分会导致 scanned > total（分子超出分母）。
        self._progress_total = len(entries) + self._unchanged_count
        self._progress_skipped = skipped
        self._progress_user_skipped = user_skipped

        try:
            if not cancelled:
                # 阶段 2：并发扫描（max_workers > 1）或顺序扫描
                # iter-117：_scan_sequential/_scan_concurrent/_collect_concurrent_results
                # 抽离到 _pipeline_phase.py，本类仅做分派调用
                scanned, matched, errors, matches = run_pipeline_phase(self, entries, results)  # pyrefly: ignore [bad-argument-type]

            # 阶段 3：顺序扫描压缩包内条目（避免 ArchiveScanner 线程安全问题）
            # 用 cancelled 而非 self.is_cancelled：collect_entries 已清除 _cancel_event，
            # walk 被取消时 is_cancelled 为 False，但 cancelled（来自 walk_result）为 True
            if self._scan_archives and self._archive_scanner is not None and not cancelled:
                # archive phase 内部直接调 CacheStore，不走 _batch_buffer，需先 flush
                # 避免批量缓冲与 archive scanner 的写入交错
                self._flush_batch()
                self._base_scanned = scanned
                self._base_matched = matched
                self._base_errors = errors
                self._base_matches = matches
                d_scanned, d_matched, d_errors, d_matches = run_archive_phase(self, entries, results)  # pyrefly: ignore [bad-argument-type]
                scanned += d_scanned
                matched += d_matched
                errors += d_errors
                matches += d_matches
                # iter-133：压缩包内条目纳入分母，避免 scanned > total（分子超出分母）
                self._progress_total += d_scanned
        finally:
            # 异常路径（如 MemoryError、walker 未捕获错误）也 flush 已累积批次，
            # 避免最后一批（最多 BATCH_THRESHOLD 个文件）缓存数据丢失
            self._flush_batch()
            # 保留 walk 阶段的取消状态：collect_entries 已清除 _cancel_event，
            # 此处若 cancelled 已为 True（walk 取消）则不能用 is_cancelled（False）覆盖；
            # 仅当 walk 未取消时才读取 scan/archive 阶段是否被取消
            if not cancelled:
                cancelled = self.is_cancelled
            self._cancel_event.clear()

        # 强制发送最终进度
        self._emit_progress("", scanned, matched, errors, matches, force=True)
        # 输出性能汇总到 DEBUG 日志（PerfStats 始终启用，但日志需配置 DEBUG 级别才可见）
        self._perf.report(logger)

        duration = time.perf_counter() - self._progress_start

        # 增量扫描合并（iter-124）：
        # 本次 scan 仅扫描变更文件（entries），未变更文件的命中结果从上次
        # ScanReport 复用（_unchanged_hits 按相对路径索引）。合并后 results
        # 包含变更文件 + 未变更命中文件，统计需相应累加。
        if self._unchanged_count > 0 and self._prev_report is not None:
            # 收集本次扫描中仍有命中的文件相对路径，避免合并时重复
            changed_hit_rels: set[str] = {IncrementalManifest.rel_key(r.path, root) for r in results if r.has_hit}
            # 合并未变更文件中仍有命中的结果（本次未重新扫描的文件）
            for rel, sr in self._unchanged_hits.items():
                if rel not in changed_hit_rels:
                    results.append(sr)
                    matched += 1
                    matches += sr.total_match_count
            # 统计累加：未变更文件视为已扫描（复用上次结果，无需重新 I/O）
            scanned += self._unchanged_count

        stats = ScanStats(
            total_files=total,
            scanned_files=scanned,
            matched_files=matched,
            skipped_files=skipped,
            errors=errors,
            duration_seconds=duration,
            total_matches=matches,
            # 用户标记跳过的文件数，与 skipped_files 区分
            user_skipped=user_skipped,
            # PerfStats 始终启用，导出各阶段统计供 GUI/CLI 展示与持久化
            perf_summary=self._perf.to_dict(),
        )
        return ScanReport(root=root, results=tuple(results), stats=stats, cancelled=cancelled)

    def _emit_progress(
        self,
        current_file: str,
        scanned: int,
        matched: int,
        errors: int,
        matches: int = 0,
        force: bool = False,
        phase: str = "scan",
    ) -> None:
        """时间节流后调用 on_progress 回调。

        :param matches: 累计匹配文本条数（区别于 matched 的命中文件数）。
        :param force: 为 True 时跳过节流，强制发送（如最终进度）。
        :param phase: 当前扫描阶段（iter-75）：``"walk"``/``"scan"``/``"archive"``，
            GUI 据此显示不同提示文案，避免 walk 阶段 scanned=0 被误以为卡住。
        """
        if self._on_progress is None:
            return
        now = time.perf_counter()
        if not force and now - self._last_progress_time < self._progress_interval:
            return
        self._last_progress_time = now
        # deque 为空时跳过 tuple 拷贝（高频进度回调下的微小优化）
        recent_skipped = tuple(self._skipped_dirs) if self._skipped_dirs else ()
        recent_matched = tuple(self._matched_files) if self._matched_files else ()
        self._on_progress(
            ProgressInfo(
                current_file=current_file,
                scanned=scanned,
                total=self._progress_total,
                skipped=self._progress_skipped,
                matched=matched,
                errors=errors,
                elapsed=now - self._progress_start,
                matches=matches,
                skipped_dirs=recent_skipped,
                matched_files=recent_matched,
                phase=phase,
                user_skipped=self._progress_user_skipped,
            )
        )

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
        """根据全局白名单 ``scan_extensions`` 判断是否扫描该文件。

        iter-87 起统一为白名单制，三种语义：

        - ``None``：用户全选，扫描所有文件（快速路径，不进入扩展名检查）
        - 空 frozenset：用户全部取消勾选，不扫描任何文件（防御性边界）
        - 非空 frozenset：仅扫描扩展名在白名单中的文件

        压缩包扩展名（zip/rar/7z）由 :meth:`ExtractorTreeModel.enabled_extensions`
        在用户勾选压缩包分类时加入白名单，与其他扩展名统一过滤，不再有 archive 特例。
        压缩包内部条目同样由 :class:`ArchiveScanner` 按此白名单过滤。
        """
        if entry.is_dir:
            return False
        if self._scan_extensions is None:
            return True
        return entry.extension in self._scan_extensions

    def _scan_entry(self, entry: FileEntry) -> ScanResult:
        """对单个文件应用所有规则，返回扫描结果。

        缓存模式下委托 :meth:`_scan_entry_cached`，否则走 :meth:`_scan_entry_uncached`。
        取消时立即返回空结果，避免已提交的 future 执行无谓的 I/O。
        """
        if self._cancel_event.is_set():
            return ScanResult(path=entry.path, size=entry.size, hits=(), errors=0)
        if self._cache is None:
            return self._scan_entry_uncached(entry)
        return self._scan_entry_cached(entry)

    def _scan_entry_uncached(self, entry: FileEntry) -> ScanResult:
        """对单个文件应用所有规则（无缓存）。

        以下两种情况跳过内容提取（使用空内容提供器），FILENAME/PATH 规则仍可命中：

        - 规则集不含任何 CONTENT 规则（``_content_rule_names`` 为空）——所有文件均跳过 I/O
        - 文件超过 ``max_file_size`` ——大文件跳过避免一次性读入内存导致卡死
        """
        if not self._content_rule_names or (self._max_file_size > 0 and entry.size > self._max_file_size):
            context = MatchContext(entry, content_provider=empty_content_provider)
        else:
            context = MatchContext(entry, content_provider=self._content_provider)
        hits: list[RuleHit] = []
        rule_errors = 0

        # 全局 scan_extensions 已在 _should_scan 阶段按白名单统一过滤，
        # 此处对进入扫描队列的文件应用全部规则（无二次过滤）
        for rule, matcher in self._compiled:
            try:
                with self._perf.measure("match"):
                    result = matcher.matches(context)
            except Exception:
                rule_errors += 1
                logger.warning("规则 %s 求值失败 %s", rule.name, entry.path, exc_info=True)
                continue
            if result.matched:
                hits.append(
                    RuleHit(
                        rule_name=rule.name,
                        severity=rule.severity,
                        detail=result.detail,
                        match_text=result.match_text,
                        match_count=result.match_count,
                        target=result.target,
                        match_texts=result.match_texts,
                        match_description=result.match_description,
                    )
                )

        return ScanResult(path=entry.path, size=entry.size, hits=tuple(hits), errors=rule_errors)

    def _extract_with_cache(self, entry: FileEntry) -> tuple[str, str]:
        """缓存模式的提取+哈希（委托 :func:`extract_with_cache`）。

        iter-109：实际逻辑抽离到 :mod:`fuscan.scanner._cache_phase`，
        本方法保留为薄包装以维持调用点简洁。
        """
        assert self._cache is not None  # 调用方已保证非 None
        return extract_with_cache(entry, self._cache, self._max_file_size, self._perf)

    def _scan_entry_cached(self, entry: FileEntry) -> ScanResult:
        """缓存模式扫描：先查缓存，命中直接复用，未命中走匹配器并写入缓存。

        优化路径：

        1. **filename/path 规则跳过 I/O**：若所有适用规则均不含 CONTENT 目标，
           走 :meth:`_scan_entry_uncached`，避免无谓的哈希计算
        2. **mtime 预筛跳过 read_bytes**：``CacheStore.lookup_file_hash`` 按
           ``(path, mtime, size)`` 查询已登记的 ``file_hash``。若所有适用规则
           都已缓存（命中或未命中），则**完全跳过文件读取**，仅复用缓存结果
        3. **提取内容缓存**（iter-39）：``CacheStore.get_extracted_content`` 按
           ``file_hash`` 查询提取器结果，命中则跳过 ``extract_content_from_bytes``；
           同内容不同路径（如 node_modules 重复依赖）可跳过 docx/pptx 提取开销
        4. **常规路径**：一次 I/O 同时取内容和文件哈希
           （:meth:`_extract_with_cache`），静态闭包包装内容
           传给 :class:`MatchContext`，避免改 MatchContext 接口
        """
        assert self._cache is not None  # 仅类型收窄，调用方已保证非 None
        # 全局 scan_extensions 已在 _should_scan 阶段按白名单统一过滤，
        # 此处对进入扫描队列的文件应用全部规则（无二次过滤）
        has_content_rule = any(rule.name in self._content_rule_names for rule, _, _ in self._compiled_with_hash)
        if not has_content_rule:
            # 无内容规则：跳过文件 I/O，直接走匹配器（filename/path 不需读文件）
            return self._scan_entry_uncached(entry)

        applicable: list[tuple[Rule, Matcher, str]] = list(self._compiled_with_hash)
        rule_hashes = [rh for _, _, rh in applicable]

        # mtime 预筛：若 (path, mtime, size) 已登记且所有规则都已缓存，
        # 完全跳过 read_bytes，仅从缓存重建 ScanResult。
        cached: dict[str, RuleHit | None] | None = None
        with self._perf.measure("cache_lookup"):
            cached_file_hash = self._cache.lookup_file_hash(entry.path, entry.mtime, entry.size)
            if cached_file_hash is not None and rule_hashes:
                cached = self._cache.get_cached_hits(cached_file_hash, rule_hashes)
        if cached_file_hash is not None and cached is not None and all(rh in cached for rh in rule_hashes):
            # 全部规则已缓存命中（含未命中记录），无需读文件
            hits, rule_errors = build_hits_from_cache(applicable, cached)
            # 累积元数据刷新到批量缓冲（无新 scan_results 需写入，hits=()）
            self._add_to_batch(
                BatchWriteItem(
                    file_hash=cached_file_hash,
                    size=entry.size,
                    path=entry.path,
                    mtime=entry.mtime,
                    hits=(),
                )
            )
            return ScanResult(path=entry.path, size=entry.size, hits=tuple(hits), errors=rule_errors)

        # 常规路径：读文件 + 算哈希 + 查提取内容缓存 + 未命中执行提取
        content, file_hash = self._extract_with_cache(entry)

        def _static_provider(_fe: FileEntry) -> str:
            return content

        context = MatchContext(entry, content_provider=_static_provider)

        with self._perf.measure("cache_lookup_hits"):
            cached = self._cache.get_cached_hits(file_hash, rule_hashes) if rule_hashes else {}

        hits: list[RuleHit] = []
        rule_errors = 0
        batch_hits: list[tuple[str, RuleHit | None]] = []
        for rule, matcher, rule_hash in applicable:
            if rule_hash in cached:
                result = cached[rule_hash]
                if result is not None:
                    # 缓存命中（匹配）——填回 rule_name（缓存中为空字符串）
                    hits.append(
                        RuleHit(
                            rule_name=rule.name,
                            severity=result.severity,
                            detail=result.detail,
                            match_text=result.match_text,
                            match_count=result.match_count,
                            target=result.target,
                            match_texts=result.match_texts,
                            match_description=result.match_description,
                        )
                    )
                # else: 缓存记录为未命中，跳过
                continue
            # 未缓存——执行匹配器
            try:
                with self._perf.measure("match"):
                    match_result = matcher.matches(context)
            except Exception:
                rule_errors += 1
                logger.warning("规则 %s 求值失败 %s", rule.name, entry.path, exc_info=True)
                continue
            if match_result.matched:
                hit = RuleHit(
                    rule_name=rule.name,
                    severity=rule.severity,
                    detail=match_result.detail,
                    match_text=match_result.match_text,
                    match_count=match_result.match_count,
                    target=match_result.target,
                    match_texts=match_result.match_texts,
                    match_description=match_result.match_description,
                )
                hits.append(hit)
                batch_hits.append((rule_hash, hit))
            else:
                # 未命中也缓存，避免重复扫描
                batch_hits.append((rule_hash, None))

        # 累积到批量缓冲，达到阈值后由 _add_to_batch 自动 flush
        self._add_to_batch(
            BatchWriteItem(
                file_hash=file_hash,
                size=entry.size,
                path=entry.path,
                mtime=entry.mtime,
                hits=tuple(batch_hits),
            )
        )

        return ScanResult(path=entry.path, size=entry.size, hits=tuple(hits), errors=rule_errors)

    def _add_to_batch(self, item: BatchWriteItem) -> None:
        """累积写入请求到批量缓冲，达到阈值时自动 flush（委托 :class:`BatchBuffer`）。

        iter-109：实际逻辑抽离到 :mod:`fuscan.scanner._cache_phase`。
        无缓存模式下 ``_batch_buffer`` 为 None，调用方 :meth:`_scan_entry_cached` 已保证。
        """
        if self._batch_buffer is not None:
            self._batch_buffer.add(item)

    def _flush_batch(self) -> None:
        """强制 flush 待写批次（委托 :class:`BatchBuffer`）。

        在扫描阶段切换（如进入 archive phase）与 ``scan()`` 末尾调用，
        确保累积的数据不丢失。
        """
        if self._batch_buffer is not None:
            self._batch_buffer.flush()
