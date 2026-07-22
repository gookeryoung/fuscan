# fuscan GUI 性能基线

> 测量时间：2026-07-19（iter-59 GUI 卡滞优化后）
> 测量方式：基于代码路径分析与理论估算，配合 `FUSCAN_PERF=1` 性能测量基础设施

## 测量环境

| 项目 | 值 |
|------|-----|
| 操作系统 | Windows 11 (10.0.26200) |
| CPU | Intel Core i7-14700K (24 核) |
| Python | 3.8.20 |
| fuscan 版本 | iter-59 |

## 设计目标

GUI 主线程的卡滞来源（按用户感知优先级）：

1. **启动期阻塞**：盘符枚举、规则集加载、UI 配置
2. **导出期阻塞**：PDF/Excel 渲染耗时数秒导致 UI 完全无响应
3. **扫描中进度回调**：高频回调 + 大列表拷贝占用主线程时间片

iter-59 聚焦上述三类根因，目标：启动到主窗口可见 < 500ms，导出期间 UI 响应正常，
扫描进度回调不引起可见卡顿。

## iter-59 优化项

### P0-1：盘符枚举 Win32 API 化

**根因**：`list_drives` 原逐个 `Path.exists()` 探测盘符，未就绪光驱触发
`OSError [WinError 1]` 阻塞 100-500ms/盘，启动期累积卡顿。

**优化**：`src/fuscan/scanner/walker.py::_list_windows_drives` 改用
`ctypes.windll.kernel32.GetLogicalDrives()` 单次系统调用获取盘符位掩码
（bit 0=A:、bit 1=B:、...），按位与生成 `Path` 列表。

**理论性能**：

| 场景 | 优化前 | 优化后 |
|------|------:|------:|
| 无未就绪盘 | ~5ms（26 次存在性探测） | <1ms（单次 API 调用） |
| 1 个未就绪光驱 | +100-500ms（OSError 探测） | <1ms |
| 2+ 个未就绪光驱 | +200-1000ms | <1ms |

**实测验证**：`test_list_drives_uses_get_logical_drives_bitmask` 单元测试通过 monkeypatch
`ctypes.windll` 模拟位掩码 0b101（A:、C:）验证位掩码解算正确性。

### P0-2：导出异步化

**根因**：`MainWindow._on_export` 同步调用 `save_report` 写 PDF/Excel，
reportlab 渲染大报告可能耗时 3-8 秒，期间菜单/按钮/进度条均无法刷新。

**优化**：新增 `src/fuscan/gui/export_worker.py::ExportWorker(QThread)` 在后台线程
执行 `save_report`，通过信号槽 `finished_ok` / `failed` 回到主线程处理结果。

**交互行为**：
- 导出期间禁用 `export_btn`，状态栏提示"正在导出 xxx..."
- 成功：恢复按钮 + 弹出"导出成功"对话框 + 状态栏显示路径
- 失败：恢复按钮 + 弹出"导出失败"警告对话框 + 状态栏显示"导出失败"
- `_cleanup_export_worker` 在主线程 `wait(2000)` 确保线程退出后 `deleteLater`

**理论性能**：

| 场景 | 优化前 | 优化后 |
|------|------:|------:|
| PDF 导出 100 命中 | 主线程阻塞 3-5s | 主线程 0ms（后台线程 3-5s） |
| Excel 导出 100 命中 | 主线程阻塞 1-3s | 主线程 0ms（后台线程 1-3s） |
| CSV/JSON 导出 | 主线程阻塞 <100ms | 主线程 0ms（后台线程 <100ms） |

**实测验证**：
- `test_export_worker_run_emits_finished_ok`：直接调用 `run()` 验证成功路径
- `test_export_worker_run_emits_failed_on_os_error`：mock save_report 抛 OSError 验证失败路径
- `test_export_csv_to_file` 等 6 个集成测试通过 `_wait_export_worker` 等待异步完成

