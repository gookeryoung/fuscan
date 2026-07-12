# ignore_dirs / ignore_extensions 全局化迁移

## 背景

当前 `ignore_dirs` 和 `ignore_extensions` 定义在 `RuleSet`（规则文件）中，每个规则文件都可独立配置。用户认为这两个字段应当是全局配置（一次设置、所有扫描共用），而非规则文件配置。经确认采用**全量迁移**方案：所有消费者改从 `Config` 读取，`RuleSet` 移除这两个字段，解析器静默忽略（log.debug 提示已弃用）。

## 当前状态分析

### 数据流

```
规则文件 YAML → Parser → RuleSet.ignore_dirs/ignore_extensions →
  ├─ Scanner → FileWalker（主扫描路径）
  ├─ IncrementalScanner → FileWatcher（增量扫描）
  ├─ TrayApp → MonitorConfig（文件监控）
  └─ CLI → _merge_ignore_dirs（命令行 --ignore-dir 合并到 ruleset）
```

### 涉及文件清单

| 文件 | 当前用途 | 迁移后 |
|------|---------|--------|
| `src/fuscan/config.py` | 无这两个字段 | 新增 `ignore_dirs` + `ignore_extensions`，带默认值 |
| `src/fuscan/rules/model.py` L126-127 | RuleSet 字段定义 | 删除两个字段 |
| `src/fuscan/rules/parser.py` L148-152,165-166 | 解析+存入 RuleSet | 解析但忽略，log.debug 弃用提示 |
| `src/fuscan/rules/merge.py` L33-34 | 取并集 | 删除两行 |
| `src/fuscan/scanner/scanner.py` L70-71 | 从 ruleset 读取传给 walker | 新增构造参数，从 Config 传入 |
| `src/fuscan/gui/worker.py` L42-56 | 不含这两个参数 | 新增参数透传给 Scanner |
| `src/fuscan/gui/main_window.py` L853-859 | ScanWorker 构造 | 传入 config 值 |
| `src/fuscan/gui/settings_dialog.py` | 无 UI | 新增两个编辑控件 |
| `src/fuscan/watcher/incremental.py` L57-58 | 从 ruleset 读取 | 新增构造参数 |
| `src/fuscan/watcher/tray.py` L68-74 | 从 ruleset 读取 | 改从 Config 读取 |
| `src/fuscan/cli.py` L71,179-180,205-206,251-256 | --ignore-dir 合并到 ruleset | 改传给 Scanner |
| `src/fuscan/builtin/rules.yaml` L9-65 | 定义 ignore_dirs + ignore_extensions | 删除这两个段 |
| `src/fuscan/builtin/__init__.py` L44 | 注释提及并集 | 更新注释 |
| `rules/example.yaml` + `rules/examples/*.yaml`（14 个） | 定义这两个字段 | 删除这两个段 |
| 测试文件 8+ 个 | 断言 ruleset 字段 | 更新为从 Config/Scanner 参数断言 |

## 实现方案

### 1. Config 新增字段（`src/fuscan/config.py`）

在 `max_depth` 之后新增两个字段，默认值从 `builtin/rules.yaml` 搬迁：

```python
# 忽略目录名（按目录名匹配任意层级，大小写不敏感）
ignore_dirs: list[str] = field(default_factory=lambda: [
    ".git", ".svn", ".hg", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "venv",
    "env", "dist", "build", "target", "out", ".idea", ".vscode",
    ".cache", ".gradle", ".tox", ".eggs", ".sass-cache",
])
# 忽略扩展名（不含点，大小写不敏感）
ignore_extensions: list[str] = field(default_factory=lambda: [
    "pyc", "pyo", "pyd", "so", "dll", "exe", "bin", "obj", "o", "a",
    "lib", "class", "jar", "war", "png", "jpg", "jpeg", "gif", "bmp",
    "ico", "svg", "mp3", "mp4", "avi", "mov", "zip", "rar", "7z",
    "tar", "gz", "bz2",
])
```

