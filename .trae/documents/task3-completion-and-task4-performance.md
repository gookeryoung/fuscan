# Task 3 收尾 + Task 4 性能优化

## 摘要

承接上一会话进度，完成 Task 3（ignore_dirs/ignore_extensions 全局化）的剩余测试更新与 bug 修复，随后推进 Task 4（扫描性能优化）。用户已确认"按顺序依次推进"。

## 当前状态分析

### Task 3 剩余工作

1. **settings_dialog.py 导入 bug**（探索阶段新发现）
   - L14-24 的 `from PySide2.QtWidgets import (...)` 列表中**缺失 `QPlainTextEdit`**
   - 但 L107、L111 已经使用 `QPlainTextEdit(ignore_group)` 构造控件
   - 一旦 `SettingsDialog.__init__` 执行到 `_build_scan_settings_page` 即抛 `NameError`
   - 必须先修复此 bug 才能让新增测试通过

2. **test_gui.py 缺少 SettingsDialog 忽略项测试**
   - 现有 `TestSettingsDialog`（L3965-4081）覆盖通用设置、深度、max_workers
   - 未覆盖新增的 `_ignore_dirs_edit` / `_ignore_extensions_edit` 控件
   - 需新增测试：默认值加载、双向绑定、空行过滤

### Task 4 性能热点（探索阶段确认）

| 编号 | 位置 | 问题 | 影响 |
|------|------|------|------|
| A | scanner.py L342-353 `_should_scan` | 每个文件都重算 `any(not rule.file_extensions)` 与 `{ext for rule in ... for ext in ...}` | O(R) per file，R=规则数 |
| B | incremental.py L183-190 `_should_scan` | 同 A，增量扫描器重复实现 | O(R) per file |
| C | walker.py L95 `sorted(os.scandir(directory), key=lambda e: e.name)` | 每个目录都排序 | O(N log N) per dir，扫描正确性不依赖顺序 |
| D | walker.py L142 `Path(name).suffix.lower().lstrip(".")` | 每个文件构造一次 `Path` 对象仅为取后缀 | O(N) 次 Path 实例化 |

E（ignore_paths fnmatch 预编译）：`_matches_ignore_path` 每目录调用一次，且 fnmatch 内部已 LRU 缓存编译结果，收益有限。本轮**不纳入**，避免过度优化。

## 提议改动

### Step 1：修复 settings_dialog.py 导入 bug

**文件**：`src/fuscan/gui/settings_dialog.py`

**改动**：在 L14-24 的 import 列表中按字母序插入 `QPlainTextEdit`（位于 `QSpinBox` 之前）。

```python
from PySide2.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
```

### Step 2：新增 TestSettingsDialogIgnore 测试类

**文件**：`tests/test_gui.py`

**改动**：在文件末尾（L4081 之后）新增 `TestSettingsDialogIgnore` 类，复用现有 `qapp` fixture 与 `_isolate_config` autouse fixture。包含以下测试方法：

- `test_ignore_group_visible_by_default`：构造 `SettingsDialog(Config())`，断言 `_ignore_dirs_edit` 与 `_ignore_extensions_edit` 均可见（`isVisible()` 为 True 或父 group 可见）
- `test_default_ignore_dirs_loaded`：用默认 `Config()` 构造对话框，断言 `_ignore_dirs_edit.toPlainText()` 包含 `.git`、`node_modules`、`__pycache__`
- `test_default_ignore_extensions_loaded`：断言 `_ignore_extensions_edit.toPlainText()` 包含 `pyc`、`exe`、`zip`
- `test_custom_ignore_dirs_loaded`：构造 `Config(ignore_dirs=["custom_dir", ".git"])`，断言编辑器文本按行等于这两项
- `test_save_config_writes_ignore_dirs`：修改 `_ignore_dirs_edit.setPlainText("new_dir\n.git\n")`，调用 `_save_config()`，断言 `config.ignore_dirs == ["new_dir", ".git"]`（验证空行被过滤）
- `test_save_config_writes_ignore_extensions`：同上，针对扩展名编辑器
- `test_save_config_strips_whitespace`：文本含前导/尾随空格与空行，断言保存后各项 `strip()` 干净

### Step 3：Scanner `_should_scan` 预计算（热点 A）

**文件**：`src/fuscan/scanner/scanner.py`

**改动**：在 `__init__` 中（L68-101 范围内，`self._compiled` 之后）预计算两个属性：

```python
self._has_unrestricted_rule: bool = any(not rule.file_extensions for rule in ruleset.rules)
self._all_extensions: frozenset[str] = frozenset(
    ext for rule in ruleset.rules for ext in rule.file_extensions
)
```

