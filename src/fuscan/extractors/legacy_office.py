"""旧版 Microsoft Office 提取器：XLS、DOC、PPT。

XLS 通过 calamine（Rust + PyO3）读取 Excel 97-2003 工作簿，与 XLSX
共用同一 Rust 后端（``_extract_calamine_workbook``）。DOC/PPT 通过 OLE
复合文档解析（fuscan-core cfb crate 或 olefile 回退），从文本流中提取
UTF-16LE 编码内容（T3 中速）。

注意：DOC/PPT 为二进制格式，本提取器仅做简单文本提取，不支持复杂格式
（如修订、嵌入对象等）。如需完整提取，建议先转换为 DOCX/PPTX。
"""

from __future__ import annotations

import io
import logging
import re

from typing_extensions import override

from fuscan.extractors.base import Extractor, ExtractorError, SpeedTier

__all__ = ["DocExtractor", "PptExtractor", "XlsExtractor"]

logger = logging.getLogger(__name__)

# fuscan-core 原生 OLE 解析：cfb crate，py.detach 释放 GIL。
# 缺失时回退 olefile，不影响功能。
try:
    from fuscan_core import (
        extract_ole_stream as _native_extract_ole_stream,  # pyrefly: ignore [missing-module-attribute]
    )

    _NATIVE_OLE_AVAILABLE: bool = True
except ImportError:  # pragma: no cover - fuscan_core 未安装时走此分支
    _NATIVE_OLE_AVAILABLE = False
    _native_extract_ole_stream = None  # type: ignore[assignment,misc]

# UTF-16LE 可打印字符的字节模式（小端序，低字节在前）：
# - ASCII 可打印（U+0020-U+007E）：低字节 [\x20-\x7E]，高字节 \x00
# - CJK 统一汉字（U+4E00-U+9FFF）：低字节任意，高字节 [\x4E-\x9F]
# - 全角标点（U+3000-U+30FF）：低字节任意，高字节 \x30
# 连续 2 个以上可打印字符构成一个文本片段
_UTF16LE_RUN = re.compile(rb"(?:[\x20-\x7E]\x00|[\x00-\xFF][\x4E-\x9F]|[\x00-\xFF]\x30){2,}")


def _extract_utf16le_text(data: bytes) -> str:
    """从二进制数据中提取 UTF-16LE 编码的文本片段。

    用正则 ``re.finditer`` 一次性扫描字节流，匹配连续的可打印 UTF-16LE
    字符序列（ASCII + CJK 汉字 + 全角标点），跳过不可打印的控制字符。
    相比逐字节 Python 循环，正则引擎在 C 层完成匹配，性能提升 3-5x。

    :param data: 二进制流内容
    :return: 提取的纯文本，片段以换行分隔
    """
    if len(data) < 2:
        return ""

    parts: list[str] = []
    for match in _UTF16LE_RUN.finditer(data):
        try:
            text = match.group().decode("utf-16-le").strip()
        except UnicodeDecodeError:  # pragma: no cover - UTF-16LE 偶长度字节不解码失败
            continue
        if len(text) >= 2:
            parts.append(text)

    return "\n".join(parts)


def _read_ole_stream(data: bytes, stream_name: str) -> bytes | None:
    """读取 OLE 复合文档中指定流的字节内容。

    优先使用 fuscan-core 原生引擎（cfb crate，``py.detach`` 释放 GIL），
    缺失时回退 olefile。流不存在时返回 ``None``，与
    ``olefile.OleFileIO.exists(name)`` 行为一致。

    :param data: OLE 复合文档字节内容
    :param stream_name: 流名称（如 ``"WordDocument"`` / ``"PowerPoint Document"``）
    :return: 流的字节内容；流不存在返回 None
    :raises OSError: olefile 路径解析失败（OLE 格式非法）
    :raises ValueError: 原生路径解析失败（cfb crate 错误，PyValueError 子类）
    :raises ImportError: 原生引擎不可用且 olefile 未安装
    """
    if _NATIVE_OLE_AVAILABLE:
        assert _native_extract_ole_stream is not None
        return _native_extract_ole_stream(data, stream_name)

    import olefile  # 抛 ImportError 由调用方处理

    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        if ole.exists(stream_name):
            return ole.openstream(stream_name).read()
        return None
    finally:
        ole.close()


