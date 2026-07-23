"""提取器默认注册表。

将所有内置提取器注册到 default_registry。
提取器实例化是轻量的（不 import 第三方库），可安全地在模块导入时执行。

iter-88 起将原 ``TextExtractor``（57 个扩展名）拆分为 5 个子提取器，
各自注册到默认注册表，``TextExtractor`` 本身不再注册（保留为基类）。
"""

from __future__ import annotations

from fuscan.extractors.base import default_registry
from fuscan.extractors.email import EmlExtractor, MsgExtractor
from fuscan.extractors.legacy_office import DocExtractor, PptExtractor, XlsExtractor
from fuscan.extractors.odf import OdtExtractor
from fuscan.extractors.office import DocxExtractor, PptxExtractor
from fuscan.extractors.pdf import PdfExtractor
from fuscan.extractors.rtf import RtfExtractor
from fuscan.extractors.spreadsheet import OdsExtractor, XlsxExtractor
from fuscan.extractors.text import (
    ConfigFileExtractor,
    MarkupDataExtractor,
    PlainTextExtractor,
    SourceCodeExtractor,
    StylesheetExtractor,
)
from fuscan.extractors.wps import WpsExtractor

__all__ = ["default_registry", "register_all"]


def register_all() -> None:
    """注册所有内置提取器到 default_registry。

    幂等：重复调用安全，已注册的扩展名会被相同实例覆盖。

    iter-88：原 ``TextExtractor`` 拆分为 5 个子提取器（纯文本/源代码/配置文件/
    标记与数据/样式表），各自注册独立扩展名子集，GUI 勾选树按分类展示。
    """
    # 纯文本子提取器（iter-88 拆分）
    default_registry.register(PlainTextExtractor())
    default_registry.register(SourceCodeExtractor())
    default_registry.register(ConfigFileExtractor())
    default_registry.register(MarkupDataExtractor())
    default_registry.register(StylesheetExtractor())
    # 文档格式提取器
    default_registry.register(PdfExtractor())
    default_registry.register(DocxExtractor())
    default_registry.register(PptxExtractor())
    default_registry.register(XlsxExtractor())
    default_registry.register(OdsExtractor())
    default_registry.register(OdtExtractor())
    default_registry.register(WpsExtractor())
    default_registry.register(RtfExtractor())
    default_registry.register(EmlExtractor())
    default_registry.register(MsgExtractor())
    default_registry.register(XlsExtractor())
    default_registry.register(DocExtractor())
    default_registry.register(PptExtractor())


# 模块导入时自动注册
register_all()
