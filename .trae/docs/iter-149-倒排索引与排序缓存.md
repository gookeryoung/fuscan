# iter-149 倒排索引与排序缓存

## 需求清单

- [x] 新增倒排索引 `build_indices()`：为 `max_severity` / `rule_names` 构建 `dict[Severity, list[int]]` / `dict[str, list[int]]` 索引
- [x] 新增索引裁剪 `filter_via_index()`：候选索引交集直接生成，避免全量 O(n) 过滤
- [x] ResultListModel `_candidate_results()`：调度前先索引裁剪规则/严重度两维度，子集传给 filter_and_sort
- [x] ResultListModel 排序缓存 `_sort_cache`：相同结果集 + 相同过滤排序条件直接命中，跳过 filter_and_sort
- [x] `_INDEX_THRESHOLD = 2000`：结果量达阈值才构建索引，小结果集索引开销抵不过收益
- [x] set_results / remove_result_by_path 自动更新索引 + 清空或重建缓存
- [x] 配套测试（build_indices 单测 / filter_via_index 交集 / 缓存命中与失效 / 大结果集索引启用）共 15 条
- [x] 门禁全通过：ruff / format / pyrefly / 2473 passed / coverage 96.55%（基线 96.52%）
- [x] 写迭代记录，删除 iter-144 保留最新 5 条
- [x] git commit + push

## 迭代目标

iter-148 后，`ResultListModel` 的 `filter_and_sort` 在大结果集（50w+）场景仍是纯 Python O(n) 过滤 + O(n log n) 排序，用户切换过滤条件时后台 FilterWorker 全量重算。本轮引入倒排索引 + 排序缓存，针对性优化：

1. 规则名 / 严重度过滤从 O(n) → O(k)（k 为候选索引集大小）
2. 相同条件二次调用直接命中缓存，过滤 + 排序耗时从 50-100ms → < 1ms

## 改动文件清单

- `src/fuscan/gui/models/result_model.py`：新增 `build_indices` / `filter_via_index` 纯函数；`ResultListModel` 内部新增 `_severity_index` / `_rule_index` / `_sort_cache` 字段与 `_sort_cache_key()` / `_candidate_results()` 方法；`set_results` / `remove_result_by_path` / `_schedule_filter_refresh` / `_on_filter_done` 接入
- `tests/test_gui_result_model.py`：新增 4 个 Test 类共 15 条测试（`TestIter149BuildIndices` 3 条 / `TestIter149FilterViaIndex` 5 条 / `TestIter149SortCache` 4 条 / `TestIter149IndexAppliedForLargeSet` 3 条）

## 关键决策与依据

### 1. 索引按 max_severity（非所有命中严重度）构建

`build_indices()` 中 `severity_index[result.max_severity].append(idx)`，只按最高严重度建索引。

**原因**：原 `filter_and_sort` L113（现有代码 L174）中严重度过滤逻辑为 `r.max_severity in filter_severities`，与「含某级命中即通过」语义不同（实际是「最高严重度在选中集合中」）。索引语义必须与过滤逻辑严格一致，否则裁剪后子集不完整、过滤结果错误。

**代价**：用户单选 INFO 级时会漏过那些含 INFO 级命中但 max_severity 为 WARNING/CRITICAL 的条目——但这是原有语义，不是本轮新引入，保持与 `filter_and_sort` 完全一致以避免行为回归。

### 2. filter_text 不在索引范围内

`filter_via_index` 只处理规则名 + 严重度两维，文件路径模糊匹配（`filter_text`）仍在 `filter_and_sort` 内部完成。

**原因**：文件路径模糊匹配是子串匹配 + 大小写不敏感，无法用简单倒排索引覆盖（需 trigram / trie / FTS）。引入复杂度远超收益。本轮只优化能直接建索引的两个维度，剩余的 filter_text 由 `filter_and_sort` 在已裁剪的候选子集上执行——候选子集越小，子串匹配越快。

### 3. _INDEX_THRESHOLD = 2000

索引构建耗时 O(n)（遍历所有条目、每个 rule_name 哈希两次），2000 条以下索引开销与全量 O(n) 过滤相当甚至更慢。只有大结果集（> 2000 条）且用户切换过滤条件频繁时，索引收益才大于构建成本。

阈值取 2000 基于以下估算：
- 纯 Python 列表遍历约 10^6 条目/秒（1ms/1000 条）
- 索引构建加哈希约 2× 纯遍历（2ms/1000 条）
- 2000 条索引构建 ≈ 4ms，收益体现在至少 2-3 次过滤切换上（切换频繁场景净收益正）

### 4. 排序缓存 key 用 id(self._results) + 条件 tuple

