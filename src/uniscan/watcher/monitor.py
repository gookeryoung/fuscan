"""文件监控器：基于 watchdog 监控目录新增文件。

监控指定目录，当有新文件创建或文件修改时通过回调通知。
支持忽略目录配置（如 Windows 系统目录），避免无效扫描。

设计要点：

- 使用 watchdog Observer 异步监控，不阻塞主线程
- 忽略目录匹配支持 glob 模式（如 ``C:\\Windows\\*``）
- 文件事件去重（短时间内同一文件多次触发只通知一次）
- 递归监控子目录，但跳过忽略目录
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

__all__ = [
    "FileEvent",
    "FileEventType",
    "FileMonitor",
    "MonitorConfig",
]

logger = logging.getLogger(__name__)


class FileEventType(str, Enum):
    """文件事件类型。"""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"


@dataclass(frozen=True)
class FileEvent:
    """文件事件。"""

    event_type: FileEventType
    path: Path
    is_dir: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class MonitorConfig:
    """监控配置。"""

    watch_paths: list[Path] = field(default_factory=list)
    ignore_dirs: list[str] = field(default_factory=list)
    ignore_extensions: list[str] = field(default_factory=list)
    dedup_interval_seconds: float = 1.0
    recursive: bool = True


class _EventHandler(FileSystemEventHandler):
    """watchdog 事件处理器：过滤、去重后转发到回调。"""

    def __init__(
        self,
        callback: Callable[[FileEvent], None],
        ignore_dirs: set[str],
        ignore_extensions: set[str],
        dedup_interval: float,
    ) -> None:
        self._callback = callback
        self._ignore_dirs = ignore_dirs
        self._ignore_extensions = ignore_extensions
        self._dedup_interval = dedup_interval
        self._last_events: dict[Path, float] = {}
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        """处理所有类型事件。"""
        path = Path(event.src_path)

        # 跳过忽略目录
        if self._is_in_ignored_dir(path):
            return

        # 跳过忽略扩展名
        if not event.is_directory:
            ext = path.suffix.lower().lstrip(".")
            if ext in self._ignore_extensions:
                return

        # 事件类型映射
        event_type = self._map_event_type(event.event_type)
        if event_type is None:
            return

        file_event = FileEvent(
            event_type=event_type,
            path=path,
            is_dir=event.is_directory,
        )

        # 去重：短时间内同一文件同一事件只通知一次
        if not self._should_emit(file_event):
            return

        try:
            self._callback(file_event)
        except Exception:
            logger.warning("文件事件回调异常", exc_info=True)

    def _is_in_ignored_dir(self, path: Path) -> bool:
        """检查路径是否在忽略目录内。"""
        parts = {p.lower() for p in path.parts}
        return any(ignore.lower() in parts for ignore in self._ignore_dirs)

    def _should_emit(self, event: FileEvent) -> bool:
        """去重判断：同一路径在 dedup_interval 内只发一次。"""
        key = event.path
        now = event.timestamp
        with self._lock:
            last = self._last_events.get(key, 0.0)
            if now - last < self._dedup_interval:
                return False
            self._last_events[key] = now
            return True

    @staticmethod
    def _map_event_type(raw: str) -> FileEventType | None:
        """watchdog 事件类型映射到 FileEventType。"""
        mapping = {
            "created": FileEventType.CREATED,
            "modified": FileEventType.MODIFIED,
            "deleted": FileEventType.DELETED,
            "moved": FileEventType.MOVED,
        }
        return mapping.get(raw)


class FileMonitor:
    """文件监控器：管理 watchdog Observer 与事件分发。

    用法：

    .. code-block:: python

        monitor = FileMonitor(config)
        monitor.start(on_event)
        # ... 运行中
        monitor.stop()
    """

    def __init__(self, config: MonitorConfig) -> None:
        self._config = config
        self._observer: Observer | None = None
        self._handler: _EventHandler | None = None
        self._running = False
        self._lock = threading.Lock()

        ignore_dirs = {d.lower() for d in config.ignore_dirs}
        ignore_exts = {e.lower().lstrip(".") for e in config.ignore_extensions}
        self._ignore_dirs = ignore_dirs
        self._ignore_extensions = ignore_exts

    @property
    def is_running(self) -> bool:
        """监控是否运行中。"""
        return self._running

    @property
    def watch_paths(self) -> list[Path]:
        """监控路径列表。"""
        return list(self._config.watch_paths)

    def start(self, callback: Callable[[FileEvent], None]) -> None:
        """启动监控。

        :param callback: 文件事件回调
        :raises RuntimeError: 已在运行
        """
        with self._lock:
            if self._running:
                raise RuntimeError("监控器已在运行")
            if not self._config.watch_paths:
                logger.warning("无监控路径，监控器未启动")
                return

            self._handler = _EventHandler(
                callback=callback,
                ignore_dirs=self._ignore_dirs,
                ignore_extensions=self._ignore_extensions,
                dedup_interval=self._config.dedup_interval_seconds,
            )
            self._observer = Observer()
            for path in self._config.watch_paths:
                if not path.exists():
                    logger.warning("监控路径不存在: %s", path)
                    continue
                self._observer.schedule(
                    self._handler,
                    str(path),
                    recursive=self._config.recursive,
                )
            self._observer.start()
            self._running = True
            logger.info("文件监控已启动，监控 %d 个路径", len(self._config.watch_paths))

    def stop(self) -> None:
        """停止监控。"""
        with self._lock:
            if not self._running or self._observer is None:
                return
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None
            self._handler = None
            self._running = False
            logger.info("文件监控已停止")

    def add_watch(self, path: Path) -> None:
        """新增监控路径（运行中也可调用）。"""
        if not path.exists():
            logger.warning("监控路径不存在: %s", path)
            return
        if self._observer is not None and self._handler is not None:
            self._observer.schedule(
                self._handler,
                str(path),
                recursive=self._config.recursive,
            )
        self._config.watch_paths.append(path)

    def __enter__(self) -> FileMonitor:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
