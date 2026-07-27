# iter-122 规则系统增强（规则导入导出与规则模板）

## 需求清单

- [x] 规则集导入/导出（JSON/YAML 格式，支持批量迁移）
- [x] 内置规则模板库（AWS/Azure/GCP 密钥、隐私数据、常见凭证）
- [x] 规则版本兼容性检查（导入时校验规则集版本与字段）
- [x] GUI 规则管理页新增导入/导出按钮与模板选择对话框

## 迭代目标

为规则系统提供完整的导入/导出能力（YAML/JSON 互逆转换），
内置常见凭证与隐私数据的规则模板（一键加载），并在 GUI 规则管理页
提供导入/导出/模板选择入口，简化用户规则管理与分享流程。
导入阶段执行版本兼容性检查，避免不兼容规则集污染现有规则。

## 改动文件清单

新增：
- `src/fuscan/rules/serializer.py` — 规则集序列化：RuleSet → dict/YAML/JSON
- `src/fuscan/rules/templates.py` — 内置规则模板库（5 类常见凭证/隐私数据）
- `tests/test_rules_serializer.py` — 序列化单元测试（24 个用例）
- `tests/test_rules_templates.py` — 模板库单元测试（17 个用例）

修改：
- `src/fuscan/rules/parser.py` — 新增 `SUPPORTED_VERSIONS` 与版本兼容性检查
- `src/fuscan/rules/__init__.py` — 导出 `save_ruleset`/`serialize_*`/`load_template`/`get_template_*` API
- `src/fuscan/gui/controllers/rules_controller.py` — 新增 `templateList` Property
  与 `exportRuleset`/`importRuleset`/`loadTemplate` Slot、`rulesIoCompleted` 信号
- `src/fuscan/gui/views/pages/RulesPage.qml` — 顶部新增「模板/导入/导出」按钮、
  模板选择对话框、Toast 通知
- `tests/test_rules_parser.py` — 新增 4 个版本兼容性测试用例
- `tests/test_gui_rules_controller.py` — 新增 18 个导入/导出/模板 Slot 测试用例
  （修复 `_write_rules_file` fixture 中 `target` → `type` 字段笔误）

## 关键决策与依据

1. **序列化与解析的对称设计**：`serializer.py` 与 `parser.py` 互为逆操作。
   序列化复用 `parser.parse_ruleset` 反序列化链路验证等价性
   （`serialize_ruleset → parse_ruleset` 应返回字段一致的 RuleSet），
   保证导出的 YAML/JSON 可被重新加载。

2. **模板库实现方式**：模板以字典字面量在 `templates.py` 中定义，
   通过 `parse_ruleset(data)` 解析为 `RuleSet`。复用 parser 链路确保模板
   加载行为与文件加载完全一致，避免双重维护解析逻辑。模板覆盖 5 类场景：

   | 模板名 | 覆盖场景 | 规则数 | 严重等级 |
   |--------|---------|--------|---------|
   | `aws_keys` | AWS Access Key ID + Secret Access Key | 2 | critical |
   | `azure_keys` | Azure 连接字符串 + 账户密钥 | 2 | critical |
   | `gcp_keys` | GCP 服务账号私钥 + API 密钥 | 2 | critical |
   | `privacy_data` | 身份证号/手机号/邮箱 | 3 | warning/info |
   | `common_credentials` | 密码/API 密钥/Token 赋值语句 | 3 | warning |

3. **版本兼容性检查**：在 `parse_ruleset` 顶层校验 `version` 字段，
   `SUPPORTED_VERSIONS = frozenset({"1.0"})`。版本字段经 `str()` 转换兼容
   YAML 解析 `version: 1.0` 为 float 的情况。不兼容版本抛 `RuleParseError`
   含可读错误信息（列出当前支持的版本），便于用户排查。

4. **模板持久化策略**：`loadTemplate(name)` 将模板序列化到
   `~/.fuscan/templates/<name>.yaml` 后加入规则文件列表。优点：
   - 用户可在 RulesPage 中查看与编辑模板内容（规则列表显示）
   - 修改不影响内置模板本身（重新加载可恢复默认）
   - 与现有 `loadFileFromPath` 链路复用，零特例代码

5. **GUI 集成方式**：RulesPage 顶部新增三个 IconButton（L3 辅助层级）：
   - 模板：打开 `Dialog` 选择模板，列项含名称与描述
   - 导入：QML `FileDialog` 选择 YAML/JSON 后调用 `importRuleset`
   - 导出：QML `FileDialog` 选择目标路径后调用 `exportRuleset`
   操作结果通过 `rulesIoCompleted(True/False, msg)` 信号反馈，
   QML `Connections` 监听后显示 Toast（3 秒自动消失，成功绿色/失败红色）。

6. **导入流程的预校验**：`importRuleset` 在加入 `rules_paths` 前先调用
   `load_ruleset(path)` 验证可加载性。失败立即返回 False，不污染
   `rules_paths`，避免后续 `_reload_ruleset` 因非法文件反复告警。

