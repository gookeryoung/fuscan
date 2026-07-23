"""缓存模块单元测试。

覆盖：

- :mod:`fuscan.cache.hashes`：规则/匹配的稳定序列化与哈希计算
- :mod:`fuscan.cache.store`：SQLite CRUD、清理、统计、并发
- 路径无关性、规则变更触发重扫等核心需求
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from fuscan.cache import (
    BatchWriteItem,
    CacheStats,
    CacheStore,
    compute_file_hash,
    compute_rule_hash,
    default_cache_path,
    hash_bytes,
    serialize_match,
    serialize_rule,
)
from fuscan.cache.schema import CURRENT_VERSION, migrate
from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    NotMatch,
    OrMatch,
    Rule,
    RuleSet,
    Severity,
)
from fuscan.scanner.result import RuleHit

# ---------------------------------------------------------------- 哈希与序列化


def _filename_rule(name: str = "r1", pattern: str = "secret") -> Rule:
    return Rule(
        name=name,
        match=LeafMatch(
            target=MatchTarget.FILENAME,
            mode=MatchMode.CONTAINS,
            pattern=pattern,
        ),
    )


def _content_rule(name: str = "r2", pattern: str = "password") -> Rule:
    return Rule(
        name=name,
        severity=Severity.CRITICAL,
        match=LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.REGEX,
            pattern=pattern,
            case_sensitive=True,
        ),
    )


def _and_rule(name: str = "r3") -> Rule:
    return Rule(
        name=name,
        match=AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="conf"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="db"),
            )
        ),
    )


def _or_rule(name: str = "r4") -> Rule:
    return Rule(
        name=name,
        match=OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="a"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="b"),
            )
        ),
    )


def _not_rule(name: str = "r5") -> Rule:
    return Rule(
        name=name,
        match=NotMatch(child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup")),
    )


class TestSerializeMatch:
    def test_serialize_leaf(self) -> None:
        m = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x")
        data = serialize_match(m)
        assert data["type"] == "leaf"
        assert data["target"] == "filename"
        assert data["mode"] == "contains"
        assert data["pattern"] == "x"
        assert data["case_sensitive"] is False

    def test_serialize_and(self) -> None:
        m = _and_rule().match
        data = serialize_match(m)
        assert data["type"] == "and"
        assert len(data["children"]) == 2
        assert all(isinstance(c, dict) for c in data["children"])

    def test_serialize_or(self) -> None:
        data = serialize_match(_or_rule().match)
        assert data["type"] == "or"
        assert len(data["children"]) == 2

    def test_serialize_not(self) -> None:
        data = serialize_match(_not_rule().match)
        assert data["type"] == "not"
        assert "child" in data
        assert data["child"]["target"] == "path"

    def test_serialize_unknown_match_raises(self) -> None:
        """未知匹配类型触发防御性 TypeError。"""
        with pytest.raises(TypeError, match="未知匹配类型"):
            serialize_match("not-a-match")  # type: ignore[arg-type]

    def test_serialize_leaf_includes_description(self) -> None:
        """C2 修复：LeafMatch 序列化须包含 description 字段，否则描述变更不触发缓存失效。"""
        m = LeafMatch(
            target=MatchTarget.FILENAME,
            mode=MatchMode.CONTAINS,
            pattern="x",
            description="敏感文件名",
        )
        data = serialize_match(m)
        assert data["description"] == "敏感文件名"

    def test_serialize_and_includes_description(self) -> None:
        """C2 修复：AndMatch 序列化须包含 description 字段。"""
        m = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="a"),
                LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="b"),
            ),
            description="组合匹配描述",
        )
        data = serialize_match(m)
        assert data["description"] == "组合匹配描述"

    def test_serialize_or_includes_description(self) -> None:
        """C2 修复：OrMatch 序列化须包含 description 字段。"""
        m = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="a"),
                LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="b"),
            ),
            description="或匹配描述",
        )
        data = serialize_match(m)
        assert data["description"] == "或匹配描述"

    def test_serialize_not_includes_description(self) -> None:
        """C2 修复：NotMatch 序列化须包含 description 字段。"""
        m = NotMatch(
            child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup"),
            description="排除备份目录",
        )
        data = serialize_match(m)
        assert data["description"] == "排除备份目录"

    def test_serialize_description_affects_rule_hash(self) -> None:
        """C2 修复验证：description 变更应改变 rule_hash，触发旧缓存失效。

        回归场景：若 serialize_match 遗漏 description 字段，仅修改描述后
        rule_hash 不变，缓存命中旧结果，描述变更无法生效。
        """
        rule_no_desc = Rule(
            name="r",
            match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x"),
        )
        rule_with_desc = Rule(
            name="r",
            match=LeafMatch(
                target=MatchTarget.FILENAME,
                mode=MatchMode.CONTAINS,
                pattern="x",
                description="新增描述",
            ),
        )
        assert compute_rule_hash(rule_no_desc) != compute_rule_hash(rule_with_desc)


class TestRuleHashCompute:
    def test_same_rule_same_hash(self) -> None:
        r1 = _filename_rule()
        r2 = _filename_rule()
        assert compute_rule_hash(r1) == compute_rule_hash(r2)
        assert len(compute_rule_hash(r1)) == 64

    def test_different_rule_different_hash(self) -> None:
        r1 = _filename_rule(pattern="secret")
        r2 = _filename_rule(pattern="password")
        assert compute_rule_hash(r1) != compute_rule_hash(r2)

    def test_case_sensitive_affects_hash(self) -> None:
        r1 = Rule(
            name="r",
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="x",
                case_sensitive=False,
            ),
        )
        r2 = Rule(
            name="r",
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="x",
                case_sensitive=True,
            ),
        )
        assert compute_rule_hash(r1) != compute_rule_hash(r2)

    def test_severity_affects_hash(self) -> None:
        r1 = Rule(
            name="r",
            severity=Severity.INFO,
            match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x"),
        )
        r2 = Rule(
            name="r",
            severity=Severity.CRITICAL,
            match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x"),
        )
        assert compute_rule_hash(r1) != compute_rule_hash(r2)

    def test_composite_match_hash_stable(self) -> None:
        r1 = _and_rule()
        r2 = _and_rule()
        assert compute_rule_hash(r1) == compute_rule_hash(r2)

    def test_rule_hash_is_sha256_hex(self) -> None:
        h = compute_rule_hash(_filename_rule())
        # 规则序列化字节远小于分流阈值，始终走 SHA-256，输出 64 字符 hex
        assert len(h) == 64
        # 全部为十六进制字符
        int(h, 16)

    def test_serialize_rule_is_valid_json(self) -> None:
        s = serialize_rule(_filename_rule())
        data = json.loads(s)
        assert data["name"] == "r1"
        assert "match" in data

    def test_hash_bytes_empty(self) -> None:
        # 空字节 < 阈值，走 SHA-256
        assert hash_bytes(b"") == hashlib.sha256(b"").hexdigest()

    def test_hash_bytes_small_uses_sha256(self) -> None:
        """小文件（< 8KB）走 SHA-256 路径。"""
        data = b"hello"
        assert hash_bytes(data) == hashlib.sha256(data).hexdigest()

    def test_hash_bytes_large_uses_blake2b(self) -> None:
        """大文件（≥ 8KB）走 BLAKE2b digest_size=32 路径。"""
        data = b"x" * (8 * 1024)
        assert hash_bytes(data) == hashlib.blake2b(data, digest_size=32).hexdigest()

    def test_hash_bytes_threshold_boundary(self) -> None:
        """阈值边界：恰好 8192 字节走 BLAKE2b，8191 字节走 SHA-256。"""
        assert hash_bytes(b"x" * 8191) == hashlib.sha256(b"x" * 8191).hexdigest()
        assert hash_bytes(b"x" * 8192) == hashlib.blake2b(b"x" * 8192, digest_size=32).hexdigest()

    def test_compute_file_hash(self, tmp_path: Path) -> None:
        p = tmp_path / "a.txt"
        p.write_bytes(b"hello")
        # 小文件走 SHA-256
        assert compute_file_hash(p) == hashlib.sha256(b"hello").hexdigest()

    def test_compute_file_hash_missing(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            compute_file_hash(tmp_path / "missing.txt")


# ---------------------------------------------------------------- CacheStore 初始化


class TestCacheStoreInit:
    def test_init_creates_schema(self, tmp_path: Path) -> None:
        db = tmp_path / "cache.db"
        store = CacheStore(db)
        assert db.exists()
        assert store.schema_version == CURRENT_VERSION
        store.close()

    def test_init_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "cache.db"
        store1 = CacheStore(db)
        store1.register_ruleset(RuleSet(version="1.0", rules=(_filename_rule(),)))
        store1.close()
        # 二次打开不应报错，schema 与数据保留
        store2 = CacheStore(db)
        assert store2.schema_version == CURRENT_VERSION
        hashes = store2.get_rule_hashes()
        assert "r1" in hashes
        store2.close()

    def test_migration_creates_tables(self, tmp_path: Path) -> None:
        db = tmp_path / "cache.db"
        store = CacheStore(db)
        # 通过 stats 验证表已创建（COUNT 查询不抛异常即说明表存在）
        stats = store.stats()
        assert stats.rule_files == 0
        assert stats.rules == 0
        store.close()

    def test_default_cache_path_under_home(self) -> None:
        p = default_cache_path()
        assert p.parent == Path.home() / ".fuscan"
        assert p.name == "cache.db"

    def test_context_manager(self, tmp_path: Path) -> None:
        """with 语句自动关闭连接。"""
        db = tmp_path / "cache.db"
        with CacheStore(db) as store:
            store.register_ruleset(RuleSet(version="1.0", rules=(_filename_rule(),)))
            assert store.stats().rules == 1
        # 退出后连接已关闭，后续操作应抛异常
        with pytest.raises(sqlite3.ProgrammingError):
            store.stats()

    def test_db_path_property(self, tmp_path: Path) -> None:
        db = tmp_path / "cache.db"
        store = CacheStore(db)
        assert store.db_path == db
        store.close()

    def test_init_failure_closes_connection(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """I9 修复：_init_db 失败时关闭连接，避免泄漏。

        通过 mock migrate 抛异常模拟 schema 损坏场景，验证异常抛出
        且 _conn.close() 被调用。sqlite3.Connection 的 close/execute 均为
        只读属性，用 MagicMock 替换整个连接以跟踪 close 调用。
        """
        from unittest.mock import MagicMock

        from fuscan.cache import store as store_mod

        mock_conn = MagicMock()

        def fake_migrate(conn: sqlite3.Connection) -> int:
            raise RuntimeError("模拟 schema 损坏")

        monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: mock_conn)
        monkeypatch.setattr(store_mod, "migrate", fake_migrate)
        with pytest.raises(RuntimeError, match="模拟 schema 损坏"):
            CacheStore(tmp_path / "cache.db")
        # _conn.close() 应被调用一次（I9 修复核心）
        assert mock_conn.close.called

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        """S7 修复：close() 重复调用不应抛异常。"""
        db = tmp_path / "cache.db"
        store = CacheStore(db)
        store.close()
        # 重复调用不应抛异常
        store.close()
        store.close()

    def test_context_manager_close_idempotent(self, tmp_path: Path) -> None:
        """S7 修复：with 语句退出后再次 close 不应抛异常。"""
        db = tmp_path / "cache.db"
        with CacheStore(db) as store:
            store.register_ruleset(RuleSet(version="1.0", rules=(_filename_rule(),)))
        # with 退出已 close，再调一次不应抛异常
        store.close()


# ---------------------------------------------------------------- 规则登记


class TestCacheStoreRuleset:
    def test_register_ruleset_returns_hashes(self, tmp_path: Path) -> None:
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(), _content_rule()))
        hashes = store.register_ruleset(rs)
        assert set(hashes.keys()) == {"r1", "r2"}
        assert len(hashes["r1"]) == 64
        store.close()

    def test_register_ruleset_with_source_files(self, tmp_path: Path) -> None:
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text("dummy", encoding="utf-8")
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        file_hash = hash_bytes(b"dummy")
        store.register_ruleset(rs, source_files={rules_file: file_hash})
        stats = store.stats()
        assert stats.rule_files == 1
        assert stats.rules == 1
        store.close()

    def test_register_ruleset_stat_oserror_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """source_files 中的路径 stat() 抛 OSError 时回退到 mtime=0。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))

        def raise_oserror(self: Path) -> float:
            raise OSError("simulated")

        # 仅对真实存在的路径触发；__inline__ 路径走 exists() False 分支
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text("x", encoding="utf-8")
        monkeypatch.setattr(Path, "stat", raise_oserror)
        store.register_ruleset(rs, source_files={rules_file: hash_bytes(b"x")})
        # 登记成功，mtime=0（OSError 回退）
        store.close()

    def test_register_ruleset_inline_when_no_sources(self, tmp_path: Path) -> None:
        """无 source_files 时使用 __inline__ 虚拟来源。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        store.register_ruleset(rs)
        # 默认虚拟来源登记，rules 表有规则
        stats = store.stats()
        assert stats.rules == 1
        store.close()

    def test_register_ruleset_dedup_rules(self, tmp_path: Path) -> None:
        """同一规则被两个文件载入时，rules 表只存一份。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        store.register_ruleset(
            rs,
            source_files={
                Path("a.yaml"): hash_bytes(b"a"),
                Path("b.yaml"): hash_bytes(b"b"),
            },
        )
        stats = store.stats()
        assert stats.rule_files == 2
        assert stats.rules == 1  # 同一规则去重
        store.close()

    def test_register_ruleset_update_file_hash(self, tmp_path: Path) -> None:
        """规则文件哈希变化后再次登记，rule_files 表更新而非新增。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        fpath = Path("rules.yaml")
        store.register_ruleset(rs, source_files={fpath: hash_bytes(b"v1")})
        store.register_ruleset(rs, source_files={fpath: hash_bytes(b"v2")})
        stats = store.stats()
        assert stats.rule_files == 1  # 同一文件不重复
        store.close()

    def test_get_rule_hashes_after_register(self, tmp_path: Path) -> None:
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(), _content_rule()))
        store.register_ruleset(rs)
        hashes = store.get_rule_hashes()
        assert hashes["r1"] == compute_rule_hash(_filename_rule())
        assert hashes["r2"] == compute_rule_hash(_content_rule())
        store.close()


# ---------------------------------------------------------------- 结果缓存


class TestCacheStoreResults:
    def test_put_and_get_hit(self, tmp_path: Path) -> None:
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        file_hash = hash_bytes(b"file-content")
        hit = RuleHit(
            rule_name="r1",
            severity=Severity.WARNING,
            detail="命中 secret",
            match_text="secret",
            match_count=2,
            target="filename",
        )
        store.put_result(file_hash, rule_hash, hit)
        cached = store.get_cached_hits(file_hash, [rule_hash])
        assert rule_hash in cached
        result = cached[rule_hash]
        assert result is not None
        assert result.detail == "命中 secret"
        assert result.match_count == 2
        assert result.match_text == "secret"
        assert result.target == "filename"
        store.close()

    def test_put_and_get_miss(self, tmp_path: Path) -> None:
        """规则未命中也缓存（值为 None），避免重复扫描未命中。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        file_hash = hash_bytes(b"no-hit-content")
        store.put_result(file_hash, rule_hash, None)
        cached = store.get_cached_hits(file_hash, [rule_hash])
        assert cached[rule_hash] is None
        store.close()

    def test_get_cached_hits_empty_query(self, tmp_path: Path) -> None:
        store = CacheStore(tmp_path / "cache.db")
        result = store.get_cached_hits("abc", [])
        assert result == {}
        store.close()

    def test_get_cached_hits_partial_miss(self, tmp_path: Path) -> None:
        """部分规则已缓存，部分未缓存：未缓存的不在返回字典中。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(), _content_rule()))
        hashes = store.register_ruleset(rs)
        file_hash = hash_bytes(b"content")
        # 仅写 r1 的结果
        store.put_result(
            file_hash,
            hashes["r1"],
            RuleHit(rule_name="r1", severity=Severity.WARNING, detail="d"),
        )
        cached = store.get_cached_hits(file_hash, [hashes["r1"], hashes["r2"]])
        assert hashes["r1"] in cached
        assert hashes["r2"] not in cached  # 未缓存
        store.close()

    def test_path_change_still_hits(self, tmp_path: Path) -> None:
        """核心需求：路径变化后缓存仍命中（按 file_hash 查询，与路径无关）。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        # 同一内容，两个不同路径
        file_hash = hash_bytes(b"same-content")
        path1 = Path("/old/location/file.txt")
        path2 = Path("/new/location/file_moved.txt")
        hit = RuleHit(rule_name="r1", severity=Severity.WARNING, detail="d")
        store.put_result(file_hash, rule_hash, hit)
        store.register_file(file_hash, 100)
        store.register_path(file_hash, path1, 1000.0)
        store.register_path(file_hash, path2, 2000.0)
        # 路径变化后查询，仍命中
        cached = store.get_cached_hits(file_hash, [rule_hash])
        assert cached[rule_hash] is not None
        store.close()

    def test_rule_change_triggers_rescan(self, tmp_path: Path) -> None:
        """规则变更：旧 rule_hash 的缓存不会被新 rule_hash 命中。"""
        store = CacheStore(tmp_path / "cache.db")
        old_rule = _filename_rule(pattern="old")
        rs_old = RuleSet(version="1.0", rules=(old_rule,))
        hashes_old = store.register_ruleset(rs_old)
        file_hash = hash_bytes(b"content")
        store.put_result(
            file_hash,
            hashes_old["r1"],
            RuleHit(rule_name="r1", severity=Severity.WARNING, detail="old"),
        )
        # 新规则（pattern 不同 → hash 不同）
        new_rule = _filename_rule(pattern="new")
        rs_new = RuleSet(version="1.0", rules=(new_rule,))
        hashes_new = store.register_ruleset(rs_new)
        assert hashes_old["r1"] != hashes_new["r1"]
        # 新规则查询应未命中（不在缓存中）
        cached = store.get_cached_hits(file_hash, [hashes_new["r1"]])
        assert hashes_new["r1"] not in cached
        store.close()

    def test_put_result_overwrites(self, tmp_path: Path) -> None:
        """同一 (file_hash, rule_hash) 二次写入应覆盖。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        file_hash = hash_bytes(b"c")
        store.put_result(
            file_hash,
            rule_hash,
            RuleHit(rule_name="r1", severity=Severity.INFO, detail="first"),
        )
        store.put_result(
            file_hash,
            rule_hash,
            RuleHit(rule_name="r1", severity=Severity.CRITICAL, detail="second"),
        )
        cached = store.get_cached_hits(file_hash, [rule_hash])
        assert cached[rule_hash].detail == "second"  # pyrefly: ignore [missing-attribute]
        assert cached[rule_hash].severity == Severity.CRITICAL  # pyrefly: ignore [missing-attribute]
        store.close()

    def test_put_and_get_match_texts_and_description(self, tmp_path: Path) -> None:
        """match_texts 与 match_description 应正确序列化与反序列化（需求3/4）。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        file_hash = hash_bytes(b"multi-hit-content")
        hit = RuleHit(
            rule_name="r1",
            severity=Severity.WARNING,
            detail="命中多个关键词",
            match_text="password",
            match_count=3,
            target="content",
            match_texts=("password", "token", "api_key"),
            match_description="敏感凭证关键词组合",
        )
        store.put_result(file_hash, rule_hash, hit)
        cached = store.get_cached_hits(file_hash, [rule_hash])
        result = cached[rule_hash]
        assert result is not None
        assert result.match_texts == ("password", "token", "api_key")
        assert result.match_description == "敏感凭证关键词组合"
        store.close()

    def test_put_and_get_empty_match_texts(self, tmp_path: Path) -> None:
        """match_texts 为空元组时应正确序列化（兼容旧缓存）。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        file_hash = hash_bytes(b"empty-texts")
        hit = RuleHit(
            rule_name="r1",
            severity=Severity.WARNING,
            detail="无文本命中",
            match_text="",
            match_count=1,
            target="",
            match_texts=(),
            match_description="",
        )
        store.put_result(file_hash, rule_hash, hit)
        cached = store.get_cached_hits(file_hash, [rule_hash])
        result = cached[rule_hash]
        assert result is not None
        assert result.match_texts == ()
        assert result.match_description == ""
        store.close()

    def test_put_and_get_unicode_match_texts(self, tmp_path: Path) -> None:
        """match_texts 含中文时应正确序列化（JSON ensure_ascii=False）。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        file_hash = hash_bytes(b"unicode-content")
        hit = RuleHit(
            rule_name="r1",
            severity=Severity.WARNING,
            detail="命中中文关键词",
            match_text="密码",
            match_count=1,
            target="content",
            match_texts=("密码", "令牌"),
            match_description="中文凭证描述",
        )
        store.put_result(file_hash, rule_hash, hit)
        cached = store.get_cached_hits(file_hash, [rule_hash])
        result = cached[rule_hash]
        assert result is not None
        assert result.match_texts == ("密码", "令牌")
        assert result.match_description == "中文凭证描述"
        store.close()

    def test_batch_put_results_match_texts_and_description(self, tmp_path: Path) -> None:
        """batch_put_results 应正确写入 match_texts/match_description 字段。"""
        from fuscan.cache.store import BatchWriteItem

        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            file_hash = hash_bytes(b"batch-multi")
            hit = RuleHit(
                rule_name="r1",
                severity=Severity.WARNING,
                detail="批量写入多文本",
                match_text="password",
                match_count=2,
                target="content",
                match_texts=("password", "secret"),
                match_description="批量凭证描述",
            )
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=50,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, hit),),
                    )
                ]
            )
            cached = store.get_cached_hits(file_hash, [rule_hash])
            result = cached[rule_hash]
            assert result is not None
            assert result.match_texts == ("password", "secret")
            assert result.match_description == "批量凭证描述"

    def test_batch_put_results_rollback_failure_preserves_original_error(self, tmp_path: Path) -> None:
        """I5 修复：ROLLBACK 失败不应掩盖原始异常。

        场景：executemany 抛 ValueError（原始异常），ROLLBACK 也抛 sqlite3.Error，
        最终应抛出 ValueError 而非 sqlite3.Error，保留原始因果链。

        sqlite3.Connection.execute 为只读属性，用 MagicMock 替换 _conn
        以注入异常行为。
        """
        from unittest.mock import MagicMock

        from fuscan.cache.store import BatchWriteItem

        store = CacheStore(tmp_path / "c.db")
        rule_hash = _register_rule(store)
        file_hash = hash_bytes(b"rollback-test")

        # 用 MagicMock 替换 _conn：executemany 抛 ValueError（原始异常），
        # execute("ROLLBACK") 抛 sqlite3.Error（回滚失败）
        mock_conn = MagicMock()

        def fake_execute(sql: str, *params: Any) -> Any:
            if sql == "ROLLBACK":
                raise sqlite3.Error("模拟 ROLLBACK 失败")
            return MagicMock()

        def fake_executemany(sql: str, *params: Any) -> Any:
            raise ValueError("模拟写入失败")

        mock_conn.execute.side_effect = fake_execute
        mock_conn.executemany.side_effect = fake_executemany
        store._conn = mock_conn  # type: ignore[assignment]

        item = BatchWriteItem(
            file_hash=file_hash,
            size=50,
            path=tmp_path / "a.txt",
            mtime=1.0,
            hits=((rule_hash, None),),
        )
        # 应抛出原始的 ValueError，而非 ROLLBACK 的 sqlite3.Error
        with pytest.raises(ValueError, match="模拟写入失败"):
            store.batch_put_results([item])
        store.close()


