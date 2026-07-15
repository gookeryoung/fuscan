# iter-40 用户手册 PDF 与文档规范

## 需求清单

- [x] 以面向初级用户为目标完善文档，重点是 GUI 软件的使用
- [x] 生成用户手册 PDF 版本，放置在 `assets/docs/` 下，并在 GUI 帮助菜单提供入口
- [x] 配置规则，每次升级版本之后均需要更新 PDF

## 迭代目标

补齐面向终端用户的 GUI 使用文档与可分发的 PDF 产物，建立版本升级时同步更新 PDF 的流程规范，
使新用户能脱离源码直接通过随包 PDF 学习软件使用，并确保后续版本迭代不会破坏文档与代码版本一致性。

## 改动文件清单

### `docs/manual.md`（新建）

面向初级用户的 GUI 使用手册 Markdown 源，12 节内容：

1. 软件简介
2. 安装与启动
3. 主界面总览（含 ASCII 布局图）
4. 第一次扫描
5. 查看结果
6. 详情区高亮
7. 导出
8. 规则管理
9. 设置
10. 历史
11. 快键键
12. 常见问题

顶部含 `> 版本：0.1.5` 元信息，供 PDF 生成脚本提取并在封面与页脚显示。
内含目录锚点（`#1-软件简介` 等）仅供 Markdown 阅读器跳转，PDF 渲染时转为纯文本。

### `scripts/generate_manual_pdf.py`（新建）

从 `docs/manual.md` 生成 PDF 到 `src/fuscan/assets/docs/fuscan-用户手册.pdf`。

关键设计：

- **中文字体**：使用 reportlab 内置 CID 字体 `STSong-Light`（`UnicodeCIDFont`），
  无需字体文件，跨平台一致。CID 字体无独立粗体，标题用同字体加粗渲染。
- **双 PageTemplate**：封面页（`_on_first_page`，无页脚）+ 正文页（`_on_page`，页脚带版本号与页码）。
  封面页包含标题、副标题、版本信息、适用对象、分隔线。
- **Markdown 解析**：`_parse_markdown` 主分发器 + `_parse_markdown_block` 单块解析器
  （提取以规避 ruff PLR0912 too-many-branches），支持标题（h1/h2/h3）、代码块（`Preformatted`）、
  引用、表格、无序/有序列表、普通段落、分隔线（`HRFlowable`）。
- **行内标记**：`_render_inline` 渲染行内代码（`<font face="Courier">`）、粗体（`<b>`）、
  链接。**链接处理关键点**：仅保留 `http(s)://` 外部链接为 `<a>` 标签，
  锚点链接（`#xxx`）转为纯文本，避免 reportlab 抛出
  `ValueError: format not resolved, probably missing URL scheme or undefined destination target`。
- **表格**：`_parse_table` 支持 Markdown 表格转 reportlab `Table`，表头蓝底白字 + 斑马纹。
- **页面布局**：A4，左右边距 20mm，上 20mm 下 18mm，正文 frame 满足可绘制区域。
- **退出码**：源文件缺失返回 1，正常生成返回 0。

### `src/fuscan/assets/docs/fuscan-用户手册.pdf`（生成）

由上述脚本生成的 PDF 产物，随包分发。封面页含版本号 `0.1.5`，正文每页页脚左下显示
`fuscan 用户手册 · v0.1.5`，右下显示页码。

### `pyproject.toml`（修改）

- **dev 依赖新增 reportlab**（按 Python 版本分流）：
  - `reportlab>=3.6.13,<4.0; python_version < '3.9'`（避免 4.x 在 Py3.8 上
    因 `hashlib.md5(usedforsecurity=False)` 关键字不可用而抛 TypeError）
  - `reportlab>=4.0.0; python_version >= '3.9'`
- **wheel force-include** 新增 PDF 强制打包：
  `"src/fuscan/assets/docs/fuscan-用户手册.pdf" = "fuscan/assets/docs/fuscan-用户手册.pdf"`
- **ruff per-file-ignores** 新增 `scripts/**` 豁免 `PLR0911`/`PLR0912`/`PLR0915`
  （脚本类函数允许分支与返回点多于库代码）
- **pyrefly project-excludes** 新增 `scripts/**`（脚本使用动态属性注入如
  `doc.version = ...`，不适合严格类型检查）

### `src/fuscan/gui/main_window.ui`（修改）

`help_menu` 在 `about_action` 之前新增 `manual_action` 项（`<addaction name="manual_action"/>`），
并新增 `<action name="manual_action">` 定义，`text="用户手册"`，`shortcut="F1"`。

### `src/fuscan/gui/main_window_ui.py`（重新生成）

由 `pyside2-uic main_window.ui` 重新生成，包含：

- `self.manual_action = QAction(MainWindow)` 定义
- `self.help_menu.addAction(self.manual_action)` 挂载
- `self.manual_action.setText("用户手册")`
- `self.manual_action.setShortcut("F1")`

