"""缓存扫描阶段：缓存查询/批量写入/缓存命中重建逻辑。

从 :class:`fuscan.scanner.scanner.Scanner` 抽离的缓存模式相关纯逻辑，

- :class:`BatchBuffer`：累积 :class:`BatchWriteItem` 并在阈值后单次事务 flush
- :func:`build_hits_from_cache`：从缓存字典重建 :class:`RuleHit` 列表
- :func:`extract_with_cache`：缓存模式的提取+哈希（优先复用提取内容缓存）

本模块仅依赖 :class:`CacheStore` / :class:`PerfStats` / :class:`FileEntry`，
不持有 :class:`Scanner` 实例引用，便于独立测试。

公共 API：

- :class:`BatchBuffer`：批量写入缓冲
- :func:`build_hits_from_cache`：从缓存重建 RuleHit 列表
- :func:`extract_with_cache`：缓存模式提取+哈希
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from fuscan.cache.hashes import hash_bytes
from fuscan.cache.store import BatchWriteItem
from fuscan.extractors import extract_content_from_bytes
from fuscan.scanner._helpers import BATCH_THRESHOLD
from fuscan.scanner.result import RuleHit

if TYPE_CHECKING:
    from fuscan.cache import CacheStore
    from fuscan.perf import PerfStats
    from fuscan.rules.model import Rule
    from fuscan.scanner.context import FileEntry
    from fuscan.scanner.matchers import Matcher

__all__ = [
    "BatchBuffer",
    "build_hits_from_cache",
    "extract_with_cache",
]

logger = logging.getLogger(__name__)


class BatchBuffer:
    """批量写入缓冲：累积 :class:`BatchWriteItem`，达阈值后单次事务 flush。

    线程安全：通过内部锁保护并发累积与 flush。
    缓存模式（``CacheStore`` 非 None）下由 :class:`Scanner` 持有，
    worker 线程在扫描每个文件后调用 :meth:`add` 累积写入请求。
    """

    def __init__(self, cache: CacheStore, perf: PerfStats) -> None:
        """初始化批量写入缓冲。

        :param cache: 缓存存储实例（``batch_put_results`` 调用目标）
        :param perf: 性能统计实例（``cache_write`` 阶段计时）
        """
        self._cache = cache
        self._perf = perf
        self._pending: list[BatchWriteItem] = []
        self._lock = threading.Lock()

    def add(self, item: BatchWriteItem) -> None:
        """累积写入请求到批量缓冲，达到阈值时自动 flush。

        :param item: 单文件的批量写入项（含 file_hash/path/mtime/hits）
        """
        with self._lock:
            self._pending.append(item)
            if len(self._pending) >= BATCH_THRESHOLD:
                self._flush_locked()

    @property
    def is_empty(self) -> bool:
        """待写批次是否为空（测试与诊断用）。"""
        with self._lock:
            return not self._pending

    def flush(self) -> None:
        """强制 flush 待写批次。

        在扫描阶段切换（如进入 archive phase）与 ``scan()`` 末尾调用，
        确保累积的数据不丢失。
        """
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        """执行批量写入（已持锁）。

        先取出并清空 ``_pending``，再调用 :meth:`CacheStore.batch_put_results`。
        ``_lock`` 仍持锁，但 ``CacheStore`` 内部的 ``RLock`` 是另一把锁，
        worker 线程在 :func:`extract_with_cache` 中查询不受影响。
        """
        if not self._pending:
            return
        items = self._pending
        self._pending = []
        with self._perf.measure("cache_write"):
            self._cache.batch_put_results(items)


def build_hits_from_cache(
    applicable: list[tuple[Rule, Matcher, str]],
    cached: dict[str, RuleHit | None],
) -> tuple[list[RuleHit], int]:
    """从缓存字典重建 :class:`RuleHit` 列表。

    :param applicable: 适用的 ``(Rule, Matcher, rule_hash)`` 列表，决定输出顺序
    :param cached: ``rule_hash -> RuleHit | None`` 字典；``None`` 表示缓存记录为未命中
    :return: ``(hits, rule_errors)``；纯缓存路径下 ``rule_errors`` 恒为 0
    """
    hits: list[RuleHit] = []
    for rule, _, rule_hash in applicable:
        result = cached.get(rule_hash)
        if result is not None:
            hits.append(
                RuleHit(
                    rule_name=rule.name,
                    severity=result.severity,
                    detail=result.detail,
                    match_text=result.match_text,
                    match_count=result.match_count,
                    target=result.target,
                    match_texts=result.match_texts,
                    match_description=result.match_description,
                )
            )
    return hits, 0


def extract_with_cache(
    entry: FileEntry,
    cache: CacheStore,
    max_file_size: int,
    perf: PerfStats,
) -> tuple[str, str]:
    """缓存模式的提取+哈希：优先复用提取内容缓存（iter-39）。

    与 :func:`default_extract_content_with_hash` 的区别：

    - 一次 ``read_bytes`` 算哈希后，先查 :meth:`CacheStore.get_extracted_content`
    - 命中则跳过 ``extract_content_from_bytes``（docx/pptx 提取 5-8ms）
    - 未命中则提取并写入缓存（非空内容才写）
    - 大文件跳过阈值由调用方传入，0 表示不限制

    各阶段接入 ``PerfStats`` 计时：
    ``read_bytes`` / ``hash`` / ``cache_lookup_extract`` / ``extract`` /
    ``cache_put_extract``，便于定位 I/O 与 CPU 瓶颈。

    :param entry: 文件元信息
    :param cache: 缓存存储实例
    :param max_file_size: 大文件跳过阈值（字节）；0 表示不限制
    :param perf: 性能统计实例
    :return: ``(content, file_hash)`` 元组；``file_hash`` 为 64 字符十六进制摘要
    """
    if entry.is_dir or (max_file_size > 0 and entry.size > max_file_size):
        return "", hash_bytes(b"")
    try:
        with perf.measure("read_bytes"):
            data = entry.path.read_bytes()
    except OSError:
        logger.debug("读取文件失败: %s", entry.path, exc_info=True)
        return "", hash_bytes(b"")
    with perf.measure("hash"):
        file_hash = hash_bytes(data)
    # 查提取内容缓存
    with perf.measure("cache_lookup_extract"):
        cached_content = cache.get_extracted_content(file_hash)
    if cached_content is not None:
        return cached_content, file_hash
    # 未命中，执行提取
    try:
        with perf.measure("extract"):
            content = extract_content_from_bytes(data, entry.extension)
    except Exception:
        logger.debug("提取器提取失败，回退到纯文本: %s", entry.path, exc_info=True)
        content = data.decode("utf-8", errors="ignore")
    # 写入提取内容缓存（非空才写）
    if content:
        with perf.measure("cache_put_extract"):
            cache.put_extracted_content(file_hash, content, entry.extension)
    return content, file_hash