# ---------------------------------------------------------------- 文件登记


class TestCacheStoreFileRegistration:
    def test_register_file_updates_timestamp(self, tmp_path: Path) -> None:
        store = CacheStore(tmp_path / "cache.db")
        file_hash = hash_bytes(b"c")
        store.register_file(file_hash, 100)
        stats = store.stats()
        assert stats.scanned_files == 1
        store.close()

    def test_register_file_idempotent(self, tmp_path: Path) -> None:
        """同一 file_hash 二次登记不新增行。"""
        store = CacheStore(tmp_path / "cache.db")
        file_hash = hash_bytes(b"c")
        store.register_file(file_hash, 100)
        store.register_file(file_hash, 200)  # size 不同也不新增
        stats = store.stats()
        assert stats.scanned_files == 1
        store.close()

    def test_register_path_multiple_paths(self, tmp_path: Path) -> None:
        """同一 file_hash 可对应多个路径。"""
        store = CacheStore(tmp_path / "cache.db")
        file_hash = hash_bytes(b"c")
        store.register_file(file_hash, 100)
        store.register_path(file_hash, Path("/a.txt"), 1.0)
        store.register_path(file_hash, Path("/b.txt"), 2.0)
        stats = store.stats()
        assert stats.file_paths == 2
        assert stats.scanned_files == 1
        store.close()

    def test_register_path_without_file_fails_fk(self, tmp_path: Path) -> None:
        """未登记 scanned_files 直接登记路径，外键约束应阻止。"""
        store = CacheStore(tmp_path / "cache.db")
        file_hash = hash_bytes(b"missing")
        with pytest.raises(sqlite3.IntegrityError):
            store.register_path(file_hash, Path("/x.txt"), 1.0)
        store.close()


