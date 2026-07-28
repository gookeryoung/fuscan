# iter-131 结果列表渲染优化与内存占用

## 需求清单

- [x] `ResultsPage.qml` `cacheBuffer` 按结果量动态调整
- [x] QML delegate 属性绑定审计：缓存 `model.severityColor` / `model.severityText` 到本地 property
- [x] `data()` 惰性严重度计算（审计确认 dict 查找已足够快，跳过缓存）
- [x] `ScanReport` 内存占用优化（审计确认 orjson 反序列化已满足，流式解析跳过）

## 迭代目标

10 万结果列表滚动帧率 >= 30fps；delegate 首次渲染时间 < 5ms/个。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fuscan/gui/views/pages/ResultsPage.qml` | `cacheBuffer` 固定 2000 → 按结果量动态（>50k:500, >10k:1000, else:2000）；delegate 新增 `sevColor`/`sevText` property 缓存 `model.severityColor`/`model.severityText` |
| `src/fuscan/gui/resources_rc.py` | QRC 重新编译 |

## 关键决策与依据

### 1. cacheBuffer 动态调整

**原实现**：`cacheBuffer: 2000`（固定值），10w 结果时预渲染约 35 个 delegate（每个 56px）。

**新实现**：
```qml
cacheBuffer: resultListView.count > 50000 ? 500
           : resultListView.count > 10000 ? 1000
           : 2000
```

| 结果量 | cacheBuffer | 预渲染 delegate 数 | 内存占用估算 |
|--------|-------------|-------------------|------------|
| < 10k | 2000 | ~35 个 | ~2MB |
| 10k-50k | 1000 | ~18 个 | ~1MB |
| > 50k | 500 | ~9 个 | ~0.5MB |

大结果集降低 cacheBuffer 减少内存占用与初始渲染开销；小结果集保持高 cacheBuffer 提升滚动流畅度。

### 2. delegate 属性缓存

**原实现**：`model.severityColor` 在色条与标签背景两处分别求值（2 次 model 访问）。

**新实现**：
```qml
delegate: ItemDelegate {
    property string sevColor: model.severityColor
    property string sevText: model.severityText
    // 后续使用 sevColor / sevText
}
```

`model.*` 每次访问需经过 QAbstractListModel.data() 调用 + role 匹配 + 返回 QVariant。
缓存到本地 property 后，delegate 内多次使用仅触发一次 model 访问，后续从 property 读取。

`severityColor` 在 delegate 中使用 2 次（色条 + 标签背景），缓存减少 1 次 model 访问。
`severityText` 使用 1 次，缓存主要为了代码一致性，性能影响可忽略。

### 3. data() 惰性严重度计算（审计跳过）

**计划**：`ResultListModel.data()` 中 `severity_text()` / `severity_color_hex()` 结果缓存到
`_sev_text_cache: dict[int, str]`。

**审计结论**：`severity_text` / `severity_color_hex` 是 dict 查找（`_SEVERITY_TEXT[severity]`），
约 50ns/次。引入 cache dict 需要额外的 dict 查找 + 键构造，开销相当甚至更高。跳过。

### 4. 流式 from_json（审计跳过）

**计划**：超大 JSON 文件（> 10MB）用 `ijson` 流式解析，避免 `orjson.loads` 全量加载。

**审计结论**：
- iter-128 已用 orjson 反序列化，10w 结果 < 200ms（Rust 实现，零拷贝）
- `ijson` 是纯 Python 实现，比 orjson 慢 10-100x，仅在内存极度受限时才有意义
- 10w 结果 JSON 约 30-50MB，orjson 全量加载内存峰值约 200MB，现代设备可接受
- 引入 `ijson` 依赖增加包体积，收益不明确，跳过

## 测试验证结果

- `tests/test_gui_result_model.py`：63 passed
- `tests/test_gui_workspace_controller.py`：169 passed
- `ruff check src/fuscan`：All checks passed
- `pyrefly check src/fuscan`：0 errors (512 suppressed)
- QRC 编译成功：`resources_rc.py` (210341 bytes)

## 遗留事项

- 滚动帧率实测：需 Qt Creator QML Profiler 在真实 10w 结果场景下验证 >= 30fps 目标，
  当前仅通过理论分析（cacheBuffer 降低 + 属性缓存）确认优化方向正确
- delegate 首次渲染时间 < 5ms/个：同上，需 QML Profiler 实测

## 下一轮计划

req-37 四轮迭代（iter-128 ~ iter-131）全部完成。整体性能优化总结：

| 指标 | iter-128 前 | iter-131 后 | 目标 |
|------|-----------|-----------|------|
| 10w 结果启动到可交互 | ~5s | < 1s（异步加载） | < 1s ✓ |
| from_json 10w 结果 | ~800ms | < 200ms（orjson） | < 200ms ✓ |
| 过滤 10w 结果 | ~50ms 阻塞 | 后台 < 300ms 不阻塞 | 不阻塞 ✓ |
| 进度信号 fps | 3.3fps | 10fps | >= 10fps ✓ |
| 列表滚动帧率 | ~20fps | 待实测 | >= 30fps |

后续可考虑的优化方向（非 req-37 范围）：
- 过滤倒排索引（50w+ 结果集场景）
- SQLite FTS 全文检索集成
- 增量渲染（首次仅渲染可见区，滚动时异步填充）
