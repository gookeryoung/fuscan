"""误报白名单（``fuscan.rules.whitelist``）单元测试。

覆盖：

- :class:`WhitelistEntry`：构造、glob 匹配、规则名通配、序列化
- :class:`Whitelist`：集合匹配、``matches_any_rule``、JSON 序列化反序列化
- :class:`WhitelistStore`：增删查改、原子持久化、线程安全快照、导入导出
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from fuscan.rules.whitelist import (
    Whitelist,
    WhitelistEntry,
    WhitelistStore,
    default_whitelist_path,
)

# --------------------------------------------------------------------------- #
# WhitelistEntry
# --------------------------------------------------------------------------- #


class TestWhitelistEntry:
    def test_default_path_under_home(self) -> None:
        """默认路径位于 ~/.fuscan/whitelist.json。"""
        assert default_whitelist_path() == Path.home() / ".fuscan" / "whitelist.json"

    def test_construct_with_explicit_fields(self) -> None:
        """显式字段构造。"""
        entry = WhitelistEntry(path_glob="/a/b.txt", rule_name="r1", created_at="2026-07-29", note="误报")
        assert entry.path_glob == "/a/b.txt"
        assert entry.rule_name == "r1"
        assert entry.created_at == "2026-07-29"
        assert entry.note == "误报"

    def test_empty_rule_name_normalized_to_wildcard(self) -> None:
        """空规则名归一化为 *。"""
        entry = WhitelistEntry(path_glob="/a", rule_name="")
        assert entry.rule_name == "*"

    def test_empty_path_glob_raises(self) -> None:
        """空 path_glob 抛 ValueError。"""
        with pytest.raises(ValueError, match="path_glob"):
            WhitelistEntry(path_glob="", rule_name="*")

    @pytest.mark.parametrize(
        ("path_glob", "rule_name", "path_str", "query_rule", "expected"),
        [
            # 精确路径 + 精确规则
            ("/a/b.txt", "r1", "/a/b.txt", "r1", True),
            # 精确路径 + 通配规则
            ("/a/b.txt", "*", "/a/b.txt", "any_rule", True),
            # 路径不匹配
            ("/a/b.txt", "r1", "/a/c.txt", "r1", False),
            # 规则不匹配
            ("/a/b.txt", "r1", "/a/b.txt", "r2", False),
            # glob 通配符
            ("/a/*.txt", "r1", "/a/c.txt", "r1", True),
            ("/a/*.txt", "r1", "/a/c.md", "r1", False),
            # 目录前缀
            ("/a/vendor/*", "*", "/a/vendor/x/y.txt", "any", True),
            ("/a/vendor/*", "*", "/a/other/x.txt", "any", False),
        ],
        ids=[
            "exact_path_exact_rule",
            "exact_path_wildcard_rule",
            "path_mismatch",
            "rule_mismatch",
            "glob_match",
            "glob_no_match",
            "dir_prefix_match",
            "dir_prefix_no_match",
        ],
    )
    def test_matches(
        self,
        path_glob: str,
        rule_name: str,
        path_str: str,
        query_rule: str,
        expected: bool,
    ) -> None:
        """matches 在多种 glob/规则组合下返回正确结果。"""
        entry = WhitelistEntry(path_glob=path_glob, rule_name=rule_name)
        assert entry.matches(path_str, query_rule) is expected

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        """to_dict/from_dict 往返一致。"""
        original = WhitelistEntry(path_glob="/a/b", rule_name="r1", created_at="2026-07-29", note="备注")
        data = original.to_dict()
        restored = WhitelistEntry.from_dict(data)
        assert restored == original

    def test_from_dict_tolerates_missing_fields(self) -> None:
        """from_dict 容忍缺失字段（向后兼容）。"""
        entry = WhitelistEntry.from_dict({"path_glob": "/a"})
        assert entry.path_glob == "/a"
        assert entry.rule_name == "*"
        assert entry.created_at == ""
        assert entry.note == ""

    def test_from_dict_skips_invalid_path(self) -> None:
        """from_dict 接受空 path_glob，但 Whitelist.from_json 跳过无效条目。"""
        # 单独 from_dict 空 path_glob 抛 ValueError
        with pytest.raises(ValueError):
            WhitelistEntry.from_dict({"path_glob": ""})

    def test_frozen_dataclass_is_hashable(self) -> None:
        """frozen dataclass 可哈希（可作 set/dict key）。"""
        entry = WhitelistEntry(path_glob="/a", rule_name="r1")
        assert hash(entry) is not None
        assert {entry}  # 可加入集合


# --------------------------------------------------------------------------- #
# Whitelist
# --------------------------------------------------------------------------- #


class TestWhitelist:
    def test_empty_whitelist_matches_nothing(self) -> None:
        """空 Whitelist 任何查询都返回 False。"""
        wl = Whitelist()
        assert wl.matches(Path("/a"), "r1") is False
        assert wl.matches_any_rule(Path("/a"), ("r1", "r2")) is False

    def test_matches_any_rule_requires_all_rules_covered(self) -> None:
        """matches_any_rule 仅在所有规则都命中白名单时返回 True。"""
        wl = Whitelist(
            entries=(
                WhitelistEntry(path_glob="/a.txt", rule_name="r1"),
                WhitelistEntry(path_glob="/a.txt", rule_name="r2"),
            )
        )
        # 两条规则都被覆盖
        assert wl.matches_any_rule(Path("/a.txt"), ("r1", "r2")) is True
        # 仅 r1 被覆盖，r2 未覆盖 → False
        assert wl.matches_any_rule(Path("/a.txt"), ("r1", "r3")) is False

    def test_matches_any_rule_wildcard_covers_all(self) -> None:
        """rule_name=* 的条目覆盖任意规则。"""
        wl = Whitelist(entries=(WhitelistEntry(path_glob="/a.txt", rule_name="*"),))
        assert wl.matches_any_rule(Path("/a.txt"), ("r1", "r2", "r3")) is True

    def test_matches_any_rule_empty_rule_names_returns_false(self) -> None:
        """rule_names 为空元组时返回 False。"""
        wl = Whitelist(entries=(WhitelistEntry(path_glob="/a.txt", rule_name="*"),))
        assert wl.matches_any_rule(Path("/a.txt"), ()) is False

    def test_to_json_and_from_json_roundtrip(self) -> None:
        """to_json/from_json 往返一致。"""
        wl = Whitelist(
            entries=(
                WhitelistEntry(path_glob="/a", rule_name="r1", created_at="2026-07-29", note="n1"),
                WhitelistEntry(path_glob="/b/*", rule_name="*", created_at="2026-07-30", note=""),
            )
        )
        json_str = wl.to_json()
        restored = Whitelist.from_json(json_str)
        assert restored.entries == wl.entries

    def test_from_json_skips_invalid_entries(self) -> None:
        """from_json 跳过无效条目（path_glob 空），不抛异常。"""
        # 混入无效条目
        payload = json.dumps(
            [
                {"path_glob": "/valid", "rule_name": "r1"},
                {"path_glob": "", "rule_name": "*"},  # 无效
                "not_a_dict",  # 无效
                {"path_glob": "/valid2", "rule_name": "*"},
            ]
        )
        wl = Whitelist.from_json(payload)
        assert len(wl.entries) == 2
        assert wl.entries[0].path_glob == "/valid"
        assert wl.entries[1].path_glob == "/valid2"

    def test_from_json_invalid_top_level_raises(self) -> None:
        """JSON 顶层非列表抛 ValueError。"""
        with pytest.raises(ValueError, match="顶层"):
            Whitelist.from_json(json.dumps({"not": "a list"}))


# --------------------------------------------------------------------------- #
# WhitelistStore
# --------------------------------------------------------------------------- #


class TestWhitelistStoreBasic:
    def test_empty_when_file_missing(self, tmp_path: Path) -> None:
        """文件不存在时按空集初始化。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        assert store.entries() == ()
        assert store.snapshot().entries == ()

    def test_add_persists_to_disk(self, tmp_path: Path) -> None:
        """add 后立即写回磁盘。"""
        path = tmp_path / "whitelist.json"
        store = WhitelistStore(path)
        entry = WhitelistEntry(path_glob="/a/b.txt", rule_name="r1", created_at="2026-07-29", note="误报")
        store.add(entry)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == [entry.to_dict()]

    def test_add_idempotent_for_duplicate(self, tmp_path: Path) -> None:
        """重复 add 相同 (path_glob, rule_name) 不产生重复。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        entry = WhitelistEntry(path_glob="/a", rule_name="r1")
        store.add(entry)
        store.add(entry)
        assert len(store.entries()) == 1

    def test_remove_by_glob_and_rule(self, tmp_path: Path) -> None:
        """remove 按 (path_glob, rule_name) 移除。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        store.add(WhitelistEntry(path_glob="/a", rule_name="r1"))
        store.add(WhitelistEntry(path_glob="/b", rule_name="r2"))
        store.remove("/a", "r1")
        assert len(store.entries()) == 1
        assert store.entries()[0].path_glob == "/b"

    def test_remove_missing_no_error(self, tmp_path: Path) -> None:
        """remove 不存在的条目不报错。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        store.remove("/never", "r1")
        assert store.entries() == ()

    def test_remove_at_valid_index(self, tmp_path: Path) -> None:
        """remove_at 有效索引移除并返回 True。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        store.add(WhitelistEntry(path_glob="/a", rule_name="r1"))
        store.add(WhitelistEntry(path_glob="/b", rule_name="r2"))
        assert store.remove_at(0) is True
        assert len(store.entries()) == 1
        assert store.entries()[0].path_glob == "/b"

    def test_remove_at_out_of_range_returns_false(self, tmp_path: Path) -> None:
        """remove_at 越界返回 False。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        assert store.remove_at(0) is False
        assert store.remove_at(-1) is False

    def test_clear_writes_empty_list(self, tmp_path: Path) -> None:
        """clear 后磁盘写入空列表。"""
        path = tmp_path / "whitelist.json"
        store = WhitelistStore(path)
        store.add(WhitelistEntry(path_glob="/a", rule_name="r1"))
        store.clear()
        assert store.entries() == ()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == []

    def test_clear_empty_store_no_write(self, tmp_path: Path) -> None:
        """clear 空存储不触发写盘。"""
        path = tmp_path / "whitelist.json"
        store = WhitelistStore(path)
        store.clear()
        assert not path.exists()


class TestWhitelistStorePersistence:
    def test_reload_after_add(self, tmp_path: Path) -> None:
        """新增条目后重新构造 store 应加载到相同条目集。"""
        path = tmp_path / "whitelist.json"
        store = WhitelistStore(path)
        store.add(WhitelistEntry(path_glob="/a", rule_name="r1"))
        store.add(WhitelistEntry(path_glob="/b", rule_name="r2"))
        reloaded = WhitelistStore(path)
        assert len(reloaded.entries()) == 2
        assert {e.path_glob for e in reloaded.entries()} == {"/a", "/b"}

    def test_load_tolerates_corrupt_file(self, tmp_path: Path) -> None:
        """文件损坏时按空集初始化（不抛异常）。"""
        path = tmp_path / "whitelist.json"
        path.write_text("{not valid json", encoding="utf-8")
        store = WhitelistStore(path)
        assert store.entries() == ()

    def test_load_tolerates_non_list_json(self, tmp_path: Path) -> None:
        """JSON 顶层非列表时按空集初始化。"""
        path = tmp_path / "whitelist.json"
        path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        store = WhitelistStore(path)
        assert store.entries() == ()

    def test_snapshot_is_isolated_from_store_mutations(self, tmp_path: Path) -> None:
        """snapshot 返回的 Whitelist 不受后续增删影响。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        store.add(WhitelistEntry(path_glob="/a", rule_name="r1"))
        snapshot = store.snapshot()
        store.add(WhitelistEntry(path_glob="/b", rule_name="r2"))
        # 快照保持原样
        assert len(snapshot.entries) == 1
        assert snapshot.entries[0].path_glob == "/a"
        # store 已有 2 条
        assert len(store.entries()) == 2


