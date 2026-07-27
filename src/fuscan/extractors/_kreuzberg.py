"""kreuzberg 加速后端适配层（iter-126）。

kreuzberg 是基于 Rust 的高性能文档提取库（MIT License），支持 91+ 格式，
包括 DOC/PPT/RTF/MSG 等 fuscan 当前用纯 Python 解析的格式。

本模块提供统一的 kreuzberg 可用性检测与文本提取接口：

- :func:`is_available`：检测 kreuzberg 是否已安装
- :func:`extract_text`：同步提取文件文本内容（kreuzberg 不可用时抛 ``RuntimeError``）
- :func:`extract_text_from_bytes`：从内存字节提取（写入临时文件后调用 kreuzberg）

各提取器（DOC/PPT/RTF/MSG）在 kreuzberg 可用时：

- 磁盘文件通过 :meth:`Extractor.extract` 调用 :func:`extract_text`
- 压缩包内条目通过 :meth:`Extractor.extract_from_bytes` 调用
  :func:`extract_text_from_bytes`（iter-127 起支持）

SpeedTier 据 kreuzberg 可用性动态调整：可用时 T2 快速（Rust 核心），
不可用时保持原档次。

kreuzberg 是**可选依赖**（``pip install fuscan[fast]``），未安装时自动回退，
不影响基础功能。
"""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path

__all__ = ["extract_text", "extract_text_from_bytes", "is_available"]


@lru_cache(maxsize=1)
def is_available() -> bool:
    """检测 kreuzberg 库是否已安装且可导入。

    结果用 :func:`functools.lru_cache` 缓存（进程内不会变化），
    避免 :meth:`Extractor.speed_tier` 等热路径每次 ``try import`` 的开销。

    :return: kreuzberg 可用返回 ``True``，否则 ``False``
    """
    try:
        import kreuzberg  # noqa: F401  # pyrefly: ignore [missing-import]
    except ImportError:
        return False
    return True


def extract_text(path: Path) -> str:
    """使用 kreuzberg 同步提取文件文本内容。

    :param path: 文件路径
    :return: 提取的文本内容（``str``）
    :raises RuntimeError: kreuzberg 未安装或提取失败
    """
    try:
        from kreuzberg import extract_file_sync  # pyrefly: ignore [missing-import]
    except ImportError as exc:
        raise RuntimeError("kreuzberg 未安装") from exc

    try:
        result = extract_file_sync(str(path))
    except Exception as exc:
        raise RuntimeError(f"kreuzberg 提取失败: {path}: {exc}") from exc

    # result.content 为提取的文本（str）
    content: str = result.content if hasattr(result, "content") else str(result)
    return content


def extract_text_from_bytes(data: bytes, extension: str) -> str:
    """从内存字节提取文本：写入临时文件后调用 kreuzberg。

    kreuzberg 的 ``extract_file_sync`` 仅接受文件路径，压缩包内条目需通过
    临时文件中转。临时文件带正确扩展名，便于 kreuzberg 识别格式。

    :param data: 文件完整字节内容
    :param extension: 文件扩展名（不含点，如 ``"rtf"``/``"doc"``）
    :return: 提取的文本内容（``str``）
    :raises RuntimeError: kreuzberg 未安装或提取失败
    """
    suffix = f".{extension}" if extension else ""
    fd, tmp_path_str = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return extract_text(Path(tmp_path_str))
    finally:
        try:
            os.unlink(tmp_path_str)
        except OSError:  # pragma: no cover - 临时文件清理失败无需上报
            pass
