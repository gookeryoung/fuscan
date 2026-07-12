# GUI 优化：图标接入 + 扫描进度界面增强 + 严重等级背景色 + 命中数 bug

## Context

用户反馈三个问题：
1. 编辑、导出、设置、重新扫描等菜单/按钮缺少图标（用户已准备 18 个 SVG 图标在 `src/fuscan/assets/icons/`）
2. 扫描进行中页面信息太少，缺少跳过文件夹列表、命中文件列表、详细统计
3. 严重项（critical）未显示红色背景块；部分文件左侧显示 1 条命中但右侧详情无命中

## Part 1：图标接入

### 现状

`src/fuscan/gui/main_window.py` L126-137 定义了 10 个图标路径常量，L359-368 创建 QIcon 并在 L369-378 设置到部分控件。但以下控件缺少图标：

| 控件 | objectName | 对应 SVG |
|------|-----------|---------|
| `_edit_rules_btn` | edit_rule_btn | edit.svg |
| `_edit_rules_action` | edit_rules_action | edit.svg |
| `_export_btn` | export_btn | export.svg |
| `_export_csv_action` | export_csv_action | export_csv.svg |
| `_export_json_action` | export_json_action | export_json.svg |
| `_settings_action` | settings_action | settings.svg |
| `about_action`（via `_ui`） | about_action | about.svg |
| `_rescan_btn` | rescan_btn | rescan.svg（常量已定义，未 setIcon） |
| `_cancel_btn` | cancel_btn | stop.svg |
| `_pause_resume_btn` | pause_resume_btn | pause.svg（常量已定义，未 setIcon） |

### 改动

**文件**：`src/fuscan/gui/main_window.py`

1. L126-137 补充 8 个图标路径常量（按字母序插入）：
```python
_ICON_ABOUT = str(_ICONS_DIR / "about.svg")
_ICON_BACK = str(_ICONS_DIR / "back.svg")
_ICON_EDIT = str(_ICONS_DIR / "edit.svg")
_ICON_EXPORT = str(_ICONS_DIR / "export.svg")
_ICON_EXPORT_CSV = str(_ICONS_DIR / "export_csv.svg")
_ICON_EXPORT_JSON = str(_ICONS_DIR / "export_json.svg")
_ICON_SETTINGS = str(_ICONS_DIR / "settings.svg")
_ICON_STOP = str(_ICONS_DIR / "stop.svg")
```

2. L358-368 补充对应 QIcon 创建：
```python
self._icon_edit = QIcon(_ICON_EDIT)
self._icon_export = QIcon(_ICON_EXPORT)
self._icon_export_csv = QIcon(_ICON_EXPORT_CSV)
self._icon_export_json = QIcon(_ICON_EXPORT_JSON)
self._icon_settings = QIcon(_ICON_SETTINGS)
self._icon_about = QIcon(_ICON_ABOUT)
self._icon_stop = QIcon(_ICON_STOP)
```
（`_icon_rescan` 和 `_icon_pause` 已存在）

3. L369-378 之后补充 setIcon 调用：
```python
self._edit_rules_btn.setIcon(self._icon_edit)
self._edit_rules_action.setIcon(self._icon_edit)
self._export_btn.setIcon(self._icon_export)
self._export_csv_action.setIcon(self._icon_export_csv)
self._export_json_action.setIcon(self._icon_export_json)
self._settings_action.setIcon(self._icon_settings)
self._ui.about_action.setIcon(self._icon_about)
self._rescan_btn.setIcon(self._icon_rescan)
self._cancel_btn.setIcon(self._icon_stop)
self._pause_resume_btn.setIcon(self._icon_pause)
```

## Part 2：扫描进度界面增强

### 现状

`scanning_page`（main_window_ui.py L220-280）布局：
- 顶部弹性空间
- 标题标签（"扫描中"）
- QProgressBar
- 当前文件标签（"正在解析: ..."）
- 暂停/取消按钮行
- 底部弹性空间

`ProgressInfo`（result.py L21-31）字段：current_file, scanned, total, skipped, matched, errors, elapsed。

`_on_scan_progress`（main_window.py L913-932）仅更新进度条、当前文件标签、状态栏统计文本。

