"""文件监控控制器：watchdog 事件驱动 + 单文件扫描 + 实时命中推送。

在文件监控独立页面中，用户可拖拽或选择文件夹加入监控；watchdog
``Observer`` 监听目录树变更（创建/修改/移动），事件经噪声目录过滤后
按文件路径防抖（300ms 单发 QTimer 合并同文件多次事件），触发
:meth:`Scanner.scan_file` 对变动文件单独扫描。命中规则时：

- 追加到 :class:`FileMonitorModel`（QML ListView 实时展示）
- emit :signal:`hitFound` 信号（携带命中详情 dict），供 ``app.py`` 触发
  系统托盘通知与声音提示

规则来源：全局规则集（内置 + 已加载的全局规则文件），与工作区无关。
监听 :signal:`RulesController.rulesetChanged`，规则集变更时清空 Scanner
缓存，下次事件用新规则集重建 Scanner。

监控目录列表持久化到 ``~/.fuscan/monitor.json``，应用启动时自动恢复；
``monitoring_enabled`` 状态同样持久化，恢复时若为 ``True`` 自动启动 Observer。

公共 API：

- :class:`FileMonitorController`：``QObject`` 子类
- :meth:`FileMonitorController.addWatch`：添加监控目录
- :meth:`FileMonitorController.removeWatch`：移除监控目录
- :meth:`FileMonitorController.setMonitoringEnabled`：启停监控
- :meth:`FileMonitorController.clearHits`：清空命中记录
- :meth:`FileMonitorController.cleanup`：窗口关闭时统一清理
"""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from typing_extensions import override

try:
    from PySide2.QtCore import Property, QObject, QTimer, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot  # pyrefly: ignore [missing-import]

if TYPE_CHECKING:
    # watchdog 仅用于类型注解（注解因 from __future__ import annotations 为字符串，
    # 运行时不求值）。运行时通过 _build_watchdog_handler_class / __init__ 内延迟导入，
    # 避免 import fuscan.app 启动阶段加载 watchdog。
    from watchdog.events import FileSystemEvent

    # _WatchdogHandler 通过 PEP 562 __getattr__ 延迟创建（运行时动态），
    # 此处仅声明类型供静态检查器识别，避免 F821。
    _WatchdogHandler: type

from fuscan.gui.models.file_monitor_model import FileMonitorModel
from fuscan.gui.severity_utils import severity_text
from fuscan.scanner.scanner import Scanner

__all__ = ["FileMonitorController"]

logger = logging.getLogger(__name__)

# 防抖窗口（毫秒）：同文件多次事件合并为一次扫描
_DEBOUNCE_MS = 300

# 匹配文本摘要最大长度（避免 QML 列表过宽）
_MATCH_TEXT_MAX = 120

# 最近事件日志最大保留条数（FIFO，超过后丢弃最旧）
_RECENT_EVENTS_MAX = 50

# 过滤统计轮询间隔（毫秒）：handler 线程仅递增计数器，controller 每 N ms
# 轮询一次并 emit 信号刷新 QML，避免逐事件信号开销
_STATS_POLL_MS = 500

# 噪声目录名（路径中包含这些片段的直接跳过，避免 IDE/构建产物刷屏）
_IGNORE_DIR_PARTS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".idea",
        ".vscode",
        ".mypy_cache",
        ".pyrefly_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "target",
        ".tox",
        ".eggs",
    }
)


class _EventEmitter(QObject):  # pyrefly: ignore [invalid-inheritance]
    """跨线程事件信号桥（watchdog 线程 → Qt 主线程）。

    watchdog ``FileSystemEventHandler`` 回调运行在 Observer 线程，
    Qt 信号发射是线程安全的，``AutoConnection`` 会自动跨线程排队到主线程。
    """

    event_received = Signal(str, str)  # path, event_type

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


