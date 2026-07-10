"""基础扫描示例：加载规则、扫描目录、遍历命中报告。

演示 uniscan 最常见的程序化用法：

1. 通过 load_ruleset 加载 YAML 规则
2. 构造 Scanner 并扫描目录
3. 遍历 ScanReport 中的命中项

运行：

    python examples/basic_scan.py /path/to/scan rules/example.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

from uniscan.rules import load_ruleset
from uniscan.scanner import Scanner


def main(scan_path: Path, rules_path: Path) -> int:
    """扫描指定路径并打印命中摘要。"""
    # 1. 加载规则集（YAML 文件）
    ruleset = load_ruleset(rules_path)
    print(f"已加载规则集：{rules_path}（共 {len(ruleset.rules)} 条规则）")

    # 2. 构造扫描器
    #    - max_depth：限制递归深度（None 为不限制）
    #    - scan_archives：是否扫描压缩包内条目
    scanner = Scanner(ruleset, max_depth=None)

    # 3. 执行扫描
    print(f"开始扫描：{scan_path}")
    report = scanner.scan(scan_path)

    # 4. 输出统计信息
    stats = report.stats
    print(
        f"\n统计：总计 {stats.total_files} | 已扫描 {stats.scanned_files} | "
        f"命中 {stats.matched_files} | 跳过 {stats.skipped_files} | "
        f"错误 {stats.errors} | 耗时 {stats.duration_seconds:.2f}s"
    )

    # 5. 遍历命中项
    if not report.hits:
        print("未发现命中项。")
        return 0

    print(f"\n命中项（{len(report.hits)} 条）：")
    for result in report.hits:
        try:
            rel = result.path.relative_to(scan_path)
        except ValueError:
            rel = result.path
        print(f"  {rel}")
        for hit in result.hits:
            print(f"    [{hit.severity.value}] {hit.rule_name}: {hit.detail}")

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"用法：python {sys.argv[0]} <扫描路径> <规则文件>")
        sys.exit(1)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
