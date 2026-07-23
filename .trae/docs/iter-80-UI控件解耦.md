# iter-80：UI 控件解耦（MVC 控制器抽取系列）

## 需求清单

- [x] 需求1：使用类设计的方式实现相关列表 MVC 控件的设计，并集成信号槽机制，提高功能内聚
- [x] 需求2：继续解耦其余 UI（从 MainWindow 识别并抽取可独立的 UI 控件类）
- [x] 需求3：继续抽离可以单独设计为类的 UI，并集成信号槽

## 迭代目标

按 rule-12 MVC 分层规则，从 MainWindow（1681 行）中识别并抽取可独立的 UI
控件类为单独的控制器（QObject 子类），通过信号槽解耦控件交互。每个控制器
持有相关 UI 控件引用与状态，主窗口通过公共 API 驱动，不直接操作底层控件。

## 改动文件清单

| 文件 | 改动内容 |
|------|---------|
| `src/fuscan/gui/content_panel.py` | 新增 ContentTabPanel 控制器：封装文件类型树 + 忽略目录 + 忽略扩展名三个 TAB 的全部交互（extractors_changed / textChanged 信号槽、blockSignals 防循环、500ms 节流保存 timer） |
| `src/fuscan/gui/scan_mode_panel.py` | 新增 ScanModePanel 控制器：封装扫描模式 combo + 盘符按钮组 + folder 路径状态（mode_changed 信号、apply_config / save_config / can_start_scan / build_scan_roots / set_folder_root API） |
| `src/fuscan/gui/result_filter_panel.py` | 新增 ResultFilterPanel 控制器：封装路径输入 + 规则筛选 + 分组模式 + 结果树 + 节流 timer（report_getter 回调避免双状态同步、populate / refresh / clear API） |
| `src/fuscan/gui/export_controller.py` | 新增 ExportController 控制器：封装导出流程（格式选择对话框 + 文件保存 + ExportWorker 后台导出 + 结果回调，button_restore_requested 信号解耦按钮状态管理） |
| `src/fuscan/gui/rules_panel.py` | 新增 RulesFilePanel 控制器：封装规则文件列表（刷新 + 上移/下移/移除 + 内置勾选 + 右键菜单，rules_changed 信号触发主窗口重载与持久化） |
| `src/fuscan/gui/main_window.py` | 删除迁出方法（约 200 行），新增 _setup_*_panel / _setup_export_controller 构造方法；_use_builtin / _rules_paths 改为 property 转发到 RulesFilePanel；closeEvent 新增 _export_controller.cleanup()；删除 Qt 导入（不再使用） |
| `tests/test_gui.py` | 约 50 处引用更新：window._on_export_menu → window._export_controller.show_menu；window._on_move_rule_up → window._rules_panel.move_up；window._refresh_rules_file_list → window._rules_panel.refresh；右键菜单 mock 从 main_window.QMenu 改为 rules_panel.QMenu 等 |

## 关键决策与依据

### D1：控制器设计模式选型

**决策**：根据状态归属选择两种模式：

- **panel 持有状态**（ScanModePanel / RulesFilePanel）：状态与 UI 操作高度内聚，
  panel 持有状态后操作方法可直接修改状态 + 刷新 UI + emit 信号
- **panel 无状态 + callback 回调**（ResultFilterPanel / ExportController）：
  状态由主窗口持有，panel 通过 `report_getter` 回调读取，避免双状态同步

**依据**：
- ScanModePanel 的 `_scan_mode` / `_selected_drive` / `_folder_root` 与 UI 操作
  强绑定（模式切换触发目标选择区可见性同步），panel 持有状态最自然
- ResultFilterPanel 的 `_last_report` 由主窗口管理（扫描完成时赋值），panel
  仅读取，callback 回调避免 panel 与主窗口各持一份 report 导致内容漂移

### D2：RulesFilePanel 的 property 转发保持向后兼容

**决策**：主窗口保留 `_use_builtin` / `_rules_paths` 作为 property 转发到
RulesFilePanel，而非直接删除让测试改用 panel API。