def _build_watchdog_handler_class() -> type:
    """构建 ``_WatchdogHandler`` 类（延迟导入 ``watchdog.events``）。

    ``_WatchdogHandler`` 继承 ``FileSystemEventHandler``，类定义时需要基类，
    故整体延迟到首次访问 ``_WatchdogHandler`` 时（通过 PEP 562 ``__getattr__``）
    才导入 watchdog 并创建类，避免 ``import fuscan.app`` 启动阶段加载 watchdog。
    """
    from watchdog.events import FileSystemEventHandler

    class _WatchdogHandler(FileSystemEventHandler):
        """watchdog 事件处理器：过滤噪声目录后通过信号转发到主线程。

        :param emitter: 跨线程信号桥
        :param scan_extensions: 全局规则集的扩展名白名单；``None`` 表示不过滤，
            空 tuple 表示都不扫描（与 Scanner ``_should_scan`` 语义一致）
        :param filter_stats: 共享过滤统计字典（controller 持有，handler 累加）。
            单写单读（handler 线程写，controller 线程读），GIL 保证安全。
            仅做整数递增，不 emit 信号，避免逐事件信号开销。
        """

        def __init__(
            self,
            emitter: _EventEmitter,
            scan_extensions: tuple[str, ...] | None,
            filter_stats: dict[str, int] | None = None,
        ) -> None:
            super().__init__()
            self._emitter = emitter
            self._filter_stats: dict[str, int] = filter_stats if filter_stats is not None else {}
            # 三态语义（与 Scanner._should_scan 一致）：
            # - None → 不过滤（扫描所有文件）
            # - 空 frozenset → 全部跳过（用户取消所有扩展名勾选）
            # - 非空 frozenset → 扩展名白名单
            if scan_extensions is None:
                self._scan_extensions: frozenset[str] | None = None
            else:
                self._scan_extensions = frozenset(ext.lower().lstrip(".") for ext in scan_extensions)

        def _should_handle(self, path: str | bytes) -> bool:
            """判断该路径是否需要触发扫描。

            - 路径任一部分命中噪声目录名 → 跳过（计入 ignored_dir）
            - ``scan_extensions`` 为 ``None`` → 通过（扫描所有文件）
            - ``scan_extensions`` 为空 frozenset → 全部跳过（计入 filtered_ext）
            - 否则按扩展名白名单过滤（不匹配计入 filtered_ext）
            """
            from pathlib import PurePath

            if isinstance(path, bytes):
                path = path.decode("utf-8", errors="replace")
            p = PurePath(path)
            for part in p.parts:
                if part.lower() in _IGNORE_DIR_PARTS:
                    self._filter_stats["ignored_dir"] = self._filter_stats.get("ignored_dir", 0) + 1
                    return False
            if self._scan_extensions is None:
                return True
            if not self._scan_extensions:
                self._filter_stats["filtered_ext"] = self._filter_stats.get("filtered_ext", 0) + 1
                return False
            ext = p.suffix.lower().lstrip(".")
            if ext not in self._scan_extensions:
                self._filter_stats["filtered_ext"] = self._filter_stats.get("filtered_ext", 0) + 1
                return False
            return True

        @override
        def on_created(self, event: FileSystemEvent) -> None:
            """文件创建事件。"""
            if event.is_directory:
                self._filter_stats["dir_events"] = self._filter_stats.get("dir_events", 0) + 1
                return
            if self._should_handle(event.src_path):
                self._emitter.event_received.emit(event.src_path, "created")  # pyrefly: ignore [missing-attribute]

        @override
        def on_modified(self, event: FileSystemEvent) -> None:
            """文件修改事件。"""
            if event.is_directory:
                self._filter_stats["dir_events"] = self._filter_stats.get("dir_events", 0) + 1
                return
            if self._should_handle(event.src_path):
                self._emitter.event_received.emit(event.src_path, "modified")  # pyrefly: ignore [missing-attribute]

        @override
        def on_moved(self, event: FileSystemEvent) -> None:
            """文件移动事件（按目标路径扫描）。"""
            if event.is_directory:
                self._filter_stats["dir_events"] = self._filter_stats.get("dir_events", 0) + 1
                return
            if self._should_handle(event.dest_path):
                self._emitter.event_received.emit(event.dest_path, "moved")  # pyrefly: ignore [missing-attribute]

    return _WatchdogHandler


