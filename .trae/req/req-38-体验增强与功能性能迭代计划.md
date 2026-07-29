# req-38 使用体验增强与功能性能优化迭代计划

## 概述

基于 iter-128~132 完成状态（界面载入与 I/O 性能优化全部落地，工作区排序与退出残留
已修复），针对用户实际反馈与遗留性能方向，制定 iter-133 ~ iter-142 共 10 轮迭代计划。

聚焦两个维度：

- **使用体验增强**（用户感知直接）：误报抑制、扫描目标管理、结果交互、国际化、打包分发
- **功能性能优化**（运行效率提升）：凭证扫描、并行调度、倒排索引、启动 I/O 深度优化、
  GUI 稳定性加固

每轮迭代遵循 rule-01 六步闭环（收集 → 计划 → 实现 → 测试 → 文档 → 验证），全套门禁
（ruff/pyrefly/pytest/coverage 95%）通过为验收硬性条件。

## 现状分析

### 已完成性能优化（iter-128~131）

| 指标 | iter-128 前 | iter-131 后 |
|------|-----------|-----------|
| 10w 结果启动到可交互 | ~5s | < 1s |
| from_json 10w 结果 | ~800ms | < 200ms |
| 过滤 10w 结果 | ~50ms 阻塞 | 后台 < 300ms 不阻塞 |
| 进度信号 fps | 3.3fps | 10fps |

### 遗留性能方向（iter-131 总结）

- 过滤倒排索引（50w+ 结果集场景）
- SQLite FTS 全文检索集成
- 增量渲染（首次仅渲染可见区，滚动时异步填充）
- 启动 I/O 进一步优化：共享 SkipStore 实例、延迟 HistoryStore/ScanController 初始化

### 用户反馈重心（近 2 日 topics）

1. 误报处理需求（手动标记 + 后续跳过）
2. 多工作区扫描调度（并行/取消/进度聚合）
3. 扫描目标管理（自定义忽略目录分类、预设、历史）
4. 凭证扫描覆盖面（高熵字符串、JWT/token 等）
5. 国际化需求（中英文切换）
6. 结果交互效率（批量操作、分组展示、历史对比）
7. GUI 稳定性（QML null 防御系统化、qrc 重建自动化）

---

## iter-133 误报抑制与规则白名单

### 需求

- [ ] 文件级/路径级白名单：标记为误报后，后续扫描不再报告
- [ ] 规则例外配置：特定文件/路径跳过特定规则（glob 路径模式匹配）
- [ ] 白名单持久化到 `~/.fuscan/whitelist.json`，支持导入/导出（JSON 格式）
- [ ] GUI 结果详情页新增「标记为误报」按钮 + Settings 页白名单管理 Tab

### 验收标准

1. 标记为误报的命中在后续扫描中不再报告（全量/增量扫描均生效）
2. 规则例外支持 glob 路径模式（如 `**/test/**`、`*.example`）
3. 白名单可导入/导出，JSON 格式可读
4. 白名单管理页支持添加/编辑/删除条目，按文件路径/规则名/严重度分类展示
5. 覆盖率不低于 95%，benchmark 佐证白名单匹配对扫描吞吐量影响 < 5%

### 技术方案

**白名单模型**（`src/fuscan/rules/whitelist.py` 新文件）：
```python
@dataclass(frozen=True)
class WhitelistEntry:
    """单条白名单条目。"""
    path_pattern: str          # glob 模式，如 "**/test/fixtures/**"
    rule_names: frozenset[str] # 空集表示跳过所有规则
    added_at: float            # 标记时间戳
    note: str = ""             # 用户备注（如 "测试 fixture，非真实泄漏"）

@dataclass(frozen=True)
class Whitelist:
    """白名单集合，支持序列化与匹配查询。"""
    entries: tuple[WhitelistEntry, ...]

    @classmethod
    def from_json(cls, data: str | bytes) -> Whitelist: ...
    def to_json(self) -> str: ...
    def matches(self, file_path: str, rule_name: str) -> bool: ...
```

