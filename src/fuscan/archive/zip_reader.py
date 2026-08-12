"""ZIP 压缩文件读取器。

基于标准库 zipfile 实现，支持加密压缩包（密码尝试）与损坏压缩包容错。

文件名编码修复：Windows 压缩工具（WinRAR/好压/360）默认用 GBK 编码中文
文件名且不设置 UTF-8 标志位（flag_bits 0x800），导致 zipfile 按 CP437
解码产生乱码（如 ``密码.txt`` → ``├▄┬δ.txt``），使下游 FILENAME/PATH
正则规则与扩展名白名单判断全部失效。``list_entries`` 对未设置 UTF-8
标志位的条目按 UTF-8 → GBK 回退解码，并通过 ``_entry_key_map`` 维持
展示名到 zipfile 内部原始键的映射，供 :meth:`read_entry` 回查。
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from typing_extensions import override

from fuscan.archive.base import ArchiveEntry, ArchiveError, ArchiveReader

__all__ = ["ZipReader"]

logger = logging.getLogger(__name__)

# ZIP general purpose bit flag 第 11 位（0x800）：文件名按 UTF-8 编码。
# 未设置时 zipfile 按 CP437 解码，Windows 中文压缩工具默认用 GBK 且不设置此位。
_UTF8_FLAG = 0x800


class ZipReader(ArchiveReader):
    """ZIP 压缩包读取器。

    使用 zipfile.ZipFile 读取标准 ZIP 格式（含 zip/gzip 场景下的常规 zip）。
    加密条目需要密码；未提供密码或密码错误时跳过并记录。
    """

    def __init__(self, path: Path, password: str | None = None) -> None:
        super().__init__(path)
        self._password = password.encode("utf-8") if password else None
        try:
            self._zip = zipfile.ZipFile(str(path), mode="r")
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"损坏的 ZIP 文件: {path}") from exc
        except OSError as exc:
            raise ArchiveError(f"无法打开 ZIP 文件: {path}: {exc}") from exc
        # 展示名 → zipfile 内部原始键映射。
        # 未设置 UTF-8 标志位的条目，list_entries 返回解码后的展示名，
        # 但 zipfile.getinfo/read 仍期望原始 CP437 乱码名作为查询键，
        # 故 read_entry 通过本映射回查原始键，避免 KeyError。
        self._entry_key_map: dict[str, str] = {}

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """支持的压缩文件扩展名。"""
        return ("zip",)

    @override
    def list_entries(self) -> list[ArchiveEntry]:
        """列出压缩包内所有条目（目录与文件均列出）。

        针对未设置 UTF-8 标志位的条目，``entry_name`` 返回解码后的正确文件名，
        并登记到 ``_entry_key_map`` 供 :meth:`read_entry` 回查 zipfile 内部键。
        """
        self._entry_key_map.clear()
        entries: list[ArchiveEntry] = []
        for info in self._zip.infolist():
            display_name = _decode_zip_filename(info)
            # 无论是否发生过解码都登记，保持映射完备；read_entry 无需区分两种情况
            self._entry_key_map[display_name] = info.filename
            entries.append(
                ArchiveEntry(
                    archive_path=self._path,
                    entry_name=display_name,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    is_dir=info.is_dir(),
                )
            )
        return entries

    @override
    def read_entry(self, entry_name: str) -> bytes:
        """读取条目内容。

        :raises ArchiveError: 读取失败（加密、损坏、找不到条目等）
        """
        # 将展示名映射回 zipfile 内部原始键（未设置 UTF-8 标志位时为 CP437 乱码名）
        raw_name = self._entry_key_map.get(entry_name, entry_name)
        try:
            info = self._zip.getinfo(raw_name)
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
                return self._zip.read(raw_name, pwd=self._password)
            except RuntimeError as exc:
                raise ArchiveError(f"ZIP 密码错误或解密失败: {entry_name}: {exc}") from exc

        try:
            return self._zip.read(raw_name)
        except RuntimeError as exc:
            raise ArchiveError(f"ZIP 条目读取失败: {entry_name}: {exc}") from exc
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"ZIP 条目损坏: {entry_name}: {exc}") from exc

    @override
    def _close_resource(self) -> None:
        self._zip.close()


def _decode_zip_filename(info: zipfile.ZipInfo) -> str:
    """解码 ZIP 条目文件名，修复未设置 UTF-8 标志位的中文乱码。

    ZIP 规范：``flag_bits`` 第 11 位（0x800）设置时文件名按 UTF-8 编码，
    否则按 CP437 编码。但 Windows 上的压缩工具（WinRAR/好压/360 等）默认
    用 GBK 编码中文文件名且不设置 UTF-8 标志位，导致 Python zipfile 按
    CP437 解码产生乱码（如 ``密码.txt`` → ``├▄┬δ.txt``），使下游扩展名判断
    与 FILENAME/PATH 正则规则全部失效。

    本函数检测未设置 UTF-8 标志位的情况，将文件名编码回 CP437 字节后
    依次尝试 UTF-8 → GBK 解码，全部失败则保留原始字符串（尽力而为）。
    纯 ASCII 文件名不受影响（CP437 与 ASCII 兼容，UTF-8 解码即成功）。
    """
    if info.flag_bits & _UTF8_FLAG:
        return info.filename
    try:
        raw_bytes = info.filename.encode("cp437")
    except (UnicodeEncodeError, LookupError):
        # filename 含 CP437 不支持的字符（zipfile 已按其他编码解码），保留原样
        return info.filename
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw_bytes.decode("gbk")
    except UnicodeDecodeError:
        return info.filename
