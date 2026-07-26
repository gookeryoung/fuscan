# iter-105：代码结构完善——常量化、死代码清理、scanner 与 cache 拆分

## 需求清单

- [x] 低风险去重与常量化：抽取扫描模式映射、50MB 默认值、工作区状态字符串等常量到单一来源
- [x] 修复 rule-12 色值违规（已在 iter-104 前完成）
- [x] 拆分 scanner.py（原 1026 行）为 scanner.py + _helpers.py + _archive_phase.py
- [x] 拆分 cache/store.py（原 886 行）为 store.py + _helpers.py + _queries.py + _writes.py + _cleanup.py

## 迭代目标

1. 消除多处重复定义的扫描模式映射、大文件默认值、工作区状态字符串，集中到单一模块作为唯一权威来源
2. 删除 scan_controller 中未被 QML 引用的 statusBadgeColor/statusBadgeBorder/statusBadgeText 死代码及对应测试
3. 拆分 scanner.py：将纯函数与常量抽到 `_helpers.py`，将压缩包扫描阶段逻辑抽到 `_archive_phase.py`，使 Scanner 类专注核心调度
4. 拆分 cache/store.py：按职责拆分为连接管理（store.py 保留）、查询（_queries.py）、写入（_writes.py）、清理统计（_cleanup.py）、数据类与工具（_helpers.py）

## 改动文件清单

### 新增模块

#### GUI 常量化
- `src/fuscan/gui/scan_mode.py`：集中管理扫描模式映射（索引↔字符串↔中文文本三向映射），消除 workspace_model/scan_controller/workspace_controller 三处重复

#### Scanner 拆分
- `src/fuscan/scanner/_helpers.py`（157 行）：从 scanner.py 抽离纯函数与模块级常量
  - 常量：`BATCH_THRESHOLD`、`PROGRESS_LIST_MAX`、`GIL_YIELD_INTERVAL`
  - 函数：`normalize_max_file_size`、`default_extract_content`、`default_extract_content_with_hash`、`empty_content_provider`、`spec_needs_content`、`cancel_all_futures`
- `src/fuscan/scanner/_archive_phase.py`（189 行）：抽离压缩包扫描阶段逻辑
  - 函数：`run_archive_phase`、`_accumulate_archive_results`、`_emit_archive_progress`、`_collect_archive_futures`
  - 通过将 Scanner 实例作为参数传入访问其运行时状态（与 _archive_phase.py 同样的 module-function-with-host 模式）

#### Cache 拆分
- `src/fuscan/cache/_helpers.py`（83 行）：数据类与无状态工具函数
  - `CacheStats`、`BatchWriteItem` dataclass
  - `default_cache_path`、`now_iso`、`iso_days_ago` 工具函数
  - `HIT_CACHE_MAX` 常量
- `src/fuscan/cache/_queries.py`（196 行）：只读查询子流程
  - `get_cached_hits`、`lookup_file_hash`、`get_extracted_content`、`get_rule_hashes`
- `src/fuscan/cache/_writes.py`（408 行）：写入子流程
  - `register_ruleset`、`_upsert_rule`、`put_result`、`register_file`、`register_path`、`batch_put_results`、`put_extracted_content`
- `src/fuscan/cache/_cleanup.py`（130 行）：清理与统计子流程
  - `prune_orphan_rules`、`prune_stale_files`、`stats`、`_count`

### 修改模块

#### 常量化与死代码清理
- `src/fuscan/config.py`：新增 `DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024` 常量，Config.max_file_size 默认值引用此常量
- `src/fuscan/gui/models/workspace_model.py`：
  - 从 scan_mode 模块导入扫描模式映射函数，移除本模块内的重复映射
  - 新增 `STR_STATUS_READY/SCANNING/PAUSED/DONE/CANCELLED/FAILED` 状态字符串常量与 `ACTIVE_STATUS_TEXTS` 集合