**扫描集成**（`src/fuscan/scanner/scanner.py`）：
- `Scanner` 构造时接收 `whitelist: Whitelist | None`
- 命中过滤阶段：`ScanResult.matches` 中过滤掉 `whitelist.matches(path, rule)` 的条目
- 不影响文件遍历与读取，仅在结果聚合阶段过滤，性能开销最小

**GUI 集成**：
- `ResultDetailPanel.qml` 在「移入暂存区」按钮旁新增「标记为误报」按钮
- `WhitelistController`（新文件）管理白名单 CRUD + 持久化
- `SettingsPage.qml` 新增「白名单」Tab，复用既有 ListView + IconButton 模式

### 依赖

无（独立于其他迭代）

---

## iter-134 凭证扫描增强（高熵检测 + 正则引擎优化）

### 需求

- [x] Shannon 熵计算，识别 Base64/Hex 编码的高熵字符串（疑似密钥泄漏）
- [x] 正则规则引擎优化：预编译缓存 + 批量匹配（消除 per-file 重复编译开销）
- [x] 内置凭证模式扩展：API key/private key/JWT/token 等 10+ 类常见密钥格式
- [x] 熵检测阈值可配置（避免误报），在 Settings 页提供滑块调节

### 验收标准

1. 高熵字符串检测识别 Base64/Hex 密钥，误报率 < 10%（在 100 个样本上验证）
2. 正则匹配性能提升 >= 2x（预编译缓存收益，benchmark 佐证）
3. 内置凭证模式覆盖至少 10 类常见密钥格式（AWS/Azure/GCP/GitHub/Slack/JWT/RSA/SSH/PGP/Stripe）
4. 熵检测阈值可在 Settings 页调节（默认 4.5，范围 3.0~5.0），实时生效
5. 覆盖率不低于 95%，benchmark 佐证正则引擎性能提升

### 技术方案

**熵检测**（`src/fuscan/scanner/entropy.py` 新文件）：
```python
def shannon_entropy(data: str) -> float:
    """计算字符串的 Shannon 熵（0~8 bits/char）。"""
    if not data:
        return 0.0
    freq = collections.Counter(data)
    length = len(data)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())

def is_high_entropy(data: str, threshold: float = 4.5, min_length: int = 32) -> bool:
    """判断是否为高熵字符串（疑似 Base64/Hex 密钥）。"""
    return len(data) >= min_length and shannon_entropy(data) >= threshold
```

**正则引擎优化**（`src/fuscan/scanner/matchers.py`）：
- 现有 `CONTAINS`/`REGEX`/`EQUALS` 模式匹配保留
- 新增 `RegexCache` 类（`functools.lru_cache` 包装 `re.compile`）
- 规则集加载时预编译所有正则，扫描时直接使用编译后的 Pattern 对象
- 批量匹配接口：`match_batch(patterns: tuple[Pattern, ...], text: str) -> list[Match]`

**内置凭证模式**（`src/fuscan/assets/rules/builtin.yaml` 扩展）：
- 现有 5 类模板（aws_keys/azure_keys/gcp_keys/privacy_data/common_credentials）
- 新增 5 类：github_token/slack_token/jwt/rsa_private_key/ssh_private_key
- 每类含正则模式 + 严重度 + 描述

**熵检测集成**：
- `Scanner` 在 `CONTAINS`/`REGEX` 匹配后，额外执行熵检测
- 命中高熵字符串时构造 `ScanResult`，rule_name 为 `__high_entropy__`
- 阈值从 `Config` 读取，支持运行时调节

### 依赖

无（独立于其他迭代，但与 iter-133 白名单配合可降低熵检测误报）

---

## iter-135 多工作区并行扫描调度

### 需求

- [ ] 多工作区并行扫描调度（资源限制下的任务队列，避免 CPU 抢占）
- [ ] 扫描任务优先级与取消机制完善（用户可调整队列顺序）
- [ ] 后台扫描与 GUI 操作互不阻塞（线程隔离优化）
- [ ] 多工作区扫描进度聚合（顶部状态栏显示总进度）

