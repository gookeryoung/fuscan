# iter-03：fuscan-re 跨平台 wheel CI 与 S3 性能基准

## 需求清单

处理 req-46 遗留事项（req-46 全部 12 条需求已在 iter-01/iter-02 完成）：

- [x] 1. 跨平台 wheel 构建 CI（GitHub Actions）
- [x] 2. S3 场景 PoolEngine vs Python ContentRegexPool 性能基准对比
- [x] 3. 修复 iter-02 遗留的 py_repr 引号选择 bug（性能测试中发现）
- [x] 4. 清理 PoolGroup dead code（Rust 编译 warning）

## 迭代目标

1. 在根目录 `.github/workflows/` 新增 `fuscan-re-wheels.yml`，实现多平台 × 多 Python 版本
   wheel 构建，tag 触发发布到 PyPI（OIDC），push/PR 仅构建验证
2. 新增 S3 AND 组合场景 PoolEngine 性能基准测试，验证加速比 ≥ 2x
3. 修复性能测试中发现的 py_repr 引号选择 bug（match_text 含单引号时 detail 不一致）

## 改动文件清单

### CI 配置
- `.github/workflows/fuscan-re-wheels.yml`：新增，5 平台矩阵（linux x86_64/aarch64,
  windows x64, macos x86_64/aarch64）× Python 3.10/3.12/3.14，tag `fuscan-re-v*.*.*`
  触发 PyPI OIDC 发布，push/PR 仅构建验证。用 maturin-action `working-directory`
  在 monorepo 子目录运行 maturin
- `packages/fuscan-re/.github/workflows/CI.yml`：删除（maturin 自动生成，位于子目录
  不被 GitHub Actions 识别，已被根目录 workflow 替代）

### Rust crate（packages/fuscan-re/src/lib.rs）
- `py_repr`：修复引号选择逻辑，复刻 CPython `unicode_repr`：
  含单引号但不含双引号 → 改用双引号（避免转义单引号），与 Python `repr()` 一致
- `PoolGroup`：删除 dead code 字段 `case_sensitive` 和 `group_to_child_id`
  （case_sensitive 已在编译时体现到 regex flags；group_to_child_id 未被 evaluate_inner
  使用，child_id 通过 `parse_pool_group_name` 直接从组名解析）
- `build_pool_groups`：移除对已删字段的填充代码
- `test_py_repr`：新增 4 个引号选择回归测试用例

### Python 测试
- `tests/test_native_regex_pool.py`：
  - `TestNativeRegexPoolEquivalence.test_match_text_with_single_quote`：新增，
    验证 match_text 含单引号时 detail 引号选择一致（iter-03 修复回归保护）
  - `TestPoolEnginePerformance.test_s3_and_combo_speedup_at_least_2x`：新增，
    S3 AND 组合场景（50 条规则 × 2~3 子项 × 48KB 文本）PoolEngine 加速比 ≥ 2x

## 关键决策与依据

### 1. fuscan-re wheel CI 放根目录而非子目录
**决策**：在根目录 `.github/workflows/fuscan-re-wheels.yml` 新增 workflow，删除子目录下
maturin 自动生成的 `packages/fuscan-re/.github/workflows/CI.yml`。
**依据**：GitHub Actions 只识别仓库根目录的 `.github/workflows/`；子目录下的 CI.yml
不会被加载执行。maturin-action 支持 `working-directory` 参数在 monorepo 子目录运行
maturin，无需将 workflow 放子目录。

### 2. fuscan-re 独立 tag 触发发布
**决策**：用 `fuscan-re-v*.*.*` tag 触发 PyPI 发布，与 fuscan 主包的 `v*.*.*` tag 分离。
**依据**：fuscan-re 是独立 PyPI 包（版本 0.1.0），与 fuscan 主包（0.2.15）独立演进。
独立 tag 避免与 fuscan 主包 release 冲突，支持 fuscan-re 单独发版。

### 3. PyPI 发布用 OIDC trusted publisher
**决策**：tag 触发时用 `uv publish` + OIDC（`id-token: write`），不存 API token。
**依据**：与 release.yml 的 fuscan 主包发布方式一致（OIDC 是 PyPI 最佳实践）。
前置条件：在 PyPI 项目 fuscan-re 配置 trusted publisher
（owner=gookeryoung/fuscan, workflow=fuscan-re-wheels.yml, environment=pypi）。

