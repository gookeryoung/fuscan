# 需求 45：三阶段扫描重构（Collect → Filter → Parse）

## 背景

当前两阶段扫描架构：
- **Collect**：`FileWalker` + `Scanner.collect_entries` 遍历目录树收集 entries
- **Parse**：`Scanner.scan_entries` 并发/顺序解析文件内容，内部在 `_scan_entry_uncached` 做 `max_file_size` 跳过

问题：
- 大文件跳过逻辑散落在 scan 阶段深处，无法独立统计被跳过的文件数与原因
- 符号链接文件、空文件、不可读文件等本不应进入扫描队列的条目仍占用扫描线程时间
- scan 阶段进度无法反映"实际待扫描文件数"（分母被无效条目稀释）

## 目标

在 Collect 与 Parse 之间插入独立 **Filter** 阶段，对 walk 产物做二次筛选：

1. **空文件**（`size == 0`）：扫描无意义（CONTENT 规则无文本可匹配）
2. **超限文件**（`size > max_file_size`）：避免一次性读入内存卡死
3. **不可读文件**（`os.access(R_OK) == False`）：避免 scan 阶段抛 OSError
4. **符号链接文件**（`follow_symlinks=False` 且 `path.is_symlink()`）：避免重复扫描链接目标

筛选后 entries 仅保留真正可扫描的文件，scan 阶段分母准确，进度反馈更真实。

## 范围

### 数据结构变更（result.py）

- [x] 新增 `FilterStats` frozen dataclass：`removed_empty / removed_oversize / removed_unreadable / removed_symlink` + `total_removed` 只读属性
- [x] `ProgressInfo` 新增 `phase="filter"` 支持与 `filter_removed_empty/oversize/unreadable/symlink` 字段
- [x] `WalkResult` 新增 `filtered_entries: tuple[FileEntry, ...]` 与 `filter_stats: FilterStats | None`
- [x] `ScanStats` 新增 `filter_removed: int` 累计被筛选剔除的文件数

### 模块组织

- [x] 新建 `src/fuscan/scanner/_filter_phase.py`：纯函数 `run_filter_phase(scanner, walk_result) -> WalkResult`
- [x] `Scanner.filter_entries(walk_result)` 薄包装方法
- [x] 模式与 `_pipeline_phase.py` / `_archive_phase.py` 一致（module-level function + Scanner 实例参数）

### 调用流程

- [x] `Scanner.collect_entries` 不变（仅 walk）
- [x] `Scanner.filter_entries` 新增：调 `run_filter_phase`，emit `phase="filter"`，返回带 `filtered_entries` 的新 WalkResult
- [x] `Scanner.scan_entries` 修改：优先用 `walk_result.filtered_entries`（`filter_stats is not None` 时），否则回退到 `walk_result.entries`（向后兼容）
- [x] `_scan_entry_uncached` 移除 `max_file_size` 跳过逻辑（已前移到筛选阶段）
- [x] `ScanWorker.run` 修改：对每个 wr 先调 `filter_entries`，再调 `scan_entries(filtered_wr)`
- [x] `_on_progress` 透传 filter 阶段字段
- [x] `ScanController._on_scan_progress` 处理 `phase="filter"`，阶段切换 walk → filter → scan → archive → done
- [x] `ScanController` 阶段常量新增 `PHASE_FILTER="filter"`

### 测试

- [x] `TestScannerFilterPhase` 类：覆盖各筛除原因（empty/oversize/symlink/unreadable）
- [x] `filter_stats` 正确填充
- [x] `scan_entries` 优先使用 `filtered_entries`
- [x] `_scan_entry_uncached` 不再跳过 oversize（已被 filter 前移）
- [x] filter phase progress emit 测试
- [x] `ScanStats.filter_removed` 累计测试

## 验收标准

- 全套门禁通过：`uv run ruff check src tests` / `uv run ruff format --check src tests` / `uv run pyrefly check` / `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`
- 不破坏现有测试（向后兼容 `scan_entries` 接受无 `filtered_entries` 的 WalkResult）
- 中文 docstring 与注释，符合 python-standards SKILL

## 迭代记录

- Iter 148：后端实现（数据结构 + filter phase + scanner/worker/controller 接入 + 测试）
- Iter 149（计划）：UI 重构 `ScanStageList.qml` 展示 filter 阶段进度
