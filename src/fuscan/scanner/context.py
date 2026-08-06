"""扫描上下文：文件元信息与懒加载内容。"""

from __future__ import annotations

import os
import stat as stat_mod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = ["FileEntry", "HashingContentProvider", "MatchContext", "default_content_provider"]


def _extract_extension(path: Path) -> str:
    """从路径提取文件扩展名（小写、去前导点），正确处理 dotfile。

    Python ``pathlib.Path.suffix`` 对 dotfile（如 ``.env``）返回空字符串——
    因为它把整个文件名视为 stem（隐藏文件约定）。但 ``.env`` 在
    ``scan_extensions`` 中应匹配 ``"env"``，否则会被错误跳过。

    规则：
    - 普通文件（``app.py``）→ ``path.suffix`` 正常返回 ``".py"`` → ``"py"``
    - dotfile（``.env``）→ ``suffix`` 为空但文件名以 ``.`` 开头且非仅 ``.``/``..`` →
      取点后部分作为扩展名 → ``"env"``
    - 无扩展名文件（``Makefile``）→ ``suffix`` 为空且非 dotfile → ``""``
    """
    suffix = path.suffix
    if suffix:
        return suffix.lower().lstrip(".")
    name = path.name
    if name.startswith(".") and len(name) > 1:
        return name[1:].lower()
    return ""


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
            st = path.stat()
            return cls(
                path=path,
                name=path.name,
                size=st.st_size,
                mtime=st.st_mtime,
                extension=_extract_extension(path),
                # 复用 stat 结果判断目录，避免再调用 path.is_dir() 产生第二次系统调用
                is_dir=stat_mod.S_ISDIR(st.st_mode),
            )
        except OSError:
            # 文件不可访问时返回空元信息，由扫描器决定是否跳过
            return cls(
                path=path,
                name=path.name,
                size=0,
                mtime=0.0,
                extension=_extract_extension(path),
                is_dir=False,
            )

    @classmethod
    def from_direntry(cls, entry: os.DirEntry[str]) -> FileEntry:
        """从 os.scandir 的 DirEntry 构造 FileEntry。

        Windows 平台 DirEntry.stat() 复用 scandir 已获取的文件属性，
        比 Path.stat() 更高效；同时用 stat 结果判断目录，避免额外系统调用。
        """
        try:
            st = entry.stat()
            path = Path(entry.path)
            return cls(
                path=path,
                name=entry.name,
                size=st.st_size,
                mtime=st.st_mtime,
                extension=_extract_extension(path),
                is_dir=stat_mod.S_ISDIR(st.st_mode),
            )
        except OSError:
            path = Path(entry.path)
            return cls(
                path=path,
                name=entry.name,
                size=0,
                mtime=0.0,
                extension=_extract_extension(path),
                is_dir=False,
            )


ContentProvider = Callable[["FileEntry"], str]

# 带哈希的内容提供器：返回 (content, file_hash)。
# 缓存模式下用此类型，使文件哈希计算与内容提取共享一次磁盘 I/O。
HashingContentProvider = Callable[["FileEntry"], tuple[str, str]]


def default_content_provider(entry: FileEntry, *, max_size: int = 50 * 1024 * 1024) -> str:
    """默认内容提供器：读取文本文件内容，限制最大 50MB。

    二进制文件或超大文件返回空字符串，由上层决定是否跳过。
    阈值与 :data:`fuscan.config.DEFAULT_MAX_FILE_SIZE` 对齐（独立硬编码以保持
    本模块无 fuscan.config 依赖，便于在轻量场景独立复用 FileEntry/default_content_provider）。
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

    __slots__ = ("_content", "_content_loaded", "_content_lower", "_content_lower_loaded", "_content_provider", "entry")

    def __init__(
        self,
        entry: FileEntry,
        content_provider: ContentProvider | None = None,
    ) -> None:
        self.entry = entry
        self._content: str = ""
        self._content_provider: ContentProvider = content_provider or default_content_provider
        self._content_loaded: bool = False
        self._content_lower: str = ""
        self._content_lower_loaded: bool = False

    @property
    def content(self) -> str:
        """懒加载文件内容；首次访问时调用 content_provider。"""
        if not self._content_loaded:
            self._content = self._content_provider(self.entry)
            self._content_loaded = True
        return self._content

    @property
    def content_lower(self) -> str:
        """懒加载小写化文件内容；供大小写不敏感预筛复用，避免每个 Matcher 重复 ``lower()``。

        首次访问时基于 :attr:`content` 计算（触发内容懒加载），后续访问直接返回缓存。
        组合规则复合组（:class:`fuscan.scanner.matchers._ContentCompositeGroup`）的
        预筛在 50+ 条 AND 规则场景下原先每条规则各调一次 ``content.lower()``，
        集中缓存后每文件仅计算一次。
        """
        if not self._content_lower_loaded:
            self._content_lower = self.content.lower()
            self._content_lower_loaded = True
        return self._content_lower

    def reset(self) -> None:
        """重置内容缓存，强制下次重新读取。"""
        self._content = ""
        self._content_loaded = False
        self._content_lower = ""
        self._content_lower_loaded = False