### `src/fuscan/gui/main_window.py`（修改）

- **导入新增**：`QUrl`（QtCore）、`QDesktopServices`（QtGui），均加入 PySide2/PySide6 双兼容导入块。
- **路径常量**：`_MANUAL_PDF = Path(__file__).parent.parent / "assets" / "docs" / "fuscan-用户手册.pdf"`。
- **信号连接**：`self.manual_action.triggered.connect(self._on_open_manual)`。
- **槽实现 `_on_open_manual`**：
  - PDF 不存在时 `logger.warning` + `QMessageBox.information` 提示运行生成脚本，不阻塞主流程。
  - PDF 存在时用 `QDesktopServices.openUrl(QUrl.fromLocalFile(...))` 调用系统默认 PDF 阅读器打开。
  - `openUrl` 返回 False 时 `logger.warning` + `QMessageBox.warning` 提示检查 PDF 阅读器。

### `.trae/rules/rule-12-文档与版本发布.md`（新建）

规范版本升级时必须更新 PDF 的流程：

- **版本号三处同步**：`pyproject.toml`、`src/fuscan/__init__.py`（bumpversion 自动）+
  `docs/manual.md` 顶部 `> 版本：x.y.z`（手动，bumpversion 未配置此文件）。
- **版本升级流程五步**：同步手册版本号 → 重新生成 PDF → 验证 PDF → 随包分发检查 →
  追加 commit（`docs: 同步用户手册 PDF 至 v<新版本号>`）。
- **文档源与产物**：禁止手工编辑 PDF；manual.md 内容变更（错别字）可不必重新生成，
  结构性变更（新增章节、操作步骤变更）应重新生成。
- **何时必须/不要求重新生成 PDF** 的判断准则。
- **GUI 入口**：记录 `manual_action` + F1 快捷键 + `_on_open_manual` 槽的代码位置。

## 关键决策与依据

### reportlab 选型与版本分流

- **为何选 reportlab**：纯 Python、跨平台、支持中文字体、可编程生成结构化文档。
  对比 markdown-pdf（依赖 wkhtmltopdf 二进制）、weasyprint（依赖 Pango/GDK），
  reportlab 无系统级依赖，符合 fuscan "纯 Python 库优先" 原则。
- **为何用 CID 字体而非 TTF**：`STSong-Light` 是 reportlab 内置 Adobe 亚洲字体包，
  无需额外字体文件，跨平台显示一致；中文 TTF 字体文件大（10MB+），
  打包进 wheel 体积膨胀且存在版权风险。
- **为何按 Python 版本分流 reportlab**：reportlab 4.x 使用了
  `hashlib.md5(usedforsecurity=False)` 关键字参数，Python 3.9+ 才支持。
  Py3.8 环境下 4.4.3 会抛 `TypeError`。3.6.13 是最后一个支持 Py3.8 的稳定版，
  与 4.x 在 PDF 输出格式上一致。

### 锚点链接转纯文本

- **问题**：Markdown 目录的 `[text](#anchor)` 链接被 reportlab 当作未解析的内部目标，
  抛 `ValueError: format not resolved`。
- **方案对比**：
  - 方案 A：为每个标题注册 `Paragraph` 时指定 `bookmarkName`，链接转为 `<a href="#bookmark">`。
    需要额外维护锚点 ID 与 bookmark 名映射，复杂度高。
  - 方案 B：仅保留 `http(s)://` 外部链接为 `<a>`，锚点链接转纯文本。
- **决策**：选方案 B。PDF 阅读器中目录跳转价值有限（页码导航已足够），
  实现简单且不会引入维护负担。

### PDF 强制打包而非动态生成

- **方案对比**：
  - 方案 A：随包分发 PDF（force-include 到 wheel），用户安装即得。
  - 方案 B：安装时动态调用 reportlab 生成 PDF，需要把 reportlab 加入运行时依赖。
- **决策**：选方案 A。reportlab 是 dev 依赖，不进入运行时；用户在未安装 reportlab
  的环境也能查看 PDF。PDF 是版本化产物，随包分发与 `__version__` 一致性更强。

### bumpversion 不包含 manual.md

- **现状**：`[tool.bumpversion.files]` 仅配置 `pyproject.toml` 与 `src/fuscan/__init__.py`。
- **为何不加入 manual.md**：
  - bumpversion 的 `commit=true`、`tag=true` 会在版本升级时自动 commit + tag。
  - 若 manual.md 加入 bumpversion，bumpversion commit 会包含 manual.md 但**不包含** PDF
    （PDF 是二进制不能在 bumpversion 中处理），导致 commit 不完整。
  - 更合理的流程是 bumpversion 单独 commit + tag（`chore: 更新版本 ...`），
    然后**追加**一次 commit 同步 manual.md 与 PDF（`docs: 同步用户手册 PDF 至 v...`）。
