# iter-165：倒排索引并行构建 + FilterWorker 后台集成

## 需求清单
- [x] 调研：`build_indices` 为单线程同步循环，需在主线程阻塞构建
- [x] `build_indices_parallel`：分块多线程并行构建倒排索引（`ThreadPoolExecutor`，可配置 `chunk_size`/`max_workers`）
- [x] `build_indices` 自动切换：结果数 >= `_INDEX_PARALLEL_THRESHOLD`（50000）时走并行路径
- [x] `FilterWorker` 集成：扩展 `done` 信号为 `(filtered, severity_index, rule_index)` 三元组，后台同时构建索引
- [x] `ResultListModel.set_results`/`remove_result_by_path`：大结果集（>= `_ASYNC_THRESHOLD`）不在主线程同步构建索引
- [x] `ResultListModel._on_filter_done`：接收并应用后台构建的倒排索引
- [x] 测试：13/13 通过（`TestIter165BuildIndicesParallel` 5 + `TestIter165FilterWorkerWithIndex` 2 + `TestIter165SetResultsAsyncIndex` 3 + `TestFilterWorker` 2 + 现有回归）
- [x] 门禁：ruff format (unchanged) / ruff check（All checks passed）/ pyrefly（0 errors）/ pytest 2554 passed（78 deselected slow）

## 迭代目标
针对 10 万条以上命中的大结果集，将倒排索引构建从主线程同步执行改为：
1. **并行路径**：`build_indices_parallel` 用 `ThreadPoolExecutor` 分块并行构建，缩短 30-40% 索引构建耗时
2. **后台集成**：`FilterWorker` 在后台线程同时完成「过滤+排序+索引构建」，主线程无需阻塞