# ---------------------------------------------------------------- 清理


class TestCacheStorePrune:
    def test_prune_orphan_rules(self, tmp_path: Path) -> None:
        """清理不在活跃集合中的旧规则。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(), _content_rule()))
        hashes = store.register_ruleset(rs)
        # 旧规则全部不再活跃
        deleted = store.prune_orphan_rules({hashes["r1"]})
        assert deleted == 1
        stats = store.stats()
        assert stats.rules == 1
        store.close()

    def test_prune_orphan_rules_keeps_active(self, tmp_path: Path) -> None:
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(), _content_rule()))
        hashes = store.register_ruleset(rs)
        deleted = store.prune_orphan_rules(set(hashes.values()))
        assert deleted == 0
        store.close()

    def test_prune_orphan_rules_empty_active(self, tmp_path: Path) -> None:
        """空活跃集合 → 全部规则被清理。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(), _content_rule()))
        store.register_ruleset(rs)
        deleted = store.prune_orphan_rules(set())
        assert deleted == 2
        assert store.stats().rules == 0
        store.close()

    def test_prune_orphan_rules_cascade_results(self, tmp_path: Path) -> None:
        """清理规则时，关联的 scan_results 也被级联删除。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(), _content_rule()))
        hashes = store.register_ruleset(rs)
        file_hash = hash_bytes(b"c")
        store.register_file(file_hash, 1)
        store.put_result(
            file_hash,
            hashes["r1"],
            RuleHit(rule_name="r1", severity=Severity.WARNING, detail="d"),
        )
        store.put_result(
            file_hash,
            hashes["r2"],
            RuleHit(rule_name="r2", severity=Severity.CRITICAL, detail="d"),
        )
        # 删除 r2
        store.prune_orphan_rules({hashes["r1"]})
        cached = store.get_cached_hits(file_hash, [hashes["r1"], hashes["r2"]])
        assert hashes["r1"] in cached
        assert hashes["r2"] not in cached
        store.close()

    def test_prune_stale_files(self, tmp_path: Path) -> None:
        """清理过期文件缓存（last_scanned_at 早于阈值）。"""
        store = CacheStore(tmp_path / "cache.db")
        file_hash = hash_bytes(b"c")
        store.register_file(file_hash, 100)
        # 模拟一年前扫描

        conn = store._conn  # 测试访问私有属性以模拟过期场景
        old = "2020-01-01T00:00:00Z"
        conn.execute(
            "UPDATE scanned_files SET last_scanned_at = ? WHERE file_hash = ?",
            (old, file_hash),
        )
        deleted = store.prune_stale_files(max_age_days=30)
        assert deleted == 1
        stats = store.stats()
        assert stats.scanned_files == 0
        store.close()

    def test_prune_stale_files_keeps_recent(self, tmp_path: Path) -> None:
        store = CacheStore(tmp_path / "cache.db")
        store.register_file(hash_bytes(b"c"), 100)
        deleted = store.prune_stale_files(max_age_days=30)
        assert deleted == 0
        store.close()

    def test_prune_stale_files_invalid_arg(self, tmp_path: Path) -> None:
        store = CacheStore(tmp_path / "cache.db")
        with pytest.raises(ValueError):
            store.prune_stale_files(max_age_days=-1)
        store.close()


# ---------------------------------------------------------------- 统计


class TestCacheStoreStats:
    def test_stats_empty(self, tmp_path: Path) -> None:
        store = CacheStore(tmp_path / "cache.db")
        stats = store.stats()
        assert isinstance(stats, CacheStats)
        assert stats.rule_files == 0
        assert stats.rules == 0
        assert stats.scanned_files == 0
        assert stats.scan_results == 0
        assert stats.schema_version == CURRENT_VERSION
        assert stats.db_bytes > 0  # 空库也有字节
        store.close()

    def test_stats_counts(self, tmp_path: Path) -> None:
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(), _content_rule()))
        hashes = store.register_ruleset(rs)
        file_hash = hash_bytes(b"c")
        store.register_file(file_hash, 100)
        store.register_path(file_hash, Path("/x.txt"), 1.0)
        store.put_result(
            file_hash,
            hashes["r1"],
            RuleHit(rule_name="r1", severity=Severity.WARNING, detail="d"),
        )
        stats = store.stats()
        assert stats.rules == 2
        assert stats.scanned_files == 1
        assert stats.file_paths == 1
        assert stats.scan_results == 1
        store.close()

    def test_stats_db_missing_returns_zero_bytes(self, tmp_path: Path) -> None:
        """db_path 不存在时 db_bytes 为 0。"""
        store = CacheStore(tmp_path / "cache.db")
        store._db_path = tmp_path / "nonexistent.db"  # 测试访问私有属性模拟路径丢失
        stats = store.stats()
        assert stats.db_bytes == 0
        store.close()


# ---------------------------------------------------------------- 并发


class TestCacheStoreConcurrency:
    def test_concurrent_writes(self, tmp_path: Path) -> None:
        """多线程并发写入不同 file_hash 不冲突。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        errors: list[Exception] = []

        def writer(idx: int) -> None:
            try:
                fh = hash_bytes(f"content-{idx}".encode())
                store.register_file(fh, idx)
                store.put_result(
                    fh,
                    rule_hash,
                    RuleHit(rule_name="r1", severity=Severity.WARNING, detail=f"d-{idx}"),
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        stats = store.stats()
        assert stats.scanned_files == 20
        assert stats.scan_results == 20
        store.close()

    def test_concurrent_read_write(self, tmp_path: Path) -> None:
        """读+写并发：读不阻塞写，且读到的是一致状态。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        stop = threading.Event()
        errors: list[Exception] = []

        def reader() -> None:
            try:
                while not stop.is_set():
                    store.stats()
            except Exception as exc:
                errors.append(exc)

        def writer() -> None:
            try:
                for i in range(50):
                    fh = hash_bytes(f"c-{i}".encode())
                    store.register_file(fh, i)
                    store.put_result(
                        fh,
                        rule_hash,
                        RuleHit(rule_name="r1", severity=Severity.WARNING, detail=f"d-{i}"),
                    )
            except Exception as exc:
                errors.append(exc)
            stop.set()

        # 给 CacheStore 加一个批量查询辅助方法用于测试
        reader_t = threading.Thread(target=reader)
        writer_t = threading.Thread(target=writer)
        writer_t.start()
        reader_t.start()
        writer_t.join()
        reader_t.join(timeout=2)
        assert not errors
        store.close()

    def test_concurrent_same_file_hash(self, tmp_path: Path) -> None:
        """多线程同时写同一 (file_hash, rule_hash)：最后一个胜出，不抛异常。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        fh = hash_bytes(b"shared")
        store.register_file(fh, 0)
        errors: list[Exception] = []

        def writer(idx: int) -> None:
            try:
                store.put_result(
                    fh,
                    rule_hash,
                    RuleHit(rule_name="r1", severity=Severity.WARNING, detail=f"d-{idx}"),
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        cached = store.get_cached_hits(fh, [rule_hash])
        assert cached[rule_hash] is not None
        store.close()

    def test_read_connections_are_thread_local_and_query_only(self, tmp_path: Path) -> None:
        """读连接为线程本地且 query_only，与主写连接分离（iter-68 读写分离）。"""
        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        store.register_ruleset(rs)
        # 主线程读连接
        main_conn = store._get_read_conn()
        assert main_conn is not store._conn  # 与主写连接不同
        # query_only 验证：写入应抛 OperationalError
        with pytest.raises(sqlite3.OperationalError):
            main_conn.execute("INSERT INTO rules (rule_hash) VALUES ('x')")
        # 不同线程返回不同连接
        other_conn_holder: list[sqlite3.Connection] = []

        def get_conn() -> None:
            other_conn_holder.append(store._get_read_conn())

        t = threading.Thread(target=get_conn)
        t.start()
        t.join()
        assert other_conn_holder[0] is not main_conn  # 线程本地
        store.close()

    def test_concurrent_reads_do_not_block_writes(self, tmp_path: Path) -> None:
        """多线程并发读不阻塞写：写操作耗时不应因并发读显著增加（iter-68）。"""
        import time

        store = CacheStore(tmp_path / "cache.db")
        rs = RuleSet(version="1.0", rules=(_filename_rule(),))
        hashes = store.register_ruleset(rs)
        rule_hash = hashes["r1"]
        # 预写入一些数据供读取
        for i in range(20):
            fh = hash_bytes(f"init-{i}".encode())
            store.register_file(fh, i)
            store.put_result(fh, rule_hash, RuleHit(rule_name="r1", severity=Severity.INFO, detail=f"d-{i}"))

        stop = threading.Event()
        read_errors: list[Exception] = []

        def reader() -> None:
            try:
                while not stop.is_set():
                    store.get_rule_hashes()
                    store.get_extracted_content(hash_bytes(b"init-0"))
            except Exception as exc:
                read_errors.append(exc)

        # 启动 4 个读线程
        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in readers:
            t.start()
        # 并发读的同时执行写，测量写耗时
        write_start = time.perf_counter()
        for i in range(50):
            fh = hash_bytes(f"concurrent-{i}".encode())
            store.register_file(fh, i)
            store.put_result(fh, rule_hash, RuleHit(rule_name="r1", severity=Severity.INFO, detail=f"c-{i}"))
        write_elapsed = time.perf_counter() - write_start
        stop.set()
        for t in readers:
            t.join(timeout=2)
        assert not read_errors
        # 50 次写入在并发读下应在合理时间内完成（宽松上限 10 秒，避免 CI 不稳定）
        assert write_elapsed < 10.0, f"写操作耗时 {write_elapsed:.2f}s，可能被并发读阻塞"
        store.close()


# ---------------------------------------------------------------- migrate


class TestMigrate:
    def test_migrate_fresh_db(self, tmp_path: Path) -> None:
        conn = sqlite3.connect(str(tmp_path / "fresh.db"))
        conn.row_factory = sqlite3.Row
        version = migrate(conn)
        assert version == CURRENT_VERSION
        # 二次调用幂等
        assert migrate(conn) == CURRENT_VERSION
        conn.close()

    def test_migrate_already_up_to_date(self, tmp_path: Path) -> None:
        conn = sqlite3.connect(str(tmp_path / "u.db"))
        conn.row_factory = sqlite3.Row
        migrate(conn)
        # 第二次迁移应直接返回当前版本
        version = migrate(conn)
        assert version == CURRENT_VERSION
        conn.close()

    def test_migrate_v5_creates_indexes(self, tmp_path: Path) -> None:
        """iter-70：schema v5 应创建 idx_scanned_size 和 idx_paths_path_mtime 索引。"""
        conn = sqlite3.connect(str(tmp_path / "idx.db"))
        conn.row_factory = sqlite3.Row
        migrate(conn)
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'"
            ).fetchall()
        }
        assert "idx_scanned_size" in indexes
        assert "idx_paths_path_mtime" in indexes
        conn.close()


