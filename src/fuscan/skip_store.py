"""用户跳过路径持久化存储。

记录用户在结果详情区点击「标记为跳过」的文件路径，供扫描器在后续扫描中
直接跳过这些文件（不计入扫描、不计入命中，单独统计为「用户跳过」类别）。

存储方式：JSON 文件（默认 ``~/.fuscan/skips.json``），原子写入（临时文件 + ``Path.replace``）。
与扫描结果缓存（:mod:`fuscan.cache`）解耦——跳过决策独立于缓存兼容版本，
缓存清空不影响用户跳过列表。

线程安全：所有公共方法经 :class:`threading.RLock` 保护。扫描线程在启动前调用
:meth:`SkipStore.paths` 获取不可变快照（:class:`frozenset`），扫描期间不访问本对象；
UI 线程的增删操作与扫描线程的快照读取互不干扰。

路径以 ``str`` 形式存储与比较（``str(Path)``），与扫描器遍历产出的 ``entry.path``
字符串一致，确保跨扫描会话匹配。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

__all__ = ["SkipStore", "default_skip_store_path"]

logger = logging.getLogger(__name__)


def default_skip_store_path() -> Path:
    """返回默认跳过存储路径：``~/.fuscan/skips.json``。"""
    return Path.home() / ".fuscan" / "skips.json"


class SkipStore:
    """用户跳过路径的 JSON 持久化存储。

    用法：

    1. 构造时加载已有 JSON（不存在则视为空）
    2. ``add`` / ``remove`` / ``clear`` 修改后立即原子写回磁盘
    3. ``contains`` 判断单条路径是否被标记跳过
    4. ``paths`` 返回不可变快照供扫描器一次性读取
    """

    def __init__(self, path: Path | None = None) -> None:
        """初始化跳过存储。

        :param path: JSON 文件路径；``None`` 使用 :func:`default_skip_store_path`
        """
        self._path: Path = path if path is not None else default_skip_store_path()
        self._lock: threading.RLock = threading.RLock()
        self._paths: set[str] = self._load()

    def add(self, path: str) -> None:
        """标记单个路径为跳过，立即写回磁盘。已存在则无变化（不发警告）。"""
        with self._lock:
            if path in self._paths:
                return
            self._paths.add(path)
            self._save()

    def remove(self, path: str) -> None:
        """取消单个路径的跳过标记，立即写回磁盘。不存在则无变化。"""
        with self._lock:
            if path not in self._paths:
                return
            self._paths.discard(path)
            self._save()

    def contains(self, path: str) -> bool:
        """判断路径是否被标记跳过。"""
        with self._lock:
            return path in self._paths

    def paths(self) -> frozenset[str]:
        """返回当前所有跳过路径的不可变快照。

        扫描器在启动前调用本方法获取快照，扫描期间持有快照而不访问本对象，
        避免与 UI 线程的增删操作竞争。
        """
        with self._lock:
            return frozenset(self._paths)

    def clear(self) -> None:
        """清空全部跳过路径，立即写回磁盘。"""
        with self._lock:
            if not self._paths:
                return
            self._paths.clear()
            self._save()

    def _load(self) -> set[str]:
        """从磁盘加载跳过路径集合；文件不存在或损坏时返回空集合并记录警告。"""
        if not self._path.exists():
            return set()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("跳过存储文件损坏，按空集处理: %s", self._path, exc_info=True)
            return set()
        # 容忍非预期结构：仅接受 list[str]，其余视为损坏
        if not isinstance(data, list):
            logger.warning("跳过存储文件结构异常（非列表），按空集处理: %s", self._path)
            return set()
        result: set[str] = set()
        for item in data:
            if isinstance(item, str):
                result.add(item)
        return result

    def _save(self) -> None:
        """原子写回磁盘：写入临时文件后 ``Path.replace`` 覆盖，避免半写损坏。

        父目录不存在时自动创建。写失败时记录错误但不抛异常，保留内存态正确性。
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            payload = sorted(self._paths)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            logger.error("写入跳过存储失败: %s", self._path, exc_info=True)
