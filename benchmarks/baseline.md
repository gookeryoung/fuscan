# fuscan 性能基线

> 测量时间：2026-07-15（性能优化策略执行 iter-39 后）
> 测量方式：`uv run pytest -m slow tests/test_benchmark.py` + `uv run python benchmarks/bench_scan.py`

## 测量环境

| 项目 | 值 |
|------|-----|
| 操作系统 | Windows 11 (10.0.26200) |
| CPU | Intel Core i7-14700K (24 核) |
| Python | 3.8.20 |
| fuscan 版本 | iter-39 (commit pending) |

## 提取器单格式速度

测量方式：`generate_sample_bytes(ext, size_hint=4096)` 生成 4KB 内容，
预热后取 20 次平均值。`extract_content_from_bytes(data, ext)` 接口。

| 格式 | 文件大小 (bytes) | 平均耗时 (ms) | 测试阈值 (ms) |
|------|-----------------:|--------------:|--------------:|
| eml  | 4,279 | 0.32 | 15.0 |
| json | 4,728 | 0.60 | 15.0 |
| rtf  | 4,534 | 0.82 | 50.0 |
| csv  | 4,663 | 0.95 | 15.0 |
| txt  | 4,189 | 0.96 | 15.0 |
| yaml | 4,868 | 0.96 | 15.0 |
| html | 4,879 | 1.00 | 15.0 |
| xlsx | 5,455 | 2.34 | 30.0 |
| xml  | 6,408 | 2.40 | 15.0 |
| md   | 4,397 | 2.50 | 15.0 |
| pptx | 43,914 | 5.78 | 50.0 |
| docx | 36,676 | 7.94 | 30.0 |

**观察**：
- 纯文本格式（txt/csv/yaml/json/eml/rtf）均 < 1ms，charset 检测 + 解码为主要开销
- XML/MD 因生成时构造结构化内容，文件略大但仍 < 3ms
- DOCX/PPTX 因 ZIP 解压 + XML 解析，耗时显著高于纯文本（5-8ms）
- XLSX 因 openpyxl 生成的工作表较简单，提取反而快于 docx/pptx

## 混合格式扫描吞吐量

测量方式：`generate_files(root, 500, seed=42)` 生成 500 个混合格式文件
（纯文本 7 种 + 二进制 5 种），总大小约 2.5MB。规则集含 2 个 CONTENT 规则。

| 场景 | 耗时 (s) | 文件/秒 | MB/秒 | 缓存命中率 |
|------|--------:|--------:|------:|----------:|
| S1 单线程无缓存 | 4.72 | 106.0 | 2.6 | - |
| S2 4 线程无缓存 | 2.92 | 171.2 | 4.3 | - |
| S3 24 线程无缓存 | 2.93 | 170.7 | 4.2 | - |
| S4 4 线程+冷缓存 | 3.05 | 163.9 | - | 0% |
| S5 4 线程+热缓存 | 0.08 | 6369.8 | - | 100% |

**观察**：
- S1 单线程无缓存较 iter-38（85.4 → 106.0 files/s）提升约 24%，主因 P2 批量写入
  将逐条 commit 改为 50 文件单事务，消除约 99% 的 fsync 开销，对单线程收益最大
- S5 热缓存较 iter-38（6006.4 → 6369.8 files/s）小幅提升，P4 提取内容缓存跳过
  重复提取的开销在热路径占比已极小（BLAKE2b + 内存查询为主），收益在测量噪声内
- S2/S3/S4 与 iter-38 基本持平，P1 大小文件分流哈希对 4-6KB 小文件无显著收益
  （BLAKE2b 在小文件上与 SHA-256 差异在测量噪声内），P3 archive 并行化在基准
  测试无压缩包场景下不触发
- 多线程场景 4 线程已接近最优并发度，24 线程无额外收益

## 性能优化记录

### iter-73 热缓存 LRU 缓存优化

新增 `_path_cache` 内存 LRU 缓存与 `batch_put_results` 主动填充 `_hit_cache`，S5 热缓存吞吐量提升 342%：

| 场景 | iter-39 基线 | iter-72（优化前） | iter-73（优化后） |
|------|----------:|----------------:|----------------:|
| S1 单线程无缓存 | 106.0 | 98.2 | 99.2 |
| S2 4线程无缓存 | 171.2 | 154.7 | 158.0 |
| S3 24线程无缓存 | 170.7 | 147.8 | 150.1 |
| S4 4线程+缓存冷 | 163.9 | 148.9 | 150.0 |
| S5 4线程+缓存热 | 6369.8 | 4248.5 | 18782.1 |

优化要点：

1. **`lookup_file_hash` 加内存 LRU**：新增 `_path_cache: OrderedDict[(path, mtime, size), file_hash]`，
   先查内存命中跳过 SQLite。`batch_put_results` 写入后主动填充，使热缓存二次扫描 100% 命中内存
2. **`batch_put_results` 主动填充 `_hit_cache`**：从 `item.hits` 构造 result dict 写入 LRU，
   替代原 invalidate 行为。`item.hits` 为空（预筛命中）时保留 LRU 条目（`scan_results` 未变）
