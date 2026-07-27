"""``fuscan.scanner._cache_phase`` 子模块单元测试（iter-109）。

覆盖 :class:`BatchBuffer`、:func:`build_hits_from_cache`、
:func:`extract_with_cache` 的纯逻辑路径，与 :mod:`tests/test_scanner.py` 中
通过 :class:`Scanner` 公共接口的集成测试形成互补。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fuscan.cache.hashes import hash_bytes
from fuscan.cache.store import BatchWriteItem
from fuscan.perf import PerfStats
from fuscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    Severity,
)
from fuscan.scanner._cache_phase import (
    BatchBuffer,
    build_hits_from_cache,
    extract_with_cache,
)
from fuscan.scanner.context import FileEntry
from fuscan.scanner.matchers import build_matcher
from fuscan.scanner.result import RuleHit


def _content_rule(name: str, pattern: str) -> Rule:
    """构造 CONTENT 目标的 contains 规则。"""
    return Rule(
        name=name,
        severity=Severity.WARNING,
        match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern=pattern),
    )


def _make_entry(tmp_path: Path, name: str, content: bytes) -> FileEntry:
    """在 tmp_path 下创建文件并返回 FileEntry。"""
    path = tmp_path / name
    path.write_bytes(content)
    return FileEntry.from_path(path)


class TestBatchBuffer:
    """BatchBuffer 累积与 flush 行为测试。"""

    def test_add_below_threshold_does_not_flush(self, tmp_path: Path) -> None:
        """累积项数低于阈值时不应触发 flush。"""
        cache = MagicMock()
        perf = PerfStats()
        buf = BatchBuffer(cache, perf)
        # BATCH_THRESHOLD=50，添加 10 项不应 flush
        for i in range(10):
            buf.add(BatchWriteItem(file_hash=f"hash{i}", size=10, path=Path(f"/x/{i}"), mtime=0, hits=()))
        cache.batch_put_results.assert_not_called()
        assert not buf.is_empty

    def test_add_at_threshold_triggers_flush(self, tmp_path: Path) -> None:
        """累积达到阈值时应自动 flush 并清空缓冲。"""
        cache = MagicMock()
        perf = PerfStats()
        buf = BatchBuffer(cache, perf)
        # 添加 50 项触发 1 次 flush
        for i in range(50):
            buf.add(BatchWriteItem(file_hash=f"hash{i}", size=10, path=Path(f"/x/{i}"), mtime=0, hits=()))
        cache.batch_put_results.assert_called_once()
        # flush 后缓冲应清空
        assert buf.is_empty

    def test_flush_empty_buffer_is_noop(self) -> None:
        """flush 空缓冲不应调用 cache.batch_put_results。"""
        cache = MagicMock()
        perf = PerfStats()
        buf = BatchBuffer(cache, perf)
        buf.flush()
        cache.batch_put_results.assert_not_called()
        assert buf.is_empty

    def test_flush_forces_write_of_pending(self) -> None:
        """flush 强制写出未达阈值的累积项。"""
        cache = MagicMock()
        perf = PerfStats()
        buf = BatchBuffer(cache, perf)
        for i in range(3):
            buf.add(BatchWriteItem(file_hash=f"h{i}", size=1, path=Path(f"/p/{i}"), mtime=0, hits=()))
        buf.flush()
        cache.batch_put_results.assert_called_once()
        items = cache.batch_put_results.call_args.args[0]
        assert len(items) == 3
        assert buf.is_empty

    def test_thread_safe_concurrent_add(self) -> None:
        """并发 add 不会丢失项（线程安全验证）。"""
        import threading

        cache = MagicMock()
        perf = PerfStats()
        buf = BatchBuffer(cache, perf)

        def producer(start: int) -> None:
            for i in range(start, start + 100):
                buf.add(BatchWriteItem(file_hash=f"h{i}", size=1, path=Path(f"/p/{i}"), mtime=0, hits=()))

        threads = [threading.Thread(target=producer, args=(i * 100,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 400 项全部写出（多次 flush 累计）
        buf.flush()
        total_written = sum(len(call.args[0]) for call in cache.batch_put_results.call_args_list)
        assert total_written == 400
        assert buf.is_empty


class TestBuildHitsFromCache:
    """build_hits_from_cache 从缓存字典重建 RuleHit 列表。"""

    def test_empty_cache_returns_empty_list(self) -> None:
        """空缓存字典返回空 hits 列表。"""
        rule = _content_rule("r1", "x")
        matcher = build_matcher(rule.match)
        applicable = [(rule, matcher, "rh1")]
        hits, errors = build_hits_from_cache(applicable, {})
        assert hits == []
        assert errors == 0

    def test_none_entries_are_skipped(self) -> None:
        """缓存中 None 值表示未命中，应跳过。"""
        rule = _content_rule("r1", "x")
        matcher = build_matcher(rule.match)
        applicable = [(rule, matcher, "rh1")]
        hits, errors = build_hits_from_cache(applicable, {"rh1": None})
        assert hits == []
        assert errors == 0

    def test_hit_is_rebuilt_with_rule_name(self) -> None:
        """缓存中的 RuleHit 应填回 rule.name 后返回。"""
        rule = _content_rule("myrule", "x")
        matcher = build_matcher(rule.match)
        applicable = [(rule, matcher, "rh1")]
        cached_hit = RuleHit(
            rule_name="",  # 缓存中 rule_name 为空字符串
            severity=Severity.WARNING,
            detail="d",
            match_text="t",
            match_count=1,
            target=MatchTarget.CONTENT,
            match_texts=("t",),
            match_description="desc",
        )
        hits, errors = build_hits_from_cache(applicable, {"rh1": cached_hit})
        assert len(hits) == 1
        assert hits[0].rule_name == "myrule"
        assert hits[0].severity == Severity.WARNING
        assert hits[0].match_texts == ("t",)
        assert errors == 0

    def test_output_order_follows_applicable(self) -> None:
        """输出顺序由 applicable 列表决定，而非缓存字典迭代顺序。"""
        rule_a = _content_rule("a", "x")
        rule_b = _content_rule("b", "x")
        rule_c = _content_rule("c", "x")
        applicable = [
            (rule_a, build_matcher(rule_a.match), "ra"),
            (rule_b, build_matcher(rule_b.match), "rb"),
            (rule_c, build_matcher(rule_c.match), "rc"),
        ]
        # 字典顺序故意打乱
        cached: dict[str, RuleHit | None] = {
            "rc": RuleHit(
                rule_name="",
                severity=Severity.WARNING,
                detail="",
                match_text="",
                match_count=0,
                target=MatchTarget.CONTENT,
                match_texts=(),
                match_description="",
            ),
            "ra": RuleHit(
                rule_name="",
                severity=Severity.WARNING,
                detail="",
                match_text="",
                match_count=0,
                target=MatchTarget.CONTENT,
                match_texts=(),
                match_description="",
            ),
            "rb": RuleHit(
                rule_name="",
                severity=Severity.WARNING,
                detail="",
                match_text="",
                match_count=0,
                target=MatchTarget.CONTENT,
                match_texts=(),
                match_description="",
            ),
        }
        hits, _ = build_hits_from_cache(applicable, cached)
        assert [h.rule_name for h in hits] == ["a", "b", "c"]


class TestExtractWithCache:
    """extract_with_cache 缓存查询与回退路径。"""

    def test_directory_entry_returns_empty(self, tmp_path: Path) -> None:
        """目录 entry 返回空内容与空字节哈希。"""
        cache = MagicMock()
        perf = PerfStats()
        dir_path = tmp_path / "subdir"
        dir_path.mkdir()
        entry = FileEntry.from_path(dir_path)
        content, file_hash = extract_with_cache(entry, cache, 0, perf)
        assert content == ""
        assert file_hash == hash_bytes(b"")
        cache.get_extracted_content.assert_not_called()

    def test_large_file_skips_read(self, tmp_path: Path) -> None:
        """文件超过 max_file_size 跳过读取，返回空内容。"""
        cache = MagicMock()
        perf = PerfStats()
        entry = _make_entry(tmp_path, "big.txt", b"x" * 100)
        content, file_hash = extract_with_cache(entry, cache, 50, perf)
        assert content == ""
        assert file_hash == hash_bytes(b"")
        cache.get_extracted_content.assert_not_called()

    def test_cache_hit_skips_extract(self, tmp_path: Path) -> None:
        """缓存命中时跳过 extract_content_from_bytes。"""
        cache = MagicMock()
        cache.get_extracted_content.return_value = "cached content"
        perf = PerfStats()
        entry = _make_entry(tmp_path, "f.txt", b"raw bytes")
        content, _ = extract_with_cache(entry, cache, 0, perf)
        assert content == "cached content"
        cache.get_extracted_content.assert_called_once()
        # 未命中才会调用 put_extracted_content；命中不写
        cache.put_extracted_content.assert_not_called()

    def test_cache_miss_extracts_and_writes(self, tmp_path: Path) -> None:
        """缓存未命中时执行提取并写入缓存。"""
        cache = MagicMock()
        cache.get_extracted_content.return_value = None
        perf = PerfStats()
        entry = _make_entry(tmp_path, "f.txt", b"hello world")
        content, _ = extract_with_cache(entry, cache, 0, perf)
        assert content == "hello world"
        cache.put_extracted_content.assert_called_once()
        # put_extracted_content(file_hash, content, extension)
        call_args = cache.put_extracted_content.call_args.args
        assert call_args[1] == "hello world"
        assert call_args[2] == "txt"

    def test_empty_content_not_written_to_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """提取返回空内容时不写入缓存。"""
        cache = MagicMock()
        cache.get_extracted_content.return_value = None
        perf = PerfStats()
        entry = _make_entry(tmp_path, "f.txt", b"")  # 空文件
        # 空文件 read_bytes 返回 b""，提取后内容为空
        _, _ = extract_with_cache(entry, cache, 0, perf)
        # 空内容不写入
        cache.put_extracted_content.assert_not_called()

    def test_read_failure_returns_empty(self, tmp_path: Path) -> None:
        """read_bytes 抛 OSError 时返回空内容。"""
        cache = MagicMock()
        perf = PerfStats()
        entry = _make_entry(tmp_path, "f.txt", b"data")
        # 删除文件使 read_bytes 失败
        entry.path.unlink()
        content, file_hash = extract_with_cache(entry, cache, 0, perf)
        assert content == ""
        assert file_hash == hash_bytes(b"")
        cache.get_extracted_content.assert_not_called()

    def test_extract_failure_falls_back_to_utf8(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """extract_content_from_bytes 抛异常时回退到 UTF-8 解码。"""
        cache = MagicMock()
        cache.get_extracted_content.return_value = None
        perf = PerfStats()
        entry = _make_entry(tmp_path, "f.txt", b"fallback content")

        def boom(data: bytes, extension: str) -> str:
            raise RuntimeError("simulated extractor failure")

        monkeypatch.setattr("fuscan.scanner._cache_phase.extract_content_from_bytes", boom)
        content, _ = extract_with_cache(entry, cache, 0, perf)
        assert content == "fallback content"
        # 回退后仍写入缓存
        cache.put_extracted_content.assert_called_once()
