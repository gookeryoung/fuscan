"""高并发扫描假卡死修复验证模块。

构造快慢混合文件 + 自定义 content_provider 注入 sleep 模拟大文件提取，
验证并发扫描的 ``wait`` 超时分支能正确切换 ``current_file`` 到真实 in-flight
慢文件，而非陈旧的「上一个完成文件」路径。

验证维度：

1. **超时分支触发**：``wait(timeout=PRE_SCAN_EMIT_INTERVAL_S)`` 超时后 emit 进度
2. **current_file 切换到慢文件**：超时时 ``next(iter(_in_flight_meta))`` 是真实正在扫描的慢文件，且 ``_current_file_size/ext/elapsed_ms`` 与该慢文件同步
3. **elapsed_ms 单调递增**：同一慢文件在多次超时 emit 中 elapsed 持续增长，
   证明在跟踪同一慢文件而非刷新陈旧快文件

公共 API：

- :func:`run_bench_progress`：执行验证并返回 :class:`BenchProgressResult`
- :class:`BenchProgressResult`：验证结果统计（含 ``passed`` 属性便于断言）
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from fuscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)
from fuscan.scanner import Scanner
from fuscan.scanner.context import ContentProvider, FileEntry
from fuscan.scanner.result import ProgressInfo

__all__ = ["BenchProgressResult", "run_bench_progress"]


@dataclass
class BenchProgressResult:
    """假卡死验证结果统计。

    :param total_elapsed_s: 总墙钟耗时（秒）
    :param scanned_files: 扫描文件总数
    :param matched_files: 命中文件数
    :param progress_emits: 进度回调总次数
    :param timeout_emits: 超时分支触发次数（``scanned`` 在两次 emit 间未变）
    :param slow_file_emits: ``current_file`` 为慢文件的 emit 次数
    :param max_elapsed_ms: 单文件最长 ``elapsed_ms`` 及对应文件
    :param slow_seen: 慢文件路径 → ``elapsed_ms`` 序列（验证单调递增）
    :param records: 全部进度记录（供调用方自定义分析）
    """

    total_elapsed_s: float
    scanned_files: int
    matched_files: int
    progress_emits: int
    timeout_emits: int
    slow_file_emits: int
    max_elapsed_ms: float
    max_elapsed_file: str
    slow_seen: dict[str, list[float]]
    records: list[ProgressInfo] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """验证是否通过：超时触发 + current 切换慢文件 + elapsed 单调递增。"""
        if self.timeout_emits == 0:
            return False
        if self.slow_file_emits == 0:
            return False
        return any(len(es) >= 2 and all(b > a for a, b in pairwise(es)) for es in self.slow_seen.values())


def _build_ruleset() -> RuleSet:
    """单条 CONTENT CONTAINS 规则，确保扫描会读取文件内容触发 content_provider。"""
    rule = Rule(
        name="r1",
        severity=Severity.INFO,
        match=LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="secret",
            case_sensitive=False,
        ),
    )
    return RuleSet(version="bench", rules=(rule,), ignore_dirs=(), ignore_paths=())


def _make_slow_content_provider(slow_duration_s: float) -> ContentProvider:
    """包裹默认 provider：对文件名含 'slow' 的文件注入 sleep 模拟大文件提取。

    :param slow_duration_s: 慢文件提取模拟耗时（秒）
    :return: ContentProvider 可直接传给 Scanner
    """
    import fuscan.scanner.context as ctx

    default = ctx.default_content_provider

    def provider(entry: FileEntry) -> str:
        if "slow" in entry.path.name.lower():
            time.sleep(slow_duration_s)
        return default(entry)

    return provider


def run_bench_progress(
    *,
    work_dir: Path,
    fast_files: int = 20,
    slow_files: int = 4,
    slow_duration_s: float = 1.5,
    workers: int = 8,
    progress_interval: float = 0.1,
    on_output: Callable[[str], None] | None = None,
) -> BenchProgressResult:
    """运行假卡死验证并返回统计结果。

    在 ``work_dir`` 下创建 ``fast_NN.txt`` 与 ``slow_NN.txt`` 文件，用自定义
    content_provider 对慢文件注入 ``slow_duration_s`` 秒 sleep 模拟大文件提取，
    以 ``workers`` 并发扫描，收集进度回调并分析超时分支行为。

    :param work_dir: 工作目录（调用方负责创建与清理）
    :param fast_files: 快文件数（默认 20）
    :param slow_files: 慢文件数（默认 4）
    :param slow_duration_s: 慢文件提取模拟耗时（默认 1.5 秒）
    :param workers: 并发 worker 数（默认 8）
    :param progress_interval: 进度回调间隔秒（默认 0.1）
    :param on_output: 输出回调（默认 print），传 ``None`` 静默
    :return: :class:`BenchProgressResult` 统计
    """
    out: Callable[[str], None] = on_output or (lambda _s: None)
    out(f"[setup] {fast_files} 快文件 + {slow_files} 慢文件（每个 {slow_duration_s}s），max_workers={workers}")

    for i in range(fast_files):
        (work_dir / f"fast_{i:02d}.txt").write_text("hello world", encoding="utf-8")
    for i in range(slow_files):
        (work_dir / f"slow_{i:02d}.txt").write_text("secret payload", encoding="utf-8")

    records: list[ProgressInfo] = []
    start = time.perf_counter()

    def on_progress(info: ProgressInfo) -> None:
        records.append(info)

    scanner = Scanner(
        _build_ruleset(),
        content_provider=_make_slow_content_provider(slow_duration_s),
        max_workers=workers,
        on_progress=on_progress,
        progress_interval=progress_interval,
    )
    report = scanner.scan(work_dir)
    total_elapsed = time.perf_counter() - start

    timeout_emits = 0
    slow_file_emits = 0
    max_elapsed_ms = 0.0
    max_elapsed_file = ""
    slow_seen: dict[str, list[float]] = {}
    prev_scanned = -1
    for info in records:
        if info.scanned == prev_scanned:
            timeout_emits += 1
        prev_scanned = info.scanned
        if "slow" in Path(info.current_file).name.lower():
            slow_file_emits += 1
            slow_seen.setdefault(info.current_file, []).append(info.current_file_elapsed_ms)
            if info.current_file_elapsed_ms > max_elapsed_ms:
                max_elapsed_ms = info.current_file_elapsed_ms
                max_elapsed_file = info.current_file

    result = BenchProgressResult(
        total_elapsed_s=total_elapsed,
        scanned_files=report.stats.scanned_files,
        matched_files=report.stats.matched_files,
        progress_emits=len(records),
        timeout_emits=timeout_emits,
        slow_file_emits=slow_file_emits,
        max_elapsed_ms=max_elapsed_ms,
        max_elapsed_file=max_elapsed_file,
        slow_seen=slow_seen,
        records=records,
    )
    out(f"[done] 总耗时 {total_elapsed:.2f}s，扫描 {result.scanned_files} 文件，命中 {result.matched_files}")
    return result
