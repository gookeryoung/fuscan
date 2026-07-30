"""SQLite 缓存 schema 定义与版本迁移。

schema 通过 ``PRAGMA user_version`` 标识 DDL 版本，未来变更走 ``migrate()`` 增量升级。
所有 DDL 幂等（``IF NOT EXISTS``），便于 ``CacheStore`` 构造时安全执行。

缓存兼容版本号 ``CACHE_COMPAT_VERSION``
-----------------------------------

与 DDL 版本分离：DDL 版本描述表结构，兼容版本号描述**缓存数据语义**。

数据语义包括：

- 文件内容哈希算法（BLAKE2b ``digest_size=32``、SHA-256 等）
- 规则哈希序列化格式
- ``RuleHit`` 字段语义
- ``scan_results`` 行的解析方式

兼容版本号仅在**重大变更**（哈希算法替换、序列化格式重写）时递增；
小修小补（新增字段、调整索引）只递增 DDL 版本，不触发清空。

升级路径：

- ``migrate()`` 检测 ``meta.cache_compat_version`` 低于当前值时，
  递归 ``DROP`` 全部业务表并重建（旧数据无法解析，宁可重新扫描也不冒险复用）
- ``meta`` 表本身保留，记录 ``cache_compat_version``、``migrated_at`` 便于审计
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

__all__ = ["CACHE_COMPAT_VERSION", "CURRENT_VERSION", "SCHEMA_SQL", "migrate"]

logger = logging.getLogger(__name__)

# schema DDL 版本号：每次 DDL 变更递增，对应一次 migrate 步骤
CURRENT_VERSION: int = 5

# 缓存数据兼容版本号：仅在哈希算法/序列化格式等数据语义变更时递增。
# v1：SHA-256 哈希
# v2：BLAKE2b digest_size=32 哈希（iter-38）
# v3：按数据大小分流算法（iter-39）——小文件 SHA-256、大文件 BLAKE2b，
#     修复 iter-38 在小文件场景的性能回退
# v4：scan_results 新增 match_texts/match_description 字段（iter-41）——
#     AND/OR 组合规则需记录全部命中文本，原 match_text 仅含首条命中，
#     语义不兼容，需清空旧缓存重新扫描以获取准确的多匹配文本
# v5：serialize_match 补充 MatchSpec.description 字段（iter-47）——
#     原序列化遗漏 description，修改 MatchSpec.description 不触发 rule_hash
#     变化，导致缓存的 match_description 过期。补字段后 rule_hash 语义变化，
#     递增版本号清空旧缓存
# 后续重大变更（如换 BLAKE3、修改 RuleHit 字段语义）才递增此值。
CACHE_COMPAT_VERSION: int = 5


SCHEMA_SQL: str = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_files (
    file_path  TEXT PRIMARY KEY,
    file_hash  TEXT NOT NULL,
    mtime      REAL NOT NULL,
    loaded_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    rule_hash   TEXT PRIMARY KEY,
    rule_name   TEXT NOT NULL,
    severity    TEXT,
    description TEXT,
    serialized  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_file_members (
    file_path TEXT NOT NULL,
    rule_hash TEXT NOT NULL,
    PRIMARY KEY (file_path, rule_hash),
    FOREIGN KEY (file_path) REFERENCES rule_files(file_path) ON DELETE CASCADE,
    FOREIGN KEY (rule_hash) REFERENCES rules(rule_hash) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scanned_files (
    file_hash       TEXT PRIMARY KEY,
    size            INTEGER NOT NULL,
    first_seen_at   TEXT NOT NULL,
    last_scanned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_paths (
    file_hash    TEXT NOT NULL,
    path         TEXT NOT NULL,
    mtime        REAL NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (file_hash, path),
    FOREIGN KEY (file_hash) REFERENCES scanned_files(file_hash) ON DELETE CASCADE
);

-- iter-70：path 单列索引保留向后兼容；新增 (path, mtime) 复合索引
-- 让 lookup_file_hash 的 WHERE path=? AND mtime=? 只用索引即可完成过滤
CREATE INDEX IF NOT EXISTS idx_paths_path ON file_paths(path);
CREATE INDEX IF NOT EXISTS idx_paths_path_mtime ON file_paths(path, mtime);

-- iter-70：scanned_files 按 size 过滤的索引，消除 lookup_file_hash 子查询全表扫描
CREATE INDEX IF NOT EXISTS idx_scanned_size ON scanned_files(size);

CREATE TABLE IF NOT EXISTS scan_results (
    file_hash        TEXT NOT NULL,
    rule_hash        TEXT NOT NULL,
    matched          INTEGER NOT NULL,
    severity         TEXT,
    detail           TEXT,
    match_text       TEXT,
    match_texts      TEXT,
    match_description TEXT,
    match_count      INTEGER NOT NULL DEFAULT 1,
    target           TEXT,
    cached_at        TEXT NOT NULL,
    PRIMARY KEY (file_hash, rule_hash),
    FOREIGN KEY (file_hash) REFERENCES scanned_files(file_hash) ON DELETE CASCADE,
    FOREIGN KEY (rule_hash) REFERENCES rules(rule_hash) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_results_file ON scan_results(file_hash);
CREATE INDEX IF NOT EXISTS idx_results_rule ON scan_results(rule_hash);

-- 提取器结果缓存（iter-39）：按 file_hash 缓存提取后的纯文本，
-- 同内容不同路径（如 node_modules 重复依赖）可跳过 extract_content_from_bytes。
-- 仅缓存高开销格式（docx/pptx/xlsx 等），纯文本不缓存。
-- 通过外键级联删除：scanned_files 删除时自动清理对应提取内容。
CREATE TABLE IF NOT EXISTS extracted_contents (
    file_hash   TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    extension   TEXT NOT NULL,
    cached_at   TEXT NOT NULL,
    FOREIGN KEY (file_hash) REFERENCES scanned_files(file_hash) ON DELETE CASCADE
);
"""


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串（含时区后缀 ``Z``）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """读取 ``meta`` 表的指定键值（已持锁）。表不存在时返回 None。"""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        # meta 表尚未创建（首次初始化前）
        return None
    return row[0] if row else None


