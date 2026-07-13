"""命令行入口。

支持子命令：

- ``scan``：扫描指定路径，输出命中报告
- ``rules``：校验规则文件格式
- ``version``：显示版本信息
- ``gui``：启动图形界面
- ``tray``：启动托盘驻守（监控新增文件并增量扫描）

- ``cache``：管理扫描结果缓存（stats/clear/prune）

用法示例：

.. code-block:: bash

    fuscan scan /path/to/scan -r rules/custom.yaml -o json -f report.json
    fuscan rules -r rules/custom.yaml
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from fuscan import __version__
from fuscan.builtin import load_with_builtin
from fuscan.config import load_config
from fuscan.rules import RuleError, RuleSet, load_ruleset, merge_multiple_rulesets
from fuscan.scanner import Scanner, ScanReport

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
        "-o", "--output-format", choices=["text", "json", "csv"], default="text", help="输出格式，默认 text"
    )
    scan_parser.add_argument("-f", "--output-file", type=Path, default=None, help="输出到文件（默认 stdout）")
    scan_parser.add_argument("--max-depth", type=int, default=None, help="最大递归深度")
    scan_parser.add_argument(
        "--ignore-dir", action="append", default=[], metavar="DIR", help="额外忽略目录名（可重复）"
    )
    scan_parser.add_argument("--no-builtin", action="store_true", help="禁用内置通用规则（需配合 -r 使用）")
    scan_parser.add_argument("--no-color", action="store_true", help="禁用彩色输出")
    scan_parser.add_argument("--no-cache", action="store_true", help="禁用扫描结果缓存")
    scan_parser.add_argument(
        "--cache-path", type=Path, default=None, metavar="DB", help="自定义缓存数据库路径（默认 ~/.fuscan/cache.db）"
    )

    # rules 子命令
    rules_parser = subparsers.add_parser("rules", help="校验规则文件")
    rules_parser.add_argument("-r", "--rules", type=Path, required=True, help="规则文件路径（YAML）")

    # gui 子命令
    subparsers.add_parser("gui", help="启动图形界面")

    # tray 子命令
    tray_parser = subparsers.add_parser("tray", help="启动托盘驻守（监控新增文件并增量扫描）")
    tray_parser.add_argument(
        "-r",
        "--rules",
        type=Path,
        action="append",
        default=None,
        metavar="FILE",
        help="规则文件路径（YAML，可重复指定多个，后面的覆盖前面的同名规则）",
    )
    tray_parser.add_argument("-w", "--watch", action="append", default=[], metavar="DIR", help="监控目录（可重复）")
    tray_parser.add_argument("--state", type=Path, default=None, help="扫描状态文件路径（用于增量扫描持久化）")
    tray_parser.add_argument("--no-builtin", action="store_true", help="禁用内置通用规则（需配合 -r 使用）")

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
        if args.command == "rules":
            return _cmd_rules(args)
        if args.command == "gui":
            return _cmd_gui(args)
        if args.command == "tray":
            return _cmd_tray(args)
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

    config = load_config()
    ignore_dirs = _merge_ignore_dirs(config.ignore_dirs, args.ignore_dir)

    use_cache = config.cache_enabled and not args.no_cache
    cache_path = _resolve_cache_path(args.cache_path, config.cache_path)

    if use_cache and cache_path is not None:
        # 仅在启用缓存时加载 SQLite 依赖
        from fuscan.cache import CacheStore, compute_source_files

        cache = CacheStore(cache_path)
        try:
            source_files = compute_source_files(args.rules or [], use_builtin=not args.no_builtin)
            scanner = Scanner(
                ruleset,
                max_depth=args.max_depth,
                ignore_dirs=ignore_dirs,
                ignore_extensions=tuple(config.ignore_extensions),
                cache=cache,
                source_files=source_files,
            )
            report = _run_scan(scanner, scan_path, args)
        finally:
            cache.close()
    else:
        scanner = Scanner(
            ruleset,
            max_depth=args.max_depth,
            ignore_dirs=ignore_dirs,
            ignore_extensions=tuple(config.ignore_extensions),
        )
        report = _run_scan(scanner, scan_path, args)

    output = _format_report(report, args.output_format)
    _write_output(output, args.output_file)
    _print_summary(report)
    return 0


def _run_scan(scanner: Scanner, scan_path: Path, args: argparse.Namespace) -> ScanReport:
    """执行扫描并记录日志。"""
    rules_desc = f"规则: {args.rules}" if args.rules else "内置通用规则"
    logger.info("开始扫描 %s（%s，规则数: %d）", scan_path, rules_desc, len(scanner.ruleset.rules))
    return scanner.scan(scan_path)


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
        exts = f" [扩展名: {', '.join(rule.file_extensions)}]" if rule.file_extensions else ""
        print(f"    {i}. [{rule.severity.value}] {rule.name}{exts}")
        if rule.description:
            print(f"       {rule.description}")
    return 0


def _cmd_gui(_args: argparse.Namespace) -> int:
    """执行 gui 子命令：启动图形界面。"""
    try:
        # 仅在 gui 子命令时加载 PySide
        from fuscan.gui import launch
    except ImportError as exc:
        print(f"GUI 启动失败（PySide 未安装）: {exc}", file=sys.stderr)
        return 3
    return launch()


def _cmd_tray(args: argparse.Namespace) -> int:
    """执行 tray 子命令：启动托盘驻守。"""
    try:
        try:
            from PySide2.QtWidgets import QApplication
        except ImportError:  # pragma: no cover
            from PySide6.QtWidgets import QApplication

        # 仅在 tray 子命令时加载 PySide 与 watchdog
        from fuscan.watcher.tray import TrayApp
    except ImportError as exc:
        print(f"托盘启动失败（PySide 未安装）: {exc}", file=sys.stderr)
        return 3

    ruleset = _load_ruleset_from_args(args)
    if ruleset is None:
        return 1

    config = load_config()
    watch_paths = [Path(w) for w in args.watch]
    state_file: Path | None = args.state

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    cache = None
    if config.cache_enabled:
        # 仅在启用缓存时加载 SQLite 依赖
        from fuscan.cache import CacheStore

        cache_path = _resolve_cache_path(None, config.cache_path)
        if cache_path is not None:
            cache = CacheStore(cache_path)

    tray = TrayApp(
        ruleset=ruleset,
        watch_paths=watch_paths,
        state_file=state_file,
        ignore_dirs=config.ignore_dirs,
        ignore_extensions=config.ignore_extensions,
        cache=cache,
    )
    return tray.start(show_window=False)


def _merge_ignore_dirs(base_dirs: list[str], extra_dirs: list[str]) -> tuple[str, ...]:
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


def _format_report(report: ScanReport, fmt: str) -> str:
    """按指定格式渲染扫描报告。"""
    if fmt == "json":
        return _format_json(report)
    if fmt == "csv":
        return _format_csv(report)
    return _format_text(report)


def _format_text(report: ScanReport) -> str:
    """渲染文本格式报告。"""
    lines: list[str] = []
    lines.append(f"扫描路径: {report.root}")
    lines.append(
        f"统计: 总计 {report.stats.total_files} | 已扫描 {report.stats.scanned_files} | "
        f"命中 {report.stats.matched_files} | 条数 {report.stats.total_matches} | "
        f"跳过 {report.stats.skipped_files} | "
        f"错误 {report.stats.errors} | 耗时 {report.stats.duration_seconds:.2f}s"
    )
    lines.append("")

    if not report.hits:
        lines.append("未发现命中项。")
        return "\n".join(lines)

    lines.append(f"命中项 ({len(report.hits)}):")
    for result in report.hits:
        try:
            rel = result.path.relative_to(report.root)
        except ValueError:
            rel = result.path
        lines.append(f"  {rel} (规则 {len(result.hits)} / 条数 {result.total_match_count})")
        for hit in result.hits:
            lines.append(f"    [{hit.severity.value}] {hit.rule_name} (条数 {hit.match_count}): {hit.detail}")
    return "\n".join(lines)


def _format_json(report: ScanReport) -> str:
    """渲染 JSON 格式报告。"""
    data = {
        "root": str(report.root),
        "stats": asdict(report.stats),
        "hits": [
            {
                "path": str(result.path),
                "size": result.size,
                "max_severity": result.max_severity.value,
                "match_count": result.total_match_count,
                "rules": [asdict(hit) for hit in result.hits],
            }
            for result in report.hits
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def _format_csv(report: ScanReport) -> str:
    """渲染 CSV 格式报告。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["path", "size", "severity", "rule", "match_count", "detail"])
    for result in report.hits:
        for hit in result.hits:
            writer.writerow(
                [
                    str(result.path),
                    result.size,
                    hit.severity.value,
                    hit.rule_name,
                    hit.match_count,
                    hit.detail,
                ]
            )
    return buf.getvalue()


def _write_output(content: str, output_file: Path | None) -> None:
    """输出报告到文件或 stdout。"""
    if output_file is None:
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        return
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content, encoding="utf-8")


def _print_summary(report: ScanReport) -> None:
    """输出简要摘要到 stderr（不干扰报告输出）。"""
    logger.info(
        "扫描完成: 总计 %d, 命中 %d, 耗时 %.2fs",
        report.stats.total_files,
        report.stats.matched_files,
        report.stats.duration_seconds,
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
