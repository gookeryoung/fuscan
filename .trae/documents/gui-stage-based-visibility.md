# GUI 第三阶段重构：基于工作流阶段的整页切换

## 摘要

将主窗口从"5区布局+3 tab"重构为"QStackedWidget 三页整页切换"，按工作流阶段（配置/扫描中/结果）只展示相关界面。扫描历史整合到配置页的目标选择区，移除独立的 tab_widget 和视图菜单，用独立按钮处理各阶段操作。

## 现状分析

### 当前结构（main_window.ui）

```
QMainWindow
└─ central (QVBoxLayout)
   ├─ control_card: scan_mode_combo + target_stack + load_rules_btn + scan_btn
   ├─ splitter (Horizontal)
   │  ├─ list_area → tab_widget (3 tabs: 结果/规则/历史)
   │  └─ detail_area → detail_action_stack + detail_main_stack
   └─ progress
```

### 关键问题

- 3 个 tab 始终可见，不区分阶段
- detail_area 始终可见（仅空态/非空态切换）
- scan_btn 一钮三义（开始/暂停/继续），状态机复杂
- 扫描历史与扫描目标选择分散在不同 tab，关联性弱

## 设计决策

### 1. 整页切换（QStackedWidget）

新增 `WorkflowStage` 枚举（SETUP/SCANNING/RESULTS），`main_stack` 三页切换。每阶段只展示相关界面。

### 2. 扫描历史整合

移除独立的"扫描历史"tab，将 `history_list` 移入配置页的 `target_group`（目标选择区下方），与扫描模式+目标选择视觉归组。

### 3. 独立按钮方案

放弃"一钮三义"，改为独立按钮各司其职：

| 按钮 | 所在页面 | 文本 | 行为 |
|------|---------|------|------|
| scan_btn | setup_page | "开始扫描"（固定） | SETUP → SCANNING |
| view_results_btn | setup_page | "查看结果" | SETUP → RESULTS（仅有结果时可见） |
| pause_resume_btn | scanning_page | "暂停扫描"/"继续扫描" | 暂停/恢复 |
| cancel_btn | scanning_page | "取消扫描" | 取消扫描 |
| rescan_btn | results_page | "重新扫描" | RESULTS → SETUP |
| export_btn | results_page | "导出结果" | 导出（逻辑不变） |

### 4. 菜单栏精简

- 删除视图菜单（view_menu）及其3个 action（view_results/rules/history_action）
- 其他菜单按阶段启用/禁用：加载规则/编辑规则仅在 SETUP；导出仅在 RESULTS

## 新 .ui 结构

```
QMainWindow
└─ central (QVBoxLayout)
   └─ main_stack (QStackedWidget, currentIndex=0)
      ├─ setup_page (Page 0)
      │  ├─ target_group (QGroupBox "扫描目标")
      │  │  ├─ scan_mode_layout: scan_mode_combo + target_stack
      │  │  ├─ history_label
      │  │  └─ history_list
      │  ├─ rules_group (QGroupBox "规则配置")
      │  │  ├─ rules_btn_row: load_rules_btn + edit_rule_btn
      │  │  ├─ rules_file_label
      │  │  ├─ rules_file_list
      │  │  └─ rules_tree
      │  └─ setup_btn_row: spacer + view_results_btn + scan_btn
      ├─ scanning_page (Page 1)
      │  ├─ stretch
      │  ├─ scanning_title_label ("扫描进行中")
      │  ├─ progress
      │  ├─ current_file_label
      │  ├─ scanning_btn_row: pause_resume_btn + cancel_btn
      │  └─ stretch
      └─ results_page (Page 2)
         ├─ results_top_bar: rescan_btn + spacer + export_btn
         └─ results_splitter (Horizontal)
            ├─ results_list_area: filter_bar + result_tree
            └─ detail_area: detail_action_stack + detail_main_stack（含 note_edit）
```

