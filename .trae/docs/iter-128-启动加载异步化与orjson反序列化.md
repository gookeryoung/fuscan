# iter-128 启动加载异步化与 orjson 反序列化

## 需求清单

- [x] `_load_cached_results()` 移至后台 QThread，主线程不阻塞
- [x] `ScanReport.from_json()` / `IncrementalManifest.from_json()` 用 `orjson.loads()` 替换 `json.loads()`
- [x] 启动时仅恢复第一个工作区的结果，其余延迟加载（切换到该工作区时才加载）
- [x] QML 启动时显示「正在恢复扫描结果…」占位态，加载完成后无缝切换

## 迭代目标

10 万命中结果的工作区启动到可交互 < 1s（原同步加载阻塞数秒）。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `pyproject.toml` | 新增 `orjson>=3.9.0` 依赖（iter-127 已完成） |
| `src/fuscan/scanner/result.py` | 新增 `_json_loads` helper；`from_json()` 接受 `str\|bytes`，用 orjson 反序列化 |
| `src/fuscan/workers/restore_worker.py` | **新文件**：`ResultRestoreWorker(QThread)` 后台加载缓存结果 |
| `src/fuscan/gui/controllers/scan_controller.py` | 新增 `restoring` 属性 + `restoringChanged` 信号 + `_set_restoring()` |
| `src/fuscan/gui/controllers/workspace_controller.py` | `_load_cached_results` → `_try_load_cached_results`（异步）+ 延迟加载 |
| `src/fuscan/gui/views/pages/ResultsPage.qml` | 新增 BusyIndicator 恢复中占位态 |
| `tests/test_gui_workspace_controller.py` | 新增 `qapp` fixture + `_wait_for_restore` helper；4 个测试改为异步 |
| `src/fuscan/gui/resources_rc.py` | QRC 重新编译 |

## 关键决策与依据

### 1. orjson 反序列化

`_json_loads` helper 平行于 iter-127 的 `_json_dumps`/`_json_dumps_bytes`，优先用
`orjson.loads()`（Rust 实现，比 stdlib `json.loads` 快 2-3x），fallback 到 `json.loads`。
`from_json()` 签名从 `str` 扩展为 `str | bytes`，配合 `read_bytes()` 跳过
`.decode()` + `.encode()` 往返。

### 2. ResultRestoreWorker(QThread)

遵循既有 `ScanWorker(QThread)` 模式：子类化 QThread，重写 `run()`，通过信号回传结果。
`ScanReport` 是 frozen dataclass（非 QObject），可安全跨线程传递。

信号设计：
- `restore_done(str, object)`：(ws_id, ScanReport) 加载成功
- `restore_failed(str, str)`：(ws_id, error_message) 加载失败
- `finished`（QThread 内置）：worker 结束后清理 QObject

### 3. 延迟加载策略

`_load_persisted()` 不再对每个工作区调 `_load_cached_results()`。改为：
1. 创建所有工作区 item（status_text/matched_count 等从 workspaces.json 恢复，列表显示正确）
2. 仅对第一个工作区启动后台恢复（QML 默认选中第一个）
3. `setCurrentWorkspaceId()` 时对目标工作区触发 `_try_load_cached_results()`

幂等性：`_restored_workspaces` + `_restoring_workspaces` 集合保证不重复加载。

### 4. restoring 属性

`ScanController` 新增 `restoring` bool 属性 + `restoringChanged` 信号。
`WorkspaceController` 在启动/完成恢复时调 `controller._set_restoring(True/False)`。
QML `ResultsPage` 据此显示 BusyIndicator + "正在恢复扫描结果…" 占位态。

### 5. 测试异步化

新增 `qapp` session fixture（创建 QApplication）+ `_wait_for_restore` helper
（`QCoreApplication.processEvents()` 循环等待 worker 完成）。
4 个 `TestLoadCachedResults` 测试全部改为异步模式。

## 代码实现情况

- `_json_loads` helper：orjson 优先，stdlib fallback，自动校验顶层 dict
- `ResultRestoreWorker`：`read_bytes` + `ScanReport.from_json(data)` + 信号回传
- `_try_load_cached_results`：幂等检查 → 标记 restoring → 创建 worker → 连接信号 → start
- `_on_restore_done`：主线程 `restoreFromReport` + 清除 restoring 态
- `_on_restore_failed`：清除 restoring 态 + 日志
- `_cleanup_restore_worker`：`finished` 信号回调 `deleteLater` 避免 QObject 泄漏
- QML：BusyIndicator + Label 居中显示，空态 Label 增加 `!restoring` 条件

## 测试验证结果

- `ruff check`：5 文件 All checks passed
- `ruff format --check`：5 files already formatted
- `pyrefly check`：0 errors（177 suppressed）
- `test_gui_workspace_controller.py`：169 passed（含 4 个异步恢复测试 + 4 个 cleanup 测试）
- `test_scanner.py`：174 passed（含 from_json round-trip 测试）
- QML 加载验证：`Loaded: True`（offscreen 模式）

## 遗留事项

- iter-128 benchmark 尚未编写（orjson vs stdlib json 反序列化性能对比）
- 实际 10 万结果启动耗时需在真实环境验证（当前仅单元测试验证功能正确性）
- iter-129~131 按 req-37 计划继续

## 下一轮计划

iter-129：大结果集过滤/排序性能优化——`_apply_filter_and_sort()` 移至后台线程 +
倒排索引 + 排序缓存。
