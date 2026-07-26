# iter-104：类别统一勾选与任务级配置覆盖

## 需求清单

- [x] 问题 1：文件类型的父节点不能统一勾选，不方便操作。
- [x] 问题 2：工作区的任务应该能够切换扫描目标，当前无法操作。
- [x] 问题 3：工作区的任务的设置应该是任务自身的设置，而不是全局设置。

## 迭代目标

1. 实现文件类型 ListView 父节点（类别）三态勾选，支持一键全选/全不选该类别下所有文件类型。
2. 在工作区卡片展开区添加「切换目标」按钮，支持修改扫描模式（full/drive/folder）与目标路径；扫描中/暂停中拒绝修改。
3. 引入任务级配置覆盖（task_overrides）：在工作区卡片展开区添加「设置」按钮，可针对单个任务覆盖 scan_archives/max_workers/max_file_size/max_depth/ignore_dirs 5 项配置，不影响全局设置；持久化到 workspaces.json 并在重启后恢复。

## 改动文件清单

### Python 后端
- `src/fuscan/gui/models/extractor_model.py`：新增 `set_category_enabled`、`category_enabled_state` 方法。
- `src/fuscan/gui/controllers/config_controller.py`：新增 `setCategoryEnabled`、`categoryEnabledState` Slot。
- `src/fuscan/gui/models/workspace_model.py`：`WorkspaceItem` 增加 `task_overrides: dict[str, object]` 字段。
- `src/fuscan/gui/controllers/workspace_controller.py`：新增 `updateWorkspaceTarget`、`setTaskOverride`、`taskOverridesJson` Slot；持久化与恢复 task_overrides；提供 `_serialize_task_overrides`、`_deserialize_task_overrides` 工具函数。
- `src/fuscan/gui/controllers/scan_controller.py`：新增 `_task_overrides` 字段与 `setTaskOverride` Slot；新增 `_effective_scan_archives`/`_effective_max_workers`/`_effective_max_file_size`/`_effective_max_depth`/`_effective_ignore_dirs` 方法；扫描时优先使用覆盖值。

### QML 视图
- `src/fuscan/gui/views/pages/SettingsPage.qml`：文件类型 ListView section.delegate 添加三态 CheckBox（`tristate: true` + `checkState` 绑定 `categoryEnabledState`）。
- `src/fuscan/gui/views/components/WorkspaceCard.qml`：
  - `QtQuick.Dialogs 1.3` 改为命名空间导入 `as Dialogs`，避免与 `QtQuick.Controls 2.15` 的 `Dialog` 类型冲突。
  - 展开区新增「🎯 切换目标」按钮，点击初始化编辑表单并打开对话框，提交时调用 `updateWorkspaceTarget`。
  - 展开区新增「⚙ 设置」按钮，点击从 `taskOverridesJson` 读取并初始化任务级设置对话框，提交时调用 `setTaskOverride`。
  - 移除 `onOpened`/`onAboutToShow` 信号处理器（Qt 5.15 PySide2 不识别），改为在按钮 `onClicked` 中显式初始化表单后再 `open()`。

### 测试
- `tests/test_gui_extractor_model.py`：新增 `TestCategoryEnabled` 测试类，覆盖 `category_enabled_state` 的全选/全不选/部分选中三态、`set_category_enabled` 的批量勾选/取消、未知类别静默忽略。
- `tests/test_gui_workspace_controller.py`：新增 `TestUpdateWorkspaceTarget`（任务目标更新与持久化）、`TestTaskOverrides`（任务级覆盖增删改查、持久化、重启恢复）、`TestScanControllerTaskOverrides`（`_effective_*` 方法优先用覆盖值）。

## 关键决策与依据

### 1. 类别勾选三态显示
- 选择 `tristate: true` 而非 `partiallyCheckedEnabled`：后者在 Qt 5.15 PySide2 中不存在；`tristate` 是 QtQuick.Controls 2 标准 API。
- 点击行为：当前全选→全不选，否则→全选（与用户对父节点勾选的直觉一致）。
- 状态查询委托给 `ExtractorListModel.category_enabled_state` 返回 0/1/2 三态，QML 端做映射。