def _write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """写入 ``meta`` 表的指定键值（upsert，已持锁）。"""
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _purge_cache_data(conn: sqlite3.Connection) -> None:
    """删除全部业务表数据（保留 ``meta`` 表），用于兼容版本号升级。

    递归 DROP 后由 ``SCHEMA_SQL`` 重建表结构。
    """
    # 顺序无关紧要，外键约束由 executescript 临时关闭
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS extracted_contents;
        DROP TABLE IF EXISTS scan_results;
        DROP TABLE IF EXISTS file_paths;
        DROP TABLE IF EXISTS scanned_files;
        DROP TABLE IF EXISTS rule_file_members;
        DROP TABLE IF EXISTS rules;
        DROP TABLE IF EXISTS rule_files;
        PRAGMA foreign_keys = ON;
        """
    )
    logger.warning("缓存兼容版本号变更，已清空全部业务表数据")


def migrate(conn: sqlite3.Connection) -> int:
    """执行 schema 迁移到 ``CURRENT_VERSION``。

    流程：

    1. 先创建 ``meta`` 表（若不存在），读取 ``cache_compat_version``
    2. 若兼容版本号低于当前值：清空全部业务表数据（旧数据无法解析）
    3. 执行 ``SCHEMA_SQL`` 建表（幂等）
    4. 写入最新 ``cache_compat_version`` 与 ``schema_version``
    5. ``PRAGMA user_version`` 标记 DDL 版本

    :param conn: SQLite 连接（``row_factory`` 已设置）
    :return: 迁移后的 schema 版本号
    """
    cur = conn.execute("PRAGMA user_version")
    current = cur.fetchone()[0]

    # 先确保 meta 表存在，用于读写兼容版本号
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    # 兼容版本号检查：旧缓存数据语义不兼容时清空
    stored_compat_str = _read_meta(conn, "cache_compat_version")
    if stored_compat_str is None:
        # 全新数据库：不需要清空，写入当前兼容版本号即可
        logger.debug("全新数据库，初始化 cache_compat_version=%d", CACHE_COMPAT_VERSION)
    else:
        try:
            stored_compat = int(stored_compat_str)
        except (TypeError, ValueError):
            # meta 损坏：视为不兼容，清空
            logger.warning("meta.cache_compat_version 损坏 (%r)，清空缓存", stored_compat_str)
            _purge_cache_data(conn)
        else:
            if stored_compat < CACHE_COMPAT_VERSION:
                logger.warning(
                    "缓存兼容版本号升级: %d → %d，清空旧数据",
                    stored_compat,
                    CACHE_COMPAT_VERSION,
                )
                _purge_cache_data(conn)
                current = 0  # 强制走 schema 重建路径
            elif stored_compat > CACHE_COMPAT_VERSION:
                # 高版本数据被低版本代码打开：保守清空，避免误解析
                logger.warning(
                    "缓存兼容版本号高于当前代码 (%d > %d)，清空未来版本数据",
                    stored_compat,
                    CACHE_COMPAT_VERSION,
                )
                _purge_cache_data(conn)
                current = 0

    if current >= CURRENT_VERSION:
        logger.debug("schema 已是最新版本: %d", current)
    else:
        # v0 → v1：初始化全部表
        # v1 → v2：新增 meta 表（iter-38），SCHEMA_SQL 中已含 IF NOT EXISTS，
        #          旧库通过本路径安全创建 meta 表
        # v2 → v3：兼容版本号升级触发 purge 后走此路径重建（iter-39）
        # v3 → v4：新增 extracted_contents 表（iter-39），IF NOT EXISTS 安全升级
        # v4 → v5：兼容版本号升级（iter-41，match_texts/match_description）触发 purge 后重建
        # v5 → v6：新增 idx_scanned_size + idx_paths_path_mtime 索引（iter-70），
        #          IF NOT EXISTS 安全升级，不触发 purge
        conn.executescript(SCHEMA_SQL)
        current = CURRENT_VERSION
        conn.execute(f"PRAGMA user_version = {current}")
        logger.info("schema 升级到 v%d", current)

    # 同步 meta 中的版本号（首次写入或清空后重建都会到达此处）
    _write_meta(conn, "cache_compat_version", str(CACHE_COMPAT_VERSION))
    _write_meta(conn, "schema_version", str(CURRENT_VERSION))
    _write_meta(conn, "migrated_at", _now_iso())

    return current
