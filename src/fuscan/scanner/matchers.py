"""匹配引擎：将规则规格转化为可执行的匹配器。

匹配器层次与 :mod:`fuscan.rules.model` 中的 MatchSpec 一一对应：

- :class:`FileNameMatcher` / :class:`ContentMatcher` / :class:`PathMatcher`
  对应 :class:`LeafMatch`，按 target 分发
- :class:`AndMatcher` / :class:`OrMatcher` / :class:`NotMatch` 对应组合规格

工厂函数 :func:`build_matcher` 根据 MatchSpec 实例类型构造对应匹配器。

性能优化：

- :func:`compile_regex_cached`：模块级 ``lru_cache`` 包装 ``re.compile``，
  跨 Scanner 实例共享编译结果（同一 pattern+flags 仅编译一次）
- :func:`match_batch`：对同一上下文批量应用多个匹配器，便于集中测量与未来扩展
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from functools import lru_cache
from re import Pattern

from typing_extensions import override

from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchSpec,
    MatchTarget,
    NotMatch,
    OrMatch,
)
from fuscan.scanner.context import MatchContext
from fuscan.scanner.result import MatchResult

__all__ = [
    "AndMatcher",
    "ContentMatcher",
    "FileNameMatcher",
    "Matcher",
    "NotMatcherImpl",
    "OrMatcher",
    "PathMatcher",
    "build_matcher",
    "compile_regex_cached",
    "match_batch",
]


@lru_cache(maxsize=512)
def compile_regex_cached(pattern: str, case_sensitive: bool) -> Pattern[str]:
    """编译正则并缓存结果（跨 Scanner 实例共享）。

    同一 ``(pattern, case_sensitive)`` 组合仅编译一次，结果在进程内缓存。
    多个 Scanner 实例使用同一 RuleSet 时共享编译产物，避免重复 ``re.compile``
    开销（典型场景：GUI 多工作区扫描、批量 benchmark）。

    :param pattern: 正则表达式字符串
    :param case_sensitive: 是否区分大小写（False 时附加 ``re.IGNORECASE``）
    :return: 编译后的 :class:`re.Pattern`
    :raises ValueError: 正则编译失败
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"正则表达式编译失败 {pattern!r}: {exc}") from exc


def match_batch(matchers: list[Matcher], context: MatchContext) -> list[MatchResult]:
    """对同一上下文批量应用多个匹配器，返回所有结果（含未命中）。

    与逐条调用 ``matcher.matches(context)`` 等价，但显式语义化为「批量」，
    便于：

    - 在热路径集中测量匹配耗时（benchmark 入口）
    - 未来扩展按 target 预分组、跳过无需内容的规则等优化
    - 调用方一次性获取全部结果，避免多次循环

    :param matchers: 匹配器列表
    :param context: 匹配上下文（内容按需懒加载，多匹配器共享）
    :return: 与 ``matchers`` 等长的 :class:`MatchResult` 列表，顺序一致
    """
    return [matcher.matches(context) for matcher in matchers]


class Matcher(ABC):
    """匹配器抽象基类。"""

    @abstractmethod
    def matches(self, context: MatchContext) -> MatchResult:
        """对上下文求值，返回匹配结果。"""

    def match_all(self, context: MatchContext) -> list[MatchResult]:
        """收集所有子匹配器的结果（默认仅返回自身结果，组合器覆写）。"""
        return [self.matches(context)]


class LeafMatcher(Matcher):
    """叶子匹配器基类，封装通用的模式应用逻辑。"""

    def __init__(self, spec: LeafMatch) -> None:
        self.spec = spec
        self._compiled: Pattern[str] | None = None
        # 预编译不区分大小写的 CONTAINS 正则，避免每次匹配重复 re.escape + 编译
        self._compiled_contains_ci: Pattern[str] | None = None
        if spec.mode == MatchMode.REGEX:
            # 经 compile_regex_cached 复用跨 Scanner 编译结果，
            # 同一 pattern+flags 在进程内仅编译一次
            self._compiled = compile_regex_cached(spec.pattern, spec.case_sensitive)
        elif spec.mode == MatchMode.CONTAINS and not spec.case_sensitive and spec.pattern:
            self._compiled_contains_ci = re.compile(re.escape(spec.pattern), re.IGNORECASE)

    @override
    def matches(self, context: MatchContext) -> MatchResult:
        text = self._extract_text(context)
        result = _apply_leaf(text, self.spec, self._compiled, self._compiled_contains_ci)
        if result.matched:
            # 命中时填充 match_texts（单元素元组）与 match_description（来自 spec）
            target = result.target or self.spec.target.value
            match_texts = (result.match_text,) if result.match_text else ()
            return MatchResult(
                matched=result.matched,
                detail=result.detail,
                match_text=result.match_text,
                match_count=result.match_count,
                target=target,
                match_texts=match_texts,
                match_description=self.spec.description,
            )
        # 未命中也填充 match_description，便于调用方区分组合规则的描述
        return MatchResult(
            matched=False,
            match_description=self.spec.description,
        )

    @abstractmethod
    def _extract_text(self, context: MatchContext) -> str:
        """从上下文中提取待匹配文本。"""


