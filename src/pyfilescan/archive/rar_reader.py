"""RAR 压缩文件读取器。

基于 rarfile 第三方库实现，依赖系统 unrar 工具。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from pyfilescan.archive.base import ArchiveEntry, ArchiveError, ArchiveReader

__all__ = ["RarReader"]

logger = logging.getLogger(__name__)


class RarReader(ArchiveReader):
    """RAR 压缩包读取器。

    使用 rarfile 库读取 RAR 格式（需系统安装 unrar 工具）。
    加密条目需要密码；未提供密码或密码错误时跳过并记录。
    """

    def __init__(self, path: Path, password: Optional[str] = None) -> None:
        try:
            import rarfile  # 惰性导入，避免无 unrar 环境下的导入失败
        except ImportError as exc:
            raise ArchiveError("rarfile 库未安装，无法读取 RAR 文件") from exc

        self._path = path
        self._password = password
        try:
            self._rar = rarfile.RarFile(str(path))
        except rarfile.BadRarFile as exc:
            raise ArchiveError(f"损坏的 RAR 文件: {path}") from exc
        except rarfile.NotRarFile as exc:
            raise ArchiveError(f"不是 RAR 文件: {path}") from exc
        except OSError as exc:
            raise ArchiveError(f"无法打开 RAR 文件: {path}: {exc}") from exc
        except Exception as exc:  # unrar 工具缺失等情况
            raise ArchiveError(f"打开 RAR 文件失败（可能缺少 unrar 工具）: {path}: {exc}") from exc

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return ("rar",)

    def list_entries(self) -> List[ArchiveEntry]:
        """列出压缩包内所有条目。"""
        entries: List[ArchiveEntry] = []
        for info in self._rar.infolist():
            entries.append(
                ArchiveEntry(
                    archive_path=self._path,
                    entry_name=info.filename,
                    size=getattr(info, "file_size", 0),
                    compressed_size=getattr(info, "compress_size", 0),
                    is_dir=bool(getattr(info, "isdir", False)),
                )
            )
        return entries

    def read_entry(self, entry_name: str) -> bytes:
        """读取条目内容。

        :raises ArchiveError: 读取失败（加密、损坏、找不到条目等）
        """
        try:
            import rarfile
        except ImportError as exc:  # pragma: no cover - 构造时已校验
            raise ArchiveError("rarfile 库未安装") from exc

        try:
            info = self._rar.getinfo(entry_name)
        except KeyError as exc:
            raise ArchiveError(f"RAR 条目不存在: {entry_name}") from exc
        except Exception as exc:
            raise ArchiveError(f"获取 RAR 条目信息失败: {entry_name}: {exc}") from exc

        if getattr(info, "isdir", False):
            return b""

        # 加密条目处理
        needs_password = bool(getattr(info, "needs_password", False)) or self._password is not None
        if getattr(info, "needs_password", False) and self._password is None:
            logger.info("RAR 条目加密且未提供密码，跳过: %s!%s", self._path, entry_name)
            raise ArchiveError(f"加密条目未提供密码: {entry_name}")

        try:
            if needs_password and self._password:
                return self._rar.read(entry_name, pwd=self._password)
            return self._rar.read(entry_name)
        except rarfile.PasswordRequired as exc:
            raise ArchiveError(f"RAR 条目需要密码: {entry_name}: {exc}") from exc
        except rarfile.BadRarFile as exc:
            raise ArchiveError(f"RAR 条目损坏: {entry_name}: {exc}") from exc
        except Exception as exc:
            raise ArchiveError(f"RAR 条目读取失败: {entry_name}: {exc}") from exc

    def close(self) -> None:
        """关闭 RAR 文件句柄。"""
        try:
            self._rar.close()
        except Exception:  # pragma: no cover - 关闭异常无需上报
            logger.debug("关闭 RAR 文件句柄失败: %s", self._path, exc_info=True)

    def __enter__(self) -> RarReader:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
