"""PDF 提取器。

使用 ``pypdfium2``（Google pdfium C++ 引擎，通过 cffi 绑定）提取 PDF 文本。
pypdfium2 兼容 Win7（无 Rust 运行时依赖），且在原生代码内释放 GIL，
多个 worker 线程可真正并行。

扫描版 PDF（文本层为空）回退 OCR：逐页渲染为位图（PIL）→ PNG 字节 →
RapidOCR-json 预编译 exe 识别，覆盖扫描件、拍照文档等无文本层的 PDF。
OCR 引擎（exe/模型）缺失时静默降级为空内容。

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
from fuscan.extractors.ocr import get_ocr_engine

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["PdfExtractor"]

logger = logging.getLogger(__name__)

# 扫描版 PDF OCR 回退参数
_MAX_PDF_OCR_PAGES = 50  # 页数超限跳过 OCR（性能保护，避免超大 PDF 卡死）
_PDF_RENDER_SCALE = 2.0  # 页面渲染缩放（≈144 DPI，OCR 精度/速度平衡）


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

    扫描版 PDF（文本层为空）回退 OCR：逐页渲染为位图（PIL）→ PNG 字节 →
    RapidOCR-json 预编译 exe 识别。:attr:`last_engine_info` 反映上次提取实际
    使用的引擎，供扫描器级上报；:attr:`engine_info` 静态保持 ``"pypdfium2"``
    供 GUI tooltip（不破坏现有断言）。
    """

    def __init__(self) -> None:
        self._last_engine_info: str = "pypdfium2"

    @property
    def last_engine_info(self) -> str:
        """上次提取实际使用的引擎（供扫描器级引擎上报）。

        纯文本提取返回 ``"pypdfium2"``，OCR 回退返回
        ``"pypdfium2 + rapidocr-json"``。
        """
        return self._last_engine_info

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
        """从内存字节提取 PDF 文本，扫描版回退 OCR。

        优先用 pypdfium2 提取文本层；文本层为空时（扫描版 PDF）回退
        逐页渲染 + RapidOCR。:attr:`last_engine_info` 反映实际引擎。

        :param data: PDF 文件字节内容
        :return: 提取的文本；加密文档或无文本时返回空字符串
        :raises ExtractorError: PDF 打开/解析失败（非加密）
        """
        self._last_engine_info = "pypdfium2"
        pdf_document = _ensure_backend()
        text = self._extract_with_pdfium2(data, pdf_document)
        if text.strip():
            return text
        # 文本层为空 → 尝试 OCR 回退（扫描版 PDF）
        ocr_text = self._ocr_fallback(data, pdf_document)
        if ocr_text:
            self._last_engine_info = "pypdfium2 + rapidocr-json"
            return ocr_text
        return ""

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

    def _ocr_fallback(self, data: bytes, pdf_document: Callable[..., object]) -> str:
        """扫描版 PDF OCR 回退：逐页渲染为位图（PIL）→ PNG 字节 → OCR。

        页数超过 :data:`_MAX_PDF_OCR_PAGES` 或 OCR 引擎（exe/模型）缺失时返回
        空字符串（降级为纯文本层结果）。加密 PDF 的页面渲染会失败，被 except
        吞掉后返回空。

        渲染得到的 PIL 图片保存为 PNG 字节后传给常驻 exe（exe 内部 opencv 解码），
        无需 numpy 转 ndarray。PNG 无损，保证 OCR 精度。

        :param data: PDF 文件字节内容
        :param pdf_document: ``pypdfium2.PdfDocument`` 类（由 :func:`_ensure_backend` 提供）
        :return: OCR 提取的文本；无法 OCR 时返回空字符串
        """
        try:
            doc = pdf_document(io.BytesIO(data))
        except Exception:
            logger.debug("OCR 回退：PDF 打开失败，跳过", exc_info=True)
            return ""
        try:
            n_pages = len(doc)  # type: ignore[arg-type]
            if n_pages > _MAX_PDF_OCR_PAGES:
                logger.info("PDF 页数 %d 超过 OCR 上限 %d，跳过 OCR", n_pages, _MAX_PDF_OCR_PAGES)
                return ""
            try:
                engine = get_ocr_engine()  # 启动常驻 exe；缺失/失败抛 ExtractorError
            except ExtractorError:
                logger.info("OCR 引擎未就位，跳过 PDF OCR 回退")
                return ""

            parts: list[str] = []
            for i in range(n_pages):
                try:
                    page = doc.get_page(i)  # type: ignore[union-attr]
                    pil_img = page.render(scale=_PDF_RENDER_SCALE).to_pil()  # type: ignore[union-attr]
                    if pil_img.mode != "RGB":
                        pil_img = pil_img.convert("RGB")
                    # PIL → PNG 字节（无损，喂 exe 内部 opencv 解码）
                    buf = io.BytesIO()
                    pil_img.save(buf, format="PNG")  # type: ignore[union-attr]
                    text = engine.recognize(buf.getvalue())
                    if text:
                        parts.append(text)
                except ExtractorError:
                    # 引擎级错误（子进程崩溃等）向上传播需由调用方决定；
                    # 但单页通信失败更可能是该页问题，记录后继续后续页
                    logger.warning("PDF 页 %d OCR 通信失败", i, exc_info=True)
                    continue
                except Exception:
                    logger.warning("PDF 页 %d OCR 失败", i, exc_info=True)
                    continue
            return "\n".join(parts)
        finally:
            doc.close()  # type: ignore[union-attr]
