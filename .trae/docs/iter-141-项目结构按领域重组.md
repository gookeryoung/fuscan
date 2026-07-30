# iter-141 项目结构按领域重组

## 需求清单

参见 `.trae/req/req-43-项目结构按领域重组.md`

- [x] 按"彻底：按领域全重组"方案重新组织 `src/fuscan/` 目录结构
- [x] `workers/` 子包移入 `gui/workers/`
- [x] 拆分 `config.py`：资产路径 → `paths.py`，规则加载 → `rules/builtin.py`，暂存/备份探测 → `processing/storage.py`
- [x] 导出逻辑集中到 `export/` 子包（`report.py` + `cli_output.py`）
- [x] `replacer.py` / `skip_store.py` 归入 `processing/` 子包
- [x] 同步更新测试文件命名与导入路径
- [x] 全套门禁验证通过

## 迭代目标

将 `src/fuscan/` 顶层按领域重组为清晰子包结构，消除跨领域混杂模块，使职责边界与目录边界一致。范围包含源码移动、子包入口构造、`__all__` 重整、测试文件改名与导入路径同步、全套门禁验证。

## 改动文件清单

### 新增文件

- `src/fuscan/paths.py`：资产路径常量（`ASSETS_DIR` / `BUILTIN_RULES_PATH` / `MANUAL_PDF_PATH`）
- `src/fuscan/processing/__init__.py`：processing 子包入口
- `src/fuscan/processing/storage.py`：暂存/备份目录探测（`detect_default_staging_dir` / `default_backup_dir`）
- `src/fuscan/rules/builtin.py`：内置规则加载（`load_builtin_ruleset` / `load_with_builtin`）
- `src/fuscan/export/__init__.py`：export 子包入口
- `src/fuscan/export/cli_output.py`：CLI 输出辅助（从 `cli.py` 抽出）
- `.trae/req/req-43-项目结构按领域重组.md`：需求记录

### 移动文件

| 原路径 | 新路径 | 说明 |
|--------|--------|------|
| `src/fuscan/scanner/export.py` | `src/fuscan/export/report.py` | 导出逻辑归 `export/` 子包 |
| `src/fuscan/workers/__init__.py` | `src/fuscan/gui/workers/__init__.py` | Worker 仅服务 GUI |
| `src/fuscan/workers/export_worker.py` | `src/fuscan/gui/workers/export_worker.py` | 同上 |
| `src/fuscan/workers/filter_worker.py` | `src/fuscan/gui/workers/filter_worker.py` | 同上 |
| `src/fuscan/workers/restore_worker.py` | `src/fuscan/gui/workers/restore_worker.py` | 同上 |
| `src/fuscan/workers/scan_worker.py` | `src/fuscan/gui/workers/scan_worker.py` | 同上 |
| `src/fuscan/workers/stats_worker.py` | `src/fuscan/gui/workers/stats_worker.py` | 同上 |
| `src/fuscan/replacer.py` | `src/fuscan/processing/replacer.py` | 替换引擎归 processing |
| `src/fuscan/skip_store.py` | `src/fuscan/processing/skip_store.py` | 跳过存储归 processing |
| `tests/test_workers.py` | `tests/test_gui_workers.py` | 测试命名对齐包路径 |
| `tests/test_replacer.py` | `tests/test_processing_replacer.py` | 同上 |
| `tests/test_skip_store.py` | `tests/test_processing_skip_store.py` | 同上 |

### 修改文件

**源码**：

- `src/fuscan/config.py`：剥离资产路径、规则加载、暂存探测职责；`__all__` 收敛为配置持久化相关符号
- `src/fuscan/cli.py`：导入改用 `fuscan.export.cli_output`
- `src/fuscan/cache/sources.py`：导入路径更新
- `src/fuscan/rules/__init__.py`：导出 `load_builtin_ruleset` / `load_with_builtin`
- `src/fuscan/rules/model.py` / `src/fuscan/rules/whitelist.py`：导入路径更新
- `src/fuscan/scanner/result.py`：导入路径更新
- `src/fuscan/gui/controllers/_result_detail.py`：导入 `fuscan.processing.replacer`
- `src/fuscan/gui/controllers/about_controller.py`：导入 `fuscan.paths.MANUAL_PDF_PATH`
- `src/fuscan/gui/controllers/rules_controller.py`：导入 `fuscan.rules.builtin`
- `src/fuscan/gui/controllers/scan_controller.py`：导入 `fuscan.export.report` / `fuscan.processing.replacer` / `fuscan.gui.workers`
- `src/fuscan/gui/controllers/workspace_controller.py`：导入路径更新
- `src/fuscan/gui/models/result_model.py`：导入路径更新

