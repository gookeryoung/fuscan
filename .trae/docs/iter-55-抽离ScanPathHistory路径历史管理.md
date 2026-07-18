# iter-55 抽离 ScanPathHistory 路径历史管理

## 需求清单

- [x] 1. 继续解耦 `main_window.py` 内部状态（用户请求"继续解耦"）

## 迭代目标

延续 iter-53 的模块抽离思路，将 `main_window.py` 中扫描路径历史管理逻辑
（去重、最近优先、限量、双控件同步）抽到独立子模块 `gui/scan_path_history.py`，
让 `MainWindow` 不再维护 `_scan_history` list 字段，避免 path_combo 与 history_list
两份冗余列表的内容漂移风险。

抽离后 `MainWindow` 的 `_add_scan_path_history` / `_refresh_history_list` /
`_apply_config` / `_save_config` 中关于历史路径的逻辑全部委托给 `ScanPathHistory`
实例，主窗口仅保留薄包装兼容现有测试。

## 改动文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/fuscan/gui/scan_path_history.py` | 新建 | `ScanPathHistory` 类：单一数据源 `_paths` + 同步两个控件 |
| `src/fuscan/gui/main_window.py` | 修改 | 删除 `self._scan_history` 字段；`_add_scan_path_history` 简化为薄包装；删除 `_refresh_history_list` 方法；`_apply_config` 中历史恢复段从 6 行简化为 1 行；`_save_config` 中保存段从列表推导简化为 `get_paths()` |
| `tests/test_gui_scan_path_history.py` | 新建 | 12 个单元测试覆盖 add/load_from_config/get_paths/refresh_list/signal blocking |

## 关键决策与依据

### 抽离目标选择依据

iter-54 收尾遗留事项中标注 `main_window.py` 仍约 1100 行，潜在抽离目标有：
规则管理、阶段切换、扫描流程协调、路径历史等。本轮选择路径历史，依据：

| 候选 | 行数 | 状态独立性 | UI 控件引用数 | 测试影响 | 抽离收益 |
|------|------|-----------|--------------|---------|---------|
| 路径历史 | ~30 | 高（仅 `_scan_history` list） | 2（path_combo / history_list） | 低（保留薄包装） | 高（消除冗余列表） |
| 规则管理 | ~130 | 中 | 4+ | 高（30+ 处测试直接调用） | 中（需保留 11 个薄包装） |
| 阶段切换 | ~50 | 低（与扫描状态耦合） | 5+ actions | 中 | 低（状态共享） |
| 扫描流程协调 | ~200 | 低（与 worker/state 深耦合） | 多 | 高 | 低（需传递 MainWindow 引用） |

路径历史是当前最适合独立抽离的候选：状态最简单（一个 `list[str]`）、控件最少
（两个）、且原有实现存在内容漂移风险（path_combo 与 _scan_history 双份维护）。

### 单一数据源设计依据

原 `MainWindow._add_scan_path_history` 实现同时维护两份冗余数据：

```python
# 原实现：先操作 path_combo（add/remove/insert 顶部），再同步 _scan_history list
self.path_combo.blockSignals(True)
idx = self.path_combo.findText(path_str)
if idx >= 0:
    self.path_combo.removeItem(idx)
self.path_combo.insertItem(0, path_str)
while self.path_combo.count() > MAX_HISTORY:
    self.path_combo.removeItem(self.path_combo.count() - 1)
self.path_combo.setCurrentIndex(0)
self.path_combo.blockSignals(False)

# 同步扫描历史（另一份冗余 list）
if path_str in self._scan_history:
    self._scan_history.remove(path_str)
self._scan_history.insert(0, path_str)
while len(self._scan_history) > MAX_HISTORY:
    self._scan_history.pop()
self._refresh_history_list()
```

这种「先操作控件 A，再同步 list B，再刷新控件 B」的模式存在两类风险：
1. path_combo 与 _scan_history 可能因某一步异常而内容不一致
2. 限量逻辑在两处分别实现（`while count > MAX` vs `while len > MAX`），易漂移

`ScanPathHistory` 改为「单一数据源 + 推送同步」模式：

