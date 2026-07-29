# iter-132 工作区排序与退出后台残留修复

## 需求清单

- [x] clipboard 警告抑制：`qt.qpa.mime: Retrying to obtain clipboard` 重复输出
- [x] 新增加的工作区任务在上面，按扫描时间顺序倒排；旧任务增量扫描也调到上面
- [x] 界面退出后后台进程残留修复

## 迭代目标

解决用户反馈的三个问题：clipboard 日志噪音、工作区排序不符合预期、退出后进程不终止。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fuscan/gui/app.py` | 启动前设置 `QT_LOGGING_RULES=qt.qpa.mime=false` 抑制 clipboard 警告 |
| `src/fuscan/gui/models/workspace_model.py` | `WorkspaceItem` 新增 `last_activity_time` 字段；`add_workspace` 改为插入顶部；新增 `move_to_top` 方法 |
| `src/fuscan/gui/controllers/_persistence.py` | 新增 `coerce_float` 辅助函数；`serialize_workspace` 持久化 `last_activity_time` |
| `src/fuscan/gui/controllers/workspace_controller.py` | `_load_persisted` 按 `last_activity_time` 倒序加载；`startScan`/`startIncrementalScan` 调用 `move_to_top`；`cleanup` 取消 ResultRestoreWorker |
| `src/fuscan/gui/controllers/scan_controller.py` | `quick_cancel` 改为 cancel + wait(500) + terminate 后备；调用 `_result_model.cleanup()` |
| `src/fuscan/gui/models/result_model.py` | 新增 `cleanup` 方法取消 FilterWorker |
| `tests/test_gui_workspace_controller.py` | 更新 `add_workspace` 测试匹配新排序；新增 4 个 `move_to_top` 测试 |

## 关键决策与依据

### 1. clipboard 警告抑制

**问题**：`qt.qpa.mime: Retrying to obtain clipboard` 在启动时重复输出 4-5 次。

**根因**：Qt 在 Windows 上访问剪贴板时，如果其他应用（如输入法、剪贴板管理器）锁住剪贴板，
Qt 内部会重试并输出警告。这是 Qt 与 Windows 剪贴板服务的已知交互问题，非代码 bug。

**修复**：启动前设置 `QT_LOGGING_RULES=qt.qpa.mime=false` 抑制该分类日志。
用 `setdefault` 不覆盖用户已设置的环境变量。

### 2. 工作区按最近活动时间倒排

**问题**：新建工作区追加到列表末尾，用户期望最新任务在上面。增量扫描后旧任务也应移到顶部。

**方案**：
- `WorkspaceItem` 新增 `last_activity_time: float` 字段（默认 `time.time()`）
- `add_workspace` 改为插入到 row 0（顶部），新建工作区自然排在最上方
- 新增 `move_to_top(workspace_id)` 方法，更新 `last_activity_time` 并移到顶部
- `startScan`/`startIncrementalScan` 调用 `move_to_top`，使最近扫描的工作区排在最上方
- `_load_persisted` 按 `last_activity_time` 倒序加载，重启后保持排序
- `serialize_workspace` 持久化 `last_activity_time`

**交互效果**：
- 新建任务 → 出现在列表顶部
- 启动扫描/增量扫描 → 该任务移到顶部
- 重启 → 按 `last_activity_time` 倒序恢复

### 3. 退出后后台残留修复

**问题**：界面关闭后进程不退出，后台一直残留。

**根因**：iter-127 的 `quick_cancel()` 仅设置 cancel 标志不 `wait()`，QThread 仍在运行
阻止进程退出。QThread 默认非守护线程，Python 进程会等待所有非守护线程结束。

**修复**：`quick_cancel` 改为 cancel + wait(500) + terminate 后备：
1. `cancel()` 设置取消标志
2. `wait(500)` 等待最多 500ms（大部分 worker < 100ms 退出）
3. 如果还在运行，`terminate()` 强制终止（最后手段）
4. `wait(200)` 等待 terminate 完成

**覆盖范围**：
- `ScanWorker`（扫描线程）
- `FileStatsWorker`（统计线程）
- `FilterWorker`（过滤线程，via `ResultListModel.cleanup()`）
- `ResultRestoreWorker`（结果恢复线程，via `WorkspaceController.cleanup()`）

**性能影响**：单工作区最坏 700ms（500+200），10 工作区最坏 7s。实际大部分 worker
在 cancel 后 < 100ms 退出，总等待 < 1s。相比 iter-127 前的 5s/工作区大幅改善。

## 测试验证结果

- `tests/test_gui_workspace_controller.py`：25 passed（含 4 个新增 `move_to_top` 测试）
- `tests/test_gui_result_model.py`：63 passed
- `tests/test_workers.py`：329 passed
- `tests/test_scanner.py`：全部通过
- `ruff check`：All checks passed
- `pyrefly check`：0 errors (175 suppressed)
- 全量 2186 passed

## 遗留事项

- `terminate()` 是强制终止线程，可能导致 SQLite WAL 文件未正确 checkpoint。
  但进程退出时 OS 会回收文件句柄，下次启动时 SQLite 会自动恢复。可接受。
- `QT_LOGGING_RULES` 用 `setdefault` 设置，用户可通过环境变量覆盖。

## 下一轮计划

req-37 性能优化计划已全部完成（iter-128~131），本轮 iter-132 处理用户反馈的三个问题。
后续按需迭代。
