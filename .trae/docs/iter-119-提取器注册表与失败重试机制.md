# iter-119 提取器注册表与失败重试机制

## 需求清单

- [x] 评估提取器调用链与现有失败处理（提取器内部降级 / 调用方 `except Exception` 回退）
- [x] 设计失败重试与降级机制：仅对瞬时 `OSError` 重试，其他异常直接抛出
- [x] `ExtractorRegistry` 新增 `extract_from_bytes_with_retry` / `extract_with_retry`
- [x] 新增 `ExtractorFailure` dataclass 聚合诊断信息，`on_failure` 回调上报
- [x] 新增 `is_retriable_error` 异常分类函数
- [x] 模块级便捷函数 `extract_content_from_bytes_with_retry` / `extract_content_with_fallback_and_retry`
- [x] 三个调用点切换到新 API：`scanner/_helpers.py`、`scanner/_cache_phase.py`、`archive/scanner.py`
- [x] 配套 23 个测试覆盖重试/降级/诊断路径
- [x] 全套门禁通过（ruff/pyrefly/pytest 1964 通过/coverage 95.87%）

## 迭代目标

为提取器调用链添加失败重试与诊断信息聚合能力，**不破坏现有降级机制**（提取器内部
后端降级、调用方纯文本回退），仅对瞬时 `OSError`（Windows AV 文件锁、网络盘抖动）
执行一次退避重试，避免不必要的纯文本降级（PDF/DOCX 降级到纯文本会读到乱码）。

## 改动文件清单

修改：
- `src/fuscan/extractors/base.py` — 新增 `ExtractorFailure` frozen dataclass、
  `is_retriable_error` 函数、`ExtractorRegistry.extract_from_bytes_with_retry` /
  `extract_with_retry` / `_retry_loop`（私有重试骨架）方法、
  `extract_content_from_bytes_with_retry` / `extract_content_with_fallback_and_retry`
  模块级便捷函数；`__all__` 与模块文档同步更新
- `src/fuscan/extractors/__init__.py` — 导出新增 API（`ExtractorFailure` /
  `extract_content_from_bytes_with_retry` / `extract_content_with_fallback_and_retry` /
  `is_retriable_error`），`__all__` 与模块文档同步更新
- `src/fuscan/scanner/_helpers.py` — `default_extract_content_with_hash` 切换到
  `extract_content_from_bytes_with_retry`，docstring 注明 iter-119 行为变更
- `src/fuscan/scanner/_cache_phase.py` — `extract_with_cache` 切换到
  `extract_content_from_bytes_with_retry`，docstring 同步更新
- `src/fuscan/archive/scanner.py` — `_extract_content_from_bytes` 切换到
  `extract_content_from_bytes_with_retry`，docstring 同步更新
- `tests/test_extractors.py` — 新增 `_FlakyExtractor` 可编程失败提取器、5 个测试类
  共 23 个测试；导入新增 API
- `tests/test_archive.py` — `test_extract_failure_falls_back_to_decode` 的
  monkeypatch 目标切换为 `extract_content_from_bytes_with_retry`
- `tests/test_scanner.py` — `test_extract_content_cache_skips_extract_on_second_path`
  与 `test_default_extract_content_with_hash_extractor_error_fallback` 的
  monkeypatch 目标切换为 `extract_content_from_bytes_with_retry`，mock 函数签名
  扩展为接受 `max_retries` / `backoff_ms` / `on_failure` 关键字参数
- `tests/test_scanner_cache_phase.py` — `test_extract_failure_falls_back_to_utf8`
  的 monkeypatch 目标与 mock 签名同步更新

## 关键决策与依据

1. **仅对 `OSError` 重试，其他异常不重试**：
   - `OSError`（含 `PermissionError`/`BlockingIOError`/`FileNotFoundError`）通常由
     Windows AV 文件锁、网络盘抖动、共享冲突等瞬时原因引起，重试一次（50ms 退避）
     通常能成功
   - `ExtractorError`（文件损坏/加密/格式错误）不可恢复，重试无意义
   - 其他异常（`ValueError`/`KeyError` 等）通常是数据问题或提取器 bug，重试无意义

