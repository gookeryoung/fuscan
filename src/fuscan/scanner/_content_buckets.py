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
import warnings
from dataclasses import dataclass, field
from re import Pattern
from typing import Any

try:
    from re import _parser as _sre_parse  # type: ignore[missing-module-attribute]  # Python 3.11+
except ImportError:
    # Python 3.10：sre_parse 仍可用但已废弃，屏蔽 DeprecationWarning
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import sre_parse as _sre_parse  # type: ignore[no-redef,import-not-found]

from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchSpec,
    MatchTarget,
    OrMatch,
    Rule,
)
from fuscan.scanner._helpers import build_hit_from_match
from fuscan.scanner.matchers import Matcher
from fuscan.scanner.result import MatchResult, RuleHit

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


_INLINE_FLAG_MAP: dict[str, int] = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "x": re.VERBOSE,
}


def _extract_inline_flags(pattern: str) -> tuple[str, int]:
    """提取正则模式开头的内联标志（如 ``(?i)``、``(?im)``）。

    Python 3.11+ 对内联标志不在表达式开头的情况发出 DeprecationWarning，
    因为 ``(?i)`` 等在命名组内部时会影响后续所有内容而非仅当前组。
    本函数将其提取出来，供调用方用 ``(?flag:...)`` 非捕获组语法包装，
    使标志仅作用于目标子模式，避免污染同一 OR 复合正则中的其他分支。

    :param pattern: 原始正则模式
    :return: ``(清理后的模式, 提取的标志位组合)``
    """
    extracted = 0
    pos = 0
    while pos < len(pattern) and pattern[pos] == "(":
        m = re.match(r"\(\?([imsx]+)\)", pattern[pos:])
        if not m:
            break
        for ch in m.group(1):
            extracted |= _INLINE_FLAG_MAP.get(ch, 0)
        pos += m.end()
    return pattern[pos:], extracted


def _flags_to_chars(flags: int) -> str:
    """将标志位组合转换为内联标志字符串（如 ``re.IGNORECASE | re.DOTALL`` → ``is``）。"""
    chars: list[str] = []
    for ch, bit in _INLINE_FLAG_MAP.items():
        if flags & bit:
            chars.append(ch)
    return "".join(chars)


def _walk_sre_ast(nodes: Any, min_len: int, prefix: str = "") -> list[str]:
    """递归遍历 sre_parse AST 节点，提取长度 >= ``min_len`` 的字面量片段。

    用于 CONTENT 桶预筛：从正则 AST 中提取所有"必然出现在匹配文本中"的字面量。
    若这些字面量均不在内容中，则正则必然不命中，可安全跳过 ``finditer``，
    避免大文本上的不可中断 C 调用阻塞主线程（5MB md 文件 770ms → ~18ms）。

    处理的节点类型：

    - ``LITERAL``：累积到当前字面串。
    - ``BRANCH``（``|``）：各分支独立递归，前缀继承（sre_parse 会把公共前缀
      提到 BRANCH 之外，例如 ``(password|passwd|pwd)`` 解析为 ``p`` + BRANCH。
      前缀递归确保正确还原 ``password``/``passwd``/``pwd``）。
    - ``SUBPATTERN``（捕获组）：递归内部，前缀继承。
    - ``MAX_REPEAT``（量词 ``*+?{n,m}``）：内部字面量可能不出现（如 ``a?``），
      前缀不传递，但内部仍递归以提取可保证出现的字面量。
    - ``IN``（字符类）：若全部为单字面量（如 ``[abc]``）则展开为各候选前缀组合；
      含 ``RANGE``/``CATEGORY``（如 ``[A-Z]``）的字符类无法提取确定字面量。

    :param nodes: sre_parse 解析后的节点列表（``list[(op, args), ...]``，运行期为
        ``SubPattern``，duck-type 为可迭代的 ``(op, args)`` 元组序列）
    :param min_len: 字面量最小长度
    :param prefix: 当前累积的字面前缀（用于 BRANCH/SUBPATTERN 共享前缀）
    :return: 字面量片段列表（可能含重复，由调用方去重）
    """
    literals: list[str] = []
    current = prefix
    for op, args in nodes:
        s = str(op)
        if s == "LITERAL":
            current += chr(args)
            continue
        # 非字面量操作：终结当前字面串
        prefix_for_recurse = current
        if current and len(current) >= min_len:
            literals.append(current)
        current = ""
        if s == "BRANCH":
            # | 分支：各分支独立，共享前缀
            for branch in args[1]:
                literals.extend(_walk_sre_ast(branch, min_len, prefix=prefix_for_recurse))
        elif s == "SUBPATTERN":
            # 捕获组：递归内部，前缀继承
            literals.extend(_walk_sre_ast(args[3], min_len, prefix=prefix_for_recurse))
        elif s == "MAX_REPEAT":
            # 量词：内部字面量可能不出现，前缀不传递
            literals.extend(_walk_sre_ast(args[2], min_len, prefix=""))
        elif s == "IN":
            # 字符类：若全为单字面量则展开为候选前缀组合
            sub = list(args)
            if sub and all(str(so) == "LITERAL" for so, _ in sub):
                for _so, sa in sub:
                    candidate = prefix_for_recurse + chr(sa)
                    if len(candidate) >= min_len:
                        literals.append(candidate)
            # 含 RANGE/CATEGORY（如 [A-Z]）的字符类不提取
    if current and len(current) >= min_len:
        literals.append(current)
    return literals