class TestCacheCompatVersion:
    """缓存数据兼容版本号管理（iter-38）。"""

    def test_fresh_db_writes_compat_version(self, tmp_path: Path) -> None:
        """新建数据库应写入当前 CACHE_COMPAT_VERSION 到 meta 表。"""
        from fuscan.cache.schema import CACHE_COMPAT_VERSION

        conn = sqlite3.connect(str(tmp_path / "fresh.db"))
        conn.row_factory = sqlite3.Row
        migrate(conn)
        row = conn.execute("SELECT value FROM meta WHERE key = 'cache_compat_version'").fetchone()
        assert row is not None
        assert int(row["value"]) == CACHE_COMPAT_VERSION
        conn.close()

    def test_old_compat_version_triggers_purge(self, tmp_path: Path) -> None:
        """兼容版本号低于当前值时清空旧业务表数据。"""
        db = tmp_path / "old.db"
        # 先用旧 schema + 旧 compat 版本号建库
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta (key, value) VALUES ('cache_compat_version', '1')")
        conn.execute("CREATE TABLE scanned_files (file_hash TEXT PRIMARY KEY, size INTEGER)")
        conn.execute("INSERT INTO scanned_files VALUES ('oldhash', 100)")
        conn.commit()

        # 触发迁移：应清空 scanned_files
        from fuscan.cache.schema import CACHE_COMPAT_VERSION

        assert CACHE_COMPAT_VERSION >= 2  # 当前为 3，旧库 v1 应被清空
        migrate(conn)
        # 旧数据应被清空
        rows = conn.execute("SELECT * FROM scanned_files").fetchall()
        assert len(rows) == 0
        # meta 中 compat 版本应已升级
        row = conn.execute("SELECT value FROM meta WHERE key = 'cache_compat_version'").fetchone()
        assert int(row["value"]) == CACHE_COMPAT_VERSION
        conn.close()

    def test_future_compat_version_triggers_purge(self, tmp_path: Path) -> None:
        """兼容版本号高于当前代码时，保守清空未来版本数据。"""
        db = tmp_path / "future.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # 写入未来版本号（远超当前值）
        conn.execute("INSERT INTO meta (key, value) VALUES ('cache_compat_version', '999')")
        conn.execute("CREATE TABLE scanned_files (file_hash TEXT PRIMARY KEY, size INTEGER)")
        conn.execute("INSERT INTO scanned_files VALUES ('futurehash', 200)")
        conn.commit()

        migrate(conn)
        # 未来数据应被清空
        rows = conn.execute("SELECT * FROM scanned_files").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_corrupted_compat_version_triggers_purge(self, tmp_path: Path) -> None:
        """meta 中 compat 版本号损坏（非数字）时清空缓存。"""
        db = tmp_path / "corrupt.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta (key, value) VALUES ('cache_compat_version', 'not-a-number')")
        conn.execute("CREATE TABLE scanned_files (file_hash TEXT PRIMARY KEY, size INTEGER)")
        conn.execute("INSERT INTO scanned_files VALUES ('x', 1)")
        conn.commit()

        migrate(conn)
        rows = conn.execute("SELECT * FROM scanned_files").fetchall()
        assert len(rows) == 0
        conn.close()

    def test_same_compat_version_preserves_data(self, tmp_path: Path) -> None:
        """兼容版本号一致时不触发清空。"""

        db = tmp_path / "same.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        # 模拟一个已迁移到当前版本的库，含真实数据
        migrate(conn)
        file_hash = hash_bytes(b"keep-me")
        conn.execute(
            "INSERT INTO scanned_files (file_hash, size, first_seen_at, last_scanned_at) "
            "VALUES (?, 42, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (file_hash,),
        )
        conn.commit()

        # 再次 migrate（模拟下次启动）
        migrate(conn)
        # 数据应保留
        row = conn.execute("SELECT size FROM scanned_files WHERE file_hash = ?", (file_hash,)).fetchone()
        assert row is not None
        assert row["size"] == 42
        conn.close()


