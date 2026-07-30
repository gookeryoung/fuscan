# iter-148 遗留事项清理与风格统一

## 需求清单

- [x] STYLE-1：统一 PySide2/6 import 的 ignore 注释风格
- [x] 评估 iter-146 STYLE-2（except Exception 窄化）→ 保留现状
- [x] 关闭 iter-145 `_TIER_TIME_LIMITS` 硬编码遗留（代码库已不存在）
- [x] 关闭 iter-146 遗留4（测试隔离已修复）
- [x] 关闭 iter-147 遗留1（daemon worker 强杀健壮性已被 FIX-2 覆盖）
- [x] 扫描类似 `_get_read_conn` 的潜在 BUG 模式 → 未发现
- [x] 门禁检查（ruff/format/pyrefly/pytest/coverage）通过
- [x] 写迭代记录，删除 iter-143 保留最新 5 条
- [x] git commit + push

## 迭代目标

iter-147 完成线程内存泄漏修复后，本轮聚焦遗留事项清理：评估多轮迭代遗留事项当前状态、统一风格、扫描潜在 BUG 模式。

## 改动文件清单

- `src/fuscan/gui/controllers/scan_controller.py`：L1209-1212 PySide2/6 import 注释风格统一
- `src/fuscan/gui/controllers/app_controller.py`：L119-122 PySide2/6 import 注释风格统一

## 关键决策与依据

### STYLE-1：PySide2/6 import ignore 注释风格统一

**问题**：3 处 PySide2/6 import 的 ignore 注释风格不一致：

- `scan_controller.py` L1210 `# type: ignore`（裸用，无规则码）
- `scan_controller.py` L1212 `# type: ignore`（裸用，无规则码）
- `app_controller.py` L120 `# type: ignore[import-not-found]`（mypy 风格，项目用 pyrefly）

项目其他位置（28 处 `from PySide2.`）统一模式：try 块 PySide2 分支无注释、except 块 PySide6 分支带 `# pyrefly: ignore [missing-import]`。

**修复尝试 1**：3 处都改为 `# pyrefly: ignore [missing-import]`。pyrefly 报 `Unused pyrefly: ignore`——PySide2 已安装，pyrefly 不报 missing-import，加 ignore 反而违反 unused-ignore。

**最终修复**：与项目其他位置一致——PySide2 分支移除 ignore 注释、PySide6 分支保留 `# pyrefly: ignore [missing-import]`。

### STYLE-2：except Exception 窄化评估（保留现状）

**iter-146 遗留**：多处 `except Exception:` 违反 python-standards「禁止 except Exception」硬约束，需单独评估每处预期异常类型。

**本轮评估**：扫描全部 56 处 `except Exception`，分类如下：

| 类别 | 位置示例 | 评估结论 |
|------|---------|---------|
| 规则求值失败 | scanner.py L738/L898、archive/scanner.py L235/L306 | matcher 是用户规则编译出来的，可能抛任何异常，需捕获所有异常防止单条规则失败阻塞整体扫描 |
| 提取器降级 | _helpers.py L172、_cache_phase.py L172、extractors/base.py L496/L563、text.py L235、pdf.py L150 | 提取器是第三方库（pdfplumber/pypdf/openpyxl/python-pptx 等），可能抛任何异常，降级到纯文本是设计目的 |
| 文件扫描容错 | _pipeline_phase.py L93/L212、_archive_phase.py L101/L153 | 单文件失败不阻塞扫描流程 |
| 重试机制 | extractors/base.py L415 | retry 框架，需捕获所有异常判断是否可重试 |
| 资源关闭 | archive/scanner.py L389、archive/base.py L102、archive/sevenz_reader.py L147 | 关闭异常无需上报 |
| 事务回滚 | cache/_writes.py L351 | 批量写入失败时 ROLLBACK，需捕获所有异常 |
| 熵检测 | scanner.py L766/L774 | context.content 读取或熵算法失败不影响主流程 |
| 初始化容错 | cache/store.py L209 | _init_db 失败时关闭连接避免泄漏 |
| 持久化恢复容错 | workspace_controller.py L883、history/store.py L196 | 单条损坏不阻塞其余 |
| Worker 顶层兜底 | scan_worker.py L247、stats_worker.py L189、export_worker.py L61 | 捕获所有异常 emit failed 信号给 UI |

