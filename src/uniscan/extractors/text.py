"""纯文本提取器。

使用 charset-normalizer 自动检测编码，支持 BOM 处理与最大读取限制。
覆盖常见纯文本与代码文件格式。
"""

from __future__ import annotations

import logging
from pathlib import Path

from uniscan.extractors.base import Extractor, ExtractorError

__all__ = ["TEXT_EXTENSIONS", "TextExtractor"]

logger = logging.getLogger(__name__)

# 支持的纯文本扩展名（不含点，小写）
TEXT_EXTENSIONS: tuple[str, ...] = (
    "txt",
    "log",
    "md",
    "rst",
    "conf",
    "ini",
    "cfg",
    "properties",
    "yaml",
    "yml",
    "json",
    "xml",
    "csv",
    "tsv",
    "html",
    "htm",
    "sql",
    "py",
    "js",
    "ts",
    "jsx",
    "tsx",
    "java",
    "c",
    "cpp",
    "h",
    "hpp",
    "cs",
    "go",
    "rs",
    "rb",
    "php",
    "sh",
    "bash",
    "bat",
    "cmd",
    "ps1",
    "tex",
    "bib",
    "toml",
    "env",
    "gitignore",
    "dockerignore",
    "gradle",
    "kt",
    "swift",
    "scala",
    "lua",
    "pl",
    "r",
    "dart",
    "vue",
    "svelte",
    "scss",
    "sass",
    "less",
    "css",
)

_DEFAULT_MAX_SIZE = 50 * 1024 * 1024  # 50MB


class TextExtractor(Extractor):
    """纯文本提取器：自动检测编码，支持 BOM 与大小限制。"""

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        self._max_size = max_size

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return TEXT_EXTENSIONS

    def extract(self, path: Path) -> str:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ExtractorError(f"无法读取文件大小: {path}: {exc}") from exc

        if size > self._max_size:
            logger.debug("文件过大，跳过提取: %s (%d bytes)", path, size)
            return ""

        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc

        if not data:
            return ""

        return self._decode(data)

    def _decode(self, data: bytes) -> str:
        """检测编码并解码字节流。"""
        try:
            from charset_normalizer import from_bytes

            result = from_bytes(data).best()
            if result is not None:
                return str(result)
        except ImportError:
            logger.warning("charset-normalizer 未安装，回退到 UTF-8 解码")
        except Exception:
            logger.warning("编码检测失败，回退到 UTF-8 解码", exc_info=True)

        # 回退：尝试 UTF-8 和 GBK，最终用 latin-1（能解码任意字节序列，永不失败）
        for encoding in ("utf-8", "gbk"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("latin-1")
