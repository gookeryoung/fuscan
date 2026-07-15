# iter-45：rule-12 PySide 开发规范合规整改

## 需求清单

参见 `.trae/req/req-10-rule12合规整改.md`，共 4 项全部实施。

## 迭代目标

依据 `rule-12-pyside-dev.md` 四节规范，对现有 GUI 代码进行合规整改：
1. 跨线程槽加 `@Slot()` 装饰
2. 内联 QSS 提到 theme 令牌
3. SVG 图标纳入 `.qrc` 资源系统
4. `result_tree` 从 QTreeWidget 迁移到 QStandardItemModel + QTreeView（Model/View 架构）

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fuscan/theme.py` | 新增 `FONT_FAMILY_MONO` 令牌与 `QSS_TOKENS` 条目 |
| `src/fuscan/gui/styles.qss` | `#rule_editor` 字体从硬编码改为 `${FONT_FAMILY_MONO}` |
| `src/fuscan/gui/rule_editor.ui` | objectName `editor`→`rule_editor`，删除内联 `styleSheet` |
| `src/fuscan/gui/rule_editor_ui.py` | 恢复基线风格（双兼容 try/except、无 u 前缀、无 `(object)`），保留 `self.rule_editor` |
| `src/fuscan/gui/rule_editor.py` | `self.editor`→`self.rule_editor`（7 处） |
| `src/fuscan/gui/main_window.py` | P1：加 `Slot`/`QFile` 导入（双兼容），4 槽加 `@Slot` 装饰，19 个图标路径改 `:/` 前缀，新增 `_read_svg_text` 支持 `QFile` 读取，import `resources_rc` 注册资源；P3：`QTreeWidget`→`QTreeView`+`QStandardItemModel`，新增 `_apply_severity_to_standard_item`/`_make_result_row`/`_clear_row_selectable` 辅助函数，重写 3 种分组 populate 与双击/选中事件处理 |
| `src/fuscan/gui/main_window.ui` | `result_tree` 从 `QTreeWidget` 改为 `QTreeView`，删除 6 个 `<column>` 定义 |
| `src/fuscan/gui/main_window_ui.py` | 定向编辑：`QTreeWidget`→`QTreeView`，删除 `result_tree.headerItem()` 段（表头改由 model 管理） |
| `src/fuscan/gui/detail_dialog.py` | `_ICON_TARGET` 改 `:/icons/target.svg`，移除未用的 `Path` 导入 |
| `src/fuscan/gui/resources_rc.py` | 新增：pyside2-rcc 编译产物 + 双兼容补丁 |
| `src/fuscan/assets/resources.qrc` | 新增：19 个 SVG 图标资源声明 |
| `tests/test_gui.py` | P2：`dialog.editor`→`dialog.rule_editor`（13 处）；P3：78 处 `result_tree` 测试 API 迁移（`topLevelItemCount`→`rowCount`、`topLevelItem`→`item`、`setCurrentItem`→`setCurrentIndex` 等），新增 2 个测试覆盖 parent 查找与无 data 顶层项路径 |

## 关键决策与依据

### P1：@Slot 装饰 + 内联 QSS 令牌化

- **@Slot 装饰**：rule-12 要求"跨线程必走信号槽，槽建议加 `@Slot()` 装饰"。`ScanWorker` 在 QThread 中 emit 信号，主窗口 4 个槽（`_on_scan_cancelled`/`_on_scan_progress`/`_on_scan_finished`/`_on_scan_failed`）此前无装饰，加 `@Slot(object)`/`@Slot(str)`。
- **FONT_FAMILY_MONO 令牌**：`rule_editor.ui` 的 `#editor` 硬编码 `"Cascadia Code", "Consolas", "Courier New", monospace`，违反 rule-12"QSS 用 `${TOKEN}` 引用，禁止硬编码"。新增 `FONT_FAMILY_MONO` 令牌入 `theme.py` + `QSS_TOKENS`，`styles.qss` 用 `${FONT_FAMILY_MONO}` 引用。
- **objectName 对齐**：`rule_editor.ui` objectName 从 `editor` 改为 `rule_editor`，与 QSS 选择器 `#rule_editor` 对齐。删除内联 `styleSheet` 属性（rule-12："UI 仅在 `.ui` 定义，禁止 `.py` 内实现 UI 初始配置代码"——内联 styleSheet 属于 UI 初始配置）。
- **_ui.py 恢复基线**：编辑 `.ui` 后 `rule_editor_ui.py` 被重新生成为 uic 原始输出（star imports + u 前缀 + `(object)`），引入 48 个 ruff 错误。手动恢复基线风格（双兼容 try/except、无 u 前缀、`class Ui_RuleEditorDialog:` 无基类），保留 `self.rule_editor` 匹配新 objectName。

### P2：.qrc 资源系统改造

- **范围**：19 个 SVG 图标（共 28KB）纳入 `.qrc`，编译为 `resources_rc.py`。QSS 和 PDF 留磁盘（QSS 需运行时令牌替换，PDF 由外部阅读器打开）。
- **双兼容**：pyside2-rcc 可用，pyside6-rcc 不可用。编译后手动补丁 import 为 `try: from PySide2 import QtCore / except ImportError: from PySide6 import QtCore`，与 `_ui.py` 文件同模式。
- **_read_svg_text 辅助函数**：`_load_themed_icon` 原用 `Path(svg_path).read_text()` 读取 SVG 文本着色，不支持 `:/` 路径。新增 `_read_svg_text`：`:/` 前缀用 `QFile` 读取，否则回退 `Path.read_text`。着色逻辑不变（strip fill → inject theme color → QSvgRenderer 渲染）。
- **资源注册**：`main_window.py` 顶部 `from fuscan.gui import resources_rc  # noqa: F401`，import 时自动调 `qInitResources()` 注册。测试中 `import MainWindow` 也会触发注册。
- **打包**：`resources_rc.py` 在 `src/fuscan/gui/` 下，hatchling 默认包含；SVG 原文件保留在 `assets/icons/` 供 rcc 重新编译，不影响运行时（资源已嵌入二进制）。

