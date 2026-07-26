"""压缩文件扫描器：对压缩包内条目应用规则集。

读取压缩包（ZIP/RAR/7Z）内文件，为每个条目构造合成 FileEntry，
通过内存字节直接复用已有提取器链，避免临时文件磁盘 I/O，最终输出 ScanResult 列表。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fuscan.archive.base import (
    ArchiveEntry,
    ArchiveError,
    ArchiveReader,
    get_reader,
)
from fuscan.cache.hashes import hash_bytes
from fuscan.config import DEFAULT_MAX_FILE_SIZE
from fuscan.extractors import ExtractorError, extract_content_from_bytes
from fuscan.rules.model import Rule, RuleSet
from fuscan.scanner._helpers import empty_content_provider, spec_needs_content
from fuscan.scanner.context import FileEntry, MatchContext
from fuscan.scanner.matchers import Matcher, build_matcher
from fuscan.scanner.result import RuleHit, ScanResult

if TYPE_CHECKING:
    from fuscan.cache import CacheStore

__all__ = ["ArchiveScanner"]

logger = logging.getLogger(__name__)


class ArchiveScanner:
    """压缩文件扫描器：对单个压缩包内所有文件应用规则。

    - 构造时一次性编译规则集为 Matcher 列表
    - 通过 :func:`get_reader` 工厂分发到 ZipReader/RarReader
    - 内容提取策略：读取条目字节 → 通过 ``extract_content_from_bytes`` 从内存直接提取
    - 加密条目未提供密码时跳过并记录错误
    """

    def __init__(
        self,
        ruleset: RuleSet,
        password: str | None = None,
        max_entry_size: int = DEFAULT_MAX_FILE_SIZE,
        cache: CacheStore | None = None,
        scan_extensions: frozenset[str] | None = None,
    ) -> None:
        self._ruleset = ruleset
        self._password = password
        self._max_entry_size = max_entry_size
        # 压缩包内部条目同样按白名单过滤：
        #   - None：用户全选，扫所有内部条目（向后兼容全选快速路径）
        #   - 空 frozenset：用户全部取消勾选，不扫任何内部条目
        #   - 非空 frozenset：仅扫扩展名在白名单中的条目
        # 压缩包内嵌套压缩包（如 a.zip 内 b.zip）由 walk 阶段不会收集到，本处不递归。
        self._scan_extensions: frozenset[str] | None = scan_extensions
        self._compiled: list[tuple[Rule, Matcher]] = [(rule, build_matcher(rule.match)) for rule in ruleset.rules]
        # 预计算规则集是否含 CONTENT 规则；为空时跳过所有条目内容读取
        self._has_content_rule: bool = any(spec_needs_content(rule.match) for rule in ruleset.rules)
        # 缓存模式：由父 Scanner 调 register_ruleset 登记规则，此处仅读取规则哈希
        self._cache: CacheStore | None = cache
        self._compiled_with_hash: list[tuple[Rule, Matcher, str]] = []
        if cache is not None:
            rule_hashes = cache.get_rule_hashes()
            self._compiled_with_hash = [
                (rule, matcher, rule_hashes[rule.name]) for rule, matcher in self._compiled if rule.name in rule_hashes
            ]

    def scan_archive(self, archive_path: Path) -> tuple[ScanResult, ...]:
        """扫描压缩包内所有条目，返回结果元组。

        压缩包无法打开时返回单条错误结果。
        """
        try:
            reader = get_reader(archive_path, password=self._password)
        except ArchiveError:
            logger.warning("打开压缩包失败（已跳过）: %s", archive_path)
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
            logger.warning("列出压缩包条目失败（已跳过）: %s", archive_path)
            self._close_reader(reader)
            return (
                ScanResult(
                    path=archive_path,
                    size=0,
                    hits=(),
                    errors=1,
                ),
            )

        results: list[ScanResult] = []
        for entry in entries:
            if entry.is_dir:
                continue
            # 按白名单过滤压缩包内部条目。
            # None 表示全选快速路径（扫所有条目）；非空 frozenset 表示仅扫扩展名在
            # 白名单中的条目（用户勾选压缩包但未勾选文本类型时，压缩包内 .txt 被跳过）。
            # 空 frozenset 表示用户全部取消勾选，跳过所有条目。
            if self._scan_extensions is not None and entry.extension not in self._scan_extensions:
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
        """对压缩包内单个条目应用规则。

        缓存模式下委托 :meth:`_scan_entry_cached`，否则走 :meth:`_scan_entry_uncached`。
        """
        if self._cache is None:
            return self._scan_entry_uncached(archive_path, entry, reader)
        return self._scan_entry_cached(archive_path, entry, reader)

    def _scan_entry_uncached(
        self,
        archive_path: Path,
        entry: ArchiveEntry,
        reader: ArchiveReader,
    ) -> ScanResult:
        """对压缩包内单个条目应用规则（无缓存）。

        规则集不含 CONTENT 规则时跳过条目内容解压，FILENAME/PATH 规则仍可命中。
        """
        file_entry = FileEntry(
            path=Path(entry.display_path),
            name=entry.name,
            size=entry.size,
            mtime=0.0,
            extension=entry.extension,
            is_dir=False,
        )

        if self._has_content_rule:

            def content_provider(_fe: FileEntry) -> str:
                return self._read_entry_content(archive_path, entry, reader)
        else:
            content_provider = empty_content_provider

        context = MatchContext(file_entry, content_provider=content_provider)
        hits: list[RuleHit] = []
        rule_errors = 0

        # 压缩包内条目已在 scan_archive 中按白名单过滤，
        # 此处对所有传入条目应用全部规则（无二次过滤）
        for rule, matcher in self._compiled:
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
                hits.append(
                    RuleHit(
                        rule_name=rule.name,
                        severity=rule.severity,
                        detail=result.detail,
                        match_text=result.match_text,
                        match_count=result.match_count,
                        target=result.target,
                    )
                )

        return ScanResult(
            path=file_entry.path,
            size=file_entry.size,
            hits=tuple(hits),
            errors=rule_errors,
            archive_path=archive_path,
        )

    def _scan_entry_cached(
        self,
        archive_path: Path,
        entry: ArchiveEntry,
        reader: ArchiveReader,
    ) -> ScanResult:
        """缓存模式扫描压缩包条目：读字节算哈希，查缓存，未命中走匹配器。"""
        assert self._cache is not None  # 仅类型收窄，调用方已保证非 None
        file_entry = FileEntry(
            path=Path(entry.display_path),
            name=entry.name,
            size=entry.size,
            mtime=0.0,
            extension=entry.extension,
            is_dir=False,
        )

        # 读字节并算哈希（算法由 hash_bytes 按大小决定）
        data = self._read_entry_bytes(archive_path, entry, reader)
        file_hash = hash_bytes(data)
        # 查提取内容缓存：命中则跳过 extract_content_from_bytes
        cached_content = self._cache.get_extracted_content(file_hash)
        if cached_content is not None:
            content = cached_content
        else:
            content = self._extract_content_from_bytes(data, entry)
            # 写入提取内容缓存（非空才写）
            if content:
                self._cache.put_extracted_content(file_hash, content, entry.extension)

        def content_provider(_fe: FileEntry) -> str:
            return content

        context = MatchContext(file_entry, content_provider=content_provider)

        # 压缩包内条目已在 scan_archive 中按白名单过滤，
        # 此处对所有传入条目应用全部规则（无二次过滤）
        applicable: list[tuple[Rule, Matcher, str]] = list(self._compiled_with_hash)
        rule_hashes = [rh for _, _, rh in applicable]
        cached: dict[str, RuleHit | None] = self._cache.get_cached_hits(file_hash, rule_hashes) if rule_hashes else {}

        hits: list[RuleHit] = []
        rule_errors = 0
        for rule, matcher, rule_hash in applicable:
            if rule_hash in cached:
                result = cached[rule_hash]
                if result is not None:
                    hits.append(
                        RuleHit(
                            rule_name=rule.name,
                            severity=result.severity,
                            detail=result.detail,
                            match_text=result.match_text,
                            match_count=result.match_count,
                            target=result.target,
                        )
                    )
                continue
            try:
                match_result = matcher.matches(context)
            except Exception:
                rule_errors += 1
                logger.warning(
                    "规则 %s 求值失败 %s",
                    rule.name,
                    entry.display_path,
                    exc_info=True,
                )
                continue
            if match_result.matched:
                hit = RuleHit(
                    rule_name=rule.name,
                    severity=rule.severity,
                    detail=match_result.detail,
                    match_text=match_result.match_text,
                    match_count=match_result.match_count,
                    target=match_result.target,
                )
                hits.append(hit)
                self._cache.put_result(file_hash, rule_hash, hit)
            else:
                self._cache.put_result(file_hash, rule_hash, None)

        self._cache.register_file(file_hash, file_entry.size)
        self._cache.register_path(file_hash, file_entry.path, file_entry.mtime)

        return ScanResult(
            path=file_entry.path,
            size=file_entry.size,
            hits=tuple(hits),
            errors=rule_errors,
            archive_path=archive_path,
        )

    def _read_entry_content(
        self,
        _archive_path: Path,
        entry: ArchiveEntry,
        reader: ArchiveReader,
    ) -> str:
        """读取条目字节并通过临时文件复用提取器链。"""
        data = self._read_entry_bytes(_archive_path, entry, reader)
        return self._extract_content_from_bytes(data, entry)

    def _read_entry_bytes(
        self,
        _archive_path: Path,
        entry: ArchiveEntry,
        reader: ArchiveReader,
    ) -> bytes:
        """读取压缩包条目字节，超大或读取失败时返回空字节。

        ``max_entry_size=0`` 表示不限制（与 ``Scanner.max_file_size=0`` 语义一致）。
        """
        if self._max_entry_size > 0 and entry.size > self._max_entry_size:
            logger.debug("条目过大，跳过内容提取: %s", entry.display_path)
            return b""
        try:
            return reader.read_entry(entry.entry_name)
        except ArchiveError as exc:
            logger.info("读取压缩包条目失败: %s", exc)
            return b""

    def _extract_content_from_bytes(self, data: bytes, entry: ArchiveEntry) -> str:
        """从字节提取文本内容，复用提取器链或直接解码。"""
        if not data:
            return ""
        if entry.extension in _TEXT_EXTENSIONS:
            return _decode_bytes(data)
        if _has_extractor(entry.extension):
            try:
                return extract_content_from_bytes(data, entry.extension)
            except (ExtractorError, OSError, ValueError):
                logger.warning("提取器提取失败，回退纯文本: %s", entry.display_path, exc_info=True)
                return _decode_bytes(data)
        return _decode_bytes(data)

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


def _has_extractor(extension: str) -> bool:
    """检查扩展名是否有注册提取器。"""
    from fuscan.extractors import get_extractor

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