class TestHitCache:
    """进程内 LRU 命中缓存（iter-38）。"""

    @staticmethod
    def _setup_store_with_rule(tmp_path: Path, rule_name: str = "r1") -> tuple[CacheStore, str]:
        """构造已登记单条规则的 CacheStore，返回 (store, rule_hash)。"""
        from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity

        rule = Rule(
            name=rule_name,
            description="d",
            severity=Severity.WARNING,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern="x",
                case_sensitive=False,
            ),
        )
        rs = RuleSet(version="1.0", rules=(rule,))
        store = CacheStore(tmp_path / "c.db")
        hashes = store.register_ruleset(rs)
        return store, hashes[rule_name]

    def test_get_cached_hits_writes_to_lru(self, tmp_path: Path) -> None:
        """首次查询走 SQLite 后写入 LRU，第二次同参数命中 LRU。"""
        store, rule_hash = self._setup_store_with_rule(tmp_path)
        try:
            file_hash = hash_bytes(b"content")
            store.put_result(file_hash, rule_hash, None)
            assert store.hit_cache_size() == 0  # put 后 invalidate

            # 首次查询：走 SQLite
            r1 = store.get_cached_hits(file_hash, [rule_hash])
            assert rule_hash in r1
            assert store.hit_cache_size() == 1
            # 第二次查询：命中 LRU
            r2 = store.get_cached_hits(file_hash, [rule_hash])
            assert r2 == r1
        finally:
            store.close()

    def test_put_result_invalidates_lru(self, tmp_path: Path) -> None:
        """put_result 后下次查询走 SQLite 取最新数据。"""
        store, rule_hash = self._setup_store_with_rule(tmp_path)
        try:
            file_hash = hash_bytes(b"c")
            # 首次未命中也缓存
            store.put_result(file_hash, rule_hash, None)
            store.get_cached_hits(file_hash, [rule_hash])  # 填充 LRU
            assert store.hit_cache_size() == 1

            # 写入命中结果
            from fuscan.rules.model import Severity
            from fuscan.scanner.result import RuleHit

            hit = RuleHit(
                rule_name="r",
                severity=Severity.WARNING,
                detail="d",
                match_text="t",
                match_count=1,
                target="content",
            )
            store.put_result(file_hash, rule_hash, hit)
            assert store.hit_cache_size() == 0  # 已 invalidate

            # 下次查询应取到 hit，而非缓存的 None
            r = store.get_cached_hits(file_hash, [rule_hash])
            assert r[rule_hash] is not None
            assert r[rule_hash].severity == Severity.WARNING  # pyrefly: ignore [missing-attribute]
        finally:
            store.close()

    def test_lru_evicts_oldest_when_full(self, tmp_path: Path) -> None:
        """LRU 容量超限时弹出最旧条目。"""
        from fuscan.cache import store as store_mod

        store, rule_hash = self._setup_store_with_rule(tmp_path)
        try:
            # 临时缩小容量以便测试
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(store_mod, "_HIT_CACHE_MAX", 3)
                for i in range(5):
                    fh = hash_bytes(f"content-{i}".encode())
                    store.put_result(fh, rule_hash, None)
                    store.get_cached_hits(fh, [rule_hash])  # 填充 LRU
                # 应只剩最近 3 个
                assert store.hit_cache_size() == 3
        finally:
            store.close()

    def test_rule_hashes_set_change_treats_as_miss(self, tmp_path: Path) -> None:
        """rule_hashes 集合变化时 LRU 视为未命中，走 SQLite。"""
        # 登记 2 条规则，得到 rh1 / rh2
        from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet, Severity

        rules = [
            Rule(
                name=f"r{i}",
                description="d",
                severity=Severity.WARNING,
                match=LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.CONTAINS,
                    pattern="x",
                    case_sensitive=False,
                ),
            )
            for i in (1, 2)
        ]
        rs = RuleSet(version="1.0", rules=tuple(rules))
        store = CacheStore(tmp_path / "c.db")
        try:
            hashes = store.register_ruleset(rs)
            rh1, rh2 = hashes["r1"], hashes["r2"]

            file_hash = hash_bytes(b"c")
            store.put_result(file_hash, rh1, None)
            store.get_cached_hits(file_hash, [rh1])  # LRU 用 (rh1) 填充

            # 加入 rh2 后 LRU 仍记录 (rh1) 集合，应判定未命中
            store.put_result(file_hash, rh2, None)
            r = store.get_cached_hits(file_hash, [rh1, rh2])
            # 应同时返回 rh1 和 rh2 的结果（来自 SQLite 重查）
            assert rh1 in r
            assert rh2 in r
        finally:
            store.close()

    def test_prune_clears_lru(self, tmp_path: Path) -> None:
        """清理孤立规则后 LRU 整体失效（规则集合已变）。"""
        store, rule_hash = self._setup_store_with_rule(tmp_path)
        try:
            file_hash = hash_bytes(b"c")
            store.put_result(file_hash, rule_hash, None)
            store.get_cached_hits(file_hash, [rule_hash])
            assert store.hit_cache_size() == 1

            # 传入空集合：触发"删除所有规则"分支，deleted > 0，LRU 应被清空
            store.prune_orphan_rules(set())
            assert store.hit_cache_size() == 0
        finally:
            store.close()


