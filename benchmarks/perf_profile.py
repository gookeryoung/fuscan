"""性能瓶颈分析脚本：直接调用 Scanner.scan() 输出 perf_summary 各阶段耗时。

用于定位 iter-73 性能回退根因，重点对比 S1/S2/S5 三类场景的各阶段占比。

用法::

    uv run python benchmarks/perf_profile.py --files 500
    uv run python benchmarks/perf_profile.py --files 500 --scenario hot
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.sample_files import generate_files  # noqa: E402
from fuscan.cache import CacheStore  # noqa: E402
from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity  # noqa: E402
from fuscan.scanner import Scanner  # noqa: E402

__all__ = ["main", "profile_scenario"]


def _build_ruleset() -> RuleSet:
    """构建基准规则集：1 个 filename 规则 + 2 个 content 规则。"""
    rules = (
        Rule(
            name="敏感文件名",
            severity=Severity.INFO,
            match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="file_"),
        ),
        Rule(
            name="AWS密钥",
            severity=Severity.CRITICAL,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="AKIA"),
        ),
        Rule(
            name="明文密码",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
        ),
    )
    return RuleSet(version="1.0", rules=rules)


def _format_ms(seconds: float) -> str:
    """秒格式化为毫秒字符串。"""
    return f"{seconds * 1000:.2f}ms"


def _print_perf_summary(label: str, perf_summary: dict[str, dict[str, float]], duration: float) -> None:
    """打印 perf_summary 表格：阶段名 | 总耗时 | 占比 | 调用次数 | 平均 | 最大。"""
    print(f"\n=== {label} ===")
    print(f"总耗时: {_format_ms(duration)} ({duration:.4f}s)")
    if not perf_summary:
        print("  （无 perf_summary 数据）")
        return

    # 按总耗时降序
    items = sorted(perf_summary.items(), key=lambda x: -x[1]["total_ms"])
    grand_total_ms = sum(info["total_ms"] for _, info in items)
    print(f"各阶段累计耗时: {_format_ms(grand_total_ms / 1000.0)}")
    print()

    header = f"{'阶段':<24} {'总计':>12} {'占比':>8} {'调用次数':>10} {'平均':>12} {'最大':>12}"
    print(header)
    print("-" * len(header))
    for name, info in items:
        total_ms = info["total_ms"]
        count = info["count"]
        max_ms = info["max_ms"]
        avg_ms = total_ms / count if count else 0.0
        ratio = total_ms / 1000.0 / duration * 100 if duration > 0 else 0.0
        print(
            f"{name:<24} {_format_ms(total_ms / 1000.0):>12} {ratio:>7.1f}% {count:>10} "
            f"{_format_ms(avg_ms / 1000.0):>12} {_format_ms(max_ms / 1000.0):>12}"
        )


def profile_scenario(
    label: str,
    root: Path,
    scanner: Scanner,
    cache: CacheStore | None = None,
    cache_before: int = 0,
) -> dict[str, object]:
    """执行单场景扫描并返回 perf_summary 与统计信息。"""
    start = time.perf_counter()
    report = scanner.scan(root)
    duration = time.perf_counter() - start
    stats = report.stats
    perf: dict[str, dict[str, float]] = stats.perf_summary or {}

    cache_after = cache.stats().scan_results if cache is not None else 0
    if cache is not None and cache_before > 0:
        misses = max(0, cache_after - cache_before)
        cache_hits = max(0, cache_before - misses)
    else:
        cache_hits = 0
        misses = cache_after
    hit_ratio = cache_hits / cache_before if cache_before > 0 else 0.0

    _print_perf_summary(label, perf, duration)
    print()
    print(f"扫描文件数: {stats.scanned_files}")
    print(f"吞吐量: {stats.scanned_files / duration:.1f} files/s" if duration > 0 else "N/A")
    print(f"缓存命中: {cache_hits} / {cache_before} = {hit_ratio * 100:.1f}%")
    print(f"新增缓存条目: {misses}")

    return {
        "label": label,
        "duration": duration,
        "files": stats.scanned_files,
        "files_per_sec": stats.scanned_files / duration if duration > 0 else 0.0,
        "cache_hits": cache_hits,
        "cache_total": cache_before,
        "hit_ratio": hit_ratio,
        "perf_summary": perf,
    }


def main(argv: list[str] | None = None) -> int:
    """脚本入口。"""
    parser = argparse.ArgumentParser(description="fuscan 性能瓶颈分析")
    parser.add_argument("--files", type=int, default=500, metavar="N", help="生成文件数（默认 500）")
    parser.add_argument("--workers", type=int, default=4, metavar="N", help="并发线程数（默认 4）")
    parser.add_argument(
        "--scenario",
        choices=("all", "seq", "concurrent", "hot"),
        default="all",
        help="运行的场景（默认 all）",
    )
    parser.add_argument("--seed", type=int, default=42, metavar="N", help="随机种子")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "fuscan_perf",
        metavar="DIR",
        help="工作目录",
    )
    parser.add_argument("--output", type=Path, default=None, metavar="FILE", help="保存 JSON 到文件")
    args = parser.parse_args(argv)

    cpu_count = max(1, __import__("os").cpu_count() or 4)

    data_dir = args.workdir / "files"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    paths = generate_files(data_dir, args.files, args.seed)
    print(f"已生成 {len(paths)} 个测试文件到 {data_dir}")
    total_bytes = sum(p.stat().st_size for p in paths if p.exists())
    print(f"总字节数: {total_bytes} ({total_bytes / 1024 / 1024:.2f} MB)")

    rs = _build_ruleset()
    workers = args.workers
    results: list[dict[str, object]] = []

    if args.scenario in ("all", "seq"):
        scanner = Scanner(rs, max_workers=1)
        result = profile_scenario("S1 单线程无缓存 (workers=1)", data_dir, scanner)
        results.append(result)

    if args.scenario in ("all", "concurrent"):
        scanner = Scanner(rs, max_workers=workers)
        result = profile_scenario(f"S2 {workers}线程无缓存", data_dir, scanner)
        results.append(result)

        scanner = Scanner(rs, max_workers=cpu_count)
        result = profile_scenario(f"S3 {cpu_count}线程无缓存", data_dir, scanner)
        results.append(result)

    if args.scenario in ("all", "hot"):
        cache_path = args.workdir / "cache.db"
        if cache_path.exists():
            cache_path.unlink()
        args.workdir.mkdir(parents=True, exist_ok=True)
        cache = CacheStore(cache_path)
        try:
            # 冷启动填缓存
            scanner = Scanner(rs, max_workers=workers, cache=cache)
            before = cache.stats().scan_results
            result_cold = profile_scenario(f"S4 {workers}线程+缓存冷", data_dir, scanner, cache, before)
            results.append(result_cold)

            # 热缓存（重点场景）
            scanner = Scanner(rs, max_workers=workers, cache=cache)
            before = cache.stats().scan_results
            result_hot = profile_scenario(f"S5 {workers}线程+缓存热（重点）", data_dir, scanner, cache, before)
            results.append(result_hot)
        finally:
            cache.close()

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n结果已保存到 {args.output}")

    # 总结对比表
    print("\n" + "=" * 80)
    print("=== 场景对比 ===")
    print(f"{'场景':<32} {'耗时(s)':>10} {'文件/秒':>10} {'命中率':>10}")
    print("-" * 80)
    for r in results:
        hit = f"{r['hit_ratio'] * 100:.1f}%" if r["cache_total"] else "-"  # type: ignore[index]
        print(
            f"{r['label']:<32} {r['duration']:>10.4f} "  # type: ignore[index]
            f"{r['files_per_sec']:>10.1f} {hit:>10}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
