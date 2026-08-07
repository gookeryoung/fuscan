"""Microsoft Office 文档提取器：DOCX 与 PPTX。

DOCX/PPTX 固定使用 lxml (libxml2 C 扩展) 直接解析 OOXML XML，
绕开 python-docx/python-pptx 的对象封装，性能提升 5-10x。
lxml 是运行时保证依赖（ODF/ODS/OOXML 主引擎），无需额外回退。

详见 :mod:`fuscan.extractors._ooxml_xml`。
"""

from __future__ import annotations

import logging
import zipfile

from typing_extensions import override

from fuscan.extractors._ooxml_xml import extract_docx_text, extract_pptx_text
from fuscan.extractors.base import Extractor, ExtractorError, SpeedTier

__all__ = ["DocxExtractor", "PptxExtractor"]

logger = logging.getLogger(__name__)


class DocxExtractor(Extractor):
    """DOCX 文档文本提取器。

    使用 lxml 直接解析 ``word/document.xml``、页眉页脚与表格，性能最优。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 DOCX 提取器支持的扩展名。"""
        return ("docx",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """lxml 直接解析 XML 为 T2 快速。"""
        return SpeedTier.FAST

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "Word（DOCX）"

    @override
    @property
    def engine_info(self) -> str:
        """DOCX 固定使用 lxml 解析引擎。"""
        return "lxml"

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节提取 DOCX 文本。"""
        try:
            return extract_docx_text(data)
        except zipfile.BadZipFile as exc:
            raise ExtractorError(f"DOCX 解析失败: {exc}") from exc


class PptxExtractor(Extractor):
    """PPTX 演示文稿文本提取器。

    使用 lxml 直接解析 ``ppt/slides/slideN.xml`` 与备注幻灯片，性能最优。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 PPTX 提取器支持的扩展名。"""
        return ("pptx",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """lxml 直接解析 XML 为 T2 快速。"""
        return SpeedTier.FAST

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "PowerPoint（PPTX）"

    @override
    @property
    def engine_info(self) -> str:
        """PPTX 固定使用 lxml 解析引擎。"""
        return "lxml"

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节提取 PPTX 文本。"""
        try:
            return extract_pptx_text(data)
        except zipfile.BadZipFile as exc:
            raise ExtractorError(f"PPTX 解析失败: {exc}") from exc
