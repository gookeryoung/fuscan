"""文件内容提取器子包。

按文件扩展名分发到对应提取器，支持纯文本、PDF、DOCX、PPTX、XLSX、
ODS、ODT、WPS、EML、XLS、DOC、PPT 等格式。
提取器在 extract 方法内部懒加载第三方库依赖。

原 ``ConfigFileExtractor``/``MarkupDataExtractor``/``StylesheetExtractor``
的扩展名合并到 :class:`SourceCodeExtractor`，文本类别仅注册「纯文本」「源代码」两项。

:class:`ExtractorRegistry` 提供带重试的提取方法
（``extract_from_bytes_with_retry`` / ``extract_with_retry``），
对瞬时 ``OSError`` 执行退避重试；:class:`ExtractorFailure` 聚合诊断信息。

公共 API：

- :class:`Extractor`, :class:`ExtractorRegistry`, :class:`ExtractorError`,
  :class:`ExtractorFailure`
- :func:`get_extractor`, :func:`extract_content`
- :func:`extract_content_cached`（带 LRU 缓存的提取，GUI 预览用）
- :func:`clear_content_cache`（清空缓存，测试/扫描完成后调用）
- :func:`extract_content_from_bytes_with_retry`（带重试的内存字节提取）
- :func:`extract_content_with_fallback_and_retry`（带重试+回退的提取）
- :func:`is_retriable_error`（判断异常是否可重试）
- :data:`default_registry`
- 各格式提取器类
"""

from __future__ import annotations

from fuscan.extractors.base import (
    Extractor,
    ExtractorError,
    ExtractorFailure,
    ExtractorRegistry,
    SpeedTier,
    default_registry,
    extract_content,
    extract_content_from_bytes,
    extract_content_from_bytes_with_retry,
    extract_content_with_fallback,
    extract_content_with_fallback_and_retry,
    get_extractor,
    get_last_extract_engine,
    is_retriable_error,
    reset_last_extract_engine,
)
from fuscan.extractors.cache import clear_content_cache, extract_content_cached
from fuscan.extractors.email import EmlExtractor
from fuscan.extractors.image import ImageExtractor
from fuscan.extractors.legacy_office import DocExtractor, PptExtractor, XlsExtractor
from fuscan.extractors.odf import OdtExtractor
from fuscan.extractors.office import DocxExtractor, PptxExtractor
from fuscan.extractors.pdf import PdfExtractor
from fuscan.extractors.registry import register_all
from fuscan.extractors.spreadsheet import OdsExtractor, XlsxExtractor
from fuscan.extractors.text import (
    PLAIN_TEXT_EXTENSIONS,
    SOURCE_CODE_EXTENSIONS,
    TEXT_EXTENSIONS,
    PlainTextExtractor,
    SourceCodeExtractor,
    TextExtractor,
)
from fuscan.extractors.wps import WpsExtractor

# 触发默认注册
register_all()

__all__ = [
    "PLAIN_TEXT_EXTENSIONS",
    "SOURCE_CODE_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "DocExtractor",
    "DocxExtractor",
    "EmlExtractor",
    "Extractor",
    "ExtractorError",
    "ExtractorFailure",
    "ExtractorRegistry",
    "ImageExtractor",
    "OdsExtractor",
    "OdtExtractor",
    "PdfExtractor",
    "PlainTextExtractor",
    "PptExtractor",
    "PptxExtractor",
    "SourceCodeExtractor",
    "SpeedTier",
    "TextExtractor",
    "WpsExtractor",
    "XlsExtractor",
    "XlsxExtractor",
    "clear_content_cache",
    "default_registry",
    "extract_content",
    "extract_content_cached",
    "extract_content_from_bytes",
    "extract_content_from_bytes_with_retry",
    "extract_content_with_fallback",
    "extract_content_with_fallback_and_retry",
    "get_extractor",
    "get_last_extract_engine",
    "is_retriable_error",
    "register_all",
    "reset_last_extract_engine",
]
