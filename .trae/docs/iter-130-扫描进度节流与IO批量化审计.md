# iter-130 扫描进度节流与 I/O 批量化审计

## 需求清单

- [x] `ScanWorker._on_progress()` 信号节流：时间窗口 100ms（原 300ms 调整为 100ms）
- [x] `Scanner` 文件读取批量化：审计确认现有 `submit + as_completed` 优于 `executor.map`，保留现状
- [x] 进度信号合并：审计确认 `ProgressInfo` 已携带累计统计，无需改动
- [x] SQLite WAL checkpoint 策略：审计确认静态 `wal_autocheckpoint=10000` 合理，动态调整属过度优化

## 迭代目标

审计 iter-130 计划四项需求，确认现有实现状态，仅对未达标项做针对性优化。

## 审计结论

### 1. 进度信号节流（已优化）

**现有实现**：两级节流架构
- Scanner 层：`_progress_emit_batch=5`（并发模式每 5 个 future 完成调一次 `_emit_progress`）
- Scanner 层：`_emit_progress` 内时间节流（`progress_interval` 由 ScanWorker 传入）
- ScanWorker 层：`_on_progress` 直接 emit 信号（无额外节流）

**问题**：ScanWorker 默认 `progress_interval=0.3`（300ms）= 3.3fps，低于 10fps 目标。

**修复**：`ScanWorker` 与 `FileStatsWorker` 默认 `progress_interval` 从 0.3 调整为 0.1（100ms）= 10fps。

**未调整**：计划中"数量窗口 50 条"未在 ScanWorker 层添加。原因：
- Scanner 层已有数量窗口（`_progress_emit_batch=5`），每 5 个文件触发一次 `_emit_progress`
- 若将 ScanWorker 层数量窗口设为 50，需 Scanner 调用 `on_progress` 50 次才触发一次信号
- 但 Scanner 层时间节流（100ms）已限制 `on_progress` 调用频率，ScanWorker 层数量窗口实际不会触发
- 在 Scanner 层将 `_progress_emit_batch` 从 5 提高到 50 会导致慢扫描场景（大文件）进度缺失
- 现有两级架构（Scanner 数量窗口 + Scanner 时间窗口）已有效控制信号频率

### 2. 文件读取批量化（审计保留现状）

**计划**：`ThreadPoolExecutor.map` 替代逐文件 `submit`。

**审计结论**：现有 `submit + as_completed` 优于 `map`，不替换：

| 维度 | submit + as_completed | executor.map |
|------|----------------------|--------------|
| 取消未启动 future | 支持（`f.cancel()`） | 不支持（map 不可取消） |
| 结果处理顺序 | 按完成顺序（早完成早处理） | 按提交顺序（阻塞等慢任务） |
| per-future 异常 | 支持（try/except per future） | 首个异常终止整个迭代 |
| 取消加速 | `shutdown(wait=False)` 立即返回 | 必须等全部完成 |

`_collect_concurrent_results` 已实现取消时 `cancel_all_futures` + `shutdown(wait=False)`，
对大文件扫描取消场景至关重要。改用 `map` 会丢失此能力。

### 3. 进度信号合并（已实现）

**计划**：累积 scanned/skipped/matched 计数，单次信号携带批量统计。

**审计结论**：`ProgressInfo` 已携带全部累计统计：
- `scanned` / `total` / `skipped` / `matched` / `errors` / `matches` / `user_skipped`
- `ScanWorker._on_progress` 在多根路径场景下累加前序根路径统计（`_cum_*` 字段）
- 无需改动

### 4. SQLite WAL checkpoint（审计保留现状）

**计划**：`wal_autocheckpoint` 阈值按结果量动态调整。

**审计结论**：当前静态 `wal_autocheckpoint=10000`（约 40MB）合理：
- WAL 累积 40MB 才 checkpoint，避免频繁 checkpoint 阻塞写
- 读连接 WAL 模式下完全并行，不受 checkpoint 影响
- 动态调整需预估结果量，引入复杂度且收益不明显
- 属过度优化，跳过

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fuscan/workers/scan_worker.py` | `progress_interval` 默认值 0.3 → 0.1（10fps 目标） |
| `src/fuscan/workers/stats_worker.py` | `progress_interval` 默认值 0.3 → 0.1（与 ScanWorker 一致） |

## 关键决策与依据

### 1. progress_interval 0.3 → 0.1

10fps 目标要求进度更新间隔 ≤ 100ms。原 300ms = 3.3fps，低于目标。调整为 100ms = 10fps。

主线程开销评估：
- 10 信号/秒 × ~1ms/信号（ProgressInfo 构造 + 跨线程 queued 信号 + QML 属性更新）= 10ms/秒
- 占主线程 1% CPU，可接受

### 2. 保留 submit + as_completed

取消能力是硬需求（req-13）。`executor.map` 不支持取消未启动 future，大文件扫描取消时
会导致用户等待已提交但未运行的未来完成。现有 `submit + as_completed` + `cancel_all_futures`
+ `shutdown(wait=False)` 组合是最优方案。

### 3. 不添加 ScanWorker 层数量窗口

两级节流（Scanner 数量窗口 + Scanner 时间窗口）已有效。在 ScanWorker 层再添加数量窗口
会形成三级节流，增加复杂度且实际不触发（因 Scanner 时间窗口已限制 on_progress 调用频率）。

## 测试验证结果

- `tests/test_workers.py`：全部通过（329 passed）
- `tests/test_scanner.py`：全部通过
- `tests/test_archive.py`：全部通过
- `ruff check`：All checks passed
- `pyrefly check`：0 errors

## 遗留事项

- benchmark 量化：本轮未新增 benchmark。10fps 目标已通过理论计算确认达标，实际帧率
  需 QML Profiler 实测，留待 iter-131 阶段统一验证
- 计划中"数量窗口 50 条"未实现：经审计确认现有两级节流已足够，三级节流属过度设计

## 下一轮计划

进入 iter-131「结果列表渲染优化 + 内存占用」：
- `ResultsPage.qml` `cacheBuffer` 按结果量动态调整
- QML delegate 属性绑定审计：减少 `model.*` 重复求值
- 大结果集（> 5 万）时 `ResultListModel.data()` 惰性计算严重度文本/色值
- `ScanReport` 内存占用优化：命中结果按需构造
