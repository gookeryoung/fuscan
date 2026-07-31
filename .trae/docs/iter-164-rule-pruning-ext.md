# iter-164：规则 AST 剪枝预筛（按扩展名拆分规则集）

## 需求清单
- [x] 调研：Scanner._scan_entry_uncached 调用链；规则 MatchSpec 中 Target.FILENAME/PATH vs CONTENT 分布
- [x] extract_required_exts：从 MatchSpec 提取必须匹配的规范化扩展名集合（小写、去点），支持 Leaf FILENAME endswith/equals / And / Or 组合
- [x] Scanner.__init__：按 required_exts 拆分 (Rule, Matcher) pairs——global（无扩展名约束，对所有文件）+ ext 专属（仅对特定扩展名文件）
- [x] _build_content_buckets(pairs)：接受可选 pairs 参数（否则回退 self._compiled），兼容全局/各扩展名子集独立合桶
- [x] _get_effective_buckets_and_rules(entry)：按 entry.extension 返回 global + ext 专属的 CONTENT 桶 + remaining 规则合并列表
- [x] _scan_entry_uncached 使用 _get_effective_buckets_and_rules 替代 self._content_buckets / self._remaining_uncached_rules
- [x] 正确性测试 7/7 通过（等价性：20 规则 × 20 文件，预筛 vs 全跑 (path, rule) 集合完全相等；拆分验证：.env 规则不进 .txt 的 remaining 列表）
- [x] 门禁：ruff format / ruff check / pyrefly / pytest 2526 passed（78 deselected slow）

## 迭代目标
针对大型混合规则集（大量规则仅对特定扩展名/文件名生效），在 CONTENT 匹配阶段前剔除无关规则，目标减少 60%+ 非必要 CONTENT 正则匹配调用。