### 4. 构建矩阵精简为 5 平台
**决策**：linux x86_64/aarch64 + windows x64 + macos x86_64/aarch64，去掉 musllinux/
windows x86/arm/s390x/ppc64le 等小众架构。
**依据**：覆盖主流平台（CPython x86_64 + ARM），musllinux（Alpine）用户少，windows x86
已弃用，ARM 生态不成熟。后续按需扩展。

### 5. py_repr 引号选择修复
**决策**：Rust `py_repr` 复刻 CPython `unicode_repr` 引号选择逻辑：含单引号但不含
双引号 → 改用双引号。
**依据**：Python `f"...{text!r}"` 调用 `repr(text)`，CPython 对含单引号的字符串用
双引号包裹（避免转义）。iter-02 的 `py_repr` 总是用单引号并转义内部单引号
（`\'`），导致 `detail` 字段在 match_text 含单引号时不一致。S3 性能测试用
`SECRET_KEY = '...'` 样本触发此 bug，iter-02 的 11 个测试未覆盖含单引号场景。

### 6. 性能测试加速比阈值 2x
**决策**：S3 AND 组合场景 PoolEngine 加速比断言 ≥ 2x（保守阈值）。
**依据**：iter-01 BucketEngine 在 S2 场景验证 4.15x 加速（50 条 CONTENT REGEX，
48KB 文本）。PoolEngine 同源（regex crate + py.detach 释放 GIL），预期类似加速比。
阈值设 2x 留余量，CI 环境波动不影响结论。

## 代码实现情况

### fuscan-re-wheels.yml（已完成）
- 5 平台矩阵：linux x86_64/aarch64（manylinux auto）、windows x64、macos x86_64/aarch64
- Python 3.10/3.12/3.14 三版本（setup-python 安装，maturin --find-interpreter 为每个
  版本构建独立 wheel，因 pyo3 非 abi3 模式每个 CPython ABI 需独立 wheel）
- sccache 加速重复编译（tag 发布时禁用以避免缓存污染）
- sdist 单独 job 构建
- publish job：tag 触发时下载所有 artifact，uv publish + OIDC 发布到 PyPI

### py_repr 修复（已完成）
```rust
fn py_repr(s: &str) -> String {
    let has_single = s.contains('\'');
    let has_double = s.contains('"');
    let quote = if has_single && !has_double { '"' } else { '\'' };
    // ... 转义逻辑与所选引号一致
}
```

### PoolGroup dead code 清理（已完成）
删除 `case_sensitive`（编译时已体现到 regex flags）和 `group_to_child_id`
（evaluate_inner 用 `parse_pool_group_name` 直接从组名解析 child_id）。

## 测试验证结果

### Rust 单元测试
- 7 个测试全部通过（含新增 4 个 py_repr 引号选择用例）

### Python 集成测试
- `test_match_text_with_single_quote`：验证含单引号 match_text 的 detail 一致
- `test_s3_and_combo_speedup_at_least_2x`：S3 场景 50 条 AND 规则 × 48KB 文本，
  PoolEngine 加速比 ≥ 2x 通过（实测约 3-4x，与 BucketEngine 同量级）

### 门禁
- ruff check：全部通过
- ruff format --check：176 files already formatted
- pyrefly check：0 errors（978 suppressed）
- pytest：3016 passed, 2 skipped, 84 deselected（比 iter-02 多 1 个引号选择测试）
- coverage：95.05%（达到 95% 门禁）

## 整合优化情况

- 删除子目录下错误的 CI.yml，消除"位置无效但看似生效"的混淆
- 清理 PoolGroup dead code，消除 Rust 编译 warning
- py_repr 修复同时影响 BucketEngine 和 PoolEngine（共用 py_repr 构造 detail），
  修复后两者在含引号 match_text 场景下均与 Python 一致

## 遗留事项

- pyrefly 对 fuscan_re 的 stub 支持（可选，低优先级）：当前 978 suppressed 均为
  fuscan_re PyO3 扩展属性缺失，不影响功能
- fuscan-re 首次发布需在 PyPI 配置 OIDC trusted publisher
- CI workflow 需首次 push 到 GitHub 后验证实际运行（本地无法验证 GH Actions）

## 下一轮计划

req-46 及其遗留事项已全部完成。fuscan-re 原生匹配引擎（iter-01 BucketEngine +
iter-02 PoolEngine + iter-03 CI/性能基准/py_repr 修复）交付完毕。后续可选方向：

1. fuscan-re 首次发布到 PyPI（打 `fuscan-re-v0.1.0` tag，需先配置 trusted publisher）
2. pyrefly stub 支持（消除 978 suppressed 警告）
3. 其他 GIL 热点优化（charset-normalizer / olefile，ROI 低暂不优先）
