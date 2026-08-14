"""备份元数据存储与完整性校验。

每次 ``replace_in_file`` 备份源文件后，向 ``~/.fuscan/state/backup_manifest.json``
追加一条 :class:`BackupEntry` 记录（源路径、备份路径、替换前后 sha256、时间戳），
供后续操作校验：

- :func:`restore_from_backup` 撤销前校验 ``.bak`` 的 size + sha256 与 manifest 一致，
  避免 .bak 被外部修改/部分损坏后恢复出损坏文件
- :func:`replace_in_file` 替换前比对当前源文件 sha256 与 manifest 中记录的
  ``post_sha256``（替换后 sha256），一致则跳过替换，避免重复扫描时覆盖原始备份

manifest 文件格式（JSON，UTF-8）::

    {
      "version": 1,
      "entries": {
        "<src_path 绝对路径字符串>": {
          "backup_path": "...",
          "src_size": 1024,
          "src_sha256": "...",
          "post_sha256": "...",
          "replaced_at": "2026-08-12T10:30:00"
        }
      }
    }

``entries`` 按 ``src_path`` 索引最新条目：同一源文件多次替换仅保留最新记录，
但 ``.bak`` 文件本身在保留相对路径模式下会被复用（iter-01 暂未实现复用，iter-04 边界优化）。

并发安全：用 :class:`threading.RLock` 保护读写，支持后台线程替换（iter-03）。

公共 API：

- :class:`BackupEntry`：单条备份元数据
- :class:`BackupManifest`：manifest 持久化与查询
- :func:`default_state_dir`：默认状态目录 ``~/.fuscan/state``
- :func:`default_manifest_path`：默认 manifest 文件路径
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fuscan import config as config_module

__all__ = [
    "BackupEntry",
    "BackupManifest",
    "default_manifest_path",
    "default_state_dir",
]

logger = logging.getLogger(__name__)

# manifest 文件格式版本，字段语义变更时递增以触发旧文件失效
_MANIFEST_VERSION: int = 1


def default_state_dir() -> Path:
    """返回默认状态目录：``~/.fuscan/state``。

    用于存放跨会话的运行时状态（备份 manifest、未来扩展的撤销栈等），
    与 ``~/.fuscan/config.yaml``（配置）、``~/.fuscan/backup``（备份数据）
    分离，便于独立备份/清理。

    :return: 状态目录路径（路径可能尚不存在，调用方按需 ``mkdir``）
    """
    # 运行时读取 ``config_module.CONFIG_DIR`` 当前值，支持测试 monkeypatch
    return config_module.CONFIG_DIR / "state"


def default_manifest_path() -> Path:
    """返回默认 manifest 文件路径：``~/.fuscan/state/backup_manifest.json``。

    :return: manifest 文件路径（路径可能尚不存在，调用方按需 ``mkdir``）
    """
    return default_state_dir() / "backup_manifest.json"


@dataclass(frozen=True)
class BackupEntry:
    """单条备份元数据。

    - ``src_path``：源文件绝对路径字符串（作为 manifest 索引键）
    - ``backup_path``：``.bak`` 备份文件绝对路径字符串
    - ``src_size``：替换前源文件字节数（与 ``.bak`` 大小一致）
    - ``src_sha256``：替换前源文件 sha256（与 ``.bak`` 内容 sha256 一致，
      用于撤销前校验 ``.bak`` 完整性）
    - ``post_sha256``：替换后源文件 sha256（用于重复扫描检测：当前源文件
      sha256 与此一致 → 文件已被替换且未修改 → 跳过替换）
    - ``replaced_at``：替换完成时间戳（ISO 8601 字符串，便于人工排查）
    """

    src_path: str
    backup_path: str
    src_size: int
    src_sha256: str
    post_sha256: str
    replaced_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackupEntry:
        """从 dict 反序列化为 :class:`BackupEntry`。

        容忍多余字段（向前兼容），但必要字段缺失时抛 ``KeyError`` 由调用方处理。

        :param data: manifest 中单条 entry 的 dict
        :return: :class:`BackupEntry` 实例
        """
        return cls(
            src_path=str(data["src_path"]),
            backup_path=str(data["backup_path"]),
            src_size=int(data["src_size"]),
            src_sha256=str(data["src_sha256"]),
            post_sha256=str(data["post_sha256"]),
            replaced_at=str(data["replaced_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict 写入 manifest。"""
        return asdict(self)


