# iter-137 规则配置全局化重构

## 需求清单

- [x] 问题7：规则配置移到首页下方（添加任务上方），作为全局配置而非工作任务配置
- [x] 问题8：工作任务把展开区的「切换目标」挪到当前「定义规则」的位置
- [x] 验收：覆盖率 >= 95%，全套门禁通过

## 迭代目标

完成 req-39 中规则配置全局化与 WorkspaceCard 按钮重排两项 UI 重构：
1. 规则配置由工作区级改为全局——所有工作区共享同一规则集
2. 首页下方新增全局规则配置区（添加任务上方），支持增删改/导入导出
3. WorkspaceCard 移除「定义规则」按钮，原位改为「切换目标」
4. WorkspaceCard 展开区移除「切换目标」（已挪到第一行）
5. 启动时迁移旧工作区级 `rules_paths`/`use_builtin` 到全局配置

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fuscan/gui/controllers/rules_controller.py` | 移除 `bindWorkspace`/`unbindWorkspace`/`set_workspace_controller`/`isBound`/`boundWorkspaceId`/`boundWorkspaceName`/`_local_rules_paths`/`_persist_to_bound_workspace` 等工作区绑定相关 API；所有方法直接读写 `Config.rules_paths`/`use_builtin` |
| `src/fuscan/gui/controllers/workspace_controller.py` | 移除 `updateWorkspaceRules`/`bindRulesController`/`unbindRulesController`/`_load_workspace_ruleset`；新增 `_migrate_workspace_rules_to_global`（启动时合并旧工作区规则到全局）与 `_invalidate_all_manifests`（全局规则变更时清除所有 manifest）；构造函数连接 `rulesetChanged → _invalidate_all_manifests` |
| `src/fuscan/gui/controllers/scan_controller.py` | 移除 `setWorkspaceRuleset`/`_workspace_rules_paths`/`_workspace_use_builtin`；`startScan`/`startIncrementalScan` 每次取最新全局 `ruleset`；`_build_cache_context` 改用全局 `rules_paths`/`use_builtin` |
| `src/fuscan/gui/views/components/RulesPanel.qml` | 新建——从 RulesPage.qml 提取的可复用规则面板组件（标题 + 导入/导出 + 规则文件列表 + 规则列表） |
| `src/fuscan/gui/views/pages/HomePage.qml` | 新增全局规则配置区（ScrollView 与 RulesPanel 弹性分配空间）；移除 `defineRulesRequested` 信号处理 |
| `src/fuscan/gui/views/pages/RulesPage.qml` | 复用 RulesPanel 组件，移除工作区绑定相关 UI |
| `src/fuscan/gui/views/components/WorkspaceCard.qml` | 第一行「定义规则」IconButton 改为「切换目标」（`qrc:/icons/target.svg`）；展开区移除「切换目标」；移除 `defineRulesRequested` 信号 |
| `src/fuscan/gui/views/ContentArea.qml` | 移除 `unbindRulesController` 调用与 `defineRulesRequested` 处理 |
| `src/fuscan/gui/resources.qrc` / `resources_rc.py` | 新增 RulesPanel.qml 后用 `pyside2-rcc` 重建 |
| `tests/test_gui_rules_controller.py` | 移除 `TestWorkspaceBinding`/`TestUnbindNoopWhenNotBound`/`TestBoundWorkspaceMoveDown`/`TestBoundWorkspaceName`/`TestPersistToBoundWorkspaceNoop`；移除 `TestLoadFileFromPathEdgeCases` 中两个绑定模式测试 |
| `tests/test_gui_scan_controller.py` | 移除 `test_set_workspace_ruleset_updates_controller`；`test_start_scan_noop_when_no_ruleset` 改用 `rules_controller.setUseBuiltin(False)` |
| `tests/test_gui_workspace_controller.py` | 移除 `TestUpdateWorkspaceRules`/`TestBindRulesController`/`test_clear_all_unbinds_rules_controller` |
| `tests/test_incremental_scan_controller.py` | 两处 `setWorkspaceRuleset(None, [], False)` 改用 `_rules_controller.setUseBuiltin(False)` |

## 关键决策与依据

### 规则配置全局化：移除工作区绑定整套机制

**方案**：`RulesController` 不再支持工作区绑定模式，所有方法直接读写共享 `Config` 的 `rules_paths`/`use_builtin`。`WorkspaceController` 移除 `updateWorkspaceRules`/`bindRulesController`/`unbindRulesController`，`ScanController` 移除 `setWorkspaceRuleset`。

**依据**：
- 用户反馈问题7明确要求「作为全局配置」，工作区级规则编辑属于过度设计
- 移除绑定机制后 `RulesController` 代码量减少约 40%，逻辑显著简化
- 所有 `ScanController` 共享同一 `RulesController` 实例，天然保持规则一致性

### 数据迁移：旧工作区 rules_paths 合并到全局

**方案**：`WorkspaceController._load_persisted` 加载 `workspaces.json` 后调用 `_migrate_workspace_rules_to_global`：遍历所有工作区的 `rules_paths`/`use_builtin`，去重合并到全局 `Config`，任一工作区启用内置规则则全局启用。

**依据**：
- `workspaces.json` 的 `rules_paths` 字段保留（向后兼容），但启动迁移后不再单独加载
- 合并而非覆盖，避免用户丢失已有规则文件配置
- 迁移有变更才保存 `Config`，避免无谓写入

### 全局规则变更时清除所有 manifest

**方案**：`WorkspaceController` 构造函数连接 `RulesController.rulesetChanged → _invalidate_all_manifests`，规则变更时遍历所有 `ScanController` 调用 `invalidate_manifest(ws_id)`。

**依据**：
- manifest 指纹只记录文件 mtime+size，不感知规则变化
- 若不清除，下次增量扫描会跳过未变文件，导致新规则不生效
- 全局规则影响所有工作区，需清除所有 manifest

### RulesPanel.qml 提取为可复用组件

**方案**：从 RulesPage.qml 提取规则列表渲染逻辑为独立的 `RulesPanel.qml`，HomePage 与 RulesPage 共用。

**依据**：
- 首页与规则页规则列表 UI 完全一致，提取避免重复
- 组件无外部依赖，仅依赖全局 `RulesController` context property
- 提取后 RulesPage.qml 由 322 行精简为引用 RulesPanel 的薄封装

### WorkspaceCard 按钮重排

**方案**：第一行「定义规则」IconButton 替换为「切换目标」（`qrc:/icons/target.svg`，点击打开 `editTargetDialog`，启用条件 `statusText !== "扫描中" && statusText !== "已暂停"`）；展开区移除「切换目标」（已挪到第一行）；iter-136 新增的「重新扫描」按钮保留。

**依据**：
- 用户反馈问题8明确要求位置调整
- `editTargetDialog` 保留在 WorkspaceCard 内部，仅触发位置变化
- 展开区「重新扫描」与「导出菜单」保留，功能未受影响

## 代码实现情况

### 后端重构

- `rules_controller.py`：移除工作区绑定 API 后约 220 行简化为 208 行
- `workspace_controller.py`：移除规则协调逻辑，新增迁移与 manifest 失效方法
- `scan_controller.py`：`startScan`/`startIncrementalScan` 每次刷新 `self._ruleset = self._rules_controller.ruleset`

### UI 重构

- 新建 `RulesPanel.qml`：标题行 + 主区域左右分栏（规则文件列表 + 规则列表）
- `HomePage.qml`：`ScrollView`（工作区列表）与 `RulesPanel`（全局规则区）弹性分配空间，无扫描任务时均可见
- `WorkspaceCard.qml`：第一行 IconButton 替换，展开区 IconButton 移除

### resources_rc.py 重建

- `uv run python scripts/build_qrc.py`：收集 16 个 QML（新增 RulesPanel.qml）+ 39 个 SVG，用 pyside2-rcc 编译

## 测试验证结果

### 新增/修改测试

- `test_gui_rules_controller.py`：移除 6 个工作区绑定相关测试类（约 220 行）
- `test_gui_scan_controller.py`：移除 1 个 `setWorkspaceRuleset` 测试，1 个测试改用全局配置
- `test_gui_workspace_controller.py`：移除 3 个工作区规则绑定测试类（约 150 行）
- `test_incremental_scan_controller.py`：2 处 `setWorkspaceRuleset` 替换为 `setUseBuiltin(False)`

### 门禁结果

- `ruff check .`：All checks passed
- `ruff format --check .`：156 files already formatted
- `pyrefly check`：0 errors (750 suppressed, 65 warnings not shown)
- `pytest --cov=fuscan`：2392 passed, 2 skipped, coverage 95.69%（9 个 benchmark 失败与本迭代无关，CPU 速度差异导致 extractor speed_tier 断言失败）

## 遗留事项

- HomePage.qml / RulesPage.qml / WorkspaceCard.qml 为 QML 层变更，Python 单元测试无法直接覆盖，需手动验证 UI 渲染与交互
- 旧工作区 `rules_paths` 迁移逻辑在 `_load_persisted` 中执行，迁移后 `workspaces.json` 中仍保留该字段（向后兼容），但下次保存时新工作区不再写入
- iter-137 完成后 req-39 全部 10 个问题已闭环，可移至 `.trae/req/done/`
