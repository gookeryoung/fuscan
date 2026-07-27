"""旧版 Microsoft Office 提取器：XLS、DOC、PPT。

XLS 通过 calamine（Rust + PyO3）读取 Excel 97-2003 工作簿，与 XLSX
共用同一 Rust 后端（``_extract_calamine_workbook``）。DOC/PPT 仍使用 olefile
读取 OLE 复合文档，从文本流中提取 UTF-16LE 编码内容。

iter-126：DOC/PPT 在 kreuzberg 可用时优先使用 Rust 核心加速提取（T2 快速），
不可用时回退到 olefile + UTF-16LE 正则扫描（T3 中速）。kreuzberg 仅支持
文件路径提取，``extract_from_bytes``（压缩包内条目）仍走 olefile。

注意：DOC/PPT 为二进制格式，本提取器仅做简单文本提取，不支持复杂格式
（如修订、嵌入对象等）。如需完整提取，建议先转换为 DOCX/PPTX。
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

from typing_extensions import override

from fuscan.extractors._kreuzberg import extract_text as kreuzberg_extract
from fuscan.extractors._kreuzberg import is_available as kreuzberg_available
from fuscan.extractors.base import Extractor, ExtractorError, SpeedTier

__all__ = ["DocExtractor", "PptExtractor", "XlsExtractor"]

logger = logging.getLogger(__name__)

# UTF-16LE 可打印字符的字节模式（小端序，低字节在前）：
# - ASCII 可打印（U+0020-U+007E）：低字节 [\x20-\x7E]，高字节 \x00
# - CJK 统一汉字（U+4E00-U+9FFF）：低字节任意，高字节 [\x4E-\x9F]
# - 全角标点（U+3000-U+30FF）：低字节任意，高字节 \x30
# 连续 2 个以上可打印字符构成一个文本片段
_UTF16LE_RUN = re.compile(rb"(?:[\x20-\x7E]\x00|[\x00-\xFF][\x4E-\x9F]|[\x00-\xFF]\x30){2,}")


def _extract_utf16le_text(data: bytes) -> str:
    """从二进制数据中提取 UTF-16LE 编码的文本片段。

    用正则 ``re.finditer`` 一次性扫描字节流，匹配连续的可打印 UTF-16LE
    字符序列（ASCII + CJK 汉字 + 全角标点），跳过不可打印的控制字符。
    相比逐字节 Python 循环，正则引擎在 C 层完成匹配，性能提升 3-5x。

    :param data: 二进制流内容
    :return: 提取的纯文本，片段以换行分隔
    """
    if len(data) < 2:
        return ""

    parts: list[str] = []
    for match in _UTF16LE_RUN.finditer(data):
        try:
            text = match.group().decode("utf-16-le").strip()
        except UnicodeDecodeError:
            continue
        if len(text) >= 2:
            parts.append(text)

    return "\n".join(parts)


class XlsExtractor(Extractor):
    """XLS (Excel 97-2003) 工作簿文本提取器。

    iter-92 起切换到 calamine (Rust + PyO3) 后端，从 T4 慢速降至 T2 快速，
    与 XLSX/ODS 共用同一 Rust 后端。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 XLS 提取器支持的扩展名。"""
        return ("xls",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """calamine (Rust + PyO3) 释放 GIL，T2 快速。"""
        return SpeedTier.FAST

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "Excel（XLS）"

    @override
    def extract(self, path: Path) -> str:
        """提取 XLS 工作表单元格文本。"""
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc
        return self.extract_from_bytes(data)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节解析 XLS 工作簿。"""
        from fuscan.extractors.spreadsheet import _extract_calamine_workbook

        return _extract_calamine_workbook(data, error_label="XLS")


