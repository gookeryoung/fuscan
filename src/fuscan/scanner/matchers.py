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
from dataclasses import dataclass, field
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
from fuscan.scanner._helpers import (
    _dedup_substrings,
    _extract_inline_flags,
    _extract_literals,
    _flags_to_chars,
)
from fuscan.scanner.context import MatchContext
from fuscan.scanner.result import MatchResult

__all__ = [
    "AndMatcher",
    "ContentMatcher",
    "ContentRegexPool",
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


# ---------------------------------------------------------------------------
# 组合规则共享扫描：复合 CONTENT REGEX 子项组
#
# AndMatcher/OrMatcher 的 CONTENT REGEX 子项原本各自独立 finditer 全文扫描，
# 50 条 AND 规则 × 2~3 子项 = 100~150 次独立 finditer（S3 基准 11010 次调用）。
# 把同 case_sensitive 的 CONTENT REGEX 子项合并为命名捕获组 OR 复合正则
# (?P<_c0>pat0)|(?P<_c1>pat1)|...，一次 finditer 收集所有子项命中状态，
# 按 AND/OR 语义求值，减少 80~90% 的 Python→C re 调用开销。
# ---------------------------------------------------------------------------


@dataclass
class _ContentCompositeGroup:
    """复合 CONTENT REGEX 子项组（同 case_sensitive）。

    在 :class:`AndMatcher` / :class:`OrMatcher` 构造期把同 ``case_sensitive`` 的
    CONTENT REGEX 子项合并为命名捕获组 OR 复合正则
    ``(?P<_c0>pat0)|(?P<_c1>pat1)|...``，``matches()`` 时一次 ``finditer`` 收集
    所有子项命中状态，按 AND/OR 语义求值，相比逐子项独立 ``finditer`` 减少约
    80~90% 的 Python→C re 调用开销。

    仅合并 ``mode=REGEX`` 的 CONTENT 子项（CONTAINS/EQUALS/STARTSWITH/ENDSWITH
    为简单字符串操作，无需合并）。

    预筛两级短路（与 :class:`fuscan.scanner._content_buckets._ContentRuleBucket`
    一致）：

    - 桶级：``prefilter_keywords`` 为组内所有子项字面量片段的并集；若全部不在
      内容中则组内所有子项必不命中，可直接跳过 ``finditer``。
    - 逐子项：``per_child_keywords[child_idx]`` 为该子项的字面量片段；若该子项
      的关键字均不出现则该子项必不命中（AND 下整体不命中，OR 下跳过该子项）。
    """

    case_sensitive: bool
    # 组内子项在父 children 中的下标（保持原顺序）
    child_indices: tuple[int, ...]
    # 组名 "_c{i}" -> 子项在父 children 中的下标
    group_to_idx: dict[str, int] = field(default_factory=dict)
    compiled: Pattern[str] | None = None
    # child_idx -> spec（仅含组内子项）
    specs_by_idx: dict[int, LeafMatch] = field(default_factory=dict)
    # 桶级预筛关键字（已按大小写规则处理）
    prefilter_keywords: list[str] = field(default_factory=list)
    # 逐子项预筛关键字: child_idx -> keywords
    per_child_keywords: dict[int, list[str]] = field(default_factory=dict)
    # 预筛是否大小写不敏感（组 case_sensitive=False 或任一子项含 (?i) 内联标志）
    prefilter_case_insensitive: bool = False


def _build_content_composite_groups(
    children: tuple[Matcher, ...],
) -> list[_ContentCompositeGroup]:
    """扫描 children 中的 ContentMatcher(REGEX) 子项，按 case_sensitive 分组构建复合组。

    仅合并 ``mode=REGEX`` 的 CONTENT 子项；单 ``case_sensitive`` 组内至少 2 个
    子项才创建复合组（单子项无合并收益，独立 ``matches()`` 即可）。

    复合正则编译失败时安全降级：放弃该组，组内子项回退到独立 ``matches()`` 路径
    （由 ``AndMatcher.matches`` / ``OrMatcher.matches`` 中 ``composite_child_indices``
    不含这些子项来保证）。

    :param children: AndMatcher/OrMatcher 的子匹配器元组
    :return: 复合组列表；空列表表示无可合并子项（行为与原逻辑完全一致）
    """
    # 按 case_sensitive 分组收集 (child_idx, ContentMatcher) 对
    grouped: dict[bool, list[tuple[int, ContentMatcher]]] = {}
    for idx, child in enumerate(children):
        if not isinstance(child, ContentMatcher):
            continue
        spec = child.spec
        if spec.target != MatchTarget.CONTENT or spec.mode != MatchMode.REGEX:
            continue
        grouped.setdefault(spec.case_sensitive, []).append((idx, child))

    groups: list[_ContentCompositeGroup] = []
    for case_sensitive, items in grouped.items():
        if len(items) < 2:
            # 单子项无合并收益，独立 matches() 即可
            continue
        group = _ContentCompositeGroup(
            case_sensitive=case_sensitive,
            child_indices=tuple(idx for idx, _ in items),
        )
        parts: list[str] = []
        prefilter_keywords: list[str] = []
        per_child_kw_by_idx: dict[int, list[str]] = {}
        specs_by_idx: dict[int, LeafMatch] = {}
        has_inline_ignorecase = False
        for i, (idx, child) in enumerate(items):
            spec = child.spec
            assert isinstance(spec, LeafMatch)
            grp_name = f"_c{i}"
            sub = spec.pattern
            # 提取内联标志（如 (?i)），用 (?flag:...) 非捕获组包装
            # 避免内联标志在命名组内部时影响后续分支（Python 3.11+ DeprecationWarning）
            sub_clean, sub_flags = _extract_inline_flags(sub)
            if sub_flags & re.IGNORECASE:
                has_inline_ignorecase = True
            if sub_flags:
                flag_str = _flags_to_chars(sub_flags)
                part = rf"(?{flag_str}:(?P<{grp_name}>{sub_clean}))"
            else:
                part = rf"(?P<{grp_name}>{sub})"
            parts.append(part)
            group.group_to_idx[grp_name] = idx
            specs_by_idx[idx] = spec
            rule_literals = _extract_literals(sub_clean)
            prefilter_keywords.extend(rule_literals)
            per_child_kw_by_idx[idx] = rule_literals
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile("|".join(parts), flags)
        except re.error:
            # 复合正则编译失败（理论不会发生——子项均已独立编译成功，
            # OR 复合仅因命名组唯一性，使用 _c{i} 保证唯一）；
            # 安全降级：放弃该组，子项回退到独立 matches() 路径
            continue
        group.compiled = compiled
        group.specs_by_idx = specs_by_idx
        # 预筛关键字：组 case_sensitive=False 或任一子项含 (?i) 时按大小写不敏感处理
        prefilter_ci = (not case_sensitive) or has_inline_ignorecase
        if prefilter_ci:
            group.prefilter_keywords = _dedup_substrings([k.lower() for k in prefilter_keywords])
            group.per_child_keywords = {
                idx: _dedup_substrings([k.lower() for k in kws]) for idx, kws in per_child_kw_by_idx.items()
            }
        else:
            group.prefilter_keywords = _dedup_substrings(prefilter_keywords)
            group.per_child_keywords = {idx: _dedup_substrings(kws) for idx, kws in per_child_kw_by_idx.items()}
        group.prefilter_case_insensitive = prefilter_ci
        groups.append(group)
    return groups


def _evaluate_composite_group(  # noqa: PLR0912
    group: _ContentCompositeGroup,
    context: MatchContext,
) -> dict[int, MatchResult]:
    """对复合组跑一次 ``finditer``，返回命中的子项的 MatchResult 字典。

    两级预筛（与 :func:`fuscan.scanner._content_buckets.match_content_via_buckets`
    一致）：

    - 桶级：``prefilter_keywords`` 全部不在 haystack 中 → 组内所有子项必不命中，
      返回空字典。
    - 逐子项：``per_child_keywords[child_idx]`` 全部不在 haystack 中 → 该子项必不
      命中，不参与 ``finditer`` 分派（保守不漏：无关键字的子项始终活跃）。

    大小写不敏感组（``prefilter_case_insensitive=True``）的预筛 haystack 为
    :attr:`MatchContext.content_lower`（跨 Matcher 共享的缓存小写化内容）；
    否则为 :attr:`MatchContext.content`。

    :param group: 复合组
    :param context: 匹配上下文（提供 ``content`` 与 ``content_lower`` 懒加载缓存）
    :return: ``{child_idx: MatchResult}``，仅含命中的子项
    """
    if group.compiled is None:
        return {}
    # 选择预筛 haystack：不敏感组用小写化 content（context 缓存，跨 Matcher 复用），
    # 否则用原 content
    if group.prefilter_case_insensitive:
        haystack = context.content_lower
    else:
        haystack = context.content
    # 桶级快速短路：组内所有子项关键字均不在 content 中 → 全部子项必不命中
    if group.prefilter_keywords and not any(kw in haystack for kw in group.prefilter_keywords):
        return {}
    # 逐子项预筛：计算活跃子项集合（无关键字的子项始终活跃）
    active: set[int] = set()
    for child_idx in group.child_indices:
        kws = group.per_child_keywords.get(child_idx, [])
        if not kws or any(kw in haystack for kw in kws):
            active.add(child_idx)
    if not active:
        return {}
    # 跑一次 finditer，按 lastgroup 分派到各子项
    content = context.content
    per_child: dict[int, tuple[str, int]] = {}
    for m in group.compiled.finditer(content):
        last = m.lastgroup
        if last is None:
            continue
        child_idx = group.group_to_idx.get(last)
        if child_idx is None or child_idx not in active:
            continue
        txt = m.group(0)
        prev = per_child.get(child_idx)
        if prev is None:
            per_child[child_idx] = (txt, 1)
        else:
            per_child[child_idx] = (prev[0], prev[1] + 1)
    # 构造 MatchResult（detail 与 _apply_regex 一致：f"正则命中: {first_txt!r}"）
    results: dict[int, MatchResult] = {}
    for child_idx, (first_txt, total_cnt) in per_child.items():
        spec = group.specs_by_idx[child_idx]
        results[child_idx] = MatchResult(
            matched=True,
            detail=f"正则命中: {first_txt!r}",
            match_text=first_txt,
            match_count=total_cnt,
            target=MatchTarget.CONTENT.value,
            match_texts=(first_txt,) if first_txt else (),
            match_description=spec.description,
        )
    return results


@dataclass
class _PoolGroup:
    """池内同 case_sensitive 的子项组。

    与 :class:`_ContentCompositeGroup` 结构对齐，但跨所有 AND/OR 规则收集子项，
    按 ``(pattern, case_sensitive)`` 去重——相同子项（如多条 AND 规则共有的
    ``password=`` 子模式）只注册一次，全局共享一次 ``finditer`` 结果。
    """

    case_sensitive: bool
    # 本组所有 child_id（保持注册顺序）
    child_ids: list[int] = field(default_factory=list)
    # 命名组名 "_p{child_id}" -> child_id
    group_to_child_id: dict[str, int] = field(default_factory=dict)
    # child_id -> spec（仅含组内子项）
    specs_by_child_id: dict[int, LeafMatch] = field(default_factory=dict)
    # 命名组子正则片段，下标对齐 child_ids
    sub_parts: list[str] = field(default_factory=list)
    compiled: Pattern[str] | None = None
    # 预筛（与 _ContentCompositeGroup 一致）
    prefilter_keywords: list[str] = field(default_factory=list)
    per_child_keywords: dict[int, list[str]] = field(default_factory=dict)
    prefilter_case_insensitive: bool = False


class ContentRegexPool:
    """跨规则共享的 CONTENT REGEX 子项池。

    收集所有 AND/OR 组合规则的 CONTENT REGEX 子项，按 ``case_sensitive`` 合并为
    复合 OR 正则。``evaluate(context)`` 一次 ``finditer`` 收集所有子项命中状态，
    各组合规则从池查询，消除跨规则重复 ``finditer``（S3 场景 50 条 AND × 2~3 子项，
    子项去重后仅 ~30 个，``finditer`` 次数从 125+ 降至 1-2 次）。

    相同 ``(pattern, case_sensitive)`` 的子项去重共享同一 ``child_id``，
    多个 AND/OR 规则可引用同一 ``child_id``。

    求值结果按 ``id(context)`` 缓存：同一文件的多个 AND/OR 规则共享一次
    ``evaluate``，避免重复 ``finditer``。

    两级预筛（桶级 + 逐子项）与 :func:`match_content_via_buckets` /
    :func:`_evaluate_composite_group` 一致，保证不产生 false negative。
    """

    def __init__(self) -> None:
        self._groups: dict[bool, _PoolGroup] = {}
        # (pattern, case_sensitive) -> child_id（去重键）
        self._pattern_to_child_id: dict[tuple[str, bool], int] = {}
        self._next_child_id: int = 0
        self._compiled: bool = False
        # 已编译组覆盖的 child_id 集合（单子项组跳过编译，其 child_id 不在此集合）
        # matches() 据此区分"池化但未命中"与"未入池（走独立 matches()）"
        self._compiled_child_ids: frozenset[int] = frozenset()
        # evaluate 结果缓存：同 context 只跑一次
        self._cached_context_id: int | None = None
        self._cached_results: dict[int, MatchResult] | None = None

    def register(self, spec: LeafMatch) -> int:
        """注册 CONTENT REGEX 子项，返回 ``child_id``。

        相同 ``(pattern, case_sensitive)`` 去重共享同一 ``child_id``。
        必须在 :meth:`compile` 之前调用。

        :param spec: 子项规格（须为 ``target=CONTENT, mode=REGEX``）
        :return: 全局唯一 ``child_id``
        """
        assert not self._compiled, "register 必须在 compile() 之前调用"
        key = (spec.pattern, spec.case_sensitive)
        existing = self._pattern_to_child_id.get(key)
        if existing is not None:
            return existing
        child_id = self._next_child_id
        self._next_child_id += 1
        self._pattern_to_child_id[key] = child_id
        group = self._groups.setdefault(spec.case_sensitive, _PoolGroup(case_sensitive=spec.case_sensitive))
        group.child_ids.append(child_id)
        group.specs_by_child_id[child_id] = spec
        return child_id

    def compile(self) -> None:
        """构建各组的复合 OR 正则与预筛关键字。

        编译后不可再 :meth:`register`。复合正则编译失败的组整体丢弃
        （组内子项回退到独立 ``matches()`` 路径——由 ``AndMatcher`` /
        ``OrMatcher`` 中 ``_pool_child_ids`` 不含这些子项保证）。
        """
        for case_sensitive, group in self._groups.items():
            if len(group.child_ids) < 2:
                # 单子项组无合并收益，跳过（子项走独立 matches() 路径）
                continue
            parts: list[str] = []
            prefilter_keywords: list[str] = []
            per_child_kw: dict[int, list[str]] = {}
            has_inline_ignorecase = False
            for child_id in group.child_ids:
                spec = group.specs_by_child_id[child_id]
                assert isinstance(spec, LeafMatch)
                grp_name = f"_p{child_id}"
                sub = spec.pattern
                sub_clean, sub_flags = _extract_inline_flags(sub)
                if sub_flags & re.IGNORECASE:
                    has_inline_ignorecase = True
                if sub_flags:
                    flag_str = _flags_to_chars(sub_flags)
                    part = rf"(?{flag_str}:(?P<{grp_name}>{sub_clean}))"
                else:
                    part = rf"(?P<{grp_name}>{sub})"
                parts.append(part)
                group.sub_parts.append(part)
                group.group_to_child_id[grp_name] = child_id
                rule_literals = _extract_literals(sub_clean)
                prefilter_keywords.extend(rule_literals)
                per_child_kw[child_id] = rule_literals
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                group.compiled = re.compile("|".join(parts), flags)
            except re.error:
                # 编译失败：放弃该组，子项回退独立 matches()
                group.compiled = None
                group.group_to_child_id.clear()
                group.sub_parts.clear()
                continue
            prefilter_ci = (not case_sensitive) or has_inline_ignorecase
            if prefilter_ci:
                group.prefilter_keywords = _dedup_substrings([k.lower() for k in prefilter_keywords])
                group.per_child_keywords = {
                    cid: _dedup_substrings([k.lower() for k in kws]) for cid, kws in per_child_kw.items()
                }
            else:
                group.prefilter_keywords = _dedup_substrings(prefilter_keywords)
                group.per_child_keywords = {cid: _dedup_substrings(kws) for cid, kws in per_child_kw.items()}
            group.prefilter_case_insensitive = prefilter_ci
        # 收集已编译组覆盖的 child_id（单子项组跳过编译，不在此集合）
        compiled_ids: set[int] = set()
        for group in self._groups.values():
            if group.compiled is not None:
                compiled_ids.update(group.child_ids)
        self._compiled_child_ids = frozenset(compiled_ids)
        self._compiled = True

    def is_compiled(self, child_id: int) -> bool:
        """判断 child_id 是否属于已编译组（可走池路径求值）。

        单子项组（``len < 2``）跳过编译，其 child_id 返回 False——
        调用方应走独立 ``matches()`` 路径。

        :param child_id: :meth:`register` 返回的子项 ID
        :return: 已编译组覆盖返回 True，否则 False
        """
        return child_id in self._compiled_child_ids

    def _evaluate_group(self, group: _PoolGroup, context: MatchContext) -> dict[int, MatchResult]:  # noqa: PLR0912
        """对单组跑一次 finditer，返回命中的 child_id -> MatchResult。"""
        if group.compiled is None:
            return {}
        if group.prefilter_case_insensitive:
            haystack = context.content_lower
        else:
            haystack = context.content
        if group.prefilter_keywords and not any(kw in haystack for kw in group.prefilter_keywords):
            return {}
        active: set[int] = set()
        for child_id in group.child_ids:
            kws = group.per_child_keywords.get(child_id, [])
            if not kws or any(kw in haystack for kw in kws):
                active.add(child_id)
        if not active:
            return {}
        content = context.content
        per_child: dict[int, tuple[str, int]] = {}
        for m in group.compiled.finditer(content):
            last = m.lastgroup
            if last is None:
                continue
            child_id = group.group_to_child_id.get(last)
            if child_id is None or child_id not in active:
                continue
            txt = m.group(0)
            prev = per_child.get(child_id)
            if prev is None:
                per_child[child_id] = (txt, 1)
            else:
                per_child[child_id] = (prev[0], prev[1] + 1)
        results: dict[int, MatchResult] = {}
        for child_id, (first_txt, total_cnt) in per_child.items():
            spec = group.specs_by_child_id[child_id]
            results[child_id] = MatchResult(
                matched=True,
                detail=f"正则命中: {first_txt!r}",
                match_text=first_txt,
                match_count=total_cnt,
                target=MatchTarget.CONTENT.value,
                match_texts=(first_txt,) if first_txt else (),
                match_description=spec.description,
            )
        return results

    def evaluate(self, context: MatchContext) -> dict[int, MatchResult]:
        """对所有池化子项跑一次 finditer，返回命中的 child_id -> MatchResult。

        结果按 ``id(context)`` 缓存：同一文件的多个 AND/OR 规则共享一次 evaluate。
        """
        ctx_id = id(context)
        if self._cached_context_id == ctx_id and self._cached_results is not None:
            return self._cached_results
        results: dict[int, MatchResult] = {}
        for group in self._groups.values():
            results.update(self._evaluate_group(group, context))
        self._cached_context_id = ctx_id
        self._cached_results = results
        return results


class AndMatcher(Matcher):
    """逻辑与：所有子匹配器均命中才算命中。

    持有 :class:`AndMatch` spec 以读取 ``description``，并收集所有子匹配器
    命中的文本到 ``match_texts``，便于 GUI 标记每个命中的内容（需求3）。

    性能优化（双层）：

    1. **池化**（:class:`ContentRegexPool`）：Scanner 构造期把所有 AND/OR 规则的
       CONTENT REGEX 子项跨规则去重合并为复合 OR 正则。``matches()`` 时若已注入
       池，从 ``pool.evaluate(context)`` 查询池化子项命中状态，消除跨规则重复
       ``finditer``。S3 场景 50 条 AND × 2~3 子项，``finditer`` 次数降 97%+。
    2. **单条规则复合组**（:class:`_ContentCompositeGroup`）：无池注入时（独立
       构造的 AndMatcher，如单元测试）仍走单条规则内同 ``case_sensitive`` 子项
       合并的复合组路径，行为与池化前完全一致。
    """

    def __init__(self, spec: AndMatch) -> None:
        self.spec = spec
        self.children: tuple[Matcher, ...] = tuple(build_matcher(c) for c in spec.children)
        self._composite_groups: list[_ContentCompositeGroup] = _build_content_composite_groups(self.children)
        # 复合组覆盖的子项下标集合 + 子项下标 -> 所属组的映射（matches 时 O(1) 查找）
        self._composite_child_indices: set[int] = set()
        self._group_by_child_idx: dict[int, _ContentCompositeGroup] = {}
        for group in self._composite_groups:
            self._composite_child_indices.update(group.child_indices)
            for child_idx in group.child_indices:
                self._group_by_child_idx[child_idx] = group
        # 池化注入（由 Scanner 构造期 attach_pool 设置）；None 时走复合组路径
        self._pool: ContentRegexPool | None = None
        self._pool_child_ids: dict[int, int] = {}

    def attach_pool(self, pool: ContentRegexPool) -> None:
        """注入跨规则共享池，注册本 matcher 的 CONTENT REGEX 子项。

        由 :class:`fuscan.scanner.scanner.Scanner` 构造期在 ``pool.compile()`` 前
        调用。注册后 ``matches()`` 走池路径；未注册则走 ``_composite_groups`` 路径。

        :param pool: 全局共享池（须尚未 compile）
        """
        self._pool = pool
        for i, child in enumerate(self.children):
            if not isinstance(child, ContentMatcher):
                continue
            spec = child.spec
            if spec.target != MatchTarget.CONTENT or spec.mode != MatchMode.REGEX:
                continue
            child_id = pool.register(spec)
            self._pool_child_ids[i] = child_id

    @override
    def matches(self, context: MatchContext) -> MatchResult:  # noqa: PLR0912
        details: list[str] = []
        match_texts: list[str] = []
        total_count = 0
        if self._pool is not None:
            # 池化路径：池化子项从 pool.evaluate(context) 查询（跨规则共享一次
            # finditer），非池化子项走原 matches()。保持 AND 短路语义与
            # detail/match_texts 顺序（按 children 下标迭代）。
            # 单子项组（pool.is_compiled=False）回退独立 matches()，避免误判未命中。
            pool_results = self._pool.evaluate(context)
            for i, child in enumerate(self.children):
                child_id = self._pool_child_ids.get(i)
                if child_id is not None and self._pool.is_compiled(child_id):
                    result = pool_results.get(child_id)
                    if result is None:
                        # AND 语义：池中该子项未命中 → 整体不命中
                        return MatchResult(matched=False, match_description=self.spec.description)
                else:
                    result = child.matches(context)
                    if not result.matched:
                        return MatchResult(matched=False, match_description=self.spec.description)
                if result.detail:
                    details.append(result.detail)
                match_texts.extend(result.match_texts)
                total_count += result.match_count
        elif not self._composite_groups:
            # 无复合组：走原逻辑（保持完全一致）
            for child in self.children:
                result = child.matches(context)
                if not result.matched:
                    return MatchResult(matched=False, match_description=self.spec.description)
                if result.detail:
                    details.append(result.detail)
                match_texts.extend(result.match_texts)
                total_count += result.match_count
        else:
            # 有复合组：按原顺序评估，复合子项从缓存的组评估结果中取。
            # 保持原短路语义——任一子项未命中立即返回 False；
            # 保持原 detail/match_texts 顺序——按 children 下标迭代。
            group_results_cache: dict[int, dict[int, MatchResult]] = {}
            for i, child in enumerate(self.children):
                if i in self._composite_child_indices:
                    group = self._group_by_child_idx[i]
                    group_key = id(group)
                    if group_key not in group_results_cache:
                        # 首次访问该组：跑一次 finditer，结果缓存供同组其他子项复用
                        results = _evaluate_composite_group(group, context)
                        group_results_cache[group_key] = results
                    results = group_results_cache[group_key]
                    if i not in results:
                        # AND 语义：组内该子项未命中 → 整体不命中
                        return MatchResult(matched=False, match_description=self.spec.description)
                    result = results[i]
                else:
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

    性能优化（双层，与 :class:`AndMatcher` 一致）：

    1. **池化**（:class:`ContentRegexPool`）：Scanner 构造期跨规则去重合并
       CONTENT REGEX 子项为复合 OR 正则，``matches()`` 从池查询命中状态。
    2. **单条规则复合组**（:class:`_ContentCompositeGroup`）：无池注入时走单条
       规则内合并路径，行为与池化前完全一致。
    """

    def __init__(self, spec: OrMatch) -> None:
        self.spec = spec
        self.children: tuple[Matcher, ...] = tuple(build_matcher(c) for c in spec.children)
        self._composite_groups: list[_ContentCompositeGroup] = _build_content_composite_groups(self.children)
        self._composite_child_indices: set[int] = set()
        self._group_by_child_idx: dict[int, _ContentCompositeGroup] = {}
        for group in self._composite_groups:
            self._composite_child_indices.update(group.child_indices)
            for child_idx in group.child_indices:
                self._group_by_child_idx[child_idx] = group
        # 池化注入（由 Scanner 构造期 attach_pool 设置）；None 时走复合组路径
        self._pool: ContentRegexPool | None = None
        self._pool_child_ids: dict[int, int] = {}

    def attach_pool(self, pool: ContentRegexPool) -> None:
        """注入跨规则共享池，注册本 matcher 的 CONTENT REGEX 子项。

        与 :meth:`AndMatcher.attach_pool` 语义一致，由 Scanner 构造期在
        ``pool.compile()`` 前调用。

        :param pool: 全局共享池（须尚未 compile）
        """
        self._pool = pool
        for i, child in enumerate(self.children):
            if not isinstance(child, ContentMatcher):
                continue
            spec = child.spec
            if spec.target != MatchTarget.CONTENT or spec.mode != MatchMode.REGEX:
                continue
            child_id = pool.register(spec)
            self._pool_child_ids[i] = child_id

    @override
    def matches(self, context: MatchContext) -> MatchResult:  # noqa: PLR0912
        details: list[str] = []
        match_texts: list[str] = []
        total_count = 0
        first_target = ""
        any_matched = False
        if self._pool is not None:
            # 池化路径：池化子项从 pool.evaluate(context) 查询（跨规则共享一次
            # finditer），非池化子项走原 matches()。保持 OR 不短路语义——
            # 收集所有命中子项；保持 detail/match_texts/first_target 顺序。
            # 单子项组（pool.is_compiled=False）回退独立 matches()。
            pool_results = self._pool.evaluate(context)
            for i, child in enumerate(self.children):
                child_id = self._pool_child_ids.get(i)
                if child_id is not None and self._pool.is_compiled(child_id):
                    result = pool_results.get(child_id)
                    if result is None:
                        # OR 语义：该子项未命中，跳过
                        continue
                else:
                    result = child.matches(context)
                    if not result.matched:
                        continue
                any_matched = True
                if result.detail:
                    details.append(result.detail)
                match_texts.extend(result.match_texts)
                total_count += result.match_count
                if not first_target:
                    first_target = result.target
        elif not self._composite_groups:
            # 无复合组：走原逻辑（保持完全一致，不短路以收集所有命中）
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
        else:
            # 有复合组：按原顺序评估，复合子项从缓存的组评估结果中取。
            # 保持原 OR 语义——不短路，收集所有命中子项；
            # 保持原 detail/match_texts/first_target 顺序——按 children 下标迭代。
            group_results_cache: dict[int, dict[int, MatchResult]] = {}
            for i, child in enumerate(self.children):
                if i in self._composite_child_indices:
                    group = self._group_by_child_idx[i]
                    group_key = id(group)
                    if group_key not in group_results_cache:
                        results = _evaluate_composite_group(group, context)
                        group_results_cache[group_key] = results
                    results = group_results_cache[group_key]
                    if i in results:
                        # OR 语义：任一子项命中即 any_matched=True，但仍继续收集所有命中
                        result = results[i]
                        any_matched = True
                        if result.detail:
                            details.append(result.detail)
                        match_texts.extend(result.match_texts)
                        total_count += result.match_count
                        if not first_target:
                            first_target = result.target
                else:
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
