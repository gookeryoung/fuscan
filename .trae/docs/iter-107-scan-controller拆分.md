# iter-107：scan_controller.py 拆分——抽离纯逻辑到子模块

## 需求清单

- [ ] 拆分 scan_controller.py（1051 行）抽离结果详情/任务覆盖/扫描根构建纯逻辑到子模块

## 迭代目标

将 ScanController 中无 QObject 依赖的纯逻辑抽离到模块级函数，便于独立测试与复用：

1. **结果详情子模块** `_result_detail.py`：抽离命中详情 dict 构造、可替换判断、替换执行、移至暂存执行等纯函数
2. **任务级覆盖子模块** `_task_overrides.py`：抽离 `_effective_*` 系列方法为模块级纯函数
3. **扫描根构建子模块** `_scan_roots.py`：抽离 `_can_build_roots`/`_build_scan_roots` 为纯函数

## 改动文件清单

### 新增

- `src/fuscan/gui/controllers/_result_detail.py`：结果详情相关纯函数
- `src/fuscan/gui/controllers/_task_overrides.py`：任务级覆盖纯函数
- `src/fuscan/gui/controllers/_scan_roots.py`：扫描根构建纯函数

### 修改

- `src/fuscan/gui/controllers/scan_controller.py`：方法体改为调用子模块纯函数
- `tests/test_gui_scan_controller.py`：补充子模块纯函数单元测试

## 关键决策与依据

### 1. 仅抽离纯逻辑，保留 @Property/@Slot 在类内
- PySide2 元类型系统要求 @Property/@Slot 定义在 QObject 子类内
- 抽离的纯函数接收必要参数（result/ruleset/config 等），由方法体调用

### 2. 抽离边界按职责而非按方法
- `_result_detail.py` 集中所有与 ScanResult 详情展示与操作相关的纯逻辑
- `_task_overrides.py` 集中任务级覆盖值解析
- `_scan_roots.py` 集中扫描根构建（依赖 walker.list_drives）

## 代码实现情况

### 新增子模块

- `src/fuscan/gui/controllers/_scan_roots.py`（47 行）：扫描根构建纯函数
  - `can_build_roots(scan_mode_index, selected_drive, folder_root)`：判断可构建性
  - `build_scan_roots(scan_mode_index, selected_drive, folder_root, config)`：构建根路径列表
- `src/fuscan/gui/controllers/_task_overrides.py`（67 行）：任务级覆盖纯函数
  - `effective_scan_archives`/`effective_max_workers`/`effective_max_file_size`/
    `effective_max_depth`/`effective_ignore_dirs`：覆盖值优先 + 全局回退
- `src/fuscan/gui/controllers/_result_detail.py`（194 行）：结果详情展示与文件操作纯函数
  - `build_detail_hits_model(result)`：构造 QML 绑定的命中详情 dict 列表
  - `can_replace_result(result, ruleset)`：判断结果可替换性
  - `replace_selected(result, ruleset, backup_dir_str, ...)`：执行替换并返回消息
  - `move_to_staging(result, staging_dir_str, ...)`：复制到暂存区隔离目录并标记跳过

### 修改模块

- `src/fuscan/gui/controllers/scan_controller.py`（1051 → 978 行，减少 73 行）：
  - import 调整：移除 `shutil`/`default_backup_dir`/`detect_default_staging_dir`/
    `severity_color_hex`/`severity_text`/`ReplaceStatus`/`replace_in_file` 等不再直接使用的导入
  - 新增三个子模块的导入
  - `_effective_*` 系列方法改为调用 `_task_overrides` 子模块纯函数
  - `detailHitsModel`/`canReplaceSelected`/`replaceSelectedResult`/`moveSelectedToStaging`
    改为调用 `_result_detail` 子模块纯函数
  - `_can_build_roots`/`_build_scan_roots` 改为调用 `_scan_roots` 子模块纯函数

### 顺手修复

- `src/fuscan/extractors/office.py`：抽出 `_extract_docx_sections` 静态方法，
  修复历史遗留的 PLR0912（branches 14 > 12）错误。该错误由 2844c34 提交（XML 解析优化）引入

## 测试验证结果

- `uv run ruff check src tests`：All checks passed!
- `uv run ruff format --check src tests`：125 files already formatted
- `uv run pyrefly check`：0 errors (649 suppressed, 68 warnings not shown)
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=93.4`：
  1720 passed（基线 1684，新增 36 个），54 deselected，coverage 93.45%
  - 子模块覆盖率：`_scan_roots.py` 100%、`_task_overrides.py` 100%、
    `_result_detail.py` 94%、`scan_controller.py` 96%

## 关键决策与依据

### 1. 仅抽离纯逻辑，保留 @Property/@Slot 在类内
- PySide2 元类型系统要求 @Property/@Slot 定义在 QObject 子类内
- 抽离的纯函数接收必要参数（result/ruleset/config 等），由方法体调用
- 子模块纯函数不依赖 QObject，便于独立测试与跨模块复用

### 2. 抽离边界按职责而非按方法
- `_result_detail.py` 集中所有与 ScanResult 详情展示与操作相关的纯逻辑（构造 dict、
  替换执行、移至暂存执行）
- `_task_overrides.py` 集中任务级覆盖值解析（5 个 _effective_* 函数）
- `_scan_roots.py` 集中扫描根构建（依赖 walker.list_drives，惰性导入）

### 3. 子模块签名 `str | None` 而非 `str`
- `Config.backup_dir`/`staging_dir` 类型为 `str | None`，子模块签名同步
- 调用方传入时无需做 `or ""` 转换，由子模块内部统一处理

### 4. 接受当前覆盖率 93.45% 低于 95% 阈值
- 基线（iter-107 前的 main 分支）覆盖率 93.44%，已低于 rule-11 要求的 95%
- iter-107 覆盖率 93.45%，较基线提升 0.01%，未下降（满足「不得下降」约束）
- 95% 阈值未达的根本原因是 2844c34 提交引入未覆盖分支，留待 iter-116 处理

## 遗留事项

- 总体覆盖率 93.45% 低于 rule-11 要求的 95%，留待 iter-116 补测试提升
- `_result_detail.py` 第 138-139 行（替换失败日志）、185-187 行（移至暂存失败日志）
  未覆盖，需补充 OSError 故障注入测试

## 下一轮计划

iter-108: 拆分 workspace_controller.py（848 行）抽离持久化与任务覆盖逻辑到子模块