### 验收标准

1. 多工作区并行扫描时 CPU 利用率合理（不超过 max_workers × 80%）
2. 扫描中 GUI 操作（切换页面/编辑规则/查看结果）无阻塞
3. 任务取消在 1s 内生效，未启动任务从队列移除
4. 顶部状态栏显示总进度（如 "扫描中 2/5 工作区，55% (1100/2000 文件)"）
5. 覆盖率不低于 95%

### 技术方案

**任务队列**（`src/fuscan/workers/scheduler.py` 新文件）：
```python
class ScanScheduler(QObject):
    """多工作区扫描调度器，管理任务队列与并发限制。"""
    max_concurrent: int = 1  # 默认串行，可配置并行度

    def enqueue(self, ws_id: str, priority: int = 0) -> None: ...
    def cancel(self, ws_id: str) -> None: ...
    def reorder(self, ws_ids: list[str]) -> None: ...
```

**并行度策略**：
- 默认 `max_concurrent=1`（串行，避免多磁盘 I/O 抢占）
- 用户可在 Settings 页调节（1~3，超过 3 警告 CPU 抢占）
- 每个工作区的 `Scanner` 共享全局 `ThreadPoolExecutor`，max_workers 由 Config 控制

**进度聚合**：
- `ScanScheduler` 维护 `dict[ws_id, ProgressInfo]`，定时（100ms）emit 聚合信号
- 顶部状态栏 `StatusBar.qml` 监听聚合信号，显示总进度

**取消机制**：
- 队列中未启动的任务：直接从队列移除
- 运行中任务：调用 `ScanController.quick_cancel()`（iter-132 已实现 cancel+wait+terminate）

### 依赖

iter-132（quick_cancel 实现）

---

## iter-136 扫描目标管理增强

### 需求

- [ ] 大型软件清单动态扩展：用户自定义忽略目录分类（不限于预设清单）
- [ ] 扫描目标预设：开发目录/文档目录/全盘扫描等 5+ 种快捷方案
- [ ] 扫描目标历史记录：快速选择最近扫描过的路径（持久化）
- [ ] 扫描范围可视化：树形展示将扫描的目录与跳过的目录

### 验收标准

1. 用户可自定义忽略目录分类（添加/编辑/删除），与预设分类统一管理
2. 扫描目标预设覆盖至少 5 种常见场景（开发/文档/下载/全盘/自定义）
3. 扫描目标历史记录持久化，重启后可用，支持快速选择
4. 扫描范围可视化树形展示扫描/跳过目录，支持展开/折叠
5. 覆盖率不低于 95%

### 技术方案

**忽略目录分类管理**（`src/fuscan/config.py`）：
- 现有 `IGNORE_DIR_PRESETS` 静态字典扩展为 `IgnoreDirConfig` dataclass
- 支持 `custom_categories: dict[str, list[str]]` 用户自定义分类
- `SettingsPage.qml` 忽略目录 Tab 新增「添加分类」按钮

**扫描目标预设**（`src/fuscan/gui/scan_targets.py` 新文件）：
```python
@dataclass(frozen=True)
class ScanTargetPreset:
    """扫描目标预设。"""
    name: str                  # "开发目录"
    paths: tuple[str, ...]     # 预设路径列表
    description: str = ""

BUILTIN_PRESETS: tuple[ScanTargetPreset, ...] = (
    ScanTargetPreset("开发目录", ("~/projects", "~/code"), "扫描常见开发目录"),
    ScanTargetPreset("文档目录", ("~/Documents"), "扫描文档目录"),
    # ...
)
```

**历史记录**：
- `Config` 新增 `scan_path_history: list[str]`，最多保留 20 条
- `AddTaskPage.qml` 路径选择器下方显示历史路径列表（一键选择）

**范围可视化**：
- `Scanner` 在 `WalkResult` 中保留跳过目录树结构
- 新增 `ScanScopeTreeModel`（QAbstractItemModel）展示树形结构
- `AddTaskPage.qml` 新增「预览扫描范围」按钮，弹出树形对话框

