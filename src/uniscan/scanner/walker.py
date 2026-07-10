"""文件遍历器：递归扫描目录，跳过忽略项，产出 FileEntry。"""

from __future__ import annotations

import fnmatch
import os
import string
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Set, Tuple

from uniscan.scanner.context import FileEntry

__all__ = ["FileWalker", "list_drives"]


def list_drives() -> List[Path]:
    """枚举系统可用盘符/根路径。

    Windows 下返回所有存在的盘符路径（如 ``C:\\``、``D:\\``）；
    Unix-like 系统返回 ``["/"]``。

    :return: 盘符路径列表
    """
    if sys.platform == "win32":
        return [Path(f"{letter}:\\") for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
    return [Path("/")]  # pragma: no cover - Unix 平台分支，Windows 测试环境无法覆盖


class FileWalker:
    """递归目录遍历器。

    - 按目录名匹配忽略目录（如 ``.git``、``__pycache__``）
    - 按相对路径 glob 通配符匹配忽略目录（如 ``*/vendor/*``）
    - 按扩展名匹配忽略文件（如 ``pyc``）
    - 可选最大深度限制
    - 默认不跟随符号链接，避免环
    """

    def __init__(
        self,
        ignore_dirs: Tuple[str, ...] = (),
        ignore_extensions: Tuple[str, ...] = (),
        ignore_paths: Tuple[str, ...] = (),
        max_depth: Optional[int] = None,
        follow_symlinks: bool = False,
    ) -> None:
        self._ignore_dirs: Set[str] = {d.lower() for d in ignore_dirs}
        self._ignore_extensions: Set[str] = {e.lower().lstrip(".") for e in ignore_extensions}
        self._ignore_paths: List[str] = [p.lower() for p in ignore_paths]
        self._max_depth = max_depth
        self._follow_symlinks = follow_symlinks
        self._root: Optional[Path] = None

    def walk(self, root: Path) -> Iterator[FileEntry]:
        """遍历根目录，产出 FileEntry（不包含目录本身）。"""
        root = root.resolve()
        if not root.exists():
            return
        self._root = root
        if root.is_file():
            yield FileEntry.from_path(root)
            return
        yield from self._walk_dir(root, depth=0)

    def _walk_dir(self, directory: Path, depth: int) -> Iterator[FileEntry]:
        if self._max_depth is not None and depth > self._max_depth:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return

        for entry in entries:
            name = entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=self._follow_symlinks)
            except OSError:
                continue

            if is_dir:
                if name.lower() in self._ignore_dirs:
                    continue
                dir_path = Path(entry.path)
                if self._matches_ignore_path(dir_path):
                    continue
                yield from self._walk_dir(dir_path, depth + 1)
            else:
                if self._is_ignored_file(name):
                    continue
                yield FileEntry.from_path(Path(entry.path))

    def _matches_ignore_path(self, path: Path) -> bool:
        """检查目录相对路径是否匹配 ignore_paths 中的任一 glob 模式。

        支持两种匹配方式：
        - 目录路径直接匹配（如 ``vendor`` 匹配 ``vendor``）
        - 目录内文件路径匹配（如 ``src/vendor`` 匹配 ``*/vendor/*``，
          因为 ``src/vendor/x`` 会命中该模式）
        """
        if not self._ignore_paths or self._root is None:
            return False
        try:
            rel = path.relative_to(self._root)
        except ValueError:
            return False
        rel_str = rel.as_posix().lower()
        for pattern in self._ignore_paths:
            if fnmatch.fnmatch(rel_str, pattern):
                return True
            # 检查目录内文件路径是否匹配（处理 */vendor/* 等 glob 模式）
            if fnmatch.fnmatch(rel_str + "/x", pattern):
                return True
        return False

    def _is_ignored_file(self, name: str) -> bool:
        suffix = Path(name).suffix.lower().lstrip(".")
        return suffix in self._ignore_extensions
