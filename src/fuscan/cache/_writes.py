"""缓存写入操作：从 :class:`CacheStore` 抽离的写入 SQL 子流程。

包含规则登记、单条/批量结果写入、文件元数据登记、提取内容缓存写入。
所有函数接收 :class:`CacheStore` 实例作为首参，通过其主写连接 +
``RLock`` 串行化保证线程安全。

模块化拆分原因：原 ``cache/store.py`` 单文件 886 行，写入逻辑混入查询
与清理职责中难以独立优化批量写入策略。本模块专责写入路径，便于针对性
优化事务粒度与 LRU 失效策略。

公共 API：

- :func:`register_ruleset`：登记规则集到缓存
- :func:`put_result`：写入单条缓存结果
- :func:`register_file` / :func:`register_path`：登记文件元数据
- :func:`batch_put_results`：批量写入结果与元数据（单次事务）
- :func:`put_extracted_content`：写入提取器结果缓存
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fuscan.cache._helpers import BatchWriteItem, now_iso
from fuscan.cache.hashes import compute_rule_hash, hash_bytes, serialize_rule
from fuscan.rules.model import Rule, RuleSet

if TYPE_CHECKING:
    from fuscan.cache.store import CacheStore
    from fuscan.scanner.result import RuleHit

__all__ = [
    "batch_put_results",
    "put_extracted_content",
    "put_result",
    "register_file",
    "register_path",
    "register_ruleset",
]

logger = logging.getLogger(__name__)


def register_ruleset(
    store: CacheStore,
    ruleset: RuleSet,
    source_files: Mapping[Path, str] | None = None,
) -> dict[str, str]:
    """登记规则集到缓存：算规则哈希，写入 ``rules``/``rule_files``/``rule_file_members``。

    相同规则的哈希跨文件去重，``rule_file_members`` 维护多对多关系。
    旧的 ``rule_file_members`` 关系在重新登记时被该文件的当前规则集替换。

    :param store: 所属 CacheStore 实例
    :param ruleset: 规则集
    :param source_files: 规则文件路径 → 文件 SHA-256 映射；
        为空时按"匿名来源"登记（``__inline__`` 虚拟文件）
    :return: ``rule_name -> rule_hash`` 映射，供 Scanner 复用
    """
    with store._lock:
        now = now_iso()
        sources: dict[Path, str] = dict(source_files) if source_files else {}
        # 默认虚拟来源，避免无 source_files 时规则无处归属
        if not sources:
            sources = {Path("__inline__"): hash_bytes(b"")}

        # 收集 (rule_name -> rule_hash)，重名规则以最后一条为准
        rule_hashes: dict[str, str] = {}
        for rule in ruleset.rules:
            rhash = compute_rule_hash(rule)
            rule_hashes[rule.name] = rhash
            _upsert_rule(store, rule, rhash)

        # 登记规则文件与成员关系
        for file_path, file_hash in sources.items():
            path_str = str(file_path)
            try:
                mtime = file_path.stat().st_mtime if file_path.exists() else 0.0
            except OSError:
                mtime = 0.0
            store._conn.execute(
                "INSERT INTO rule_files (file_path, file_hash, mtime, loaded_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(file_path) DO UPDATE SET "
                "  file_hash = excluded.file_hash, "
                "  mtime = excluded.mtime, "
                "  loaded_at = excluded.loaded_at",
                (path_str, file_hash, mtime, now),
            )
            # 替换该文件下的成员关系（先删后插）
            store._conn.execute(
                "DELETE FROM rule_file_members WHERE file_path = ?",
                (path_str,),
            )
            for rule in ruleset.rules:
                store._conn.execute(
                    "INSERT OR IGNORE INTO rule_file_members (file_path, rule_hash) VALUES (?, ?)",
                    (path_str, rule_hashes[rule.name]),
                )
        return rule_hashes


def _upsert_rule(store: CacheStore, rule: Rule, rule_hash: str) -> None:
    """写入或更新单条规则（按 rule_hash 去重）。

    :param store: 所属 CacheStore 实例
    :param rule: 规则对象
    :param rule_hash: 规则哈希
    """
    serialized = serialize_rule(rule)
    store._conn.execute(
        "INSERT INTO rules (rule_hash, rule_name, severity, description, serialized) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(rule_hash) DO UPDATE SET "
        "  rule_name = excluded.rule_name, "
        "  severity = excluded.severity, "
        "  description = excluded.description, "
        "  serialized = excluded.serialized",
        (rule_hash, rule.name, rule.severity.value, rule.description, serialized),
    )


def put_result(
    store: CacheStore,
    file_hash: str,
    rule_hash: str,
    hit: RuleHit | None,
) -> None:
    """写入单条缓存结果。

    仅写入 ``scan_results``；文件元数据（``scanned_files``/``file_paths``）请由调用方
    通过 :func:`register_file` 与 :func:`register_path` 单独登记，避免单次调用承担过多职责。

    写入后失效对应 ``file_hash`` 的进程内 LRU 条目，下次查询走 SQLite 取最新数据。

    :param store: 所属 CacheStore 实例
    :param file_hash: 文件内容哈希
    :param rule_hash: 规则哈希
    :param hit: ``RuleHit`` 表示命中；``None`` 表示该规则对该文件未命中（也缓存，避免重复扫描）
    """
    now = now_iso()
    with store._lock:
        # 确保 scanned_files 中存在该 file_hash，避免外键约束失败；
        # size 未知时用 0 占位，调用方可通过 register_file() 更新真实 size。
        store._conn.execute(
            "INSERT OR IGNORE INTO scanned_files (file_hash, size, first_seen_at, last_scanned_at) VALUES (?, 0, ?, ?)",
            (file_hash, now, now),
        )
        if hit is None:
            store._conn.execute(
                "INSERT INTO scan_results "
                "(file_hash, rule_hash, matched, severity, detail, match_text, "
                " match_texts, match_description, match_count, target, cached_at) "
                "VALUES (?, ?, 0, NULL, NULL, NULL, NULL, '', 0, '', ?) "
                "ON CONFLICT(file_hash, rule_hash) DO UPDATE SET "
                "  matched = 0, severity = NULL, detail = NULL, match_text = NULL, "
                "  match_texts = NULL, match_description = '', "
                "  match_count = 0, target = '', cached_at = excluded.cached_at",
                (file_hash, rule_hash, now),
            )
        else:
            # match_texts 以 JSON 数组形式序列化，便于跨行解析且保持顺序
            texts_json = json.dumps(list(hit.match_texts), ensure_ascii=False) if hit.match_texts else None
            store._conn.execute(
                "INSERT INTO scan_results "
                "(file_hash, rule_hash, matched, severity, detail, match_text, "
                " match_texts, match_description, match_count, target, cached_at) "
                "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(file_hash, rule_hash) DO UPDATE SET "
                "  matched = 1, severity = excluded.severity, detail = excluded.detail, "
                "  match_text = excluded.match_text, match_texts = excluded.match_texts, "
                "  match_description = excluded.match_description, "
                "  match_count = excluded.match_count, target = excluded.target, "
                "  cached_at = excluded.cached_at",
                (
                    file_hash,
                    rule_hash,
                    hit.severity.value,
                    hit.detail,
                    hit.match_text,
                    texts_json,
                    hit.match_description,
                    hit.match_count,
                    hit.target,
                    now,
                ),
            )
        # 失效 LRU 条目：调用方下次查询时会从 SQLite 取最新数据并回填 LRU
        with store._lru_lock:
            store._hit_cache_invalidate(file_hash)


def _register_file_locked(store: CacheStore, file_hash: str, size: int, now: str) -> None:
    """登记文件哈希到 ``scanned_files``（已持 ``_lock``）。

    ``put_result`` 会用 ``size=0`` 占位插入以满足外键约束；
    本方法用真实 size 覆盖占位值（仅在 size > 0 时更新，避免覆盖已登记的真实值）。

    :param store: 所属 CacheStore 实例
    :param file_hash: 文件内容哈希
    :param size: 文件大小（字节）
    :param now: 当前 ISO 时间字符串
    """
    store._conn.execute(
        "INSERT INTO scanned_files (file_hash, size, first_seen_at, last_scanned_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(file_hash) DO UPDATE SET "
        "  size = CASE WHEN excluded.size > 0 THEN excluded.size ELSE scanned_files.size END, "
        "  last_scanned_at = excluded.last_scanned_at",
        (file_hash, size, now, now),
    )


def _register_path_locked(store: CacheStore, file_hash: str, path: Path, mtime: float, now: str) -> None:
    """登记文件路径到 ``file_paths``（已持 ``_lock``）。

    :param store: 所属 CacheStore 实例
    :param file_hash: 文件内容哈希
    :param path: 文件路径
    :param mtime: 文件 mtime（秒）
    :param now: 当前 ISO 时间字符串
    """
    store._conn.execute(
        "INSERT INTO file_paths (file_hash, path, mtime, last_seen_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(file_hash, path) DO UPDATE SET "
        "  mtime = excluded.mtime, last_seen_at = excluded.last_seen_at",
        (file_hash, str(path), mtime, now),
    )


def register_file(store: CacheStore, file_hash: str, size: int) -> None:
    """登记/更新 ``scanned_files`` 的 ``last_scanned_at``。

    写入后失效对应 ``file_hash`` 的进程内 LRU 条目。

    :param store: 所属 CacheStore 实例
    :param file_hash: 文件内容哈希
    :param size: 文件大小（字节）
    """
    with store._lock:
        now = now_iso()
        _register_file_locked(store, file_hash, size, now)
        with store._lru_lock:
            store._hit_cache_invalidate(file_hash)


def register_path(store: CacheStore, file_hash: str, path: Path, mtime: float) -> None:
    """登记/更新 ``file_paths``。

    写入后失效对应 ``file_hash`` 的进程内 LRU 条目。

    :param store: 所属 CacheStore 实例
    :param file_hash: 文件内容哈希
    :param path: 文件路径
    :param mtime: 文件 mtime（秒）
    """
    with store._lock:
        now = now_iso()
        _register_path_locked(store, file_hash, path, mtime, now)
        with store._lru_lock:
            store._hit_cache_invalidate(file_hash)


def batch_put_results(store: CacheStore, items: list[BatchWriteItem]) -> None:
    """批量写入扫描结果与文件元数据，单次事务提交。

    适用于扫描器累积一批后 flush 的场景。相比逐条
    :func:`put_result` + :func:`register_file` + :func:`register_path`，
    显著减少 commit/fsync 次数，提升冷缓存场景吞吐。

    - ``items[i].hits`` 为非空时，等价于对该 ``file_hash`` 调用多次
      :func:`put_result`（含命中与未命中两种 RuleHit）
    - ``items[i].hits`` 为空元组时，仅刷新 ``scanned_files`` 与 ``file_paths``
      元数据（预筛命中场景，无新结果需写入）
    - ``scanned_files`` 用 :func:`_register_file_locked` 同款 UPSERT 语义
      （``size > 0`` 才覆盖占位值）
    - 异常时整批 ROLLBACK，已写入数据不受影响

    COMMIT 成功后统一失效涉及到的 ``file_hash`` 的进程内 LRU 条目。

    :param store: 所属 CacheStore 实例
    :param items: 批量写入项列表；空列表直接返回
    """
    if not items:
        return
    now = now_iso()
    with store._lock:
        try:
            store._conn.execute("BEGIN")
            # 1. scanned_files（executemany 比 循环 execute 快）
            store._conn.executemany(
                "INSERT INTO scanned_files (file_hash, size, first_seen_at, last_scanned_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(file_hash) DO UPDATE SET "
                "  size = CASE WHEN excluded.size > 0 THEN excluded.size ELSE scanned_files.size END, "
                "  last_scanned_at = excluded.last_scanned_at",
                [(item.file_hash, item.size, now, now) for item in items],
            )
            # 2. file_paths
            store._conn.executemany(
                "INSERT INTO file_paths (file_hash, path, mtime, last_seen_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(file_hash, path) DO UPDATE SET "
                "  mtime = excluded.mtime, last_seen_at = excluded.last_seen_at",
                [(item.file_hash, str(item.path), item.mtime, now) for item in items],
            )
            # 3. scan_results（扁平化为行列表后一次性 executemany）
            result_rows: list[tuple[Any, ...]] = []
            for item in items:
                for rule_hash, hit in item.hits:
                    if hit is None:
                        result_rows.append((item.file_hash, rule_hash, 0, None, None, None, None, "", 0, "", now))
                    else:
                        texts_json = json.dumps(list(hit.match_texts), ensure_ascii=False) if hit.match_texts else None
                        result_rows.append(
                            (
                                item.file_hash,
                                rule_hash,
                                1,
                                hit.severity.value,
                                hit.detail,
                                hit.match_text,
                                texts_json,
                                hit.match_description,
                                hit.match_count,
                                hit.target,
                                now,
                            )
                        )
            if result_rows:
                store._conn.executemany(
                    "INSERT INTO scan_results "
                    "(file_hash, rule_hash, matched, severity, detail, match_text, "
                    " match_texts, match_description, match_count, target, cached_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(file_hash, rule_hash) DO UPDATE SET "
                    "  matched = excluded.matched, severity = excluded.severity, "
                    "  detail = excluded.detail, match_text = excluded.match_text, "
                    "  match_texts = excluded.match_texts, "
                    "  match_description = excluded.match_description, "
                    "  match_count = excluded.match_count, target = excluded.target, "
                    "  cached_at = excluded.cached_at",
                    result_rows,
                )
            store._conn.execute("COMMIT")
        except Exception:
            try:
                store._conn.execute("ROLLBACK")
            except sqlite3.Error:
                # ROLLBACK 失败不应掩盖原始异常，仅记录警告
                logger.warning("ROLLBACK 失败", exc_info=True)
            raise
        # 仅 COMMIT 成功后更新内存缓存
        with store._lru_lock:
            for item in items:
                # 主动填充 _hit_cache（iter-73）：从 item.hits 构造 result dict，
                # 使下次 get_cached_hits 命中内存跳过 SQLite。item.hits 完整时
                # （如冷缓存首次扫描所有规则）LRU 命中；不完整时（混合路径部分
                # 规则已缓存）_hit_cache_get 检测 rule_hashes 集合不匹配，走
                # SQLite 回填，安全降级
                if item.hits:
                    rule_hashes = [rh for rh, _ in item.hits]
                    result_dict: dict[str, RuleHit | None] = dict(item.hits)
                    store._hit_cache_put(item.file_hash, rule_hashes, result_dict)
                # item.hits 为空（预筛命中，仅刷新元数据）：scan_results 未变，
                # 保留 LRU 中已有条目，避免下次查询走 SQLite
                # 主动填充路径预筛 LRU：使下次 lookup_file_hash 命中内存
                store._path_cache_put(str(item.path), item.mtime, item.size, item.file_hash)


def put_extracted_content(store: CacheStore, file_hash: str, content: str, extension: str) -> None:
    """写入提取器结果缓存。

    仅缓存非空内容；空内容（如提取失败回退到空字符串）不缓存，避免哨兵值污染。
    ``scanned_files`` 中须已存在该 ``file_hash``（外键约束），
    调用方通常先 :func:`register_file` 再调本方法。

    写入后主动填充进程内 LRU（iter-118），使下次 ``get_extracted_content``
    命中内存跳过 SQLite 查询。

    :param store: 所属 CacheStore 实例
    :param file_hash: 文件内容哈希
    :param content: 提取后的纯文本内容
    :param extension: 文件扩展名（用于诊断与未来按格式清理）
    """
    if not content:
        return
    now = now_iso()
    with store._lock:
        # 确保 scanned_files 存在该 file_hash，避免外键约束失败
        store._conn.execute(
            "INSERT OR IGNORE INTO scanned_files (file_hash, size, first_seen_at, last_scanned_at) VALUES (?, 0, ?, ?)",
            (file_hash, now, now),
        )
        store._conn.execute(
            "INSERT INTO extracted_contents (file_hash, content, extension, cached_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(file_hash) DO UPDATE SET "
            "  content = excluded.content, extension = excluded.extension, "
            "  cached_at = excluded.cached_at",
            (file_hash, content, extension, now),
        )
        # 主动填充 LRU（iter-118）：COMMIT 成功后使下次查询命中内存
        with store._lru_lock:
            store._extract_cache_put(file_hash, content)
