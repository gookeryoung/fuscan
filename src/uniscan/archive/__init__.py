"""压缩文件扫描模块。

提供 ZIP/RAR 压缩包条目列举与内容读取能力，供 ArchiveScanner 调用。
"""

from __future__ import annotations

from uniscan.archive.base import (
    ArchiveEntry,
    ArchiveError,
    ArchiveReader,
    ArchiveReaderFactory,
    default_factory,
    get_reader,
)
from uniscan.archive.rar_reader import RarReader
from uniscan.archive.zip_reader import ZipReader

__all__ = [
    "ArchiveEntry",
    "ArchiveError",
    "ArchiveReader",
    "ArchiveReaderFactory",
    "ArchiveScanner",
    "RarReader",
    "ZipReader",
    "default_factory",
    "get_reader",
    "register_all",
]


def register_all(factory: ArchiveReaderFactory = default_factory) -> None:
    """注册所有内置压缩文件读取器（幂等）。"""
    if factory.get("zip") is None:
        factory.register("zip", ZipReader)
    if factory.get("rar") is None:
        factory.register("rar", RarReader)


# 模块导入即注册
register_all()


# 延迟导入避免循环依赖
from uniscan.archive.scanner import ArchiveScanner  # noqa: E402
