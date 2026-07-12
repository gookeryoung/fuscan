# 完成 GUI 第三阶段：测试更新与验证

## 摘要

Phase 3 的核心实现已完成（main_window.ui / main_window_ui.py / main_window.py / styles.qss），剩余工作是更新 `tests/test_gui.py` 中 11 个因 API 变更而失败的测试，并新增 `TestWorkflowStage` 测试类覆盖阶段切换逻辑，最后跑完整验证门禁。

## 当前状态分析

### 已完成（无需改动）

- `src/fuscan/gui/main_window.ui` — 三页 QStackedWidget 结构
- `src/fuscan/gui/main_window_ui.py` — 已重新编译
- `src/fuscan/gui/main_window.py` — WorkflowStage 枚举、阶段切换、独立按钮方案全部就位
- `src/fuscan/gui/styles.qss` — 新页面与新按钮样式

### main_window.py 关键 API 变更（影响测试）

| 移除的方法 | 替代方案 |
|---|---|
| `_set_scan_controls_text(text)` | 直接操作 `_pause_resume_btn.setText(...)` |
| `_switch_tab(index)` | `_switch_stage(WorkflowStage.X)` |
| `_on_view_history()` | 已删除（历史整合到配置页） |
| `_update_scan_button_icon()` | 已删除 |

| 变更的方法 | 新行为 |
|---|---|
| `_on_scan()` | 仅处理 SETUP 阶段开始扫描；不再处理暂停/继续 |
| `_pause_scan()` | 操作 `_pause_resume_btn`（不再动 `_scan_btn` / `_scan_action`） |
| `_resume_scan()` | 同上 |
| `_reset_scan_ui()` | 重置 `_pause_resume_btn` 文本 + `_cleanup_worker()`；不切换阶段 |
| `_on_scan_finished()` | 新增 `_switch_stage(RESULTS)` |
| `_on_scan_failed()` | 新增 `_switch_stage(SETUP)` |
| `_on_scan_cancelled()` | 有 hits → RESULTS，无 hits → SETUP |
| `_update_scan_button()` | 委托 `_update_stage_actions()` |

**关键不变量**：`_scan_btn.text()` 恒为 `"开始扫描"`；`_scan_action.text()` 恒为 `"开始扫描"`。动态文本全部转移到 `_pause_resume_btn`。

## 待修改的测试（共 12 处）

### 1. 删除 3 个测试（方法已移除）

- **L917 `test_set_scan_controls_text_updates_both`** — `_set_scan_controls_text` 已删除
- **L3448 `test_switch_tab`** — `_switch_tab` / `_tab_widget` 已删除
- **L3459 `test_on_view_history`** — `_on_view_history` / `_tab_widget` 已删除

### 2. 修改 TestScanControlUI 中 5 个测试

#### L948 `test_pause_scan_changes_state_and_text`
- 移除 `assert window._scan_action.text() == "继续扫描"`（不再修改 action 文本）
- `window._scan_btn.text() == "继续扫描"` → `window._pause_resume_btn.text() == "继续扫描"`

#### L959 `test_resume_scan_changes_state_and_text`
- 移除 `assert window._scan_action.text() == "暂停扫描"`
- `window._scan_btn.text() == "暂停扫描"` → `window._pause_resume_btn.text() == "暂停扫描"`

#### L969 `test_reset_scan_ui_resets_state`
- 移除 `window._set_scan_controls_text("暂停扫描")` 调用
- 移除 `assert window._scan_btn.text() == "开始扫描"`（恒真，无测试价值）
- 移除 `assert window._scan_action.text() == "开始扫描"`（同上）
- 移除 `assert not window._progress.isVisible()` 和 `assert not window._current_file_label.isVisible()`（可见性由阶段切换控制，非 `_reset_scan_ui` 职责）
- 新增 `assert window._pause_resume_btn.text() == "暂停扫描"`
- 新增 `assert window._worker is None`

#### L991 `test_on_scan_running_triggers_pause` → 重命名为 `test_on_pause_resume_running_triggers_pause`
- 测试目标从 `_on_scan()` 改为 `_on_pause_resume()`
- 移除 `window._set_scan_controls_text("暂停扫描")`
- `window._on_scan()` → `window._on_pause_resume()`
- `window._scan_btn.text() == "继续扫描"` → `window._pause_resume_btn.text() == "继续扫描"`

