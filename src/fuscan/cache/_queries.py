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

__all__ = [
    "get_cached_hits",
    "get_extracted_content",
    "get_rule_hashes",
    "lookup_file_hash",
    "lookup_file_hashes_batch",
]

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


def lookup_file_hashes_batch(
    store: CacheStore,
    keys: Collection[tuple[Path, float, int]],
) -> dict[tuple[Path, float, int], str | None]:
    """批量查询多个 ``(path, mtime, size)`` 对应的 ``file_hash``。

    iter-158：**路径预筛批量查询**。用单条 ``WITH targets(...) VALUES (...)``
    CTE + JOIN 一次性从 SQLite 取回所有 keys 的 file_hash，取代 N 次
    :func:`lookup_file_hash` 单次查询，减少 90%+ 的 SQL 解析/执行器开销。
    查询结果（包括 None 未命中）同步写回路径预筛 LRU，使后续 worker 中
    :func:`lookup_file_hash` 直接命中内存，跳过 SQLite。

    - ``keys <= 0``：直接返回空 dict
    - ``keys >= 1``：按 200 条一批拆分（SQLite ``SQLITE_MAX_VARIABLE_NUMBER``
      默认 999，每键 3 参数，200 条 = 600 参数安全留余量），多批拼接结果。
    - 每批先查进程内 LRU：命中的直接出结果不进 SQL；SQL 仅查询 miss 集
    - 返回值保证所有 ``keys`` 都在 dict 中（None 表示未命中）

    :param store: 所属 CacheStore 实例
    :param keys: 三元组 ``(路径, mtime, 大小)`` 的集合
    :return: 键为三元组，值为 file_hash（未命中为 None）
    """
    keys_list = list(keys)
    if not keys_list:
        return {}
    result: dict[tuple[Path, float, int], str | None] = {}
    # 先查进程内 LRU，命中的直接出结果（减少 SQL 参数个数）
    pending: list[tuple[Path, float, int]] = []
    with store._lru_lock:
        for key in keys_list:
            path, mtime, size = key
            cached = store._path_cache_get(str(path), mtime, size)
            if cached is not None:
                result[key] = cached
            else:
                pending.append(key)
    if not pending:
        return result
    # BATCH_SIZE: 每键 3 参数，默认 999，取 250 × 3 = 750 留余量
    BATCH_SIZE = 250
    n = len(pending)
    for batch_start in range(0, n, BATCH_SIZE):
        batch_end = min(n, batch_start + BATCH_SIZE)
        batch = pending[batch_start:batch_end]
        # 构造 VALUES CTE：每个键 3 个 ?
        values_clause = ", ".join(["(?, ?, ?)"] * len(batch))
        params: list[Any] = []
        for path, mtime, size in batch:
            params.append(str(path))
            params.append(mtime)
            params.append(size)
        sql = (
            "WITH targets(path, mtime, size) AS (VALUES " + values_clause + ") "
            "SELECT t.path AS q_path, t.mtime AS q_mtime, t.size AS q_size, "
            "fp.file_hash AS file_hash "
            "FROM targets t "
            "LEFT JOIN file_paths fp ON t.path = fp.path AND t.mtime = fp.mtime "
            "LEFT JOIN scanned_files sf ON fp.file_hash = sf.file_hash "
            "WHERE sf.size IS NULL OR sf.size = t.size"
        )
        # 说明：LEFT JOIN + WHERE sf.size IS NULL OR sf.size = t.size
        # 等价于：当 fp 有记录但 sf.size != t.size 时，返回 file_hash = NULL（
        # 表示 size 不匹配，视为未命中）。当 fp 无记录时，LEFT JOIN 返回
        # file_hash = NULL 同样视为未命中。当 fp 有记录且 size 匹配时返回
        # 正确 file_hash。
        rows = store._get_read_conn().execute(sql, params).fetchall()
        # rows: [(q_path, q_mtime, q_size, file_hash or None)]
        # 需要构造 (path,mtime,size) -> file_hash
        lookup: dict[tuple[str, float, int], str | None] = {}
        for row in rows:
            lookup[(row["q_path"], row["q_mtime"], row["q_size"])] = row["file_hash"]
        # 回填到 result + 写回 LRU
        for path, mtime, size in batch:
            key = (path, mtime, size)
            path_str = str(path)
            file_hash = lookup.get((path_str, mtime, size))
            result[key] = file_hash
            if file_hash is not None:
                # 命中 SQLite：写回 LRU（None 不写，避免污染缓存）
                with store._lru_lock:
                    store._path_cache_put(path_str, mtime, size, file_hash)
    # 补全：确保所有 keys_list 都在 result（None 未命中的也显式写入 None）
    for key in keys_list:
        if key not in result:
            result[key] = None
    return result


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
