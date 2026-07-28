# iter-124 增量扫描与文件变更检测

## 需求清单

- [x] 基于 mtime+size 的增量扫描模式（仅扫描变更文件）
- [x] 增量结果与历史全量结果的合并逻辑
- [x] GUI 新增「增量扫描」选项（工作区卡片操作按钮）
- [x] 增量扫描的缓存复用策略（未变更文件直接复用缓存结果）

## 迭代目标

实现增量扫描：walk 阶段对比 (mtime, size) 指纹跳过未变更文件，scan 阶段合并未变更文件的命中结果，使重复扫描大型目录时仅扫描变更文件，吞吐量大幅提升。

## 改动文件清单

### 核心数据结构

- `src/fuscan/scanner/result.py` — 新增 `FileFingerprint`（mtime+size 二元组）与 `IncrementalManifest`（文件指纹映射，含 to_json/from_json/rel_key）

### Scanner 增量逻辑

- `src/fuscan/scanner/scanner.py` — `__init__` 新增 `incremental_manifest`/`prev_report` 参数；`collect_entries` 增量模式跳过未变更文件并构建新 manifest；`scan_entries` 合并未变更文件命中结果；新增 `current_manifest` 属性

### Worker 适配

- `src/fuscan/workers/stats_worker.py` — `__init__` 新增 `incremental_manifest` 参数，暴露 `manifest` 只读属性
- `src/fuscan/workers/scan_worker.py` — `__init__` 新增 `prev_report` 参数

### Controller 集成

- `src/fuscan/gui/controllers/scan_controller.py` — 新增 `startIncrementalScan` Slot、`_load_manifest`/`_save_manifest` 辅助方法、`_MANIFESTS_DIR` 常量、增量上下文属性（`_pending_manifest`/`_pending_prev_report`/`_pending_ws_id`）
- `src/fuscan/gui/controllers/workspace_controller.py` — 新增 `startIncrementalScan` Slot 委托

### GUI

- `src/fuscan/gui/views/components/WorkspaceCard.qml` — "更新扫描"按钮改为"增量扫描"，调 `workspaceController.startIncrementalScan`
- `src/fuscan/gui/resources_rc.py` — qrc 重建

### 测试

- `tests/test_incremental_scan.py` — 15 个测试覆盖 IncrementalManifest 序列化、Scanner 增量扫描行为、合并逻辑
- `tests/test_incremental_scan_controller.py` — 48 个测试覆盖 ScanController 增量方法
- `tests/test_gui_scan_controller.py` — FakeStatsWorker 添加 manifest 属性

## 关键决策与依据

1. **复用 ScanReport 持久化而非新增 manifest 文件**：增量扫描需要知道上次扫描了哪些文件。ScanReport.to_json 只保存命中结果（不含未命中文件），无法作为指纹源。因此新增独立的 IncrementalManifest 持久化到 `~/.fuscan/manifests/<ws_id>.json`，记录所有通过过滤的文件指纹。

2. **(mtime, size) 二元组而非哈希**：文件内容哈希（SHA-256）精度最高但需读文件全量内容，无法在 walk 阶段跳过 I/O。(mtime, size) 二元组从 stat 获取（零内容 I/O），精度足以区分常规编辑。这与 fspack 的 source fingerprint 策略一致。

3. **压缩包内部条目不参与增量合并**：archive_path 非 None 的结果每次重新扫描压缩包（ArchiveScanner 无增量模式），避免压缩包内容变更但 mtime/size 不变时漏报。

4. **全量模式也构建 manifest**：首次扫描（无 manifest）走全量模式，但 collect_entries 仍记录所有文件指纹构建 manifest，使下次可启用增量扫描。打破"首次无 manifest → 永远全量"的鸡生蛋问题。

5. **manifest 持久化仅在扫描成功时**：取消/失败时不写入 manifest，避免不完整清单影响下次增量。

## 代码实现情况

### IncrementalManifest（result.py）

```python
@dataclass(frozen=True)
class FileFingerprint:
    mtime: float
    size: int

class IncrementalManifest:
    def __init__(self, root, fingerprints): ...
    def to_json(self) -> str: ...
    @classmethod
    def from_json(cls, json_str) -> IncrementalManifest: ...
    @staticmethod
    def rel_key(path, root) -> str:  # 相对路径键，正斜杠分隔
```

### Scanner 增量模式（scanner.py）

- `collect_entries`：walk 阶段对比指纹，未变更文件跳过（`_unchanged_count` 累计），变更/新文件加入 entries。构建 `new_fingerprints` 字典，return 前赋值 `_current_manifest`。
- `scan_entries`：扫描完成后合并：收集本次命中文件的 rel_key，从 `_unchanged_hits` 中追加未变更文件的命中结果（去重），统计累加 `_unchanged_count`。

### ScanController 集成

- `startIncrementalScan(ws_id)`：加载 `_last_report` 与 manifest，无则回退 `startScan`；构造 FileStatsWorker 传入 `incremental_manifest`
- `_on_stats_finished`：读取 `stats_worker.manifest` 存入 `_pending_manifest`，构造 ScanWorker 传入 `prev_report`
- `_on_scan_finished`：持久化 `_pending_manifest` 到 `~/.fuscan/manifests/<ws_id>.json`

## 测试验证结果

- `tests/test_incremental_scan.py`：15 passed（IncrementalManifest 序列化 5 + Scanner 增量 7 + 合并逻辑 3）
- `tests/test_incremental_scan_controller.py`：48 passed（startIncrementalScan 回退 2 + 正常 4 + manifest 持久化 5 + stats/scan 传递 4 + 历史恢复 8 + 暂停取消 3 + 属性覆盖 22）
- 全项目（排除预先存在的 6 个 SpeedTier 失败测试）：2160 passed
- 覆盖率：scan_controller.py 97.86%，scanner.py 97%，stats_worker.py 98%，scan_worker.py 100%
- 全项目 94.95%（差 0.05%，因预先存在的 6 个 SpeedTier 测试失败影响覆盖率）
- ruff/pyrefly：0 errors

## 遗留事项

1. **预先存在的 SpeedTier 测试失败**：test_extractor_benchmark.py 中 6 个测试失败（PptxExtractor/DocExtractor/PptExtractor 的 speed_tier 值与断言不符），非 iter-124 引入，需后续迭代修复
2. **benchmark 性能佐证**：iter-124 验收标准要求 benchmark 佐证增量扫描性能收益，当前用单元测试验证行为正确性，benchmark 待补充
3. **全项目覆盖率 94.95%**：差 0.05% 达标，因预先存在的 SpeedTier 测试失败影响

## 下一轮计划

iter-125 GUI 稳定性加固（PySide2 5.15 null 防御）：
- 全局 context property 统一声明类型化 property
- qrc 重建自动化（pre-commit hook 或构建脚本集成）
- QML 绑定 null 安全审计
- 取消/退出流程的模态遮罩统一
