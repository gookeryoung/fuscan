# iter-100 扫描中隐藏其他工作区与规则列表修复

## 需求清单

- [x] 修复规则文件列表的上下移动和移除按钮无法操作
- [x] 扫描中（含暂停态）隐藏其余工作区，显示扫描进度面板
- [x] 暂停过程保留扫描进度面板
- [x] 扫描结束后恢复显示所有工作区列表
- [x] 全套门禁通过

## 迭代目标

修复 RulesPage 规则文件列表点击无响应的缺陷，并实现扫描中聚焦当前任务的 UX：
扫描进行/暂停期间隐藏其他工作区，仅展示扫描进度数据；扫描结束自动恢复。

## 改动文件清单

### 新增

- `src/fuscan/gui/views/components/ScanProgressCard.qml`
  - 扫描进度卡片组件：任务名+状态徽标、模式/目标元数据、当前文件、
    ProgressBar 进度条（支持 indeterminate 统计阶段）、通过/命中/跳过/错误计数、
    状态摘要、暂停/继续 + 取消按钮
  - 状态色与文本依据 `scanController.isPaused` 切换（扫描中=warning 黄，已暂停=text secondary）
  - ProgressBar 用 id 引用 `visualPosition`，避免 attached property 在
    contentItem 子项中识别失败

### 修改

- `src/fuscan/gui/views/pages/RulesPage.qml`
  - 规则文件列表 delegate 添加 `onClicked: rulesFileList.currentIndex = index`
  - 原因：ItemDelegate 在 Qt Quick Controls 2 中不会自动设置 ListView.currentIndex，
    需在 onClicked 显式同步。原代码仅靠 `currentIndex: rulesController.selectedFileIndex`
    绑定 + `onCurrentIndexChanged` 回写，但点击 delegate 不触发 currentIndex 变化，
    导致 `setSelectedFileIndex` 永不被调用，canMoveUp/canMoveDown/canRemove 始终 False，
    上移/下移/移除按钮一直禁用

- `src/fuscan/gui/controllers/workspace_controller.py`
  - 新增 `activeScanChanged` 信号
  - 新增 `_active_scan_workspace_id` 内部字段
  - 新增 `activeScanWorkspaceId`/`hasActiveScan`/`activeScanController` Property
  - 新增 `activeScanWorkspaceName`/`activeScanModeText`/`activeScanTarget` 便捷 Property
    供 ScanProgressCard 展示工作区元数据
  - `_sync_workspace_state` 中根据 `scanState == "scanning"` 维护 active ID：
    进入扫描态设置 active，离开（results/setup）清空；暂停（isPaused=True）
    仍属 scanning 态，保留 active
  - `removeWorkspace` 与 `cleanup` 中新增 active 状态清空逻辑

- `src/fuscan/gui/views/pages/HomePage.qml`
  - 根据 `workspaceController.hasActiveScan` 切换显示：
    - true：显示 ScanProgressCard 居中布局，隐藏工作区列表
    - false：显示工作区列表（原行为）
  - 标题区文案随状态切换（「扫描中」/「工作区」）
  - 任务计数在扫描中替换为「扫描进行中...」避免信息冗余

- `pyproject.toml`
  - `[tool.hatch.build.targets.wheel.force-include]` 新增 ScanProgressCard.qml 声明

- `tests/test_gui_workspace_controller.py`
  - 新增 `TestActiveScan` 测试类（13 个用例）：
    初始无 active、进入 scanning 设置 active、暂停保留 active、
    results/setup 清空 active、activeScanController 返回正确实例、
    元数据 Property 正确、信号发射时机、删除/cleanup 清空 active

## 关键决策与依据

1. **ItemDelegate 默认不设置 ListView.currentIndex**：Qt Quick Controls 2 的
   ItemDelegate 是 AbstractButton 子类，点击仅发射 clicked 信号，不会自动同步
   ListView.currentIndex。这是为灵活性设计的（让开发者决定是否需要点击选中）。
   显式 `onClicked: ListView.view.currentIndex = index` 是标准修复方式。

2. **active 状态基于 scanState 而非 statusText**：`statusText` 是 i18n 后的中文
   字符串（"扫描中"/"已暂停"/"已完成"...），匹配字符串脆弱且依赖文本一致性。
   `scanState` 是稳定的状态枚举（"setup"/"scanning"/"results"），暂停仍属 scanning
   态（通过 `isPaused` 区分），语义清晰且无歧义。

3. **activeScanController 返回 fallback 而非 None**：与 `currentScanController`
   同策略，避免 QML 绑定 null 报错。HomePage 的 ScanProgressCard 在
   `hasActiveScan=false` 时不可见，但 QML binding 仍会求值，需保证返回 QObject。

4. **HomePage 用两个 ScrollView 而非 StackView**：扫描进度面板与工作区列表
   互斥显示，用 `visible` 切换比 StackView 更轻量（无页面切换动画开销），
   且 HomePage 不负责页面导航（由 ContentArea.StackView 处理）。

5. **不实施：扫描中允许添加新任务**：用户需求是「扫描中隐藏其他工作区」，
   隐含一次只关注一个扫描任务。添加任务通过 Sidebar 的「添加任务」入口，
   与 HomePage 显示无关，无需特殊处理。

## 代码实现情况

- P0 缺陷（规则列表点击无响应）与 P1 功能（扫描中聚焦视图）全部实现
- ScanProgressCard.qml 完整展示扫描进度数据，支持暂停/继续/取消操作
- WorkspaceController active scan 状态机覆盖扫描中/暂停/完成/取消/失败全路径
- 13 个新增测试覆盖 active 状态生命周期、信号发射、属性返回、清理逻辑

## 测试验证结果

- `uv run ruff check src tests`：通过
- `uv run ruff format --check`（修改文件）：通过
  （注：仓库存在历史遗留格式问题，非本次改动引入）
- `uv run pyrefly check`：0 errors
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：
  1540 passed（新增 13 个），覆盖率 95.72%

## 遗留事项

- 无

## 下一轮计划

- 无（待用户反馈或新需求）
