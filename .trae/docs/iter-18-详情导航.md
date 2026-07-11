# iter-18：详情对话框命中位置导航

## 本轮目标

实现需求02-7/8：详情对话框打开时滚动到首个命中并高亮；多命中时提供前后跳转功能。

## 改动文件清单

### 源代码（1 文件）

- `src/uniscan/gui/detail_dialog.py`：
  - 导入 `QColor`、`QTextCharFormat`、`QTextCursor`（来自 `PySide2.QtGui`）
  - `__init__` 新增 `_hit_positions` 和 `_current_hit_index` 状态
  - `_init_ui` 新增命中位置导航栏（上一个/下一个按钮 + "N / M" 计数标签）
  - `_populate_preview` 在 `setHtml()` 后调用 `_find_hit_positions()` 查找关键词位置，定位到首个命中
  - 新增 `_find_hit_positions(keywords)`：用 `QTextDocument.find()` 遍历文档查找所有关键词出现位置，去重后按位置排序
  - 新增 `_highlight_current_hit()`：用 `QTextEdit.ExtraSelection` 给当前命中加橙色背景（区别于 HTML 中其他命中的黄色背景）
  - 新增 `_scroll_to_current_hit()`：用 `QTextCursor.setPosition()` + `ensureCursorVisible()` 滚动到当前命中
  - 新增 `_on_prev_hit()` / `_on_next_hit()`：循环导航（到达末尾后回到首个）
  - 新增 `_update_nav_label()`：更新计数标签和按钮启用状态

### 测试（1 文件）

- `tests/test_gui.py`：
  - 新增 `TestHitDetailDialogNavigation` 测试类（10 项测试）：
    - 导航按钮存在性、命中位置查找、首个命中自动定位
    - 下一个/上一个前进与循环回绕
    - 无命中时按钮禁用、空文件不崩溃、文件不存在不崩溃
    - 多关键词命中均被找到

### 需求文档（1 文件）

- `.trae/req/需求02.md`：需求02-7/8 标记为 `[x]`

## 关键决策与依据

1. **ExtraSelections 区分当前命中**：HTML 中所有关键词已有黄色背景高亮（`_build_preview_html`），当前命中用 `QTextEdit.setExtraSelections()` 叠加橙色背景，视觉上区分"当前位置"与"其他命中"。
2. **QTextDocument.find() 查找位置**：`setHtml()` 后文档包含渲染的纯文本，`doc.find(keyword)` 按 QTextCursor 定位每个出现位置，去重后按起始位置排序，形成可导航的位置列表。
3. **循环导航**：`_on_prev_hit` / `_on_next_hit` 用模运算实现循环（到达末尾后回到首个，首个的上一个跳到末尾），符合用户对"前后跳转"的直觉。
4. **空文件/不存在文件的防御**：`_populate_preview` 在无法读取内容或内容为空时也调用 `_update_nav_label()`，确保导航按钮状态正确（禁用 + "无命中"）。

## 验证结果

- ruff check：detail_dialog.py 5 errors（4 既有 UP006/UP045 + 0 新增）
- pytest：509 passed, 1 skipped, 1 deselected（既有 `test_window_geometry_restored`）
- coverage：89.70%（较基线 88.26% 提升 1.44%）

## 遗留事项

- `test_window_geometry_restored`：既有 PySide2 offscreen 环境问题。
- coverage 89.70% 低于 95% 门槛：既有技术债。
- UP006/UP045 全量迁移：需单独迭代。
