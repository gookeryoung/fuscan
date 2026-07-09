"""文件遍历器单元测试。"""

from __future__ import annotations

from pathlib import Path

from pyfilescan.scanner.walker import FileWalker


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
