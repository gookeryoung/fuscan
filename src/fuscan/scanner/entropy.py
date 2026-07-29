"""高熵字符串检测：识别疑似密钥/令牌的随机字符串。

熵检测作为正则规则的补充，对未在规则集中显式定义的密钥格式（如自定义生成的
Base64/Hex 串）进行兜底识别。基于 Shannon 信息熵度量字符串随机性：

- Base64 随机串熵接近 ``log2(64) = 6.0``
- Hex 随机串熵接近 ``log2(16) = 4.0``（混合大小写可达 ~4.46）
- 自然语言文本熵通常 < 4.0（受字符频率分布不均影响）

默认阈值 ``4.5`` 可捕获 Base64 与混合大小写 Hex，对自然语言文本误报率低；
用户可在设置页调节阈值（范围 3.0~5.0）以适应不同场景。

公共 API：

- :func:`shannon_entropy`：计算字符串的 Shannon 熵
- :func:`is_high_entropy`：判断单字符串是否为高熵
- :func:`find_high_entropy_strings`：从文本中提取所有高熵子串
"""

from __future__ import annotations

import math
import re
from collections import Counter

__all__ = [
    "DEFAULT_ENTROPY_THRESHOLD",
    "DEFAULT_MIN_ENTROPY_LENGTH",
    "ENTROPY_RULE_NAME",
    "find_high_entropy_strings",
    "is_high_entropy",
    "shannon_entropy",
]

# 默认熵阈值：捕获 Base64（~6.0）与混合大小写 Hex（~4.46），过滤自然语言（<4.0）
DEFAULT_ENTROPY_THRESHOLD: float = 4.5
# 默认最短候选长度：32 字符（Base64 编码 24 字节约 32 字符，覆盖常见密钥长度）
DEFAULT_MIN_ENTROPY_LENGTH: int = 32
# 熵检测命中的规则名（独立命名空间，避免与 builtin.yaml 中 P0xxx 冲突）
ENTROPY_RULE_NAME: str = "E001-高熵字符串"

# 候选 token 提取正则：Base64/Base64URL 字符集（字母数字 + / + - + _）
# 注意：不含 ``=``（Base64 padding），因为 ``=`` 也是赋值分隔符，包含会导致
# ``key1=c2VjcmV0...`` 被误合并为单个 token。padding 的缺失不影响熵计算。
# 用 finditer 避免一次性 list 占内存
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9+/_-]+")


def shannon_entropy(data: str) -> float:
    """计算字符串的 Shannon 信息熵（比特/字符）。

    :param data: 待计算的字符串
    :return: 熵值 ``H = -sum(p_i * log2(p_i))``，空字符串返回 0.0
    """
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    entropy = 0.0
    for count in counts.values():
        probability = count / length
        entropy -= probability * math.log2(probability)
    return entropy


def is_high_entropy(
    data: str,
    threshold: float = DEFAULT_ENTROPY_THRESHOLD,
    min_length: int = DEFAULT_MIN_ENTROPY_LENGTH,
) -> bool:
    """判断字符串是否为高熵（疑似密钥/令牌）。

    :param data: 待判断的字符串
    :param threshold: 熵阈值，>= 此值视为高熵（默认 4.5）
    :param min_length: 最短候选长度，短于此值的串视为无意义（默认 32）
    :return: 字符串长度 >= ``min_length`` 且熵 >= ``threshold`` 时返回 True
    """
    if len(data) < min_length:
        return False
    return shannon_entropy(data) >= threshold


def find_high_entropy_strings(
    text: str,
    threshold: float = DEFAULT_ENTROPY_THRESHOLD,
    min_length: int = DEFAULT_MIN_ENTROPY_LENGTH,
) -> list[tuple[str, float]]:
    """从文本中提取所有高熵子串。

    用正则 ``[A-Za-z0-9+/=_-]+`` 提取候选 token（覆盖 Base64/Base64URL 字符集），
    对每个长度 >= ``min_length`` 的 token 计算熵，保留熵 >= ``threshold`` 的项。
    结果按出现顺序排列，每个 token 至多出现一次（同一 token 重复出现仅记录首次）。

    :param text: 待分析的文本
    :param threshold: 熵阈值（默认 4.5）
    :param min_length: 最短候选长度（默认 32）
    :return: ``(子串, 熵值)`` 列表，按出现顺序
    """
    if not text or min_length <= 0:
        return []
    seen: set[str] = set()
    results: list[tuple[str, float]] = []
    for match in _TOKEN_PATTERN.finditer(text):
        token = match.group(0)
        if len(token) < min_length or token in seen:
            continue
        entropy = shannon_entropy(token)
        if entropy >= threshold:
            results.append((token, entropy))
            seen.add(token)
    return results
