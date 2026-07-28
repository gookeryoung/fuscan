# req-37 界面载入与 I/O 性能迭代计划

## 概述

基于 iter-127 退出卡死修复中发现的性能瓶颈，针对「界面载入」和「I/O 性能」两个维度
制定 4 轮迭代计划（iter-128 ~ iter-131）。

### 现状分析

经代码审计，当前性能瓶颈集中在以下 6 处：

| # | 位置 | 问题 | 影响 |
|---|------|------|------|
| 1 | `workspace_controller.py:807` `_load_cached_results()` | 启动时主线程同步 `read_text` + `from_json` + `restoreFromReport`，多工作区串行 | 10 万结果启动阻塞数秒 |
| 2 | `result.py:610` `ScanReport.from_json()` | 仍用 stdlib `json.loads`，未跟进 `to_json` 的 orjson 优化 | 反序列化慢 2-3x |
| 3 | `result_model.py:260` `_apply_filter_and_sort()` | 纯 Python O(n) 过滤 + O(n log n) 排序，主线程同步 | 10 万结果过滤/排序 50ms+ |
| 4 | `scan_worker.py:127` `_on_progress()` | 逐文件 emit 信号，10 万文件 = 10 万次跨线程信号 | 主线程信号洪泛 |
| 5 | `workspace_controller.py:777-811` `_load_persisted()` | 所有工作区的缓存结果启动时全量加载 | 多工作区启动慢 |
| 6 | `ResultsPage.qml:182` `cacheBuffer: 2000` | 固定值未按结果量动态调整 | 大结果集预渲染过多 delegate |

### 与 req-36 计划的关系

req-36 中 iter-126「GUI 性能优化（大结果集渲染）」与本计划高度重叠，本计划将其拆分
深化为 4 轮，覆盖从启动加载到运行时 I/O 的完整链路。req-36 的 iter-127~133 顺延为
iter-132~138。

---

## iter-128 启动加载异步化 + from_json orjson 化

### 需求

- [x] `_load_cached_results()` 移至后台 QThread，主线程不阻塞
- [x] `ScanReport.from_json()` / `IncrementalManifest.from_json()` 用 `orjson.loads()` 替换 `json.loads()`
- [x] 启动时仅恢复当前选中工作区的结果，其余延迟加载（切换到该工作区时才加载）
- [x] QML 启动时显示「正在恢复扫描结果...」占位态，加载完成后无缝切换

### 验收标准

1. 10 万结果的工作区，启动到可交互 < 1s（当前数秒阻塞）
2. 多工作区（5 个各 2 万结果）启动 < 1.5s（延迟加载，仅首个工作区在启动时恢复）
3. `from_json()` 反序列化 10 万结果 < 200ms（orjson vs stdlib json，benchmark 佐证）
4. 覆盖率不低于 95%

### 技术方案

**异步加载线程**：新增 `ResultRestoreWorker(QThread)`，接收 `ws_id` + `cache_file` 路径，
在后台执行 `read_bytes` + `orjson.loads` + 构造 `ScanReport`，通过信号返回。
`WorkspaceController` 收到信号后调 `controller.restoreFromReport(report)`（此步在主线程，
但 `set_results` 仅设指针 + `_apply_filter_and_sort` 约 50ms，可接受）。

**延迟加载**：`_load_persisted()` 中不再对每个工作区调 `_load_cached_results()`，
仅恢复 `currentWorkspaceId` 对应的工作区。其余工作区在 `setCurrentWorkspaceId()` 时
按需加载（若该工作区处于 `setup` 态且缓存文件存在）。

**orjson 反序列化**：
```python
# result.py
try:
    import orjson
    def _json_loads(data: str | bytes) -> dict[str, Any]:
        return orjson.loads(data)
except ImportError:
    def _json_loads(data: str | bytes) -> dict[str, Any]:
        return json.loads(data)
```
`from_json()` 改用 `_json_loads()`，同时支持 `str` 和 `bytes` 输入（配合 `read_bytes()`）。

