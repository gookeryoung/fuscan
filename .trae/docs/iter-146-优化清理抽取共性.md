# iter-146 优化清理抽取共性

## 需求清单

- [x] 扫描 src/fuscan 找共性可抽取代码、无效代码、潜在 BUG
- [x] 与用户确认优化范围（4 项全部确认）
- [x] DEAD-1：清理 3 处空 `if TYPE_CHECKING: pass` 死代码块
- [x] DUP-1：抽取 `build_hit_from_match` / `rebuild_hit_from_cache` 工厂函数
- [x] BUG-1：修复 archive/scanner.py 三处 RuleHit 构造缺失 `match_texts`/`match_description`
- [x] DUP-2：archive reader `close`/`__enter__`/`__exit__` 上移到 `ArchiveReader` 基类
- [x] 新增 BUG-1 回归测试 3 条
- [x] 门禁检查（ruff/format/pyrefly/pytest/coverage）
- [x] 写迭代记录，删除 iter-141 保留最新 5 条
- [x] git commit + push

## 迭代目标

用户要求「优化清理代码，抽取共性部分，移除无效代码，修复BUG」。扫描代码库后发现 1 个明确 BUG、2 处可抽取共性、1 处死代码，与用户确认后全部修复。

## 改动文件清单

- `src/fuscan/cache/schema.py`：删除空 `if TYPE_CHECKING: pass` 块及未使用导入
- `src/fuscan/gui/controllers/config_controller.py`：同上
- `src/fuscan/gui/controllers/whitelist_controller.py`：同上
- `src/fuscan/scanner/_helpers.py`：新增 `build_hit_from_match` / `rebuild_hit_from_cache` 两个工厂函数
- `src/fuscan/scanner/scanner.py`：3 处 RuleHit 构造改为调用工厂函数
- `src/fuscan/scanner/_cache_phase.py`：`build_hits_from_cache` 改用 `rebuild_hit_from_cache`
- `src/fuscan/archive/scanner.py`：3 处 RuleHit 构造改为调用工厂函数（同时修复 BUG-1）
- `src/fuscan/archive/base.py`：`ArchiveReader` 新增 `_close_resource` 抽象方法 + `close`/`__enter__`/`__exit__` 模板
- `src/fuscan/archive/zip_reader.py`：移除 `close`/`__enter__`/`__exit__`，改为实现 `_close_resource`
- `src/fuscan/archive/rar_reader.py`：同上
- `src/fuscan/archive/sevenz_reader.py`：同上，额外覆盖 `close` 调用 `super().close()` 后清空 `_bytes_cache`
- `tests/test_archive.py`：新增 `TestArchiveScannerHitFields` 3 条 BUG-1 回归测试

## 关键决策与依据

### BUG-1：archive/scanner.py RuleHit 字段缺失

`archive/scanner.py` 三处 `RuleHit` 构造（L239-247 uncached、L305-314 cached-from-cache、L328-336 cached-new-match）缺失 `match_texts` 与 `match_description` 字段，导致字段默认为空。

对比 `scanner/scanner.py` L738-748、L895-907、L920-930 与 `_cache_phase.py` L120-130 均完整包含两字段。archive 路径遗漏属回归 BUG（iter-41 引入 `match_texts`/`match_description` 时未同步 archive）。

**影响**：压缩包扫描结果丢失 AND/OR 组合规则的多匹配文本与描述；缓存写入的也是缺字段 RuleHit，后续读取同样缺失；GUI 详情表与导出结果中压缩包条目信息不完整。

### DUP-1：抽取 RuleHit 工厂函数

`RuleHit` 构造模式在 6 处重复（scanner.py 3 处 + archive/scanner.py 3 处），从 `MatchResult` 构造 4 处、从缓存 `RuleHit` 重建 2 处。满足「三处相似才考虑提取」阈值。

抽取到 `scanner/_helpers.py`（纯函数辅助模块，已有 `rules.model` 运行时导入）：

- `build_hit_from_match(rule, result: MatchResult) -> RuleHit`：从 MatchResult 构造，字段映射集中
- `rebuild_hit_from_cache(rule, cached: RuleHit) -> RuleHit`：从缓存重建，填回 rule_name/severity

抽取同时修复 BUG-1（archive 三处自动获得完整字段）。

### DUP-2：archive reader 上下文管理器上移

`zip_reader`/`rar_reader`/`sevenz_reader` 三个 reader 重复实现 `close()`（try/except + log debug 包装）+ `__enter__` + `__exit__`，共约 45 行样板代码。

`ArchiveReader` 基类新增：

