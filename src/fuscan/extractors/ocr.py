"""RapidOCR 引擎线程局部单例。

懒加载 + 线程局部：每 worker 线程独立引擎实例，onnxruntime 推理释放 GIL
时多 worker 真正并行（代价：N workers × ~17MB 模型内存）。rapidocr 缺失
或模型文件缺失时抛 :class:`~fuscan.extractors.base.ExtractorError`，
由 :class:`~fuscan.extractors.image.ImageExtractor` /
:class:`~fuscan.extractors.pdf.PdfExtractor` 转换为降级（返回空内容）。

模型文件随软件打包内置（``src/fuscan/assets/ocr/models/``），通过
:func:`importlib.resources.files` 解析路径，PyInstaller/fspack 打包后
仍能正确定位（避免 ``Path(__file__)`` 在打包后路径变化的问题）。

公共 API：

- :func:`get_ocr_engine`：返回当前线程的 OCR 引擎（线程局部单例）
- :func:`is_ocr_available`：探测 rapidocr 是否可导入（不加载模型）
- :func:`recognize`：对图像执行 OCR，返回拼接文本
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from fuscan.extractors.base import ExtractorError

if TYPE_CHECKING:
    from rapidocr import RapidOCR  # pyrefly: ignore [missing-import]

__all__ = ["get_ocr_engine", "is_ocr_available", "recognize"]

logger = logging.getLogger(__name__)

# PP-OCRv4 mobile 模型文件名（中英文通用，对齐 RapidOCR v3+ ModelScope 上游命名）
# 由 scripts/download_ocr_models.py 从魔搭社区下载，脚本见该文件
_DET_MODEL = "ch_PP-OCRv4_det_mobile.onnx"
_CLS_MODEL = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"
_REC_MODEL = "ch_PP-OCRv4_rec_mobile.onnx"
_REC_KEYS = "ppocr_keys_v1.txt"

# 线程局部存储：每 worker 线程独立引擎实例
_thread_local = threading.local()
# 仅保护首次构建的探测锁，不保护推理（推理由 onnxruntime 内部锁保证线程安全）
_probe_lock = threading.Lock()


def _models_dir() -> Path:
    """返回内置 OCR 模型目录路径。

    通过 :func:`importlib.resources.files` 解析，PyInstaller/fspack 打包后
    仍能正确定位。避免用 ``Path(__file__).parent / ...``——打包后 ``__file__``
    指向临时解压目录，路径可能失效。

    :return: 模型目录 :class:`~pathlib.Path`
    """
    from importlib.resources import files

    return Path(str(files("fuscan.assets.ocr.models")))


def _build_engine() -> RapidOCR:
    """构建 RapidOCR 引擎实例，从内置模型路径离线加载。

    :raises ExtractorError: rapidocr 未安装或模型文件缺失
    :return: :class:`rapidocr.RapidOCR` 引擎实例
    """
    try:
        from rapidocr import RapidOCR  # pyrefly: ignore [missing-import]
    except ImportError as exc:  # pragma: no cover - 环境依赖：仅 rapidocr 未安装时执行
        raise ExtractorError("无可用 OCR 引擎（rapidocr 未安装）") from exc

    models_dir = _models_dir()
    det_path = models_dir / _DET_MODEL
    cls_path = models_dir / _CLS_MODEL
    rec_path = models_dir / _REC_MODEL
    keys_path = models_dir / _REC_KEYS

    # 校验模型文件存在（打包缺失或用户未放置时给出明确错误，而非 rapidocr 内部晦涩报错）
    for model_file in (det_path, cls_path, rec_path, keys_path):
        if not model_file.exists():
            raise ExtractorError(f"OCR 模型文件缺失: {model_file.name}")

    return RapidOCR(
        params={
            "Det.model_path": str(det_path),
            "Cls.model_path": str(cls_path),
            "Rec.model_path": str(rec_path),
            "Rec.rec_keys_path": str(keys_path),
        }
    )


def get_ocr_engine() -> RapidOCR:
    """返回当前线程的 OCR 引擎（线程局部单例）。

    首次调用时懒加载 rapidocr 并加载内置模型；同一线程后续调用复用实例，
    避免 N 个文件重复加载模型（模型加载约 200ms，推理才释放 GIL）。
    rapidocr 缺失或模型文件缺失时抛 :class:`~fuscan.extractors.base.ExtractorError`，
    不缓存异常（便于用户安装依赖后重试）。

    :raises ExtractorError: rapidocr 未安装或模型文件缺失
    :return: :class:`rapidocr.RapidOCR` 引擎实例
    """
    cached = getattr(_thread_local, "engine", None)
    if cached is not None:
        return cached
    with _probe_lock:
        # 双检锁：避免多线程同时构建引擎（模型加载耗内存与时间）
        cached = getattr(_thread_local, "engine", None)
        if cached is not None:
            return cached
        cached = _build_engine()  # 缺失时抛 ExtractorError（不缓存异常）
    _thread_local.engine = cached
    return cached


def is_ocr_available() -> bool:
    """探测 rapidocr 是否可导入（不加载模型）。

    供 GUI 灰显 OCR 选项与测试 ``skipif`` 使用，避免在未安装 rapidocr 的
    环境下触发模型加载（模型加载约 200ms + 17MB 内存）。

    :return: True 表示 rapidocr 已安装可导入
    """
    try:
        import rapidocr  # noqa: F401  # pyrefly: ignore [missing-import]
    except ImportError:
        return False
    return True


def recognize(img: object) -> str:
    """对图像执行 OCR，返回拼接文本。

    :param img: RapidOCR 接受的图像输入（``ndarray`` / ``bytes`` / ``Path`` / ``str``）
    :return: 识别出的文本（多行用 ``\\n`` 拼接）；无文本返回空字符串
    :raises ExtractorError: OCR 推理失败（onnxruntime 异常、图像损坏等）
    """
    engine = get_ocr_engine()
    try:
        result = engine(img)
    except Exception as exc:  # onnxruntime/rapidocr 异常类型不可控，统一包装
        raise ExtractorError(f"OCR 推理失败: {exc}") from exc
    txts = getattr(result, "txts", None) or ()
    return "\n".join(t for t in txts if t)