class TestWhitelistStoreImportExport:
    def test_export_json_returns_json_string(self, tmp_path: Path) -> None:
        """export_json 返回 JSON 字符串。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        store.add(WhitelistEntry(path_glob="/a", rule_name="r1", created_at="2026-07-29"))
        exported = store.export_json()
        data = json.loads(exported)
        assert data == [{"path_glob": "/a", "rule_name": "r1", "created_at": "2026-07-29", "note": ""}]

    def test_import_json_merges_and_dedupes(self, tmp_path: Path) -> None:
        """import_json 合并到现有条目并去重。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        store.add(WhitelistEntry(path_glob="/a", rule_name="r1"))
        # 导入包含 /a/r1（重复）+ /b/r2（新增）
        payload = json.dumps(
            [
                {"path_glob": "/a", "rule_name": "r1"},
                {"path_glob": "/b", "rule_name": "r2"},
            ]
        )
        added = store.import_json(payload)
        assert added == 1  # 仅 /b/r2 新增
        assert len(store.entries()) == 2

    def test_import_json_all_duplicates_returns_zero(self, tmp_path: Path) -> None:
        """导入全部重复条目返回 0。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        store.add(WhitelistEntry(path_glob="/a", rule_name="r1"))
        payload = json.dumps([{"path_glob": "/a", "rule_name": "r1"}])
        assert store.import_json(payload) == 0
        assert len(store.entries()) == 1


class TestWhitelistStoreThreadSafety:
    def test_concurrent_adds_no_lost_entries(self, tmp_path: Path) -> None:
        """并发 add 不丢失条目（RLock 保护）。"""
        store = WhitelistStore(tmp_path / "whitelist.json")
        n_threads = 8
        per_thread = 50

        def worker(tid: int) -> None:
            """每个线程添加 per_thread 条不同条目。"""
            for i in range(per_thread):
                store.add(WhitelistEntry(path_glob=f"/t{tid}/f{i}", rule_name="r"))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(store.entries()) == n_threads * per_thread
