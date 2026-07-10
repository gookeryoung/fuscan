# iter-13：杀毒软件风格 UI 重构

## 本轮目标

将 GUI 扫描界面重构为杀毒软件风格，支持三种扫描模式（全盘扫描/选择盘符/选择文件夹），
使扫描操作和结果列表更为醒目。

## 改动文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `src/uniscan/scanner/walker.py` | 修改 | 新增 `list_drives()` 跨平台盘符枚举 |
| `src/uniscan/scanner/__init__.py` | 修改 | 导出 `list_drives` |
| `src/uniscan/gui/worker.py` | 重写 | `ScanWorker` 参数从 `root: Path` 改为 `roots: List[Path]`，支持多根路径扫描与结果合并 |
| `src/uniscan/config.py` | 修改 | 新增 `scan_mode` 与 `last_drive` 字段（注：`window_geometry`/`window_state`/`splitter_sizes` 的默认值由用户提交 `38fbc0a` 改为 `default_factory`） |
| `src/uniscan/gui/main_window.py` | 重写 | 杀毒软件风格 UI：模式卡片 + 醒目扫描按钮 + 大进度条 + QSS 样式 |
| `src/uniscan/watcher/tray.py` | 修改 | `_init_main_window(show=False)` 时显式 `hide()`，避免 `showMaximized` 副作用 |
| `tests/test_gui.py` | 修改 | 更新 `ScanWorker` 测试为 `roots` 参数；新增 `TestScanWorkerMultiRoot`、`TestScanMode`、`TestScanModePersistence` |
| `tests/test_config.py` | 修改 | 更新默认值断言以匹配 `default_factory` 变更 |

## 关键决策与依据

### 1. 三种扫描模式

- **全盘扫描（full）**：调用 `list_drives()` 枚举所有盘符，`ScanWorker` 依次扫描并合并结果
- **选择盘符（drive）**：`QComboBox` 展示可用盘符，扫描单个盘符
- **选择文件夹（folder）**：保持原有路径选择行为（历史下拉 + 文件对话框）

选择按钮用 `QButtonGroup`（exclusive）管理，三个 `QPushButton#modeCard` 可选中样式。

### 2. ScanWorker 多根路径支持

`ScanWorker.__init__` 参数从 `root: Path` 改为 `roots: List[Path]`，
`run()` 依次扫描所有根路径，累加统计并合并 `results`，
最终构造单一 `ScanReport`（单路径时 `root` 为实际路径，多路径时为 `Path("（多路径）")`）。

### 3. 目标选择器可见性

`_update_target_visibility()` 根据扫描模式切换可见性：
- full：隐藏路径行与盘符下拉
- drive：显示盘符下拉，隐藏路径行
- folder：显示路径行，隐藏盘符下拉

### 4. 扫描按钮就绪逻辑

`_update_scan_button()` 按模式判断就绪状态：
- full：有规则即可
- drive：有规则且有盘符
- folder：有规则且有路径

### 5. QSS 样式

- 模式卡片：选中时蓝色边框 + 浅蓝背景 + 加粗
- 扫描按钮：绿色背景 + 白色文字 + 大字号 + 悬停/按下变色
- 进度条：绿色 chunk + 圆角
- 结果树：交替行颜色 + 增大行高

### 6. tray.py hide 修复

`MainWindow._apply_config()` 在 `window_state == "maximized"` 时调用 `showMaximized()`，
导致 tray 的 `_init_main_window(show=False)` 创建窗口后变为可见。
修复：`show=False` 时显式调用 `hide()`。

## 验证结果

- ruff：全部通过
- pytest：437 passed, 1 skipped
- coverage：88.35%（branch）

## 新增测试

- `TestScanWorkerMultiRoot`：多根路径扫描（2 个测试）
- `TestScanMode`：模式切换、可见性、按钮状态、根路径构造（9 个测试）
- `TestScanModePersistence`：scan_mode 与 last_drive 持久化（4 个测试）

## 遗留事项

- 无（iter-06～10 已归档至 `.trae/skills/uniscan-development.md`）