3. **S5 `cache_lookup` 从 295ms 降到 2.49ms**（-99.2%），全部命中内存

### iter-39 性能优化策略执行

四项策略执行，S1 单线程无缓存提升 24%，无回退：

1. **P1 大小文件分流哈希**：`< 8KB` 用 SHA-256，`≥ 8KB` 用 BLAKE2b（`hash_bytes`
   按大小自动选择）。小文件避免 BLAKE2b 上下文初始化开销，大文件利用 BLAKE2b
   的 GIL 释放与 SIMD 加速。基准测试文件多为 4-6KB，收益在测量噪声内
2. **P2 SQLite 批量写入**：`BatchWriteItem` 数据类封装单文件元数据 + 所有规则缓存
   结果，`batch_put_results` 单次事务 `executemany` 写入 scanned_files/file_paths/
   scan_results 三表，`_BATCH_THRESHOLD=50` 累积后自动 flush。消除约 99% 的逐条
   commit/fsync 开销，S1 单线程提升 24%。`_batch_lock` 保护跨 worker 并发累积
3. **P3 Archive 文件级别并行**：`max_workers > 1` 时不同 archive 文件用
   `ThreadPoolExecutor` 并行扫描，单个 archive 内条目顺序执行（避免 reader 共享
   竞争）。`is_archive` 仅按扩展名过滤（不实例化），损坏文件交由 `scan_archive`
   捕获返回错误结果。`_accumulate_archive_results` 提取结果累积公共逻辑
4. **P4 提取内容缓存**：新增 `extracted_contents` 表按 `file_hash` 缓存提取器结果，
   同内容不同路径跳过重复提取。`ArchiveScanner._scan_entry_cached` 与
   `Scanner.default_extract_content_with_hash` 均接入此缓存

### iter-38 缓存性能优化与版本兼容

四项优化，热缓存场景跨越两个数量级提升：

1. **BLAKE2b 替代 SHA-256**：`hashlib.blake2b(data, digest_size=32)` 输出 64 字符
   hex，`scanned_files.file_hash` 列 schema 无需变更；通过 OpenSSL 加速且释放 GIL
2. **LRU 命中缓存**：`CacheStore._hit_cache` 容量 4096，`OrderedDict` 实现 O(1)
   访问与淘汰；写操作（put_result/register_file/register_path）后 invalidate
3. **mtime + size 三元组预筛**：`CacheStore.lookup_file_hash(path, mtime, size)`
   命中且所有规则已缓存时跳过 `read_bytes`，热路径降为纯内存查询
4. **CACHE_COMPAT_VERSION 版本号机制**：`meta` 表存储数据语义版本号，
   哈希算法/序列化结构变更时递增触发自动 purge，避免旧缓存污染

### iter-37 扫描热路径性能优化

扫描热路径三项优化，单线程吞吐量提升约 7%：

1. **walker + context 减少 syscall**：`FileEntry.from_direntry` 复用 `os.scandir` 的
   `DirEntry.stat()`（Windows 平台缓存 stat 结果），并用 `stat.st_mode` 位运算判断目录，
   避免原 `path.stat()` + `path.is_dir()` 两次系统调用
2. **matchers 预编译 CONTAINS 正则**：不区分大小写的 CONTAINS 模式在 `LeafMatcher.__init__`
   预编译 `re.compile(re.escape(pattern), re.IGNORECASE)`，避免每次匹配重复编译；
   `_apply_regex` 改用迭代器收集匹配，避免 `list(finditer)` 对大文本创建大列表
3. **archive scanner 内存版提取**：`_extract_content_from_bytes` 直接调用
   `extract_content_from_bytes`，删除原 `_extract_via_temp` 写临时文件再读回的逻辑，
   消除压缩包每个二进制条目的 2 次冗余磁盘 I/O

## slow 回归断言阈值

| 测试 | 断言 | 基线值 |
|------|------|-------:|
| test_sequential_throughput | ≥ 50 files/s | 85.4 |
| test_concurrent_throughput | ≥ 50 files/s | 172.5 |
| test_cache_throughput | ≥ 200 files/s | filename 规则热缓存 |
| test_cache_hit_ratio | ≥ 95% | 100% |
| test_extract_speed (各格式) | 见上表阈值 | 见上表 |

## 不可生成格式

以下格式因无法动态生成测试文件，未纳入提取器速度基准：

- **PDF**：需 reportlab 等库生成，当前仅通过 mock 测试覆盖
- **DOC/PPT**：OLE 二进制格式，无简单生成方式
- **XLS**：BIFF 格式，需 xlwt 库（xlrd 2.0+ 只读）
- **MSG**：extract-msg 仅支持读取
- **ODT/ODS**：需 odfpy 库

## 复现方式

```bash
# 提取器单格式速度
uv run pytest -m slow tests/test_benchmark.py::TestExtractorBenchmark -v

# 混合格式扫描吞吐量
uv run pytest -m slow tests/test_benchmark.py::TestScanBenchmark -v

# CLI 基准脚本（可配置文件数和线程数）
uv run python benchmarks/bench_scan.py --files 1000 --workers 4
uv run python benchmarks/bench_scan.py --output json --files 500
```
