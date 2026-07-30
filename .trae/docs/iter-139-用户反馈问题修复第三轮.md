# iter-139 用户反馈问题修复第三轮

## 需求清单

参见 `.trae/req/req-41-用户反馈问题修复第三轮迭代计划.md`

## 迭代目标

修复 5 个用户反馈问题，覆盖 GUI 控制器、模型、提取器与 QML 视图，配套单元测试，全套门禁通过。

## 改动文件清单

### 源码

- `src/fuscan/gui/controllers/about_controller.py`：新增 `_open_path_robustly` 与 `openFailed` 信号
- `src/fuscan/gui/controllers/rules_controller.py`：`rulesFileModel` 增加 `exists` 字段
- `src/fuscan/gui/controllers/scan_controller.py`：连接 `rulesetChanged`，`canStartScan`/`rulesCount` 读最新 `ruleset`；`moveSelectedToStaging` 移除结果
- `src/fuscan/gui/models/result_model.py`：新增 `remove_result_by_path`
- `src/fuscan/gui/models/extractor_model.py`：新增 `engineInfo` role（`Qt.UserRole+10`）
- `src/fuscan/extractors/base.py`：`Extractor.engine_info` 属性与 `list_extractors` 5 元组
- `src/fuscan/extractors/{pdf,office,spreadsheet,legacy_office,odf,rtf,email,text,wps}.py`：各提取器覆盖 `engine_info`
- `src/fuscan/gui/views/components/RulesPanel.qml`：规则文件缺失标记
- `src/fuscan/gui/views/pages/AboutPage.qml`：`openFailed` Toast
- `src/fuscan/gui/views/pages/SettingsPage.qml`：tooltip 显示引擎信息
- `src/fuscan/gui/resources_rc.py`：QML 改动后重建

### 测试

- `tests/test_extractors.py`：`test_list_extractors_entry_format` 适配 5 元组；新增 `test_engine_info_*`
- `tests/test_gui_about.py`：新增 `TestOpenFailedSignal`
- `tests/test_gui_rules_controller.py`：新增 `exists` 字段测试
- `tests/test_gui_result_model.py`：新增 `TestRemoveResultByPath`
- `tests/test_gui_extractor_model.py`：`engineInfo` role 测试
- `tests/test_gui_scan_controller.py`：`TestRulesetChange` 补充信号与最新 ruleset 测试
- `tests/test_gui_scan_result_detail.py`：新增 `test_move_success_removes_result_from_list`

## 关键决策与依据

1. **`_open_path_robustly` 兜底策略**：`QDesktopServices.openUrl` 在 Windows 上对含中文路径的本地 PDF 偶发失败，回退 `os.startfile`（PySide2 已知问题）。失败时通过 `openFailed` 信号通知 QML 显示 Toast，避免用户点击无反馈。
2. **`rulesFileModel` 增加 `exists` 字段**：直接用 `Path(p).exists()` 判断，QML 侧用红色「缺失」标记，无需额外查询。
3. **`canStartScan`/`rulesCount` 直接读 `rules_controller.ruleset`**：消除 `__init__` 快照导致的陈旧缓存；同时连接 `rulesetChanged` 信号同步 `self._ruleset`（供 `startScan` 等仍引用缓存的方法使用），并发 `canStartScanChanged`/`rulesCountChanged`。
4. **`remove_result_by_path` 同步 `_last_report`**：移至暂存成功后，从 `_result_model._results` 移除该路径，并从 `_last_report.hits` 过滤同路径条目，重置 `selectedResultIndex`。
5. **`engine_info` 作为 `Extractor` 属性**：各子类覆盖返回具体引擎名（如 `pdf_oxide`/`pypdf`、`python-calamine`、`lxml`/`python-docx`），`list_extractors` 返回 5 元组，`ExtractorListModel` 暴露 `engineInfo` role，QML tooltip 显示「引擎：xxx」。

## 代码实现情况

- 5 项修复全部实现，覆盖源码与 QML。
- `resources_rc.py` 已用 `scripts/build_qrc.py` 重建。

## 整合优化情况

- `engine_info` 复用各提取器已有的后端探测逻辑（如 `_lxml_available`、`kreuzberg_available`），无重复实现。
- `remove_result_by_path` 复用 `_schedule_filter_refresh`，自动处理同步/异步过滤刷新。

## 测试验证结果

- ruff check：通过
- ruff format --check：通过
- pyrefly check：通过
- pytest --cov=fuscan：覆盖率 95.36%（≥95%）
- 本次新增/修改的测试全部通过（467 passed）
- 预存在的环境相关失败（lxml 可用导致 speed_tier 断言、SQLite database is locked、perf benchmark 波动）已用 `git stash` 验证非本次回归

## 遗留事项

- `release.yml` 与 `pyproject.toml` 的 fspack 打包配置改动（标注 iter-139）属于另一逻辑变更，单独提交，不混入本次用户反馈修复提交。
- 预存在的 `test_extractor_benchmark.py` speed_tier 断言写死档位问题（lxml 可用时档位变快），建议后续迭代改为动态断言。

## 下一轮计划

- 视用户反馈决定是否继续修复其他问题。
- 跟进 `test_extractor_benchmark.py` 动态档位断言改造。
