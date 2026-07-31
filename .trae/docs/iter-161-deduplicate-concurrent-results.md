# iter-161：并发扫描路径去重

## 需求清单

- [x] 并发扫描 entries 按路径去重，避免同一文件被重复扫描
- [x] 验证去重后结果列表与统计一致
- [x] 单条唯一 entries 不受影响

## 迭代目标

在 ThreadPoolExecutor 提交前对 `entries` 按文件路径去重，避免：

- 同一文件被重复提交到线程池，造成无谓 I/O 与 Match 对象构造
- 重复结果进入 `results` 列表，导致 GUI 层扁平化数据冗余
- 进度计数（scanned/matched）被同一文件累计多次

## 改动文件

- `src/fuscan/scanner/_pipeline_phase.py`：`_scan_concurrent` 入口处按路径去重
- `tests/test_scanner.py`：新增 `TestIter161ConcurrentDedup` 测试类

## 关键决策与依据

### 去重位置

选择在 **entries 提交前**（而非收集后）去重，原因：

1. **提交前去重**：直接阻止重复 FileEntry 进入线程池，避免 I/O + matcher 开销
2. **收集后去重**：虽然可减少结果列表，但已产生的 I/O 与 Match 对象构造无法回收

### 去重键

使用 `str(entry.path)` 作为去重键。`FileEntry.path` 为 `Path` 对象，
直接用 `Path` 作为键在不同平台上可能出现语义相同但对象不等的情况
（如 `Path("a.txt")` 与 `Path("./a.txt")`）。使用字符串形式统一规范化。

### 日志

当存在重复条目时记录 `INFO` 日志，便于问题排查。

## 代码实现情况

### `_scan_concurrent` 去重逻辑

```python
# iter-161：并发提交前按路径去重，避免同一文件被重复扫描
seen_paths: set[str] = set()
unique_entries: list[FileEntry] = []
dup_skipped = 0
for entry in entries:
    entry_path_str = str(entry.path)
    if entry_path_str in seen_paths:
        dup_skipped += 1
        continue
    seen_paths.add(entry_path_str)
    unique_entries.append(entry)
if dup_skipped > 0:
    logger.info("并发扫描：去重 %d 个重复条目", dup_skipped)
```

提交循环改用 `unique_entries` 替代 `entries`。

## 测试验证结果

```
tests/test_scanner.py::TestIter161ConcurrentDedup::test_concurrent_dedup_when_duplicate_entries PASSED
tests/test_scanner.py::TestIter161ConcurrentDedup::test_concurrent_dedup_single_entry_no_duplicate PASSED
```

全量回归：`2544 passed, 78 deselected`

覆盖场景：

1. 构造含重复 FileEntry 的 entries 列表 → 验证 scanned/matched/results 数量均为 2（去重后仅 2 个唯一文件）
2. 单条唯一 entries → 验证不受去重逻辑影响

## 整合优化情况

- 顺序扫描（`_scan_sequential`）路径已通过 `entries` 构造阶段去重，无需额外处理
- 并发路径通过本次改动确保重复 FileEntry 被过滤
- 无新依赖引入，仅新增日志输出

## 遗留事项

- [ ] 大规模扫描场景（10k+ files）下的去重性能基准（当前 entries 构造阶段已基本保证唯一性，此处为防御性去重）

## 下一轮计划

iter-163：Match/ScanResult 对象构造频率调研与优化
