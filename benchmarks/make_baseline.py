"""生成官方性能基线：造数 → 多轮 benchmark → 写 baselines。

固定 ``seed`` 生成可复现的混合格式数据集，运行多轮扫描聚合各阶段耗时，
将结果写入 ``benchmarks/baselines/scan_stage_baseline.json``。基线 JSON 的
``meta`` 额外记录文件数/seed/Python 版本/平台信息，便于跨机器/跨版本核对
基线是否在同等条件下产出。

**冷扫描-文档为主** 场景（默认）：每轮使用全新空缓存执行一次冷扫描，使
``read_bytes`` / ``hash`` / ``extract`` / ``cache_put_extract`` 各阶段耗时
被独立埋点捕获（这些埋点仅存在于缓存提取路径 :func:`extract_with_cache`）。
无缓存路径下提取被懒加载折叠进 ``match`` 阶段，无法单独度量提取开销，
故基线固定走冷缓存路径以给 R2（extract）/R5（read_bytes/hash）提供对比锚点。

微基准基线（``perf_micro_baseline.json``）由 pytest-benchmark 单独生成：
    uv run pytest -m slow tests/test_perf_benchmark.py --benchmark-save=perf_micro
再从 ``.benchmarks/`` 拷贝稳定副本到 ``benchmarks/baselines/``。

用法::

    uv run python benchmarks/make_baseline.py
    uv run python benchmarks/make_baseline.py --files 500 --rounds 5 --warmup 1
"""

from __future__ import annotations

import argparse
import platform
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

# 确保项目根目录在 sys.path 中，使 benchmarks 包可导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.sample_files import generate_files  # noqa: E402
from fuscan.benchmark import BenchmarkResult, _aggregate_stage, save_baseline  # noqa: E402
from fuscan.cache import CacheStore  # noqa: E402
from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity  # noqa: E402
from fuscan.scanner import Scanner  # noqa: E402

__all__ = [
    "DEFAULT_FILES",
    "DEFAULT_ROUNDS",
    "DEFAULT_SEED",
    "DEFAULT_WARMUP",
    "build_ruleset",
    "main",
    "make_env_meta",
    "run_cold_cache_benchmark",
]

# 基线默认参数（与计划一致：固定 seed、warmup≥1、多轮取均值降低方差）
DEFAULT_FILES: int = 1000
DEFAULT_ROUNDS: int = 5
DEFAULT_WARMUP: int = 1
DEFAULT_SEED: int = 42

# 官方基线输出路径（相对项目根）
_BASELINE_DIR = _PROJECT_ROOT / "benchmarks" / "baselines"
_STAGE_BASELINE = _BASELINE_DIR / "scan_stage_baseline.json"


def build_ruleset() -> RuleSet:
    """构建基线规则集：1 个 filename 规则 + 2 个 content 规则。

    与 :mod:`benchmarks.bench_scan` / :mod:`benchmarks.perf_profile` 保持
    一致，确保阶段耗时可跨脚本横向对比。
    """
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


