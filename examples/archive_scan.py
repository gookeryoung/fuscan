"""压缩包扫描示例：递归扫描 ZIP/RAR 压缩包内的文件。

演示 scan_archives 选项的用法：

1. 构造 Scanner 时启用 scan_archives
2. 可选指定 archive_password 解密加密压缩包
3. 扫描时自动递归进入 ZIP/RAR 内部条目

适用场景：

- 扫描邮件附件归档
- 检查备份压缩包是否包含敏感文件
- 审查代码依赖的第三方 jar/zip 包

注意：

- RAR 解压需要系统安装 unrar 命令行工具
- 加密压缩包需提供正确密码，否则跳过
- 大型压缩包解压耗时较长，建议配合增量扫描

运行：

    python examples/archive_scan.py /path/to/scan rules/example.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

from pyfilescan.rules import load_ruleset
from pyfilescan.scanner import Scanner


def main(scan_path: Path, rules_path: Path) -> int:
    ruleset = load_ruleset(rules_path)

    # 构造扫描器并启用压缩包扫描
    # archive_password：解密加密压缩包的密码（None 表示不尝试解密）
    scanner = Scanner(
        ruleset,
        scan_archives=True,
        archive_password=None,  # 如需解密：archive_password="your_password"
    )

    print(f"开始扫描（含压缩包）：{scan_path}")
    report = scanner.scan(scan_path)

    stats = report.stats
    print(
        f"\n统计：总计 {stats.total_files} | 已扫描 {stats.scanned_files} | "
        f"命中 {stats.matched_files} | 跳过 {stats.skipped_files} | "
        f"错误 {stats.errors} | 耗时 {stats.duration_seconds:.2f}s"
    )

    # 单独扫描某个压缩包（不扫描目录）
    # archive_path = Path("archive.zip")
    # if archive_path.exists():
    #     results = scanner.scan_archive(archive_path)
    #     for result in results:
    #         print(f"  压缩包内条目：{result.path}")

    if not report.hits:
        print("未发现命中项。")
        return 0

    print(f"\n命中项（{len(report.hits)} 条）：")
    for result in report.hits:
        print(f"  {result.path}")
        for hit in result.hits:
            print(f"    [{hit.severity.value}] {hit.rule_name}: {hit.detail}")

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"用法：python {sys.argv[0]} <扫描路径> <规则文件>")
        sys.exit(1)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
