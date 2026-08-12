"""``processing.backup_manifest`` 模块单元测试。

覆盖：

- :class:`BackupEntry` 序列化/反序列化
- :class:`BackupManifest` 增删查改与持久化
- :meth:`BackupManifest.verify` 完整性校验（size/sha256 一致/不一致）
- :meth:`BackupManifest.find_by_src` / :meth:`find_by_post_hash` 查询
- manifest 文件损坏容错（JSON 解析失败 → 空索引）
- 并发安全（threading.RLock）
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from fuscan.processing.backup_manifest import (
    BackupEntry,
    BackupManifest,
    default_manifest_path,
    default_state_dir,
)


class TestDefaultPaths:
    def test_default_state_dir_under_config_dir(self) -> None:
        """默认状态目录位于 ``~/.fuscan/state``。"""
        result = default_state_dir()
        assert result.name == "state"
        assert result.parent.name == ".fuscan"

    def test_default_manifest_path_under_state_dir(self) -> None:
        """默认 manifest 路径位于状态目录下。"""
        result = default_manifest_path()
        assert result.name == "backup_manifest.json"
        assert result.parent.name == "state"


class TestBackupEntry:
    def test_entry_round_trip(self) -> None:
        """BackupEntry.to_dict + from_dict 往返一致。"""
        entry = BackupEntry(
            src_path="/abs/src.txt",
            backup_path="/abs/src.txt.bak",
            src_size=100,
            src_sha256="a" * 64,
            post_sha256="b" * 64,
            replaced_at="2026-08-12T10:30:00",
        )
        data = entry.to_dict()
        restored = BackupEntry.from_dict(data)
        assert restored == entry

    def test_entry_from_dict_missing_field_raises(self) -> None:
        """from_dict 缺少必要字段 → KeyError。"""
        with pytest.raises(KeyError):
            BackupEntry.from_dict({"src_path": "/x"})  # type: ignore[arg-type]

    def test_entry_from_dict_tolerates_extra_fields(self) -> None:
        """from_dict 容忍多余字段（向前兼容）。"""
        entry = BackupEntry(
            src_path="/x",
            backup_path="/x.bak",
            src_size=10,
            src_sha256="a" * 64,
            post_sha256="b" * 64,
            replaced_at="2026-08-12T10:30:00",
        )
        data = entry.to_dict()
        data["extra_field"] = "ignored"
        restored = BackupEntry.from_dict(data)
        assert restored.src_path == "/x"


class TestBackupManifestRecord:
    def test_record_creates_entry_and_persists(self, tmp_path: Path) -> None:
        """record 写入内存索引并持久化到磁盘。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)

        src = tmp_path / "src.txt"
        backup = tmp_path / "src.txt.bak"
        src.write_bytes(b"original")
        backup.write_bytes(b"original")

        entry = manifest.record(src, backup, b"original", b"replaced")

        assert entry.src_path == str(src.resolve())
        assert entry.backup_path == str(backup.resolve())
        assert entry.src_size == 8
        assert entry.post_sha256 != entry.src_sha256
        # 持久化到磁盘
        assert manifest_path.exists()
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert entry.src_path in data["entries"]

    def test_record_overwrites_existing_entry(self, tmp_path: Path) -> None:
        """同 src 多次 record → 仅保留最新条目。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)

        src = tmp_path / "src.txt"
        backup = tmp_path / "src.txt.bak"
        src.write_bytes(b"v1")
        backup.write_bytes(b"v1")

        manifest.record(src, backup, b"v1", b"v1_replaced")
        # 第二次 record（模拟再次替换）
        manifest.record(src, backup, b"v2", b"v2_replaced")

        entry = manifest.find_by_src(src)
        assert entry is not None
        assert entry.src_sha256 != "v1"  # 应为 v2 的 sha256

    def test_record_loads_existing_entries_on_init(self, tmp_path: Path) -> None:
        """新实例加载已有 manifest 文件。"""
        manifest_path = tmp_path / "manifest.json"
        manifest1 = BackupManifest(manifest_path)
        src = tmp_path / "src.txt"
        backup = tmp_path / "src.txt.bak"
        src.write_bytes(b"orig")
        backup.write_bytes(b"orig")
        manifest1.record(src, backup, b"orig", b"replaced")

        # 新实例加载同一文件
        manifest2 = BackupManifest(manifest_path)
        entry = manifest2.find_by_src(src)
        assert entry is not None
        assert entry.backup_path == str(backup.resolve())


class TestBackupManifestVerify:
    def test_verify_passes_for_intact_backup(self, tmp_path: Path) -> None:
        """备份完整 → verify 返回 True。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)
        src = tmp_path / "src.txt"
        backup = tmp_path / "src.txt.bak"
        src_content = b"original content"
        backup.write_bytes(src_content)
        src.write_bytes(src_content)
        manifest.record(src, backup, src_content, b"replaced content")

        assert manifest.verify(backup) is True

    def test_verify_fails_for_size_mismatch(self, tmp_path: Path) -> None:
        """备份大小不匹配 → verify 返回 False。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)
        src = tmp_path / "src.txt"
        backup = tmp_path / "src.txt.bak"
        src_content = b"original"
        backup.write_bytes(src_content)
        src.write_bytes(src_content)
        manifest.record(src, backup, src_content, b"replaced")

        # 篡改备份大小
        backup.write_bytes(b"original-plus-extra")

        assert manifest.verify(backup) is False

    def test_verify_fails_for_sha256_mismatch(self, tmp_path: Path) -> None:
        """备份 sha256 不匹配（同大小不同内容）→ verify 返回 False。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)
        src = tmp_path / "src.txt"
        backup = tmp_path / "src.txt.bak"
        src_content = b"original"
        backup.write_bytes(src_content)
        src.write_bytes(src_content)
        manifest.record(src, backup, src_content, b"replaced")

        # 同大小但内容不同
        backup.write_bytes(b"modified!")

        assert manifest.verify(backup) is False

    def test_verify_fails_for_missing_backup_file(self, tmp_path: Path) -> None:
        """备份文件不存在 → verify 返回 False。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)
        src = tmp_path / "src.txt"
        backup = tmp_path / "src.txt.bak"
        src.write_bytes(b"orig")
        backup.write_bytes(b"orig")
        manifest.record(src, backup, b"orig", b"repl")

        backup.unlink()

        assert manifest.verify(backup) is False

    def test_verify_fails_for_unknown_backup(self, tmp_path: Path) -> None:
        """manifest 中无对应条目 → verify 返回 False。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)
        unknown_backup = tmp_path / "unknown.bak"
        unknown_backup.write_bytes(b"unknown")

        assert manifest.verify(unknown_backup) is False