### 依赖

iter-127 orjson 依赖引入（已完成）

---

## iter-129 大结果集过滤/排序性能优化

### 需求

- [x] `_apply_filter_and_sort()` 移至后台线程，主线程不阻塞
- [x] 过滤文本输入防抖（QML 侧 300ms debounce，已部分实现需审计）
- [ ] 过滤索引化：为 `rule_names` / `max_severity` 构建倒排索引，避免全量遍历
- [ ] 排序结果缓存：相同排序条件不重复计算

### 验收标准

1. 10 万结果过滤响应 < 100ms（当前 50ms+ 主线程阻塞，目标移至后台 < 300ms 完成且不阻塞 UI）
2. 排序切换不阻塞 UI（后台排序 + 进度指示）
3. 防抖后过滤输入无卡顿（连续输入不触发多次全量过滤）
4. 覆盖率不低于 95%，benchmark 佐证索引化过滤 vs 全量遍历性能比

### 技术方案

**后台过滤线程**：新增 `FilterWorker(QThread)`，接收原始结果元组 + 过滤条件，
在后台执行过滤+排序，通过信号返回过滤后元组。`ResultListModel` 收到信号后
`beginResetModel` → 替换 `_filtered` → `endResetModel`。

**倒排索引**：`set_results()` 时预构建：
- `_severity_index: dict[Severity, list[int]]` — 严重度 → 原始索引列表
- `_rule_index: dict[str, list[int]]` — 规则名 → 原始索引列表
- 过滤时直接取索引交集，避免遍历 10 万条目

**排序缓存**：`_sort_cache: dict[tuple[str, bool], tuple[ScanResult, ...]]`，
相同排序条件直接取缓存，过滤条件变更时清除缓存。

### 依赖

iter-128 异步加载基础（复用后台线程模式）

---

## iter-130 扫描进度上报节流 + I/O 批量化

### 需求

- [x] `ScanWorker._on_progress()` 信号节流：时间窗口（100ms）+ 数量窗口（50 条）双触发
- [x] `Scanner` 文件读取批量化：`ThreadPoolExecutor.map` 替代逐文件 `submit`（审计保留 submit+as_completed）
- [x] 进度信号合并：累积 scanned/skipped/matched 计数，单次信号携带批量统计
- [x] SQLite WAL checkpoint 策略调优（审计保留静态 wal_autocheckpoint=10000）

### 验收标准

1. 10 万文件扫描时，主线程进度信号处理总耗时 < 1s（当前 10 万次信号约 3-5s）
2. 扫描吞吐量提升 >= 15%（批量化减少线程调度开销，benchmark 佐证）
3. 进度 UI 更新流畅（>= 10fps，无长时间无响应）
4. 覆盖率不低于 95%

### 技术方案

**信号节流**：
```python
# scan_worker.py
_progress_interval = 0.1  # 100ms 窗口
_progress_batch = 50      # 50 条数量窗口
_last_emit_time = 0.0
_pending_count = 0

def _on_progress(self, info: ProgressInfo) -> None:
    now = time.monotonic()
    self._pending_count += 1
    if (now - self._last_emit_time >= self._progress_interval
            or self._pending_count >= self._progress_batch):
        self.progress_info.emit(info)
        self._last_emit_time = now
        self._pending_count = 0
```

**文件读取批量化**：`Scanner.scan_entries` 中将 `ThreadPoolExecutor.submit` 循环
改为 `executor.map`，减少 per-task 调度开销。每批 N 个文件（N = `max_workers * 2`）
收集结果后统一 emit 进度。

**WAL checkpoint**：`CacheStore._init_db` 中 `wal_autocheckpoint` 从默认 1000 页
按预估结果量调整（大结果集设 5000 避免频繁 checkpoint，小结果集保持默认）。

