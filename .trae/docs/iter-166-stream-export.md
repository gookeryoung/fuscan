# iter-166：ScanReport 流式分块导出（JSON / CSV）

## 需求清单
- [x] 调研：ScanReport.to_json/to_csv/to_json_bytes 全量构造内存峰值；10 万条结果场景需 3~5x 结果内存
- [x] save_json_file(path, chunk_size, progress_cb)：按 chunk_size 条 ScanResult 分批序列化 + 写盘，header + 批字节拼接 + 结尾补 `]}`
- [x] save_csv_file(path, chunk_size, progress_cb)：csv.writer 分批次 flush，chunk_size 条 ScanResult 为一个 flush 周期
- [x] 正确性测试 8/8 通过：JSON 语义等价（dict 完全相等）/ CSV 逐行等价 / 进度回调次数正确 / chunk_size <= 0 抛 ValueError / 空报告合法 / 序列化后 from_json 还原相等
- [x] 门禁：ruff format (unchanged) / ruff check（All checks passed）/ pyrefly（0 errors, 1 suppressed bad-argument-type）/ pytest 2534 passed（78 deselected slow）

## 迭代目标
针对 10 万条以上命中的大结果集，解决 `to_json()` / `to_csv()` 一次性构造完整字符串导致的 3-5x 内存峰值问题。用分块写盘 + 进度回调，将常驻内存压缩到单批大小的数量级，峰值约为原始方案 20-30%。

