"""RapidOCR-json 引擎：通过常驻子进程调用预编译 C++ OCR 引擎。

使用 RapidOCR-json（hiroi-sora，MIT，v0.2.0）预编译 exe，绕过 Python onnxruntime
wheel 的 Win7 兼容性死结——onnxruntime 1.12+ 官方 wheel 依赖 Win10 API（microsoft/
onnxruntime#12718 实证 1.12.1 在 Win7 缺 ``api-ms-win-core-*`` dlls），1.11.1 虽兼容
Win7 但无 cp310 wheel，而项目锁定 Python 3.10（PySide2 不支持 3.11+）。exe 内部
onnxruntime C++ 静态链接进单文件（16MB，无外部 DLL），明确支持 Win7 x64。
PP-OCRv3 简中模型，中英文识别。

通信协议：:func:`subprocess.Popen` 启动 exe 常驻进程（``--ensureAscii=1``），exe 先
输出版本行与 ``OCR init completed.``，随后循环接收 stdin 逐行 json 请求
``{"image_base64":"..."}``（base64 编码图片字节），每请求返回一行 json
``{"code":100,"data":[{"text":...,"score":...}]}``。``code`` 101 表示未识别到文字。

并发模型：全局单例 exe + :class:`threading.Lock` 串行化（stdin/stdout 管道为顺序
协议，不可并发）。exe 内部 ``numThread`` 并行处理单张图片的 det/cls+rec 流水线。
OCR 在文件扫描中为少数触发（仅图片文件与扫描版 PDF），串行可接受；内存仅 1 个 exe
进程（峰值约 500MB），远低于线程局部多实例方案。

公共 API：

- :func:`get_ocr_engine`：返回全局 :class:`OcrEngine` 单例
- :func:`is_ocr_available`：探测 exe 与模型就位（不启动子进程）
- :func:`get_ocr_status`：检测完整可用性及原因（供 GUI 展示）
- :func:`recognize`：对图片字节执行 OCR，返回拼接文本
"""

from __future__ import annotations

import atexit
import base64
import contextlib
import json
import logging
import subprocess
import sys
import threading
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from fuscan.extractors.base import ExtractorError

__all__ = [
    "OcrDepStatus",
    "OcrEngine",
    "OcrStatus",
    "get_ocr_engine",
    "get_ocr_status",
    "is_ocr_available",
    "recognize",
]

logger = logging.getLogger(__name__)

# RapidOCR-json 预编译 exe 文件名（hiroi-sora/RapidOCR-json v0.2.0，MIT）
_EXE_NAME = "RapidOCR-json.exe"
# PP-OCRv3 简中模型文件名（exe 默认配置，中英文通用）
_DET_MODEL = "ch_PP-OCRv3_det_infer.onnx"
_CLS_MODEL = "ch_ppocr_mobile_v2.0_cls_infer.onnx"
_REC_MODEL = "ch_PP-OCRv3_rec_infer.onnx"
_REC_KEYS = "ppocr_keys_v1.txt"
_MODELS: tuple[str, ...] = (_DET_MODEL, _CLS_MODEL, _REC_MODEL, _REC_KEYS)

# exe 初始化超时（秒）：模型加载约 0.1s，慢机留余量
_INIT_TIMEOUT = 15
# exe 内部推理线程数（det/cls+rec 流水线并行）
_NUM_THREAD = 4
# 初始化完成标志（exe 启动后输出此行表示就绪）
_INIT_OK = "OCR init completed."
# exe 版本号（展示用，对齐 release tag）
_EXE_VERSION = "v0.2.0"

# 全局单例引擎 + 保护锁（仅保护创建，推理由 OcrEngine 内部锁保护）
_engine: OcrEngine | None = None
_engine_lock = threading.Lock()


def _ocr_assets_dir() -> Path:
    """返回 OCR 资源目录（exe + models 所在目录）路径。

    通过 :func:`importlib.resources.files` 解析，fspack 打包后仍能正确定位，
    避免用 ``Path(__file__).parent / ...``（打包后 ``__file__`` 指向临时解压目录）。

    :return: 资源目录 :class:`~pathlib.Path`
    """
    return Path(str(files("fuscan.assets.ocr")))


def _exe_path() -> Path:
    """返回 RapidOCR-json.exe 路径。"""
    return _ocr_assets_dir() / _EXE_NAME


def _models_dir() -> Path:
    """返回模型目录路径（exe 同级 ``models/`` 子目录）。"""
    return _ocr_assets_dir() / "models"


def _has_exe() -> bool:
    """检查 exe 文件是否存在。"""
    return _exe_path().exists()


def _has_models() -> bool:
    """检查 4 个 PP-OCRv3 简中模型文件是否全部存在。"""
    d = _models_dir()
    return all((d / f).exists() for f in _MODELS)


