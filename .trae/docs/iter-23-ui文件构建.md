# 迭代 23：UI 文件构建

## 迭代目标

将 GUI 界面从 Python 硬编码构建改为使用 Qt `.ui` 文件定义，通过 pyside2-uic 编译为 `_ui.py` 加载。同时将 QSS 样式表迁移到独立 `.qss` 文件，菜单栏和工具栏纳入 `.ui` 文件。

## 改动文件清单

### 新增文件

- `src/uniscan/gui/styles.qss`：GitHub Desktop 风格样式表（从 main_window.py 的 _apply_qss 提取）
- `src/uniscan/gui/main_window.ui`：主窗口 UI 定义（QMainWindow + menubar + toolbar + 5 区布局）
- `src/uniscan/gui/main_window_ui.py`：pyside2-uic 编译产物
- `src/uniscan/gui/detail_dialog.ui`：命中详情对话框 UI 定义
- `src/uniscan/gui/detail_dialog_ui.py`：pyside2-uic 编译产物
- `src/uniscan/gui/rule_editor.ui`：规则编辑器 UI 定义
- `src/uniscan/gui/rule_editor_ui.py`：pyside2-uic 编译产物

### 修改文件

- `src/uniscan/gui/main_window.py`：移除 _init_ui/_build_*/_apply_qss 等方法，改为 _bind_widgets + _configure_ui 模式
- `src/uniscan/gui/detail_dialog.py`：移除 _init_ui 方法，改为 _bind_widgets + _configure_ui 模式
- `src/uniscan/gui/rule_editor.py`：移除 _init_ui 方法，改为 _bind_widgets + _configure_ui 模式
- `src/uniscan/gui/app.py`：加载 styles.qss 应用程序级样式表，事件循环双兼容（exec/exec_）
- `tests/test_gui.py`：FakeApp/ExistingApp 添加 setStyleSheet 方法
- `Makefile`：新增 `ui` 目标编译 .ui 文件

## 关键决策与依据

### 1. 别名模式保持业务逻辑兼容

`.ui` 编译生成的 `Ui_MainWindow` 类将部件作为属性挂在 `self._ui` 上。为避免重写大量业务逻辑（信号槽、状态更新等），采用别名模式：

```python
def _bind_widgets(self) -> None:
    ui = self._ui
    self._scan_btn = ui.scan_btn
    self._stop_btn = ui.stop_btn
    # ...
```

这样所有 `self._xxx` 引用保持不变，测试代码也无需修改。

### 2. layout stretch vector 在代码中设置

pyside2-uic 不支持 QBoxLayout 的 `<property name="stretch"><vector>..</vector></property>` 属性（编译报错 "Unexpected element vector"）。改在 `_configure_ui` 中用 `layout.setStretch(index, factor)` 设置。

### 3. QButtonGroup 在代码中创建

`.ui` 文件无法声明 QButtonGroup，三个扫描模式按钮的互斥组在 `_configure_ui` 中用代码创建并 addButton。

### 4. 信号槽连接保留在代码中

虽然 `.ui` 支持声明信号槽连接，但为保持业务逻辑可读性和测试可控性，所有信号槽连接保留在 `_configure_ui` 中用代码完成。

### 5. objectName 使用 snake_case

与 Python 属性命名一致（如 `scan_btn`、`result_tree`），便于别名映射。三个模式按钮用独立 objectName（`full_btn`/`drive_btn`/`folder_btn`），QSS 用三个选择器分别样式。

### 6. QSS 迁移到独立文件

`_apply_qss` 方法（约 220 行）移除，样式表提取到 `styles.qss`，由 `app.py` 在启动时加载到 QApplication 级别。这样修改样式无需改动 Python 代码。

## 验证结果

- **ruff check**：main_window.py 和 detail_dialog.py 有预先存在的 UP006/UP045 类型注解风格警告（与重构前一致），rule_editor.py 全部通过
- **pytest**：565 passed, 2 skipped（全部通过）
- **覆盖率**：90.61%（高于基线 90.32%）
- **GUI 测试**：192 passed, 1 skipped（全部通过）

## 遗留事项

1. UP006/UP045 类型注解风格警告（`List` → `list`、`Optional` → `X | None`）在 main_window.py 和 detail_dialog.py 中预先存在，需后续统一修复
2. pyproject.toml 未注册 `gui` marker，导致 PytestUnknownMarkWarning（预先存在）
3. 覆盖率 95% 门槛未达到（90.61%），预先存在的问题，与本次重构无关
