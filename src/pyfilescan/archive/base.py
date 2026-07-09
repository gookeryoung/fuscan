"""压缩文件扫描抽象层。

定义 ArchiveEntry 数据结构与 ArchiveReader 抽象基类。
具体实现见 zip_reader.py 与 rar_reader.py。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

__all__ = [
    "ArchiveEntry",
    "ArchiveError",
    "ArchiveReader",
    "ArchiveReaderFactory",
    "default_factory",
    "get_reader",
]


class ArchiveError(Exception):
    """压缩文件相关错误。"""


@dataclass(frozen=True)
class ArchiveEntry:
    """压缩包内文件条目。"""

    archive_path: Path
    entry_name: str
    size: int
    compressed_size: int
    is_dir: bool = False

    @property
    def name(self) -> str:
        """条目文件名（不含目录部分）。"""
        return Path(self.entry_name).name

    @property
    def extension(self) -> str:
        """条目扩展名（不含点，小写）。"""
        return Path(self.entry_name).suffix.lower().lstrip(".")

    @property
    def display_path(self) -> str:
        """展示用路径：archive.zip!inner/file.txt。"""
        return f"{self.archive_path}!{self.entry_name}"


class ArchiveReader(ABC):
    """压缩文件读取器抽象基类。"""

    @property
    @abstractmethod
    def supported_extensions(self) -> tuple[str, ...]:
        """支持的压缩文件扩展名。"""

    @abstractmethod
    def list_entries(self) -> List[ArchiveEntry]:
        """列出压缩包内所有条目。"""

    @abstractmethod
    def read_entry(self, entry_name: str) -> bytes:
        """读取条目内容到内存。

        :raises ArchiveError: 读取失败（加密、损坏等）
        """


class ArchiveReaderFactory:
    """压缩文件读取器工厂：按扩展名分发。"""

    def __init__(self) -> None:
        self._factories: dict[str, type[ArchiveReader]] = {}

    def register(self, extension: str, reader_cls: type[ArchiveReader]) -> None:
        self._factories[extension.lower().lstrip(".")] = reader_cls

    def get(self, extension: str) -> Optional[type[ArchiveReader]]:
        return self._factories.get(extension.lower().lstrip("."))

    def create(self, path: Path, password: Optional[str] = None) -> Optional[ArchiveReader]:
        """按扩展名创建读取器实例。"""
        ext = path.suffix.lower().lstrip(".")
        reader_cls = self._factories.get(ext)
        if reader_cls is None:
            return None
        try:
            return reader_cls(path, password=password)  # type: ignore[call-arg]
        except TypeError:
            return reader_cls(path)  # type: ignore[call-arg]


default_factory = ArchiveReaderFactory()


def get_reader(path: Path, password: Optional[str] = None) -> Optional[ArchiveReader]:
    """从默认工厂创建读取器。"""
    return default_factory.create(path, password=password)
