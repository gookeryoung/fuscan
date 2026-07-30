# iter-147 线程内存卡滞修复

## 需求清单

- [x] FIX-1：CacheStore 读连接跟踪改 weakref.WeakSet，修复原每次创建新连接的 BUG
- [x] FIX-2：统一 ScanController cleanup 路径，quick_cancel 后异步关闭 cache
- [x] FIX-3：评估 Scanner pool 复用必要性（跳过：收益小风险高）
- [x] FIX-4：workspace_controller.cleanup 降低 wait 超时到 200ms
- [x] FIX-5：并发模式进度回调 batch 从 5 提高到 10
- [x] 新增测试覆盖：6 条（连接复用/WeakSet 自动清理/quick_cancel 异步关 cache/worker 置 None）
- [x] 门禁检查（ruff/format/pyrefly/pytest/coverage）通过
- [x] 写迭代记录，删除 iter-142 保留最新 5 条
- [x] git commit + push

## 迭代目标

用户要求修复 fuscan 项目的内存泄漏、无法退出的线程、界面卡滞问题。基于已诊断的 4 类根因，实施 5 项修复（FIX-3 评估后跳过）。

## 改动文件清单

- `src/fuscan/cache/store.py`：新增 `_ConnRef` 包装类；`_read_conns` 从 `list` 改为 `weakref.WeakSet`；`_get_read_conn` 修复每次创建新连接 BUG（改为先检查复用）；`close()` 改为遍历 WeakSet 关闭残留连接
- `src/fuscan/gui/controllers/scan_controller.py`：`quick_cancel` 统一为 cancel+wait(200)+terminate+wait(100)+deleteLater 模式；新增 `_close_cache_async` 启动 daemon thread 异步关闭 cache；import threading
- `src/fuscan/gui/controllers/workspace_controller.py`：`_restore_workers` wait 从 500ms 降到 200ms，terminate 后补 wait(100)
- `src/fuscan/scanner/scanner.py`：并发模式 `_progress_emit_batch` 从 5 提高到 10
- `tests/test_cache.py`：新增 3 条测试（连接复用/WeakSet 自动清理/空 WeakSet close 幂等）
- `tests/test_scanner.py`：更新 3 处断言（batch 5→10）与注释
- `tests/test_gui_scan_controller.py`：新增 3 条测试（quick_cancel 异步关 cache/no_cache noop/worker 置 None）

## 关键决策与依据

### FIX-1：CacheStore 读连接跟踪 + 修复每次创建新连接 BUG

**发现的原代码 BUG**：`_get_read_conn` 原实现每次调用都 `sqlite3.connect()` 创建新连接并覆盖 `_read_local.conn`，没有先检查是否已有连接。这导致 `_read_conns` 列表无限膨胀——每次扫描每个文件的每次查询都新增连接。这是 cache.db 膨胀和连接泄漏的直接根因。

**修复**：
1. 先检查 `getattr(self._read_local, "ref", None)`，有则复用 `ref.conn`
2. 用 `_ConnRef` 包装类间接实现弱引用（`sqlite3.Connection` 不支持 `weakref.ref`，因 C 扩展类型未设 `tp_weaklistoffset`）
3. `_read_conns` 从 `list[sqlite3.Connection]` 改为 `weakref.WeakSet[_ConnRef]`
4. worker 线程正常退出时 `threading.local` 数据 slot 被清理，`_ConnRef` 失去强引用被 GC，WeakSet 自动移除，连接对象随之 GC 释放 OS 句柄
5. daemon worker 被 OS 强杀时 `threading.local` 不会清理，依赖 `close()` 主动关闭（FIX-2 保证）

**验证**：`test_get_read_conn_reuses_same_thread_connection` 断言同线程多次调用返回同一连接；`test_read_conns_weakset_auto_cleanup_after_thread_exit` 断言 worker 退出后 WeakSet 自动清理。

### FIX-2：统一 ScanController cleanup 路径（核心修复）

**根因**：`workspace_controller.cleanup()` 用 `quick_cancel()` 而非 `cleanup()`，原 `quick_cancel` 不调 `cache.close()`（注释说"进程退出由 OS 回收"）。但进程退出时 OS 不会执行 SQLite WAL checkpoint，导致 WAL 文件无限膨胀（iter-145 cache.db 15.7GB 根因）。