### 2. RuleSet 移除字段（`src/fuscan/rules/model.py`）

删除 L126-127 的 `ignore_dirs` 和 `ignore_extensions` 字段。保留 `ignore_paths`（不在本次迁移范围）。更新 docstring。

### 3. Parser 静默忽略（`src/fuscan/rules/parser.py`）

`parse_ruleset` 中（L148-152）：仍读取 `ignore_dirs` 和 `ignore_extensions` 键，但不存入 RuleSet。若非空，`logger.debug("规则文件中 ignore_dirs/ignore_extensions 已弃用，请改用全局配置")`。

构造 RuleSet 时（L162-168）：删除 `ignore_dirs=ignore_dirs` 和 `ignore_extensions=ignore_extensions` 两行。

### 4. Merge 删除并集逻辑（`src/fuscan/rules/merge.py`）

L33-34 删除 `ignore_dirs=_union(...)` 和 `ignore_extensions=_union(...)` 两行。更新模块 docstring。

### 5. Scanner 接受构造参数（`src/fuscan/scanner/scanner.py`）

`__init__` 新增参数：
```python
ignore_dirs: tuple[str, ...] = (),
ignore_extensions: tuple[str, ...] = (),
```

FileWalker 构造（L69-75）改为：
```python
self._walker = FileWalker(
    ignore_dirs=ignore_dirs,
    ignore_extensions=ignore_extensions,
    ignore_paths=ruleset.ignore_paths,
    max_depth=max_depth,
    follow_symlinks=follow_symlinks,
)
```

### 6. ScanWorker 透传参数（`src/fuscan/gui/worker.py`）

`__init__` 新增 `ignore_dirs: tuple[str, ...] = ()` 和 `ignore_extensions: tuple[str, ...] = ()` 参数，存为私有属性。`run()` 中构造 Scanner 时传入。

### 7. MainWindow 传 Config 值（`src/fuscan/gui/main_window.py`）

`_on_scan` 方法（L853-859）ScanWorker 构造时新增：
```python
ignore_dirs=tuple(self._config.ignore_dirs),
ignore_extensions=tuple(self._config.ignore_extensions),
```

### 8. SettingsDialog 新增 UI（`src/fuscan/gui/settings_dialog.py`）

在扫描设置页新增"忽略项"分组：
- `QGroupBox("忽略项")` 含 `QFormLayout`
- `ignore_dirs_edit: QPlainTextEdit`（一行一个目录名，setPlaceholderText 提示）
- `ignore_extensions_edit: QPlainTextEdit`（一行一个扩展名）
- 固定高度 ~80px

`_load_config`：将 `config.ignore_dirs` / `config.ignore_extensions` 列表按行填入。
`_save_config`：按行分割文本，过滤空行，存回 `config.ignore_dirs` / `config.ignore_extensions`。

### 9. IncrementalScanner 接受构造参数（`src/fuscan/watcher/incremental.py`）

`__init__` 新增 `ignore_dirs` 和 `ignore_extensions` 参数，传给 FileWalker。`ruleset.ignore_dirs` / `ruleset.ignore_extensions` 改为新参数。

### 10. TrayApp 改从 Config 读取（`src/fuscan/watcher/tray.py`）

`__init__` 新增 `ignore_dirs: list[str] | None = None` 和 `ignore_extensions: list[str] | None = None` 参数。MonitorConfig 构造（L68-74）改为：
```python
ignore_dirs_list = list(default_ignore_dirs())
if ignore_dirs:
    ignore_dirs_list.extend(ignore_dirs)
self._monitor_config = MonitorConfig(
    watch_paths=list(self._watch_paths),
    ignore_dirs=ignore_dirs_list,
    ignore_extensions=list(ignore_extensions or []),
)
```

IncrementalScanner 构造也需传入这两个参数。

CLI `_cmd_tray` 中构造 TrayApp 时传入 `config.ignore_dirs` / `config.ignore_extensions`。