**测试**：

- `tests/test_builtin.py`：导入 `fuscan.rules.builtin`
- `tests/test_cache.py`：导入路径更新
- `tests/test_config.py`：`detect_default_staging_dir` 改从 `fuscan.processing.storage` 导入；`monkeypatch` 目标改为 `storage_mod.shutil`
- `tests/test_credential_patterns.py`：导入路径更新
- `tests/test_export.py`：导入 `fuscan.export.report`
- `tests/test_export_sarif.py`：同上
- `tests/test_gui_controllers_submodules.py`：导入路径更新
- `tests/test_gui_workers.py`：6 处 `monkeypatch.setattr` 目标从 `fuscan.workers.*` 改为 `fuscan.gui.workers.*`
- `tests/test_incremental_scan_controller.py`：导入路径更新
- `tests/test_multi_format_scan.py`：导入路径更新
- `tests/test_processing_replacer.py`：导入 `fuscan.processing.replacer`
- `tests/test_processing_skip_store.py`：导入 `fuscan.processing.skip_store`

## 关键决策与依据

1. **彻底按领域重组（用户选定）**：用户在两轮提问中明确选择"彻底：按领域全重组"与"包含完整验证"，故采用全量重组而非局部微调。重组后的领域子包：`archive/` / `cache/` / `export/` / `extractors/` / `gui/` / `history/` / `processing/` / `rules/` / `scanner/`，顶层仅保留入口模块（`cli.py` / `config.py` / `paths.py` / `perf.py` / `__init__.py` / `__main__.py`）。

2. **`workers/` 移入 `gui/workers/`**：5 个 Worker（Scan/Stats/Export/Filter/Restore）全部仅服务于 GUI 层，无 CLI 或库调用方。放顶层 `workers/` 易被误判为通用基础设施，移入 `gui/workers/` 后职责归属明确。

3. **`config.py` 三向拆分**：原 `config.py` 同时承载配置持久化、资产路径常量、内置规则加载、暂存目录探测四类职责，违反单一职责。拆分后：
   - `config.py` 仅保留 `Config` dataclass 与 `load/save_config`（纯配置持久化）
   - `paths.py` 集中资产路径常量（与配置无关的静态资源定位）
   - `rules/builtin.py` 承接规则加载（规则相关逻辑归 `rules/` 子包）
   - `processing/storage.py` 承接暂存/备份目录探测（与 `processing/replacer.py` 替换时备份源文件的职责内聚）

4. **`export/` 子包集中导出逻辑**：原 `scanner/export.py`（PDF/Excel/JSON/CSV/HTML/SARIF 生成）与 `cli.py` 中的 CLI 输出辅助混杂。抽出 `export/cli_output.py`，`scanner/export.py` 改名 `export/report.py`，统一归 `export/` 子包。`scanner/` 子包回归"扫描核心"职责。

5. **`replacer.py` / `skip_store.py` 归 `processing/`**：两者均为扫描后处理（内容替换与跳过路径存储），与 `scanner/` 扫描核心职责不同。归 `processing/` 子包后，"扫描 → 处理"的流程边界清晰。

6. **测试文件改名遵循 `test_<包>_<模块>.py`**：`test_workers.py` → `test_gui_workers.py`，`test_replacer.py` → `test_processing_replacer.py`，`test_skip_store.py` → `test_processing_skip_store.py`。对齐 `python-standards` SKILL 测试命名规范。

7. **惰性导入打破循环依赖**：`processing/storage.py` 的 `detect_default_staging_dir` 内部惰性导入 `fuscan.scanner.walker.list_drives`（注释说明原因），避免 `processing` → `scanner` 顶层循环依赖。

## 代码实现情况

### `paths.py`（新增）

集中资产路径常量，与 `config.py` 解耦：

```python
ASSETS_DIR: Path = Path(__file__).parent / "assets"
BUILTIN_RULES_PATH: Path = ASSETS_DIR / "rules" / "builtin.yaml"
MANUAL_PDF_PATH: Path = ASSETS_DIR / "docs" / "fuscan-用户手册.pdf"
```

### `rules/builtin.py`（新增）

从 `config.py` 迁入规则加载便利函数，`lru_cache` 缓存内置规则集：

```python
@lru_cache(maxsize=1)
def load_builtin_ruleset() -> RuleSet:
    return load_ruleset(BUILTIN_RULES_PATH)

def load_with_builtin(user_paths: Sequence[Path] | None = None) -> RuleSet:
    builtin = load_builtin_ruleset()
    if not user_paths:
        return builtin
    user_rulesets = [load_ruleset(p) for p in user_paths]
    return merge_multiple_rulesets(builtin, *user_rulesets)
```

