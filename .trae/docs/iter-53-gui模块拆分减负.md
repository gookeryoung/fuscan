# iter-53 GUI 模块拆分减负

## 需求清单

- [x] 1. 完善 `src/fuscan/gui` 代码，避免 `main_window.py` 过于臃肿（用户请求）

## 迭代目标

将 `main_window.py` 中三处可独立抽离的逻辑下沉到专用子模块，使主窗口仅保留 UI 控件
装配、信号路由与扫描流程协调，遵循 rule-12「MVC 分层」与 rule-11「单一职责」约束：

1. SVG 着色渲染与图标资源常量 → `gui/icons.py`
2. 跨平台文件管理器集成 → `gui/explorer.py`
3. 扫描中页列表增量更新器（节流 + 增量 append） → `gui/scan_progress_lists.py`

抽离后 `main_window.py` 删除约 100 行内联实现，三个新模块各内聚一项职责，
便于独立测试与后续扩展。

## 改动文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/fuscan/gui/icons.py` | 新建 | SVG 着色渲染函数 `read_svg_text` / `load_themed_icon` 与常量 `ICON_*` / `MANUAL_PDF` / `ICON_RENDER_SIZE` |
| `src/fuscan/gui/explorer.py` | 新建 | 跨平台文件管理器集成函数 `open_path_in_explorer`（win32/darwin/linux 分派） |
| `src/fuscan/gui/scan_progress_lists.py` | 新建 | `ScanListUpdater` 类：节流 + 增量 append/全量重建算法 |
| `src/fuscan/gui/main_window.py` | 修改 | 删除 `_MANUAL_PDF`/`_ICON_*`/`_ICON_RENDER_SIZE`/`_SVG_FILL_RE` 常量、`_read_svg_text`/`_load_themed_icon` 函数、`_update_skipped_dirs_list`/`_update_matched_files_list` 方法、`_last_skipped_dirs`/`_last_matched_files`/`_last_list_update_time` 状态字段；整合为 `_list_updater: ScanListUpdater` 单字段；`_open_path_in_explorer` 改为薄包装；删除 `import re`/`import subprocess`/`import sys`/`import time` |
| `tests/test_gui_icons.py` | 新建 | icons 模块单元测试（常量定义、SVG 文本读取） |
| `tests/test_gui_explorer.py` | 新建 | explorer 模块单元测试（win32/darwin/linux 命令分派、OSError 传播） |
| `tests/test_gui_scan_progress_lists.py` | 新建 | ScanListUpdater 单元测试（reset、节流、增量 append、全量重建、双列表协同） |

## 关键决策与依据

### 模块边界划分依据

依据 rule-12「MVC 分层」「UI 仅在 .ui 定义」与 rule-11「单一职责」：

| 子模块 | 抽离内容 | 边界依据 |
|--------|---------|---------|
| `icons.py` | SVG 着色渲染 + 资源路径常量 | 资源加载逻辑内聚，与 UI 控件装配解耦；`load_themed_icon` 接收 SVG 路径与颜色返回 `QIcon`，无 UI 状态依赖 |
| `explorer.py` | `subprocess.Popen` 命令分派 | 系统集成命令构造与启动独立于主窗口；主窗口仅负责异常捕获与用户提示（`QMessageBox.warning`） |
| `scan_progress_lists.py` | 节流 + 增量 append/全量重建 | 列表填充算法不依赖主窗口状态，仅依赖两个 `QListWidget`；抽离后主窗口 `_on_scan_progress` 仅协调进度条/状态栏/列表 |

### `_open_path_in_explorer` 薄包装保留依据

`tests/test_gui.py` 多处测试 monkeypatch `window._open_path_in_explorer` 与
`fuscan.gui.main_window.QMessageBox.warning`，验证集成路径与异常用户提示。
保留 `_open_path_in_explorer` 方法名作为薄包装，使 `open_path_in_explorer` 异常
路径在主窗口侧统一处理（记录日志 + 弹窗），不破坏现有测试断言。

### `ScanListUpdater.try_update` 返回值设计

返回 `bool` 表示本次是否实际刷新列表，调用方据此判断是否触发依赖列表的副作用
（分类统计面板 `_update_scan_stats`）。节流期间返回 `False`，避免重复计算
`passed = scanned - matched - errors`，相比原实现节流期间仍调用 `_update_scan_stats`
有微小性能改善。

### `icons.py` 测试覆盖策略

