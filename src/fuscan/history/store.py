"""扫描历史 JSON 持久化存储。

:class:`HistoryStore` 负责 :class:`ScanHistoryEntry` 的增删查改与磁盘持久化。
设计要点：

- **存储位置**：``~/.fuscan/history.json``，与 ``workspaces.json``/``skips.json`` 同目录
- **原子写入**：临时文件 + ``Path.replace``，避免半写损坏
- **线程安全**：所有公共方法经 :class:`threading.RLock` 保护，
  扫描完成线程写入与 UI 线程读取互不干扰
- **容量限制**：每个工作区默认保留最近 50 条记录，超出时按时间倒序丢弃最旧
- **容错加载**：单条损坏跳过，不阻塞其余条目恢复

文件格式：

```json
{
  "version": 1,
  "entries": [
    {"scan_id": "...", "workspace_id": "...", ...},
    ...
  ]
}
```
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from fuscan import config as config_module
from fuscan.history.model import ScanHistoryEntry

__all__ = ["DEFAULT_MAX_ENTRIES_PER_WORKSPACE", "HistoryStore", "default_history_store_path"]

logger = logging.getLogger(__name__)

HISTORY_FILENAME: str = "history.json"
HISTORY_VERSION: int = 1
DEFAULT_MAX_ENTRIES_PER_WORKSPACE: int = 50


def default_history_store_path() -> Path:
    """返回默认历史存储路径：``~/.fuscan/history.json``。"""
    return config_module.CONFIG_DIR / HISTORY_FILENAME


class HistoryStore:
    """扫描历史的 JSON 持久化存储。

    用法：

    1. 构造时加载已有 JSON（不存在或损坏视为空）
    2. :meth:`add` 添加单次扫描归档，立即原子写回磁盘
    3. :meth:`workspace_history` 返回指定工作区历史列表（时间倒序）
    4. :meth:`clear_workspace` / :meth:`clear_all` 清理历史

    所有方法线程安全（``RLock`` 保护）。
    """

    def __init__(
        self,
        path: Path | None = None,
        max_entries_per_workspace: int = DEFAULT_MAX_ENTRIES_PER_WORKSPACE,
    ) -> None:
        """初始化历史存储。

        :param path: JSON 文件路径；``None`` 使用 :func:`default_history_store_path`
        :param max_entries_per_workspace: 每个工作区保留的最大条目数，
            超出时按 ``finished_at`` 倒序丢弃最旧
        """
        self._path: Path = path if path is not None else default_history_store_path()
        self._max_per_ws: int = max(1, max_entries_per_workspace)
        self._lock: threading.RLock = threading.RLock()
        self._entries: list[ScanHistoryEntry] = self._load()

    def add(self, entry: ScanHistoryEntry) -> None:
        """添加单次扫描归档，立即原子写回磁盘。

        超出 ``max_entries_per_workspace`` 时按 ``finished_at`` 倒序保留最新，
        最旧的被丢弃。同 ``scan_id`` 视为重复，覆盖原条目。

        :param entry: 待归档的扫描历史条目
        """
        with self._lock:
            # 去重：同 scan_id 覆盖
            self._entries = [e for e in self._entries if e.scan_id != entry.scan_id]
            self._entries.append(entry)
            # 按工作区分组裁剪
            self._trim_per_workspace()
            self._save()

    def workspace_history(self, workspace_id: str, limit: int = 0) -> tuple[ScanHistoryEntry, ...]:
        """返回指定工作区的历史条目（按 ``finished_at`` 倒序）。

        :param workspace_id: 工作区 ID
        :param limit: 最大返回条目数；``0`` 表示不限制
        :return: 历史条目元组（最新在前）
        """
        with self._lock:
            items = [e for e in self._entries if e.workspace_id == workspace_id]
            items.sort(key=lambda e: e.finished_at, reverse=True)
            if limit > 0:
                items = items[:limit]
            return tuple(items)

    def latest_entry(self, workspace_id: str) -> ScanHistoryEntry | None:
        """返回指定工作区的最新一条历史（无历史返回 ``None``）。"""
        items = self.workspace_history(workspace_id, limit=1)
        return items[0] if items else None

    def previous_entry(self, workspace_id: str, current_scan_id: str) -> ScanHistoryEntry | None:
        """返回指定工作区在 ``current_scan_id`` 之前最近一条历史。

        用于对比当前扫描与上次扫描：传入当前 ``scan_id``，在历史列表（按
        ``finished_at`` 倒序）中定位该条目，返回紧随其后的下一条（即更早一次
        的扫描）。若 ``current_scan_id`` 不在历史中或无更早历史，返回 ``None``。

        :param workspace_id: 工作区 ID
        :param current_scan_id: 当前扫描 ID
        :return: 上一次扫描条目；无则 ``None``
        """
        with self._lock:
            items = self.workspace_history(workspace_id)
            for idx, item in enumerate(items):
                if item.scan_id == current_scan_id and idx + 1 < len(items):
                    return items[idx + 1]
            return None

    def clear_workspace(self, workspace_id: str) -> int:
        """清空指定工作区的全部历史，返回被清除的条目数。"""
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.workspace_id != workspace_id]
            removed = before - len(self._entries)
            if removed > 0:
                self._save()
            return removed

    def clear_all(self) -> int:
        """清空全部历史，返回被清除的条目数。"""
        with self._lock:
            removed = len(self._entries)
            self._entries.clear()
            if removed > 0:
                self._save()
            return removed

    def all_entries(self) -> tuple[ScanHistoryEntry, ...]:
        """返回全部历史条目快照（不可变元组）。"""
        with self._lock:
            return tuple(self._entries)

    def _trim_per_workspace(self) -> None:
        """按工作区分组裁剪到 ``max_per_ws`` 条最新记录。"""
        # 按 finished_at 倒序排序后分组取前 N
        sorted_entries = sorted(self._entries, key=lambda e: e.finished_at, reverse=True)
        per_ws_count: dict[str, int] = {}
        kept: list[ScanHistoryEntry] = []
        for entry in sorted_entries:
            count = per_ws_count.get(entry.workspace_id, 0)
            if count < self._max_per_ws:
                kept.append(entry)
                per_ws_count[entry.workspace_id] = count + 1
        # 保留原始插入顺序（按 finished_at 升序），便于序列化稳定
        kept.sort(key=lambda e: e.finished_at)
        self._entries = kept

    def _load(self) -> list[ScanHistoryEntry]:
        """从磁盘加载历史条目；文件不存在或损坏时返回空列表。"""
        if not self._path.exists():
            return []
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("历史存储文件读取失败: %s", exc)
            return []
        if not isinstance(payload, dict) or payload.get("version") != HISTORY_VERSION:
            logger.warning(
                "历史存储版本不兼容，跳过: %s", payload.get("version") if isinstance(payload, dict) else None
            )
            return []
        raw_entries = payload.get("entries", [])
        if not isinstance(raw_entries, list):
            return []
        result: list[ScanHistoryEntry] = []
        for raw in raw_entries:
            # 跳过非 dict 条目（如序列化损坏产生的字符串/数值）
            if not isinstance(raw, dict):
                logger.warning("历史条目格式异常（非 dict），跳过: %r", raw)
                continue
            try:
                result.append(ScanHistoryEntry.from_dict(raw))
            except Exception as exc:  # 单条损坏不阻塞其余
                logger.warning("历史条目反序列化失败，跳过: %s", exc)
        return result

    def _save(self) -> None:
        """原子写回磁盘：写入临时文件后 ``Path.replace`` 覆盖。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            payload = {
                "version": HISTORY_VERSION,
                "entries": [e.to_dict() for e in self._entries],
            }
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            logger.error("写入历史存储失败: %s", exc)