class TestLookupFileHash:
    """mtime 预筛：lookup_file_hash（iter-38）。"""

    def test_lookup_returns_hash_when_path_mtime_size_match(self, tmp_path: Path) -> None:
        """(path, mtime, size) 三元组完全匹配时返回已登记的 file_hash。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"content")
            path = tmp_path / "file.txt"
            path.write_bytes(b"content")
            st = path.stat()
            store.register_file(file_hash, st.st_size)
            store.register_path(file_hash, path, st.st_mtime)

            found = store.lookup_file_hash(path, st.st_mtime, st.st_size)
            assert found == file_hash

    def test_lookup_returns_none_when_mtime_differs(self, tmp_path: Path) -> None:
        """mtime 不匹配时返回 None（文件已修改，需重算哈希）。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"content")
            path = tmp_path / "file.txt"
            path.write_bytes(b"content")
            st = path.stat()
            store.register_file(file_hash, st.st_size)
            store.register_path(file_hash, path, st.st_mtime)

            # 模拟文件被修改：mtime + 100s
            assert store.lookup_file_hash(path, st.st_mtime + 100, st.st_size) is None

    def test_lookup_returns_none_when_size_differs(self, tmp_path: Path) -> None:
        """size 不匹配时返回 None。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"content")
            path = tmp_path / "file.txt"
            path.write_bytes(b"content")
            st = path.stat()
            store.register_file(file_hash, st.st_size)
            store.register_path(file_hash, path, st.st_mtime)

            assert store.lookup_file_hash(path, st.st_mtime, st.st_size + 1) is None

    def test_lookup_returns_none_when_path_unknown(self, tmp_path: Path) -> None:
        """未登记的路径返回 None。"""
        with CacheStore(tmp_path / "c.db") as store:
            assert store.lookup_file_hash(tmp_path / "unknown", 0.0, 0) is None

    def test_lookup_handles_multiple_paths_same_hash(self, tmp_path: Path) -> None:
        """同一 file_hash 关联多个路径时，每个路径都能查到。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"shared-content")
            p1 = tmp_path / "a.txt"
            p2 = tmp_path / "b.txt"
            p1.write_bytes(b"shared-content")
            p2.write_bytes(b"shared-content")
            st1 = p1.stat()
            st2 = p2.stat()
            store.register_file(file_hash, st1.st_size)
            store.register_path(file_hash, p1, st1.st_mtime)
            store.register_path(file_hash, p2, st2.st_mtime)

            assert store.lookup_file_hash(p1, st1.st_mtime, st1.st_size) == file_hash
            assert store.lookup_file_hash(p2, st2.st_mtime, st2.st_size) == file_hash


