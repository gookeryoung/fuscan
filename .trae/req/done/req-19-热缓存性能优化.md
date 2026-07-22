# req-19：热缓存性能优化

## 需求

- [x] 确认 iter-73 性能基线，对比 iter-39 基线分析性能回退
- [x] 定位 S5 热缓存场景 -29.4% 回退根因
- [x] 优化 `lookup_file_hash` 加内存 LRU 缓存，消除热缓存场景 SQLite 查询开销
- [x] 优化 `batch_put_results` 主动填充 `_hit_cache` 与 `_path_cache`，使热缓存二次扫描 100% 命中内存
- [x] S5 热缓存吞吐量恢复并超过 iter-39 基线
- [x] 全门禁通过（ruff/pyrefly/pytest/coverage）

## 背景

iter-72 性能基线确认发现 S5 热缓存场景较 iter-39 基线回退 29.4%（6369.8 → 4248.5 files/s），
需定位根因并优化。其他场景（S1-S4）小幅回退 3-9%，属累积变更与 PerfStats 开销范围内。
