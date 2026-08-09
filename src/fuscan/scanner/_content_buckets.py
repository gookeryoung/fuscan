"""CONTENT 规则桶：将顶层纯 CONTENT 叶子规则按 (mode, case_sensitive) 合并为复合 OR 正则。

模块背景
----------

:fuscan.scanner.scanner.Scanner; 在初始化阶段会把 ``ruleset.rules`` 编译为
``list[tuple[Rule, Matcher]]``，其中**顶层 ``LeafMatch(target=CONTENT)``** 规则
（未被 AND/OR/NOT 包裹）通常占大头。逐条调用 ``matcher.matches(context)`` 会产生
大量 Python→C re 调用，对 20+ 条 CONTENT 规则的规则集性能不佳。

本模块把同 (mode, case_sensitive) 的多条 CONTENT 规则合并为一个命名捕获组的
OR 复合正则（``(?P<_f0>pat0)|(?P<_f1>pat1)|...``），一次 ``finditer`` 得到全部
命中后按 ``lastgroup`` 分派到各规则，相比逐条独立 re 调用减少约 80~90% 的开销。

仅当规则是**顶层 LeafMatch 且 target=CONTENT**（无组合器包裹）时会被加入桶；
组合型 / 非 CONTENT 目标规则保留在 Scanner 的 ``_remaining_rules`` 中走原路径。

公共 API
--------

- :func:`extract_required_exts`：从 MatchSpec 提取必要扩展名集合（用于按扩展名
  分组规则，减少非必要 CONTENT re 调用）。
- :func:`build_content_buckets`：把 compiled pairs 拆为 (buckets, remaining_pairs)。
- :func:`match_content_via_buckets`：对指定 buckets 执行一次 finditer 分派并返回命中。
- :class:`_ContentRuleBucket`：桶数据结构（mode/case_sensitive/rules/compiled 正则等）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from re import Pattern
from typing import TYPE_CHECKING

from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchSpec,
    MatchTarget,
    OrMatch,
    Rule,
)
from fuscan.scanner._helpers import (
    GIL_YIELD_THRESHOLD_S,
    _dedup_substrings,
    _extract_inline_flags,
    _extract_literals,
    _flags_to_chars,
    build_hit_from_match,
)
from fuscan.scanner.matchers import Matcher
from fuscan.scanner.result import MatchResult, RuleHit

if TYPE_CHECKING:
    # fuscan_re 是 PyO3 编译扩展，无 Python stub；仅用于类型检查提示
    from fuscan_re import ContentBucketEngine  # pyrefly: ignore [missing-module-attribute]

__all__ = [
    "build_content_buckets",
    "extract_required_exts",
    "match_content_via_buckets",
]


def extract_required_exts(match: MatchSpec | None) -> frozenset[str] | None:  # noqa: PLR0912
    """从 MatchSpec 提取**必须匹配任一扩展名**的集合。

    若规则对扩展名无任何约束（例如纯 CONTENT 匹配、OR 组合的各分支提取不出来），
    返回 ``None``（表示所有扩展名都「可能命中」，不能在预筛阶段跳过）。
    若规则只对某些扩展名可能命中（例如 ``filename endswith ".env"`` AND ...），
    返回 **规范化扩展名集合**（小写、去点）。

    提取规则：
    - ``LeafMatch(target=FILENAME, mode=ENDWITH, pattern=X)``：从 X 提取扩展名
      （取最后一个 ``.`` 之后的部分）。
    - ``LeafMatch(target=FILENAME, mode=EQUALS, pattern=X)``：同上，X 有 ``.`` 时
      提取扩展名。
    - ``AndMatch(children=...)``：**取所有子项的交集**（每个子项均约束扩展名 →
      取并集中公共的；若有子项返回 None 则整体可能 None）——实际上 And 下只要求
      所有孩子均成立，因此若 C1 返回 E1、C2 返回 E2，则实际要求扩展名 ∈ E1 ∩ E2；
      若任一子项返回 None（无约束），则整体约束等价于其他孩子的约束集合。
    - ``OrMatch(children=...)``：若**所有**子项均返回非 None，取并集；若任一子项
      为 None，则整体返回 None。
    - ``NotMatch(child)``：无法安全反转扩展名约束，返回 None。
    - ``LeafMatch(target=CONTENT)`` / ``LeafMatch(target=PATH)``：返回 None（PATH
      匹配太难从路径片段提取扩展名）。

    :param match: 要分析的 MatchSpec（可从 ``Rule.match`` 得到）
    :return: 必要扩展名集合（小写去点）或 None（无约束）
    """
    if match is None:
        return None

    if isinstance(match, LeafMatch):
        if match.target is MatchTarget.FILENAME and match.mode in (MatchMode.ENDSWITH, MatchMode.EQUALS):
            # 从 pattern 中提取最后一个 "." 之后的部分
            pat = match.pattern
            dot = pat.rfind(".")
            if 0 <= dot < len(pat) - 1:
                ext = pat[dot + 1 :].lower()
                if ext:
                    return frozenset({ext})
            return None
        return None

    if isinstance(match, AndMatch):
        # 收集 children 中所有非 None 约束
        constrained: list[frozenset[str]] = []
        for c in match.children:
            e = extract_required_exts(c)
            if e is not None:
                constrained.append(e)
        if not constrained:
            return None
        # AND 下必须满足所有 constrained → 取交集
        result = constrained[0]
        for s in constrained[1:]:
            result = result & s
        return result if result else None

    if isinstance(match, OrMatch):
        # 必须所有子项都能提取出非 None，才能取并集
        exts: list[frozenset[str]] = []
        for c in match.children:
            e = extract_required_exts(c)
            if e is None:
                return None
            exts.append(e)
        if not exts:
            return None
        result = frozenset()
        for s in exts:
            result = result | s
        return result if result else None

    # NotMatch / 其他：放弃
    return None


@dataclass
class _ContentRuleBucket:
    """一组同 mode + 同 case_sensitive 的顶层纯 CONTENT 规则。

    组内规则使用命名捕获组的 OR 复合正则（``(?P<_f0>pat0)|(?P<_f1>pat1)|...``）
    一次 ``finditer`` 得到全部命中后按 ``lastgroup`` 分派到各规则，
    相比 20 条 CONTENT 规则各自独立调用 re 减少约 80~90% 的 Python→C re 调用开销。

    仅当规则是**顶层 LeafMatch 且 target=CONTENT**（无组合器包装）时会
    被加入桶；组合型/非 CONTENT 目标规则保留在 ``_remaining_rules`` 中走原路径。
    """

    mode: MatchMode
    case_sensitive: bool
    rules: list[Rule] = field(default_factory=list)
    # 组名 "_f{i}" → i（即 self.rules[i] 的下标）
    group_to_idx: dict[str, int] = field(default_factory=dict)
    compiled: Pattern[str] | None = None
    # 对 CONTAINS 模式：保存原始子串（含非空时需用 count 统计非重叠次数），
    # 以便构造 MatchResult 的 detail/match_text（保持与旧实现一致）
    contains_patterns: list[str] = field(default_factory=list)
    # 预筛关键字：从各规则字面量片段提取（长度 >= 3）。若 content 中不含
    # 任一关键字，则桶内所有规则必然不命中，可跳过 finditer；空列表表示
    # 无可用关键字（如纯 ``\d+`` 正则），不预筛，仍走 finditer
    prefilter_keywords: list[str] = field(default_factory=list)
    # 预筛是否大小写不敏感（桶 ``case_sensitive=False`` 或任一规则含 ``(?i)`` 内联标志）。
    # True 时关键字已小写化，匹配时 content 也小写化（lazy 计算）
    prefilter_case_insensitive: bool = False
    # 逐规则预筛关键字：per_rule_keywords[i] 为 rules[i] 的字面量片段列表（长度>=3，
    # 已去子串/去重）。空列表表示该规则无可提取字面量（如纯 ``[A-Z]{16}``），
    # 匹配时始终参与（保守不预筛）。与 rules/contains_patterns/sub_parts 下标对齐。
    per_rule_keywords: list[list[str]] = field(default_factory=list)
    # 逐规则子正则片段：sub_parts[i] 为 rules[i] 的命名捕获组子正则
    # （``(?P<_fi>...)``，含内联 flag 包装），供活跃子集动态复合正则拼接复用。
    sub_parts: list[str] = field(default_factory=list)
    # 活跃子集复合正则缓存：frozenset(活跃规则下标) -> 已编译复合正则。
    # 避免每次匹配都重编译活跃子集（普通文档活跃子集稳定，命中率高）。
    sub_compiled_cache: dict[frozenset[int], Pattern[str]] = field(default_factory=dict)


def build_content_buckets(  # noqa: PLR0912
    src_pairs: list[tuple[Rule, Matcher]],
) -> tuple[list[_ContentRuleBucket], list[tuple[Rule, Matcher]]]:
    """从 compiled pairs 中挑出顶层纯 LeafMatch(target=CONTENT)
    规则按 (mode, case_sensitive) 合并为复合 OR 正则桶。

    :param src_pairs: 已编译的 (Rule, Matcher) 对列表（按 ext 拆分后的子集或全局全量）
    :return: (buckets, remaining_pairs)
      - buckets: 可合并的 CONTENT 规则桶（数量 = 桶数）
      - remaining_pairs: 无法合入桶（组合型 / FILENAME / PATH 目标）
        的规则+匹配器对，保留给 _scan_entry_uncached 原循环。
    """
    grouped: dict[tuple[str, bool], _ContentRuleBucket] = {}
    bucketed_rule_names: set[str] = set()
    # 仅合并 LeafMatch(target=CONTENT)，且 mode 为 REGEX/CONTAINS/EQUALS/STARTSWITH/ENDSWITCH
    # 其中 EQUALS/STARTSWITH/ENDSWITH 也能转成正则：
    #   EQUALS(p)     -> ^p$
    #   STARTSWITH(p) -> ^p
    #   ENDSWITH(p)   -> p$
    for rule, _matcher in src_pairs:
        spec = rule.match
        if not isinstance(spec, LeafMatch) or spec.target != MatchTarget.CONTENT:
            continue
        # 仅处理可转为正则模式的叶子；特殊模式留作后续迭代
        if spec.mode not in (
            MatchMode.REGEX,
            MatchMode.CONTAINS,
            MatchMode.EQUALS,
            MatchMode.STARTSWITH,
            MatchMode.ENDSWITH,
        ):
            continue
        key = (spec.mode.value, spec.case_sensitive)
        if key not in grouped:
            grouped[key] = _ContentRuleBucket(mode=spec.mode, case_sensitive=spec.case_sensitive)
        bucket = grouped[key]
        bucket.rules.append(rule)
        bucketed_rule_names.add(rule.name)
        if spec.mode == MatchMode.CONTAINS:
            bucket.contains_patterns.append(spec.pattern)
        else:
            bucket.contains_patterns.append("")
    # 构造复合 OR 正则
    compiled_buckets: list[_ContentRuleBucket] = []
    for key, bucket in grouped.items():
        if len(bucket.rules) <= 1:
            # 单条规则无合并收益（合并的开销会高于直接跑），丢回 remaining
            bucketed_rule_names.discard(bucket.rules[0].name)
            continue
        _mode_val, case_sensitive = key
        parts: list[str] = []
        # 收集预筛关键字（桶内所有规则字面量片段）与内联 (?i) 标志
        prefilter_keywords: list[str] = []
        # 逐规则关键字与子正则片段（下标与 bucket.rules 对齐）
        per_rule_keywords: list[list[str]] = []
        sub_parts: list[str] = []
        has_inline_ignorecase = False
        for i, rule in enumerate(bucket.rules):
            spec = rule.match
            assert isinstance(spec, LeafMatch)
            # 根据 mode 生成对应子正则片段（对需要 escape 的先 escape）
            if spec.mode == MatchMode.REGEX:
                sub = spec.pattern
            elif spec.mode == MatchMode.CONTAINS:
                sub = re.escape(spec.pattern)
            elif spec.mode == MatchMode.EQUALS:
                sub = rf"^{re.escape(spec.pattern)}$"
            elif spec.mode == MatchMode.STARTSWITH:
                sub = rf"^{re.escape(spec.pattern)}"
            else:  # ENDSWITH
                sub = rf"{re.escape(spec.pattern)}$"
            grp_name = f"_f{i}"
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
            sub_parts.append(part)
            bucket.group_to_idx[grp_name] = i
            # 从清洗后的子正则中提取字面量片段作为预筛关键字（桶级合并 + 逐规则记录）
            rule_literals = _extract_literals(sub_clean)
            prefilter_keywords.extend(rule_literals)
            per_rule_keywords.append(rule_literals)
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile("|".join(parts), flags)
        except re.error:
            # 若某规则含非法命名组号或其它导致 OR 复合失败的情况，
            # 安全降级：该桶整体回到 remaining 原循环
            for r in bucket.rules:
                bucketed_rule_names.discard(r.name)
            continue
        bucket.compiled = compiled
        bucket.sub_parts = sub_parts
        # 设置预筛关键字：桶 case_sensitive=False 或任一规则含 (?i) 时按大小写不敏感处理
        # （关键字小写化，匹配时 content 也小写化）；否则保持原样
        prefilter_ci = (not case_sensitive) or has_inline_ignorecase
        if prefilter_ci:
            # 桶级与逐规则关键字同步小写化
            bucket.prefilter_keywords = _dedup_substrings([k.lower() for k in prefilter_keywords])
            bucket.per_rule_keywords = [_dedup_substrings([k.lower() for k in kws]) for kws in per_rule_keywords]
        else:
            bucket.prefilter_keywords = _dedup_substrings(prefilter_keywords)
            bucket.per_rule_keywords = [_dedup_substrings(kws) for kws in per_rule_keywords]
        bucket.prefilter_case_insensitive = prefilter_ci
        compiled_buckets.append(bucket)
    remaining = [(r, m) for r, m in src_pairs if r.name not in bucketed_rule_names]
    return compiled_buckets, remaining


def _compute_active_indices(bucket: _ContentRuleBucket, haystack: str) -> list[int]:
    """计算桶内**活跃规则下标**：其 per-rule 关键字出现在 haystack 中（或无关键字）。

    逐规则预筛的核心——相比桶级 ``any()`` 粗粒度短路，本函数精确到"哪几条规则
    的字面量真正出现"，使普通文档只对少数命中关键字的规则运行正则，
    而非整桶复合正则全量 ``finditer``（普通 md 偶现常见词即触发全桶的根因）。

    - ``per_rule_keywords[i]`` 为空：该规则无可提取字面量（如纯 ``[A-Z]{16}``），
      无法安全预筛，始终视为活跃（保守不漏）。
    - 非空：任一关键字出现在 haystack 中则活跃；均不出现则该规则必不命中，剔除。

    :param bucket: 目标桶（须已构建 per_rule_keywords）
    :param haystack: 已按大小写规则处理的内容（不敏感桶为小写化后的 content）
    :return: 活跃规则下标列表（升序，与 rules 下标对齐）
    """
    active: list[int] = []
    for idx in range(len(bucket.rules)):
        kws: list[str] = bucket.per_rule_keywords[idx] if idx < len(bucket.per_rule_keywords) else []
        if not kws or any(kw in haystack for kw in kws):
            active.append(idx)
    return active


def _get_active_compiled(bucket: _ContentRuleBucket, active_idx: list[int]) -> Pattern[str] | None:
    """获取仅含 ``active_idx`` 规则的复合正则（带缓存），全部活跃时复用 bucket.compiled。

    普通文档活跃子集稳定（通常 0~1 条），以 ``frozenset(active_idx)`` 为键缓存
    编译结果，避免每次匹配重编译；密钥密集文档退化为全桶（直接用 bucket.compiled），
    无回归。命名组 ``_f{i}`` 与 ``group_to_idx`` 一致，分派逻辑不变。

    :param bucket: 目标桶（须已构建 sub_parts）
    :param active_idx: 活跃规则下标列表（非空）
    :return: 已编译复合正则；编译失败返回 None（调用方回退到 bucket.compiled）
    """
    if len(active_idx) == len(bucket.rules):
        # 全部活跃：直接用整桶复合正则（等价旧行为）
        return bucket.compiled
    key = frozenset(active_idx)
    cached = bucket.sub_compiled_cache.get(key)
    if cached is not None:
        return cached
    parts = [bucket.sub_parts[i] for i in active_idx]
    flags = 0 if bucket.case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile("|".join(parts), flags)
    except re.error:
        # 理论上不会发生（子片段均来自构建期已编译成功的整桶正则）；
        # 保守回退到整桶正则，避免漏匹配
        return bucket.compiled
    bucket.sub_compiled_cache[key] = compiled
    return compiled


def match_content_via_buckets(  # noqa: PLR0912
    content: str,
    buckets: list[_ContentRuleBucket],
    native_engine: ContentBucketEngine | None = None,
) -> list[RuleHit]:
    """对指定的 CONTENT 桶执行**逐规则预筛 + 活跃子集匹配**并返回命中列表。

    若 ``native_engine`` 非 None，优先调用原生引擎（Rust + PyO3，释放 GIL），
    与本函数 Python 实现语义完全等价。原生引擎异常时返回空列表，调用方应回退到
    Python 路径重试（典型用法：catch ``match_content_via_buckets`` 异常后再以
    ``native_engine=None`` 调用一次）。当 ``buckets`` 与构造 ``native_engine``
    时的桶集不一致时，结果可能包含/缺失部分规则命中——调用方需保证二者一致
    （Scanner 在 ``_CompiledRuleset`` 缓存层维护 global + 各 ext 对应引擎）。

    两级预筛：先用桶级关键字 ``any()`` 快速短路整桶（0 关键字命中直接跳过），
    再用 per-rule 关键字精确筛出**活跃规则子集**，仅对该子集动态编译复合正则并
    ``finditer``。相比旧的桶级粗粒度预筛（偶现一个常见词即跑整桶复合正则），
    普通文档只对真正命中关键字的规则运行正则，避免大文本 finditer 阻塞。

    预筛保证不产生 false negative：

    - 关键字为正则字面量片段，必然出现在任何匹配文本中
    - 对 ``|`` 分支提取所有分支的字面量，预筛用 ``any()`` 短路
    - 无字面量规则（如纯 ``[A-Z]{16}``）始终活跃，不漏匹配
    - 大小写不敏感桶（``case_sensitive=False`` 或含 ``(?i)``）：关键字小写化，
      content 也小写化（lazy 一次性计算）

    :param content: 文件文本内容
    :param buckets: 已编译的 CONTENT 桶列表（global + ext 专属可合并传入）
    :param native_engine: 可选的原生引擎（须与 ``buckets`` 同源构建）；非 None 时
        优先走原生路径，异常时返回空列表
    :return: 命中的 RuleHit 列表（每个桶内每条规则最多产出一条聚合命中）
    """
    if native_engine is not None:
        # 延迟导入打破循环依赖：_native_matchers -> _content_buckets -> _native_matchers
        from fuscan.scanner._native_matchers import match_content_via_native

        return match_content_via_native(native_engine, content)
    hits: list[RuleHit] = []
    # 大小写不敏感预筛时复用同一份小写化 content（lazy 计算）
    content_lower: str | None = None
    # GIL 让步基线：本函数在扫描 worker 线程内执行，桶间的 finditer 是持 GIL 的
    # 纯 Python C 调用。在每个桶处理完成后按时间式判断让步（距上次让步超过
    # GIL_YIELD_THRESHOLD_S 才 sleep(0)），把「单文件内多个 finditer 背靠背」
    # 拆成多个可让步点，令 GUI 主线程能在文件扫描中途抢到 GIL，缓解界面冻结。
    # 基线为函数局部变量（严禁挂到共享状态——多 worker 会竞争）；每次调用重置
    # 计时可接受（每个文件独立起点，反而让步更勤更保守）。
    last_yield = time.perf_counter()
    for bucket in buckets:
        if bucket.compiled is None:
            continue
        # 选择预筛 haystack：不敏感桶用小写化 content（lazy），否则用原 content
        if bucket.prefilter_case_insensitive:
            if content_lower is None:
                content_lower = content.lower()
            haystack = content_lower
        else:
            haystack = content
        # 桶级快速短路：整桶关键字均不在 content 中 → 桶内所有规则必不命中
        if bucket.prefilter_keywords and not any(kw in haystack for kw in bucket.prefilter_keywords):
            continue
        # 逐规则预筛：计算活跃规则下标，仅对活跃子集运行匹配
        active_idx = _compute_active_indices(bucket, haystack)
        if not active_idx:
            continue
        active_set = set(active_idx)
        # 先按规则聚合：rule_idx -> [first_match_text, total_count]
        # 对 CONTAINS(case_sensitive)：直接用 count 计算，不走 finditer
        per_rule: list[tuple[str, int] | None] = [None] * len(bucket.rules)
        if bucket.mode == MatchMode.CONTAINS and bucket.case_sensitive:
            # 与旧 _apply_contains 一致：非重叠 count，match_text=pattern；仅活跃规则
            for idx in active_idx:
                pat = bucket.contains_patterns[idx]
                if not pat:
                    continue
                cnt = content.count(pat)
                if cnt > 0:
                    per_rule[idx] = (pat, cnt)
        else:
            compiled = _get_active_compiled(bucket, active_idx)
            if compiled is None:
                continue
            for m in compiled.finditer(content):
                last = m.lastgroup
                if last is None:
                    continue
                idx = bucket.group_to_idx.get(last)
                # 活跃子集正则理论上只含活跃组，但整桶回退时可能命中非活跃组，
                # 用 active_set 二次校验保持语义一致
                if idx is None or idx not in active_set:
                    continue
                txt = m.group(0)
                prev = per_rule[idx]
                if prev is None:
                    per_rule[idx] = (txt, 1)
                else:
                    per_rule[idx] = (prev[0], prev[1] + 1)
        # 构造 MatchResult 并转 RuleHit
        for idx, accum in enumerate(per_rule):
            if accum is None:
                continue
            first_txt, total_cnt = accum
            rule = bucket.rules[idx]
            spec = rule.match
            assert isinstance(spec, LeafMatch)
            # detail / match_description 要与旧实现的 _apply_leaf 一致
            if bucket.mode == MatchMode.REGEX:
                detail = f"正则命中: {first_txt!r}"
            elif bucket.mode == MatchMode.CONTAINS:
                detail = f"包含 {spec.pattern!r}"
                first_txt = spec.pattern
            elif bucket.mode == MatchMode.EQUALS:
                detail = "完全相等"
                first_txt = spec.pattern
            elif bucket.mode == MatchMode.STARTSWITH:
                detail = f"以 {spec.pattern!r} 开头"
                first_txt = spec.pattern
            else:  # ENDSWITH
                detail = f"以 {spec.pattern!r} 结尾"
                first_txt = spec.pattern
            result = MatchResult(
                matched=True,
                detail=detail,
                match_text=first_txt,
                match_count=total_cnt,
                target=MatchTarget.CONTENT.value,
                match_texts=(first_txt,) if first_txt else (),
                match_description=spec.description,
            )
            hits.append(build_hit_from_match(rule, result))
        # 桶处理完成：按时间式让步。continue 短路路径（无关键字/无活跃规则）
        # 开销极小且不持 GIL 太久，不经过此处也无碍；走到这里的桶意味着刚跑过
        # 可能较重的 finditer/count，正是需要给主线程让出 GIL 的时机。
        now = time.perf_counter()
        if now - last_yield >= GIL_YIELD_THRESHOLD_S:
            last_yield = now
            time.sleep(0)
    return hits