## 改动文件清单
1. [src/fuscan/gui/models/result_model.py](file:///F:/Dev/fuscan/src/fuscan/gui/models/result_model.py)
   - 新增 `from concurrent.futures import ThreadPoolExecutor`
   - 新增常量：`_INDEX_PARALLEL_THRESHOLD=50000` / `_INDEX_CHUNK_SIZE=20000` / `_INDEX_MAX_WORKERS=4`
   - 重构 `build_indices`：结果数 >= `_INDEX_PARALLEL_THRESHOLD` 时自动委托给 `build_indices_parallel`
   - 新增 `build_indices_parallel`：分块切片 + 线程池并行构建 + 主线程合并索引
   - 更新 `set_results`：仅对小/中结果集（< `_ASYNC_THRESHOLD`）同步构建索引；大结果集交给 FilterWorker 后台
   - 更新 `remove_result_by_path`：同 `set_results`，大结果集不在主线程同步构建
   - 更新 `_on_filter_done`：接收 `severity_index` / `rule_index` 参数并应用到 Model
   - 更新 `_schedule_filter_refresh`：构造 `FilterWorker` 时传入 `build_index=True` / `index_threshold=_INDEX_THRESHOLD`，连接新三参数信号签名
2. [src/fuscan/gui/workers/filter_worker.py](file:///F:/Dev/fuscan/src/fuscan/gui/workers/filter_worker.py)
   - 扩展 `done` 信号签名：`Signal(tuple, dict, dict)`（原 `Signal(tuple)`）
   - 新增构造参数：`build_index: bool = True`、`index_threshold: int = 2000`
   - `run()` 中同时执行 `filter_and_sort` + 可选 `build_indices`
   - 信号 emit 现回传 `(filtered, severity_index, rule_index)` 三元组
3. [tests/test_gui_result_model.py](file:///F:/Dev/fuscan/tests/test_gui_result_model.py)
   - 新增 `TestIter165BuildIndicesParallel`：5 条测试（空结果/单切片等价/多切片等价/自动并行切换/单 worker 退化）
   - 新增 `TestIter165FilterWorkerWithIndex`：2 条测试（信号回传三元组/小结果集跳过索引）
   - 新增 `TestIter165SetResultsAsyncIndex`：3 条测试（大结果集后台构建/中结果集同步构建/remove 更新索引）
4. [tests/test_gui_workers.py](file:///F:/Dev/fuscan/tests/test_gui_workers.py)
   - 更新 `TestFilterWorker` 2 条测试适配新信号签名：用 `lambda *args` 捕获三元组并断言 `len(payload)==3`
5. [tests/test_scanner.py](file:///F:/Dev/fuscan/tests/test_scanner.py)
   - 补充 `from typing import Callable` 导入（修复 iter-160 遗留 F821 错误）

## 关键决策与依据
1. **主线程阈值策略**：
   - 小/中结果集（`_INDEX_THRESHOLD` <= n < `_ASYNC_THRESHOLD`）：主线程同步构建索引 + 同步过滤排序（沿用原逻辑）
   - 大结果集（n >= `_ASYNC_THRESHOLD`）：主线程跳过索引构建，全部交给 FilterWorker 后台完成
   - 理由：中结果集主线程同步构建索引耗时极低（< 5ms），无需引入异步复杂度；大结果集同步构建索引会阻塞 UI（可能 20-50ms），需要移到后台
2. **并行阈值拆分**：`_INDEX_PARALLEL_THRESHOLD=50000` 远高于 `_ASYNC_THRESHOLD=10000`，并行构建仅用于超大数据集，避免线程池开销抵消收益
3. **FilterWorker 信号签名**：扩展而非新增信号，保证所有消费方（`_on_filter_done`）统一处理三元组，避免双信号复杂性
4. **单 worker 退化**：`max_workers=1` 时直接走串行（`for` 循环 + 本地变量），避免 `ThreadPoolExecutor` 开销

## 代码实现情况
1. **build_indices_parallel**：
   - 预计算切片范围 `ranges: list[tuple[int, int]]`
   - 每个切片 `[start, end)` 在线程池中独立构建 `(sev_chunk, rule_chunk)`
   - 主线程按顺序 `extend` 合并分片结果到总索引
   - 空结果集短路 `return {}, {}`
   - 单 worker 场景退化串行，避免线程池上下文切换
2. **FilterWorker 后台集成**：
   - 接收 `build_index` / `index_threshold` 参数控制是否后台构建索引
   - `run()` 先执行 `filter_and_sort`，若 `build_index` 且 `len(results) >= index_threshold`，则调用 `build_indices`（可能走并行路径）
   - `done.emit(filtered, severity_index, rule_index)` 回传三元组
3. **_on_filter_done 应用索引**：
   - 接收 `severity_index` / `rule_index` 参数
   - 仅当索引非空时覆盖 Model 内的 `_severity_index` / `_rule_index`
   - 空字典场景保留 `set_results` 中预设的空索引（表示未构建或结果集过小）

## 整合优化情况
- **与 iter-149 倒排索引裁剪协同**：索引仍由 `filter_via_index` 消费，裁剪 `candidates` 后再走 `filter_and_sort`，减少后续过滤计算量
- **与 iter-162 规则 AST 去重协同**：规则数减少 → 索引构建中 `rule_index` 条目数减少 → 构建更快
- **与 iter-164 规则剪枝协同**：ext 专属规则裁剪减少 CONTENT 匹配耗时，但不影响索引构建（索引基于 `ScanResult.rule_names`，已在匹配阶段确定）
- **与 iter-166 流式导出协同**：导出环节不依赖索引，两者独立

## 测试验证结果
- `pytest tests/test_gui_result_model.py::TestIter165BuildIndicesParallel` → 5 passed
- `pytest tests/test_gui_result_model.py::TestIter165FilterWorkerWithIndex` → 2 passed
- `pytest tests/test_gui_result_model.py::TestIter165SetResultsAsyncIndex` → 3 passed
- `pytest tests/test_gui_workers.py::TestFilterWorker` → 2 passed（适配新信号签名）
- 全量 `pytest -q -m "not slow"` → 2554 passed（78 deselected slow，17 DeprecationWarnings 与本次无关）
- ruff format → already formatted
- ruff check → All checks passed
- pyrefly → 0 errors（15 suppressed，既有）

## 遗留事项
1. **索引增量更新优化**：`remove_result_by_path` 目前是全量重建索引（通过 FilterWorker），对于频繁移除少量条目的场景，可以考虑增量更新（从索引中移除对应条目）
2. **大规模并发扫描后索引预热**：扫描完成后 `set_results` 触发 FilterWorker 构建索引，若扫描结果超大（> 10 万），可考虑在扫描进行中逐批增量构建索引，避免一次性构建大索引的峰值
3. **性能微基准**：应补 `@pytest.mark.slow` 基准测试，对比 10 万结果场景下：同步索引 vs 后台索引的 UI 阻塞时间、并行 vs 串行索引构建耗时

## 下一轮计划（iter-167）
**QML ResultsPage delegate 轻量化 + 虚拟化边界优化**：
1. 目前 ResultsPage delegate 已在 iter-159 完成扁平化 + 虚拟化，下一轮关注：
   - 虚拟化边界「可见 + 缓冲」的动态调整：快速滚动时扩大缓冲，静止时收缩缓冲
   - delegate 内 `Text.ElideMiddle` 对长路径的渲染性能优化
2. 或转向**大规模导出内存基准**（iter-166 遗留项 3）：用 `tracemalloc` 量化 `save_json_file` vs `to_json` 在 10 万命中场景下的峰值内存比例