def _extract_literals(pattern: str, min_len: int = 3) -> list[str]:
    """从正则模式中提取字面量片段（长度 >= ``min_len``）。

    解析 sre_parse AST，提取所有"必然出现在匹配文本中"的字面量。
    内联标志（如 ``(?i)``）先剥离——它们不影响字面量提取，仅影响匹配大小写。

    用途：CONTENT 桶预筛关键字。若所有提取的字面量均不在内容中，
    则正则必然不命中，可安全跳过 ``finditer``。

    :param pattern: 正则模式（可能含内联标志 ``(?i)`` 等）
    :param min_len: 字面量最小长度（默认 3，避免过短关键字如单字母导致高误报率）
    :return: 去重后的字面量列表（保留首次出现顺序）
    """
    cleaned, _ignored = _extract_inline_flags(pattern)
    try:
        ast: Any = _sre_parse.parse(cleaned)
    except Exception:
        # 非法正则或解析失败：保守返回空列表（不预筛，仍走 finditer）
        return []
    seen: set[str] = set()
    result: list[str] = []
    for lit in _walk_sre_ast(ast, min_len):
        if lit not in seen:
            seen.add(lit)
            result.append(lit)
    return result


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
    # True 时关键字已小写化，匹配时 content 也需小写化（lazy 计算）
    prefilter_case_insensitive: bool = False


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
                parts.append(rf"(?{flag_str}:(?P<{grp_name}>{sub_clean}))")
            else:
                parts.append(rf"(?P<{grp_name}>{sub})")
            bucket.group_to_idx[grp_name] = i
            # 从清洗后的子正则中提取字面量片段作为预筛关键字
            prefilter_keywords.extend(_extract_literals(sub_clean))
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
        # 设置预筛关键字：桶 case_sensitive=False 或任一规则含 (?i) 时按大小写不敏感处理
        # （关键字小写化，匹配时 content 也小写化）；否则保持原样
        prefilter_ci = (not case_sensitive) or has_inline_ignorecase
        if prefilter_ci:
            bucket.prefilter_keywords = list(dict.fromkeys(k.lower() for k in prefilter_keywords))
        else:
            bucket.prefilter_keywords = list(dict.fromkeys(prefilter_keywords))
        bucket.prefilter_case_insensitive = prefilter_ci
        compiled_buckets.append(bucket)
    remaining = [(r, m) for r, m in src_pairs if r.name not in bucketed_rule_names]
    return compiled_buckets, remaining


def match_content_via_buckets(  # noqa: PLR0912
    content: str,
    buckets: list[_ContentRuleBucket],
) -> list[RuleHit]:
    """对指定的 CONTENT 桶执行一次 finditer 分派并返回命中列表。

    在调用 ``finditer`` 前对每个桶做**字面量预筛**：从桶内规则的字面量片段中
    提取关键字，若 content 中不含任一关键字，则桶内所有规则必然不命中，
    可直接跳过 ``finditer``。对大文件（5MB md）从 770ms 降至 ~18ms（60x 加速）。

    预筛保证不产生 false negative：

    - 关键字为正则字面量片段，必然出现在任何匹配文本中
    - 对 ``|`` 分支提取所有分支的字面量，预筛用 ``any()`` 短路
    - 大小写不敏感桶（``case_sensitive=False`` 或含 ``(?i)``）：关键字小写化，
      content 也小写化（lazy 一次性计算）

    :param content: 文件文本内容
    :param buckets: 已编译的 CONTENT 桶列表（global + ext 专属可合并传入）
    :return: 命中的 RuleHit 列表（每个桶内每条规则最多产出一条聚合命中）
    """
    hits: list[RuleHit] = []
    # 大小写不敏感预筛时复用同一份小写化 content（lazy 计算）
    content_lower: str | None = None
    for bucket in buckets:
        if bucket.compiled is None:
            continue
        # 字面量预筛：若所有关键字均不在 content 中，跳过 finditer
        if bucket.prefilter_keywords:
            if bucket.prefilter_case_insensitive:
                if content_lower is None:
                    content_lower = content.lower()
                haystack = content_lower
            else:
                haystack = content
            # any() 短路：命中任一关键字即通过
            if not any(kw in haystack for kw in bucket.prefilter_keywords):
                continue
        # 先按规则聚合：rule_idx -> [first_match_text, total_count]
        # 对 CONTAINS(case_sensitive)：直接用 count 计算，不走 finditer
        per_rule: list[tuple[str, int] | None] = [None] * len(bucket.rules)
        if bucket.mode == MatchMode.CONTAINS and bucket.case_sensitive:
            # 与旧 _apply_contains 一致：非重叠 count，match_text=pattern
            for idx, rule in enumerate(bucket.rules):
                spec = rule.match
                assert isinstance(spec, LeafMatch)
                pat = bucket.contains_patterns[idx]
                if not pat:
                    continue
                cnt = content.count(pat)
                if cnt > 0:
                    per_rule[idx] = (pat, cnt)
        else:
            for m in bucket.compiled.finditer(content):
                last = m.lastgroup
                if last is None:
                    continue
                idx = bucket.group_to_idx.get(last)
                if idx is None:
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
    return hits
