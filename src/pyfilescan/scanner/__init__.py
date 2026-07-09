"""扫描器子包：文件遍历、匹配调度、结果汇总。

公共 API：

- 数据结构： :class:`FileEntry`, :class:`MatchContext`, :class:`MatchResult`,
  :class:`RuleHit`, :class:`ScanResult`, :class:`ScanReport`, :class:`ScanStats`
- 扫描器： :class:`Scanner`, :class:`FileWalker`
- 匹配器： :class:`Matcher` 与具体实现， :func:`build_matcher`
- 内容提供器： :class:`ContentProvider`, :func:`default_content_provider`
"""

from __future__ import annotations

from pyfilescan.scanner.context import (
    ContentProvider,
    FileEntry,
    MatchContext,
    default_content_provider,
)
from pyfilescan.scanner.matchers import (
    AndMatcher,
    ContentMatcher,
    FileNameMatcher,
    Matcher,
    NotMatcherImpl,
    OrMatcher,
    PathMatcher,
    build_matcher,
)
from pyfilescan.scanner.result import (
    MatchResult,
    RuleHit,
    ScanReport,
    ScanResult,
    ScanStats,
)
from pyfilescan.scanner.scanner import Scanner
from pyfilescan.scanner.walker import FileWalker

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
    "RuleHit",
    "ScanReport",
    "ScanResult",
    "ScanStats",
    "Scanner",
    "build_matcher",
    "default_content_provider",
]
