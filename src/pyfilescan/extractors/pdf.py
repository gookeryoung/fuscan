"""PDF 提取器。

使用 pypdf 提取 PDF 文本，处理加密文档与扫描版文档。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Tuple

from pyfilescan.extractors.base import Extractor, ExtractorError

__all__ = ["PdfExtractor"]

logger = logging.getLogger(__name__)

# 抑制 pypdf 的 MediaBox 等重复定义警告（不影响文本提取）
logging.getLogger("pypdf").setLevel(logging.ERROR)


class PdfExtractor(Extractor):
    """PDF 文档文本提取器。"""

    @property
    def supported_extensions(self) -> Tuple[str, ...]:
        return ("pdf",)

    def extract(self, path: Path) -> str:
        try:
            from pypdf import PdfReader
            from pypdf.errors import PdfReadError
        except ImportError as exc:
            raise ExtractorError("pypdf 未安装，无法提取 PDF") from exc

        try:
            reader = PdfReader(str(path))
        except PdfReadError as exc:
            raise ExtractorError(f"PDF 解析失败: {path}: {exc}") from exc
        except Exception as exc:
            raise ExtractorError(f"PDF 打开失败: {path}: {exc}") from exc

        if reader.is_encrypted:
            logger.info("PDF 已加密，跳过: %s", path)
            return ""

        return self._extract_pages(reader)

    def _extract_pages(self, reader: object) -> str:
        """提取所有页面文本。"""
        parts = []
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
                if text:
                    parts.append(text)
            except Exception:
                logger.warning("PDF 页面提取失败", exc_info=True)
                continue
        return "\n".join(parts)