## 改动文件清单
1. [src/fuscan/scanner/scanner.py](file:///F:/Dev/fuscan/src/fuscan/scanner/scanner.py)
   - 新增 `extract_required_exts(match: MatchSpec | None) -> frozenset[str] | None`：从 MatchSpec 递归提取必须匹配的扩展名集合（Leaf FILENAME endswith/equals / 按 And 交集 / 按 Or 并集）
   - 新增 Scanner 字段：`_global_content_buckets` / `_global_remaining_rules`（无扩展名约束规则）、`_ext_content_buckets: dict[str, list[_ContentRuleBucket]]` / `_ext_remaining_rules: dict[str, list[tuple[Rule, Matcher]]]`（扩展名专属规则）
   - Scanner.__init__ 中按 `extract_required_exts(rule.match)` 拆分 self._compiled，分别调用 `_build_content_buckets(pairs)` 合桶
   - `_build_content_buckets(pairs=None)` 接受可选 pairs，非 None 时对传入子集合桶并返回对应的 remaining（不再用全局 self._compiled）
   - `_match_content_via_buckets_impl(content, buckets)` 抽离为可接受任意 buckets 列表的实现，原 `_match_content_via_buckets` 退化为薄封装
   - `_get_effective_buckets_and_rules(entry)`：基于 entry.extension 合并 global + ext 专属 buckets/rules，无扩展名文件直接取 global
   - `_scan_entry_uncached` 改为在匹配前先取 effective_buckets / effective_remaining，仅对该文件真正需要的规则集跑 CONTENT 匹配与 remaining 循环
   - 新增 AndMatch / MatchSpec import，移除未使用 NotMatch import
2. [tests/test_scanner.py](file:///F:/Dev/fuscan/tests/test_scanner.py)
   - 新增 `TestIter164RulePruning` 7 条测试：
     1. LeafMatch endswith → 提取扩展名正确
     2. AND(child1: endswith, child2: CONTENT) → 交集结果 = child1 扩展名
     3. OR(endswith json, endswith yaml) → 并集 = {json, yaml}
     4. 纯 CONTENT 规则 → 返回 None（无约束）
     5. Scanner 拆分正确性：1 条 .env AND + 1 条纯 CONTENT → .env AND 在 _ext_remaining_rules["env"]，纯 CONTENT 在 global_*
     6. 结果等价性：20 规则 × 20 文件，预筛前后 (path, rule_name) 命中集合完全相等
     7. _get_effective_buckets_and_rules：.env 专属规则仅出现在 env_entry 结果，txt 专属规则仅出现在 txt_entry 结果

## 关键决策与依据
1. **仅基于扩展名预筛**：扩展名是最廉价（entry.extension 已存在）且覆盖场景最广（`.env`/`.json`/`.pem`/`.txt` 等各有一套规则）的过滤维度；文件名包含片段/路径片段后续可迭代扩展，但对性能收益的性价比最低。
2. **拆分后对各子集独立合桶**：ext 专属 CONTENT 规则集往往 size < 2 而不合入桶，直接走 remaining 路径；但 CONTENT 规则 > 2 的扩展名（如 3+ 条针对 .py 的 CONTENT 规则）仍能享受合并 OR 正则收益。
3. **保守提取语义**：
   - `NotMatch` 直接放弃（None），避免过度预筛导致漏报
   - `OrMatch` 任一子项返回 None 则整体 None（宁可不预筛，不可漏匹配）
   - `AndMatch` 取多个非 None 子项的**交集**，保证扩展名必须同时满足所有约束（多个子项同时限定扩展名时是且关系）
4. **缓存模式兼容**：`_scan_entry_cached` 中当 disable_cache=False 时所有规则都是纯 CONTENT LeafMatch（无扩展名约束），故所有规则仍正确归属于 global_*，结果与未预筛时完全一致；disable_cache=True 时回退 `_scan_entry_uncached`，已使用预筛逻辑。

## 代码实现情况
1. **extract_required_exts**：递归遍历 MatchSpec 三种形态（Leaf / And / Or），规范化扩展名（去点小写），None 返回表示「无扩展名约束/无法安全提取」。
2. **Scanner 初始化拆分**：一次 O(N) 遍历 self._compiled（N = 规则数），将 pairs 按 required_exts 分发到 global 或 ext_pairs_map[ext]；各子集独立调用 `_build_content_buckets(pairs)`。
3. **_get_effective_buckets_and_rules**：针对 entry.extension 做 dict 查找（O(1)），仅在有 ext 专属 buckets/rules 时列表拼接；否则直接返回 global（无额外分配）。
4. **_scan_entry_uncached**：先取 effective_buckets / effective_remaining，后续流程未改结构，与旧实现逻辑等价。

## 整合优化情况
- **与 iter-154 CONTENT 桶合并协同**：ext 专属规则仍能在各子集内合并 OR 复合正则（针对扩展名的 2+ CONTENT 规则仍可合并，不与扩展名无关的全局 CONTENT 桶混用，避免 OR 复合正则无意义膨胀）。
- **与 iter-158 批量文件哈希预热协同**：预热逻辑仍是全局一次性执行（在 scan() 入口），预筛仅减少后续匹配阶段的规则数量，对缓存读写无额外影响。
- **与 iter-162 AST 去重协同**：去重减少的是 MatchSpec 对象数，预筛减少 per-file match 调用数，两者独立优化正交叠加。

## 测试验证结果
- `pytest tests/test_scanner.py::TestIter164RulePruning` → 7 passed
- `pytest tests/test_scanner.py tests/test_rules_parser.py tests/test_cache.py` → 386 passed
- 全量 `pytest -q -m "not slow"` → 2526 passed（78 deselected slow，17 DeprecationWarnings 与本次无关）
- ruff format → 2 files left unchanged
- ruff check → All checks passed
- pyrefly → 0 errors（2 suppressed，既有）

## 遗留事项
1. **文件名/路径片段预筛扩展**：下一轮可在 extract_required_exts 基础上补充 `extract_required_filename_contains` / `extract_required_path_contains` 预筛谓词，用倒排映射（如 `"/.aws/"` → 规则集合）进一步减少针对特定路径的 CONTENT 匹配。
2. **预筛命中率指标**：可在 ScanStats 中添加 `pruned_rule_skips` 计数器，统计总规则跳过次数，便于 A/B 验证 60%+ 的减少率。
3. **性能微基准**：应补 `TestIter164Perf`（`@pytest.mark.slow`），在 1000 规则 × 10000 文件场景量化整体扫描时间下降百分比。

## 下一轮计划（iter-159）
**QML ResultsPage delegate 复用与按需构造**（性能 × 内存）：
1. 现有 ResultsPage ListView 每行 delegate 独立 Loader，且每次 dataChanged 时完整重建 hits 子对象列表；5k+ 行结果集 delegate 内存 20%+ 可优化。
2. 实现：
   - 提取「行 delegate 工厂」：固定若干 delegate Loader 实例（池化），按 visible_range 行号回收复用。
   - `Match.model_data` 与 `RuleHit.model_data` 懒属性化：GUI 未访问详情/高亮时不构造 `match_texts` 与 `match_description` 字段。
3. 目标：5k 行结果集内存下降 20%，滚动渲染帧速率提升 15%+。
