# iter-82：正则测试工具健壮性优化

## 需求清单

- [x] 修复 `RegexTesterDialog` 编译失败时 `_compiled` 残留旧 Pattern 的 Bug
- [x] 修复「区分大小写」复选框状态变化不触发重测的 Bug
- [x] 输入信号加 300ms 防抖（rule-12 性能规则）
- [x] 测试文本长度上限与命中展示上限，防止 UI 卡顿
- [x] docstring「行为一致」表述修正为「匹配语义一致」
- [x] `_compiled` 类属性移入 `__init__`（实例属性语义更清晰）
- [x] 修复 `Slot` / `QTimer` 导入未走 try/except 双兼容的潜在问题

## 迭代目标

`RegexTesterDialog`（iter-81 抽离）存在若干缺陷与最佳实践偏离：
两个测试用例（`test_test_regex_invalid_pattern_shows_error`、
`test_test_regex_empty_text`）当前实际失败；缺少防抖与输入上限在大文本+
复杂正则场景下会冻结 UI。本轮围绕「正确性 + 健壮性 + rule-12 合规」收敛。

## 改动文件清单

| 文件 | 改动内容 |
|------|---------|
| `src/fuscan/gui/regex_tester.py` | 重构：合并 `_on_pattern_changed` 到 `_on_test_regex`（统一编译+匹配入口）；新增 `_schedule_refresh` + `QTimer` 防抖（300ms）；连接 `case_sensitive_check.stateChanged` 信号；新增 `_MAX_TEXT_LEN`/`_MAX_DISPLAY_MATCHES` 常量与截断/上限逻辑；移除类级 `_compiled` 属性；模块/类/方法 docstring 表述修正；导入合并为单 try/except（含 `QTimer`/`Slot`） |
| `tests/test_gui.py` | `TestRegexTesterDialog` 新增 6 测试：`test_case_sensitive_state_changed_triggers_refresh`（Bug 2）、`test_text_truncated_above_limit`、`test_match_display_cap`、`test_debounce_timer_started_on_text_changed`、`test_debounce_timeout_triggers_refresh`、`test_invalid_pattern_does_not_retain_old_compiled`（Bug 1）；2 个原失败测试同步通过 |

## 关键决策与依据

### D1：合并 `_on_pattern_changed` 到 `_on_test_regex`

**决策**：废弃 `_on_pattern_changed`，由 `_on_test_regex` 统一承担「编译+匹配」
全流程。

**依据**：
- 既有测试契约（`test_test_regex_invalid_pattern_shows_error`、
  `test_test_regex_empty_text`）期望 `_on_test_regex` 本身能触发编译并显示
  编译失败/未命中信息；原拆分到 `_on_pattern_changed` 导致 textChanged
  触发的编译错误被后续 `_on_test_regex` 的 `（请输入正则表达式）` 覆盖
- 单一入口简化防抖逻辑：所有信号统一连接 `_schedule_refresh` →
  `_debounce_timer.timeout → _on_test_regex`
- `returnPressed` 信号作为用户显式触发（Enter 键），直接同步连接
  `_on_test_regex` 绕过防抖，符合「显式操作即时响应」直觉

### D2：编译失败不再保留旧 `_compiled`

**决策**：`_on_test_regex` 每次进入都重新编译；编译异常时显示错误并 return，
`compiled` 局部变量不复用上次成功结果。

**依据**：
- 原类属性 `_compiled: re.Pattern | None = None` 在编译失败时保留旧 Pattern，
  下次 `_on_test_regex` 会用旧 pattern 匹配新文本，产生与用户输入不符的结果
- 重构后 `compiled` 退化为 `_on_test_regex` 内局部变量，无跨调用状态残留
- 测试 `test_invalid_pattern_does_not_retain_old_compiled` 覆盖该场景

### D3：防抖而非 Worker 化

**决策**：用 `QTimer` 300ms 防抖处理 textChanged/stateChanged，**不**引入
QThread Worker。

**依据**：
- 99% 场景正则测试 <10ms，QThread 启动开销（~5-10ms）反而劣化即时反馈体验
- Python `re` 无超时机制，Worker 对灾难性回溯（如 `(a+)+b` 对 `aaaa...`）
  无防护作用——后台线程卡死后取消信号也无法处理；Windows 上 `signal.SIGALRM`
  不可用
- 真正的防护是输入规模限制（`_MAX_TEXT_LEN=100_000` + `_MAX_DISPLAY_MATCHES=1000`）
- 与既有 `result_filter_panel.py`、`content_panel.py`、`watcher/tray.py` 的
  QTimer 防抖模式一致

### D4：输入/输出上限取值