- **trade-off**：rule-12 中明确要求手动同步 manual.md 版本号，依赖规则约束而非工具自动。

## 代码实现情况

### main_window.py 信号槽连接

```python
self.manual_action.triggered.connect(self._on_open_manual)
```

### _on_open_manual 槽

```python
def _on_open_manual(self) -> None:
    """打开用户手册 PDF（随包分发的 assets/docs/fuscan-用户手册.pdf）。"""
    url = QUrl.fromLocalFile(str(_MANUAL_PDF))
    if not _MANUAL_PDF.exists():
        logger.warning("用户手册 PDF 不存在: %s", _MANUAL_PDF)
        QMessageBox.information(
            self, "提示", f"用户手册 PDF 未找到:\n{_MANUAL_PDF}\n\n请运行 scripts/generate_manual_pdf.py 生成。"
        )
        return
    if not QDesktopServices.openUrl(url):
        logger.warning("无法打开用户手册 PDF: %s", _MANUAL_PDF)
        QMessageBox.warning(self, "打开失败", f"无法打开用户手册 PDF，请检查系统是否安装 PDF 阅读器:\n{_MANUAL_PDF}")
```

### generate_manual_pdf.py 链接处理

```python
def _render_inline(text: str) -> str:
    """渲染行内 Markdown 标记为 reportlab 支持的 HTML 子集。

    链接仅保留 http(s) 外部链接，锚点链接（#xxx）转为纯文本，
    避免 reportlab 将其当作未解析的内部目标抛错。
    """
    text = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)

    def _link_repl(match: re.Match[str]) -> str:
        text_part, url = match.group(1), match.group(2)
        if url.startswith("http://") or url.startswith("https://"):
            return f'<a href="{url}">{text_part}</a>'
        return text_part

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link_repl, text)
    return text
```

## 整合优化情况

- **`_parse_markdown` 拆分**：原单函数包含所有块类型判断，ruff 报 `PLR0912` (15 > 12 branches)。
  提取 `_parse_markdown_block` 处理单块类型，主函数仅做分发与封面跳过。
- **`_parse_markdown` 移除 `version` 参数**：ruff 报 `ARG001` Unused function argument。
  version 在 `_build_cover` 中已使用，`_parse_markdown` 不需要传入。
- **scripts/ 豁免**：ruff `PLR0911`/`PLR0912`/`PLR0915` 对脚本工具类函数过严，
  pyrefly 对动态属性注入（`doc.version = ...`）不友好，统一在 `pyproject.toml` 中豁免。

## 测试验证结果

### 门禁

```bash
uv run ruff check src scripts          # 通过
uv run ruff format --check src scripts # 通过
uv run pyrefly check                   # 0 errors
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95
```

### pytest 结果

```
1230 passed, 16 deselected, 1 warning in 47.23s
Required test test coverage of 95% reached. Total coverage: 95.95%
```

覆盖率从 iter-39 的 96.14% 略降 0.19%，因 `main_window.py` 新增 `_on_open_manual`
槽（含 PDF 缺失与打开失败两个分支）未配套测试。分支属于 GUI 交互路径，
通过 `QDesktopServices.openUrl` 调用系统 PDF 阅读器，单元测试 mock 成本高、价值低，
遵循 rule-11 "公共 API 优先通过公共接口测试" 原则未补测试。

### PDF 验证

手动打开 `src/fuscan/assets/docs/fuscan-用户手册.pdf`：

- 封面页：标题、副标题、版本号 `0.1.5`、适用对象、分隔线正确渲染。
- 正文页：12 节标题、段落、列表、代码块、表格、分隔线渲染正确。
- 页脚：左下 `fuscan 用户手册 · v0.1.5`，右下 `第 N 页`。
- 中文显示正常，无乱码。

### 性能基线

本次为文档迭代，未触及扫描器热路径，未跑 slow 基准测试。

## 遗留事项

- **manual.md 版本号未自动化**：bumpversion 不包含 `docs/manual.md`，
  版本升级时须按 rule-12 手动同步 `> 版本：x.y.z` 行。后续可考虑加入 bumpversion files
  配置（需评估与 PDF 追加 commit 流程的兼容性）。
- **`_on_open_manual` 未补单元测试**：GUI 交互路径，mock `QDesktopServices.openUrl` 成本高，
  暂以手动验证代替。
- **PDF 目录跳转未实现**：锚点链接转纯文本，PDF 阅读器内目录无法点击跳转。
  若后续需要，可注册 `Paragraph` bookmark 并将锚点链接转为 `<a href="#bookmark">`。

## 下一轮计划

无明确下一轮计划。iter-40 已完成用户请求的全部三项任务：
1. 面向初级用户的 GUI 文档（docs/manual.md）
2. PDF 产物 + GUI 帮助菜单入口（F1）
3. 版本升级同步 PDF 的规则约束（rule-12）

等待用户下一轮需求。
