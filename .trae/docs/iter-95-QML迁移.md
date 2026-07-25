# iter-95 QML 迁移与极简重构

## 需求清单

- [x] 简化复杂界面，聚焦关键功能，实现极简设计
- [x] 采用 WSL Dashboard 风格（Sidebar + ContentArea 两区布局）
- [x] 技术路线迁移至 PySide2 + QML，遵守性能最佳实践
- [x] 高耗时代码下沉至 QThread Worker 或调用高性能库
- [x] 移除旧 widget GUI（35+ 文件）
- [x] 移除托盘 UI（保留 watcher 功能代码，后续单独设计）
- [x] 重写 GUI 测试为 QML controller 测试
- [x] 更新 pyproject.toml 资源声明（移除 styles.qss，新增 QML 文件）

## 迭代目标

将 fuscan GUI 从基于 QWidget + .ui 的复杂界面迁移至 PySide2 + QML 声明式范式，采用 WSL Dashboard 风格的极简两区布局（Sidebar + ContentArea），移除冗余功能与旧代码，确保性能通过 QThread Worker 下沉与高性能库（pdf_oxide/calamine）保障。

## 改动文件清单

### 新增（QML 视图层）
- `src/fuscan/gui/qml/Main.qml` - 主窗口（ApplicationWindow + RowLayout 两区布局）
- `src/fuscan/gui/qml/Sidebar.qml` - 侧边栏（深色背景 + 导航项 + 主题切换）
- `src/fuscan/gui/qml/ContentArea.qml` - 内容区（StackView 页面切换）
- `src/fuscan/gui/qml/NavItem.qml` - 导航项组件（可复用）
- `src/fuscan/gui/qml/pages/ScanPage.qml` - 扫描页（setup/scanning/results 三态）
- `src/fuscan/gui/qml/pages/RulesPage.qml` - 规则管理页
- `src/fuscan/gui/qml/pages/SettingsPage.qml` - 设置页
- `src/fuscan/gui/qml/pages/AboutPage.qml` - 关于页
- `src/fuscan/gui/qml/qtquickcontrols2.conf` - Qt Quick Controls 风格配置

### 新增（QML 控制层）
- `src/fuscan/gui/qml/__init__.py` - 子包入口，导出 AppController
- `src/fuscan/gui/qml/app_controller.py` - 主控制器聚合（注册所有 controller 到 QML context）
- `src/fuscan/gui/qml/theme.py` - ThemeController（设计令牌 + 暗色模式）
- `src/fuscan/gui/qml/config_controller.py` - 配置控制器（扫描设置/提取器勾选/路径历史）
- `src/fuscan/gui/qml/rules_controller.py` - 规则控制器（规则文件管理/内置规则勾选）
- `src/fuscan/gui/qml/scan_controller.py` - 扫描控制器（状态机/Worker 协调/结果管理）
- `src/fuscan/gui/qml/about_controller.py` - 关于控制器（版本/依赖/手册打开）
- `src/fuscan/gui/qml/extractor_model.py` - ExtractorListModel（QAbstractListModel）
- `src/fuscan/gui/qml/rule_model.py` - RuleListModel（QAbstractListModel）
- `src/fuscan/gui/qml/result_model.py` - ResultListModel（QAbstractListModel）
- `src/fuscan/gui/qml/_severity_utils.py` - 严重级别文本/色值工具函数

### 新增（测试）
- `tests/test_gui_theme.py` - ThemeController 令牌与暗色模式测试
- `tests/test_gui_extractor_model.py` - ExtractorListModel 测试
- `tests/test_gui_rule_model.py` - RuleListModel 测试
- `tests/test_gui_result_model.py` - ResultListModel 测试
- `tests/test_gui_about.py` - AboutController 测试
- `tests/test_gui_config.py` - ConfigController 测试
- `tests/test_gui_rules_controller.py` - RulesController 测试
- `tests/test_gui_scan_controller.py` - ScanController 测试

