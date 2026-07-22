"""文件内容提取器子包。

按文件扩展名分发到对应提取器，支持纯文本、PDF、DOCX、PPTX、XLSX、
ODS、ODT、WPS、RTF、EML、MSG、XLS、DOC、PPT 等格式。
提取器在 extract 方法内部懒加载第三方库依赖。

公共 API：

- :class:`Extractor`, :class:`ExtractorRegistry`, :class:`ExtractorError`
- :func:`get_extractor`, :func:`extract_content`
- :func:`extract_content_cached`（带 LRU 缓存的提取，GUI 预览用）
- :func:`clear_content_cache`（清空缓存，测试/扫描完成后调用）
- :data:`default_registry`
- 各格式提取器类
"""

from __future__ import annotations

from fuscan.extractors.base import (
    Extractor,
    ExtractorError,
    ExtractorRegistry,
    default_registry,
    extract_content,
    extract_content_from_bytes,
    extract_content_with_fallback,
    get_extractor,
)
from fuscan.extractors.cache import clear_content_cache, extract_content_cached
from fuscan.extractors.email import EmlExtractor, MsgExtractor
from fuscan.extractors.legacy_office import DocExtractor, PptExtractor, XlsExtractor
from fuscan.extractors.odf import OdtExtractor
from fuscan.extractors.office import DocxExtractor, PptxExtractor
from fuscan.extractors.pdf import PdfExtractor
from fuscan.extractors.registry import register_all
from fuscan.extractors.rtf import RtfExtractor
from fuscan.extractors.spreadsheet import OdsExtractor, XlsxExtractor
from fuscan.extractors.text import TEXT_EXTENSIONS, TextExtractor
from fuscan.extractors.wps import WpsExtractor

# 触发默认注册
register_all()

__all__ = [
    "TEXT_EXTENSIONS",
    "DocExtractor",
    "DocxExtractor",
    "EmlExtractor",
    "Extractor",
    "ExtractorError",
    "ExtractorRegistry",
    "MsgExtractor",
    "OdsExtractor",
    "OdtExtractor",
    "PdfExtractor",
    "PptExtractor",
    "PptxExtractor",
    "RtfExtractor",
    "TextExtractor",
    "WpsExtractor",
    "XlsExtractor",
    "XlsxExtractor",
    "clear_content_cache",
    "default_registry",
    "extract_content",
    "extract_content_cached",
    "extract_content_from_bytes",
    "extract_content_with_fallback",
    "get_extractor",
    "register_all",
]
