# 收尾：补齐 3 个测试类 + 门禁验证 + 提交推送

## Summary

上一轮会话已完成全部 4 部分实现（图标接入 / 扫描进度界面增强 / 严重等级背景色 / 命中数 bug 修复），代码改动均在磁盘上但**尚未提交**。13 项计划测试中已完成 10 项（4 walker + 2 scanner + 1 worker + 3 scan callback），剩余 3 个测试类未添加。本计划完成收尾：补齐测试 → 门禁验证 → 提交推送。

## Current State Analysis

### 已完成（磁盘上，未提交）

| 文件 | 改动 | 状态 |
|------|------|------|
| `src/fuscan/gui/main_window.py` | +106 行：18 图标常量/QIcon/setIcon、`_SEVERITY_BACKGROUNDS` 背景色、critical 整行背景、预览回退提示、强制刷新、分组项不可选中、进度 UI（stats/skipped/matched 列表更新） | 完成 |
| `src/fuscan/gui/main_window.ui` | +66 行：stats_group + lists_splitter（两个 QListWidget） | 完成 |
| `src/fuscan/gui/main_window_ui.py` | +53 行：pyside2-uic 重新编译 | 完成 |
| `src/fuscan/gui/worker.py` | +3 行：`_on_progress` 透传 `skipped_dirs`/`matched_files` | 完成 |
| `src/fuscan/scanner/result.py` | +4 行：ProgressInfo 新增 `skipped_dirs`/`matched_files` 字段 | 完成 |
| `src/fuscan/scanner/scanner.py` | +21 行：`_skipped_dirs`/`_matched_files` 收集器、`_on_skip_dir_internal` 回调、`_emit_progress` 填充新字段 | 完成 |
| `src/fuscan/scanner/walker.py` | +10 行：`on_skip_dir` 回调参数 + 两处目录跳过点调用 | 完成 |
| `tests/test_walker.py` | +39 行：4 个 on_skip_dir 测试 | 完成 |
| `tests/test_scanner.py` | +23 行：ProgressInfo 字段 + skipped_dirs 收集测试 | 完成 |
| `tests/test_gui.py` | +107 行：1 worker 透传测试 + 3 scan callback 测试 | 完成 |
| `tests/test_rules_parser.py` | ruff format 格式化（dict 字面量换行） | 完成（纯格式） |

### 待完成

1. **3 个测试类**（test_gui.py 末尾追加）：
   - `TestIcons` — 验证按钮/菜单动作图标已设置
   - `TestSeverityBackground` — 验证 critical 整行背景色 + 分组项不可选中
   - `TestDetailPreviewFallback` — 验证无关键词时预览显示回退提示

2. **门禁验证**：ruff check + format check + pyrefly + pytest --cov=96

3. **提交推送**：git commit（中文，遵循 rule-09）+ git push（分支已跟踪 origin/main）

## Proposed Changes

### 改动 1：`tests/test_gui.py` — 追加 3 个测试类

在文件末尾（L4274 `TestSettingsDialogIgnore` 类结束后）追加以下 3 个测试类。

#### TestIcons

验证所有按钮和菜单动作的图标非空（`icon().isNull() == False`）。

```python
class TestIcons:
    """按钮与菜单动作图标接入测试。"""

    def test_all_action_buttons_have_icons(self, qapp: QApplication) -> None:
        """所有操作按钮应设置图标。"""
        window = MainWindow()
        assert not window._edit_rule_btn.icon().isNull()
        assert not window._export_btn.icon().isNull()
        assert not window._rescan_btn.icon().isNull()
        assert not window._cancel_btn.icon().isNull()
        assert not window._pause_resume_btn.icon().isNull()
        window.close()

    def test_all_menu_actions_have_icons(self, qapp: QApplication) -> None:
        """所有菜单动作应设置图标。"""
        window = MainWindow()
        assert not window._edit_rules_action.icon().isNull()
        assert not window._export_csv_action.icon().isNull()
        assert not window._export_json_action.icon().isNull()
        assert not window._settings_action.icon().isNull()
        assert not window._ui.about_action.icon().isNull()
        window.close()
```

**依据**：main_window.py L406-415 已对上述控件调用 `setIcon`。`_edit_rule_btn`（单数）是按钮属性，`_edit_rules_action`（复数）是菜单动作属性。

#### TestSeverityBackground

验证 critical 项整行背景色为浅红，分组模式下顶层项不可选中。

```python
class TestSeverityBackground:
    """严重等级背景色与分组项可选性测试。"""

    def test_critical_tree_item_has_background(self, qapp: QApplication, tmp_path: Path) -> None:
        """critical 等级文件项各列应有浅红背景色。"""
        from fuscan.scanner import Scanner
        from fuscan.gui.main_window import _SEVERITY_BACKGROUNDS

        (tmp_path / "leak.conf").write_text("AKIAIOSFODNN7EXAMPLE", encoding="utf-8")
        # 用 critical 规则命中
        rs = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="AWS密钥",
                    severity=Severity.CRITICAL,
                    match=LeafMatch(
                        target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="AKIA"
                    ),
                ),
            ),
        )
        report = Scanner(rs).scan(tmp_path)
        window = MainWindow()
        window._last_report = report
        window._switch_stage(WorkflowStage.RESULTS)
        window._refresh_result_tree()

        top_item = window._result_tree.topLevelItem(0)
        assert top_item is not None
        expected_bg = _SEVERITY_BACKGROUNDS[Severity.CRITICAL]
        for col in range(top_item.columnCount()):
            bg = top_item.background(col)
            assert bg.color().rgb() == expected_bg.rgb()
        window.close()

    def test_group_items_non_selectable(self, qapp: QApplication, tmp_path: Path) -> None:
        """按严重等级分组模式下，顶层分组项应不可选中。"""
        from fuscan.scanner import Scanner

        (tmp_path / "secret.txt").write_text("password", encoding="utf-8")
        rs = _build_ruleset()  # WARNING 等级
        report = Scanner(rs).scan(tmp_path)

        window = MainWindow()
        window._last_report = report
        window._switch_stage(WorkflowStage.RESULTS)
        # 切换到"按严重等级"分组
        idx = window._group_mode_combo.findData("severity")
        window._group_mode_combo.setCurrentIndex(idx)
        window._refresh_result_tree()

        top_item = window._result_tree.topLevelItem(0)
        assert top_item is not None
        assert not (top_item.flags() & Qt.ItemIsSelectable)
        window.close()
```

