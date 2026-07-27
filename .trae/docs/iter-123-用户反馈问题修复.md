# iter-123 用户反馈问题修复（忽略目录全选/结果持久化/QML null 修复）

## 需求清单

- [x] 忽略目录提供「全选/全不选」选项，并修复该处界面宽度未展开问题
- [x] 修复 ResultDetailPanel.qml Terminal#19-32 「Cannot read property 'xxx' of null」错误
- [x] 已扫描工作目录重启后扫描结果为空 → 实现扫描结果持久化与重启恢复
- [ ] 「更新扫描」触发全量重扫（增量扫描延后至 iter-124，本次仅做结果持久化）

## 迭代目标

修复用户反馈的三个高优问题：
1. 忽略目录管理体验（全选/全不选 + 宽度）
2. ResultDetailPanel.qml 在切换工作区/无选中结果时的 null 引用崩溃
3. 扫描结果重启丢失（用户被迫重新扫描）

第三个问题分两步解决：本次实现扫描结果 JSON 持久化与重启恢复
（避免用户被迫重扫已扫描过的内容）；真正的增量扫描（仅扫描变更文件）
作为 iter-124 独立迭代推进。

## 改动文件清单

修改：
- `src/fuscan/gui/views/pages/SettingsPage.qml` — 忽略目录 Tab 顶部新增
  「全选/全不选」IconButton 行；ColumnLayout 设置 `width: settingsStack.width`
  修复宽度未展开问题
- `src/fuscan/gui/controllers/config_controller.py` — 新增
  `selectAllIgnoreDirs`/`unselectAllIgnoreDirs` Slot（大小写不敏感，
  仅操作预设分类目录，保留自定义目录）
- `src/fuscan/gui/views/components/ResultDetailPanel.qml` — 移除本地
  `scanController` property（PySide2 5.15 下绑定 null），
  全部改用 `workspaceController.currentScanController.xxx` 链式访问
- `src/fuscan/scanner/result.py` — `ScanReport` 新增 `to_json`/`from_json`
  类方法，支持序列化到 JSON 与反序列化回 `ScanReport`/`ScanResult`/`RuleHit`
- `src/fuscan/gui/controllers/scan_controller.py` — 新增
  `restoreFromReport` Slot，从持久化 `ScanReport` 恢复 `_last_report`/
  `_result_model`/统计字段/扫描状态
- `src/fuscan/gui/controllers/workspace_controller.py` — 新增
  `_cached_results_dir`/`_cached_results_path`/`_save_cached_results`/
  `_load_cached_results`/`_delete_cached_results`；
  扫描完成时保存、`_load_persisted` 时加载、`removeWorkspace` 时删除
- `tests/test_export.py` — 新增 `TestScanReportJsonRoundtrip`（9 个用例）
- `tests/test_gui_workspace_controller.py` — 新增 `TestCachedResultsPaths`/
  `TestSaveCachedResults`/`TestLoadCachedResults`/`TestDeleteCachedResults`
  共 21 个用例
- `tests/test_gui_config.py` — 新增 `selectAllIgnoreDirs`/`unselectAllIgnoreDirs`
  8 个用例（全选/全不选/幂等/大小写不敏感/保留自定义目录）

## 关键决策与依据

1. **忽略目录全选/全不选的实现层级**：在 `ConfigController` 实现
   `selectAllIgnoreDirs`/`unselectAllIgnoreDirs`，仅操作预设分类目录
   （通过 `IGNORE_DIR_CATEGORIES` 派生），保留用户自定义目录。
   大小写不敏感去重/移除（`.GIT` 与 `.git` 视为同一目录），
   与既有 `toggleIgnoreDir`/`setIgnoreDirCategoryEnabled` 语义一致。

2. **忽略目录宽度修复**：根因是 `ColumnLayout` 未显式设置宽度，
   在 `StackLayout` 子页中默认宽度为 0 导致内容被压缩。
   设置 `width: settingsStack.width` 使其填满父容器宽度。