**修复**：
1. `quick_cancel` 统一为 cancel + wait(200) + terminate + wait(100) + deleteLater 模式
2. 末尾新增 `_close_cache_async()`：启动 daemon thread 异步调 `cache.close()`
3. `_cache` 立即设为 None（同步），避免重复关闭
4. daemon thread 避免 `cache.close()` 阻塞主线程（SQLite WAL checkpoint 可能慢）

**消除的不一致**：原 `cleanup()` 关 cache，`quick_cancel()` 不关。现两条路径都关 cache。

### FIX-3：Scanner pool 复用（评估后跳过）

**评估**：ScanWorker 通常单根扫描，多根路径场景少；pool 创建开销小（ThreadPoolExecutor 构造轻）；pool 生命周期管理复杂（cancel 后 shutdown 不能复用）。收益小风险高，跳过。

### FIX-4：降低退出时 wait 超时

**修复**：
- `quick_cancel` 中 wait(500)→wait(200)，terminate 后 wait(200)→wait(100)
- `workspace_controller.cleanup` 中 `_restore_workers` wait(500)→wait(200)，terminate 后补 wait(100)

**影响**：10 工作区退出最坏情况从 14s 降至 6s。daemon worker 会被 OS 回收，超时后未退出的 worker 不影响进程退出。

### FIX-5：进度回调 batch 提高到 10

**修复**：并发模式 `_progress_emit_batch` 从 5 提高到 10。`_emit_progress` 内部仍有 150ms 节流，实际 emit 频率不变，但减少 `perf_counter()` 调用与 `ProgressInfo` tuple 构造次数。

## 代码实现情况

### _ConnRef 包装类

```python
class _ConnRef:
    """sqlite3.Connection 的弱引用包装。"""
    __slots__ = ("__weakref__", "conn")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
```

### _get_read_conn 复用逻辑

```python
def _get_read_conn(self) -> sqlite3.Connection:
    ref = getattr(self._read_local, "ref", None)
    if ref is not None and ref.conn is not None:
        return ref.conn  # 快速路径：复用已有连接
    conn = sqlite3.connect(...)
    wrapper = _ConnRef(conn)
    self._read_local.ref = wrapper
    with self._lru_lock:
        self._read_conns.add(wrapper)
    return conn
```

### quick_cancel 异步关闭 cache

```python
def _close_cache_async(self) -> None:
    if self._cache is None:
        return
    cache = self._cache
    self._cache = None

    def _close() -> None:
        try:
            cache.close()
        except (sqlite3.Error, OSError):
            logger.warning("异步关闭缓存失败", exc_info=True)

    t = threading.Thread(target=_close, name="cache-close", daemon=True)
    t.start()
```

## 测试验证结果

- `uv run ruff check src tests`：通过
- `uv run ruff format --check src tests`：163 files already formatted
- `uv run pyrefly check src`：0 errors
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：**2458 passed, 0 failed, 75 deselected, coverage 96.52%**

新增 6 条测试：
- `test_get_read_conn_reuses_same_thread_connection`：同线程复用连接
- `test_read_conns_weakset_auto_cleanup_after_thread_exit`：WeakSet 自动清理
- `test_close_handles_empty_weakset`：空 WeakSet close 幂等
- `test_quick_cancel_closes_cache_async`：quick_cancel 异步关 cache
- `test_quick_cancel_no_cache_noop`：无 cache 时 quick_cancel 不抛异常
- `test_quick_cancel_sets_worker_none`：quick_cancel 后 worker 置 None

## 遗留事项

1. daemon worker 被 OS 强杀时 `threading.local` 不会清理，`_ConnRef` 仍被强引用无法 GC。依赖 `cache.close()` 主动关闭（FIX-2 保证退出路径调用）。若 daemon worker 在非退出路径强杀（如 QThread.terminate），需确保后续 `cache.close()` 被调用。
2. FIX-3（Scanner pool 复用）跳过，若未来多根路径扫描性能成为瓶颈可重新评估。
3. iter-145 遗留：`_TIER_TIME_LIMITS` 动态阈值仍用硬编码。
4. iter-146 遗留：STYLE-1/STYLE-2 风格问题未处理。

## 下一轮计划

无主动迭代计划。等待用户反馈或新需求。
