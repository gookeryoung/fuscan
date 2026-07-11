"""ZIP 压缩文件读取器。

基于标准库 zipfile 实现，支持加密压缩包（密码尝试）与损坏压缩包容错。
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from uniscan.archive.base import ArchiveEntry, ArchiveError, ArchiveReader

__all__ = ["ZipReader"]

logger = logging.getLogger(__name__)


class ZipReader(ArchiveReader):
    """ZIP 压缩包读取器。

    使用 zipfile.ZipFile 读取标准 ZIP 格式（含 zip/gzip 场景下的常规 zip）。
    加密条目需要密码；未提供密码或密码错误时跳过并记录。
    """

    def __init__(self, path: Path, password: str | None = None) -> None:
        self._path = path
        self._password = password.encode("utf-8") if password else None
        try:
            self._zip = zipfile.ZipFile(str(path), mode="r")
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"损坏的 ZIP 文件: {path}") from exc
        except OSError as exc:
            raise ArchiveError(f"无法打开 ZIP 文件: {path}: {exc}") from exc

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return ("zip",)

    def list_entries(self) -> list[ArchiveEntry]:
        """列出压缩包内所有条目（目录与文件均列出）。"""
        entries: list[ArchiveEntry] = []
        for info in self._zip.infolist():
            entries.append(
                ArchiveEntry(
                    archive_path=self._path,
                    entry_name=info.filename,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    is_dir=info.is_dir(),
                )
            )
        return entries

    def read_entry(self, entry_name: str) -> bytes:
        """读取条目内容。

        :raises ArchiveError: 读取失败（加密、损坏、找不到条目等）
        """
        try:
            info = self._zip.getinfo(entry_name)
        except KeyError as exc:
            raise ArchiveError(f"ZIP 条目不存在: {entry_name}") from exc

        if info.is_dir():
            return b""

        # 加密条目：尝试密码；无密码则跳过
        if info.flag_bits & 0x1:
            if self._password is None:
                logger.info("ZIP 条目加密且未提供密码，跳过: %s!%s", self._path, entry_name)
                raise ArchiveError(f"加密条目未提供密码: {entry_name}")
            try:
                return self._zip.read(entry_name, pwd=self._password)
            except RuntimeError as exc:
                raise ArchiveError(f"ZIP 密码错误或解密失败: {entry_name}: {exc}") from exc

        try:
            return self._zip.read(entry_name)
        except RuntimeError as exc:
            raise ArchiveError(f"ZIP 条目读取失败: {entry_name}: {exc}") from exc
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"ZIP 条目损坏: {entry_name}: {exc}") from exc

    def close(self) -> None:
        """关闭 ZIP 文件句柄。"""
        try:
            self._zip.close()
        except Exception:  # pragma: no cover - 关闭异常无需上报
            logger.debug("关闭 ZIP 文件句柄失败: %s", self._path, exc_info=True)

    def __enter__(self) -> ZipReader:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