- `src/fuscan/gui/controllers/scan_controller.py`：
  - 从 scan_mode 与 workspace_model 导入常量，移除本模块内的扫描模式映射
  - 移除未被 QML 引用的 `statusBadgeColor`/`statusBadgeBorder`/`statusBadgeText` 属性及对应测试
  - 新增 `STATE_SETUP/SCANNING/RESULTS` 与 `PHASE_SETUP/WALK/SCAN/ARCHIVE/DONE` 状态/阶段字符串常量
- `src/fuscan/gui/controllers/workspace_controller.py`：从 scan_mode 与 workspace_model 导入常量，移除本模块内的扫描模式映射 `_MODE_STR_TO_INDEX`

#### Scanner 重构
- `src/fuscan/scanner/scanner.py`（1026→873 行）：
  - 从 `_helpers` 导入常量与函数，保留模块内别名 `_BATCH_THRESHOLD`/`_DEFAULT_MAX_FILE_SIZE`/`_PROGRESS_LIST_MAX`/`_GIL_YIELD_INTERVAL` 兼容历史引用
  - 移除 `_normalize_max_file_size` 静态方法（已移至 _helpers.normalize_max_file_size）
  - 移除 `_accumulate_archive_results`/`_scan_archive_phase`/`_collect_archive_futures` 死代码（已移至 _archive_phase.py）
  - 补全 `Future` 导入（原未显式导入，靠 `from __future__ import annotations` 延迟求值）
- `src/fuscan/scanner/__init__.py`：从 _helpers 导入 `default_extract_content`/`default_extract_content_with_hash`
- `src/fuscan/archive/scanner.py`：将 `_empty_content_provider`/`_spec_needs_content` 引用改为从 _helpers 导入 `empty_content_provider`/`spec_needs_content`
- `src/fuscan/archive/__init__.py`：修复 `__all__` 导出顺序，将 `ArchiveScanner` 延迟导入放在 `register_all()` 之后并加 `# noqa: E402`

#### Cache 重构
- `src/fuscan/cache/store.py`（886→364 行）：
  - 保留 `CacheStore` 类的连接生命周期管理（`__init__`/`_get_read_conn`/`_init_db`/`close`/`__enter__`/`__exit__`）与内存 LRU 缓存方法（`_hit_cache_*`/`_path_cache_*`）
  - 公共方法改为薄包装委托到 `_queries`/`_writes`/`_cleanup` 子模块
  - 数据类与工具函数从 `_helpers` 导入并 re-export（保持 `from fuscan.cache.store import CacheStats, BatchWriteItem, default_cache_path` 兼容）
  - 常量 `_HIT_CACHE_MAX` 重命名为 `HIT_CACHE_MAX`（从 _helpers 导入）

### 测试
- `tests/test_scanner.py`：
  - `Scanner._normalize_max_file_size` → `fuscan.scanner._helpers.normalize_max_file_size`
  - `from fuscan.scanner.scanner import _cancel_all_futures` → `from fuscan.scanner._helpers import cancel_all_futures`
  - `_DEFAULT_MAX_FILE_SIZE` → 顶层 `DEFAULT_MAX_FILE_SIZE`（从 fuscan.config 导入）
- `tests/test_cache.py`：`mp.setattr(store_mod, "_HIT_CACHE_MAX", 3)` → `mp.setattr(store_mod, "HIT_CACHE_MAX", 3)`
- `tests/test_gui_scan_controller.py`：移除与 `statusBadgeColor` 等死代码相关的测试

## 关键决策与依据

### 1. 常量集中管理的边界
- 扫描模式映射放 `gui/scan_mode.py` 而非 `config.py`：该映射仅 GUI 层使用（QML 切换控件索引↔字符串），不属于全局配置语义
- 工作区状态字符串放 `workspace_model.py`：状态文本与 WorkspaceItem 强相关，QML 侧用字符串字面量与此处对齐
- `DEFAULT_MAX_FILE_SIZE` 放 `config.py`：scanner/archive/config_controller 多处引用，config 是最低层依赖

