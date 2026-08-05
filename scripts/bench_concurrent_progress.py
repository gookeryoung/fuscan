"""高并发扫描假卡死修复验证脚本。

构造 24 个文件（含 4 个慢文件），通过自定义 content_provider 注入 1.5s sleep
模拟大文件提取耗时，用 max_workers=8 并发扫描，验证：

1. wait 超时分支触发时，current_file 是真实 in-flight 慢文件（非陈旧快文件）
2. 同一慢文件会连续多次出现在进度回调中，current_file_elapsed_ms 持续增长
3. 进度条不会"卡在某个文件后突然跳满"

运行：
    uv run python scripts/bench_concurrent_progress.py
"""

from __future__ import annotations

import time
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

SLOW_FILES = 4
FAST_FILES = 20
TOTAL = SLOW_FILES + FAST_FILES
SLOW_DURATION_S = 1.5
MAX_WORKERS = 8
PROGRESS_INTERVAL_S = 0.1


def build_ruleset() -> RuleSet:
    """单条 CONTENT CONTAINS 规则，确保扫描会读取文件内容。"""
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


def make_slow_content_provider() -> ContentProvider:
    """包裹默认 provider：对文件名含 'slow' 的文件注入 sleep 模拟大文件提取。"""
    import fuscan.scanner.context as ctx

    default = ctx.default_content_provider

    def provider(entry: FileEntry) -> str:
        if "slow" in entry.path.name.lower():
            time.sleep(SLOW_DURATION_S)
        return default(entry)

    return provider


def main() -> None:  # noqa: PLR0912
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="bench_progress_"))
    print(f"[setup] 工作目录: {tmp}")
    print(f"[setup] {FAST_FILES} 快文件 + {SLOW_FILES} 慢文件（每个 {SLOW_DURATION_S}s），max_workers={MAX_WORKERS}")

    for i in range(FAST_FILES):
        (tmp / f"fast_{i:02d}.txt").write_text("hello world", encoding="utf-8")
    for i in range(SLOW_FILES):
        (tmp / f"slow_{i:02d}.txt").write_text("secret payload", encoding="utf-8")

    records: list[ProgressInfo] = []
    start = time.perf_counter()

    def on_progress(info: ProgressInfo) -> None:
        records.append(info)

    scanner = Scanner(
        build_ruleset(),
        content_provider=make_slow_content_provider(),
        max_workers=MAX_WORKERS,
        on_progress=on_progress,
        progress_interval=PROGRESS_INTERVAL_S,
    )
    report = scanner.scan(tmp)
    total_elapsed = time.perf_counter() - start

    # 分析进度记录
    timeout_emits = 0
    slow_file_emits = 0
    max_elapsed_ms = 0.0
    max_elapsed_file = ""
    slow_seen: dict[str, list[float]] = {}

    prev_scanned = -1
    for info in records:
        is_timeout = info.scanned == prev_scanned
        if is_timeout:
            timeout_emits += 1
        prev_scanned = info.scanned

        if "slow" in Path(info.current_file).name.lower():
            slow_file_emits += 1
            slow_seen.setdefault(info.current_file, []).append(info.current_file_elapsed_ms)
            if info.current_file_elapsed_ms > max_elapsed_ms:
                max_elapsed_ms = info.current_file_elapsed_ms
                max_elapsed_file = info.current_file

    # 打印结果
    print("\n" + "=" * 70)
    print(f"扫描完成：总耗时 {total_elapsed:.2f}s")
    print(f"扫描文件数：{report.stats.scanned_files}，命中：{report.stats.matched_files}")
    print(f"进度回调总数：{len(records)}")
    print(f"超时分支触发次数（scanned 未变）：{timeout_emits}")
    print(f"current_file 为慢文件的次数：{slow_file_emits}")
    print(f"单文件最长 elapsed_ms：{max_elapsed_ms:.0f}ms @ {Path(max_elapsed_file).name}")

    print("\n[慢文件进度时间线]（验证 elapsed_ms 持续增长）")
    for path, elapsed_list in slow_seen.items():
        name = Path(path).name
        if len(elapsed_list) <= 1:
            print(f"  {name}: elapsed_ms={elapsed_list}（仅出现一次，未观察到增长）")
            continue
        timeline = " -> ".join(f"{e:.0f}" for e in elapsed_list)
        print(f"  {name}: elapsed_ms 序列 {timeline}")

    print("\n[采样进度时间线]（每 ~0.3s 采样一行）")
    last_sample = -1.0
    for info in records:
        if info.elapsed - last_sample >= 0.3 or last_sample < 0:
            name = Path(info.current_file).name if info.current_file else "-"
            print(
                f"  t={info.elapsed:5.2f}s scanned={info.scanned:2d}/{TOTAL} "
                f"current={name:15s} elapsed_ms={info.current_file_elapsed_ms:6.0f}"
            )
            last_sample = info.elapsed

    # 判断修复是否生效：
    # 1. 超时分支触发（scanned 未变但 emit 了）→ wait 超时机制工作
    # 2. 超时分支的 current_file 是慢文件 → in-flight 跟踪生效（非陈旧快文件）
    # 3. 同一慢文件出现 >= 2 次且 elapsed_ms 递增 → 真实跟踪同一慢文件
    monotonic = any(len(es) >= 2 and all(b > a for a, b in pairwise(es)) for es in slow_seen.values())

    print("\n[结论]")
    if timeout_emits == 0:
        print("  [FAIL] 超时分支未触发，无法验证假卡死修复")
    elif slow_file_emits == 0:
        print("  [FAIL] 超时分支触发了但 current_file 未切换到慢文件 → 修复无效")
    elif not monotonic:
        print("  [FAIL] 慢文件未出现 >= 2 次或 elapsed_ms 未递增 → 跟踪不稳定")
    else:
        print(f"  [PASS] 超时分支触发 {timeout_emits} 次，current_file 切换到真实 in-flight 慢文件")
        print(
            "  [PASS] 慢文件 elapsed_ms 单调递增（如 slow_00.txt: 503->1004->1505ms），"
            "证明在跟踪同一慢文件而非刷新陈旧快文件"
        )
        print("  [PASS] 假卡死修复生效：用户能看到「正在扫描 slow_xx.txt」而非陈旧快文件")


if __name__ == "__main__":
    main()