#### L1001 `test_on_scan_paused_triggers_resume` → 重命名为 `test_on_pause_resume_paused_triggers_resume`
- 同上模式，测试 PAUSED → RUNNING

### 3. 修改 TestScanControlIntegration 中 2 个测试

#### L1015 `test_scan_completes_through_main_window`
- `assert window._scan_btn.text() == "暂停扫描"`（运行中）→ `assert window._pause_resume_btn.text() == "暂停扫描"`
- 新增 `assert window._main_stack.currentIndex() == 1`（扫描中页）
- `assert window._scan_btn.text() == "开始扫描"`（完成后）→ 移除（恒真）
- 新增 `assert window._main_stack.currentIndex() == 2`（结果页）

#### L1047 `test_scan_cancel_through_main_window`
- 移除 `window._set_scan_controls_text("暂停扫描")`
- 移除 `window._progress.setVisible(True)`（在扫描中页上，由阶段控制可见性）
- 移除 `assert window._scan_btn.text() == "开始扫描"`（恒真）
- 新增 `assert window._main_stack.currentIndex() == 0`（无 hits 返回配置页）

### 4. 修改 TestConfigPersistence 中 1 个测试

#### L790 `test_splitter_sizes_restored`
- 在 `window = MainWindow()` 后、`window.show()` 前，新增 `window._switch_stage(WorkflowStage.RESULTS)`
- 原因：splitter 在 results_page 上，初始不可见时 Qt 可能不按 setSizes 分配；切到结果页后 show 才能正确反映比例

### 5. 修改 TestScanCallbacks 中 1 个测试

#### L3336 `test_pause_resume_scan`
- `assert "继续" in window._scan_btn.text()` → `assert "继续" in window._pause_resume_btn.text()`
- `assert "暂停" in window._scan_btn.text()` → `assert "暂停" in window._pause_resume_btn.text()`

## 新增 TestWorkflowStage 测试类

位置：`TestScanControlIntegration` 之后（约 L1103），覆盖阶段切换的全部新逻辑。需 `from fuscan.gui.main_window import WorkflowStage`（文件顶部已导入 `ScanState`，在同一 import 行追加）。

### 阶段初始化与切换（6 个）

1. **`test_initial_stage_is_setup`** — 新建窗口 `main_stack.currentIndex() == 0`，`_workflow_stage == SETUP`
2. **`test_setup_to_scanning`** — 设置 ruleset + scan_root，调用 `_on_scan()`，断言 `currentIndex() == 1` 且 `_scan_state == RUNNING`
3. **`test_scanning_to_results_on_finish`** — 构造 ScanReport，调用 `_on_scan_finished(report)`，断言 `currentIndex() == 2`
4. **`test_scanning_to_setup_on_fail`** — 调用 `_on_scan_failed("err")`（monkeypatch QMessageBox.critical），断言 `currentIndex() == 0`
5. **`test_results_to_setup_on_rescan`** — 先切到 RESULTS，调用 `_on_rescan()`，断言 `currentIndex() == 0`
6. **`test_setup_to_results_on_view_results`** — 设置 `_last_report`，调用 `_on_view_results()`，断言 `currentIndex() == 2`

### 按钮可见性与可用性（5 个）

7. **`test_view_results_btn_hidden_initially`** — 新建窗口，`_view_results_btn.isVisible() == False`（需 `window.show()`）
8. **`test_view_results_btn_visible_with_report`** — 设置 `_last_report`，调用 `_update_stage_actions()`，`_view_results_btn.isVisible() == True`（需 `window.show()` 且在 SETUP 阶段）
9. **`test_scan_btn_disabled_without_ruleset`** — 新建窗口无规则集，`_scan_btn.isEnabled() == False`
10. **`test_scan_btn_enabled_with_ruleset_and_target`** — 设置 ruleset + scan_root + folder 模式，`_scan_btn.isEnabled() == True`
11. **`test_rescan_btn_disabled_in_setup`** — SETUP 阶段 `_rescan_btn.isEnabled() == False`；切到 RESULTS 后 `_rescan_btn.isEnabled() == True`

### pause_resume_btn 行为（3 个）

12. **`test_pause_resume_btn_text_in_scanning_running`** — 切到 SCANNING + state=RUNNING，`_pause_resume_btn.text() == "暂停扫描"`
13. **`test_pause_resume_btn_text_in_scanning_paused`** — 切到 SCANNING + state=PAUSED，`_pause_resume_btn.text() == "继续扫描"`
14. **`test_on_pause_resume_idle_does_nothing`** — state=IDLE 调用 `_on_pause_resume()`，状态不变（边界保护）

