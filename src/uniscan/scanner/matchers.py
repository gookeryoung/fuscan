"""匹配引擎：将规则规格转化为可执行的匹配器。

匹配器层次与 :mod:`uniscan.rules.model` 中的 MatchSpec 一一对应：

- :class:`FileNameMatcher` / :class:`ContentMatcher` / :class:`PathMatcher`
  对应 :class:`LeafMatch`，按 target 分发
- :class:`AndMatcher` / :class:`OrMatcher` / :class:`NotMatch` 对应组合规格

工厂函数 :func:`build_matcher` 根据 MatchSpec 实例类型构造对应匹配器。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import List, Optional, Pattern, Tuple

from uniscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchSpec,
    MatchTarget,
    NotMatch,
    OrMatch,
)
from uniscan.scanner.context import MatchContext
from uniscan.scanner.result import MatchResult

__all__ = [
    "AndMatcher",
    "ContentMatcher",
    "FileNameMatcher",
    "Matcher",
    "NotMatcherImpl",
    "OrMatcher",
    "PathMatcher",
    "build_matcher",
]


class Matcher(ABC):
    """匹配器抽象基类。"""

    @abstractmethod
    def matches(self, context: MatchContext) -> MatchResult:
        """对上下文求值，返回匹配结果。"""

    def match_all(self, context: MatchContext) -> List[MatchResult]:
        """收集所有子匹配器的结果（默认仅返回自身结果，组合器覆写）。"""
        return [self.matches(context)]


class LeafMatcher(Matcher):
    """叶子匹配器基类，封装通用的模式应用逻辑。"""

    def __init__(self, spec: LeafMatch) -> None:
        self.spec = spec
        self._compiled: Optional[Pattern[str]] = None
        if spec.mode == MatchMode.REGEX:
            flags = 0 if spec.case_sensitive else re.IGNORECASE
            try:
                self._compiled = re.compile(spec.pattern, flags)
            except re.error as exc:
                raise ValueError(f"正则表达式编译失败 {spec.pattern!r}: {exc}") from exc

    def matches(self, context: MatchContext) -> MatchResult:
        text = self._extract_text(context)
        return _apply_leaf(text, self.spec, self._compiled)

    @abstractmethod
    def _extract_text(self, context: MatchContext) -> str:
        """从上下文中提取待匹配文本。"""


class FileNameMatcher(LeafMatcher):
    """对文件名应用叶子匹配。"""

    def _extract_text(self, context: MatchContext) -> str:
        return context.entry.name


class ContentMatcher(LeafMatcher):
    """对文件内容应用叶子匹配。

    首次访问会触发上下文的内容懒加载。
    """

    def _extract_text(self, context: MatchContext) -> str:
        return context.content


class PathMatcher(LeafMatcher):
    """对文件路径字符串应用叶子匹配。"""

    def _extract_text(self, context: MatchContext) -> str:
        return str(context.entry.path)


class AndMatcher(Matcher):
    """逻辑与：所有子匹配器均命中才算命中。"""

    def __init__(self, children: Tuple[Matcher, ...]) -> None:
        self.children = children

    def matches(self, context: MatchContext) -> MatchResult:
        details: List[str] = []
        for child in self.children:
            result = child.matches(context)
            if not result.matched:
                return MatchResult(matched=False)
            if result.detail:
                details.append(result.detail)
        return MatchResult(matched=True, detail=" AND ".join(details) if details else "全部命中")

    def match_all(self, context: MatchContext) -> List[MatchResult]:
        results: List[MatchResult] = []
        for child in self.children:
            results.extend(child.match_all(context))
        return results


class OrMatcher(Matcher):
    """逻辑或：任一子匹配器命中即算命中。"""

    def __init__(self, children: Tuple[Matcher, ...]) -> None:
        self.children = children

    def matches(self, context: MatchContext) -> MatchResult:
        for child in self.children:
            result = child.matches(context)
            if result.matched:
                return MatchResult(matched=True, detail=result.detail or "任一命中")
        return MatchResult(matched=False)

    def match_all(self, context: MatchContext) -> List[MatchResult]:
        results: List[MatchResult] = []
        for child in self.children:
            results.extend(child.match_all(context))
        return results


class NotMatcherImpl(Matcher):
    """逻辑非：子匹配器不命中才算命中。"""

    def __init__(self, child: Matcher) -> None:
        self.child = child

    def matches(self, context: MatchContext) -> MatchResult:
        result = self.child.matches(context)
        if result.matched:
            return MatchResult(matched=False, detail=f"NOT 子条件命中: {result.detail}")
        return MatchResult(matched=True, detail="子条件未命中")


def _apply_leaf(text: str, spec: LeafMatch, compiled: Optional[Pattern[str]]) -> MatchResult:
    """对文本应用叶子匹配规格。"""
    if spec.mode == MatchMode.REGEX:
        if compiled is None:
            return MatchResult(matched=False, detail="正则未编译")
        m = compiled.search(text)
        if m is None:
            return MatchResult(matched=False)
        return MatchResult(matched=True, detail=f"正则命中: {m.group(0)!r}")

    pattern = spec.pattern
    target = text
    if not spec.case_sensitive:
        pattern = pattern.lower()
        target = text.lower()

    if spec.mode == MatchMode.CONTAINS:
        idx = target.find(pattern)
        if idx >= 0:
            return MatchResult(matched=True, detail=f"包含 {pattern!r}")
        return MatchResult(matched=False)

    if spec.mode == MatchMode.EQUALS:
        if target == pattern:
            return MatchResult(matched=True, detail="完全相等")
        return MatchResult(matched=False)

    if spec.mode == MatchMode.STARTSWITH:
        if target.startswith(pattern):
            return MatchResult(matched=True, detail=f"以 {pattern!r} 开头")
        return MatchResult(matched=False)

    if spec.mode == MatchMode.ENDSWITH:
        if target.endswith(pattern):
            return MatchResult(matched=True, detail=f"以 {pattern!r} 结尾")
        return MatchResult(matched=False)

    return MatchResult(matched=False, detail=f"未知模式 {spec.mode.value}")


def build_matcher(spec: MatchSpec) -> Matcher:
    """根据 MatchSpec 实例构造对应的 Matcher。

    :param spec: 规则模型中的匹配规格
    :return: 可执行的 Matcher 实例
    :raises TypeError: spec 类型未知
    :raises ValueError: 正则表达式编译失败
    """
    if isinstance(spec, LeafMatch):
        if spec.target == MatchTarget.FILENAME:
            return FileNameMatcher(spec)
        if spec.target == MatchTarget.CONTENT:
            return ContentMatcher(spec)
        if spec.target == MatchTarget.PATH:
            return PathMatcher(spec)
        raise TypeError(f"未知匹配目标: {spec.target}")

    if isinstance(spec, AndMatch):
        children = tuple(build_matcher(c) for c in spec.children)
        return AndMatcher(children)

    if isinstance(spec, OrMatch):
        children = tuple(build_matcher(c) for c in spec.children)
        return OrMatcher(children)

    if isinstance(spec, NotMatch):
        return NotMatcherImpl(build_matcher(spec.child))

    raise TypeError(f"未知匹配规格类型: {type(spec).__name__}")
