# iter-75：扫描阶段提示区分与 UI 紧凑度修复

## 需求清单

- [x] 文件目录分析阶段（walk）与文件解析阶段（scan）在用户提示上区分开，避免误以为速度很慢
- [x] 文件类型选择 UI 更紧凑，避免文字截断显示不全
- [x] 修复 QSS 解析错误 `Could not parse application stylesheet`

## 迭代目标

iter-74 引入的「解析器勾选区 MVC」存在两个 UI 遗留问题，同时终端报
`Could not parse application stylesheet` 警告需要定位修复。本次迭代解决
三个用户可见问题：

1. **扫描阶段提示区分**：两阶段架构（walk 遍历 + scan 解析）在 UI 上
   文案不区分，walk 阶段 `scanned=0` 时用户误以为扫描卡住
2. **UI 紧凑度**：iter-74 的 `grid_w=260` 太宽，且 `display_text` 含完整
   扩展名列表导致长名（如「PowerPoint（PPTX） (pptx, ppt)」）被截断
3. **QSS 解析错误**：终端输出的 `Could not parse application stylesheet`
   警告，需定位根因并修复

## 改动文件清单

| 文件 | 改动内容 |
|------|---------|
| `src/fuscan/scanner/result.py` | `ProgressInfo` 新增 `phase: str = "scan"` 字段；`summary()` 增加 walk/archive 分支文案 |
| `src/fuscan/scanner/scanner.py` | `_emit_progress` 加 `phase: str = "scan"` 参数；6 处调用点接入对应 phase（walk 1 处、archive 3 处、scan 2 处） |
| `src/fuscan/gui/main_window.py` | 新增 `_PHASE_LABELS` 常量；`_on_scan_progress` 按 `info.phase` 切换 current_file_label 前缀文案；`_setup_file_types` 调整 `grid_w` 从 260 到 180 |
| `src/fuscan/gui/extractor_model.py` | 移除 `ext_hint` 属性与 `_EXT_HINT_LIMIT` 常量；`display_text` 改为只返回 `display_name`（扩展名信息已在 display_name 中体现） |
| `src/fuscan/gui/styles.qss` | 4 处 `QPushButton[drive!=""]` 替换为 `QPushButton[drive]`（属性存在选择器，Qt QSS 不支持 `!=` 语法） |
| `tests/test_scanner.py` | `TestProgressInfoSummary` 新增 3 个测试：walk phase / archive phase / 未知 phase 回退 |
| `tests/test_extractor_model.py` | `test_display_text_format` 断言改为只检查 display_name；移除 `test_ext_hint_truncates_when_more_than_five`（ext_hint 属性已删除） |

## 关键决策与依据

### D1：ProgressInfo 加 phase 字段而非新增类型

**决策**：`ProgressInfo` 新增 `phase: str = "scan"` 字段（带默认值向后兼容），
而非新增 `WalkProgressInfo` / `ScanProgressInfo` 子类。

**依据**：
- frozen dataclass 加字段是最低侵入方式，所有现有 `_emit_progress` 调用点
  不传 phase 时默认 `scan`，无需批量修改
- summary() 内根据 phase 字段分支返回不同文案，比新增类型 + 多态 dispatch
  更简单直接
- GUI 侧 `_on_scan_progress` 用 `_PHASE_LABELS.get(info.phase, "正在解析")`
  即可切换文案，缺省回退到 scan 阶段文案，健壮性好

### D2：display_text 简化为只返回 display_name

**决策**：移除 `ext_hint` 属性，`display_text` 改为只返回 `display_name`，
完整扩展名列表通过 `tooltip_text` 在鼠标悬停时展示。

**依据**：
- 14 个提取器的 `display_name` 已包含主要扩展名信息（如「Word（DOCX）」、
  「PDF」、「Excel（XLSX）」），无需在 item 文本中重复展示完整扩展名列表
- iter-74 的 `display_text` 形如「PowerPoint（PPTX） (pptx, ppt)」过长，
  配合 `grid_w=260` 仍出现截断；改为只展示 display_name 后可缩到 `grid_w=180`
- 扩展名详情通过 `Qt.ToolTipRole` 暴露，用户需要时可悬停查看，不占主视图空间

### D3：QSS `[drive!=""]` → `[drive]` 属性存在选择器

**决策**：`QPushButton[drive!=""]` 替换为 `QPushButton[drive]`。

**依据**：
- **根因**：Qt QSS 仅支持 `[attr]` / `[attr="x"]` / `[attr~="x"]` /
  `[attr|="x"]` 四种属性选择器，**不支持** CSS3 的 `[attr!="x"]` 语法
- **定位过程**：通过二分法 + `qInstallMessageHandler` 捕获 Qt 警告 + 独立
  属性选择器测试，确认 `QPushButton[drive!=""]` 是 QSS 解析错误根因
- **语义等价**：盘符按钮均通过 `setProperty("drive", str(drive))` 设置非空
  drive 属性，未设置 drive 属性的按钮不匹配 `[drive]`，与原 `[drive!=""]`
  语义等价
- **历史溯源**：此错误非 iter-74 引入，iter-72（commit `30167a2`）已存在，
  是更早的回归

### D4：grid_w 从 260 降到 180

**决策**：`_setup_file_types` 中 `grid_w` 从 260 调整为 180。