3. **ResultDetailPanel null 修复**：PySide2 5.15 下 `readonly property var scanController: workspaceController.currentScanController`
   在 `currentWorkspaceChanged` 信号触发时机与 QML 绑定求值时机错位时
   会求值为 `null`，导致下游所有 `scanController.xxx` 访问抛 TypeError。
   改为直接 `workspaceController.currentScanController.xxx` 链式访问，
   由 QML 引擎在每次访问时即时求值，避免中间变量缓存 null。
   与既有 `ScanProgressCard` 链式访问策略一致（已在 project_memory 记录）。

4. **扫描结果持久化格式选择**：采用 JSON 而非 pickle，原因：
   - 跨版本兼容性更好（pickle 对类定义变更敏感）
   - 可读性更好，便于调试与人工检查
   - 与既有 `save_report` 的 JSON 导出复用 `to_json` 序列化逻辑
   - 安全性更高（pickle 反序列化可执行任意代码）

5. **`ScanReport.to_json`/`from_json` 对称设计**：`to_json` 基于
   `dataclasses.asdict` 序列化所有字段（tuple → list），
   `from_json` 反序列化时将 `match_texts` list 转回 tuple（保持数据类
   不可变契约）。`perf_summary` 不持久化（运行时统计重启后无意义），
   `severity` 字符串通过 `Severity.from_value` 容错解析（未知值回退 INFO）。

6. **缓存文件位置与生命周期**：
   - 位置：`~/.fuscan/results/<ws_id>.json`，与 `workspaces.json` 同目录
   - 保存时机：`scan_finished`/`scan_cancelled` 信号触发后调用
     `_save_cached_results`
   - 加载时机：`_load_persisted` 恢复工作区后立即调用
     `_load_cached_results`
   - 删除时机：`removeWorkspace` 时同步删除缓存文件
   - 容错：单工作区恢复失败不阻塞其余（`_load_persisted` 已有 try/except）

7. **`restoreFromReport` 的状态恢复范围**：恢复 `_last_report`/
   `_result_model`/统计字段（total/scanned/matched/skipped/errors）/
   `_scan_done`/`_scan_phase`/`statusText`/`scanState`。
   不恢复 `_progress_*` 实时进度字段（扫描已结束，进度无意义）。
   `statusText` 恢复为「已完成」或「已取消」（根据 `report.cancelled`）。

8. **测试 `currentScanController` 的注意事项**：`currentScanController`
   在未设置 `_current_workspace_id` 时返回 `_fallback_controller`。
   测试中必须先调用 `setCurrentWorkspaceId(ws_id)` 才能获取工作区
   对应的 ScanController。此行为已在 project_memory 记录，
   后续测试编写需注意。

## 代码实现情况

### ConfigController 全选/全不选 Slot

```python
@Slot()
def selectAllIgnoreDirs(self) -> None:
    """全选所有预设分类下的忽略目录（自定义目录不动）。"""
    existing_lower = {d.lower() for d in self._config.ignore_dirs}
    changed = False
    for _, dirs in IGNORE_DIR_CATEGORIES:
        for d in dirs:
            if d.lower() not in existing_lower:
                self._config.ignore_dirs.append(d)
                existing_lower.add(d.lower())
                changed = True
    if changed:
        self.save()
        self.ignoreDirsChanged.emit()

@Slot()
def unselectAllIgnoreDirs(self) -> None:
    """全不选所有预设分类下的忽略目录（自定义目录不动）。"""
    preset_lower = {d.lower() for _, dirs in IGNORE_DIR_CATEGORIES for d in dirs}
    before = len(self._config.ignore_dirs)
    self._config.ignore_dirs = [d for d in self._config.ignore_dirs if d.lower() not in preset_lower]
    if len(self._config.ignore_dirs) != before:
        self.save()
        self.ignoreDirsChanged.emit()
```

### SettingsPage.qml 全选按钮行

```qml
// 忽略目录 Tab 顶部
RowLayout {
    Layout.fillWidth: true
    spacing: 8
    IconButton {
        iconSource: "qrc:/icons/check_box.svg"
        text: "全选"
        tooltip: "勾选所有预设分类下的忽略目录（自定义目录不动）"
        accent: "secondary"
        onClicked: configController.selectAllIgnoreDirs()
    }
    IconButton {
        iconSource: "qrc:/icons/check_box_blank.svg"
        text: "全不选"
        tooltip: "取消所有预设分类下的忽略目录（自定义目录不动）"
        accent: "secondary"
        onClicked: configController.unselectAllIgnoreDirs()
    }
    Item { Layout.fillWidth: true }
}
```