将 `_should_scan`（L342-353）重写为：

```python
def _should_scan(self, entry: FileEntry) -> bool:
    """根据规则集的 file_extensions 限制决定是否扫描该文件。

    若任一规则未限定扩展名，则扫描所有文件；
    否则只扫描规则限定扩展名的并集。
    """
    if entry.is_dir:
        return False
    if self._has_unrestricted_rule:
        return True
    return entry.extension in self._all_extensions
```

**注意**：`_scan_entry`（L355-372）已对每条规则单独检查 `entry.extension not in rule.file_extensions`，逻辑不变；预计算只优化 `_should_scan` 这一入口过滤。

### Step 4：IncrementalScanner `_should_scan` 预计算（热点 B）

**文件**：`src/fuscan/watcher/incremental.py`

**改动**：在 `__init__`（L46-63）中 `self._compiled` 之后预计算：

```python
self._has_unrestricted_rule: bool = any(not rule.file_extensions for rule in ruleset.rules)
self._all_extensions: frozenset[str] = frozenset(
    ext for rule in ruleset.rules for ext in rule.file_extensions
)
```

将 `_should_scan`（L183-190）重写为：

```python
def _should_scan(self, entry: FileEntry) -> bool:
    """根据规则集的 file_extensions 限制决定是否扫描。"""
    if entry.is_dir:
        return False
    if self._has_unrestricted_rule:
        return True
    return entry.extension in self._all_extensions
```

### Step 5：FileWalker 移除排序（热点 C）

**文件**：`src/fuscan/scanner/walker.py`

**改动**：L94-97 由

```python
try:
    entries = sorted(os.scandir(directory), key=lambda e: e.name)
except OSError:
    return
```

改为：

```python
try:
    entries = list(os.scandir(directory))
except OSError:
    return
```

L99 `for entry in entries:` 保持不变（仍需 `list` 物化以避免 scandir 句柄在递归中保持打开）。扫描结果顺序在 `ScanReport.results` 中由 `tuple(results)` 保留插入顺序，不依赖 walker 排序；现有测试不应对顺序有硬性断言。

### Step 6：FileWalker 扩展名提取用字符串操作（热点 D）

**文件**：`src/fuscan/scanner/walker.py`

**改动**：`_is_ignored_file`（L141-142）由

```python
def _is_ignored_file(self, name: str) -> bool:
    suffix = Path(name).suffix.lower().lstrip(".")
    return suffix in self._ignore_extensions
```

改为：

```python
def _is_ignored_file(self, name: str) -> bool:
    dot = name.rfind(".")
    if dot < 0:
        return False
    suffix = name[dot + 1:].lower()
    return suffix in self._ignore_extensions
```

无后缀文件（如 `Makefile`）`rfind` 返回 -1，直接返回 False，行为与原 `Path(name).suffix == ""` 一致。`lstrip(".")` 在新实现中不需要——`name[dot+1:]` 已经不含点。

## 假设与决策

1. **不预编译 ignore_paths 的 fnmatch 模式**：fnmatch 内部已用 `functools.lru_cache` 缓存 `re.compile`，且 `_matches_ignore_path` 每目录调用一次（非每文件），收益有限。避免引入 `re.compile` 与 `translate` 的复杂度。
2. **保留 `list(os.scandir(...))` 物化**：避免递归遍历时 scandir 句柄长期打开。`list()` 是 O(N)，比 `sorted()` 的 O(N log N) 快。
3. **`_should_scan` 预计算不改 `_scan_entry`**：`_scan_entry` 对每条规则单独检查扩展名是为了支持"同文件多规则不同扩展名范围"的精确过滤，逻辑正确且必要；预计算只优化入口的"是否值得进入扫描"判断。
4. **新增测试不修改现有 TestSettingsDialog**：现有 6 个测试已覆盖通用设置，新测试类独立验证忽略项控件，避免互相干扰。
5. **frozenset 而非 set**：预计算结果是只读的，`frozenset` 语义更明确且成员查询与 set 同速。

## 验证

完成 Step 1-6 后执行完整门禁：

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyrefly check
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96
```

全部通过后，按文件名 `git add` 改动文件，提交中文 commit，push 到已跟踪的远程分支。

预期覆盖率：Task 3 新增测试会增加 settings_dialog 分支覆盖；Task 4 预计算逻辑简单，覆盖率应保持 ≥96%。

## 执行顺序

Step 1（修复导入 bug）→ Step 2（新增测试）→ Step 3-6（性能优化）→ 验证门禁 → 提交推送。
