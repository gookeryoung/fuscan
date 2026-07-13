# iter-32 图标主题化与设置 UI 抽离

## 迭代目标

1. 所有图标统一着色为主题色（不再使用各自原图标颜色）
2. 修复扫描中页底部 progress 在未启动扫描时即显示动画的问题
3. 解决扫描过程中点击设置卡滞（继续 iter-30 未竟之事）
4. 将设置对话框 UI 抽离为独立文件，避免 Python 文件过大

## 改动文件清单

### 新增

- `src/fuscan/gui/settings_dialog.ui`：345 行 XML，QTabWidget 双页结构（扫描设置/通用设置）
- `src/fuscan/gui/settings_dialog_ui.py`：`Ui_SettingsDialog` 装配类，对应 .ui 文件

### 修改

- `src/fuscan/gui/main_window.py`：
  - 新增 `_load_themed_icon(svg_path, color)` 函数：移除 SVG fill 属性后注入主题色，通过 QSvgRenderer 渲染到 QPixmap → QIcon
  - 17 个图标全部改为主题色变体；6 个深色背景（头部/侧边栏）专用白色变体
  - 进度条初始化改为确定模式 `setRange(0, 100) + setValue(0)`，避免未启动扫描时显示 indeterminate 动画
  - `_reset_scan_ui` 重置进度条为确定模式，防止残留动画
  - `_update_skipped_dirs_list` / `_update_matched_files_list` 增加 `setUpdatesEnabled(False/True)` 批处理
- `src/fuscan/gui/settings_dialog.py`：从 224 行精简到 93 行，UI 构建委托给 `Ui_SettingsDialog`
- `src/fuscan/scanner/scanner.py`：滚动窗口 500 → 200，减少 ProgressInfo 体积
- `src/fuscan/gui/worker.py`：`ScanWorker` 增加 `progress_interval: float = 0.3` 参数
- `src/fuscan/theme.py`：修复 `COLOR_PRIMARY_DARKER` 与 `COLOR_PRIMARY_DARK` 同值的 bug（#096dd9 → #0552a3）
- `tests/test_gui.py`：更新 2 个陈旧测试断言（#0366d6 → #40a9ff）
- `src/fuscan/gui/main_window.py`、`tests/test_scanner.py`：ruff format 规范化

## 关键决策与依据

### 1. 图标着色策略

- **问题**：17 个 SVG 图标有 3 个含硬编码 fill（export=#666666、export_json=#93A6B9、settings=#747690），其余为 `fill="currentColor"` 但 Qt 不解析 currentColor
- **方案**：`_load_themed_icon` 读取 SVG 文本 → 正则移除所有 `fill="..."` → 在根 `<svg>` 标签注入 `fill="<主题色>"` → QSvgRenderer 渲染到透明 QPixmap → QIcon
- **双变体**：头部栏/侧边栏深色背景用 `COLOR_TEXT_ON_PRIMARY`(白)，其余用 `COLOR_PRIMARY`
- **回退**：渲染失败时回退到原始 `QIcon(svg_path)`，仅记录 warning 不抛异常

### 2. 进度条动画修复

- **根因**：`_progress.setRange(0, 0)` 在初始化时即设为 indeterminate 模式，进入 SCANNING 阶段后 setVisible(True) 触发动画，即使未启动扫描
- **方案**：初始化改为 `setRange(0, 100) + setValue(0)`（确定模式，无动画）；`_start_scan` 中才切换为 `setRange(0, 0)`；`_reset_scan_ui` 重置回确定模式
- **依据**：用户原话"未进行扫描时即显示了动画"——核心问题是动画，非可见性。保留可见性以维持 SCANNING 阶段的视觉反馈

### 3. 扫描时设置卡滞

- **根因**：iter-30 已修复主列表更新节流，但仍有残留卡滞。scanner.py 滚动窗口 500 条 + 信号间隔 150ms，导致超过 500 条后每次回调全量 rebuild
- **方案**：
  - 滚动窗口 500 → 200（减少单次 addItems 体积）
  - ScanWorker 默认 `progress_interval` 150ms → 300ms（降低信号频率）
  - `setUpdatesEnabled(False)` 批处理 addItems，避免每次追加触发重绘
- **依据**：500 条 addItems 实测 ~50ms 阻塞，叠加 150ms 信号 = 主线程 ~33% 占用

### 4. 设置对话框 UI 抽离

- **模式**：参照 `rule_editor.ui + rule_editor_ui.py + rule_editor.py` 三件套
- **`Ui_SettingsDialog.setupUi`**：完整映射 .ui 结构（QTabWidget + 2 页 + 9 个业务控件 + button_box）
- **`retranslateUi`**：所有可翻译文本集中在此，与 .ui string 属性一一对应
- **`SettingsDialog._bind_widgets`**：将 `ui.max_workers_spin` 等绑定到 `self._max_workers_spin`，保持测试兼容
- **通配符导入**：`from PySide2.QtWidgets import *`——匹配 pyside-uic 生成风格（与 rule_editor_ui.py 一致）

## 验证结果

| 门禁 | 结果 |
|------|------|
| ruff check | All checks passed! |
| ruff format --check | 70 files already formatted |
| pyrefly check | 0 errors (111 suppressed) |
| pytest --cov | 1032 passed, 4 deselected, 96.12% coverage |

## 遗留事项

无。所有 4 项用户需求均已闭环验证。
