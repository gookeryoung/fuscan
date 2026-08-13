"""提取器默认注册表。

将所有内置提取器注册到 default_registry。
提取器实例化是轻量的（不 import 第三方库），可安全地在模块导入时执行。

原 ``ConfigFileExtractor``/``MarkupDataExtractor``/``StylesheetExtractor``
的扩展名合并到 :class:`SourceCodeExtractor`，文本类别仅注册「纯文本」「源代码」两项。
``TextExtractor`` 本身不再注册（保留为基类）。
"""

from __future__ import annotations

from fuscan.extractors.base import default_registry
from fuscan.extractors.email import EmlExtractor
from fuscan.extractors.image import ImageExtractor
from fuscan.extractors.legacy_office import DocExtractor, PptExtractor, XlsExtractor
from fuscan.extractors.odf import OdtExtractor
from fuscan.extractors.office import DocxExtractor, PptxExtractor
from fuscan.extractors.pdf import PdfExtractor
from fuscan.extractors.spreadsheet import OdsExtractor, XlsxExtractor
from fuscan.extractors.text import PlainTextExtractor, SourceCodeExtractor
from fuscan.extractors.wps import WpsExtractor

__all__ = ["default_registry", "register_all"]


def register_all() -> None:
    """注册所有内置提取器到 default_registry。

    幂等：重复调用安全，已注册的扩展名会被相同实例覆盖。

    文本类别仅注册「纯文本」「源代码」两项，原 ConfigFile/MarkupData/
    Stylesheet 三类子提取器的扩展名合并到 SourceCodeExtractor，避免 GUI 勾选树过度细分。
    """
    # 文本子提取器（合并后仅两项）
    default_registry.register(PlainTextExtractor())
    default_registry.register(SourceCodeExtractor())
    # 文档格式提取器
    default_registry.register(PdfExtractor())
    default_registry.register(DocxExtractor())
    default_registry.register(PptxExtractor())
    default_registry.register(XlsxExtractor())
    default_registry.register(OdsExtractor())
    default_registry.register(OdtExtractor())
    default_registry.register(WpsExtractor())
    default_registry.register(EmlExtractor())
    default_registry.register(XlsExtractor())
    default_registry.register(DocExtractor())
    default_registry.register(PptExtractor())
    # 图片 OCR 提取器（T5 极慢，默认不勾选）
    default_registry.register(ImageExtractor())


# 模块导入时自动注册
register_all()
