"""命令行入口。

支持子命令：

- ``scan``：扫描指定路径，输出命中报告
- ``rules``：校验规则文件格式
- ``version``：显示版本信息
- ``gui``：启动图形界面（P3 阶段实现，当前占位）

用法示例：

.. code-block:: bash

    pyfilescan scan /path/to/scan -r rules/custom.yaml -o json -f report.json
    pyfilescan rules -r rules/custom.yaml
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
from typing import List, Optional, Sequence

from pyfilescan import __version__
from pyfilescan.rules import RuleError, RuleSet, load_ruleset
from pyfilescan.scanner import Scanner, ScanReport

__all__ = ["build_parser", "main"]

logger = logging.getLogger("pyfilescan")


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="pyfilescan",
        description="通用文件扫描器：基于 YAML 规则的多格式内容扫描工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"pyfilescan {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="增加日志详细度（-v INFO, -vv DEBUG）")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    # scan 子命令
    scan_parser = subparsers.add_parser("scan", help="扫描指定路径")
    scan_parser.add_argument("path", type=Path, help="要扫描的目录或文件路径")
    scan_parser.add_argument("-r", "--rules", type=Path, required=True, help="规则文件路径（YAML）")
    scan_parser.add_argument(
        "-o", "--output-format", choices=["text", "json", "csv"], default="text", help="输出格式，默认 text"
    )
    scan_parser.add_argument("-f", "--output-file", type=Path, default=None, help="输出到文件（默认 stdout）")
    scan_parser.add_argument("--max-depth", type=int, default=None, help="最大递归深度")
    scan_parser.add_argument(
        "--ignore-dir", action="append", default=[], metavar="DIR", help="额外忽略目录名（可重复）"
    )
    scan_parser.add_argument("--no-color", action="store_true", help="禁用彩色输出")

    # rules 子命令
    rules_parser = subparsers.add_parser("rules", help="校验规则文件")
    rules_parser.add_argument("-r", "--rules", type=Path, required=True, help="规则文件路径（YAML）")

    # gui 子命令（P3 占位）
    subparsers.add_parser("gui", help="启动图形界面（P3 阶段实现）")

    # version 子命令
    subparsers.add_parser("version", help="显示版本信息")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
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
        if args.command == "version":
            print(f"pyfilescan {__version__}")
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

    parser.print_help()
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    """执行 scan 子命令。"""
    scan_path: Path = args.path
    rules_path: Path = args.rules

    if not scan_path.exists():
        print(f"错误: 扫描路径不存在: {scan_path}", file=sys.stderr)
        return 1
    if not rules_path.exists():
        print(f"错误: 规则文件不存在: {rules_path}", file=sys.stderr)
        return 1

    ruleset = load_ruleset(rules_path)
    if args.ignore_dir:
        ruleset = _merge_ignore_dirs(ruleset, args.ignore_dir)

    scanner = Scanner(ruleset, max_depth=args.max_depth)
    logger.info("开始扫描 %s（规则: %s，规则数: %d）", scan_path, rules_path, len(ruleset.rules))
    report = scanner.scan(scan_path)

    output = _format_report(report, args.output_format)
    _write_output(output, args.output_file)

    _print_summary(report)
    return 0


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
    print(f"  忽略目录: {', '.join(ruleset.ignore_dirs) or '(无)'}")
    print(f"  忽略扩展名: {', '.join(ruleset.ignore_extensions) or '(无)'}")
    print("  规则列表:")
    for i, rule in enumerate(ruleset.rules, 1):
        exts = f" [扩展名: {', '.join(rule.file_extensions)}]" if rule.file_extensions else ""
        print(f"    {i}. [{rule.severity.value}] {rule.name}{exts}")
        if rule.description:
            print(f"       {rule.description}")
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    """执行 gui 子命令（P3 阶段实现）。"""
    print("GUI 界面将在 P3 阶段实现", file=sys.stderr)
    return 0


def _merge_ignore_dirs(ruleset: RuleSet, extra_dirs: List[str]) -> RuleSet:
    """合并额外忽略目录到规则集。"""
    from dataclasses import replace

    merged = tuple(dict.fromkeys((*ruleset.ignore_dirs, *extra_dirs)))
    return replace(ruleset, ignore_dirs=merged)


def _format_report(report: ScanReport, fmt: str) -> str:
    """按指定格式渲染扫描报告。"""
    if fmt == "json":
        return _format_json(report)
    if fmt == "csv":
        return _format_csv(report)
    return _format_text(report)


def _format_text(report: ScanReport) -> str:
    """渲染文本格式报告。"""
    lines: List[str] = []
    lines.append(f"扫描路径: {report.root}")
    lines.append(
        f"统计: 总计 {report.stats.total_files} | 已扫描 {report.stats.scanned_files} | "
        f"命中 {report.stats.matched_files} | 跳过 {report.stats.skipped_files} | "
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
        lines.append(f"  {rel}")
        for hit in result.hits:
            lines.append(f"    [{hit.severity.value}] {hit.rule_name}: {hit.detail}")
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
    writer.writerow(["path", "size", "severity", "rule", "detail"])
    for result in report.hits:
        for hit in result.hits:
            writer.writerow([str(result.path), result.size, hit.severity.value, hit.rule_name, hit.detail])
    return buf.getvalue()


def _write_output(content: str, output_file: Optional[Path]) -> None:
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
