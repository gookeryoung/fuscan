"""下载 PP-OCRv4 mobile ONNX 模型，替换占位文件。

fuscan 的 OCR 功能（:mod:`fuscan.extractors.ocr`）需要 4 个模型文件离线加载，
随软件打包内置。本脚本从官方上游下载真实模型并 SHA256 校验后原子替换占位文件。

模型来源（文件名对齐 RapidOCR v3+ ModelScope 上游命名）：

- 文本检测 / 方向分类 / 中英文识别：魔搭社区 RapidAI/RapidOCR 仓库（tag v3.9.2）
- 识别字符字典 ``ppocr_keys_v1.txt``：PaddleOCR 官方仓库（release/2.7 分支）

SHA256 校验值取自 RapidOCR ``default_models.yaml`` 与本地实测，确保下载内容
未被篡改。下载采用「临时文件 + 原子替换」：先写入 ``*.tmp``，校验通过后
:func:`os.replace` 覆盖目标，失败绝不破坏已存在的可用文件。

使用方式::

    # 下载并替换占位文件（已存在且校验通过则跳过）
    uv run python scripts/download_ocr_models.py

    # 强制重新下载（忽略已存在的可用文件）
    uv run python scripts/download_ocr_models.py --force

    # 仅校验现有文件，不下载
    uv run python scripts/download_ocr_models.py --check

退出码：0 全部成功 / 1 任一文件下载或校验失败。

仅依赖标准库（无 rapidocr/onnxruntime/Pillow 依赖），任意 Python 环境可运行。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# 仓库根目录（scripts/download_ocr_models.py 的上两级）
ROOT = Path(__file__).resolve().parent.parent
# 模型默认存放目录（与 ocr.py 的 _models_dir() 解析路径一致）
DEFAULT_MODELS_DIR = ROOT / "src" / "fuscan" / "assets" / "ocr" / "models"

# 下载分块大小（64KB，平衡内存与系统调用次数）
_CHUNK_SIZE = 64 * 1024
# 单文件下载超时（秒，rec 模型约 11MB，慢网络留足余量）
_TIMEOUT = 120
# 伪装 User-Agent，部分 CDN 拦截默认 urllib UA
_USER_AGENT = "fuscan-model-downloader/1.0"

# ModelScope RapidOCR 仓库 tag（与 default_models.yaml 的 resolve 路径一致）
_MODELSCOPE_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4"
# PaddleOCR 官方字典文件（release/2.7 分支，内容稳定）
_PPOCR_KEYS_URL = "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/release/2.7/ppocr/utils/ppocr_keys_v1.txt"


@dataclass(frozen=True)
class ModelFile:
    """单个模型文件描述。

    :ivar name: 本地文件名（对齐上游命名，与 :mod:`fuscan.extractors.ocr` 常量一致）
    :ivar url: 下载地址
    :ivar sha256: 期望的 SHA256 校验值（小写十六进制）
    :ivar desc: 中文用途说明
    """

    name: str
    url: str
    sha256: str
    desc: str


# 模型清单（PP-OCRv4 mobile，中英文通用，合计约 17MB）
# ONNX 校验值取自 RapidOCR default_models.yaml；字典校验值为本地实测
_MODELS: tuple[ModelFile, ...] = (
    ModelFile(
        name="ch_PP-OCRv4_det_mobile.onnx",
        url=f"{_MODELSCOPE_BASE}/det/ch_PP-OCRv4_det_mobile.onnx",
        sha256="d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
        desc="文本检测",
    ),
    ModelFile(
        name="ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        url=f"{_MODELSCOPE_BASE}/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        sha256="e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
        desc="方向分类",
    ),
    ModelFile(
        name="ch_PP-OCRv4_rec_mobile.onnx",
        url=f"{_MODELSCOPE_BASE}/rec/ch_PP-OCRv4_rec_mobile.onnx",
        sha256="48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
        desc="中英文识别",
    ),
    ModelFile(
        name="ppocr_keys_v1.txt",
        url=_PPOCR_KEYS_URL,
        sha256="28b2362ad4ab2dc38769aa72feb535e3a9ddb3fd2a7585a05920e6393b1dc7f7",
        desc="识别字符字典",
    ),
)


def _sha256_of(path: Path) -> str:
    """计算文件 SHA256（流式读取，避免大文件一次性载入内存）。

    :param path: 文件路径
    :return: 小写十六进制 SHA256
    """
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _human_size(num_bytes: int) -> str:
    """字节数转人类可读字符串（如 4.5MB）。"""
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024  # type: ignore[assignment]
    return f"{num_bytes:.1f}TB"


def _download(url: str, dest_tmp: Path) -> int:
    """下载 URL 到临时文件，返回字节数。

    :param url: 下载地址
    :param dest_tmp: 临时文件路径（调用方负责清理）
    :return: 已下载字节数
    :raises RuntimeError: 下载失败（网络错误、HTTP 非 2xx）
    """
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        total = 0
        with dest_tmp.open("wb") as fh:
            for chunk in iter(lambda: resp.read(_CHUNK_SIZE), b""):
                fh.write(chunk)
                total += len(chunk)
    return total


def _process_model(model: ModelFile, models_dir: Path, *, force: bool) -> str:
    """处理单个模型：已存在且校验通过则跳过，否则下载 + 校验 + 原子替换。

    :return: 结果描述（用于汇总输出）
    :raises RuntimeError: 下载或校验失败
    """
    dest = models_dir / model.name

    # 已存在且校验通过：默认跳过（除非 --force）
    if dest.exists() and not force and _sha256_of(dest) == model.sha256:
        return f"跳过（已存在且校验通过）: {model.name}"

    # 下载到临时文件，校验通过后原子替换（失败不破坏既有文件）
    dest_tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        print(f"下载 {model.desc} ({model.name}) ...", flush=True)
        size = _download(model.url, dest_tmp)
        actual_sha = _sha256_of(dest_tmp)
        if actual_sha != model.sha256:
            raise RuntimeError(f"SHA256 校验失败: 期望 {model.sha256[:12]}… 实际 {actual_sha[:12]}…")
        dest_tmp.replace(dest)  # 原子替换（Path.replace 底层即 os.replace）
        return f"完成: {model.name} ({_human_size(size)})"
    except Exception:
        # 清理临时文件，保留既有目标文件不变
        dest_tmp.unlink(missing_ok=True)
        raise


def _check_only(models_dir: Path) -> bool:
    """仅校验现有文件，不下载。

    :return: True 表示全部文件存在且校验通过
    """
    all_ok = True
    for model in _MODELS:
        dest = models_dir / model.name
        if not dest.exists():
            print(f"缺失: {model.name}")
            all_ok = False
            continue
        actual = _sha256_of(dest)
        if actual == model.sha256:
            print(f"通过: {model.name} ({_human_size(dest.stat().st_size)})")
        else:
            print(f"校验失败: {model.name} (期望 {model.sha256[:12]}… 实际 {actual[:12]}…)")
            all_ok = False
    return all_ok


def main(argv: list[str] | None = None) -> int:
    """脚本入口。

    :return: 0 全部成功 / 1 任一失败
    """
    parser = argparse.ArgumentParser(description="下载 PP-OCRv4 mobile 模型并替换占位文件")
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新下载（忽略已存在且校验通过的文件）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅校验现有文件，不下载",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=DEFAULT_MODELS_DIR,
        help=f"模型目录（默认: {DEFAULT_MODELS_DIR}）",
    )
    args = parser.parse_args(argv)

    models_dir: Path = args.models_dir
    models_dir.mkdir(parents=True, exist_ok=True)

    if args.check:
        return 0 if _check_only(models_dir) else 1

    print(f"模型目录: {models_dir}")
    print(f"待处理 {len(_MODELS)} 个文件\n")

    failures: list[str] = []
    for model in _MODELS:
        try:
            msg = _process_model(model, models_dir, force=args.force)
            print(f"  {msg}\n")
        except Exception as exc:  # 汇总所有失败而非首错即退
            failures.append(f"{model.name}: {exc}")
            print(f"  失败: {model.name}: {exc}\n")

    if failures:
        print(f"失败 {len(failures)}/{len(_MODELS)} 个文件:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"全部完成 ({len(_MODELS)}/{len(_MODELS)})。OCR 模型就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
