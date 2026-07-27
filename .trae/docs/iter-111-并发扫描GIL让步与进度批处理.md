# iter-111 并发扫描 GIL 让步与进度批处理

## 需求清单

- [x] 优化并发扫描 GIL 释放与线程池批次平衡（req-34 第 5 项）

## 迭代目标

将 `_scan_concurrent` 的 GIL 让步从硬编码常量改为实例级自适应阈值，
并在并发模式下引入进度 emit 批处理，减少高吞吐场景下的回调开销。

## 改动文件清单

- `src/fuscan/scanner/scanner.py`：
  - 删除未使用的常量别名 `_BATCH_THRESHOLD`/`_DEFAULT_MAX_FILE_SIZE`/`_GIL_YIELD_INTERVAL`
  - 删除未使用的 `DEFAULT_MAX_FILE_SIZE` import
  - `_skipped_dirs`/`_matched_files` 改用 `PROGRESS_LIST_MAX` 而非别名
  - 新增实例字段 `_gil_yield_interval`：顺序扫描=20，并发扫描=50
  - 新增实例字段 `_progress_emit_batch`：顺序扫描=1，并发扫描=5
  - 抽离 `_collect_concurrent_results` 子方法（控制 PLR0912 分支数）
  - `_scan_concurrent` 改用 `_gil_yield_interval` 与 `_collect_concurrent_results`
  - `_scan_sequential` 改用 `_gil_yield_interval`
  - `_collect_concurrent_results` 实现批处理 emit + 尾部补发
- `tests/test_scanner.py`：新增 6 个 iter-111 专项测试

## 关键决策与依据

1. **自适应 GIL 让步间隔**：
   - 顺序扫描：`max_workers<=1` 时主线程独占 GIL，需每 20 个文件让步避免 UI 卡死
   - 并发扫描：PyO3 提取器（pdf_oxide/calamine）在 Rust 层释放 GIL，worker I/O
     期间主线程自然获得调度，让步间隔提高到 50 减少 sleep(0) 调用开销
   - 公式：`GIL_YIELD_INTERVAL * 5 // 2 = 50`（10万文件节省约 3ms）

2. **进度 emit 批处理**：
   - 顺序扫描：保持每文件 emit（用户期望实时反馈）
   - 并发扫描：每 5 个 future 完成才调用一次 `_emit_progress`（内部仍有 150ms 节流）
   - 减少 `time.perf_counter()` + deque tuple 拷贝的函数调用开销
   - 尾部不足一批的剩余进度补发一次，避免状态丢失

3. **抽离 `_collect_concurrent_results`**：
   - 原 `_scan_concurrent` 单方法 13 个分支（PLR0912 阈值 12）
   - 抽离 future 收集循环为独立方法，分支数降到 6
   - 提高可读性，便于独立测试批处理逻辑

4. **清理冗余别名**：
   - `_BATCH_THRESHOLD`/`_DEFAULT_MAX_FILE_SIZE`/`_GIL_YIELD_INTERVAL` 模块级别名
     在 iter-109 抽离 `_cache_phase` 后已无人引用，删除避免误导

## 代码实现情况

### scanner.py 关键改动

```python
# iter-111：自适应 GIL 让步间隔
self._gil_yield_interval: int = (
    GIL_YIELD_INTERVAL if not max_workers or max_workers <= 1
    else GIL_YIELD_INTERVAL * 5 // 2
)
# iter-111：进度 emit 批处理阈值
self._progress_emit_batch: int = 5 if (max_workers and max_workers > 1) else 1

# _collect_concurrent_results 内的批处理逻辑
emit_counter += 1
if emit_counter >= self._progress_emit_batch:
    self._emit_progress(str(entry.path), scanned, matched, errors, matches)
    emit_counter = 0
# 尾部补发
if emit_counter > 0 and self._on_progress is not None:
    self._emit_progress("", scanned, matched, errors, matches)
```

### 测试覆盖

新增 6 个测试验证 iter-111 行为：

- `test_iter111_gil_yield_interval_sequential`：顺序扫描使用基础间隔 20
- `test_iter111_gil_yield_interval_concurrent`：并发扫描使用扩大间隔 50
- `test_iter111_progress_emit_batch_sequential`：顺序扫描 emit 批处理为 1
- `test_iter111_progress_emit_batch_concurrent`：并发扫描 emit 批处理为 5
- `test_iter111_concurrent_progress_emitted_at_least_once`：并发批处理至少触发最终进度
- `test_iter111_concurrent_batch_tail_flush`：非整除时尾部补发生效

## 整合优化情况

- 抽离 `_collect_concurrent_results` 解决 PLR0912 警告
- 清理未使用的常量别名与 import，减少代码噪音

## 测试验证结果

- `ruff check`：通过
- `ruff format --check`：通过
- `pyrefly check`：通过（1 suppressed 非新增）
- `pytest TestScannerConcurrency`：14 passed（8 原有 + 6 新增）
- `pytest`（全套）：1744 passed
- 覆盖率：93.56%（从 93.45% 提升 0.11%，未达 95% 阈值，留待 iter-116）

## 遗留事项

- 全套覆盖率 93.56% 未达 95% 阈值，iter-116 将专门处理低覆盖模块
  （walker.py 92% / _archive_phase.py 91% / scan_mode.py 83% 等）

## 下一轮计划

iter-112：增强结果列表过滤/搜索/排序能力（按规则/严重度/类型）
