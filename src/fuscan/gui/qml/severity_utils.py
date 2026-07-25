"""严重度文本/色值工具（共享给 ResultListModel / RuleListModel / ScanController）。

将 :class:`Severity` 枚举映射为中文文本与十六进制色值，集中定义避免
多个 model 重复实现。色值与 :mod:`fuscan.theme` 中的 :data:`COLOR_DANGER`
等令牌保持一致。
"""

from __future__ import annotations

from fuscan.rules.model import Severity

__all__ = ["severity_color_hex", "severity_text"]

_SEVERITY_TEXT: dict[Severity, str] = {
    Severity.INFO: "信息",
    Severity.WARNING: "警告",
    Severity.CRITICAL: "严重",
}

_SEVERITY_COLOR_HEX: dict[Severity, str] = {
    Severity.INFO: "#0366D6",  # 主色蓝
    Severity.WARNING: "#F0883E",  # 警告橙
    Severity.CRITICAL: "#D73A49",  # 危险红
}


def severity_text(severity: Severity) -> str:
    """返回严重度中文文本（信息/警告/严重）。"""
    return _SEVERITY_TEXT[severity]


def severity_color_hex(severity: Severity) -> str:
    """返回严重度对应的十六进制色值。"""
    return _SEVERITY_COLOR_HEX[severity]