**结论**：所有 `except Exception` 都是合法的容错模式，强行窄化会丢失对第三方库意外的兜底能力，且每次添加新异常类型都要更新 except 列表，维护负担大、引入新 BUG 风险高。**保留现状**，关闭该遗留。

### iter-145 `_TIER_TIME_LIMITS` 硬编码遗留关闭

代码库搜索 `_TIER_TIME_LIMITS`、`TIER_TIME_LIMITS`、`time_limit`、`tier`（不区分大小写）均无匹配。该遗留事项在 iter-145 之后的某轮迭代中已处理或命名变更，**遗留失效**，关闭。

### iter-146 遗留4（测试隔离）关闭

`test_gui_scan_controller.py` `TestBuildCacheContext.test_build_cache_context_enabled` L1864 已显式设置 `controller._config.cache_path = str(cache_path)`，使代码走 `Path(self._config.cache_path)` 路径而非 `default_cache_path()`。测试隔离已实现，**关闭该遗留**。

### iter-147 遗留1（daemon worker 强杀健壮性）关闭

iter-147 FIX-2 已统一 `quick_cancel` 与 `cleanup` 路径，两条路径末尾都调用 `_close_cache_async()` 异步关闭 cache。`workspace_controller.cleanup` L795 调用 `controller.quick_cancel()`，quick_cancel 末尾调用 `_close_cache_async`，所以非退出路径的强杀也覆盖了 cache.close()。**关闭该遗留**。

### 潜在 BUG 模式扫描（未发现）

扫描类似 `_get_read_conn` 的"每次调用创建新对象未复用"模式：

- `re.compile`：7 处全部在模块级或 `__init__` 中（entropy.py L46、matchers.py L68/L115、email.py L32/L34、legacy_office.py L38、extractor_model.py L62）。良好。
- `threading.Lock()/RLock()/local()`：9 处全部在模块级或 `__init__` 中。良好。
- 其他 `_get_*_conn` / `thread_local` 模式：仅 cache/store.py 一处（iter-147 已修复）。良好。
- `PoolManager` / `HTTPConnection` / `socket.socket` / `ssl.create`：无匹配。良好。

**未发现新的潜在 BUG 模式**，FIX-1 是孤例。

### GUI 控制器扫描（未发现新问题）

search agent 扫描 `src/fuscan/gui/controllers/` 的性能/并发/资源问题：

- 信号连接无重复：`workspace_controller.py` L420-428 的 `scanStateChanged/progressChanged/statusChanged.connect` 在 `_ensure_scan_controller` 内执行，每个 scan_controller 仅创建一次，连接只发生一次
- `removeWorkspace` L442-445 调用 `controller.cleanup()` + `deleteLater()`，QObject 析构自动断开信号
- 资源清理完整：`quick_cancel` / `cleanup` / `_close_cache_async` 路径都已覆盖
- 模型同步：`result_model.py` 有 generation 防过期、`__del__` 阻断信号

**未发现新问题**。

## 代码实现情况

### scan_controller.py L1209-1212

```python
try:
    from PySide2.QtGui import QGuiApplication
except ImportError:  # pragma: no cover
    from PySide6.QtGui import QGuiApplication  # pyrefly: ignore [missing-import]
```

### app_controller.py L119-122

```python
try:
    from PySide2.QtGui import QFont, QGuiApplication
except ImportError:  # pragma: no cover
    from PySide6.QtGui import QFont, QGuiApplication  # pyrefly: ignore [missing-import]
```

## 测试验证结果

- `uv run ruff check src tests`：通过
- `uv run ruff format --check src tests`：163 files already formatted
- `uv run pyrefly check src`：0 errors (513 suppressed, 17 warnings not shown)
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：见本次运行结果

改动仅注释变化，不影响测试逻辑。本轮未新增测试（无新功能/无 BUG 修复，仅风格统一与遗留评估）。

## 遗留事项

无新遗留。多轮遗留事项已在本轮全部关闭。

## 下一轮计划

无主动迭代计划。等待用户反馈或新需求。长期规划（req-35/36/37/38）仍超出初始范围，需用户确认方可启动。
