# iter-101 双阶段进度条与收集统计

## 需求清单

- [x] 文件类型勾选实际生效：未勾选的扩展名一律不进入扫描队列（白名单制，已在 iter-87 实现）
- [x] 扫描过程分解为「收集文件清单」与「解析文件内容」两步，分别独立进度条展示
- [x] 完善收集阶段相关统计数据与展示信息

## 迭代目标

用户反馈未勾选 HTML 类别仍持续解析该文件，且希望扫描过程能可视化分解为收集与解析两阶段。
本次迭代在已有 stats/scan worker 拆分架构（iter-71）与白名单制（iter-87）基础上，
于 ScanController 暴露收集阶段独立进度字段，并在 ScanProgressCard 与 StatsPage 中
以双进度条 + 阶段标识圆点呈现，让用户清晰看到「哪些文件被白名单跳过」「收集到多少文件」。

## 改动文件清单

- `src/fuscan/gui/controllers/scan_controller.py`：新增 scanPhase/walkDiscovered/walkSkipped/
  walkUserSkipped/walkIndeterminate/walkDone/scanDone/walkProgress 等属性；`_on_scan_progress`
  按 `info.phase` 分流更新 walk 与 scan 字段；`startScan` 初始化阶段字段；
  `_on_stats_finished` 同步 walk 最终统计并切到 scan 阶段；`_on_scan_finished`/`_on_scan_cancelled`
  标记 scan 阶段完成
- `src/fuscan/gui/views/components/ScanProgressCard.qml`：单进度条改造为双进度条
  （walk + scan），各自带阶段标识圆点（进行中/已完成/未开始三态）与计数标签
- `src/fuscan/gui/views/pages/StatsPage.qml`：原「进度」GroupBox 拆为「收集文件清单」与
  「解析文件内容」两个 GroupBox，各自展示阶段状态、进度条与计数；收集阶段新增
  「已发现/白名单跳过/用户标记跳过」三列统计网格
- `tests/test_gui_scan_controller.py`：新增 `TestScanPhaseProgress` 测试类，覆盖
  scanPhase/walkProgress 字段初始化、startScan 进入 walk 阶段、walk 进度仅更新 walk 字段、
  walk→scan 阶段切换标记 walkDone、walkDiscovered=0 时 walkProgress=0、stats_finished
  同步 walk 总计、scan_finished/cancelled 标记 scanDone 等 8 个用例

## 关键决策与依据

1. **walkProgress 计算方式**：walk 阶段无确定 total（文件随遍历持续发现），采用
   `(discovered - skipped - user_skipped) / discovered` 作为「已分类文件占比」，
   discovered=0 时返回 0 避免除零。理由：用户希望看到收集进度推进感，而非简单的
   indeterminate 动画；当所有发现文件都被分类（进入扫描队列或被跳过）时进度为 100%
2. **walkIndeterminate 语义**：startScan 时设为 True，首次收到 walk 阶段进度后置 False。
   理由：避免 walk 阶段刚启动时 progress=0% 给用户「卡住」错觉
3. **阶段标识圆点三态**：进行中=主色（walk）/状态色（scan，黄/warning）、已完成=成功色（绿）、
   未开始=边框灰。理由：圆点比文字「●/○」更直观，颜色与状态语义一致
4. **保留旧 progressScanned/progressTotal 字段**：scan 阶段进度仍用原字段，避免破坏
   既有 QML 绑定（result page、状态栏摘要等）；新增 walkDiscovered/walkSkipped/...
   字段独立反映 walk 阶段
5. **未修改 ScanController 信号**：所有新增属性共用 `progressChanged` 信号 NOTIFY，
   避免新增信号导致 QML Connections 重写
6. **白名单机制无需改动**：iter-87 已实现 `_should_scan` 白名单制，HTML 在
   SourceCodeExtractor.supported_extensions 中，用户取消勾选「源代码」后 html/htm
   扩展名不会进入 scan_extensions，collect_entries 阶段直接 skipped，不进入扫描队列
7. **StatsPage GroupBox 仅在 scanPhase≠setup 时可见**：未开始扫描时不显示进度区，
   避免空态误导

## 代码实现情况

### ScanController 阶段字段（scan_controller.py）

```python
self._scan_phase: str = "setup"  # setup / walk / scan / archive / done
self._walk_discovered: int = 0
self._walk_skipped: int = 0
self._walk_user_skipped: int = 0
self._walk_indeterminate: bool = False
self._walk_done: bool = False
self._scan_done: bool = False
```

`_on_scan_progress` 按 `info.phase` 分流：
- walk 阶段：仅更新 walk_discovered/walk_skipped/walk_user_skipped
- scan/archive 阶段：更新 progress_scanned/total 与 matched/skipped/errors/passed
- phase 切换时同步 _scan_phase，walk→scan/archive 标记 _walk_done=True

### ScanProgressCard 双进度条

```
收集文件清单  ●已完成   200 个文件 | 跳过 50 | 用户跳过 10
[████████████████████████████████████████] 100%

解析文件内容  ●进行中   5 / 140
[█████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░] 3%
```

圆点颜色：进行中=primary/warning，已完成=success，未开始=border
进度条颜色：进行中=primary/warning，已完成=success

### StatsPage 双 GroupBox

- **收集文件清单**：阶段状态行 + 进度条 + 三列统计（已发现/白名单跳过/用户标记跳过）
- **解析文件内容**：阶段状态行 + 进度条 + 已扫描/总数

## 整合优化情况

- 阶段切换逻辑集中到 `_on_scan_progress`，避免分散到 `_on_stats_finished` 等回调
- 新增属性全部用 `progressChanged` 信号 NOTIFY，无新增信号
- 测试用例覆盖阶段切换全路径（setup→walk→scan→done，含 cancelled 分支）

## 测试验证结果

- `TestScanPhaseProgress` 8 个新用例全部通过
- `tests/test_gui_scan_controller.py` 全部 47 用例通过
- `tests/test_gui_qml_scan_progress.py` 2 个 QML 集成测试通过（无 null TypeError）
- `tests/test_scanner.py` + `tests/test_workers.py` 274 用例通过
- 全套门禁：ruff check/format/pyrefly 全部通过
- 全套测试：1591 passed, 95.52% coverage（≥95% 阈值）

## 遗留事项

- 用户原始反馈「未勾选 HTML 仍解析」经核查为白名单机制已正确工作（iter-87），
  推测为用户未在「源代码」类别下取消勾选（HTML/htm 归属 SourceCodeExtractor，
  display_name=「源代码（CODE）」）。本次迭代通过双进度条让白名单跳过数可视化，
  用户可直观看到 walkSkipped 计数
- manual.md 截图与操作描述未同步（沿用 req-32 遗留项）

## 下一轮计划

- 等待用户验证双进度条与收集统计在真实扫描中的展示效果
- 如有反馈调整 walkProgress 计算方式或 UI 布局
