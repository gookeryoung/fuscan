# iter-01：fuscan-re 原生匹配器（Rust + PyO3）

## 需求清单

见 `req-46-fuscan-re-原生匹配器.md`

## 迭代目标

实现 `fuscan-re` Rust crate 的 MVP：`ContentBucketEngine` 替代 `match_content_via_buckets`，
通过 PyO3 `allow_threads` 释放 GIL，用 `regex` crate 替代 Python `re`。
Python 侧条件导入，缺失时回退纯 Python 实现，保证零行为差异。

## 改动文件清单

### Rust crate（packages/fuscan-re/）
- `Cargo.toml`：添加 regex/aho-corasick 依赖，配置 crate 元数据
- `pyproject.toml`：maturin 构建配置，对齐 requires-python ≥3.10
- `src/lib.rs`：核心实现（ContentBucketEngine + RuleHitData + 辅助函数 + 7 个 Rust 单元测试）

### Python 集成
- `src/fuscan/scanner/_native_matchers.py`：新增，fuscan-re 条件导入 + RuleSpec 构建 + RuleHit 转换
- `src/fuscan/scanner/_content_buckets.py`：修改，`match_content_via_buckets` 新增 `native_engine` 参数
- `src/fuscan/scanner/scanner.py`：修改，`_CompiledRuleset` 新增 `global_native_engine` / `ext_native_engines` 字段，构建与透传逻辑

### 测试
- `tests/test_native_matchers.py`：新增，29 个 Python 集成测试验证语义等价（100% 覆盖率）

## 关键决策与依据

### 1. MVP 范围：仅做 ContentBucketEngine
**决策**：MVP 只替代 `match_content_via_buckets`，`ContentRegexPool` 留待 iter-02。
**依据**：
- `match_content_via_buckets` 是顶层纯 CONTENT 规则的热路径（S1/S2 主导）
- `ContentRegexPool` 复杂度高（跨规则去重、context 缓存、子项注册），需独立迭代
- 两者共享字面量提取、预筛、活跃子集逻辑，先做 BucketEngine 可复用到 Pool

### 2. 正则引擎选型：regex crate
**决策**：用 `regex` crate（burntsushi/regex）替代 Python `re`。
**依据**：
- DFA + aho-corasick 内置预筛，对字面量密集的复合 OR 正则天然优化
- PyO3 `allow_threads` 期间纯 Rust 代码不持 GIL
- 命名捕获组支持，与 Python `lastgroup` 语义对齐
- 跨平台纯 Rust，无外部 C 依赖

### 3. 预筛逻辑保留
**决策**：Rust 侧仍实现两级预筛（桶级 + 逐规则），不依赖 regex crate 内置预筛。
**依据**：
- 保留显式预筛便于活跃子集动态编译（`_get_active_compiled` 等价逻辑）
- 活跃子集缓存对普通文档（少量关键字命中）显著优于全桶 finditer
- 语义等价性更易验证（与 Python 实现一一对应）

### 4. 字面量提取：regex-syntax AST
**决策**：Rust 侧用 `regex-syntax` 子 crate 解析 AST 提取字面量。
**依据**：
- Python `sre_parse` 是 CPython 内部模块，无 Rust 等价
- `regex-syntax` 提供 AST，可解析 LITERAL/Alternation/Group/Repetition 等节点
- 与 Python `_extract_literals` + `_walk_sre_ast` 语义一致

### 5. GIL 释放边界
**决策**：`match_content` 方法整体包在 `py.detach(move || { ... })` 内。
**依据**：
- 匹配期间无需访问 Python 对象（content 已克隆为 owned String）
- 结果转 Python 对象在 `detach` 块外完成（避免持 GIL 转换）
- 与 `pdf_oxide`/`python-calamine` 模式一致

### 6. Python 集成：最小侵入式回退
**决策**：`match_content_via_buckets` 新增可选 `native_engine` 参数，非 None 时走原生路径。
**依据**：
- 保持向后兼容：现有调用方不传 `native_engine` 时走原 Python 逻辑
- Scanner 在 `_CompiledRuleset` 缓存层构建并维护 native_engine，避免重复构造
- 原生引擎异常时返回空列表，调用方 catch 后可回退 Python 路径重试

