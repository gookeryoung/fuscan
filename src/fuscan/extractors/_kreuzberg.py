"""kreuzberg 加速后端适配层（iter-126）。

kreuzberg 是基于 Rust 的高性能文档提取库（MIT License），支持 91+ 格式，
包括 DOC/PPT/RTF/MSG 等 fuscan 当前用纯 Python 解析的格式。

本模块提供统一的 kreuzberg 可用性检测与文本提取接口：

- :func:`is_available`：检测 kreuzberg 是否已安装
- :func:`extract_text`：同步提取文件文本内容（kreuzberg 不可用时抛 ``RuntimeError``）

各提取器（DOC/PPT/RTF/MSG）在 kreuzberg 可用时优先调用 :func:`extract_text`，
不可用时回退到原有纯 Python 实现。SpeedTier 据 kreuzberg 可用性动态调整：
可用时 T2 快速（Rust 核心），不可用时保持原档次。

kreuzberg 是**可选依赖**（``pip install fuscan[fast]``），未安装时自动回退，
不影响基础功能。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["extract_text", "is_available"]


def is_available() -> bool:
    """检测 kreuzberg 库是否已安装且可导入。

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
