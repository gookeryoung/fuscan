"""文件遍历器：递归扫描目录，跳过忽略项，产出 FileEntry。"""

from __future__ import annotations

import ctypes
import fnmatch
import logging
import os
import string
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from fuscan.scanner.context import FileEntry

__all__ = ["FileWalker", "list_drives"]

logger = logging.getLogger(__name__)


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

    使用 ``GetLogicalDrives`` Win32 API 一次性获取盘符位掩码（bit 0=A:、
    bit 1=B:、...），单次系统调用完成枚举。相比逐个 ``Path.exists()`` 探测：

    - 避免对未就绪光驱/虚拟盘触发 ``OSError [WinError 1]`` 长时间阻塞
      （每次 OSError 探测可能耗时 100-500ms，未就绪光驱会让 GUI 启动卡顿）
    - 性能从最坏 ~500ms 降至 <1ms，符合 iter-59 GUI 启动卡滞优化目标
    - ``GetLogicalDrives`` 仅返回已挂载逻辑盘符，未格式化或未就绪的设备自动排除
    """
    windll = getattr(ctypes, "windll", None)
    if windll is None:  # pragma: no cover - 非 Windows 环境的防御性回退
        return [Path(f"{letter}:\\") for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
    try:
        bitmask = windll.kernel32.GetLogicalDrives()
    except OSError:  # pragma: no cover - API 调用失败的防御性回退
        return [Path(f"{letter}:\\") for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
    if bitmask == 0:  # pragma: no cover - 极端场景回退
        return []
    return [Path(f"{letter}:\\") for i, letter in enumerate(string.ascii_uppercase) if bitmask & (1 << i)]


def _is_network_drive(drive: Path) -> bool:
    """判断盘符是否为网络映射盘。

    使用 Windows API GetDriveTypeW 检测驱动器类型，DRIVE_REMOTE=4 表示网络驱动器。
    探测失败时返回 False（视为本地盘，避免误排除可用盘符）。

    :param drive: 盘符路径（如 ``C:\\``）
    :return: True 表示网络映射盘
    """
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
    - 可选最大深度限制
    - 默认不跟随符号链接，避免环

    .. note::
       iter-87 起，扩展名过滤改由白名单制（``Scanner._should_scan`` 按
       ``scan_extensions`` 判断）统一管理，``FileWalker`` 不再持有扩展名黑名单。
       待扫描文件由 walk 阶段收集后，Scanner 在 ``collect_entries`` 中按白名单过滤。
    """

    def __init__(
        self,
        ignore_dirs: tuple[str, ...] = (),
        ignore_paths: tuple[str, ...] = (),
        max_depth: int | None = None,
        follow_symlinks: bool = False,
        on_skip_dir: Callable[[str], None] | None = None,
    ) -> None:
        self._ignore_dirs: set[str] = {d.lower() for d in ignore_dirs}
        self._ignore_paths: list[str] = [p.lower() for p in ignore_paths]
        self._max_depth = max_depth
        self._follow_symlinks = follow_symlinks
        self._root: Path | None = None
        self._on_skip_dir = on_skip_dir
        # follow_symlinks 时跟踪已访问目录的真实路径，检测环路避免无限递归
        self._seen_realpaths: set[str] = set()

    def walk(self, root: Path) -> Iterator[FileEntry]:
        """遍历根目录，产出 FileEntry（不包含目录本身）。"""
        root = root.resolve()
        if not root.exists():
            return
        self._root = root
        # 每次遍历重置环路检测集合；follow_symlinks 时记录根目录真实路径
        self._seen_realpaths = {str(root)} if self._follow_symlinks else set()
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
            logger.debug("无法访问目录，已跳过: %s", directory, exc_info=True)
            return

        for entry in entries:
            name = entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=self._follow_symlinks)
            except OSError:
                logger.debug("无法读取目录条目状态，已跳过: %s", entry.path, exc_info=True)
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
                # follow_symlinks 时检测符号链接环路（如 a/link -> a），避免无限递归
                if self._is_symlink_loop(dir_path):
                    continue
                yield from self._walk_dir(dir_path, depth + 1)
            else:
                # 用 DirEntry 构造，复用 scandir 已缓存的 stat，避免 Path.stat() 重复系统调用
                yield FileEntry.from_direntry(entry)

    def _is_symlink_loop(self, dir_path: Path) -> bool:
        """检测目录是否为符号链接环路（仅在 follow_symlinks 时生效）。

        跟踪已访问目录的真实路径（``resolve()`` 解析符号链接），
        若真实路径已访问过则判定为环路。首次访问的目录会登记到集合。

        :param dir_path: 待进入的目录路径
        :return: True 表示环路，应跳过；False 表示可安全进入
        """
        if not self._follow_symlinks:
            return False
        real = str(dir_path.resolve())
        if real in self._seen_realpaths:
            logger.debug("检测到符号链接环路，跳过: %s", dir_path)
            return True
        self._seen_realpaths.add(real)
        return False

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