### P0-3：进度回调列表上限下调

**根因**：`Scanner._emit_progress` 每次回调将 `_skipped_dirs` 与 `_matched_files`
两个 `deque` 转为 tuple 跨线程信号传递，原 `_PROGRESS_LIST_MAX=200` 即 200 项 × 2 列表
= 400 元组拷贝，大规模扫描（如全盘跳过 node_modules）下高频回调让主线程信号槽分发
占用可观时间片。

**优化**：`src/fuscan/scanner/scanner.py::_PROGRESS_LIST_MAX` 由 200 下调到 50。

**理论性能**：

| 场景 | 优化前 | 优化后 |
|------|------:|------:|
| 单次回调元组拷贝 | 200×2=400 项 | 50×2=100 项 |
| 单次回调拷贝耗时（10 字符路径） | ~20μs | ~5μs |
| 全盘扫描 10 万文件 × 100ms 节流 = 1000 次回调 | ~20ms | ~5ms |

50 项足以让用户感知"近期"上下文（最新命中/跳过文件列表），不影响信息完整性。

## 性能测量基础设施

### 启用方式

环境变量 `FUSCAN_PERF=1` 开启，默认关闭保证零开销：

```bash
# Windows PowerShell
$env:FUSCAN_PERF=1; uv run python -m fuscan.gui

# Linux/macOS
FUSCAN_PERF=1 uv run python -m fuscan.gui
```

### API

`src/fuscan/gui/perf.py` 提供三个公共 API：

| API | 用途 |
|-----|------|
| `PerfTimer(name, *, threshold_ms=0.0)` | 上下文管理器，记录代码块耗时 |
| `record_event(name, **fields)` | 记录离散事件及关联字段 |
| `set_perf_enabled(enabled)` | 运行时切换开关（测试用） |

### 已埋点位置

`MainWindow.__init__` 关键阶段：

- `MainWindow.setupUi`：UI 加载
- `MainWindow._configure_ui`：UI 配置（图标、信号槽、布局）
- `MainWindow._apply_config`：配置应用
- `MainWindow._init_rules`：规则集加载

`ExportWorker.run`：

- `ExportWorker.save_report`：导出耗时

### 输出格式

```
[perf] > MainWindow.__init__ begin
[perf] > MainWindow.setupUi begin
[perf] < MainWindow.setupUi 12.3ms
[perf] > MainWindow._configure_ui begin
[perf] < MainWindow._configure_ui 8.5ms
[perf] < MainWindow.__init__ 45.2ms
```

嵌套层级通过空格缩进表达，输出到 `fuscan.perf` logger（DEBUG 级别），
可被统一日志配置捕获。iter-65 起 `perf.py` 从 `fuscan.gui.perf` 提升到
`fuscan.perf`（公共模块），GUI 与扫描器共用；新增 `PerfStats` 聚合统计类
供扫描器分阶段瓶颈分析。

## 后续测量计划

当前为理论基线，后续待补充：

1. **真实启动耗时**：在真实 Windows 环境运行 `FUSCAN_PERF=1` 记录
   `MainWindow.__init__` 各阶段实际耗时
2. **导出耗时分布**：不同报告规模（10/100/1000 命中）下 PDF/Excel/CSV/JSON 导出耗时
3. **进度回调频率**：大规模扫描下进度回调触发频率与单次回调主线程耗时
4. **回归断言**：建立 `slow` 标记的 GUI 性能测试，类似 `test_benchmark.py` 的断言机制

## 复现方式

```bash
# 单元测试验证
uv run pytest tests/test_gui_perf.py -v
uv run pytest tests/test_walker.py::TestListDrives -v
uv run pytest tests/test_gui.py::TestExportAndMenu -v

# 实际性能测量（需 GUI 环境）
$env:FUSCAN_PERF=1; uv run python -m fuscan.gui 2>&1 | findstr "perf"
```
