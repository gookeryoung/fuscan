"""扫描历史归档与对比模块测试（iter-115）。

覆盖：

- :class:`ScanHistoryEntry`：序列化/反序列化、容错、字段默认值
- :class:`HistoryStore`：增删查改、原子写入、容量限制、线程安全、容错加载
- :func:`compare_scans`：首次扫描、新增/已解决/持续命中、规则变化、趋势判断
- :class:`ScanComparison`：``summary``/``trend``/``is_first_scan`` 属性
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from fuscan.history import (
    HistoryStore,
    ScanHistoryEntry,
    compare_scans,
    default_history_store_path,
)
from fuscan.history.model import STATUS_COMPLETED


def _make_entry(
    *,
    scan_id: str = "scan-1",
    workspace_id: str = "ws-1",
    workspace_name: str = "任务 1",
    finished_at: str = "2026-07-27T10:00:00Z",
    status: str = STATUS_COMPLETED,
    matched_files: int = 0,
    hit_paths: tuple[str, ...] = (),
    rule_names: tuple[str, ...] = (),
) -> ScanHistoryEntry:
    """构造测试用 :class:`ScanHistoryEntry`。"""
    return ScanHistoryEntry(
        scan_id=scan_id,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        finished_at=finished_at,
        started_at="2026-07-27T09:55:00Z",
        status=status,
        matched_files=matched_files,
        hit_paths=hit_paths,
        rule_names=rule_names,
    )


class TestScanHistoryEntry:
    """``ScanHistoryEntry`` 数据模型测试。"""

    def test_default_values(self) -> None:
        entry = ScanHistoryEntry()
        assert entry.status == STATUS_COMPLETED
        assert entry.hit_paths == ()
        assert entry.rule_names == ()
        assert entry.matched_files == 0
        assert entry.scan_id  # 自动生成
        assert entry.started_at  # 自动生成
        assert entry.finished_at  # 自动生成

    def test_to_dict_roundtrip(self) -> None:
        entry = _make_entry(
            hit_paths=("/a/b.txt", "/c/d.txt"),
            rule_names=("rule1", "rule2"),
            matched_files=2,
        )
        d = entry.to_dict()
        # 序列化字段完整
        assert d["scan_id"] == "scan-1"
        assert d["hit_paths"] == ["/a/b.txt", "/c/d.txt"]
        assert d["rule_names"] == ["rule1", "rule2"]
        assert d["matched_files"] == 2
        # 反序列化后应与原对象相等
        restored = ScanHistoryEntry.from_dict(d)
        assert restored == entry

    def test_from_dict_non_dict_returns_default(self) -> None:
        # 非 dict 输入返回默认实例（不抛异常）
        restored = ScanHistoryEntry.from_dict("not a dict")  # pyrefly: ignore [wrong-argument-type]
        assert restored.workspace_id == ""
        assert restored.matched_files == 0
        assert restored.status == STATUS_COMPLETED

    def test_from_dict_coerces_types(self) -> None:
        # 类型不符字段回退到默认值，不抛异常
        raw = {
            "scan_id": 12345,  # 非字符串
            "matched_files": "10",  # 非整数
            "duration_seconds": "1.5",  # 非浮点
            "hit_paths": "should-be-list",  # 非列表
            "status": "unknown-status",  # 非法状态
            "total_files": True,  # bool 不视为 int
        }
        restored = ScanHistoryEntry.from_dict(raw)
        assert restored.scan_id  # 自动生成新 ID
        assert restored.matched_files == 0
        assert restored.duration_seconds == 0.0
        assert restored.hit_paths == ()
        assert restored.status == STATUS_COMPLETED  # 非法状态回退
        assert restored.total_files == 0  # bool 不视为 int

    def test_from_dict_partial(self) -> None:
        # 缺失字段使用默认值
        restored = ScanHistoryEntry.from_dict({"scan_id": "x"})
        assert restored.scan_id == "x"
        assert restored.workspace_id == ""
        assert restored.matched_files == 0

    def test_frozen_dataclass(self) -> None:
        entry = _make_entry()
        with pytest.raises(AttributeError):
            entry.matched_files = 100  # pyrefly: ignore [misc]


class TestHistoryStore:
    """``HistoryStore`` 持久化存储测试。"""

    def test_default_path_under_config_dir(self) -> None:
        path = default_history_store_path()
        assert path.name == "history.json"
        assert path.parent.name == ".fuscan"

    def test_add_and_query(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.json")
        entry1 = _make_entry(scan_id="s1", finished_at="2026-07-27T10:00:00Z")
        entry2 = _make_entry(scan_id="s2", finished_at="2026-07-27T11:00:00Z")
        store.add(entry1)
        store.add(entry2)

        # 倒序返回（最新在前）
        history = store.workspace_history("ws-1")
        assert [e.scan_id for e in history] == ["s2", "s1"]

        # limit 参数
        assert len(store.workspace_history("ws-1", limit=1)) == 1
        # 无历史工作区
        assert store.workspace_history("ws-other") == ()

    def test_latest_and_previous_entry(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.json")
        entry1 = _make_entry(scan_id="s1", finished_at="2026-07-27T10:00:00Z")
        entry2 = _make_entry(scan_id="s2", finished_at="2026-07-27T11:00:00Z")
        store.add(entry1)
        store.add(entry2)

        assert store.latest_entry("ws-1").scan_id == "s2"
        # previous_entry 排除当前 scan_id 后返回最新一条
        assert store.previous_entry("ws-1", "s2").scan_id == "s1"
        # 无更早历史返回 None
        assert store.previous_entry("ws-1", "s1") is None
        # 无任何历史返回 None
        assert store.latest_entry("ws-other") is None

    def test_add_dedup_same_scan_id(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.json")
        store.add(_make_entry(scan_id="s1", matched_files=1))
        # 同 scan_id 覆盖
        store.add(_make_entry(scan_id="s1", matched_files=99))
        history = store.workspace_history("ws-1")
        assert len(history) == 1
        assert history[0].matched_files == 99

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        store = HistoryStore(path=path)
        store.add(_make_entry(scan_id="s1"))
        # 重新加载应能读到
        store2 = HistoryStore(path=path)
        assert len(store2.workspace_history("ws-1")) == 1

    def test_clear_workspace(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.json")
        store.add(_make_entry(scan_id="s1", workspace_id="ws-1"))
        store.add(_make_entry(scan_id="s2", workspace_id="ws-2"))
        removed = store.clear_workspace("ws-1")
        assert removed == 1
        assert store.workspace_history("ws-1") == ()
        assert len(store.workspace_history("ws-2")) == 1
        # 清空不存在的工作区返回 0
        assert store.clear_workspace("ws-other") == 0

    def test_clear_all(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.json")
        store.add(_make_entry(scan_id="s1"))
        store.add(_make_entry(scan_id="s2", workspace_id="ws-2"))
        removed = store.clear_all()
        assert removed == 2
        assert store.all_entries() == ()

    def test_max_entries_per_workspace_trim(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.json", max_entries_per_workspace=3)
        # 添加 5 条，应仅保留最新 3 条
        for i in range(5):
            store.add(
                _make_entry(
                    scan_id=f"s{i}",
                    finished_at=f"2026-07-2{i}T10:00:00Z",
                )
            )
        history = store.workspace_history("ws-1")
        assert len(history) == 3
        # 保留最新的 s4/s3/s2
        assert [e.scan_id for e in history] == ["s4", "s3", "s2"]

    def test_corrupted_file_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        path.write_text("not a json {{{", encoding="utf-8")
        store = HistoryStore(path=path)
        assert store.all_entries() == ()

    def test_incompatible_version_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        path.write_text(
            json.dumps({"version": 999, "entries": []}),
            encoding="utf-8",
        )
        store = HistoryStore(path=path)
        assert store.all_entries() == ()

    def test_load_skips_corrupted_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        # 一条正常 + 一条异常（非 dict）
        payload = {
            "version": 1,
            "entries": [
                _make_entry(scan_id="s1").to_dict(),
                "corrupted entry",
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        store = HistoryStore(path=path)
        # 异常条目被跳过，正常的保留
        assert len(store.all_entries()) == 1

    def test_thread_safety(self, tmp_path: Path) -> None:
        """多线程并发 add 不丢失数据。"""
        store = HistoryStore(path=tmp_path / "history.json")

        def worker(thread_id: int) -> None:
            for i in range(20):
                store.add(
                    _make_entry(
                        scan_id=f"t{thread_id}-s{i}",
                        workspace_id=f"ws-{thread_id}",
                        finished_at=f"2026-07-2{i}T10:00:00Z",
                    )
                )

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 每个工作区应有 20 条记录（max=50 不裁剪）
        for thread_id in range(4):
            assert len(store.workspace_history(f"ws-{thread_id}")) == 20

    def test_all_entries_returns_snapshot(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.json")
        store.add(_make_entry(scan_id="s1"))
        snapshot = store.all_entries()
        # 修改原存储不影响快照
        store.add(_make_entry(scan_id="s2"))
        assert len(snapshot) == 1


class TestCompareScans:
    """``compare_scans`` 对比逻辑测试。"""

    def test_first_scan_all_new(self) -> None:
        current = _make_entry(
            scan_id="s1",
            matched_files=3,
            hit_paths=("/a", "/b", "/c"),
            rule_names=("rule1", "rule2"),
        )
        comparison = compare_scans(current, None)
        assert comparison.is_first_scan is True
        assert comparison.trend == "首次"
        assert set(comparison.new_hits) == {"/a", "/b", "/c"}
        assert comparison.resolved_hits == ()
        assert comparison.persistent_hits == ()
        assert comparison.matched_delta == 3
        assert set(comparison.new_rules) == {"rule1", "rule2"}
        assert comparison.dropped_rules == ()

    def test_second_scan_improved(self) -> None:
        previous = _make_entry(
            scan_id="s1",
            matched_files=3,
            hit_paths=("/a", "/b", "/c"),
            rule_names=("rule1", "rule2"),
        )
        current = _make_entry(
            scan_id="s2",
            matched_files=1,
            hit_paths=("/a",),  # 仅 /a 持续命中
            rule_names=("rule1",),
        )
        comparison = compare_scans(current, previous)
        assert comparison.is_first_scan is False
        assert comparison.trend == "改善"
        assert comparison.matched_delta == -2
        assert comparison.new_hits == ()
        assert set(comparison.resolved_hits) == {"/b", "/c"}
        assert comparison.persistent_hits == ("/a",)
        assert comparison.new_rules == ()
        assert set(comparison.dropped_rules) == {"rule2"}

    def test_second_scan_worsened(self) -> None:
        previous = _make_entry(
            scan_id="s1",
            matched_files=1,
            hit_paths=("/a",),
            rule_names=("rule1",),
        )
        current = _make_entry(
            scan_id="s2",
            matched_files=3,
            hit_paths=("/a", "/b", "/c"),
            rule_names=("rule1", "rule2", "rule3"),
        )
        comparison = compare_scans(current, previous)
        assert comparison.trend == "恶化"
        assert comparison.matched_delta == 2
        assert set(comparison.new_hits) == {"/b", "/c"}
        assert comparison.resolved_hits == ()
        assert comparison.persistent_hits == ("/a",)
        assert set(comparison.new_rules) == {"rule2", "rule3"}
        assert comparison.dropped_rules == ()

    def test_second_scan_unchanged(self) -> None:
        previous = _make_entry(
            scan_id="s1",
            matched_files=2,
            hit_paths=("/a", "/b"),
            rule_names=("rule1",),
        )
        current = _make_entry(
            scan_id="s2",
            matched_files=2,
            hit_paths=("/a", "/b"),
            rule_names=("rule1",),
        )
        comparison = compare_scans(current, previous)
        assert comparison.trend == "持平"
        assert comparison.matched_delta == 0
        assert comparison.new_hits == ()
        assert comparison.resolved_hits == ()
        assert set(comparison.persistent_hits) == {"/a", "/b"}

    def test_summary_first_scan(self) -> None:
        current = _make_entry(
            scan_id="s1",
            matched_files=3,
            rule_names=("rule1", "rule2"),
        )
        comparison = compare_scans(current, None)
        summary = comparison.summary()
        assert "首次扫描" in summary
        assert "命中 3" in summary

    def test_summary_with_previous(self) -> None:
        previous = _make_entry(scan_id="s1", matched_files=5)
        current = _make_entry(scan_id="s2", matched_files=3)
        comparison = compare_scans(current, previous)
        summary = comparison.summary()
        assert "本次命中 3" in summary
        assert "上次命中 5" in summary
        assert "差值 -2" in summary
        assert "改善" in summary

    def test_scan_comparison_is_frozen(self) -> None:
        current = _make_entry(scan_id="s1")
        comparison = compare_scans(current, None)
        with pytest.raises(AttributeError):
            comparison.matched_delta = 100  # pyrefly: ignore [misc]


class TestHistoryStoreIntegration:
    """``HistoryStore`` 与 ``compare_scans`` 集成场景测试。"""

    def test_compare_with_previous_via_store(self, tmp_path: Path) -> None:
        """通过 store 模拟两次扫描后取对比。"""
        store = HistoryStore(path=tmp_path / "history.json")
        # 第一次扫描
        store.add(
            _make_entry(
                scan_id="s1",
                matched_files=2,
                hit_paths=("/a", "/b"),
                rule_names=("rule1",),
                finished_at="2026-07-27T10:00:00Z",
            )
        )
        # 第二次扫描（新增 /c，已解决 /b）
        store.add(
            _make_entry(
                scan_id="s2",
                matched_files=2,
                hit_paths=("/a", "/c"),
                rule_names=("rule1", "rule2"),
                finished_at="2026-07-27T11:00:00Z",
            )
        )
        entries = store.workspace_history("ws-1", limit=2)
        assert len(entries) == 2
        current = entries[0]
        previous = entries[1]
        comparison = compare_scans(current, previous)
        assert comparison.trend == "持平"  # 命中数都是 2
        assert set(comparison.new_hits) == {"/c"}
        assert set(comparison.resolved_hits) == {"/b"}
        assert comparison.persistent_hits == ("/a",)
        assert set(comparison.new_rules) == {"rule2"}

    def test_only_one_scan_no_previous(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "history.json")
        store.add(_make_entry(scan_id="s1"))
        entries = store.workspace_history("ws-1", limit=2)
        assert len(entries) == 1
        comparison = compare_scans(entries[0], None)
        assert comparison.is_first_scan is True


class TestHistoryStoreEdgeCases:
    """``HistoryStore`` 边界场景与异常路径补充测试。"""

    def test_previous_entry_scan_id_not_in_history(self, tmp_path: Path) -> None:
        """``current_scan_id`` 不在历史中时返回 ``None``。"""
        store = HistoryStore(path=tmp_path / "history.json")
        store.add(_make_entry(scan_id="s1", finished_at="2026-07-27T10:00:00Z"))
        # 不存在的 scan_id
        assert store.previous_entry("ws-1", "nonexistent") is None

    def test_previous_entry_earliest_scan_returns_none(self, tmp_path: Path) -> None:
        """``current_scan_id`` 是最早一条时返回 ``None``（无更早历史）。"""
        store = HistoryStore(path=tmp_path / "history.json")
        store.add(_make_entry(scan_id="s1", finished_at="2026-07-27T10:00:00Z"))
        store.add(_make_entry(scan_id="s2", finished_at="2026-07-27T11:00:00Z"))
        # s1 是最早一条（按 finished_at 升序），无更早历史
        assert store.previous_entry("ws-1", "s1") is None

    def test_clear_workspace_no_op_when_empty(self, tmp_path: Path) -> None:
        """清空无历史的工作区返回 0 且不写盘。"""
        path = tmp_path / "history.json"
        store = HistoryStore(path=path)
        # 即便文件不存在也应正常返回 0
        assert store.clear_workspace("ws-empty") == 0
        # 文件未被创建
        assert not path.exists()

    def test_load_with_non_dict_payload(self, tmp_path: Path) -> None:
        """顶层 payload 不是 dict 时返回空列表。"""
        path = tmp_path / "history.json"
        path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        store = HistoryStore(path=path)
        assert store.all_entries() == ()

    def test_load_with_non_list_entries(self, tmp_path: Path) -> None:
        """entries 字段非 list 时返回空列表。"""
        path = tmp_path / "history.json"
        path.write_text(
            json.dumps({"version": 1, "entries": "should-be-list"}),
            encoding="utf-8",
        )
        store = HistoryStore(path=path)
        assert store.all_entries() == ()

    def test_save_oserror_logged_no_raise(self, tmp_path: Path) -> None:
        """``_save`` 发生 ``OSError`` 时仅记录日志不抛异常。"""
        store = HistoryStore(path=tmp_path / "history.json")
        store.add(_make_entry(scan_id="s1"))
        # 将路径改为不可写位置（Windows 下用文件占位父目录）
        bad_path = tmp_path / "blocker.txt"  # 已存在的文件作为父路径非法
        bad_path.write_text("blocker", encoding="utf-8")
        store._path = bad_path / "history.json"  # 父路径是文件，无法 mkdir
        # 不应抛异常
        store.add(_make_entry(scan_id="s2"))
