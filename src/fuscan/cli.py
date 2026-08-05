"""命令行入口。

支持子命令：

- ``scan``：扫描指定路径，输出命中报告
- ``benchmark``（别名 ``bench``）：多轮扫描测量各阶段性能，支持导出/对比基准线
- ``bp``：验证并发扫描假卡死修复（构造慢文件场景，检查 in-flight 进度跟踪）
- ``rules``：校验规则文件格式
- ``version``：显示版本信息
- ``gui``：启动图形界面
- ``cache``：管理扫描结果缓存（stats/clear/prune）

用法示例：

.. code-block:: bash

    fuscan scan /path/to/scan -r rules/custom.yaml -o json -f report.json
    fuscan benchmark /path/to/scan --rounds 5 --save-baseline baseline.json
    fuscan benchmark /path/to/scan --baseline baseline.json
    fuscan bp --slow-duration 1.5 --workers 8
    fuscan rules -r rules/custom.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from fuscan import __version__
from fuscan.benchmark import BaselineComparison, BenchmarkResult
from fuscan.config import load_config
from fuscan.export.cli_output import output_report
from fuscan.rules import RuleError, RuleSet, load_ruleset, load_with_builtin, merge_multiple_rulesets
from fuscan.scanner import Scanner, ScanReport

if TYPE_CHECKING:
    from fuscan.perf import FilePerfRecorder

__all__ = ["build_parser", "main"]

logger = logging.getLogger("fuscan")


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="fuscan",
        description="通用文件扫描器：基于 YAML 规则的多格式内容扫描工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"fuscan {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="增加日志详细度（-v INFO, -vv DEBUG）")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    # scan 子命令
    scan_parser = subparsers.add_parser("scan", help="扫描指定路径")
    scan_parser.add_argument("path", type=Path, help="要扫描的目录或文件路径")
    scan_parser.add_argument(
        "-r",
        "--rules",
        type=Path,
        action="append",
        default=None,
        metavar="FILE",
        help="规则文件路径（YAML，可重复指定多个，后面的覆盖前面的同名规则）",
    )
    scan_parser.add_argument(
        "-o",
        "--output-format",
        choices=["text", "json", "csv", "pdf", "excel"],
        default="text",
        help="输出格式，默认 text（pdf/excel 需配合 -f 输出到文件）",
    )
    scan_parser.add_argument("-f", "--output-file", type=Path, default=None, help="输出到文件（默认 stdout）")
    scan_parser.add_argument("--max-depth", type=int, default=None, help="最大递归深度")
    scan_parser.add_argument(
        "--max-file-size",
        type=int,
        default=None,
        metavar="MB",
        help="跳过大于此大小（MB）的文件，避免大文件卡死；0 表示不限制（默认走配置或 100MB）",
    )
    scan_parser.add_argument(
        "--ignore-dir", action="append", default=[], metavar="DIR", help="额外忽略目录名（可重复）"
    )
    scan_parser.add_argument("--no-builtin", action="store_true", help="禁用内置通用规则（需配合 -r 使用）")
    scan_parser.add_argument("--no-color", action="store_true", help="禁用彩色输出")
    scan_parser.add_argument("--no-cache", action="store_true", help="禁用扫描结果缓存")
    scan_parser.add_argument(
        "--cache-path", type=Path, default=None, metavar="DB", help="自定义缓存数据库路径（默认 ~/.fuscan/cache.db）"
    )
    scan_parser.add_argument(
        "--perf", action="store_true", help="启用性能详细日志（PerfTimer 各阶段进入/退出耗时，需配合 -vv）"
    )
    scan_parser.add_argument(
        "--perf-save", type=Path, default=None, metavar="FILE", help="将性能统计保存为 JSON 文件供后续分析"
    )
    scan_parser.add_argument(
        "--file-perf",
        type=Path,
        default=None,
        metavar="FILE",
        help="记录单文件扫描耗时到 JSON 基线文件（用于调试与优化对比）",
    )
    scan_parser.add_argument(
        "--file-perf-compare",
        type=Path,
        default=None,
        metavar="BASELINE",
        help="对比单文件性能基线，输出回归/改善文件列表",
    )

    # benchmark 子命令：多轮扫描测量各阶段性能，支持导出/对比基准线
    bench_parser = subparsers.add_parser("benchmark", aliases=["bench"], help="多轮扫描测量各阶段性能并支持基准线对比")
    bench_parser.add_argument("path", type=Path, help="要基准测试的目录或文件路径")
    bench_parser.add_argument(
        "-r",
        "--rules",
        type=Path,
        action="append",
        default=None,
        metavar="FILE",
        help="规则文件路径（YAML，可重复指定多个，后面的覆盖前面的同名规则）",
    )
    bench_parser.add_argument("--rounds", type=int, default=5, metavar="N", help="正式测量轮数（默认 5）")
    bench_parser.add_argument("--warmup", type=int, default=1, metavar="N", help="预热轮数，不计入统计（默认 1）")
    bench_parser.add_argument(
        "--save-baseline", type=Path, default=None, metavar="FILE", help="将本次结果导出为基准线 JSON 文件"
    )
    bench_parser.add_argument(
        "--baseline", type=Path, default=None, metavar="FILE", help="加载历史基准线并与本次结果逐阶段对比回归"
    )
    bench_parser.add_argument(
        "-o", "--output-format", choices=["table", "json"], default="table", help="输出格式，默认 table"
    )
    bench_parser.add_argument(
        "--max-file-size",
        type=int,
        default=None,
        metavar="MB",
        help="跳过大于此大小（MB）的文件；0 表示不限制（默认走配置或 100MB）",
    )
    bench_parser.add_argument(
        "--ignore-dir", action="append", default=[], metavar="DIR", help="额外忽略目录名（可重复）"
    )
    bench_parser.add_argument("--no-builtin", action="store_true", help="禁用内置通用规则（需配合 -r 使用）")
    bench_parser.add_argument("--no-cache", action="store_true", help="禁用扫描结果缓存")
    bench_parser.add_argument(
        "--cache-path", type=Path, default=None, metavar="DB", help="自定义缓存数据库路径（默认 ~/.fuscan/cache.db）"
    )

    # rules 子命令
    rules_parser = subparsers.add_parser("rules", help="校验规则文件")
    rules_parser.add_argument("-r", "--rules", type=Path, required=True, help="规则文件路径（YAML）")

    # gui 子命令
    subparsers.add_parser("gui", help="启动图形界面")

    # version 子命令
    subparsers.add_parser("version", help="显示版本信息")

    # cache 子命令：--cache-path 通过 parents 共享给各子操作，支持 `cache <action> --cache-path X` 顺序
    cache_parent = argparse.ArgumentParser(add_help=False)
    cache_parent.add_argument(
        "--cache-path", type=Path, default=None, metavar="DB", help="自定义缓存数据库路径（默认 ~/.fuscan/cache.db）"
    )
    cache_parser = subparsers.add_parser("cache", help="管理扫描结果缓存")
    cache_sub = cache_parser.add_subparsers(dest="cache_action", metavar="<action>", required=True)
    cache_sub.add_parser("stats", help="显示缓存统计信息", parents=[cache_parent])
    cache_sub.add_parser("clear", help="清空缓存（删除数据库文件）", parents=[cache_parent])
    cache_prune = cache_sub.add_parser("prune", help="清理过期文件缓存", parents=[cache_parent])
    cache_prune.add_argument("--max-age-days", type=int, default=30, help="清理超过指定天数的文件缓存（默认 30）")

    # bp 子命令：验证并发扫描假卡死修复（构造慢文件场景，检查 in-flight 进度跟踪）
    bp_parser = subparsers.add_parser(
        "bp",
        help="验证并发扫描假卡死修复（构造慢文件场景，检查 in-flight 进度跟踪）",
    )
    bp_parser.add_argument("--fast-files", type=int, default=20, metavar="N", help="快文件数（默认 20）")
    bp_parser.add_argument("--slow-files", type=int, default=4, metavar="N", help="慢文件数（默认 4）")
    bp_parser.add_argument(
        "--slow-duration",
        type=float,
        default=1.5,
        metavar="S",
        help="慢文件提取模拟耗时秒数（默认 1.5）",
    )
    bp_parser.add_argument("--workers", type=int, default=8, metavar="N", help="并发 worker 数（默认 8）")
    bp_parser.add_argument(
        "--progress-interval",
        type=float,
        default=0.1,
        metavar="S",
        help="进度回调间隔秒（默认 0.1）",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口。

    :param argv: 命令行参数（默认从 sys.argv 读取）
    :return: 退出码，0 成功，非 0 失败
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    _configure_logging(getattr(args, "verbose", 0))

    try:
        if args.command == "scan":
            return _cmd_scan(args)
        if args.command in ("benchmark", "bench"):
            return _cmd_benchmark(args)
        if args.command == "rules":
            return _cmd_rules(args)
        if args.command == "gui":
            return _cmd_gui(args)
        if args.command == "bp":
            return _cmd_bp(args)
        if args.command == "cache":
            return _cmd_cache(args)
        if args.command == "version":
            print(f"fuscan {__version__}")
            return 0
    except RuleError as exc:
        print(f"规则错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except Exception as exc:
        logger.exception("执行失败")
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    return 0  # pragma: no cover


def _load_ruleset_from_args(args: argparse.Namespace) -> RuleSet | None:
    """根据 CLI 参数加载规则集，返回 None 表示出错（错误信息已打印）。

    - ``--no-builtin``：仅加载用户规则（需至少一个 -r），多个文件按顺序合并
    - 默认：内置规则 + 用户规则（按顺序合并，后者覆盖前者）
    """
    rules_paths: list[Path] | None = args.rules

    if args.no_builtin:
        if not rules_paths:
            print("错误: --no-builtin 需要配合 -r/--rules 使用", file=sys.stderr)
            return None
        for p in rules_paths:
            if not p.exists():
                print(f"错误: 规则文件不存在: {p}", file=sys.stderr)
                return None
        rulesets = [load_ruleset(p) for p in rules_paths]
        return merge_multiple_rulesets(*rulesets)

    for p in rules_paths or []:
        if not p.exists():
            print(f"错误: 规则文件不存在: {p}", file=sys.stderr)
            return None
    return load_with_builtin(rules_paths)


def _cmd_scan(args: argparse.Namespace) -> int:
    """执行 scan 子命令。"""
    scan_path: Path = args.path

    if not scan_path.exists():
        print(f"错误: 扫描路径不存在: {scan_path}", file=sys.stderr)
        return 1

    ruleset = _load_ruleset_from_args(args)
    if ruleset is None:
        return 1

    # --perf 启用 PerfTimer 详细日志
    if getattr(args, "perf", False):
        from fuscan.perf import set_perf_enabled

        set_perf_enabled(True)

    config = load_config()
    # 扫描参数已迁移到 RuleSet 顶层（scan_params/ignore_dirs）
    sp = ruleset.scan_params
    ignore_dirs = _merge_ignore_dirs(ruleset.ignore_dirs, args.ignore_dir)

    cache_enabled = sp.cache_enabled if sp is not None and sp.cache_enabled is not None else True
    use_cache = cache_enabled and not args.no_cache
    cache_path = _resolve_cache_path(args.cache_path, config.cache_path)

    # 大文件跳过阈值：CLI 参数优先（MB 转 byte），其次走规则集，None 让 Scanner 用默认值
    max_file_size = _resolve_max_file_size(args.max_file_size, sp.max_file_size if sp is not None else None)

    # 单文件性能基线记录
    file_perf_path: Path | None = getattr(args, "file_perf", None)
    file_perf_compare: Path | None = getattr(args, "file_perf_compare", None)
    file_perf = None
    if file_perf_path is not None or file_perf_compare is not None:
        from fuscan.perf import FilePerfRecorder

        file_perf = FilePerfRecorder()

    if use_cache and cache_path is not None:
        # 仅在启用缓存时加载 SQLite 依赖
        from fuscan.cache import CacheStore, compute_source_files

        cache = CacheStore(cache_path)
        try:
            source_files = compute_source_files(args.rules or [], use_builtin=not args.no_builtin)
            scanner = Scanner(
                ruleset,
                max_depth=args.max_depth,
                max_file_size=max_file_size,
                ignore_dirs=ignore_dirs,
                cache=cache,
                source_files=source_files,
                scan_extensions=ruleset.scan_extensions,
                file_perf=file_perf,
            )
            report = _run_scan(scanner, scan_path, args)
        finally:
            cache.close()
    else:
        scanner = Scanner(
            ruleset,
            max_depth=args.max_depth,
            max_file_size=max_file_size,
            ignore_dirs=ignore_dirs,
            scan_extensions=ruleset.scan_extensions,
            file_perf=file_perf,
        )
        report = _run_scan(scanner, scan_path, args)

    output_report(report, args.output_format, args.output_file)
    _print_summary(report)

    # --perf-save 持久化性能统计到 JSON
    perf_save: Path | None = getattr(args, "perf_save", None)
    if perf_save and report.stats.perf_summary:
        from fuscan.perf import PerfStats

        perf_obj = PerfStats()
        perf_obj.merge_dict(report.stats.perf_summary)
        perf_obj.save_to_json(
            perf_save,
            meta={
                "scanned_files": report.stats.scanned_files,
                "duration_seconds": report.stats.duration_seconds,
                "speed_files_per_sec": round(report.stats.speed, 1),
                "root": str(report.root),
            },
        )
        print(f"性能统计已保存到: {perf_save}", file=sys.stderr)

    # --file-perf 单文件性能基线
    if file_perf is not None:
        _handle_file_perf(file_perf, file_perf_path, file_perf_compare, report)

    return 0


def _handle_file_perf(
    file_perf: FilePerfRecorder,
    file_perf_path: Path | None,
    file_perf_compare: Path | None,
    report: ScanReport,
) -> None:
    """处理单文件性能基线：打印汇总、保存基线、对比历史基线。"""
    from fuscan.perf import FilePerfRecorder

    file_perf.print_summary(log=logger)
    if file_perf_path is not None:
        file_perf.save_to_json(
            file_perf_path,
            meta={"scanned_files": report.stats.scanned_files, "root": str(report.root)},
        )
        print(f"单文件性能基线已保存到: {file_perf_path}", file=sys.stderr)
    if file_perf_compare is not None:
        baseline = FilePerfRecorder.load_from_json(file_perf_compare)
        diffs = file_perf.compare(baseline, threshold_pct=20.0)
        if diffs:
            print(f"\n性能差异（对比基线 {file_perf_compare.name}，阈值 ±20%）:", file=sys.stderr)
            for d in diffs:
                direction = "回归" if d.delta_pct > 0 else "改善"
                print(
                    f"  {direction} {d.delta_pct:+.1f}%  {d.baseline_ms:.2f}ms → {d.current_ms:.2f}ms  {d.path}",
                    file=sys.stderr,
                )
        else:
            print(f"\n无显著性能差异（对比基线 {file_perf_compare.name}）", file=sys.stderr)


def _run_scan(scanner: Scanner, scan_path: Path, args: argparse.Namespace) -> ScanReport:
    """执行扫描并记录日志。"""
    rules_desc = f"规则: {args.rules}" if args.rules else "内置通用规则"
    logger.info("开始扫描 %s（%s，规则数: %d）", scan_path, rules_desc, len(scanner.ruleset.rules))
    return scanner.scan(scan_path)


def _cmd_benchmark(args: argparse.Namespace) -> int:
    """执行 benchmark 子命令：多轮扫描测量各阶段性能，支持导出/对比基准线。"""
    from fuscan.benchmark import compare_to_baseline, load_baseline, run_benchmark, save_baseline

    scan_path: Path = args.path
    if not scan_path.exists():
        print(f"错误: 路径不存在: {scan_path}", file=sys.stderr)
        return 1
    if args.rounds < 1:
        print(f"错误: --rounds 必须 >= 1（收到 {args.rounds}）", file=sys.stderr)
        return 1
    if args.warmup < 0:
        print(f"错误: --warmup 必须 >= 0（收到 {args.warmup}）", file=sys.stderr)
        return 1

    ruleset = _load_ruleset_from_args(args)
    if ruleset is None:
        return 1

    # --baseline 若指定，先加载校验，避免跑完多轮才发现文件缺失
    baseline: dict[str, object] | None = None
    if args.baseline is not None:
        try:
            baseline = load_baseline(args.baseline)
        except (FileNotFoundError, ValueError) as exc:
            print(f"错误: 加载基准线失败: {exc}", file=sys.stderr)
            return 1

    config = load_config()
    # 扫描参数已迁移到 RuleSet 顶层（scan_params/ignore_dirs）
    sp = ruleset.scan_params
    ignore_dirs = _merge_ignore_dirs(ruleset.ignore_dirs, args.ignore_dir)
    cache_enabled = sp.cache_enabled if sp is not None and sp.cache_enabled is not None else True
    use_cache = cache_enabled and not args.no_cache
    cache_path = _resolve_cache_path(args.cache_path, config.cache_path)
    max_file_size = _resolve_max_file_size(args.max_file_size, sp.max_file_size if sp is not None else None)

    def on_round(idx: int, total: int, label: str) -> None:
        logger.info("基准 %s 第 %d/%d 轮", label, idx, total)

    if use_cache and cache_path is not None:
        from fuscan.cache import CacheStore, compute_source_files

        cache = CacheStore(cache_path)
        try:
            source_files = compute_source_files(args.rules or [], use_builtin=not args.no_builtin)
            scanner = Scanner(
                ruleset,
                max_file_size=max_file_size,
                ignore_dirs=ignore_dirs,
                cache=cache,
                source_files=source_files,
                scan_extensions=ruleset.scan_extensions,
            )
            result = run_benchmark(scanner, scan_path, rounds=args.rounds, warmup=args.warmup, on_round=on_round)
        finally:
            cache.close()
    else:
        scanner = Scanner(
            ruleset, max_file_size=max_file_size, ignore_dirs=ignore_dirs, scan_extensions=ruleset.scan_extensions
        )
        result = run_benchmark(scanner, scan_path, rounds=args.rounds, warmup=args.warmup, on_round=on_round)

    comparison = compare_to_baseline(result, baseline) if baseline is not None else None

    if args.output_format == "json":
        print(_benchmark_json(result, comparison))
    else:
        print(_benchmark_table(result, comparison))

    if args.save_baseline is not None:
        save_baseline(result, args.save_baseline)
        print(f"基准线已保存到: {args.save_baseline}", file=sys.stderr)

    # 存在回归时以非 0 退出码提示（便于 CI 门禁使用）
    return 1 if (comparison is not None and comparison.has_regression) else 0


def _benchmark_table(result: BenchmarkResult, comparison: BaselineComparison | None) -> str:
    """将基准结果（及可选对比）格式化为表格字符串。"""
    lines = [
        f"基准测量: {result.root}",
        f"轮数 {result.rounds}（预热 {result.warmup}）| 文件 {result.scanned_files} | "
        f"单轮均值 {result.mean_duration_ms:.1f} ms",
        "",
    ]
    header = f"{'阶段':<24} {'均值(ms)':>10} {'最小':>9} {'最大':>9} {'标准差':>9} {'占比':>7}"
    lines.append(header)
    lines.append("-" * len(header))
    total_mean = sum(s.mean_ms for s in result.stages) or 1.0
    for s in result.stages:
        pct = s.mean_ms / total_mean * 100.0
        lines.append(
            f"{s.name:<24} {s.mean_ms:>10.2f} {s.min_ms:>9.2f} {s.max_ms:>9.2f} {s.stddev_ms:>9.2f} {pct:>6.1f}%"
        )

    if comparison is not None:
        lines.append("")
        lines.append(f"对比基准线（{comparison.baseline_timestamp}，回归阈值 {comparison.threshold * 100:.0f}%）:")
        cmp_header = f"{'阶段':<24} {'本次(ms)':>10} {'基准(ms)':>10} {'变化':>9} {'状态':>6}"
        lines.append(cmp_header)
        lines.append("-" * len(cmp_header))
        for d in comparison.deltas:
            cur = f"{d.current_ms:.2f}" if d.current_ms is not None else "-"
            base = f"{d.baseline_ms:.2f}" if d.baseline_ms is not None else "-"
            if d.change_ratio is None:
                change = "新增" if d.baseline_ms is None else "消失"
                status = ""
            else:
                change = f"{d.change_ratio * 100:+.1f}%"
                status = "回归" if d.regressed else "正常"
            lines.append(f"{d.name:<24} {cur:>10} {base:>10} {change:>9} {status:>6}")
        if comparison.has_regression:
            lines.append("")
            lines.append("检测到性能回归（见上表标记为“回归”的阶段）")

    return "\n".join(lines)


def _benchmark_json(result: BenchmarkResult, comparison: BaselineComparison | None) -> str:
    """将基准结果（及可选对比）格式化为 JSON 字符串。"""
    payload: dict[str, object] = {
        "root": result.root,
        "timestamp": result.timestamp,
        "rounds": result.rounds,
        "warmup": result.warmup,
        "scanned_files": result.scanned_files,
        "mean_duration_ms": round(result.mean_duration_ms, 3),
        "stages": [
            {
                "name": s.name,
                "mean_ms": s.mean_ms,
                "min_ms": s.min_ms,
                "max_ms": s.max_ms,
                "stddev_ms": s.stddev_ms,
                "samples": s.samples,
            }
            for s in result.stages
        ],
    }
    if comparison is not None:
        payload["comparison"] = {
            "baseline_timestamp": comparison.baseline_timestamp,
            "threshold": comparison.threshold,
            "has_regression": comparison.has_regression,
            "deltas": [
                {
                    "name": d.name,
                    "current_ms": d.current_ms,
                    "baseline_ms": d.baseline_ms,
                    "change_ratio": d.change_ratio,
                    "regressed": d.regressed,
                }
                for d in comparison.deltas
            ],
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _cmd_rules(args: argparse.Namespace) -> int:
    """执行 rules 子命令：校验规则文件。"""
    rules_path: Path = args.rules
    if not rules_path.exists():
        print(f"错误: 规则文件不存在: {rules_path}", file=sys.stderr)
        return 1

    ruleset = load_ruleset(rules_path)
    print(f"规则文件校验通过: {rules_path}")
    print(f"  版本: {ruleset.version}")
    print(f"  规则数: {len(ruleset.rules)}")
    print(f"  忽略路径: {', '.join(ruleset.ignore_paths) or '(无)'}")
    print("  规则列表:")
    for i, rule in enumerate(ruleset.rules, 1):
        print(f"    {i}. [{rule.severity.value}] {rule.name}")
        if rule.description:
            print(f"       {rule.description}")
    return 0


def _cmd_gui(_args: argparse.Namespace) -> int:
    """执行 gui 子命令：启动图形界面。"""
    try:
        # 仅在 gui 子命令时加载 PySide
        from fuscan.app import main as gui_main
    except ImportError as exc:
        print(f"GUI 启动失败（PySide 未安装）: {exc}", file=sys.stderr)
        return 3
    return gui_main()


def _cmd_bp(args: argparse.Namespace) -> int:  # noqa: PLR0912
    """执行 bp 子命令：验证并发扫描假卡死修复。

    在临时目录构造快慢混合文件，通过自定义 content_provider 注入 sleep 模拟
    大文件提取，验证 ``wait`` 超时分支能切换 ``current_file`` 到真实 in-flight
    慢文件而非陈旧快文件。退出码 0 表示验证通过，1 表示未通过。
    """
    import tempfile

    from fuscan.bench_progress import run_bench_progress

    if args.fast_files < 0 or args.slow_files < 0:
        print("错误: --fast-files/--slow-files 不能为负", file=sys.stderr)
        return 1
    if args.slow_files == 0:
        print("错误: --slow-files 至少为 1（无慢文件无法触发超时分支）", file=sys.stderr)
        return 1
    if args.workers < 1:
        print(f"错误: --workers 必须 >= 1（收到 {args.workers}）", file=sys.stderr)
        return 1
    if args.slow_duration <= 0:
        print(f"错误: --slow-duration 必须 > 0（收到 {args.slow_duration}）", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="bench_progress_") as tmp:
        result = run_bench_progress(
            work_dir=Path(tmp),
            fast_files=args.fast_files,
            slow_files=args.slow_files,
            slow_duration_s=args.slow_duration,
            workers=args.workers,
            progress_interval=args.progress_interval,
            on_output=print,
        )

    total = args.fast_files + args.slow_files
    print("\n" + "=" * 70)
    print(f"扫描完成：总耗时 {result.total_elapsed_s:.2f}s")
    print(f"扫描文件数：{result.scanned_files}，命中：{result.matched_files}")
    print(f"进度回调总数：{result.progress_emits}")
    print(f"超时分支触发次数（scanned 未变）：{result.timeout_emits}")
    print(f"current_file 为慢文件的次数：{result.slow_file_emits}")
    print(
        f"单文件最长 elapsed_ms：{result.max_elapsed_ms:.0f}ms @ "
        f"{Path(result.max_elapsed_file).name if result.max_elapsed_file else '-'}"
    )

    print("\n[慢文件进度时间线]（验证 elapsed_ms 持续增长）")
    if not result.slow_seen:
        print("  （无慢文件出现在 current_file）")
    for path, elapsed_list in result.slow_seen.items():
        name = Path(path).name
        if len(elapsed_list) <= 1:
            print(f"  {name}: elapsed_ms={elapsed_list}（仅出现一次，未观察到增长）")
            continue
        timeline = " -> ".join(f"{e:.0f}" for e in elapsed_list)
        print(f"  {name}: elapsed_ms 序列 {timeline}")

    print("\n[采样进度时间线]（每 ~0.3s 采样一行）")
    last_sample = -1.0
    for info in result.records:
        if info.elapsed - last_sample >= 0.3 or last_sample < 0:
            name = Path(info.current_file).name if info.current_file else "-"
            print(
                f"  t={info.elapsed:5.2f}s scanned={info.scanned:2d}/{total} "
                f"current={name:15s} elapsed_ms={info.current_file_elapsed_ms:6.0f}"
            )
            last_sample = info.elapsed

    print("\n[结论]")
    if result.timeout_emits == 0:
        print("  [FAIL] 超时分支未触发，无法验证假卡死修复")
    elif result.slow_file_emits == 0:
        print("  [FAIL] 超时分支触发了但 current_file 未切换到慢文件 → 修复无效")
    elif not result.passed:
        print("  [FAIL] 慢文件未出现 >= 2 次或 elapsed_ms 未递增 → 跟踪不稳定")
    else:
        print(f"  [PASS] 超时分支触发 {result.timeout_emits} 次，current_file 切换到真实 in-flight 慢文件")
        print("  [PASS] 慢文件 elapsed_ms 单调递增，证明在跟踪同一慢文件而非刷新陈旧快文件")
        print("  [PASS] 假卡死修复生效：用户能看到「正在扫描 slow_xx.txt」而非陈旧快文件")

    return 0 if result.passed else 1


def _merge_ignore_dirs(base_dirs: Sequence[str], extra_dirs: Sequence[str]) -> tuple[str, ...]:
    """合并全局忽略目录与命令行额外忽略目录（去重保序）。"""
    return tuple(dict.fromkeys((*base_dirs, *extra_dirs)))


def _resolve_cache_path(arg_path: Path | None, config_path: str | None) -> Path | None:
    """解析缓存数据库路径：命令行参数 > 配置文件 > 默认路径。"""
    if arg_path is not None:
        return arg_path
    if config_path:
        return Path(config_path)
    # 延迟加载避免无缓存场景的 SQLite 依赖
    from fuscan.cache import default_cache_path

    return default_cache_path()


def _resolve_max_file_size(arg_mb: int | None, ruleset_bytes: int | None) -> int | None:
    """解析大文件跳过阈值：CLI 参数（MB）优先 > 规则集（字节）> None（走 Scanner 默认）。

    :param arg_mb: ``--max-file-size`` 参数值（MB 单位），None 表示未指定
    :param ruleset_bytes: ``RuleSet.scan_params.max_file_size`` 字节值；None 表示未设置
    :return: 字节值；None 表示让 Scanner 走 ``_DEFAULT_MAX_FILE_SIZE``
    """
    if arg_mb is not None:
        return arg_mb * 1024 * 1024
    if ruleset_bytes is None:
        return None
    if ruleset_bytes > 0:
        return ruleset_bytes
    # 显式设为 0 表示不限制，传给 Scanner 让其判断 0 不限制
    return 0


def _cmd_cache(args: argparse.Namespace) -> int:
    """执行 cache 子命令：管理扫描结果缓存。"""
    # 仅在 cache 子命令时加载 SQLite 依赖
    from fuscan.cache import CacheStore, default_cache_path

    action: str = args.cache_action

    if action == "stats":
        cache_path = _resolve_cache_path(getattr(args, "cache_path", None), None) or default_cache_path()
        if not cache_path.exists():
            print("缓存数据库不存在，尚未扫描或缓存已清空")
            return 0
        cache = CacheStore(cache_path)
        try:
            stats = cache.stats()
        finally:
            cache.close()
        print(f"缓存数据库: {cache_path}")
        print(f"  schema 版本: {stats.schema_version}")
        print(f"  规则文件数: {stats.rule_files}")
        print(f"  规则数:     {stats.rules}")
        print(f"  已扫描文件: {stats.scanned_files}")
        print(f"  文件路径数: {stats.file_paths}")
        print(f"  缓存结果数: {stats.scan_results}")
        print(f"  数据库大小: {stats.db_bytes} 字节")
        return 0

    if action == "clear":
        cache_path = _resolve_cache_path(getattr(args, "cache_path", None), None) or default_cache_path()
        if not cache_path.exists():
            print("缓存数据库不存在，无需清理")
            return 0
        # 删除主数据库文件及 WAL/SHM 副文件
        for suffix in ("", "-wal", "-shm"):
            sidecar = cache_path.with_name(cache_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        print(f"已清空缓存: {cache_path}")
        return 0

    if action == "prune":
        cache_path = _resolve_cache_path(getattr(args, "cache_path", None), None) or default_cache_path()
        if not cache_path.exists():
            print("缓存数据库不存在，无需清理")
            return 0
        cache = CacheStore(cache_path)
        try:
            deleted = cache.prune_stale_files(args.max_age_days)
        finally:
            cache.close()
        print(f"已清理 {deleted} 条过期文件缓存（>={args.max_age_days} 天）")
        return 0

    print(f"未知缓存操作: {action}", file=sys.stderr)
    return 1  # pragma: no cover


def _print_summary(report: ScanReport) -> None:
    """输出简要摘要到 stderr（不干扰报告输出）。"""
    speed = report.stats.speed
    logger.info(
        "扫描完成: 总计 %d, 命中 %d, 耗时 %.2fs, 速度 %.0f 文件/s",
        report.stats.total_files,
        report.stats.matched_files,
        report.stats.duration_seconds,
        speed,
    )
    # 性能统计摘要（PerfStats 始终启用）
    perf = report.stats.perf_summary
    if perf:
        total_ms = sum(s.get("total_ms", 0.0) for s in perf.values()) or 1.0
        logger.info("性能统计:")
        for name, info in perf.items():
            pct = info.get("total_ms", 0.0) / total_ms * 100
            avg = info.get("total_ms", 0.0) / info.get("count", 1)
            logger.info(
                "  %-24s 总计 %8.1fms (%5.1f%%)  调用 %6d 次  平均 %7.2fms  最大 %8.1fms",
                name,
                info.get("total_ms", 0.0),
                pct,
                info.get("count", 0),
                avg,
                info.get("max_ms", 0.0),
            )


def _configure_logging(verbose: int) -> None:
    """根据 -v 计数配置日志级别。"""
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


if __name__ == "__main__":
    sys.exit(main())