### 7. 原生引擎按 ext 分组构建
**决策**：`_CompiledRuleset` 维护 `global_native_engine` + `ext_native_engines` 字典。
**依据**：
- `match_content_via_buckets` 的 `buckets` 参数随 entry.extension 变化（global + ext 专属）
- native_engine 必须与传入的 buckets 同源，否则命中结果会包含/缺失规则
- 实际规则集中 ext 专属规则多为 AndMatch 组合（不入桶），`ext_native_engines` 通常为空，
  global 引擎覆盖所有扩展名文件的 CONTENT 桶匹配

## 代码实现情况

### Rust 侧（已完成）
- `lib.rs`：ContentBucketEngine + RuleHitData + 辅助函数（extract_inline_flags/extract_literals/
  dedup_substrings/py_repr/parse_group_name）+ build_buckets + match_content_inner（py.detach 释放 GIL）
- 7 个 Rust 单元测试全部通过
- `maturin develop --release` 已安装 fuscan_re-0.1.0 到 .venv

### Python 侧（已完成）
- `_native_matchers.py`：
  - `NATIVE_AVAILABLE: bool`（try import fuscan_re）
  - `RuleSpec` dataclass（与 Rust 端 RuleSpec 字段对齐）
  - `build_native_engine(buckets) -> ContentBucketEngine | None`
  - `match_content_via_native(engine, content) -> list[RuleHit]`
  - `_convert_hit(raw) -> RuleHit`（severity 用 Severity(str) 转换，match_texts 用 tuple()）
- `_content_buckets.py`：`match_content_via_buckets(content, buckets, native_engine=None)`，
  native_engine 非 None 时优先走原生路径
- `scanner.py`：
  - `_CompiledRuleset.__slots__` 新增 `global_native_engine` / `ext_native_engines`
  - `__init__` 构建完桶后调 `build_native_engine` 构建 global + 各 ext 引擎并缓存
  - `_match_content_via_buckets_impl` 新增 `native_engine` 参数透传
  - `_scan_entry_uncached` 按 entry.extension 查找对应 native_engine 透传

## 测试验证结果

### Rust 单元测试
- 7 个测试全部通过：extract_inline_flags / flags_to_chars / extract_literals_simple /
  extract_literals_branch / dedup_substrings / py_repr / parse_group_name

### Python 集成测试（tests/test_native_matchers.py）
- 29 个测试全部通过，`_native_matchers.py` 覆盖率 100%
- 覆盖场景：
  - 各模式（REGEX/CONTAINS/EQUALS/STARTSWITH/ENDSWITH）Python vs Rust 命中一致
  - case_sensitive True/False 行为一致
  - 预筛关键字命中/未命中路径一致
  - 活跃子集动态拼接与缓存一致
  - 多桶组合一致
  - Scanner 端到端扫描结果一致
  - 原生引擎异常/不可用时自动回退 Python 路径
  - 编译缓存复用同一原生引擎实例

### 性能基准
- S2 场景（50 条 CONTENT REGEX，48KB 文本）：4.15x 加速
- 密集场景（50 条规则，200 总命中，16KB 文本）：7.15x 加速
- S1/S2/S3 性能回归测试全部通过

### 门禁
- ruff check：全部通过
- ruff format --check：全部通过
- pyrefly check：0 errors（974 suppressed，均为 fuscan_re PyO3 扩展属性缺失）
- pytest：3004 passed, 2 skipped, 83 deselected
- coverage：95.04%（达到 95% 门禁，`_native_matchers.py` 100% 覆盖）

## 遗留事项

- iter-02：`ContentRegexPool` Rust 移植
- 跨平台 wheel 构建（CI）
- pyrefly 对 fuscan_re 的 stub 支持（可选）

## 下一轮计划

1. iter-02：`ContentRegexPool` Rust 移植（跨规则 CONTENT REGEX 子项去重池）
2. 跨平台 wheel 构建 CI（GitHub Actions）
