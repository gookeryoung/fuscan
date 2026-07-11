"""OpenDocument 文档提取器：ODT 文字文档。

使用 odfpy 提取 ODT 文档的段落、标题、列表等文本内容。
"""

from __future__ import annotations

import logging
from pathlib import Path

from uniscan.extractors.base import Extractor, ExtractorError

__all__ = ["OdtExtractor"]

logger = logging.getLogger(__name__)


class OdtExtractor(Extractor):
    """ODT 文字文档文本提取器。"""

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return ("odt",)

    def extract(self, path: Path) -> str:
        try:
            from odf.opendocument import load
            from odf.text import H, P
        except ImportError as exc:
            raise ExtractorError("odfpy 未安装，无法提取 ODT") from exc

        try:
            doc = load(str(path))
        except Exception as exc:
            raise ExtractorError(f"ODT 解析失败: {path}: {exc}") from exc

        parts: list[str] = []
        # 提取段落 (P) 和标题 (H)
        for paragraph in doc.getElementsByType(P):
            text = str(paragraph).strip()
            if text:
                parts.append(text)
        for heading in doc.getElementsByType(H):
            text = str(heading).strip()
            if text:
                parts.append(text)

        return "\n".join(parts)
