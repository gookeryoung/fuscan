"""文件内容提取器子包。

按文件扩展名分发到对应提取器，支持纯文本、PDF、DOCX、PPTX、XLSX、
ODS、ODT、WPS 等格式。提取器在 extract 方法内部懒加载第三方库依赖。

公共 API：

- :class:`Extractor`, :class:`ExtractorRegistry`, :class:`ExtractorError`
- :func:`get_extractor`, :func:`extract_content`
- :data:`default_registry`
- 各格式提取器类
"""

from __future__ import annotations

from uniscan.extractors.base import (
    Extractor,
    ExtractorError,
    ExtractorRegistry,
    default_registry,
    extract_content,
    get_extractor,
)
from uniscan.extractors.odf import OdtExtractor
from uniscan.extractors.office import DocxExtractor, PptxExtractor
from uniscan.extractors.pdf import PdfExtractor
from uniscan.extractors.registry import register_all
from uniscan.extractors.spreadsheet import OdsExtractor, XlsxExtractor
from uniscan.extractors.text import TEXT_EXTENSIONS, TextExtractor
from uniscan.extractors.wps import WpsExtractor

# 触发默认注册
register_all()

__all__ = [
    "TEXT_EXTENSIONS",
    "DocxExtractor",
    "Extractor",
    "ExtractorError",
    "ExtractorRegistry",
    "OdsExtractor",
    "OdtExtractor",
    "PdfExtractor",
    "PptxExtractor",
    "TextExtractor",
    "WpsExtractor",
    "XlsxExtractor",
    "default_registry",
    "extract_content",
    "get_extractor",
    "register_all",
]
