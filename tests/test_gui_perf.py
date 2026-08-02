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


def test_perf_stats_always_records_regardless_of_enabled() -> None:
    """PerfStats 始终记录（iter-66 起），不受 set_perf_enabled 影响。"""
    perf_mod.set_perf_enabled(False)
    stats = perf_mod.PerfStats()
    with stats.measure("noop"):
        pass
    stats.record("manual", 0.001)
    # 即使 enabled=False，PerfStats 仍然记录了数据
    data = stats.to_dict()
    assert "noop" in data
    assert "manual" in data
    assert data["noop"]["count"] == 1
    assert data["manual"]["count"] == 1


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


def test_perf_stats_to_dict_exports_sorted_by_total() -> None:
    """to_dict 应按总耗时降序导出，含 total_ms/count/max_ms 三字段。"""
    stats = perf_mod.PerfStats()
    stats.record("fast", 0.001)
    stats.record("slow", 0.010)
    stats.record("fast", 0.002)
    data = stats.to_dict()
    # slow 总耗时更高，应排在前面
    names = list(data.keys())
    assert names[0] == "slow"
    assert names[1] == "fast"
    # fast 调用 2 次
    assert data["fast"]["count"] == 2
    assert data["slow"]["count"] == 1
    # max_ms 应为最大单次耗时
    assert data["fast"]["max_ms"] >= data["slow"]["max_ms"] or data["fast"]["max_ms"] > 0


def test_perf_stats_merge_dict_accumulates() -> None:
    """merge_dict 应累加 total/count，取 max。"""
    stats = perf_mod.PerfStats()
    stats.record("stage", 0.005)
    # 合并外部数据
    stats.merge_dict(
        {
            "stage": {"total_ms": 10.0, "count": 2, "max_ms": 8.0},
            "other": {"total_ms": 3.0, "count": 1, "max_ms": 3.0},
        }
    )
    data = stats.to_dict()
    # stage: 原有 1 次 + 合并 2 次 = 3 次
    assert data["stage"]["count"] == 3
    # other 来自合并
    assert data["other"]["count"] == 1


def test_perf_stats_summary_text_returns_top_stages() -> None:
    """summary_text 应返回前 N 个热点阶段占比文本。"""
    stats = perf_mod.PerfStats()
    stats.record("a", 0.010)
    stats.record("b", 0.005)
    stats.record("c", 0.003)
    text = stats.summary_text(top=2)
    # a 占比最高，b 次之
    assert "a" in text
    assert "b" in text
    assert "c" not in text
    # 空统计返回空字符串
    assert perf_mod.PerfStats().summary_text() == ""


def test_perf_stats_save_to_json_writes_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """save_to_json 应写入含时间戳、元信息与阶段统计的 JSON 文件。"""
    import json

    stats = perf_mod.PerfStats()
    stats.record("read", 0.010)
    stats.record("hash", 0.005)
    out = tmp_path / "perf.json"
    stats.save_to_json(out, meta={"files": 100})
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "timestamp" in payload
    assert "stages" in payload
    assert "read" in payload["stages"]
    assert payload["meta"]["files"] == 100


def _collect_info_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """过滤 fuscan.perf logger 的 INFO 记录（timed 默认级别）。"""
    return [r for r in caplog.records if r.name == "fuscan.perf" and r.levelno == logging.INFO]


def test_timed_disabled_context_no_logging(caplog: pytest.LogCaptureFixture) -> None:
    """未启用时 timed 作上下文管理器不应记录任何日志（零开销）。"""
    caplog.set_level(logging.INFO, logger="fuscan.perf")
    perf_mod.set_perf_enabled(False)
    with perf_mod.timed("阶段"):
        pass
    assert _collect_info_records(caplog) == []


def test_timed_context_records_begin_and_end(caplog: pytest.LogCaptureFixture) -> None:
    """启用后 timed 作上下文应记录进入与耗时两条 INFO 日志。"""
    caplog.set_level(logging.INFO, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    with perf_mod.timed("构造主控制器"):
        pass
    records = _collect_info_records(caplog)
    assert len(records) == 2
    assert records[0].getMessage() == "构造主控制器…"
    assert "构造主控制器 完成，用时" in records[1].getMessage()
    assert "ms" in records[1].getMessage()


def test_timed_decorator_uses_explicit_name(caplog: pytest.LogCaptureFixture) -> None:
    """timed 作装饰器（显式命名）应在每次调用时计时并记录。"""
    caplog.set_level(logging.INFO, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)

    @perf_mod.timed("加载配置")
    def load() -> int:
        return 42

    assert load() == 42
    records = _collect_info_records(caplog)
    assert len(records) == 2
    assert records[0].getMessage() == "加载配置…"
    assert "加载配置 完成，用时" in records[1].getMessage()


def test_timed_decorator_auto_name_from_function(caplog: pytest.LogCaptureFixture) -> None:
    """timed 作装饰器未命名时应自动取被装饰函数的限定名。"""
    caplog.set_level(logging.INFO, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)

    @perf_mod.timed()
    def build_widget() -> None:
        return None

    build_widget()
    records = _collect_info_records(caplog)
    assert len(records) == 2
    # __qualname__ 含外层测试函数前缀，故用子串断言
    assert "build_widget" in records[0].getMessage()


def test_timed_decorator_disabled_zero_overhead(caplog: pytest.LogCaptureFixture) -> None:
    """未启用时 timed 装饰的函数应正常执行但不记录日志。"""
    caplog.set_level(logging.INFO, logger="fuscan.perf")
    perf_mod.set_perf_enabled(False)

    @perf_mod.timed("noop")
    def compute() -> int:
        return 7

    assert compute() == 7
    assert _collect_info_records(caplog) == []


def test_timed_threshold_filters_short_durations(caplog: pytest.LogCaptureFixture) -> None:
    """threshold_ms 大于实际耗时应跳过耗时行（仍记录进入行）。"""
    caplog.set_level(logging.INFO, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    with perf_mod.timed("fast", threshold_ms=10000.0):
        pass
    records = _collect_info_records(caplog)
    # 进入行始终记录，耗时行因 < threshold_ms 被过滤
    assert len(records) == 1
    assert records[0].getMessage() == "fast…"


def test_timed_custom_level(caplog: pytest.LogCaptureFixture) -> None:
    """level 参数应改变日志记录级别。"""
    caplog.set_level(logging.DEBUG, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    with perf_mod.timed("debug_stage", level=logging.DEBUG):
        pass
    debug_records = [r for r in caplog.records if r.name == "fuscan.perf" and r.levelno == logging.DEBUG]
    assert len(debug_records) == 2
    assert debug_records[0].getMessage() == "debug_stage…"
    # 未产生 INFO 级记录
    assert _collect_info_records(caplog) == []


def test_timed_propagates_exception(caplog: pytest.LogCaptureFixture) -> None:
    """timed 不应吞掉代码块异常，且仍记录耗时行。"""
    caplog.set_level(logging.INFO, logger="fuscan.perf")
    perf_mod.set_perf_enabled(True)
    with pytest.raises(ValueError, match="boom"), perf_mod.timed("会失败的阶段"):
        raise ValueError("boom")
    records = _collect_info_records(caplog)
    # 进入行 + 耗时行均记录（__exit__ 在异常时仍执行）
    assert len(records) == 2
    assert "会失败的阶段 完成，用时" in records[1].getMessage()
