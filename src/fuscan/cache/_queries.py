"""缓存查询操作：从 :class:`CacheStore` 抽离的只读 SQL 子流程。

包含规则命中查询、路径预筛查询、提取内容查询。所有函数接收
:class:`CacheStore` 实例作为首参，通过其线程本地只读连接并行执行。

模块化拆分原因：原 ``cache/store.py`` 单文件 886 行，查询/写入/清理
职责混杂。本模块专责只读路径，便于独立优化查询逻辑与索引策略。

公共 API：

- :func:`get_cached_hits`：批量查询规则命中缓存
- :func:`lookup_file_hash`：按 (path, mtime, size) 预筛文件哈希
- :func:`get_extracted_content`：查询提取器结果缓存
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Collection

from fuscan.rules.model import Severity

if TYPE_CHECKING:
    from fuscan.cache.store import CacheStore
    from fuscan.scanner.result import RuleHit

__all__ = ["get_cached_hits", "get_extracted_content", "get_rule_hashes", "lookup_file_hash"]

logger = logging.getLogger(__name__)


def get_rule_hashes(store: CacheStore) -> dict[str, str]:
    """查询当前已登记的 ``rule_name -> rule_hash`` 映射。

    重名规则以最后登记的为准（与 :func:`register_ruleset` 行为一致）。

    :param store: 所属 CacheStore 实例
    :return: 规则名到规则哈希的映射字典
    """
    rows = store._get_read_conn().execute("SELECT rule_name, rule_hash FROM rules").fetchall()
    return {row["rule_name"]: row["rule_hash"] for row in rows}


def get_cached_hits(
    store: CacheStore,
    file_hash: str,
    rule_hashes: Collection[str],
) -> dict[str, RuleHit | None]:
    """批量查询缓存结果。

    优先命中进程内 LRU 缓存；未命中走 SQLite 查询，结果写回 LRU。

    线程安全：LRU 访问经 ``_lru_lock`` 保护，SQLite 查询使用线程本地
    只读连接并行执行（iter-68）。

    :param store: 所属 CacheStore 实例
    :param file_hash: 被扫描文件内容哈希
    :param rule_hashes: 待查询的规则哈希集合
    :return: ``rule_hash -> RuleHit | None`` 映射；
        值为 ``RuleHit`` 表示该规则命中且已缓存；
        值为 ``None`` 表示该规则未命中且已缓存（避免重复扫描未命中）；
        不在返回字典中的 ``rule_hash`` 表示未缓存，需扫描。
    """
    if not rule_hashes:
        return {}
    # 先查进程内 LRU（细粒度锁，不阻塞 DB 读）
    with store._lru_lock:
        cached = store._hit_cache_get(file_hash, rule_hashes)
    if cached is not None:
        return cached

    placeholders = ",".join("?" for _ in rule_hashes)
    params: tuple[Any, ...] = (file_hash, *rule_hashes)
    rows = (
        store._get_read_conn()
        .execute(
            f"SELECT rule_hash, matched, severity, detail, match_text, "
            f"       match_texts, match_description, match_count, target "
            f"FROM scan_results WHERE file_hash = ? AND rule_hash IN ({placeholders})",
            params,
        )
        .fetchall()
    )
    # 延迟导入打破循环：cache.store → scanner.result → scanner.__init__ → scanner.scanner → cache.store
    from fuscan.scanner.result import RuleHit

    result: dict[str, RuleHit | None] = {}
    for row in rows:
        if row["matched"]:
            severity = Severity(row["severity"]) if row["severity"] else Severity.INFO
            # match_texts 以 JSON 数组形式存储；NULL 或空数组视为空元组
            raw_texts = row["match_texts"]
            if raw_texts:
                try:
                    texts_list = json.loads(raw_texts)
                    match_texts = tuple(str(t) for t in texts_list) if isinstance(texts_list, list) else ()
                except (TypeError, ValueError):
                    logger.warning("match_texts 反序列化失败，回退到空元组: %r", raw_texts)
                    match_texts = ()
            else:
                match_texts = ()
            result[row["rule_hash"]] = RuleHit(
                rule_name="",  # 调用方按 rule_hash 反查 name，避免冗余存储
                severity=severity,
                detail=row["detail"] or "",
                match_text=row["match_text"] or "",
                match_count=row["match_count"],
                target=row["target"] or "",
                match_texts=match_texts,
                match_description=row["match_description"] or "",
            )
        else:
            result[row["rule_hash"]] = None
    # 写回 LRU（细粒度锁）
    with store._lru_lock:
        store._hit_cache_put(file_hash, rule_hashes, result)
    return result


def lookup_file_hash(
    store: CacheStore,
    path: Path,
    mtime: float,
    size: int,
) -> str | None:
    """按 ``(path, mtime, size)`` 查询已登记的 ``file_hash``。

    用于缓存模式预筛：文件 mtime 与 size 未变时，
    可直接复用已登记的 ``file_hash``，跳过 ``read_bytes`` 与哈希计算。

    线程安全：使用线程本地只读连接，无锁并行（iter-68）。
    查询优化：JOIN 替代 IN 子查询 + 复合索引（iter-70），消除全表扫描。
    内存缓存（iter-73）：先查进程内 LRU，命中跳过 SQLite 查询；
    ``register_path`` / ``batch_put_results`` 写入后主动填充 LRU，
    使热缓存二次扫描完全命中内存。

    安全性说明：mtime 可被人为修改，本方法仅作为性能优化；
    对安全性敏感场景，调用方可关闭此预筛（始终走哈希校验）。

    :param store: 所属 CacheStore 实例
    :param path: 文件路径
    :param mtime: 当前文件 mtime（秒，浮点）
    :param size: 当前文件大小（字节）
    :return: 命中时返回 ``file_hash``（64 字符 hex）；未命中返回 None
    """
    path_str = str(path)
    # 先查进程内 LRU（iter-73）：热缓存二次扫描时 100% 命中，跳过 SQLite
    with store._lru_lock:
        cached = store._path_cache_get(path_str, mtime, size)
    if cached is not None:
        return cached
    # JOIN 形式：先用 idx_paths_path_mtime 按 (path, mtime) 定位，
    # 再用 scanned_files 主键 (file_hash) JOIN 验证 size，全程索引扫描
    row = (
        store._get_read_conn()
        .execute(
            "SELECT fp.file_hash FROM file_paths fp "
            "JOIN scanned_files sf ON fp.file_hash = sf.file_hash "
            "WHERE fp.path = ? AND fp.mtime = ? AND sf.size = ?",
            (path_str, mtime, size),
        )
        .fetchone()
    )
    if row is None:
        # 未登记路径不缓存（None 不写入 LRU），避免污染缓存
        return None
    file_hash = row["file_hash"]
    # 命中 SQLite 后回填 LRU，下次同一 (path, mtime, size) 直接命中内存
    with store._lru_lock:
        store._path_cache_put(path_str, mtime, size, file_hash)
    return file_hash


def get_extracted_content(store: CacheStore, file_hash: str) -> str | None:
    """查询提取器结果缓存。

    用于缓存模式：同内容不同路径（如 node_modules 重复依赖）的文件，
    通过 ``file_hash`` 复用提取结果，跳过 ``extract_content_from_bytes``。

    线程安全：使用线程本地只读连接，无锁并行（iter-68）。
    内存缓存（iter-118）：先查进程内 LRU，命中跳过 SQLite 查询；
    ``put_extracted_content`` 写入后主动填充 LRU，使重复内容查询
    完全命中内存。

    :param store: 所属 CacheStore 实例
    :param file_hash: 文件内容哈希
    :return: 命中时返回提取后的纯文本；未命中返回 None
    """
    # 先查进程内 LRU（iter-118）：node_modules 重复依赖场景下，
    # 同一 file_hash 的内容会被查询多次，内存 LRU 跳过 SQLite 查询
    with store._lru_lock:
        cached = store._extract_cache_get(file_hash)
    if cached is not None:
        return cached
    row = (
        store._get_read_conn()
        .execute(
            "SELECT content FROM extracted_contents WHERE file_hash = ?",
            (file_hash,),
        )
        .fetchone()
    )
    if row is None:
        # 未命中 SQLite 不缓存（None 不写入 LRU），避免污染缓存
        return None
    content = row["content"]
    # 命中 SQLite 后回填 LRU，下次同一 file_hash 直接命中内存
    with store._lru_lock:
        store._extract_cache_put(file_hash, content)
    return content