class FileNameMatcher(LeafMatcher):
    """对文件名应用叶子匹配。"""

    @override
    def _extract_text(self, context: MatchContext) -> str:
        return context.entry.name


class ContentMatcher(LeafMatcher):
    """对文件内容应用叶子匹配。

    首次访问会触发上下文的内容懒加载。
    """

    @override
    def _extract_text(self, context: MatchContext) -> str:
        return context.content


class PathMatcher(LeafMatcher):
    """对文件路径字符串应用叶子匹配。"""

    @override
    def _extract_text(self, context: MatchContext) -> str:
        return str(context.entry.path)


class AndMatcher(Matcher):
    """逻辑与：所有子匹配器均命中才算命中。

    持有 :class:`AndMatch` spec 以读取 ``description``，并收集所有子匹配器
    命中的文本到 ``match_texts``，便于 GUI 标记每个命中的内容（需求3）。
    """

    def __init__(self, spec: AndMatch) -> None:
        self.spec = spec
        self.children: tuple[Matcher, ...] = tuple(build_matcher(c) for c in spec.children)

    @override
    def matches(self, context: MatchContext) -> MatchResult:
        details: list[str] = []
        match_texts: list[str] = []
        total_count = 0
        for child in self.children:
            result = child.matches(context)
            if not result.matched:
                return MatchResult(matched=False, match_description=self.spec.description)
            if result.detail:
                details.append(result.detail)
            match_texts.extend(result.match_texts)
            total_count += result.match_count
        # 去重保序，避免相同关键词在多个子匹配器中重复出现
        unique_texts = _dedup_preserve_order(match_texts)
        return MatchResult(
            matched=True,
            detail=" AND ".join(details) if details else "全部命中",
            match_text=unique_texts[0] if unique_texts else "",
            match_count=total_count,
            target="",
            match_texts=tuple(unique_texts),
            match_description=self.spec.description,
        )

    @override
    def match_all(self, context: MatchContext) -> list[MatchResult]:
        results: list[MatchResult] = []
        for child in self.children:
            results.extend(child.match_all(context))
        return results


class OrMatcher(Matcher):
    """逻辑或：任一子匹配器命中即算命中。

    持有 :class:`OrMatch` spec 以读取 ``description``，并遍历所有子匹配器
    收集命中的文本到 ``match_texts``（不止首个命中），便于 GUI 标记每个命中
    的内容（需求3）。``match_count`` 为所有命中子匹配器的匹配条数之和。
    ``target`` 透传首个命中子匹配器的目标类型，供 GUI 判断是否在内容预览中高亮。
    """

    def __init__(self, spec: OrMatch) -> None:
        self.spec = spec
        self.children: tuple[Matcher, ...] = tuple(build_matcher(c) for c in spec.children)

    @override
    def matches(self, context: MatchContext) -> MatchResult:
        details: list[str] = []
        match_texts: list[str] = []
        total_count = 0
        first_target = ""
        any_matched = False
        for child in self.children:
            result = child.matches(context)
            if result.matched:
                any_matched = True
                if result.detail:
                    details.append(result.detail)
                match_texts.extend(result.match_texts)
                total_count += result.match_count
                if not first_target:
                    first_target = result.target
        if not any_matched:
            return MatchResult(matched=False, match_description=self.spec.description)
        unique_texts = _dedup_preserve_order(match_texts)
        return MatchResult(
            matched=True,
            detail=" OR ".join(details) if details else "任一命中",
            match_text=unique_texts[0] if unique_texts else "",
            match_count=total_count,
            target=first_target,
            match_texts=tuple(unique_texts),
            match_description=self.spec.description,
        )

    @override
    def match_all(self, context: MatchContext) -> list[MatchResult]:
        results: list[MatchResult] = []
        for child in self.children:
            results.extend(child.match_all(context))
        return results