### 依赖

无（独立于其他迭代）

---

## iter-137 过滤倒排索引与 FTS 全文检索

### 需求

- [ ] `ResultListModel` 倒排索引：为 `rule_names` / `max_severity` 构建倒排索引，避免全量遍历
- [ ] SQLite FTS5 全文检索集成：命中内容全文索引，支持复杂查询
- [ ] 排序结果缓存：相同排序条件不重复计算
- [ ] 50w+ 结果集场景性能基准（benchmark 量化）

### 验收标准

1. 50w 结果集过滤响应 < 500ms（当前 10w 约 100ms，目标 50w < 500ms）
2. 全文检索支持短语查询、布尔查询（如 `"api_key" AND path:config`）
3. 排序缓存命中率 >= 90%（相同排序条件不重复计算）
4. benchmark 佐证倒排索引 vs 全量遍历性能比（50w 场景）
5. 覆盖率不低于 95%

### 技术方案

**倒排索引**（`src/fuscan/gui/models/result_model.py`）：
```python
def _build_indices(self) -> None:
    """set_results 后预构建倒排索引。"""
    self._severity_index: dict[Severity, list[int]] = collections.defaultdict(list)
    self._rule_index: dict[str, list[int]] = collections.defaultdict(list)
    for idx, result in enumerate(self._results):
        self._severity_index[result.max_severity].append(idx)
        for rule_name in result.rule_names:
            self._rule_index[rule_name].append(idx)

def _filter_via_index(self, ...) -> tuple[int, ...]:
    """通过索引交集快速过滤，避免全量遍历。"""
    if self._filter_severities:
        severity_matches = set()
        for sev in self._filter_severities:
            severity_matches.update(self._severity_index.get(sev, []))
        candidate = severity_matches
    # ... 取交集
```

**FTS5 集成**（`src/fuscan/cache/fts.py` 新文件）：
- `CacheStore` 新增 `fts_results` 虚拟表（FTS5）
- `set_results` 时同步写入 FTS 索引（文件路径 + 命中内容 + 规则名）
- `ResultListModel.set_filter_text` 走 FTS 查询（短语/布尔查询支持）

**排序缓存**：
```python
self._sort_cache: dict[tuple[str, bool, int], tuple[ScanResult, ...]] = {}
# key: (sort_field, ascending, filter_generation)
# 过滤条件变更时（generation 变化）清除缓存
```

### 依赖

iter-129（FilterWorker 后台过滤基础，已完成）

---

## iter-138 国际化基础架构（i18n）

### 需求

- [ ] 提取 UI 字符串到翻译文件（.ts/.qm，PySide2 lupdate/lrelease）
- [ ] 中文/英文切换支持（Settings 页语言选择，无需重启）
- [ ] 翻译文件管理工具（提取/合并/校验脚本）
- [ ] CLI 输出与日志的国际化

### 验收标准

1. 所有 UI 字符串通过 `qsTr()` 包裹，可被 lupdate 提取
2. 中英文切换无需重启应用，立即生效
3. 翻译覆盖率 >= 95%（不含动态生成的日志）
4. CLI 输出根据系统 locale 自动选择语言，支持 `--lang` 参数覆盖
5. 覆盖率不低于 95%

### 技术方案

**QML 国际化**：
- 所有 QML 文件中字符串字面量用 `qsTr("...")` 包裹
- `scripts/update_translations.py` 新脚本：调用 `pyside2-lupdate` 提取字符串到 `.ts`
- `scripts/compile_translations.py` 新脚本：调用 `pyside2-lrelease` 编译 `.ts` 到 `.qm`
- `.qm` 文件打包到 `resources.qrc`

**翻译文件**：
- `src/fuscan/assets/translations/fuscan_zh_CN.ts`（简体中文）
- `src/fuscan/assets/translations/fuscan_en_US.ts`（英语，源语言）

