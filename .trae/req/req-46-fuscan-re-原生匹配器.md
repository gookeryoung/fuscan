# req-46：fuscan-re 原生匹配器（Rust + PyO3）

## 背景

fuscan 当前扫描引擎受 Python GIL 限制，`re.finditer` 在大文本复合正则上为不可中断的 C 调用，
多个 worker 线程长时间持 GIL 导致 GUI 主线程冻结。已落地的缓解措施（`setswitchinterval(0.001)`、
`sleep(0)` 让步、动态降并发、桶级/逐规则预筛）已逼近 Python 层极限：

- S2 场景 71% 耗时在 I/O，29% 在 `match_content_via_buckets` 的 finditer 循环
- ContentRegexPool 优化后 S1 builtin -42.6%、S2 -66.2%、S3 -72.1%
- 项目记忆判定：「桶路径优化空间已耗尽」

## 目标

在 monorepo 下设计独立的 `fuscan-re` Rust crate（PyO3 绑定），将 `match_content_via_buckets`
与 `ContentRegexPool` 的核心匹配逻辑下沉到 Rust，借 PyO3 `allow_threads` 真正释放 GIL，
并用 `regex` crate（burntsushi/regex，DFA + aho-corasick 预筛）替代 Python `re`。

## 需求清单

- [x] 1. monorepo 结构：`packages/fuscan-re/` 独立 crate，maturin 构建
- [x] 2. Rust API：`ContentBucketEngine` 替代 `match_content_via_buckets`
- [ ] 3. Rust API：`ContentRegexPool` 替代 Python ContentRegexPool（待评估，MVP 先做 BucketEngine，留 iter-02）
- [x] 4. 辅助函数：`extract_literals` / `extract_inline_flags` / `dedup_substrings` Rust 移植
- [x] 5. PyO3 `allow_threads` 释放 GIL：匹配期间不持 Python 锁
- [x] 6. 语义等价：与 Python 实现完全一致的命中结果（first_match_text / total_count / detail）
- [x] 7. Python 集成：`scanner/_native_matchers.py` 条件导入，缺失时回退纯 Python
- [x] 8. `_content_buckets.py` 在 fuscan-re 可用时走原生路径
- [x] 9. Rust 单元测试覆盖核心逻辑
- [x] 10. Python 集成测试验证语义等价
- [x] 11. 性能基准对比（S1/S2/S3 场景）
- [x] 12. 门禁通过：ruff + pyrefly + pytest（coverage 95.04% 达到 95% 门禁）

## 约束

- fuscan-re 为**可选依赖**：缺失时 fuscan 回退纯 Python，行为完全一致
- 不破坏现有缓存兼容性（`CACHE_COMPAT_VERSION` 不递增，因命中结果不变）
- 不引入新的 Python 运行期依赖（仅 Rust 编译期工具链）
- 遵循既有降级模式（参考 `pdf_oxide` → `pypdfium2`）
- Rust 代码须跨平台（Windows/Linux/macOS），静态链接避免运行时依赖
- 保留 GIL 让步机制（`sleep(0)` 仍作用于收割循环，原生匹配内不再需要）
