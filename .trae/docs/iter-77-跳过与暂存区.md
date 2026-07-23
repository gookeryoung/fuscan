# iter-77：跳过标记与暂存区

## 需求清单

- [x] 移除结果内容预览下方的「备注/批注/导出说明」QPlainTextEdit
- [x] 在原位替换为操作按钮行：移动至暂存区、标记为跳过
- [x] 暂存区由用户配置，默认路径为剩余空间最大的盘符下 `.fuscan-cache` 文件夹
- [x] 在模型中增加「标记为跳过」标识，后续扫描直接跳过
- [x] 统计数据增加「用户跳过」类别

## 迭代目标

详情区原「备注/批注/导出说明」字段（`note_edit`）从未被业务逻辑消费，仅为
装饰性输入框，占用详情区底部空间。本次迭代将其替换为两个操作按钮：

1. **移动至暂存区**：将当前结果文件移动到暂存区目录（用户配置或自动探测剩余
   空间最大的盘符下 `.fuscan-cache`），从结果树移除该项
2. **标记为跳过**：将当前结果路径写入 `SkipStore`，下次扫描时 Scanner 在 walk
   阶段直接跳过该路径，单独计入「用户跳过」统计类别

模型层 `ScanStats` / `ProgressInfo` / `ScanResult` 新增 `user_skipped` 字段，
GUI 统计面板与状态栏同步显示。

## 改动文件清单

| 文件 | 改动内容 |
|------|---------|
| `src/fuscan/skip_store.py` | **新增**：`SkipStore` 类（JSON 持久化用户跳过路径），`default_skip_store_path()`，原子写入（`Path.replace`） |
| `src/fuscan/scanner/result.py` | `ScanResult` 新增 `user_skipped: bool = False`；`ScanStats` 新增 `user_skipped: int = 0`；`ProgressInfo` 新增 `user_skipped: int = 0` 与 `phase: str = "scan"` 字段；`summary()` 文案增加用户跳过类别 |
| `src/fuscan/config.py` | `Config` 新增 `staging_dir: str \| None = None`；新增 `detect_default_staging_dir()`（探测剩余空间最大盘符下 `.fuscan-cache`）；`ignore_dirs` 默认列表追加 `.fuscan-cache` |
| `src/fuscan/scanner/scanner.py` | `Scanner.__init__` 新增 `skip_paths` 参数；walk 阶段跳过用户标记路径并计入 `user_skipped`；`ScanStats` 构造与 `_emit_progress` 传递新字段 |
| `src/fuscan/gui/worker.py` | `ScanWorker.__init__` 新增 `skip_paths` 参数；`_on_progress` 转发 `phase` 与累计 `user_skipped`；`run()` 传入 Scanner 并在合并报告中累计 `total_user_skipped` |
| `src/fuscan/gui/main_window.ui` | 详情区 `note_edit`（QPlainTextEdit）替换为 `detail_actions_layout`（QHBoxLayout）：`move_to_staging_btn` + `toggle_skip_btn`（checkable）+ spacer |
| `src/fuscan/gui/main_window_ui.py` | 由 `pyside2-uic` 从 .ui 重新生成（uic 产物，勿手改） |
| `src/fuscan/gui/detail_panel.py` | `DetailControls` 移除 `note_edit` 字段，新增 `move_to_staging_btn` / `toggle_skip_btn`；`DetailPanel` 新增 `move_to_staging_requested` / `toggle_skip_requested` 信号与 `set_skip_state` / `move_to_staging` / `toggle_skip` 方法；`clear()` 重置跳过按钮状态 |
| `src/fuscan/gui/main_window.py` | 集成 `SkipStore`（`__init__` 初始化）；`_create_detail_panel` 更新控件引用；连接新信号到 `_on_move_to_staging` / `_on_toggle_skip`；新增 `_resolve_staging_dir` / `_on_move_to_staging` / `_on_toggle_skip` / `_remove_result_from_report`；`_on_result_selected` 同步跳过按钮状态；`_update_scan_stats` 增加 `user_skipped` 参数（紫色 #6F42C1）；`_on_scan_progress` 传入 `user_skipped`；`ScanWorker` 创建处传入 `skip_paths=self._skip_store.paths()` |
| `src/fuscan/gui/styles.qss` | 移除 `QPlainTextEdit#note_edit` 与 `:focus` 样式段（保留「文本编辑」分节标题与 `QTextEdit#detail_preview`） |
| `src/fuscan/gui/settings_dialog.ui` | 「通用设置」Tab 缓存分组后新增「暂存区」GroupBox（QFormLayout）：`staging_dir_label` + `staging_dir_edit` + `staging_dir_browse_btn` |
| `src/fuscan/gui/settings_dialog_ui.py` | 由 `pyside2-uic` 从 .ui 重新生成 |
| `src/fuscan/gui/settings_dialog.py` | 导入 `QFileDialog`；`_configure_ui` 连接 `staging_dir_browse_btn.clicked`；`_load_config` / `_save_config` 读写 `staging_dir`；新增 `_on_browse_staging_dir` |
| `tests/test_skip_store.py` | **新增**：13 个测试覆盖 SkipStore 增删查、原子写入、损坏回退、路径默认值 |
| `tests/test_scanner.py` | 模型层 user_skipped 字段测试；Scanner skip_paths 行为测试（4 个） |
| `tests/test_config.py` | `detect_default_staging_dir` 测试（4 个）+ staging_dir 持久化 + `.fuscan-cache` 在 ignore_dirs |
| `tests/test_gui.py` | `TestScanWorkerSkipPaths`（4 个）；`TestSettingsDialog` 暂存区测试（5 个） |

