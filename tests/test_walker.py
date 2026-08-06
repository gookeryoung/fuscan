"""文件遍历器单元测试。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

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
        # 关闭内置默认兜底以验证纯遍历行为（否则 .git/node_modules 会被默认跳过）
        walker = FileWalker(use_default_ignore_dirs=False)
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert names == {"config", "lib.js", "app.py", "app.pyc", "README.md", "doc.TXT"}

    def test_walk_scan_extensions_filter(self, tmp_path: Path) -> None:
        """scan_extensions 早期过滤：仅 yield 匹配文件，跳过计数器累加。"""
        _create_tree(tmp_path)
        walker = FileWalker(scan_extensions=frozenset({"py", "txt"}), use_default_ignore_dirs=False)
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        # 仅 py 和 txt（大小写不敏感）文件被 yield
        assert names == {"app.py", "doc.TXT"}
        # 被跳过的文件数：config, lib.js, app.pyc, README.md = 4
        assert walker.skipped_by_extension == 4

    def test_walk_ignore_dirs(self, tmp_path: Path) -> None:
        _create_tree(tmp_path)
        walker = FileWalker(ignore_dirs=(".git", "node_modules"), use_default_ignore_dirs=False)
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "config" not in names
        assert "lib.js" not in names
        assert "app.py" in names

    def test_walk_max_depth(self, tmp_path: Path) -> None:
        _create_tree(tmp_path)
        walker = FileWalker(max_depth=0, use_default_ignore_dirs=False)
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        # depth=0 仅根目录下的文件
        assert names == {"README.md", "doc.TXT"}

    def test_walk_max_depth_1(self, tmp_path: Path) -> None:
        _create_tree(tmp_path)
        walker = FileWalker(max_depth=1, use_default_ignore_dirs=False)
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

    def test_on_skip_dir_none_default(self, tmp_path: Path) -> None:
        """on_skip_dir 默认 None 时不报错，正常遍历。"""
        _create_tree(tmp_path)
        walker = FileWalker(ignore_dirs=(".git",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "config" not in names  # .git 被跳过


class TestDefaultIgnoreDirs:
    """内置默认忽略目录兜底测试：无论是否传入 ignore_dirs 都跳过 .venv 等开发目录。"""

    def test_venv_skipped_without_explicit_ignore(self, tmp_path: Path) -> None:
        """不传 ignore_dirs 时 .venv 仍被内置兜底跳过。"""
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "pyvenv.cfg").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")
        walker = FileWalker()
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "pyvenv.cfg" not in names
        assert "main.py" in names

    def test_common_dev_dirs_skipped_by_default(self, tmp_path: Path) -> None:
        """常见开发目录（node_modules/__pycache__/.git 等）默认全部跳过。"""
        for dir_name in ("node_modules", "__pycache__", ".git", "venv", "dist", ".idea"):
            sub = tmp_path / dir_name
            sub.mkdir()
            (sub / "junk.txt").write_text("", encoding="utf-8")
        (tmp_path / "keep.py").write_text("", encoding="utf-8")
        walker = FileWalker()
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "junk.txt" not in names
        assert "keep.py" in names

    def test_default_case_insensitive(self, tmp_path: Path) -> None:
        """内置默认目录名匹配大小写不敏感（如 Node_Modules）。"""
        (tmp_path / "Node_Modules").mkdir()
        (tmp_path / "Node_Modules" / "lib.js").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")
        walker = FileWalker()
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "lib.js" not in names
        assert "main.py" in names

    def test_default_ignore_reports_via_on_skip_dir(self, tmp_path: Path) -> None:
        """内置默认跳过的目录也应触发 on_skip_dir 回调。"""
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "x.txt").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")
        skipped: list[str] = []
        walker = FileWalker(on_skip_dir=skipped.append)
        list(walker.walk(tmp_path))
        assert any(".venv" in p for p in skipped)

    def test_disable_default_allows_venv(self, tmp_path: Path) -> None:
        """use_default_ignore_dirs=False 时不再兜底跳过 .venv。"""
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "pyvenv.cfg").write_text("", encoding="utf-8")
        walker = FileWalker(use_default_ignore_dirs=False)
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "pyvenv.cfg" in names

    def test_default_merges_with_explicit(self, tmp_path: Path) -> None:
        """传入的 ignore_dirs 与内置默认集合取并集：两者都生效。"""
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "a.txt").write_text("", encoding="utf-8")
        (tmp_path / "custom_skip").mkdir()
        (tmp_path / "custom_skip" / "b.txt").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")
        walker = FileWalker(ignore_dirs=("custom_skip",))
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert "a.txt" not in names  # 内置默认跳过 .venv
        assert "b.txt" not in names  # 传入 custom_skip 跳过
        assert "main.py" in names


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

        # 关闭内置默认兜底：vendor 已进入默认忽略集合，否则会被目录级跳过
        walker = FileWalker(ignore_paths=(), use_default_ignore_dirs=False)
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

    def test_list_drives_uses_get_logical_drives_bitmask(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``_list_windows_drives`` 应基于 ``GetLogicalDrives`` 位掩码返回盘符。

        iter-59 改用 Win32 API 单次调用获取位掩码（bit 0=A:、bit 1=B:、...），
        避免逐个 ``Path.exists()`` 探测未就绪光驱时的 OSError 阻塞。模拟
        位掩码 0b101（A:、C:）验证仅返回位掩码中已设置的盘符。
        """
        import sys

        if sys.platform != "win32":
            return

        from fuscan.scanner import walker as walker_mod

        class _FakeWindll:
            class kernel32:
                @staticmethod
                def GetLogicalDrives() -> int:
                    # bit 0=A:、bit 2=C:，仅返回这两个盘符
                    return 0b101

            def __getattr__(self, _name: str) -> object:  # type: ignore[no-untyped-def]
                raise AttributeError(_name)

        monkeypatch.setattr(walker_mod.ctypes, "windll", _FakeWindll())
        drives = walker_mod._list_windows_drives()
        assert drives == [Path("A:\\"), Path("C:\\")]

    def test_list_drives_network_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``list_drives`` 默认应排除网络映射盘（DRIVE_REMOTE=4）。"""
        import sys

        if sys.platform != "win32":
            return

        from fuscan.scanner import walker as walker_mod

        class _FakeWindll:
            class kernel32:
                @staticmethod
                def GetLogicalDrives() -> int:
                    # bit 2=C:、bit 6=G:（G: 视为网络盘）
                    return 0b1000100

            def __getattr__(self, _name: str) -> object:  # type: ignore[no-untyped-def]
                raise AttributeError(_name)

        monkeypatch.setattr(walker_mod.ctypes, "windll", _FakeWindll())
        # G:\\ 模拟为网络盘（GetDriveTypeW 返回 DRIVE_REMOTE=4）
        monkeypatch.setattr(walker_mod, "_is_network_drive", lambda drive: str(drive).upper().startswith("G:"))
        drives = walker_mod.list_drives()
        # 网络盘 G:\\ 应被过滤掉
        assert not any(str(d).upper().startswith("G:") for d in drives)
        # C:\\ 仍应保留
        assert any(str(d).upper().startswith("C:") for d in drives)


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
            return original_scandir(path)  # pyrefly: ignore [no-matching-overload]

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


class TestSymlinkLoopDetection:
    """符号链接环路检测测试（I3 修复）。

    直接测试 _is_symlink_loop 方法，避免依赖真实符号链接创建
    （Windows 需要管理员权限或开发者模式）。
    """

    def test_no_follow_returns_false(self, tmp_path: Path) -> None:
        """follow_symlinks=False 时环路检测直接返回 False。"""
        walker = FileWalker(follow_symlinks=False)
        # 预填充 _seen_realpaths 模拟已访问场景，确保未启用时不检测
        walker._seen_realpaths = {str(tmp_path.resolve())}
        assert walker._is_symlink_loop(tmp_path) is False

    def test_follow_first_visit_returns_false_and_registers(self, tmp_path: Path) -> None:
        """follow_symlinks=True 时首次访问返回 False 并登记真实路径。"""
        walker = FileWalker(follow_symlinks=True)
        resolved = str(tmp_path.resolve())
        assert resolved not in walker._seen_realpaths
        assert walker._is_symlink_loop(tmp_path) is False
        assert resolved in walker._seen_realpaths

    def test_follow_second_visit_returns_true(self, tmp_path: Path) -> None:
        """follow_symlinks=True 时重复访问同一真实路径判定为环路。"""
        walker = FileWalker(follow_symlinks=True)
        # 首次访问登记
        assert walker._is_symlink_loop(tmp_path) is False
        # 第二次访问判定为环路
        assert walker._is_symlink_loop(tmp_path) is True

    def test_walk_resets_seen_realpaths(self, tmp_path: Path) -> None:
        """每次 walk() 重置 _seen_realpaths，避免跨多次遍历误判。"""
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "a.txt").write_text("", encoding="utf-8")
        walker = FileWalker(follow_symlinks=True)
        # 预填充一个无关路径，模拟上一次 walk 的残留
        walker._seen_realpaths = {"/stale/path"}
        list(walker.walk(tmp_path))
        # walk() 后 stale 路径应被清除，仅保留本次遍历的真实路径
        assert "/stale/path" not in walker._seen_realpaths
        assert str(tmp_path.resolve()) in walker._seen_realpaths

    def test_walk_follow_symlinks_normal_tree(self, tmp_path: Path) -> None:
        """follow_symlinks=True 时正常目录树（无环路）完整遍历。"""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("", encoding="utf-8")
        (tmp_path / "main.py").write_text("", encoding="utf-8")
        walker = FileWalker(follow_symlinks=True)
        entries = list(walker.walk(tmp_path))
        names = {e.name for e in entries}
        assert names == {"app.py", "main.py"}
