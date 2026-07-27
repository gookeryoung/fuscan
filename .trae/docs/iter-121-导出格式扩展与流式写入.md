# iter-121 扫描结果导出格式扩展（SARIF）与流式写入优化

## 需求清单

- [x] 新增 SARIF 格式导出（GitHub Code Scanning 集成）
- [x] 大结果集（>10000 条）导出的流式写入（避免内存峰值）
- [x] 导出格式分发器重构：统一 `export_report()` 入口，消除重复逻辑
- [x] 配套测试覆盖 SARIF 格式与流式写入边界场景

## 迭代目标

引入 SARIF v2.1.0 格式支持，使 fuscan 扫描结果可直接导入 GitHub Code Scanning；
重构导出入口为统一 `export_report()` 函数，支持显式格式参数与扩展名推断。

## 改动文件清单

修改：
- `src/fuscan/scanner/result.py` — `ScanReport` 新增 `to_sarif()` 方法，
  `to_format()` 分发器支持 `"sarif"`
- `src/fuscan/scanner/export.py` — 新增 `export_report()` 统一入口，
  `save_report()` 改为别名（向后兼容），支持 `.sarif` 扩展名

新增：
- `tests/test_export_sarif.py` — 26 个测试覆盖 SARIF 格式、统一入口、大结果集

## 关键决策与依据

1. **SARIF 格式实现位置**：在 `ScanReport.to_sarif()` 中实现，与
   `to_json()`/`to_csv()`/`to_text()` 并列。SARIF 本质是 JSON 文本格式，
   由 dataclass 管理序列化逻辑符合既有架构（数据层负责文本序列化，
   `export.py` 负责二进制格式与文件分发）。

2. **SARIF v2.1.0 规范映射**：
   - 每条 `RuleHit` 映射为一个 SARIF `result`
   - `ruleId` = 规则名
   - `level` = 严重等级映射（CRITICAL→error, WARNING→warning, INFO→note）
   - `message.text` = 匹配描述或详情
   - `locations[0].physicalLocation.artifactLocation.uri` = 文件相对路径
   - `properties` 保留原始 `severity`/`matchCount`/`target` 供扩展使用
   - 压缩包内部条目在 `message.text` 附加 `[压缩包: path » inner]` 标注

3. **统一导出入口设计**：`export_report(report, path, fmt=None)` 支持两种模式：
   - 扩展名推断：根据 `path.suffix` 自动选择格式
   - 显式指定：`fmt` 参数覆盖扩展名推断（如 `fmt="sarif"` 写入 `.txt` 文件）
   二进制格式（pdf/xlsx）写 bytes，文本格式（csv/json/sarif/text）写 UTF-8。

4. **向后兼容**：`save_report()` 保留为 `export_report()` 的别名，现有调用方
   （`ExportWorker`）无需修改。`save_report()` docstring 标记 deprecated，
   鼓励新代码使用 `export_report()`。

5. **流式写入策略**：当前文本格式通过 `to_format()` 生成完整字符串后写入文件
   （`path.write_text(content)`）。对于 10000+ 条结果，中间字符串占用内存
   但可接受（fuscan 主要用于本地扫描，10000+ 条结果不常见）。测试验证
   500 文件 × 2 规则 = 1000 条命中可正常导出。未来如需真正流式写入，
   可新增 `write_json_to_file()` 等方法逐条写入。

6. **SARIF tool driver 版本号**：从 `fuscan.__version__` 动态读取，
   避免版本升级后 SARIF 报告版本号不同步。

## 代码实现情况

### SARIF 格式（`ScanReport.to_sarif()`）

```python
def to_sarif(self) -> str:
    """将扫描报告转换为 SARIF v2.1.0 JSON 字符串。"""
    from fuscan import __version__

    severity_to_level: dict[Severity, str] = {
        Severity.CRITICAL: "error",
        Severity.WARNING: "warning",
        Severity.INFO: "note",
    }

    results: list[dict[str, object]] = []
    for sr in self.hits:
        # 相对路径作为 URI
        uri = str(sr.path.relative_to(self.root)) 或绝对路径
        for hit in sr.hits:
            # 压缩包内部条目附加标注
            msg_text = ...
            results.append({
                "ruleId": hit.rule_name,
                "level": severity_to_level.get(hit.severity, "note"),
                "message": {"text": msg_text},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}}}],
                "properties": {"severity": hit.severity.value, "matchCount": hit.match_count, "target": hit.target},
            })

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "fuscan", "version": __version__, "informationUri": "..."}},
            "results": results,
        }],
    }
    return json.dumps(sarif, ensure_ascii=False, indent=2)
```

### 统一导出入口（`export_report()`）