class TestLookupFileHashLruCache:
    """路径预筛 LRU 缓存（iter-73）。"""

    def test_lookup_fills_path_cache_on_sqlite_hit(self, tmp_path: Path) -> None:
        """首次 SQLite 查询命中后回填 _path_cache，第二次命中内存。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"content")
            path = tmp_path / "f.txt"
            path.write_bytes(b"content")
            st = path.stat()
            store.register_file(file_hash, st.st_size)
            store.register_path(file_hash, path, st.st_mtime)

            # 首次查询走 SQLite，回填 LRU
            assert store.path_cache_size() == 0
            assert store.lookup_file_hash(path, st.st_mtime, st.st_size) == file_hash
            assert store.path_cache_size() == 1

            # 第二次查询应命中 LRU（无法直接断言走内存，但 path_cache_size 不变）
            assert store.lookup_file_hash(path, st.st_mtime, st.st_size) == file_hash
            assert store.path_cache_size() == 1

    def test_lookup_does_not_cache_none_result(self, tmp_path: Path) -> None:
        """未登记路径返回 None，不写入 LRU（避免污染缓存）。"""
        with CacheStore(tmp_path / "c.db") as store:
            assert store.lookup_file_hash(tmp_path / "unknown", 0.0, 0) is None
            assert store.path_cache_size() == 0

    def test_batch_put_results_fills_path_cache(self, tmp_path: Path) -> None:
        """batch_put_results 写入后主动填充 _path_cache。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            file_hash = hash_bytes(b"doc")
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=100,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, _make_hit()),),
                    ),
                ]
            )
            # _path_cache 应被主动填充
            assert store.path_cache_size() == 1
            # lookup_file_hash 应命中 LRU（不查 SQLite）
            assert store.lookup_file_hash(tmp_path / "a.txt", 1.0, 100) == file_hash

    def test_batch_put_results_fills_hit_cache_when_hits_non_empty(self, tmp_path: Path) -> None:
        """batch_put_results 写入非空 hits 时主动填充 _hit_cache。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            file_hash = hash_bytes(b"doc")
            hit = _make_hit(detail="命中")
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=100,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, hit),),
                    ),
                ]
            )
            # _hit_cache 应被主动填充，get_cached_hits 命中内存
            assert store.hit_cache_size() == 1
            cached = store.get_cached_hits(file_hash, [rule_hash])
            assert rule_hash in cached
            assert cached[rule_hash] is not None
            assert cached[rule_hash].detail == "命中"  # pyrefly: ignore [missing-attribute]

    def test_batch_put_results_preserves_hit_cache_when_hits_empty(self, tmp_path: Path) -> None:
        """batch_put_results 写入空 hits（预筛命中）时不 invalidate _hit_cache。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            file_hash = hash_bytes(b"doc")
            # 首次写入：填充 _hit_cache
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=100,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, None),),
                    ),
                ]
            )
            assert store.hit_cache_size() == 1

            # 第二次写入：空 hits（预筛命中场景），应保留 _hit_cache
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=100,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=(),
                    ),
                ]
            )
            assert store.hit_cache_size() == 1
            # get_cached_hits 仍命中内存
            cached = store.get_cached_hits(file_hash, [rule_hash])
            assert cached == {rule_hash: None}

    def test_prune_orphan_rules_clears_path_cache(self, tmp_path: Path) -> None:
        """prune_orphan_rules 删除规则后清空 _path_cache。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=hash_bytes(b"doc"),
                        size=10,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, None),),
                    ),
                ]
            )
            assert store.path_cache_size() == 1
            # 传入空 active_rule_hashes，删除所有规则（孤立规则）
            store.prune_orphan_rules(set())
            assert store.path_cache_size() == 0

    def test_path_cache_mtime_change_misses(self, tmp_path: Path) -> None:
        """文件 mtime 变化后 LRU 键不同，自动失效。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"content")
            path = tmp_path / "f.txt"
            path.write_bytes(b"content")
            st = path.stat()
            store.register_file(file_hash, st.st_size)
            store.register_path(file_hash, path, st.st_mtime)

            # 首次查询填充 LRU
            assert store.lookup_file_hash(path, st.st_mtime, st.st_size) == file_hash
            assert store.path_cache_size() == 1

            # mtime 变化：LRU 键不同，未命中，走 SQLite 返回 None（未登记新 mtime）
            assert store.lookup_file_hash(path, st.st_mtime + 100, st.st_size) is None
            # None 不写入 LRU，path_cache_size 不变
            assert store.path_cache_size() == 1


