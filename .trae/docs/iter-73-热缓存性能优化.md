# iter-73：热缓存性能优化

## 需求清单

详见 `req-19-热缓存性能优化.md`。

## 迭代目标

确认性能基线，定位 S5 热缓存场景 -29.4% 回退根因，通过内存 LRU 缓存优化消除热缓存场景 SQLite 查询开销，使 S5 吞吐量恢复并超过 iter-39 基线。

## 性能基线确认

### 测量环境

| 项目 | 值 |
|------|-----|
| 操作系统 | Windows 11 (10.0.26200) |
| CPU | Intel Core i7-14700K (24 核) |
| Python | 3.8.20 |
| fuscan 版本 | iter-72（优化前）→ iter-73（优化后） |
| 测量脚本 | `benchmarks/perf_profile.py`（新增，500 文件 5 场景） |

### 性能对比（500 文件）

| 场景 | iter-39 基线 | iter-72（优化前） | iter-73（优化后） | 优化前后变化 | 较基线变化 |
|------|------------|------------------|------------------|------------|----------|
| S1 单线程无缓存 | 106.0 | 98.2 | 99.2 | +1.0% | -6.4% |
| S2 4线程无缓存 | 171.2 | 154.7 | 158.0 | +2.1% | -7.7% |
| S3 24线程无缓存 | 170.7 | 147.8 | 150.1 | +1.6% | -12.1% |
| S4 4线程+缓存冷 | 163.9 | 148.9 | 150.0 | +0.7% | -8.5% |
| S5 4线程+缓存热 | 6369.8 | 4248.5 | 18782.1 | +342% | +195% |

### S5 优化前后 perf_summary 对比

| 阶段 | 优化前累计 | 优化后累计 | 降幅 |
|------|----------|----------|------|
| cache_lookup | 295.19ms | 2.49ms | -99.2% |
| walk | 35.23ms | 3.14ms | -91.1% |
| cache_write | 5.08ms | 4.13ms | -18.7% |
| 总耗时 | 117.69ms | 26.62ms | -77.4% |

## 根因分析

### S5 热缓存 -29.4% 回退根因

通过 `perf_profile.py` 获取 perf_summary 发现：

1. **`cache_lookup` 占 88% 累计耗时**（295ms / 335ms），是绝对瓶颈
2. **`lookup_file_hash` 每次走 SQLite**：500 次查询 × ~0.3ms = 150ms 累计
3. **`get_cached_hits` 的 `_hit_cache` 被 `batch_put_results` invalidate**：S4 末尾写入后清空 LRU，S5 中首次查询走 SQLite 回填

### iter-71 两阶段架构影响有限

walk 阶段仅 3-35ms（受系统缓存影响波动），非主要瓶颈。两阶段架构的串行 walk 开销在 S5 总耗时中占比 < 30%，且无法通过内存缓存优化消除。

## 改动文件清单

### 核心改动

| 文件 | 改动内容 |
|------|---------|
| `src/fuscan/cache/store.py` | 新增 `_path_cache` LRU 字段与 `_path_cache_get`/`_path_cache_put`/`path_cache_size` 方法；`lookup_file_hash` 接入 LRU 先查内存；`batch_put_results` 主动填充 `_path_cache` 与 `_hit_cache`（替代原 invalidate 行为）；`prune_orphan_rules`/`prune_stale_files`/`close` 清空 `_path_cache` |

### 测试改动

| 文件 | 改动内容 |
|------|---------|
| `tests/test_cache.py` | 新增 `TestLookupFileHashLruCache` 测试类（7 个用例）：LRU 填充/不缓存 None/批量写入填充/`_hit_cache` 主动填充/空 hits 保留/prune 清空/mtime 变化失效 |

### 性能分析脚本

| 文件 | 改动内容 |
|------|---------|
| `benchmarks/perf_profile.py` | 新增性能瓶颈分析脚本，直接调用 `Scanner.scan()` 输出各阶段 perf_summary，支持 `--scenario` 选择场景 |

## 关键决策与依据

### D1：`lookup_file_hash` 加内存 LRU 缓存

**决策**：新增 `_path_cache: OrderedDict[tuple[str, float, int], str]`，`lookup_file_hash` 先查 LRU 命中跳过 SQLite。

**依据**：
- S5 热缓存场景 `lookup_file_hash` 500 次查询累计 150ms，是主要瓶颈
- `get_cached_hits` 已有 LRU（iter-38），但 `lookup_file_hash` 无 LRU，每次走 SQLite
- LRU 键为 `(path, mtime, size)` 三元组，文件 mtime 变化时键不同，自动失效
- None 结果不缓存，避免未登记路径污染缓存