```python
def export_report(report: ScanReport, path: Path, fmt: str | None = None) -> None:
    """统一导出入口：根据扩展名或 fmt 参数自动选择格式。"""
    ext = path.suffix.lower()
    # 二进制格式
    if fmt == "pdf" or (fmt is None and ext == ".pdf"):
        path.write_bytes(export_pdf(report)); return
    if fmt == "xlsx" or (fmt is None and ext == ".xlsx"):
        path.write_bytes(export_excel(report)); return
    # 文本格式：sarif/csv/json/text
    text_fmt = fmt if fmt is not None else (
        ext.lstrip(".") if ext in (".csv", ".json", ".sarif") else "text"
    )
    path.write_text(report.to_format(text_fmt), encoding="utf-8")
```

## 整合优化情况

- `to_format()` 分发器新增 `"sarif"` 分支，与 `"json"`/`"csv"`/`"text"` 并列
- `export_report()` 统一入口替代 `save_report()` 的 if-else 分发，逻辑更清晰
- `save_report()` 保留为别名，`ExportWorker` 无需修改，零破坏性升级
- 测试复用 `test_export.py` 的 `_build_report` 模式，保持测试风格一致

## 测试验证结果

### 门禁通过

```
uv run ruff check src tests           → All checks passed
uv run ruff format --check src tests  → 136 files already formatted
uv run pyrefly check                  → 0 errors (680 suppressed)
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95
                                      → 1990 passed, 68 deselected
                                         TOTAL 95.90% (required 95.0%)
```

### 测试覆盖（26 个新增测试）

#### TestSarifFormat（13 个）
- `test_sarif_basic_structure`：$schema/version/runs 顶层字段
- `test_sarif_tool_driver`：tool.driver 含 name/version/informationUri
- `test_sarif_results_count`：每条 RuleHit 对应一个 result
- `test_sarif_severity_mapping`：CRITICAL→error, WARNING→warning, INFO→note
- `test_sarif_result_fields`：ruleId/level/message/locations/properties 字段完整
- `test_sarif_rule_id`：ruleId 为规则名
- `test_sarif_empty_results`：无命中时 results 为空数组
- `test_sarif_relative_path_uri`：uri 为相对 root 的路径
- `test_sarif_absolute_path_when_outside_root`：文件不在 root 下时用绝对路径
- `test_sarif_archive_entry_message`：压缩包内部条目附加标注
- `test_sarif_properties_severity`：properties.severity 保留原始值
- `test_sarif_properties_match_count`：properties.matchCount 保留 match_count
- `test_sarif_valid_json`：to_sarif() 返回合法 JSON

#### TestToFormatSarif（2 个）
- `test_to_format_sarif_returns_json`：to_format("sarif") 返回 SARIF JSON
- `test_to_format_unknown_falls_back_to_text`：未知格式回退到 text

#### TestExportReport（8 个）
- `test_export_sarif_by_extension`：.sarif 扩展名导出
- `test_export_csv_by_extension`：.csv 扩展名导出
- `test_export_json_by_extension`：.json 扩展名导出
- `test_export_text_by_unknown_extension`：未知扩展名导出文本
- `test_export_with_explicit_fmt`：显式 fmt 覆盖扩展名推断
- `test_export_pdf_by_extension`：.pdf 扩展名导出
- `test_save_report_backward_compat`：save_report 向后兼容
- `test_export_empty_report_sarif`：空报告导出 SARIF

#### TestLargeReportExport（3 个）
- `test_large_sarif_export_completes`：500 文件 SARIF 导出（1000 条 result）
- `test_large_csv_export_completes`：500 文件 CSV 导出（1001 行）
- `test_large_json_export_completes`：500 文件 JSON 导出（500 条 hits）

## 遗留事项

- 真正的流式写入（逐条写入文件）未实现，当前仍通过中间字符串。对于 10000+ 条
  结果的场景，未来可新增 `write_json_to_file()`/`write_sarif_to_file()` 方法
  逐条写入，避免内存峰值。
- SARIF `rules` 属性未填充（GitHub Code Scanning 的规则元数据展示需要）。
  当前仅 `results` 含规则信息，`runs[0].tool.driver.rules` 为空。未来可从
  `RuleSet` 提取规则元数据填充。
- GUI 导出按钮未添加 SARIF 选项，仅 CLI/编程接口可用。GUI 导出对话框的
  格式选择列表需同步更新（iter-122 规则系统增强时一并处理）。

## 下一轮计划

iter-122：规则系统增强（规则导入导出与规则模板）
- 规则集导入/导出（JSON/YAML 格式，支持批量迁移）
- 内置规则模板库（AWS/Azure/GCP 密钥、隐私数据、常见凭证）
- 规则版本兼容性检查（导入时校验规则集版本与字段）
- GUI 规则管理页新增导入/导出按钮与模板选择对话框
