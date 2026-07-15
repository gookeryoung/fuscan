---
name: "rule-12-pyside-dev"
alwaysApply: true
---

# PySide 开发规则

## 架构与分层

- UI 仅在 `.ui` 定义，禁止 `.py` 内实现 UI 初始配置代码。
- MVC 分层：UI、业务逻辑、数据访问分离；大数据量优先用 `QAbstractItemModel` 而非 `QTreeWidget`/`QListWidget` 便利类。
- 用信号槽解耦控件；跨线程必走信号槽，槽建议加 `@Slot()` 装饰，禁止工作线程直接访问 UI。
- 信号过去时命名（`scan_finished`），槽用 `on_<信号>`；高频信号槽内节流；信号参数用 frozen dataclass。
- 显示层格式化逻辑下沉到后端 `@dataclass` 方法，禁止 UI 层重复实现。
- 系统集成：资源管理器定位封装公共方法（`explorer /select,` / `open -R` / `xdg-open`）失败 warning 不抛异常；打开外部 PDF/URL 用 `QDesktopServices.openUrl`，不内置渲染。

## 配置与资源

- 颜色、尺寸在 `theme.py` 定义；其余配置在 `config.py` 定义。QSS 在 QApplication 级别应用，用 `${TOKEN}` 引用，禁止硬编码；语义不同的颜色用独立令牌（`COLOR_SPLITTER` ≠ `COLOR_BORDER`）。
- 布局用 `QLayout` 管理器，禁止绝对像素坐标，确保高 DPI 与跨平台适配。
- 同一菜单图标风格一致；窗口图标常量化指向 `assets/icons/`，`QIcon.isNull()` 校验。
- 10MB 以下图标、图片、字体、QSS 纳入 `.qrc` 资源文件；10MB 以上用 `QResource` 加载，避免占用内存。

## 控件与窗口生命周期

- 复用控件（hide/show + 刷数据），禁止反复创建销毁。
- 临时子对话框传 `parent` 时须设 `WA_DeleteOnClose`；长期复用窗口不设，用 hide/show。
- 资源在 `closeEvent` 释放，不依赖 GC；大文件预览（`QTextDocument`）按需加载，关闭即释放。

## 性能

- 输入触发的列表重建用 `QTimer.singleShot(300ms)` 防抖；重置数据时 `stop()` 挂起 timer，避免重复刷新。
- 批量插入 `QTreeWidget`/`QListWidget` 用 `setUpdatesEnabled(False)` + `try/finally`。