```python
def add(self, path_str: str) -> None:
    if path_str in self._paths:
        self._paths.remove(path_str)
    self._paths.insert(0, path_str)
    while len(self._paths) > MAX_HISTORY:
        self._paths.pop()
    self._sync_combo()  # _paths 推送到 path_combo
    self._sync_list()   # _paths 推送到 history_list
```

限量、去重、顺序逻辑只在一处实现（`_paths` 操作），两个控件只是只读视图。
这是 MVC 中典型的「model 推送到 view」模式，符合 rule-12「MVC 分层」约束。

### 薄包装保留依据

`tests/test_gui.py` 中以下测试直接调用 `window._add_scan_path_history(...)`：
- 行 745: `test_close_saves_scan_paths_history`
- 行 766-767: `test_path_history_dedup`
- 行 777: `test_path_history_limit`

保留 `MainWindow._add_scan_path_history` 方法名作为薄包装转发到
`self._path_history.add(path_str)`，使现有测试无需修改即可通过。

### `_refresh_history_list` 删除依据

原 `MainWindow._refresh_history_list` 方法仅在两处被调用：
1. `_add_scan_path_history` 内部（已被 `ScanPathHistory.add` 内部 `_sync_list` 替代）
2. `_apply_config` 中恢复历史时（已被 `ScanPathHistory.load_from_config` 内部 `_sync_list` 替代）

无测试直接调用 `window._refresh_history_list`（已通过 grep 验证），可安全删除。
新模块提供等价的 `refresh_list()` 公共方法，供未来需要强制刷新时调用。

### `_sync_combo` 信号阻塞依据

原 `_add_scan_path_history` 中 `path_combo.blockSignals(True)` 是为了
避免 `removeItem` / `insertItem` / `setCurrentIndex` 触发 `currentIndexChanged`
信号导致 `_on_path_selected` 回调。`ScanPathHistory._sync_combo` 保留此行为，
通过 `test_sync_combo_does_not_emit_current_index_changed` 单元测试验证。

## 代码实现情况

### `src/fuscan/gui/scan_path_history.py`

```python
class ScanPathHistory:
    """扫描路径历史：去重、最近优先、限量、双控件同步。"""

    def __init__(self, path_combo: QComboBox, history_list: QListWidget) -> None:
        self._path_combo = path_combo
        self._history_list = history_list
        self._paths: list[str] = []

    def add(self, path_str: str) -> None:
        """添加路径到历史顶部（去重 + 最近优先 + 限量 + 同步控件）。"""
        if path_str in self._paths:
            self._paths.remove(path_str)
        self._paths.insert(0, path_str)
        while len(self._paths) > MAX_HISTORY:
            self._paths.pop()
        self._sync_combo()
        self._sync_list()

    def load_from_config(self, paths: list[str]) -> None:
        """从配置加载历史路径（启动时由 MainWindow._apply_config 调用）。"""
        self._paths = list(paths)
        self._sync_combo()
        self._sync_list()

    def get_paths(self) -> list[str]:
        """返回当前历史路径列表的副本（用于保存到配置）。"""
        return list(self._paths)

    def _sync_combo(self) -> None:
        """同步 path_combo 内容到 _paths（blockSignals 避免触发 currentIndexChanged）。"""
        # ...

    def _sync_list(self) -> None:
        """同步 history_list 内容到 _paths（每项附 tooltip 显示完整路径）。"""
        # ...
```

### `src/fuscan/gui/main_window.py` 集成

`__init__` 中 `self._scan_history: list[str] = []` 替换为
`self._path_history: ScanPathHistory = ScanPathHistory(self.path_combo, self.history_list)`。

`_add_scan_path_history` 从 25 行简化为 3 行薄包装：

```python
# 替换前（25 行：操作 path_combo + 同步 _scan_history + 调用 _refresh_history_list）
self.path_combo.blockSignals(True)
idx = self.path_combo.findText(path_str)
# ... 13 行操作 path_combo
# ... 6 行操作 _scan_history

# 替换后（3 行）
def _add_scan_path_history(self, path_str: str) -> None:
    """将路径添加到扫描历史（去重、最近优先、限制数量，同步两个控件）。"""
    self._path_history.add(path_str)
```

`_apply_config` 中历史恢复段从 6 行简化为 1 行：

```python
# 替换前
self.path_combo.blockSignals(True)
for p in self._config.scan_paths:
    self.path_combo.addItem(p)
self.path_combo.blockSignals(False)
self._scan_history = list(self._config.scan_paths)
self._refresh_history_list()

# 替换后
self._path_history.load_from_config(self._config.scan_paths)
```