### 2. 任务级配置覆盖架构
- **覆盖范围**：仅限 `scan_archives`/`max_workers`/`max_file_size`/`max_depth`/`ignore_dirs` 5 个扫描相关字段；`backup_dir`/`cache_enabled` 等全局状态字段不允许覆盖。
- **存储位置**：`WorkspaceItem.task_overrides: dict[str, object]`（frozen dataclass 字段，通过 `update_workspace` 替换重建）。
- **生效路径**：`ScanController._task_overrides` 字段 + `_effective_<field>()` 方法；扫描调用 `Scanner`/`ScanWorker` 时统一走 `_effective_*`，避免分散判断。
- **持久化**：`workspaces.json` 中每个工作区新增 `task_overrides` 字段；序列化时 `tuple` → `list`（JSON 不支持 tuple），反序列化时 `list` → `tuple` 并做类型校验。
- **重启恢复**：`WorkspaceController._load_persisted` 反序列化后通过 `_create_workspace(task_overrides=...)` 传入，并在创建 `ScanController` 后立即调用 `setTaskOverride` 同步覆盖值。

### 3. 任务目标切换的边界约束
- 扫描中（`扫描中`/`已暂停`）拒绝修改目标，避免破坏运行时 worker 状态。
- 全盘模式（`full`）强制清空 target，与 `addWorkspace` 行为一致。
- 无效 mode_str（不在 `full/drive/folder` 中）拒绝修改并 warning。

### 4. QML Dialog 类型冲突修复
- 问题：`QtQuick.Dialogs 1.3` 与 `QtQuick.Controls 2.15` 都导出 `Dialog` 类型，QML 解析时优先匹配到 `QtQuick.Dialogs 1.x` 的 `Dialog`（Window-based，无 `modal`/`standardButtons`/`opened` 等属性）。
- 修复：`import QtQuick.Dialogs 1.3 as Dialogs`，FileDialog 改为 `Dialogs.FileDialog`，让 `Dialog` 类型唯一解析到 `QtQuick.Controls 2.15`。

### 5. 信号处理器改写
- 问题：`onOpened`/`onAboutToShow` 在 PySide2 5.15 中报 `Cannot assign to non-existent property`，疑似 QtQuick.Controls 2 Dialog 的信号未被 PySide2 元类型系统正确暴露。
- 修复：移除信号处理器，在按钮 `onClicked` 中显式初始化表单后再调用 `dialog.open()`，等价且更直观。

## 代码实现情况

### 类别统一勾选 API
```python
def set_category_enabled(self, category: str, enabled: bool) -> bool:
    """批量设置某类别下所有提取器的勾选状态。"""
    changed = False
    for i, row in enumerate(self._rows):
        if row.category == category and row.enabled != enabled:
            row.enabled = enabled
            idx = self.index(i)
            self.dataChanged.emit(idx, idx, [Qt.UserRole + 6])
            changed = True
    return changed

def category_enabled_state(self, category: str) -> int:
    """返回类别勾选状态：0=全不选, 1=全选, 2=部分选中。"""
    rows = [row for row in self._rows if row.category == category]
    if not rows:
        return 0
    enabled_count = sum(1 for row in rows if row.enabled)
    if enabled_count == 0:
        return 0
    if enabled_count == len(rows):
        return 1
    return 2
```

### 任务级覆盖生效
```python
def _effective_scan_archives(self) -> bool:
    """任务级覆盖优先的 scan_archives。"""
    value = self._task_overrides.get("scan_archives")
    if isinstance(value, bool):
        return value
    return self._config.scan_archives
```
扫描时构造 `ScanContext`/`Scanner` 均使用 `_effective_*`，未覆盖时回退到全局 `Config`。

## 整合优化情况

- 持久化工具函数 `_serialize_task_overrides`/`_deserialize_task_overrides` 提取到模块级，便于复用与单元测试。
- 任务级覆盖白名单 `_TASK_OVERRIDE_KEYS` 集中定义，避免散落多处硬编码。
- WorkspaceCard.qml 中两个 Dialog 都采用「按钮 onClicked 初始化 + open()」模式，统一交互风格。

## 测试验证结果

- ruff check：通过
- ruff format --check：通过
- pyrefly check：0 errors
- pytest：1622 passed, 43 deselected, coverage 95.22%（>= 95%）
  - `TestCategoryEnabled`：7 个测试全过
  - `TestUpdateWorkspaceTarget`：7 个测试全过
  - `TestTaskOverrides`：10 个测试全过
  - `TestScanControllerTaskOverrides`：4 个测试全过
  - `test_gui_launch.py`、`test_gui_qml_scan_progress.py`：QML 加载验证通过

## 遗留事项

- 任务级设置对话框的 UI 可进一步细化（如 max_workers 范围提示、ignore_dirs 路径校验），当前为功能完整可用版本。
- 类别勾选的状态在 `disabled_extractors` 变化时需手动刷新 section.delegate，当前通过 `extractorCountChanged` 信号触发 QML 重算，已验证可用。

## 下一轮计划

无明确下一轮需求。等待用户反馈。
