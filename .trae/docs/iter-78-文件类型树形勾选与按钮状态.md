# iter-78：文件类型树形勾选与按钮状态

## 需求清单

- [x] 文件类型显示使用树形列表形式，避免文字截断，增加父类别如`文档`便于统一勾选，子项采取`类别+扩展名`格式，例如 `WORD文档（doc, docx, dotx, dotm）`
- [x] 文件类型支持单项勾选和批量勾选，批量勾选时显示勾选的文件类型数量
- [x] 开始扫描前和扫描完成后，暂停和取消的按钮应当不可用

## 迭代目标

将文件类型勾选区从平铺 QListView（IconMode 多列网格）重构为树形 QTreeView，
按父类别（文档/表格/演示/邮件）分组，支持父子勾选联动。同时修复扫描前/后
暂停与取消按钮的可用性问题。

## 改动文件清单

| 文件 | 改动内容 |
|------|---------|
| `src/fuscan/gui/extractor_model.py` | **重写**：`ExtractorListModel` → `ExtractorTreeModel`（QAbstractItemModel 树形）；新增 `_EXTRACTOR_CATEGORIES` 字典（14 个提取器 → 4 父类别）、`_CATEGORY_ORDER`、`_PAREN_RE`、`_CHILD_BIT` 常量；`ExtractorItem` 新增 `tree_display_text` 属性；实现 `index`/`parent`/`rowCount`/`columnCount`/`data`/`flags`/`setData`；父子勾选联动（分类节点批量设置子项 + 子项变化触发父节点 dataChanged）；新增 `checked_count`/`total_count`/`category_count`/`category_name` API |
| `src/fuscan/gui/main_window.ui` | `file_types_view` 从 QListView 改为 QTreeView（`headerHidden=true`、`expandsOnDoubleClick=false`、`editTriggers=NoEditTriggers`）；新增 `file_types_count_label`（「已勾选 N/M 项」） |
| `src/fuscan/gui/main_window_ui.py` | 由 `pyside2-uic` 从 .ui 重新生成（uic 产物，勿手改） |
| `src/fuscan/gui/main_window.py` | 导入 `ExtractorListModel` → `ExtractorTreeModel`；`_setup_file_types` 移除 QListView IconMode 配置（gridSize/uniformItemSizes/spacing），改为 `expandAll()`；新增 `_update_file_types_count()` 方法同步计数标签；`_on_extractor_toggled` 末尾调用 `_update_file_types_count`；`_apply_config` 恢复勾选后同步计数；`_update_stage_actions` 新增 `pause_resume_btn`/`cancel_btn` 的 `setEnabled(is_scanning)` |
| `tests/test_extractor_model.py` | **重写**：stub 类名改为匹配 `_EXTRACTOR_CATEGORIES` 键（PdfExtractor/TextExtractor/XlsxExtractor）以测试多分类；测试类适配树形 index 访问（`_cat_index`/`_child_index` 辅助函数）；新增 `TestExtractorTreeModelParentChildLinkage`（7 个父子联动测试）、`TestExtractorTreeModelCount`（4 个计数测试） |
| `tests/test_gui.py` | 新增 3 个按钮状态测试：`test_pause_cancel_btn_disabled_in_setup`、`test_pause_cancel_btn_enabled_in_scanning`、`test_pause_cancel_btn_disabled_in_results` |

## 关键决策与依据

### D1：internalId 编码用高位标记而非负数

**决策**：分类节点 `internalId = cat_index + 1`（1-4），子项节点
`internalId = (cat_index + 1) | _CHILD_BIT`（`_CHILD_BIT = 0x10000`）。

**依据**：
- PySide2 的 `createIndex(row, column, id)` 第三参数要求 `quintptr`（unsigned），
  传入负数会抛 `OverflowError: can't convert negative int to unsigned`
- 用高位标记区分分类/子项，解码时 `(internal_id & ~_CHILD_BIT) - 1` 取 cat_index，
  `internal_id >= _CHILD_BIT` 判断是否子项节点
- 编码方案在 docstring 中明确记录，避免后续维护时误用负数

### D2：子项显示文本去掉 display_name 全角括号后缀

**决策**：`ExtractorItem.tree_display_text` 用 `_PAREN_RE` 去掉 display_name
中的全角括号后缀（如 "Word（DOCX）" → "Word"），再拼接小写扩展名列表，
格式为 `{中文名}（{扩展名列表}）`。

**依据**：
- 需求1要求子项格式 `类别+扩展名`，如 `WORD文档（doc, docx, dotx, dotm）`
- display_name 中的括号后缀（如 DOCX）与扩展名列表重复，去掉后更简洁
- 全角括号 `（）` 是 display_name 的统一后缀风格，正则 `（[^）]*）` 精确匹配

### D3：.ui 用 `headerHidden` 而非 `headerVisible`

**决策**：.ui 中 QTreeView 属性用 `<property name="headerHidden"><bool>true</bool></property>`，
而非 `headerVisible=false`。

