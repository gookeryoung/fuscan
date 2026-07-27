# iter-115 扫描历史归档与多次扫描对比摘要

## 需求清单

- [x] 扫描结束时将关键指标（命中数、文件路径集合、规则名、耗时等）归档到 `~/.fuscan/history.json`
- [x] 提供按工作区查询历史记录的接口（最新在前，limit 可控）
- [x] 支持两次扫描对比：计算新增/已解决/持续命中文件集合、规则变化、趋势判断
- [x] WorkspaceController 集成 HistoryStore，扫描结束自动归档
- [x] QML 暴露 `workspaceHistoryJson` / `compareWithPreviousScan` / `clearWorkspaceHistory` 槽
- [x] WorkspaceCard 增加历史按钮与对话框（对比摘要 + 历史列表 + 清空按钮）
- [x] 修复历史中重复 `scan_id` 自动覆盖、容量超限自动裁剪
- [x] 修复 `previous_entry` 逻辑 bug（原实现「第一条不等于 current」会返回更新的条目）
- [x] 修复 `_load` 对非 dict 条目的容错（原 `from_dict` 容错导致损坏条目仍被加载）

## 迭代目标

将每次扫描关键指标持久化到磁盘，重启后仍可在 GUI 中查看历史并对比两次扫描差异，
为后续趋势分析、回归监控奠定基础。

## 改动文件清单

新增：
- `src/fuscan/history/__init__.py` — 模块入口，导出公共 API
- `src/fuscan/history/model.py` — `ScanHistoryEntry` frozen dataclass + 序列化容错
- `src/fuscan/history/store.py` — `HistoryStore` JSON 持久化（线程安全 + 原子写入 + 容量裁剪）
- `src/fuscan/history/comparator.py` — `ScanComparison` + `compare_scans` 集合运算
- `tests/test_history.py` — 34 个测试覆盖所有公共 API（覆盖率 98%）

修改：
- `src/fuscan/gui/controllers/scan_controller.py` — 新增 `build_history_entry` 方法
- `src/fuscan/gui/controllers/workspace_controller.py` — 集成 HistoryStore，新增 3 个 QML 槽
- `src/fuscan/gui/views/components/WorkspaceCard.qml` — 新增「历史」按钮与对话框

## 关键决策与依据

1. **存储位置**：`~/.fuscan/history.json`，与 `workspaces.json`/`skips.json` 同目录，保持配置一致性
2. **JSON 而非 SQLite**：历史条目数有限（默认每工作区 50 条），JSON 易于人眼检视与跨版本调试
3. **frozen dataclass**：归档条目不可变，避免误修改后与磁盘数据不一致
4. **集合运算**：`hit_paths`/`rule_names` 用 set 做 new/resolved/persistent 计算，O(n) 复杂度
5. **JSON 序列化槽**：避免直接暴露 Python 对象到 QML，降低类型推断复杂度
6. **历史条目数限制 50**：足够覆盖近一个月日均扫描频次，超出按 `finished_at` 倒序丢弃最旧
7. **归档失败不影响主流程**：`_archive_scan_history` 捕获 `Exception` 仅 warning，不阻断扫描完成

## 代码实现情况

### 数据模型

`ScanHistoryEntry` 包含 14 个字段：
- 标识：`scan_id`（时间前缀+随机后缀）、`workspace_id`、`workspace_name`（快照）
- 时间：`started_at`、`finished_at`（ISO UTC）
- 状态：`status`（completed/cancelled/failed）
- 计数：`total_files`、`scanned_files`、`matched_files`、`skipped_files`、`error_count`
- 耗时：`duration_seconds`
- 对比键：`hit_paths`、`rule_names`（排序元组）
- 摘要：`summary`（状态栏文本快照）

`from_dict` 对每个字段类型校验，不符则回退默认值，避免损坏条目阻塞加载。

### 持久化存储

`HistoryStore` 线程安全（`RLock`），原子写入（临时文件 + `Path.replace`）。
- `add`：同 `scan_id` 覆盖，自动按工作区裁剪
- `workspace_history`：倒序返回，支持 limit
- `latest_entry` / `previous_entry`：便捷查询
- `clear_workspace` / `clear_all`：清理接口
- `_load`：版本不兼容/非 dict payload/非 dict 条目均跳过

### 对比逻辑

`compare_scans(current, previous)` 用集合运算计算：
- `new_hits = current_paths - previous_paths`
- `resolved_hits = previous_paths - current_paths`
- `persistent_hits = current_paths & previous_paths`
- `matched_delta = current.matched_files - previous.matched_files`
- `new_rules` / `dropped_rules` 同理

`ScanComparison.trend` 属性：首次/改善/恶化/持平
`ScanComparison.summary()` 方法：直接供 UI 展示的中文摘要

### GUI 集成

- 扫描结束（scanning → 非 scanning）时 `WorkspaceController._sync_workspace_state` 触发 `_archive_scan_history`
- `workspaceHistoryJson(ws_id)` 返回 JSON 数组（含 14 个字段），供 QML 解析展示
- `compareWithPreviousScan(ws_id)` 返回对比 JSON（含 trend/summary/前 50 条 new/resolved hits）
- `clearWorkspaceHistory(ws_id)` 返回清除条目数
- WorkspaceCard 展开区新增「历史」按钮，对话框含对比摘要 + 历史列表 + 清空按钮

## 整合优化情况

- 修复 `previous_entry` 逻辑 bug：原实现遍历返回第一条不等于 current 的条目，
  若 current 是最新则返回更早一条（正确），若 current 是最早则仍返回最新一条（错误）。
  新实现先定位 current 位置再取下一条（idx+1）。
- 修复 `_load` 对非 dict 条目容错：原 `from_dict` 对非 dict 返回默认实例，
  导致损坏字符串条目仍被加载为默认 ScanHistoryEntry。新实现先 `isinstance(raw, dict)` 判断。

## 测试验证结果

`tests/test_history.py` 34 个测试覆盖：
- `TestScanHistoryEntry`：默认值/序列化往返/类型容错/部分字段/frozen 6 个测试
- `TestHistoryStore`：增删查改/去重/持久化/清理/容量裁剪/损坏文件/版本不兼容/线程安全 12 个测试
- `TestCompareScans`：首次/改善/恶化/持平/摘要/frozen 7 个测试
- `TestHistoryStoreIntegration`：通过 store 模拟两次扫描对比 2 个测试
- `TestHistoryStoreEdgeCases`：previous_entry 边界/非 dict payload/非 list entries/OSError 7 个测试

覆盖率：
- `__init__.py` 100%
- `comparator.py` 100%
- `model.py` 100%
- `store.py` 97%
- **总计 98%**（超过 95% 阈值）

门禁：
- `ruff check src/fuscan/history tests/test_history.py`：通过
- `ruff format --check`：通过
- `pytest tests/test_history.py`：34 passed in 0.32s

## 遗留事项

- `store.py:130` `previous_entry` 最早条目分支已在测试中覆盖但未达 100%（分支计数差异）
- `store.py:196-197` `from_dict` 抛异常的 except 分支为防御性代码，实际不可达（已加测试验证）
- iter-116 将系统性提升整体测试覆盖率并优化慢测试

## 下一轮计划

iter-116：测试覆盖率提升与测试性能优化
- 跑全套测试得到当前覆盖率基线
- 识别未覆盖模块与低覆盖文件
- 补齐关键模块测试至 95%+
- 标记慢测试（`@pytest.mark.slow`）并优化执行时间
- 修复遗留 lint 警告（如 `test_gui_controllers_submodules.py:675` 的 UP031）
