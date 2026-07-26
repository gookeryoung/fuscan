# iter-106：清空对话框风格统一与忽略目录分类管理

## 需求清单

- [x] 清空所有工作区的对话框风格与本体风格统一（自绘 Dialog 替代原生 MessageDialog）
- [x] 忽略目录增加分类管理，便于管理（分类折叠列表 + 自定义目录区）
- [x] 忽略目录增加常规大型软件的忽略（ANSYS/AUTOCAD/SOLIDWORKS/OFFICE/WPS/MATLAB/Adobe 等）

## 迭代目标

1. 将 HomePage 的清空确认/结果对话框从 `QtQuick.Dialogs 1.3` 的原生 `MessageDialog` 改为 `QtQuick.Controls 2.15` 的自绘 `Dialog`，使用 theme 令牌配色，与 WorkspaceCard 中的对话框风格一致
2. 将 SettingsPage 忽略目录 Tab 从平铺 TextArea 改为分类卡片列表 + 自定义目录区，支持按分类批量勾选/取消，支持添加/删除自定义目录
3. 在默认忽略目录中增加大型软件安装目录（ANSYS Inc/Autodesk/SOLIDWORKS Corp/Microsoft Office/Kingsoft 等），按目录名匹配任意层级

## 改动文件清单

### 数据层
- `src/fuscan/config.py`：
  - 新增 `IGNORE_DIR_CATEGORIES` 常量（有序分类元组，含 15 个分类）
  - 新增 `_default_ignore_dirs()` 函数，从分类常量扁平化派生默认忽略目录
  - `Config.ignore_dirs` 默认值改为 `field(default_factory=_default_ignore_dirs)`
  - 新增"大型软件"分类：ANSYS Inc/Autodesk/SOLIDWORKS Corp/SolidWorks/Microsoft Office/Office16/Office15/Office14/Kingsoft/WPS Office/MATLAB/MathWorks/Adobe/Corel/TecPlot/STK/Altium
  - `__all__` 导出 `IGNORE_DIR_CATEGORIES`

### 控制层
- `src/fuscan/gui/controllers/config_controller.py`：
  - 新增 `ignoreDirsChanged` 信号
  - 新增 `ignoreDirCategories` Property（`QVariantList`，返回分类视图数据）
  - 新增 `customIgnoreDirs` Property（`QVariantList`，返回自定义目录列表）
  - 新增 `toggleIgnoreDir(dir_name, enabled)` Slot：勾选/取消单个目录
  - 新增 `setIgnoreDirCategoryEnabled(category, enabled)` Slot：批量勾选/取消整个分类
  - 新增 `addCustomIgnoreDir(dir_name)` Slot：添加自定义目录
  - 新增 `removeCustomIgnoreDir(dir_name)` Slot：移除自定义目录
  - 移除旧的 `ignoreDirsText` Property 和 `setIgnoreDirsText` Slot
  - `resetToDefaults` 增加发射 `ignoreDirsChanged` 信号

### 视图层
- `src/fuscan/gui/views/pages/HomePage.qml`：
  - 调整 import 顺序：`QtQuick.Dialogs 1.3` 移到 `QtQuick.Controls 2.15` 之前，避免 `Dialog` 类型被 `QtQuick.Dialogs 1.x` 的同名类型覆盖
  - 清空确认对话框：`MessageDialog` → 自绘 `Dialog`，contentItem 为含 theme 令牌配色的 Rectangle + ColumnLayout，含警告图标 + 标题 + 描述 + 取消/清空按钮（清空按钮用 colorDanger）
  - 清空结果对话框：`MessageDialog` → 自绘 `Dialog`，含信息图标 + 标题 + 描述 + 知道了按钮（主色填充）
- `src/fuscan/gui/views/pages/SettingsPage.qml`：
  - 忽略目录 Tab 从 TextArea 改为 ScrollView + ColumnLayout
  - 预设分类列表：Repeater 遍历 `configController.ignoreDirCategories`，每个分类一个卡片（Rectangle + border + radius），含分类标题行（三态 CheckBox + 分类名 + 目录计数）+ 目录项列表（CheckBox + monospace 目录名）
  - 自定义目录区：卡片含标题 + 说明 + 输入框 + 添加按钮 + 自定义目录列表（monospace 目录名 + ✕ 删除按钮）+ 空状态提示

### 测试
- `tests/test_gui_config.py`：
  - `TestIgnoreDirs` 类重写：10 个测试覆盖分类视图、toggle、批量操作、自定义增删、大型软件分类存在性
  - `TestResetToDefaults.test_resets_ignore_dirs` 改用 `addCustomIgnoreDir` + `config.ignore_dirs` 断言

## 关键决策与依据

### 1. 数据结构保持 list[str] 不变
- `Config.ignore_dirs` 仍为扁平 `list[str]`，Scanner/FileWalker 接口不变
- 分类信息由 `IGNORE_DIR_CATEGORIES` 常量定义，仅作为 UI 层元数据
- 好处：向后兼容已保存的配置，不破坏 Scanner/FileWalker 接口

### 2. 清空对话框用 contentItem Rectangle 而非 background 属性
- PySide2 5.15 的 `Dialog` 继承自 `Popup`，但 `background` 属性在某些情况下不可用
- 方案：contentItem 设为 Rectangle（含 color/border/radius），内部用 ColumnLayout 布局内容
- 好处：完全控制对话框视觉风格，不依赖主题默认背景

### 3. import 顺序解决 Dialog 类型冲突
- `QtQuick.Dialogs 1.3` 和 `QtQuick.Controls 2.15` 都有 `Dialog` 类型
- 后导入的模块覆盖前面的同名类型
- 方案：`QtQuick.Dialogs 1.3` 在 `QtQuick.Controls 2.15` 之前导入，确保 Controls 的 `Dialog` 胜出
- `QtQuick.Dialogs 1.3` 仍需保留（用于 FileDialog）

### 4. 大型软件目录按目录名匹配
- FileWalker 按目录名匹配（大小写不敏感，任意层级）
- "ANSYS Inc" 匹配任何层级下名为 "ANSYS Inc" 的目录
- "Autodesk" 匹配任何层级下名为 "Autodesk" 的目录
- 用户可将软件安装在非默认位置，仍能被忽略

## 代码实现情况

### 新增 API

ConfigController 新增属性/Slot：
- `ignoreDirCategories` → `list[dict[str, object]]`：分类视图数据
- `customIgnoreDirs` → `list[str]`：自定义目录列表
- `toggleIgnoreDir(dir_name, enabled)`：勾选/取消单个目录
- `setIgnoreDirCategoryEnabled(category, enabled)`：批量勾选/取消分类
- `addCustomIgnoreDir(dir_name)`：添加自定义目录
- `removeCustomIgnoreDir(dir_name)`：移除自定义目录

### 大型软件分类目录列表
```
ANSYS Inc, Autodesk, SOLIDWORKS Corp, SolidWorks,
Microsoft Office, Office16, Office15, Office14,
Kingsoft, WPS Office, MATLAB, MathWorks,
Adobe, Corel, TecPlot, STK, Altium
```

## 测试验证结果

- `uv run ruff check src tests`：All checks passed!
- `uv run ruff format --check src tests`：119 files already formatted
- `uv run pyrefly check`：0 errors (645 suppressed, 68 warnings not shown)
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：1684 passed, 43 deselected, coverage 95.32%

## 遗留事项

- 无

## 下一轮计划

无明确下一轮计划。三个需求已全部达成，待用户提出新需求。
