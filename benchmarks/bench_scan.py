"""fuscan 扫描性能基准脚本。

生成可配置的混合格式测试文件集（纯文本 + 二进制），运行多场景扫描并测量
吞吐量（文件/秒、字节/秒）与缓存收益。

用法::

    uv run python benchmarks/bench_scan.py --files 1000 --workers 4
    uv run python benchmarks/bench_scan.py --output json --files 500
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# 确保项目根目录在 sys.path 中，使 benchmarks 包可导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.sample_files import generate_files  # noqa: E402
from fuscan.cache import CacheStore  # noqa: E402
from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity  # noqa: E402
from fuscan.scanner import Scanner  # noqa: E402

__all__ = ["ScenarioResult", "format_json", "format_table", "generate_files", "main", "run_scenario"]


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


def _total_bytes(paths: list[Path]) -> int:
    """汇总文件总字节数。"""
    return sum(p.stat().st_size for p in paths if p.exists())


@dataclass(frozen=True)
class ScenarioResult:
    """单场景测量结果。"""

    label: str
    files: int
    duration: float
    bytes_scanned: int
    cache_hits: int = 0
    cache_total: int = 0

    @property
    def files_per_sec(self) -> float:
        """文件/秒吞吐量。"""
        return self.files / self.duration if self.duration > 0 else 0.0

    @property
    def bytes_per_sec(self) -> float:
        """字节/秒吞吐量。"""
        return self.bytes_scanned / self.duration if self.duration > 0 else 0.0

    @property
    def hit_ratio(self) -> float:
        """缓存命中率（0.0-1.0）；无缓存场景为 0.0。"""
        return self.cache_hits / self.cache_total if self.cache_total > 0 else 0.0


def run_scenario(
    label: str,
    root: Path,
    total_bytes: int,
    scanner_factory: Callable[[], Scanner],
    cache: CacheStore | None = None,
    cache_before: int = 0,
) -> ScenarioResult:
    """执行单场景扫描，返回测量结果。

    :param label: 场景标签
    :param root: 扫描根目录
    :param total_bytes: 预期总字节数
    :param scanner_factory: 每次调用返回新 Scanner（避免状态污染）
    :param cache: 缓存实例（用于计算命中率）；无缓存场景传 None
    :param cache_before: 扫描前的 scan_results 行数；热缓存场景非 0，
        据此推算新增条数（未命中数）与命中率
    """
    scanner = scanner_factory()
    start = time.perf_counter()
    report = scanner.scan(root)
    duration = time.perf_counter() - start
    cache_after = cache.stats().scan_results if cache is not None else 0
    if cache is not None and cache_before > 0:
        # 热缓存：新增写入数 = 未命中数；命中数 = 已有条数 - 未命中数
        misses = max(0, cache_after - cache_before)
        cache_hits = max(0, cache_before - misses)
        cache_total = cache_before
    else:
        cache_hits = 0
        cache_total = 0
    return ScenarioResult(
        label=label,
        files=report.stats.scanned_files,
        duration=duration,
        bytes_scanned=total_bytes,
        cache_hits=cache_hits,
        cache_total=cache_total,
    )


def _format_bytes(n: int) -> str:
    """字节数格式化为人类可读字符串。"""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def format_table(results: list[ScenarioResult]) -> str:
    """格式化结果为表格字符串。"""
    header = f"{'场景':<22} {'文件数':>6} {'耗时(s)':>8} {'文件/秒':>10} {'字节/秒':>12} {'缓存命中率':>10}"
    sep = "-" * len(header)
    lines = [header, sep]
    for r in results:
        bps = _format_bytes(int(r.bytes_per_sec))
        hit = f"{r.hit_ratio * 100:.1f}%" if r.cache_total > 0 else "-"
        lines.append(f"{r.label:<22} {r.files:>6} {r.duration:>8.2f} {r.files_per_sec:>10.1f} {bps:>12} {hit:>10}")
    return "\n".join(lines)


def format_json(results: list[ScenarioResult]) -> str:
    """格式化结果为 JSON 字符串。"""
    return json.dumps(
        [
            {
                "label": r.label,
                "files": r.files,
                "duration": round(r.duration, 4),
                "files_per_sec": round(r.files_per_sec, 1),
                "bytes_per_sec": round(r.bytes_per_sec, 0),
                "cache_hit_ratio": round(r.hit_ratio, 4),
            }
            for r in results
        ],
        ensure_ascii=False,
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    """基准脚本入口。

    :param argv: 命令行参数；None 时用 sys.argv
    :return: 进程退出码
    """
    parser = argparse.ArgumentParser(description="fuscan 扫描性能基准")
    parser.add_argument("--files", type=int, default=1000, metavar="N", help="生成文件数（默认 1000）")
    parser.add_argument("--workers", type=int, default=4, metavar="N", help="并发场景线程数（默认 4）")
    parser.add_argument("--output", choices=("table", "json"), default="table", help="输出格式")
    parser.add_argument("--seed", type=int, default=42, metavar="N", help="随机种子（默认 42）")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "fuscan_bench",
        metavar="DIR",
        help="工作目录（默认系统临时目录，避免污染项目根目录影响类型检查）",
    )
    args = parser.parse_args(argv)

    cpu_count = os.cpu_count() or 4

    data_dir = args.workdir / "files"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    paths = generate_files(data_dir, args.files, args.seed)
    total_bytes = _total_bytes(paths)
    rs = _build_ruleset()
    workers = args.workers

    results: list[ScenarioResult] = []

    # S1 单线程无缓存
    results.append(
        run_scenario(
            "S1 单线程无缓存",
            data_dir,
            total_bytes,
            lambda: Scanner(rs, max_workers=1),
        )
    )

    # S2 多线程无缓存
    results.append(
        run_scenario(
            f"S2 {workers}线程无缓存",
            data_dir,
            total_bytes,
            lambda: Scanner(rs, max_workers=workers),
        )
    )

    # S3 cpu_count 线程无缓存
    results.append(
        run_scenario(
            f"S3 {cpu_count}线程无缓存",
            data_dir,
            total_bytes,
            lambda: Scanner(rs, max_workers=cpu_count),
        )
    )

    # S4 多线程 + 缓存首次（冷启动）
    cache_path = args.workdir / "cache.db"
    if cache_path.exists():
        cache_path.unlink()
    args.workdir.mkdir(parents=True, exist_ok=True)
    cache = CacheStore(cache_path)
    try:
        before = cache.stats().scan_results
        results.append(
            run_scenario(
                f"S4 {workers}线程+缓存冷",
                data_dir,
                total_bytes,
                lambda: Scanner(rs, max_workers=workers, cache=cache),
                cache=cache,
                cache_before=before,
            )
        )
        # S5 多线程 + 缓存二次（热缓存）
        before = cache.stats().scan_results
        results.append(
            run_scenario(
                f"S5 {workers}线程+缓存热",
                data_dir,
                total_bytes,
                lambda: Scanner(rs, max_workers=workers, cache=cache),
                cache=cache,
                cache_before=before,
            )
        )
    finally:
        cache.close()

    if args.output == "json":
        print(format_json(results))
    else:
        print(format_table(results))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