def _extract_ole_text(data: bytes, stream_name: str, error_label: str) -> str:
    """从 OLE 复合文档中提取指定流的 UTF-16LE 文本。

    统一 :class:`DocExtractor` 与 :class:`PptExtractor` 的 OLE 解析逻辑：
    打开 OLE 复合文档 → 检查指定流是否存在 → 读取流内容 → UTF-16LE 正则扫描。
    无指定流时返回空字符串（部分老版本文档结构差异）。

    :param data: OLE 复合文档字节内容
    :param stream_name: 流名称（如 ``"WordDocument"`` / ``"PowerPoint Document"``）
    :param error_label: 错误信息前缀（如 ``"DOC"`` / ``"PPT"``）
    :return: 提取的文本；无指定流返回空字符串
    :raises ExtractorError: olefile 未安装或 OLE 解析失败
    """
    try:
        stream_data = _read_ole_stream(data, stream_name)
    except ImportError as exc:
        raise ExtractorError(f"olefile 未安装，无法提取 {error_label}") from exc
    except (OSError, ValueError) as exc:
        raise ExtractorError(f"{error_label} 解析失败: {exc}") from exc

    if stream_data is None:
        logger.debug("%s 文件无 %s 流", error_label, stream_name)
        return ""

    return _extract_utf16le_text(stream_data)


class XlsExtractor(Extractor):
    """XLS (Excel 97-2003) 工作簿文本提取器。

    切换到 calamine (Rust + PyO3) 后端，从 T4 慢速降至 T2 快速，
    与 XLSX/ODS 共用同一 Rust 后端。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 XLS 提取器支持的扩展名。"""
        return ("xls",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """calamine (Rust + PyO3) 释放 GIL，T2 快速。"""
        return SpeedTier.FAST

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "Excel（XLS）"

    @override
    @property
    def engine_info(self) -> str:
        """python-calamine (Rust + PyO3)。"""
        return "python-calamine"

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节解析 XLS 工作簿。"""
        from fuscan.extractors.spreadsheet import _extract_calamine_workbook

        return _extract_calamine_workbook(data, error_label="XLS")


class DocExtractor(Extractor):
    """DOC (Word 97-2003) 文档文本提取器。

    通过 OLE 复合文档解析（fuscan-core cfb crate 或 olefile 回退）读取
    WordDocument 流，提取 UTF-16LE 编码的文本（T3 中速）。仅做简单文本
    提取，不解析复杂格式。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 DOC 提取器支持的扩展名。"""
        return ("doc",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """OLE 解析 + UTF-16LE 正则扫描，T3 中速。"""
        return SpeedTier.MEDIUM

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "Word（DOC）"

    @override
    @property
    def engine_info(self) -> str:
        """OLE 解析引擎：fuscan-core cfb 原生优先，缺失时 olefile。"""
        return "fuscan-core (cfb)" if _NATIVE_OLE_AVAILABLE else "olefile"

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节解析 DOC 文档（OLE 解析 + UTF-16LE 正则扫描）。"""
        return _extract_ole_text(data, stream_name="WordDocument", error_label="DOC")


class PptExtractor(Extractor):
    """PPT (PowerPoint 97-2003) 演示文稿文本提取器。

    通过 OLE 复合文档解析（fuscan-core cfb crate 或 olefile 回退）读取
    PowerPoint Document 流，提取 UTF-16LE 编码的文本（T3 中速）。仅做
    简单文本提取，不解析幻灯片结构。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 PPT 提取器支持的扩展名。"""
        return ("ppt",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """OLE 解析 + UTF-16LE 正则扫描，T3 中速。"""
        return SpeedTier.MEDIUM

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "PowerPoint（PPT）"

    @override
    @property
    def engine_info(self) -> str:
        """OLE 解析引擎：fuscan-core cfb 原生优先，缺失时 olefile。"""
        return "fuscan-core (cfb)" if _NATIVE_OLE_AVAILABLE else "olefile"

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节解析 PPT 演示文稿（OLE 解析 + UTF-16LE 正则扫描）。"""
        return _extract_ole_text(data, stream_name="PowerPoint Document", error_label="PPT")
