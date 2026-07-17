# iter-51 detail_dialog.py 性能优化收尾

## 需求清单

- [x] 代码清理重构，性能优化（延续 iter-50，将相同优化应用到 `detail_dialog.py`）

## 迭代目标

将 iter-50 中 DetailPanel 的 `_plain_text` 缓存优化模式应用到
`HitDetailDialog`，消除对话框命中导航时的 `toPlainText()` 重复调用。
HitDetailDialog 与 DetailPanel 有完全相同的导航热路径问题。

## 改动文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/fuscan/gui/detail_dialog.py` | 修改 | 缓存 `_plain_text`，消除导航时重复 `toPlainText()` |
| `tests/test_gui.py` | 修改 | 同步 `test_dialog_highlight_skips_out_of_range` 设置 `_plain_text` 缓存 |

## 关键决策与依据

### `_plain_text` 缓存模式复用（性能优化）

- **问题**：`HitDetailDialog._find_hit_positions` /
  `_highlight_current_hit` / `_scroll_to_current_hit` 各自调用
  `self.preview.toPlainText()` 获取文档纯文本或长度，与 iter-50 中
  DetailPanel 的问题完全相同。对 100KB 文档：
  - 对话框显示时：3 次 `toPlainText()` → 3 次 100KB 字符串分配
  - 上/下一条按钮导航时：2 次 `toPlainText()` → 2 次 100KB 字符串分配
- **方案**：复用 iter-50 的模式。在 `_find_hit_positions` 中一次性取
  `toPlainText()` 缓存到 `self._plain_text`，后续 `_highlight_current_hit` /
  `_scroll_to_current_hit` 复用 `len(self._plain_text)` 做长度校验。
- **依据**：`_plain_text` 在对话框展示期间不变（文档内容仅在
  `_populate_preview` 中通过 `setHtml`/`setPlainText` 设置），缓存生命周期
  与 `_hit_positions` 一致。`_populate_preview` 的所有提前返回路径
  （读取失败、空内容）不会触发导航，无需缓存。
- **差异**：HitDetailDialog 无 `clear()` 方法（关闭即销毁，
  `WA_DeleteOnClose`），不需要在 `clear` 中重置缓存。

## 代码实现情况

### detail_dialog.py

- `__init__`：新增 `self._plain_text: str = ""` 字段（L78-80），注释说明
  缓存用途
- `_find_hit_positions()`：`plain = self.preview.toPlainText()` 改为
  `self._plain_text = self.preview.toPlainText()`，后续 `finditer` 用
  `self._plain_text`
- `_highlight_current_hit()`：`len(self.preview.toPlainText())` 改为
  `len(self._plain_text)`
- `_scroll_to_current_hit()`：同上
- docstring 同步补充缓存说明

### test_gui.py

- `test_dialog_highlight_skips_out_of_range`：在 `setPlainText("short")`
  后同步设置 `dialog._plain_text = "short"`，确保缓存与文档内容一致，
  与 iter-50 中 `test_highlight_skips_out_of_range_position` 的修改对齐。

## 测试验证结果

| 门禁 | 结果 | 基线（iter-50） | 变化 |
|------|------|----------------|------|
| ruff check | 0 errors | 0 errors | — |
| ruff format --check | 通过 | 通过 | — |
| pyrefly check | 0 errors (452 suppressed) | 0 errors (452 suppressed) | — |
| pytest | 1324 passed / 0 failed | 1324 passed / 0 failed | — |
| coverage | 96.10% | 96.04% | +0.06% |

覆盖率提升 0.06% 来自 `detail_dialog.py` 的 `_plain_text` 相关行被
`test_dialog_highlight_skips_out_of_range` 覆盖。

## 整合优化情况

- 与 iter-50 的优化模式保持完全一致，便于后续维护时单点更新算法。
- 无新增重复代码或抽象。

## 遗留事项

- 无

## 下一轮计划

- 无具体计划，视用户需求而定