### 2. scanner.py 拆分模式：module-function-with-host
- 选择将 `_archive_phase` 实现为模块级函数（接收 Scanner 实例）而非 Mixin/基类：
  - 符合项目规则"模块拆分优于基类抽象（无多态需要时）"
  - 与已有 `_archive_phase.py` 模式一致
  - 避免 Mixin 导致的 MRO 复杂性与私有状态暴露
- `_helpers.py` 抽离纯函数：便于独立测试与复用，无状态依赖

### 3. cache/store.py 拆分边界
- 连接管理保留在 store.py：`__init__`/`_get_read_conn`/`_init_db`/`close` 管理连接生命周期，是 CacheStore 类的核心职责
- LRU 缓存方法保留在 store.py：`_hit_cache_*`/`_path_cache_*` 与 `_lru_lock`/`_hit_cache`/`_path_cache` 状态强耦合，抽离需暴露过多私有状态
- 查询/写入/清理抽离为模块函数：SQL 操作通过 `store._conn`/`store._get_read_conn()`/`store._lock` 访问，与 _archive_phase 同模式

### 4. pyrefly `bad-argument-type` 处理
- 现象：`Self@CacheStore` 传递给期望 `CacheStore` 的模块函数参数时，pyrefly 误报
- 选择 `# pyrefly: ignore[bad-argument-type]` 而非 Protocol/`Any`：
  - 14 处调用点的局部忽略，不影响整体类型安全
  - Protocol 需列举所有被访问的私有属性（~15 个），过度工程化
  - `Any` 完全丢失类型检查

### 5. 常量重命名 `_HIT_CACHE_MAX` → `HIT_CACHE_MAX`
- 移到 _helpers 后作为模块公共常量，去掉下划线前缀表示可被同包其他模块引用
- store.py 通过 `from fuscan.cache._helpers import HIT_CACHE_MAX` 导入，测试 monkeypatch 路径同步更新

## 代码实现情况

### scanner.py 拆分前后对比
| 文件 | 拆分前 | 拆分后 |
|------|--------|--------|
| scanner.py | 1026 行 | 873 行 |
| _helpers.py | - | 157 行 |
| _archive_phase.py | - | 189 行 |

### cache/store.py 拆分前后对比
| 文件 | 拆分前 | 拆分后 |
|------|--------|--------|
| store.py | 886 行 | 364 行 |
| _helpers.py | - | 83 行 |
| _queries.py | - | 196 行 |
| _writes.py | - | 408 行 |
| _cleanup.py | - | 130 行 |

## 整合优化情况

- 移除 scanner.py 中三处死代码方法（`_accumulate_archive_results`/`_scan_archive_phase`/`_collect_archive_futures`），这些逻辑已由 `_archive_phase.py` 中的模块级函数实现
- 移除 scan_controller 中三个未被 QML 引用的 `@Property`（statusBadgeColor 等），减少死代码
- 统一扫描模式映射来源，消除三处重复定义
- 统一工作区状态字符串来源，消除散落硬编码

## 测试验证结果

- `uv run ruff check src tests`：All checks passed!
- `uv run ruff format --check src tests`：119 files already formatted
- `uv run pyrefly check`：0 errors (634 suppressed, 68 warnings not shown)
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：1677 passed, 43 deselected, coverage 95.34%

## 遗留事项

- `req-32-QML迁移.md` 未在本迭代处理（与本迭代范围无关）
- `_archive_phase.py` 与 `cache/_queries.py`/`_writes.py`/`_cleanup.py` 中的 `# noqa: SLF001` 注释在 ruff 自动修复时被移除（项目未启用 SLF001 规则），改为依赖 `# pyrefly: ignore[bad-argument-type]` 处理类型检查

## 下一轮计划

无明确下一轮计划。本次代码结构完善已达成全部目标，待用户提出新需求。
