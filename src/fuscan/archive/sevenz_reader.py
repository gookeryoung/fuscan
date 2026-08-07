"""7Z 压缩文件读取器。

基于第三方库 py7zr 实现，纯 Python 无需系统工具，支持加密条目（需提供密码）。

实现要点（py7zr API 限制与优化策略）：

- ``py7zr.SevenZipFile.read(targets)`` 多次调用同一 ``SevenZipFile`` 实例时，
  第二次起的 ``decompress`` 会因内部流状态污染而**死锁**（py7zr 0.22 复现）。
- 优化：不再在 ``__init__`` 中一次性 ``readall()`` 预读全部条目
  （大压缩包极慢、内存峰值高、无法中途取消），改为 **惰性读取**：
  ``read_entry`` 时每次创建新的 ``SevenZipFile`` 实例读取单个条目。
  虽然每次打开有头部解析开销（毫秒级），但解压才是耗时主体，且：

  1. 内存峰值从「全部条目」降到「单条目」
  2. 扫描过程中可取消（已解压条目无需回滚）
  3. 跳过目录/大文件时不解压无用内容

- 加密条目在 ``read_entry`` 时按密码策略抛出 ``ArchiveError``。
- ``list_entries`` 仍用 ``__init__`` 中缓存的 ``_info_map``（仅元数据，不解压）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from fuscan.archive.base import ArchiveEntry, ArchiveError, ArchiveReader

if TYPE_CHECKING:
    import py7zr

__all__ = ["SevenZReader"]

logger = logging.getLogger(__name__)


class SevenZReader(ArchiveReader):
    """7Z 压缩包读取器。

    使用 py7zr 库读取 7z 格式（纯 Python 实现，无需系统工具）。
    加密条目需要密码；未提供密码或密码错误时跳过并记录。

    惰性读取优化，``__init__`` 仅解析元数据（list），
    ``read_entry`` 按需解压单个条目，避免 ``readall()`` 预读全部内容。
    """

    def __init__(self, path: Path, password: str | None = None) -> None:
        try:
            import py7zr  # 惰性导入，避免未安装时的导入失败
        except ImportError as exc:
            raise ArchiveError("py7zr 库未安装，无法读取 7Z 文件") from exc

        super().__init__(path)
        self._password = password
        try:
            self._sevenz: py7zr.SevenZipFile = py7zr.SevenZipFile(str(path), mode="r", password=password)
        except py7zr.Bad7zFile as exc:
            raise ArchiveError(f"损坏的 7Z 文件: {path}") from exc
        except py7zr.PasswordRequired as exc:
            raise ArchiveError(f"7Z 文件需要密码: {path}: {exc}") from exc
        except py7zr.UnsupportedCompressionMethodError as exc:
            raise ArchiveError(f"不支持的 7Z 压缩方法: {path}: {exc}") from exc
        except OSError as exc:
            raise ArchiveError(f"无法打开 7Z 文件: {path}: {exc}") from exc
        except Exception as exc:
            raise ArchiveError(f"打开 7Z 文件失败: {path}: {exc}") from exc
        # 预构建 entry_name -> FileInfo 映射（仅元数据，不解压内容）
        # py7zr.SevenZipFile 无 getinfo 方法，list() 返回 List[FileInfo]
        self._info_map: dict[str, Any] = {info.filename: info for info in self._sevenz.list()}
        # 不再 _preload_bytes()，改为 read_entry 惰性读取
        # 加密条目集合：首次 read_entry 失败时标记，后续直接跳过
        self._encrypted_entries: set[str] = set()
        # 已读取条目字节缓存（避免重复解压同一条目）
        self._bytes_cache: dict[str, bytes] = {}

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """支持的压缩文件扩展名。"""
        return ("7z",)

    @override
    def list_entries(self) -> list[ArchiveEntry]:
        """列出压缩包内所有条目。"""
        entries: list[ArchiveEntry] = []
        for info in self._info_map.values():
            entries.append(
                ArchiveEntry(
                    archive_path=self._path,
                    entry_name=info.filename,
                    size=int(getattr(info, "uncompressed", 0) or 0),
                    compressed_size=int(getattr(info, "compressed", 0) or 0),
                    is_dir=bool(getattr(info, "is_directory", False)),
                )
            )
        return entries

    def _lazy_read_entry(self, entry_name: str) -> bytes:
        """惰性读取单个条目：创建新 ``SevenZipFile`` 实例解压。

        :raises ArchiveError: 读取失败（加密、损坏等）
        """
        try:
            import py7zr
        except ImportError as exc:  # pragma: no cover - 构造时已校验
            raise ArchiveError("py7zr 库未安装") from exc

        try:
            with py7zr.SevenZipFile(str(self._path), mode="r", password=self._password) as sz:
                data = sz.read(targets=[entry_name])
        except py7zr.PasswordRequired:
            # 未提供密码或条目加密：标记后永久跳过
            self._encrypted_entries.add(entry_name)
            raise ArchiveError(f"加密条目未提供密码: {entry_name}") from None
        except py7zr.Bad7zFile as exc:
            # 损坏条目不可恢复，标记跳过避免重试
            self._encrypted_entries.add(entry_name)
            raise ArchiveError(f"7Z 条目损坏: {self._path}!{entry_name}: {exc}") from exc
        except (OSError, py7zr.UnsupportedCompressionMethodError) as exc:
            # 瞬时 IO 错误（AV 文件锁、网络盘抖动）不标记，允许上层重试；
            # 不支持的压缩方法不可恢复，标记跳过
            if isinstance(exc, py7zr.UnsupportedCompressionMethodError):
                self._encrypted_entries.add(entry_name)
                raise ArchiveError(f"不支持的 7Z 压缩方法: {entry_name}: {exc}") from exc
            raise ArchiveError(f"7Z 条目读取 IO 错误（可重试）: {entry_name}: {exc}") from exc
        except Exception as exc:
            # 密码错误等其他错误：标记为加密避免重试，降级为跳过
            logger.warning("7Z 条目读取失败，标记为加密跳过: %s!%s: %s", self._path, entry_name, exc)
            self._encrypted_entries.add(entry_name)
            raise ArchiveError(f"条目读取失败（密码错误或解压失败）: {entry_name}") from exc

        if data is None or entry_name not in data:
            return b""
        bio = data[entry_name]
        if bio is None:
            return b""
        try:
            return bio.read()
        finally:
            close = getattr(bio, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:  # pragma: no cover - 关闭异常无需上报
                    logger.debug("关闭 7Z 条目流失败: %s", entry_name, exc_info=True)

    @override
    def read_entry(self, entry_name: str) -> bytes:
        """读取条目内容（惰性解压）。

        每次创建新的 ``SevenZipFile`` 实例读取单个条目，避免多次调用 ``read()``
        触发 py7zr 死锁。已读取条目缓存到 ``_bytes_cache`` 避免重复解压。

        :raises ArchiveError: 读取失败（加密、损坏、找不到条目等）
        """
        info = self._info_map.get(entry_name)
        if info is None:
            raise ArchiveError(f"7Z 条目不存在: {entry_name}")
        if bool(getattr(info, "is_directory", False)):
            return b""
        # 已标记加密/损坏/不支持的条目直接跳过（避免重复解压浪费 CPU）
        if entry_name in self._encrypted_entries:
            logger.info("7Z 条目已标记不可读，跳过: %s!%s", self._path, entry_name)
            raise ArchiveError(f"条目不可读（加密/损坏/不支持）: {entry_name}")
        # 命中缓存直接返回
        cached = self._bytes_cache.get(entry_name)
        if cached is not None:
            return cached
        # 惰性读取并缓存
        content = self._lazy_read_entry(entry_name)
        self._bytes_cache[entry_name] = content
        return content

    @override
    def _close_resource(self) -> None:
        self._sevenz.close()

    @override
    def close(self) -> None:
        """关闭 7Z 文件句柄并释放字节缓存。

        覆盖基类：在基类 ``close`` 包装的 ``_close_resource`` 之外，额外清空
        ``_bytes_cache`` 释放大块内存（py7zr 惰性读取缓存的解压字节）。
        """
        super().close()
        # 释放字节缓存，避免长期持有大块内存
        self._bytes_cache.clear()
