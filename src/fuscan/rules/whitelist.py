"""误报白名单：用户标记的「路径 + 规则」组合在后续扫描中过滤。

记录用户在结果详情区点击「标记为误报」的 (路径 glob 模式, 规则名) 组合，
扫描器在命中聚合阶段过滤命中白名单的结果，不计入 ScanReport.hits。

存储方式：JSON 文件（默认 ``~/.fuscan/whitelist.json``），原子写入
（临时文件 + ``Path.replace``）。与 :mod:`fuscan.processing.skip_store` 同样的持久化策略。

路径匹配采用 glob 通配符（``fnmatch``），支持：

- 精确路径：``/a/b/c.txt``
- 通配符：``/a/*/c.txt``、``/a/**/*.txt``
- 目录前缀：``/a/vendor/*`` 匹配 ``/a/vendor/`` 下所有文件

``rule_name`` 为 ``*`` 时匹配任意规则（用于「此文件全部命中均为误报」语义）。

线程安全：所有公共方法经 :class:`threading.RLock` 保护。扫描线程在启动前
调用 :meth:`Whitelist.snapshot` 获取不可变快照（:class:`frozenset`），
扫描期间不访问本对象；UI 线程的增删操作与扫描线程的快照读取互不干扰。
"""

from __future__ import annotations

import fnmatch
import logging
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import orjson

    def _json_dumps(data: list[dict[str, Any]]) -> str:
        """高性能 JSON 序列化（orjson，带缩进）。"""
        return orjson.dumps(data, option=orjson.OPT_INDENT_2).decode("utf-8")

    def _json_loads(data: str | bytes) -> list[dict[str, Any]]:
        """高性能 JSON 反序列化（orjson，接受 str 或 bytes）。"""
        result = orjson.loads(data)
        if not isinstance(result, list):
            raise ValueError("JSON 顶层必须是列表")
        return result

except ImportError:  # pragma: no cover
    import json

    def _json_dumps(data: list[dict[str, Any]]) -> str:
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _json_loads(data: str | bytes) -> list[dict[str, Any]]:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        result = json.loads(data)
        if not isinstance(result, list):
            raise ValueError("JSON 顶层必须是列表")
        return result


from fuscan.utils.io import atomic_write_text

__all__ = [
    "Whitelist",
    "WhitelistEntry",
    "default_whitelist_path",
]

logger = logging.getLogger(__name__)

# Windows 路径大小写不敏感，其他平台敏感
_CASE_SENSITIVE: bool = sys.platform != "win32"


def default_whitelist_path() -> Path:
    """返回默认白名单存储路径：``~/.fuscan/whitelist.json``。"""
    return Path.home() / ".fuscan" / "whitelist.json"


