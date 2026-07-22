# iter-60：规则清单内置规则条目与详情对话框卡滞修复

## 需求清单

1. 默认规则也列在清单当中，允许用户取消，取消后下次不再自动加载
2. 分析结果页面命中详情对话框打开多次以后卡滞的问题，并解决

## 迭代目标

- 需求1：在规则文件列表顶部展示内置通用规则条目（带复选框），用户可整体勾选/取消，取消后持久化到配置
- 需求2：为文件内容提取添加进程内 LRU 缓存，避免同一文件多次打开对话框时重复提取导致卡滞

## 改动文件清单

### 新建文件

| 文件 | 说明 |
|------|------|
| `src/fuscan/extractors/cache.py` | 内容提取 LRU 缓存模块，提供 `extract_content_cached` 与 `clear_content_cache` |
| `.trae/req/req-11-iter60需求.md` | 需求清单 |

### 修改文件

| 文件 | 说明 |
|------|------|
| `src/fuscan/extractors/__init__.py` | re-export `extract_content_cached` 与 `clear_content_cache`，更新 `__all__` 与模块文档 |
| `src/fuscan/gui/main_window.py` | `_refresh_rules_file_list` 在 row 0 插入内置规则条目（带复选框）；`_on_move_rule_up`/`_on_move_rule_down`/`_on_remove_rule` 适配 row 偏移（用户规则索引 = row - 1）；新增 `_on_rules_file_item_changed` 槽监听勾选状态变化并持久化；`_setup_context_menus` 连接 `itemChanged` 信号；`_on_rules_file_list_context_menu` 内置规则条目禁用所有操作 |
| `src/fuscan/gui/detail_dialog.py` | `extract_content_with_fallback` 替换为 `extract_content_cached` |
| `src/fuscan/gui/detail_panel.py` | 同上 |
| `tests/test_gui.py` | 适配规则文件列表 row 偏移（row 0=内置规则，row 1+=用户规则）；新增 6 个需求1测试（勾选状态、持久化、不可移动/移除）；autouse fixture 清空内容缓存 |
| `tests/test_extractors.py` | 新增 `TestContentCache` 测试类（7 例：缓存命中、失效、清空、LRU 淘汰、stat 失败回退） |

## 关键决策与依据

### 需求1：整体勾选粒度

用户确认粒度为"整体勾选"——内置规则集作为一条目列在清单顶部，用户可勾选/取消整个内置规则集。与现有 `use_builtin` 配置语义一致，无需新增配置字段。

### 需求1：row 0 固定不可操作

内置规则条目固定在 row 0，不可移动、不可移除。右键菜单在 row 0 时禁用所有操作；Delete 快捷键在 row 0 时不执行。用户规则索引 = `currentRow() - 1`。

### 需求1：阻塞信号避免循环

`_refresh_rules_file_list` 在 `clear()` 和 `addItem()` 期间阻塞 `itemChanged` 信号（`blockSignals(True)`），避免刷新列表时触发勾选状态回写导致循环。

### 需求2：缓存放在提取器层

缓存放在 `extractors/cache.py` 而非 GUI 层，因为 DetailPanel 与 HitDetailDialog 都调用 `extract_content_with_fallback`，缓存放在提取器层可以让两者共享缓存。

### 需求2：缓存键设计

缓存键为 `(str(path), st_mtime, st_size)`，确保文件修改后缓存自动失效。`stat` 失败时回退到无缓存提取（透传 `extract_content_with_fallback`）。

### 需求2：缓存大小限制

最大 32 项，单次提取内容上限由调用方截断（GUI 预览限制 100KB），总内存占用可控。使用 `OrderedDict` 实现 LRU 淘汰。

## 代码实现情况

### 需求1

- `_refresh_rules_file_list`：先 `blockSignals(True)`，`clear()`，添加内置规则条目（`Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable`，根据 `_use_builtin` 设置 `Qt.Checked`/`Qt.Unchecked`），再添加用户规则条目，最后 `blockSignals(False)`
- `_on_rules_file_item_changed(item)`：仅处理 row 0，`checkState` 变化时调用 `_set_use_builtin(enabled)` + `_save_config()`
- `_on_move_rule_up`/`_on_move_rule_down`/`_on_remove_rule`：row 0 不可操作，用户规则索引 = `row - 1`
- `_on_rules_file_list_context_menu`：row 0 时禁用所有操作

### 需求2

- `extractors/cache.py`：`extract_content_cached(path)` 先 `path.stat()` 取 `(mtime, size)` 作为缓存键，查 `OrderedDict` 缓存，未命中时调用 `extract_content_with_fallback` 并存入缓存，超过 `_CONTENT_CACHE_MAX=32` 时淘汰最旧项。`clear_content_cache()` 清空缓存。
- `detail_dialog.py` 与 `detail_panel.py`：`from fuscan.extractors import extract_content_cached`，`_populate_preview` 中调用 `extract_content_cached(path)` 替代 `extract_content_with_fallback(path)`

## 整合优化情况

- 需求1的 row 偏移逻辑统一在 `_on_move_rule_up`/`_on_move_rule_down`/`_on_remove_rule` 中处理，调用方无需感知
- 需求2的缓存对 DetailPanel 和 HitDetailDialog 透明，两者共享同一进程级缓存实例

## 测试验证结果

### 需求1测试（6 例新增 + 10 例适配）

- `test_builtin_item_check_state_reflects_use_builtin`：验证勾选状态反映 `_use_builtin`
- `test_uncheck_builtin_item_persists_to_config`：取消勾选后持久化到配置
- `test_recheck_builtin_item_persists_to_config`：重新勾选后持久化到配置
- `test_builtin_item_not_removable`：内置规则条目不可移除
- `test_builtin_item_not_movable`：内置规则条目不可上移/下移
- 10 例既有测试适配 row 偏移（`setCurrentRow` 索引 +1）

### 需求2测试（7 例新增）

- `test_cached_returns_same_content`：缓存提取与直接提取结果一致
- `test_second_call_uses_cache`：第二次调用命中缓存不重复提取
- `test_file_modified_invalidates_cache`：文件修改后缓存失效
- `test_clear_cache_empties_entries`：`clear_content_cache` 清空缓存
- `test_different_files_cached_separately`：不同文件分别缓存
- `test_stat_failure_falls_back_to_uncached`：stat 失败回退到无缓存提取
- `test_lru_eviction_when_exceeding_max`：超过最大缓存数时淘汰最旧项

### 全门禁

- ruff check：通过
- ruff format check：通过
- pyrefly check：0 errors（476 suppressed, 58 warnings）
- pytest：1385 passed, 16 deselected
- coverage：96.24%（>= 95%）
- `extractors/cache.py`：100% 覆盖率

## 遗留事项

无。

## 下一轮计划

无待办事项。等待用户下一轮需求。