- `_close_resource()` 抽象方法：子类实现裸关闭调用
- `close()` 模板方法：try/except 包装 + debug 日志
- `__enter__`/`__exit__`：统一上下文管理器，用 `TypeVar` 保留子类返回类型

`SevenZReader` 特殊处理：覆盖 `close()` 调用 `super().close()` 后清空 `_bytes_cache`（释放惰性读取缓存的解压字节）。

### DEAD-1：空 TYPE_CHECKING 块

`cache/schema.py`、`config_controller.py`、`whitelist_controller.py` 三处 `if TYPE_CHECKING: pass` 空块，连同未使用的 `from typing import TYPE_CHECKING` 一并删除。属历史重构遗留（原 TYPE_CHECKING 块内的导入被移走后未清理空壳）。

## 代码实现情况

### _helpers.py 新增工厂函数

```python
def build_hit_from_match(rule: Rule, result: MatchResult) -> RuleHit:
    """从 MatchResult 构造 RuleHit，字段映射集中在此处。"""
    return RuleHit(
        rule_name=rule.name,
        severity=rule.severity,
        detail=result.detail,
        match_text=result.match_text,
        match_count=result.match_count,
        target=result.target,
        match_texts=result.match_texts,
        match_description=result.match_description,
    )


def rebuild_hit_from_cache(rule: Rule, cached: RuleHit) -> RuleHit:
    """从缓存 RuleHit 重建并填回 rule_name。"""
    return RuleHit(
        rule_name=rule.name,
        severity=rule.severity,
        detail=cached.detail,
        match_text=cached.match_text,
        match_count=cached.match_count,
        target=cached.target,
        match_texts=cached.match_texts,
        match_description=cached.match_description,
    )
```

### ArchiveReader 基类模板方法

```python
@abstractmethod
def _close_resource(self) -> None:
    """关闭底层资源句柄（由 close() 包装异常处理）。"""

def close(self) -> None:
    """关闭资源，捕获并记录异常（不抛出）。"""
    try:
        self._close_resource()
    except Exception:  # pragma: no cover - 关闭异常无需上报
        logger.debug("关闭压缩文件句柄失败: %s", getattr(self, "_path", "<unknown>"), exc_info=True)

def __enter__(self: _T) -> _T:
    return self

def __exit__(self, exc_type, exc, tb) -> None:
    self.close()
```

### SevenZReader 覆盖 close

```python
@override
def _close_resource(self) -> None:
    self._sevenz.close()

@override
def close(self) -> None:
    """关闭 7Z 文件句柄并释放字节缓存。"""
    super().close()
    self._bytes_cache.clear()
```

## 测试验证结果

- `uv run ruff check src tests`：通过
- `uv run ruff format --check src tests`：163 files already formatted
- `uv run pyrefly check src`：0 errors
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：**2452 passed, 0 failed, 75 deselected, coverage 96.55%**

### cache.db 锁问题已解除

iter-145 遗留的 42 个 cache 测试失败（`~/.fuscan/cache.db` 15.7GB 被残留进程 PID 14852 锁定）现已全部通过。用户已手动终止进程并清理 cache.db，本次完整套件 2452 passed 0 failed。

### BUG-1 回归测试

新增 `TestArchiveScannerHitFields` 3 条测试覆盖三条路径：

- `test_uncached_hit_includes_match_texts_and_description`：无缓存路径
- `test_cached_new_match_includes_match_texts_and_description`：缓存首次扫描（新匹配）
- `test_cached_hit_from_cache_preserves_match_texts_and_description`：缓存命中（第二次扫描）

## 遗留事项

1. STYLE-1：`scan_controller.py` L1209/L1211 两处裸 `# type: ignore`（无规则码），其他 PySide2/6 导入统一用 `# pyrefly: ignore [missing-import]`。低优先级风格问题，未纳入本轮。
2. STYLE-2：多处 `except Exception:` 用法（scanner/scanner.py L733/L915、archive/scanner.py L229/L318、_helpers.py L122、extractors/base.py L496/L563、_archive_phase.py L101/L153、scan_worker.py L247、stats_worker.py L189）大多是 intentional 防御性模式，违反 python-standards「禁止 except Exception」硬约束。改为窄异常会丢失对第三方库意外的兜底，需单独评估每处的预期异常类型，未纳入本轮。
3. iter-145 遗留：`_TIER_TIME_LIMITS` 动态阈值仍用硬编码，待后续处理。
4. iter-145 遗留：`ScanController._build_cache_context` 用 `default_cache_path()` 而非 tmp_path，建议后续 mock `config.cache_path` 实现测试隔离。

## 下一轮计划

无主动迭代计划。等待用户反馈或新需求。
