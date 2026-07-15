"""文件遍历器：递归扫描目录，跳过忽略项，产出 FileEntry。"""

from __future__ import annotations

import fnmatch
import os
import string
import sys
from pathlib import Path
from typing import Callable, Iterator

from fuscan.scanner.context import FileEntry

__all__ = ["FileWalker", "list_drives"]


def list_drives(include_network: bool = False) -> list[Path]:
    """枚举系统可用盘符/根路径。

    Windows 下返回所有存在的盘符路径（如 ``C:\\``、``D:\\``），
    默认排除网络映射盘（DRIVE_REMOTE=4），可通过参数控制。
    Unix-like 系统返回 ``["/"]``。

    :param include_network: 是否包含网络映射盘，默认 False
    :return: 盘符路径列表
    """
    if sys.platform == "win32":
        if include_network:
            return [Path(f"{letter}:\\") for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
        return [drive for drive in _list_windows_drives() if not _is_network_drive(drive)]
    return [Path("/")]  # pragma: no cover - Unix 平台分支，Windows 测试环境无法覆盖


def _list_windows_drives() -> list[Path]:
    """枚举 Windows 所有可访问的盘符。

    使用 ``Path.exists()`` 探测盘符；对未就绪的光驱/虚拟盘等会抛
    ``OSError [WinError 1]`` 的盘符视为不可用并跳过。
    """
    drives: list[Path] = []
    for letter in string.ascii_uppercase:
        path = Path(f"{letter}:\\")
        try:
            if not path.exists():
                continue
        except OSError:
            # 未就绪的光驱/特殊文件系统：函数不正确(1)、设备未就绪(21) 等
            continue
        drives.append(path)
    return drives


def _is_network_drive(drive: Path) -> bool:
    """判断盘符是否为网络映射盘。

    使用 Windows API GetDriveTypeW 检测驱动器类型，DRIVE_REMOTE=4 表示网络驱动器。
    探测失败时返回 False（视为本地盘，避免误排除可用盘符）。

    :param drive: 盘符路径（如 ``C:\\``）
    :return: True 表示网络映射盘
    """
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    DRIVE_REMOTE = 4
    drive_str = str(drive)
    try:
        drive_type = windll.kernel32.GetDriveTypeW(drive_str)
    except OSError:
        return False
    return drive_type == DRIVE_REMOTE


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
        ignore_dirs: tuple[str, ...] = (),
        ignore_extensions: tuple[str, ...] = (),
        ignore_paths: tuple[str, ...] = (),
        max_depth: int | None = None,
        follow_symlinks: bool = False,
        on_skip_dir: Callable[[str], None] | None = None,
    ) -> None:
        self._ignore_dirs: set[str] = {d.lower() for d in ignore_dirs}
        self._ignore_extensions: set[str] = {e.lower().lstrip(".") for e in ignore_extensions}
        self._ignore_paths: list[str] = [p.lower() for p in ignore_paths]
        self._max_depth = max_depth
        self._follow_symlinks = follow_symlinks
        self._root: Path | None = None
        self._on_skip_dir = on_skip_dir

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
            entries = list(os.scandir(directory))
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
                    if self._on_skip_dir is not None:
                        self._on_skip_dir(str(Path(entry.path)))
                    continue
                dir_path = Path(entry.path)
                if self._matches_ignore_path(dir_path):
                    if self._on_skip_dir is not None:
                        self._on_skip_dir(str(dir_path))
                    continue
                yield from self._walk_dir(dir_path, depth + 1)
            else:
                if self._is_ignored_file(name):
                    continue
                # 用 DirEntry 构造，复用 scandir 已缓存的 stat，避免 Path.stat() 重复系统调用
                yield FileEntry.from_direntry(entry)

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
        dot = name.rfind(".")
        if dot < 0:
            return False
        suffix = name[dot + 1 :].lower()
        return suffix in self._ignore_extensions
