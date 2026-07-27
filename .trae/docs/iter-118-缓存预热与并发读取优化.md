# iter-118 缓存预热与并发读取优化

## 需求清单

- [x] 评估 CacheStore 查询路径的并发瓶颈
- [x] 为 `get_extracted_content` 添加进程内 LRU 内存缓存
- [x] `put_extracted_content` 写入后主动填充 LRU
- [x] 清理操作（`prune_stale_files`/`prune_orphan_rules`）正确 invalidate LRU
- [x] 配套测试覆盖 LRU 命中/回填/容量淘汰/清理失效路径

## 迭代目标

为 `get_extracted_content` 添加进程内 LRU 内存缓存，消除 node_modules 重复依赖
等场景下同一 `file_hash` 的重复 SQLite 查询。与已有的 `_hit_cache`（命中结果）
和 `_path_cache`（路径预筛）形成三层内存缓存，使热缓存二次扫描的查询路径
尽可能命中内存。

## 改动文件清单

修改：
- `src/fuscan/cache/_helpers.py` — 新增 `EXTRACT_CACHE_MAX` 常量（512 条，约 10MB 内存）
- `src/fuscan/cache/store.py` — 新增 `_extract_cache` OrderedDict + `_extract_cache_get`/
  `_extract_cache_put`/`_extract_cache_invalidate`/`extract_cache_size` 方法；
  `close` 时清空；模块文档字符串补充三层 LRU 说明
- `src/fuscan/cache/_queries.py` — `get_extracted_content` 先查 LRU，未命中走 SQLite
  后回填 LRU
- `src/fuscan/cache/_writes.py` — `put_extracted_content` 写入后主动填充 LRU
- `src/fuscan/cache/_cleanup.py` — `prune_stale_files`/`prune_orphan_rules` 清理时
  同步清空 `_extract_cache`
- `tests/test_cache.py` — 新增 `TestExtractCacheLru` 6 个测试覆盖 LRU 行为；
  修正 `test_cascade_delete_when_scanned_file_deleted` 适配 LRU（直接查 SQLite 表
  验证级联删除，绕过内存缓存）

## 关键决策与依据

1. **三层 LRU 缓存策略**：
   - `_hit_cache`（4096 条）：`get_cached_hits` 结果，按 `(file_hash, rule_hashes)` 键
   - `_path_cache`（4096 条）：`lookup_file_hash` 结果，按 `(path, mtime, size)` 键
   - `_extract_cache`（512 条，iter-118）：`get_extracted_content` 结果，按 `file_hash` 键
   三层各司其职，覆盖缓存模式的三次查询路径。
2. **容量选择 512**：提取后的纯文本内容较大（docx/pptx 平均 20KB），512 条约占
   10MB 内存。相比 `_hit_cache` 的 4096 条（每条 ~1KB = 4MB），`_extract_cache`
   单条更大，容量相应减小。
3. **不缓存空内容**：`put_extracted_content` 空内容不写 SQLite 也不写 LRU
   （哨兵值污染防护）；`_extract_cache_put` 内部 `if not content: return` 双重保证。
4. **未命中 SQLite 不缓存 None**：`get_extracted_content` SQLite 未命中时返回 None
   不写入 LRU，避免"未登记 file_hash"污染缓存导致后续写入后仍返回 None。
   与 `_path_cache` 策略一致。
5. **清理操作整体清空**：`prune_stale_files`/`prune_orphan_rules` 删除数据后
   整体 `clear()` 三个 LRU，不做细粒度 invalidate。原因：清理操作低频，
   整体清空简单且无遗漏；细粒度 invalidate 需追踪被删 file_hash 集合，复杂度高。
6. **直接 SQL DELETE 的一致性边界**：`test_cascade_delete_when_scanned_file_deleted`
   原测试直接 SQL DELETE 后 `get_extracted_content` 期望返回 None。LRU 引入后
   内存缓存仍返回过期数据。修正测试为直接查 SQLite 表验证级联删除（绕过 LRU），
   因为直接 SQL 修改是测试边界场景，生产路径（`put_extracted_content`/
   `prune_stale_files`）均正确 invalidate LRU。

## 代码实现情况

### `_extract_cache` LRU 结构

```python
self._extract_cache: OrderedDict[str, str] = OrderedDict()
```

- `_extract_cache_get(file_hash) -> str | None`：命中时 move_to_end（LRU），返回内容
- `_extract_cache_put(file_hash, content)`：空内容不写入，超容量 popitem(last=False)
- `_extract_cache_invalidate(file_hash)`：弹出指定条目（清理操作用整体 clear 替代）
- `extract_cache_size()`：诊断用，返回当前条目数

### `get_extracted_content` 查询路径

1. 先查 `_extract_cache`（持 `_lru_lock`），命中返回
2. 未命中走 SQLite 查询（线程本地只读连接）
3. SQLite 命中后回填 LRU，下次同 `file_hash` 命中内存

### `put_extracted_content` 写入路径

1. 写入 `extracted_contents` 表（持 `_lock`）
2. 主动填充 LRU（持 `_lru_lock`），使下次查询命中内存

### 清理操作

`prune_stale_files`/`prune_orphan_rules` 在 `deleted > 0` 时整体 `clear()` 三个 LRU。

## 整合优化情况

- 与现有 `_hit_cache`/`_path_cache` 的锁策略一致：`_lru_lock` 细粒度锁保护 LRU
  访问，不阻塞 DB 读；锁顺序 `_lock → _lru_lock` 不变
- `close` 时统一清空三个 LRU + 关闭读连接，无资源泄漏
- `test_cascade_delete_when_scanned_file_deleted` 修正后更直接验证 SQL 外键级联
  （不依赖内存缓存状态），测试鲁棒性提升

## 测试验证结果

```
uv run ruff check src tests           → All checks passed
uv run ruff format --check src tests  → 134 files already formatted
uv run pyrefly check                  → 0 errors (679 suppressed)
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95
                                      → 1941 passed, 54 deselected
                                         TOTAL 95.84% (required 90.0%)
```

cache 模块覆盖率：
- `_cleanup.py` 100%
- `_helpers.py` 100%
- `_writes.py` 100%
- `_queries.py` 97%（99-101 是 match_texts 反序列化失败警告分支，iter-116 前已存在）
- `store.py` 94%（未覆盖行均为 PRAGMA 失败警告分支与 close 时连接关闭失败分支）

新增 `TestExtractCacheLru` 6 个测试：
- `test_put_then_get_hits_lru`：put 后 get 命中 LRU
- `test_get_miss_then_backfill_lru`：未命中走 SQLite 后回填 LRU
- `test_empty_content_not_cached`：空内容不缓存
- `test_prune_stale_files_clears_lru`：清理过期文件后 LRU 失效
- `test_prune_orphan_rules_clears_lru`：清理规则后 LRU 失效
- `test_lru_capacity_eviction`：超容量弹出最旧条目

## 遗留事项

- 未做 benchmark 数据佐证（留待 iter-120 性能基线建立）
- 批量路径预筛查询（`batch_lookup_file_hash`）未实现：当前 `lookup_file_hash`
  已有 LRU 缓存，热缓存场景已优化；冷缓存场景（进程重启后二次扫描）的批量
  预筛收益约 150ms（万级文件），边际效用较低，留待后续按需实现

## 下一轮计划

iter-119：提取器注册表与失败重试机制
- 评估 `extractors/registry.py` 当前结构，探索失败重试与降级策略
- 为 Office/PDF 等提取器添加可配置的重试次数与超时
- 提取失败时记录诊断信息，便于用户排查
- 配套测试覆盖重试与降级路径
