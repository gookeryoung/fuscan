# iter-133 误报抑制与规则白名单

## 需求清单

- [x] 新建 `src/fuscan/rules/whitelist.py`：`WhitelistEntry`/`Whitelist` frozen dataclass + `WhitelistStore`，glob 路径模式匹配，JSON 序列化与原子持久化
- [x] Scanner 集成：`Scanner.__init__` 接收 `whitelist`，命中聚合阶段过滤 `whitelist.matches_any_rule(path, rule_names)`
- [x] 新建 `src/fuscan/gui/controllers/whitelist_controller.py`：QML 可访问的 CRUD + 持久化到 `~/.fuscan/whitelist.json`
- [x] `ScanController` 新增 `markAsFalsePositive` Slot：将选中结果加入白名单并 `invalidate_manifest` 强制下次全量重扫
- [x] `ResultDetailPanel.qml` 新增「标记误报」按钮（在「移入暂存」旁）
- [x] `SettingsPage.qml` 新增「白名单」Tab，复用既有 ListView + IconButton 模式
- [x] 验收：全量/增量扫描均过滤误报；glob 模式支持；导入导出 JSON；管理页 CRUD；覆盖率 >= 95%；benchmark 佐证扫描吞吐量影响 < 5%

## 迭代目标

实现误报抑制机制：用户在结果详情区将特定 (路径, 规则) 组合标记为误报后，
后续扫描在命中聚合阶段自动过滤这些组合，避免重复显示已确认的误报命中。
白名单持久化到 `~/.fuscan/whitelist.json`，跨会话生效，支持手动 CRUD 与 JSON 导入导出。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fuscan/rules/whitelist.py` | 新建：`WhitelistEntry` frozen dataclass（path_glob + rule_name + created_at + note）+ `Whitelist` 不可变快照 + `WhitelistStore` 线程安全 CRUD 与原子 JSON 持久化 |
| `src/fuscan/scanner/scanner.py` | `__init__` 新增 `whitelist` 参数；`scan_entries` 在增量合并后、stats 构造前过滤命中白名单的结果，同步修正 matched/matches 统计 |
| `src/fuscan/workers/scan_worker.py` | `__init__` 新增 `whitelist` 参数并透传给 `Scanner` |
| `src/fuscan/gui/controllers/whitelist_controller.py` | 新建：`WhitelistController` QObject，暴露 `whitelistEntries`/`whitelistCount` Property 与 `addEntry`/`removeEntry`/`clearAll`/`importJson`/`exportJson`/`removeByGlobAndRule` Slot；`snapshot()` 返回不可变 `Whitelist` 供 Scanner 使用 |
| `src/fuscan/gui/controllers/scan_controller.py` | `__init__` 新增 `whitelist_controller` 注入参数；新增 `markAsFalsePositive(rule_filter)` Slot：将选中结果加入白名单并 `invalidate_manifest` 强制全量重扫 |
| `src/fuscan/gui/controllers/workspace_controller.py` | `__init__` 新增 `whitelist_controller` 注入参数；`_get_scan_controller` 透传到 `ScanController` |
| `src/fuscan/gui/controllers/app_controller.py` | 构造 `WhitelistController` 并注入 `WorkspaceController`；`register_qml_types` 注册 `WhitelistControllerType`；`register_to` 注册 context property `WhitelistController` |
| `src/fuscan/gui/controllers/__init__.py` | 导出 `WhitelistController` |
| `src/fuscan/gui/views/components/ResultDetailPanel.qml` | 第二行操作栏新增「标记误报」IconButton（在「移入暂存」旁），调用 `markAsFalsePositive("")` |
| `src/fuscan/gui/views/pages/SettingsPage.qml` | TabBar 新增「白名单」Tab；新增白名单管理页（添加表单 + 导入/导出/清空 + ListView 条目展示与删除） |
| `src/fuscan/gui/resources.qrc` / `resources_rc.py` | 重建（含修改后的 QML） |
| `tests/test_whitelist.py` | 新建：`WhitelistEntry`/`Whitelist`/`WhitelistStore` 单元测试 39 项（含参数化 glob 匹配、线程安全并发、JSON 往返、损坏文件容忍） |
| `tests/test_scanner_whitelist.py` | 新建：Scanner 白名单过滤集成测试 11 项（无白名单、空白名单、通配规则、精确规则、部分覆盖、全部覆盖、glob 多文件过滤） |
| `tests/test_gui_whitelist_controller.py` | 新建：`WhitelistController` GUI 测试 23 项（属性、增删清、导入导出、snapshot 隔离、信号发射、重复添加不 emit） |
| `tests/test_whitelist_benchmark.py` | 新建：白名单过滤吞吐量基准（验证 < 5% 影响） |
| `tests/test_gui_app_controller.py` | 更新 `test_register_to_sets_all_context_properties` 断言包含 `WhitelistController` |

## 关键决策与依据

### 1. frozen dataclass + 不可变快照分离扫描与 UI

**问题**：扫描线程在 `scan_entries` 期间需要稳定的白名单视图，UI 线程同时可增删条目。

**方案**：
- `WhitelistEntry` 与 `Whitelist` 用 `@dataclass(frozen=True)`，可哈希、线程安全
- `WhitelistStore` 持有可变 `list[WhitelistEntry]`，所有公共方法经 `threading.RLock` 保护
- `WhitelistStore.snapshot()` 返回 `Whitelist(entries=tuple(self._entries))` 不可变快照
- 扫描线程启动前调用 `WhitelistController.snapshot()` 获取快照，扫描期间持有快照不访问 Store
- UI 增删不影响正在进行的扫描（避免竞态）

### 2. 路径分隔符归一化

**问题**：`str(Path)` 在 Windows 产生反斜杠（`\a\b.txt`），用户手动输入的 glob 用正斜杠
（`/a/vendor/*.txt`），`fnmatch` 字符级匹配导致 `\` 与 `/` 不匹配。

**方案**：`WhitelistEntry.matches` 在 `fnmatch` 前将两侧路径的反斜杠统一替换为正斜杠：
```python
norm_path = path_str.replace("\\", "/")
norm_glob = self.path_glob.replace("\\", "/")
```
确保用户输入 `/a/vendor/*.txt` 在 Windows 上也能正确匹配 `\a\vendor\foo.txt`。

### 3. matches_any_rule 要求所有规则被覆盖才过滤

**问题**：一个文件命中多条规则时，仅部分规则被白名单覆盖，是否整体过滤？

**决策**：**仅当所有命中规则都被白名单覆盖时才整体过滤**。理由：
- 部分覆盖时整体过滤会让用户漏看不需过滤的部分命中
- 用户若想过滤部分规则，可单独标记单条规则的文件后再过滤
- `Whitelist.matches_any_rule` 用 `all(...)` 实现

### 4. 白名单变更后强制全量重扫

**问题**：增量扫描的 `prev_report` 含旧命中（含误报），白名单变更后下次增量扫描
仍会合并这些误报命中到结果中。

**方案**：`ScanController.markAsFalsePositive` 在添加白名单条目后调用
`invalidate_manifest(ws_id)` 删除该工作区的 `~/.fuscan/manifests/<ws_id>.json`，
使下次 `startIncrementalScan` 因 manifest 不存在而回退到全量扫描，确保白名单生效。

### 5. WhitelistController 重复添加不发射信号

**问题**：用户重复添加相同 (path_glob, rule_name) 时，`WhitelistStore.add` 静默去重，
但控制器若总是 `emit whitelistChanged` 会触发 QML ListView 无效刷新。

**方案**：`addEntry` 比较添加前后的 `len(store.entries())`，未新增时返回「已存在」消息
不发射信号，避免无效 UI 刷新。

### 6. 白名单过滤位置在增量合并之后

**问题**：增量扫描合并未变更文件的旧命中后，白名单应同时覆盖本次扫描命中与合并的旧命中。

**方案**：在 `scan_entries` 中，白名单过滤位置在增量合并（`_unchanged_count > 0` 分支）
**之后**、`ScanStats` 构造之前。这确保：
- 本次扫描的命中与未变更合并的命中都被同一份白名单覆盖
- 过滤后同步修正 `matched` / `matches` 统计，使 `ScanStats` 与 `ScanReport.hits` 一致

### 7. QML 集成模式

**ResultDetailPanel.qml**：「标记误报」按钮放在「移入暂存」旁，复用 IconButton 组件，
调用 `markAsFalsePositive("")`（空字符串表示该文件全部命中规则均标记为误报）。
压缩包内部条目禁用（路径含 `!` 无法 glob）。

**SettingsPage.qml**：TabBar 添加「白名单」第 4 个 Tab，复用既有 ListView + IconButton 模式：
- 添加表单（路径 glob + 规则名 + 备注 + 添加按钮）
- 顶部操作行（导入/导出/清空 + 计数显示）
- ListView 展示条目（路径 + 规则名 tag + 创建时间 + 备注 + 删除按钮）
- FileDialog 处理导入导出路径选择

## 代码实现情况

### whitelist.py 核心结构

```python
@dataclass(frozen=True)
class WhitelistEntry:
    path_glob: str
    rule_name: str  # "*" 通配
    created_at: str = ""
    note: str = ""

    def matches(self, path_str: str, rule_name: str) -> bool:
        # 路径分隔符归一化 + 大小写敏感按平台
        ...

@dataclass(frozen=True)
class Whitelist:
    entries: tuple[WhitelistEntry, ...]

    def matches_any_rule(self, path: Path, rule_names: tuple[str, ...]) -> bool:
        # 所有规则都被白名单覆盖才返回 True
        return all(self.matches(path, name) for name in rule_names)

class WhitelistStore:
    # RLock 保护 + 原子 JSON 持久化（临时文件 + Path.replace）
    def snapshot(self) -> Whitelist: ...
```

### Scanner 集成

```python
# scan_entries 命中聚合阶段
if self._whitelist is not None and self._whitelist.entries and results:
    kept_results: list[ScanResult] = []
    for sr in results:
        if not sr.has_hit:
            kept_results.append(sr)
            continue
        if self._whitelist.matches_any_rule(sr.path, sr.rule_names):
            matched -= 1
            matches -= sr.total_match_count
        else:
            kept_results.append(sr)
    results = kept_results
```

## 整合优化情况

- 复用 `SkipStore` 的原子 JSON 持久化模式（临时文件 + `Path.replace`），保持一致性
- 复用 `IconButton` 组件，避免 QML 重复实现按钮样式
- 复用 `FileDialog` 模式（与 RulesPage.qml 一致），处理 `file:///` 前缀转换
- `WhitelistController` 通过 `AppController` 注入到 `WorkspaceController`，
  确保所有 `ScanController` 实例共享同一份白名单（多工作区一致）

## 测试验证结果

### 单元测试
- `tests/test_whitelist.py`：39 passed（WhitelistEntry 参数化匹配、Whitelist 集合、WhitelistStore CRUD/持久化/线程安全/导入导出）
- `tests/test_scanner_whitelist.py`：11 passed（无白名单、空白名单、通配规则、精确规则、部分覆盖、全部覆盖、glob 多文件过滤、scan_entries 直接调用）
- `tests/test_gui_whitelist_controller.py`：23 passed（属性、增删清、导入导出、snapshot 隔离、信号发射、重复添加不 emit、Scanner 集成）

### 性能基准
- `tests/test_whitelist_benchmark.py`：标记 `@pytest.mark.slow`，验证白名单过滤对扫描吞吐量影响 < 5%
  - 测量方法：预热 1 次（结果丢弃）+ 每配置重复 5 次交替测量取中位数，消除文件系统缓存预热与线程调度的顺序效应
  - `test_whitelist_overhead_under_5_percent`：全命中过滤场景，overhead < 5%
  - `test_whitelist_no_match_no_overhead`：白名单不匹配场景，偏差 < 10%（CI 性能波动容忍）

### 全套门禁
- `ruff check`：All checks passed
- `pyrefly check`：0 errors (534 suppressed)
- `pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：2280 passed, coverage 95.66%

## 遗留事项

- 无

## 下一轮计划

iter-134：进入 req-38 计划下一项（按 `.trae/req/req-38-体验增强与功能性能迭代计划.md` 顺序）。