class NotMatcherImpl(Matcher):
    """逻辑非：子匹配器不命中才算命中。

    持有 :class:`NotMatch` spec 以读取 ``description``。
    """

    def __init__(self, spec: NotMatch) -> None:
        self.spec = spec
        self.child: Matcher = build_matcher(spec.child)

    @override
    def matches(self, context: MatchContext) -> MatchResult:
        result = self.child.matches(context)
        if result.matched:
            return MatchResult(
                matched=False,
                detail=f"NOT 子条件命中: {result.detail}",
                match_description=self.spec.description,
            )
        return MatchResult(
            matched=True,
            detail="子条件未命中",
            match_count=1,
            match_description=self.spec.description,
        )


def _dedup_preserve_order(items: list[str]) -> list[str]:
    """去重保序：剔除空字符串与重复项，保留首次出现顺序。"""
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _apply_regex(text: str, compiled: Pattern[str] | None) -> MatchResult:
    """正则模式匹配：用迭代器收集匹配，避免大文本一次性加载全部 match 对象。"""
    if compiled is None:
        return MatchResult(matched=False, detail="正则未编译")
    iterator = compiled.finditer(text)
    first_match = next(iterator, None)
    if first_match is None:
        return MatchResult(matched=False)
    first = first_match.group(0)
    # 已消耗 1 个匹配，剩余迭代计数；避免 list() 对大文本创建大列表
    count = 1 + sum(1 for _ in iterator)
    return MatchResult(
        matched=True,
        detail=f"正则命中: {first!r}",
        match_text=first,
        match_count=count,
    )


def _apply_contains(
    text: str,
    pattern: str,
    case_sensitive: bool,
    compiled_ci: Pattern[str] | None,
) -> MatchResult:
    """CONTAINS 模式：统计非重叠出现次数。

    不区分大小写时用预编译正则 ``compiled_ci`` 的 ``finditer``，
    避免每次匹配重复 ``re.escape`` 与编译，且避免对整个大文本做 ``lower()``。
    """
    if not pattern:
        return MatchResult(matched=False)
    if case_sensitive:
        count = text.count(pattern)
    elif compiled_ci is not None:
        count = sum(1 for _ in compiled_ci.finditer(text))
    else:  # pragma: no cover - 预编译应已覆盖所有非空 pattern
        count = sum(1 for _ in re.finditer(re.escape(pattern), text, re.IGNORECASE))
    if count > 0:
        return MatchResult(matched=True, detail=f"包含 {pattern!r}", match_text=pattern, match_count=count)
    return MatchResult(matched=False)


def _apply_equality(text: str, pattern: str, mode: MatchMode, case_sensitive: bool) -> MatchResult:
    """EQUALS/STARTSWITH/ENDSWITH 模式：命中时 match_count 固定为 1。"""
    target = text
    if not case_sensitive:
        pattern = pattern.lower()
        target = text.lower()

    if mode == MatchMode.EQUALS and target == pattern:
        return MatchResult(matched=True, detail="完全相等", match_text=pattern, match_count=1)
    if mode == MatchMode.STARTSWITH and target.startswith(pattern):
        return MatchResult(matched=True, detail=f"以 {pattern!r} 开头", match_text=pattern, match_count=1)
    if mode == MatchMode.ENDSWITH and target.endswith(pattern):
        return MatchResult(matched=True, detail=f"以 {pattern!r} 结尾", match_text=pattern, match_count=1)
    return MatchResult(matched=False)


def _apply_leaf(
    text: str,
    spec: LeafMatch,
    compiled: Pattern[str] | None,
    compiled_contains_ci: Pattern[str] | None,
) -> MatchResult:
    """对文本应用叶子匹配规格。

    regex 模式用 ``finditer`` 迭代器收集所有匹配，``match_count`` 为匹配条数，
    ``match_text`` 取首个匹配文本用于高亮定位；
    contains 模式用 ``count`` 统计非重叠出现次数作为 ``match_count``，
    不区分大小写时复用预编译正则避免重复编译；
    equals/startswith/endswith 命中时 ``match_count`` 固定为 1。
    """
    if spec.mode == MatchMode.REGEX:
        return _apply_regex(text, compiled)
    if spec.mode == MatchMode.CONTAINS:
        return _apply_contains(text, spec.pattern, spec.case_sensitive, compiled_contains_ci)
    if spec.mode in (MatchMode.EQUALS, MatchMode.STARTSWITH, MatchMode.ENDSWITH):
        return _apply_equality(text, spec.pattern, spec.mode, spec.case_sensitive)
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
        return AndMatcher(spec)

    if isinstance(spec, OrMatch):
        return OrMatcher(spec)

    if isinstance(spec, NotMatch):
        return NotMatcherImpl(spec)

    raise TypeError(f"未知匹配规格类型: {type(spec).__name__}")