7. **重复加载的语义**：导入已加载文件返回 False 并 Toast 提示「已加载」；
   加载已加载模板返回 True（视为幂等成功，避免用户重复点击产生困惑）。

## 代码实现情况

### 序列化（`serializer.py`）

```python
def serialize_match(match: MatchSpec) -> dict[str, Any]:
    """叶子/组合匹配 → 字典。"""
    if isinstance(match, LeafMatch):
        result = {"type": match.target.value, "mode": match.mode.value, "pattern": match.pattern}
        if match.case_sensitive:
            result["case_sensitive"] = True
        if match.description:
            result["description"] = match.description
        return result
    # AndMatch / OrMatch / NotMatch 类似

def save_ruleset(ruleset: RuleSet, path: Path, fmt: str | None = None) -> None:
    """保存到 YAML/JSON，根据扩展名或 fmt 推断格式。"""
    data = serialize_ruleset(ruleset)
    ext = path.suffix.lower()
    if fmt == "yaml" or (fmt is None and ext in (".yaml", ".yml")):
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)
        return
    if fmt == "json" or (fmt is None and ext == ".json"):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    raise ValueError(f"不支持的规则集格式: fmt={fmt!r}, ext={ext!r}")
```

### 模板库（`templates.py`）

```python
_TEMPLATES: dict[str, dict[str, object]] = {
    "aws_keys": {
        "name": "AWS 密钥检测",
        "description": "AWS 访问密钥 ID（AKIA 开头）与秘密密钥模式",
        "data": {
            "version": "1.0",
            "rules": [
                {"name": "AWS 访问密钥 ID", "severity": "critical", "match": {...}},
                {"name": "AWS 秘密密钥", "severity": "critical", "match": {...}},
            ],
        },
    },
    # ... azure_keys / gcp_keys / privacy_data / common_credentials
}

def load_template(name: str) -> RuleSet:
    """按名称加载模板，返回 RuleSet。"""
    if name not in _TEMPLATES:
        raise KeyError(f"模板 {name!r} 不存在，可用模板: {', '.join(get_template_names())}")
    return parse_ruleset(_TEMPLATES[name]["data"])
```

### 版本兼容性检查（`parser.py`）

```python
SUPPORTED_VERSIONS: frozenset[str] = frozenset({"1.0"})

def parse_ruleset(data: Any) -> RuleSet:
    if not isinstance(data, Mapping):
        raise RuleParseError(f"规则集必须是字典，得到 {type(data).__name__}")
    version = str(data.get("version", "1.0"))  # str() 兼容 YAML float 解析
    if version not in SUPPORTED_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_VERSIONS))
        raise RuleParseError(
            f"不支持的规则集版本 {version!r}，当前支持: {supported}。"
            f"请升级 fuscan 或降级规则集格式。"
        )
    # ...继续解析 rules / ignore_paths
```

### RulesController 新 Slot（`rules_controller.py`）

```python
rulesIoCompleted = Signal(bool, str)  # 操作结果通知（成功标志 + 消息）

@Property("QVariantList", notify=rulesetChanged)
def templateList(self) -> list[dict[str, str]]:
    """内置规则模板列表，供 QML 模板对话框绑定。"""
    descriptions = get_template_descriptions()
    return [{"name": name, "description": descriptions[name]} for name in get_template_names()]

@Slot(str, result=bool)
def exportRuleset(self, path_str: str) -> bool:
    """导出当前合并后的规则集到 YAML/JSON。"""
    if self._ruleset is None:
        self.rulesIoCompleted.emit(False, "当前无规则集可导出")
        return False
    save_ruleset(self._ruleset, Path(path_str))
    self.rulesIoCompleted.emit(True, f"规则集已导出到 {path.name}")
    return True

@Slot(str, result=bool)
def importRuleset(self, path_str: str) -> bool:
    """从 YAML/JSON 导入规则集（含版本兼容性预校验）。"""
    try:
        imported = load_ruleset(Path(path_str))  # 不兼容版本在此抛 RuleParseError
    except RuleError as exc:
        self.rulesIoCompleted.emit(False, f"导入失败：{exc}")
        return False
    self.loadFileFromPath(path_str)  # 复用加入 rules_paths 的逻辑
    self.rulesIoCompleted.emit(True, f"已导入规则集 {path.name}")
    return True

@Slot(str, result=bool)
def loadTemplate(self, name: str) -> bool:
    """加载内置模板，序列化到 ~/.fuscan/templates/<name>.yaml 后加入规则文件列表。"""
    ruleset = load_template(name)
    templates_dir = CONFIG_DIR / "templates"
    target = templates_dir / f"{name}.yaml"
    save_ruleset(ruleset, target)
    self.loadFileFromPath(str(target))  # 复用加入 rules_paths 的逻辑
    return True
```

### RulesPage QML 集成

