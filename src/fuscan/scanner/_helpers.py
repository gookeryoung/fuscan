"""扫描器纯函数与常量辅助模块。

从 :mod:`fuscan.scanner.scanner` 抽离的不依赖 :class:`Scanner` 实例状态的
纯函数与模块级常量，便于独立测试与复用。

公共 API：

- :data:`BATCH_THRESHOLD`：批量写入阈值
- :data:`PROGRESS_LIST_MAX`：进度收集列表上限
- :data:`GIL_YIELD_INTERVAL`：GIL 让步间隔
- :func:`default_extract_content`：默认内容提供器
- :func:`default_extract_content_with_hash`：带哈希的内容提供器
- :func:`empty_content_provider`：空内容提供器
- :func:`spec_needs_content`：检查 MatchSpec 是否包含 CONTENT 目标
- :func:`cancel_all_futures`：取消全部 future
- :func:`normalize_max_file_size`：规范化大文件跳过阈值
"""

from __future__ import annotations

import logging
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any, Iterable

from fuscan.cache.hashes import hash_bytes
from fuscan.config import DEFAULT_MAX_FILE_SIZE
from fuscan.extractors import (
    extract_content_from_bytes_with_retry,
    extract_content_with_fallback,
)
from fuscan.rules.model import MatchSpec, MatchTarget

if TYPE_CHECKING:
    from fuscan.scanner.context import FileEntry

__all__ = [
    "BATCH_THRESHOLD",
    "DEFAULT_MAX_FILE_SIZE",
    "GIL_YIELD_INTERVAL",
    "PROGRESS_LIST_MAX",
    "cancel_all_futures",
    "default_extract_content",
    "default_extract_content_with_hash",
    "empty_content_provider",
    "normalize_max_file_size",
    "spec_needs_content",
]

logger = logging.getLogger(__name__)

# 批量写入阈值：累积到该文件数后自动 flush 一次事务。
# 50 个文件 × 平均 2 条规则 = 100 行 scan_results + 50 行 scanned_files + 50 行 file_paths，
# 单次事务约 200 行写入，相比逐条 commit（200 次 fsync）减少 99% 提交开销。
BATCH_THRESHOLD: int = 50

# 默认大文件跳过阈值：引用 config 模块的权威常量，避免多模块重复硬编码。
# 0 表示不限制；可通过 Config.max_file_size 与 Scanner(max_file_size=...) 覆盖。
# DEFAULT_MAX_FILE_SIZE 已从 fuscan.config 重新导出，便于 scanner 子包内统一引用。

# 进度收集列表上限：_skipped_dirs 与 _matched_files 使用 deque(maxlen=) 防止
# 大规模扫描（如全盘跳过 node_modules）时列表无界增长导致内存膨胀。
# _emit_progress 取该上限条 recent 条目，足够 GUI 展示近期跳过/命中情况。
# 50 项已足够用户感知"近期"上下文，更大的值会导致高频进度回调时
# tuple 拷贝与信号槽分发占用主线程时间片引起 UI 卡滞。
PROGRESS_LIST_MAX: int = 50

# GIL 让步间隔：_scan_concurrent 每处理 N 个文件 sleep(0) 一次，
# 让 UI 线程有机会处理 Qt 事件队列。20 个文件约对应 1-5ms 扫描时间，
# sleep(0) 开销约 1μs，对吞吐影响可忽略。
GIL_YIELD_INTERVAL: int = 20


def default_extract_content(entry: FileEntry) -> str:
    """默认内容提供器：通过提取器注册表按扩展名提取文本。

    无注册提取器时回退到纯文本读取；提取失败返回空字符串。
    """
    return extract_content_with_fallback(entry.path)


def empty_content_provider(_fe: FileEntry) -> str:
    """空内容提供器：返回空字符串，跳过所有文件 I/O。

    用于规则集不含 CONTENT 规则或文件超过大小上限的场景，
    使 FILENAME/PATH 规则仍可命中而无需读取文件内容。
    """
    return ""


def default_extract_content_with_hash(entry: FileEntry) -> tuple[str, str]:
    """带哈希的内容提供器：读字节算 BLAKE2b，再从同一份字节提取内容。

    一次 ``read_bytes`` 既算哈希又提取内容，避免提取器内部重复读磁盘。
    缓存模式下，``Scanner`` 用此函数替代 :func:`default_extract_content`，
    使文件哈希计算与内容提取共享一次磁盘 I/O。

    哈希算法由 :func:`fuscan.cache.hashes.hash_bytes` 决定（BLAKE2b，
    ``digest_size=32``，64 字符 hex）。算法变更需递增
    :data:`fuscan.cache.schema.CACHE_COMPAT_VERSION` 触发旧缓存失效。

    超过 :data:`DEFAULT_MAX_FILE_SIZE`（50MB）的文件跳过读取，
    返回空内容与空字节哈希；``Scanner`` 在缓存模式下走自己的
    :meth:`Scanner._extract_with_cache`，使用可配置的 ``max_file_size``。

    iter-119：使用 :func:`extract_content_from_bytes_with_retry` 替代
    :func:`extract_content_from_bytes`，对瞬时 ``OSError``（Windows AV 文件锁、
    网络盘抖动）重试一次（退避 50ms），避免不必要的纯文本降级。

    :param entry: 文件元信息
    :return: ``(content, file_hash)`` 元组；``file_hash`` 为 64 字符十六进制摘要
    """
    if entry.is_dir or entry.size > DEFAULT_MAX_FILE_SIZE:
        return "", hash_bytes(b"")
    try:
        data = entry.path.read_bytes()
    except OSError:
        logger.debug("读取文件失败: %s", entry.path, exc_info=True)
        return "", hash_bytes(b"")
    file_hash = hash_bytes(data)
    try:
        content = extract_content_from_bytes_with_retry(data, entry.extension)
    except Exception:
        logger.debug("提取器提取失败，回退到纯文本: %s", entry.path, exc_info=True)
        content = data.decode("utf-8", errors="ignore")
    return content, file_hash


def spec_needs_content(spec: MatchSpec) -> bool:
    """递归检查 MatchSpec 是否包含 CONTENT 目标。

    若所有规则均不需要内容，扫描器可跳过文件 I/O（缓存与无缓存模式均适用）。
    """
    from fuscan.rules.model import AndMatch, LeafMatch, NotMatch, OrMatch

    if isinstance(spec, LeafMatch):
        return spec.target == MatchTarget.CONTENT
    if isinstance(spec, AndMatch):
        return any(spec_needs_content(c) for c in spec.children)
    if isinstance(spec, OrMatch):
        return any(spec_needs_content(c) for c in spec.children)
    if isinstance(spec, NotMatch):
        return spec_needs_content(spec.child)
    return False


def cancel_all_futures(futures: Iterable[Future[Any]]) -> None:
    """对全部 future 调 ``cancel()``。

    已启动的 future 调 ``cancel()`` 返回 False（无法中断），未启动的会成功取消。
    用于扫描取消时跳过 ``as_completed`` 阻塞等待（需求 req-13 R1）。
    """
    for future in futures:
        future.cancel()


def normalize_max_file_size(value: int | None) -> int:
    """规范化大文件跳过阈值：None 或负数退化为默认值，0 表示不限制。

    :param value: 调用方传入的原始值
    :return: 实际生效的阈值；0 表示不限制
    """
    if value is None or value < 0:
        return DEFAULT_MAX_FILE_SIZE
    return value
