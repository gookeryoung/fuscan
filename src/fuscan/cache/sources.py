"""规则来源文件哈希计算工具。

供 CLI 和 GUI 在构造 ``Scanner`` 时计算 ``source_files`` 参数，
将规则文件路径映射到文件 SHA-256，用于 :meth:`CacheStore.register_ruleset`。
"""

from __future__ import annotations

from pathlib import Path

from fuscan.cache.hashes import hash_bytes

__all__ = ["compute_source_files"]


def compute_source_files(rules_paths: list[Path], use_builtin: bool) -> dict[Path, str]:
    """计算规则文件 SHA-256 映射，供 CacheStore.register_ruleset 使用。

    :param rules_paths: 用户规则文件路径列表
    :param use_builtin: 是否包含内置规则文件
    :return: ``规则文件路径 -> 文件 SHA-256`` 映射；内置规则文件存在时也包含在内
    """
    sources: dict[Path, str] = {}
    if use_builtin:
        from fuscan.builtin import BUILTIN_RULES_PATH

        if BUILTIN_RULES_PATH.exists():
            sources[BUILTIN_RULES_PATH] = hash_bytes(BUILTIN_RULES_PATH.read_bytes())
    for p in rules_paths:
        if p.exists():
            sources[p] = hash_bytes(p.read_bytes())
    return sources