**缺失**：跳过文件夹列表（哪些目录被跳过）、命中文件列表（哪些文件命中规则）、详细统计面板。

### 数据流扩展

要让 UI 实时显示跳过的文件夹和命中的文件，需要 Scanner/FileWalker 收集并上报这些信息。

#### 改动 1：FileWalker 上报跳过的目录

**文件**：`src/fuscan/scanner/walker.py`

FileWalker.`__init__` 增加 `on_skip_dir: Callable[[str], None] | None = None` 参数（须同步在 `typing` 中导入 `Callable`，或复用现有 `from typing import Iterator` 行扩展）。

`_walk_dir` 中两处目录跳过点（L107 `ignore_dirs` 跳过、L110 `ignore_paths` 跳过）前调用回调。文件扩展名跳过（L114 `_is_ignored_file`）不上报，因为它不是目录：
```python
if name.lower() in self._ignore_dirs:
    if self._on_skip_dir is not None:
        self._on_skip_dir(str(Path(entry.path)))
    continue
dir_path = Path(entry.path)
if self._matches_ignore_path(dir_path):
    if self._on_skip_dir is not None:
        self._on_skip_dir(str(dir_path))
    continue
```

#### 改动 2：Scanner 收集命中文件并扩展 ProgressInfo

**文件**：`src/fuscan/scanner/result.py`

ProgressInfo 增加两个字段（带默认值，向后兼容现有调用点）：
```python
@dataclass(frozen=True)
class ProgressInfo:
    current_file: str = ""
    scanned: int = 0
    total: int = 0
    skipped: int = 0
    matched: int = 0
    errors: int = 0
    elapsed: float = 0.0
    skipped_dirs: tuple[str, ...] = ()       # 跳过的目录路径
    matched_files: tuple[tuple[str, str], ...] = ()  # (文件路径, 规则名)
```

由于新增字段都有默认值，`tests/test_gui.py` 中现有 4 处 `ProgressInfo(...)` 构造（L2028、L2076、L3642、L3663）无需修改即可编译运行。

**文件**：`src/fuscan/scanner/scanner.py`

Scanner.`__init__` 增加 `on_skip_dir: Callable[[str], None] | None = None` 参数，传给 FileWalker。`scan()` 方法开头（L145 `self._progress_start = ...` 附近）初始化 `self._skipped_dirs: list[str] = []` 和 `self._matched_files: list[tuple[str, str]] = []`，确保每次扫描重置。

`_emit_progress`（L201-229）中将这两个列表截断为 tuple 放入 ProgressInfo。截断到最近 500 条避免无限增长：
```python
recent_skipped = tuple(self._skipped_dirs[-500:])
recent_matched = tuple(self._matched_files[-500:])
self._on_progress(
    ProgressInfo(
        current_file=current_file,
        scanned=scanned,
        total=self._progress_total,
        skipped=self._progress_skipped,
        matched=matched,
        errors=errors,
        elapsed=now - self._progress_start,
        skipped_dirs=recent_skipped,
        matched_files=recent_matched,
    )
)
```

`_scan_sequential`（L240-254）/ `_scan_concurrent`（L270-288）/ `_scan_archive_phase`（L306-328）中，当 `result.has_hit` 时收集（三处均需添加，避免遗漏并发与压缩包扫描路径）：
```python
if result.has_hit:
    matched += 1
    for hit in result.hits:
        self._matched_files.append((str(entry.path), hit.rule_name))
```

#### 改动 3：ScanWorker 透传新字段

**文件**：`src/fuscan/gui/worker.py`

ScanWorker.`__init__` 无需新增参数（on_skip_dir 由 Scanner 内部处理，不暴露给 Worker）。

但 `_on_progress`（L87-100）当前**显式构造新 ProgressInfo 仅复制 7 个旧字段**，会丢弃新增的 `skipped_dirs` / `matched_files`。必须补齐转发：
```python
def _on_progress(self, info: ProgressInfo) -> None:
    """Scanner 进度回调：累加前序根路径的统计后 emit。"""
    elapsed = time.monotonic() - self._start_time
    self.progress_info.emit(
        ProgressInfo(
            current_file=info.current_file,
            scanned=info.scanned + self._cum_scanned,
            total=info.total + self._cum_total,
            skipped=info.skipped + self._cum_skipped,
            matched=info.matched + self._cum_matched,
            errors=info.errors + self._cum_errors,
            elapsed=elapsed,
            skipped_dirs=info.skipped_dirs,        # 新增：直接透传
            matched_files=info.matched_files,      # 新增：直接透传
        )
    )
```

