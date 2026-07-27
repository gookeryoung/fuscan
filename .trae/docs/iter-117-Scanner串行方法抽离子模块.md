# iter-117 Scanner 串行方法抽离子模块（`_scan_pipeline`）

## 需求清单

- [x] 评估 `scanner.py` 中 `_scan_pipeline`/`_scan_sequential`/`_scan_concurrent` 等串行方法的边界
- [x] 抽离纯逻辑到 `_pipeline_phase.py` 子模块
- [x] 保持 `Scanner` 类公共 API 不变，已有测试全部通过
- [x] `scanner.py` 行数下降到 700 行以下（验收标准）

## 迭代目标

将 `scanner.py` 中三个长方法（`_scan_sequential`/`_scan_concurrent`/`_collect_concurrent_results`）
抽离到 `_pipeline_phase.py`，使 `scanner.py` 仅保留主类编排逻辑，单文件单一职责更清晰。
遵循 `_archive_phase.py` 已建立的抽离模式（Scanner 实例作为参数传入）。

## 改动文件清单

新增：
- `src/fuscan/scanner/_pipeline_phase.py` — scan 阶段顺序/并发扫描子流程（189 行）

修改：
- `src/fuscan/scanner/scanner.py` — 删除三个抽离方法（约 150 行），改为调用 `run_pipeline_phase`；清理未使用的导入（`Future`/`ThreadPoolExecutor`/`as_completed`/`cancel_all_futures`）；更新模块文档字符串

## 关键决策与依据

1. **抽离模式对齐 `_archive_phase.py`**：模块级函数 + Scanner 实例作为参数传入，
   访问 `_check_control`/`_emit_progress`/`_scan_entry`/`_matched_files` 等内部状态。
   不引入新的抽象基类或协议，保持与现有 `_archive_phase`/`_cache_phase` 一致。
2. **统一入口 `run_pipeline_phase`**：按 `_max_workers` 分派到 `_scan_sequential`
   或 `_scan_concurrent`，调用方 `scan_entries` 仅做单次分派调用，简化主流程。
3. **不抽离 `_scan_entry_cached`/`_scan_entry_uncached`**：这两个方法与 `_compiled`/
   `_compiled_with_hash`/`_content_rule_names` 等 Scanner 实例状态紧耦合，
   抽离收益低；已在 iter-109 抽离 `extract_with_cache`/`build_hits_from_cache`/
   `BatchBuffer` 到 `_cache_phase.py`，进一步抽离边际效用递减。
4. **pyrefly `bad-argument-type` 抑制**：`run_pipeline_phase(self, ...)` 调用处
   添加 `# pyrefly: ignore [bad-argument-type]`，与 `run_archive_phase(self, ...)`
   调用一致。这是 pyrefly 对 `Self@Scanner` 与 `Scanner` 类型参数推断差异的已知限制。

## 代码实现情况

### `_pipeline_phase.py` 结构

- `run_pipeline_phase(scanner, entries, results) -> tuple[int, int, int, int]`：
  统一入口，按 `_max_workers` 分派
- `_scan_sequential(scanner, entries, results)`：单线程顺序扫描，含 GIL 让步逻辑
- `_scan_concurrent(scanner, entries, results)`：ThreadPoolExecutor 并发扫描，
  含提交期间取消加速
- `_collect_concurrent_results(scanner, future_to_entry, results, pool)`：
  阻塞收集 future 结果，含 GIL 让步与进度 emit 批处理

### `scanner.py` 改动

- `scan_entries` 阶段 2 改为 `run_pipeline_phase(self, entries, results)` 单次调用
- 删除三个原方法（约 150 行）
- 清理未使用导入：`Future`/`ThreadPoolExecutor`/`as_completed`/`cancel_all_futures`
- 更新模块文档字符串，添加 `_pipeline_phase` 与 `_cache_phase` 说明

## 整合优化情况

- 抽离后 `scanner.py` 从 834 行降至 603 行（满足 < 700 行验收标准）
- 模块结构与 `_archive_phase.py`/`_cache_phase.py` 一致：模块级函数 + Scanner 实例参数
- 未覆盖分支（`_pipeline_phase.py:141-142, 147-149`）为提交期间取消的边缘场景，
  原本在 `scanner.py` 中也未被测试覆盖，抽离前后行为一致

## 测试验证结果

```
uv run ruff check src tests           → All checks passed
uv run ruff format --check src tests  → 134 files already formatted
uv run pyrefly check                  → 0 errors (679 suppressed)
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95
                                      → 1935 passed, 54 deselected
                                         TOTAL 95.84% (required 90.0%)
```

覆盖率保持 95.84%，无回归。模块级覆盖率：
- `_pipeline_phase.py` 93%（未覆盖：提交期间取消分支）
- `scanner.py` 97%（从 96% 提升，因减少分支）
- `_cache_phase.py` 100%
- `_archive_phase.py` 91%

## 遗留事项

- `_pipeline_phase.py:141-142, 147-149` 提交期间取消分支未测试覆盖
  （原 `scanner.py` 中同样未覆盖，非本次抽离引入）
- 可考虑后续补一个测试：在 `_scan_concurrent` 提交循环中触发取消，
  验证 `cancel_all_futures` + `shutdown(wait=False)` 路径

## 下一轮计划

iter-118：缓存预热与并发读取优化
- 评估 `CacheStore` 查询路径（`lookup_file_hash`/`get_cached_hits`/`get_extracted_content`）的并发瓶颈
- 探索缓存预热：扫描开始前批量预加载高频路径的 file_hash 与提取内容
- 优化并发读取：减少 RLock 持锁粒度或引入读多写少的并发结构
- benchmark 数据佐证改进或证明无回归
