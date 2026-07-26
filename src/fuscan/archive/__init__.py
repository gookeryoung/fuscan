"""压缩文件扫描模块。

提供 ZIP/RAR/7Z 压缩包条目列举与内容读取能力，供 ArchiveScanner 调用。

公共 API：

- :class:`ArchiveEntry` / :class:`ArchiveError` / :class:`ArchiveReader`
- :class:`ArchiveReaderFactory` / :func:`default_factory` / :func:`get_reader`
- :func:`is_archive`：判断路径是否为支持的压缩包
- :class:`RarReader` / :class:`SevenZReader` / :class:`ZipReader`：具体读取器
- :func:`register_all`：注册所有内置读取器（幂等，模块导入时自动调用一次）
- :class:`ArchiveScanner`：压缩包扫描器（延迟导入以避免与
  :mod:`fuscan.archive.scanner` 形成循环依赖）
"""

from __future__ import annotations

from fuscan.archive.base import (
    ArchiveEntry,
    ArchiveError,
    ArchiveReader,
    ArchiveReaderFactory,
    default_factory,
    get_reader,
    is_archive,
)
from fuscan.archive.rar_reader import RarReader
from fuscan.archive.sevenz_reader import SevenZReader
from fuscan.archive.zip_reader import ZipReader

__all__ = [
    "ArchiveEntry",
    "ArchiveError",
    "ArchiveReader",
    "ArchiveReaderFactory",
    "ArchiveScanner",
    "RarReader",
    "SevenZReader",
    "ZipReader",
    "default_factory",
    "get_reader",
    "is_archive",
    "register_all",
]


def register_all(factory: ArchiveReaderFactory = default_factory) -> None:
    """注册所有内置压缩文件读取器（幂等）。"""
    if factory.get("zip") is None:
        factory.register("zip", ZipReader)
    if factory.get("rar") is None:
        factory.register("rar", RarReader)
    if factory.get("7z") is None:
        factory.register("7z", SevenZReader)


# 模块导入即注册
register_all()


# 延迟导入避免循环依赖：fuscan.archive.scanner 依赖 fuscan.scanner.scanner，
# 而 fuscan.scanner.scanner 通过 TYPE_CHECKING 引用 ArchiveScanner，模块加载顺序
# 上需先完成 base/reader 子模块导入，再导入 scanner.scanner。
from fuscan.archive.scanner import ArchiveScanner  # noqa: E402