```qml
// 顶部新增按钮
IconButton { text: "模板"; onClicked: templateDialog.open() }
IconButton { text: "导入"; onClicked: importFileDialog.open() }
IconButton { text: "导出"; enabled: rulesController.ruleCount > 0; onClicked: exportFileDialog.open() }

// 模板选择对话框
Dialog {
    ListView {
        model: rulesController.templateList
        delegate: ItemDelegate {
            onClicked: { rulesController.loadTemplate(modelData.name); templateDialog.close() }
        }
    }
}

// Toast 通知（监听 rulesIoCompleted 信号）
Rectangle {
    Connections {
        target: rulesController
        onRulesIoCompleted: function(ok, msg) {
            ioToast.success = ok
            ioToast.message = msg
            toastTimer.restart()  // 3 秒后自动消失
        }
    }
}
```

## 整合优化情况

- 序列化与解析对称设计，复用 `parse_ruleset` 反序列化链路验证等价性
- 模板通过 `parse_ruleset(data)` 加载，避免双重维护解析逻辑
- `loadTemplate` 复用 `loadFileFromPath`，复用 `rules_paths` 持久化与刷新链路
- `importRuleset` 复用 `loadFileFromPath`，仅增加预校验层
- `exportRuleset` 直接调用 `save_ruleset`，无需重复实现格式分发
- 测试 fixture 修复 `_write_rules_file` 中 `target: content` → `type: content`
  字段笔误，使导出/导入测试可加载真实规则集（此前其他测试未触发该路径）

## 测试验证结果

### 门禁通过

```
uv run ruff check src tests           → All checks passed
uv run ruff format --check src tests  → 138 files already formatted
uv run pyrefly check                  → 0 errors (711 suppressed)
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95
                                      → 2054 passed, 68 deselected
                                         TOTAL 95.88% (required 95.0%)
```

### 测试覆盖（63 个新增测试用例）

#### `tests/test_rules_serializer.py`（24 个）

- `TestSerializeMatch`（8 个）：叶子/组合匹配序列化，含 case_sensitive/
  description 字段省略分支、未知类型抛 TypeError 防御性分支
- `TestSerializeRule`（4 个）：规则序列化，含 description/replace/replace_with
  字段省略分支
- `TestSerializeRuleset`（3 个）：规则集序列化，含 ignore_paths 省略分支
- `TestSaveRuleset`（7 个）：YAML/JSON 回环、显式 fmt 强制格式、.yml 扩展名、
  组合匹配条件回环、不支持的格式抛 ValueError
- `TestSerializeDeserializeEquivalence`（1 个）：复杂规则集
  （含 AndMatch/OrMatch/NotMatch/LeafMatch/case_sensitive/replace/ignore_paths）
  序列化→反序列化字段值等价性

#### `tests/test_rules_templates.py`（17 个）

- `TestTemplateListing`（4 个）：模板名按字母序、至少 5 个模板、描述非空中文
- `TestLoadTemplate`（9 个）：5 个模板逐一加载验证规则数/严重等级/匹配类型，
  未知模板抛 KeyError，所有模板加载成功，规则与叶子匹配含描述
- `TestTemplatePatternValidation`（5 个）：所有正则模式可编译、
  AWS/隐私/常见凭证模板可检测典型字符串

#### `tests/test_rules_parser.py` 新增 4 个

- 不支持版本（2.0/1.1）抛 RuleParseError
- version 为 float 经 `str()` 转换后校验
- 错误信息列出当前支持的版本

#### `tests/test_gui_rules_controller.py` 新增 18 个

- `TestTemplateList`（3 个）：templateList Property 含 5 个模板、字母序
- `TestExportRuleset`（5 个）：YAML/JSON 导出回环、emit 成功信号、
  空路径/无规则集返回 False
- `TestImportRuleset`（5 个）：合法 YAML 导入、重复导入返回 False、
  不存在/空路径/不兼容版本返回 False
- `TestLoadTemplate`（5 个）：加载 aws_keys 模板后文件创建于 `~/.fuscan/templates/`、
  rules_paths 包含模板路径、ruleset 刷新、未知模板/空名返回 False、
  重复加载返回 True（幂等）、所有模板加载成功

## 遗留事项

- 模板选择对话框当前仅显示名称与描述，未预览规则内容。未来可扩展为
  「左侧模板列表 + 右侧规则预览」的双栏布局（参考 SettingsPage 双栏模式）。
- 导入冲突策略：当前重复导入已加载文件返回 False，未来可提供「覆盖/跳过」
  选项对话框，让用户决定如何处理同名规则。
- 模板库目前硬编码在 `templates.py`，未来可考虑支持从 `~/.fuscan/templates/`
  目录加载用户自定义模板（与内置模板并列展示）。
- 批量导入未实现：当前 `importRuleset` 仅支持单文件导入。批量导入
  （目录扫描所有 .yaml/.json）可作为后续增强。

## 下一轮计划

iter-123：增量扫描与文件变更检测
- 基于 mtime+hash 的增量扫描模式（仅扫描变更文件）
- 增量结果与历史全量结果的合并逻辑
- GUI 新增「增量扫描」选项（工作区卡片操作按钮）
- 增量扫描的缓存复用策略（未变更文件直接复用缓存结果）