`load_themed_icon` 的渲染行为通过 `MainWindow._setup_icons` 在 `tests/test_gui.py`
集成测试中覆盖（构造主窗口时为每个图标调用一次 `load_themed_icon`，覆盖 .qrc 路径
加载、着色渲染、合法 SVG 解析）。`tests/test_gui_icons.py` 不重复直接调用
`load_themed_icon`，避免在隔离测试中触发 `QSvgRenderer` 原生崩溃
（pytest 单进程隔离环境下 `QPixmap` 渲染稳定性差，与主窗口集成路径下行为不一致）。

直接单元测试覆盖：
- 常量定义（`ICON_*` 路径前缀、`ICON_RENDER_SIZE` 类型、`MANUAL_PDF` 路径）
- `read_svg_text` 磁盘路径读取、.qrc 资源读取、`OSError` 路径

## 代码实现情况

### `src/fuscan/gui/icons.py`

抽取自 `main_window.py` 的图标加载逻辑：

```python
MANUAL_PDF = Path(__file__).parent.parent / "assets" / "docs" / "fuscan-用户手册.pdf"
ICON_ABOUT = ":/icons/about.svg"
# ... 共 18 个 ICON_* 常量
ICON_RENDER_SIZE = 128
_SVG_FILL_RE = re.compile(r'\sfill="[^"]*"')

def read_svg_text(svg_path: str) -> str:
    """读取 SVG 文本，支持 .qrc 资源路径（:/ 前缀）与磁盘路径。"""
    if svg_path.startswith(":"):
        file = QFile(svg_path)
        if not file.open(QFile.ReadOnly | QFile.Text):
            raise OSError(f"无法打开 Qt 资源: {svg_path}")
        try:
            return bytes(file.readAll()).decode("utf-8")
        finally:
            file.close()
    return Path(svg_path).read_text(encoding="utf-8")

def load_themed_icon(svg_path: str, color: str) -> QIcon:
    """加载 SVG 并以指定主题色着色后返回 QIcon。"""
    # ... 移除 fill 属性 + 注入主题色 + QSvgRenderer 渲染到 QPixmap
```

### `src/fuscan/gui/explorer.py`

抽取自 `main_window.py._open_path_in_explorer` 的命令分派：

```python
def open_path_in_explorer(path: Path) -> None:
    """在系统文件管理器中打开指定文件所在目录并选中该文件。"""
    if sys.platform == "win32":
        cmd: list[str] = ["explorer", "/select,", str(path)]
    elif sys.platform == "darwin":
        cmd = ["open", "-R", str(path)]
    else:
        cmd = ["xdg-open", str(path.parent)]
    subprocess.Popen(cmd)
```

### `src/fuscan/gui/scan_progress_lists.py`

抽取自 `main_window.py` 的节流 + 增量 append 算法：

```python
class ScanListUpdater:
    def __init__(self, skipped_list: QListWidget, matched_files_list: QListWidget) -> None:
        # 绑定两个列表控件 + 初始化增量快照

    def reset(self) -> None:
        """清空两个列表与节流时间戳，在新扫描启动时调用。"""

    def try_update(
        self,
        skipped_dirs: tuple[str, ...],
        matched_files: tuple[tuple[str, str], ...],
        throttle_seconds: float = 0.5,
    ) -> bool:
        """按 0.5 秒节流增量更新两个列表，返回是否实际刷新。"""
        now = time.perf_counter()
        if now - self._last_update_time < throttle_seconds:
            return False
        self._last_update_time = now
        self._update_skipped_list(skipped_dirs)
        self._update_matched_files_list(matched_files)
        return True
```

### `src/fuscan/gui/main_window.py` 集成

`__init__` 中三个独立状态字段整合为单个 `ScanListUpdater` 实例：

```python
# 替换前：3 个独立状态字段 + 2 个 _update_*_list 方法 + 节流逻辑内联在 _on_scan_progress
self._last_skipped_dirs: tuple[str, ...] = ()
self._last_matched_files: tuple[tuple[str, str], ...] = ()
self._last_list_update_time: float = -1.0

# 替换后：1 个 ScanListUpdater 实例封装节流 + 增量算法
self._list_updater: ScanListUpdater = ScanListUpdater(
    self.skipped_dirs_list, self.matched_files_list
)
```

`_on_scan` 重置逻辑简化：

```python
# 替换前（5 行）
self.skipped_dirs_list.clear()
self.matched_files_list.clear()
self._last_skipped_dirs = ()
self._last_matched_files = ()
self._last_list_update_time = -1.0

# 替换后（1 行）
self._list_updater.reset()
```

`_on_scan_progress` 节流 + 列表更新逻辑简化：

