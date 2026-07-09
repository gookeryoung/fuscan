"""增量扫描示例：仅扫描新增或修改的文件。

演示 IncrementalScanner 的用法，适用于：

- 托盘驻守场景下的持续监控
- 大型代码库的周期性扫描（避免每次全量扫描）
- CI 流水线中只扫描本次提交变更的文件

关键 API：

- IncrementalScanner.scan(root)：扫描目录，跳过 mtime 未变化的文件
- save_state(path) / load_state(path)：持久化扫描状态到 JSON
- scan_paths(paths)：扫描指定路径列表（由文件监控触发）

运行：

    python examples/incremental_scan.py /path/to/scan rules/example.yaml
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from pyfilescan.rules import load_ruleset
from pyfilescan.watcher import IncrementalScanner


def main(scan_path: Path, rules_path: Path) -> int:
    ruleset = load_ruleset(rules_path)
    state_file = Path("incremental_state.json")

    scanner = IncrementalScanner(ruleset)

    # 首次扫描：加载已有状态（如果存在）
    if state_file.exists():
        scanner.load_state(state_file)
        print(f"已加载状态：跟踪 {scanner.tracked_count} 个文件")
    else:
        print("首次扫描，无历史状态")

    # 第一次扫描（或增量扫描）
    print(f"\n第 1 次扫描：{scan_path}")
    report = scanner.scan(scan_path)
    print(
        f"  总计 {report.stats.total_files} | 已扫描 {report.stats.scanned_files} | "
        f"命中 {report.stats.matched_files} | 跳过 {report.stats.skipped_files} | "
        f"耗时 {report.stats.duration_seconds:.2f}s"
    )

    # 持久化状态
    scanner.save_state(state_file)
    print(f"  状态已保存：{state_file}（跟踪 {scanner.tracked_count} 个文件）")

    # 模拟文件变更：等待 1 秒后再次扫描
    print("\n等待 1 秒后再次扫描（演示增量跳过）...")
    time.sleep(1.0)

    report2 = scanner.scan(scan_path)
    print(
        f"第 2 次扫描：总计 {report2.stats.total_files} | "
        f"已扫描 {report2.stats.scanned_files} | "
        f"跳过 {report2.stats.skipped_files}（未变化文件）| "
        f"耗时 {report2.stats.duration_seconds:.2f}s"
    )

    # 增量扫描指定路径列表（由文件监控触发时使用）
    print("\n演示 scan_paths：扫描指定路径列表")
    specific_files = [scan_path / "example.txt"] if (scan_path / "example.txt").exists() else []
    if specific_files:
        report3 = scanner.scan_paths(specific_files)
        print(f"  扫描 {len(specific_files)} 个文件，命中 {report3.stats.matched_files} 个")
    else:
        print("  无指定文件可扫描")

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"用法：python {sys.argv[0]} <扫描路径> <规则文件>")
        sys.exit(1)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
