# iter-136 UI 调整与功能补全

## 需求清单

- [x] 问题2：展开区「切换目标」右侧增加「重新扫描」按钮（全量扫描，非增量）
- [x] 问题4：PDF 报告生成接通 GUI（WorkspaceCard 导出菜单 PDF 选项 + HomePage 保存对话框）
- [x] 问题5：设置 Tab 顺序调整，扫描页放在通用前面
- [x] 问题6：定义规则页面取消模板功能（移除模板按钮、对话框与后端代码）
- [x] 问题9：XML 等通用文件类型标注在文件类型标签中（SourceCodeExtractor format_tags 扩展）
- [x] 验收：覆盖率 >= 95%，全套门禁通过

## 迭代目标

完成 req-39 中五个独立 UI 调整与功能补全项：
1. WorkspaceCard 展开区「切换目标」右侧补「重新扫描」按钮，触发全量扫描
2. 导出菜单补 PDF 选项，接通后端 `export_report` 的 PDF 分支
3. SettingsPage Tab 顺序由「通用、扫描、忽略目录、白名单」改为「扫描、通用、忽略目录、白名单」，扫描作为默认页
4. RulesPage 移除模板按钮与对话框，RulesController 清理 `templateList`/`loadTemplate`
5. SourceCodeExtractor 的 format_tags 由 `("HTML", "C", "CPP", "PY")` 扩展为 `("HTML", "XML", "JSON", "JS", "C", "CPP", "PY", "SH")`

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fuscan/gui/views/pages/SettingsPage.qml` | TabBar model 顺序改为 `["扫描", "通用", "忽略目录", "白名单"]`；StackLayout 内 Tab 内容顺序同步交换；文件类型 ListView 的 model/cacheBuffer 绑定到 `currentIndex === 0`（扫描 Tab） |
| `src/fuscan/gui/models/extractor_model.py` | `_FORMAT_TAGS_BY_CLASS["SourceCodeExtractor"]` 由 `("HTML", "C", "CPP", "PY")` 扩展为 `("HTML", "XML", "JSON", "JS", "C", "CPP", "PY", "SH")` |
| `src/fuscan/gui/views/pages/RulesPage.qml` | 移除 `templateDialog` 定义与「模板」IconButton |
| `src/fuscan/gui/controllers/rules_controller.py` | 移除 `templateList` Property、`loadTemplate` Slot 及 `get_template_descriptions`/`get_template_names`/`load_template` 导入 |
| `src/fuscan/gui/views/components/WorkspaceCard.qml` | 展开区新增「重新扫描」IconButton（`qrc:/icons/rescan.svg`，`enabled: card.isCompletedState()`，调用 `workspaceController.startScan`）；导出菜单新增 `MenuItem { text: "PDF (*.pdf)" }`；新增信号 `exportPdfRequested` |
| `src/fuscan/gui/views/pages/HomePage.qml` | 新增 `exportPdfDialog` FileDialog（`defaultSuffix: "pdf"`）；处理 `onExportPdfRequested` 信号打开对话框 |
| `src/fuscan/gui/controllers/scan_controller.py` | `exportResults` 改用 `export_report(self._last_report, Path(path_str), fmt)`，统一支持 pdf/csv/json/sarif/text |
| `src/fuscan/gui/controllers/workspace_controller.py` | `exportResults` docstring 补充 pdf 格式说明 |
| `src/fuscan/gui/resources.qrc` / `resources_rc.py` | QML 改动后用 `pyside2-rcc` 重建（详见关键决策） |
| `tests/test_gui_rules_controller.py` | 移除 `TestTemplateList` 与 `TestLoadTemplate` 测试类 |
| `tests/test_gui_scan_controller.py` | 新增 `test_export_results_writes_pdf` 验证 PDF 头 `%PDF-` |
| `tests/test_gui_extractor_model.py` | 更新 SourceCodeExtractor formatTags 断言为 8 标签 |
| `tests/test_gui_workspace_controller.py` | qapp fixture 与 `_wait_for_restore` 补 PySide6 回退导入 |
| `tests/test_gui_qml_scan_progress.py` / `tests/test_gui_launch.py` | 增加 `PYSIDE2_AVAILABLE` 检查，PySide2 缺失时跳过 QML 集成测试 |
| `tests/test_gui_app_controller.py` | 新增 `TestRegisterQmlTypes`、`TestApplyFontConfigToTheme` 提升覆盖 |
| `tests/test_gui_about.py` | 新增 `openManual`/`openConfigDir` 失败路径与桌面服务调用测试 |
| `tests/test_gui_config.py` | 新增 `TestEntropySettings` 与 `cpuCount` 正数测试 |
| `tests/test_gui_whitelist_controller.py` | 新增导入导出边界与 `store` 属性测试 |
| `tests/test_gui_controllers_submodules.py` | 新增 `TestCoerceFloat` 测试 |
| `.coveragerc` | `omit` 新增 `src/fuscan/gui/resources_rc.py`（自动生成文件不纳入覆盖率） |

## 关键决策与依据

### 问题2：重新扫描按钮直接复用 startScan（全量扫描）

**方案**：WorkspaceCard 展开区「切换目标」右侧新增「重新扫描」IconButton，`onClicked` 直接调用 `workspaceController.startScan(card.workspaceId)`。

**依据**：`WorkspaceController.startScan` 默认走全量扫描路径（非增量），与「增量扫描」按钮（调用 `startIncrementalScan`）形成互补。`enabled: card.isCompletedState()` 与增量扫描按钮一致，避免扫描中重复触发。图标复用 `rescan.svg`（与顶部「重置」同图标，语义一致）。

### 问题4：PDF 导出复用 export_report 统一入口

**现状**：后端 `src/fuscan/scanner/export.py` 已实现 `export_report(report, path, fmt)` 按 fmt 分发到 `export_pdf`/`export_csv`/`export_json`/`export_sarif`/`export_text`，但 `ScanController.exportResults` 原实现按格式 if-else 分别调用，未接通 PDF。

**方案**：`exportResults` 改为直接调用 `export_report(self._last_report, Path(path_str), fmt)`，一行覆盖所有格式。QML 侧 WorkspaceCard 导出菜单新增 PDF MenuItem，HomePage 新增 `exportPdfDialog`（`defaultSuffix: "pdf"`），保存路径经 `workspaceController.exportResults(wsId, "pdf", path)` 传入。

**依据**：`export_report` 已按文件扩展名/ fmt 分发，GUI 只需传 fmt 与路径，无需重复分支逻辑。PDF 中文字体由 `export_pdf` 内 reportlab + STSong-Light 注册处理，已验证。

### 问题5：Tab 顺序调整 + 内容区同步交换

**方案**：SettingsPage TabBar `model` 由 `["通用", "扫描", "忽略目录", "白名单"]` 改为 `["扫描", "通用", "忽略目录", "白名单"]`。StackLayout 内 Tab 内容顺序同步交换（扫描页移到 index 0，通用页移到 index 1）。文件类型 ListView 的 `model` 与 `cacheBuffer` 绑定由 `currentIndex === 1` 改为 `currentIndex === 0`（扫描 Tab 新位置）。

**依据**：StackLayout 的 currentIndex 与 TabBar 同步，纯交换 Tab 顺序不涉及内容重构。扫描 Tab 作为默认页（currentIndex=0），符合用户「扫描是高频操作」的反馈。iter-135 的延迟加载绑定（非激活 Tab 时 model 为 null）保持有效，仅索引调整。

### 问题6：模板功能完整移除

**方案**：RulesPage.qml 移除 `templateDialog` 与「模板」IconButton；RulesController 移除 `templateList` Property、`loadTemplate` Slot 及 `templates.py` 的三个函数导入。`src/fuscan/rules/templates.py` 与 `tests/test_rules_templates.py` 保留（CLI/其他场景可能仍用，且无 GUI 依赖）。

**依据**：模板功能在 GUI 不再暴露，但 `templates.py` 作为纯数据模块无副作用，保留供潜在 CLI 用途，避免过度删除。测试同步移除 `TestTemplateList`/`TestLoadTemplate` 两个 GUI 控制器测试类。

### 问题9：format_tags 扩展覆盖 SourceCodeExtractor 全部常见类型

**方案**：`_FORMAT_TAGS_BY_CLASS["SourceCodeExtractor"]` 由 `("HTML", "C", "CPP", "PY")` 扩展为 `("HTML", "XML", "JSON", "JS", "C", "CPP", "PY", "SH")`。

**依据**：`SourceCodeExtractor`（text.py）支持的扩展名包含 xml/json/js/sh 等，原 4 标签无法体现覆盖范围。扩展后 8 标签在 QML `RowLayout` + `Repeater` 中自动换行，无布局问题。

### resources_rc.py 重建：pyside2-rcc 而非 pyside6-rcc

**问题**：iter-136 中途曾误用 `pyside6-rcc` 编译 resources_rc.py，生成 `from PySide6 import QtCore`。但项目运行环境（.venv）仅安装 PySide2 5.15.2.1，导致：
1. `pyrefly check` 报 `missing-import: Cannot find module PySide6`
2. 运行时 `import resources_rc` 会触发 `ModuleNotFoundError: No module named 'PySide6'`

**修复**：用 `uv run python scripts/build_qrc.py` 重新编译。`detect_rcc_tool` 优先检测 `pyside2-rcc`（环境已安装于 `.venv/Scripts/pyside2-rcc.EXE`），生成 `from PySide2 import QtCore` 的版本。重建后 pyrefly 0 errors，QML 集成测试恢复通过。

**教训**：`build_qrc.py` 的 `detect_rcc_tool` 已按 pyside2→pyside6 顺序优先选择，应直接用脚本而非手动指定 pyside6-rcc。pyrefly 门禁能有效捕获自动生成文件的导入不匹配问题。

### .coveragerc 排除 resources_rc.py

**方案**：`omit` 新增 `src/fuscan/gui/resources_rc.py`。

**依据**：resources_rc.py 是 rcc 自动生成的资源注册代码，其 `qInitResources` 等函数依赖 Qt 事件循环，单元测试无法有意义地覆盖。按「自动生成文件不纳入覆盖率」惯例排除，避免覆盖率统计失真。该排除不影响门禁达标（实际覆盖率 95.85%）。

## 代码实现情况

### 问题2/4（WorkspaceCard.qml + HomePage.qml + scan_controller.py）

- WorkspaceCard 展开区「切换目标」右侧新增「重新扫描」IconButton（accent: "ghost"，`enabled: card.isCompletedState()`）
- 导出菜单 `exportFormatMenu` 新增 `MenuItem { text: "PDF (*.pdf)"; onTriggered: card.exportPdfRequested(card.workspaceId) }`
- 新增信号 `signal exportPdfRequested(string workspaceId)`
- HomePage 新增 `exportPdfDialog`（`defaultSuffix: "pdf"`，`nameFilters: ["PDF (*.pdf)"]`），`onAccepted` 调用 `workspaceController.exportResults(wsId, "pdf", path)`
- HomePage `onExportPdfRequested` 处理：暂存 wsId 到 `_pendingExportWsId` 并打开对话框
- ScanController.exportResults 改为 `export_report(self._last_report, Path(path_str), fmt)` 一行

### 问题5（SettingsPage.qml）

- TabBar `Repeater.model`: `["通用", "扫描", "忽略目录", "白名单"]` → `["扫描", "通用", "忽略目录", "白名单"]`
- StackLayout 内扫描页与通用页位置交换（扫描页 index 0，通用页 index 1）
- 文件类型 ListView `model`: `currentIndex === 1 ? ... : null` → `currentIndex === 0 ? ... : null`
- 文件类型 ListView `cacheBuffer`: `currentIndex === 1 ? 500 : 0` → `currentIndex === 0 ? 500 : 0`

### 问题6（RulesPage.qml + rules_controller.py）

- RulesPage.qml 删除 `templateDialog`（原 line 63-162）与「模板」IconButton（原 line 234-239）
- rules_controller.py 删除 `from fuscan.rules import ... get_template_descriptions, get_template_names, load_template` 导入
- rules_controller.py 删除 `templateList` Property 与 `loadTemplate` Slot

### 问题9（extractor_model.py）

- `_FORMAT_TAGS_BY_CLASS["SourceCodeExtractor"]`: `("HTML", "C", "CPP", "PY")` → `("HTML", "XML", "JSON", "JS", "C", "CPP", "PY", "SH")`

### resources_rc.py 重建

- `uv run python scripts/build_qrc.py`：收集 15 个 QML + 39 个 SVG，用 pyside2-rcc 编译为 219580 bytes 的 resources_rc.py（`from PySide2 import QtCore`）

## 测试验证结果

### 新增/修改测试

- `test_export_results_writes_pdf`：导出 PDF 验证文件头 `%PDF-`，确认 `export_report` PDF 分支接通
- `test_gui_rules_controller.py`：移除模板相关测试类（与后端移除同步）
- `test_gui_extractor_model.py`：SourceCodeExtractor formatTags 断言更新为 8 标签
- PySide2/PySide6 兼容性修复：qapp fixture、QML 集成测试 skip 条件、子模块测试导入回退
- 覆盖率补强：about/config/whitelist/controllers_submodules 新增边界与失败路径测试

### 门禁结果

- `ruff check src tests`：All checks passed
- `ruff format --check src tests`：150 files already formatted
- `pyrefly check src`：0 errors (530 suppressed)
- `pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：2356 passed, 75 deselected, coverage 95.85%

## 遗留事项

- SettingsPage.qml / RulesPage.qml / WorkspaceCard.qml 为 QML 层变更，Python 单元测试无法直接覆盖，需手动验证 UI 渲染与交互
- PDF 导出依赖 reportlab + STSong-Light 中文字体，Win7 老系统需确认字体注册路径（iter-136 未触发字体变更，沿用既有实现）
- iter-137 规则配置全局化重构依赖 iter-136 的展开区「重新扫描」按钮（已保留）

## 下一轮计划

进入 iter-137：规则配置全局化重构（req-39 问题7/8）。