def _sha256_bytes(data: bytes) -> str:
    """计算字节流的 SHA-256 十六进制摘要。

    与 :func:`fuscan.cache.hashes.hash_bytes` 不同，manifest 校验统一用 SHA-256
    而非按大小分流：备份文件通常较小（< 16MB），SHA-256 的 CPython 内建实现
    足够快；统一算法便于跨版本/跨平台校验一致性。

    :param data: 任意字节流
    :return: 64 字符十六进制字符串
    """
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    """计算文件内容的 SHA-256 十六进制摘要。

    :param path: 文件路径
    :return: 64 字符十六进制字符串
    :raises OSError: 文件读取失败
    """
    return _sha256_bytes(path.read_bytes())


class BackupManifest:
    """备份元数据持久化与查询。

    线程安全：内部 :class:`threading.RLock` 保护 ``_entries`` 读写与持久化，
    支持后台线程并发调用 :meth:`record` / :meth:`verify` / :meth:`find_by_src`。

    持久化策略：

    - 构造时从 ``manifest_path`` 加载现有 entries（文件不存在 → 空 entries）
    - :meth:`record` / :meth:`remove` 立即写回磁盘（atomic write）
    - :meth:`verify` / :meth:`find_by_src` 仅读内存索引，不触发 I/O

    文件损坏容错：加载时 JSON 解析失败 → 记录 WARNING 并重置为空 entries，
    避免损坏的 manifest 阻塞后续替换操作（最坏情况是丢失重复扫描检测能力）。
    """

    def __init__(self, manifest_path: Path | None = None) -> None:
        """初始化 manifest，从指定路径加载现有 entries。

        :param manifest_path: manifest 文件路径，默认 :func:`default_manifest_path`
        """
        self._path = manifest_path if manifest_path is not None else default_manifest_path()
        self._lock = threading.RLock()
        self._entries: dict[str, BackupEntry] = {}
        self._load()

    def _load(self) -> None:
        """从磁盘加载 manifest 文件到内存索引。

        文件不存在 → 空索引。JSON 解析失败或格式异常 → 记录 WARNING 并重置为空，
        避免损坏的 manifest 阻塞替换流程。
        """
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            entries_data: dict[str, Any] = data.get("entries", {}) if isinstance(data, dict) else {}
            loaded: dict[str, BackupEntry] = {}
            for src_path, entry_dict in entries_data.items():
                if not isinstance(entry_dict, dict):
                    continue
                try:
                    entry = BackupEntry.from_dict(entry_dict)
                    loaded[src_path] = entry
                except (KeyError, TypeError, ValueError):
                    logger.warning("manifest 条目字段缺失或类型异常，跳过: %s", src_path)
            self._entries = loaded
        except (json.JSONDecodeError, OSError):
            logger.warning("manifest 加载失败，重置为空: %s", self._path, exc_info=True)
            self._entries = {}

    def _save_locked(self) -> None:
        """持久化内存索引到磁盘（调用方需持锁）。

        使用 :func:`fuscan.utils.io.atomic_write_text` 原子写入，避免半写损坏。
        父目录不存在时自动创建。
        """
        from fuscan.utils.io import atomic_write_text

        data = {
            "version": _MANIFEST_VERSION,
            "entries": {src: entry.to_dict() for src, entry in self._entries.items()},
        }
        atomic_write_text(self._path, json.dumps(data, ensure_ascii=False, indent=2))

    def record(
        self,
        src: Path,
        backup: Path,
        src_content: bytes,
        post_content: bytes,
    ) -> BackupEntry:
        """记录一次替换的元数据并持久化。

        计算替换前后内容的 sha256，构造 :class:`BackupEntry` 写入内存索引与磁盘。
        已有同 ``src_path`` 条目会被覆盖（仅保留最新替换记录）。

        :param src: 源文件路径
        :param backup: ``.bak`` 备份文件路径
        :param src_content: 替换前源文件字节内容（用于计算 src_sha256）
        :param post_content: 替换后源文件字节内容（用于计算 post_sha256）
        :return: 写入的 :class:`BackupEntry`
        """
        entry = BackupEntry(
            src_path=str(src.resolve()),
            backup_path=str(backup.resolve()),
            src_size=len(src_content),
            src_sha256=_sha256_bytes(src_content),
            post_sha256=_sha256_bytes(post_content),
            replaced_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self._lock:
            self._entries[entry.src_path] = entry
            self._save_locked()
        logger.debug("manifest 记录替换: %s -> %s", entry.src_path, entry.backup_path)
        return entry

    def verify(self, backup: Path) -> bool:
        """校验 ``.bak`` 备份文件完整性。

        校验项：

        1. ``.bak`` 文件存在
        2. manifest 中存在该 ``backup_path`` 对应条目
        3. ``.bak`` 实际大小 == 条目 ``src_size``
        4. ``.bak`` 实际 sha256 == 条目 ``src_sha256``

        任一项不满足 → 返回 ``False``，调用方应拒绝撤销并提示用户。

        :param backup: ``.bak`` 备份文件路径
        :return: 完整性校验通过返回 ``True``
        """
        with self._lock:
            entry = self._find_by_backup_locked(backup)
            if entry is None:
                logger.warning("manifest 中无备份条目: %s", backup)
                return False
        if not backup.exists():
            logger.warning("备份文件不存在: %s", backup)
            return False
        try:
            actual_size = backup.stat().st_size
            if actual_size != entry.src_size:
                logger.warning(
                    "备份文件大小不匹配: 期望 %d, 实际 %d (%s)",
                    entry.src_size,
                    actual_size,
                    backup,
                )
                return False
            actual_sha = _sha256_file(backup)
            if actual_sha != entry.src_sha256:
                logger.warning(
                    "备份文件 sha256 不匹配: 期望 %s, 实际 %s (%s)",
                    entry.src_sha256,
                    actual_sha,
                    backup,
                )
                return False
        except OSError:
            logger.warning("备份文件读取失败: %s", backup, exc_info=True)
            return False
        return True

    def find_by_src(self, src: Path) -> BackupEntry | None:
        """按源文件路径查找 manifest 条目。

        :param src: 源文件路径
        :return: :class:`BackupEntry` 或 ``None``（无记录）
        """
        key = str(src.resolve())
        with self._lock:
            return self._entries.get(key)

    def find_by_post_hash(self, post_sha256: str) -> BackupEntry | None:
        """按替换后 sha256 查找 manifest 条目（用于重复扫描检测）。

        :param post_sha256: 替换后 sha256 字符串
        :return: :class:`BackupEntry` 或 ``None``
        """
        with self._lock:
            for entry in self._entries.values():
                if entry.post_sha256 == post_sha256:
                    return entry
        return None

    def remove(self, src: Path) -> None:
        """删除源文件对应的 manifest 条目并持久化。

        撤销替换后调用，清理 manifest 中过期记录。

        :param src: 源文件路径
        """
        key = str(src.resolve())
        with self._lock:
            if key in self._entries:
                del self._entries[key]
                self._save_locked()

    def _find_by_backup_locked(self, backup: Path) -> BackupEntry | None:
        """按备份路径查找条目（调用方需持锁）。

        :param backup: ``.bak`` 备份文件路径
        :return: :class:`BackupEntry` 或 ``None``
        """
        backup_key = str(backup.resolve())
        for entry in self._entries.values():
            if entry.backup_path == backup_key:
                return entry
        return None