**依据**：
- D2 简化 `display_text` 后，最长 display_name「PowerPoint（PPTX）」约 130px，
  加 checkbox（约 16px）+ padding 后取整 180 足够
- 14 项按视图宽度自适应排成 4-5 列（视图宽约 720-900px / 180 ≈ 4-5 列），
  比原 2-3 列更紧凑
- 配合 `setUniformItemSizes(True)` + `setSpacing(4)` 保持视觉对齐

## 代码实现情况

### 阶段提示区分

`ProgressInfo.summary()` 按 phase 返回不同文案：

```python
def summary(self) -> str:
    if self.phase == "walk":
        return f"正在分析目录结构 | 已发现 {self.total} 个文件 | 跳过 {self.skipped} | 已用 {self.elapsed:.1f}s"
    if self.phase == "archive":
        return (
            f"正在扫描压缩包 | 已扫描 {self.scanned} | 命中 {self.matched} | "
            f"错误 {self.errors} | 已用 {self.elapsed:.1f}s"
        )
    speed = self.scanned / self.elapsed if self.elapsed > 0 else 0.0
    return (
        f"已扫描 {self.scanned} | 跳过 {self.skipped} | "
        f"命中 {self.matched} | 条数 {self.matches} | 错误 {self.errors} | "
        f"已用 {self.elapsed:.1f}s | 速度 {speed:.0f} 文件/s"
    )
```

GUI 侧 `_on_scan_progress` 按 phase 切换 `current_file_label` 前缀：

```python
_PHASE_LABELS: dict[str, str] = {
    "walk": "正在遍历",
    "scan": "正在解析",
    "archive": "正在扫描压缩包",
}

# ...
prefix = _PHASE_LABELS.get(info.phase, "正在解析")
self.current_file_label.setText(f"{prefix}: {path_text}")
```

Scanner 侧 6 处 `_emit_progress` 调用点接入对应 phase：

- walk 阶段（line 337）：`phase="walk"`，每 200 个文件发一次进度
- archive 阶段（3 处）：`phase="archive"`，含单线程 + `_collect_archive_futures`
  错误分支与正常分支
- scan 阶段（2 处 + 最终 force 1 处）：默认 `phase="scan"`

### UI 紧凑度

`display_text` 简化：

```python
@property
def display_text(self) -> str:
    """返回 QListView 中展示的文本：仅 ``display_name``（扩展名信息已在 display_name 中体现）。"""
    return self.display_name

@property
def tooltip_text(self) -> str:
    """返回鼠标悬停提示文本：列出所有扩展名。"""
    return f"扩展名: {', '.join(self.extensions)}"
```

`grid_w` 调整：

```python
grid_w = 180  # 原 260
grid_h = 28
self.file_types_view.setGridSize(QSize(grid_w, grid_h))
```

### QSS 修复

```css
/* Qt QSS 不支持 [attr!=""] 语法（仅支持 [attr]/[attr="x"]/[attr~="x"]/[attr|="x"]）。
 * 用 [drive] 属性存在选择器等效替代：盘符按钮均 setProperty("drive", str(drive))，
 * 未设置 drive 属性的按钮不匹配（iter-75 修复 QSS 解析错误）。
 */
QPushButton[drive] { ... }
QPushButton[drive]:hover { ... }
QPushButton[drive]:checked { ... }
QPushButton[drive]:checked:hover { ... }
```

## 整合优化情况

- 移除 iter-74 引入但本次简化后无用的 `ext_hint` 属性与 `_EXT_HINT_LIMIT` 常量，
  避免无用代码残留
- 移除对应的 `test_ext_hint_truncates_when_more_than_five` 测试，
  避免测试失败或测试死代码
- `_PHASE_LABELS` 提取为模块级常量，便于后续扩展新阶段时单点维护

## 测试验证结果

### 单元测试

- `tests/test_scanner.py::TestProgressInfoSummary` 新增 3 个测试：
  - `test_summary_walk_phase`：walk phase summary 含「正在分析目录结构」与「已发现 N 个文件」，不含「速度」/「条数」
  - `test_summary_archive_phase`：archive phase summary 含「正在扫描压缩包」与「已扫描 N」，不含「速度」/「条数」
  - `test_summary_unknown_phase_falls_back_to_scan`：未知 phase 回退到 scan 阶段文案（含速度）
- `tests/test_extractor_model.py` 更新：
  - `test_display_text_format` 断言改为只检查 display_name
  - 移除 `test_ext_hint_truncates_when_more_than_five`

### 全套门禁

| 检查项 | 结果 |
|--------|------|
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 95 files already formatted |
| `pyrefly check` | 0 errors (478 suppressed, 60 warnings) |
| `pytest -m "not slow" --cov=fuscan --cov-fail-under=95` | **1484 passed** (较 iter-74 的 1482 +2，因新增 3 个 phase 测试 -1 个 ext_hint 测试)，coverage **96.10%** |

## 遗留事项

- 无功能遗留
- `pytest.ini` 仅注册 `slow` marker，`gui` marker 未注册导致
  `PytestUnknownMarkWarning`（已有问题，本次未触及）

## 下一轮计划

无。本次迭代三个用户问题全部修复，门禁全通过，进入收尾提交。