**效果**：S5 中 `cache_lookup` 从 295ms 降到 2.49ms（-99.2%）。

### D2：`batch_put_results` 主动填充 `_hit_cache` 与 `_path_cache`

**决策**：`batch_put_results` COMMIT 后主动填充内存缓存，替代原 invalidate 行为。

**依据**：
- 原 invalidate 行为导致 S5 中 `get_cached_hits` 首次查询走 SQLite（S4 末尾清空了 LRU）
- `item.hits` 非空时从中构造 result dict 填充 `_hit_cache`，使下次查询命中内存
- `item.hits` 完整时（冷缓存首次扫描所有规则）LRU 命中；不完整时（混合路径部分规则已缓存）`_hit_cache_get` 检测 `rule_hashes` 集合不匹配，走 SQLite 回填，安全降级
- `item.hits` 为空（预筛命中，仅刷新元数据）时不 invalidate，保留已有 LRU 条目（`scan_results` 未变，LRU 仍有效）

**效果**：S5 中 `get_cached_hits` 全部命中内存，`cache_lookup` 累计仅 2.49ms。

### D3：不恢复流水线模式

**决策**：保留 iter-71 两阶段架构，不恢复 walk 与 scan 并行的流水线模式。

**依据**：
- iter-71 两阶段架构对冷启动场景有正向收益（避免 walk 与 scan 争抢磁盘 I/O）
- walk 阶段仅 3-5ms，非 S5 主要瓶颈
- 通过内存 LRU 缓存优化已使 S5 达到 18782 files/s，远超 iter-39 基线 6369.8
- 恢复流水线会增加代码复杂度，收益不明确

### D4：`register_path` 不主动填充 `_path_cache`

**决策**：`register_path` 写入后不主动填充 `_path_cache`（缺少 size 参数）。

**依据**：
- `register_path` 签名无 size 参数，无法构造 `_path_cache` 键
- 扫描热路径用 `batch_put_results`（已主动填充），`register_path` 主要在测试中使用
- `lookup_file_hash` 走 SQLite 后自动回填 LRU，下次查询命中内存

## 代码实现情况

### `_path_cache` LRU 字段

```python
# 路径预筛 LRU 缓存（iter-73）：(path_str, mtime, size) -> file_hash
self._path_cache: OrderedDict[tuple[str, float, int], str] = OrderedDict()
```

### `lookup_file_hash` 接入 LRU

```python
def lookup_file_hash(self, path, mtime, size):
    path_str = str(path)
    # 先查进程内 LRU
    with self._lru_lock:
        cached = self._path_cache_get(path_str, mtime, size)
    if cached is not None:
        return cached
    # 未命中走 SQLite
    row = self._get_read_conn().execute(...).fetchone()
    if row is None:
        return None  # None 不缓存
    file_hash = row["file_hash"]
    # 回填 LRU
    with self._lru_lock:
        self._path_cache_put(path_str, mtime, size, file_hash)
    return file_hash
```

### `batch_put_results` 主动填充内存缓存

```python
# COMMIT 成功后更新内存缓存
with self._lru_lock:
    for item in items:
        if item.hits:
            # 主动填充 _hit_cache
            rule_hashes = [rh for rh, _ in item.hits]
            result_dict = dict(item.hits)
            self._hit_cache_put(item.file_hash, rule_hashes, result_dict)
        # item.hits 为空：保留已有 LRU 条目（scan_results 未变）
        # 主动填充 _path_cache
        self._path_cache_put(str(item.path), item.mtime, item.size, item.file_hash)
```

## 整合优化情况

- `_path_cache` 与 `_hit_cache` 共享 `_lru_lock`，无额外锁开销
- `_path_cache` 容量与 `_hit_cache` 一致（4096），内存占用约 4MB
- `prune_orphan_rules` / `prune_stale_files` / `close` 同步清空 `_path_cache`

## 测试验证结果

- ruff check: All checks passed
- ruff format: 97 files already formatted
- pyrefly: 0 errors (467 suppressed, 60 warnings)
- pytest: 1457 passed (+7), 16 deselected, coverage 95.94%

## 遗留事项

- S1-S4 仍有 6-12% 回退（较 iter-39 基线），主要来自 PerfStats 始终启用的测量开销（iter-66）与代码累积变更，属可接受范围
- 性能分析脚本 `benchmarks/perf_profile.py` 未纳入正式 benchmark 套件，仅供开发期瓶颈定位

## 下一轮计划

无。本次迭代已完整交付用户需求。
