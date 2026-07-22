# iter-74：解析器勾选区 MVC

## 需求清单

- [x] 文件类型勾选列表使用 MVC（Model/View）架构替代 QCheckBox×14 + QGridLayout
- [x] 风格美观，与整体 GitHub Desktop 风格保持一致

## 迭代目标

将 iter-72 的「文件类型勾选区」从「14 个 QCheckBox + 2 列 QGridLayout」
重构为「QAbstractListModel + QListView」Model/View 架构，遵循 rule-12
「大数据量优先用 QAbstractItemModel 而非便利类」约束；同时通过 QSS 让
勾选区视觉与整体卡片风格一致。

## 改动文件清单

| 文件 | 改动内容 |
|------|---------|
| `src/fuscan/gui/extractor_model.py` | 新建：`ExtractorItem` frozen dataclass + `ExtractorListModel(QAbstractListModel)`，封装提取器元数据与勾选状态 |
| `src/fuscan/gui/main_window.ui` | `file_types_hint_label` + `file_types_container`/`file_types_grid` 替换为 `file_types_view` QListView（IconMode + Adjust + Static + wordWrap） |
| `src/fuscan/gui/main_window_ui.py` | 同步 .ui 生成的控件代码 |
| `src/fuscan/gui/main_window.py` | 移除 `QCheckBox` 导入与 `_extractor_checkboxes` 字典；`_setup_file_types` 改为构造 `ExtractorListModel` + `setModel`/`setGridSize`/`setUniformItemSizes`；`_apply_config` 用 `model.set_disabled_extractors`；`_compute_scan_extensions` 用 `model.enabled_extensions()` |
| `src/fuscan/gui/styles.qss` | 新增 `QListView#file_types_view` 样式块（透明背景、无边框、rule-12 选中色硬约束） |
| `tests/test_extractor_model.py` | 新建：25 个单元测试覆盖 rowCount/data/flags/setData、disabled_extractors / set_disabled_extractors / enabled_extensions / extractors_changed 信号 |

## 关键决策与依据

### D1：QAbstractListModel 而非 QStandardItemModel

**决策**：自定义 `ExtractorListModel(QAbstractListModel)`，而非用
`QStandardItemModel` + `QStandardItem`（设置 checkable）。

**依据**：
- rule-12 明确「大数据量优先用 QAbstractItemModel 而非 QTreeWidget/QListWidget
  便利类」，QStandardItemModel 也是便利类，自定义子类化是更纯的 MVC
- 14 个提取器条目结构简单固定（class_name + display_name + extensions + enabled），
  自定义模型可直接用 `list[ExtractorItem]` + `list[bool]` 存储，避免 QStandardItem
  的指针开销与编辑/拖放等无用能力
- 自定义模型可显式暴露 `disabled_extractors()` / `set_disabled_extractors()` /
  `enabled_extensions()` 三个业务 API，主窗口无需感知内部存储细节

### D2：QListView IconMode + GridSize 实现多列网格

**决策**：QListView 设置 `viewMode=IconMode` + `gridSize=QSize(260, 28)` +
`movement=Static` + `resizeMode=Adjust` + `uniformItemSizes=True`，
模拟原 2 列 GridLayout 视觉且支持窗口宽度自适应重排。

**依据**：
- QListView 默认 ListMode 是单列竖排，14 个 item 会过高
- IconMode 默认 flow=LeftToRight + wrapping=True，配合 GridSize 即可实现
  「宽度足够时多列、宽度收窄时自动换行」的响应式布局
- `setGridSize` 强制每个 item 占据固定单元格，保证视觉对齐
- `setResizeMode(Adjust)` 让 layout 在窗口 resize 时自动重算列数

### D3：item 默认勾选 + CheckStateRole 渲染 checkbox

**决策**：模型 `data(CheckStateRole)` 返回 `Qt.Checked`/`Qt.Unchecked`，
`flags()` 含 `Qt.ItemIsUserCheckable`，让 QListView 的默认 item delegate
（`QStyledItemDelegate`）自动渲染 checkbox + display text，无需自定义委托。

