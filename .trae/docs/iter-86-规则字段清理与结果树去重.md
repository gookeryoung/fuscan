# iter-86 规则字段清理与结果树去重

## 需求清单

- [x] 移除规则中废弃的 file_extensions 字段（req-23）
- [x] 去掉结果 Table 的"命中数"和"条数"列

## 迭代目标

完成两项清理：1) 彻底移除自 iter-71 起废弃但代码中仍广泛存在的 `Rule.file_extensions` 字段（model/parser/cache 序列化/规则 yaml/文档/测试）；2) 去掉结果 Table 与"详情"列重复的"命中数"和"条数"列，让结果树更紧凑。

## 关键决策与依据

1. **完全移除而非保留兼容**：用户在 AskUserQuestion 中明确选择"完全移除"方案。iter-71 已废弃 `Rule.file_extensions` 长达 15 个迭代，期间 Scanner 不再读取此字段，但 parser 仍主动解析、cache 仍序列化、builtin/rules.yaml 与 examples/*.yaml 中 59 处仍含此字段、README/manual 还在介绍使用方法（误导用户）。完全移除后：
   - 旧规则文件中保留的 file_extensions 字段会被 parser 静默忽略（YAML 解析不报错，data.get 不再读取）
   - 旧缓存文件因 rule_hash 序列化内容变化而失效——首次扫描重新计算 hash，是预期内的破坏性变更
   - 用户自定义规则文件不会因字段存在而报错
2. **批量清理用临时脚本**：9 个 yaml 文件共 59 个 file_extensions 块，手动 Edit 工作量过大且易错。临时脚本 `_clean_fe.py` 用正则 `\n    file_extensions:\n(?:      - .*\n)+` 一次性删除所有块并保留规则间空行，运行后立即删除。脚本验证 yaml 合法性后通过。
3. **结果树 4 列而非 6 列**：原 `_HEADERS = ["文件名", "规则", "严重等级", "命中数", "条数", "详情"]`，其中"命中数"= `str(sr.total_match_count)` (M)、"条数"= `str(len(sr.hits))` (N)、"详情"= `sr.summary()` = `"N 条规则 / M 处匹配"`——前两列完全被"详情"列包含；右侧详情区 `file_info_html` 也显示"命中规则数 N | 匹配条数 M"。三处重复，去掉独立列后表头从 6 列缩减为 4 列 `["文件名", "规则", "严重等级", "详情"]`，横向空间更宽裕。
4. **保留详情区 hits_table 6 列**：用户问题仅针对"结果 Table"（result_tree），详情区命中表 `detail_hits_table` 的"位置/条数/详情/desc"列在 detail_panel.py 中独立维护，不属于本次清理范围。
5. **保留分组顶层行的汇总信息**：by-rule/by-severity 模式下顶层行的"详情"列仍填 `f"{N} 个文件 / {M} 处匹配"`，信息未丢失，只是不再拆成独立列。

## 改动文件清单

修改：
- `src/fuscan/rules/model.py`：移除 `Rule.file_extensions` 字段与废弃注释
- `src/fuscan/rules/parser.py`：移除 `parse_rule` 中 file_extensions 解析逻辑，添加注释说明字段已移除、旧规则文件中的该字段被静默忽略
- `src/fuscan/cache/hashes.py`：移除 `serialize_rule` 中 `"file_extensions": sorted(rule.file_extensions)` 行（影响 rule_hash 计算，旧缓存失效）
- `src/fuscan/builtin/rules.yaml`：移除 3 处 file_extensions 块
- `rules/examples/*.yaml`（13 个文件）：移除共 56 处 file_extensions 块
- `rules/examples/README.md`：移除顶层结构示例中 file_extensions 字段；移除"### 1. 限定 file_extensions 提升性能"章节；后续章节重新编号 2→3→4→5
- `docs/manual.md`：将"检查规则的 file_extensions 是否包含目标文件扩展名"改为"检查主界面'文件类型'勾选是否包含目标文件扩展名"
- `src/fuscan/gui/result_tree.py`：
  - `_HEADERS` 从 6 列改为 4 列 `["文件名", "规则", "严重等级", "详情"]`
  - `__init__` 中 header resize 模式调整：0/1/3 Interactive（文件名 220px / 规则 140px / 详情 200px），2 ResizeToContents（严重等级），stretchLastSection=True
  - `_populate_flat`：file_row 从 6 列文本改为 4 列 `[sr.path.name, "", "", sr.summary()]`；child_row 从 6 列改为 4 列 `["", hit.rule_name, "", hit.detail]`；移除 file_row[3]/[4]、child_row[4] 的 setTextAlignment
  - `_populate_grouped_by_rule`：top_row 改为 4 列 `["", rule_name, "", f"{N} 个文件 / {M} 处匹配"]`；child_row 改为 4 列 `[sr.path.name, "", "", hit.detail]`；移除 setTextAlignment
  - `_populate_grouped_by_severity`：top_row 改为 4 列 `["", "", severity_text, f"{N} 个文件 / {M} 处匹配"]`；child_row 改为 4 列 `[sr.path.name, "", "", sr.summary()]`；移除 setTextAlignment
  - `_make_result_row` docstring 中列名描述同步更新
- `tests/test_rules_parser.py`：移除 `test_parse_rule_with_extensions` 与 `test_parse_rule_extensions_wrong_type_raises`；新增 `test_parse_rule_legacy_file_extensions_ignored` 验证旧字段被静默忽略（`assert not hasattr(rule, "file_extensions")`）
- `tests/test_rules_model.py`：移除 `test_create_rule` 中 `assert rule.file_extensions == ()`
- `tests/test_cache.py`：移除 `test_serialize_with_extensions` 与 `test_serialize_extensions_order_independent`；移除 `_setup_store_with_rule` 与 `test_rule_hashes_set_change_treats_as_miss` 中 `file_extensions=()` 传参
- `tests/test_multiformat_scan.py`：`_leaf` helper 移除 `exts` 参数与 `file_extensions=exts` 传参（无调用方传 exts=）
- `tests/test_gui.py`：
  - `test_column_count_includes_hit_count` 重命名为 `test_column_count_is_four_after_dedup`，断言 `columnCount() == 4`
  - 删除 `TestMatchCountDisplay` 中三个针对结果树列的测试（`test_flat_file_item_shows_match_count`、`test_flat_child_item_shows_rule_match_count`、`test_group_by_rule_shows_match_sum`）
  - 保留 `test_detail_hits_table_shows_match_count` 与 `test_detail_info_label_shows_match_count`（针对详情区，与结果树列无关）
  - 类 docstring 更新说明 iter-86 移除结果树列后仅保留详情区验证

## 代码实现情况

### 1. file_extensions 字段移除（model + parser）

`model.py` 中 `Rule` dataclass 移除 `file_extensions: tuple[str, ...] = field(default_factory=tuple)` 字段与废弃注释。`parser.py` 中 `parse_rule` 移除 line 135-138 的 extensions_raw 解析与 line 145 的 file_extensions= 传参，替换为注释说明"file_extensions 已移除（iter-86）：旧规则文件中的该字段被静默忽略，文件后缀过滤由全局 Config.extractors 统一管理"。

### 2. cache/hashes.py 序列化清理

`serialize_rule` 函数中 `data` 字典移除 `"file_extensions": sorted(rule.file_extensions)` 键。由于 `compute_rule_hash` 基于 `serialize_rule` 输出计算 SHA-256，旧缓存中基于含 file_extensions 序列化计算的 hash 会与新 hash 不匹配——首次扫描时 `CacheStore.register_ruleset` 会重新登记规则并视为缓存未命中，重新扫描，这是预期内的破坏性变更。

### 3. 规则 yaml 文件批量清理

临时脚本 `_clean_fe.py` 用正则 `\n    file_extensions:\n(?:      - .*\n)+` 匹配并删除所有 file_extensions 块及其下属列表项，保留规则间空行。共清理 59 个块（builtin/rules.yaml 3 处、examples/*.yaml 56 处）。脚本运行后立即删除。所有 yaml 文件经 `yaml.safe_load` 验证仍合法。

### 4. 文档同步

`rules/examples/README.md` 顶层结构示例移除 file_extensions 字段，添加注释说明"文件后缀过滤已由全局配置（解析器勾选）统一管理（iter-86 起规则中不再支持 file_extensions 字段，旧规则文件中保留该字段会被静默忽略）"。"规则编写最佳实践"章节移除"### 1. 限定 file_extensions 提升性能"，后续 4 个章节重新编号 2/3/4/5。`docs/manual.md` FAQ 中"检查规则的 file_extensions"改为"检查主界面'文件类型'勾选"。

### 5. 结果树列精简

`result_tree.py` 中 `_HEADERS` 从 6 列缩减为 4 列。三个 `_populate_*` 方法同步调整列填充：file_row/child_row/top_row 均传 4 列文本；移除所有针对已删列的 `setTextAlignment(Qt.AlignCenter)` 调用。`__init__` 中 header resize 配置同步调整：0/1/3 Interactive，2 ResizeToContents，stretchLastSection=True 让详情列填充剩余空间。

### 6. 测试更新

`test_rules_parser.py` 移除两个针对 file_extensions 解析的测试，新增一个验证旧字段被静默忽略的测试（断言 `not hasattr(rule, "file_extensions")`）。`test_rules_model.py` 移除字段存在性断言。`test_cache.py` 移除两个序列化测试与两处 `file_extensions=()` 传参。`test_multiformat_scan.py` 的 `_leaf` helper 移除 exts 参数。`test_gui.py` 重命名列数测试为 4 列断言；删除 `TestMatchCountDisplay` 中三个针对结果树列的测试，保留针对详情区的两个测试。

## 整合优化情况

- README.md 章节编号在移除"### 1. 限定 file_extensions 提升性能"后重新顺延，避免编号断层。
- `result_tree.py` `_make_result_row` docstring 中列名描述同步更新为"文件名/规则/严重等级/详情"。
- `test_rules_parser.py` 新增 `test_parse_rule_legacy_file_extensions_ignored` 测试覆盖向后兼容场景，确保用户旧规则文件不会因字段存在而报错。
- scanner.py / archive/scanner.py / workers/scan_worker.py 中关于"不再按 rule.file_extensions 过滤"的历史注释保留作为说明，避免未来重新引入字段时遗忘 iter-71 的迁移决策。

## 测试验证结果

- ruff check src tests：**All checks passed**
- ruff format --check src tests：**104 files already formatted**
- pyrefly check：**0 errors**（555 suppressed, 62 warnings not shown）
- pytest -m "not slow" --cov=fuscan --cov-fail-under=95：**1581 passed**，覆盖率 **95.13%**（≥ 95% 阈值）
- 首次运行时 2 个测试失败（`test_column_count_is_four_after_dedup` 与 `test_critical_tree_item_has_background`），根因是首次 Edit 修改 `_HEADERS` 时未生效（文件回滚或 Edit 匹配失败）。第二次手动 Edit 后通过，columnCount 现为 4，critical 整行背景高亮循环 `range(columnCount())` 不再访问不存在的列。

## 遗留事项

- scanner.py / archive/scanner.py / workers/scan_worker.py 中仍保留 5 处关于 rule.file_extensions 的历史注释，作为 iter-71 迁移决策的说明保留。
- `tests/test_archive.py` 与 `tests/test_scanner.py` 中 `test_file_extensions_filter` 测试是针对全局 Config.scan_extensions（解析器勾选）过滤功能，与已移除的 Rule.file_extensions 字段无关，保留。
- 缓存失效：iter-86 发布后用户首次扫描时旧缓存自动失效并重建，无需手动清理。

## 下一轮计划

无。本次需求已完成，等待用户实测确认。