注意：`skipped_dirs` 与 `matched_files` 不做累计，仅反映最近一次 Scanner.scan() 的快照（截断到 500 条）。这符合 UI 需求——用户关注的是"最近发生了什么"，而非跨根路径的全量累积。ScanWorker 不需要额外信号。

#### 改动 4：扫描中页面 UI 重构

**文件**：`src/fuscan/gui/main_window_ui.py` + 重新 `pyside2-uic` 编译

`scanning_page` 改为以下布局（去掉顶部弹性空间，改为内容驱动）：

```
┌─────────────────────────────────────────────────┐
│                 扫描中                           │
│                                                  │
│  [============>          ] 50% (1000/2000)      │
│  正在解析: /path/to/file.py                      │
│                                                  │
│  ┌─ 统计 ─────────────────────────────────────┐  │
│  │ 已扫描 1000  跳过 500  命中 30  错误 5     │  │
│  │ 已用 12.3s   速度 81 文件/s                │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ┌─跳过的文件夹──────┐ ┌─命中的文件──────────┐  │
│  │ .git/             │ │ /path/secret.py     │  │
│  │ node_modules/     │ │  → 敏感文件名        │  │
│  │ __pycache__/      │ │ /path/config.yaml   │  │
│  │ ...               │ │  → 配置明文密码      │  │
│  └───────────────────┘ └─────────────────────┘  │
│                                                  │
│              [暂停扫描]  [取消]                   │
└──────────────────────────────────────────────────┘
```

具体实现：
- `scanning_page` 改为 QVBoxLayout，去掉顶部 spacer
- 在 `current_file_label` 下方新增 `stats_group`（QGroupBox "统计"），内含 QFormLayout 展示详细统计
- 在 stats_group 下方新增 `lists_splitter`（QSplitter Horizontal），包含两个 QListWidget：
  - `skipped_dirs_list`：跳过的文件夹列表
  - `matched_files_list`：命中的文件列表（显示 "路径 → 规则名"）
- 底部保持按钮行

**文件**：`src/fuscan/gui/main_window.py`

`_on_scan_progress` 扩展为：
1. 更新进度条（已有）
2. 更新当前文件标签（已有）
3. 更新统计面板：已扫描/跳过/命中/错误/已用/速度（速度 = scanned / elapsed）
4. 更新跳过文件夹列表：`self._skipped_dirs_list.clear()` + 添加 `info.skipped_dirs` 各项
5. 更新命中文件列表：`self._matched_files_list.clear()` + 添加 `info.matched_files` 各项（格式 "路径 → 规则名"）
6. 自动滚动到列表底部（`scrollToBottom`）

`_on_scan` / `_reset_scan_ui` 中清空两个列表。

## Part 3a：严重等级背景色

### 现状

L94-99 `_SEVERITY_COLORS` 仅定义前景色（文字色），L114-123 `_apply_severity_to_tree_item` / `_apply_severity_to_table_item` 仅调用 `setForeground`，未调用 `setBackground`。critical 项有红色文字但无红色背景块。

### 改动

**文件**：`src/fuscan/gui/main_window.py`

1. L94-99 之后新增背景色映射（浅色背景，不遮挡文字）：
```python
# 严重等级 → 背景色（浅色，用于整行高亮）
_SEVERITY_BACKGROUNDS: dict[Severity, QColor] = {
    Severity.CRITICAL: QColor(255, 235, 235),   # 浅红
    Severity.WARNING: QColor(255, 243, 224),    # 浅橙
    Severity.INFO: QColor(235, 244, 255),       # 浅蓝
}
```

2. `_apply_severity_to_tree_item` 增加 `setBackground`：
```python
def _apply_severity_to_tree_item(item: QTreeWidgetItem, column: int, severity: Severity) -> None:
    """为 QTreeWidgetItem 的指定列设置中文标签、前景色和背景色。"""
    item.setText(column, _severity_text(severity))
    item.setForeground(column, _SEVERITY_COLORS[severity])
    item.setBackground(column, _SEVERITY_BACKGROUNDS[severity])
```