## 关键决策与依据

### D1：SkipStore 用 JSON 而非 SQLite，与缓存 DB 解耦

**决策**：用户跳过路径独立持久化到 `~/.fuscan/skips.json`，不写入扫描结果缓存
`~/.fuscan/cache.db`。

**依据**：
- 跳过决策独立于缓存兼容版本（`CACHE_COMPAT_VERSION`），缓存 purge 不应丢失
  用户跳过列表；反之跳过列表变更不应触发缓存 purge
- JSON 结构简单（`list[str]`），写入频率低（用户点击按钮），无需 SQLite 索引
- 原子写入用临时文件 + `Path.replace`，与 `Config` 的 YAML 写入策略一致

### D2：跳过键选型为路径字符串而非内容哈希

**决策**：`SkipStore` 以 `str(Path)` 为键，与扫描器遍历产出的 `entry.path` 字符串
一致比较。

**依据**：
- 用户点击「标记为跳过」时持有的是文件路径，无内容哈希上下文
- 路径语义更直观：用户希望跳过「这个文件」而非「所有内容相同的文件」
- 文件移动到暂存区后路径变化，下次扫描自然不再命中跳过列表，符合预期

### D3：跳过检查放在 walk 阶段而非 scan 阶段

**决策**：Scanner 在 walk 遍历目录树时检查 `entry.path in self._skip_paths`，
命中则计入 `user_skipped` 并 `continue`，不进入提取/匹配流程。

**依据**：
- walk 阶段跳过避免后续的文件读取、哈希计算、规则匹配、缓存写入全部开销
- 与扩展名/目录过滤的 `skipped` 同级统计，便于用户区分「自动跳过」与「用户跳过」
- 进度面板可在 walk 阶段实时显示 user_skipped 计数

### D4：toggle_skip 按钮状态以 SkipStore 持久化状态为准取反

**决策**：`_on_toggle_skip` 不读取按钮 `isChecked()`，而是查询 `SkipStore.contains()`
取反后写入，处理完成后通过 `set_skip_state` 同步按钮。

**依据**：
- 若按钮状态作为判断依据，主窗口处理失败时按钮状态与持久化存储不一致
- 以持久化存储为真相源，按钮仅作显示，避免状态漂移
- `set_skip_state` 用 `blockSignals(True/False)` 包裹 `setChecked`，避免触发
  `clicked` 信号循环

### D5：暂存区默认路径探测剩余空间最大盘符

**决策**：`detect_default_staging_dir()` 遍历本地盘符（不含网络映射），取
`shutil.disk_usage(drive).free` 最大者，返回 `<drive>/.fuscan-cache`。

**依据**：
- 暂存区用于存放移动过来的文件，剩余空间大者优先
- 网络映射盘延迟高且可能无写入权限，排除（`include_network=False`）
- 探测失败（OSError / 无盘符）回退到 `Path.home() / ".fuscan-cache"`
- `.fuscan-cache` 同步加入 `Config.ignore_dirs`，避免扫描被移动到暂存区的文件

### D6：移动至暂存区从结果树移除该项

**决策**：`_on_move_to_staging` 移动文件成功后调用 `_remove_result_from_report`
从 `_last_report.results` 过滤掉该路径，重建 `ScanReport` 并刷新结果树。

