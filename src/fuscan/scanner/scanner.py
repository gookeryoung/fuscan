"""扫描器：协调遍历器与匹配引擎，输出扫描报告。

三阶段扫描架构：

1. 单线程遍历目录树收集待扫描文件清单（按全局 ``scan_extensions`` 过滤）
2. 对 walk 产物二次筛选（剔除空/超限/不可读/符号链接文件）
3. ``max_workers > 1`` 时用 ThreadPoolExecutor 并发扫描清单，否则顺序扫描

压缩包扫描在 ``max_workers > 1`` 时按 archive 文件级别并行：不同 archive
用线程池并发扫描，单个 archive 内条目顺序执行（避免 reader 共享竞争）。

模块结构：

- :mod:`fuscan.scanner._helpers`：纯函数与模块级常量（内容提供器、规则求值辅助等）
- :mod:`fuscan.scanner._content_buckets`：CONTENT 规则桶构建与匹配（合并 OR 正则加速）
- :mod:`fuscan.scanner._archive_phase`：archive 阶段并行扫描子流程
- :mod:`fuscan.scanner._pipeline_phase`：scan 阶段顺序/并发扫描子流程
- :mod:`fuscan.scanner._filter_phase`：filter 阶段二次筛选子流程
- :mod:`fuscan.scanner._cache_phase`：缓存模式扫描辅助（BatchBuffer/缓存命中重建）
- 本模块：:class:`Scanner` 主类，串联 walk → filter → scan → archive 四阶段
"""

from __future__ import annotations

