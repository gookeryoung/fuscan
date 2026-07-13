"""规则与文件内容哈希计算。

规则哈希基于规则的稳定 JSON 序列化，跨 Python 版本与运行时不变；
文件内容哈希使用 SHA-256。

序列化原则：

- 字段按字典序排序（``sort_keys=True``）
- 枚举用 ``.value`` 字符串而非 ``str(enum)``
- 嵌套 ``MatchSpec`` 递归处理 ``LeafMatch``/``AndMatch``/``OrMatch``/``NotMatch``
- 编码 UTF-8，禁用 ASCII 转义（``ensure_ascii=False``）保留可读性

如此保证：规则逻辑等价 → 哈希相同；规则任一字段变化 → 哈希不同。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchSpec,
    NotMatch,
    OrMatch,
    Rule,
)

__all__ = [
    "compute_file_hash",
    "compute_rule_hash",
    "hash_bytes",
    "serialize_match",
    "serialize_rule",
]

logger = logging.getLogger(__name__)

# SHA-256 十六进制摘要长度（用于审计/断言）
HASH_HEX_LEN: int = 64


def hash_bytes(data: bytes) -> str:
    """计算字节流的 SHA-256 十六进制摘要。

    :param data: 任意字节流（空字节返回固定摘要，便于哨兵场景）
    :return: 64 字符十六进制字符串
    """
    return hashlib.sha256(data).hexdigest()


def compute_file_hash(path: Path) -> str:
    """计算文件内容的 SHA-256 哈希。

    读取失败时抛 ``OSError``，由调用方决定是否跳过。
    大文件一次性读入内存，与 :func:`default_extract_content_with_hash` 的 100MB 上限对齐。

    :param path: 文件路径
    :return: 64 字符十六进制 SHA-256 摘要
    :raises OSError: 文件读取失败
    """
    return hash_bytes(path.read_bytes())


def serialize_match(match: MatchSpec) -> dict[str, Any]:
    """递归序列化匹配条件为稳定字典。

    :param match: ``LeafMatch``/``AndMatch``/``OrMatch``/``NotMatch`` 之一
    :return: 可 JSON 序列化的字典，含 ``type`` 字段区分类型
    :raises TypeError: 未知匹配类型（不应发生，仅防御）
    """
    if isinstance(match, LeafMatch):
        return {
            "type": "leaf",
            "target": match.target.value,
            "mode": match.mode.value,
            "pattern": match.pattern,
            "case_sensitive": match.case_sensitive,
        }
    if isinstance(match, AndMatch):
        return {"type": "and", "children": [serialize_match(c) for c in match.children]}
    if isinstance(match, OrMatch):
        return {"type": "or", "children": [serialize_match(c) for c in match.children]}
    if isinstance(match, NotMatch):
        return {"type": "not", "child": serialize_match(match.child)}
    raise TypeError(f"未知匹配类型: {type(match).__name__}")


def serialize_rule(rule: Rule) -> str:
    """序列化规则为稳定 JSON 字符串。

    字段按字典序排序，枚举用 ``.value``，嵌套匹配递归处理。
    同一规则的序列化结果跨运行时稳定，可作为哈希输入。

    :param rule: 规则对象
    :return: JSON 字符串
    """
    data: dict[str, Any] = {
        "name": rule.name,
        "match": serialize_match(rule.match),
        "description": rule.description,
        "severity": rule.severity.value,
        "file_extensions": sorted(rule.file_extensions),
    }
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def compute_rule_hash(rule: Rule) -> str:
    """计算单条规则的 SHA-256 哈希。

    基于规则的稳定 JSON 序列化，跨 Python 版本与运行时不变。
    规则逻辑等价 → 哈希相同；任一字段变化 → 哈希不同。

    :param rule: 规则对象
    :return: 64 字符十六进制 SHA-256 摘要
    """
    serialized = serialize_rule(rule)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
