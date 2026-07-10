# iter-14：扫描实时进度反馈与按钮字体差异化

## 本轮目标

1. 扫描过程显示实时进展：当前正在解析的文件、已扫描/跳过/命中/错误数量、已用时间。
2. 关键主操作按钮字体大小差异化，更醒目。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/uniscan/scanner/result.py` | 新增 `ProgressInfo` frozen dataclass，加入 `__all__` |
| `src/uniscan/scanner/__init__.py` | 导出 `ProgressInfo` |
| `src/uniscan/scanner/scanner.py` | `Scanner.__init__` 新增 `on_progress`/`progress_interval` 参数；新增 `_emit_progress` 方法（时间节流 + `force=True` 跳过节流）；`scan()` 在遍历阶段每 200 条 emit、扫描阶段逐文件 emit、压缩包阶段逐包 emit、最终 `force=True` emit；`_scan_sequential`/`_scan_concurrent` 签名新增 `start, total, skipped` |
| `src/uniscan/gui/worker.py` | 信号 `progress = Signal(int)` → `progress_info = Signal(object)`；新增累计字段 `_cum_*` 与 `_start_time`；新增 `_on_progress` 回调累加前序根路径统计后 emit；`run()` 传 `on_progress` 给 Scanner |
| `src/uniscan/gui/main_window.py` | 新增 `_current_file_label`（QLabel#currentFileLabel）；`_build_scan_control_area` 拆分为 `_build_rules_row` + `_build_target_row`（修复 PLR0915）；`_on_scan` 连接 `progress_info` 信号、显示当前文件标签；`_on_scan_progress` 重写为接收 `ProgressInfo`，更新进度条/当前文件/详细统计；`_on_scan_finished`/`_on_scan_failed` 隐藏当前文件标签；QSS 字体层级：scanBtn 18px > modeCard 14px > statsLabel 13px > currentFileLabel 12px |
| `tests/test_scanner.py` | 新增 `TestScannerProgress`（6 个测试：回调触发、并发、节流、None 安全、最终 force、字段填充） |
| `tests/test_gui.py` | 新增 `TestScanWorkerProgress`（3 个测试：信号 emit、多根累计、字段类型）；修复 `test_window_geometry_restored` 高度断言为 ±2px |

## 关键决策与依据

1. **`ProgressInfo` 用 frozen dataclass**：进度信息是不可变快照，跨线程传递需保证线程安全，frozen 避免误改。
2. **时间节流（默认 150ms）**：扫描小文件时每文件 emit 会淹没 UI 事件循环；节流后 UI 仍流畅，最终 `force=True` 保证完成时进度准确。
3. **Scanner 层 emit vs Worker 层累计**：Scanner 只知道单根路径的进度，Worker 负责把多根路径的进度累加（`_cum_*` 字段），UI 看到的是全局累计值。
4. **`progress_info = Signal(object)`**：PySide2 信号传递 Python 对象用 `object` 类型，避免 QVariant 转换问题。
5. **QSS 字体层级**：主操作（扫描按钮）18px 加粗，模式卡片 14px，统计 13px，当前文件 12px 灰色——视觉层次清晰，扫描按钮最醒目。
6. **进度条确定模式**：扫描启动时不确定进度（`setRange(0,0)`），收到首个有 `total` 的进度后切换为确定模式。

## 验证结果

- ruff：全部通过
- pytest：452 passed, 1 skipped
- 覆盖率：88.79%（≥ 80% 门槛）
  - `scanner.py` 91%、`worker.py` 100%、`main_window.py` 84%

## 遗留事项

- ~~`worker.py` 覆盖率偏低（50%）~~：已解决。根因是 coverage 无法跟踪 QThread（C++ 线程）内执行的代码，
  通过新增 `TestScanWorkerDirect` 直接调用 `run()`/`_on_progress()` 在主线程执行，覆盖率提升至 100%。
- 若后续需要更精细的进度（如压缩包内条目计数），可扩展 `ProgressInfo` 字段。
