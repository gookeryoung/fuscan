# iter-02：fuscan-re ContentRegexPool 原生移植

## 需求清单

见 `req-46-fuscan-re-原生匹配器.md` 第 3 条（iter-02 完成）

## 迭代目标

将 Python `ContentRegexPool` 的核心匹配逻辑下沉到 Rust，新增
`ContentRegexPoolEngine` 类，通过 PyO3 `allow_threads` 释放 GIL，
用 `regex` crate 替代 Python `re`。Python 侧 `ContentRegexPool.compile()`
构建原生引擎，`evaluate()` 优先走原生路径，失败回退 Python。

## 改动文件清单

### Rust crate（packages/fuscan-re/）
- `src/lib.rs`：新增 `ContentRegexPoolEngine` + `PoolGroupSpec` + `PoolHitData`
  + `PoolGroup` + `build_pool_groups` + `evaluate_inner` + `parse_pool_group_name`

### Python 集成
- `src/fuscan/scanner/_native_matchers.py`：新增 `PoolGroupSpecData` dataclass
  + `build_native_regex_pool` + `evaluate_regex_pool_via_native` + `_convert_pool_hit`
- `src/fuscan/scanner/matchers.py`：
  - `ContentRegexPool.__init__` 新增 `_native_engine` 字段
  - `compile()` 末尾构建原生引擎，用原生引擎的 `compiled_child_ids` 替换 Python 侧
  - `evaluate()` 优先走原生路径（释放 GIL），失败回退 Python `_evaluate_group`

### 测试
- `tests/test_native_regex_pool.py`：新增，11 个集成测试验证语义等价

## 关键决策与依据

### 1. 缓存层级留在 Python 侧
**决策**：`evaluate()` 的 `id(context)` 缓存留在 Python 侧，Rust 只负责单次 evaluate。
**依据**：Rust 无法访问 Python 对象 id；缓存是 Python 层优化，与原生路径无关。

### 2. compiled_child_ids 由原生引擎提供
**决策**：`compile()` 后用原生引擎的 `compiled_child_ids` 替换 Python 侧。
**依据**：原生引擎可能因编译失败丢弃某些组，需保持 `is_compiled()` 判断一致。

### 3. 单子项组跳过逻辑保留
**决策**：Rust 侧 `build_pool_groups` 跳过单子项组（len < 2），与 Python 一致。
**依据**：单子项无合并收益，子项走独立 `matches()` 路径。

### 4. 命名组用 `_p{child_id}` 而非 `_f{idx}`
**决策**：Rust 侧池命名组用 `_p{child_id}` 全局唯一 ID。
**依据**：与 Python `_PoolGroup.group_to_child_id` 一致；`child_id` 跨规则去重，
不能像 BucketEngine 那样用桶内下标。

### 5. 不使用活跃子集动态编译
**决策**：PoolEngine 直接用整组 `compiled` finditer + `active_set` 过滤。
**依据**：与 Python `_evaluate_group` 一致——命名组名 `_p{child_id}` 必须与
`group_to_child_id` 映射一致，动态拼接子集会破坏映射。

## 代码实现情况

### Rust 侧（已完成）
- `ContentRegexPoolEngine`：接收 `Vec<PoolGroupSpec>`，按 case_sensitive 分组，
  构建复合 OR 正则 `(?P<_p{child_id}>pat)|...`，两级预筛，`py.detach` 释放 GIL
- `PoolHitData`：返回 child_id + first_match_text + match_count + detail + match_description
- `compiled_child_ids` getter：供 Python 侧维护 `_compiled_child_ids`

### Python 侧（已完成）
- `_native_matchers.py`：
  - `PoolGroupSpecData` dataclass（child_id/pattern/case_sensitive/description）
  - `build_native_regex_pool(specs)` 构建原生引擎
  - `evaluate_regex_pool_via_native(engine, content)` 返回 `dict[int, MatchResult]`
  - `_convert_pool_hit(raw)` 将 `PoolHitData` 转为 `MatchResult`
- `matchers.py`：
  - `ContentRegexPool.compile()` 末尾构建原生引擎
  - `ContentRegexPool.evaluate()` 优先走原生路径

## 测试验证结果

### Python 集成测试（tests/test_native_regex_pool.py）
- 11 个测试全部通过
- 覆盖场景：
  - 基本 REGEX 匹配 Python vs Rust 一致
  - 大小写敏感/不敏感一致
  - 同一子项多次命中 match_count 一致
  - 预筛未命中返回空字典一致
  - 混合大小写敏感组一致
  - 子项去重共享 child_id
  - 单子项组跳过编译
  - evaluate 缓存（同 context 只跑一次）
  - AndMatcher 端到端扫描结果一致
  - 空规格返回 None
  - 原生引擎异常回退 Python 路径

### 门禁
- ruff check：全部通过
- ruff format --check：186 files already formatted
- pyrefly check：0 errors（978 suppressed）
- pytest：3015 passed, 2 skipped, 83 deselected
- coverage：95.06%（达到 95% 门禁）

## 遗留事项

- 跨平台 wheel 构建 CI（GitHub Actions）
- pyrefly 对 fuscan_re 的 stub 支持（可选）
- 性能基准对比（S3 AND 组合场景，iter-01 已验证 BucketEngine 4.15x，
  PoolEngine 预期类似加速比）

## 下一轮计划

1. 跨平台 wheel 构建 CI（GitHub Actions）
2. 性能基准对比（S3 场景 PoolEngine vs Python）
