"""压缩文件扫描抽象层。

定义 ArchiveEntry 数据结构与 ArchiveReader 抽象基类。
具体实现见 zip_reader.py 与 rar_reader.py。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TypeVar

__all__ = [
    "ArchiveEntry",
    "ArchiveError",
    "ArchiveReader",
    "ArchiveReaderFactory",
    "default_factory",
    "get_reader",
    "is_archive",
]

logger = logging.getLogger(__name__)

_T = TypeVar("_T", bound="ArchiveReader")


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
        """条目扩展名（不含点，小写），正确处理 dotfile 如 ``.env``。"""
        p = Path(self.entry_name)
        suffix = p.suffix
        if suffix:
            return suffix.lower().lstrip(".")
        # dotfile（如 .env）：suffix 为空但文件名以 . 开头，取点后部分作为扩展名
        name = p.name
        if name.startswith(".") and len(name) > 1:
            return name[1:].lower()
        return ""

    @property
    def display_path(self) -> str:
        """展示用路径：archive.zip!inner/file.txt。"""
        return f"{self.archive_path}!{self.entry_name}"


class ArchiveReader(ABC):
    """压缩文件读取器抽象基类。

    子类须实现 :meth:`_close_resource` 关闭底层句柄；:meth:`close` 与
    :meth:`__enter__`/:meth:`__exit__` 由基类统一提供，避免 3 个子类重复
    try/except 包装与上下文管理器样板。
    """

    @property
    @abstractmethod
    def supported_extensions(self) -> tuple[str, ...]:
        """支持的压缩文件扩展名。"""

    @abstractmethod
    def list_entries(self) -> list[ArchiveEntry]:
        """列出压缩包内所有条目。"""

    @abstractmethod
    def read_entry(self, entry_name: str) -> bytes:
        """读取条目内容到内存。

        :raises ArchiveError: 读取失败（加密、损坏等）
        """

    @abstractmethod
    def _close_resource(self) -> None:
        """关闭底层资源句柄（由 :meth:`close` 包装异常处理）。

        子类实现应仅包含「关闭句柄」的裸调用，无需 try/except —— 基类
        :meth:`close` 统一捕获异常并记录 debug 日志。
        """

    def close(self) -> None:
        """关闭资源，捕获并记录异常（不抛出）。

        关闭异常属于「清理路径异常」，无需上报调用方；基类统一捕获并记录
        debug 日志，子类如需额外清理（如释放缓存）可覆盖本方法并在末尾
        调用 ``super().close()``。
        """
        try:
            self._close_resource()
        except Exception:  # pragma: no cover - 关闭异常无需上报
            logger.debug("关闭压缩文件句柄失败: %s", getattr(self, "_path", "<unknown>"), exc_info=True)

    def __enter__(self: _T) -> _T:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class ArchiveReaderFactory:
    """压缩文件读取器工厂：按扩展名分发。"""

    def __init__(self) -> None:
        self._factories: dict[str, type[ArchiveReader]] = {}

    def register(self, extension: str, reader_cls: type[ArchiveReader]) -> None:
        """注册指定扩展名的读取器类。"""
        self._factories[extension.lower().lstrip(".")] = reader_cls

    def get(self, extension: str) -> type[ArchiveReader] | None:
        """按扩展名查询已注册的读取器类，未注册返回 None。"""
        return self._factories.get(extension.lower().lstrip("."))

    @property
    def registered_extensions(self) -> frozenset[str]:
        """已注册的全部扩展名（小写，不含点）。"""
        return frozenset(self._factories.keys())

    def create(self, path: Path, password: str | None = None) -> ArchiveReader | None:
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


def get_reader(path: Path, password: str | None = None) -> ArchiveReader | None:
    """从默认工厂创建读取器。"""
    return default_factory.create(path, password=password)


def is_archive(path: Path) -> bool:
    """判断文件是否为已注册的压缩文件类型（仅按扩展名，不实例化）。

    用于避免对非压缩文件启动并行扫描任务；损坏的压缩文件仍返回 True，
    交由 :meth:`ArchiveScanner.scan_archive` 捕获并返回错误结果。
    """
    return default_factory.get(path.suffix) is not None
