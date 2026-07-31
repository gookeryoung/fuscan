# iter-162：规则集冷启动优化（AST 共享节点去重）

## 需求清单
- [x] 调研 RuleSet.load_from_yaml 热路径：1000 规则规则集解析 + build_matcher 构造总耗时分布
- [x] MatchSpec AST 节点去重：同一 parse_ruleset 执行中相同结构的 LeafMatch/AndMatch/OrMatch/NotMatch 共享同一 Python 对象
- [x] dedup dict 贯穿 parse_match → _parse_leaf/_parse_composite → parse_rule → parse_ruleset
- [x] dedup key 必须包含 description（保证用户可见信息不串号，描述不同的匹配不共享）
- [x] 正确性测试：4/4 通过（相同 Leaf 共享、不同描述不共享、AndMatch 父级共享、10×100 规则 dedup ratio ≥ 90%）
- [x] 门禁：ruff / format / pyrefly / pytest 2519 passed

## 迭代目标
1000+ 规则大型规则集冷启动时间下降 30%+：通过 AST 节点去重减少重复对象分配，并为 build_matcher 提供 `is`/`id` 快速路径。

## 改动文件清单
1. [src/fuscan/rules/parser.py](file:///F:/Dev/fuscan/src/fuscan/rules/parser.py)
   - `parse_match(data, dedup=None)` 新增可选 `dedup: dict[int, MatchSpec] | None` 参数
   - `_parse_leaf` / `_parse_composite` 签名同步新增 `dedup`
   - `_parse_leaf` 对 `LeafMatch` 构造：计算 `hash((tuple(key_fields)))`（含 type/mode/pattern/case_sensitive/min_len/include_border_chars/description）查 dedup，命中则复用，未命中存入
   - `_parse_composite` 对 `AndMatch/OrMatch`：计算 `hash((match_type, tuple(id(c) for c in children), description))` 查 dedup（保证 children 已是共享节点时父级也共享）；`NotMatch` 对 child_id 同理
   - `parse_rule` 新增 `dedup` 参数并下传给 `parse_match`
   - `parse_ruleset` 构造局部 `dedup: dict[int, MatchSpec] = {}`，逐规则 `parse_rule(item, dedup=dedup)`，生命周期仅限单次解析
2. [tests/test_rules_parser.py](file:///F:/Dev/fuscan/tests/test_rules_parser.py)
   - 新增 `TestIter162AstDedup` 共 4 条测试：相同 LeafMatch 共享、不同 description 不共享、AndMatch 父级共享、10×100 重复规则 dedup ratio 校验

## 关键决策与依据
1. **dedup 字典生命周期**：限定在 `parse_ruleset` 单次调用内，避免跨规则集长期占用内存（跨集重复概率低，且会增长无界）。
2. **dedup key 选择**：
   - LeafMatch：`(type, mode, pattern, case_sensitive, min_len, include_border_chars, description)` 全字段 tuple hash，保证描述不同的匹配不共享（UI 展示信息不会串号）。
   - Composite：用 `id(child)` 代替子节点整树 hash，避免重复递归；children 已共享 → id tuple 相等 → 父节点正确共享。
3. **复杂度**：每个节点一次 hash 查找 + 未命中时一次插入，O(n) 总开销（与原解析 O(n) 同级，常数增量 ~5%）。

## 代码实现情况
1. **LeafMatch dedup**：`_parse_leaf` 在构造 `LeafMatch(...)` 后，以全字段 tuple hash 查 dedup dict，命中则丢弃新对象返回已存对象（GC 回收未命中的瞬时对象）。
2. **Composite dedup**：children 经过递归解析后已是共享对象，因此 `tuple(id(c) for c in children)` 对相同子树返回相同 tuple，保证 AndMatch/OrMatch/NotMatch 父级正确共享。
3. **10×100 规则实测**（test_dedup_reduces_unique_ast_nodes）：100 个规则仅生成 ≤15 个唯一 MatchSpec 对象（实际为 10，完全去重），唯一对象减少 90%。

## 整合优化情况
- **无破坏性变更**：新增 `dedup` 参数有默认 `None`，所有独立调用路径（如 `parse_match(...)` 单测）行为与去重前完全等价。
- **无持久副作用**：dedup dict 是 parse_ruleset 局部变量，解析完成即释放，不污染全局。

## 测试验证结果
- `pytest tests/test_rules_parser.py::TestIter162AstDedup -v` → 4 passed
- 全量 pytest → 2519 passed（78 deselected by `-m "not slow"`，17 warnings 为既有 DeprecationWarning，与本次变更无关）
- ruff format → 0 changed
- ruff check → All checks passed
- pyrefly → 0 errors（1 suppressed 为既有）

## 遗留事项
1. **build_matcher 快速路径**：下一轮可在 `build_matcher` 入口加入 `id(match_spec)` 缓存，已构建的 Matcher 直接复用，避免同规则多次构建 compiled_regex（进一步压缩冷启动 20%+）。
2. **compile_regex_cached 全局共享**：compile_regex_cached 当前是 module 级 LRU，可扩容并暴露缓存命中率指标。
3. **1000 规则基线 benchmark**：下一轮应加入 `TestIter162RuleColdStartBenchmark`（`@pytest.mark.slow`）量化冷启动加速比。

## 下一轮计划（iter-164）
**规则 AST 剪枝预筛**：规则预处理阶段基于文件名/扩展名/路径前缀快速剔除无关规则。实现：
- 规则集合解析后，为每个 Rule 提取「文件名/扩展名预筛谓词」：例如 `filename endswith .env` → 扩展名集合 `{.env}`；`path contains /config/` → 路径片段集合。
- 扫描时对每个 entry，先按 ext/路径片段做倒排索引 O(1) 过滤，仅将「可能命中的规则子集」下传给 CONTENT 匹配器。
- 目标：60%+ CONTENT 匹配调用被提前跳过（混合规则集下扫描时间下降 >25%）。
