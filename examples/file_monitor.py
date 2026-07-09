"""文件监控示例：实时监控目录新增/修改文件并触发扫描。

演示 FileMonitor 的用法，基于 watchdog 实现：

1. 配置 MonitorConfig 指定监控路径与忽略目录
2. 启动 FileMonitor，注册回调函数
3. 回调中调用 IncrementalScanner.scan_paths 增量扫描
4. Ctrl+C 优雅停止

适用场景：

- 开发机持续监控下载目录，发现敏感文件立即告警
- 服务器监控上传目录，自动扫描恶意内容
- 替代定时 cron 扫描，实现近实时响应

运行：

    python examples/file_monitor.py /path/to/watch rules/example.yaml
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path
from typing import List

from pyfilescan.rules import load_ruleset
from pyfilescan.watcher import FileEventType, FileMonitor, IncrementalScanner, MonitorConfig

logger = logging.getLogger("file_monitor")


class MonitorApp:
    """文件监控应用：监控目录并增量扫描新增/修改文件。"""

    def __init__(self, watch_path: Path, rules_path: Path) -> None:
        self._watch_path = watch_path
        self._scanner = IncrementalScanner(load_ruleset(rules_path))
        self._stop_event = threading.Event()
        self._pending: List[Path] = []
        self._lock = threading.Lock()

        # 配置监控器
        config = MonitorConfig(
            watch_paths=[watch_path],
            ignore_dirs=[".git", "__pycache__", "node_modules", ".venv", "venv", "temp"],
            ignore_extensions=["pyc", "pyo", "tmp", "swp"],
            dedup_interval_seconds=1.0,
            recursive=True,
        )
        self._monitor = FileMonitor(config)

    def start(self) -> int:
        """启动监控，阻塞直到收到中断信号。"""
        logger.info("启动文件监控：%s", self._watch_path)

        # 首次全量扫描
        logger.info("执行首次全量扫描...")
        report = self._scanner.scan(self._watch_path)
        logger.info(
            "全量扫描完成：总计 %d，命中 %d，耗时 %.2fs",
            report.stats.total_files,
            report.stats.matched_files,
            report.stats.duration_seconds,
        )
        for result in report.hits:
            logger.warning("发现命中：%s", result.path)

        # 启动监控
        self._monitor.start(self._on_event)
        logger.info("监控已启动，等待文件变更...（Ctrl+C 停止）")

        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # 阻塞等待
        self._stop_event.wait()
        self._monitor.stop()
        logger.info("监控已停止")
        return 0

    def _on_event(self, event) -> None:
        """文件事件回调。"""
        if event.is_dir:
            return
        if event.event_type in (FileEventType.CREATED, FileEventType.MODIFIED):
            logger.info("检测到文件变更：%s (%s)", event.path, event.event_type.value)
            # 增量扫描单个文件
            report = self._scanner.scan_paths([event.path])
            for result in report.hits:
                logger.warning("命中规则：%s", result.path)
                for hit in result.hits:
                    logger.warning("  [%s] %s: %s", hit.severity.value, hit.rule_name, hit.detail)

    def _signal_handler(self, signum, frame) -> None:
        """信号处理：触发停止。"""
        logger.info("收到信号 %d，准备停止...", signum)
        self._stop_event.set()


def main(watch_path: Path, rules_path: Path) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    app = MonitorApp(watch_path=watch_path, rules_path=rules_path)
    return app.start()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"用法：python {sys.argv[0]} <监控路径> <规则文件>")
        sys.exit(1)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
