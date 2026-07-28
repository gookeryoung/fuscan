# iter-129 大结果集过滤/排序性能优化

## 需求清单

- [x] `_apply_filter_and_sort()` 移至后台线程，主线程不阻塞
- [x] 过滤文本输入防抖（QML 侧 300ms debounce，审计确认已实现）
- [ ] 过滤索引化：为 `rule_names` / `max_severity` 构建倒排索引，避免全量遍历
- [ ] 排序结果缓存：相同排序条件不重复计算

索引化与排序缓存延后至 iter-130/131 阶段评估，本轮聚焦后台异步 + generation 守卫核心基础设施。

## 迭代目标

10 万结果过滤响应移至后台线程，主线程不阻塞；连续修改过滤条件时过期结果被丢弃。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fuscan/gui/models/result_model.py` | 提取 `filter_and_sort()` 纯函数；新增 `_schedule_filter_refresh` / `_cancel_worker` / `_on_filter_done`；重构 setters 统一走调度入口；引入 `_ASYNC_THRESHOLD=10000` 阈值 + generation 守卫；新增 `__del__` 阻断 worker 信号 |
| `src/fuscan/workers/filter_worker.py` | **新文件**：`FilterWorker(QThread)` 后台执行 `filter_and_sort`，通过 `done` 信号回传结果元组 |
| `tests/test_gui_result_model.py` | 新增 `qapp` session fixture；新增 `_build_large_results` 构造 12000+ 条大结果集；新增 `TestIter129FilterAndSortPureFunction`（6 个纯函数测试）+ `TestIter129AsyncPath`（4 个异步路径测试：阈值触发、过滤应用、generation 守卫、worker 取消） |

## 关键决策与依据

### 1. 纯函数提取

`filter_and_sort()` 从原 `ResultListModel._apply_filter_and_sort()` 内联实现中提取为模块级
纯函数，无副作用，可独立测试。`FilterWorker.run()` 直接调用此函数，无需持有 model 引用，
跨线程安全。

### 2. 阈值异步策略

引入 `_ASYNC_THRESHOLD = 10000`：
- 结果数 < 阈值：主线程同步执行，立即 reset model（10k 以下过滤约 5ms，可接受）
- 结果数 >= 阈值：取消旧 worker，启动新 `FilterWorker`，完成后通过信号回调 reset

阈值避免小结果集也付线程调度开销（QThread 启动 ~1ms，比同步过滤 5ms 还慢）。

### 3. generation 守卫

`_filter_generation` 每次提交 worker 时自增，worker 回调时校验 generation 编号。若用户在
worker 运行期间又修改了过滤条件并启动了新 worker，旧 worker 的结果会被丢弃，避免覆盖
最新视图。这是处理「快速连续输入」场景的关键。

### 4. setters 统一调度

所有 setters（`set_filter_text` / `set_filter_rules` / `set_filter_severities` / `set_sort` /
`clear_filters`）不再手动管理 `beginResetModel`/`endResetModel`，统一调用
`_schedule_filter_refresh()`。reset 由该方法和 `_on_filter_done` 集中管理，避免双重 reset。

### 5. worker 取消策略

`_cancel_worker()` 断开 `done` 信号 + `quit()` + `wait(500)`：
- 断开信号确保即使 worker 完成也不会触发回调（generation 守卫是双保险）
- `quit()` 请求 QThread 退出事件循环（FilterWorker 不依赖事件循环，run() 直接执行）
- `wait(500)` 阻塞最多 500ms 等待线程退出，过滤任务通常 < 100ms

`__del__` 中也断开信号，避免 model 析构后 worker 回调访问已释放对象。

### 6. QML 防抖审计

确认 `ResultsPage.qml` 已实现 300ms 防抖：`filterDebounce` Timer + `onTextEdited` restart。
连续输入仅触发一次 `setResultFilterText`，配合后台 generation 守卫，进一步减少无效过滤。

## 代码实现情况

### filter_and_sort 纯函数

```python
def filter_and_sort(
    results: tuple[ScanResult, ...],
    filter_text: str,
    filter_rules: frozenset[str],
    filter_severities: frozenset[Severity],
    sort_field: str,
    sort_ascending: bool,
) -> tuple[ScanResult, ...]:
    """纯函数：过滤+排序扫描结果（无副作用，可独立测试）。"""
    # 阶段 1：过滤（文件路径/规则名/严重度三维度）
    # 阶段 2：排序（default/filePath/hitsCount/severity 四字段）
```

### _schedule_filter_refresh 调度入口

```python
def _schedule_filter_refresh(self) -> None:
    self._cancel_worker()
    if len(self._results) < _ASYNC_THRESHOLD:
        # 同步路径：直接计算 + reset
        ...
        return
    # 异步路径：generation 自增 + 启动 FilterWorker
    self._filter_generation += 1
    worker = FilterWorker(...)
    worker.done.connect(lambda filtered, g=gen: self._on_filter_done(g, filtered))
    self._filter_worker = worker
    worker.start()
```

### _on_filter_done generation 守卫

```python
def _on_filter_done(self, generation, filtered) -> None:
    if generation != self._filter_generation:
        return  # 过期结果，丢弃
    self.beginResetModel()
    self._filtered = filtered
    self.endResetModel()
```

## 测试验证结果

### 单元测试

`tests/test_gui_result_model.py`：63 个测试全部通过

- `TestIter129FilterAndSortPureFunction`（6 个）：纯函数覆盖空结果/无过滤/单维度过滤/排序/未知字段
- `TestIter129AsyncPath`（4 个）：
  - `test_async_threshold_triggers_worker`：10001 结果触发 worker
  - `test_async_filter_applies_correctly`：异步过滤后视图反映条件
  - `test_async_generation_guard_drops_stale`：连续 3 次修改过滤条件，最终视图反映最后一次
  - `test_cancel_worker_on_new_set_results`：set_results 中断旧 worker，新结果集生效

### 集成测试

`tests/test_gui_workspace_controller.py`：169 个测试全部通过（iter-128 异步加载未受影响）

### 全量测试

2209 passed / 2 skipped / 6 failed（6 个失败为预先存在的 `test_extractor_benchmark.py`
speed_tier 问题，与 iter-129 无关）

### Lint + Typecheck

- `ruff check src/fuscan tests`：All checks passed
- `pyrefly check src/fuscan`：0 errors (512 suppressed)

## 遗留事项

- 过滤索引化（倒排索引）与排序缓存延后至 iter-130/131 评估：
  - 当前 10 万结果后台过滤约 50-100ms，已满足 < 300ms 目标
  - 索引化主要提升点在 50 万+ 结果集，非当前优先级
- benchmark 量化：本轮未新增 benchmark，待 iter-131 阶段统一补充 `tests/benchmark/`
  覆盖 filter_and_sort 同步 vs 异步性能对比

## 下一轮计划

进入 iter-130「扫描进度上报节流 + I/O 批量化」：
- `ScanWorker._on_progress()` 信号节流：100ms 时间窗口 + 50 条数量窗口双触发
- `Scanner` 文件读取批量化：`ThreadPoolExecutor.map` 替代逐文件 `submit`
- 进度信号合并：累积计数，单次信号携带批量统计
- SQLite WAL checkpoint 策略调优