### `processing/storage.py`（新增）

暂存/备份目录探测，惰性导入 `walker.list_drives` 避免循环：

```python
def detect_default_staging_dir() -> Path:
    from fuscan.scanner.walker import list_drives
    # 遍历盘符选剩余空间最大者，回退到 ~/.fuscan-cache
    ...

def default_backup_dir() -> Path:
    return CONFIG_DIR / "backup"
```

### `export/` 子包（新增入口 + 移入）

`__init__.py` 统一导出 `export_pdf` / `export_excel` / `export_report` / `save_report` / `output_report` / `write_output`。

### `config.py`（瘦身）

`__all__` 收敛为：

```python
__all__ = [
    "CONFIG_DIR",
    "CONFIG_PATH",
    "DEFAULT_MAX_FILE_SIZE",
    "IGNORE_DIR_CATEGORIES",
    "Config",
    "load_config",
    "save_config",
]
```

## 整合优化情况

- **消除跨领域混杂**：`config.py` 不再承载规则加载与目录探测；`scanner/` 不再承载导出；`workers/` 不再放顶层。
- **子包入口统一**：新增的 `processing/__init__.py` / `export/__init__.py` 均有完整 docstring 与 `__all__`，符合 `python-project-structure` SKILL 包内部结构规范。
- **测试命名规范化**：3 个测试文件改名对齐 `test_<包>_<模块>.py` 规范。
- **无重复代码**：拆分后无逻辑重复，仅职责迁移。
- **`__all__` 排序**：`rules/__init__.py` 的 `__all__` 按 `RUF022` 规则排序。

## 测试验证结果

### 全套门禁

| 检查项 | 命令 | 结果 |
|--------|------|------|
| ruff check | `uv run ruff check src tests` | All checks passed |
| ruff format | `uv run ruff format --check src tests` | 157 files already formatted |
| pyrefly | `uv run pyrefly check` | 0 errors (780 suppressed, 66 warnings) |
| pytest | `uv run pytest --cov=fuscan --cov-fail-under=95 -m "not slow"` | 2354 passed, 75 deselected, cov 95.80% |

### 关键模块覆盖率（重组后）

| 模块 | 覆盖率 |
|------|--------|
| `gui/workers/__init__.py` | 100% |
| `gui/workers/export_worker.py` | 100% |
| `gui/workers/filter_worker.py` | 100% |
| `gui/workers/restore_worker.py` | 100% |
| `gui/workers/scan_worker.py` | 99% |
| `gui/workers/stats_worker.py` | 100% |
| `processing/replacer.py` | 99% |
| `processing/skip_store.py` | 95% |
| `processing/storage.py` | 97% |
| `rules/builtin.py` | 100% |
| `paths.py` | 100% |
| `export/cli_output.py` | 100%（含于 export 子包） |

### 修复过程

- **`tests/test_gui_workers.py` 16 ERROR + 4 FAILED**：6 处 `monkeypatch.setattr("fuscan.workers.*.Scanner", ...)` 仍用旧路径。修正为 `fuscan.gui.workers.*.Scanner` 后 36 个 worker 测试全部通过。
- **`tests/test_config.py` pyrefly 缺属性**：`monkeypatch` 目标 `fuscan.config.shutil` 已失效（`shutil` 迁至 `processing.storage`）。改为 `import fuscan.processing.storage as storage_mod` 后 `monkeypatch.setattr(storage_mod.shutil, "disk_usage", ...)`。
- **`tests/test_processing_replacer.py` pyrefly 缺模块**：`from fuscan import replacer` 失效。改为 `from fuscan.processing import replacer as replacer_module`。

## 遗留事项

1. **`processing/skip_store.py` 覆盖率 95%（边界）**：第 89、123-124 行未覆盖（异常路径），未达 95%+ 缓冲。后续可补 SQLite 异常路径测试提升至 98%+。
2. **`gui/controllers/workspace_controller.py` 覆盖率 90%**：重组未改其逻辑，覆盖率与重组前一致；遗留至后续控制器测试补全迭代。
3. **`scanner/scanner.py` 覆盖率 94%**：同上，非本次回归。
4. **`extractors/office.py` 86% / `_ooxml_xml.py` 86% / `pdf.py` 88%**：提取器分支路径覆盖不足，非本次回归，遗留至提取器测试补全迭代。

## 下一轮计划

- 视用户反馈决定下一步方向。
- 可选跟进：补 `processing/skip_store.py` SQLite 异常路径测试至 98%+。
- 可选跟进：补 `gui/controllers/workspace_controller.py` 测试至 95%+。