### 11. CLI 适配（`src/fuscan/cli.py`）

- `_merge_ignore_dirs` 函数删除（不再合并到 ruleset）
- `_cmd_scan`：加载 Config，`--ignore-dir` 的值与 `config.ignore_dirs` 合并后传给 Scanner 构造函数
- `_cmd_rules`：删除 L205-206 打印 ignore_dirs/ignore_extensions 的行（或改为从 Config 读取，但 rules 子命令不加载 Config，直接删除）
- `_cmd_tray`：加载 Config，传给 TrayApp

### 12. Builtin 规则文件（`src/fuscan/builtin/rules.yaml`）

删除 L8-65 的 `ignore_dirs` 和 `ignore_extensions` 段。保留 `ignore_paths`（L67-71）。更新文件头注释。

### 13. Builtin 加载器注释（`src/fuscan/builtin/__init__.py`）

L44 注释更新：删除 "ignore_dirs / ignore_extensions" 提及，仅保留 "ignore_paths 取并集"。

### 14. 示例规则文件（`rules/example.yaml` + `rules/examples/*.yaml`）

删除所有文件中的 `ignore_dirs:` 和 `ignore_extensions:` 段。保留 `ignore_paths:`（如有）。更新 `rules/examples/README.md` 中相关文档。

### 15. 测试更新

| 测试文件 | 改动 |
|---------|------|
| `tests/test_config.py` | 新增：默认值非空、保存加载往返 |
| `tests/test_rules_model.py` | 删除 ignore_dirs/ignore_extensions 字段测试 |
| `tests/test_rules_parser.py` | 更新：parse_ruleset 不再返回这两个字段；新增：YAML 含这两个字段时不报错（静默忽略） |
| `tests/test_merge.py` | 删除 ignore_dirs/ignore_extensions 并集测试 |
| `tests/test_scanner.py` | `_build_ruleset` helper 删除字段；Scanner 构造传 ignore_dirs 参数；测试改为从 Scanner 参数断言 |
| `tests/test_walker.py` | 无改动（FileWalker API 不变） |
| `tests/test_cli.py` | 更新：--ignore-dir 通过 Scanner 参数传入；rules 子命令不再打印忽略目录 |
| `tests/test_builtin.py` | 删除 ignore_dirs/ignore_extensions 相关断言 |
| `tests/test_watcher.py` | 更新：TrayApp/IncrementalScanner 从参数获取 ignore_dirs |
| `tests/test_gui.py` | 新增：SettingsDialog 忽略项编辑测试；ScanWorker 接收 ignore_dirs 测试 |

## 假设与决策

1. **`ignore_paths` 保留在 RuleSet**：用户仅提及 `ignore_dirs` 和 `ignore_extensions`，`ignore_paths` 更偏项目特定，保留在规则文件中。
2. **Config 默认值来自 builtin/rules.yaml**：确保迁移后默认行为不变 — 不配置任何东西时，忽略项与当前内置规则一致。
3. **Parser 静默忽略**：现有规则文件（含示例文件）中的 `ignore_dirs`/`ignore_extensions` 不报错，但不再生效。log.debug 提示弃用。
4. **CLI `--ignore-dir` 保留**：命令行参数仍可用，但改传给 Scanner 而非合并到 ruleset。
5. **SettingsDialog 用 QPlainTextEdit**：一行一个条目，适合较长列表。保存时按行分割、过滤空行。
6. **TrayApp 签名向后兼容**：新参数默认 None，不传时退化为空列表（仅用 default_ignore_dirs）。

## 验证

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyrefly check
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96
```

手动验证：
- 启动 GUI → 设置对话框 → 可见"忽略项"分组，预填默认目录和扩展名
- 修改忽略项 → 扫描 → 确认忽略项生效
- 加载含 `ignore_dirs` 的旧规则文件 → 不报错，log.debug 提示弃用
- CLI `fuscan scan . --ignore-dir tmp` → tmp 目录被忽略
