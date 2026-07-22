# iter-65：扫描性能测量基础设施（req-15 筹备）

## 需求清单

来源：用户反馈"扫描性能始终较低，请问有无提高扫描速度的方案"

- [x] R1：评估扫描器现状，给出优化方案
- [x] R2：实施性能测量基础设施，为定向优化提供数据支撑

## 迭代目标

用户反馈扫描性能低。基于 `benchmarks/baseline.md` 现有基线分析：
首次扫描（无缓存）4 线程 171 files/s，24 线程无额外收益（GIL 限制）；
热缓存 6370 files/s（60 倍提升）。

经 AskUserQuestion 确认：
- 慢的场景：首次扫描慢（无缓存命中）
- 优先方向：先测量再决定

本迭代目标：为 Scanner 关键路径建设分阶段性能测量基础设施，
让用户用 `FUSCAN_PERF=1` 跑一次真实扫描，得到各阶段耗时占比，
再决定定向优化方向（进程池 / 提取器优化 / SQLite 读写分离 / mmap 等）。

## 现状评估

### 既有优化（iter-37/38/39/50/59）

| 优化项 | 说明 |
|--------|------|
| ThreadPoolExecutor 流水线 | walk 与 scan 并行，4 线程已饱和 |
| 三层缓存 | mtime 预筛 + 提取内容缓存 + 规则结果缓存 |
| BLAKE2b 哈希 | OpenSSL 加速 + 释放 GIL |
| SQLite 批量写入 | 50 文件单事务，消除 99% fsync |
| LRU 命中缓存 | 4096 条 OrderedDict，O(1) 访问 |
| walker + DirEntry | os.scandir 复用 stat 缓存 |
| matchers 预编译 | CONTAINS 不区分大小写预编译正则 |
| 取消加速 | _cancel_all_futures 跳过未启动 future |

### 性能基线（iter-39，i7-14700K）

| 场景 | 吞吐量 |
|------|--------|
| 单线程无缓存 | 106 files/s |
| 4 线程无缓存 | 171 files/s |
| 24 线程无缓存 | 171 files/s（4 线程已饱和，GIL 限制） |
| 4 线程+热缓存 | 6370 files/s |

### 瓶颈假设

首次扫描慢的可能根因（待测量验证）：

1. **GIL 限制多线程效率**：4→24 线程无收益，正则匹配/docx/pptx 提取受 GIL
2. **内容提取开销**：docx 7.94ms / pptx 5.78ms 是热点
3. **SQLite 锁竞争**：CacheStore 单连接 RLock 串行化所有读写
4. **文件 I/O 模式**：read_bytes 一次全量读取，大文件可能成为瓶颈
5. **规则集扩展名过滤**：unrestricted rule 会导致所有文件都读内容

## R1 优化方案（按改动成本排序）

### 短期（零代码改动，立即可用）

1. **max_workers 调优**：HDD 建议 4，SSD 保持 8，NVMe 可试 12（GIL 限制收益有限）
2. **规则集审查**：为规则添加 `file_extensions` 限制，减少无关文件读取
3. **scan_archives 权衡**：压缩包扫描开销大，不需要时关闭
4. **ignore_dirs / ignore_extensions 扩展**：添加项目特定忽略项

### 中期（代码改动，下一迭代）

5. **ProcessPoolExecutor 突破 GIL**（最高收益）：正则匹配、docx/pptx 提取是
   CPU 密集型，线程池受 GIL 限制。改进程池可利用多核，预期 2-4 倍。
   难点：缓存共享、ScanResult pickle 开销、进程池预热
6. **SQLite 读写连接分离**：当前单连接 + RLock 串行化。改为每 worker 一个读连接
   + 单写连接，减少锁竞争（WAL 模式已支持）
7. **提取器优化**：评估 lxml 替代 ElementTree、大文件流式提取
8. **mmap 大文件读取**：`read_bytes()` 改 `mmap`，避免内存拷贝

### 长期（大改动，未来迭代）

9. **增量扫描**：基于 mtime + 文件系统监听（watchdog），仅扫描变化文件
10. **Rust/C 扩展**：关键路径用原生扩展释放 GIL，预期 5-10 倍

## R2 性能测量基础设施实施

### 设计决策

**决策1：perf.py 提升到 fuscan/ 根**