**依据**：
- main_window.py L1482-1484 `_populate_flat` 中 critical 整行 setBackground
- main_window.py L1549 `_populate_grouped_by_severity` 中 `top.setFlags(top.flags() & ~Qt.ItemIsSelectable)`
- `_SEVERITY_BACKGROUNDS[CRITICAL]` = `QColor(255, 235, 235)` (L102-103)
- `_group_mode_combo` 的 "按严重等级" 项 data 为 `"severity"` (L336)

#### TestDetailPreviewFallback

验证命中规则但无法提取关键词时，预览面板显示回退提示。

```python
class TestDetailPreviewFallback:
    """详情预览回退提示测试。"""

    def test_preview_shows_fallback_when_no_keywords(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        """命中规则但 detail 无单引号关键词时，预览应显示回退提示。"""
        from fuscan.scanner.result import RuleHit, ScanResult

        path = tmp_path / "config.yaml"
        path.write_text("some: content\nhere: value", encoding="utf-8")

        # detail 不含单引号，_extract_keywords 返回空列表
        result = ScanResult(
            path=path,
            size=path.stat().st_size,
            hits=(RuleHit("路径规则", Severity.INFO, "路径匹配"),),
        )

        window = MainWindow()
        window._detail_show_result(result)
        text = window._detail_preview.toPlainText()
        assert "无内容关键词可高亮" in text
        assert "路径规则" in text
        window.close()
```

**依据**：main_window.py L1175-1185 `_populate_detail_preview` 中 `if not keywords and result.hits:` 分支显示 `f"（此文件因【{rule_names}】规则命中，但无内容关键词可高亮。命中详情见上方表格。）"`。`_detail_show_result`（L1106-1115）调用 `_populate_detail_preview`。RuleHit detail="路径匹配" 不含单引号，`_KEYWORD_RE`（L77 `r"'([^']+)'`）无法匹配。

### 改动 2：门禁验证

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyrefly check
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96
```

若 ruff format 报差异，运行 `uv run ruff format src tests` 修正。若 pyrefly 报新错误，定位修复。若覆盖率 < 96%，补充测试。若测试失败，定位根因修复。

### 改动 3：提交推送

按文件名 stage 所有改动文件 + 新计划文件，提交（中文，遵循 rule-09），推送。

```bash
git add src/fuscan/gui/main_window.py src/fuscan/gui/main_window.ui src/fuscan/gui/main_window_ui.py src/fuscan/gui/worker.py src/fuscan/scanner/result.py src/fuscan/scanner/scanner.py src/fuscan/scanner/walker.py tests/test_gui.py tests/test_scanner.py tests/test_walker.py tests/test_rules_parser.py .trae/documents/gui-icons-progress-severity-fix.md .trae/documents/complete-tests-and-commit.md
git commit -m "..."
git push
```

提交信息（中文，一段落）：
```
feat(gui): 接入按钮/菜单图标并增强扫描进度界面与严重等级背景色

为编辑/导出/设置/重新扫描等控件接入 SVG 图标；扫描中页面新增统计面板与跳过目录/命中文件列表；critical 项整行浅红背景高亮；修复命中数不一致问题（预览回退提示、强制刷新、分组项不可选中）；FileWalker 新增 on_skip_dir 回调，ProgressInfo 扩展 skipped_dirs/matched_files 字段。
```

## Assumptions & Decisions

1. **测试追加位置**：test_gui.py 末尾（L4274 后），与现有 `TestSettingsDialogIgnore` 同级。
2. **critical 背景色断言用 rgb()**：`QColor(255,235,235)` 比较 rgb 值而非 name()，避免 alpha 通道差异。
3. **TestDetailPreviewFallback 直接构造 ScanResult**：不经过 Scanner 扫描，直接构造 detail 无单引号的 RuleHit，确保 `_extract_keywords` 返回空列表。比构造特定规则更确定、更快速。
4. **TestSeverityBackground 用 CONTENT+CRITICAL 规则**：`_build_ruleset()` 返回的是 WARNING 规则，无法测 critical 背景所以需自定义 critical 规则。
5. **test_rules_parser.py 格式化改动保留**：纯 ruff format 字面量换行，无逻辑变化，随本次提交。
6. **不修改 pyproject.toml**：walker.py 的 PLR0913 用 inline `# noqa` 处理（上轮已 done），不新增 per-file-ignores。
7. **推送策略**：origin/main 落后 1 commit（用户的 6fea624），本次 push 会推送 6fea624 + 新 commit。

## Verification

1. `uv run ruff check src tests` — 0 errors
2. `uv run ruff format --check src tests` — 0 差异
3. `uv run pyrefly check` — 0 errors（107 suppressed 不变）
4. `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96` — 全部通过，覆盖率 ≥ 96%
5. `git log --oneline -3` 确认新 commit 在 6fea624 之上
6. `git status` 确认工作区干净
