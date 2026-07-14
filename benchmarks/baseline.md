# fuscan 性能基线

> 测量时间：2026-07-14（扫描热路径性能优化后）
> 测量方式：`uv run pytest -m slow tests/test_benchmark.py` + `uv run python benchmarks/bench_scan.py`

## 测量环境

| 项目 | 值 |
|------|-----|
| 操作系统 | Windows 11 (10.0.26200) |
| CPU | Intel Core i7-14700K (24 核) |
| Python | 3.8.20 |
| fuscan 版本 | iter-36 (commit pending) |

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
| S1 单线程无缓存 | 4.70 | 106.5 | 2.6 | - |
| S2 4 线程无缓存 | 2.80 | 178.6 | 4.4 | - |
| S3 24 线程无缓存 | 2.91 | 171.8 | 4.3 | - |
| S4 4 线程+冷缓存 | 2.98 | 167.6 | - | 0% |
| S5 4 线程+热缓存 | 2.98 | 167.5 | - | 100% |

**观察**：
- 单线程较优化前（99.2 → 106.5 files/s）提升约 7.4%，主要来自减少系统调用
  （DirEntry.stat 复用 scandir 缓存 + 合并 stat/is_dir 判断）
- 4 线程相比单线程提升约 1.7 倍（受 GIL 和 I/O 竞争限制）
- 24 线程与 4 线程相当，说明 4 线程已接近最优并发度
- 热缓存命中率 100%，但吞吐量与无缓存相近，因 CONTENT 规则仍需读取文件计算哈希
- 缓存收益主要体现在 filename-only 规则（跳过文件 I/O，见 test_cache_throughput ≥ 200 files/s）

## 性能优化记录（iter-37）

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
| test_sequential_throughput | ≥ 50 files/s | 99.2 |
| test_concurrent_throughput | ≥ 50 files/s | 171.6 |
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
