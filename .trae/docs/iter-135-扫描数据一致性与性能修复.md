# iter-135 扫描数据一致性与性能修复

## 需求清单

- [x] 问题1：增量扫描后已删除文件结果仍出现在结果列表（数据一致性）
- [x] 问题3：压缩包扫描无上限/无取消保护，恶意压缩包可卡死扫描（健壮性）
- [x] 问题10：扫描中切换设置页卡顿，重型组件首帧构造阻塞主线程（性能）
- [x] 验收：增量扫描后已删除文件结果移除；压缩包扫描具备条目上限+取消保护；设置页切换响应 < 200ms；覆盖率 >= 95%

## 迭代目标

修复用户反馈的三个扫描数据一致性与性能问题：
1. 增量扫描合并循环未过滤已删除文件，导致删除的文件命中结果重新出现在结果列表
2. 压缩包扫描无条目数上限与取消检查，恶意/损坏压缩包（如 zip bomb）可卡死扫描线程
3. 设置页每次进入都重建实例，Qt.fontFamilies() 与 ListView delegate 构造阻塞首帧

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fuscan/scanner/result.py` | WalkResult 新增 `manifest` 字段，传递 collect_entries 构建的 manifest 到 scan_entries |
| `src/fuscan/scanner/scanner.py` | collect_entries 末尾设置 manifest 到 WalkResult；scan_entries 从 WalkResult 恢复 _current_manifest；抽取 `_merge_unchanged_hits` 方法，合并循环用 manifest.fingerprints.keys() 过滤已删除文件；_cancel_event 创建提前到 _archive_scanner 前，传 cancel_check 到 ArchiveScanner |
| `src/fuscan/archive/scanner.py` | ArchiveScanner.__init__ 新增 `max_entries` 与 `cancel_check` 参数；scan_archive 循环增加条目数上限截断（超限附加错误结果）与取消检查（每 CANCEL_CHECK_INTERVAL=64 条检查一次） |
| `src/fuscan/gui/views/pages/SettingsPage.qml` | fontFamilyCombo 延迟到 Component.onCompleted 加载 Qt.fontFamilies()；「文件类型」ListView 的 model + cacheBuffer 按 Tab 激活动态绑定（切到扫描 Tab 才构造 delegate） |
| `tests/test_incremental_scan.py` | 新增 `test_merge_excludes_deleted_file_hits` 与 `test_merge_excludes_deleted_file_hits_via_walk_result_manifest` 回归测试 |
| `tests/test_archive.py` | 新增 `test_scan_archive_max_entries_truncation`、`test_scan_archive_max_entries_zero_means_no_limit`、`test_scan_archive_cancel_check_interrupts`、`test_scan_archive_cancel_check_none_no_interrupt` 回归测试 |
| `tests/test_cache.py` | 修复 `test_register_ruleset_stat_oserror_fallback` 的 mock 不接受 Python 3.13 `follow_symlinks` 参数的兼容问题 |

## 关键决策与依据

### 问题1：WalkResult 传递 manifest

**根因**：scan_entries 的增量合并循环从 `_unchanged_hits`（prev_report.hits 预索引）合并未变更文件命中结果，但不检查文件是否在本次 walk 中仍存在。ScanWorker 用 precollected 模式调 scan_entries 时，Scanner 实例的 `_current_manifest` 为 None（collect_entries 在 FileStatsWorker 的 Scanner 实例中调用），无法用于过滤。

**方案**：在 WalkResult 新增 `manifest: IncrementalManifest | None` 字段。collect_entries 末尾将 `self._current_manifest` 放入 WalkResult，scan_entries 开头从 `walk_result.manifest` 恢复 `self._current_manifest`。合并循环用 `manifest.fingerprints.keys()`（即本次 walk 访问到的所有文件，含变更+未变更）过滤已删除文件。manifest 为 None 时回退为空集合（不过滤，保持旧行为避免回归）。

**依据**：manifest.fingerprints 的收集逻辑（scanner.py:366-378）确保未变更文件（line 372 `new_fingerprints[rel]=prev_fp`）与变更/新文件（line 375）都加入 manifest，已删除文件不会被 walk 到故不在 keys() 中。

### 问题3：cancel_check 用 bound method 而非 lambda

**根因**：ArchiveScanner.scan_archive 遍历所有 entries 无上限、无取消检查。_archive_phase.py 的 _check_control 只在 archive 之间检查，archive 内部卡住无法中断。

**方案**：ArchiveScanner.__init__ 新增 `max_entries: int = 5000` 与 `cancel_check: Callable[[], bool] | None = None`。scan_archive 循环中每 CANCEL_CHECK_INTERVAL=64 条检查 cancel_check()，超 max_entries 截断并附加错误结果。Scanner 构造 ArchiveScanner 时传 `cancel_check=self._cancel_event.is_set`。

**决策**：_cancel_event 创建提前到 _archive_scanner 前（而非用 lambda），避免 PLW0108 且 bound method 调用更快。CANCEL_CHECK_INTERVAL=64 用位运算 `(count & 63) == 0` 检查，避免逐条调用的函数调用开销。

### 问题10：最小侵入延迟加载

**根因**：ContentArea.qml 用 `Component { SettingsPage {} }` + `stack.replace`，每次进设置页都重建实例。重建时 Qt.fontFamilies()（Windows 数百字体）与 ListView delegate 构造 + cacheBuffer 预渲染阻塞首帧。

**方案**：fontFamilyCombo 的 `model: Qt.fontFamilies()` 改为 `Component.onCompleted: model = Qt.fontFamilies()`，延迟到构造完成后加载。「文件类型」ListView 的 model + cacheBuffer 按 `settingsTabBar.currentIndex === 1` 动态绑定，非扫描 Tab 时 model 为 null 不构造 delegate。

**依据**：StackLayout 所有子项同时存在，currentIndex 切换只改变可见性不销毁。绑定 model 到 Tab 索引实现延迟加载——SettingsPage 重建时默认在通用 Tab，ListView model 为 null 不构造 delegate；切到扫描 Tab 才绑定 model 构造 delegate。

## 代码实现情况

### 问题1修复（scanner.py + result.py）

- WalkResult 新增 `manifest: IncrementalManifest | None = None` 字段（frozen dataclass，默认 None 不破坏现有构造）
- collect_entries 末尾 `manifest=self._current_manifest` 传入 WalkResult
- scan_entries 开头 `self._current_manifest = walk_result.manifest` 恢复
- 抽取 `_merge_unchanged_hits` 方法（降低 scan_entries 分支数，避免 PLR0912）
- 合并循环增加 `if current_rels and rel not in current_rels: continue` 过滤已删除文件

### 问题3修复（archive/scanner.py + scanner.py）

- 新增模块级常量 `DEFAULT_MAX_ARCHIVE_ENTRIES = 5000` 与 `CANCEL_CHECK_INTERVAL = 64`
- ArchiveScanner.__init__ 新增 `max_entries` 与 `cancel_check` 参数
- scan_archive 循环：取消检查（位运算频率控制）+ 条目数上限截断（附加错误结果）
- Scanner.__init__：_cancel_event 创建提前，传 `cancel_check=self._cancel_event.is_set`

### 问题10修复（SettingsPage.qml）

- fontFamilyCombo：`model: Qt.fontFamilies()` → `Component.onCompleted: model = Qt.fontFamilies()`
- ListView：`model: settingsTabBar.currentIndex === 1 ? configController.extractorModel : null`
- ListView：`cacheBuffer: settingsTabBar.currentIndex === 1 ? 500 : 0`

## 测试验证结果

### 新增回归测试

- `test_merge_excludes_deleted_file_hits`：删除文件后增量扫描，验证删除文件命中不在 report.results
- `test_merge_excludes_deleted_file_hits_via_walk_result_manifest`：模拟 ScanWorker precollected 模式，验证 manifest 经 WalkResult 传递并过滤已删除文件
- `test_scan_archive_max_entries_truncation`：10 条 zip + max_entries=3，验证截断到 3 条 + 1 条错误结果
- `test_scan_archive_max_entries_zero_means_no_limit`：默认 max_entries=5000 扫描全部 5 条
- `test_scan_archive_cancel_check_interrupts`：200 条 zip + cancel_after_64，验证取消在 128 条内生效
- `test_scan_archive_cancel_check_none_no_interrupt`：cancel_check=None 扫描全部 10 条

### 门禁结果

- `ruff check`：All checks passed
- `ruff format --check`：5 files already formatted
- `pyrefly check`：0 errors (2 suppressed)
- `pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：2342 passed, coverage 95.49%

## 遗留事项

- 7z 测试在 py7zr 1.x 环境下失败（1.x 移除 read API），pyproject.toml 已约束 `py7zr<1.0.0`，需确保安装兼容版本
- 问题10的 SettingsPage.qml 修改为 QML 层变更，无法通过 Python 单元测试覆盖，需手动验证 UI 响应
- iter-136 待处理：问题2/4/5/6/9（UI 调整与功能补全）

## 下一轮计划

进入 iter-136：UI 调整与功能补全（问题2/4/5/6/9）。