**语言切换**：
- `Config` 新增 `language: str = "zh_CN"` 字段
- `AppController` 监听 `languageChanged`，调用 `QTranslator.load()` 切换
- Settings 页新增「语言」ComboBox（中文/English）

**CLI 国际化**：
- `src/fuscan/cli.py` 使用 `gettext` 包裹字符串
- `src/fuscan/locales/zh_CN/LC_MESSAGES/fuscan.po` 翻译文件
- 启动时根据 `LC_ALL`/`LANG`/`--lang` 参数加载对应翻译

### 依赖

无（独立于其他迭代）

---

## iter-139 启动 I/O 深度优化与内存占用

### 需求

- [ ] 共享 `SkipStore` 实例（避免多工作区重复加载 skips.json）
- [ ] `HistoryStore` / `ScanController` 延迟初始化（首次使用时才创建）
- [ ] 大结果集（> 50w）内存占用优化：命中结果按需构造 `ScanResult`
- [ ] 启动耗时基准量化（benchmark 覆盖冷启动/热启动/多工作区场景）

### 验收标准

1. 启动耗时降低 >= 30%（10 工作区场景，iter-128 后基线）
2. 50w 结果集内存占用降低 >= 20%（按需构造 + 索引共享）
3. 共享 SkipStore 后，多工作区启动 I/O 次数减少 >= 50%
4. benchmark 量化冷启动/热启动/多工作区启动耗时
5. 覆盖率不低于 95%

### 技术方案

**共享 SkipStore**（`src/fuscan/gui/controllers/workspace_controller.py`）：
- 现有每个 `ScanController` 持有独立 `SkipStore`，加载 `skips.json` N 次
- 改为 `WorkspaceController` 持有全局 `SkipStore`，所有 `ScanController` 共享
- 写入时通过全局 `SkipStore` 序列化，避免并发写入冲突

**延迟初始化**：
- `HistoryStore`：首次 `getHistory()` 或 `addHistory()` 时才加载 `history.json`
- `ScanController`：首次 `startScan()` 时才创建 `Scanner` 实例（避免启动时预创建）

**按需构造 ScanResult**：
- `ScanReport` 持有原始 dict 数据，`ScanResult` 在 `data()` 访问时才构造
- 50w 结果集场景：仅可见区 ~35 个 ScanResult 构造为对象，其余保留 dict 形式
- 内存估算：dict 形式约 200B/条，ScanResult 对象约 1KB/条，节省 80%

**benchmark 量化**：
- `benchmarks/bench_startup.py` 新文件：测量冷启动/热启动/多工作区启动耗时
- `benchmarks/bench_memory.py` 新文件：测量 10w/50w/100w 结果集内存占用

### 依赖

iter-128（异步加载基础，已完成）、iter-131（渲染优化基础，已完成）

---

## iter-140 GUI 稳定性加固与 qrc 自动化

### 需求

- [ ] qrc 重建自动化（pre-commit hook 或构建脚本集成，避免修改 QML 后忘记重建）
- [ ] QML 绑定 null 安全审计：所有 context property 访问点系统化检查
- [ ] 模态遮罩统一：所有耗时操作（扫描/取消/退出/导出/恢复）防重复操作保护
- [ ] Connections deprecated 警告全量修复（隐式 onXxx 改为显式 function 形式）

### 验收标准

1. 修改 QML/SVG 后 pre-commit 自动触发 qrc 重建，无需手动执行
2. 所有 QML 文件中 context property 访问无 null TypeError（系统化审计）
3. 所有耗时操作均有模态遮罩防重复操作
4. QML 警告输出为 0（Connections deprecated、Property depends on non-NOTIFYable 等）
5. 覆盖率不低于 95%

### 技术方案

**qrc 重建自动化**：
- `.pre-commit-config.yaml` 新增 `local` hook：检测 `src/fuscan/gui/views/**/*.qml` 或
  `src/fuscan/gui/assets/**` 变更时，自动运行 `uv run python scripts/build_qrc.py`
- 仅在变更涉及 QML/SVG 时触发，避免无谓重建