3. `_apply_severity_to_table_item` 同理增加 `setBackground`。

**注意**：`setBackground` 仅对指定列生效。在 `_populate_flat` 中，file_item 的第 2 列（severity 列）会被设置背景色。若要整行背景色，需要对所有列调用 `setBackground`。考虑到效果一致性，对 critical 等级额外对 file_item 的所有列设置背景色。

在 `_populate_flat`、`_populate_grouped_by_severity` 中，对 file_item 增加整行背景色设置（仅 critical）：
```python
if sr.max_severity == Severity.CRITICAL:
    for col in range(item.columnCount()):
        item.setBackground(col, _SEVERITY_BACKGROUNDS[Severity.CRITICAL])
```

## Part 3b：命中数不一致 bug

### 分析

静态分析未发现明确的数据不一致路径。`ScanResult` 是 frozen dataclass，`has_hit = bool(hits)`，`report.hits` 仅返回 `has_hit=True` 的结果。树中显示的 `len(sr.hits)` 与 UserRole 存储的 `sr.hits` 应一致。

**最可能的原因**（按可能性排序）：

1. **预览面板无高亮被误解为"无命中"**：`_extract_keywords`（L151-165）从 `hit.detail` 的单引号中提取关键词。若 detail 不含单引号包裹的模式（如纯路径匹配、filename 匹配），keywords 为空，预览无高亮。用户看到预览无高亮，误以为"无命中"。

2. **详情面板 stack 未正确切换**：`_detail_main_stack.setCurrentIndex(1)` 可能因 Qt 渲染时序问题未生效。

3. **选中 group 项时清空详情**：在 `grouped_by_severity` / `grouped_by_rule` 模式下，选中顶层分组项时 `_detail_clear()` 被调用，用户可能误以为选中了文件项。

### 改动

**文件**：`src/fuscan/gui/main_window.py`

1. **修复原因 1**：在 `_populate_detail_preview` 中，当 keywords 为空但 hits 非空时，显示提示信息：
```python
keywords = _extract_keywords(result.hits)
if not keywords and result.hits:
    # 命中规则但无法提取关键词（如纯文件名/路径匹配），显示提示
    rule_names = "、".join(h.rule_name for h in result.hits)
    self._detail_preview.setPlainText(
        f"（此文件因【{rule_names}】规则命中，但无内容关键词可高亮）"
    )
    self._update_detail_nav_label()
    return
```

2. **修复原因 2**：在 `_detail_show_result` 中，确保 stack 切换后强制刷新：
```python
self._detail_action_stack.setCurrentIndex(1)
self._detail_main_stack.setCurrentIndex(1)
self._detail_main_stack.currentWidget().update()  # 强制刷新
```

3. **修复原因 3**：在 `_populate_grouped_by_severity` 和 `_populate_grouped_by_rule` 中，为顶层分组项设置不可选中或显示提示文本：
```python
top.setFlags(top.flags() & ~Qt.ItemIsSelectable)  # 分组项不可选中
```
或在 `_on_result_selection_changed` 中，当 result 为 None 时显示"请选择文件项"而非直接清空。

4. **增加诊断日志**：在 `_on_result_selection_changed` 和 `_populate_detail_hits_table` 中增加 DEBUG 级别日志，记录 result.path 和 len(result.hits)，便于运行时排查。

## 假设与决策

1. **图标用磁盘路径而非 qrc 资源**：现有模式（L127-137）用 `str(_ICONS_DIR / "xxx.svg")` 磁盘路径，新增图标保持一致。
2. **ProgressInfo 列表截断到 500 条**：避免大扫描量时 ProgressInfo 过大。500 条足够用户了解最近跳过/命中的情况。
3. **扫描中页面用 QSplitter 而非固定布局**：允许用户调整两个列表的相对宽度。
4. **背景色用浅色**：避免深色背景遮挡深色文字。critical 用浅红（#FFEBEB），与红色文字（#D73A49）形成层次。
5. **分组项设为不可选中**：避免用户选中分组项后详情面板被清空，产生"无命中"的误解。
6. **back.svg 暂不使用**：当前没有"返回"按钮场景，留作备用。