**依据**：
- checkbox 视觉由 Qt 默认委托渲染，与全局 QCheckBox 样式一致
- 避免为 14 个简单条目编写 `QStyledItemDelegate` 子类，控制复杂度
- 勾选状态通过 `setData(CheckStateRole)` 切换，发 `dataChanged` + 自定义
  `extractors_changed` 信号；主窗口连接后者持久化到 `Config.disabled_extractors`

### D4：set_disabled_extractors 先更新数据再 emit

**决策**：`set_disabled_extractors` 内部先更新 `_enabled_flags` 列表，
再 `dataChanged.emit`，最后 `extractors_changed.emit`。

**依据**：
- Qt Model/View 规范：emit `dataChanged` 时模型 `data()` 必须已返回新值，
  否则视图若同步读取会得到旧数据
- iter-72 之前 `_apply_config` 遍历 checkboxes 时用 `blockSignals(True)` 避免回写，
  改用模型后改为对整个模型 `blockSignals(True)` 后调用 `set_disabled_extractors`
  一次性批量恢复，避免逐项触发持久化

### D5：QSS 透明背景融入 QGroupBox 卡片

**决策**：`QListView#file_types_view` 设 `background: transparent; border: none`，
item 默认 `background: transparent`，仅在 hover/selected 时绘制高亮。

**依据**：
- 勾选区嵌入在 `file_types_group` QGroupBox 内（卡片背景 `${COLOR_BG_CARD}`），
  QListView 自身再加白底+边框会造成「卡片套卡片」视觉冗余
- 透明背景让 QListView 融入 QGroupBox，与原 QGridLayout+QCheckBox 视觉一致
- `selection-background-color` / `selection-color` 遵循 rule-12 硬约束
  （6 处 item view 控件统一深蓝底+白字）

## 代码实现情况

### ExtractorItem frozen dataclass

```python
@dataclass(frozen=True)
class ExtractorItem:
    class_name: str
    display_name: str
    extensions: tuple[str, ...]

    @property
    def ext_hint(self) -> str:
        head = ", ".join(self.extensions[:_EXT_HINT_LIMIT])
        if len(self.extensions) > _EXT_HINT_LIMIT:
            return f"{head}..."
        return head

    @property
    def display_text(self) -> str:
        return f"{self.display_name} ({self.ext_hint})"
```

### ExtractorListModel 核心方法

- `rowCount(parent=None)`：父索引有效时返回 0（列表模型无层级）
- `data(index, role)`：DisplayRole 返回 `display_text`，ToolTipRole 返回全部扩展名，
  CheckStateRole 返回 `Qt.Checked`/`Qt.Unchecked`
- `setData(index, value, role)`：仅处理 CheckStateRole，先比对避免无变化触发信号
- `disabled_extractors()`：返回禁用类名列表（按 _items 顺序）
- `set_disabled_extractors(class_names)`：批量恢复勾选状态，未知类名忽略
- `enabled_extensions()`：全选返回 None（Scanner 快速路径），部分取消返回
  启用扩展名并集（小写、去重、排序后元组）
- 信号 `extractors_changed`：勾选状态变化时发出，主窗口连接以持久化

### main_window._setup_file_types 简化

iter-72 中 14 行循环创建 QCheckBox + 2 列 GridLayout 计算，iter-74 简化为：

```python
self._extractor_model = ExtractorListModel(default_registry, parent=self)
self.file_types_view.setModel(self._extractor_model)
self.file_types_view.setGridSize(QSize(260, 28))
self.file_types_view.setUniformItemSizes(True)
self.file_types_view.setSpacing(4)
self._extractor_model.extractors_changed.connect(self._on_extractor_toggled)
```

## 测试验证结果

- ruff check: All checks passed
- ruff format: 95 files already formatted
- pyrefly: 0 errors (478 suppressed, 60 warnings)
- pytest: 1482 passed (+25 新增), coverage 96.10%（较 iter-72 的 95.88% 提升 0.22%）

## 遗留事项

无。

## 下一轮计划

无。本次迭代已完整交付 MVC 重构需求。