import logging
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from fuscan.cache.store import BatchWriteItem
from fuscan.perf import FilePerfRecorder, PerfStats
from fuscan.rules.model import (
    LeafMatch,
    MatchTarget,
    Rule,
    RuleSet,
)
from fuscan.scanner._archive_phase import run_archive_phase
from fuscan.scanner._cache_phase import (
    BatchBuffer,
    build_hits_from_cache,
    extract_with_cache,
)
from fuscan.scanner._content_buckets import (
    _ContentRuleBucket,
    build_content_buckets,
    extract_required_exts,
    match_content_via_buckets,
)
from fuscan.scanner._filter_phase import run_filter_phase
from fuscan.scanner._helpers import (
    GIL_YIELD_THRESHOLD_S,
    PROGRESS_LIST_MAX,
    PROGRESS_MIN_DELTA_FILES,
    PROGRESS_MIN_DELTA_MATCHES,
    build_hit_from_match,
    default_extract_content,
    default_extract_content_with_hash,
    empty_content_provider,
    engine_for_extension,
    is_minified_content,
    is_native_engine,
    normalize_max_file_size,
    rebuild_hit_from_cache,
    spec_needs_content,
)
from fuscan.scanner._pipeline_phase import run_pipeline_phase
from fuscan.scanner.context import ContentProvider, FileEntry, MatchContext
from fuscan.scanner.manifest import FileFingerprint, IncrementalManifest
from fuscan.scanner.matchers import Matcher, build_matcher
from fuscan.scanner.result import (
    FilterStats,
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
    from fuscan.rules.whitelist import Whitelist

__all__ = ["Scanner", "default_extract_content", "default_extract_content_with_hash"]

logger = logging.getLogger(__name__)


class _CompiledRuleset:
    """规则集编译产物缓存项（不可变，跨扫描复用）。

    缓存规则编译（``build_matcher``）与 CONTENT 桶构建结果，
    避免每次 Scanner 构造都重新编译所有规则（~112ms → ~0ms 命中）。

    缓存的对象在 Scanner 构造后不会被修改，可安全共享。
    """

    __slots__ = (
        "bucketed_rule_names",
        "compiled",
        "content_rule_names",
        "ext_content_buckets",
        "ext_remaining_rules",
        "global_content_buckets",
        "global_remaining_rules",
    )

    def __init__(
        self,
        compiled: list[tuple[Rule, Matcher]],
        global_content_buckets: list[_ContentRuleBucket],
        global_remaining_rules: list[tuple[Rule, Matcher]],
        ext_content_buckets: dict[str, list[_ContentRuleBucket]],
        ext_remaining_rules: dict[str, list[tuple[Rule, Matcher]]],
        bucketed_rule_names: frozenset[str],
        content_rule_names: frozenset[str],
    ) -> None:
        self.compiled = compiled
        self.global_content_buckets = global_content_buckets
        self.global_remaining_rules = global_remaining_rules
        self.ext_content_buckets = ext_content_buckets
        self.ext_remaining_rules = ext_remaining_rules
        self.bucketed_rule_names = bucketed_rule_names
        self.content_rule_names = content_rule_names


# 模块级编译缓存：id(ruleset) → (weakref, _CompiledRuleset)
# weakref 防止 ruleset 被 GC 后 id 被新对象复用导致假命中
# pyrefly: weakref.ref 为 ReferenceType 泛型，省略类型参数（运行时无影响）
_compiled_cache: dict[int, tuple[weakref.ref, _CompiledRuleset]] = {}  # pyrefly: ignore [implicit-any-type-argument]
_compiled_cache_lock = threading.Lock()
_COMPILED_CACHE_MAX: int = 4

# 措施3 并发降档目标：CONTENT 正则密集 + 非原生（持 GIL）提取器场景，将有效并发
# 降至此值，缓解多 worker 线程独占 GIL 导致的 GUI 冻结。取 2 而非 1：仍保留一路
# 并发让原生提取器（若清单中有少量 PDF/Excel）与 I/O 重叠，又不至于让持 GIL 的
# 纯 Python worker 数量压垮主线程。
_DOWNSCALED_MAX_WORKERS: int = 2


def clear_compiled_cache() -> None:
    """清空规则集编译缓存（供测试隔离与规则变更后强制重编译）。"""
    with _compiled_cache_lock:
        _compiled_cache.clear()


class Scanner:
    """扫描器：对目录或单文件应用规则集，产出扫描报告。

    - 构造时一次性编译规则集为 Matcher 列表，避免重复编译
    - 默认使用提取器注册表（extractors）提取文件内容，支持多格式
    - 支持自定义内容提供器覆盖默认提取逻辑
    - 两阶段架构：先单线程遍历收集文件清单（按全局 ``scan_extensions``
      过滤），再 ``max_workers > 1`` 时用线程池并发扫描清单
    - ``on_progress`` 回调在扫描过程中按时间节流（默认 150ms）反馈进度
    """

    def __init__(  # noqa: PLR0912
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
        whitelist: Whitelist | None = None,
        file_perf: FilePerfRecorder | None = None,
    ) -> None:
        self.ruleset = ruleset
        self._content_provider: ContentProvider = content_provider or default_extract_content
        self._file_perf: FilePerfRecorder | None = file_perf
        # 大文件跳过阈值：None 或 0 表示不限制，否则超过此大小的文件不读取内容
        self._max_file_size: int = normalize_max_file_size(max_file_size)
        self._compiled: list[tuple[Rule, Matcher]]
        self._global_content_buckets: list[_ContentRuleBucket]
        self._global_remaining_rules: list[tuple[Rule, Matcher]]
        self._ext_content_buckets: dict[str, list[_ContentRuleBucket]]
        self._ext_remaining_rules: dict[str, list[tuple[Rule, Matcher]]]
        self._bucketed_rule_names: frozenset[str]
        self._content_rule_names: frozenset[str]

        # 规则编译缓存：ruleset 未变时复用已编译的 Matcher 列表与 CONTENT 桶，
        # 避免每次 Scanner 构造都重新编译所有规则（~112ms → ~0ms 命中）。
        # 缓存键为 id(ruleset)，weakref 防止 ruleset GC 后 id 被新对象复用导致假命中。
        cache_key = id(ruleset)
        compiled_rs: _CompiledRuleset | None = None
        with _compiled_cache_lock:
            entry = _compiled_cache.get(cache_key)
            if entry is not None:
                ref, compiled_rs = entry
                if ref() is not ruleset:
                    # ruleset 已被 GC，id 被新对象复用——清除过期条目
                    compiled_rs = None
                    del _compiled_cache[cache_key]

        if compiled_rs is not None:
            # 缓存命中：复用编译产物
            self._compiled = compiled_rs.compiled
            self._global_content_buckets = compiled_rs.global_content_buckets
            self._global_remaining_rules = compiled_rs.global_remaining_rules
            self._ext_content_buckets = compiled_rs.ext_content_buckets
            self._ext_remaining_rules = compiled_rs.ext_remaining_rules
            self._bucketed_rule_names = compiled_rs.bucketed_rule_names
            self._content_rule_names = compiled_rs.content_rule_names
        else:
            # 缓存未命中：编译规则 + 构建 CONTENT 桶
            self._compiled = [(rule, build_matcher(rule.match)) for rule in ruleset.rules]
            # 规则按 required_exts 分组 + 分别 CONTENT 桶合并
            #
            # - 无扩展名约束（纯 CONTENT / NOT / OR 混合）的规则 → global pairs
            # - 有扩展名约束（如 filename endswith ".env" AND ...）的规则 → 对每个
            #   扩展名单独建 pairs 并分别跑 _build_content_buckets
            #
            # 减少 60%+ 的非必要 CONTENT re 调用（大型混合规则集场景）。
            global_pairs: list[tuple[Rule, Matcher]] = []
            ext_pairs_map: dict[str, list[tuple[Rule, Matcher]]] = {}
            for rule, matcher in self._compiled:
                required = extract_required_exts(rule.match)
                if required is None:
                    global_pairs.append((rule, matcher))
                else:
                    for ext in required:
                        ext_pairs_map.setdefault(ext, []).append((rule, matcher))
            self._global_content_buckets, self._global_remaining_rules = self._build_content_buckets(global_pairs)
            self._ext_content_buckets = {}
            self._ext_remaining_rules = {}
            all_bucketed_names: set[str] = {r.name for b in self._global_content_buckets for r in b.rules}
            for ext, pairs in ext_pairs_map.items():
                buckets, remaining = self._build_content_buckets(pairs)
                self._ext_content_buckets[ext] = buckets
                self._ext_remaining_rules[ext] = remaining
                for b in buckets:
                    for r in b.rules:
                        all_bucketed_names.add(r.name)
            self._bucketed_rule_names = frozenset(all_bucketed_names)
            self._content_rule_names = frozenset(rule.name for rule in ruleset.rules if spec_needs_content(rule.match))
            # 写入缓存供后续 Scanner 复用
            compiled_rs = _CompiledRuleset(
                compiled=self._compiled,
                global_content_buckets=self._global_content_buckets,
                global_remaining_rules=self._global_remaining_rules,
                ext_content_buckets=self._ext_content_buckets,
                ext_remaining_rules=self._ext_remaining_rules,
                bucketed_rule_names=self._bucketed_rule_names,
                content_rule_names=self._content_rule_names,
            )
            with _compiled_cache_lock:
                if len(_compiled_cache) >= _COMPILED_CACHE_MAX:
                    _compiled_cache.clear()
                _compiled_cache[cache_key] = (weakref.ref(ruleset), compiled_rs)

        # 兼容性别名：老代码（_scan_entry_uncached 等）可先临时指向 global 版本，
        # 实际扫描时再叠加 ext 的 buckets/rules
        self._content_buckets = self._global_content_buckets
        self._remaining_uncached_rules = self._global_remaining_rules
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
            scan_extensions=self._scan_extensions,
        )
        self._scan_archives = scan_archives
        self._max_workers = max_workers
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
        # _cancel_event 在 _archive_scanner 前创建，以便传入 cancel_check
        # bound method（避免 lambda 触发 PLW0108，且 bound method 调用更快）
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancel_event = threading.Event()
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
                # 传 cancel_check 让压缩包内部循环能及时响应取消信号。
                # 每 CANCEL_CHECK_INTERVAL 条检查一次，平衡响应性与开销。
                cancel_check=self._cancel_event.is_set,
            )
        self._on_progress = on_progress
        self._progress_interval = progress_interval
        self._last_progress_time: float = 0.0
        # 双门限节流的「上次已发送进度快照」，用于计算 scanned/matched 增量。
        # 初始值为 0，首次 emit 会直接通过（因 elapsed >= 0），后续与当前值比较。
        self._last_progress_scanned: int = 0
        self._last_progress_matched: int = 0
        # GIL 让步时间基线：扫描循环中以此值为起点用 time.perf_counter() 判断
        # 距上次让步是否超过 GIL_YIELD_THRESHOLD_S（5ms），超过才调一次 sleep(0)。
        # 替代原固定计数（顺序 20 / 并发 200）：时间式按实际墙钟判断，
        # 小文件密集场景合并多余 sleep(0)，大文件场景保证每 5ms 让步一次
        self._last_yield_time: float = 0.0
        # 进度 emit 批处理阈值。
        # 并发扫描时每 N 个 future 完成才调用一次 _emit_progress（内部仍有 150ms 节流），
        # 减少 time.perf_counter() + 比较的函数调用开销。
        # 顺序扫描保持每文件 emit（用户期望实时反馈）。
        # 默认并发 batch=10，后续 scan_entries 按 entries 规模自适应调整
        # （见 _adapt_progress_batch），避免一刀切导致小清单过度丢实时性或大清单开销高。
        self._progress_emit_batch: int = 10 if (max_workers and max_workers > 1) else 1
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
        # 抽取为 :class:`BatchBuffer` 子模块，消除 scanner.py 内的锁与
        # 缓冲管理细节；无缓存模式下 :attr:`_cache` 为 None，BatchBuffer 不创建。
        self._batch_buffer: BatchBuffer | None = None
        # 性能聚合统计：PerfStats 始终启用，仅做聚合统计无日志开销，不影响生产性能。
        self._perf: PerfStats = PerfStats()
        if cache is not None:
            self._batch_buffer = BatchBuffer(cache, self._perf)
        # 增量扫描上下文：
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
        # _unchanged_hits 只依赖 prev_report 预索引上次命中结果，
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
        # 误报白名单快照——扫描期间持有不可变快照，UI 增删不影响本次扫描。
        # 在 scan_entries 命中聚合阶段过滤命中白名单的结果（不计入 ScanReport.hits）。
        self._whitelist: Whitelist | None = whitelist
        # collect_entries 阶段 1 walk 结束后批量预热的 file_hash 结果。
        # 键为 str(Path)，值为 file_hash（64 hex，None 表示未登记/不适用）。
        # _scan_entry_cached 优先查本 dict，省掉 SQLite/路径 LRU 查询。
        self._precomputed_file_hashes: dict[str, str | None] = {}
        # 当前扫描文件元信息缓存：_pipeline_phase 在调 _scan_entry 前设置，
        # _emit_progress 读取以填充 ProgressInfo 的单文件字段（size/ext/elapsed_ms）。
        # _current_file_start_time 为 perf_counter 基线，0 表示未设置（walk/archive 阶段）
        self._current_file_path: str = ""
        self._current_file_size: int = 0
        self._current_file_ext: str = ""
        self._current_file_start_time: float = 0.0
        # 并发模式下正在扫描的文件元信息映射（仅主线程读写，无需锁）：
        # path → (size, ext, submit_time)。submit 时登记，future 完成时 pop。
        # wait 超时分支据此同步设置 _current_file_* 为真实 in-flight 文件元信息，
        # 避免 UI 显示「路径是 A、大小/扩展名是上一个完成的 B」的错配，
        # 修复「卡在一个文件后 elapsed_ms 持续涨但 size/ext 不变」的假卡死观感。
        # dict 在 3.7+ 保序：next(iter(...)) 取最早提交（最可能卡最久）的文件。
        self._in_flight_meta: dict[str, tuple[int, str, float]] = {}
        # 有效并发度（措施3：CONTENT 正则密集 + 非原生提取器场景动态降档）：
        # 保留用户配置的原始 self._max_workers 不动（供展示/覆盖语义），实际扫描
        # 分派用 self._effective_max_workers。判据见 _compute_effective_max_workers。
        self._effective_max_workers: int | None = self._compute_effective_max_workers()

    def _compute_effective_max_workers(self) -> int | None:
        """计算有效并发度：CONTENT 正则密集 + 非原生提取器场景动态降档至 2。

        扫描期 GUI 冻结的主因是「多 worker 线程持 GIL 跑纯 Python CPU 密集任务」。
        当以下条件同时成立时，多 worker 只会加剧 GIL 争抢而非提升吞吐（纯 Python
        任务受 GIL 串行化，并发数越高主线程越难抢到 GIL），故降并发至 2 保住 GUI 响应：

        - ``max_workers > 2``：本就低并发无需再降。
        - 规则以 **CONTENT 正则为主**（``_content_rule_names`` 非空）：CONTENT 匹配
          ``re.finditer`` 持 GIL，是主线程争抢的直接对手。
        - 扫描目标 **主要落在非原生引擎**（纯 Python 解析，持 GIL）：由 ``scan_extensions``
          判断——``None``（扫描所有扩展名）以文本源码为主（``charset-normalizer`` 持 GIL），
          视为非原生；显式白名单则看其中原生引擎（PDF/Excel/XML，解析期释放 GIL）扩展名
          占比，占比不足半数视为非原生为主。

        原生引擎为主（如只扫 PDF/XLSX/DOCX）时保持用户配置的高并发——原生代码解析期
        释放 GIL，多线程可真正并行且不长时间独占 GIL，GUI 不受影响。

        :return: 有效并发度；不满足降档条件时返回原始 ``self._max_workers``
        """
        max_workers = self._max_workers
        if max_workers is None or max_workers <= _DOWNSCALED_MAX_WORKERS:
            # 未指定或本就 <=2：无降档空间
            return max_workers
        if not self._content_rule_names:
            # 无 CONTENT 正则规则：主线程无 finditer 争抢对手，保持高并发
            return max_workers
        # 判断扫描目标是否以非原生（持 GIL）引擎为主
        exts = self._scan_extensions
        if exts is None:
            # 扫描所有扩展名：以文本源码为绝对多数（charset-normalizer 持 GIL），
            # 视为非原生为主，降档
            return _DOWNSCALED_MAX_WORKERS
        if not exts:
            # 空白名单（用户全部取消勾选）：无文件可扫，并发度无意义，保持原值
            return max_workers
        native = sum(1 for ext in exts if is_native_engine(ext))
        if native * 2 >= len(exts):
            # 原生引擎扩展名占半数及以上：解析期释放 GIL，保持高并发
            return max_workers
        return _DOWNSCALED_MAX_WORKERS

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
        """扫描根目录，返回完整报告（``collect_entries`` + ``filter_entries`` + ``scan_entries`` 串联）。

        三阶段扫描架构：

        1. **阶段 1 - 收集**：:meth:`collect_entries` 单线程遍历目录树，按全局
           ``scan_extensions`` 过滤生成待扫描文件清单。遍历为 I/O 轻量操作，单线程已足够。
        2. **阶段 2 - 筛选**：:meth:`filter_entries` 对 walk 产物二次筛选，剔除空/
           超限/不可读/符号链接文件，使 scan 阶段分母准确、进度反馈更真实。
        3. **阶段 3 - 扫描**：:meth:`scan_entries` 在 ``max_workers > 1`` 时用
           ThreadPoolExecutor 并发扫描清单，否则顺序扫描。先收集再扫描避免了 walk
           与 scan 争抢磁盘 I/O 导致的吞吐下降，且可对清单做全局后缀过滤减少无效提交。
        4. **阶段 4 - 压缩包**：顺序扫描压缩包内条目（避免 ArchiveScanner 线程安全问题）。

        ``on_progress`` 回调在遍历、筛选和扫描阶段按时间节流反馈进度。
        职责拆分后，``FileStatsWorker`` 可独立调用 :meth:`collect_entries`，
        ``ScanWorker`` 接收 :class:`WalkResult` 后依次调用 :meth:`filter_entries`、
        :meth:`scan_entries` 跳过 walk。
        """
        walk_result = self.collect_entries(root)
        filtered_walk = self.filter_entries(walk_result)
        return self.scan_entries(root, filtered_walk)

    def filter_entries(self, walk_result: WalkResult) -> WalkResult:
        """filter 阶段：对 walk 产物二次筛选，剔除空/超限/不可读/符号链接文件。

        委托 :func:`run_filter_phase` 执行，本方法为薄包装以维持调用点简洁
        （与 :meth:`scan_entries` 委托 :func:`run_pipeline_phase` 模式一致）。

        筛选后返回新 WalkResult，``filtered_entries`` 为可扫描清单，
        ``filter_stats`` 为剔除明细。``scan_entries`` 优先使用 ``filtered_entries``
        （非空时），未调用本方法时回退到 ``entries``（向后兼容）。

        :param walk_result: :meth:`collect_entries` 的产物
        :return: 带 ``filtered_entries`` 与 ``filter_stats`` 的新 WalkResult
        """
        return run_filter_phase(self, walk_result)  # pyrefly: ignore [bad-argument-type]

    def collect_entries(self, root: Path) -> WalkResult:
        """walk 阶段：单线程遍历目录树收集待扫描文件清单，按过滤规则筛选。

        独立调用（如 ``FileStatsWorker``）时仅执行 walk 阶段；与 :meth:`scan_entries`
        配合时由 :meth:`scan` 串联。本方法重置进度上下文与收集列表，并在结束时
        清除 ``_cancel_event``，使 Scanner 可在取消/异常后复用（C1 修复语义保持）。

        过滤规则：

        - ``skip_paths``：用户标记跳过的文件计入 ``user_skipped``，不进入清单
        - ``scan_extensions``：不在白名单的文件计入 ``skipped``，不进入清单
          （统一白名单制：None 全选，空集合都不扫，非空按白名单过滤）
        - ``ignore_dirs``：在 ``FileWalker`` 内部过滤，
          跳过的目录收集到 ``skipped_dirs`` 供 UI 展示

        增量模式：构造时传入 ``incremental_manifest`` 启用。walk 阶段
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
            # walker 已做扩展名早期过滤（scan_extensions），不匹配的文件不 yield，
            # 仅累加 walker.skipped_by_extension 计数器，此处累加到 total/skipped
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
                    # rel_key 仅计算一次（之前两分支各算一次，
                    # 大目录下省几十万次 path.relative_to + 字符串替换）
                    rel = IncrementalManifest.rel_key(entry.path, root)
                    # 增量模式：指纹匹配的未变更文件跳过（不加入扫描队列），
                    # 仅累计 _unchanged_count 供 scan_entries 合并统计
                    if prev_fps:
                        prev_fp = prev_fps.get(rel)
                        if prev_fp is not None and prev_fp.mtime == entry.mtime and prev_fp.size == entry.size:
                            self._unchanged_count += 1
                            # 未变更文件指纹直接复用（mtime/size 未变，sha1_prefix 也沿用）
                            new_fingerprints[rel] = prev_fp
                            continue
                    # 变更/新文件/全量模式：记录当前指纹供下次增量扫描
                    new_fingerprints[rel] = FileFingerprint(mtime=entry.mtime, size=entry.size)
                    entries.append(entry)
                    if total % 200 == 0:
                        # 实时同步进度上下文，使 _emit_progress 反映 walk 阶段累计值。
                        # 旧实现在此处未同步 self._progress_*，导致 ProgressInfo 中
                        # total/skipped/user_skipped 始终为旧值（0 或上次扫描值），
                        # UI 的 walkDiscovered/walkSkipped 不增长，进度条不动。
                        # 累加 walker 早期过滤跳过的文件数，使进度统计完整
                        ws = self._walker.skipped_by_extension
                        self._progress_total = total + ws
                        self._progress_skipped = skipped + ws
                        self._progress_user_skipped = user_skipped
                        self._emit_progress(str(entry.path), 0, 0, 0, phase="walk")

        # walk 结束后同步最终统计并强制发送进度，确保 UI 收到完整 walk 统计。
        # 文件数 < 200 时循环内不触发 emit，此处 force=True 是唯一的进度上报点。
        # 累加 walker 早期扩展名过滤跳过的文件数到 total/skipped
        walker_skipped = self._walker.skipped_by_extension
        total += walker_skipped
        skipped += walker_skipped
        self._progress_total = total
        self._progress_skipped = skipped
        self._progress_user_skipped = user_skipped
        self._emit_progress(str(root), 0, 0, 0, phase="walk", force=True)
        # 记录取消状态后清除标志，使 Scanner 可在取消/异常后复用（C1 修复）：
        # 否则下次 collect_entries 的 is_cancelled 仍为 True，静默跳过全部逻辑
        cancelled = self.is_cancelled
        self._cancel_event.clear()

        # 预热路径预筛 file_hash（批量 SQL 查询）
        # 对进入扫描队列的变更/新文件，一条 CTE JOIN 批量查 SQLite，
        # 结果写入 _precomputed_file_hashes + 主动填充路径预筛 LRU，
        # 使后续 _scan_entry_cached 内 lookup_file_hash 全部命中内存，
        # 减少 90%+ 的 SQL 解析/执行器开销。
        # 无缓存模式：跳过预热
        if self._cache is not None and entries and not cancelled:
            with self._perf.measure("cache_lookup_batch"):
                keys: list[tuple[Path, float, int]] = [(e.path, e.mtime, e.size) for e in entries]
                batch_result = self._cache.lookup_file_hashes(keys)
                # 写入 _precomputed_file_hashes（键为 str(path)）
                precomputed: dict[str, str | None] = {}
                for e in entries:
                    key = (e.path, e.mtime, e.size)
                    precomputed[str(e.path)] = batch_result.get(key)
                self._precomputed_file_hashes = precomputed
        else:
            # 无缓存/空列表：清空预计算，避免残留上次扫描结果
            self._precomputed_file_hashes = {}

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
            # 传递未变更文件数到 scan_entries，供合并未变更命中结果
            unchanged_count=self._unchanged_count,
            # 传递本次构建的 manifest，供 scan_entries 合并循环过滤
            # 已删除文件（keys() 即本次 walk 访问到的所有文件，含变更+未变更）
            manifest=self._current_manifest,
        )

    @property
    def current_manifest(self) -> IncrementalManifest | None:
        """本次 collect_entries 构建的新 manifest（供下次增量扫描持久化）。

        全量模式也构建 manifest（记录所有通过过滤的文件指纹），使下次可启用
        增量扫描。增量模式合并未变更文件旧指纹 + 变更文件新指纹。
        """
        return self._current_manifest

    def _adapt_progress_batch(self, n_entries: int) -> None:
        """根据待扫描条目数自适应设置 _progress_emit_batch。

        取代一刀切 batch=10 的默认值，按清单规模在 10~25 之间分档：
        - 小清单（<=1000）：batch=10，保留实时反馈
        - 中等清单（1001~10000）：batch=15，平衡实时性与开销
        - 大清单（10001~50000）：batch=20，降低主线程 as_completed 循环 overhead
        - 超大清单（>50000）：batch=25，最大化减少 emit 相关函数调用

        顺序扫描（max_workers<=1）保持 batch=1 不变，确保用户期望的逐文件反馈。

        原 50/35/20/10 分档对中等清单（300-5000 文件）emit 频次过低，
        与下调后的双门限（50/10）配合时进度条仍可能停滞；下调到 25/20/15/10
        使中等清单在 150ms 时间窗内能 emit 1-2 次，进度条更顺滑。
        """
        if not self._max_workers or self._max_workers <= 1:
            return  # 顺序扫描保持每文件 emit
        if n_entries <= 1000:
            self._progress_emit_batch = 10
        elif n_entries <= 10000:
            self._progress_emit_batch = 15
        elif n_entries <= 50000:
            self._progress_emit_batch = 20
        else:
            self._progress_emit_batch = 25

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

        # 优先使用 filter 阶段产出的 filtered_entries（已剔除空/超限/不可读/符号链接）。
        # 用 filter_stats 是否为 None 判断 filter 是否运行过，而非 filtered_entries 真值——
        # 否则当所有文件都被剔除时 filtered_entries 为空 tuple 会误回退到 entries，
        # 导致已剔除的超限文件被重新纳入扫描。
        # 未调用 filter_entries（filter_stats 为 None）时回退到 entries（向后兼容）。
        if walk_result.filter_stats is not None:
            entries = list(walk_result.filtered_entries)
            filter_removed = walk_result.filter_stats.total_removed
        else:
            entries = list(walk_result.entries)
            filter_removed = 0
        total = walk_result.total
        skipped = walk_result.skipped
        user_skipped = walk_result.user_skipped
        # 从 WalkResult 恢复未变更文件数——collect_entries 在
        # FileStatsWorker 的 Scanner 实例中累加 _unchanged_count，但 ScanWorker
        # 使用新 Scanner 实例调 scan_entries，_unchanged_count 初始为 0。
        # 若不从 WalkResult 恢复，合并条件 _unchanged_count > 0 永远为 False，
        # 未变更命中结果不会被合并，导致增量扫描结果清零。
        self._unchanged_count = walk_result.unchanged_count
        # 从 WalkResult 恢复 manifest——collect_entries 在 FileStatsWorker
        # 的 Scanner 实例中构建 _current_manifest，但 ScanWorker 使用新 Scanner
        # 实例调 scan_entries，_current_manifest 初始为 None。若不从 WalkResult
        # 恢复，合并循环无法过滤已删除文件（_current_manifest.fingerprints.keys()
        # 即本次 walk 访问到的所有文件，已删除文件不在其中）。
        self._current_manifest = walk_result.manifest
        # walk_result.cancelled 来自 collect_entries 末尾的 is_cancelled 快照，
        # 此处沿用：walk 被取消则跳过 scan/archive 阶段
        cancelled = walk_result.cancelled

        results: list[ScanResult] = []
        scanned = 0
        matched = 0
        errors = 0
        matches = 0
        # 压缩包内条目数（archive 阶段扫描的条目，含在 scanned 中）
        archive_entries = 0
        # 复位 walk 累积的进度上下文，供 _emit_progress 在 scan 阶段使用。
        # scan 阶段 total 必须为实际待扫描文件数 len(entries)（符合类型的文件），
        # 而非 walk 阶段的 walk_result.total（含白名单/用户标记跳过的文件）。
        # 否则 progress = scanned / walk_total * 100 会偏低，与"已扫描 N / M 个文件"
        # 数值不匹配（如 1000 个发现 / 300 跳过 → entries=700，但 total=1000 导致
        # 进度条 350/1000=35% 而非正确的 350/700=50%）。
        # total 须纳入未变更文件数（_unchanged_count），因为 scanned
        # 在合并阶段会累加 _unchanged_count（未变更文件视为已扫描），若 total 不含
        # 此部分会导致 scanned > total（分子超出分母）。
        self._progress_total = len(entries) + self._unchanged_count
        self._progress_skipped = skipped
        self._progress_user_skipped = user_skipped
        # 根据 entries 规模自适应 emit batch，避免一刀切 10 导致
        # 小清单丢实时性或大清单主线程 as_completed 循环 overhead 过高。
        self._adapt_progress_batch(len(entries))

        try:
            if not cancelled:
                # 阶段 2：并发扫描（max_workers > 1）或顺序扫描
                # _scan_sequential/_scan_concurrent/_collect_concurrent_results
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
                # 记录压缩包内条目数，用于摘要注明
                archive_entries += d_scanned
                # 压缩包内条目纳入分母，避免 scanned > total（分子超出分母）
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

        # 增量扫描合并：
        # 本次 scan 仅扫描变更文件（entries），未变更文件的命中结果从上次
        # ScanReport 复用（_unchanged_hits 按相对路径索引）。合并后 results
        # 包含变更文件 + 未变更命中文件，统计需相应累加。
        # 合并时过滤已删除文件（不在本次 walk manifest 中）。
        if self._unchanged_count > 0 and self._prev_report is not None:
            matched, matches, scanned = self._merge_unchanged_hits(results, root, matched, matches, scanned)

        # 误报白名单过滤——在命中聚合阶段过滤命中白名单的结果。
        # 过滤位置在增量合并之后、stats 构造之前，确保本次扫描与未变更合并的
        # 命中结果都被同一份白名单覆盖。一个 ScanResult 仅在其所有命中规则
        # 都被白名单覆盖时才整体过滤（部分命中过滤会让用户漏看不需过滤的部分）。
        # 过滤后同步修正 matched/matches 统计，使 ScanStats 与 ScanReport.hits 一致。
        if self._whitelist is not None and self._whitelist.entries and results:
            kept_results: list[ScanResult] = []
            for sr in results:
                if not sr.has_hit:
                    kept_results.append(sr)
                    continue
                # rule_names 为该文件命中的所有规则名（去重保序）
                if self._whitelist.matches_any_rule(sr.path, sr.rule_names):
                    # 全部命中规则被白名单覆盖 → 视为误报，过滤
                    matched -= 1
                    matches -= sr.total_match_count
                else:
                    kept_results.append(sr)
            results = kept_results

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
            # 压缩包内条目数，用于摘要注明
            archive_entries=archive_entries,
            # 增量扫描未变更文件数（本次从 prev_report 复用的文件数）
            unchanged_files=self._unchanged_count,
            # filter 阶段剔除的文件总数（empty/oversize/unreadable/symlink 之和）；
            # 未调用 filter_entries 时为 0（向后兼容）
            filter_removed=filter_removed,
            # PerfStats 始终启用，导出各阶段统计供 GUI/CLI 展示与持久化
            perf_summary=self._perf.to_dict(),
        )
        return ScanReport(root=root, results=tuple(results), stats=stats, cancelled=cancelled)

    def _merge_unchanged_hits(
        self,
        results: list[ScanResult],
        root: Path,
        matched: int,
        matches: int,
        scanned: int,
    ) -> tuple[int, int, int]:
        """合并未变更文件的命中结果到 results，返回更新后的 (matched, matches, scanned)。

        未变更文件的命中结果从 prev_report 复用（_unchanged_hits 按相对
        路径索引），避免重新 I/O 读取未变更文件内容。

        合并时用 _current_manifest.fingerprints.keys() 过滤已删除文件。
        manifest 来自 WalkResult（FileStatsWorker 构建并传递），其 keys() 即本次
        walk 访问到的所有文件（含变更+未变更）。已删除文件不会被 walk 到，故不在
        keys() 中，据此跳过其命中结果，避免已删除文件的命中重新出现在结果列表。
        manifest 为 None 时（全量模式或传递缺失）回退为空集合，不过滤保持旧行为。
        """
        # 收集本次扫描中仍有命中的文件相对路径，避免合并时重复
        changed_hit_rels: set[str] = {IncrementalManifest.rel_key(r.path, root) for r in results if r.has_hit}
        # 本次 walk 访问到的所有文件相对路径集合（含变更+未变更）
        current_rels: set[str] = (
            set(self._current_manifest.fingerprints.keys()) if self._current_manifest is not None else set()
        )
        # 合并未变更文件中仍有命中的结果（本次未重新扫描的文件）
        for rel, sr in self._unchanged_hits.items():
            # 跳过已删除文件（不在本次 walk 访问集合中）
            if current_rels and rel not in current_rels:
                continue
            if rel not in changed_hit_rels:
                results.append(sr)
                matched += 1
                matches += sr.total_match_count
        # 统计累加：未变更文件视为已扫描（复用上次结果，无需重新 I/O）
        scanned += self._unchanged_count
        return matched, matches, scanned

    def _emit_progress(
        self,
        current_file: str,
        scanned: int,
        matched: int,
        errors: int,
        matches: int = 0,
        force: bool = False,
        phase: str = "scan",
        filter_stats: FilterStats | None = None,
        filter_total: int | None = None,
    ) -> None:
        """双门限节流后调用 on_progress 回调。

        相比旧版本仅按 ``_progress_interval`` 做时间节流，新增**增量门限**作为
        「附加抑制条件」（AND 抑制，不会导致慢阶段漏报）：

        1. ``force=True``：直接发送（用于 scan_start / scan_end / phase_change 关键节点）。
        2. 否则先按 ``_progress_interval`` 做时间门。
        3. 时间门通过后再检查「自上次 emit 以来 scanned/matched 增量是否都低于阈值」，
           若两者都低于阈值则**跳过本次 emit**，减少 50k+ 小文件场景下的主线程刷新次数。
        4. 一旦 emit 被允许，立即更新 ``_last_progress_time``/``_last_progress_scanned``/
           ``_last_progress_matched``，作为下一次节流的基线。

        :param matches: 累计匹配文本条数（区别于 matched 的命中文件数）。
        :param force: 为 True 时跳过节流，强制发送（如最终进度）。
        :param phase: 当前扫描阶段：``"walk"``/``"filter"``/``"scan"``/``"archive"``，
            GUI 据此显示不同提示文案，避免 walk 阶段 scanned=0 被误以为卡住。
        :param filter_stats: filter 阶段四类剔除原因累计数；仅 ``phase=="filter"``
            时传入，其他阶段为 None（ProgressInfo 的 filter_removed_* 字段默认为 0）
        :param filter_total: filter 阶段待筛选文件总数；仅 ``phase=="filter"`` 时
            传入，覆盖 ``self._progress_total``（filter 阶段 total 应为 entries 长度，
            而非 scan 阶段的 len(entries)+unchanged_count）
        """
        if self._on_progress is None:
            return
        now = time.perf_counter()
        if not force:
            if now - self._last_progress_time < self._progress_interval:
                return
            # 双门限的「增量抑制」分支——时间窗满足但进度无实质变化时跳过
            # 仅当已有基线（不是首次 emit 或基准不为 0）时才检查增量
            if self._last_progress_scanned > 0 or self._last_progress_matched > 0:
                scanned_delta = scanned - self._last_progress_scanned
                matched_delta = matched - self._last_progress_matched
                if scanned_delta < PROGRESS_MIN_DELTA_FILES and matched_delta < PROGRESS_MIN_DELTA_MATCHES:
                    return
        # 更新基线，供下一轮增量门限使用
        self._last_progress_time = now
        self._last_progress_scanned = scanned
        self._last_progress_matched = matched
        # skipped_dirs/matched_files 快照不再每次 emit 构建（deque → list → 切片 → tuple 的
        # O(PROGRESS_LIST_MAX) 拷贝）。GUI 控制器的 _on_scan_progress 不读取这两个字段，
        # 它们仅作为 ProgressInfo 的占位默认值保留。若后续有消费方需要实时快照，
        # 可在此处恢复构建逻辑。
        # 单文件元信息：scan 阶段且 current_file 非空时从缓存读取
        # walk/filter/archive 阶段或最终空 emit 时清零，避免展示陈旧数据
        if phase == "scan" and current_file:
            current_file_size = self._current_file_size
            current_file_ext = self._current_file_ext
            current_file_engine = engine_for_extension(current_file_ext)
            current_file_elapsed_ms = (
                (now - self._current_file_start_time) * 1000.0 if self._current_file_start_time > 0 else 0.0
            )
        else:
            current_file_size = 0
            current_file_ext = ""
            current_file_engine = ""
            current_file_elapsed_ms = 0.0
        # filter 阶段填充四类剔除字段；其他阶段恒为 0（ProgressInfo 默认值）
        if phase == "filter" and filter_stats is not None:
            filter_removed_empty = filter_stats.removed_empty
            filter_removed_oversize = filter_stats.removed_oversize
            filter_removed_unreadable = filter_stats.removed_unreadable
            filter_removed_symlink = filter_stats.removed_symlink
        else:
            filter_removed_empty = 0
            filter_removed_oversize = 0
            filter_removed_unreadable = 0
            filter_removed_symlink = 0
        # filter 阶段 total 覆盖：filter_total 为 entries 长度，
        # scan 阶段的 _progress_total 含 unchanged_count 不适用于 filter
        progress_total = filter_total if (phase == "filter" and filter_total is not None) else self._progress_total
        self._on_progress(
            ProgressInfo(
                current_file=current_file,
                scanned=scanned,
                total=progress_total,
                skipped=self._progress_skipped,
                matched=matched,
                errors=errors,
                elapsed=now - self._progress_start,
                matches=matches,
                # skipped_dirs/matched_files 使用 ProgressInfo 默认空元组，
                # 不再每次 emit 构建快照（见上方注释）
                phase=phase,
                user_skipped=self._progress_user_skipped,
                current_file_size=current_file_size,
                current_file_ext=current_file_ext,
                current_file_elapsed_ms=current_file_elapsed_ms,
                current_file_engine=current_file_engine,
                filter_removed_empty=filter_removed_empty,
                filter_removed_oversize=filter_removed_oversize,
                filter_removed_unreadable=filter_removed_unreadable,
                filter_removed_symlink=filter_removed_symlink,
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

        统一为白名单制，三种语义：

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

    def _get_effective_buckets_and_rules(
        self,
        entry: FileEntry,
    ) -> tuple[list[_ContentRuleBucket], list[tuple[Rule, Matcher]]]:
        """基于 entry.extension 返回当前文件真正需要执行的 CONTENT 桶
        和 remaining 规则对（global + ext 专属）。

        - 无扩展名的文件（如 Makefile、Dockerfile）：entry.extension == ""，
          仅执行 global_content_buckets / global_remaining_rules。
          （注：dotfile 如 ``.env`` 经 :func:`FileEntry._extract_extension` 解析为
          ``"env"``，会被视为有扩展名的文件。）
        - 有扩展名的文件：global + 对应 ext 的专属 buckets/rules。
        - ``_ext_*`` dict 中未找到 ext 时视为空 list，不抛异常。

        :param entry: 待扫描文件条目（用于读取 extension）
        :return: (buckets, remaining_rules_pairs)
        """
        ext = entry.extension
        if not ext:
            return self._global_content_buckets, self._global_remaining_rules
        ext_buckets = self._ext_content_buckets.get(ext, [])
        ext_remaining = self._ext_remaining_rules.get(ext, [])
        if not ext_buckets and not ext_remaining:
            return self._global_content_buckets, self._global_remaining_rules
        merged_buckets = self._global_content_buckets + ext_buckets
        merged_rules = self._global_remaining_rules + ext_remaining
        return merged_buckets, merged_rules

    def _build_content_buckets(
        self,
        pairs: list[tuple[Rule, Matcher]] | None = None,
    ) -> tuple[list[_ContentRuleBucket], list[tuple[Rule, Matcher]]]:
        """从 compiled pairs 中挑出顶层纯 LeafMatch(target=CONTENT)
        规则按 (mode, case_sensitive) 合并为复合 OR 正则桶。

        薄包装：委托 :func:`fuscan.scanner._content_buckets.build_content_buckets`。
        ``pairs`` 为 None 时回退到 ``self._compiled``（全局全量规则）。

        :return: (buckets, remaining_pairs)
          - buckets: 可合并的 CONTENT 规则桶（数量 = 桶数）
          - remaining_pairs: 无法合入桶（组合型 / FILENAME / PATH 目标）
            的规则+匹配器对，保留给 _scan_entry_uncached 原循环。
        """
        src_pairs = self._compiled if pairs is None else pairs
        return build_content_buckets(src_pairs)

    def _match_content_via_buckets_impl(
        self,
        content: str,
        buckets: list[_ContentRuleBucket],
    ) -> list[RuleHit]:
        """对指定的 CONTENT 桶执行一次 finditer 分派并返回命中列表。

        薄包装：委托 :func:`fuscan.scanner._content_buckets.match_content_via_buckets`，
        可接受任意 buckets 列表（global + ext 专属）。
        """
        return match_content_via_buckets(content, buckets)

    def _match_content_via_buckets(self, content: str) -> list[RuleHit]:
        """通过合并的 CONTENT 桶对 content 执行一次 finditer 分派。

        所有桶均使用 named-group OR 复合正则，遍历 ``compiled.finditer(content)``
        拿到匹配后按 ``m.lastgroup`` 映射到对应规则，按规则汇总：
        - 第一个命中的文本填充 ``match_text`` 和 detail
        - 匹配条数累计到 ``match_count``
        - CONTAINS(case_sensitive=True) 模式：若子串模式为非正则，仍用原
          ``text.count(pattern)`` 统计非重叠次数，避免正则 ``finditer`` 重叠
          语义差异导致的 match_count 与旧实现不一致。
        """
        return self._match_content_via_buckets_impl(content, self._content_buckets)

    def _run_cached_applicable_bucket_pass(
        self,
        content: str,
        bucket_applicable: list[tuple[Rule, Matcher, str]],
        cached: dict[str, RuleHit | None],
        hits: list[RuleHit],
        batch_hits: list[tuple[str, RuleHit | None]],
    ) -> int:
        """对 bucket_applicable（被 CONTENT 桶覆盖的规则集）执行：
        缓存命中先取，再跑一次 `_match_content_via_buckets(content)` 拿命中，
        最后对未缓存 + 未命中规则写 ``None`` 缓存占位。

        直接改写入参 ``hits`` / ``batch_hits``，返回本 pass 中发生的 rule_errors 数。
        """
        if not bucket_applicable:
            return 0
        errors = 0
        # rule_hash 是否已被处理：避免缓存命中后再查桶结果
        processed_hashes: set[str] = set()
        # ----------- 1 阶段：处理缓存命中 --------------------
        for rule, _matcher, rule_hash in bucket_applicable:
            if rule_hash in cached:
                cached_hit = cached[rule_hash]
                if cached_hit is not None:
                    hits.append(rebuild_hit_from_cache(rule, cached_hit))
                processed_hashes.add(rule_hash)
        # ----------- 2 阶段：跑一次 content 桶匹配 --------------------
        bucket_hits_by_name: dict[str, list[RuleHit]] = {}
        if len(processed_hashes) < len(bucket_applicable):
            # 至少有 1 条未缓存，跑桶匹配
            try:
                with self._perf.measure("match"):
                    matched = self._match_content_via_buckets(content)
            except Exception:
                # 桶匹配异常：fallback 到逐条 remaining 处理
                errors += 1
                logger.warning("CONTENT 合并桶(缓存模式)匹配失败 %s", bucket_applicable[0][0].name, exc_info=True)
                matched_empty: list[RuleHit] = []
                matched = matched_empty
            for hit in matched:
                bucket_hits_by_name.setdefault(hit.rule_name, []).append(hit)
        # ----------- 3 阶段：分发桶结果到未处理的 rule_hash --------------------
        for rule, _matcher, rule_hash in bucket_applicable:
            if rule_hash in processed_hashes:
                continue
            hit_list = bucket_hits_by_name.get(rule.name)
            if hit_list:
                # 若同规则多条命中（极少），聚合 match_count，保留第一条
                primary = hit_list[0]
                if len(hit_list) > 1:
                    total = sum(h.match_count for h in hit_list)
                    primary_match_texts = tuple({t for h in hit_list for t in h.match_texts})  # type: ignore[var-annotated]
                    primary = RuleHit(
                        rule_name=primary.rule_name,
                        severity=primary.severity,
                        detail=primary.detail,
                        match_text=primary.match_text,
                        match_count=total,
                        target=primary.target,
                        match_texts=primary_match_texts,
                        match_description=primary.match_description,
                    )
                hits.append(primary)
                batch_hits.append((rule_hash, primary))
            else:
                # 未命中：写 None 到缓存（下次扫同一 file_hash 不再重复匹配）
                batch_hits.append((rule_hash, None))
            processed_hashes.add(rule_hash)
        return errors

    def _scan_entry(self, entry: FileEntry) -> ScanResult:
        """对单个文件应用所有规则，返回扫描结果。

        缓存模式下委托 :meth:`_scan_entry_cached`，否则走 :meth:`_scan_entry_uncached`。
        取消时立即返回空结果，避免已提交的 future 执行无谓的 I/O。

        始终测量单文件解析耗时并回填到 ``ScanResult.elapsed_ms``：并发模式下
        collector 无法从 ``submit_time`` 得到真实单文件耗时（submit_time≈扫描起点，
        now-submit_time 为累计耗时），故由本方法在 worker 内实测。``perf_counter``
        开销为纳秒级，远小于单文件解析耗时，对吞吐无实质影响。

        若 ``file_perf`` 记录器已启用，另在每个文件扫描完成后 ``record`` 总耗时，
        用于性能基线对比与瓶颈定位。
        """
        if self._cancel_event.is_set():
            return ScanResult(path=entry.path, size=entry.size, hits=(), errors=0)
        t0 = time.perf_counter()
        if self._cache is None:
            result = self._scan_entry_uncached(entry)
        else:
            result = self._scan_entry_cached(entry)
        total_ms = (time.perf_counter() - t0) * 1000.0
        if self._file_perf is not None:
            self._file_perf.record(
                path=str(entry.path),
                extension=entry.extension,
                size=entry.size,
                total_ms=total_ms,
                hit_count=len(result.hits),
            )
        # 回填单文件耗时与解析引擎名（引擎按扩展名静态反查，供 GUI 明细行标注）。
        return replace(result, elapsed_ms=total_ms, engine=engine_for_extension(entry.extension))

    def _scan_entry_uncached(self, entry: FileEntry) -> ScanResult:
        """对单个文件应用所有规则（无缓存）。

        当规则集不含任何 CONTENT 规则（``_content_rule_names`` 为空）时跳过
        内容提取（使用空内容提供器），FILENAME/PATH 规则仍可命中。

        通过 ``_get_effective_buckets_and_rules`` 仅取当前 entry.extension
        真正需要的 CONTENT 桶 + remaining 规则，减少 60%+ 非必要 CONTENT re 调用。

        .. note::
            ``max_file_size`` 大文件跳过逻辑已前移到 filter 阶段（:func:`run_filter_phase`），
            超限文件不会进入 ``entries`` 清单，故本方法不再做 size 检查。``scan_file``
            单文件扫描入口未走 filter 阶段，但仍由 :func:`extract_with_cache` /
            内容提供器内部做 size 限制保护（``max_size`` 默认 50MB）。
        """
        # 是否需要读取内容：含 CONTENT 规则时才读取
        need_content = bool(self._content_rule_names)
        skip_content = not need_content
        if skip_content:
            context = MatchContext(entry, content_provider=empty_content_provider)
        else:
            # 先取一次内容并检测是否为压缩/打包产物（min.js/chunk.js/bundle 等）。
            # 命中则以空内容做 CONTENT 匹配——超长单行的正则 finditer 是解析耗时的
            # 根源，跳过后 CONTENT 规则对空串瞬间返回不命中；用静态 provider 复用
            # 已读内容（或替换为空串），避免二次文件 I/O。FILENAME/PATH 规则不依赖
            # 内容，仍正常评估。
            raw_content = self._content_provider(entry)
            match_content = "" if is_minified_content(raw_content) else raw_content

            def _static_provider(_fe: FileEntry, _c: str = match_content) -> str:
                return _c

            context = MatchContext(entry, content_provider=_static_provider)
        hits: list[RuleHit] = []
        rule_errors = 0

        # 仅取当前 entry.ext 真需要的 buckets + remaining
        effective_buckets, effective_remaining = self._get_effective_buckets_and_rules(entry)

        # 对不 skip_content 且有桶的情况，先走合并 CONTENT 桶匹配
        # （一次 finditer + 分派取代 N 次独立 re 调用）
        if not skip_content and effective_buckets:
            try:
                with self._perf.measure("match"):
                    bucket_hits = self._match_content_via_buckets_impl(context.content, effective_buckets)
                hits.extend(bucket_hits)
            except Exception:
                # 桶匹配失败：记录为规则错误，但不要阻断后续 remaining 规则。
                # 具体哪条规则出错在 compile 阶段已通过降级避免，这里兜底捕获异常。
                logger.warning("CONTENT 合并桶匹配失败 %s", entry.path, exc_info=True)
                rule_errors += 1

        # 全局 scan_extensions 已在 _should_scan 阶段按白名单统一过滤，
        # 此处对进入扫描队列的文件应用剩余规则（组合型 / FILENAME/PATH /
        # 非 CONTENT 目标 / 单条规则未达合并阈值 / 编译失败降级）。
        # GIL 让步基线（函数局部）：remaining 规则逐条 matcher.matches 含持 GIL 的
        # 纯 Python re 调用，在 worker 线程内按时间式让步给 GUI 主线程让出 GIL。
        last_yield = time.perf_counter()
        for rule, matcher in effective_remaining:
            try:
                with self._perf.measure("match"):
                    result = matcher.matches(context)
            except Exception:
                rule_errors += 1
                logger.warning("规则 %s 求值失败 %s", rule.name, entry.path, exc_info=True)
                continue
            if result.matched:
                hits.append(build_hit_from_match(rule, result))
            now = time.perf_counter()
            if now - last_yield >= GIL_YIELD_THRESHOLD_S:
                last_yield = now
                time.sleep(0)

        return ScanResult(path=entry.path, size=entry.size, hits=tuple(hits), errors=rule_errors)

    def _extract_with_cache(self, entry: FileEntry) -> tuple[str, str]:
        """缓存模式的提取+哈希（委托 :func:`extract_with_cache`）。

        实际逻辑抽离到 :mod:`fuscan.scanner._cache_phase`，
        本方法保留为薄包装以维持调用点简洁。
        """
        assert self._cache is not None  # 调用方已保证非 None
        return extract_with_cache(entry, self._cache, self._max_file_size, self._perf)

    def _rebuild_from_full_cache(
        self,
        entry: FileEntry,
        cacheable_pairs: list[tuple[Rule, Matcher, str]],
        path_only_pairs: list[tuple[Rule, Matcher, str]],
        cached: dict[str, RuleHit | None],
        cached_file_hash: str,
    ) -> ScanResult:
        """全部 CONTENT 规则已缓存命中且无组合规则需内容时，从缓存重建 ScanResult。

        - ``cacheable_pairs``（纯 CONTENT LeafMatch）：从 ``cached`` 重建命中，不读文件
        - ``path_only_pairs``（FILENAME/PATH LeafMatch + 仅含路径的纯组合）：
          用空内容提供器重新评估，结果**不写回缓存**（避免同 file_hash 不同路径串号）

        路径预筛 ``lookup_file_hash`` 已通过 ``(path, mtime, size)`` 验证路径未变，
        FILENAME/PATH 规则结果在路径不变时必然不变，重新评估结果与首次扫描一致，
        故无需查/写缓存。
        """
        hits, rule_errors = build_hits_from_cache(cacheable_pairs, cached)
        if path_only_pairs:
            context = MatchContext(entry, content_provider=empty_content_provider)
            for rule, matcher, _rule_hash in path_only_pairs:
                try:
                    match_result = matcher.matches(context)
                except Exception:
                    rule_errors += 1
                    logger.warning("规则 %s 求值失败 %s", rule.name, entry.path, exc_info=True)
                    continue
                if match_result.matched:
                    hits.append(build_hit_from_match(rule, match_result))
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

    def _scan_entry_cached(self, entry: FileEntry) -> ScanResult:  # noqa: PLR0912
        """缓存模式扫描：先查缓存，命中直接复用，未命中走匹配器并写入缓存。

        规则按可缓存性分类（避免同 ``file_hash`` 不同路径串号）：

        - **cacheable**（纯 CONTENT LeafMatch）：按 ``file_hash`` 查/写缓存
        - **path_only**（FILENAME/PATH LeafMatch + 纯路径组合）：不缓存，
          路径预筛已验证 ``(path, mtime, size)`` 一致时直接重新评估，无 I/O
        - **combo_needs_content**（含 CONTENT 子项的组合）：不缓存，需读文件

        优化路径：

        1. **无 CONTENT 规则**：走 :meth:`_scan_entry_uncached`，避免哈希计算
        2. **mtime 预筛 + 全 CONTENT 命中**：跳过文件读取，从缓存重建
           CONTENT 命中 + 用空内容提供器重新评估 path_only 规则
        3. **提取内容缓存**：``get_extracted_content`` 命中跳过提取器开销
        4. **常规路径**：一次 I/O 同时取内容和文件哈希（:meth:`_extract_with_cache`）
        """
        assert self._cache is not None  # 仅类型收窄，调用方已保证非 None
        # 全局 scan_extensions 已在 _should_scan 阶段按白名单统一过滤，
        # 此处对进入扫描队列的文件应用全部规则（无二次过滤）
        has_content_rule = any(rule.name in self._content_rule_names for rule, _, _ in self._compiled_with_hash)
        if not has_content_rule:
            # 无内容规则：跳过文件 I/O，直接走匹配器（filename/path 不需读文件）
            return self._scan_entry_uncached(entry)

        applicable: list[tuple[Rule, Matcher, str]] = list(self._compiled_with_hash)

        # 按可缓存性分类规则，避免对 FILENAME/PATH/组合规则误用 file_hash 缓存：
        #
        # 缓存键为 ``file_hash``，仅与文件内容相关。CONTENT 规则结果完全由
        # 内容决定，可按 ``file_hash`` 安全缓存。FILENAME/PATH 规则结果与
        # 路径/文件名相关，同内容不同路径的文件会从缓存读到彼此的命中结果
        # （串号），不可按 ``file_hash`` 缓存——但因路径预筛已验证
        # ``(path, mtime, size)`` 一致，重新评估结果与首次一致，且无 I/O 开销。
        # 组合规则可能依赖内容（``spec_needs_content`` 为 True 时需读文件），
        # 亦不缓存以避免串号。
        cacheable_pairs: list[tuple[Rule, Matcher, str]] = []  # 纯 CONTENT LeafMatch
        path_only_pairs: list[tuple[Rule, Matcher, str]] = []  # FILENAME/PATH LeafMatch + 纯路径组合
        combo_needs_content_pairs: list[tuple[Rule, Matcher, str]] = []  # 含 CONTENT 子项的组合
        for triplet in applicable:
            rule = triplet[0]
            spec = rule.match
            if isinstance(spec, LeafMatch) and spec.target is MatchTarget.CONTENT:
                cacheable_pairs.append(triplet)
            elif spec_needs_content(spec):
                combo_needs_content_pairs.append(triplet)
            else:
                path_only_pairs.append(triplet)
        cacheable_rule_hashes: list[str] = [rh for _, _, rh in cacheable_pairs]
        cacheable_rule_hash_set: set[str] = set(cacheable_rule_hashes)
        has_combo_needs_content: bool = bool(combo_needs_content_pairs)

        # mtime 预筛：若 (path, mtime, size) 已登记且所有 CONTENT 规则都已缓存，
        # 完全跳过 read_bytes——CONTENT 规则从缓存重建，FILENAME/PATH 规则
        # 用空内容提供器重新评估（无 I/O）。组合规则需内容时仍走慢路径。
        cached: dict[str, RuleHit | None] = {}
        cached_file_hash: str | None = None
        if cacheable_rule_hashes:
            with self._perf.measure("cache_lookup"):
                # 优先查批量预热的预计算结果，省掉 LRU 锁/SQLite
                pre_key = str(entry.path)
                if pre_key in self._precomputed_file_hashes:
                    cached_file_hash = self._precomputed_file_hashes[pre_key]
                else:
                    cached_file_hash = self._cache.lookup_file_hash(entry.path, entry.mtime, entry.size)
                if cached_file_hash is not None:
                    cached = self._cache.get_cached_hits(cached_file_hash, cacheable_rule_hashes)
            if (
                cached_file_hash is not None
                and all(rh in cached for rh in cacheable_rule_hashes)
                and not has_combo_needs_content
            ):
                # 全部 CONTENT 规则已缓存命中，无组合规则需内容：跳过 I/O 重建
                return self._rebuild_from_full_cache(entry, cacheable_pairs, path_only_pairs, cached, cached_file_hash)

        # 常规路径：读文件 + 算哈希 + 查提取内容缓存 + 未命中执行提取
        content, file_hash = self._extract_with_cache(entry)

        # 压缩/打包产物（min.js/chunk.js/bundle 等）检测：命中则以空内容做 CONTENT
        # 匹配，跳过超长单行 finditer 这一解析耗时根源。file_hash 仍用真实内容计算
        # （缓存键不变），故本次写入的「未命中」在同 file_hash 再扫时读回一致，行为
        # 稳定；缓存已命中的路径走 _rebuild_from_full_cache 不跑 finditer，无需处理。
        match_content = "" if is_minified_content(content) else content

        def _static_provider(_fe: FileEntry) -> str:
            return match_content

        context = MatchContext(entry, content_provider=_static_provider)

        # 用实际 file_hash 查缓存命中（path 预筛未命中或 file_hash 与预筛不一致时）
        if (
            file_hash is not None
            and cacheable_rule_hashes
            and (cached_file_hash is None or file_hash != cached_file_hash)
        ):
            with self._perf.measure("cache_lookup_hits"):
                cached = self._cache.get_cached_hits(file_hash, cacheable_rule_hashes)
        # else: 无 CONTENT 规则或 file_hash 为 None（空文件/无法提取）→ cached 保持为 {}

        hits: list[RuleHit] = []
        rule_errors = 0
        batch_hits: list[tuple[str, RuleHit | None]] = []
        # applicable 拆分为桶覆盖（bucket_applicable）+ 剩余原循环（remaining）
        # 前者一次桶匹配取代 N 条 content 规则的 matcher.matches()
        bucket_applicable: list[tuple[Rule, Matcher, str]] = []
        remaining_applicable: list[tuple[Rule, Matcher, str]] = []
        if self._bucketed_rule_names:
            for triplet in applicable:
                rule = triplet[0]
                if rule.name in self._bucketed_rule_names:
                    bucket_applicable.append(triplet)
                else:
                    remaining_applicable.append(triplet)
        else:
            remaining_applicable = applicable
        rule_errors += self._run_cached_applicable_bucket_pass(content, bucket_applicable, cached, hits, batch_hits)
        # GIL 让步基线（函数局部，多 worker 各自持有不竞争）：remaining 规则逐条
        # matcher.matches 含持 GIL 的纯 Python re 调用，在 worker 线程内按时间式
        # 让步给 GUI 主线程让出 GIL。缓存命中的 continue 分支开销极小、不持 GIL 太久，
        # 不经过末尾让步检查也无碍。
        last_yield = time.perf_counter()
        for rule, matcher, rule_hash in remaining_applicable:
            if rule_hash in cached:
                result = cached[rule_hash]
                if result is not None:
                    # 缓存命中（匹配）——填回 rule_name/severity（缓存中为空/占位）
                    hits.append(rebuild_hit_from_cache(rule, result))
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
                hit = build_hit_from_match(rule, match_result)
                hits.append(hit)
                # 仅缓存纯 CONTENT LeafMatch 规则，FILENAME/PATH/组合规则
                # 不写入缓存避免同 file_hash 不同路径串号
                if rule_hash in cacheable_rule_hash_set:
                    batch_hits.append((rule_hash, hit))
            elif rule_hash in cacheable_rule_hash_set:
                # 未命中也缓存（仅 CONTENT 规则），避免重复扫描
                batch_hits.append((rule_hash, None))
            now = time.perf_counter()
            if now - last_yield >= GIL_YIELD_THRESHOLD_S:
                last_yield = now
                time.sleep(0)

        # 累积到批量缓冲，达到阈值后由 _add_to_batch 自动 flush
        # file_hash 为 None（空文件 / 无法提取内容）时跳过写入缓存
        if file_hash is not None:
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

        实际逻辑抽离到 :mod:`fuscan.scanner._cache_phase`。
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