### 重写
- `src/fuscan/gui/app.py` - 改用 QQmlApplicationEngine 加载 QML
- `src/fuscan/gui/__init__.py` - 移除 MainWindow 导出，改为惰性导入 launch/AppController
- `src/fuscan/watcher/__init__.py` - 移除 TrayApp 导出
- `.trae/rules/rule-12-pyside-dev.md` - 从 widget 规则改为 QML 规则
- `.trae/rules/rule-03-触发场景.md` - GUI SKILL 引用改为 fuscan-gui-layout

### 删除（旧 widget GUI，35 文件）
- `src/fuscan/gui/main_window.py` / `.ui` / `_ui.py`
- `src/fuscan/gui/about_dialog.py` / `.ui` / `_ui.py`
- `src/fuscan/gui/regex_tester.py` / `.ui` / `_ui.py`
- `src/fuscan/gui/rule_editor.py` / `.ui` / `_ui.py`
- `src/fuscan/gui/scan_target.py` / `.ui` / `_ui.py`
- `src/fuscan/gui/settings_dialog.py` / `.ui` / `_ui.py`
- `src/fuscan/gui/rule_panel.ui` / `_ui.py`
- `src/fuscan/gui/content_panel.py` / `detail_panel.py` / `rules_panel.py`
- `src/fuscan/gui/scan_mode_panel.py` / `scan_path_history.py` / `scan_progress_lists.py`
- `src/fuscan/gui/result_filter_panel.py` / `result_tree.py` / `stage_controller.py`
- `src/fuscan/gui/export_controller.py` / `preview_utils.py` / `extractor_model.py`（widget 版）
- `src/fuscan/gui/styles.qss`
- `src/fuscan/resources_rc.py`

### 删除（托盘 UI）
- `src/fuscan/watcher/tray.py` - TrayApp（QSystemTrayIcon + QMenu）
- `tests/test_tray.py` - 托盘测试

### 删除（旧测试）
- `tests/test_gui.py` - MainWindow widget 测试（7000+ 行）
- `tests/test_gui_scan_path_history.py`
- `tests/test_gui_scan_progress_lists.py`
- `tests/test_extractor_model.py` - widget ExtractorTreeModel 测试

### 修改
- `src/fuscan/cli.py` - 移除 tray 子命令与 _cmd_tray 函数
- `src/fuscan/watcher/incremental.py` - docstring 移除 TrayApp 引用
- `pyproject.toml` - force-include 移除 styles.qss，新增 9 个 QML 文件
- `README.md` / `docs/index.rst` / `rules/examples/README.md` - 移除 tray 命令文档

## 关键决策与依据

### 1. PySide2 + QML 而非继续用 QWidget
- **依据**：用户明确要求迁移至 QML，参考 `ref/pyside2_qml_dashboard` 风格
- **收益**：声明式 UI、GPU 加速渲染、关注点分离（QML 视图 + Python 控制器）
- **代价**：需重写全部 GUI 代码，但旧 widget 代码已技术债沉重

### 2. WSL Dashboard 两区布局
- **依据**：用户要求极简设计，仅 Sidebar + ContentArea
- **实现**：Sidebar（200px 固定宽，深色背景，导航项 + 主题切换）+ ContentArea（StackView 页面切换）
- **页面**：扫描页（核心）、规则页、设置页、关于页

### 3. QAbstractListModel 而非 QML ListModel
- **依据**：rule-12 性能要求，大数据量必须用 Model
- **实现**：ExtractorListModel/RuleListModel/ResultListModel 均继承 QAbstractListModel
- **收益**：批量插入用 beginInsertRows/endInsertRows，避免 QML 侧逐项 append

### 4. 扫描状态机三态
- **依据**：ScanPage 需根据状态切换 UI（setup/scanning/results）
- **实现**：ScanController.scanState 属性 + scanStateChanged 信号
- **转换**：setup→scanning（startScan）/ scanning→results（完成）/ →setup（取消/失败）

