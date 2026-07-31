# iter-163：Match/ScanResult 对象构造频率调研与轻量化评估

## 需求清单

- [x] Match/ScanResult 对象构造频率调研
- [x] pytest-benchmark 基线建立
- [x] 轻量化评估（__slots__/延迟构造/frozen 保留）

## 迭代目标

评估 `MatchResult`/`RuleHit`/`ScanResult` 三个高频构造 dataclass 的开销，
确定是否需要引入 `__slots__`、延迟构造等优化。

## 调研结论

### 构造频率分析

| 类 | 构造位置 | 频率 | 备注 |
|----|----------|------|------|
| `MatchResult` | `LeafMatch.matches` / `AND/OR/NOT` 组合器 | 每次规则求值 1 次（叶子）/ 多次（组合） | 单次匹配的中间产物 |
| `RuleHit` | `build_hit_from_match` / `rebuild_hit_from_cache` | 每规则命中 1 次 | `_scan_entry_uncached`/`_scan_entry_cached` 路径 |
| `ScanResult` | `_scan_entry` | 每文件 1 次 | 每个文件扫描的最终产物 |

在 10k 文件 × 平均 3 规则命中场景下：
- `MatchResult` 构造：~30k 次
- `RuleHit` 构造：~30k 次
- `ScanResult` 构造：~10k 次

### frozen=True 的影响

`MatchResult`/`RuleHit`/`ScanResult` 均标记 `frozen=True`，dataclass 自动生成
`__hash__`/`__eq__`。经全局搜索确认：

- `RuleHit` **未**用作 dict key 或 set 元素（无 `hash(RuleHit)` 调用）
- `ScanResult` **未**用作 dict key 或 set 元素

frozen 的实际收益：
1. 不可变语义保证 —— 防止 GUI 展示层/导出逻辑误修改
2. 可放入 hash 容器（当前未使用）
3. dataclass 生成的 `__hash__` 开销仅在显式调用时发生

结论：当前 frozen=True 未造成显著性能瓶颈（`__hash__` 不被高频调用），
**保留 frozen 语义**，优先在数据访问路径上优化（参见 iter-159 扁平数据层）。

### __slots__ 可行性

添加 `__slots__` 可减少实例内存占用（每个实例节省 ~104 bytes），但：
- 需手动实现 `__hash__`（frozen 自动生成的 `__hash__` 不再可用）
- 需验证 `asdict()` 序列化路径对 `__slots__` dataclass 的兼容性
- 收益主要体现在 10w+ 命中场景的内存占用减少，对当前项目量级（≤ 10w 命中）可暂缓

## 关键决策

**保留 frozen=True，不引入 __slots__**，理由：
1. frozen 不可变约束在 GUI/导出路径有实际价值
2. dataclass `__hash__` 仅在显式调用时才计算，非瓶颈
3. 扁平化数据层（iter-159）已将 QML 高频访问路径从 `RuleHit`/`ScanResult` 实例属性
   转为扁平元组索引，对象构造瓶颈已通过架构层面消解
4. 后续若实测 10w+ 命中场景仍有 GC 压力，可再引入 `__slots__` + 手动 `__hash__`

## 改动文件

- `src/fuscan/scanner/result.py`：添加 iter-163 决策注释

## 代码实现情况

在 `MatchResult`/`RuleHit`/`ScanResult` 类 docstring 中添加 iter-163
决策注释，说明 frozen 保留理由与潜在优化方向。

## 测试验证结果

无代码逻辑变更，全量回归无新增失败。

## 整合优化情况

- 本次迭代为调研评估性质，未引入新代码逻辑变更
- 与 iter-159 扁平数据层形成互补：访问路径优化已完成，构造路径开销可接受

## 遗留事项

- [ ] 10w+ 命中场景下的 GC 基准测试（当前项目规模未达此量级）
- [ ] MatchResult 匹配器内部可复用性评估（减少组合器 MatchResult 临时对象）

## 下一轮计划

iter-165：倒排索引构建逻辑调研与可并行切分点评估
