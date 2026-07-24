"""PDF 提取器。

iter-91：优先使用 ``pdf_oxide``（Rust + PyO3，0.8ms/文档，释放 GIL），
import 失败时回退到 ``pypdf``（纯 Python，12.1ms/文档）。

``pdf_oxide`` 优势：
- 15 倍于 pypdf 的提取速度
- Rust 核心执行期间释放 GIL，不饿死主线程
- 100% 通过率（3,830 个测试 PDF）
- MIT/Apache-2.0 许可证
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from typing_extensions import override

from fuscan.extractors.base import Extractor, ExtractorError, SpeedTier

__all__ = ["PdfExtractor"]

logger = logging.getLogger(__name__)

# 抑制 pypdf 的 MediaBox 等重复定义警告（不影响文本提取）
logging.getLogger("pypdf").setLevel(logging.ERROR)

# 模块级检测 pdf_oxide 是否可用（仅 import 一次）
try:
    from pdf_oxide import PdfDocument as _PdfOxideDocument

    _PDF_OXIDE_AVAILABLE = True
except ImportError:  # pragma: no cover - 环境依赖：仅 pdf_oxide 未安装时执行
    _PDF_OXIDE_AVAILABLE = False
    _PdfOxideDocument = None  # type: ignore[assignment, unused-ignore]


class PdfExtractor(Extractor):
    """PDF 文档文本提取器。

    优先使用 ``pdf_oxide``（Rust + PyO3），回退到 ``pypdf``。
    :attr:`speed_tier` 根据可用后端动态返回：``pdf_oxide`` → T2 快速，
    ``pypdf`` → T5 极慢。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 PDF 提取器支持的扩展名。"""
        return ("pdf",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """PDF 提取速度档次：pdf_oxide 可用时 T2 快速，否则 T5 极慢。

        iter-91：pdf_oxide（Rust + PyO3）0.8ms/文档 + 释放 GIL → T2；
        pypdf（纯 Python）12.1ms/文档 + 持有 GIL → T5。
        """
        return SpeedTier.FAST if _PDF_OXIDE_AVAILABLE else SpeedTier.VERY_SLOW

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "PDF"

    @override
    def extract(self, path: Path) -> str:
        """提取 PDF 文本内容，加密文档返回空字符串。"""
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc
        return self.extract_from_bytes(data)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节提取 PDF 文本，加密文档返回空字符串。

        优先调用 ``pdf_oxide`` 后端（iter-91）；不可用时回退 ``pypdf``。
        """
        if _PDF_OXIDE_AVAILABLE:
            return self._extract_with_pdf_oxide(data)
        return self._extract_with_pypdf(data)

    def _extract_with_pdf_oxide(self, data: bytes) -> str:
        """使用 pdf_oxide（Rust + PyO3）提取 PDF 文本。

        ``to_plain_text_all()`` 一次性提取全部页面纯文本，避免逐页调用的
        Python 循环开销，Rust 侧批量处理 + 释放 GIL。
        """
        try:
            doc = _PdfOxideDocument.from_bytes(data)  # type: ignore[union-attr]
        except Exception as exc:
            # 加密文档无密码时 from_bytes 可能抛异常
            msg = str(exc).lower()
            if "encrypt" in msg or "password" in msg:
                logger.info("PDF 已加密，跳过")
                return ""
            raise ExtractorError(f"PDF 解析失败: {exc}") from exc

        try:
            return doc.to_plain_text_all() or ""
        except Exception as exc:
            msg = str(exc).lower()
            if "encrypt" in msg or "password" in msg:
                logger.info("PDF 已加密，跳过")
                return ""
            logger.warning("PDF 文本提取失败", exc_info=True)
            return ""

    def _extract_with_pypdf(self, data: bytes) -> str:
        """使用 pypdf 回退提取 PDF 文本。"""
        try:
            from pypdf import PdfReader
            from pypdf.errors import PdfReadError
        except ImportError as exc:
            raise ExtractorError("pypdf 未安装，无法提取 PDF") from exc

        try:
            reader = PdfReader(io.BytesIO(data))
        except PdfReadError as exc:
            raise ExtractorError(f"PDF 解析失败: {exc}") from exc
        except Exception as exc:
            raise ExtractorError(f"PDF 打开失败: {exc}") from exc

        if reader.is_encrypted:
            logger.info("PDF 已加密，跳过")
            return ""

        return self._extract_pages_pypdf(reader)

    def _extract_pages_pypdf(self, reader: object) -> str:
        """pypdf 回退路径：提取所有页面文本。"""
        parts = []
        for page in reader.pages:  # pyrefly: ignore [missing-attribute]
            try:
                text = page.extract_text() or ""
                if text:
                    parts.append(text)
            except Exception:
                logger.warning("PDF 页面提取失败", exc_info=True)
                continue
        return "\n".join(parts)
