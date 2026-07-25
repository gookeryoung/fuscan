# iter-97 文件类型列表格式 tag 与解析速度标签

## 需求清单

- [x] 文件类型列表的五格速度指示器需有「解析速度」文字标签提示其含义
- [x] 速度指示器 hover 时通过 ToolTip 显示完整速度档次（如「T2 快速」）
- [x] 文件类型按格式显示蓝色 tag 标签（如 DOCX/PDF/XLSX）
- [x] 全套门禁通过（ruff/pyrefly/pytest/coverage）

## 迭代目标

延续 iter-95 QML 迁移后的细节打磨。设置页「文件类型」列表的 delegate 此前
仅有五格速度指示器，无文字说明，用户难以理解其含义；同时 displayName 已剥离
括号后缀（如「Word（DOCX）」→「Word」），格式信息丢失。

本次迭代：

1. 在 ExtractorListModel 新增 `formatLabel` role，从 display_name 全角括号内
   提取格式标签（如「DOCX」），无括号时回退到首扩展名大写（如「PDF」）
2. QML delegate 在 displayName 后追加蓝色 tag（`colorPrimary` 背景 +
   `colorTextOnPrimary` 文字 + `radiusSm` 圆角）显示 formatLabel
3. 五格速度指示器前加「解析速度」次要色小字标签，hover 时 ToolTip 显示
   完整速度档次文本（含描述）

## 改动文件清单

### 修改

- `src/fuscan/gui/models/extractor_model.py`
  - 新增 `_ROLE_FORMAT_LABEL = b"formatLabel"`（Qt.UserRole + 7）
  - `_PAREN_RE` 改为带捕获组的正则 `（([^）]*)）`（原 `（[^）]*）` 仅用于 sub，现需 search 提取内容）
  - `_ExtractorRow.__slots__` 新增 `format_label` 字段
  - `_ExtractorRow.__init__` 提取括号内文本（大写），无括号回退到 `extensions[0].upper()`，
    无扩展名防御性回退到 `class_name.upper()`（pragma: no cover）
  - `data()` 新增 `Qt.UserRole + 7` 分支返回 `row.format_label`

- `src/fuscan/gui/views/pages/SettingsPage.qml`
  - 「文件类型」ListView delegate 改造：
    - displayName 后追加蓝色格式 tag（Rectangle + Label，引用 `theme.colorPrimary`/
      `theme.colorTextOnPrimary`/`theme.radiusSm` 令牌，禁止硬编码色值）
    - 速度五格前加「解析速度」Label（10px，次要色）
    - 速度五格 Row 加 MouseArea（hoverEnabled + WhatsThisCursor）+ ToolTip，
      hover 300ms 后显示「解析速度：T2 快速」等完整文本
    - 布局：[CheckBox] [displayName] [蓝色tag] [弹簧] [解析速度] [五格]

- `tests/test_gui_extractor_model.py`
  - `TestRoleNames.test_role_names_contains_expected_roles` 新增 `formatLabel` 断言
  - `TestData` 新增三个测试：
    - `test_data_returns_format_label_non_empty`：所有行 formatLabel 非空且大写
    - `test_data_returns_format_label_from_paren`：DocxExtractor → "DOCX"（括号提取）
    - `test_data_returns_format_label_fallback_to_extension`：PdfExtractor → "PDF"（扩展名回退）

## 关键决策与依据

1. **formatLabel 数据源选择**：优先从 display_name 全角括号内提取，因 display_name
   由提取器作者明确标注格式（如「Word（DOCX）」「邮件（EML）」），比扩展名更符合
   用户认知（「DOCX」比「docx」更直观）。无括号时回退到首扩展名大写，覆盖 PDF/RTF/
   纯文本等场景。

2. **蓝色 tag 复用现有令牌**：背景 `theme.colorPrimary`、文字 `theme.colorTextOnPrimary`、
   圆角 `theme.radiusSm`，与 rule-12「禁止硬编码色值」一致。tag 字号 10px 加粗，
   与状态徽标视觉风格统一。

3. **解析速度标签 + ToolTip 双重提示**：五格指示器前加常驻「解析速度」小字标签
   解决「无标签提示」问题；hover ToolTip 显示完整「T2 快速」文本，进一步消除
   速度档次含义的歧义。ToolTip 延迟 300ms 避免误触闪烁。

4. **正则 `_PAREN_RE` 改为捕获组**：原 `（[^）]*）` 仅用于 `sub` 替换，现需 `search`
   提取括号内容，改为 `（([^）]*)）`。`sub` 行为不变（捕获组不影响替换），
   `search` 通过 `group(1)` 取括号内文本。

## 代码实现情况

- 后端 `extractor_model.py` 通过 `paren_match = _PAREN_RE.search(display_name)`
  优先提取括号内文本，回退到首扩展名，再防御性回退到类名。三段逻辑清晰，
  覆盖率 99%（仅 `# pragma: no cover` 兜底分支未覆盖）。
- QML delegate 使用 `Layout.fillWidth: true` 的 Item 作为弹簧，确保速度标签
  与五格指示器始终右对齐，蓝色 tag 紧跟 displayName。
- ToolTip 通过 `speedIndicatorMouseArea.containsMouse` 触发，cursorShape
  设为 `Qt.WhatsThisCursor` 提示用户此处可 hover 获取说明。

## 整合优化情况

- 无重复代码引入。`format_label` 提取逻辑内聚于 `_ExtractorRow.__init__`，
  QML 侧仅消费 role 数据，符合 rule-12「三层 MVC」分层。
- 测试覆盖括号提取与扩展名回退两种路径，确保数据源切换正确。

## 测试验证结果

- `uv run ruff check src tests`：通过
- `uv run ruff format --check src tests`：通过
- `uv run pyrefly check`：0 errors
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：1505 passed，
  覆盖率 95.62%
- `extractor_model.py` 覆盖率：99%

## 遗留事项

- 无

## 下一轮计划

- 无（待用户反馈或新需求）
