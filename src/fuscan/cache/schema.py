"""SQLite 缓存 schema 定义与版本迁移。

schema 通过 ``PRAGMA user_version`` 标识版本，未来变更走 ``migrate()`` 增量升级。
所有 DDL 幂等（``IF NOT EXISTS``），便于 ``CacheStore`` 构造时安全执行。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = ["CURRENT_VERSION", "SCHEMA_SQL", "migrate"]

logger = logging.getLogger(__name__)

# schema 版本号：每次 DDL 变更递增，对应一次 migrate 步骤
CURRENT_VERSION: int = 1


SCHEMA_SQL: str = """
PRAGMA foreign_keys = ON;

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

CREATE INDEX IF NOT EXISTS idx_paths_path ON file_paths(path);

CREATE TABLE IF NOT EXISTS scan_results (
    file_hash   TEXT NOT NULL,
    rule_hash   TEXT NOT NULL,
    matched     INTEGER NOT NULL,
    severity    TEXT,
    detail      TEXT,
    match_text  TEXT,
    match_count INTEGER NOT NULL DEFAULT 1,
    target      TEXT,
    cached_at   TEXT NOT NULL,
    PRIMARY KEY (file_hash, rule_hash),
    FOREIGN KEY (file_hash) REFERENCES scanned_files(file_hash) ON DELETE CASCADE,
    FOREIGN KEY (rule_hash) REFERENCES rules(rule_hash) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_results_file ON scan_results(file_hash);
CREATE INDEX IF NOT EXISTS idx_results_rule ON scan_results(rule_hash);
"""


def migrate(conn: sqlite3.Connection) -> int:
    """执行 schema 迁移到 ``CURRENT_VERSION``。

    初次打开数据库时创建全部表；已存在的表通过 ``IF NOT EXISTS`` 跳过。
    ``executescript`` 在执行前隐式提交当前事务，脚本本身原子执行。

    :param conn: SQLite 连接（``row_factory`` 已设置）
    :return: 迁移后的 schema 版本号
    """
    cur = conn.execute("PRAGMA user_version")
    current = cur.fetchone()[0]
    if current >= CURRENT_VERSION:
        logger.debug("schema 已是最新版本: %d", current)
        return current

    target = current
    # v0 → v1：初始化全部表
    if current < 1:
        conn.executescript(SCHEMA_SQL)
        target = 1
        logger.info("schema 初始化: v0 → v1")
    # 未来 v1 → v2 在此追加：
    # if current < 2: ...
    conn.execute(f"PRAGMA user_version = {target}")
    return target