2. **默认 `max_retries=1`、`backoff_ms=50`**：
   - 1 次重试足以覆盖瞬时错误（AV 扫描通常 < 50ms 释放锁）
   - 50ms 退避不会显著拖慢扫描（单个文件最多额外 50ms，相比 docx 提取 5-8ms
     是 6-10 倍开销，但仅在失败时发生，正常路径零开销）
   - 不增加 Config 配置项：重试策略是内部行为，避免界面臃肿（符合 rule-01
     不过度设计原则）

3. **`on_failure` 回调作为扩展点，扫描热路径暂不集成**：
   - 新增 API 已支持 `on_failure: Callable[[ExtractorFailure], None] | None`
   - 扫描热路径（`default_extract_content_with_hash` / `extract_with_cache`）
     当前不传递回调，失败信息通过 `logger.debug` 在 `_retry_loop` 内部记录
   - 未来如需聚合统计「N 个文件提取失败」，Scanner 可注入回调写入 `PerfStats`

4. **`ExtractorFailure` 截断 `error_message` 到 200 字符**：
   - 避免大 traceback 撑爆统计（部分 PDF 异常消息可能 > 1KB）
   - 200 字符足够定位问题（含异常类型 + 关键消息）

5. **`_retry_loop` 私有方法消除重复**：
   - `extract_from_bytes_with_retry` 与 `extract_with_retry` 的重试逻辑完全一致
   - 抽出 `_retry_loop(action, *, extractor_name, extension, context_label, ...)`
     作为骨架，两个公共方法只负责查找提取器与构造 action lambda

6. **向后兼容**：
   - 保留原 `extract_content_from_bytes` / `extract_content_with_fallback` 公共 API
   - 新增 `*_with_retry` 系列函数，调用方可按需切换
   - 三个内部调用点（scanner/archive）切换到新 API，外部行为不变（重试仅对
     瞬时错误生效，最终仍走纯文本回退）

## 代码实现情况

### `ExtractorFailure` 诊断结构

```python
@dataclass(frozen=True)
class ExtractorFailure:
    extractor_name: str        # 提取器类名
    extension: str             # 文件扩展名
    error_type: str            # 异常类型名
    error_message: str         # 异常消息前 200 字符
    retried: bool              # 是否触发了重试
    succeeded_after_retry: bool  # 重试后是否成功
```

### `is_retriable_error` 异常分类

```python
def is_retriable_error(exc: Exception) -> bool:
    """仅 OSError 视为可重试瞬时错误。"""
    return isinstance(exc, OSError)
```

### `ExtractorRegistry._retry_loop` 重试骨架

```python
def _retry_loop(self, action, *, extractor_name, extension, context_label,
                max_retries, backoff_ms, on_failure) -> str:
    attempt = 0
    while True:
        try:
            return action()
        except Exception as exc:
            retriable = is_retriable_error(exc)
            if not retriable or attempt >= max_retries:
                # 不可重试或已达上限：上报后抛出
                if on_failure is not None:
                    on_failure(ExtractorFailure(..., retried=attempt > 0, ...))
                raise
            # 可重试且未达上限：上报「准备重试」后 sleep 并重试
            if on_failure is not None:
                on_failure(ExtractorFailure(..., retried=False, ...))
            logger.debug("提取器 %s 提取 %s 失败（%s），%dms 后重试...", ...)
            time.sleep(backoff_ms / 1000.0)
            attempt += 1
```

### 调用点切换

三个调用点统一改为：

```python
content = extract_content_from_bytes_with_retry(data, entry.extension)
```

默认参数 `max_retries=1`、`backoff_ms=50.0`，瞬时 `OSError` 自动重试一次。
失败信息通过 `logger.debug` 在 `_retry_loop` 内部记录。

## 整合优化情况

- 抽出 `_retry_loop` 私有方法消除 `extract_from_bytes_with_retry` /
  `extract_with_retry` 之间的代码重复（约 50 行 → 1 处实现）
- 三个调用点（scanner/_helpers、scanner/_cache_phase、archive/scanner）的
  `except Exception` 回退逻辑保持不变，仅替换内部调用的提取函数
- 测试 mock 签名扩展为接受 `max_retries` / `backoff_ms` / `on_failure` 关键字参数，
  保持向后兼容（旧 mock 不传关键字参数时仍可用 `extract_content_from_bytes`）
- `_FlakyExtractor` 测试夹具复用 `Extractor` 基类与 `@override` 装饰器，
  与既有提取器测试风格一致

