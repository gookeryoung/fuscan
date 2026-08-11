"""PDF 提取器。

使用 ``pypdfium2``（Google pdfium C++ 引擎，通过 cffi 绑定）提取 PDF 文本。
pypdfium2 兼容 Win7（无 Rust 运行时依赖），且在原生代码内释放 GIL，
多个 worker 线程可真正并行。

pypdfium2 的 C 扩展在模块顶层 import 会增加启动耗时（约 64ms），
故通过 :func:`_ensure_backend` 延迟到首次 :meth:`PdfExtractor.extract_from_bytes`
调用时才加载，``import fuscan.app`` 启动阶段不触发 pypdfium2 加载。
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from typing_extensions import override

from fuscan.extractors.base import Extractor, ExtractorError, SpeedTier

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["PdfExtractor"]

logger = logging.getLogger(__name__)


def _ensure_backend() -> Callable[..., object]:
    """延迟导入 pypdfium2，返回 ``PdfDocument`` 类。

    :raises ExtractorError: pypdfium2 未安装时抛出
    :return: ``pypdfium2.PdfDocument`` 类
    """
    try:
        from pypdfium2 import PdfDocument
    except ImportError as exc:  # pragma: no cover - 环境依赖：仅 pypdfium2 未安装时执行
        raise ExtractorError("无可用 PDF 引擎（pypdfium2 未安装）") from exc
    return PdfDocument


class PdfExtractor(Extractor):
    """PDF 文档文本提取器。

    后端固定为 pypdfium2（Google pdfium C++ 引擎，cffi 绑定），
    兼容 Win7。:attr:`speed_tier` 固定返回 T3 中速。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 PDF 提取器支持的扩展名。"""
        return ("pdf",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """PDF 提取速度档次。

        pypdfium2（pdfium C++）：接近原生性能 + 释放 GIL → T3 中速
        """
        return SpeedTier.MEDIUM

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "PDF"

    @override
    @property
    def engine_info(self) -> str:
        """实际使用的 PDF 解析引擎。"""
        return "pypdfium2"

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节提取 PDF 文本，加密文档返回空字符串。"""
        pdf_document = _ensure_backend()
        return self._extract_with_pdfium2(data, pdf_document)

    def _extract_with_pdfium2(self, data: bytes, pdf_document: Callable[..., object]) -> str:
        """使用 pypdfium2（Google pdfium C++）提取 PDF 文本。

        pypdfium2 基于 Google pdfium C++ 引擎，通过 cffi 绑定，
        性能接近原生且兼容 Win7（无 Rust 依赖）。在原生代码内释放 GIL，
        多 worker 线程可真正并行。

        :param data: PDF 文件字节内容
        :param pdf_document: ``pypdfium2.PdfDocument`` 类（由 :func:`_ensure_backend` 提供）
        """
        try:
            doc = pdf_document(io.BytesIO(data))
        except Exception as exc:
            raise ExtractorError(f"PDF 打开失败: {exc}") from exc

        try:
            parts: list[str] = []
            for page_index in range(len(doc)):  # type: ignore[arg-type]
                try:
                    page = doc.get_page(page_index)  # type: ignore[union-attr]
                    textpage = page.get_textpage()  # type: ignore[union-attr]
                    text = textpage.get_text_range() or ""  # type: ignore[union-attr]
                    if text:
                        parts.append(text)
                except Exception:
                    logger.warning("pypdfium2 页面提取失败", exc_info=True)
                    continue
            return "\n".join(parts)
        except Exception as exc:
            msg = str(exc).lower()
            if "encrypt" in msg or "password" in msg:
                logger.info("PDF 已加密，跳过")
                return ""
            raise ExtractorError(f"PDF 解析失败: {exc}") from exc
        finally:
            doc.close()  # type: ignore[union-attr]
