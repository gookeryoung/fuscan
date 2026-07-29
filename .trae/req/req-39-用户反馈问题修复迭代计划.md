# req-39 用户反馈问题修复迭代计划

## 概述

基于用户实际使用反馈，针对 10 个问题制定 3 轮迭代计划（iter-135 ~ iter-137）。
原 req-38 中 iter-135 ~ iter-142 的计划顺延为 iter-138 ~ iter-145，本计划优先解决用户反馈。

问题分类：
- **Bug 修复**（优先级高）：文件删除未更新、压缩包扫描卡死、扫描中切换设置卡顿
- **UI 调整与功能补全**（独立小项）：设置 Tab 顺序、取消模板、XML 标签、PDF 导出、重新扫描按钮
- **UI 重构**（影响较大）：规则配置全局化、工作任务区结构调整

每轮迭代遵循 rule-01 六步闭环，全套门禁（ruff/pyrefly/pytest/coverage 95%）通过为验收硬性条件。

---

## 问题清单与归属

| 编号 | 问题 | 归属迭代 | 类型 |
|------|------|---------|------|
| 1 | 同一任务下，文件被删除了，但信息没有更新 | iter-135 | Bug |
| 2 | 展开区「切换目标」右侧增加「重新扫描」功能 | iter-136 | 功能补全 |
| 3 | 压缩文件扫描极慢，扫描任务始终无法结束 | iter-135 | Bug |
| 4 | PDF 报告生成没有实现（GUI 未接通） | iter-136 | 功能补全 |
| 5 | 设置中扫描页面放在通用前面 | iter-136 | UI 调整 |
| 6 | 定义规则页面，取消模板功能 | iter-136 | UI 调整 |
| 7 | 规则配置移到首页下方（添加任务上方），作为全局配置 | iter-137 | UI 重构 |
| 8 | 工作任务把展开区的「切换目标」挪到当前「定义规则」的位置 | iter-137 | UI 重构 |
| 9 | XML 等通用文件类型没有标注在文件类型标签中 | iter-136 | 功能补全 |
| 10 | 扫描过程切换设置出现明显卡顿 | iter-135 | Bug |

---

## iter-135 扫描数据一致性与性能修复

### 需求

- [x] 问题1：同一任务下，文件被删除后扫描结果未更新（增量扫描未清理已删除文件的结果）
- [x] 问题3：压缩文件扫描极慢，扫描任务始终无法结束（死循环或无超时）
- [x] 问题10：扫描过程中切换到设置页出现明显卡顿

### 验收标准

1. 增量扫描后，已删除文件的结果从结果列表中移除（重启后也不再现）
2. 压缩包扫描具备超时与条目数上限保护，单压缩包扫描时间 < 60s（1000 条目以内）
3. 压缩包扫描过程中可被取消（cancel 信号 1s 内生效）
4. 扫描中切换设置页无明显卡顿（界面响应 < 200ms）
5. 覆盖率不低于 95%，新增 bug 修复测试用例

### 技术方案

#### 问题1：文件删除后结果未更新

**根因分析**：
- 增量扫描（`startIncrementalScan`）基于 manifest 检测文件变更（mtime + size），
  但未检测文件删除
- 已删除文件的结果仍保留在 `ScanReport.results` 与缓存中

**修复方案**（`src/fuscan/scanner/scanner.py`）：
- 增量扫描完成后，对比 manifest 中的旧文件路径集合与当前扫描到的文件路径集合
- 差集（旧有新无）即为已删除文件，从 `ScanReport.results` 中移除对应条目
- 同步更新缓存（`CacheStore` 删除已删除文件的记录）
- 持久化结果 JSON（`~/.fuscan/results/<ws_id>.json`）同步更新

**关键代码位置**：
- `src/fuscan/scanner/scanner.py`：`_incremental_scan` / `scan_incremental` 方法
- `src/fuscan/scanner/result.py`：`ScanReport` 新增 `remove_deleted(paths_to_remove)` 方法
- `src/fuscan/gui/controllers/scan_controller.py`：增量扫描完成回调中触发清理

#### 问题3：压缩包扫描卡死

