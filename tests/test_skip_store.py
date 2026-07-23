"""``SkipStore`` 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

from fuscan.skip_store import SkipStore, default_skip_store_path


class TestSkipStoreBasic:
    def test_default_path_under_home(self) -> None:
        """默认路径位于 ~/.fuscan/skips.json。"""
        path = default_skip_store_path()
        assert path == Path.home() / ".fuscan" / "skips.json"

    def test_empty_when_file_missing(self, tmp_path: Path) -> None:
        """文件不存在时按空集初始化。"""
        store = SkipStore(tmp_path / "skips.json")
        assert store.paths() == frozenset()
        assert store.contains("/x/y") is False

    def test_add_persists_and_contains(self, tmp_path: Path) -> None:
        """add 后 contains 返回 True 并写回磁盘。"""
        path = tmp_path / "skips.json"
        store = SkipStore(path)
        store.add("/a/b.txt")
        assert store.contains("/a/b.txt") is True
        # 文件已写入磁盘
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == ["/a/b.txt"]

    def test_add_idempotent(self, tmp_path: Path) -> None:
        """重复 add 同一路径不产生重复项。"""
        store = SkipStore(tmp_path / "skips.json")
        store.add("/a")
        store.add("/a")
        assert store.paths() == frozenset({"/a"})

    def test_remove(self, tmp_path: Path) -> None:
        """remove 后不再 contains 并写回磁盘。"""
        store = SkipStore(tmp_path / "skips.json")
        store.add("/a")
        store.add("/b")
        store.remove("/a")
        assert store.contains("/a") is False
        assert store.contains("/b") is True
        assert store.paths() == frozenset({"/b"})

    def test_remove_missing_no_error(self, tmp_path: Path) -> None:
        """remove 不存在的路径不报错。"""
        store = SkipStore(tmp_path / "skips.json")
        store.remove("/never")
        assert store.paths() == frozenset()

    def test_clear(self, tmp_path: Path) -> None:
        """clear 清空全部路径。"""
        store = SkipStore(tmp_path / "skips.json")
        store.add("/a")
        store.add("/b")
        store.clear()
        assert store.paths() == frozenset()
        # 空集写回磁盘为空列表
        data = json.loads((tmp_path / "skips.json").read_text(encoding="utf-8"))
        assert data == []


class TestSkipStorePersistence:
    def test_reload_after_add(self, tmp_path: Path) -> None:
        """新增路径后重新构造 SkipStore 应加载到相同集合。"""
        path = tmp_path / "skips.json"
        store = SkipStore(path)
        store.add("/a")
        store.add("/b")
        store.add("/c")
        # 重新加载
        reloaded = SkipStore(path)
        assert reloaded.paths() == frozenset({"/a", "/b", "/c"})

    def test_paths_returns_immutable_snapshot(self, tmp_path: Path) -> None:
        """paths 返回 frozenset 快照，后续增删不影响已返回的快照。"""
        store = SkipStore(tmp_path / "skips.json")
        store.add("/a")
        snapshot = store.paths()
        store.add("/b")
        # 快照保持添加 /b 之前的状态
        assert snapshot == frozenset({"/a"})
        assert store.paths() == frozenset({"/a", "/b"})

    def test_corrupt_file_treated_as_empty(self, tmp_path: Path) -> None:
        """损坏的 JSON 文件按空集处理并记录警告，不抛异常。"""
        path = tmp_path / "skips.json"
        path.write_text("{not valid json", encoding="utf-8")
        store = SkipStore(path)
        assert store.paths() == frozenset()

    def test_non_list_structure_treated_as_empty(self, tmp_path: Path) -> None:
        """非列表 JSON 结构按空集处理。"""
        path = tmp_path / "skips.json"
        path.write_text('{"paths": ["/a"]}', encoding="utf-8")
        store = SkipStore(path)
        assert store.paths() == frozenset()

    def test_non_string_items_filtered(self, tmp_path: Path) -> None:
        """列表中的非字符串项被过滤。"""
        path = tmp_path / "skips.json"
        path.write_text('["/a", 123, null, "/b"]', encoding="utf-8")
        store = SkipStore(path)
        assert store.paths() == frozenset({"/a", "/b"})

    def test_parent_dir_auto_created(self, tmp_path: Path) -> None:
        """父目录不存在时自动创建。"""
        path = tmp_path / "nested" / "deep" / "skips.json"
        store = SkipStore(path)
        store.add("/a")
        assert path.exists()
        assert store.contains("/a") is True
