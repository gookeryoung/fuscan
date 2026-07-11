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

- **ruff check**：全部通过（UP006/UP045 已修复，*_ui.py 生成文件已排除）
- **ruff format**：全部通过
- **pytest**：649 passed, 2 skipped（全部通过）
- **覆盖率**：95.61%（达到 95% 门槛）

## 遗留事项处理（iter-23 后续补充）

### 1. UP006/UP045 类型注解修复 ✅

使用 `ruff check --fix --unsafe-fixes` 全代码库统一修复：`List[X]` → `list[X]`、`Optional[X]` → `X | None`。
项目所有模块均使用 `from __future__ import annotations`，现代类型语法在 Python 3.8 下安全。

### 2. gui marker 注册 ✅

pyproject.toml 的 `[tool.pytest.ini_options].markers` 已注册 `gui` marker，消除 PytestUnknownMarkWarning。

### 3. 覆盖率提升至 95% ✅

从 90.61% 提升至 95.61%，新增测试覆盖：
- `tests/test_watcher.py`：IncrementalScanner 异常路径（6 个测试）、FileMonitor 边界条件（6 个测试）
- `tests/test_matchers.py`：匹配器边界条件（8 个测试）
- `tests/test_extractors.py`：WPS 提取器异常路径（5 个测试）
- `tests/test_archive.py`：压缩包读取器异常路径（26 个测试）
- `tests/test_cli.py`：CLI 异常路径（5 个测试）
- `tests/test_gui.py`：ScanWorker 异常路径（5 个测试）
- `tests/test_scanner.py`：Scanner 异常路径（6 个测试）
- `src/uniscan/extractors/text.py`：删除不可达死代码（latin-1 fallback 永不失败）
- `src/uniscan/cli.py`：删除不可达死代码（required=True 时 parser.print_help 不可达）

### 4. 图标美化 ✅

为扫描控制按钮添加 SVG 图标（`src/uniscan/assets/icons/` 下的 scan.svg、stop.svg、pause.svg、rescan.svg），
采用磁盘路径加载方式（QIcon(str(path))），扫描状态切换时图标随之变化。

### 5. ruff per-file-ignores 优化

- `**/*_ui.py`：忽略 F401/F403/F405（pyside2-uic 生成文件使用 star import）
- `**/tests/**`：增加 ARG005（测试 lambda 回调常用未使用参数）、PLR0913（参数化测试参数多）
- `cli.py`/`matchers.py`：忽略 PLR0911（命令分发/模式匹配自然多返回语句）
- `scanner.py`/`worker.py`：忽略 PLR0913（构造函数参数多）