@dataclass(frozen=True)
class WhitelistEntry:
    """单条白名单记录：路径 glob 模式 + 规则名。

    :param path_glob: 路径 glob 模式（``fnmatch`` 语法，如 ``/a/vendor/*.txt``
        或 ``/a/b/c.txt``）。``*`` 匹配任意字符（含分隔符），与 ``fnmatch`` 一致。
    :param rule_name: 规则名；``*`` 表示匹配任意规则（用于「此文件全部命中均为误报」）。
        空字符串视为 ``*``（兼容旧数据）。
    :param created_at: 创建时间 ISO 字符串（如 ``2026-07-29T10:30:00``），便于审计。
    :param note: 用户备注（可空）。
    :param source: 条目来源。``"rules"`` 表示来自规则文件预定义（YAML whitelist 段），
        ``"runtime"`` 表示用户在结果详情区点击「标记为误报」运行时写入。
        缺省为 ``"rules"``。``WhitelistStore`` 加载的旧 JSON 数据未带该字段时按
        ``"runtime"`` 处理（兼容历史数据），由 :meth:`from_dict` 处理。
    """

    path_glob: str
    rule_name: str
    created_at: str = ""
    note: str = ""
    source: str = "rules"

    def __post_init__(self) -> None:
        if not self.path_glob:
            raise ValueError("path_glob 不能为空")
        if not self.rule_name:
            # 空规则名视为通配（兼容用户输入）
            object.__setattr__(self, "rule_name", "*")
        if self.source not in ("rules", "runtime"):
            object.__setattr__(self, "source", "rules")

    def matches(self, path_str: str, rule_name: str) -> bool:
        """判断给定 (路径, 规则名) 是否命中本条白名单。

        :param path_str: 待匹配的文件路径字符串（``str(Path)``）
        :param rule_name: 待匹配的规则名
        :return: 命中返回 ``True``

        路径分隔符归一化：``str(Path)`` 在 Windows 下产生反斜杠，而用户
        手动输入的 glob 模式可能用正斜杠（``/a/vendor/*.txt``）。统一将
        两侧的反斜杠替换为正斜杠后再匹配，确保跨平台一致性。
        """
        norm_path = path_str.replace("\\", "/")
        norm_glob = self.path_glob.replace("\\", "/")
        if _CASE_SENSITIVE:
            path_match = fnmatch.fnmatchcase(norm_path, norm_glob)
        else:
            # Windows 路径大小写不敏感：统一小写比较
            path_match = fnmatch.fnmatchcase(norm_path.lower(), norm_glob.lower())
        if not path_match:
            return False
        if self.rule_name == "*":
            return True
        return self.rule_name == rule_name

    def to_dict(self) -> dict[str, str]:
        """序列化为字典（JSON 持久化用）。

        ``source`` 字段始终写出，确保 YAML/JSON 导出可往返。
        """
        return {
            "path_glob": self.path_glob,
            "rule_name": self.rule_name,
            "created_at": self.created_at,
            "note": self.note,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WhitelistEntry:
        """从字典反序列化（容忍缺失字段，向后兼容）。

        旧 JSON 数据未带 ``source`` 字段时按 ``"runtime"`` 处理
        （历史数据均由 :class:`WhitelistStore` 运行时写入）。
        """
        source = str(data.get("source", ""))
        if not source:
            source = "runtime"
        return cls(
            path_glob=str(data.get("path_glob", "")),
            rule_name=str(data.get("rule_name", "*") or "*"),
            created_at=str(data.get("created_at", "")),
            note=str(data.get("note", "")),
            source=source,
        )


@dataclass(frozen=True)
class Whitelist:
    """白名单集合：不可变快照，扫描期间使用。

    用 :meth:`matches` 判断 (路径, 规则名) 是否命中任一条目。扫描器在命中
    聚合阶段调用本方法过滤误报。

    可变操作（``add``/``remove``）通过 :class:`WhitelistStore` 实现，本类仅
    提供只读视图与匹配查询。
    """

    entries: tuple[WhitelistEntry, ...] = field(default_factory=tuple)

    def matches(self, path: Path, rule_name: str) -> bool:
        """判断 (路径, 规则名) 是否命中白名单。

        :param path: 文件路径
        :param rule_name: 规则名
        :return: 命中任一条目返回 ``True``
        """
        if not self.entries:
            return False
        path_str = str(path)
        return any(entry.matches(path_str, rule_name) for entry in self.entries)

    def matches_any_rule(self, path: Path, rule_names: tuple[str, ...]) -> bool:
        """判断路径在指定规则集合中是否有任一规则命中白名单。

        用于扫描结果过滤：一个 :class:`~fuscan.scanner.result.ScanResult`
        的所有命中规则若都被白名单覆盖，则该结果视为误报需过滤。

        :param path: 文件路径
        :param rule_names: 该文件命中的所有规则名
        :return: 所有命中规则都被白名单覆盖时返回 ``True``（即该结果应过滤）
        """
        if not self.entries or not rule_names:
            return False
        # 每条规则都需命中白名单才整体过滤
        return all(self.matches(path, name) for name in rule_names)

    def to_json(self) -> str:
        """序列化为 JSON 字符串。"""
        return _json_dumps([entry.to_dict() for entry in self.entries])

    @classmethod
    def from_json(cls, json_str: str | bytes) -> Whitelist:
        """从 JSON 反序列化。

        :param json_str: :meth:`to_json` 输出的 JSON 字符串或字节串
        :return: :class:`Whitelist` 实例
        :raises ValueError: JSON 格式非法
        """
        data = _json_loads(json_str)
        entries: list[WhitelistEntry] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(WhitelistEntry.from_dict(item))
            except ValueError:
                # path_glob 为空等无效条目跳过
                logger.warning("跳过无效白名单条目: %s", item)
        return cls(entries=tuple(entries))


class WhitelistStore:
    """白名单可变存储：JSON 持久化 + 线程安全增删。

    用法：

    1. 构造时加载已有 JSON（不存在则视为空）
    2. ``add`` / ``remove`` / ``clear`` 修改后立即原子写回磁盘
    3. ``snapshot`` 返回不可变 :class:`Whitelist` 供扫描器一次性读取

    与 :class:`~fuscan.processing.skip_store.SkipStore` 同样的持久化与线程安全策略，
    区别在于存储的是 (路径 glob, 规则名) 组合而非单一路径。
    """

    def __init__(self, path: Path | None = None) -> None:
        """初始化白名单存储。

        :param path: JSON 文件路径；``None`` 使用 :func:`default_whitelist_path`
        """
        self._path: Path = path if path is not None else default_whitelist_path()
        self._lock: threading.RLock = threading.RLock()
        self._entries: list[WhitelistEntry] = self._load()

    def add(self, entry: WhitelistEntry) -> None:
        """添加白名单条目，立即写回磁盘。已存在相同 (path_glob, rule_name) 则无变化。"""
        with self._lock:
            if any(e.path_glob == entry.path_glob and e.rule_name == entry.rule_name for e in self._entries):
                return
            self._entries.append(entry)
            self._save()

    def remove(self, path_glob: str, rule_name: str) -> None:
        """移除匹配 (path_glob, rule_name) 的条目，立即写回磁盘。不存在则无变化。"""
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if not (e.path_glob == path_glob and e.rule_name == rule_name)]
            if len(self._entries) != before:
                self._save()

    def remove_at(self, index: int) -> bool:
        """按索引移除条目。索引越界返回 ``False``，否则移除并写回磁盘返回 ``True``。"""
        with self._lock:
            if index < 0 or index >= len(self._entries):
                return False
            self._entries.pop(index)
            self._save()
            return True

    def clear(self) -> None:
        """清空全部白名单条目，立即写回磁盘。"""
        with self._lock:
            if not self._entries:
                return
            self._entries.clear()
            self._save()

    def snapshot(self) -> Whitelist:
        """返回当前白名单的不可变快照。

        扫描器在启动前调用本方法获取快照，扫描期间持有快照而不访问本对象，
        避免与 UI 线程的增删操作竞争。
        """
        with self._lock:
            return Whitelist(entries=tuple(self._entries))

    def entries(self) -> tuple[WhitelistEntry, ...]:
        """返回当前条目的不可变快照（供 UI 展示）。"""
        with self._lock:
            return tuple(self._entries)

    def import_json(self, json_str: str | bytes) -> int:
        """从 JSON 字符串导入白名单（合并到现有条目，去重）。

        :param json_str: :meth:`Whitelist.to_json` 输出格式
        :return: 实际新增的条目数
        """
        new_whitelist = Whitelist.from_json(json_str)
        with self._lock:
            existing = {(e.path_glob, e.rule_name) for e in self._entries}
            added = 0
            for entry in new_whitelist.entries:
                key = (entry.path_glob, entry.rule_name)
                if key not in existing:
                    self._entries.append(entry)
                    existing.add(key)
                    added += 1
            if added > 0:
                self._save()
            return added

    def export_json(self) -> str:
        """导出当前白名单为 JSON 字符串（与 :meth:`Whitelist.to_json` 等价）。"""
        with self._lock:
            return Whitelist(entries=tuple(self._entries)).to_json()

    def _load(self) -> list[WhitelistEntry]:
        """从磁盘加载白名单；文件不存在或损坏时返回空列表并记录警告。"""
        if not self._path.exists():
            return []
        try:
            data = self._path.read_bytes()
            entries = list(Whitelist.from_json(data).entries)
        except (OSError, ValueError):
            logger.warning("白名单文件损坏，按空集处理: %s", self._path, exc_info=True)
            return []
        return entries

    def _save(self) -> None:
        """原子写回磁盘：写入临时文件后 ``Path.replace`` 覆盖，避免半写损坏。

        父目录不存在时自动创建。写失败时记录错误但不抛异常，保留内存态正确性。
        """
        try:
            payload = [entry.to_dict() for entry in self._entries]
            atomic_write_text(self._path, _json_dumps(payload))
        except OSError:
            logger.error("写入白名单失败: %s", self._path, exc_info=True)
