# 扫描期 GUI 完全冻结修复方案

## Context（背景与目标）

用户反馈："扫描过程中 GUI 完全无法操作，否则未响应卡死。" 这是**完全冻结**（非轻微卡顿）。

**根因（已在代码中逐一核对确认）——GIL 饥饿**：

- 扫描在独立 QThread（[scan_worker.py](file:///f:/Dev/fuscan/src/fuscan/gui/workers/scan_worker.py)）中运行，已设 `QThread.LowPriority`。
- 并发模式用 `ThreadPoolExecutor`，默认 `max_workers=5`（[_task_overrides.py:62](file:///f:/Dev/fuscan/src/fuscan/gui/controllers/_task_overrides.py#L62) `effective_max_workers` 回退 5）。
- 5 个 worker 线程执行 `Scanner._scan_entry` → 内容提取（charset-normalizer 解码，纯 Python 持 GIL）+ CONTENT 桶匹配 `re.finditer`（[_content_buckets.py:554](file:///f:/Dev/fuscan/src/fuscan/scanner/_content_buckets.py#L554) `compiled.finditer(content)`，纯 Python re 持 GIL）。
- 现有 `time.sleep(0)` 让步只在**扫描主线程**的收割循环（[_pipeline_phase.py](file:///f:/Dev/fuscan/src/fuscan/scanner/_pipeline_phase.py)）和顺序循环里，**worker 线程内部无任何让步点**。
- CPython 默认切换间隔 5ms + 5 个持 GIL 的 worker 线程 → GUI 主线程极难抢到 GIL 处理绘制/输入 → 完全冻结。`QThread.LowPriority` 是有益辅助但非解药（GIL 是应用层锁，worker 持有期间 OS 再偏向主线程也拿不到）。

**预期结果**：扫描期间 GUI 保持可操作（可点击、可滚动、可响应取消），且不破坏取消/缓存/吞吐/覆盖率。

**约束**：不引入进程池、不加新依赖（沿用「方案 B」既定路线）；提交前过 `make check`（ruff + pyrefly + pytest + branch coverage ≥95%）；Python 3.8+ 兼容、行宽 120；不在 UI 堆逻辑。

**用户确认力度**：采用**三措施完整组合**。

---

## 方案（三措施组合）

### 措施 1：worker 线程内按「桶间/规则间」边界定时 `sleep(0)` 让步（解决冻结主力）

在 worker 线程真正执行匹配的循环边界插入时间式让步，把「单文件内一连串 `finditer` 背靠背」拆成多个可让步点，主线程能在文件扫描中途就抢到 GIL。

**为何在桶/规则边界而非分块 finditer**：一个规则集通常有多个 bucket + 多条 remaining 规则，每个 `finditer` 是一次中等长度 C 调用（超长单行已被上一需求的 `is_minified_content` 跳过）。在边界让步天然无跨块漏匹配风险，覆盖绝大多数场景。

**落点**：
- [_content_buckets.py](file:///f:/Dev/fuscan/src/fuscan/scanner/_content_buckets.py) `match_content_via_buckets`：`for bucket in buckets:` 循环体末尾（约 L520 循环）。让步基线用**函数局部变量** `last_yield = time.perf_counter()`（严禁挂到 `self` 或共享状态——5 个 worker 会竞争；每文件重置计时可接受，反而更保守=让步更勤）。
- [scanner.py](file:///f:/Dev/fuscan/src/fuscan/scanner/scanner.py) `_scan_entry_uncached` 的 `for rule, matcher in effective_remaining:`（约 L1240）与 `_scan_entry_cached` 的 remaining 规则循环末尾，同样加函数局部时间式让步。

**代码形态**（`match_content_via_buckets` 内，复用 [_helpers.py](file:///f:/Dev/fuscan/src/fuscan/scanner/_helpers.py) 的 `GIL_YIELD_THRESHOLD_S`）：
```python
last_yield = time.perf_counter()
for bucket in buckets:
    ...  # 现有匹配逻辑不变
    now = time.perf_counter()
    if now - last_yield >= GIL_YIELD_THRESHOLD_S:
        last_yield = now
        time.sleep(0)  # 让出 GIL，主线程有机会处理绘制/输入
```
> `_content_buckets.py` 需新增 `import time` 与从 `_helpers` 引 `GIL_YIELD_THRESHOLD_S`（注意避免循环导入——`GIL_YIELD_THRESHOLD_S` 是常量，必要时在本模块内单独定义同值常量或从 `_helpers` 顶层导入）。

**影响**：取消响应不变（`sleep(0)` 不阻塞，取消仍靠 `_scan_entry` 入口 + 收割循环）；缓存/结果零影响；吞吐微秒级开销可忽略。

### 措施 2：进程级下调 GIL 切换间隔至 1ms

在应用启动时一次性 `sys.setswitchinterval(0.001)`（默认 5ms → 1ms），让持 GIL 的纯 Python 线程更频繁在字节码边界让出 GIL。

**为何进程级设一次、而非扫描开始/结束调**：GUI 全程需要响应性；「扫描开始调小、结束恢复」需在所有出口（含取消/异常）恢复，易漏易 flaky。空闲主线程无额外开销（切换只在多线程争抢时发生）。对单次超长 C 调用无效（故必须与措施 1/3 配合）。

**落点**：[app.py](file:///f:/Dev/fuscan/src/fuscan/app.py) `main()` 早期。**抽独立小函数** `_tune_gil_switch_interval(interval: float = 0.001) -> None` 便于单测（`main()` 大部分在 `# pragma: no cover`）。

**影响**：取消/缓存零影响；吞吐轻微（<2%，benchmark 验证）；受益的不止扫描 worker，export/stats/filter/restore worker 同样受益。

### 措施 3：CONTENT 正则密集场景动态降并发至 2

对「CONTENT 正则为主 + 提取器非原生（纯文本/olefile）」的规则集，5 个持 GIL 线程无并行收益且加剧争抢；把实际提交并发夹到 2（保留一点 I/O 重叠：一个 worker 在 `read_bytes` 释放 GIL 时另一个可跑），争抢比从 5:1 降到 2:1。对 pdf_oxide/calamine/lxml（Rust/C 释放 GIL）为主的规则集**保持高并发**（有真实并行收益），故判据必须区分。

**落点与判据**：不改 `_task_overrides.effective_max_workers`（那是配置语义）。在 [scanner.py](file:///f:/Dev/fuscan/src/fuscan/scanner/scanner.py) `Scanner.__init__` 末尾计算 `self._effective_max_workers`（保留原始 `self._max_workers` 配置值不动，仅调整实际提交用的数），[_pipeline_phase.py](file:///f:/Dev/fuscan/src/fuscan/scanner/_pipeline_phase.py) `_scan_concurrent` 用 `_effective_max_workers` 分派/提交。

**判据（保守起步）**：`max_workers > 1` 且规则集「以 CONTENT 正则为主」（`self._content_rule_names` 覆盖规则集主体）且扫描扩展名白名单主要落在**非原生引擎**（用 `engine_for_extension`/`get_extractor().engine_info` 判断）时，`_effective_max_workers = min(max_workers, 2)`；否则保持原值。判据可迭代细化，先只覆盖最明确的负优化场景。

**影响**：取消/缓存零影响，并发模型（提交/收割/取消加速）不变；CONTENT 密集场景吞吐≈持平（GIL 下纯 Python 无并行加速），原生提取器场景因判据保护不受损。

### 保留：`QThread.LowPriority`（措施 4 评估结论）

Windows 上 `QThread::Priority` 映射 `SetThreadPriority` 确实生效，是有益辅助（worker 让出 GIL 后主线程更快被调度），保留现状即可，无需改动，也不引入手动 `processEvents`。

---

## 明确否决

- **分块 finditer（按行/窗口切片 + 片间 sleep(0)）**：`compiled` 是多规则合并的复合 OR 正则，切片会漏跨块边界命中（敏感信息扫描的致命 false negative）；措施 1 已覆盖同场景且无正确性风险。
- **扫描开始调小/结束恢复 switchinterval**：多出口恢复易漏、易 flaky，用进程级设一次替代。
- **ProcessPoolExecutor / 新依赖**：既定路线否决，且有序列化/缓存/取消/跨平台 spawn 成本。

---

## 复用的既有设施

- `GIL_YIELD_THRESHOLD_S = 0.005`（[_helpers.py:104](file:///f:/Dev/fuscan/src/fuscan/scanner/_helpers.py#L104)）：措施 1 让步阈值直接复用。
- `engine_for_extension` / `get_extractor().engine_info`（[_helpers.py:163](file:///f:/Dev/fuscan/src/fuscan/scanner/_helpers.py#L163)）：措施 3 判断提取器是否原生。
- `self._content_rule_names`（[scanner.py](file:///f:/Dev/fuscan/src/fuscan/scanner/scanner.py) `__init__`）：措施 3 判断 CONTENT 正则占比。
- 既有时间式让步设计（[_pipeline_phase.py:135-140](file:///f:/Dev/fuscan/src/fuscan/scanner/_pipeline_phase.py#L135)）：措施 1 沿用同一理念（时间判断而非固定计数）。

---

## 待修改文件

- [_content_buckets.py](file:///f:/Dev/fuscan/src/fuscan/scanner/_content_buckets.py)：措施 1 核心（`match_content_via_buckets` 桶间让步）+ `import time`/让步阈值。
- [scanner.py](file:///f:/Dev/fuscan/src/fuscan/scanner/scanner.py)：措施 1（remaining 规则循环让步）+ 措施 3（`__init__` 并发降档判据、`_effective_max_workers`）。
- [_pipeline_phase.py](file:///f:/Dev/fuscan/src/fuscan/scanner/_pipeline_phase.py)：措施 3 落点（`_scan_concurrent` 用 `_effective_max_workers`）。
- [app.py](file:///f:/Dev/fuscan/src/fuscan/app.py)：措施 2（`_tune_gil_switch_interval` 小函数 + `main()` 调用）。
- [tests/test_scanner.py](file:///f:/Dev/fuscan/tests/test_scanner.py) 及新增测试文件：测试点见下。

---

## 验证（Verification）

**测试原则：不测墙钟/是否卡顿（必然 flaky），只测行为/状态/分支——跨平台确定性。**

1. **措施 2**：`_tune_gil_switch_interval()` 调用后 `assert sys.getswitchinterval() == 0.001`；`finally` 恢复原值避免污染其他测试。
2. **措施 1**：`monkeypatch` 替换 `_content_buckets` 内 `time.sleep` 为计数 mock、`time.perf_counter` 为可控递增序列（跨过阈值），构造 ≥2 个 bucket 的输入，断言 `sleep` 被调 ≥1 次；反向用例（单桶/未达阈值）断言未额外调用，覆盖 else 分支。
3. **措施 3**：CONTENT 正则为主 + 非原生提取器的 RuleSet → 断言 `scanner._effective_max_workers == 2`；含原生提取器扩展名/无 CONTENT 规则 → 断言保持 5。覆盖判据两分支。放 [tests/test_scanner.py](file:///f:/Dev/fuscan/tests/test_scanner.py)（无 GUI 依赖，最稳）。
4. **不新增**真实 QThread 计时断言测试（Windows 上真实 `start()` 会崩溃，且计时 flaky）。
5. **端到端**：`make check` 全绿（2850+ passed、coverage ≥95%）；手动运行 GUI 扫描大目录，确认扫描期可点击/滚动/取消（人工验收，因用户运行的 GUI 会锁默认缓存库，跑 `make check` 前需先关闭 GUI 实例）。
6. **吞吐权衡**：跑既有 benchmark（`test_benchmark_20_rules_1000_files` 等）确认措施 2/3 未引入明显吞吐回归。

**实施顺序**：措施 2（最小、零风险）→ 措施 1（解决冻结主力）→ 措施 3（需 benchmark 验证吞吐后定判据）。每步独立可回滚。
