"""旧版 Microsoft Office 提取器：XLS、DOC、PPT。

XLS 通过 calamine（Rust + PyO3）读取 Excel 97-2003 工作簿，与 XLSX
共用同一 Rust 后端（``_extract_calamine_workbook``）。DOC/PPT 使用 olefile
读取 OLE 复合文档，从文本流中提取 UTF-16LE 编码内容（T3 中速）。

注意：DOC/PPT 为二进制格式，本提取器仅做简单文本提取，不支持复杂格式
（如修订、嵌入对象等）。如需完整提取，建议先转换为 DOCX/PPTX。
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

from typing_extensions import override

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


def _extract_ole_text(data: bytes, stream_name: str, error_label: str) -> str:
    """从 OLE 复合文档中提取指定流的 UTF-16LE 文本。

    统一 :class:`DocExtractor` 与 :class:`PptExtractor` 的 OLE 解析逻辑：
    打开 OLE 复合文档 → 检查指定流是否存在 → 读取流内容 → UTF-16LE 正则扫描。
    无指定流时返回空字符串（部分老版本文档结构差异）。

    :param data: OLE 复合文档字节内容
    :param stream_name: 流名称（如 ``"WordDocument"`` / ``"PowerPoint Document"``）
    :param error_label: 错误信息前缀（如 ``"DOC"`` / ``"PPT"``）
    :return: 提取的文本；无指定流返回空字符串
    :raises ExtractorError: olefile 未安装或 OLE 解析失败
    """
    try:
        import olefile
    except ImportError as exc:
        raise ExtractorError(f"olefile 未安装，无法提取 {error_label}") from exc

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception as exc:
        raise ExtractorError(f"{error_label} 解析失败: {exc}") from exc

    try:
        if ole.exists(stream_name):
            stream = ole.openstream(stream_name)
            return _extract_utf16le_text(stream.read())
        logger.debug("%s 文件无 %s 流", error_label, stream_name)
        return ""
    finally:
        ole.close()


class XlsExtractor(Extractor):
    """XLS (Excel 97-2003) 工作簿文本提取器。

    切换到 calamine (Rust + PyO3) 后端，从 T4 慢速降至 T2 快速，
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
    @property
    def engine_info(self) -> str:
        """python-calamine (Rust + PyO3)。"""
        return "python-calamine"

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
    编码的文本（T3 中速）。仅做简单文本提取，不解析复杂格式。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 DOC 提取器支持的扩展名。"""
        return ("doc",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """olefile + UTF-16LE 正则扫描，T3 中速。"""
        return SpeedTier.MEDIUM

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "Word（DOC）"

    @override
    @property
    def engine_info(self) -> str:
        """DOC 固定使用 olefile 解析引擎。"""
        return "olefile"

    @override
    def extract(self, path: Path) -> str:
        """提取 DOC 文档文本（olefile + UTF-16LE 正则扫描）。"""
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc
        return self.extract_from_bytes(data)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节解析 DOC 文档（olefile + UTF-16LE 正则扫描）。"""
        return _extract_ole_text(data, stream_name="WordDocument", error_label="DOC")


class PptExtractor(Extractor):
    """PPT (PowerPoint 97-2003) 演示文稿文本提取器。

    通过 olefile 读取 OLE 复合文档中的 PowerPoint Document 流，提取
    UTF-16LE 编码的文本（T3 中速）。仅做简单文本提取，不解析幻灯片结构。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 PPT 提取器支持的扩展名。"""
        return ("ppt",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """olefile + UTF-16LE 正则扫描，T3 中速。"""
        return SpeedTier.MEDIUM

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "PowerPoint（PPT）"

    @override
    @property
    def engine_info(self) -> str:
        """PPT 固定使用 olefile 解析引擎。"""
        return "olefile"

    @override
    def extract(self, path: Path) -> str:
        """提取 PPT 演示文稿文本（olefile + UTF-16LE 正则扫描）。"""
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc
        return self.extract_from_bytes(data)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节解析 PPT 演示文稿（olefile + UTF-16LE 正则扫描）。"""
        return _extract_ole_text(data, stream_name="PowerPoint Document", error_label="PPT")