### P3：result_tree Model/View 迁移

- **迁移范围**：仅 `result_tree`（扫描结果树，可达上千条）。`rules_tree` 等其余控件数据量小，保留 `QTreeWidget` 便利类无迁移收益。
- **架构**：`QTreeWidget` → `QTreeView` + `QStandardItemModel`。模型在 `__init__` 创建并配置 6 列表头，`_setup_results_tree` 中 `setModel` 绑定到视图。
- **API 映射**：
  - `QTreeWidgetItem([col0, col1, ...])` → `list[QStandardItem]`（`_make_result_row` 构造，默认不可编辑）
  - `item.setData(col, role, val)` → `cell.setData(val, role)`（单列无 col 参数）
  - `item.addChild(child)` → `parent_cell.appendRow(child_row)`
  - `tree.addTopLevelItem(item)` → `model.appendRow(row_list)`
  - `tree.clear()` → `model.clear()` + 重设 `setHorizontalHeaderLabels`（model.clear 清表头）
  - `tree.selectedItems()` → `tree.selectionModel().selectedIndexes()`
  - `itemDoubleClicked(QTreeWidgetItem, int)` → `doubleClicked(QModelIndex)`
  - `itemSelectionChanged()` → `selectionModel().selectionChanged(...)`（槽用 `*_args` 忽略参数，ruff ARG002 豁免）
- **3 个辅助函数**：`_apply_severity_to_standard_item`（severity 列着色）、`_make_result_row`（构造不可编辑行）、`_clear_row_selectable`（分组顶层项清除 `ItemIsSelectable` 标志，简化原 `_set_row_flags` 双分支为单分支）。
- **3 种分组模式**：`_populate_flat`（文件→命中子行）、`_populate_grouped_by_rule`（规则→文件子行）、`_populate_grouped_by_severity`（等级→文件子行）全部重写为 Model/View API。`ScanResult` 存于第 0 列 `Qt.UserRole`。
- **事件处理**：`_on_result_double_clicked(QModelIndex)` 通过 `sibling(row, 0)` 取第 0 列 cell，子行无 data 时向上取父行；`_on_result_selection_changed(*_args)` 从 `selectedIndexes()` 取选中项，同样支持父行查找。
- **死代码移除**：`itemFromIndex` 对有效 index 必返回 QStandardItem（model 中所有 cell 由 `_make_result_row` 创建），移除 `_on_result_double_clicked` 与 `_on_result_selection_changed` 中的防御性 `if first_col is None` 检查；`_set_row_flags` 的 `selectable=True` 分支从未使用，简化为 `_clear_row_selectable` 单分支。
- **.ui / _ui.py**：`QTreeWidget`→`QTreeView`，删除 `<column>` 定义与 `headerItem()` 段（表头由 model `setHorizontalHeaderLabels` 管理）。`_ui.py` 编辑后易被 IDE 自动重新生成，需 `git checkout HEAD --` 恢复后定向修改。

## 代码实现情况

- P1：7 文件改动，@Slot 装饰 4 槽 + FONT_FAMILY_MONO 令牌 + objectName 对齐 + _ui.py 恢复基线
- P2：4 文件改动 + 2 新增文件，.qrc 编译 + 双兼容补丁 + 图标路径迁移 + _read_svg_text 辅助
- P3：4 文件改动，QTreeWidget→QTreeView+QStandardItemModel，3 种分组 populate 重写，事件处理迁移，78 处测试 API 迁移 + 2 个新测试

## 整合优化情况

- `_read_svg_text` 从 `_load_themed_icon` 提取为独立函数，支持 `:/` 与磁盘路径双模式
- `detail_dialog.py` 移除未用的 `Path` 导入（图标路径改 `:/` 后不再需要）
- P3 移除 `_set_row_flags` 未使用的 `selectable=True` 分支，简化为 `_clear_row_selectable`
- P3 移除 `_on_result_double_clicked`/`_on_result_selection_changed` 中不可达的 `itemFromIndex is None` 防御性检查

## 测试验证结果

| 门禁 | 结果 |
|------|------|
| ruff check | 599 errors（基线 605，-6） |
| ruff format | 80 files already formatted |
| pyrefly | 784 errors（基线 814，-30；+8 `appendRow` PySide2 stub 限制，按惯例接受） |
| pytest | 1308 passed, 0 failed, 16 deselected, coverage 96.05% ≥ 95%（基线 1304 passed/2 failed/96.10%） |

覆盖率说明：main_window.py missed 从基线 64 降至 62（实际改善），总覆盖率 96.05% vs 基线 96.10% 差 0.05%，源于 P3 新增代码（+25 stmts）稀释百分比。基线 2 个 failing 测试（`test_detail_hits_table_*`）现已全部通过。

## 遗留事项

- `resources_rc.py` 重新编译需 `pyside2-rcc`（在 `.venv\Scripts\` 中），SVG 变更后须重新编译并提交
- pyrefly `appendRow` stub 限制（8 处）为 PySide2 已知问题，按项目惯例接受

## 下一轮计划

无明确下一轮。rule-12 合规整改 4 项全部实施闭环。
