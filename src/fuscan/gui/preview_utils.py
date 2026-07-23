"""GUI 预览区共用工具：关键词提取、HTML 构建、严重等级配色。

``main_window.py`` 与 ``detail_panel.py`` 共用此模块，避免 5 个函数与 6 个常量
的重复定义。任何一处修复 bug 即两处生效，符合 DRY 原则。

QSS 与代码颜色统一引用 :mod:`fuscan.theme` 令牌。
"""

from __future__ import annotations

import html
import logging
import re
from typing import Sequence

try:
    from PySide2.QtGui import QColor
except ImportError:  # pragma: no cover
    from PySide6.QtGui import QColor  # pyrefly: ignore [missing-import]

from fuscan.rules.model import Severity
from fuscan.scanner.result import RuleHit, format_size

__all__ = [
    "HIGHLIGHT_STYLE",
    "KEYWORD_RE",
    "PREVIEW_MAX_CHARS",
    "PREVIEW_STYLE",
    "SEVERITY_BACKGROUNDS",
    "SEVERITY_LABELS",
    "build_keyword_to_rule_map",
    "build_preview_html",
    "compile_keyword_pattern",
    "extract_keywords",
    "format_size",
    "severity_text",
]

logger = logging.getLogger(__name__)

# 内容预览最大字符数，避免大文件阻塞 UI
PREVIEW_MAX_CHARS = 100 * 1024

# 从 detail 中提取关键词的正则，匹配单引号包裹的内容
KEYWORD_RE = re.compile(r"'([^']+)'")

# 内容预览 pre 标签样式
PREVIEW_STYLE = (
    "font-family: Consolas, 'Courier New', monospace; font-size: 12px; white-space: pre-wrap; word-wrap: break-word;"
)

# 关键词高亮 span 样式
HIGHLIGHT_STYLE = "background-color: yellow; color: black;"

# 严重等级 → 中文标签
SEVERITY_LABELS: dict[Severity, str] = {
    Severity.CRITICAL: "严重",
    Severity.WARNING: "警告",
    Severity.INFO: "一般",
}

# 严重等级 → 浅色背景（用于 severity 列与 critical 整行高亮）
# 注：不提供前景色，避免 setForeground 覆盖 QSS ::item:selected 选中态白字
SEVERITY_BACKGROUNDS: dict[Severity, QColor] = {
    Severity.CRITICAL: QColor(255, 235, 235),  # 浅红
    Severity.WARNING: QColor(255, 243, 224),  # 浅橙
    Severity.INFO: QColor(235, 244, 255),  # 浅蓝
}


def severity_text(severity: Severity) -> str:
    """返回严重等级的中文标签，未知等级回退到枚举原始值。

    供 main_window / detail_panel / result_tree 三处 ``_apply_severity_to_*`` 共用，
    消除原先三处重复的 ``SEVERITY_LABELS.get(severity, severity.value)`` 内联逻辑。
    """
    return SEVERITY_LABELS.get(severity, severity.value)


def extract_keywords(hits: Sequence[RuleHit]) -> list[str]:
    """从命中规则中提取高亮关键词。

    优先遍历 ``RuleHit.match_texts``（含组合规则全部命中文本，去重保序）；
    ``match_texts`` 为空时回退到 ``match_text``（兼容旧缓存）；
    两者均空时回退到从 ``detail`` 中提取单引号包裹的内容。
    """
    keywords: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        # 优先 match_texts（AND/OR 等组合规则记录的全部命中文本）
        texts = hit.match_texts if hit.match_texts else ((hit.match_text,) if hit.match_text else ())
        if not texts:
            # 组合规则无单一匹配文本，回退到 detail 解析
            for match in KEYWORD_RE.finditer(hit.detail):
                kw = match.group(1)
                if kw:
                    texts = (kw,)
                    break
        for kw in texts:
            if kw and kw not in seen:
                seen.add(kw)
                keywords.append(kw)
    return keywords


def build_preview_html(content: str, keywords: Sequence[str]) -> str:
    """构建内容预览 HTML，关键词以黄色背景高亮。

    先对内容做 html.escape 转义，再用单次正则替换插入高亮 span，
    避免多次 replace 破坏已插入的 HTML 标签。
    关键词中的换行符规范化为 ``\\s+`` 以支持跨行高亮。
    """
    escaped = html.escape(content)
    if keywords:
        kw_patterns: list[str] = []
        for kw in sorted({k for k in keywords if k}, key=len, reverse=True):
            escaped_kw = html.escape(kw)
            if re.search(r"[\r\n]", escaped_kw):
                # 包含换行符：分段转义，用 \s+ 连接以支持跨行高亮
                parts = [p for p in re.split(r"[\r\n]+", escaped_kw) if p]
                kw_patterns.append(r"\s+".join(re.escape(p) for p in parts))
            else:
                kw_patterns.append(re.escape(escaped_kw))
        if kw_patterns:
            pattern = "|".join(kw_patterns)
            regex = re.compile(pattern, re.IGNORECASE)
            escaped = regex.sub(
                lambda m: f'<span style="{HIGHLIGHT_STYLE}">{m.group(0)}</span>',
                escaped,
            )
    # 保留换行
    escaped = escaped.replace("\n", "<br>")
    return f"<pre style='{PREVIEW_STYLE}'>{escaped}</pre>"


def build_keyword_to_rule_map(hits: Sequence[RuleHit]) -> dict[str, int]:
    """构建关键词到规则索引的映射，同一关键词仅归属首条规则。

    优先遍历 ``RuleHit.match_texts``（含组合规则全部命中文本）；
    ``match_texts`` 为空时回退到 ``match_text``；两者均空时回退到从 ``detail``
    中提取单引号包裹的内容。同一关键词被多条规则命中时，仅归属到首条规则，
    避免同一位置被重复计数。
    ``target=="filename"`` 的规则跳过（文件名匹配不应在内容预览中搜索高亮，
    否则可能产生误导性的高亮位置）。
    """
    keyword_to_rule: dict[str, int] = {}
    for rule_idx, hit in enumerate(hits):
        if hit.target == "filename":
            continue
        texts = hit.match_texts if hit.match_texts else ((hit.match_text,) if hit.match_text else ())
        if not texts:
            for match in KEYWORD_RE.finditer(hit.detail):
                kw = match.group(1)
                if kw:
                    texts = (kw,)
                    break
        for kw in texts:
            if kw and kw not in keyword_to_rule:
                keyword_to_rule[kw] = rule_idx
    return keyword_to_rule


def compile_keyword_pattern(kw: str) -> str:
    """将关键词编译为正则模式字符串。

    关键词中的换行符（\\r\\n/\\r/\\n）规范化为 ``\\s+`` 以支持跨行匹配；
    其他字符按字面量转义。
    """
    if re.search(r"[\r\n]", kw):
        parts = [p for p in re.split(r"[\r\n]+", kw) if p]
        return r"\s+".join(re.escape(p) for p in parts)
    return re.escape(kw)