### ResultDetailPanel.qml 链式访问

```qml
// 修改前（null 引用）：
// readonly property var scanController: workspaceController.currentScanController
// visible: scanController.selectedResultIndex < 0

// 修改后（链式访问）：
visible: workspaceController.currentScanController.selectedResultIndex < 0
model: workspaceController.currentScanController.detailHitsModel
enabled: workspaceController.currentScanController.canSelectPrev
```

### ScanReport JSON 序列化

```python
@classmethod
def from_json(cls, json_str: str) -> "ScanReport":
    data = json.loads(json_str)
    if not isinstance(data, dict):
        raise ValueError("扫描报告 JSON 顶层必须是字典")
    root = Path(data["root"])
    cancelled = bool(data.get("cancelled", False))
    stats_data = data.get("stats", {})
    stats = ScanStats(
        total_files=int(stats_data.get("total_files", 0)),
        scanned_files=int(stats_data.get("scanned_files", 0)),
        # ... 其余字段
        perf_summary=None,  # 不持久化运行时统计
    )
    results = []
    for hit_data in data.get("hits", []):
        hits = tuple(
            RuleHit(
                rule_name=r["rule_name"],
                severity=Severity.from_value(r["severity"]),  # 容错解析
                detail=r["detail"],
                match_count=int(r.get("match_count", 0)),
                match_texts=tuple(r.get("match_texts", [])),  # list → tuple
            )
            for r in hit_data.get("rules", [])
        )
        results.append(ScanResult(
            path=Path(hit_data["path"]),
            size=int(hit_data["size"]),
            hits=hits,
        ))
    return cls(root=root, results=tuple(results), stats=stats, cancelled=cancelled)
```

### WorkspaceController 缓存管理

```python
@property
def _cached_results_dir(self) -> Path:
    return config_module.CONFIG_DIR / "results"

def _cached_results_path(self, ws_id: str) -> Path:
    return self._cached_results_dir / f"{ws_id}.json"

def _save_cached_results(self, ws_id: str, controller: ScanController) -> None:
    report = controller._last_report
    if report is None:
        return
    try:
        self._cached_results_dir.mkdir(parents=True, exist_ok=True)
        self._cached_results_path(ws_id).write_text(report.to_json(), encoding="utf-8")
    except (OSError, ValueError) as exc:
        logger.warning("工作区 %s 扫描结果缓存失败: %s", ws_id, exc)

def _load_cached_results(self, ws_id: str) -> None:
    cache_file = self._cached_results_path(ws_id)
    if not cache_file.exists():
        return
    controller = self._scan_controllers.get(ws_id)
    if controller is None:
        return
    try:
        report = ScanReport.from_json(cache_file.read_text(encoding="utf-8"))
        controller.restoreFromReport(report)
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("工作区 %s 扫描结果恢复失败: %s", ws_id, exc)
```

### ScanController.restoreFromReport

```python
@Slot(object)
def restoreFromReport(self, report: ScanReport) -> None:
    """从持久化的 ScanReport 恢复扫描结果。"""
    self._last_report = report
    self._result_model.set_results(report.hits)
    self._sync_stats_from_report(report)
    self._scan_done = True
    self._scan_phase = PHASE_DONE
    self._reset_scan_ui()
    summary = report.summary()
    speed = report.stats.speed
    if speed > 0:
        summary += f" | 速度 {speed:.0f} 文件/s"
    self._set_status(
        STR_STATUS_DONE if not report.cancelled else STR_STATUS_CANCELLED,
        summary,
    )
    self._set_scan_state(STATE_RESULTS if report.hits else STATE_SETUP)
```

## 整合优化情况

- `ScanReport.to_json` 复用既有 `dataclasses.asdict` 序列化，避免重复实现
- `from_json` 复用 `Severity.from_value` 容错解析（与规则解析链路一致）
- `restoreFromReport` 复用 `_sync_stats_from_report`/`_reset_scan_ui`/
  `_set_status`/`_set_scan_state` 既有方法，零特例代码