**决策**：`_MAX_TEXT_LEN=100_000`（10 万字符）、`_MAX_DISPLAY_MATCHES=1000`。

**依据**：
- 10 万字符约为 100KB UTF-8 文本，覆盖绝大多数用户测试场景；
  超出时截断并在结果首行标注「（测试文本已截断至 100000 字符）」
- 1000 条命中已足够定位问题，超出时展示前 1000 条 + 「...还有 N 处未显示」
- 二者均为模块常量，未来如需调整可集中修改

### D5：连接 `case_sensitive_check.stateChanged`

**决策**：将 `stateChanged` 信号连接到 `_schedule_refresh`，使勾选/取消
「区分大小写」后立即重新编译（flags 变化）并重测。

**依据**：
- 原 `_connect_signals` 未连接此信号，用户切换大小写敏感后必须改 pattern
  或文本才触发刷新，与「区分大小写」语义不符
- 走防抖路径避免快速连续切换导致多次重编译
- 测试 `test_case_sensitive_state_changed_triggers_refresh` 通过
  `_debounce_timer.timeout.emit()` 模拟定时器到期验证

## 代码实现情况

### 新结构

```python
class RegexTesterDialog(QDialog, Ui_RegexTesterDialog):
    def __init__(self, parent=None, initial_pattern="") -> None:
        # 初始化 _debounce_timer（QTimer.singleShot, 300ms）
        # 预填 initial_pattern 时立即同步执行一次
    def _connect_signals(self) -> None: ...
    def _schedule_refresh(self) -> None: ...
    @Slot()
    def _on_test_regex(self) -> None: ...  # 编译+匹配全流程，含截断/上限
```

### 防抖与信号连接

- `pattern_edit.textChanged` → `_schedule_refresh` → timer → `_on_test_regex`
- `test_text_edit.textChanged` → `_schedule_refresh` → timer → `_on_test_regex`
- `case_sensitive_check.stateChanged` → `_schedule_refresh` → timer → `_on_test_regex`
- `pattern_edit.returnPressed` → `_on_test_regex`（直接同步，绕过防抖）

### 截断与上限逻辑

```python
truncated = len(text) > _MAX_TEXT_LEN
if truncated:
    text = text[:_MAX_TEXT_LEN]
matches = list(compiled.finditer(text))
total = len(matches)
shown = matches[:_MAX_DISPLAY_MATCHES]
# 超限时提示「（仅展示前 1000 处）」+「...还有 N 处未显示」
```

## 测试验证结果

- ruff check：All checks passed
- ruff format --check：105 files already formatted
- pyrefly check：0 errors（528 suppressed）
- pytest：1577 passed（+6 净增），16 deselected，coverage 95.14%
  - `regex_tester.py`：100% 覆盖率（79 stmt / 0 miss / 22 branch / 0 partial）

### 新增测试清单（+6 净增）

| 测试方法 | 说明 |
|---------|------|
| test_case_sensitive_state_changed_triggers_refresh | Bug 2：stateChanged 触发重测 |
| test_text_truncated_above_limit | 超长文本截断 + 提示 |
| test_match_display_cap | 命中数超限展示前 N + 剩余提示 |
| test_debounce_timer_started_on_text_changed | 防抖定时器启动验证 |
| test_debounce_timeout_triggers_refresh | 定时器到期触发刷新 |
| test_invalid_pattern_does_not_retain_old_compiled | Bug 1：编译失败不残留旧 Pattern |

### 修复的失败测试（2 个）

| 测试方法 | 原因 | 修复 |
|---------|------|------|
| test_test_regex_invalid_pattern_shows_error | `_on_test_regex` 见 `_compiled=None` 覆盖为「请输入正则表达式」 | `_on_test_regex` 自身负责编译，失败直接显示「正则编译失败」 |
| test_test_regex_empty_text | 空文本走「请输入测试文本」分支 | 空文本走「未命中（扫描 0 字符）」分支，与测试契约一致 |

## 遗留事项

- 灾难性回溯（pathological regex）仍无超时防护——Python `re` 模块限制，
  Worker 化亦无法解决；当前依赖用户自觉 + 文本上限间接缓解
- 顶层空置的 `src/fuscan/workers/` 目录仍存在（脚手架遗留），建议未来
  整合 `gui/worker.py` + `gui/export_worker.py` 到 `gui/workers/` 包时
  一并清理

## 下一轮计划

正则测试工具健壮性收敛完成。如用户确认 Workers 整合方案（D 选项），
下一轮可执行：迁移 `ScanWorker`/`ExportWorker` 到 `gui/workers/` 包，
删除顶层空置 `workers/` 目录。