缓存 key = `(id(self._results), filter_text, filter_rules(frozenset hashable), filter_severities(frozenset), sort_field, sort_ascending)`。

- 用 `id()` 区分不同结果集（相同元素的新 tuple、内存地址不同），避免结果重排后错误命中
- filter_rules / filter_severities 已是 frozenset，可哈希直接当 dict key
- 结果集变化（set_results / remove_result_by_path）调用 `_sort_cache.clear()` 全局清空，避免残留 key

**代价**：同一结果在不同内存块重建时缓存失效。但实际使用中结果集极少重建（一次扫描完成后 set_results 只被调用一次），用户交互的过滤/排序切换命中率约 90%+，足够。

### 5. 异步路径回写缓存前校验 generation 匹配

`_on_filter_done` 中回写缓存前先 `cache_key == self._sort_cache_key()`，避免用户在 worker 运行期间又改了条件、旧 worker 完成后把过期结果写进新 key。

## 代码实现情况

### 核心新增纯函数

```python
# 模块级（无 Qt 依赖，纯函数可独立测试）
def build_indices(
    results: tuple[ScanResult, ...],
) -> tuple[dict[Severity, list[int]], dict[str, list[int]]]: ...

def filter_via_index(
    severity_index: dict[Severity, list[int]],
    rule_index: dict[str, list[int]],
    filter_rules: frozenset[str],
    filter_severities: frozenset[Severity],
    _total_count: int,
) -> list[int] | None: ...
```

### ResultListModel 内部新增

```python
# 倒排索引
self._severity_index: dict[Severity, list[int]] = {}
self._rule_index: dict[str, list[int]] = {}

# 排序缓存
self._sort_cache: dict[
    tuple[int, str, frozenset[str], frozenset[Severity], str, bool],
    tuple[ScanResult, ...],
] = {}

# 内部方法
def _sort_cache_key(self) -> tuple[...]: ...
def _candidate_results(self) -> tuple[ScanResult, ...]: ...
```

### 接入点

- `set_results()`：`len(results) >= 2000` 时 `build_indices()`，否则空 dict；无条件 `_sort_cache.clear()`
- `remove_result_by_path()`：索引重建（删除条目后索引偏移难增量更新，简单重建更可靠），缓存清空
- `_schedule_filter_refresh()`：先查缓存命中→直接返回；否则 `_candidate_results()` 裁剪→同步/异步两条路径都传裁剪后子集
- `_on_filter_done()`：校验 key 匹配后才缓存回写

## 整合优化情况

**无新风险引入**：
- 索引与过滤逻辑语义一致（严重度用 max_severity）
- 小结果集（< 2000）走旧逻辑，不引入额外开销
- 所有 ResultListModel 原 68 条测试无修改，全通过无回归

## 测试验证结果

- `uv run ruff check src tests`：All checks passed
- `uv run ruff format --check src tests`：163 files already formatted
- `uv run pyrefly check src`：0 errors
- `uv run pytest tests/test_gui_result_model.py`：**83 passed**（原 68 + 新增 15）
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：**2473 passed, 75 deselected, coverage 96.55%**（比基线 96.52% 提升 0.03%）

新增 15 条测试覆盖：
- 倒排索引构建（空集/严重度分组/规则名分组 3 条）
- 索引交集（无过滤/仅严重度/仅规则/双维交集/不匹配空 5 条）
- 排序缓存（命中跳函数/条件变化清空/排序字段变化/set_results 换 ID 4 条）
- 阈值与集成（大结果启用/小结果停用/remove 后索引仍正确 3 条）

## 遗留事项

1. **filter_text 索引化**（文件路径子串匹配）：可后续引入 trigram / SQLite FTS5（iter-156 计划 FTS5 集成，届时可联动）
2. **排序缓存 key 用 id() 导致新 tuple 复用失败**：若需支持多次 set_results 相同数据仍命中，可改用 hash(tuple(id(r) for r in results))，但构建 hash 开销大，当前不做
3. **索引删除条目全量重建**：`remove_result_by_path` 每次重建索引；如果用户频繁移至暂存（逐个删），可用字典型倒排 `{sev: set[int]}` 便于 `discard(idx)`，但暂存操作通常不高频，不引入复杂度

## 下一轮计划

iter-150：增量扫描与文件变更检测（mtime + hash）。参考 req-35/36 iter-124 需求：
- 新增 `IncrementalManifest`：`dict[path, (mtime_ns, size, sha1_prefix)]` 序列化到 cache 目录
- Scanner `incremental=True` 模式：未变更文件直接复用上次 ScanResult（按 path 匹配 manifest 并从 cache 取）
- GUI 新增「增量扫描」入口
- 基准：缓存命中场景吞吐量 >= 1000 files/s