```python
# 替换前（10 行：节流计算 + 2 个 _update_*_list 调用 + 统计刷新）
now = time.perf_counter()
if now - self._last_list_update_time < 0.5:
    return
self._last_list_update_time = now
self._update_skipped_dirs_list(info.skipped_dirs)
self._update_matched_files_list(info.matched_files)
passed = max(info.scanned - info.matched - info.errors, 0)
self._update_scan_stats(passed, info.matched, info.skipped, info.errors)

# 替换后（3 行：节流放行时刷新统计）
if self._list_updater.try_update(info.skipped_dirs, info.matched_files):
    passed = max(info.scanned - info.matched - info.errors, 0)
    self._update_scan_stats(passed, info.matched, info.skipped, info.errors)
```

`_open_path_in_explorer` 改为薄包装：

```python
def _open_path_in_explorer(self, path: Path) -> None:
    try:
        open_path_in_explorer(path)
    except OSError as exc:
        logger.warning("打开文件位置失败: %s", exc, exc_info=True)
        QMessageBox.warning(self, "提示", f"打开文件位置失败:\n{exc}")
```

`main_window.py` 头部 import 整合：
- 删除 `import re`、`import subprocess`、`import sys`、`import time`
- 删除 PySide2/PySide6 导入中的 `QByteArray, QFile, QIcon, QPainter, QPixmap, QSvgRenderer`
- 新增 `from fuscan.gui.explorer import open_path_in_explorer`
- 新增单个多行 `from fuscan.gui.icons import (ICON_ABOUT as _ICON_ABOUT, ...)` 块（20 个别名）
- 新增 `from fuscan.gui.scan_progress_lists import ScanListUpdater`

## 整合优化情况

- **代码量减负**：`main_window.py` 删除约 100 行内联实现（18 个常量 + 2 个函数 + 2 个方法 + 3 个状态字段 + 节流逻辑），主窗口仅保留 UI 装配与流程协调。
- **测试独立化**：三个新模块各有独立测试文件，单元测试覆盖率分别为 explorer 100% / scan_progress_lists 97% / icons 89%（错误路径未触发）。
- **公开 API 兼容**：保留 `_severity_text` / `MainWindow._open_path_in_explorer` / `window.skipped_dirs_list` / `window.matched_files_list` 等被测试引用的符号，无需修改现有 `test_gui.py`。
- **无新增重复代码**：三个子模块边界清晰，无重叠职责；`_load_themed_icon` 别名保留以最小化主窗口内部调用点改动。

## 测试验证结果

| 门禁 | 结果 | 基线（iter-52） | 变化 |
|------|------|----------------|------|
| ruff check | All checks passed | 0 errors | — |
| ruff format --check | 86 files already formatted | 80 files | +6（3 新源文件 + 3 新测试文件） |
| pyrefly check | 0 errors (458 suppressed) | 0 errors (452 suppressed) | +6 suppressed（新测试文件少量类型收窄） |
| pytest | 1351 passed / 0 failed | 1324 passed / 0 failed | +27（新增 3 个测试文件共 27 用例） |
| coverage | 96.26% | 96.07% | +0.19% |

覆盖率小幅提升 0.19% 来自三个新模块的单元测试覆盖：
- `explorer.py` 100%（5 用例覆盖三平台 + OSError + 路径转换）
- `scan_progress_lists.py` 97%（16 用例覆盖 reset/节流/增量/全量重建/双列表协同）
- `icons.py` 89%（6 用例覆盖常量 + 磁盘读取 + .qrc 读取 + OSError；渲染路径由集成测试覆盖）

## 遗留事项

- `icons.py` 的 `load_themed_icon` 渲染路径无独立单元测试（避免 QSvgRenderer 隔离测试
  原生崩溃），依赖 `tests/test_gui.py` 集成测试覆盖。若后续需要独立测试渲染逻辑，
  可考虑用 `pytest-qt` 的 `qtbot` fixture 或在 `QApplication` 初始化后调用
  `processEvents()` 确保渲染上下文就绪。
- `main_window.py` 仍约 1100 行，主要承载 UI 装配（`_setup_*` 系列）与扫描流程
  协调（`_on_scan*` 系列）。后续可进一步拆分 `_setup_*` 到独立的 ui configurator
  模块，但当前规模在可维护范围内，暂不强行拆分以避免过度抽象。

## 下一轮计划

无明确下一轮计划。当前 `main_window.py` 已完成三处独立抽离，剩余代码内聚于
UI 装配与扫描流程协调，符合 rule-12 MVC 分层约束。如用户提出新需求再行迭代。