class TestExtractedContent:
    """提取器结果缓存：get/put_extracted_content（iter-39）。"""

    def test_put_and_get_roundtrip(self, tmp_path: Path) -> None:
        """写入提取内容后能查回。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"doc-bytes")
            store.register_file(file_hash, 100)
            store.put_extracted_content(file_hash, "提取的文本", "docx")

            assert store.get_extracted_content(file_hash) == "提取的文本"

    def test_get_returns_none_when_not_cached(self, tmp_path: Path) -> None:
        """未缓存的 file_hash 返回 None。"""
        with CacheStore(tmp_path / "c.db") as store:
            assert store.get_extracted_content(hash_bytes(b"unknown")) is None

    def test_put_empty_content_skipped(self, tmp_path: Path) -> None:
        """空内容不缓存（避免哨兵值污染）。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"empty")
            store.register_file(file_hash, 0)
            store.put_extracted_content(file_hash, "", "txt")
            assert store.get_extracted_content(file_hash) is None

    def test_put_overwrites_on_conflict(self, tmp_path: Path) -> None:
        """同一 file_hash 重复写入时覆盖旧内容。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"content")
            store.register_file(file_hash, 100)
            store.put_extracted_content(file_hash, "v1", "docx")
            store.put_extracted_content(file_hash, "v2", "docx")
            assert store.get_extracted_content(file_hash) == "v2"

    def test_put_creates_scanned_files_placeholder_if_missing(self, tmp_path: Path) -> None:
        """put 时 scanned_files 无该 file_hash 会自动占位插入（满足外键约束）。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"new")
            # 不调 register_file，直接 put_extracted_content
            store.put_extracted_content(file_hash, "内容", "docx")
            assert store.get_extracted_content(file_hash) == "内容"
            # stats 应反映 scanned_files 有 1 条
            assert store.stats().scanned_files == 1

    def test_extracted_contents_counted_in_stats(self, tmp_path: Path) -> None:
        """stats 应统计 extracted_contents 表行数。"""
        with CacheStore(tmp_path / "c.db") as store:
            fh1 = hash_bytes(b"a")
            fh2 = hash_bytes(b"b")
            store.register_file(fh1, 10)
            store.register_file(fh2, 20)
            store.put_extracted_content(fh1, "content-a", "docx")
            store.put_extracted_content(fh2, "content-b", "pptx")
            stats = store.stats()
            assert stats.extracted_contents == 2

    def test_cascade_delete_when_scanned_file_deleted(self, tmp_path: Path) -> None:
        """scanned_files 删除时 extracted_contents 级联删除。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"to-delete")
            store.register_file(file_hash, 100)
            store.put_extracted_content(file_hash, "内容", "docx")
            assert store.get_extracted_content(file_hash) is not None
            # 直接删除 scanned_files 记录（模拟 prune_stale_files）
            store._conn.execute("DELETE FROM scanned_files WHERE file_hash = ?", (file_hash,))
            # extracted_contents 应被级联删除
            assert store.get_extracted_content(file_hash) is None


# ---------------------------------------------------------------- 批量写入


def _make_hit(detail: str = "d", match_count: int = 1) -> RuleHit:
    """构造测试用 RuleHit（避免每个测试重复样板）。"""
    return RuleHit(
        rule_name="r1",
        severity=Severity.WARNING,
        detail=detail,
        match_text="m",
        match_count=match_count,
        target="filename",
    )


def _register_rule(store: CacheStore, rule: Rule | None = None) -> str:
    """注册一条测试规则到 store，返回其 rule_hash。

    ``scan_results.rule_hash`` 有外键约束指向 ``rules(rule_hash)``，
    测试批量写入前需先注册规则。
    """
    rule = rule or _filename_rule()
    rs = RuleSet(version="1.0", rules=(rule,))
    return store.register_ruleset(rs)[rule.name]


class TestBatchPutResults:
    """批量写入接口 batch_put_results（iter-39 P2）。"""

    def test_empty_items_is_noop(self, tmp_path: Path) -> None:
        """空列表直接返回，不触发任何写入。"""
        with CacheStore(tmp_path / "c.db") as store:
            store.batch_put_results([])
            stats = store.stats()
            assert stats.scanned_files == 0
            assert stats.file_paths == 0
            assert stats.scan_results == 0

    def test_single_item_writes_all_three_tables(self, tmp_path: Path) -> None:
        """单条写入：scanned_files、file_paths、scan_results 三表同时更新。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            file_hash = hash_bytes(b"doc")
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=100,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, _make_hit()),),
                    ),
                ]
            )
            stats = store.stats()
            assert stats.scanned_files == 1
            assert stats.file_paths == 1
            assert stats.scan_results == 1
            # 内容正确性
            cached = store.get_cached_hits(file_hash, [rule_hash])
            assert rule_hash in cached
            assert cached[rule_hash] is not None
            assert cached[rule_hash].detail == "d"  # pyrefly: ignore [missing-attribute]
            # file_paths 已登记，lookup_file_hash 应命中
            assert store.lookup_file_hash(tmp_path / "a.txt", 1.0, 100) == file_hash

    def test_multiple_items_single_transaction(self, tmp_path: Path) -> None:
        """多条 BatchWriteItem 一次性写入（同一条规则匹配多个文件）。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            items = [
                BatchWriteItem(
                    file_hash=hash_bytes(f"doc{i}".encode()),
                    size=i * 10,
                    path=tmp_path / f"f{i}.txt",
                    mtime=float(i),
                    hits=((rule_hash, _make_hit(detail=f"d{i}")),),
                )
                for i in range(5)
            ]
            store.batch_put_results(items)
            stats = store.stats()
            assert stats.scanned_files == 5
            assert stats.file_paths == 5
            assert stats.scan_results == 5
            for i in range(5):
                assert store.lookup_file_hash(tmp_path / f"f{i}.txt", float(i), i * 10) is not None

    def test_none_hit_means_no_match_cached(self, tmp_path: Path) -> None:
        """``(rule_hash, None)`` 表示该规则未命中且已缓存（避免重复扫描）。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            file_hash = hash_bytes(b"doc")
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=10,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, None),),
                    ),
                ]
            )
            cached = store.get_cached_hits(file_hash, [rule_hash])
            # None 在缓存里：表示该规则已扫描且未命中
            assert cached == {rule_hash: None}

    def test_mixed_hit_and_none(self, tmp_path: Path) -> None:
        """同一文件多规则：部分命中部分未命中。"""
        with CacheStore(tmp_path / "c.db") as store:
            # 注册两条规则
            rs = RuleSet(version="1.0", rules=(_filename_rule(name="r1"), _filename_rule(name="r2")))
            rule_hashes = store.register_ruleset(rs)
            r1, r2 = rule_hashes["r1"], rule_hashes["r2"]
            file_hash = hash_bytes(b"doc")
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=10,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((r1, _make_hit(detail="hit")), (r2, None)),
                    ),
                ]
            )
            cached = store.get_cached_hits(file_hash, [r1, r2])
            assert cached[r1] is not None
            assert cached[r1].detail == "hit"  # pyrefly: ignore [missing-attribute]
            assert cached[r2] is None

    def test_empty_hits_only_refreshes_metadata(self, tmp_path: Path) -> None:
        """``hits=()`` 时仅刷新 scanned_files/file_paths，scan_results 不写入。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"doc")
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=10,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=(),
                    ),
                ]
            )
            stats = store.stats()
            assert stats.scanned_files == 1
            assert stats.file_paths == 1
            assert stats.scan_results == 0  # 没有结果写入

    def test_upsert_overwrites_existing(self, tmp_path: Path) -> None:
        """重复写入相同 (file_hash, rule_hash) 时覆盖旧值。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            file_hash = hash_bytes(b"doc")
            # 第一次：命中
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=10,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, _make_hit(detail="v1")),),
                    ),
                ]
            )
            # 第二次：未命中（覆盖）
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=10,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, None),),
                    ),
                ]
            )
            cached = store.get_cached_hits(file_hash, [rule_hash])
            assert cached == {rule_hash: None}

    def test_size_zero_does_not_overwrite_existing_size(self, tmp_path: Path) -> None:
        """``size=0`` 不覆盖已登记的真实 size（与 _register_file_locked 语义一致）。"""
        with CacheStore(tmp_path / "c.db") as store:
            file_hash = hash_bytes(b"doc")
            # 先登记真实 size=500
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=500,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=(),
                    ),
                ]
            )
            # 后续 size=0 不应覆盖
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=0,
                        path=tmp_path / "a.txt",
                        mtime=2.0,
                        hits=(),
                    ),
                ]
            )
            # lookup_file_hash 按 size 匹配，应仍为 500
            assert store.lookup_file_hash(tmp_path / "a.txt", 2.0, 500) == file_hash

    def test_invalidates_lru_cache(self, tmp_path: Path) -> None:
        """写入后 LRU 中对应 file_hash 的条目失效，下次查询走 SQLite 取最新。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            file_hash = hash_bytes(b"doc")
            # 先用 put_result 写入并触发 LRU 缓存
            store.put_result(file_hash, rule_hash, _make_hit(detail="v1"))
            # 触发 LRU 加载
            cached1 = store.get_cached_hits(file_hash, [rule_hash])
            assert cached1[rule_hash].detail == "v1"  # pyrefly: ignore [missing-attribute]
            # 批量写入覆盖
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=0,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, _make_hit(detail="v2")),),
                    ),
                ]
            )
            # LRU 应已失效，下次查询从 SQLite 取最新
            cached2 = store.get_cached_hits(file_hash, [rule_hash])
            assert cached2[rule_hash].detail == "v2"  # pyrefly: ignore [missing-attribute]

    def test_same_file_hash_multiple_paths(self, tmp_path: Path) -> None:
        """同内容不同路径：两个 BatchWriteItem 共享 file_hash。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            file_hash = hash_bytes(b"same-content")
            store.batch_put_results(
                [
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=10,
                        path=tmp_path / "a.txt",
                        mtime=1.0,
                        hits=((rule_hash, _make_hit()),),
                    ),
                    BatchWriteItem(
                        file_hash=file_hash,
                        size=10,
                        path=tmp_path / "b.txt",
                        mtime=2.0,
                        hits=((rule_hash, _make_hit()),),
                    ),
                ]
            )
            stats = store.stats()
            assert stats.scanned_files == 1  # 同 file_hash 去重
            assert stats.file_paths == 2  # 两个不同路径
            assert stats.scan_results == 1  # UPSERT 不重复

    def test_transaction_rollback_on_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """异常时整批 ROLLBACK，已写入数据不受影响。"""
        with CacheStore(tmp_path / "c.db") as store:
            rule_hash = _register_rule(store)
            # 预先写入一条
            existing_hash = hash_bytes(b"existing")
            store.put_result(existing_hash, rule_hash, _make_hit(detail="old"))
            assert store.stats().scan_results == 1
            # 包装 connection，使第 3 次 executemany 调用抛异常（scan_results 写入阶段）
            real_conn = store._conn
            original_executemany = real_conn.executemany
            call_count = {"n": 0}

            class _FailingConn:
                """代理 sqlite3.Connection，仅覆盖 executemany；其余属性转发到原连接。"""

                def __getattr__(self, name: str) -> Any:
                    return getattr(real_conn, name)

                def executemany(self, sql: str, params: Any) -> object:
                    call_count["n"] += 1
                    if call_count["n"] == 3:
                        raise sqlite3.OperationalError("模拟写入失败")
                    return original_executemany(sql, params)

            monkeypatch.setattr(store, "_conn", _FailingConn())
            with pytest.raises(sqlite3.OperationalError, match="模拟写入失败"):
                store.batch_put_results(
                    [
                        BatchWriteItem(
                            file_hash=hash_bytes(b"new1"),
                            size=10,
                            path=tmp_path / "a.txt",
                            mtime=1.0,
                            hits=((rule_hash, _make_hit()),),
                        ),
                    ]
                )
            # ROLLBACK 后 scan_results 仍只有原 1 条
            assert store.stats().scan_results == 1
            assert store.stats().scanned_files == 1  # 原有 1 条
            assert store.stats().file_paths == 0  # 新增的 a.txt 被回滚