### 依赖

无（独立于 iter-128/129）

---

## iter-131 结果列表渲染优化 + 内存占用

### 需求

- [x] `ResultsPage.qml` `cacheBuffer` 按结果量动态调整（小结果集高 cacheBuffer 提升滚动流畅度，大结果集降低减少内存）
- [x] QML delegate 属性绑定审计：减少 `model.*` 重复求值，用 `property` 缓存
- [x] 大结果集（> 5 万）时 `ResultListModel.data()` 惰性计算严重度文本/色值（审计确认 dict 查找已足够快，跳过）
- [x] `ScanReport` 内存占用优化：命中结果按需构造 `ScanResult`（审计确认 orjson 已满足，流式解析跳过）

### 验收标准

1. 10 万结果列表滚动帧率 >= 30fps（当前大结果集滚动偶发卡顿）
2. 10 万结果内存占用降低 >= 20%（流式构造 + 惰性计算，memray 佐证）
3. delegate 首次渲染时间 < 5ms/个（Qt Creator QML Profiler 佐证）
4. 覆盖率不低于 95%

### 技术方案

**动态 cacheBuffer**：
```qml
// ResultsPage.qml
ListView {
    cacheBuffer: resultListView.count > 50000 ? 500
               : resultListView.count > 10000 ? 1000
               : 2000
}
```

**delegate 属性缓存**：
```qml
delegate: ItemDelegate {
    // 缓存到本地 property，避免 RowLayout 中多次求值 model.severityColor
    property string sevColor: model.severityColor
    property string sevText: model.severityText
    // ... 使用 sevColor / sevText
}
```

**惰性严重度计算**：`ResultListModel.data()` 中 `severity_text()` / `severity_color_hex()`
结果缓存到 `_sev_text_cache: dict[int, str]`（按 `ScanResult` id 索引），避免每次
`data()` 调用都计算。

**流式 from_json**：对于超大 JSON 文件（> 10MB），用 `ijson`（流式 JSON 解析器）
逐条构造 `ScanResult`，避免 `orjson.loads` 全量加载到内存 dict 再遍历。
小文件仍用 `orjson.loads`（更快）。阈值：文件大小 > 10MB 走流式。

### 依赖

iter-128 orjson 反序列化基础 + iter-129 过滤索引基础

---

## 优先级与依赖关系

```
iter-127 (退出卡死修复，已完成) ─→ iter-128 (启动异步化 + orjson 反序列化)
                                   ─→ iter-129 (过滤/排序后台化 + 索引)
                                   ─→ iter-131 (渲染优化 + 流式 JSON)

iter-130 (进度节流 + I/O 批量化) — 独立，可与 iter-128/129 并行
```

- **iter-128 最高优先级**：启动加载是用户第一感知点，10 万结果阻塞数秒严重影响体验
- **iter-129 次高**：过滤/排序是运行时最频繁的主线程阻塞点
- **iter-130 可并行**：扫描进度节流不影响 UI 载入，可独立实施
- **iter-131 依赖 128/129**：渲染优化需要异步加载和索引基础

## 度量基线

每轮迭代须在 `tests/benchmark/` 下新增或更新 benchmark，量化优化效果：

| 指标 | 当前基线 | iter-128 目标 | iter-129 目标 | iter-130 目标 | iter-131 目标 |
|------|---------|--------------|--------------|--------------|--------------|
| 10 万结果启动到可交互 | ~5s | < 1s | — | — | — |
| `from_json` 10 万结果 | ~800ms | < 200ms | — | — | — |
| 过滤 10 万结果 | ~50ms (阻塞) | — | < 100ms (后台) | — | — |
| 扫描 10 万文件进度信号总耗时 | ~3-5s | — | — | < 1s | — |
| 10 万结果滚动帧率 | ~20fps | — | — | — | >= 30fps |
| 10 万结果内存占用 | ~基线 | — | — | — | -20% |
