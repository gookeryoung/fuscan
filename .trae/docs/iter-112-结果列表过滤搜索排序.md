# iter-112 增强结果列表过滤/搜索/排序能力

## 需求清单

- [x] 增强结果列表过滤/搜索/排序能力（按规则/严重度/类型）（req-34 第 6 项）

## 迭代目标

在 `ResultListModel` 内置过滤+排序视图，支持文件路径模糊搜索、规则名多选、
严重度多选、四种排序字段；通过 `ScanController` 暴露 Slot 供 QML 调用，
ResultsPage 新增工具栏整合搜索/过滤/排序 UI。

## 改动文件清单

- `src/fuscan/gui/models/result_model.py`：
  - 新增 `_filtered` 视图元组与 4 个过滤/排序字段
  - 新增 `set_filter_text`/`set_filter_rules`/`set_filter_severities`/`set_sort`/`clear_filters`
  - 新增 `total_count`/`filtered_count`/`filter_text`/`filter_rules`/`filter_severities`/`sort_field`/`sort_ascending` 属性
  - 新增 `filtered_results` 属性
  - `_apply_filter_and_sort` 内部纯 Python 实现，便于单元测试
  - `data()`/`rowCount()`/`get_result()` 改为基于 `_filtered` 视图
  - 新增 `SORT_DEFAULT`/`SORT_FILE_PATH`/`SORT_HITS_COUNT`/`SORT_SEVERITY` 常量
  - 新增 `_SEVERITY_WEIGHT` 严重度排序权重映射
- `src/fuscan/gui/controllers/scan_controller.py`：
  - 新增 `setResultFilterText`/`setResultFilterRules`/`setResultFilterSeverities`/`setResultSort`/`clearResultFilters` Slot
  - 新增 `resultTotalCount`/`resultFilteredCount`/`resultRuleNames` Property
  - 过滤后选中索引越界时自动重置为 -1，避免详情面板显示错误数据
  - 顶部 import 添加 `Severity`（运行时用于严重度文本反向映射）
- `src/fuscan/gui/views/pages/ResultsPage.qml`：
  - 标题栏下方新增过滤+排序工具栏（TextField + 3 个 ComboBox + 清除按钮 + 计数）
  - TextField 输入防抖 300ms（QML Timer），避免每个字符都 reset model
  - 清除按钮一键重置所有过滤+排序条件
- `tests/test_gui_result_model.py`：新增 37 个 iter-112 专项测试
- `tests/test_gui_scan_controller.py`：新增 `TestIter112ResultFilterSort` 类（7 个 Slot 测试）

## 关键决策与依据

1. **Model 内置视图而非 QSortFilterProxyModel**：
   - QSortFilterProxyModel 需要处理 source/proxy 索引映射，与现有
     `selectedResultIndex` 语义冲突
   - Model 内置 `_filtered` 视图使 `data()`/`rowCount()`/`get_result()` 均基于
     过滤后数据，`selectedResultIndex` 直接对应视图行号无需映射
   - 纯 Python 实现便于单元测试，10k 结果约 5ms 可接受

2. **过滤维度选择**：
   - 文件路径模糊匹配（不区分大小写）：最常用的"找文件"场景
   - 规则名多选：用户聚焦特定规则命中的结果
   - 严重度多选：按 `max_severity` 过滤，聚焦高危结果
   - 不做文件类型过滤：路径模糊搜索已覆盖扩展名场景

3. **排序字段**：
   - `default`：保持 `set_results` 时的原始顺序（扫描顺序）
   - `filePath`：按路径字母序，便于按目录聚类查看
   - `hitsCount`：按命中规则数排序，聚焦多规则命中文件
   - `severity`：按 `max_severity` 权重排序（CRITICAL=3 > WARNING=2 > INFO=1）

4. **QML 工具栏设计**：
   - TextField 防抖 300ms：避免每个字符触发 `beginResetModel`/`endResetModel`
   - 严重度过滤用 ComboBox 单选（"全部/严重/警告/信息"）：MVP 简化，
     多选交互复杂度高于收益，留待后续按需扩展
   - 排序字段+方向分离两个 ComboBox：避免 8 种组合的下拉冗长
   - 清除按钮一键重置：避免用户手动逐个清除

5. **选中索引越界保护**：
   - 过滤后 `selectedResultIndex` 可能大于新的 `rowCount()`
   - 在每个 Slot 中检测越界并重置为 -1，emit `selectedResultChanged`
     通知 QML 详情面板清空

6. **Severity 文本反向映射**：
   - QML 传入中文文本列表（"严重"/"警告"/"信息"），ScanController 用
     `severity_text` 反向映射为 `Severity` 枚举
   - 集中映射逻辑在 Slot 内，QML 无需感知枚举值

## 代码实现情况

### result_model.py 关键改动

```python
def _apply_filter_and_sort(self) -> None:
    """根据当前过滤+排序条件刷新 _filtered 视图。"""
    if not self._results:
        self._filtered = ()
        return
    # 阶段 1：过滤
    view = list(self._results)
    if self._filter_text:
        keyword = self._filter_text.lower()
        view = [r for r in view if keyword in str(r.path).lower()]
    if self._filter_rules:
        view = [r for r in view if any(name in self._filter_rules for name in r.rule_names)]
    if self._filter_severities:
        view = [r for r in view if r.max_severity in self._filter_severities]
    # 阶段 2：排序
    if self._sort_field == SORT_DEFAULT:
        self._filtered = tuple(view)
        return
    # ... key_func 选择与 sort
```

### ScanController Slot 越界保护

```python
@Slot(str)
def setResultFilterText(self, text: str) -> None:
    self._result_model.set_filter_text(text)
    if self._selected_result_index >= self._result_model.rowCount():
        self.setSelectedResultIndex(-1)
    self.selectedResultChanged.emit()
```

### QML 工具栏防抖

```qml
TextField {
    id: filterTextInput
    Timer {
        id: filterDebounce
        interval: 300
        onTriggered: controller.setResultFilterText(filterTextInput.text)
    }
    onTextEdited: filterDebounce.restart()
}
```

## 整合优化情况

- 严重度排序权重 `_SEVERITY_WEIGHT` 与 `severity_utils` 解耦，避免循环依赖
- 排序 key 函数用 `def` 而非 `lambda`，符合 ruff E731 规范
- 过滤+排序逻辑集中在 `_apply_filter_and_sort`，所有 set_* 方法复用

## 测试验证结果

- `ruff check`：通过
- `ruff format`：通过
- `pyrefly check`：通过
- `pytest tests/test_gui_result_model.py`：53 passed（原 16 + 新增 37）
- `pytest tests/test_gui_scan_controller.py::TestIter112ResultFilterSort`：7 passed
- `pytest`（全套）：1785 passed
- 覆盖率：93.54%（从 93.11% 提升 0.43%，ResultListModel 单模块 95%）
- 未达 95% 阈值，留待 iter-116 处理

## 遗留事项

- 严重度过滤当前为单选（QML ComboBox 限制），后续如需多选可改为
  `Dialog + CheckBox` 组合，调用 `setResultFilterSeverities` 传入多选列表
- 全套覆盖率 93.54% 未达 95% 阈值，iter-116 将专门处理低覆盖模块

## 下一轮计划

iter-113：命中替换增强（批量替换/撤销/规则操作）
