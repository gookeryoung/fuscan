---
name: "rule-12-pyside-dev"
alwaysApply: true
---

# PySide + QML 开发规则

fuscan GUI 采用 **PySide2 + QML** 范式（参考 `ref/pyside2_qml_dashboard`），以声明式 QML 描述视图、Python `QObject` 充当控制器与数据模型，分离关注点并获得 GPU 加速的流畅渲染。

## 架构与分层

- **UI 仅在 `.qml` 文件定义**，禁止 `.py` 内创建/布局 QML 控件或动态构造视觉树。
- **三层 MVC**：
  - 视图层（`.qml`）：仅负责呈现与交互，不含业务逻辑；格式化逻辑下沉到 Python `@dataclass` 方法或 controller。
  - 控制层（`src/fuscan/gui/controllers/*.py`）：`QObject` 子类，通过 `Property`/`Signal`/`Slot` 暴露状态与操作给 QML；不持有 QML 控件引用。
  - 模型层（`QAbstractListModel`/`QAbstractTableModel`）：大数据量（结果、规则、文件类型）必须用 Model，禁止 QML 侧 `ListModel` 动态 append 大量元素。
- **跨线程通信**：工作线程（`QThread`）通过信号槽通知 UI 线程，槽加 `@Slot()` 装饰；**禁止工作线程直接访问 QML 控件或 controller 属性**，仅 emit 信号。
- **信号命名**：信号过去时（`scan_finished`/`progress_changed`），槽用 `on_<signal>` 或 `<subject>Changed`；高频信号在 controller 内节流（`QTimer.singleShot(300ms)`）后再 emit 给 QML。
- **信号参数**：用 `object` 传递 frozen `@dataclass` 或 `dataclass(frozen=True)` 实例，避免裸 `dict`/`list`。
- **系统集成**：
  - 资源管理器定位封装公共方法（`explorer /select,` / `open -R` / `xdg-open`）失败 warning 不抛异常。
  - 打开外部 PDF/URL 用 `QDesktopServices.openUrl`，不内置渲染。
  - 文件路径选择用 `QFileDialog`，目录选择同理；不在 QML 内自实现。

## 配置与资源

- **设计令牌**集中定义在 `src/fuscan/gui/theme.py`，QML 通过 `ThemeController` 单例读取：
  - 色彩（主色/背景/文本/边框/危险/警告/成功/速度档次 T1-T5）、排版（字体族/字号/字重）、间距、圆角、按钮层级。
  - 令牌以 `@Property` 暴露为 `QColor`/`int`/`str`，QML 直接绑定（如 `color: Theme.colorPrimary`）。
  - **禁止在 QML 或 Python 中硬编码色值/字号/圆角**，须引用 `theme.py` 中的对应令牌；新增令牌同步追加到 `__all__` 与 `ThemeController` 的 `@Property`。
  - 例外：QML 内 `ColorAnimation` 的目标色可引用 `Theme.isDark ? colorA : colorB` 三元表达式，色值仍来自 `ThemeController`。
- **暗色模式**：`ThemeController.isDark` 双向绑定，QML 通过 `Theme.isDark ? dark : light` 切换；切换时仅 emit `themeChanged`，QML 绑定自动刷新，不重建控件。
- **按钮三级层级差异化**（QML 通过 `Theme.btnHeightPrimary/Secondary/Ghost` 等令牌绑定 `height`/`radius`/`color`）：
  - **L1 主操作**（`scan`/`rescan`/`export`）：48px 高，主色填充或主色边框。
  - **L2 次要**（`pause`/`cancel`/`selectPath`）：40px 高，灰边框。
  - **L3 辅助**（详情导航/规则管理/页面内辅助按钮）：32px 高，扁平兜底。
- **布局**用 QML `Layout`/`Anchor`，禁止绝对像素坐标，确保高 DPI 与跨平台适配；`ApplicationWindow` 自带缩放。
- **图标**统一放 `src/fuscan/assets/icons/`，通过 `qrc://` 引用；`QIcon.isNull()` 校验仅用于菜单/托盘。
- **资源**：10MB 以下图标/字体纳入 `.qrc`；10MB 以上（如用户手册 PDF）用 `QResource` 加载，避免占用内存。
- **QML 文件**须在 `pyproject.toml` 的 `[tool.hatch.build.targets.wheel.force-include]` 中声明，确保随包分发。

## 控件与窗口生命周期

- **页面复用**：`StackView` 切换页面时，已创建的 Component 实例复用（`StackView.replace` 而非 `push` 重复创建）；页面 `visible` 切换优于销毁重建。
- **资源释放**：扫描完成/窗口关闭时，controller 显式 `cleanup()` 释放 `QThread`/`CacheStore`/文件句柄；不依赖 GC。
- **大文件预览**：QML 侧 `TextArea`/`TextEdit` 按需加载（截断显示），不一次性塞入超大文本。

## 性能

- **高耗时操作下沉**：扫描/统计/导出/规则加载等 I/O 与 CPU 密集任务一律走 `QThread` Worker（`ScanWorker`/`FileStatsWorker`/`ExportWorker`），禁止在 QML 主线程执行。
- **高性能库优先**：表格用 `python-calamine`、PDF 用 `pdf_oxide`、ZIP 用 `zipfile`（zlib C 后端），避免纯 Python 实现；新依赖须 GIL 释放验证。
- **列表性能**：结果/规则/文件类型列表必须用 `QAbstractListModel`/`QAbstractTableModel`，`data()` 按 role 返回；禁止 QML 侧 `ListElement` 动态 append 大量项。
- **节流**：输入触发的列表重建用 `QTimer.singleShot(300ms)` 防抖；扫描进度回调 0.3s 节流 emit 给 QML。
- **批量更新**：`QAbstractItemModel` 批量插入用 `beginInsertRows`/`endInsertRows` 包裹，避免逐项 `dataChanged`。
- **避免重复连接**：每个信号槽对每个信号只连接一次；`QObject` 生命周期由 parent 管理。

## 详细参考

本规则为硬约束简表，2 区布局规范（Sidebar + ContentArea）、QML 页面结构、controller 实现模式与代码模板见 `fuscan-gui-layout` SKILL（调用指引见 `rule-03-触发场景.md`）。fuscan 采用 WSL Dashboard 风格（参考 `ref/pyside2_qml_dashboard`），配色沿用 GitHub Desktop 风格（详见 `theme.py`），按钮三级层级差异化设计详见「配置与资源」章节。
