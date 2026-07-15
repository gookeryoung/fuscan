# 需求10：rule-12 PySide 开发规范合规整改

- [x] 1. 跨线程槽函数加 `@Slot()` 装饰器（`_on_scan_cancelled`/`_on_scan_progress`/`_on_scan_finished`/`_on_scan_failed`）。
- [x] 2. 内联 QSS 提到 theme：`rule_editor.ui` 的 `#editor` 等宽字体从硬编码改为 `FONT_FAMILY_MONO` 令牌，删除内联 `styleSheet` 属性。
- [x] 3. `.qrc` 资源系统改造：19 个 SVG 图标纳入 `resources.qrc`，编译为 `resources_rc.py`（双兼容 PySide2/PySide6），图标引用改为 `:/` 前缀，`_load_themed_icon` 支持 `QFile` 读取资源。
- [x] 4. `result_tree` Model/View 迁移：`QTreeWidget` → `QTreeView` + `QStandardItemModel`，3 种分组模式（flat/by-rule/by-severity）populate 重写，双击/选中事件迁移到 `QModelIndex` API，78 处测试 API 迁移 + 2 个新测试覆盖 parent 查找与无 data 顶层项路径。