控件 objectName 保持不变（result_tree、rules_file_list、rules_tree、history_list、detail_* 等），仅重组层级。`splitter` 重命名为 `results_splitter`。`export_btn` 从 detail_nonempty_main 移到 results_top_bar。`current_file_label` 从代码创建改为 .ui 定义。

## 实施步骤

### 步骤 1：重写 main_window.ui

按新结构完整重写 XML。注意事项：
- 保持所有保留控件的 objectName 完全一致
- detail_action_stack / detail_main_stack 的子控件和属性完整搬移
- 移除 view_menu 及 view_results_action / view_rules_action / view_history_action
- 移除 detail_export_row（export_btn 移到 results_top_bar）
- 新增 main_stack / setup_page / scanning_page / results_page / target_group / rules_group / view_results_btn / pause_resume_btn / cancel_btn / rescan_btn / scanning_title_label

### 步骤 2：重新编译 main_window_ui.py

```bash
pyside2-uic src/fuscan/gui/main_window.ui -o src/fuscan/gui/main_window_ui.py
```

验证生成的文件包含所有预期控件。

### 步骤 3：更新 main_window.py

#### 3a. 新增 WorkflowStage 枚举

```python
class WorkflowStage(enum.Enum):
    """工作流阶段。"""
    SETUP = "setup"
    SCANNING = "scanning"
    RESULTS = "results"
```

在 `__init__` 中新增 `self._workflow_stage: WorkflowStage = WorkflowStage.SETUP`。

#### 3b. 更新 _bind_widgets

新增绑定：main_stack、view_results_btn、pause_resume_btn、cancel_btn、rescan_btn、scanning_title_label、current_file_label（从 ui 绑定，移除代码创建逻辑）

移除绑定：tab_widget、view_results_action、view_history_action

重命名：`self._splitter = ui.results_splitter`

#### 3c. 更新 _configure_ui

移除：tab_widget.setTabIcon、view_history_action.setIcon、view_results/rules/history_action 信号槽连接、旧 layout stretch 设置

新增：main_stack 初始页设置、view_results_btn 初始不可见、新按钮信号槽连接、新页面 layout stretch

#### 3d. 新增阶段切换方法

- `_switch_stage(stage)`: 切换 main_stack 页面并调用 _update_stage_actions
- `_update_stage_actions()`: 根据阶段和扫描状态更新所有按钮和菜单可用性
- `_can_start_scan() -> bool`: 判断是否满足开始扫描条件
- `_on_view_results()`: SETUP → RESULTS（仅有结果时）
- `_on_rescan()`: RESULTS → SETUP
- `_on_pause_resume()`: 暂停/继续扫描
- `_on_cancel_scan()`: 取消扫描

#### 3e. 更新现有方法

- `_on_scan`: 简化为仅处理 SETUP 阶段开始扫描，扫描前 `_switch_stage(SCANNING)`
- `_pause_scan` / `_resume_scan`: 操作 pause_resume_btn 而非 scan_btn
- `_reset_scan_ui`: 重置 pause_resume_btn 文本，调用 _update_stage_actions
- `_on_scan_finished`: 新增 `_switch_stage(RESULTS)`
- `_on_scan_failed`: 新增 `_switch_stage(SETUP)`
- `_on_scan_cancelled`: 有结果切 RESULTS，无结果切 SETUP
- `_on_history_item_double_clicked`: 移除 `_switch_tab(0)`，改为 `_update_stage_actions()`
- `_update_scan_button`: 简化为委托 `_update_stage_actions()`

#### 3f. 移除的方法

- `_switch_tab(index)` — 不再需要
- `_on_view_history()` — 不再需要
- `_set_scan_controls_text(text)` — 不再需要
- `_update_scan_button_icon()` — 不再需要

#### 3g. 更新 __all__

新增 `WorkflowStage`。

### 步骤 4：更新 styles.qss

- 移除 QTabWidget/QTabBar 样式（可选，保留不影响）
- 新增 QStackedWidget#main_stack 背景色（可选）
- 确认 QGroupBox 样式适用于 target_group / rules_group
- 新增按钮样式（rescan_btn / view_results_btn / pause_resume_btn / cancel_btn，可复用 export_btn 样式）

