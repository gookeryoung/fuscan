"""扫描器子包：文件遍历、匹配调度、结果汇总。

公共 API：

- 数据结构： :class:`FileEntry`, :class:`MatchContext`, :class:`MatchResult`,
  :class:`RuleHit`, :class:`ScanResult`, :class:`ScanReport`, :class:`ScanStats`
- 扫描器： :class:`Scanner`, :class:`FileWalker`
- 匹配器： :class:`Matcher` 与具体实现， :func:`build_matcher`
- 内容提供器： :class:`ContentProvider`, :func:`default_content_provider`
"""

from __future__ import annotations

from fuscan.scanner.context import (
    ContentProvider,
    FileEntry,
    HashingContentProvider,
    MatchContext,
    default_content_provider,
)
from fuscan.scanner.matchers import (
    AndMatcher,
    ContentMatcher,
    FileNameMatcher,
    Matcher,
    NotMatcherImpl,
    OrMatcher,
    PathMatcher,
    build_matcher,
)
from fuscan.scanner.result import (
    MatchResult,
    ProgressInfo,
    RuleHit,
    ScanReport,
    ScanResult,
    ScanStats,
)
from fuscan.scanner.scanner import Scanner, default_extract_content, default_extract_content_with_hash
from fuscan.scanner.walker import FileWalker, list_drives

__all__ = [
    "AndMatcher",
    "ContentMatcher",
    "ContentProvider",
    "FileEntry",
    "FileNameMatcher",
    "FileWalker",
    "HashingContentProvider",
    "MatchContext",
    "MatchResult",
    "Matcher",
    "NotMatcherImpl",
    "OrMatcher",
    "PathMatcher",
    "ProgressInfo",
    "RuleHit",
    "ScanReport",
    "ScanResult",
    "ScanStats",
    "Scanner",
    "build_matcher",
    "default_content_provider",
    "default_extract_content",
    "default_extract_content_with_hash",
    "list_drives",
]
