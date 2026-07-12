"""扫描上下文：文件元信息与懒加载内容。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Tuple

__all__ = ["FileEntry", "HashingContentProvider", "MatchContext", "default_content_provider"]


@dataclass(frozen=True)
class FileEntry:
    """文件元信息。"""

    path: Path
    name: str
    size: int
    mtime: float
    extension: str
    is_dir: bool = False

    @classmethod
    def from_path(cls, path: Path) -> FileEntry:
        """从路径构造 FileEntry，执行一次 stat 调用。"""
        try:
            stat = path.stat()
            return cls(
                path=path,
                name=path.name,
                size=stat.st_size,
                mtime=stat.st_mtime,
                extension=path.suffix.lower().lstrip("."),
                is_dir=path.is_dir(),
            )
        except OSError:
            # 文件不可访问时返回空元信息，由扫描器决定是否跳过
            return cls(
                path=path,
                name=path.name,
                size=0,
                mtime=0.0,
                extension=path.suffix.lower().lstrip("."),
                is_dir=False,
            )


ContentProvider = Callable[["FileEntry"], str]

# 带哈希的内容提供器：返回 (content, file_hash)。
# 缓存模式下用此类型，使文件哈希计算与内容提取共享一次磁盘 I/O。
HashingContentProvider = Callable[["FileEntry"], Tuple[str, str]]


def default_content_provider(entry: FileEntry, *, max_size: int = 50 * 1024 * 1024) -> str:
    """默认内容提供器：读取文本文件内容，限制最大 50MB。

    二进制文件或超大文件返回空字符串，由上层决定是否跳过。
    """
    if entry.is_dir or entry.size > max_size:
        return ""
    try:
        return entry.path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


class MatchContext:
    """匹配上下文，懒加载文件内容。

    只有需要内容匹配的 Matcher 才会触发内容读取，避免不必要的 I/O。
    """

    __slots__ = ("_content", "_content_loaded", "_content_provider", "entry")

    def __init__(
        self,
        entry: FileEntry,
        content_provider: ContentProvider | None = None,
    ) -> None:
        self.entry = entry
        self._content: str = ""
        self._content_provider: ContentProvider = content_provider or default_content_provider
        self._content_loaded: bool = False

    @property
    def content(self) -> str:
        """懒加载文件内容；首次访问时调用 content_provider。"""
        if not self._content_loaded:
            self._content = self._content_provider(self.entry)
            self._content_loaded = True
        return self._content

    def reset(self) -> None:
        """重置内容缓存，强制下次重新读取。"""
        self._content = ""
        self._content_loaded = False
