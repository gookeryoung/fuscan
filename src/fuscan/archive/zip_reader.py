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
    依次尝试 UTF-8 → GBK 解码，并通过 :func:`_looks_like_real_filename`
    校验解码结果合理性后采用，全部失败则保留原始字符串（尽力而为）。

    合理性校验的必要性：GBK 双字节字符的两个字节都落在 0x80-0xFF，恰好
    落在 UTF-8 2 字节序列的合法范围内（0xC2-0xDF + 0x80-0xBF），故 GBK
    字节序列可能被 UTF-8 "成功"解码为亚美尼亚/希伯来等非 CJK 字符
    （如 ``凭证`` 的 GBK 字节 ``c6 be d6 a4`` 会被 UTF-8 解码为 ``ƾ֤``）。
    仅当解码结果含 CJK 字符或常见拉丁补充字符时才采用，避免此类误判。
    """
    if info.flag_bits & _UTF8_FLAG:
        return info.filename
    try:
        raw_bytes = info.filename.encode("cp437")
    except (UnicodeEncodeError, LookupError):
        # filename 含 CP437 不支持的字符（zipfile 已按其他编码解码），保留原样
        return info.filename
    # 优先 UTF-8：解码成功且结果通过合理性校验才采用
    try:
        utf8_decoded = raw_bytes.decode("utf-8")
        if _looks_like_real_filename(utf8_decoded):
            return utf8_decoded
    except UnicodeDecodeError:
        pass
    # 回退 GBK（Windows 中文压缩工具默认）：同样需通过合理性校验
    try:
        gbk_decoded = raw_bytes.decode("gbk")
        if _looks_like_real_filename(gbk_decoded):
            return gbk_decoded
    except UnicodeDecodeError:
        pass
    return info.filename


def _looks_like_real_filename(text: str) -> bool:
    """判断解码结果是否像真实文件名（含 CJK/拉丁补充/全角或全 ASCII）。

    GBK 双字节字符的字节序列可能被 UTF-8 误判解码为亚美尼亚/希伯来/希腊等
    罕见于文件名的字符，本函数通过要求非 ASCII 字符必须落在常见文件名字符
    范围来过滤此类误判。

    允许的字符范围：

    - ASCII（``\\x00-\\x7F``）：文件名主体
    - 拉丁补充（``U+0080-U+00FF``）：如 ``café.txt`` 的 ``é``
    - CJK 统一表意文字（``U+4E00-U+9FFF``）与扩展 A（``U+3400-U+4DBF``）
    - CJK 标点（``U+3000-U+303F``）与全角字符（``U+FF00-U+FFEF``）

    拒绝亚美尼亚（``U+0530-U+058F``）、希伯来（``U+0590-U+05FF``）、
    希腊（``U+0370-U+03FF``）、西里尔（``U+0400-U+04FF``）等不常见于
    中文文件名字符，使 GBK 字节误判回退到 GBK 解码。
    """
    for ch in text:
        if ch.isascii():
            continue
        cp = ord(ch)
        if (
            0x0080 <= cp <= 0x00FF  # 拉丁补充（café 等）
            or 0x3000 <= cp <= 0x303F  # CJK 标点
            or 0x3400 <= cp <= 0x4DBF  # CJK 扩展 A
            or 0x4E00 <= cp <= 0x9FFF  # CJK 统一表意文字
            or 0xFF00 <= cp <= 0xFFEF  # 全角字符
        ):
            continue
        return False
    return True