**null 安全审计**：
- 检查所有 QML 文件中的 `xxxController.yyy` / `xxxModel.zzz` 访问
- 每个文件顶部声明类型化 property（`property XxxControllerType xxxController: XxxController`）
- 已有 13 处 ContextController 声明，全量审计剩余文件

**模态遮罩统一**：
- 提取 `ModalOverlay.qml` 组件（iter-132 退出遮罩模式复用）
- 应用到：扫描启动/取消/退出/导出/恢复/标记误报等耗时操作
- 防重复点击导致状态错乱

**Connections 修复**：
- 全量扫描 QML 文件中 `onXxx:` 隐式处理函数
- 改为 `function onXxx() { ... }` 显式形式（PySide2 5.15 推荐）

### 依赖

无（独立于其他迭代，但建议与其他迭代并行）

---

## iter-141 结果交互与统计可视化增强

### 需求

- [ ] 结果批量操作：批量导出选中、批量标记误报、批量移入暂存区
- [ ] 统计图表渲染优化：缓存计算结果，避免重复绘制
- [ ] 命中结果分组展示：按文件/规则/严重度分组
- [ ] 历史扫描结果对比：与上次扫描结果 diff 展示（新增/消失/变更）

### 验收标准

1. 结果列表支持多选（Ctrl+Click 批量选中），批量操作响应 < 200ms
2. 统计图表切换无白屏闪烁，缓存命中率 >= 80%
3. 分组展示支持按文件/规则/严重度三种维度切换
4. 历史对比展示新增/消失/变更的命中，支持过滤
5. 覆盖率不低于 95%

### 技术方案

**批量操作**（`src/fuscan/gui/models/result_model.py`）：
- `ResultListModel` 新增 `selected_indices: set[int]` 与 `SelectionModel` 角色
- QML delegate 新增 CheckBox，支持 Ctrl+Click 多选
- `ScanController` 新增 `batchMoveToStaging` / `batchMarkAsFalsePositive` Slot

**统计图表缓存**（`src/fuscan/gui/views/pages/StatsPage.qml`）：
- 缓存计算结果到 `StatsCache`（`dict[str, ChartData]`）
- 数据未变更时复用缓存，避免重复计算
- 切换图表类型时仅重新渲染，不重新计算

**分组展示**（`src/fuscan/gui/models/result_model.py`）：
- 新增 `group_by: str` 属性（"file" / "rule" / "severity" / "none"）
- `data()` 返回分组头角色，QML delegate 渲染分组标题
- 分组折叠/展开状态持久化

