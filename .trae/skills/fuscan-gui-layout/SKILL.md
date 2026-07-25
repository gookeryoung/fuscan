# fuscan GUI 界面布局规范（WSL Dashboard 风格 · PySide2 + QML）

fuscan GUI 采用 **WSL Dashboard 风格**（参考 `ref/pyside2_qml_dashboard`），仅含 **Sidebar + ContentArea** 2 区结构，PySide2 + QML 实现。通用 PySide2/PySide6 编码规范见 `rule-12-pyside-dev.md`。

## 2 区布局结构

```
┌──────────────┬─────────────────────────────────────────┐
│              │                                         │
│  Sidebar     │  ContentArea                            │
│  (200px)     │  (StackView 切换 4 页)                  │
│              │                                         │
│  ┌────────┐  │  ┌───────────────────────────────────┐  │
│  │ Logo   │  │  │                                   │  │
│  ├────────┤  │  │  当前页内容                        │  │
│  │ 扫描   │  │  │  （扫描/规则/设置/关于）            │  │
│  │ 规则   │  │  │                                   │  │
│  │ 设置   │  │  │                                   │  │
│  │ 关于   │  │  │                                   │  │
│  ├────────┤  │  │                                   │  │
│  │ 暗色   │  │  │                                   │  │
│  │ 切换   │  │  │                                   │  │
│  └────────┘  │  └───────────────────────────────────┘  │
└──────────────┴─────────────────────────────────────────┘
```

2 区职责：

1. **Sidebar（左侧 200px 固定宽度）**：Logo 区 + 导航项列表（NavItem）+ 底部暗色切换。
2. **ContentArea（右侧填充）**：`StackView` 切换 4 个页面（扫描/规则/设置/关于），24px 外边距。

## Sidebar 规范

- **宽度**：固定 200px，不随窗口伸缩；背景色深色/浅色由 `Theme.isDark` 切换。
- **右侧 1px 分割线**：颜色 `Theme.isDark ? colorBorderDark : colorBorder`。
- **Logo 区**：高 64px，含 28x28 圆角图标块（主色填充 + 白色字母 "F"）+ "fuscan" 文字。
- **导航项（NavItem）**：
  - 高 40px，左侧 18px 缩进；选中态左 3px 主色指示条 + 浅色背景。
  - 图标（emoji 或 SVG 字符）+ 文字（13px）；选中态文字主色，未选中态次要色。
  - 颜色过渡用 `ColorAnimation { duration: 120 }`。
- **底部**：暗色模式切换（36x20 圆角开关 + "🌙 Dark Mode" 文字），点击切换 `Theme.isDark`。

## ContentArea 规范

- **背景透明**，继承 `ApplicationWindow` 背景色。
- **`StackView`**：24px 外边距，`initialItem` 为扫描页；页面切换由 `sidebar.currentPage` 驱动。
- **页面切换动画**：默认 `StackView.Transition`，不自定义复杂动画。

## 4 个页面结构

### 1. 扫描页（ScanPage）

扫描页内嵌 **`StackView` 三态切换**（不切换外层页面，仅内部状态）：

```
┌─────────────────────────────────────────┐
│  扫描页标题 + 状态徽标                   │
├─────────────────────────────────────────┤
│  内嵌 StackView 三态：                   │
│  - setup: 扫描目标 + 规则简述 + 扫描按钮 │
│  - scanning: 进度条 + 当前文件 + 统计    │
│  - results: 结果列表 + 简化详情          │
└─────────────────────────────────────────┘
```

- **标题区**：左 "扫描" 22px 加粗，右侧状态徽标（"就绪" 灰 / "扫描中" 绿 / "已完成" 蓝 / "已取消" 橙）。
- **配置态（setup）**：
  - 扫描模式选择（ComboBox：全盘/盘符/文件夹）+ 目标路径（ComboBox + 选择按钮）。
  - 已加载规则摘要（"N 条规则" + "管理规则" 链接跳到规则页）。
  - 扫描按钮（L1 主操作，48px 高，主色填充）。
- **扫描中态（scanning）**：
  - ProgressBar（确定模式）+ 当前文件名（截断 100 字符）。
  - 分类统计：已通过/命中/跳过/错误（彩色 Label 横排）。
  - 暂停/继续按钮 + 取消按钮（L2 次要，40px 高）。
- **结果态（results）**：
  - 结果列表（`ListView` + `ResultListModel`）：路径 + 命中数 + 严重度。
  - 选中后右侧详情：文件信息 + 命中表（规则名 + 严重度 + 上下文预览）。
  - 顶部操作栏：导出按钮（CSV/JSON）+ 重新扫描按钮。

### 2. 规则页（RulesPage）

- **规则文件列表**（左侧 1/3）：显示已加载规则文件 + 内置规则勾选 + 上移/下移/移除按钮 + "加载规则文件" 按钮。
- **规则列表**（右侧 2/3）：当前规则集所有规则（`RuleListModel`：名称 + 严重度 + 描述）。
- **顶部**：标题 "规则" + 规则总数标签。

### 3. 设置页（SettingsPage）

`ScrollView` + `ColumnLayout` 分组：

- **扫描设置**：扫描压缩包（Switch）、最大线程（SpinBox）、最大文件大小（SpinBox MB）、最大深度（SpinBox）。
- **文件类型**：勾选区（`ListView` + `ExtractorModel`，按扩展名勾选启用）。
- **忽略目录**：多行 `TextArea` 编辑（一行一个目录名）。
- **缓存设置**：启用缓存（Switch）+ 缓存路径（TextField + 选择按钮）。
- **路径历史**：扫描路径历史列表 + 清除按钮。
- **性能**：性能日志（Switch）。
- 修改即保存（每个控件 `onCheckedChanged`/`onValueChanged` 触发 `ConfigController.save()`）。

### 4. 关于页（AboutPage）

- fuscan logo + 版本号 + 描述 + 作者 + License。
- 用户手册入口（"打开用户手册 PDF" 按钮，调 `QDesktopServices.openUrl`）。
- 第三方依赖列表（简化版，从 `pyproject.toml` 读取）。

## 主题令牌绑定

所有色值/字号/圆角通过 `ThemeController` 暴露给 QML，QML 直接绑定（如 `color: Theme.colorPrimary`），禁止硬编码。令牌清单见 `src/fuscan/theme.py`。

## 性能约束

- **结果/规则/文件类型列表**必须用 `QAbstractListModel`/`QAbstractTableModel`，禁止 QML `ListModel` 动态 append 大量项。
- **扫描/统计/导出**走 `QThread` Worker（`ScanWorker`/`FileStatsWorker`/`ExportWorker`），QML 主线程仅渲染。
- **进度回调** 0.3s 节流后 emit 给 QML，避免高频刷新卡顿。
- **输入触发**的列表重建用 `QTimer.singleShot(300ms)` 防抖。

## 样式约束（GitHub Desktop 配色）

- 配色：背景 `#f6f8fa`（中性灰），主色 `#0366d6`（蓝），危险色 `#d73a49`（红），边框 `#e1e4e8`。
- 暗色模式：背景 `#1A1B26`，侧栏 `#16161E`，主色 `#7AA2F7`，文本 `#E0E0EF`。
- 字体层级：页面标题 22px > 区块标题 16px > 正文 13px > 辅助说明 12px；标题加粗。
- 速度档次色（T1-T5）：`#28A745`/`#17A2B8`/`#FFC107`/`#FD7E14`/`#DC3545`，定义在 `theme.py`。
- 空态引导文案居中 + 次要色。