class OcrEngine:
    """RapidOCR-json 常驻子进程引擎。

    启动 exe 常驻进程，通过 stdin/stdout 管道逐行 json 通信。线程安全：
    内部锁串行化通信（管道为顺序协议，不可并发调用）。

    :raises ExtractorError: exe/模型缺失、启动失败、初始化失败或超时
    """

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ExtractorError("OCR 引擎仅支持 Windows（RapidOCR-json 预编译 exe）")
        exe = _exe_path()
        if not exe.exists():
            raise ExtractorError(f"OCR 引擎不存在: {exe}")
        if not _has_models():
            raise ExtractorError(f"OCR 模型不完整: {_models_dir()}")

        # 启动参数：--ensureAscii=1（ASCII 转义输出避免编码问题）+ --numThread
        # + --models=models（相对 cwd）。cwd 设为 exe 目录，使 --models=models
        # 解析为 exe 同级 models/ 子目录，不受用户名含中文等路径影响。
        args = [str(exe), "--ensureAscii=1", f"--numThread={_NUM_THREAD}", "--models=models"]
        try:
            self._proc = subprocess.Popen(
                args,
                cwd=str(exe.parent),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                startupinfo=self._startupinfo(),
            )
        except OSError as exc:
            raise ExtractorError(f"OCR 引擎启动失败: {exc}") from exc

        atexit.register(self.stop)
        self._lock = threading.Lock()
        self._stopped = False

        if not self._wait_init():
            self.stop()
            raise ExtractorError("OCR 引擎初始化失败或超时")

    @staticmethod
    def _startupinfo() -> subprocess.STARTUPINFO | None:
        """Windows 下隐藏 exe 控制台窗口（避免扫描时弹黑窗）。"""
        if sys.platform != "win32":
            return None
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si

    def _wait_init(self) -> bool:
        """等待 exe 输出初始化完成标志（含超时保护）。

        exe 启动后先输出版本行，再输出 ``OCR init completed.``，需循环读取直到
        匹配标志行。超时或子进程提前退出返回 False。

        :return: True 表示初始化成功
        """
        timer = threading.Timer(_INIT_TIMEOUT, self._on_init_timeout)
        timer.start()
        try:
            assert self._proc.stdout is not None
            while True:
                if self._proc.poll() is not None:
                    return False
                line = self._proc.stdout.readline()
                if not line:
                    return False
                if _INIT_OK in line.decode("ascii", errors="ignore"):
                    return True
        finally:
            timer.cancel()

    def _on_init_timeout(self) -> None:
        """初始化超时强制终止子进程（由 :class:`threading.Timer` 触发）。"""
        logger.warning("OCR 引擎初始化超时 %ds，终止子进程", _INIT_TIMEOUT)
        self._proc.kill()

    def is_alive(self) -> bool:
        """子进程是否存活（供 :func:`get_ocr_engine` 判断是否需重建）。"""
        return self._proc.poll() is None

    def recognize(self, data: bytes) -> str:
        """对图片字节执行 OCR，返回拼接文本。

        :param data: 图片字节（任意格式，exe 内部 opencv 解码）
        :return: 识别文本（多行 ``\\n`` 拼接）；未识别到文字返回空字符串
        :raises ExtractorError: 子进程崩溃、通信失败或 OCR 返回错误码
        """
        b64 = base64.b64encode(data).decode("ascii")
        req = json.dumps({"image_base64": b64}, ensure_ascii=True) + "\n"
        with self._lock:
            resp = self._send(req)
        return self._parse(resp)

    def _send(self, req: str) -> bytes:
        """发送一行请求并读取一行响应（调用方持锁）。"""
        if self._proc.poll() is not None:
            raise ExtractorError("OCR 子进程已退出")
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        try:
            self._proc.stdin.write(req.encode("ascii"))
            self._proc.stdin.flush()
            resp = self._proc.stdout.readline()
        except (BrokenPipeError, OSError) as exc:
            raise ExtractorError(f"OCR 通信失败: {exc}") from exc
        if not resp:
            raise ExtractorError("OCR 子进程无响应（EOF）")
        return resp

    @staticmethod
    def _parse(resp: bytes) -> str:
        """解析 exe 的 json 响应为文本。

        :raises ExtractorError: 响应非合法 json 或 OCR 返回错误码（非 100/101）
        """
        try:
            result = json.loads(resp)
        except json.JSONDecodeError as exc:
            raise ExtractorError(f"OCR 响应解析失败: {exc}") from exc
        code = result.get("code")
        if code == 100:
            lines = result.get("data") or []
            return "\n".join(item.get("text", "") for item in lines if item.get("text"))
        if code == 101:
            return ""
        msg = result.get("data", "未知错误")
        raise ExtractorError(f"OCR 失败 (code={code}): {msg}")

    def stop(self) -> None:
        """终止子进程并关闭管道（重复调用安全）。"""
        if self._stopped:
            return
        self._stopped = True
        proc = self._proc
        if proc.poll() is None:
            proc.kill()
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()


