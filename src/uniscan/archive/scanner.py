"""压缩文件扫描器：对压缩包内条目应用规则集。

读取压缩包（ZIP/RAR）内文件，为每个条目构造合成 FileEntry，
通过临时文件复用已有提取器链，最终输出 ScanResult 列表。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from uniscan.archive.base import (
    ArchiveEntry,
    ArchiveError,
    ArchiveReader,
    get_reader,
)
from uniscan.extractors import extract_content
from uniscan.rules.model import Rule, RuleSet
from uniscan.scanner.context import FileEntry, MatchContext
from uniscan.scanner.matchers import Matcher, build_matcher
from uniscan.scanner.result import RuleHit, ScanResult

__all__ = ["ArchiveScanner"]

logger = logging.getLogger(__name__)


class ArchiveScanner:
    """压缩文件扫描器：对单个压缩包内所有文件应用规则。

    - 构造时一次性编译规则集为 Matcher 列表
    - 通过 :func:`get_reader` 工厂分发到 ZipReader/RarReader
    - 内容提取策略：读取条目字节 → 写入临时文件 → 调用 extract_content
    - 加密条目未提供密码时跳过并记录错误
    """

    def __init__(
        self,
        ruleset: RuleSet,
        password: Optional[str] = None,
        max_entry_size: int = 50 * 1024 * 1024,
    ) -> None:
        self._ruleset = ruleset
        self._password = password
        self._max_entry_size = max_entry_size
        self._compiled: List[Tuple[Rule, Matcher]] = [(rule, build_matcher(rule.match)) for rule in ruleset.rules]

    def scan_archive(self, archive_path: Path) -> Tuple[ScanResult, ...]:
        """扫描压缩包内所有条目，返回结果元组。

        压缩包无法打开时返回单条错误结果。
        """
        try:
            reader = get_reader(archive_path, password=self._password)
        except ArchiveError:
            logger.warning("打开压缩包失败: %s", archive_path, exc_info=True)
            return (
                ScanResult(
                    path=archive_path,
                    size=0,
                    hits=(),
                    errors=1,
                ),
            )
        if reader is None:
            logger.debug("无注册读取器，跳过压缩包: %s", archive_path)
            return ()

        try:
            entries = reader.list_entries()
        except ArchiveError:
            logger.warning("列出压缩包条目失败: %s", archive_path, exc_info=True)
            self._close_reader(reader)
            return (
                ScanResult(
                    path=archive_path,
                    size=0,
                    hits=(),
                    errors=1,
                ),
            )

        results: List[ScanResult] = []
        for entry in entries:
            if entry.is_dir:
                continue
            result = self._scan_entry(archive_path, entry, reader)
            results.append(result)

        self._close_reader(reader)
        return tuple(results)

    def _scan_entry(
        self,
        archive_path: Path,
        entry: ArchiveEntry,
        reader: ArchiveReader,
    ) -> ScanResult:
        """对压缩包内单个条目应用规则。"""
        file_entry = FileEntry(
            path=Path(entry.display_path),
            name=entry.name,
            size=entry.size,
            mtime=0.0,
            extension=entry.extension,
            is_dir=False,
        )

        def content_provider(fe: FileEntry) -> str:
            return self._read_entry_content(archive_path, entry, reader)

        context = MatchContext(file_entry, content_provider=content_provider)
        hits: List[RuleHit] = []
        rule_errors = 0

        for rule, matcher in self._compiled:
            if rule.file_extensions and entry.extension not in rule.file_extensions:
                continue
            try:
                result = matcher.matches(context)
            except Exception:
                rule_errors += 1
                logger.warning(
                    "规则 %s 求值失败 %s",
                    rule.name,
                    entry.display_path,
                    exc_info=True,
                )
                continue
            if result.matched:
                hits.append(RuleHit(rule_name=rule.name, severity=rule.severity, detail=result.detail))

        return ScanResult(
            path=file_entry.path,
            size=file_entry.size,
            hits=tuple(hits),
            errors=rule_errors,
        )

    def _read_entry_content(
        self,
        archive_path: Path,
        entry: ArchiveEntry,
        reader: ArchiveReader,
    ) -> str:
        """读取条目字节并通过临时文件复用提取器链。"""
        if entry.size > self._max_entry_size:
            logger.debug("条目过大，跳过内容提取: %s", entry.display_path)
            return ""

        try:
            data = reader.read_entry(entry.entry_name)
        except ArchiveError as exc:
            logger.info("读取压缩包条目失败: %s", exc)
            return ""

        if not data:
            return ""

        # 纯文本类条目直接解码
        if entry.extension in _TEXT_EXTENSIONS:
            return _decode_bytes(data)

        # 有注册提取器的格式走临时文件提取
        if _has_extractor(entry.extension):
            return self._extract_via_temp(data, entry)

        # 其他情况按文本解码
        return _decode_bytes(data)

    def _extract_via_temp(self, data: bytes, entry: ArchiveEntry) -> str:
        """写入临时文件并调用提取器。"""
        suffix = f".{entry.extension}" if entry.extension else ""
        fd, tmp_name = tempfile.mkstemp(suffix=suffix)
        tmp_path = Path(tmp_name)
        os.close(fd)
        try:
            tmp_path.write_bytes(data)
            try:
                return extract_content(tmp_path)
            except Exception:
                logger.debug("临时文件提取失败: %s", entry.display_path, exc_info=True)
                return _decode_bytes(data)
        finally:
            _safe_unlink(tmp_path)

    @staticmethod
    def _close_reader(reader: ArchiveReader) -> None:
        """安全关闭读取器。"""
        close = getattr(reader, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:  # pragma: no cover - 关闭异常无需上报
            logger.debug("关闭读取器失败", exc_info=True)


def _safe_unlink(path: Path) -> None:
    """安全删除文件，忽略 Windows 上的文件锁定错误。"""
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        logger.debug("临时文件被锁定，跳过删除: %s", path)


def _has_extractor(extension: str) -> bool:
    """检查扩展名是否有注册提取器。"""
    from uniscan.extractors import get_extractor

    return get_extractor(extension) is not None


def _decode_bytes(data: bytes) -> str:
    """字节流转字符串，优先 UTF-8 回退到 charset-normalizer。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes
    except ImportError:
        return data.decode("utf-8", errors="ignore")
    result = from_bytes(data).best()
    return str(result) if result is not None else data.decode("utf-8", errors="ignore")


# 纯文本类扩展名（直接解码无需第三方库）
_TEXT_EXTENSIONS = {
    "txt",
    "md",
    "rst",
    "log",
    "csv",
    "tsv",
    "json",
    "yaml",
    "yml",
    "xml",
    "html",
    "htm",
    "css",
    "js",
    "ts",
    "py",
    "java",
    "c",
    "h",
    "cpp",
    "hpp",
    "cs",
    "go",
    "rs",
    "rb",
    "php",
    "sh",
    "bat",
    "ps1",
    "ini",
    "cfg",
    "conf",
    "toml",
    "properties",
    "sql",
    "lua",
    "pl",
}