## 测试验证结果

```
uv run ruff check src tests           → All checks passed
uv run ruff format --check src tests  → 134 files already formatted
uv run pyrefly check                  → 0 errors (680 suppressed)
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95
                                      → 1964 passed, 54 deselected
                                         TOTAL 95.87% (required 95.0%)
```

`extractors/base.py` 模块覆盖率：96%（未覆盖行为 `SpeedTier.label`/`description`/
`color` 的映射分支，iter-90 起已存在，与 iter-119 无关）

新增 23 个测试（5 个测试类）：

- `TestIsRetriableError`（3 个）：异常分类
  - `test_os_error_is_retriable`：OSError 及子类可重试
  - `test_extractor_error_is_not_retriable`：ExtractorError 不可重试
  - `test_other_exception_is_not_retriable`：ValueError/RuntimeError/KeyError 不可重试

- `TestExtractFromBytesWithRetry`（10 个）：内存字节提取重试逻辑
  - `test_success_no_retry`：成功不重试
  - `test_retry_succeeds_on_second_attempt`：第一次 OSError，重试成功
  - `test_retry_exhausted_raises_os_error`：重试耗尽抛原始 OSError
  - `test_non_retriable_error_no_retry`：ExtractorError 不重试
  - `test_max_retries_zero_means_no_retry`：max_retries=0 退化为不重试
  - `test_multiple_retries_until_success`：max_retries=3 前 2 次失败、第 3 次成功
  - `test_unregistered_extension_returns_empty`：未注册扩展名返回空字符串
  - `test_on_failure_callback_invoked_for_retriable`：可重试错误回调（retried=False）
  - `test_on_failure_callback_invoked_on_exhaustion`：重试耗尽回调（retried=True）
  - `test_on_failure_callback_invoked_for_non_retriable`：不可重试错误回调
  - `test_backoff_delay_applied_between_retries`：sleep 参数正确传递

- `TestExtractWithPathRetry`（3 个）：路径版本重试
  - `test_path_retry_succeeds_on_second_attempt`：路径版本重试成功
  - `test_path_uses_extension_inference`：extension=None 从路径推断
  - `test_path_unregistered_extension_returns_empty`：未注册扩展名返回空

- `TestModuleLevelRetryFunctions`（4 个）：模块级便捷函数
  - `test_extract_content_from_bytes_with_retry_uses_default_registry`：使用默认注册表
  - `test_extract_content_with_fallback_and_retry_falls_back_to_plaintext`：失败回退纯文本
  - `test_extract_content_with_fallback_and_retry_returns_extracted_content`：成功返回提取内容
  - `test_extract_content_with_fallback_and_retry_retry_succeeds`：重试成功不走回退

- `TestExtractorFailureDataclass`（2 个）：诊断数据类
  - `test_failure_is_frozen`：frozen dataclass 不可变
  - `test_failure_truncates_long_message`：error_message 截断到 200 字符

## 遗留事项

- `on_failure` 回调机制作为公共 API 已提供，但扫描热路径（Scanner）暂未注入回调
  聚合统计到 `PerfStats`。未来如需在 GUI 展示「N 个文件提取失败」，可由 Scanner
  构造回调写入 `PerfStats` 的新字段（如 `extractor_failures`）
- 未做 benchmark 数据佐证（留待 iter-120 性能基线建立）：重试仅在失败时发生，
  正常路径零开销（`max_retries=1` 时仅多一次 `is_retriable_error` 调用，约 1μs）
- `extract_content_with_fallback` 旧 API 保留，未标记 deprecated（外部调用方可能
  依赖）；新调用方应使用 `extract_content_with_fallback_and_retry`

## 下一轮计划

iter-120：性能基线建立与回归门禁（pytest-benchmark）
- 评估 `tests/test_benchmark.py` 与 `tests/test_extractor_benchmark.py` 现状
- 建立扫描热路径性能基线（`extract_with_cache` / `default_extract_content_with_hash` /
  缓存查询路径）
- 配置 `pytest-benchmark` 回归门禁：基线偏差 > 10% 时 CI 失败
- 为 iter-118 的三层 LRU 缓存与 iter-119 的重试机制提供 benchmark 数据佐证
- 文档化性能基线到 `.trae/docs/perf-baseline.md`，供后续迭代对比