**根因分析**（需运行时确认，可能原因）：
- 大压缩包条目数过多（如 10w+ 条目），单线程遍历阻塞
- 加密条目密码错误时无限重试（`SevenZReader` 已有限制，但 `ZipReader`/`RarReader` 可能未覆盖）
- 嵌套压缩包递归扫描（虽然 `ArchiveScanner` 注释说不递归，但需确认）
- `extract_content_from_bytes_with_retry` 对损坏文件重试耗时
- 压缩包扫描在主扫描线程池中执行，占用 worker 导致整体停滞

**修复方案**：
- `src/fuscan/archive/scanner.py`：
  - 新增 `max_entries` 参数（默认 5000），超过时记录 warning 并截断
  - 单条目提取增加超时保护（`_read_entry_content` 包装 timeout）
  - 条目遍历过程中检查 cancel 信号，及时退出
- `src/fuscan/archive/base.py`：`ArchiveReader` 接口新增 `entry_count` 属性，预先评估
- `src/fuscan/scanner/scanner.py`：
  - 压缩包扫描作为独立 phase，限制并发压缩包数量（避免多个大压缩包同时扫描）
  - 压缩包扫描进度上报到 `ScanProgress`（含 `archive_entries_scanned` 字段）
- `src/fuscan/workers/scan_worker.py`：cancel 信号传递到 `ArchiveScanner`

**关键代码位置**：
- `src/fuscan/archive/scanner.py`：`scan_archive` / `_scan_entry`
- `src/fuscan/archive/sevenz_reader.py` / `rar_reader.py` / `zip_reader.py`：超时与重试限制
- `src/fuscan/scanner/_archive_phase.py`：压缩包扫描调度

#### 问题10：扫描中切换设置卡顿

**根因分析**（需运行时确认，可能原因）：
- `SettingsPage.qml` 加载时 `ExtractorListModel` 重建（`load_from_registry` 耗时）
- 扫描进度信号高频更新（10fps）导致主线程繁忙，页面切换渲染被阻塞
- `ConfigController` 的 `@Property` 频繁变更触发 QML 绑定重算
- 设置页 `ListView` 的 `cacheBuffer` 过大导致内存压力

**修复方案**：
- `src/fuscan/gui/views/pages/SettingsPage.qml`：
  - 页面首次加载后才初始化重型组件（`Loader.active` 延迟加载）
  - `ExtractorListModel` 全局共享单例（避免每次进设置页重建）
- `src/fuscan/gui/controllers/config_controller.py`：
  - 扫描中冻结非关键配置信号（`@Property` notify 抑制）
  - 批量更新信号合并（`QTimer.singleShot` 节流）
- `src/fuscan/gui/controllers/scan_controller.py`：
  - 进度信号节流确认（10fps 已实现，审计是否有额外信号泛滥）
- `src/fuscan/gui/views/pages/SettingsPage.qml`：
  - `ListView.cacheBuffer` 动态调整（参考 iter-131 ResultsPage 模式）

**关键代码位置**：
- `src/fuscan/gui/views/pages/SettingsPage.qml`：Tab 内容加载策略
- `src/fuscan/gui/controllers/config_controller.py`：信号节流
- `src/fuscan/gui/models/extractor_model.py`：单例化

### 依赖

无（独立于其他迭代，bug 修复优先）

---

## iter-136 UI 调整与功能补全

### 需求

- [x] 问题2：展开区「切换目标」右侧增加「重新扫描」按钮（全量扫描，非增量）
- [x] 问题4：PDF 报告生成接通 GUI（WorkspaceCard 导出菜单 + ResultsPage 导出按钮）
- [x] 问题5：设置 Tab 顺序调整，扫描页放在通用前面
- [x] 问题6：定义规则页面取消模板功能（移除模板按钮与对话框）
- [x] 问题9：XML 等通用文件类型标注在文件类型标签中

### 验收标准

