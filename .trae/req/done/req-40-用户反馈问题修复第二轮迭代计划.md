# req-40 用户反馈问题修复迭代计划（第二轮）

## 概述

基于用户实际使用反馈（req-39 后的第二轮），针对 6 个问题制定 1 轮迭代计划（iter-138）。
问题覆盖 UI 视觉差异、PDF 导出稳定性、扫描进度显示、设置页 binding loop、
扫描资源配置展示、规则区可折叠 6 个方向。

每轮迭代遵循 rule-01 六步闭环，全套门禁（ruff/pyrefly/pytest/coverage 95%）通过为验收硬性条件。

---

## 问题清单与归属

| 编号 | 问题 | 归属迭代 | 类型 |
|------|------|---------|------|
| 1 | 全局规则区始终占用大块空间，需可折叠 | iter-138 | UI 优化 |
| 2 | SettingsPage ListView binding loop for property "model" | iter-138 | Bug |
| 3 | PDF 导出 LayoutError（超长 detail 单行行高超过页面） | iter-138 | Bug |
| 4 | 扫描中未显示资源配置（CPU/内存/深度） | iter-138 | 功能补全 |
| 5 | 扫描完成 100% 进度条显示为空 | iter-138 | Bug |
| 6 | 全局规则列表选中态颜色无区别（暗色下选中与 hover 同色） | iter-138 | UI 优化 |

---

## iter-138 用户反馈问题修复第二轮

### 需求

- [x] 问题1：全局规则区可折叠（HomePage 内嵌 RulesPanel 默认收起）
- [x] 问题2：SettingsPage ListView cacheBuffer 固定值修复 binding loop
- [x] 问题3：PDF 导出超长 detail 截断 + 行高限制
- [x] 问题4：ScanProgressCard 显示资源配置
- [x] 问题5：scanProgressBar 改用 progress 百分比
- [x] 问题6：RulesPanel 选中态颜色区分（左侧色条 + 主色叠加）

### 验收标准

1. HomePage 全局规则区默认收起仅显示标题栏，点击展开显示完整规则面板
2. SettingsPage 切换 Tab 不再报 "model" binding loop 警告
3. 含超长 detail（5000 字符）的扫描报告能正常导出 PDF，不抛 LayoutError
4. 扫描中卡片显示「配置」行：最多 M 线程 / 最大 XX MB / 深度 D
5. 扫描完成后 scanProgressBar 进度条满（visualPosition=1）
6. 全局规则文件列表选中态与 hover 态有明显视觉差异（主色叠加 + 色条 + 加粗）
7. 覆盖率不低于 95%

### 技术方案

#### 问题1：全局规则区可折叠

**修改**（`src/fuscan/gui/views/components/RulesPanel.qml`）：
- 新增 `property bool collapsible: false` 与 `property bool collapsed: false`
- 标题行新增展开/收起 IconButton（`collapsible=true` 时可见）
- 导入/导出按钮 `visible: !collapsed`
- 主区域 RowLayout `visible: !collapsed`

**修改**（`src/fuscan/gui/views/pages/HomePage.qml`）：
- Rectangle `Layout.fillHeight: !rulesPanelInner.collapsed`
- `Layout.preferredHeight: rulesPanelInner.collapsed ? 48 : 1`
- RulesPanel 设 `collapsible: true, collapsed: true`

#### 问题2：SettingsPage cacheBuffer 固定

**修改**（`src/fuscan/gui/views/pages/SettingsPage.qml`）：
- `cacheBuffer: 500`（固定，移除 `currentIndex === 0 ? 500 : 0` 三元）
- `model` 保持 `currentIndex === 0 ? extractorModel : null` 切换

#### 问题3：PDF 超长 detail 截断

**修改**（`src/fuscan/scanner/export.py`）：
- 新增 `_truncate_text(text, max_chars=200)` 辅助函数
- 表格行 path/rule_name/match_description/detail 均经截断

#### 问题4：ScanProgressCard 资源配置

**修改**（`src/fuscan/gui/views/components/ScanProgressCard.qml`）：
- 新增 `property ConfigControllerType configController: ConfigController`
- 元数据 GridLayout 增加「配置」行：`最多 M 线程 / 最大 XX MB / 深度 D`

#### 问题5：scanProgressBar 改用 progress

**修改**（`src/fuscan/gui/views/components/ScanProgressCard.qml`）：
- `from: 0.0, to: 100.0, value: workspaceController.activeScanController.progress`

#### 问题6：RulesPanel 选中态颜色

**修改**（`src/fuscan/gui/views/components/RulesPanel.qml`）：
- delegate background 选中态改用 `Qt.rgba(theme.colorPrimary.r/g/b, 0.15)`
- 新增左侧 3px 色条 Rectangle
- ItemDelegate `font.bold: ListView.isCurrentItem`
- contentItem 选中态文字色改 `theme.colorPrimary`

### 依赖

无（独立于其他迭代，6 个问题互不依赖）

---

## 度量与门禁

- 全套门禁：`ruff check` + `ruff format --check` + `pyrefly check` + `pytest` + `coverage >= 95%`
- bug 修复须新增回归测试用例
- 文档同步更新（docstring / changelog）
- 迭代记录到 `.trae/docs/iter-138-用户反馈问题修复第二轮.md`

## 遗留说明

- 9 个 benchmark 失败（extractor speed_tier 断言）与本迭代无关，iter-137 已记录为 CPU 速度差异
- 问题2 binding loop 修复采用最小变更，若实际运行仍出现可进一步改为 `visible` 控制 + model 始终绑定
