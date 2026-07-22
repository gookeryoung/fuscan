# iter-71：扫描架构两阶段重构

## 需求清单

详见 `req-17-扫描架构两阶段重构.md`。

## 迭代目标

将扫描架构从"流水线模式（walk 与 scan 并行）"重构为"两阶段模式（先收集文件清单再并发扫描）"，同时将文件后缀过滤从规则级 `Rule.file_extensions` 提升为全局 `Config.scan_extensions`，提升 I/O 密集型场景的吞吐量。

## 改动文件清单

### 核心改动

| 文件 | 改动内容 |
|------|---------|
| `src/fuscan/config.py` | 新增 `scan_extensions: list[str] \| None = None` 字段 |
| `src/fuscan/scanner/scanner.py` | 重构 `scan()` 为两阶段架构；新增 `_scan_concurrent`；删除 `_scan_pipelined`/`_collect_scan_futures`/`_drain_futures`；`_should_scan` 改用 `_scan_extensions`；`__init__` 新增 `scan_extensions` 参数与 `_archive_extensions` 字段；`_scan_entry_uncached`/`_scan_entry_cached` 移除 `rule.file_extensions` 过滤 |
| `src/fuscan/archive/scanner.py` | `_scan_entry_uncached`/`_scan_entry_cached` 移除 `rule.file_extensions` 过滤 |
| `src/fuscan/watcher/incremental.py` | `__init__` 新增 `scan_extensions` 参数并传递给 Scanner |
| `src/fuscan/rules/model.py` | `Rule.file_extensions` 字段标记废弃（保留向后兼容） |

### GUI 改动

| 文件 | 改动内容 |
|------|---------|
| `src/fuscan/gui/worker.py` | `ScanWorker.__init__` 新增 `scan_extensions` 参数；`run()` 传递给 Scanner |
| `src/fuscan/gui/main_window.py` | 创建 ScanWorker 时传递 `scan_extensions`；规则列表显示改为 "(全局)" |
| `src/fuscan/gui/settings_dialog.py` | `_load_config`/`_save_config` 新增 `scan_extensions` 加载/保存 |
| `src/fuscan/gui/settings_dialog.ui` | 新增"后缀过滤"分组与 `scan_extensions_edit` 控件 |
| `src/fuscan/gui/settings_dialog_ui.py` | 同步 .ui 生成的控件代码 |

### 其他改动

| 文件 | 改动内容 |
|------|---------|
| `src/fuscan/cli.py` | 移除规则列表中的 file_extensions 显示 |

### 测试改动

| 文件 | 改动内容 |
|------|---------|
| `tests/test_scanner.py` | `test_file_extensions_filter`/`test_concurrent_with_file_extensions_filter` 改用 `scan_extensions`；`test_pipelined_large_fileset_triggers_drain` → `test_concurrent_large_fileset_two_phase`；`test_pipelined_drain_error_handling` → `test_concurrent_scan_entry_error_handling` |
| `tests/test_watcher.py` | 两处 `file_extensions` 测试改用 `IncrementalScanner(scan_extensions=...)` |
| `tests/test_multiformat_scan.py` | 两处 `file_extensions` 测试改用 `Scanner(scan_extensions=...)` |
| `tests/test_archive.py` | `test_scan_archive_file_extensions_filter` → `test_scan_archive_scans_all_entries` |

## 关键决策与依据

### D1：两阶段架构替代流水线模式

**决策**：将 `scan()` 从"walk 与 scan 并行的流水线模式"改为"先收集文件清单再并发扫描的两阶段模式"。

**依据**：
- 流水线模式中 walk 线程与 worker 线程争抢磁盘 I/O，导致吞吐量下降
- 两阶段模式下，阶段1单线程遍历（I/O 轻量）完成后，阶段2可对完整清单做全局后缀过滤，减少无效 future 提交
- 先收集再扫描使 walk 不再被 worker 阻塞，遍历速度更快

**代价**：大目录树需在内存中保存全部 entries 列表，但典型场景（万级文件）内存占用可接受。

### D2：全局 scan_extensions 替代规则级 file_extensions

**决策**：新增 `Config.scan_extensions` 全局后缀过滤，`Rule.file_extensions` 标记废弃。

**依据**：
- 用户请求"把文件后缀调为全局规则而不是特定规则"
- 全局过滤在遍历阶段一次完成，避免每个规则重复检查
- `Rule.file_extensions` 字段保留以向后兼容旧规则文件解析，但 Scanner 不再读取

### D3：archive 文件不受 scan_extensions 限制

**决策**：`scan_archives=True` 时，archive 文件（zip/rar/7z）即使不在 `scan_extensions` 中也收集。

**依据**：
- `scan_extensions=("conf",)` 时用户可能仍想扫描压缩包内的 .conf 文件
- 压缩包内条目不再按 `scan_extensions` 二次过滤，由 ArchiveScanner 统一扫描全部条目
- 实现：`_should_scan` 中检查 `entry.extension in self._archive_extensions`

### D4：IncrementalScanner 同步支持 scan_extensions

**决策**：`IncrementalScanner.__init__` 新增 `scan_extensions` 参数并传递给底层 Scanner。

**依据**：`IncrementalScanner.scan_paths` 调用 `self._scanner._should_scan(entry)` 判断是否扫描，需通过底层 Scanner 的 `scan_extensions` 生效。

## 代码实现情况

### Scanner.scan() 两阶段架构

```python
# 阶段1：单线程遍历收集 entries（I/O 轻量，按全局后缀过滤）
with self._perf.measure("walk"):
    for entry in self._walker.walk(root):
        if self._check_control():
            break
        total += 1
        if not self._should_scan(entry):
            skipped += 1
            continue
        entries.append(entry)

# 阶段2：并发扫描（max_workers > 1）或顺序扫描
if self._max_workers and self._max_workers > 1:
    scanned, matched, errors, matches = self._scan_concurrent(entries, results)
else:
    scanned, matched, errors, matches = self._scan_sequential(entries, results)
```

### _scan_concurrent 方法

一次性提交所有 entries 到 ThreadPoolExecutor，用 as_completed 按完成顺序收集结果。取消时对未启动 future 调 `cancel()` 跳过阻塞等待。

### _should_scan 方法

```python
def _should_scan(self, entry: FileEntry) -> bool:
    if entry.is_dir:
        return False
    if not self._scan_extensions:
        return True
    if entry.extension in self._scan_extensions:
        return True
    # scan_archives=True 时 archive 文件即使不在 scan_extensions 中也收集
    return bool(self._archive_extensions) and entry.extension in self._archive_extensions
```

## 整合优化情况

- 删除废弃的 `_scan_pipelined`/`_collect_scan_futures`/`_drain_futures` 三个方法（约 130 行）
- `cli.py` 移除 file_extensions 显示（字段已废弃）
- `main_window.py` 规则列表扩展名列统一显示 "(全局)"

## 测试验证结果

- ruff check: All checks passed
- ruff format: 93 files already formatted
- pyrefly: 0 errors (463 suppressed, 60 warnings)
- pytest: 1445 passed, 16 deselected, coverage 96.02%

## 遗留事项

无。

## 下一轮计划

无。本次迭代已完整交付用户需求。
