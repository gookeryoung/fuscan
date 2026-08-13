"""RapidOCR 引擎线程局部单例。

懒加载 + 线程局部：每 worker 线程独立引擎实例，onnxruntime 推理释放 GIL
时多 worker 真正并行（代价：N workers × ~17MB 模型内存）。rapidocr 缺失
时抛 :class:`~fuscan.extractors.base.ExtractorError`，由
:class:`~fuscan.extractors.image.ImageExtractor` /
:class:`~fuscan.extractors.pdf.PdfExtractor` 转换为降级（返回空内容）。

模型加载策略（两级回退）：

1. **内置模型**：``src/fuscan/assets/ocr/models/`` 存在模型文件时优先使用，
   通过 :func:`importlib.resources.files` 解析路径，fspack 打包后仍能正确定位。
   fspack 通过 ``[tool.fspack] data-dirs`` 将模型文件随 exe 分发，离线可用。
2. **rapidocr 默认模型**：内置模型不存在时（如 PyPI wheel 不含模型），
   ``RapidOCR()`` 无参构建，rapidocr 自动从网络下载或从 cache 加载默认模型。

wheel 与 sdist 均不含模型文件（~17MB），仅 fspack 打包 exe 与 CI/CD 时下载。

公共 API：

- :func:`get_ocr_engine`：返回当前线程的 OCR 引擎（线程局部单例）
- :func:`is_ocr_available`：探测 rapidocr 是否可导入（不加载模型）
- :func:`get_ocr_status`：检测 OCR 完整可用性及未启用原因（供 GUI 展示）
- :func:`recognize`：对图像执行 OCR，返回拼接文本
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

from fuscan.extractors.base import ExtractorError

if TYPE_CHECKING:
    from rapidocr import RapidOCR  # pyrefly: ignore [missing-import]

__all__ = [
    "OcrDepStatus",
    "OcrStatus",
    "get_ocr_engine",
    "get_ocr_status",
    "is_ocr_available",
    "recognize",
]

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


def _has_builtin_models() -> bool:
    """检查内置模型目录中 4 个模型文件是否全部存在。

    fspack 打包 exe 时通过 data-dirs 内置模型；PyPI wheel/sdist 不含模型文件。
    """
    models_dir = _models_dir()
    return all((models_dir / f).exists() for f in (_DET_MODEL, _CLS_MODEL, _REC_MODEL, _REC_KEYS))


def _build_engine() -> RapidOCR:
    """构建 RapidOCR 引擎实例（两级模型回退）。

    1. 内置模型存在时从 ``src/fuscan/assets/ocr/models/`` 离线加载（fspack exe）
    2. 内置模型不存在时用 ``RapidOCR()`` 无参构建，rapidocr 自动下载/从 cache 加载

    :raises ExtractorError: rapidocr 未安装
    :return: :class:`rapidocr.RapidOCR` 引擎实例
    """
    try:
        from rapidocr import RapidOCR  # pyrefly: ignore [missing-import]
    except ImportError as exc:  # pragma: no cover - 环境依赖：仅 rapidocr 未安装时执行
        raise ExtractorError("无可用 OCR 引擎（rapidocr 未安装）") from exc

    if not _has_builtin_models():
        # PyPI wheel 不含模型，rapidocr 自动从网络下载或从 cache 加载默认模型
        logger.info("内置 OCR 模型不存在，使用 rapidocr 默认模型（联网下载或 cache）")
        return RapidOCR()

    models_dir = _models_dir()
    det_path = models_dir / _DET_MODEL
    cls_path = models_dir / _CLS_MODEL
    rec_path = models_dir / _REC_MODEL
    keys_path = models_dir / _REC_KEYS

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


# OCR 运行链全部必需的依赖模块名（按检测顺序排列）。
# rapidocr：OCR 框架本体；onnxruntime：推理后端；Pillow：图片解码；
# numpy：ndarray 转换。任一缺失则 OCR 无法运行，需在 GUI 明确展示原因。
_OCR_RUNTIME_DEPS: tuple[str, ...] = ("rapidocr", "onnxruntime", "PIL", "numpy")

# 依赖模块名 → 展示名映射（PIL 包展示为 Pillow，其余同名）
_OCR_DEP_DISPLAY: dict[str, str] = {
    "rapidocr": "rapidocr",
    "onnxruntime": "onnxruntime",
    "PIL": "Pillow",
    "numpy": "numpy",
}


@dataclass(frozen=True)
class OcrDepStatus:
    """单个 OCR 依赖项的可用性状态（供 GUI 关于页逐项展示绿勾/红叉）。

    :ivar name: 依赖展示名（rapidocr/onnxruntime/Pillow/numpy/模型文件）
    :ivar installed: 是否就位（模块可导入或模型文件齐全）
    :ivar version: 版本号或就位详情；未就位为空字符串
    """

    name: str
    installed: bool
    version: str


@dataclass(frozen=True)
class OcrStatus:
    """OCR 引擎可用性状态（供 GUI 关于页展示启用情况与各依赖明细）。

    :ivar available: OCR 是否可用（所有依赖与模型文件就位）
    :ivar reason: 不可用原因（首个缺失项）；可用时为空字符串
    :ivar version: rapidocr 版本号；不可用时为空字符串
    :ivar dependencies: 各依赖项状态（rapidocr/onnxruntime/Pillow/numpy/模型文件）
    """

    available: bool
    reason: str
    version: str
    dependencies: tuple[OcrDepStatus, ...]


def get_ocr_status() -> OcrStatus:
    """检测 OCR 完整可用性及各依赖状态（不加载模型，供 GUI 展示）。

    逐项探测运行链：rapidocr → onnxruntime → Pillow → numpy → 模型文件，
    构建各依赖的 :class:`OcrDepStatus`。首个缺失项决定 ``reason``；全部通过
    返回 ``available=True`` 与 rapidocr 版本号。不触发模型加载（约 200ms +
    17MB 内存），仅做导入探测与文件存在性检查。

    :return: :class:`OcrStatus` 状态对象（含各依赖明细）
    """
    deps: list[OcrDepStatus] = []
    # 依赖模块：逐项导入探测，记录就位状态与版本号
    for mod_name in _OCR_RUNTIME_DEPS:
        display = _OCR_DEP_DISPLAY[mod_name]
        try:
            __import__(mod_name)
        except ImportError:
            deps.append(OcrDepStatus(display, False, ""))
            continue
        # 取版本号（PIL 包元数据名为 Pillow；取不到留空，不影响 available 判定）
        pkg = "Pillow" if mod_name == "PIL" else display
        try:
            v = version(pkg)
        except PackageNotFoundError:  # 已通过导入探测，元数据异常属边缘情况
            v = ""
        deps.append(OcrDepStatus(display, True, v))
    # 模型文件：内置模型存在时显示「内置」，不存在时显示「rapidocr 默认」（不影响 available）
    # wheel/sdist 不含模型，仅 fspack exe 内置；PyPI 用户由 rapidocr 自动下载
    if _has_builtin_models():
        deps.append(OcrDepStatus("模型文件", True, "内置"))
    else:
        deps.append(OcrDepStatus("模型文件", True, "rapidocr 默认"))
    # 汇总：首个缺失项决定 reason
    first_missing = next((d for d in deps if not d.installed), None)
    available = first_missing is None
    reason = "" if available else f"{first_missing.name} 未就位"
    # rapidocr 版本从依赖明细取（_OCR_RUNTIME_DEPS 首项为 rapidocr），避免重复探测
    version_str = deps[0].version if deps[0].installed else ""
    return OcrStatus(available, reason, version_str, tuple(deps))


def recognize(img: object) -> str:
    """对图像执行 OCR，返回拼接文本。

    :param img: RapidOCR 接受的图像输入（``ndarray`` / ``bytes`` / ``Path`` / ``str``）
    :return: 识别出的文本（多行用 ``\\n`` 拼接）；无文本返回空字符串
    :raises ExtractorError: OCR 推理失败（onnxruntime 异常、图像损坏等）
    """
    engine = get_ocr_engine()
    try:
        result = engine(img)  # pyrefly: ignore [bad-argument-type]
    except Exception as exc:  # onnxruntime/rapidocr 异常类型不可控，统一包装
        raise ExtractorError(f"OCR 推理失败: {exc}") from exc
    txts = getattr(result, "txts", None) or ()
    return "\n".join(t for t in txts if t)