def get_ocr_engine() -> OcrEngine:
    """返回全局 OCR 引擎单例。

    首次调用启动 exe 子进程；后续复用。子进程已退出时自动重建（崩溃恢复）。
    exe/模型缺失抛 :class:`~fuscan.extractors.base.ExtractorError`，由
    :class:`~fuscan.extractors.image.ImageExtractor` /
    :class:`~fuscan.extractors.pdf.PdfExtractor` 转换为降级（返回空内容）。

    :raises ExtractorError: exe/模型缺失或启动失败
    :return: :class:`OcrEngine` 单例
    """
    global _engine  # noqa: PLW0603  # 单例模式需 global 更新模块级缓存
    if _engine is not None and _engine.is_alive():
        return _engine
    with _engine_lock:
        if _engine is not None and _engine.is_alive():
            return _engine
        if _engine is not None:
            _engine.stop()
        _engine = OcrEngine()
    return _engine


def is_ocr_available() -> bool:
    """探测 exe 与模型是否就位（不启动子进程）。

    RapidOCR-json 仅提供 Windows 预编译 exe，非 Windows 平台始终返回 False
    （避免 Linux/macOS 上误判可用后启动 exe 失败的模糊错误）。

    供 GUI 灰显 OCR 选项与测试 ``skipif`` 使用，避免在未就位环境下启动 exe。

    :return: True 表示运行于 Windows 且 exe 与 4 个模型文件均存在
    """
    if sys.platform != "win32":
        return False
    return _has_exe() and _has_models()


@dataclass(frozen=True)
class OcrDepStatus:
    """单个 OCR 依赖项的可用性状态（供 GUI 关于页逐项展示绿勾/红叉）。

    :ivar name: 依赖展示名（RapidOCR-json 引擎/模型文件）
    :ivar installed: 是否就位
    :ivar version: 版本号或就位详情；未就位为空字符串
    """

    name: str
    installed: bool
    version: str


@dataclass(frozen=True)
class OcrStatus:
    """OCR 引擎可用性状态（供 GUI 关于页展示启用情况与各依赖明细）。

    :ivar available: OCR 是否可用（exe 与模型文件就位）
    :ivar reason: 不可用原因（首个缺失项）；可用时为空字符串
    :ivar version: exe 版本号；不可用时为空字符串
    :ivar dependencies: 各依赖项状态（RapidOCR-json 引擎/模型文件）
    """

    available: bool
    reason: str
    version: str
    dependencies: tuple[OcrDepStatus, ...]


def get_ocr_status() -> OcrStatus:
    """检测 OCR 完整可用性及各依赖状态（不启动子进程，供 GUI 展示）。

    RapidOCR-json 仅提供 Windows 预编译 exe，非 Windows 平台返回不可用
    （reason 说明平台限制），依赖项均标记未就位，避免 GUI 误展示「已启用」。

    探测 exe 文件与 4 个 PP-OCRv3 模型文件是否就位，构建各依赖
    :class:`OcrDepStatus`。首个缺失项决定 ``reason``；全部通过返回
    ``available=True`` 与 exe 版本号。不启动 exe 子进程，仅文件存在性检查。

    :return: :class:`OcrStatus` 状态对象（含各依赖明细）
    """
    if sys.platform != "win32":
        return OcrStatus(
            available=False,
            reason="OCR 引擎仅支持 Windows",
            version="",
            dependencies=(
                OcrDepStatus("RapidOCR-json 引擎", False, ""),
                OcrDepStatus("模型文件 (PP-OCRv3)", False, ""),
            ),
        )
    deps: list[OcrDepStatus] = []
    exe_ok = _has_exe()
    deps.append(OcrDepStatus("RapidOCR-json 引擎", exe_ok, _EXE_VERSION if exe_ok else ""))
    models_ok = _has_models()
    deps.append(OcrDepStatus("模型文件 (PP-OCRv3)", models_ok, "内置" if models_ok else ""))
    first_missing = next((d for d in deps if not d.installed), None)
    available = first_missing is None
    reason = "" if available else f"{first_missing.name} 未就位"
    version_str = _EXE_VERSION if exe_ok else ""
    return OcrStatus(available, reason, version_str, tuple(deps))


def recognize(data: bytes) -> str:
    """对图片字节执行 OCR，返回拼接文本。

    :param data: 图片字节（任意格式，exe 内部 opencv 解码）
    :return: 识别文本；未识别到文字返回空字符串
    :raises ExtractorError: 引擎缺失或 OCR 失败
    """
    engine = get_ocr_engine()
    return engine.recognize(data)
