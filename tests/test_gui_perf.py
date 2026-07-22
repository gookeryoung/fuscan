"""性能测量基础设施测试。

验证 ``fuscan.perf`` 的零开销开关、计时记录、事件记录与嵌套缩进，
以及 :class:`PerfStats` 聚合统计的线程安全与汇总输出。
"""

from __future__ import annotations

import logging
import threading
from typing import Iterator

import pytest

from fuscan import perf as perf_mod


@pytest.fixture(autouse=True)
def _restore_perf_state() -> Iterator[None]:
    """每个测试后恢复 PERF_ENABLED 默认值（False），避免相互污染。"""
    original = perf_mod._PerfState.enabled
    yield
    perf_mod._PerfState.enabled = original
    perf_mod._PerfState.depth = 0


def _collect_debug_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """过滤 fuscan.perf logger 的 DEBUG 记录。"""
    return [r for r in caplog.records if r.name == "fuscan.perf" and r.levelno == logging.DEBUG]


def test_perf_disabled_by_default_no_logging(caplog: pytest.LogCaptureFixture) -> None:
    """默认 PERF_ENABLED=False 时 PerfTimer 不应记录任何日志。"""
    perf_mod.set_perf_enabled(False)
    with perf_mod.PerfTimer("noop"):
        pass
    assert _collect_debug_records(caplog) == []


def test_perf_enabled_records_begin_and_end(caplog: pytest.LogCaptureFixture) -> None:
    """启用后 PerfTimer 应记录 begin 与 end 两条 DEBUG 日志。"""
    caplog.set_level(logging.DEBUG, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    with perf_mod.PerfTimer("stage_x"):
        pass
    records = _collect_debug_records(caplog)
    assert len(records) == 2
    assert "stage_x begin" in records[0].getMessage()
    assert "stage_x" in records[1].getMessage()
    assert "ms" in records[1].getMessage()


def test_perf_threshold_filters_short_durations(caplog: pytest.LogCaptureFixture) -> None:
    """threshold_ms 大于实际耗时应跳过 end 日志（仍记录 begin）。"""
    caplog.set_level(logging.DEBUG, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    with perf_mod.PerfTimer("fast_op", threshold_ms=10000.0):
        pass
    records = _collect_debug_records(caplog)
    # begin 始终记录，end 因耗时 < threshold_ms 被过滤
    assert len(records) == 1
    assert "fast_op begin" in records[0].getMessage()


def test_perf_nested_indent_levels(caplog: pytest.LogCaptureFixture) -> None:
    """嵌套 PerfTimer 应通过空格缩进表达层级关系。"""
    caplog.set_level(logging.DEBUG, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    with perf_mod.PerfTimer("outer"), perf_mod.PerfTimer("inner"):
        pass
    records = _collect_debug_records(caplog)
    # outer begin / inner begin / inner end / outer end 共 4 条
    assert len(records) == 4
    messages = [r.getMessage() for r in records]
    outer_begin = next(m for m in messages if "outer begin" in m)
    inner_begin = next(m for m in messages if "inner begin" in m)
    # outer indent="" 消息为 "[perf] > outer begin"
    assert outer_begin == "[perf] > outer begin"
    # inner indent="  " 消息为 "[perf]   > inner begin"（两个空格前缀）
    assert inner_begin == "[perf]   > inner begin"


def test_perf_record_event_disabled(caplog: pytest.LogCaptureFixture) -> None:
    """未启用时 record_event 不应记录任何日志。"""
    perf_mod.set_perf_enabled(False)
    perf_mod.record_event("evt", count=1)
    assert _collect_debug_records(caplog) == []


def test_perf_record_event_enabled_with_fields(caplog: pytest.LogCaptureFixture) -> None:
    """启用后 record_event 应记录事件名称与字段键值对。"""
    caplog.set_level(logging.DEBUG, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    perf_mod.record_event("scan_progress", files=100, matched=5)
    records = _collect_debug_records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert "scan_progress" in message
    assert "files=100" in message
    assert "matched=5" in message


def test_perf_set_perf_enabled_toggles_state() -> None:
    """set_perf_enabled 应切换 _PerfState.enabled 运行时状态。"""
    perf_mod.set_perf_enabled(True)
    assert perf_mod._PerfState.enabled is True
    perf_mod.set_perf_enabled(False)
    assert perf_mod._PerfState.enabled is False


def test_perf_stats_disabled_zero_overhead(caplog: pytest.LogCaptureFixture) -> None:
    """未启用时 PerfStats.measure 不应记录任何日志，report 不应输出。"""
    perf_mod.set_perf_enabled(False)
    stats = perf_mod.PerfStats()
    with stats.measure("noop"):
        pass
    stats.record("manual", 0.001)
    stats.report(logging.getLogger(__name__))
    assert _collect_debug_records(caplog) == []


def test_perf_stats_aggregates_multiple_measurements(caplog: pytest.LogCaptureFixture) -> None:
    """启用后 PerfStats 应累计多次 measure 的总耗时、调用次数与最大值。"""
    caplog.set_level(logging.DEBUG, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    stats = perf_mod.PerfStats()
    # 同一阶段调用 3 次
    for _ in range(3):
        with stats.measure("read_bytes"):
            pass
    # 直接 record 一次（模拟手动计时）
    stats.record("manual_stage", 0.002)
    stats.report(logging.getLogger("fuscan.perf"))
    records = _collect_debug_records(caplog)
    # 1 条标题 + 2 条阶段汇总（read_bytes、manual_stage）
    assert len(records) == 3
    report_text = "\n".join(r.getMessage() for r in records)
    assert "性能汇总" in report_text
    assert "read_bytes" in report_text
    assert "manual_stage" in report_text
    # read_bytes 调用 3 次
    read_bytes_line = next(r.getMessage() for r in records if "read_bytes" in r.getMessage())
    assert "调用      3 次" in read_bytes_line
    # manual_stage 调用 1 次
    manual_line = next(r.getMessage() for r in records if "manual_stage" in r.getMessage())
    assert "调用      1 次" in manual_line


def test_perf_stats_thread_safe_concurrent_measure(caplog: pytest.LogCaptureFixture) -> None:
    """多线程并发 measure 应正确累计次数，无丢失。"""
    caplog.set_level(logging.DEBUG, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    stats = perf_mod.PerfStats()
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        for _ in range(100):
            with stats.measure("concurrent"):
                pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stats.report(logging.getLogger("fuscan.perf"))
    records = _collect_debug_records(caplog)
    concurrent_line = next(r.getMessage() for r in records if "concurrent" in r.getMessage())
    # 8 线程 × 100 次 = 800 次
    assert "调用    800 次" in concurrent_line


def test_perf_stats_reset_clears_stages(caplog: pytest.LogCaptureFixture) -> None:
    """reset 应清空所有阶段统计，后续 report 无输出。"""
    caplog.set_level(logging.DEBUG, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    stats = perf_mod.PerfStats()
    with stats.measure("stage_a"):
        pass
    stats.reset()
    stats.report(logging.getLogger("fuscan.perf"))
    # reset 后无阶段数据，report 不输出
    assert _collect_debug_records(caplog) == []