`_save_config` 中保存段从列表推导简化为函数调用：

```python
# 替换前
self._config.scan_paths = [self.path_combo.itemText(i) for i in range(self.path_combo.count())]

# 替换后
self._config.scan_paths = self._path_history.get_paths()
```

删除 `MainWindow._refresh_history_list` 方法（7 行），由 `ScanPathHistory._sync_list`
承担等价职责。

## 整合优化情况

- **代码量减负**：`main_window.py` 减少约 35 行（25 行 `_add_scan_path_history` +
  7 行 `_refresh_history_list` + 5 行 `_apply_config`/`_save_config` 中的内联实现）。
- **状态内聚**：`_scan_history` 字段从 MainWindow 移到 `ScanPathHistory._paths`，
  主窗口不再维护路径历史状态，与 `ScanListUpdater`（iter-53）抽离 `_last_skipped_dirs`
  等字段的设计风格一致。
- **单一数据源**：消除 path_combo 与 _scan_history 两份冗余列表，所有写入通过
  `_paths` 一次完成，两个控件作为只读视图由 `_sync_combo` / `_sync_list` 推送。
- **公开 API 兼容**：保留 `MainWindow._add_scan_path_history` 方法名作为薄包装，
  3 处测试调用无需修改；`window.path_combo` / `window.history_list` 控件直接访问
  仍然有效（控件实例未变，只是被 ScanPathHistory 引用）。
- **无新增重复代码**：`ScanPathHistory` 与 `ScanListUpdater`（iter-53）虽都封装
  QListWidget + 增量算法，但职责不同（前者管理路径历史 list，后者管理扫描中
  跳过/命中列表的节流 + 增量 append），无重叠。

## 测试验证结果

| 门禁 | 结果 | 基线（iter-54） | 变化 |
|------|------|----------------|------|
| ruff check | All checks passed | 0 errors | — |
| ruff format --check | 42 files already formatted | — | +1（新测试文件） |
| pyrefly check | 0 errors (65 suppressed) | 0 errors (66 suppressed) | — |
| pytest | 1363 passed / 0 failed | 1351 passed | +12（新增 12 个 ScanPathHistory 单元测试） |
| coverage | 96.23% | 96.19% | +0.04% |

覆盖率小幅提升 0.04% 来自新模块 `scan_path_history.py` 100% 覆盖：
- 5 个 `add` 测试覆盖单路径同步、去重、移至顶部、限量、当前选中
- 3 个 `load_from_config` 测试覆盖加载、覆盖、清空
- 2 个 `get_paths` 测试覆盖副本返回、顺序
- 1 个 `refresh_list` 测试覆盖强制刷新 + tooltip
- 1 个 `signal_blocking` 测试覆盖 `_sync_combo` 阻塞 `currentIndexChanged`

`main_window.py` 覆盖率从 94% 略升（删除的 `_refresh_history_list` 与内联实现
原本在 `_apply_config` / `_save_config` 测试路径中被覆盖，但简化后只剩薄包装调用，
被 `_path_history` 的方法调用路径覆盖，整体行数减少带来比例上升）。

## 遗留事项

- `main_window.py` 仍约 1070 行。后续可继续抽离候选：
  - **规则管理**：约 130 行，但需保留 11 个薄包装兼容测试，且 `_reload_ruleset`
    被 `monkeypatch.setattr(window, "_reload_ruleset", ...)` 直接替换，抽离到
    独立类后 monkeypatch 失效，需重新设计测试替身策略。
  - **阶段切换**：`_update_stage_actions` 与 `_scan_state` / `_last_report` /
    `_ruleset` / `_scan_root` / `_worker` 深耦合，抽离需传递 MainWindow 引用
    或大量回调，收益低于风险。
  - 当前规模在可维护范围内，暂不强行抽离以避免过度抽象（rule-01「不过早抽象」）。

## 下一轮计划

无明确下一轮计划。`main_window.py` 已完成 4 处独立抽离（icons/explorer/
scan_progress_lists/scan_path_history），剩余代码内聚于 UI 装配与扫描流程协调。
如用户提出新需求或明确新解耦目标再行迭代。
