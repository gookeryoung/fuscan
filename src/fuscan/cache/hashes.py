"""规则与文件内容哈希计算。

规则哈希基于规则的稳定 JSON 序列化，跨 Python 版本与运行时不变；
文件内容哈希按数据大小分流（``hash_bytes``）：

- 小文件（< :data:`_BIG_FILE_THRESHOLD`）用 SHA-256：CPython 内建实现，
  无 OpenSSL 上下文初始化开销，4-8KB 文件吞吐量高于 BLAKE2b
- 大文件用 BLAKE2b（``digest_size=32``）：64 位平台 OpenSSL 后端加速，
  且释放 GIL，多线程大文件场景占优
- 两者均输出 64 字符 hex，``scanned_files.file_hash`` 列 schema 无需变更

序列化原则：

- 字段按字典序排序（``sort_keys=True``）
- 枚举用 ``.value`` 字符串而非 ``str(enum)``
- 嵌套 ``MatchSpec`` 递归处理 ``LeafMatch``/``AndMatch``/``OrMatch``/``NotMatch``
- 编码 UTF-8，禁用 ASCII 转义（``ensure_ascii=False``）保留可读性

如此保证：规则逻辑等价 → 哈希相同；规则任一字段变化 → 哈希不同。

哈希算法选型历史
----------------

- iter-37 及之前：统一 SHA-256
- iter-38：统一 BLAKE2b ``digest_size=32``，实测 64 位平台对小文件
  （5KB 量级）的吞吐量约为 SHA-256 的 1.5-2 倍；但在 CPython +
  OpenSSL 后端下，BLAKE2b 对小文件存在上下文初始化开销，实测 4-6KB
  样本反而比 SHA-256 慢 ~20%，导致单线程无缓存场景性能回退
- iter-39：按数据大小分流，小文件用 SHA-256，大文件用 BLAKE2b；
  兼容版本号递增到 v3，触发旧缓存自动失效
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

# BLAKE2b 摘要字节数（输出 64 字符 hex，与 SHA-256 长度一致）
_DIGEST_SIZE: int = 32

# SHA-256 十六进制摘要长度（用于审计/断言；BLAKE2b digest_size=32 输出长度相同）
HASH_HEX_LEN: int = 64

# 大小文件分流阈值（字节）：小于此值用 SHA-256（CPython 内建无初始化开销），
# 大于等于此值用 BLAKE2b（OpenSSL 加速且释放 GIL）。
# 实测 4-6KB 样本 SHA-256 比 BLAKE2b 快约 20%，8KB+ BLAKE2b 反超
_BIG_FILE_THRESHOLD: int = 8 * 1024


def hash_bytes(data: bytes) -> str:
    """计算字节流的十六进制摘要（64 字符）。

    按数据大小分流算法：

    - ``len(data) < :data:`_BIG_FILE_THRESHOLD``：SHA-256（CPython 内建实现）
    - 否则：BLAKE2b ``digest_size=32``（OpenSSL 加速 + 释放 GIL）

    两者输出均为 64 字符 hex。空字节返回固定摘要，便于哨兵场景。

    .. note::

        算法切换需递增 :data:`fuscan.cache.schema.CACHE_COMPAT_VERSION`，
        以触发旧缓存自动失效。

    :param data: 任意字节流
    :return: 64 字符十六进制字符串
    """
    if len(data) < _BIG_FILE_THRESHOLD:
        return hashlib.sha256(data).hexdigest()
    return hashlib.blake2b(data, digest_size=_DIGEST_SIZE).hexdigest()


def compute_file_hash(path: Path) -> str:
    """计算文件内容的哈希（按大小分流算法）。

    读取失败时抛 ``OSError``，由调用方决定是否跳过。
    大文件一次性读入内存，与 :func:`default_extract_content_with_hash` 的 100MB 上限对齐。

    :param path: 文件路径
    :return: 64 字符十六进制摘要（算法由 :func:`hash_bytes` 按大小决定）
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
            "description": match.description,
        }
    if isinstance(match, AndMatch):
        return {
            "type": "and",
            "children": [serialize_match(c) for c in match.children],
            "description": match.description,
        }
    if isinstance(match, OrMatch):
        return {
            "type": "or",
            "children": [serialize_match(c) for c in match.children],
            "description": match.description,
        }
    if isinstance(match, NotMatch):
        return {
            "type": "not",
            "child": serialize_match(match.child),
            "description": match.description,
        }
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
    }
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def compute_rule_hash(rule: Rule) -> str:
    """计算单条规则的 SHA-256 哈希。

    规则序列化后字节数通常远小于 :data:`_BIG_FILE_THRESHOLD`，
    始终走 SHA-256 路径（CPython 内建实现，无 OpenSSL 初始化开销）。

    基于规则的稳定 JSON 序列化，跨 Python 版本与运行时不变。
    规则逻辑等价 → 哈希相同；任一字段变化 → 哈希不同。

    :param rule: 规则对象
    :return: 64 字符十六进制 SHA-256 摘要
    """
    serialized = serialize_rule(rule)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
