"""图片 OCR 提取器（RapidOCR + Pillow）。

Pillow 解码图片（GIF 取首帧），转 ndarray 喂 RapidOCR 推理。
大图片跳过（>10MB），Pillow ``MAX_IMAGE_PIXELS`` 默认解压炸弹保护生效。
Pillow/rapidocr 缺失抛 :class:`~fuscan.extractors.base.ExtractorError`（同
:class:`~fuscan.extractors.pdf.PdfExtractor` 模式）。

OCR 引擎通过 :mod:`fuscan.extractors.ocr` 的线程局部单例获取，
onnxruntime 推理时 C++ 后端释放 GIL，多 worker 真正并行。
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from typing_extensions import override

from fuscan.extractors.base import Extractor, ExtractorError, SpeedTier
from fuscan.extractors.ocr import get_ocr_engine

if TYPE_CHECKING:
    import numpy as np  # 仅供类型注解  # pyrefly: ignore [missing-import]

__all__ = ["ImageExtractor"]

logger = logging.getLogger(__name__)

# 超过此大小的图片跳过 OCR（避免 OOM 与超时）
_MAX_OCR_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB


class ImageExtractor(Extractor):
    """图片 OCR 提取器。

    使用 Pillow 解码图片，RapidOCR（onnxruntime 后端）识别文本。
    :attr:`speed_tier` 固定返回 T5 极慢（神经网络推理 det+cls+rec 三阶段）。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回图片提取器支持的扩展名。"""
        return ("png", "jpg", "jpeg", "tiff", "tif", "bmp", "webp", "gif")

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """图片 OCR 速度档次。

        RapidOCR 神经网络推理（det+cls+rec 三阶段）→ T5 极慢
        """
        return SpeedTier.VERY_SLOW

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "图片（OCR）"

    @override
    @property
    def engine_info(self) -> str:
        """实际使用的 OCR 引擎。"""
        return "rapidocr-onnxruntime"

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节提取图片文本（OCR）。

        :param data: 图片文件完整字节内容
        :return: 识别出的文本；大图片跳过或无文本时返回空字符串
        :raises ExtractorError: Pillow/rapidocr 缺失、图片解码失败、OCR 推理失败
        """
        if len(data) > _MAX_OCR_IMAGE_BYTES:
            logger.info("图片 %d bytes 超过 OCR 阈值 %d，跳过", len(data), _MAX_OCR_IMAGE_BYTES)
            return ""

        engine = get_ocr_engine()  # 缺失抛 ExtractorError
        arr = self._decode_to_ndarray(data)  # Pillow 缺失抛 ExtractorError

        try:
            result = engine(arr)
        except Exception as exc:  # onnxruntime 异常类型不可控，统一包装
            raise ExtractorError(f"OCR 推理失败: {exc}") from exc
        txts = getattr(result, "txts", None) or ()
        return "\n".join(t for t in txts if t)

    @staticmethod
    def _decode_to_ndarray(data: bytes) -> np.ndarray:
        """用 Pillow 解码图片字节为 ndarray（GIF 取首帧）。

        :param data: 图片字节
        :return: RGB 或灰度 ndarray
        :raises ExtractorError: Pillow 缺失、解码失败或解压炸弹触发
        """
        import numpy as np  # pyrefly: ignore [missing-import]

        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - 环境依赖：仅 Pillow 未安装时执行
            raise ExtractorError("无可用图片解码库（Pillow 未安装）") from exc

        try:
            img = Image.open(io.BytesIO(data))
            img.load()  # 强制解码（触发 MAX_IMAGE_PIXELS 解压炸弹检查）
            # GIF/动画取首帧（OCR 多帧无意义且耗时）
            if getattr(img, "is_animated", False):
                img.seek(0)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            return np.asarray(img)
        except Image.DecompressionBombError as exc:  # pragma: no cover - 默认阈值下难触发
            raise ExtractorError(f"图片像素超限（疑似解压炸弹）: {exc}") from exc
        except Exception as exc:  # Pillow 解码异常类型不可控，统一包装
            raise ExtractorError(f"图片解码失败: {exc}") from exc