原 `src/fuscan/gui/perf.py` 仅服务 GUI 层。Scanner 是 core 层不应依赖 gui 模块。
将 `perf.py` 移到 `src/fuscan/perf.py`（公共模块），GUI 与扫描器共用。
配套更新 `gui/export_worker.py`、`gui/main_window.py`、`tests/test_gui_perf.py`
的导入与 logger 名（`fuscan.gui.perf` → `fuscan.perf`）。

**决策2：新增 PerfStats 聚合统计类**

`PerfTimer` 是单阶段上下文计时器，适合 GUI 卡滞定位。但扫描器需要累计
每个文件的各阶段耗时（read_bytes × 10000 次 + hash × 10000 次 + ...），
用 `PerfTimer` 会产生海量日志。新增 `PerfStats`：

- 线程安全（`threading.Lock` 保护）
- `measure(name)` 上下文管理器累计阶段总耗时/调用次数/最大值
- `record(name, elapsed)` 直接记录（适用于回调内手动计时）
- `report(logger)` 扫描结束时输出汇总，按总耗时降序排列
- `reset()` 清空统计（Scanner 复用时重置）
- 未启用时（`FUSCAN_PERF=0`）零开销（仅一次 bool 检查）

**决策3：Scanner 关键路径分阶段接入**

| 阶段名 | 接入点 | 度量内容 |
|--------|--------|----------|
| `read_bytes` | `_extract_with_cache` | 文件 I/O 读取 |
| `hash` | `_extract_with_cache` | BLAKE2b 哈希计算 |
| `cache_lookup` | `_scan_entry_cached` | mtime 预筛 + 规则结果缓存查询 |
| `cache_lookup_extract` | `_extract_with_cache` | 提取内容缓存查询 |
| `cache_lookup_hits` | `_scan_entry_cached` | 常规路径规则结果缓存查询 |
| `extract` | `_extract_with_cache` | 内容提取（docx/pptx 热点） |
| `cache_put_extract` | `_extract_with_cache` | 提取内容缓存写入 |
| `match` | `_scan_entry_cached` / `_scan_entry_uncached` | 规则匹配 |
| `cache_write` | `_flush_batch_locked` | SQLite 批量写入 |

### 使用方式

```powershell
# 启用性能测量（PowerShell）
$env:FUSCAN_PERF=1
uv run python -m fuscan

# 或 CLI
$env:FUSCAN_PERF=1
uv run fuscan scan <path>
```

扫描结束后查看 `fuscan.perf` logger 的 DEBUG 输出，形如：

```
[perf] === 性能汇总 ===
[perf] read_bytes              总计  4500.0ms  调用   500 次  平均    9.00ms  最大   120.0ms
[perf] extract                 总计  2800.0ms  调用   500 次  平均    5.60ms  最大    45.0ms
[perf] match                   总计  1200.0ms  调用  1000 次  平均    1.20ms  最大    15.0ms
[perf] hash                    总计   800.0ms  调用   500 次  平均    1.60ms  最大    30.0ms
[perf] cache_lookup            总计   150.0ms  调用   500 次  平均    0.30ms  最大     2.0ms
[perf] cache_write             总计   120.0ms  调用    10 次  平均   12.00ms  最大    25.0ms
...
```

按总耗时降序排列，一眼定位瓶颈阶段，再决定定向优化方向。

### 零开销保证

`PerfStats.measure` 在 `FUSCAN_PERF` 未启用时：

```python
@contextmanager
def measure(self, name: str) -> Iterator[None]:
    if not _PerfState.enabled:
        yield
        return
    # ... 计时逻辑
```

仅一次 bool 检查 + yield，开销约 1-2 微秒/次。每文件 5-10 个 measure 点，
总开销 < 20μs/文件，按 171 files/s 计约 0.3% 开销，可接受。

## 改动文件清单

### 移动文件

| 原路径 | 新路径 | 说明 |
|--------|--------|------|
| `src/fuscan/gui/perf.py` | `src/fuscan/perf.py` | 提升为公共模块，GUI 与扫描器共用 |

### 修改文件

