# iter-17：结果筛选、排序与分组显示

## 本轮目标

实现需求02-4/5/6/12：扫描结果支持按规则筛选、按路径筛选、表头排序、以及按规则/严重等级分组显示。

## 改动文件清单

### 源代码（1 文件）

- `src/uniscan/gui/main_window.py`：
  - 导入 `QLineEdit` 和 `ScanResult`/`RuleHit`
  - `_build_main_splitter()`：结果树右侧增加筛选栏容器；结果树新增"命中数"列（共 5 列）；启用 `setSortingEnabled(True)` 支持表头点击排序
  - 新增 `_build_result_filter_bar()`：路径筛选输入框 + 规则筛选下拉 + 分组模式下拉
  - 重构 `_populate_results(report)`：存储报告 → 更新规则下拉 → 调用 `_refresh_result_tree()`
  - 新增 `_update_rule_filter_options(report)`：从扫描结果提取规则名填充下拉，保留上次选择
  - 新增 `_refresh_result_tree()`：读取筛选条件与分组模式，调用对应的填充方法
  - 新增 `_filter_results(report, path_filter, rule_filter)`：路径大小写不敏感子串匹配 + 规则精确匹配（规则筛选时 hits 被过滤为仅匹配规则的命中）
  - 新增 `_populate_flat(results)`：不分组模式（文件→命中子项）
  - 新增 `_populate_grouped_by_rule(results)`：按规则名分组（规则→文件子项）
  - 新增 `_populate_grouped_by_severity(results)`：按严重等级分组（等级→文件子项，critical 在前）
  - 重构 `_on_result_double_clicked`：分组模式下子项携带 ScanResult，优先取当前项再取父项

### 测试（1 文件）

- `tests/test_gui.py`：
  - 新增 `_build_multi_hit_report(tmp_path)` 辅助函数：构造多规则、多文件命中的测试报告
  - 新增 `TestResultFilterAndGroup` 测试类（16 项测试）：
    - 筛选栏控件存在性、表头排序启用、列数验证
    - 规则下拉填充、路径筛选（含大小写不敏感）、规则筛选、组合筛选
    - 无匹配结果、清空筛选恢复结果
    - 按规则分组、按严重等级分组、分组子项 UserRole 数据
    - 分组模式双击打开详情对话框、无报告刷新不异常
    - 重新填充后规则筛选恢复

### 需求文档（1 文件）

- `.trae/req/需求02.md`：需求02-4/5/6/12 标记为 `[x]`

## 关键决策与依据

1. **筛选栏置于结果树上方**：QFrame + HBox 布局，路径输入框（stretch=2）+ 规则下拉（stretch=1）+ 分组下拉（stretch=1），紧凑且不占垂直空间。
2. **"命中数"列（index 3）**：原来"详情"列显示"N 条命中"为文本，无法数值排序。新增独立数值列，`setTextAlignment(Qt.AlignCenter)` 居中显示，`setSortingEnabled(True)` 使表头点击可排序。
3. **规则筛选时 hits 被过滤**：选中"密钥内容"规则后，仅显示该规则的命中，其他规则的命中从 ScanResult.hits 中移除。通过构造新的 `ScanResult(path=sr.path, size=sr.size, hits=matching_hits)` 实现（frozen dataclass 不可变）。
4. **分组模式 UserRole 数据位置**：不分组模式下 ScanResult 存在顶层文件项；分组模式下存在子项（文件项）。`_on_result_double_clicked` 优先取当前项，取不到再取父项，兼容两种模式。
5. **UP006 零增量**：新代码使用 `list[ScanResult]` 而非 `List[ScanResult]`（`from __future__ import annotations` 已启用，注解不求值），不增加技术债。

## 验证结果

- ruff check：main_window.py 8 errors（全部为既有 UP045/UP006/ARG002，本轮零增量）
- pytest：499 passed, 1 skipped, 1 deselected（既有 `test_window_geometry_restored`）
- coverage：89.73%（较 P1 的 89.36% 提升 0.37%，较基线 88.26% 提升 1.47%）

## 遗留事项

- `test_window_geometry_restored`：既有 PySide2 offscreen 环境问题。
- coverage 89.73% 低于 95% 门槛：既有技术债。
- UP006/UP045 全量迁移：213 个错误，需单独迭代。