**依据**：
- pyside2-uic 从 `headerVisible` 生成的代码调用 `setHeaderVisible(False)`，
  但 PySide2 的 QTreeView 没有此方法（只有 `setHeaderHidden`）
- `headerHidden` 是 QTreeView 的标准属性，pyside2-uic 生成 `setHeaderHidden(True)`
- `headerVisible` 可能是 Qt Designer 较新版本的属性名，PySide2 不兼容

### D4：父子勾选联动在 setData 中处理

**决策**：分类节点 `setData` 批量设置所有子项 flags 并 emit dataChanged
（分类 + 子项范围）；子项 `setData` 更新自身并 emit 父节点 dataChanged。
分类节点的 CheckStateRole 由 `_category_check_state` 动态计算（全选 Checked /
部分 PartiallyChecked / 全不选 Unchecked）。

**依据**：
- Qt Model/View 规范：setData 内必须先更新内部状态再 emit dataChanged，
  确保 emit 时 `data()` 返回最新值
- 分类节点 CheckState 不持久化，每次 `data()` 调用时根据子项 flags 实时计算，
  避免状态不一致
- 批量勾选时只 emit 一次 `extractors_changed`，避免主窗口多次保存配置

### D5：按钮可用性按 WorkflowStage 管理

**决策**：`_update_stage_actions` 中 `pause_resume_btn.setEnabled(is_scanning)`
与 `cancel_btn.setEnabled(is_scanning)`，仅在 SCANNING 阶段可用。

**依据**：
- 需求3：开始扫描前（SETUP）和扫描完成后（RESULTS）暂停/取消按钮不可用
- `is_scanning = self._workflow_stage == WorkflowStage.SCANNING`，已有的阶段
  判断变量直接复用
- 与 `scan_btn`（仅 SETUP）、`rescan_btn`（仅 RESULTS）的管理方式一致

## 代码实现情况

### ExtractorTreeModel 树形结构

```python
class ExtractorTreeModel(QAbstractItemModel):
    """节点编码（PySide2 要求 unsigned，故用高位标记）：
    - 分类节点：internalId = cat_index + 1（1-4）
    - 子项节点：internalId = (cat_index + 1) | _CHILD_BIT（>= 0x10000）
    """
    _CHILD_BIT = 0x10000

    def index(self, row, column, parent=None):
        if not parent.isValid():
            return self.createIndex(row, 0, row + 1)  # 分类
        parent_id = parent.internalId()
        if parent_id == 0 or parent_id >= _CHILD_BIT:
            return QModelIndex()  # 无效或子项父
        cat_index = parent_id - 1
        return self.createIndex(row, 0, (cat_index + 1) | _CHILD_BIT)  # 子项
```

### 父子勾选联动

```python
def setData(self, index, value, role=Qt.CheckStateRole):
    if internal_id >= _CHILD_BIT:
        # 子项：更新自身 + emit 父节点 dataChanged
        flags[row] = new_checked
        self.dataChanged.emit(index, index, [role])
        parent_idx = self.parent(index)
        if parent_idx.isValid():
            self.dataChanged.emit(parent_idx, parent_idx, [role])
    elif internal_id > 0:
        # 分类：批量设置所有子项 + emit 子项范围 dataChanged
        for i in range(len(flags)):
            flags[i] = new_checked
        self.dataChanged.emit(index, index, [role])
        top_child = self.index(0, 0, index)
        bottom_child = self.index(child_count - 1, 0, index)
        self.dataChanged.emit(top_child, bottom_child, [role])
```

### 计数标签同步

```python
def _update_file_types_count(self) -> None:
    checked = self._extractor_model.checked_count()
    total = self._extractor_model.total_count()
    self.file_types_count_label.setText(f"已勾选 {checked}/{total} 项")
```

### 按钮状态管理

```python
def _update_stage_actions(self) -> None:
    is_scanning = self._workflow_stage == WorkflowStage.SCANNING
    # ...
    self.pause_resume_btn.setEnabled(is_scanning)
    self.cancel_btn.setEnabled(is_scanning)
```

## 整合优化情况

- 无额外整合优化

## 测试验证结果

### 单元测试

- `tests/test_extractor_model.py`：47 passed（树形构造 17 + setData 6 +
  父子联动 7 + disabled 7 + enabled_extensions 4 + count 4 + 其他 2）
- `tests/test_gui.py`：新增 3 个按钮状态测试，全部通过

### 全套门禁

| 检查项 | 结果 |
|--------|------|
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 97 files already formatted |
| `pyrefly check` | 0 errors (492 suppressed, 61 warnings) |
| `pytest -m "not slow" --cov=fuscan --cov-fail-under=95` | **1549 passed**（较 iter-77 的 1521 +28），coverage **95.14%** |

## 遗留事项

- 无

## 下一轮计划

无。本次迭代 3 个子需求全部完成，门禁全通过，进入收尾提交。
