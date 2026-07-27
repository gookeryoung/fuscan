# iter-120 性能基线建立与回归门禁（pytest-benchmark）

## 需求清单

- [x] 评估现有 benchmark 测试与性能门禁现状
- [x] 引入 `pytest-benchmark` 作为 test 可选依赖
- [x] 为 iter-118 三层 LRU 缓存提供 benchmark 数据佐证
- [x] 为 iter-119 提取器重试机制提供 benchmark 数据佐证
- [x] 建立扫描热路径关键函数性能基线
- [x] 全套门禁验证通过（ruff/pyrefly/pytest/coverage）

## 迭代目标

引入 `pytest-benchmark` 为关键热路径建立微基准基线，验证 iter-118 LRU 缓存与
iter-119 重试机制的性能收益，并为后续迭代的回归对比提供可复现的 benchmark 工作流。

## 改动文件清单

修改：
- `pyproject.toml` — `[project.optional-dependencies].test` 新增 `pytest-benchmark>=4.0.0`
- `tests/test_perf_benchmark.py` — 新增性能基线微基准测试文件（14 个测试）

## 关键决策与依据

1. **保留现有端到端吞吐量测试**：`tests/test_benchmark.py` 与
   `tests/test_extractor_benchmark.py` 用 `time.perf_counter` 手动计时，适合
   多文件循环的端到端吞吐量测量（如 ``≥ 50 files/s``）。`pytest-benchmark` 的
   `benchmark` fixture 适合单函数微基准（自动统计中位数/方差/百分位），
   两者分工互补，不替换。

2. **`pytest-benchmark` 版本锁定 4.0.0**：5.x 要求 Python>=3.9，与 fuscan
   `requires-python>=3.8` 冲突。4.0.0 支持 pytest 8.x，功能足够（`benchmark`
   fixture、`--benchmark-save`/`--benchmark-compare` 回归对比）。

3. **不修改 `pytest.ini`**：rule-01 将 `pytest.ini` 列为工具链配置文件，
   修改需暂停确认。`pytest-benchmark` 默认配置（`min_rounds=5`、
   `min_time=0.000005`）已足够；测试内通过 `benchmark.stats.stats.mean` 断言
   提供硬性门禁，不依赖全局 ini 配置。后续如需调整可通过命令行参数
   `--benchmark-min-time` 等传递。

4. **三层测试结构**：
   - `TestLruCacheBenchmark`（iter-118 佐证）：LRU 命中 vs SQLite 查询延迟对比
   - `TestRetryMechanismBenchmark`（iter-119 佐证）：重试零开销 + 退避延迟验证
   - `TestHotPathBenchmark`（热路径基线）：哈希、提取、匹配的单点延迟基线

5. **手动计时与 benchmark fixture 混合**：
   - 单点延迟用 `benchmark` fixture（自动统计、可对比基线）
   - 加速比/零开销等"对比断言"用手动计时（`benchmark` fixture 每测试仅能调用
     一次，无法在单测试内对比两个函数）

6. **阈值策略**：
   - LRU 命中延迟断言 < 10μs（实测 0.3μs，20 倍余量）
   - 重试开销断言 < 20μs（实测 ~12μs，Python 函数调用本身约 1-10μs）
   - 退避延迟断言偏差 < 30ms（Windows `time.sleep` 精度约 15ms，2 次 sleep
     累计 20-30ms 误差正常）
   - LRU 加速比断言 >= 5x（实测 867x，远超阈值）

## 代码实现情况

### 依赖声明（`pyproject.toml`）

```toml
test = [
    "openpyxl>=3.1.0",
    "pytest-asyncio>=0.24.0",
    "pytest-benchmark>=4.0.0",  # 新增
    "pytest-cov>=5.0.0",
    "pytest-html>=4.1.1",
    "pytest-xdist>=3.6.1",
    "pytest>=8.0.0",
]
```

### 测试文件结构（`tests/test_perf_benchmark.py`）

#### TestLruCacheBenchmark（iter-118 佐证）

- `test_extract_cache_lru_hit`：提取内容 LRU 命中延迟（`benchmark` fixture）
- `test_extract_cache_sqlite_query`：SQLite 查询延迟（冷 LRU，`benchmark` fixture）
- `test_path_cache_lru_hit`：路径预筛 LRU 命中延迟（`benchmark` fixture）
- `test_lru_speedup_over_sqlite`：手动计时验证 LRU >= 5x 加速

#### TestRetryMechanismBenchmark（iter-119 佐证）

- `test_retry_success_path`：重试版成功路径延迟（`benchmark` fixture，基线用）
- `test_no_retry_success_path`：原版成功路径延迟（`benchmark` fixture，对比用）
- `test_retry_zero_overhead_on_success`：手动计时验证开销 < 20μs
- `test_retry_failure_backoff_delay`：失败重试退避延迟验证（~50ms × 2 次）

#### TestHotPathBenchmark（扫描热路径基线）

- `test_hash_bytes_4kb`：4KB 文件哈希延迟（SHA-256 路径）
- `test_hash_bytes_100kb`：100KB 文件哈希延迟（BLAKE2b 路径）
- `test_extract_text_4kb`：4KB 纯文本提取延迟（T1 极速）
- `test_extract_docx_typical`：典型 DOCX 提取延迟（T3 中速）
- `test_extract_eml_typical`：典型 EML 提取延迟（T2 快速）
- `test_matcher_contains_apply`：CONTAINS 规则匹配延迟