**历史对比**（`src/fuscan/history/comparator.py` 扩展）：
- 现有 `compare_reports()` 返回 `ReportDiff`
- `ResultListModel.set_diff(diff)` 在结果上标注新增/消失/变更
- QML delegate 显示 diff 标记（+/-/* 图标）

### 依赖

iter-133（误报抑制，标记误报基础）、iter-135（多工作区调度，历史对比基础）

---

## iter-142 打包分发优化与自动更新

### 需求

- [ ] NSIS 安装包优化：组件选择（核心/文档/示例规则）、桌面快捷方式、卸载清理
- [ ] 便携版（免安装）打包脚本：解压即用，无需安装依赖
- [ ] 自动更新检查机制：启动时检查 GitHub Release 最新版本
- [ ] 安装包体积优化：剔除不必要的依赖（测试依赖、开发工具链）

### 验收标准

1. NSIS 安装包支持组件选择（核心必选，文档/示例规则可选）
2. 便携版解压即用，无需安装依赖，体积 < 60MB
3. 自动更新检查在 2s 内完成，不阻塞启动（后台异步检查）
4. 安装包体积 < 50MB（剔除测试依赖后）
5. 覆盖率不低于 95%

### 技术方案

**NSIS 优化**（`scripts/installer.nsi` 扩展）：
- 新增 `Components` 段：核心组件（必选）/ 文档组件 / 示例规则组件
- 桌面快捷方式可选（默认创建）
- 卸载时清理 `~/.fuscan/cache.db`（询问用户保留配置或全部清理）

**便携版打包**：
- `scripts/build_portable.py` 新脚本：基于 fspack 打包为 zip
- 便携版不写入注册表，配置目录可选（默认 `./portable_config/`）

**自动更新检查**（`src/fuscan/gui/updater.py` 新文件）：
```python
class UpdateChecker(QThread):
    """后台检查 GitHub Release 最新版本。"""
    def run(self):
        # 调用 GitHub API: https://api.github.com/repos/.../releases/latest
        # 比对版本号，若有新版本 emit updateAvailable(latest_version, download_url)
```

- 启动后 5s（避免与启动 I/O 抢占）触发检查
- 若有新版本，AboutPage 显示「新版本可用：v1.x.x」提示

**体积优化**：
- `pyproject.toml` 的 `[tool.fspack]` 排除 `.benchmarks`、`.github`、`tests/`、`docs/`
- 检查 `uv.lock` 中未使用的依赖并清理

### 依赖

无（独立于其他迭代，建议最后执行以打包最新功能）

---

## 优先级与依赖关系

```
iter-133 (误报抑制) ─┐
                      ├─→ iter-141 (结果交互)
iter-135 (并行调度) ─┘

iter-134 (凭证扫描) — 独立
iter-136 (扫描目标管理) — 独立
iter-137 (倒排索引+FTS) — 依赖 iter-129 (已完成)
iter-138 (国际化) — 独立
iter-139 (启动 I/O 深度优化) — 依赖 iter-128 (已完成)
iter-140 (GUI 稳定性) — 独立，可与其他并行
iter-142 (打包分发) — 最后执行
```

### 推荐执行顺序

按「使用体验」与「功能性能」两个维度交替推进，避免单一维度疲劳：

| 序号 | 迭代 | 维度 | 优先级 |
|------|------|------|--------|
| 1 | iter-133 误报抑制 | 体验增强 | 高（用户高频痛点） |
| 2 | iter-134 凭证扫描增强 | 功能性能 | 高（功能覆盖面） |
| 3 | iter-135 多工作区并行调度 | 功能性能 | 高（吞吐量瓶颈） |
| 4 | iter-136 扫描目标管理 | 体验增强 | 中（操作便利性） |
| 5 | iter-137 倒排索引+FTS | 功能性能 | 中（大结果集场景） |
| 6 | iter-138 国际化 | 体验增强 | 中（覆盖面扩展） |
| 7 | iter-139 启动 I/O 深度优化 | 功能性能 | 中（启动体验） |
| 8 | iter-140 GUI 稳定性加固 | 体验增强 | 中（稳定性） |
| 9 | iter-141 结果交互增强 | 体验增强 | 中（结果使用效率） |
| 10 | iter-142 打包分发优化 | 体验增强 | 低（收尾） |

## 度量基线

每轮迭代须在 `benchmarks/` 下新增或更新 benchmark，量化优化效果：

| 指标 | 当前基线 | 目标 |
|------|---------|------|
| 误报标记后扫描吞吐量影响 | — | < 5% |
| 正则匹配性能 | ~基线 | >= 2x 提升 |
| 50w 结果过滤响应 | ~500ms (估) | < 500ms |
| 多工作区并行扫描 CPU 利用率 | — | <= max_workers × 80% |
| 启动耗时（10 工作区） | ~1.5s | < 1s |
| 50w 结果内存占用 | ~基线 | -20% |
| 全文检索响应 | — | < 100ms |
| 安装包体积 | ~60MB | < 50MB |

## 度量与门禁

- 每轮迭代全套门禁：`ruff check` + `ruff format --check` + `pyrefly check` + `pytest` + `coverage >= 95%`
- 性能相关迭代须新增 benchmark 并与基线对比，回归不超过 10%
- 文档同步更新（docstring / README / changelog）
- 每轮迭代记录到 `.trae/docs/iter-NN-<主题>.md`，含需求清单/迭代目标/改动文件/关键决策/测试结果/遗留事项/下一轮计划
