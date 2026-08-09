"""fuscan-re 原生匹配引擎集成层：条件导入 + RuleSpec 构建 + RuleHit 转换。

模块背景
----------

``fuscan_re`` 是 fuscan 的 Rust + PyO3 原生匹配引擎，将 Python
:func:`fuscan.scanner._content_buckets.match_content_via_buckets` 的核心逻辑下沉到
Rust：用 ``regex`` crate（DFA + aho-corasick）替代 Python ``re``，并通过 PyO3
``py.detach`` 释放 GIL，实现大文本复合正则的真正并行匹配。

本模块在 Python 侧提供薄包装：

- 运行时条件导入 ``fuscan_re``：未安装/导入失败时 ``NATIVE_AVAILABLE=False``，
  扫描器自动回退纯 Python 路径，不影响功能。
- :func:`build_native_engine` 从 Python 桶提取 :class:`RuleSpec` 构建原生引擎。
- :func:`match_content_via_native` 调用原生引擎匹配并将 :class:`RuleHitData`
  转回 :class:`fuscan.scanner.result.RuleHit`。

语义等价：与 Python ``match_content_via_buckets`` 完全一致的命中结果
（first_match_text / total_count / detail / match_texts / match_description）。
当原生引擎抛异常时，:func:`match_content_via_native` 返回空列表，调用方应回退到
Python 路径重试（已由 :func:`match_content_via_buckets` 的 ``native_engine=None``
回退路径覆盖）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fuscan.rules.model import LeafMatch, MatchTarget, Severity
from fuscan.scanner._content_buckets import _ContentRuleBucket
from fuscan.scanner.result import MatchResult, RuleHit

if TYPE_CHECKING:
    # fuscan_re 是 PyO3 编译扩展，无 Python stub；仅用于类型检查提示
    from fuscan_re import (
        ContentBucketEngine,  # pyrefly: ignore [missing-module-attribute]
        ContentRegexPoolEngine,  # pyrefly: ignore [missing-module-attribute]
        PoolHitData,  # pyrefly: ignore [missing-module-attribute]
        RuleHitData,  # pyrefly: ignore [missing-module-attribute]
    )

__all__ = [
    "NATIVE_AVAILABLE",
    "build_native_engine",
    "build_native_regex_pool",
    "evaluate_regex_pool_via_native",
    "match_content_via_native",
]

logger = logging.getLogger(__name__)

try:
    from fuscan_re import ContentBucketEngine as _ContentBucketEngine  # pyrefly: ignore [missing-module-attribute]
    from fuscan_re import (
        ContentRegexPoolEngine as _ContentRegexPoolEngine,  # pyrefly: ignore [missing-module-attribute]
    )

    NATIVE_AVAILABLE: bool = True
except ImportError:  # pragma: no cover - fuscan_re 未安装时走此分支，CI 已安装跳过
    NATIVE_AVAILABLE = False
    _ContentBucketEngine = None  # type: ignore[assignment,misc]
    _ContentRegexPoolEngine = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class RuleSpec:
    """传给原生引擎的规则规格。

    PyO3 ``FromPyObject`` derive 通过 ``getattr`` 提取字段，因此需用 dataclass
    实例（具备同名属性）传入，而非 dict。字段顺序与命名严格对齐 Rust 端
    :class:`fuscan_re::RuleSpec`。
    """

    rule_name: str
    severity: str
    description: str
    mode: str
    pattern: str
    case_sensitive: bool


def build_native_engine(
    buckets: list[_ContentRuleBucket],
) -> ContentBucketEngine | None:
    """从 Python CONTENT 桶构建原生匹配引擎。

    遍历每个桶的 ``rules``，从 :class:`LeafMatch` 提取六元组
    (rule_name/severity/description/mode/pattern/case_sensitive) 交给原生引擎，
    由其按 (mode, case_sensitive) 重新分桶并编译复合 OR 正则。单条规则的桶会被
    原生引擎自动跳过（与 Python ``build_content_buckets`` 一致）。

    :param buckets: 已编译的 CONTENT 桶列表（global + ext 专属可合并传入）
    :return: 原生引擎实例；``fuscan_re`` 不可用、无规则或构建失败时返回 None
    """
    if not NATIVE_AVAILABLE or not buckets:
        return None

    specs: list[RuleSpec] = []
    for bucket in buckets:
        for rule in bucket.rules:
            spec = rule.match
            if not isinstance(spec, LeafMatch):
                continue
            specs.append(
                RuleSpec(
                    rule_name=rule.name,
                    severity=rule.severity.value,
                    description=spec.description,
                    mode=spec.mode.value,
                    pattern=spec.pattern,
                    case_sensitive=spec.case_sensitive,
                )
            )

    if not specs:
        return None

    try:
        assert _ContentBucketEngine is not None
        return _ContentBucketEngine(specs)
    except Exception:
        logger.warning("构建原生匹配引擎失败，回退到 Python 路径", exc_info=True)
        return None


def match_content_via_native(
    engine: ContentBucketEngine,
    content: str,
) -> list[RuleHit]:
    """通过原生引擎匹配内容并转 :class:`RuleHit` 列表。

    释放 GIL 期间执行纯 Rust 匹配，结果与 Python ``match_content_via_buckets``
    完全一致。原生引擎抛异常时返回空列表，调用方应回退到 Python 路径重试
    （由 :func:`match_content_via_buckets` 的 ``native_engine=None`` 回退路径覆盖）。

    :param engine: :func:`build_native_engine` 返回的原生引擎
    :param content: 文件文本内容
    :return: 命中的 RuleHit 列表；引擎异常时返回空列表
    """
    try:
        raw_hits = engine.match_content(content)
    except Exception:
        logger.warning("原生匹配引擎执行失败，回退到 Python 路径", exc_info=True)
        return []

    return [_convert_hit(raw) for raw in raw_hits]


def _convert_hit(raw: RuleHitData) -> RuleHit:
    """将原生引擎的 :class:`RuleHitData` 转为 :class:`RuleHit`。

    字段映射：

    - ``rule_name`` / ``detail`` / ``match_text`` / ``match_count`` /
      ``target`` / ``match_description`` 直接透传
    - ``severity`` 由 ``str`` 转回 :class:`Severity` 枚举
    - ``match_texts`` 由 ``list[str]`` 转 ``tuple[str, ...]``（与 Python
      ``match_content_via_buckets`` 输出类型一致）
    """
    return RuleHit(
        rule_name=raw.rule_name,
        severity=Severity(raw.severity),
        detail=raw.detail,
        match_text=raw.match_text,
        match_count=raw.match_count,
        target=raw.target,
        match_texts=tuple(raw.match_texts),
        match_description=raw.match_description,
    )


# ============================================================================
# ContentRegexPoolEngine：跨规则 CONTENT REGEX 子项池原生引擎
# ============================================================================


@dataclass(frozen=True)
class PoolGroupSpecData:
    """传给原生正则池引擎的子项规格。

    与 Rust 端 :class:`fuscan_re::PoolGroupSpec` 字段严格对齐。
    PyO3 ``FromPyObject`` 通过 ``getattr`` 提取字段，须用 dataclass 实例传入。
    """

    child_id: int
    pattern: str
    case_sensitive: bool
    description: str


def build_native_regex_pool(
    specs: list[PoolGroupSpecData],
) -> ContentRegexPoolEngine | None:
    """从子项规格列表构建原生正则池引擎。

    遍历 ContentRegexPool 的所有已注册子项，提取四元组
    (child_id/pattern/case_sensitive/description) 交给原生引擎，
    由其按 case_sensitive 重新分组并编译复合 OR 正则。单子项组会被
    原生引擎自动跳过（与 Python ``ContentRegexPool.compile`` 一致）。

    :param specs: 池中所有已注册子项的规格列表
    :return: 原生引擎实例；``fuscan_re`` 不可用、无规格或构建失败时返回 None
    """
    if not NATIVE_AVAILABLE or not specs:
        return None

    try:
        assert _ContentRegexPoolEngine is not None
        return _ContentRegexPoolEngine(specs)
    except Exception:
        logger.warning("构建原生正则池引擎失败，回退到 Python 路径", exc_info=True)
        return None


def evaluate_regex_pool_via_native(
    engine: ContentRegexPoolEngine,
    content: str,
) -> dict[int, MatchResult]:
    """通过原生正则池引擎匹配内容并转 ``child_id -> MatchResult`` 字典。

    释放 GIL 期间执行纯 Rust 匹配，结果与 Python
    ``ContentRegexPool.evaluate`` 完全一致。原生引擎抛异常时返回空字典，
    调用方应回退到 Python 路径重试。

    :param engine: :func:`build_native_regex_pool` 返回的原生引擎
    :param content: 文件文本内容
    :return: ``{child_id: MatchResult}``，仅含命中的子项
    """
    try:
        raw_hits = engine.evaluate(content)
    except Exception:
        logger.warning("原生正则池引擎执行失败，回退到 Python 路径", exc_info=True)
        return {}

    return {hit.child_id: _convert_pool_hit(hit) for hit in raw_hits}


def _convert_pool_hit(raw: PoolHitData) -> MatchResult:
    """将原生池命中结果 :class:`PoolHitData` 转为 :class:`MatchResult`。

    字段映射与 Python ``_evaluate_group`` 构造的 :class:`MatchResult` 一致：
    ``detail`` / ``match_count`` / ``match_description`` 直接透传，
    ``match_text`` 取 ``first_match_text``，``match_texts`` 按
    ``(first_txt,) if first_txt else ()`` 规则构造。
    """
    first_txt = raw.first_match_text
    return MatchResult(
        matched=True,
        detail=raw.detail,
        match_text=first_txt,
        match_count=raw.match_count,
        target=MatchTarget.CONTENT.value,
        match_texts=(first_txt,) if first_txt else (),
        match_description=raw.match_description,
    )