## 测试新增

按 rule-11 要求，公共 API 配套测试，覆盖率不得低于 96%。新增/扩展以下测试：

**`tests/test_gui.py`**：
- `TestScanCallbacks` 扩展：
  - `test_on_scan_progress_updates_skipped_dirs_list`：传入带 `skipped_dirs` 的 ProgressInfo，断言 `_skipped_dirs_list` 显示对应条目
  - `test_on_scan_progress_updates_matched_files_list`：传入带 `matched_files` 的 ProgressInfo，断言 `_matched_files_list` 显示"路径 → 规则名"
  - `test_on_scan_progress_clears_lists_on_new_scan`：`_on_scan` 启动新扫描时清空两个列表
- 新增 `TestSeverityBackground`：
  - `test_critical_tree_item_has_background`：构造 critical ScanResult，断言 file_item 各列 `background()` 等于 `_SEVERITY_BACKGROUNDS[CRITICAL]`
  - `test_warning_tree_item_has_background`：warning 等级断言浅橙背景
  - `test_group_items_non_selectable`：分组模式下顶层项 `flags() & Qt.ItemIsSelectable` 为 0
- 新增 `TestDetailPreviewFallback`：
  - `test_preview_shows_fallback_when_no_keywords`：构造 hits 不含单引号关键词的 ScanResult，断言预览面板显示"无内容关键词可高亮"提示
- 新增 `TestIcons`：
  - `test_all_action_buttons_have_icons`：断言 `_edit_rules_btn`、`_export_btn`、`_rescan_btn`、`_cancel_btn`、`_pause_resume_btn` 的 `icon().isNull()` 为 False
  - `test_all_menu_actions_have_icons`：断言 `_edit_rules_action`、`_export_csv_action`、`_export_json_action`、`_settings_action`、`about_action` 的 icon 非空

**`tests/test_scanner.py` 或 `tests/test_walker.py`**：
- `test_walker_calls_on_skip_dir_for_ignored_dirs`：传入 ignore_dirs，构造包含 `.git` 子目录的 tmp_path，断言回调被调用且参数含 `.git` 路径
- `test_walker_calls_on_skip_dir_for_ignored_paths`：传入 ignore_paths glob，断言回调被调用
- `test_scanner_progress_info_includes_skipped_dirs_and_matched_files`：扫描含命中文件的 tmp_path，捕获 on_progress 回调，断言最终 ProgressInfo 的 `matched_files` 含命中文件路径与规则名

**`tests/test_worker.py`**：
- `test_on_progress_forwards_skipped_dirs_and_matched_files`：mock Scanner 发出带新字段的 ProgressInfo，断言 ScanWorker 的 `progress_info` 信号携带这些字段

## 验证

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyrefly check
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96
```

手动验证：
1. 启动 GUI，确认所有菜单项和按钮有图标（编辑/导出/设置/重新扫描/取消/暂停/导出CSV/导出JSON/关于）
2. 开始扫描，确认扫描中页面显示统计面板、跳过文件夹列表、命中文件列表
3. 扫描完成后，确认 critical 项有红色背景块（浅红 #FFEBEB）
4. 选择不同分组模式，确认详情面板正确显示命中信息；分组顶层项不可选中
5. 选择命中文件，确认预览面板显示高亮或"无内容关键词可高亮"提示
6. 选中左侧显示"1 条命中"的文件，确认右侧详情表也显示 1 条命中

## pyside2-uic 重编译步骤

Part 2 改动 4 修改 `src/fuscan/gui/main_window.ui`（源文件已存在）后，按 rule-12 要求重新编译 `main_window_ui.py`：

```bash
pyside2-uic src/fuscan/gui/main_window.ui -o src/fuscan/gui/main_window_ui.py
```

源文件 `.ui` 与编译产物 `_ui.py` 均需提交（构建环境可能无 pyside 工具链）。若 `pyside2-uic` 在当前环境不可用，可直接编辑 `main_window_ui.py`，但须同步手动更新 `.ui` 源文件以保持一致。

## 执行顺序

Part 1（图标）→ Part 3a（背景色）→ Part 3b（命中 bug）→ Part 2（进度 UI，最复杂）→ 测试补齐 → 验证门禁 → 提交推送。
