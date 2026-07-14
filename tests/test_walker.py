"""文件遍历器单元测试。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from fuscan.scanner.walker import FileWalker, list_drives


def _create_tree(root: Path) -> None:
    """在 root 下创建测试目录树。"""
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "lib.js").write_text("", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("", encoding="utf-8")
    (root / "src" / "app.pyc").write_text("", encoding="utf-8")
    (root / "README.md").write_text("", encoding="utf-8")
    (root / "doc.TXT").write_text("", encoding="utf-8")


class TestFileWalker:
    def test_walk_all_files(self, tmp_path: Path) -> None:
        _create_tree(tmp_path)
        walker = FileWalker()
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert names == {"config", "lib.js", "app.py", "app.pyc", "README.md", "doc.TXT"}

    def test_walk_ignore_dirs(self, tmp_path: Path) -> None:
        _create_tree(tmp_path)
        walker = FileWalker(ignore_dirs=(".git", "node_modules"))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "config" not in names
        assert "lib.js" not in names
        assert "app.py" in names

    def test_walk_ignore_extensions(self, tmp_path: Path) -> None:
        _create_tree(tmp_path)
        walker = FileWalker(ignore_extensions=("pyc",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "app.pyc" not in names
        assert "app.py" in names

    def test_walk_ignore_extensions_with_dot(self, tmp_path: Path) -> None:
        _create_tree(tmp_path)
        walker = FileWalker(ignore_extensions=(".pyc",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "app.pyc" not in names

    def test_walk_max_depth(self, tmp_path: Path) -> None:
        _create_tree(tmp_path)
        walker = FileWalker(max_depth=0)
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        # depth=0 仅根目录下的文件
        assert names == {"README.md", "doc.TXT"}

    def test_walk_max_depth_1(self, tmp_path: Path) -> None:
        _create_tree(tmp_path)
        walker = FileWalker(max_depth=1)
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "app.py" in names  # src/app.py 在 depth 1
        assert "README.md" in names

    def test_walk_single_file(self, tmp_path: Path) -> None:
        path = tmp_path / "single.txt"
        path.write_text("", encoding="utf-8")
        walker = FileWalker()
        entries = list(walker.walk(path))
        assert len(entries) == 1
        assert entries[0].name == "single.txt"

    def test_walk_nonexistent_returns_empty(self, tmp_path: Path) -> None:
        walker = FileWalker()
        entries = list(walker.walk(tmp_path / "missing"))
        assert entries == []

    def test_walk_ignore_dirs_case_insensitive(self, tmp_path: Path) -> None:
        (tmp_path / "Build").mkdir()
        (tmp_path / "Build" / "out.txt").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")
        walker = FileWalker(ignore_dirs=("build",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "out.txt" not in names
        assert "main.py" in names

    def test_walk_ignore_extensions_case_insensitive(self, tmp_path: Path) -> None:
        (tmp_path / "log.LOG").write_text("", encoding="utf-8")
        (tmp_path / "data.txt").write_text("", encoding="utf-8")
        walker = FileWalker(ignore_extensions=("log",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "log.LOG" not in names
        assert "data.txt" in names

    def test_on_skip_dir_called_for_ignored_dirs(self, tmp_path: Path) -> None:
        """ignore_dirs 跳过目录时应调用 on_skip_dir 回调，参数为目录绝对路径字符串。"""
        _create_tree(tmp_path)
        skipped: list[str] = []
        walker = FileWalker(ignore_dirs=(".git", "node_modules"), on_skip_dir=skipped.append)
        list(walker.walk(tmp_path))
        # 两个忽略目录均应上报
        assert len(skipped) == 2
        # 路径包含目录名
        assert any(p.endswith(".git") or p.endswith(".git\\") or p.endswith(".git/") for p in skipped)
        assert any("node_modules" in p for p in skipped)

    def test_on_skip_dir_called_for_ignored_paths(self, tmp_path: Path) -> None:
        """ignore_paths glob 跳过目录时应调用 on_skip_dir 回调。"""
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "lib.js").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")
        skipped: list[str] = []
        walker = FileWalker(ignore_paths=("vendor/*",), on_skip_dir=skipped.append)
        list(walker.walk(tmp_path))
        assert len(skipped) == 1
        assert "vendor" in skipped[0]

    def test_on_skip_dir_not_called_for_ignored_files(self, tmp_path: Path) -> None:
        """文件扩展名跳过不应触发 on_skip_dir（仅目录跳过才上报）。"""
        _create_tree(tmp_path)
        skipped: list[str] = []
        walker = FileWalker(ignore_extensions=("pyc",), on_skip_dir=skipped.append)
        list(walker.walk(tmp_path))
        assert skipped == []

    def test_on_skip_dir_none_default(self, tmp_path: Path) -> None:
        """on_skip_dir 默认 None 时不报错，正常遍历。"""
        _create_tree(tmp_path)
        walker = FileWalker(ignore_dirs=(".git",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "config" not in names  # .git 被跳过


class TestIgnorePaths:
    """ignore_paths 路径 glob 过滤测试。"""

    def test_ignore_paths_skips_root_vendor(self, tmp_path: Path) -> None:
        """``vendor/*`` 应跳过根级 vendor 目录及其子目录。"""
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "lib.js").write_text("", encoding="utf-8")
        (tmp_path / "vendor" / "sub").mkdir()
        (tmp_path / "vendor" / "sub" / "deep.js").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")

        walker = FileWalker(ignore_paths=("vendor/*",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "lib.js" not in names
        assert "deep.js" not in names
        assert "main.py" in names

    def test_ignore_paths_nested(self, tmp_path: Path) -> None:
        """``*/vendor/*`` 应跳过嵌套目录中的 vendor。"""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "vendor").mkdir()
        (tmp_path / "src" / "vendor" / "lib.js").write_text("", encoding="utf-8")
        (tmp_path / "src" / "app.py").write_text("", encoding="utf-8")

        walker = FileWalker(ignore_paths=("*/vendor/*",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "lib.js" not in names
        assert "app.py" in names

    def test_ignore_paths_multiple_patterns(self, tmp_path: Path) -> None:
        """多个 glob 模式同时生效。"""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "vendor").mkdir()
        (tmp_path / "src" / "vendor" / "v.js").write_text("", encoding="utf-8")
        (tmp_path / "src" / ".cache").mkdir()
        (tmp_path / "src" / ".cache" / "c.txt").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")

        walker = FileWalker(ignore_paths=("*/vendor/*", "*/.cache/*"))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "v.js" not in names
        assert "c.txt" not in names
        assert "main.py" in names

    def test_ignore_paths_case_insensitive(self, tmp_path: Path) -> None:
        """glob 模式匹配应大小写不敏感。"""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Vendor").mkdir()
        (tmp_path / "src" / "Vendor" / "lib.js").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")

        walker = FileWalker(ignore_paths=("*/vendor/*",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "lib.js" not in names
        assert "main.py" in names

    def test_ignore_paths_empty_no_effect(self, tmp_path: Path) -> None:
        """空 ignore_paths 不影响遍历。"""
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "lib.js").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")

        walker = FileWalker(ignore_paths=())
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "lib.js" in names
        assert "main.py" in names

    def test_ignore_paths_partial_match_not_skipped(self, tmp_path: Path) -> None:
        """部分匹配的目录不应被跳过。"""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "vendors").mkdir()
        (tmp_path / "src" / "vendors" / "lib.js").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")

        walker = FileWalker(ignore_paths=("*/vendor/*",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        # vendors 不匹配 */vendor/*，应保留
        assert "lib.js" in names
        assert "main.py" in names

    def test_ignore_paths_combined_with_ignore_dirs(self, tmp_path: Path) -> None:
        """ignore_paths 与 ignore_dirs 可同时生效。"""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "vendor").mkdir()
        (tmp_path / "src" / "vendor" / "lib.js").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")

        walker = FileWalker(ignore_dirs=(".git",), ignore_paths=("*/vendor/*",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "config" not in names
        assert "lib.js" not in names
        assert "main.py" in names


class TestListDrives:
    """list_drives 函数测试。"""

    def test_list_drives_returns_list(self) -> None:
        """list_drives 应返回列表。"""
        drives = list_drives()
        assert isinstance(drives, list)
        assert len(drives) > 0

    def test_list_drives_windows_returns_existing(self) -> None:
        """Windows 下返回存在的盘符路径。"""
        import sys

        if sys.platform == "win32":
            drives = list_drives()
            # 至少有一个盘符存在（通常是 C:）
            assert all(isinstance(d, Path) for d in drives)


class TestWalkerErrorHandling:
    """异常路径测试。"""

    def test_walk_scandir_os_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """os.scandir 失败时跳过该目录。"""
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "file.txt").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")

        original_scandir = os.scandir

        def mock_scandir(path: object) -> object:
            if Path(str(path)).name == "subdir":
                raise OSError("模拟权限拒绝")
            return original_scandir(path)

        monkeypatch.setattr(os, "scandir", mock_scandir)
        walker = FileWalker()
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        # subdir 被跳过，main.py 仍在
        assert "file.txt" not in names
        assert "main.py" in names

    def test_walk_is_dir_os_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """entry.is_dir() 失败时跳过该条目。"""
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "inner.txt").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")

        original_scandir = os.scandir

        class FakeEntry:
            def __init__(self, entry: os.DirEntry[str]) -> None:
                self._entry = entry
                self.name = entry.name
                self.path = entry.path

            def is_dir(self, follow_symlinks: bool = False) -> bool:
                if self._entry.name == "subdir":
                    raise OSError("模拟访问失败")
                return self._entry.is_dir(follow_symlinks=follow_symlinks)

            def stat(self) -> os.stat_result:
                return self._entry.stat()

        def mock_scandir(path: object) -> Iterator[FakeEntry]:
            for entry in original_scandir(Path(str(path))):
                yield FakeEntry(entry)  # type: ignore[misc]

        monkeypatch.setattr(os, "scandir", mock_scandir)
        walker = FileWalker()
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        # subdir 的 is_dir 失败被跳过，main.py 仍在
        assert "inner.txt" not in names
        assert "main.py" in names

    def test_matches_ignore_path_value_error(self, tmp_path: Path) -> None:
        """_matches_ignore_path 传入非子路径时返回 False。"""
        (tmp_path / "main.py").write_text("", encoding="utf-8")
        walker = FileWalker(ignore_paths=("vendor/*",))
        list(walker.walk(tmp_path))  # 消耗生成器以设置 _root
        # 传入一个不在 root 下的路径
        result = walker._matches_ignore_path(Path("/other/path"))
        assert result is False

    def test_matches_ignore_path_direct_match(self, tmp_path: Path) -> None:
        """_matches_ignore_path 直接匹配目录路径。"""
        (tmp_path / "vendor").mkdir()
        (tmp_path / "main.py").write_text("", encoding="utf-8")
        walker = FileWalker(ignore_paths=("vendor",))
        list(walker.walk(tmp_path))  # 消耗生成器以设置 _root
        # walk() 会 resolve 根路径，传入的路径也需 resolve 才能匹配
        result = walker._matches_ignore_path((tmp_path / "vendor").resolve())
        assert result is True
