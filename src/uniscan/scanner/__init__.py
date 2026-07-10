"""扫描器子包：文件遍历、匹配调度、结果汇总。

公共 API：

- 数据结构： :class:`FileEntry`, :class:`MatchContext`, :class:`MatchResult`,
  :class:`RuleHit`, :class:`ScanResult`, :class:`ScanReport`, :class:`ScanStats`
- 扫描器： :class:`Scanner`, :class:`FileWalker`
- 匹配器： :class:`Matcher` 与具体实现， :func:`build_matcher`
- 内容提供器： :class:`ContentProvider`, :func:`default_content_provider`
"""

from __future__ import annotations

from uniscan.scanner.context import (
    ContentProvider,
    FileEntry,
    MatchContext,
    default_content_provider,
)
from uniscan.scanner.matchers import (
    AndMatcher,
    ContentMatcher,
    FileNameMatcher,
    Matcher,
    NotMatcherImpl,
    OrMatcher,
    PathMatcher,
    build_matcher,
)
from uniscan.scanner.result import (
    MatchResult,
    ProgressInfo,
    RuleHit,
    ScanReport,
    ScanResult,
    ScanStats,
)
from uniscan.scanner.scanner import Scanner, default_extract_content
from uniscan.scanner.walker import FileWalker, list_drives

__all__ = [
    "AndMatcher",
    "ContentMatcher",
    "ContentProvider",
    "FileEntry",
    "FileNameMatcher",
    "FileWalker",
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
    "list_drives",
]
