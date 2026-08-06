"""``benchmarks/make_baseline.py`` 的单元测试（非 slow）。

只测纯函数与元信息组装/合并逻辑（规则集构建、环境元信息、meta 合并回写），
不执行实际造数与扫描（``main()`` 已标 ``# pragma: no cover``），以保覆盖率
且不拖慢功能门禁。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.make_baseline import build_ruleset, make_env_meta, run_cold_cache_benchmark
from fuscan.rules.model import MatchTarget, RuleSet


def test_build_ruleset_returns_expected_rules() -> None:
    """规则集含 1 个 filename 规则 + 2 个 content 规则，与其他基准脚本一致。"""
    ruleset = build_ruleset()
    assert isinstance(ruleset, RuleSet)
    assert len(ruleset.rules) == 3
    targets = [r.match.target for r in ruleset.rules]  # type: ignore[union-attr]
    assert targets.count(MatchTarget.FILENAME) == 1
    assert targets.count(MatchTarget.CONTENT) == 2


def test_make_env_meta_records_files_seed_and_environment() -> None:
    """环境元信息包含文件数/seed/Python 版本/平台等可比性字段。"""
    meta = make_env_meta(files=123, seed=7)
    assert meta["files"] == 123
    assert meta["seed"] == 7
    assert isinstance(meta["python"], str) and meta["python"]
    assert isinstance(meta["platform"], str) and meta["platform"]
    assert isinstance(meta["machine"], str)


def test_merge_env_meta_updates_existing_meta(tmp_path: Path) -> None:
    """合并环境元信息保留原有 meta 键，并补充造数字段。"""
    from benchmarks.make_baseline import _merge_env_meta

    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"timestamp": "2026-01-01T00:00:00", "stages": {}, "meta": {"rounds": 5}}),
        encoding="utf-8",
    )
    _merge_env_meta(baseline, make_env_meta(files=50, seed=42))

    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["meta"]["rounds"] == 5  # 原有键保留
    assert data["meta"]["files"] == 50  # 新增造数字段
    assert data["meta"]["seed"] == 42


def test_merge_env_meta_creates_meta_when_absent(tmp_path: Path) -> None:
    """基线无 meta 字段（或类型异常）时，合并直接以环境元信息建 meta。"""
    from benchmarks.make_baseline import _merge_env_meta

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"timestamp": "t", "stages": {}}), encoding="utf-8")
    _merge_env_meta(baseline, make_env_meta(files=10, seed=1))

    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["meta"]["files"] == 10
    assert data["meta"]["seed"] == 1


def test_run_cold_cache_benchmark_rejects_invalid_rounds(tmp_path: Path) -> None:
    """rounds < 1 时抛 ValueError（校验先于任何造数/扫描）。"""
    with pytest.raises(ValueError, match="rounds"):
        run_cold_cache_benchmark(build_ruleset(), tmp_path, tmp_path / "cache", rounds=0, warmup=1)


def test_run_cold_cache_benchmark_rejects_negative_warmup(tmp_path: Path) -> None:
    """warmup < 0 时抛 ValueError（校验先于任何造数/扫描）。"""
    with pytest.raises(ValueError, match="warmup"):
        run_cold_cache_benchmark(build_ruleset(), tmp_path, tmp_path / "cache", rounds=1, warmup=-1)
