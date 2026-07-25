# req-32 QML 迁移与极简重构

## 需求

- [x] 简化复杂界面，聚焦关键功能，实现极简设计
- [x] 采用 WSL Dashboard 风格（Sidebar + ContentArea 两区布局）
- [x] 技术路线迁移至 PySide2 + QML，遵守性能最佳实践
- [x] 高耗时代码下沉至 QThread Worker 或调用高性能库
- [x] 移除旧 widget GUI（35+ 文件）
- [x] 移除托盘 UI（保留 watcher 功能代码，后续单独设计）
- [x] 重写 GUI 测试为 QML controller 测试
- [x] 更新 pyproject.toml 资源声明（移除 styles.qss，新增 QML 文件）
- [x] 更新规则文件（rule-03/rule-12 适配 QML）
- [ ] manual.md 同步更新 GUI 截图与操作描述（遗留）
- [ ] 托盘 UI 后续单独设计（遗留）

## 验收标准

1. GUI 采用 PySide2 + QML 范式，UI 全部在 .qml 文件定义
2. 布局为 Sidebar + ContentArea 两区结构，参考 WSL Dashboard 风格
3. 大数据量列表使用 QAbstractListModel，禁止 QML 侧 ListModel 动态 append
4. 高耗时操作（扫描/统计/导出）走 QThread Worker，禁止主线程执行
5. 设计令牌集中定义在 ThemeController，禁止 QML/Python 硬编码色值
6. 旧 widget GUI 文件全部删除，无残留引用
7. 托盘 UI 移除，watcher 功能模块（FileMonitor/IncrementalScanner）保留
8. GUI 测试覆盖所有 QML controller 的公共 API
9. ruff/pyrefly/pytest/coverage 全套门禁通过