class TestBackupManifestFind:
    def test_find_by_src_returns_entry(self, tmp_path: Path) -> None:
        """find_by_src 返回对应条目。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)
        src = tmp_path / "src.txt"
        backup = tmp_path / "src.txt.bak"
        src.write_bytes(b"orig")
        backup.write_bytes(b"orig")
        manifest.record(src, backup, b"orig", b"repl")

        entry = manifest.find_by_src(src)
        assert entry is not None
        assert entry.backup_path == str(backup.resolve())

    def test_find_by_src_returns_none_for_unknown(self, tmp_path: Path) -> None:
        """find_by_src 无记录 → None。"""
        manifest = BackupManifest(tmp_path / "manifest.json")
        assert manifest.find_by_src(tmp_path / "unknown.txt") is None

    def test_find_by_post_hash_returns_entry(self, tmp_path: Path) -> None:
        """find_by_post_hash 返回对应条目。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)
        src = tmp_path / "src.txt"
        backup = tmp_path / "src.txt.bak"
        src.write_bytes(b"orig")
        backup.write_bytes(b"orig")
        manifest.record(src, backup, b"orig", b"repl")

        entry = manifest.find_by_src(src)
        assert entry is not None
        found = manifest.find_by_post_hash(entry.post_sha256)
        assert found is not None
        assert found.src_path == str(src.resolve())

    def test_find_by_post_hash_returns_none_for_unknown(self, tmp_path: Path) -> None:
        """find_by_post_hash 无匹配 → None。"""
        manifest = BackupManifest(tmp_path / "manifest.json")
        assert manifest.find_by_post_hash("unknown_hash") is None


