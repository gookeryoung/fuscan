"""邮件文件提取器：EML 与 MSG。

EML 使用标准库 email 解析，提取主题、发件人与正文。
MSG 使用 extract-msg 库解析 Outlook 邮件格式。

iter-126：MSG 在 kreuzberg 可用时优先使用 Rust 核心加速提取（T2 快速），
不可用时回退到 extract-msg 纯 Python 实现（T3 中速）。
"""

from __future__ import annotations

import email
import email.policy
import io
import logging
import re
from email.message import Message
from pathlib import Path

from typing_extensions import override

from fuscan.extractors._kreuzberg import extract_text as kreuzberg_extract
from fuscan.extractors._kreuzberg import is_available as kreuzberg_available
from fuscan.extractors.base import Extractor, ExtractorError, SpeedTier

__all__ = ["EmlExtractor", "MsgExtractor"]

logger = logging.getLogger(__name__)

# 匹配 HTML 标签的正则，用于从 text/html 部分提取纯文本
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# 连续空白压缩为单个空格
_WS_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    """去除 HTML 标签并压缩空白。"""
    text = _HTML_TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


class EmlExtractor(Extractor):
    """EML 邮件文件文本提取器。"""

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 EML 提取器支持的扩展名。"""
        return ("eml",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """EML 标准库 email 解析（含 multipart 遍历）为 T2 快速。"""
        return SpeedTier.FAST

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "邮件（EML）"

    @override
    def extract(self, path: Path) -> str:
        """提取 EML 邮件主题、发件人与正文。"""
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc
        return self.extract_from_bytes(data)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节解析 EML 邮件。"""
        try:
            msg = email.message_from_bytes(data, policy=email.policy.default)
        except Exception as exc:
            raise ExtractorError(f"EML 解析失败: {exc}") from exc

        parts: list[str] = []
        subject = msg.get("Subject", "")
        if subject:
            parts.append(f"主题: {subject}")
        sender = msg.get("From", "")
        if sender:
            parts.append(f"发件人: {sender}")

        body = self._extract_body(msg)
        if body:
            parts.append(body)

        return "\n".join(parts)

    def _extract_body(self, msg: Message) -> str:
        """提取邮件正文，优先 text/plain，回退到 text/html。

        :param msg: email.message.Message 对象
        :return: 正文纯文本；无正文返回空字符串
        """
        plain: str | None = None
        html: str | None = None

        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = part.get_content_disposition()
            if disposition == "attachment":
                continue
            if content_type == "text/plain" and plain is None:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        plain = payload.decode(charset, errors="ignore")  # pyrefly: ignore [missing-attribute]
                    except (LookupError, UnicodeDecodeError):
                        plain = payload.decode("utf-8", errors="ignore")  # pyrefly: ignore [missing-attribute]
            elif content_type == "text/html" and html is None:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html_text = payload.decode(charset, errors="ignore")  # pyrefly: ignore [missing-attribute]
                    except (LookupError, UnicodeDecodeError):
                        html_text = payload.decode("utf-8", errors="ignore")  # pyrefly: ignore [missing-attribute]
                    html = _strip_html(html_text)

        if plain:
            return plain.strip()
        if html:
            return html.strip()
        return ""


class MsgExtractor(Extractor):
    """Outlook MSG 邮件文件文本提取器。

    iter-126：kreuzberg 可用时优先使用 Rust 核心加速提取（T2 快速），
    不可用时回退到 extract-msg 纯 Python 实现（T3 中速）。
    """

    @property
    @override
    def supported_extensions(self) -> tuple[str, ...]:
        """返回 MSG 提取器支持的扩展名。"""
        return ("msg",)

    @property
    @override
    def speed_tier(self) -> SpeedTier:
        """kreuzberg 可用时 T2 快速（Rust 核心），否则 T3 中速（extract-msg）。"""
        if kreuzberg_available():
            return SpeedTier.FAST
        return SpeedTier.MEDIUM

    @override
    @property
    def display_name(self) -> str:
        """返回提取器的中文显示名称。"""
        return "Outlook 邮件（MSG）"

    @override
    def extract(self, path: Path) -> str:
        """提取 MSG 邮件主题、发件人与正文。

        kreuzberg 可用时优先使用 Rust 核心加速提取；不可用时回退到 extract-msg。
        """
        if kreuzberg_available():
            try:
                return kreuzberg_extract(path)
            except RuntimeError as exc:
                logger.debug("kreuzberg MSG 提取失败，回退到 extract-msg: %s: %s", path, exc)
        # 回退：extract-msg 纯 Python 实现
        try:
            from extract_msg import Message
        except ImportError as exc:
            raise ExtractorError("extract-msg 未安装，无法提取 MSG") from exc

        try:
            msg = Message(str(path))
        except Exception as exc:
            raise ExtractorError(f"MSG 解析失败: {exc}") from exc

        return self._extract_from_message(msg)

    @override
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节解析 MSG 邮件。"""
        try:
            from extract_msg import Message
        except ImportError as exc:
            raise ExtractorError("extract-msg 未安装，无法提取 MSG") from exc

        try:
            msg = Message(io.BytesIO(data))
        except Exception as exc:
            raise ExtractorError(f"MSG 解析失败: {exc}") from exc

        return self._extract_from_message(msg)

    def _extract_from_message(self, msg: object) -> str:
        """从 extract_msg.Message 对象提取文本。

        :param msg: extract_msg.Message 实例
        :return: 邮件主题、发件人与正文拼接的纯文本
        """
        parts: list[str] = []
        subject = getattr(msg, "subject", None)
        if subject:
            parts.append(f"主题: {subject}")
        sender = getattr(msg, "sender", None)
        if sender:
            parts.append(f"发件人: {sender}")
        body = getattr(msg, "body", None)
        if body:
            text = body if isinstance(body, str) else str(body)
            parts.append(text.strip())
        return "\n".join(parts)
