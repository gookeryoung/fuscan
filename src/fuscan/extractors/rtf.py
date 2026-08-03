"""RTF 富文本提取器。

使用 striprtf 库将 RTF 转换为纯文本，保留可见文字内容。

kreuzberg 可用时优先使用 Rust 核心加速提取（T2 快速），
不可用时回退到 striprtf 纯 Python 实现（T3 中速）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from typing_extensions import override

from fuscan.extractors._kreuzberg import extract_text as kreuzberg_extract
from fuscan.extractors._kreuzberg import extract_text_from_bytes as kreuzberg_extract_bytes
from fuscan.extractors._kreuzberg import is_available as kreuzberg_available
from fuscan.extractors.base import Extractor, ExtractorError, SpeedTier

__all__ = ["RtfExtractor"]

logger = logging.getLogger(__name__)


class RtfExtractor(Extractor):
    """RTF 富文本文件文本提取器。"""

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 RTF 提取器支持的扩展名。"""
        return ("rtf",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """kreuzberg 可用时 T2 快速（Rust 核心），否则 T3 中速（striprtf）。"""
        if kreuzberg_available():
            return SpeedTier.FAST
        return SpeedTier.MEDIUM

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "RTF"

    @override
    @property
    def engine_info(self) -> str:
        """kreuzberg 可用时优先，回退 striprtf。"""
        return "kreuzberg" if kreuzberg_available() else "striprtf"

    @override
    def extract(self, path: Path) -> str:
        """提取 RTF 文件纯文本内容。

        kreuzberg 可用时优先使用 Rust 核心加速提取；不可用时回退到 striprtf。
        """
        if kreuzberg_available():
            try:
                return kreuzberg_extract(path)
            except RuntimeError as exc:
                logger.debug("kreuzberg RTF 提取失败，回退到 striprtf: %s: %s", path, exc)
        # 回退：striprtf 纯 Python 实现
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc
        return self.extract_from_bytes(data)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节提取 RTF 纯文本。

        kreuzberg 可用时通过临时文件走 Rust 核心加速（压缩包内条目同样加速），
        不可用时回退到 striprtf 纯 Python 实现。
        """
        if kreuzberg_available():
            try:
                return kreuzberg_extract_bytes(data, "rtf")
            except RuntimeError as exc:
                logger.debug("kreuzberg RTF bytes 提取失败，回退到 striprtf: %s", exc)
        # 回退：striprtf 纯 Python 实现
        try:
            from striprtf.striprtf import rtf_to_text
        except ImportError as exc:
            raise ExtractorError("striprtf 未安装，无法提取 RTF") from exc

        try:
            text = data.decode("utf-8", errors="ignore")
            return rtf_to_text(text)
        except Exception as exc:
            raise ExtractorError(f"RTF 解析失败: {exc}") from exc
