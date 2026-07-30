"""文件统计后台线程：扫描前先行遍历目录收集待扫描文件清单。

FileStatsWorker 在独立 QThread 中执行 Scanner.collect_entries（walk 阶段），
通过信号通知 UI 进度、完成与错误。与 ScanWorker 职责拆分：

- FileStatsWorker 负责 walk 阶段，产出 ``list[WalkResult]``
- ScanWorker 接收 ``precollected`` 后跳过 walk，直接进入 scan/archive 阶段

二者串行执行（stats 完成后启动 scan），不并行，避免磁盘 I/O 争抢。
收益在于 UI 能更早展示确定的 ``total``，且两 worker 的取消/暂停各自独立。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

try:
    from PySide2.QtCore import QObject, QThread, Signal
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QObject, QThread, Signal  # pyrefly: ignore [missing-import]

from fuscan.rules.model import RuleSet
from fuscan.scanner.manifest import IncrementalManifest
from fuscan.scanner.result import ProgressInfo, WalkResult
from fuscan.scanner.scanner import Scanner

__all__ = ["FileStatsWorker"]

logger = logging.getLogger(__name__)


class FileStatsWorker(QThread):  # pyrefly: ignore [invalid-inheritance]
    """后台文件统计线程：执行 walk 阶段收集待扫描文件清单。

    信号：

    - ``progress_info``：实时进度信息（ProgressInfo，walk 阶段，``total`` 持续增长）
    - ``finished_stats``：统计完成，携带多根路径合并的 ``list[WalkResult]``
    - ``failed``：统计异常，携带错误信息
    - ``cancelled``：统计被用户取消，携带已完成的 ``list[WalkResult]``（部分结果）
    """

    progress_info = Signal(object)
    finished_stats = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(
        self,
        ruleset: RuleSet,
        roots: list[Path],
        max_depth: int | None = None,
        scan_archives: bool = False,
        ignore_dirs: tuple[str, ...] = (),
        progress_interval: float = 0.1,
        scan_extensions: tuple[str, ...] | None = None,
        skip_paths: frozenset[str] | None = None,
        incremental_manifest: IncrementalManifest | None = None,
        parent: QObject | None = None,
    ) -> None:
        """初始化统计线程。

        :param ruleset: 规则集（仅用于构造 Scanner 获取 ``ignore_paths`` 与
            ``scan_extensions`` 过滤，不参与内容匹配）
        :param roots: 待统计的根路径列表（如全盘扫描时为多个盘符）
        :param max_depth: 最大遍历深度，None 表示不限制
        :param scan_archives: 是否扫描压缩包（构造 ArchiveScanner 进入压缩包扫描阶段）
        :param ignore_dirs: 忽略的目录名（如 ``.git``、``__pycache__``）
        :param progress_interval: 进度回调最小间隔（秒）
        :param scan_extensions: 全局后缀白名单（iter-87 起统一白名单制），
            None 表示扫描所有文件；非空 tuple 按白名单过滤；空 tuple 不扫描任何文件
        :param skip_paths: 用户标记跳过的路径集合
        :param incremental_manifest: 增量扫描清单（iter-124），非 None 启用增量模式，
            walk 阶段对比指纹跳过未变更文件；None 走全量模式
        :param parent: 父 QObject
        """
        super().__init__(parent)
        self._ruleset = ruleset
        self._roots = roots
        self._max_depth = max_depth
        self._scan_archives = scan_archives
        self._ignore_dirs = ignore_dirs
        self._progress_interval: float = progress_interval
        self._scan_extensions: tuple[str, ...] | None = scan_extensions
        self._skip_paths: frozenset[str] = skip_paths or frozenset()
        self._incremental_manifest: IncrementalManifest | None = incremental_manifest
        self._scanner: Scanner | None = None
        self._cancel_requested: bool = False
        # 多根路径累计统计（供 _on_progress 累加前序根的统计后 emit）
        self._cum_total = 0
        self._cum_skipped = 0
        self._cum_user_skipped = 0
        self._start_time: float = 0.0

    def pause(self) -> None:
        """暂停统计。"""
        if self._scanner is not None:
            self._scanner.pause()

    def resume(self) -> None:
        """恢复统计。"""
        if self._scanner is not None:
            self._scanner.resume()

    def cancel(self) -> None:
        """取消统计，即使 Scanner 尚未创建也能生效。"""
        self._cancel_requested = True
        if self._scanner is not None:
            self._scanner.cancel()

    def _on_progress(self, info: ProgressInfo) -> None:
        """Scanner 进度回调：累加前序根路径的统计后 emit。

        walk 阶段 ``scanned``/``matched``/``errors``/``matches`` 恒为 0，
        仅 ``total``/``skipped``/``user_skipped`` 随遍历增长。
        """
        elapsed = time.monotonic() - self._start_time
        self.progress_info.emit(  # pyrefly: ignore [missing-attribute]
            ProgressInfo(
                current_file=info.current_file,
                scanned=info.scanned,
                total=info.total + self._cum_total,
                skipped=info.skipped + self._cum_skipped,
                matched=info.matched,
                errors=info.errors,
                elapsed=elapsed,
                matches=info.matches,
                # skipped_dirs/matched_files 不累计，仅反映最近一次 collect 的快照
                skipped_dirs=info.skipped_dirs,
                matched_files=info.matched_files,
                phase=info.phase,
                user_skipped=info.user_skipped + self._cum_user_skipped,
            )
        )

    @property
    def manifest(self) -> IncrementalManifest | None:
        """本次 collect_entries 构建的新 manifest（供增量扫描持久化）。

        stats 完成后由 :class:`ScanController` 读取并持久化到
        ``~/.fuscan/manifests/<ws_id>.json``，供下次增量扫描加载。
        ``_scanner`` 未创建（run 尚未启动）时返回 ``None``。
        """
        if self._scanner is None:
            return None
        return self._scanner.current_manifest

    def run(self) -> None:
        """线程入口：依次统计所有根路径并合并结果。"""
        try:
            self._start_time = time.monotonic()
            self._scanner = Scanner(
                ruleset=self._ruleset,
                max_depth=self._max_depth,
                scan_archives=self._scan_archives,
                # walk 阶段不读内容，max_workers/max_file_size 不影响 collect_entries；
                # 传 None 走默认值，cache=None 避免初始化 SQLite
                on_progress=self._on_progress,
                ignore_dirs=self._ignore_dirs,
                progress_interval=self._progress_interval,
                scan_extensions=self._scan_extensions,
                skip_paths=self._skip_paths,
                incremental_manifest=self._incremental_manifest,
            )
            if self._cancel_requested:
                self._scanner.cancel()
            all_results: list[WalkResult] = []
            # 基于 walk_result.cancelled 判断取消状态：collect_entries 末尾清除
            # _cancel_event，必须用返回值累积取消标志，否则取消的统计会被误判为正常完成
            was_cancelled = False

            for root in self._roots:
                if was_cancelled:
                    break
                walk_result = self._scanner.collect_entries(root)
                all_results.append(walk_result)
                # 更新累计值，供下一个根路径的进度回调使用
                self._cum_total += walk_result.total
                self._cum_skipped += walk_result.skipped
                self._cum_user_skipped += walk_result.user_skipped
                if walk_result.cancelled:
                    was_cancelled = True
            if was_cancelled:
                self.cancelled.emit(all_results)  # pyrefly: ignore [missing-attribute]
            else:
                self.finished_stats.emit(all_results)  # pyrefly: ignore [missing-attribute]
        except Exception as exc:
            logger.exception("后台统计失败")
            self.failed.emit(str(exc))  # pyrefly: ignore [missing-attribute]
