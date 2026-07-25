# iter-98 问题修复与文件类型树形聚类

## 需求清单

- [x] 加载规则文件后，规则文件显示在内置规则下方的列表里
- [x] 修复 RulesPage.qml:112 undefined 错误（model.fileName → modelData.fileName）
- [x] 文件类型按 category 树形聚类，按分组显示并支持统一勾选
- [x] 启动扫描后能正确执行扫描（enabled_extensions 全选时返回 None）
- [x] 修复 SettingsPage.qml:284 Row anchors 错误
- [x] 修复 RulesPage.qml:113 ItemDelegate 不存在 color 属性错误
- [x] 全套门禁通过（ruff/pyrefly/pytest/coverage）

## 迭代目标

修复用户反馈的 5 个 GUI 问题，并将文件类型列表从扁平排列改为按类别
树形聚类，便于统一勾选、避免过多行。

## 改动文件清单

### 修改

- `src/fuscan/gui/views/pages/RulesPage.qml`
  - 规则文件列表 delegate：`model.fileName` → `modelData.fileName`
    （QVariantList of dict 通过 modelData 访问字段）
  - 移除 ItemDelegate 不存在的 `color` 属性，文字颜色下沉到
    contentItem Label 直接引用 `theme.colorTextPrimary`
  - 新增选中高亮与 hover 效果（colorBgSelected/colorBgHover + ColorAnimation）

- `src/fuscan/gui/views/pages/SettingsPage.qml`
  - 速度指示器 Row + MouseArea 包裹在 Item 内，避免 Row 内子项使用
    anchors 导致的「Row will not function」错误
  - 「解析速度」文字标签 + 蓝色格式 tag（formatLabel）
  - 文件类型 ListView 改为按 `category` role 分组（section.property）：
    - section.delegate 渲染类别头部（三态 CheckBox + 类别名 + 状态文本）
    - 类别头部 CheckBox 通过 `Connections` 监听 `categoryStatesChanged`
      信号更新 checkState，`onToggled` 调用 `setCategoryEnabled` Slot
    - 内层 delegate 增加 leftMargin 32 表示层级缩进
  - 高度从 280 调整为 320 适配分组头部

- `src/fuscan/gui/models/extractor_model.py`
  - 新增 `_ROLE_CATEGORY = b"category"`（Qt.UserRole + 8）
  - 新增 `_category_sort_key()` 与 `_category_state()` 辅助函数
  - `_ExtractorRow.__slots__` 新增 `category` 字段（__init__ 通过
    `_classify(class_name)` 计算）
  - `data()` 新增 `Qt.UserRole + 8` 分支返回 `row.category`
  - `load_from_registry()` 按 `(category_order, display_name)` 排序，
    保证同类相邻，便于 QML section 分组
  - 新增 `categoryStatesChanged` 信号
  - 新增 `categoryStates` Property（QVariantMap，返回各类别
    `"all"`/`"none"`/`"partial"` 状态）
  - 新增 `setCategoryEnabled(category, enabled)` Slot：批量切换
    类别内所有提取器勾选状态
  - `set_disabled_extractors`/`set_extractor_enabled`/`_set_all_enabled`
    在状态变化时 emit `categoryStatesChanged`
  - `enabled_extensions()` 全部勾选时返回 `None`（原返回空 tuple，
    导致 Scanner 跳过所有文件）

- `tests/test_gui_extractor_model.py`
  - `TestRoleNames` 新增 `category` role 断言
  - `TestData` 新增三个 category 数据测试：
    - `test_data_returns_category_for_known_class`：DocxExtractor → "Office 文档"
    - `test_data_returns_category_for_pdf`：PdfExtractor → "PDF/RTF"
    - `test_data_returns_category_non_empty_for_all`：所有行 category 非空
  - `TestLoadFromRegistry.test_load_with_disabled_extractors` 适配排序：
    查找 disabled class 所在行而非假设 index 0
  - 新增 `TestCategorySort`：验证行按 category_order 单调非递减、同类连续
  - 新增 `TestCategoryStates`：验证全选/全不选/部分选返回正确状态
  - 新增 `TestSetCategoryEnabled`：验证启用/禁用类别、不影响其他类别、
    未知类别 noop

## 关键决策与依据

1. **modelData vs model 访问**：规则文件模型是 `QVariantList`（list of dict），
   QML delegate 中 dict 字段通过 `modelData.fileName` 访问。原 `model.fileName`
   适用于 `QAbstractListModel` 的 role，不适用于 QVariantList。

2. **enabled_extensions 返回 None 而非空 tuple**：`Scanner.scan_extensions`
   参数语义：`None` = 扫描所有文件（快速路径），空 tuple = 不扫描任何文件。
   全部勾选时原返回空 tuple 导致扫描器跳过所有文件，改为返回 None 对齐语义。

3. **树形聚类用 ListView.section 而非嵌套 Repeater**：QML `ListView.section`
   是分组渲染的惯用模式，配合已排序的扁平模型即可实现分组头部，无需引入
   `QAbstractItemModel` 树模型或嵌套 Repeater，复杂度最低。

4. **categoryStates Property + Connections 模式**：Qt Quick Controls 2 的
   CheckBox `checkState` 若直接绑定到 Property，用户点击会破坏绑定。采用
   `Connections` 监听 `categoryStatesChanged` 信号 + `onToggled` 处理用户
   交互的标准模式，避免绑定冲突。

5. **类别映射用 class_name 而非 display_name**：display_name 字符串匹配
   脆弱（如「Word（DOCX）」括号后缀变化），按 class_name 映射稳定且
   可维护。

## 代码实现情况

- 后端 `extractor_model.py` 新增类别分组 API（categoryStates/setCategoryEnabled），
  覆盖率 98%（仅防御性分支未覆盖）
- QML section.delegate 通过 `Connections` + `Component.onCompleted` + `updateState()`
  函数实现三态 CheckBox 与模型状态同步
- 内层 delegate leftMargin 32 提供视觉层级缩进
- RulesPage delegate 修复后选中高亮、hover 效果完整

## 整合优化情况

- 无重复代码引入。类别分组逻辑内聚于 `ExtractorListModel`，
  QML 侧仅消费 categoryStates/setCategoryEnabled，符合 rule-12「三层 MVC」
- 测试覆盖类别排序、状态计算、批量切换三类场景，确保分组行为正确

## 测试验证结果

- `uv run ruff check src tests`：通过
- `uv run ruff format --check`（修改文件）：通过
- `uv run pyrefly check`：0 errors
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：
  1519 passed，覆盖率 95.62%
- `extractor_model.py` 覆盖率：98%

## 遗留事项

- 无

## 下一轮计划

- 无（待用户反馈或新需求）