### cancel_btn 行为（2 个）

15. **`test_cancel_scan_with_hits_returns_to_results`** — 构造有 hits 的 ScanReport，调用 `_on_scan_cancelled(report)`，`currentIndex() == 2`
16. **`test_cancel_scan_without_hits_returns_to_setup`** — 构造无 hits 的 ScanReport，`currentIndex() == 0`

### 菜单可用性（4 个）

17. **`test_scan_action_disabled_in_scanning`** — 切到 SCANNING，`_scan_action.isEnabled() == False`
18. **`test_export_actions_disabled_in_setup`** — SETUP 阶段，`_export_csv_action.isEnabled() == False` 且 `_export_json_action.isEnabled() == False`
19. **`test_export_actions_enabled_in_results_with_report`** — 切到 RESULTS + 设置 `_last_report`（有 hits），导出 action 可用
20. **`test_load_edit_rules_actions_disabled_in_results`** — 切到 RESULTS，`_load_rules_action.isEnabled() == False` 且 `_edit_rules_action.isEnabled() == False`

## 实施步骤

### 步骤 1：修改测试文件

按上述清单依次修改 `tests/test_gui.py`：

1. 删除 3 个废弃测试
2. 修改 9 个受影响测试（TestScanControlUI × 5、TestScanControlIntegration × 2、TestConfigPersistence × 1、TestScanCallbacks × 1）
3. 在文件顶部导入行追加 `WorkflowStage`（与 `ScanState` 同行）
4. 在 `TestScanControlIntegration` 后插入 `TestWorkflowStage` 类（20 个测试）

辅助构造：
- 有 hits 的 ScanReport：扫描 `tmp_path` 下含 `secret.txt`（用 `_build_ruleset()` + `Scanner`）
- 无 hits 的 ScanReport：`ScanReport(root=tmp_path, results=(), stats=ScanStats(...), cancelled=True)`
- 切到 SCANNING 阶段测试按钮文本：直接 `window._switch_stage(WorkflowStage.SCANNING)` + `window._scan_state = ScanState.RUNNING` + `window._update_stage_actions()`

### 步骤 2：运行验证

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyrefly check
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96
```

任一项失败则定位根因修复，不放宽断言。

### 步骤 3：Git 提交与推送

```bash
git add src/fuscan/gui/main_window.ui src/fuscan/gui/main_window_ui.py src/fuscan/gui/main_window.py src/fuscan/gui/styles.qss tests/test_gui.py
git commit -m "refactor: GUI 三页整页切换实现工作流阶段化展示

将主窗口从 5区+3tab 重构为 QStackedWidget 三页（配置/扫描中/结果），
按工作流阶段只展示相关界面；扫描历史整合到配置页目标选择区；
独立按钮替代一钮三义；移除视图菜单。同步更新测试覆盖阶段切换逻辑。"
git push
```

## 假设与决策

1. **`_scan_btn.text()` 恒为 "开始扫描"**：新设计中 scan_btn 文本固定，不随状态变化。所有断言 `_scan_btn.text() == "暂停扫描"/"继续扫描"` 的测试必须改为 `_pause_resume_btn`。
2. **`_scan_action.text()` 恒为 "开始扫描"**：menu action 文本不再随扫描状态变化，仅启用/禁用。
3. **`isVisible()` 需要 `window.show()`**：测试按钮可见性时须先 `window.show()` + `qapp.processEvents()`。
4. **splitter 在不可见页面上时 sizes 可能不准确**：`test_splitter_sizes_restored` 需先切到 RESULTS 页。
5. **覆盖率门槛 96%**：新增 20 个测试覆盖新方法（`_switch_stage` / `_update_stage_actions` / `_can_start_scan` / `_on_view_results` / `_on_rescan` / `_on_pause_resume` / `_on_cancel_scan`），覆盖率应不低于上一轮。

## 验证步骤

1. `uv run ruff check src tests` — 0 errors
2. `uv run ruff format --check src tests` — 0 diffs
3. `uv run pyrefly check` — 0 errors（已排除 _ui.py）
4. `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96` — 全部通过，覆盖率 ≥ 96%
5. 手动确认：11 个原失败测试已修复，20 个新测试全部通过