### 步骤 5：更新 tests/test_gui.py

#### 受影响需修改的测试（约15个）

- `TestExportAndMenu.test_switch_tab` — 删除或改为测试 _switch_stage
- `TestExportAndMenu.test_on_view_history` — 删除
- `TestExportAndMenu.test_history_item_double_clicked` — 移除 tab 切换断言
- `TestScanControlUI`（8个）— 改为断言 pause_resume_btn 文本而非 scan_btn
- `TestScanControlIntegration.test_scan_completes_through_main_window` — 断言 main_stack.currentIndex()
- `TestScanControlIntegration.test_scan_cancel_through_main_window` — 移除 scan_btn 文本断言
- `TestConfigPersistence.test_splitter_sizes_restored` — 先切到 results_page 再验证
- `TestScanCallbacks.test_pause_resume_scan` — 改为断言 pause_resume_btn 文本

#### 新增测试（约20个，TestWorkflowStage 类）

- 阶段初始化与切换：test_initial_stage_is_setup / test_setup_to_scanning / test_scanning_to_results / test_scanning_to_setup_on_fail / test_results_to_setup_on_rescan / test_setup_to_results_on_view_results
- 按钮可见性：test_view_results_btn_hidden_initially / test_view_results_btn_visible_after_scan / test_view_results_disabled_without_report
- pause_resume_btn 行为：test_pause_resume_btn_text_running / test_pause_resume_btn_text_paused / test_pause_resume_toggles_state
- cancel_btn 行为：test_cancel_scan_returns_to_setup
- 菜单可用性：test_scan_action_disabled_in_scanning / test_export_actions_disabled_in_setup / test_export_actions_enabled_in_results / test_menu_actions_enabled_by_stage
- 历史双击：test_history_double_click_stays_in_setup

### 步骤 6：验证

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyrefly check
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96
```

## 风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| pyside2-uic 重新编译遗漏属性 | 中 | 编译后对比新旧 _ui.py，确认控件 objectName 一致 |
| 约15个测试需更新 | 高 | 逐一修改，修改后立即运行验证；新增20个测试覆盖新代码 |
| splitter sizes 恢复 | 中 | Qt 允许对不可见控件设置 sizes，待 results_page 显示时生效；测试先切到 results_page |
| QSS 样式适配 | 低 | 移除 tab 样式，新增按钮样式复用现有 |
| pyrefly 类型检查 | 低 | _ui.py 已排除；新增方法添加完整类型注解 |

## 验收标准

1. main_stack 三页切换正常：SETUP → SCANNING → RESULTS，各阶段只展示相关界面
2. 扫描历史列表在配置页目标选择区下方，双击可设置路径
3. 扫描中页显示进度条和当前文件，暂停/继续/取消按钮正常工作
4. 结果页显示结果列表+详情区，导出按钮在顶部工具栏
5. 菜单栏移除视图菜单，其他菜单项按阶段正确启用/禁用
6. 配置持久化（窗口几何、splitter sizes、扫描模式、规则路径、历史路径）正常
7. ruff check + ruff format --check + pyrefly check 全部通过
8. pytest --cov=fuscan --cov-fail-under=96 通过，覆盖率不低于 96%

## 关键文件

- `f:\Dev\fuscan\src\fuscan\gui\main_window.ui` — 完整重写为3页 QStackedWidget 结构
- `f:\Dev\fuscan\src\fuscan\gui\main_window_ui.py` — pyside2-uic 重新编译生成
- `f:\Dev\fuscan\src\fuscan\gui\main_window.py` — 新增 WorkflowStage、阶段切换逻辑、更新扫描回调
- `f:\Dev\fuscan\tests\test_gui.py` — 修改约15个受影响测试，新增约20个阶段切换测试
- `f:\Dev\fuscan\src\fuscan\gui\styles.qss` — 适配新页面结构的样式