**依据**：
- 文件已移动，原路径不再存在，保留在结果树中点击会触发「源文件不存在」错误
- 重建 `ScanReport`（frozen dataclass）而非原地修改，保持不可变语义
- 刷新结果树后清空详情区，避免悬空引用

## 代码实现情况

### SkipStore 持久化

```python
class SkipStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_skip_store_path()
        self._paths: set[str] = set()
        self._lock = threading.RLock()
        self._paths = self._load()

    def add(self, path: str) -> None: ...
    def remove(self, path: str) -> None: ...
    def contains(self, path: str) -> bool: ...
    def paths(self) -> frozenset[str]: ...  # 扫描线程启动前取快照
```

### Scanner walk 阶段跳过

```python
for entry in self._walker.walk(root):
    if self._check_control():
        break
    total += 1
    if str(entry.path) in self._skip_paths:
        user_skipped += 1
        continue
    if not self._should_scan(entry):
        skipped += 1
        continue
    entries.append(entry)
```

### 详情区操作按钮

```xml
<layout class="QHBoxLayout" name="detail_actions_layout">
  <item><widget class="QPushButton" name="move_to_staging_btn">
    <property name="text"><string>移动至暂存区</string></property>
  </widget></item>
  <item><widget class="QPushButton" name="toggle_skip_btn">
    <property name="text"><string>标记为跳过</string></property>
    <property name="checkable"><bool>true</bool></property>
  </widget></item>
  <item><spacer name="detail_actions_spacer">...</spacer></item>
</layout>
```

### 统计面板五类计数

```python
self.scan_stats_label.setText(
    f'<span style="color: #28A745;">已通过 {passed}</span>'
    f" &nbsp;|&nbsp; "
    f'<span style="color: #DC3545;">命中 {matched}</span>'
    f" &nbsp;|&nbsp; "
    f'<span style="color: #FFC107;">跳过 {skipped}</span>'
    f" &nbsp;|&nbsp; "
    f'<span style="color: #6F42C1;">用户跳过 {user_skipped}</span>'
    f" &nbsp;|&nbsp; "
    f'<span style="color: #DC3545;">错误 {errors}</span>'
)
```

### 设置对话框暂存区分组

「通用设置」Tab 缓存分组后新增「暂存区」GroupBox（QFormLayout）：
- `staging_dir_label` + `staging_dir_edit`（QLineEdit，placeholder 提示自动探测）
- `staging_dir_browse_btn`（QPushButton「选择...」）打开 `QFileDialog.getExistingDirectory`

## 整合优化情况

- 顺带修复 iter-75 遗留：`ScanWorker._on_progress` 未转发 `phase` 字段，本次
  在转发 `user_skipped` 时一并补上
- `skip_store.py` 初版用 `os.replace`，ruff PTH105 规则要求改用 `Path.replace`，
  已修复并移除 `import os`
- `detail_panel.py` 移除未使用的 `QPlainTextEdit` 导入（PySide2 与 PySide6 两处）
- `styles.qss` 移除 `QPlainTextEdit#note_edit` 与 `:focus` 样式段，保留分节标题
  与 `QTextEdit#detail_preview`

## 测试验证结果

### 单元测试

- `tests/test_skip_store.py`：13 passed（SkipStore 增删查、原子写入、损坏回退）
- `tests/test_scanner.py`：159 passed（含 4 个 skip_paths 行为测试）
- `tests/test_config.py`：30 passed（含 4 个 detect_default_staging_dir 测试）
- `tests/test_gui.py`：362 passed（含 TestScanWorkerSkipPaths 4 个 + 暂存区 5 个）

### 全套门禁

| 检查项 | 结果 |
|--------|------|
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 97 files already formatted |
| `pyrefly check` | 0 errors (490 suppressed, 61 warnings) |
| `pytest -m "not slow" --cov=fuscan --cov-fail-under=95` | **1521 passed**（较 iter-76 的 1485 +36），coverage **95.30%** |

## 遗留事项

- 已有用户保存的 `config.yaml` 若无 `staging_dir` 字段，加载时 `Config` 默认
  `None` 自动探测盘符，无需迁移
- `ScanResult.user_skipped` 字段当前仅作为显示标识，未在导出（CSV/JSON/PDF/Excel）
  中体现；若用户需要导出跳过标记，后续迭代可扩展 `to_dict` / `to_row`
- 暂存区目录下文件累积后无清理机制，用户需手动管理；后续可考虑「暂存区管理」
  面板或自动清理策略

## 下一轮计划

无。本次迭代 5 个子需求全部完成，门禁全通过，进入收尾提交。
