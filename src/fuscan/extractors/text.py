"""纯文本提取器。

使用 charset-normalizer 自动检测编码，支持 BOM 处理与最大读取限制。
覆盖常见纯文本与代码文件格式。

大文件（>10MB）采用分块流式读取 + 增量解码，跳过 charset-normalizer
全量分析以降低内存峰值。

iter-88 起将原 ``TextExtractor``（57 个扩展名）拆分为 5 个子提取器，
各自管理一类文本扩展名，在 GUI 勾选树中作为独立分类展示：

- :class:`PlainTextExtractor`：纯文本（txt, log）
- :class:`SourceCodeExtractor`：源代码（py, js, c, java, ...）
- :class:`ConfigFileExtractor`：配置文件（ini, yaml, toml, ...）
- :class:`MarkupDataExtractor`：标记与数据（md, json, html, ...）
- :class:`StylesheetExtractor`：样式表（css, scss, sass, less）

``TextExtractor`` 保留为基类提供提取逻辑，不再直接注册到默认注册表。
"""

from __future__ import annotations

import codecs
import logging
from pathlib import Path

from typing_extensions import override

from fuscan.extractors.base import Extractor, ExtractorError

__all__ = [
    "CONFIG_FILE_EXTENSIONS",
    "MARKUP_DATA_EXTENSIONS",
    "PLAIN_TEXT_EXTENSIONS",
    "SOURCE_CODE_EXTENSIONS",
    "STYLESHEET_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "ConfigFileExtractor",
    "MarkupDataExtractor",
    "PlainTextExtractor",
    "SourceCodeExtractor",
    "StylesheetExtractor",
    "TextExtractor",
]

logger = logging.getLogger(__name__)

# 纯文本扩展名（不含点，小写）
PLAIN_TEXT_EXTENSIONS: tuple[str, ...] = (
    "txt",
    "log",
)

# 源代码扩展名（编程语言 + 脚本）
SOURCE_CODE_EXTENSIONS: tuple[str, ...] = (
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
    "kt",
    "swift",
    "scala",
    "lua",
    "pl",
    "r",
    "dart",
    "vue",
    "svelte",
    "sh",
    "bash",
    "bat",
    "cmd",
    "ps1",
)

# 配置文件扩展名
CONFIG_FILE_EXTENSIONS: tuple[str, ...] = (
    "conf",
    "ini",
    "cfg",
    "properties",
    "yaml",
    "yml",
    "toml",
    "env",
    "gradle",
    "gitignore",
    "dockerignore",
)

# 标记与数据文件扩展名
MARKUP_DATA_EXTENSIONS: tuple[str, ...] = (
    "md",
    "rst",
    "html",
    "htm",
    "tex",
    "bib",
    "json",
    "xml",
    "csv",
    "tsv",
    "sql",
)

# 样式表扩展名
STYLESHEET_EXTENSIONS: tuple[str, ...] = (
    "css",
    "scss",
    "sass",
    "less",
)

# 全部纯文本扩展名（5 组并集，向后兼容）
TEXT_EXTENSIONS: tuple[str, ...] = (
    *PLAIN_TEXT_EXTENSIONS,
    *SOURCE_CODE_EXTENSIONS,
    *CONFIG_FILE_EXTENSIONS,
    *MARKUP_DATA_EXTENSIONS,
    *STYLESHEET_EXTENSIONS,
)

_DEFAULT_MAX_SIZE = 100 * 1024 * 1024  # 100MB
_LARGE_FILE_THRESHOLD = 10 * 1024 * 1024  # 10MB：超过此阈值启用流式读取
_HEADER_SIZE = 65536  # 64KB：编码检测取样大小
_CHUNK_SIZE = 4 * 1024 * 1024  # 4MB：流式读取分块大小


