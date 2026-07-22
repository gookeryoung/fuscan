"""文件内容提取缓存。

为 GUI 详情对话框/面板提供进程内 LRU 缓存，避免同一文件多次打开时
重复提取内容导致卡滞（PDF/DOCX 等二进制格式提取可能耗时数百毫秒）。

缓存键为 ``(path_str, mtime, size)``，文件修改后自动失效。
最大缓存 32 项，单次提取内容上限由调用方截断（GUI 预览限制 100KB），
总内存占用可控。

公共 API：

- :func:`extract_content_cached`：带缓存的提取，签名与 :func:`extract_content_with_fallback` 兼容
- :func:`clear_content_cache`：清空缓存（测试隔离或扫描完成后调用）
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path

from fuscan.extractors.base import extract_content_with_fallback

__all__ = ["clear_content_cache", "extract_content_cached"]

logger = logging.getLogger(__name__)

# 内容缓存：键为 (path_str, mtime, size)，值为提取的文本
_CONTENT_CACHE: OrderedDict[tuple[str, float, int], str] = OrderedDict()
_CONTENT_CACHE_MAX: int = 32
_CONTENT_CACHE_LOCK = threading.Lock()


def extract_content_cached(path: Path) -> str:
    """提取文件内容并缓存，相同文件（path+mtime+size）不重复提取。

    用于 GUI 详情对话框/面板的预览，避免同一文件多次打开时重复提取内容
    导致卡滞（PDF/DOCX 等二进制格式提取可能耗时数百毫秒）。

    缓存键包含 ``mtime`` 和 ``size``，文件修改后自动失效。
    ``stat`` 失败时回退到无缓存提取，不影响调用方。

    :param path: 文件路径
    :return: 提取的文本内容
    :raises OSError: 文件读取失败（透传自 :func:`extract_content_with_fallback`）
    """
    try:
        stat = path.stat()
    except OSError:
        # stat 失败时直接调用原函数，不缓存
        return extract_content_with_fallback(path)
    key = (str(path), stat.st_mtime, stat.st_size)
    with _CONTENT_CACHE_LOCK:
        cached = _CONTENT_CACHE.get(key)
        if cached is not None:
            _CONTENT_CACHE.move_to_end(key)
            return cached
    content = extract_content_with_fallback(path)
    with _CONTENT_CACHE_LOCK:
        _CONTENT_CACHE[key] = content
        if len(_CONTENT_CACHE) > _CONTENT_CACHE_MAX:
            _CONTENT_CACHE.popitem(last=False)
    return content


def clear_content_cache() -> None:
    """清空内容缓存。

    用于测试隔离或扫描完成后释放内存。
    """
    with _CONTENT_CACHE_LOCK:
        _CONTENT_CACHE.clear()