def make_env_meta(files: int, seed: int) -> dict[str, object]:
    """组装基线环境元信息，供跨机器/跨版本核对基线可比性。

    :param files: 生成的固定数据集文件数
    :param seed: 生成数据集的随机种子（可复现）
    :return: 环境元信息字典（文件数/seed/Python 版本/平台）
    """
    return {
        "files": files,
        "seed": seed,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def run_cold_cache_benchmark(
    ruleset: RuleSet,
    root: Path,
    cache_dir: Path,
    *,
    rounds: int,
    warmup: int,
    on_round: Callable[[int, int, str], None] | None = None,
) -> BenchmarkResult:
    """冷缓存多轮基准：每轮使用全新空缓存执行一次冷扫描。

    与 :func:`fuscan.benchmark.run_benchmark` 的差异：每轮重建 :class:`CacheStore`
    （删旧建新），确保提取路径 :func:`extract_with_cache` 走「未命中→提取」分支，
    使 ``read_bytes`` / ``hash`` / ``extract`` / ``cache_put_extract`` 各阶段被
    独立埋点捕获，反映冷扫描-文档为主场景的提取开销。

    :param ruleset: 基准规则集
    :param root: 扫描根路径
    :param cache_dir: 缓存文件所在目录（每轮在此目录内重建 cache.db）
    :param rounds: 正式测量轮数（至少 1）
    :param warmup: 预热轮数（不计入统计，至少 0）
    :param on_round: 每轮开始前回调 ``(序号从1起, 总轮数, "预热"/"测量")``
    :return: 聚合后的基准测量结果（各阶段按均值降序）
    :raises ValueError: ``rounds < 1`` 或 ``warmup < 0``
    """
    if rounds < 1:
        raise ValueError(f"rounds 必须 >= 1，收到 {rounds}")
    if warmup < 0:
        raise ValueError(f"warmup 必须 >= 0，收到 {warmup}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    total = warmup + rounds
    per_stage: dict[str, list[float]] = {}
    durations_ms: list[float] = []
    scanned_files = 0

    for i in range(total):
        is_warmup = i < warmup
        if on_round is not None:
            on_round(i + 1, total, "预热" if is_warmup else "测量")
        cache_path = cache_dir / "cache.db"
        if cache_path.exists():
            cache_path.unlink()
        cache = CacheStore(cache_path)
        try:
            scanner = Scanner(ruleset, max_workers=4, cache=cache)
            start = time.perf_counter()
            report = scanner.scan(root)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
        finally:
            cache.close()
        if is_warmup:
            continue
        durations_ms.append(elapsed_ms)
        scanned_files = report.stats.scanned_files
        perf = report.stats.perf_summary or {}
        for name, info in perf.items():
            per_stage.setdefault(name, []).append(info.get("total_ms", 0.0))

    stages = tuple(
        sorted(
            (_aggregate_stage(name, totals) for name, totals in per_stage.items()),
            key=lambda s: -s.mean_ms,
        )
    )
    mean_duration = sum(durations_ms) / len(durations_ms) if durations_ms else 0.0
    return BenchmarkResult(
        stages=stages,
        rounds=rounds,
        warmup=warmup,
        scanned_files=scanned_files,
        mean_duration_ms=mean_duration,
        root=str(root),
    )


def _merge_env_meta(baseline_path: Path, env_meta: dict[str, object]) -> None:
    """将环境元信息合并进已保存的基线 JSON 的 ``meta`` 字段。

    :func:`fuscan.benchmark.save_baseline` 只写 ``rounds/warmup/scanned_files``
    等测量元信息，此处补充造数环境信息，写回同一文件。
    """
    import json

    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    meta = data.get("meta")
    if isinstance(meta, dict):
        meta.update(env_meta)
    else:
        data["meta"] = env_meta
    baseline_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - 脚本入口，实机造数+扫描
    """基线生成脚本入口。

    :param argv: 命令行参数；None 时用 sys.argv
    :return: 进程退出码
    """
    parser = argparse.ArgumentParser(description="生成 fuscan 官方性能基线")
    parser.add_argument(
        "--files", type=int, default=DEFAULT_FILES, metavar="N", help=f"生成文件数（默认 {DEFAULT_FILES}）"
    )
    parser.add_argument(
        "--rounds", type=int, default=DEFAULT_ROUNDS, metavar="N", help=f"正式测量轮数（默认 {DEFAULT_ROUNDS}）"
    )
    parser.add_argument(
        "--warmup", type=int, default=DEFAULT_WARMUP, metavar="N", help=f"预热轮数（默认 {DEFAULT_WARMUP}）"
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, metavar="N", help=f"随机种子（默认 {DEFAULT_SEED}）")
    parser.add_argument(
        "--output",
        type=Path,
        default=_STAGE_BASELINE,
        metavar="FILE",
        help="基线 JSON 输出路径（默认 benchmarks/baselines/scan_stage_baseline.json）",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "fuscan_baseline",
        metavar="DIR",
        help="造数工作目录（默认系统临时目录）",
    )
    args = parser.parse_args(argv)

    data_dir = args.workdir / "files"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    paths = generate_files(data_dir, args.files, args.seed)
    print(f"已生成 {len(paths)} 个测试文件到 {data_dir}（seed={args.seed}）")

    ruleset = build_ruleset()

    def on_round(idx: int, total: int, label: str) -> None:
        print(f"基线 {label} 第 {idx}/{total} 轮（冷缓存）")

    result = run_cold_cache_benchmark(
        ruleset,
        data_dir,
        args.workdir / "cache",
        rounds=args.rounds,
        warmup=args.warmup,
        on_round=on_round,
    )
    save_baseline(result, args.output)
    _merge_env_meta(args.output, make_env_meta(args.files, args.seed))
    print(f"阶段级基线已保存到: {args.output}")
    print(f"端到端均值耗时: {result.mean_duration_ms:.2f}ms，扫描文件数: {result.scanned_files}")
    for stage in result.stages:
        print(f"  {stage.name:<22} 均值 {stage.mean_ms:.2f}ms")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