class TextExtractor(Extractor):
    """纯文本提取器：自动检测编码，支持 BOM 与大小限制。

    大文件（>10MB）用分块读取 + 增量解码降低内存峰值，
    小文件用 charset-normalizer 精确检测编码。
    """

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        self._max_size = max_size

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回纯文本提取器支持的扩展名。"""
        return TEXT_EXTENSIONS

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "纯文本"

    @override
    def extract(self, path: Path) -> str:
        """提取纯文本内容，自动检测编码并应用大小限制。"""
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ExtractorError(f"无法读取文件大小: {path}: {exc}") from exc

        if size > self._max_size:
            logger.debug("文件过大，跳过提取: %s (%d bytes)", path, size)
            return ""

        if size > _LARGE_FILE_THRESHOLD:
            return self._extract_large(path)

        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc

        return self.extract_from_bytes(data)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节提取纯文本内容，自动检测编码并应用大小限制。"""
        if len(data) > self._max_size:
            logger.debug("数据过大，跳过提取: %d bytes", len(data))
            return ""
        if not data:
            return ""
        return self._decode(data)

    def _extract_large(self, path: Path) -> str:
        """流式读取大文件，分块解码降低内存峰值。

        用文件头检测编码后，以 ``IncrementalDecoder`` 分块解码，
        避免 ``read_bytes`` 一次性分配和 charset-normalizer 全量分析。
        文件头无法确定编码时回退到全量读取 + charset-normalizer。
        """
        try:
            with path.open("rb") as fh:
                header = fh.read(_HEADER_SIZE)
                fh.seek(0)
                encoding = _detect_encoding_from_header(header)
                if encoding is None:
                    # 文件头无法确定编码，回退到全量读取 + charset-normalizer
                    data = fh.read()
                    return self._decode(data)
                decoder = codecs.getincrementaldecoder(encoding)(errors="ignore")
                parts: list[str] = []
                while True:
                    chunk = fh.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    parts.append(decoder.decode(chunk))
                parts.append(decoder.decode(b"", final=True))
            return _normalize_newlines("".join(parts))
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc

    def _decode(self, data: bytes) -> str:
        """检测编码并解码字节流。

        统一行尾为 ``\\n``：Windows 上 ``write_text`` 会将 ``\\n`` 写为 ``\\r\\n``，
        若不规范化会导致 CONTENT EQUALS 等严格比较在跨平台时失败。

        大 bytes（>10MB）用文件头检测编码，跳过 charset-normalizer 全量分析；
        小 bytes 用 charset-normalizer 精确检测。
        """
        if len(data) > _LARGE_FILE_THRESHOLD:
            encoding = _detect_encoding_from_header(data[:_HEADER_SIZE])
            if encoding is not None:
                return _normalize_newlines(data.decode(encoding, errors="ignore"))

        try:
            from charset_normalizer import from_bytes

            result = from_bytes(data).best()
            if result is not None:
                return _normalize_newlines(str(result))
        except ImportError:
            logger.warning("charset-normalizer 未安装，回退到 UTF-8 解码")
        except Exception:
            logger.warning("编码检测失败，回退到 UTF-8 解码", exc_info=True)

        # 回退：尝试 UTF-8 和 GBK，最终用 latin-1（能解码任意字节序列，永不失败）
        for encoding in ("utf-8", "gbk"):
            try:
                return _normalize_newlines(data.decode(encoding))
            except UnicodeDecodeError:
                continue
        return _normalize_newlines(data.decode("latin-1"))


class PlainTextExtractor(TextExtractor):
    """纯文本子提取器：处理 txt/log 等基础文本文件。

    iter-88 从 ``TextExtractor`` 拆分，提取逻辑继承基类，
    仅限定支持的扩展名子集与显示名。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        return PLAIN_TEXT_EXTENSIONS

    @override
    @property
    def display_name(self) -> str:
        return "纯文本"


class SourceCodeExtractor(TextExtractor):
    """源代码子提取器：处理 py/js/java/c 等编程语言与脚本文件。

    iter-88 从 ``TextExtractor`` 拆分，提取逻辑继承基类，
    仅限定支持的扩展名子集与显示名。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        return SOURCE_CODE_EXTENSIONS

    @override
    @property
    def display_name(self) -> str:
        return "源代码"


class ConfigFileExtractor(TextExtractor):
    """配置文件子提取器：处理 ini/yaml/toml 等配置文件。

    iter-88 从 ``TextExtractor`` 拆分，提取逻辑继承基类，
    仅限定支持的扩展名子集与显示名。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        return CONFIG_FILE_EXTENSIONS

    @override
    @property
    def display_name(self) -> str:
        return "配置文件"


class MarkupDataExtractor(TextExtractor):
    """标记与数据子提取器：处理 md/json/xml/html 等标记与数据文件。

    iter-88 从 ``TextExtractor`` 拆分，提取逻辑继承基类，
    仅限定支持的扩展名子集与显示名。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        return MARKUP_DATA_EXTENSIONS

    @override
    @property
    def display_name(self) -> str:
        return "标记与数据"


class StylesheetExtractor(TextExtractor):
    """样式表子提取器：处理 css/scss/sass/less 样式文件。

    iter-88 从 ``TextExtractor`` 拆分，提取逻辑继承基类，
    仅限定支持的扩展名子集与显示名。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        return STYLESHEET_EXTENSIONS

    @override
    @property
    def display_name(self) -> str:
        return "样式表"


def _detect_encoding_from_header(header: bytes) -> str | None:
    """从文件头检测编码（BOM 优先，否则尝试 UTF-8/GBK 启发式）。

    :param header: 文件头字节（建议 >= 64KB 以提高检测准确性）
    :return: 编码名（如 ``"utf-8"``、``"gbk"``），无法确定时返回 ``None``
    """
    # BOM 检测（UTF-32 须在 UTF-16 前检查，因其 BOM 是 UTF-16 BOM 的扩展）
    if header.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if header.startswith(b"\xff\xfe\x00\x00") or header.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32"
    if header.startswith(b"\xff\xfe") or header.startswith(b"\xfe\xff"):
        return "utf-16"
    # 启发式：尝试 UTF-8 严格解码文件头
    try:
        header.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    # 尝试 GBK（Windows 中文环境常见）
    try:
        header.decode("gbk")
        return "gbk"
    except UnicodeDecodeError:
        pass
    return None


def _normalize_newlines(text: str) -> str:
    """将 CRLF/CR 统一为 LF，保证跨平台内容比较一致。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")