### 5. 托盘 UI 移除而非迁移
- **依据**：用户明确要求"托盘部分的界面功能暂时移除，只保留功能代码，后续单独设计"
- **实现**：删除 tray.py（TrayApp），保留 watcher/monitor.py + incremental.py（功能代码）
- **CLI**：移除 tray 子命令；watcher 子包仅导出 FileMonitor/IncrementalScanner

### 6. 设计令牌集中管理
- **依据**：rule-12 配置与资源章节要求令牌集中定义
- **实现**：ThemeController（theme.py）暴露色彩/排版/间距/圆角/按钮层级/速度档次色
- **QML 绑定**：`color: Theme.colorPrimary`，禁止硬编码色值

## 代码实现情况

### 视图层（QML）
- Main.qml：ApplicationWindow + RowLayout（Sidebar + ContentArea）
- Sidebar.qml：Logo + 4 个 NavItem + 主题切换开关，深色背景（#16161E）
- ContentArea.qml：StackView 切换 4 个页面，支持侧边栏折叠
- ScanPage.qml：三态切换（setup 配置 / scanning 进度 / results 结果列表 + 详情）
- RulesPage.qml：规则文件列表 + 内置规则勾选 + 加载/上移/下移/移除
- SettingsPage.qml：扫描设置 + 提取器勾选 + 忽略目录
- AboutPage.qml：版本/描述/作者/License/依赖列表

### 控制层（Python QObject）
- AppController：聚合 5 个 controller，注册到 QML rootContext
- ThemeController：50+ 设计令牌 @Property，暗色模式切换
- ConfigController：扫描设置读写、提取器勾选、路径历史、盘符列表
- RulesController：规则文件 CRUD、内置规则勾选、规则集合并
- ScanController：状态机、FileStatsWorker/ScanWorker 协调、结果/详情管理
- AboutController：版本/依赖列表、手册 PDF 打开

### 模型层（QAbstractListModel）
- ExtractorListModel：提取器列表（类名/显示名/扩展名/速度档次/勾选状态）
- RuleListModel：规则列表（名称/严重级别文本/色值/描述）
- ResultListModel：结果列表（文件路径/规则名/严重级别/命中数/索引）

## 整合优化情况

- 移除 35 个旧 widget 文件，GUI 代码量大幅缩减
- 移除 resources_rc.py（pyside2-rcc 产物），QML 直接文件加载
- 移除 styles.qss，改用 ThemeController 令牌系统
- watcher 子包精简：仅保留 monitor/incremental/ignore_dirs 三个功能模块
- CLI 移除 tray 子命令，子命令精简为 scan/rules/gui/cache/version

## 测试验证结果

- 新增 8 个 QML controller 测试文件，覆盖 Theme/Extractor/Rule/Result/About/Config/Rules/Scan
- 删除 4 个旧 widget 测试文件（test_gui.py 等 7000+ 行）
- test_tray.py 随托盘 UI 一并删除
- test_cli.py 移除 TestTrayCommand 类
- test_watcher.py 移除 TestWatcherLazyImport 类
- 测试使用 QT_QPA_PLATFORM=offscreen 支持无显示器环境
- pytest.mark.gui marker 标记 GUI 测试，CI 可 -m "not gui" 跳过

## 遗留事项

- [ ] manual.md 同步更新 GUI 截图与操作描述（需重新生成 PDF）
- [ ] .trae/skills/fuscan-development/SKILL.md 更新目录结构与可复用模式
- [ ] 扫描页 QML 实际运行验证（需 PySide2 + QML 运行时环境）
- [ ] 托盘 UI 后续单独设计（基于 watcher 功能模块构建新 UI 层）
- [ ] 性能基线测量（QML 渲染 vs 旧 widget 渲染对比）

## 下一轮计划

- P8：运行全套门禁（ruff/pyrefly/pytest/coverage）并修复失败
- P8：git commit + push（遵循 rule-09 提交规则）
- 后续迭代：manual.md 更新与 PDF 重新生成
- 后续迭代：托盘 UI 重新设计