### 回归门禁工作流

```bash
# 1. 首次运行保存基线（在优化前的 commit 上执行）
uv run pytest -m slow tests/test_perf_benchmark.py --benchmark-save=baseline

# 2. 优化后运行并对比基线（偏差 > 10% 的测试会标记 FAIL）
uv run pytest -m slow tests/test_perf_benchmark.py \
    --benchmark-compare=baseline --benchmark-compare-fail=mean:10%

# 3. 查看已保存的基线列表
uv run pytest --benchmark-list
```

## 整合优化情况

- 与现有 `test_benchmark.py`（端到端吞吐量）分工明确，不重复测量
- 复用 `benchmarks/sample_files.py` 的 `generate_sample_bytes` 生成测试样本
- 复用 `tests/test_extractors.py` 的 `_FlakyExtractor` 测试夹具验证重试退避
- 所有测试标记 `@pytest.mark.slow`，CI 默认跳过，不影响常规测试耗时

## 测试验证结果

### 门禁通过

```
uv run ruff check src tests           → All checks passed
uv run ruff format --check src tests  → 136 files already formatted
uv run pyrefly check                  → 0 errors (680 suppressed)
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95
                                      → 1964 passed, 68 deselected
                                         TOTAL 95.87% (required 95.0%)
```

### Benchmark 实测数据（Windows 11 / i7-14700K / Python 3.8.20）

#### iter-118 LRU 缓存佐证

| 测试 | 中位数 | 说明 |
|------|-------:|------|
| test_extract_cache_lru_hit | 300ns | LRU 命中延迟 |
| test_extract_cache_sqlite_query | 260,500ns (260μs) | SQLite 查询延迟（冷 LRU） |
| test_path_cache_lru_hit | 500ns | 路径预筛 LRU 命中延迟 |
| test_lru_speedup_over_sqlite | 867x 加速 | LRU vs SQLite 加速比 |

**结论**：iter-118 的 `_extract_cache` LRU 缓存使提取内容查询延迟从 260μs 降到
0.3μs，加速 867 倍。三层 LRU（`_hit_cache`/`_path_cache`/`_extract_cache`）
在热缓存场景下完全命中内存，跳过 SQLite 查询。

#### iter-119 重试机制佐证

| 测试 | 中位数 | 说明 |
|------|-------:|------|
| test_retry_success_path | 981,600ns (981μs) | 重试版成功路径延迟 |
| test_no_retry_success_path | 981,950ns (981μs) | 原版成功路径延迟 |
| test_retry_zero_overhead_on_success | ~12μs 开销 | 重试包装额外开销 |
| test_retry_failure_backoff_delay | ~121ms | 2 次 50ms 退避（Windows sleep 精度内） |

**结论**：iter-119 的重试机制在成功路径上零开销（差异在测量噪声内，手动计时
验证开销 < 20μs）。失败重试的退避延迟符合预期（2 × 50ms = 100ms，Windows
sleep 精度导致 +21ms 误差正常）。

#### 扫描热路径基线

| 测试 | 中位数 | 说明 |
|------|-------:|------|
| test_hash_bytes_4kb | 1.3μs | 4KB SHA-256 哈希 |
| test_hash_bytes_100kb | 88μs | 100KB BLAKE2b 哈希 |
| test_extract_text_4kb | 1.05ms | 4KB 纯文本提取（T1 极速） |
| test_extract_eml_typical | 295μs | 典型 EML 提取（T2 快速） |
| test_extract_docx_typical | 11.2ms | 典型 DOCX 提取（T3 中速） |
| test_matcher_contains_apply | 63μs | CONTAINS 规则匹配 |

**结论**：热路径基线符合 SpeedTier 档次声明，纯文本提取 < 2ms（T1），
EML < 1ms（T2），DOCX ~11ms（T3）。后续迭代可通过 `--benchmark-compare`
检测这些基线的回归。

## 遗留事项

- `[tool.pytest-benchmark]` 配置节在 pytest-benchmark 4.0 中不被读取（4.0
  通过 pytest ini 配置，键名以 `benchmark_` 前缀）。当前依赖默认配置 +
  测试内断言，未修改 `pytest.ini`（rule-01 暂停条件）。后续如需全局配置
  `warmup`/`disable_gc`，可在 `pytest.ini` 添加 `benchmark_warmup = true`
  等键（需用户确认）。
- benchmark 基线未持久化到 CI（CI 默认跳过 slow 测试）。开发者需在本地
  手动运行 `--benchmark-save`/`--benchmark-compare` 工作流对比回归。
- `test_retry_on_failure_callback_overhead` 测试因 `_FlakyExtractor` 序列
  消耗模式与 `benchmark` fixture 多次调用不兼容而删除。callback 功能已由
  iter-119 的 23 个测试覆盖，性能通过 `test_retry_zero_overhead_on_success`
  佐证。

## 下一轮计划

iter-121：扫描结果导出格式扩展与性能优化
- 评估现有导出格式（JSON/CSV/HTML）的覆盖率与性能瓶颈
- 探索 SARIF 格式支持（GitHub Code Scanning 集成）
- 大结果集（>10000 条）导出的流式写入优化
- 配套测试覆盖新格式与边界场景
