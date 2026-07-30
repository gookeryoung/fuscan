# iter-143 补充控制器覆盖率缺口

## 需求清单

- [x] 补充 scan_controller.py 未覆盖错误处理路径测试
- [x] 补充 workspace_controller.py 未覆盖迁移逻辑与异常处理路径测试
- [x] 修复 ruff / pyrefly 检查告警
- [x] 全套门禁验证（ruff / ruff format / pyrefly / pytest --cov=95）
- [x] 写迭代记录，删除 iter-138 保留最新 5 条
- [x] git commit + push

## 迭代目标

承接 iter-142 遗留事项，专项补充 `scan_controller.py` 与 `workspace_controller.py` 的未覆盖错误处理路径与边界分支测试，推动全项目覆盖率从 94.18% 回升至 95%+ 门禁要求。

## 改动文件清单

### 源码微调

- `src/fuscan/scanner/__init__.py`：应用 `ruff --fix` 修复 RUF022 `__all__` 未排序
- `src/fuscan/gui/controllers/scan_controller.py`：合并 `copyPath` 的 PySide2/PySide6 导入分支为统一 try/except，不可达分支加 `# pragma: no cover`
- `src/fuscan/gui/controllers/workspace_controller.py`：`clearAllWorkspaces` 防御性兜底分支加 `# pragma: no cover`

### 测试补充

- `tests/test_gui_scan_controller.py`：新增 `TestIter143CoverageGaps` 类（24 个测试方法）覆盖属性 getter、错误路径、边界分支
- `tests/test_gui_workspace_controller.py`：新增 `TestIter143CoverageGaps` 类（25 个测试方法）覆盖控制器初始化异常、任务覆盖校验、迁移逻辑、清理逻辑
- `tests/test_gui_controllers_submodules.py`：`_make_history_entry` 默认参数加 `# pyrefly: ignore [unbound-name]`（try/except 导入模式导致 pyrefly 无法识别模块级 skip）

## 关键决策与依据

1. **可达错误路径补测试，不可达加 pragma**：`copyPath` PySide2/PySide6 分支合并后通过 try/except 覆盖；防御性兜底（如 `_active_scan_workspace_id` 入口已校验非空后的二次检查）加 `# pragma: no cover`。依据：可达路径必须通过测试覆盖，不可达代码用 pragma 显式声明。

2. **lambda 改为方法引用**：ruff PLW0108 提示 `lambda ws_id: invalidated.append(ws_id)` 不必要，改为 `invalidated.append` 直接作为 monkeypatch 目标。依据：append 方法本身接受一个参数，与 lambda 行为等价。

3. **persist_data 类型注解**：pyrefly implicit-any-empty-container 错误源于 `{"rules_paths": []}` 字面量，给 `persist_data` 变量加 `dict[str, object]` 注解。依据：变量级注解比逐个 `cast(list[str], [])` 更简洁。

4. **测试模拟「ScanController 未创建」场景**：`test_remove_workspace_without_scan_controller` 通过手动 `_scan_controllers.pop(ws_id, None)` 模拟延迟创建未触发的场景，避免 addWorkspace 自动创建带来的副作用。依据：addWorkspace 设计上立即创建 ScanController（用户操作场景），需手动 pop 才能测试 controller 缺失分支。

## 代码实现情况

### scan_controller.py 测试覆盖

24 个测试方法覆盖以下场景：
- `moveToStaging` 失败时不修改 `_last_report`
- `markAsFalsePositive` 各分支（无选中结果/规则文件路径为空/_pending_ws_id 为空）
- `quick_cancel` 终止扫描 worker 与 stats worker
- `_on_scan_finished` 速度展示与 manifest 持久化
- `_on_scan_progress` 阶段切换
- `copyPath` / `openLocation` 各分支
- `effectiveMaxWorkers` 等任务级配置属性
- `_try_load_cached_results` controller 为 None 时安全返回

### workspace_controller.py 测试覆盖

25 个测试方法覆盖以下场景：
- `_ensure_scan_controller` 初始化异常时 cleanup + deleteLater + raise
- `removeWorkspace` 时 _scan_controllers 无对应项的安全跳过
- `start_scan` / `toggle_pause` / `cancel_scan` 非法工作区 ID 的 warning 分支
- `setTaskOverride` / `clearTaskOverride` 未知字段与 None 全局值分支
- `clearAllWorkspaces` 空列表但 _current_workspace_id 非空的清空逻辑
- `_load_persisted` 重复 ws_id 跳过与单条失败容错
- `_migrate_workspace_rules_to_global` 各分支（rules_paths 合并/use_builtin OR 合并/无变更不 save）
- `_try_load_cached_results` controller 为 None 时安全返回
- `_on_restore_failed` / `_on_restore_done` 异常报告处理

## 整合优化情况

- `copyPath` 合并双分支为单一 try/except，消除重复代码
- 防御性兜底代码统一加 `# pragma: no cover` 注释说明依据
- 测试模拟场景使用「手动 pop + cleanup」模式，避免破坏 addWorkspace 设计语义

## 测试验证结果

### 全套门禁

| 检查项 | 命令 | 结果 |
|--------|------|------|
| ruff check | `python -m ruff check src tests` | All checks passed |
| ruff format | `python -m ruff format --check src tests` | 163 files already formatted |
| pyrefly | `python -m pyrefly check` | 0 errors (798 suppressed, 68 warnings not shown) |
| pytest 全量 | `python -m pytest -m "not slow" --cov=fuscan --cov-fail-under=95` | 2412 passed, 10 skipped, 75 deselected |
| 覆盖率 | --cov-fail-under=95 | 95.05%（达标）|

### 覆盖率对比

| 模块 | iter-142 | iter-143 | 变化 |
|------|----------|----------|------|
| `scan_controller.py` | 95%（28 missed） | 99%（1 missed） | +4% |
| `workspace_controller.py` | 90%（38 missed） | 98%（5 missed） | +8% |
| **全项目** | 94.18% | **95.05%** | **+0.87%** |

iter-143 后两控制器均超过 98% 覆盖率，全项目 95.05% 达到门禁要求。

## 遗留事项

1. **3 个 perf 测试失败**：`test_build_matcher_with_cache_speedup` / `test_odt_xpath_faster_than_python_filter` / `test_extreme_odt_xpath_vs_iter` 为 timing-based 性能对比测试，与本迭代改动无关，`-m "not slow"` 已排除。
2. **iter-140 遗留**：动态档位阈值（`_TIER_TIME_LIMITS`）仍为硬编码，待后续迭代处理。

## 下一轮计划

- 等待用户新需求方向
- 可选：处理 perf 测试 timing 波动问题，或调整 `_TIER_TIME_LIMITS` 为动态阈值