| 文件 | 说明 |
|------|------|
| `src/fuscan/perf.py` | 模块 docstring 更新；新增 `PerfStats` 聚合统计类（线程安全）+ `_StageStats` 内部类；`__all__` 增加 `PerfStats` |
| `src/fuscan/gui/export_worker.py` | 导入 `from fuscan.gui.perf` → `from fuscan.perf` |
| `src/fuscan/gui/main_window.py` | 导入 `from fuscan.gui.perf` → `from fuscan.perf`；导入顺序调整（ruff I001） |
| `src/fuscan/scanner/scanner.py` | 新增 `from fuscan.perf import PerfStats`；`__init__` 持有 `self._perf`；`scan()` 开头 `reset()` + 结尾 `report(logger)`；`_extract_with_cache` 接入 read_bytes/hash/cache_lookup_extract/extract/cache_put_extract；`_scan_entry_cached` 接入 cache_lookup/cache_lookup_hits/match；`_scan_entry_uncached` 接入 match；`_flush_batch_locked` 接入 cache_write |
| `tests/test_gui_perf.py` | 导入 `from fuscan.gui import perf` → `from fuscan import perf`；logger 名 `fuscan.gui.perf` → `fuscan.perf`；新增 4 个 `PerfStats` 测试（零开销/聚合/线程安全/reset） |
| `benchmarks/gui-baseline.md` | logger 名 `fuscan.gui.perf` → `fuscan.perf`；补充 iter-65 说明 |

## 关键决策与依据

### 决策1：先测量再优化，不盲目实施进程池改造

用户选择"先测量再决定"。ProcessPoolExecutor 改造工作量大（缓存共享、
pickle 开销、进程池预热），且收益依赖瓶颈定位。若测量显示 read_bytes
占比 60%（I/O 瓶颈），则进程池收益有限，应优先 mmap 或异步 I/O；
若 match 占比 50%（CPU 瓶颈），则进程池收益最大。

### 决策2：perf.py 提升而非 scanner 内新建

避免重复代码。`PerfTimer` 与 `PerfStats` 共享 `_PerfState` 开关与
`FUSCAN_PERF` 环境变量。提升后 GUI 与扫描器统一通过 `from fuscan.perf
import ...` 导入，logger 名统一为 `fuscan.perf`。

### 决策3：PerfStats 用 contextmanager 而非 try/finally 内联

`contextmanager` 装饰器开销约 1-2μs/次。对于热路径（每文件 5-10 个
measure 点），总开销 < 20μs/文件，按 171 files/s 计约 0.3%。代码清晰度
优先于微优化。若未来测量显示 contextmanager 开销显著，可改为 try/finally
内联或 `__enter__/__exit__` 类实现。

## 代码实现情况

- `PerfStats` 类：线程安全聚合统计，`measure`/`record`/`report`/`reset`
- Scanner 9 个阶段接入 PerfStats（read_bytes/hash/extract/match/cache_*
  系列）
- `scan()` 末尾 `self._perf.report(logger)` 输出汇总
- 测试：4 个新测试覆盖零开销/聚合/线程安全/reset

## 整合优化情况

- `perf.py` 从 GUI 专属提升为公共模块，消除分层违反
- `PerfStats` 与 `PerfTimer` 共享 `_PerfState` 开关，统一启用入口
- logger 名 `fuscan.gui.perf` → `fuscan.perf`，与模块路径一致

## 测试验证结果

- ruff check：全部通过（修复 I001 导入排序、RUF023 __slots__ 排序、PLR1730 max() 替换）
- ruff format --check：93 files already formatted
- pyrefly check：0 errors（修复 `cached` 变量类型 `dict | None`、移除多余 pyrefly ignore）
- pytest -m "not slow" --cov=fuscan --cov-fail-under=95：1423 passed, 16 deselected, 覆盖率 96.08%
- perf.py 覆盖率：100%（新增 4 个 PerfStats 测试覆盖零开销/聚合/线程安全/reset）

## 遗留事项

- 用户需用 `FUSCAN_PERF=1` 跑一次真实扫描，收集各阶段耗时数据
- 根据数据决定下一迭代定向优化方向：
  - 若 read_bytes 占比高 → mmap 或异步 I/O
  - 若 match 占比高 → ProcessPoolExecutor
  - 若 extract 占比高 → 提取器优化（lxml / 流式）
  - 若 cache_* 占比高 → SQLite 读写分离

## 下一轮计划

- 用户提供 FUSCAN_PERF=1 测量数据后，根据瓶颈定向实施优化方案
- 若数据不足，可扩展 PerfStats 测量更细粒度阶段（如单规则匹配耗时）