# PEP 562: _WatchdogHandler 延迟创建，首次访问时导入 watchdog 并构建类，
# 随后缓存到模块命名空间。import fuscan.app 不会触发 watchdog 加载。
def __getattr__(name: str) -> object:
    if name == "_WatchdogHandler":
        cls = _build_watchdog_handler_class()
        globals()[name] = cls  # 缓存，后续直接从 __dict__ 取
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class FileMonitorController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """文件监控控制器。

    :param rules_controller: 规则控制器（共享全局规则集）
    :param parent: 父 QObject
    :param _observer_factory: 测试用 Observer 工厂（生产环境 None，使用默认 ``Observer``）
    """

    # 命中信号：携带命中详情 dict，app.py 据此触发托盘通知 + 声音
    # dict 字段：time/path/rule_name/severity/match_text
    hitFound = Signal(dict)
    directoryAdded = Signal(str)
    directoryRemoved = Signal(str)
    monitorStateChanged = Signal(bool)
    watchedDirectoriesChanged = Signal()
    # 事件日志变更信号——收到任意文件变更事件时 emit，
    # 驱动 QML 刷新 eventCount / recentEvents 属性，让用户知道监控在工作
    eventLogChanged = Signal()
    # model 属性变更信号——model 实例在 __init__ 后稳定不变，
    # 实际不会 emit；声明仅为满足 QML「属性必须可 NOTIFY」要求，
    # 避免 QML 绑定警告「depends on non-NOTIFYable properties」。
    modelChanged = Signal()

    def __init__(
        self,
        rules_controller: object,
        parent: QObject | None = None,
        *,
        _observer_factory: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self._rules_controller = rules_controller
        self._model = FileMonitorModel(parent=self)
        # watchdog Observer 实例（启用监控时构造，关闭时 stop+join）
        self._observer: object | None = None
        # Observer 工厂：测试注入用 _observer_factory；生产环境延迟导入 watchdog.observers.Observer
        if _observer_factory is not None:
            self._observer_factory: Callable[[], object] = _observer_factory
        else:
            from watchdog.observers import Observer

            self._observer_factory = Observer
        # 当前是否启用监控
        self._monitoring_enabled: bool = False
        # 监控目录列表（path_str → watchdog watch handle）
        # watchdog Observer.schedule 返回一个 EmitWatchDogWatch 对象，
        # 用于 observer.unschedule(watch) 精确移除
        self._watched: dict[str, object] = {}
        # Scanner 缓存（key=id(ruleset)），避免每次事件重编译规则
        self._scanner_cache: dict[int, Scanner] = {}
        # 跨线程事件信号桥
        self._emitter = _EventEmitter(self)
        self._emitter.event_received.connect(self._on_event_received)  # pyrefly: ignore [missing-attribute]
        # 当前 watchdog handler（共享 scan_extensions；规则集变更时重建）
        self._handler: _WatchdogHandler | None = None  # noqa: F821
        # 防抖定时器：path_str → QTimer（单发，300ms）
        self._debounce_timers: dict[str, QTimer] = {}
        # 持久化文件锁（多线程写 monitor.json 互斥；测试并发用）
        self._persist_lock = threading.Lock()
        # 事件日志：累计事件计数 + 最近 N 条事件摘要（不限命中）
        # 让用户能直观看到「监控在工作」而非仅「等待文件变更」
        self._event_count: int = 0
        self._recent_events: deque[dict[str, str]] = deque(maxlen=_RECENT_EVENTS_MAX)
        # 过滤统计（共享引用，handler 线程累加，controller 线程轮询读取）
        # 单写单读 + GIL 保证安全，仅整数递增不 emit 信号，无性能开销
        self._filter_stats: dict[str, int] = {"ignored_dir": 0, "filtered_ext": 0, "dir_events": 0}
        # 过滤统计轮询定时器：500ms 间隔，有变化时 emit eventLogChanged
        self._stats_timer: QTimer = QTimer(self)
        self._stats_timer.setSingleShot(False)
        self._stats_timer.setInterval(_STATS_POLL_MS)
        self._stats_timer.timeout.connect(self._poll_filter_stats)
        # 上次轮询的过滤统计快照（用于检测变化）
        self._last_filter_stats: tuple[int, int, int] = (0, 0, 0)

        # 监听规则集变化
        rules_controller.rulesetChanged.connect(self._on_ruleset_changed)  # pyrefly: ignore [missing-attribute]
        # 初始化 handler 用当前规则集的 scan_extensions
        self._rebuild_handler()

        # 从 monitor.json 恢复监控目录列表与启用状态
        self._load_persisted()

    # ----------------------------- 公共属性 -----------------------------

    @Property(QObject, notify=modelChanged)  # pyrefly: ignore [not-callable]
    def model(self) -> FileMonitorModel:
        """命中记录列表模型（QML ListView 绑定）。

        用 ``QObject`` 作为 Property 类型，避免 PySide2/6 元类型系统对
        ``QAbstractListModel*`` 未注册导致的 ``QMetaProperty::read`` 警告
        （``FileMonitorModel`` 继承 ``QAbstractListModel``，``@Property`` 会
        生成 ``Q_PROPERTY(QAbstractListModel* model ...)``，该指针类型未
        ``qRegisterMetaType``）。QML 侧仍可通过 ``model.count``/``model.time``
        等 role 正常访问，因 ``FileMonitorModel`` 已通过 ``qmlRegisterType``
        注册到 ``fuscan.models`` URI。
        """
        return self._model

    @Property(bool, notify=monitorStateChanged)  # pyrefly: ignore [not-callable]
    def monitoringEnabled(self) -> bool:
        """当前是否启用监控。"""
        return self._monitoring_enabled

    @Property("QVariantList", notify=watchedDirectoriesChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def watchedDirectories(self) -> list[str]:
        """当前监控目录列表（字符串路径）。"""
        return list(self._watched.keys())

    @Property(int, notify=watchedDirectoriesChanged)  # pyrefly: ignore [not-callable]
    def watchedCount(self) -> int:
        """监控目录数。"""
        return len(self._watched)

    @Property(int, notify=eventLogChanged)  # pyrefly: ignore [not-callable]
    def eventCount(self) -> int:
        """自监控启动以来累计收到的文件变更事件数。"""
        return self._event_count

    @Property("QVariantList", notify=eventLogChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def recentEvents(self) -> list[dict[str, str]]:
        """最近 N 条文件变更事件摘要（FIFO，最多 ``_RECENT_EVENTS_MAX`` 条）。

        每项为 ``{"time": "HH:MM:SS", "path": "...", "event_type": "created"}``。
        """
        return list(self._recent_events)

    @Property(int, notify=eventLogChanged)  # pyrefly: ignore [not-callable]
    def ignoredDirCount(self) -> int:
        """被噪声目录过滤的事件数（.git/node_modules 等）。"""
        return self._filter_stats.get("ignored_dir", 0)

    @Property(int, notify=eventLogChanged)  # pyrefly: ignore [not-callable]
    def filteredExtCount(self) -> int:
        """被扩展名白名单过滤的事件数。"""
        return self._filter_stats.get("filtered_ext", 0)

    @Property(int, notify=eventLogChanged)  # pyrefly: ignore [not-callable]
    def dirEventCount(self) -> int:
        """目录事件数（文件夹创建/修改/移动，不触发文件扫描）。"""
        return self._filter_stats.get("dir_events", 0)

    # ----------------------------- QML Slots -----------------------------

    @Slot(str)  # pyrefly: ignore [not-callable]
    def addWatch(self, path: str) -> bool:
        """添加监控目录。

        路径规范化为绝对路径；已存在则跳过；若监控已启用则立即开始监听。
        持久化到 ``monitor.json``。

        :param path: 目录路径字符串
        :return: 实际添加返回 ``True``，已存在或路径无效返回 ``False``
        """
        if not path:
            return False
        try:
            abs_path = str(Path(path).resolve())
        except (OSError, ValueError) as exc:
            logger.warning("监控路径解析失败 %s: %s", path, exc)
            return False
        if not Path(abs_path).is_dir():
            logger.warning("监控路径不是目录: %s", abs_path)
            return False
        if abs_path in self._watched:
            return False
        # 若监控启用，立即 schedule
        if self._observer is not None and self._handler is not None:
            try:
                watch = self._observer.schedule(self._handler, abs_path, recursive=True)  # pyrefly: ignore [missing-attribute]
                self._watched[abs_path] = watch
            except OSError as exc:
                logger.warning("监控目录失败 %s: %s", abs_path, exc)
                return False
        else:
            self._watched[abs_path] = None
        self._persist()
        self.watchedDirectoriesChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.directoryAdded.emit(abs_path)  # pyrefly: ignore [missing-attribute]
        return True

    @Slot(str)  # pyrefly: ignore [not-callable]
    def removeWatch(self, path: str) -> bool:
        """移除监控目录。

        移除后若监控目录列表清空且当前启用监控，自动停用以保持状态一致
        （避免 UI 显示「监控中」但实际无目录可监控，且开关被禁用无法关闭）。

        :param path: 目录路径字符串（绝对或相对）
        :return: 实际移除返回 ``True``，未找到返回 ``False``
        """
        if not path:
            return False
        try:
            abs_path = str(Path(path).resolve())
        except (OSError, ValueError):
            return False
        if abs_path not in self._watched:
            return False
        # 若 observer 持有 watch handle，显式 unschedule
        watch = self._watched.pop(abs_path)
        if watch is not None and self._observer is not None:
            with _suppress_observer_errors():
                self._observer.unschedule(watch)  # pyrefly: ignore [missing-attribute]
        self._persist()
        self.watchedDirectoriesChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.directoryRemoved.emit(abs_path)  # pyrefly: ignore [missing-attribute]
        # 监控目录清空后自动停用监控，保持状态一致
        if not self._watched and self._monitoring_enabled:
            self.setMonitoringEnabled(False)
        return True

    @Slot(bool)  # pyrefly: ignore [not-callable]
    def setMonitoringEnabled(self, enabled: bool) -> None:
        """启用或停用监控。

        启用时：构造 Observer（若尚未构造），重新 schedule 所有已添加目录。
        停用时：stop + join Observer，清空 watch handle（目录列表保留，
        下次启用时重新 schedule）。

        :param enabled: ``True`` 启用，``False`` 停用
        """
        if enabled == self._monitoring_enabled:
            return
        if enabled:
            # 重新启用时重置事件日志与过滤统计，从 0 开始计数本次会话
            self._event_count = 0
            self._recent_events.clear()
            for key in self._filter_stats:
                self._filter_stats[key] = 0
            self._start_observer()
        else:
            self._stop_observer()
        self._monitoring_enabled = enabled
        self._persist()
        self.monitorStateChanged.emit(enabled)  # pyrefly: ignore [missing-attribute]
        if enabled:
            self.eventLogChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def clearHits(self) -> None:
        """清空命中记录列表。"""
        self._model.clear()

    @Slot()  # pyrefly: ignore [not-callable]
    def clearEvents(self) -> None:
        """清空事件日志（累计计数 + 最近事件列表 + 过滤统计）。"""
        self._event_count = 0
        self._recent_events.clear()
        for key in self._filter_stats:
            self._filter_stats[key] = 0
        self.eventLogChanged.emit()  # pyrefly: ignore [missing-attribute]

    def cleanup(self) -> None:
        """窗口关闭时统一清理：停用 Observer，取消未触发的防抖定时器。"""
        if self._monitoring_enabled:
            self._stop_observer()
            self._monitoring_enabled = False
        for timer in list(self._debounce_timers.values()):
            timer.stop()
        self._debounce_timers.clear()

    # ----------------------------- 内部实现 -----------------------------

    def _start_observer(self) -> None:
        """启动 watchdog Observer 并 schedule 所有已添加目录。"""
        if self._observer is None:
            self._observer = self._observer_factory()
        # 重建 handler（确保 scan_extensions 与当前规则集一致）
        self._rebuild_handler()
        with contextlib.suppress(RuntimeError):
            # Observer 已启动（重复调用），忽略
            self._observer.start()  # pyrefly: ignore [missing-attribute]
        # 重新 schedule 所有已添加目录（之前未启用时 watch handle 为 None）
        if self._handler is not None:
            new_watched: dict[str, object] = {}
            for path_str in list(self._watched.keys()):
                try:
                    watch = self._observer.schedule(self._handler, path_str, recursive=True)  # pyrefly: ignore [missing-attribute]
                    new_watched[path_str] = watch
                except OSError as exc:
                    logger.warning("监控目录失败 %s: %s", path_str, exc)
                    new_watched[path_str] = None
            self._watched = new_watched
        # 启动过滤统计轮询定时器
        self._stats_timer.start()  # pyrefly: ignore [missing-argument]

    def _stop_observer(self) -> None:
        """停止 watchdog Observer。"""
        self._stats_timer.stop()
        if self._observer is None:
            return
        with _suppress_observer_errors():
            self._observer.stop()  # pyrefly: ignore [missing-attribute]
            self._observer.join(timeout=2.0)  # pyrefly: ignore [missing-attribute]
        # 不销毁 Observer 实例，下次启用直接复用（避免重复构造开销）
        # 但清空 watch handle（下次启用时重新 schedule）
        for path_str in list(self._watched.keys()):
            self._watched[path_str] = None
        # 停止时最后轮询一次，确保 QML 显示最终统计值
        self._poll_filter_stats()

    def _poll_filter_stats(self) -> None:
        """轮询 handler 的过滤统计，有变化时 emit eventLogChanged。

        handler 线程仅递增 dict 值（无信号开销），controller 每 500ms 轮询一次，
        检测到变化才 emit，驱动 QML 刷新 ignoredDirCount 等属性。
        """
        current = (
            self._filter_stats.get("ignored_dir", 0),
            self._filter_stats.get("filtered_ext", 0),
            self._filter_stats.get("dir_events", 0),
        )
        if current != self._last_filter_stats:
            self._last_filter_stats = current
            self.eventLogChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _rebuild_handler(self) -> None:
        """根据当前规则集重建 watchdog handler（刷新 scan_extensions）。"""
        ruleset = self._rules_controller.ruleset  # pyrefly: ignore [missing-attribute]
        scan_extensions = ruleset.scan_extensions if ruleset is not None else None
        # 延迟导入 watchdog：首次调用时构建 handler 类，后续从模块全局缓存取。
        # 不能用 PEP 562 __getattr__（函数内全局名查找不触发），需显式调用工厂。
        handler_cls = globals().get("_WatchdogHandler")
        if handler_cls is None:
            handler_cls = _build_watchdog_handler_class()
            globals()["_WatchdogHandler"] = handler_cls
        self._handler = handler_cls(self._emitter, scan_extensions, self._filter_stats)

    def _on_ruleset_changed(self) -> None:
        """规则集变更：清空 Scanner 缓存，重建 handler，下次事件用新规则集。"""
        self._scanner_cache.clear()
        self._rebuild_handler()
        # 若 observer 正在运行，已 schedule 的 handler 引用不变（watchdog 内部弱引用），
        # 新事件会用新 handler；这里直接替换 _handler 即可（下次 schedule 用新 handler）

    def _get_scanner(self) -> Scanner | None:
        """获取当前规则集对应的 Scanner（按 id(ruleset) 缓存）。

        :return: Scanner 实例；规则集为 ``None`` 时返回 ``None``
        """
        ruleset = self._rules_controller.ruleset  # pyrefly: ignore [missing-attribute]
        if ruleset is None:
            return None
        key = id(ruleset)
        scanner = self._scanner_cache.get(key)
        if scanner is None:
            scanner = Scanner(
                ruleset,
                scan_extensions=ruleset.scan_extensions,
                ignore_dirs=ruleset.ignore_dirs,
                max_workers=1,
            )
            self._scanner_cache[key] = scanner
        return scanner

    def _on_event_received(self, path: str, _event_type: str) -> None:
        """主线程接收 watchdog 事件：启动/重启该路径的防抖定时器。

        同路径多次事件会重置定时器（QTimer.start 重启计时期），
        300ms 内无新事件则触发扫描。
        """
        # 监控停用或路径已被移除时忽略残留事件
        if not self._monitoring_enabled:
            return
        # 记录事件日志（不限命中，让用户看到监控在工作）
        self._event_count += 1
        self._recent_events.append(
            {
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                "path": path,
                "event_type": _event_type,
            }
        )
        self.eventLogChanged.emit()  # pyrefly: ignore [missing-attribute]
        # 路径不在监控列表中（可能已被移除，或为子路径——子路径扫描仍允许）
        # 这里仅过滤掉明显已不在监控根目录下的事件（如监控目录被移除后残留事件）
        # 由于 watchdog recursive=True，事件路径可能是监控目录的子路径，这是正常的
        timer = self._debounce_timers.get(path)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda p=path: self._scan_path(p))
            self._debounce_timers[path] = timer
        timer.start(_DEBOUNCE_MS)  # pyrefly: ignore [missing-argument]

    def _scan_path(self, path: str) -> None:
        """扫描单个变动文件，命中规则则追加到模型并 emit hitFound。

        扫描完成后从防抖定时器表中移除该路径的定时器（释放内存）。
        """
        # 清理防抖定时器引用
        timer = self._debounce_timers.pop(path, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        # 监控已停用或文件已被删除/不存在 → 跳过
        if not self._monitoring_enabled:
            return
        file_path = Path(path)
        if not file_path.is_file():
            return
        scanner = self._get_scanner()
        if scanner is None:
            return
        try:
            result = scanner.scan_file(file_path)
        except OSError as exc:
            logger.debug("监控扫描失败 %s: %s", path, exc)
            return
        except Exception as exc:  # 扫描器内部异常不应中断监控
            logger.warning("监控扫描异常 %s: %s", path, exc)
            return
        if not result.has_hit:
            return
        # 命中：取首条命中作为列表展示的主要规则
        first_hit = result.hits[0]
        time_str = datetime.datetime.now().strftime("%H:%M:%S")
        match_text = first_hit.match_text or first_hit.detail
        if len(match_text) > _MATCH_TEXT_MAX:
            match_text = match_text[: _MATCH_TEXT_MAX - 1] + "…"
        # 追加到 Model
        self._model.append_hit(
            time_str,
            path,
            first_hit.rule_name,
            first_hit.severity.value,
            match_text,
        )
        # emit 命中信号（供 app.py 触发托盘通知 + 声音）
        self.hitFound.emit(  # pyrefly: ignore [missing-attribute]
            {
                "time": time_str,
                "path": path,
                "rule_name": first_hit.rule_name,
                "severity": first_hit.severity.value,
                "severity_text": severity_text(first_hit.severity),
                "match_text": match_text,
            }
        )

    # ----------------------------- 持久化 -----------------------------

    @staticmethod
    def _monitor_config_path() -> Path:
        """获取 monitor.json 路径（运行时读取 CONFIG_DIR，支持测试 monkeypatch）。"""
        # 局部 import 避免循环；运行时取 fuscan.config.CONFIG_DIR 当前值
        from fuscan.config import CONFIG_DIR

        return CONFIG_DIR / "monitor.json"

    def _persist(self) -> None:
        """持久化监控目录列表与启用状态到 ``monitor.json``。"""
        data = {
            "directories": list(self._watched.keys()),
            "monitoring_enabled": self._monitoring_enabled,
        }
        try:
            from fuscan.config import CONFIG_DIR

            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with self._persist_lock:
                self._monitor_config_path().write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except OSError as exc:
            logger.warning("监控配置持久化失败: %s", exc)

    def _load_persisted(self) -> None:
        """从 ``monitor.json`` 恢复监控目录列表与启用状态。"""
        path = self._monitor_config_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("监控配置加载失败: %s", exc)
            return
        directories = data.get("directories", [])
        if not isinstance(directories, list):
            return
        for path_str in directories:
            if not isinstance(path_str, str):
                continue
            try:
                abs_path = str(Path(path_str).resolve())
            except (OSError, ValueError):
                continue
            if abs_path in self._watched:
                continue
            if not Path(abs_path).is_dir():
                # 持久化的路径已不存在，跳过但不报错（用户可能已删除该目录）
                continue
            self._watched[abs_path] = None
        if self._watched:
            self.watchedDirectoriesChanged.emit()  # pyrefly: ignore [missing-attribute]
        # 恢复启用状态（仅当有监控目录时）
        if data.get("monitoring_enabled", False) and self._watched:
            # 延迟到事件循环启动后再启动 Observer，避免构造期触发 watchdog 线程
            QTimer.singleShot(0, lambda: self.setMonitoringEnabled(True))  # pyrefly: ignore [missing-argument, bad-argument-type]


class _suppress_observer_errors:
    """Observer.stop/join/unschedule 在异常路径上抛 RuntimeError 的兜底上下文管理器。"""

    def __enter__(self) -> _suppress_observer_errors:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return exc_type is RuntimeError