class TestBackupManifestRemove:
    def test_remove_deletes_entry_and_persists(self, tmp_path: Path) -> None:
        """remove 删除条目并持久化。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)
        src = tmp_path / "src.txt"
        backup = tmp_path / "src.txt.bak"
        src.write_bytes(b"orig")
        backup.write_bytes(b"orig")
        manifest.record(src, backup, b"orig", b"repl")

        manifest.remove(src)

        assert manifest.find_by_src(src) is None
        # 持久化文件也更新
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert str(src.resolve()) not in data["entries"]

    def test_remove_unknown_src_is_noop(self, tmp_path: Path) -> None:
        """remove 不存在的 src → 无操作（不报错）。"""
        manifest = BackupManifest(tmp_path / "manifest.json")
        manifest.remove(tmp_path / "unknown.txt")  # 不抛异常


class TestBackupManifestCorruptionTolerance:
    def test_load_corrupted_json_resets_to_empty(self, tmp_path: Path) -> None:
        """manifest 文件 JSON 损坏 → 重置为空索引。"""
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text("{ invalid json", encoding="utf-8")

        manifest = BackupManifest(manifest_path)

        assert manifest.find_by_src(tmp_path / "x.txt") is None

    def test_load_missing_file_is_empty(self, tmp_path: Path) -> None:
        """manifest 文件不存在 → 空索引。"""
        manifest = BackupManifest(tmp_path / "nonexistent.json")
        assert manifest.find_by_src(tmp_path / "x.txt") is None

    def test_load_entry_with_missing_field_skipped(self, tmp_path: Path) -> None:
        """manifest 中某条目字段缺失 → 跳过该条目，其他正常加载。"""
        manifest_path = tmp_path / "manifest.json"
        # 用 resolve() 后的绝对路径作为 key，与 find_by_src 的 lookup 逻辑一致
        valid_src = tmp_path / "valid.txt"
        valid_key = str(valid_src.resolve())
        valid_entry = {
            "src_path": valid_key,
            "backup_path": str((tmp_path / "valid.txt.bak").resolve()),
            "src_size": 10,
            "src_sha256": "a" * 64,
            "post_sha256": "b" * 64,
            "replaced_at": "2026-08-12T10:30:00",
        }
        invalid_src = tmp_path / "invalid.txt"
        invalid_key = str(invalid_src.resolve())
        invalid_entry = {"src_path": invalid_key}  # 缺字段
        data = {"version": 1, "entries": {valid_key: valid_entry, invalid_key: invalid_entry}}
        manifest_path.write_text(json.dumps(data), encoding="utf-8")

        manifest = BackupManifest(manifest_path)

        # 有效条目正常加载
        assert manifest.find_by_src(valid_src) is not None
        # 无效条目被跳过
        assert manifest.find_by_src(invalid_src) is None


class TestBackupManifestConcurrency:
    def test_concurrent_record_is_thread_safe(self, tmp_path: Path) -> None:
        """多线程并发 record → 无数据竞争，所有条目均写入。"""
        manifest_path = tmp_path / "manifest.json"
        manifest = BackupManifest(manifest_path)

        def record_one(idx: int) -> None:
            src = tmp_path / f"src_{idx}.txt"
            backup = tmp_path / f"src_{idx}.txt.bak"
            src.write_bytes(f"orig_{idx}".encode())
            backup.write_bytes(f"orig_{idx}".encode())
            manifest.record(src, backup, f"orig_{idx}".encode(), f"repl_{idx}".encode())

        threads = [threading.Thread(target=record_one, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 重新加载验证持久化
        manifest2 = BackupManifest(manifest_path)
        for i in range(20):
            assert manifest2.find_by_src(tmp_path / f"src_{i}.txt") is not None