1. WorkspaceCard 展开区「切换目标」右侧有「重新扫描」按钮，点击触发全量扫描（非增量）
2. 导出菜单包含 PDF 选项，生成的 PDF 含扫描统计与命中文件表格（中文正常显示）
3. 设置页 Tab 顺序为「扫描、通用、忽略目录、白名单」，默认显示扫描页
4. 规则页无「模板」按钮与模板对话框，模板相关后端代码清理
5. 文件类型标签中 XML/JSON/JS/SH 等通用类型可见（SourceCodeExtractor 的 format_tags 扩展）
6. 覆盖率不低于 95%

### 技术方案

#### 问题2：展开区增加重新扫描按钮

**修改**（`src/fuscan/gui/views/components/WorkspaceCard.qml`）：
- 展开区 RowLayout 中，「切换目标」按钮右侧新增「重新扫描」IconButton
- 图标：`qrc:/icons/rescan.svg`
- 点击触发 `workspaceController.startScan(card.workspaceId)`（全量扫描，非增量）
- 启用条件：`card.isCompletedState()`（与增量扫描按钮一致）
- 新增信号：`rescanRequested(string workspaceId)`

**关键代码位置**：
- `src/fuscan/gui/views/components/WorkspaceCard.qml`：line 357-374 展开区按钮行
- `src/fuscan/gui/views/pages/HomePage.qml`：信号处理

#### 问题4：PDF 报告接通 GUI

**现状**：
- 后端 `src/fuscan/scanner/export.py` 的 `export_pdf` 已实现（reportlab + STSong-Light 中文字体）
- GUI 仅在 WorkspaceCard 导出菜单有 CSV/JSON（line 383-393），无 PDF 选项
- `resources.qrc` 已有 `export_pdf.svg` 图标

**修改**：
- `src/fuscan/gui/views/components/WorkspaceCard.qml`：
  - 导出菜单 `exportFormatMenu` 新增 `MenuItem { text: "PDF (*.pdf)"; onTriggered: card.exportPdfRequested(card.workspaceId) }`
  - 新增信号 `exportPdfRequested(string workspaceId)`
- `src/fuscan/gui/views/pages/HomePage.qml`：
  - 处理 `exportPdfRequested` 信号，调用 `workspaceController.exportResults("pdf", path)`
  - 新增 PDF 文件保存对话框
- `src/fuscan/gui/controllers/workspace_controller.py`：
  - 确认 `exportResults` 已支持 "pdf" 格式（应已支持，后端 `export_report` 按扩展名分发）
- `src/fuscan/gui/views/pages/ResultsPage.qml`：
  - 结果页工具栏增加「导出 PDF」按钮（与 CSV/JSON 并列）

**关键代码位置**：
- `src/fuscan/gui/views/components/WorkspaceCard.qml`：line 383-393 导出菜单
- `src/fuscan/gui/views/pages/ResultsPage.qml`：工具栏导出按钮
- `src/fuscan/gui/controllers/workspace_controller.py`：`exportResults` Slot

#### 问题5：设置 Tab 顺序调整

**修改**（`src/fuscan/gui/views/pages/SettingsPage.qml`）：
- line 88：`model: ["通用", "扫描", "忽略目录", "白名单"]` → `model: ["扫描", "通用", "忽略目录", "白名单"]`
- 确认 `StackLayout.currentIndex` 与 TabBar 同步（默认 0 = 扫描页）
- 同步更新 `project_memory.md` 中相关约束

**关键代码位置**：
- `src/fuscan/gui/views/pages/SettingsPage.qml`：line 88 TabBar model

#### 问题6：取消模板功能

**修改**：
- `src/fuscan/gui/views/pages/RulesPage.qml`：
  - 移除 `templateDialog`（line 63-162）
  - 移除「模板」按钮（line 234-239）
  - 保留导入/导出按钮
- `src/fuscan/gui/controllers/rules_controller.py`：
  - 移除 `templateList` Property 与 `loadTemplate` Slot
  - 移除 `templates.py` 依赖（或保留 `templates.py` 但不在 GUI 暴露）
- `src/fuscan/rules/templates.py`：评估是否删除（若无其他引用则删除）
- `tests/test_rules_templates.py`：同步移除或保留（若删除 templates.py 则移除测试）