class DocExtractor(Extractor):
    """DOC (Word 97-2003) 文档文本提取器。

    通过 olefile 读取 OLE 复合文档中的 WordDocument 流，提取 UTF-16LE
    编码的文本。仅做简单文本提取，不解析复杂格式。

    iter-126：kreuzberg 可用时优先使用 Rust 核心加速提取（T2 快速），
    不可用时回退到 olefile + UTF-16LE 正则扫描（T3 中速）。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 DOC 提取器支持的扩展名。"""
        return ("doc",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """kreuzberg 可用时 T2 快速（Rust 核心），否则 T3 中速（olefile）。"""
        if kreuzberg_available():
            return SpeedTier.FAST
        return SpeedTier.MEDIUM

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "Word（DOC）"

    @override
    def extract(self, path: Path) -> str:
        """提取 DOC 文档文本。

        kreuzberg 可用时优先使用 Rust 核心加速提取；不可用时回退到 olefile。
        """
        if kreuzberg_available():
            try:
                return kreuzberg_extract(path)
            except RuntimeError as exc:
                logger.debug("kreuzberg DOC 提取失败，回退到 olefile: %s: %s", path, exc)
        # 回退：olefile + UTF-16LE 正则扫描
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc
        return self.extract_from_bytes(data)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节解析 DOC 文档（olefile 回退路径）。

        .. note::
           kreuzberg 仅支持文件路径提取，``extract_from_bytes`` 始终走 olefile。
           压缩包内条目通过临时文件走 :meth:`extract` 时才会用 kreuzberg。
        """
        try:
            import olefile
        except ImportError as exc:
            raise ExtractorError("olefile 未安装，无法提取 DOC") from exc

        try:
            ole = olefile.OleFileIO(io.BytesIO(data))
        except Exception as exc:
            raise ExtractorError(f"DOC 解析失败: {exc}") from exc

        try:
            if ole.exists("WordDocument"):
                stream = ole.openstream("WordDocument")
                return _extract_utf16le_text(stream.read())
            logger.debug("DOC 文件无 WordDocument 流")
            return ""
        finally:
            ole.close()


class PptExtractor(Extractor):
    """PPT (PowerPoint 97-2003) 演示文稿文本提取器。

    通过 olefile 读取 OLE 复合文档中的 PowerPoint Document 流，提取
    UTF-16LE 编码的文本。仅做简单文本提取，不解析幻灯片结构。

    iter-126：kreuzberg 可用时优先使用 Rust 核心加速提取（T2 快速），
    不可用时回退到 olefile + UTF-16LE 正则扫描（T3 中速）。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 PPT 提取器支持的扩展名。"""
        return ("ppt",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """kreuzberg 可用时 T2 快速（Rust 核心），否则 T3 中速（olefile）。"""
        if kreuzberg_available():
            return SpeedTier.FAST
        return SpeedTier.MEDIUM

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "PowerPoint（PPT）"

    @override
    def extract(self, path: Path) -> str:
        """提取 PPT 演示文稿文本。

        kreuzberg 可用时优先使用 Rust 核心加速提取；不可用时回退到 olefile。
        """
        if kreuzberg_available():
            try:
                return kreuzberg_extract(path)
            except RuntimeError as exc:
                logger.debug("kreuzberg PPT 提取失败，回退到 olefile: %s: %s", path, exc)
        # 回退：olefile + UTF-16LE 正则扫描
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc
        return self.extract_from_bytes(data)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节解析 PPT 演示文稿（olefile 回退路径）。

        .. note::
           kreuzberg 仅支持文件路径提取，``extract_from_bytes`` 始终走 olefile。
           压缩包内条目通过临时文件走 :meth:`extract` 时才会用 kreuzberg。
        """
        try:
            import olefile
        except ImportError as exc:
            raise ExtractorError("olefile 未安装，无法提取 PPT") from exc

        try:
            ole = olefile.OleFileIO(io.BytesIO(data))
        except Exception as exc:
            raise ExtractorError(f"PPT 解析失败: {exc}") from exc

        try:
            if ole.exists("PowerPoint Document"):
                stream = ole.openstream("PowerPoint Document")
                return _extract_utf16le_text(stream.read())
            logger.debug("PPT 文件无 PowerPoint Document 流")
            return ""
        finally:
            ole.close()
