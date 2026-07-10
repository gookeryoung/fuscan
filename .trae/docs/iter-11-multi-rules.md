# iter-11 多规则文件加载与排序

## 本轮目标

允许加载多个规则文件，GUI 以列表展示，支持上下排序调整，后面的规则覆盖前面的同名规则。

## 改动文件清单

### 核心逻辑

- `src/uniscan/rules/merge.py`：新增 `merge_multiple_rulesets(*rulesets)`，按顺序链式合并多个规则集
- `src/uniscan/rules/__init__.py`：导出 `merge_multiple_rulesets`
- `src/uniscan/builtin/__init__.py`：`load_with_builtin` 参数从 `Optional[Path]` 改为 `Optional[Sequence[Path]]`，支持多文件顺序合并

### CLI

- `src/uniscan/cli.py`：
  - `scan`/`tray` 子命令的 `-r/--rules` 改为 `action="append"`，支持重复指定
  - 新增 `_load_ruleset_from_args(args)` 辅助函数，统一处理 `--no-builtin` + 多 `-r` 的加载逻辑

### GUI

- `src/uniscan/gui/main_window.py`：
  - `_rules_path: Optional[Path]` → `_rules_paths: List[Path]`
  - `_init_ui` 拆分为 `_build_top_controls` / `_build_main_splitter` / `_build_left_panel` 三个子方法（避免 PLR0915）
  - 左侧面板新增 `QListWidget`（规则文件列表）+ 上移/下移/移除按钮
  - `_on_load_rules` 改为追加模式（非替换），支持去重
  - 新增 `_on_move_rule_up` / `_on_move_rule_down` / `_on_remove_rule` / `_refresh_rules_file_list` / `_build_rules_label` / `_reload_and_refresh`
  - `_reload_ruleset` 适配列表路径：builtin 开启时 `load_with_builtin(self._rules_paths)`，关闭时 `merge_multiple_rulesets(*[load_ruleset(p) for p in self._rules_paths])`

### 测试

- `tests/test_merge.py`：新增 `TestMergeMultipleRulesets`（8 个测试：无参/单参/双参覆盖/三参链式/不相交规则/ignore并集/版本号/综合场景）
- `tests/test_builtin.py`：所有 `load_with_builtin(path)` 调用改为 `load_with_builtin([path])`
- `tests/test_cli.py`：新增 `test_parse_scan_multiple_rules`、`test_scan_multiple_user_rules_merged`、`test_scan_multiple_user_rules_order_matters`
- `tests/test_gui.py`：新增 `TestMultiRulesList`（10 个测试：初始空列表/多文件加载/去重/上移/下移/边界noop/移除/全移除/顺序影响覆盖/标签展示）

## 关键决策与依据

1. **合并语义**：`merge_multiple_rulesets` 采用顺序链式合并，后者覆盖前者同名规则；ignore 列表取并集。复用已有 `merge_rulesets` 的逻辑，避免重复实现。
2. **GUI 交互**：规则文件列表用 `QListWidget`（非 `QTreeWidget`），因为只需展示路径无需层级。上移=优先级降低，下移=优先级升高，与"后者覆盖前者"语义一致。
3. **去重策略**：同一路径不重复加载，通过 `path in self._rules_paths` 检查，提示用户而非静默忽略。
4. **错误回滚**：`_on_load_rules` 加载失败时移除刚追加的路径并重新加载，恢复到操作前状态。
5. **`_init_ui` 拆分**：ruff PLR0915 限制 50 语句，拆分为三个 `_build_*` 方法，每个职责单一且可独立测试。

## 验证结果

- ruff check：全部通过
- pytest：397 passed, 1 skipped
- coverage：88.62%（门槛 80%）
- `rules/merge.py` 覆盖率 100%
- `builtin/__init__.py` 覆盖率 100%

## 遗留事项

- iter-06～10 已满 5 轮，按 dev-workflow 规则应归档至 skills 并清理 docs 目录，待后续处理。