- 缓存文件复用 `~/.fuscan/` 目录结构，与 `workspaces.json`/`config.yaml` 同级
- 忽略目录全选/全不选复用 `IGNORE_DIR_CATEGORIES` 常量，与既有
  `ignoreDirCategories`/`setIgnoreDirCategoryEnabled` 共享分类定义
- 测试 fixture 复用 `config_dir`/`controller`/`rules_controller` 既有定义

## 测试验证结果

### 门禁通过

```
uv run ruff check src tests           → All checks passed
uv run ruff format --check src tests  → 140 files already formatted
uv run pyrefly check                  → 0 errors (716 suppressed)
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95
                                      → 2084 passed, 68 deselected
                                         TOTAL 95.88% (required 95%)
```

### 测试覆盖（38 个新增测试用例）

#### `tests/test_export.py` 新增 9 个（`TestScanReportJsonRoundtrip`）

- `from_json` 回环保留 root/cancelled/stats 基本字段
- `from_json` 回环保留命中结果（路径/大小/规则命中）
- `from_json` 回环保留严重等级（Severity 枚举）
- `from_json` 回环保留 match_count（区分命中规则数与匹配条数）
- `from_json` 回环将 list 转回 tuple（match_texts 不可变契约）
- 非法 JSON 抛 ValueError
- 非 dict 顶层抛 ValueError
- 空命中报告回环
- 未知 severity 回退 INFO（不抛异常）
- perf_summary 不持久化

#### `tests/test_gui_workspace_controller.py` 新增 21 个

- `TestCachedResultsPaths`（2 个）：缓存目录在 `CONFIG_DIR/results/`、
  文件名格式 `<ws_id>.json`
- `TestSaveCachedResults`（4 个）：自动创建目录、无 report 跳过、
  持久化 JSON 内容、root/hits/stats 字段完整
- `TestLoadCachedResults`（4 个）：恢复 `_last_report`/`_result_model`、
  无缓存文件静默跳过、损坏 JSON 静默跳过、完整重启场景恢复
- `TestDeleteCachedResults`（3 个）：删除缓存文件、无文件静默跳过、
  删除失败不抛异常
- `TestCachedResultsIntegration`（8 个）：扫描完成后自动保存、
  重启后自动加载、删除工作区同步删除缓存、缓存损坏不影响工作区恢复

#### `tests/test_gui_config.py` 新增 8 个

- `selectAllIgnoreDirs` 添加所有预设目录
- `selectAllIgnoreDirs` 保留自定义目录
- `selectAllIgnoreDirs` 幂等
- `selectAllIgnoreDirs` 大小写不敏感去重
- `unselectAllIgnoreDirs` 移除所有预设目录
- `unselectAllIgnoreDirs` 保留自定义目录
- `unselectAllIgnoreDirs` 幂等
- `unselectAllIgnoreDirs` 大小写不敏感移除

## 遗留事项

- **增量扫描未实现**：用户反馈的「更新扫描又全部重来」根本解决方案是
  基于 mtime+hash 的增量扫描（仅扫描变更文件，未变更文件复用缓存结果）。
  本次仅实现结果持久化（避免重启后被迫重扫），增量扫描作为 iter-124
  独立迭代推进。
- **缓存文件清理策略**：当前缓存文件随 `removeWorkspace` 删除，
  但工作区持久化文件 `workspaces.json` 损坏导致工作区无法恢复时，
  孤儿缓存文件不会被清理。未来可增加启动时扫描 `results/` 目录
  清理无对应工作区的孤儿文件。
- **缓存文件大小限制**：当前无大小限制，超大扫描结果（10万+命中）
  可能导致缓存文件过大。未来可考虑压缩存储或分页持久化。

## 下一轮计划

iter-124：增量扫描与文件变更检测（原 req-35 iter-123 计划）
- 基于 mtime+hash 的增量扫描模式（仅扫描变更文件）
- 增量结果与历史全量结果的合并逻辑
- GUI 新增「增量扫描」选项（工作区卡片操作按钮）
- 增量扫描的缓存复用策略（未变更文件直接复用缓存结果）
- 验收：增量扫描仅读取变更文件，未变更文件跳过 I/O；
  增量结果合并后与全量扫描结果一致；缓存命中场景吞吐量 >= 1000 files/s
