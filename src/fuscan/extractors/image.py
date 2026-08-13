"""图片 OCR 提取器（RapidOCR-json 预编译 exe）。

通过 :mod:`fuscan.extractors.ocr` 的常驻子进程引擎识别图片文本。
exe 内部 opencv 解码图片字节（支持 png/jpg/jpeg/tiff/tif/bmp/webp/gif 等），
Python 侧无需 Pillow/numpy 解码——直接把原始图片字节 base64 喂给 exe。

预编译 exe（C++ 静态链接 onnxruntime+opencv，单文件 16MB，无外部 DLL）绕过
Python onnxruntime wheel 的 Win7 兼容性死结，明确支持 Win7 x64。
大图片跳过（>10MB），避免 OCR 推理超时与内存峰值。

OCR 引擎通过 :mod:`fuscan.extractors.ocr` 的全局单例获取（exe 常驻 + 锁串行化），
exe 内部 ``numThread`` 并行处理单张图片的 det/cls+rec 流水线，推理释放 GIL。
"""

from __future__ import annotations

import logging

from typing_extensions import override

from fuscan.extractors.base import Extractor, SpeedTier
from fuscan.extractors.ocr import recognize

__all__ = ["ImageExtractor"]

logger = logging.getLogger(__name__)

# 超过此大小的图片跳过 OCR（避免推理超时与内存峰值）
_MAX_OCR_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB


class ImageExtractor(Extractor):
    """图片 OCR 提取器。

    使用 RapidOCR-json 预编译 exe（C++ 静态链接 onnxruntime+opencv，
    兼容 Win7）识别文本。:attr:`speed_tier` 固定返回 T5 极慢
    （神经网络推理 det+cls+rec 三阶段）。

    exe/模型缺失时 :meth:`extract_from_bytes` 抛 :class:`ExtractorError`
    （由扫描器转换为「提取器不可用」跳过，同 PDF 静默降级模式的差异：
    图片无降级路径——无 OCR 即无法提取，故显式报错让用户感知）。
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

        RapidOCR-json 神经网络推理（det+cls+rec 三阶段）→ T5 极慢
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
        return "rapidocr-json"

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节提取图片文本（OCR）。

        exe 内部 opencv 解码图片字节，Python 侧无需解码。原始字节直接
        base64 编码传给常驻 exe 子进程，识别结果按行拼接返回。

        :param data: 图片文件完整字节内容
        :return: 识别出的文本；大图片跳过或无文本时返回空字符串
        :raises ExtractorError: OCR 引擎（exe/模型）缺失或推理失败
        """
        if len(data) > _MAX_OCR_IMAGE_BYTES:
            logger.info("图片 %d bytes 超过 OCR 阈值 %d，跳过", len(data), _MAX_OCR_IMAGE_BYTES)
            return ""
        return recognize(data)
