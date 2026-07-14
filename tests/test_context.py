"""文件上下文与遍历器单元测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fuscan.scanner.context import FileEntry, MatchContext, default_content_provider


class TestFileEntry:
    def test_from_path_file(self, tmp_path: Path) -> None:
        path = tmp_path / "test.txt"
        path.write_text("hello", encoding="utf-8")
        entry = FileEntry.from_path(path)
        assert entry.path == path
        assert entry.name == "test.txt"
        assert entry.size == 5
        assert entry.extension == "txt"
        assert entry.is_dir is False

    def test_from_path_directory(self, tmp_path: Path) -> None:
        entry = FileEntry.from_path(tmp_path)
        assert entry.is_dir is True
        assert entry.name == tmp_path.name

    def test_from_path_extension_uppercase(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.PDF"
        path.write_text("x", encoding="utf-8")
        entry = FileEntry.from_path(path)
        assert entry.extension == "pdf"

    def test_from_path_inaccessible(self, tmp_path: Path) -> None:
        """不存在的路径返回空元信息而不抛异常。"""
        entry = FileEntry.from_path(tmp_path / "missing")
        assert entry.size == 0
        assert entry.mtime == 0.0

    def test_from_direntry_file(self, tmp_path: Path) -> None:
        """from_direntry 从 scandir 的 DirEntry 构造 FileEntry。"""
        path = tmp_path / "data.csv"
        path.write_text("a,b,c", encoding="utf-8")
        entries = list(os.scandir(tmp_path))
        assert len(entries) == 1
        entry = FileEntry.from_direntry(entries[0])
        assert entry.path == path
        assert entry.name == "data.csv"
        assert entry.size == 5
        assert entry.extension == "csv"
        assert entry.is_dir is False

    def test_from_direntry_directory(self, tmp_path: Path) -> None:
        """from_direntry 正确识别目录。"""
        (tmp_path / "subdir").mkdir()
        entries = list(os.scandir(tmp_path))
        assert len(entries) == 1
        entry = FileEntry.from_direntry(entries[0])
        assert entry.is_dir is True
        assert entry.name == "subdir"

    def test_from_direntry_stat_oserror(self, tmp_path: Path) -> None:
        """DirEntry.stat() 抛 OSError 时返回空元信息。"""
        path = tmp_path / "err.txt"
        path.write_text("x", encoding="utf-8")

        class FakeEntry:
            def __init__(self) -> None:
                self.name = "err.txt"
                self.path = str(path)

            def stat(self) -> os.stat_result:
                raise OSError("模拟 stat 失败")

        entry = FileEntry.from_direntry(FakeEntry())  # type: ignore[arg-type]
        assert entry.size == 0
        assert entry.mtime == 0.0
        assert entry.is_dir is False


class TestMatchContext:
    def test_content_lazy_load(self, tmp_path: Path) -> None:
        path = tmp_path / "lazy.txt"
        path.write_text("content here", encoding="utf-8")
        entry = FileEntry.from_path(path)
        ctx = MatchContext(entry)
        assert ctx._content_loaded is False
        _ = ctx.content
        assert ctx._content_loaded is True

    def test_content_cached(self, tmp_path: Path) -> None:
        path = tmp_path / "cached.txt"
        path.write_text("v1", encoding="utf-8")
        entry = FileEntry.from_path(path)
        ctx = MatchContext(entry)
        first = ctx.content
        path.write_text("v2", encoding="utf-8")  # 修改文件
        second = ctx.content  # 应返回缓存
        assert first == second

    def test_custom_content_provider(self, tmp_path: Path) -> None:
        path = tmp_path / "custom.txt"
        path.write_text("real", encoding="utf-8")
        entry = FileEntry.from_path(path)
        ctx = MatchContext(entry, content_provider=lambda e: "stub-content")
        assert ctx.content == "stub-content"

    def test_reset_clears_cache(self, tmp_path: Path) -> None:
        path = tmp_path / "reset.txt"
        path.write_text("v1", encoding="utf-8")
        entry = FileEntry.from_path(path)
        ctx = MatchContext(entry)
        _ = ctx.content
        ctx.reset()
        assert ctx._content_loaded is False


class TestDefaultContentProvider:
    def test_read_text_file(self, tmp_path: Path) -> None:
        path = tmp_path / "read.txt"
        path.write_text("hello world", encoding="utf-8")
        entry = FileEntry.from_path(path)
        assert default_content_provider(entry) == "hello world"

    def test_skip_directory(self, tmp_path: Path) -> None:
        entry = FileEntry.from_path(tmp_path)
        assert default_content_provider(entry) == ""

    def test_skip_oversized(self, tmp_path: Path) -> None:
        path = tmp_path / "big.txt"
        path.write_text("x" * 100, encoding="utf-8")
        entry = FileEntry.from_path(path)
        # 用很小的 max_size 触发跳过
        assert default_content_provider(entry, max_size=10) == ""

    def test_oserror_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "err.txt"
        path.write_text("content", encoding="utf-8")
        entry = FileEntry.from_path(path)

        def raise_oserror(self: Path, *args: object, **kwargs: object) -> str:
            raise OSError("denied")

        monkeypatch.setattr(Path, "read_text", raise_oserror)
        assert default_content_provider(entry) == ""