**关键代码位置**：
- `src/fuscan/gui/views/pages/RulesPage.qml`：line 63-162, 234-239
- `src/fuscan/gui/controllers/rules_controller.py`：`templateList` / `loadTemplate`
- `src/fuscan/rules/templates.py`：模板定义

#### 问题9：XML 等通用文件类型标签

**现状**：
- `src/fuscan/gui/models/extractor_model.py` line 100-102：
  ```python
  _FORMAT_TAGS_BY_CLASS = {
      "SourceCodeExtractor": ("HTML", "C", "CPP", "PY"),
  }
  ```
- `SourceCodeExtractor` 支持 XML/JSON/JS/SH 等多种扩展名，但 format_tags 仅显示 4 种

**修改**（`src/fuscan/gui/models/extractor_model.py`）：
- 扩展 `_FORMAT_TAGS_BY_CLASS["SourceCodeExtractor"]`：
  ```python
  "SourceCodeExtractor": ("HTML", "XML", "JSON", "JS", "C", "CPP", "PY", "SH"),
  ```
- 确认 `SourceCodeExtractor` 实际支持的扩展名（查看 `src/fuscan/extractors/text.py`）
- 标签过多时 QML 自动换行（`RowLayout` + `Repeater` 已支持）

**关键代码位置**：
- `src/fuscan/gui/models/extractor_model.py`：line 100-102 `_FORMAT_TAGS_BY_CLASS`
- `src/fuscan/extractors/text.py`：`SourceCodeExtractor` 支持的扩展名

### 依赖

无（独立于其他迭代）

---

## iter-137 规则配置全局化重构

### 需求

- [x] 问题7：规则配置移到首页下方（添加任务上方），作为全局配置而非工作任务配置
- [x] 问题8：工作任务把展开区的「切换目标」挪到当前「定义规则」的位置

### 验收标准

1. 首页下方显示全局规则配置区（添加任务上方），可添加/编辑/删除/导入/导出规则
2. 规则配置作为全局配置，所有工作任务共享同一规则集
3. WorkspaceCard 移除「定义规则」按钮，原位置改为「切换目标」按钮
4. WorkspaceCard 展开区移除「切换目标」按钮（已挪到第一行）
5. 切换任务目标功能正常，扫描使用全局规则集
6. 覆盖率不低于 95%

### 技术方案

#### 问题7：规则配置移到首页下方

**现状**：
- 规则配置在工作任务级：WorkspaceCard 有「定义规则」按钮 → 跳转 RulesPage
- `RulesController` 支持 `bindWorkspace(ws_id)` 切换到工作区本地规则编辑
- `WorkspaceController` 通过 `updateWorkspaceRules` / `bindRulesController` 协调
- `ScanController` 通过 `setWorkspaceRuleset` 接收工作区特定规则集

**重构方案**：
- 规则配置改为全局：所有工作任务共享同一规则集（`RulesController.ruleset`）
- 移除工作区级规则绑定（`bindWorkspace` / `unbindWorkspace` / `setWorkspaceRuleset`）

**UI 调整**：
- `src/fuscan/gui/views/pages/HomePage.qml`：
  - 首页布局调整为：顶部任务列表 → 中间全局规则配置区 → 底部「添加任务」入口
  - 全局规则配置区内嵌规则列表 + 添加/编辑/删除/导入/导出按钮
  - 复用 `RulesPage.qml` 的规则列表渲染逻辑（提取为 `RulesPanel.qml` 组件）
- `src/fuscan/gui/views/pages/RulesPage.qml`：
  - 保留为独立页面（供 Sidebar 「规则」入口跳转），或合并到首页
  - 评估是否仍需独立 RulesPage（若首页已有全局配置，RulesPage 可作为详细编辑页）
- `src/fuscan/gui/views/Sidebar.qml`：
  - 确认导航项调整（首页包含规则配置，是否仍需独立规则入口）

**后端调整**：
- `src/fuscan/gui/controllers/rules_controller.py`：
  - 移除 `bindWorkspace` / `unbindWorkspace` 方法
  - `RulesController` 始终为全局模式
