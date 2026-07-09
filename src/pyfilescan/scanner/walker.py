"""文件遍历器：递归扫描目录，跳过忽略项，产出 FileEntry。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Optional, Set, Tuple

from pyfilescan.scanner.context import FileEntry

__all__ = ["FileWalker"]


class FileWalker:
    """递归目录遍历器。

    - 按目录名匹配忽略目录（如 ``.git``、``__pycache__``）
    - 按扩展名匹配忽略文件（如 ``pyc``）
    - 可选最大深度限制
    - 默认不跟随符号链接，避免环
    """

    def __init__(
        self,
        ignore_dirs: Tuple[str, ...] = (),
        ignore_extensions: Tuple[str, ...] = (),
        max_depth: Optional[int] = None,
        follow_symlinks: bool = False,
    ) -> None:
        self._ignore_dirs: Set[str] = {d.lower() for d in ignore_dirs}
        self._ignore_extensions: Set[str] = {e.lower().lstrip(".") for e in ignore_extensions}
        self._max_depth = max_depth
        self._follow_symlinks = follow_symlinks

    def walk(self, root: Path) -> Iterator[FileEntry]:
        """遍历根目录，产出 FileEntry（不包含目录本身）。"""
        root = root.resolve()
        if not root.exists():
            return
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
                yield from self._walk_dir(Path(entry.path), depth + 1)
            else:
                if self._is_ignored_file(name):
                    continue
                yield FileEntry.from_path(Path(entry.path))

    def _is_ignored_file(self, name: str) -> bool:
        suffix = Path(name).suffix.lower().lstrip(".")
        return suffix in self._ignore_extensions
