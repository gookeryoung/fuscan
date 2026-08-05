"""扫描 fuscan 项目自身，分析性能瓶颈。

运行方式：
    uv run python scripts/profile_self_scan.py
"""

from __future__ import annotations

import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

from fuscan.rules import load_builtin_ruleset
from fuscan.scanner import Scanner

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """扫描 fuscan 项目自身，输出性能分析。"""
    root = PROJECT_ROOT
    print(f"扫描目标: {root}")
    print()

    # 加载内置规则
    ruleset = load_builtin_ruleset()
    print(f"规则总数: {len(ruleset.rules)}")
    print(f"scan_extensions: {ruleset.scan_extensions}")
    print(f"ignore_dirs: {ruleset.ignore_dirs}")
    print()

    # 第一次扫描：带 scan_extensions 过滤
    print("=" * 80)
    print("阶段 1：带 scan_extensions 过滤的扫描")
    print("=" * 80)
    scanner = Scanner(ruleset, scan_extensions=ruleset.scan_extensions)
    t0 = time.perf_counter()
    report = scanner.scan(root)
    elapsed = time.perf_counter() - t0
    print(f"总耗时: {elapsed:.2f}s")
    print(f"总文件数: {report.stats.total_files}")
    print(f"已扫描: {report.stats.scanned_files}")
    print(f"跳过(后缀): {report.stats.skipped_files}")
    print(f"命中: {report.stats.matched_files}")
    print(f"错误: {report.stats.errors}")
    print()

    # 第二次扫描：cProfile 分析
    print("=" * 80)
    print("阶段 2：cProfile 分析")
    print("=" * 80)
    profiler = cProfile.Profile()
    scanner2 = Scanner(ruleset, scan_extensions=ruleset.scan_extensions)
    t0 = time.perf_counter()
    profiler.enable()
    scanner2.scan(root)
    profiler.disable()
    elapsed2 = time.perf_counter() - t0
    print(f"总耗时(含 profiler): {elapsed2:.2f}s")
    print()

    # 打印 top 30 热点函数
    print("-" * 80)
    print("cProfile top 30 热点（按 cumtime 排序）:")
    print("-" * 80)
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.sort_stats("cumulative")
    stats.print_stats(30)
    print(stream.getvalue())

    # 打印 top 20 热点函数（按 tottime 排序）
    print("-" * 80)
    print("cProfile top 20 热点（按 tottime 排序）:")
    print("-" * 80)
    stream2 = io.StringIO()
    stats2 = pstats.Stats(profiler, stream=stream2)
    stats2.sort_stats("tottime")
    stats2.print_stats(20)
    print(stream2.getvalue())

    return 0


if __name__ == "__main__":
    sys.exit(main())
