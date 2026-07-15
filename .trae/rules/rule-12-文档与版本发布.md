# 文档与版本发布

## 适用范围

凡涉及 fuscan 版本号升级（通过 `uv run bump-my-version bump <part>` 或等效命令）的变更，必须遵循本章约束。

## 版本号同步

fuscan 版本号在三处出现，**必须保持一致**：

| 位置 | 字段 | 当前值 |
|------|------|--------|
| `pyproject.toml` | `project.version` | 由 `[tool.bumpversion]` 自动更新 |
| `src/fuscan/__init__.py` | `__version__` | 由 `[tool.bumpversion]` 自动更新 |
| `docs/manual.md` 顶部 | `> 版本：x.y.z` | **手动同步**（bumpversion 未配置此文件） |

## 版本升级流程

执行 `uv run bump-my-version bump <part>` 后，**必须**按顺序完成以下步骤：

1. **同步手册版本号**：将 `docs/manual.md` 顶部的 `> 版本：x.y.z` 改为新版本号。
2. **重新生成 PDF**：执行 `uv run python scripts/generate_manual_pdf.py`，
   覆盖 `src/fuscan/assets/docs/fuscan-用户手册.pdf`。
3. **验证 PDF**：打开生成的 PDF，确认封面页版本号正确、目录可读、无中文乱码。
4. **随包分发检查**：`pyproject.toml` 的 `[tool.hatch.build.targets.wheel.force-include]`
   已配置 PDF 强制打包；`src/fuscan/assets/docs/fuscan-用户手册.pdf` 必须存在且为最新。
5. **提交**：将 `docs/manual.md`、`src/fuscan/assets/docs/fuscan-用户手册.pdf`、
   bumpversion 自动改动的两个文件一并 `git add`（按文件名）后 `git commit`。
   bumpversion 自身的 commit（`chore: 更新版本 ...`）由工具自动产生，
   PDF 与 manual.md 的同步作为**追加 commit**：
   ```
   docs: 同步用户手册 PDF 至 v<新版本号>
   ```

## 文档源与产物

- **Markdown 源**：`docs/manual.md`，面向初级用户、聚焦 GUI 使用。
  内容变更（新增章节、修订说明）可直接编辑此文件，**不要求**版本号递增。
- **PDF 产物**：`src/fuscan/assets/docs/fuscan-用户手册.pdf`，
  由 `scripts/generate_manual_pdf.py` 从 Markdown 源生成。**禁止**手工编辑 PDF。
- **生成脚本**：`scripts/generate_manual_pdf.py`，使用 reportlab + `STSong-Light` CID 字体。
  依赖：`reportlab>=3.6.13,<4.0; python_version < '3.9'`、`reportlab>=4.0.0; python_version >= '3.9'`。

## GUI 入口

主窗口「帮助 → 用户手册」（`manual_action`，快捷键 F1）通过 `QDesktopServices.openUrl`
调用系统默认 PDF 阅读器打开随包分发的 PDF。
- PDF 缺失时弹出提示并 `logger.warning`，不阻塞主流程。
- 槽实现：`MainWindow._on_open_manual`（`src/fuscan/gui/main_window.py`）。
- PDF 路径常量：`_MANUAL_PDF`（同文件内）。

## 何时必须重新生成 PDF

- **版本号升级**：见上文「版本升级流程」。
- **manual.md 内容变更**：若仅修订错别字、措辞，可不重新生成；若新增章节、变更截图描述、
  变更操作步骤，应重新生成以保持 PDF 与源一致。
- **PDF 生成脚本变更**：`scripts/generate_manual_pdf.py` 自身修改后须重新生成以验证渲染正确。

## 不要求重新生成 PDF 的情况

- 纯代码重构不影响 GUI 行为。
- 测试用例增删。
- 性能优化（除非影响 GUI 操作流程描述）。
- 文档（README、迭代记录、需求清单）变更。
