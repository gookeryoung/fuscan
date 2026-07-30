"""OpenDocument 文档提取器：ODT 文字文档。

iter-109 起改用 ``zipfile`` + ``lxml`` 直接解析 ODT 的 ``content.xml``，
移除 odfpy 依赖（odfpy 在 PyPI 上仅有 sdist，无预编译 wheel，与 fspack
的 ``--only-binary=:all:`` 打包策略冲突）。lxml 基于 libxml2 C 扩展，
比 ElementTree 快 3-5x；lxml 不可用时自动回退到 ElementTree。
"""

from __future__ import annotations

import logging
from pathlib import Path

from typing_extensions import override

from fuscan.extractors._odf_xml import element_text, iter_text_paragraphs, load_content_xml
from fuscan.extractors.base import Extractor, ExtractorError, SpeedTier

__all__ = ["OdtExtractor"]

logger = logging.getLogger(__name__)


class OdtExtractor(Extractor):
    """ODT 文字文档文本提取器。

    用 ``zipfile`` 解压 ODT 包，``lxml`` 解析 ``content.xml`` 中的
    ``<text:p>`` 段落与 ``<text:h>`` 标题，递归提取元素文本。
    无需 odfpy 依赖。lxml 不可用时回退到 ElementTree。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 ODT 提取器支持的扩展名。"""
        return ("odt",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """lxml 解析 XML 为 T2 快速；回退 ElementTree 为 T3 中速。"""
        from fuscan.extractors._odf_xml import _LXML_AVAILABLE

        return SpeedTier.FAST if _LXML_AVAILABLE else SpeedTier.MEDIUM

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "ODT 文档"

    @override
    @property
    def engine_info(self) -> str:
        """iter-139：lxml 可用时优先使用，回退 ElementTree。"""
        from fuscan.extractors._odf_xml import _LXML_AVAILABLE

        return "lxml" if _LXML_AVAILABLE else "ElementTree"

    @override
    def extract(self, path: Path) -> str:
        """提取 ODT 文档的段落与标题文本。"""
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc
        return self.extract_from_bytes(data)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节提取 ODT 文档文本。

        :param data: ODT 文件完整字节内容（ZIP 格式）
        :return: 段落与标题文本以 ``\\n`` 分隔
        :raises ExtractorError: ZIP 解压失败、content.xml 缺失或 XML 解析失败
        """
        try:
            root = load_content_xml(data)
        except KeyError as exc:
            raise ExtractorError(f"ODT 解析失败: 缺少 content.xml: {exc}") from exc
        except Exception as exc:
            # BadZipFile / ParseError / ET.ParseError 等统一包装
            raise ExtractorError(f"ODT 解析失败: {exc}") from exc

        parts: list[str] = []
        for paragraph in iter_text_paragraphs(root):
            text = element_text(paragraph)
            if text:
                parts.append(text)

        return "\n".join(parts)