- `src/fuscan/gui/controllers/workspace_controller.py`：
  - 移除 `updateWorkspaceRules` / `bindRulesController` / `unbindRulesController`
  - 移除工作区级规则持久化（`workspaces.json` 中的 `rules_paths` 字段保留但不再单独加载）
  - 所有 `ScanController` 共享全局 `RulesController.ruleset`
- `src/fuscan/gui/controllers/scan_controller.py`：
  - 移除 `setWorkspaceRuleset` 方法
  - `startScan` 时从全局 `RulesController` 获取规则集
- `src/fuscan/gui/controllers/_task_overrides.py`：
  - 评估任务级覆盖中是否还有规则相关字段（应只剩 scan_archives/max_workers 等扫描参数）

**数据迁移**：
- `workspaces.json` 中已有的 `rules_paths` 字段：启动时迁移到全局规则配置
- 首次启动检测到旧工作区级规则时，合并到全局规则集（去重）

**关键代码位置**：
- `src/fuscan/gui/views/pages/HomePage.qml`：首页布局重构
- `src/fuscan/gui/views/pages/RulesPage.qml`：提取为 `RulesPanel.qml` 组件
- `src/fuscan/gui/controllers/rules_controller.py`：移除工作区绑定
- `src/fuscan/gui/controllers/workspace_controller.py`：移除规则协调逻辑
- `src/fuscan/gui/controllers/scan_controller.py`：移除 `setWorkspaceRuleset`

#### 问题8：工作任务展开区切换目标挪位置

**配合问题7**：
- WorkspaceCard 第一行操作按钮（line 236-277）：
  - 原：「定义规则」+「启动扫描」+「增量扫描」+「查看结果」
  - 新：「切换目标」+「启动扫描」+「增量扫描」+「查看结果」
- WorkspaceCard 展开区（line 353-444）：
  - 移除「切换目标」按钮（line 357-374）
  - iter-136 新增的「重新扫描」按钮保留在展开区

**修改**（`src/fuscan/gui/views/components/WorkspaceCard.qml`）：
- line 237-243：「定义规则」IconButton 改为「切换目标」IconButton
  - 图标：`qrc:/icons/target.svg`
  - 点击打开 `editTargetDialog`（原展开区的切换目标对话框）
  - 启用条件：`statusText !== "扫描中" && statusText !== "已暂停"`
  - 移除 `defineRulesRequested` 信号
- line 357-374：展开区移除「切换目标」IconButton
- `editTargetDialog` 保留在 WorkspaceCard 内（仅位置调整）

**关键代码位置**：
- `src/fuscan/gui/views/components/WorkspaceCard.qml`：line 237-243, 357-374
- `src/fuscan/gui/views/pages/HomePage.qml`：移除 `defineRulesRequested` 信号处理

### 依赖

- iter-136（展开区「重新扫描」按钮已添加，重构时保留）
- 建议在 iter-136 完成后执行，避免合并冲突

---

## 优先级与依赖关系

```
iter-135 (Bug修复) — 独立，优先执行
iter-136 (UI调整与功能补全) — 独立，可与 iter-135 后执行
iter-137 (规则配置全局化重构) — 依赖 iter-136（展开区重新扫描按钮）
```

### 推荐执行顺序

| 序号 | 迭代 | 主题 | 优先级 |
|------|------|------|--------|
| 1 | iter-135 | 扫描数据一致性与性能修复 | 高（Bug 影响） |
| 2 | iter-136 | UI 调整与功能补全 | 中（独立小项） |
| 3 | iter-137 | 规则配置全局化重构 | 中（UI 重构） |

## 度量与门禁

- 每轮迭代全套门禁：`ruff check` + `ruff format --check` + `pyrefly check` + `pytest` + `coverage >= 95%`
- bug 修复须新增回归测试用例
- UI 调整须同步更新 `project_memory.md` 中相关约束
- 文档同步更新（docstring / changelog）
- 每轮迭代记录到 `.trae/docs/iter-NN-<主题>.md`

## 遗留说明

- 原 req-38 中 iter-135 ~ iter-142 的计划顺延为 iter-138 ~ iter-145
- 本计划（req-39）优先解决用户实际反馈问题
- iter-137 规则配置全局化重构涉及数据迁移，需评估对现有用户工作区的影响
