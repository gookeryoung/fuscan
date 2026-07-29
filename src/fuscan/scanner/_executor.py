"""守护线程版 ThreadPoolExecutor。

提供 :class:`DaemonThreadPoolExecutor`，与标准库
:class:`concurrent.futures.ThreadPoolExecutor` 行为一致，区别在于 worker
线程为 daemon=True 且不注册到 ``_threads_queues``，确保进程退出时
``_python_exit`` atexit 不会 join worker 导致阻塞。

iter-139 修复进程退出问题：``ScanWorker.run`` 内 ``pool.shutdown(wait=True)``
在 worker 卡在慢 I/O（大文件 read_bytes、慢正则）时会无限阻塞，
导致 ``finished_report`` 信号不 emit、``ScanController`` 卡在 STATE_SCANNING，
``quick_cancel`` 的 ``wait(500)`` 超时后 ``QThread.terminate()`` 也无法杀掉
Python ThreadPool worker，最终 ``_python_exit`` atexit 等待 worker → 进程不退。

修复策略：
1. worker 线程 ``daemon=True``（在 ``start()`` 前设置，CPython 强约束）
2. 不注册到 ``concurrent.futures.thread._threads_queues``，使 ``_python_exit``
   atexit 跳过对 worker 的 ``put(None)`` 与 ``t.join()``，避免阻塞
3. 配合 ``shutdown(wait=False)`` 让正常退出路径 worker 自然结束

公共 API：

- :class:`DaemonThreadPoolExecutor`：守护线程版 ThreadPoolExecutor
"""

from __future__ import annotations

import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.thread import _worker

from typing_extensions import override

__all__ = ["DaemonThreadPoolExecutor"]


class DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """worker 为 daemon 且不注册 ``_threads_queues`` 的 ThreadPoolExecutor。

    与标准库 :class:`ThreadPoolExecutor` 行为一致，区别：

    1. worker 线程 ``daemon=True``（``start()`` 前设置）
    2. worker 不注册到 ``concurrent.futures.thread._threads_queues``，
       使 ``_python_exit`` atexit 跳过 ``t.join()``，避免 worker 卡在慢 I/O
       时阻塞进程退出。``daemon=True`` 保证进程退出时 OS 直接回收 worker。

    使用场景：扫描取消或正常退出时，主线程 ``shutdown(wait=False)`` 立即返回，
    残余 worker（卡在 read_bytes 或正则匹配中）由 daemon 标志在进程退出时
    被 OS 回收，不阻塞 ``_python_exit`` atexit。

    实现说明：覆写 ``_adjust_thread_count`` 与 CPython 3.10
    ``ThreadPoolExecutor._adjust_thread_count`` 实现一致，仅 (a) 在 ``t.start()``
    前设 ``t.daemon = True``，(b) 不执行 ``_threads_queues[t] = self._work_queue``
    注册。父类实现变更时本覆写可能失效，需同步更新。
    """

    @override
    def _adjust_thread_count(self) -> None:
        """覆写父类方法：创建 daemon worker 且不注册 _threads_queues。"""
        # if idle threads are available, don't spin new threads
        if self._idle_semaphore.acquire(timeout=0):
            return

        # When the executor got lost, the weakref callback will wake up
        # the worker threads.
        def weakref_cb(_ref: object, q: object = self._work_queue) -> None:
            q.put(None)  # type: ignore[union-attr]

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = f"{self._thread_name_prefix or self}_{num_threads}"
            t = threading.Thread(
                name=thread_name,
                target=_worker,
                args=(
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                ),
            )
            # 关键：start() 前设 daemon=True（CPython 强约束，start 后设置抛 RuntimeError）
            t.daemon = True
            t.start()
            self._threads.add(t)  # pyrefly: ignore [missing-attribute]
            # iter-139：不注册到 _threads_queues——避免 _python_exit atexit 对 worker
            # t.join() 阻塞进程退出。worker 已为 daemon，进程退出时由 OS 回收。
            # 注：_python_exit 也不会对本 worker 的 work_queue put(None) 信号，
            # 但 pool.shutdown(wait=False) 已 put(None)，正常路径 worker 自然退出；
            # 异常路径（worker 卡在 read_bytes）依赖 daemon 标志被 OS 回收。
