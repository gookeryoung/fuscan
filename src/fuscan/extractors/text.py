"""纯文本提取器。

编码检测优先使用 fuscan-core 原生引擎（encoding_rs + chardetng，释放 GIL），
缺失时回退 charset-normalizer。支持 BOM 处理与最大读取限制，
覆盖常见纯文本与代码文件格式。

大文件（>10MB）采用分块流式读取 + 增量解码，跳过全量编码分析以降低内存峰值。

GUI 文件类型树中文本类别仅展示「纯文本」与「源代码」两项，
原 ``ConfigFileExtractor``/``MarkupDataExtractor``/``StylesheetExtractor``
的扩展名（配置文件/标记数据/样式表）合并到 :class:`SourceCodeExtractor`，
避免文件类型树过度细分。``TextExtractor`` 保留为基类提供提取逻辑，不再直接注册。
"""

from __future__ import annotations

import codecs
import logging
from pathlib import Path

from typing_extensions import override

from fuscan.extractors.base import Extractor, ExtractorError, SpeedTier

__all__ = [
    "PLAIN_TEXT_EXTENSIONS",
    "SOURCE_CODE_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "PlainTextExtractor",
    "SourceCodeExtractor",
    "TextExtractor",
]

logger = logging.getLogger(__name__)

# fuscan-core 原生编码检测：encoding_rs + chardetng，py.detach 释放 GIL。
# 缺失时回退 charset-normalizer，不影响功能。
try:
    from fuscan_core import decode_bytes as _native_decode_bytes  # pyrefly: ignore [missing-module-attribute]

    _NATIVE_DECODE_AVAILABLE: bool = True
except ImportError:  # pragma: no cover - fuscan_core 未安装时走此分支
    _NATIVE_DECODE_AVAILABLE = False
    _native_decode_bytes = None  # type: ignore[assignment,misc]

# 纯文本扩展名（不含点，小写）
PLAIN_TEXT_EXTENSIONS: tuple[str, ...] = (
    "txt",
    "log",
)

# 源代码扩展名（编程语言 + 脚本 + 配置文件 + 标记数据 + 样式表）
# 合并原 ConfigFile/MarkupData/Stylesheet 三类文本子提取器，
# GUI 文件类型树中文本类别仅保留「纯文本」「源代码」两项，简化勾选界面。
SOURCE_CODE_EXTENSIONS: tuple[str, ...] = (
    # 编程语言 + 脚本
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
    # 配置文件
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
    # 标记与数据文件
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
    # 样式表
    "css",
    "scss",
    "sass",
    "less",
)

# 全部纯文本扩展名（向后兼容）
TEXT_EXTENSIONS: tuple[str, ...] = (
    *PLAIN_TEXT_EXTENSIONS,
    *SOURCE_CODE_EXTENSIONS,
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

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """纯文本解码为 T1 极速（charset-normalizer + 字节解码）。"""
        return SpeedTier.VERY_FAST

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "纯文本"

    @override
    @property
    def engine_info(self) -> str:
        """编码检测引擎：fuscan-core 原生优先，缺失时 charset-normalizer。"""
        return "fuscan-core" if _NATIVE_DECODE_AVAILABLE else "charset-normalizer"

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

        解码优先级：

        1. **头部快路径**（BOM / 整段严格 UTF-8）：命中即跳过全量编码分析。
           为零误判，仅当 BOM 明确或整段字节严格 UTF-8 解码成功
           （纯 ASCII 属其子集）时走快路径；GBK 等无法确证的编码不走，
           避免「头部纯 ASCII 但正文 GBK」被 UTF-8 ``errors="ignore"`` 误吞。
        2. **大 bytes（>10MB）头部检测**：超阈值文件用文件头启发式检测编码。
        3. **原生编码检测**（fuscan-core）：encoding_rs + chardetng 统计检测，
           ``py.detach`` 释放 GIL；缺失/异常时回退 charset-normalizer。
        4. **charset-normalizer 精确检测**：原生不可用时的 Python 回退。
        """
        fast = _fast_decode(data)
        if fast is not None:
            return _normalize_newlines(fast)

        if len(data) > _LARGE_FILE_THRESHOLD:
            encoding = _detect_encoding_from_header(data[:_HEADER_SIZE])
            if encoding is not None:
                return _normalize_newlines(data.decode(encoding, errors="ignore"))

        # 原生编码检测优先（fuscan-core），缺失/异常回退 charset-normalizer
        if _NATIVE_DECODE_AVAILABLE:
            try:
                assert _native_decode_bytes is not None
                return _normalize_newlines(_native_decode_bytes(data))
            except Exception:
                logger.warning("原生编码检测失败，回退 charset-normalizer", exc_info=True)

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

    从 ``TextExtractor`` 拆分，提取逻辑继承基类，
    仅限定支持的扩展名子集与显示名。
    display_name 包含全角括号后缀 ``（TXT）``，供 GUI 提取格式 TAG。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        return PLAIN_TEXT_EXTENSIONS

    @override
    @property
    def display_name(self) -> str:
        return "纯文本（TXT）"

    @override
    @property
    def engine_info(self) -> str:
        """编码检测引擎：fuscan-core 原生优先，缺失时 charset-normalizer。"""
        return "fuscan-core" if _NATIVE_DECODE_AVAILABLE else "charset-normalizer"


class SourceCodeExtractor(TextExtractor):
    """源代码子提取器：处理编程语言、脚本、配置文件、标记数据与样式表。

    合并原 ConfigFile/MarkupData/Stylesheet 三类子提取器，
    GUI 文件类型树中文本类别仅展示「纯文本」「源代码」两项，
    避免勾选界面过度细分。提取逻辑继承基类。
    display_name 包含全角括号后缀 ``（CODE）``，供 GUI 提取格式 TAG。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        return SOURCE_CODE_EXTENSIONS

    @override
    @property
    def display_name(self) -> str:
        return "源代码（CODE）"

    @override
    @property
    def engine_info(self) -> str:
        """编码检测引擎：fuscan-core 原生优先，缺失时 charset-normalizer。"""
        return "fuscan-core" if _NATIVE_DECODE_AVAILABLE else "charset-normalizer"


def _fast_decode(data: bytes) -> str | None:
    """头部快路径解码：仅对可确证的编码返回解码结果，否则返回 None。

    命中条件（保守，零误判）：

    - **BOM 明确**：UTF-8-SIG / UTF-32 / UTF-16，直接按对应编码解码整段。
    - **整段严格 UTF-8**：无 BOM 时对整段字节做严格 UTF-8 解码，成功即确证
      为 UTF-8（纯 ASCII 属其子集）。对整段而非仅头部解码，规避多字节字符
      在取样边界被截断的误判。

    GBK 等无法仅凭字节确证的编码返回 None，交由 charset-normalizer 统计检测，
    避免「头部纯 ASCII 但正文 GBK 中文」被 UTF-8 ``errors="ignore"`` 误吞。

    :param data: 完整文件字节
    :return: 命中时返回解码字符串；未命中返回 None
    """
    # BOM 检测（UTF-32 须在 UTF-16 前检查，其 BOM 是 UTF-16 BOM 的扩展）
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="ignore")
    if data.startswith(b"\xff\xfe\x00\x00") or data.startswith(b"\x00\x00\xfe\xff"):
        return data.decode("utf-32", errors="ignore")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="ignore")
    # 无 BOM：整段严格 UTF-8 解码，成功即确证 UTF-8
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


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