**依据**：
- `_use_builtin` / `_rules_paths` 被主窗口 10+ 处方法访问
  （_reload_ruleset / _apply_ruleset_loaded / _on_load_rules / _build_cache_context /
  _apply_config / _save_config 等），property 转发让这些方法无改动
- 测试中 30+ 处直接访问 `window._use_builtin` / `window._rules_paths`，
  property 转发让测试无改动（仅 12 处方法调用改为 panel API）
- 与 ScanModePanel 的 `folder_root` property 风格一致

### D3：rules_changed 信号统一处理重载与持久化

**决策**：RulesFilePanel 的 `rules_changed` 信号触发主窗口 `_on_rules_changed`
槽，统一执行 `_reload_and_refresh()` + `_save_config()`。

**依据**：
- 内置勾选 / 上移 / 下移 / 移除四类操作后都需要重载规则集 + 保存配置，
  统一槽避免 4 处重复代码
- panel 内部已刷新列表显示，主窗口槽不调 `panel.refresh()`，避免重复刷新
- `set_use_builtin` 仅赋值不 emit 信号，供主窗口 `_set_use_builtin` 外部
  主动设置后统一调 `_apply_ruleset_loaded` 刷新（与用户勾选触发的信号槽分离）

### D4：ExportController 的 button_restore_requested 信号

**决策**：ExportController 导出完成/失败后 emit `button_restore_requested`，
主窗口连接到 `_update_stage_actions` 重新计算按钮状态。

**依据**：
- 导出期间按钮禁用由 controller 管理（`export_btn.setEnabled(False)`）
- 非导出期间按钮状态由主窗口 `_update_stage_actions` 统一管理
  （基于 workflow_stage 与 has_report）
- 信号解耦避免 controller 反向调用主窗口方法

## 代码实现情况

### 已抽取的 8 个控制器

| 控制器 | 职责 | 信号 | commit |
|--------|------|------|--------|
| DetailPanel | 详情区状态、填充、导航、文件操作 | path_copy_requested / open_location_requested / move_to_staging_requested / toggle_skip_requested | （既有） |
| ScanPathHistory | 扫描路径历史去重 + 最近优先 + 限量 | — | （既有） |
| ScanListUpdater | 扫描中列表 0.5s 节流 + 增量 append | — | （既有） |
| ContentTabPanel | 文件类型树 + 忽略目录 + 忽略扩展名 | extractors_changed | 6a4b6f9 |
| ScanModePanel | 扫描模式 combo + 盘符按钮组 + folder 路径 | mode_changed | 0040247 |
| ResultFilterPanel | 结果树筛选 + 节流 timer + 刷新 | — | 7f9f594 |
| ExportController | 导出流程（格式选择 + 文件保存 + 后台导出） | button_restore_requested | 1d20a85 |
| RulesFilePanel | 规则文件列表（刷新 + 上移/下移/移除 + 内置勾选 + 右键菜单） | rules_changed | 7f947b8 |

### main_window.py 规模变化

- 抽离前：1681 行
- 抽离后：1634 行（减少 47 行；删除迁出方法约 200 行，新增 property 转发 +
  _setup_*_panel + _on_rules_changed 等约 150 行）

## 测试验证结果

- ruff check：All checks passed
- ruff format --check：101 files already formatted
- pyrefly check：0 errors
- pytest：1564 passed, 16 deselected, coverage 95.09%

## 遗留事项

- 剩余模块（工作流阶段切换 7 方法、扫描流程 12 方法、详情区回调 9 方法）与
  主窗口状态（_workflow_stage / _scan_state / _worker / _ruleset / _last_report）
  耦合过深，抽离需要传入大量状态或 callback，边际收益递减，暂不抽离
- 扫描统计面板（_setup_scan_stats_panel / _update_scan_stats）仅 2 个方法、
  HTML 富文本格式化逻辑独立但规模太小，抽离价值有限

## 下一轮计划

UI 控件解耦系列已完成 8 个控制器抽取，剩余模块抽离收益有限。后续如有新功能
开发，优先将新 UI 控件设计为独立控制器，避免 MainWindow 再次膨胀。