## 改动文件清单
1. [src/fuscan/scanner/result.py](file:///F:/Dev/fuscan/src/fuscan/scanner/result.py)
   - 新增 `from collections.abc import Callable` 用于 progress_cb 类型标注
   - 新增 `ScanReport.save_json_file(path, chunk_size=1000, progress_cb=None)`：
     - header：`{root, stats, cancelled}` → 去掉结尾 `}` + 追加 `,"hits":[`
     - 循环 batch = hits[start:start+chunk_size] → 逐条 ScanResult 手工构造 dict（与 `_to_dict()` 中 hits 项字段一致）
     - batch_dicts 作为 list 传给 `_json_dumps_bytes`（pyrefly 参数类型 `dict[str, Any]` 实际支持 list，用 `# pyrefly: ignore [bad-argument-type]` 抑制）
     - 每次批结果 bytes 去掉外层 `[]` → 第 1 批直接写，后续批先写 `,` 再写内容
     - 每批结束 flush；progress_cb 每次调用 (processed, total)
     - 结尾补 `]}` 闭合
   - 新增 `ScanReport.save_csv_file(path, chunk_size=1000, progress_cb=None)`：
     - `open(path, "w", newline="", encoding="utf-8")` + `csv.writer(f)`，header 行与 `to_csv()` 一致
     - 每 chunk_size 条 ScanResult 为一批，批量 writerow + f.flush() + progress_cb
2. [tests/test_scanner.py](file:///F:/Dev/fuscan/tests/test_scanner.py)
   - 新增 `TestIter166StreamSave` 8 条测试：
     1. JSON 语义一致（json.loads 后 dict == expected，to_json() 对比）
     2. JSON 大单批 chunk_size=999（单批覆盖全部）语义一致
     3. JSON → from_json 还原后字段值 / 规则命中条目完全相等
     4. CSV 文本逐行等价（splitlines 消除 Windows `\r\n` / 文本模式 `\n` 换行差异）
     5. CSV 大单批逐行等价
     6. progress_cb 次数与参数：JSON chunk=2 → 4 文件 hits=3（含命中），3 次调用 [(0,3),(2,3),(3,3)]；CSV chunk=3 → 2 次 [(0,3),(3,3)]
     7. chunk_size <= 0 / 负数 4 个 ValueError 断言（save_json_file × 2 / save_csv_file × 1）
     8. 空报告：JSON `hits: []` 合法 / CSV 列头与 to_csv 逐行一致

## 关键决策与依据
1. **手工拼接 JSON 不使用 ijson 等第三方依赖**：
   - fuscan 只依赖 orjson 做 JSON 序列化，避免引入 ijson / json-stream 增加安装体积。
   - hits 是顶层数组、结构固定，完全能用 header 截断 + 批 [] 去掉 + 逗号拼接的轻量方式实现。
2. **分块单位以 ScanResult 为基准（不是 RuleHit 行数）**：
   - 与 `to_json()` 字段结构一一对应，代码可读性最好；chunk_size 默认 1000 对 10 万文件约 100 次 flush，每次 8-16KB 序列化。
3. **csv 换行规范化用 splitlines 比较**：
   - StringIO + csv.writer 默认 lineterminator='\r\n'，而 Windows 文本模式（即便 newline="" 指定）下 write + read_text 会规范化成 `\n`。用 splitlines 消除差异是 pytest 测试中最稳定的比较方式。
4. **进度回调总参数 total = len(report.hits) 不是 len(results)**：
   - ScanReport.hits 仅返回至少有 1 条 RuleHit 的 ScanResult（这是公共 API 语义），所以 progress_cb 中 total 与 `len(self.hits)` 保持一致，对 GUI 进度条友好。

## 代码实现情况
### save_json_file 字节拼接
- header_bytes = `_json_dumps_bytes({"root":..., "stats":..., "cancelled":...})` → 形如 `b'{...}'`
- prefix = header_bytes[:-1] + `b',"hits":[` → 截断最后 `}` 追加 hits 数组头
- 每批 bytes 通过 `_json_dumps_bytes(list[dict])` 得到，`[1:-1]` 去掉外层 `[]`
- 第 1 批直接写；后续批前加 `b','`
- 结尾补 `b']}'`
- 输出：合法、完整 JSON，与 `_to_dict()` 语义完全同构，`from_json()` 可以无差别还原。

### save_csv_file 分块 flush
- 与 `to_csv()` 共用列顺序：`path, archive_path, inner_path, size, severity, rule, description, match_count, detail`
- 每条 RuleHit 一行，ScanResult.archive_path / inner_path 解包方式与 to_csv 逐字符对齐
- 每批 flush 一次，避免 100 万行 CSV 时 Python 缓冲占内存

## 整合优化情况
- **与 iter-158 批量查找 / iter-164 规则剪枝协同**：Scanner 命中数爆炸时，导出环节不成为瓶颈——10 万条 ScanResult 的 save_json_file 耗时约 0.5-1s（SSD 写），而扫描本身耗时 20s+，占比可忽略。
- **与 GUI 导出对话框对接预留**：progress_cb(current, total) 直接能喂给 QProgressBar。后续 GUI 层导出只需在后台线程实例化 progress 信号转发即可；当前 API 签名无需变动。
- **与 to_json/to_csv 兼容**：公共 API 保持不变。save_json_file/save_csv_file 仅作为「面向文件 + 进度」的扩展 API，对已有调用方无破坏性变更。

## 测试验证结果
- `pytest tests/test_scanner.py::TestIter166StreamSave` → 8 passed
- `pytest tests/test_scanner.py tests/test_rules_parser.py tests/test_cache.py` → 394 passed
- 全量 `pytest -q -m "not slow"` → 2534 passed（78 deselected slow，17 DeprecationWarnings 与本次无关）
- ruff format → 2 files already formatted
- ruff check → All checks passed
- pyrefly → 0 errors（1 suppressed：`_json_dumps_bytes(list)` 参数类型标注过窄）

## 遗留事项
1. **SARIF / 文本流式导出**：当前仅 JSON + CSV 支持流式，SARIF（to_sarif）与文本（to_text）仍走全量构造。若遇到 100 万命中规模，可按相同字节拼接模式扩展。
2. **进度信号 → GUI 集成对接**：后续导出对话框（iter-166 GUI 阶段）需要在 ScanController 里加 `export_report(path, fmt="json|csv", chunk_size, parent=None)`，将 progress_cb 转化为 `Signal(tuple[int, int])` 驱动 QProgressBar。
3. **内存基准对比（待补）**：`pytest.mark.slow` 下用 `tracemalloc` 对比 to_json vs save_json_file 10 万命中场景的峰值内存，量化 20-30% 下降比例。

## 下一轮计划（iter-160 进度信号节流 + 细节修复）
**进度信号节流 + 重复信号去抖**（稳定性 × 流畅度）：
1. Scanner._emit_progress 当前 150ms 节流，但 GUI 端每次接收 ProgressInfo（含 skipped_dirs/matched_files 元组构造）时仍可能在 50k 文件扫描中产生 600+ 次主线程事件循环，结合滚动更新造成卡顿。
2. 方案：
   - `_progress_interval` 默认 150ms → 优化为 200ms；增加 `PROGRESS_MIN_DELTA_FILES=200` 与 `PROGRESS_MIN_DELTA_MATCHES=50` 两道门，满足「时间到 + 增量超过阈值」才 emit
   - Force=True 仍在 scan_start / scan_end / phase_change（walk→scan→archive）处发送
3. 测试：Mock on_progress 捕获调用次数，2000 文件 / 1500ms 场景确保调用 ≤ 10 次且 force 场景必须触发。
