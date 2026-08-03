# 启动性能：引入 rich 汇总表

## Context（背景与目标）

当前 GUI 启动在启用性能测量（`FUSCAN_PERF=1` / CLI `--perf` / GUI 配置 `perf_log_enabled`）时，会由
[app.py](file:///F:/Dev/fuscan/src/fuscan/app.py) 中 6 个嵌套的 `timed(...)` 上下文逐阶段打印两行 INFO 日志
（`"<阶段>…"` + `"<阶段> 完成，用时 X.Xms"`），形成一堵「一行一阶段」的日志墙，难以一眼识别瓶颈：

```
INFO:fuscan.perf:构造 QGuiApplication…
INFO:fuscan.perf:构造 QGuiApplication 完成，用时 13.8ms
INFO:fuscan.perf:注册 QML 类型…
...
INFO:fuscan.perf:启动流程 完成，用时 1905.6ms
```

参考个人 fspack 项目的做法，引入 `rich`，在启动结束时把各环节耗时与占比汇总为**单张表格**直观展示，
仅在调试/性能测量状态下启用，便于识别瓶颈、指导优化。

关键设计张力：[test_gui_perf.py](file:///F:/Dev/fuscan/tests/test_gui_perf.py) 有多条测试硬断言 `timed`
产生恰好 2 条（或 threshold 过滤后 1 条）日志且消息文本固定。**方案必须保持 `timed` 现有日志行为字节级不变**，
以零改动通过这些既有测试。

## 用户确认的决策

1. **rich 作为可选依赖**（`perf` extra + 并入 `test` extra），核心库不强依赖；未安装 rich 时自动回退纯文本汇总。
2. **逐阶段 INFO 日志降为 DEBUG**（仅 `-vv` 可见），启用 perf 时默认只呈现末尾的单张汇总表。

## 实现方案

### 1. 数据模型（新增于 [perf.py](file:///F:/Dev/fuscan/src/fuscan/perf.py)）

新增两个 dataclass，收集启动分阶段耗时（仅 GUI 主线程顺序调用，无需加锁）：

```python
@dataclass(slots=True)
class StageTiming:
    """单个启动阶段计时记录：名称 / 耗时(ms) / 嵌套层级(0 为最外层) / 登记顺序。"""
    name: str
    elapsed_ms: float
    depth: int
    order: int


@dataclass(slots=True)
class PerfReport:
    """启动流程分阶段计时收集器，交由 render_startup_summary 渲染为单张汇总表。"""
    stages: list[StageTiming] = field(default_factory=list)

    def add(self, name: str, elapsed_ms: float, depth: int) -> None: ...
    def total_ms(self) -> float:  # 取 depth==0 的最外层耗时作总计；无则取最大值
        ...
```

### 2. `timed` 最小改动（向后兼容）

给 `timed.__init__` 增加关键字参数 `report: PerfReport | None = None`，`__slots__` 增加 `_report`、`_depth`：
- `__enter__`：当 `report` 非空时记录进入层级并 `_PerfState.depth += 1`。
- `__exit__`：当 `report` 非空时 `_PerfState.depth -= 1` 并 `report.add(name, elapsed_ms, depth)`。
- **`report is None`（所有旧调用点与旧测试）分支完全不执行新逻辑，日志条数与文本逐字节不变。**

### 3. rich 渲染函数（新增于 [perf.py](file:///F:/Dev/fuscan/src/fuscan/perf.py)）

```python
def render_startup_summary(report: PerfReport, *, log: logging.Logger | None = None) -> None:
    """将启动分阶段计时渲染为单张 rich 表格。

    仅在 _PerfState.enabled 且有数据时输出（未启用零开销直接 return）。
    rich 惰性导入：缺失时回退 _render_plain 纯文本 INFO 汇总。
    列：阶段(子阶段按 depth 缩进) / 耗时 / 占比(以最外层"启动流程"为 100% 基准)；
    行序保持时间顺序；末尾追加加粗"总计"行。
    """
```

- 惰性 `from rich.console import Console` / `from rich.table import Table`，`except ImportError` 走 `_render_plain`。
- 表格：`title="启动性能汇总"`、`title_style="bold magenta"`，数值列 `justify="right"`。
- `_render_plain` 用 `%` 延迟格式化逐行 INFO 打印阶段/耗时/占比 + 总计行，作为无 rich 时的回退。

### 4. 接线（[app.py](file:///F:/Dev/fuscan/src/fuscan/app.py) `main()`）

- 构造 `report = PerfReport()`；6 个 `timed(...)` 均传 `report=report`，并将其 `level` 改为 `logging.DEBUG`（逐行细节降级）。
- 外层 `启动流程` 块退出后调用 `render_startup_summary(report)`（perf 未启用时内部即刻 return，零开销）。
- 提前 `return -1`（QML 加载失败）分支不出汇总表，保持简单。

### 5. 文档与导出

- `perf.py` 的 `__all__` 增加 `PerfReport`、`StageTiming`、`render_startup_summary`。
- 更新 `perf.py` 模块 docstring（工具由三类扩为四类）、`timed` docstring（`:param report:`）、`app.py` `main` docstring。

### 6. 依赖变更（[pyproject.toml](file:///F:/Dev/fuscan/pyproject.toml)）

`[project.optional-dependencies]` 新增 `perf = ["rich>=13.0.0"]`，并在 `test` extra 加入 `"rich>=13.0.0"`
（保证 CI 覆盖 rich 分支）。核心 `dependencies` 不变。

## 关键复用点

- `_PerfState.enabled` / `set_perf_enabled`：既有的调试/性能测量总开关，直接复用作汇总表的显示门禁。
- `_PerfState.depth`：既有嵌套层级计数（原用于 `PerfTimer`），`timed` 传 `report` 时复用维护层级。
- `timed` 的 `level`/`threshold_ms` 现有参数：直接用 `level=logging.DEBUG` 实现逐行降级，无需新增机制。

## 测试（追加到 [test_gui_perf.py](file:///F:/Dev/fuscan/tests/test_gui_perf.py)）

既有 15 条测试预期**零改动**全绿。新增：

1. `test_perf_report_add_collects_stages`：`add` 多条，断言长度、`order` 递增、`total_ms()` 取 depth==0。
2. `test_perf_report_total_ms_fallback`：无 depth==0 时 `total_ms()` 取最大值。
3. `test_timed_with_report_registers_stage`：启用后 `timed(..., report=report)` 登记 1 条且仍产生 2 条日志（并存）。
4. `test_timed_with_report_disabled_no_collect`：`enabled=False` 时不登记（零开销路径）。
5. `test_timed_nested_report_depth`：外层 depth==0、内层 depth==1。
6. `test_render_startup_summary_disabled_no_output`：`enabled=False` 无输出、无异常。
7. `test_render_startup_summary_with_rich`：`capsys` 断言 stdout 含「启动性能汇总」「总计」与占比「%」。
8. `test_render_startup_summary_fallback_no_rich`：`monkeypatch` 使 rich 导入抛 `ImportError`，断言走 `_render_plain` INFO 分支。

覆盖率：分支门禁 ≥95% 不得下降；rich 分支与 fallback 分支分别由第 7、8 条覆盖，不加 `# pragma: no cover`。

## 验证

```powershell
# 安装含 rich 的测试依赖（依赖变更落地后）
uv pip install -e ".[test]"

# 风格 / 类型 / 测试 + 分支覆盖率
uv run ruff check src/fuscan/perf.py src/fuscan/app.py tests/test_gui_perf.py
uv run ruff format --check src/fuscan/perf.py src/fuscan/app.py tests/test_gui_perf.py
uv run pyrefly check
uv run pytest tests/test_gui_perf.py -v
uv run pytest -m "not slow" --cov=fuscan --cov-branch --cov-fail-under=95

# 手动验证：启用 perf 启动 GUI，预期末尾打印单张 rich 汇总表（标题「启动性能汇总」，
# 列 阶段/耗时/占比，末行加粗「总计」），逐阶段细节降到 DEBUG（-vv 才可见）
$env:FUSCAN_PERF="1"; fuscan
```

## 改动文件清单

- [perf.py](file:///F:/Dev/fuscan/src/fuscan/perf.py)：新增 `StageTiming`/`PerfReport`/`render_startup_summary`/`_render_plain`；`timed` 增 `report` 参数；更新 `__all__` 与 docstring。
- [app.py](file:///F:/Dev/fuscan/src/fuscan/app.py)：6 个 `timed` 传 `report` 且降级 DEBUG；退出后调用 `render_startup_summary`；更新 docstring。
- [test_gui_perf.py](file:///F:/Dev/fuscan/tests/test_gui_perf.py)：追加 8 条新测试。
- [pyproject.toml](file:///F:/Dev/fuscan/pyproject.toml)：新增 `perf` extra 与 `test` extra 的 `rich`。
