# 迭代 07：提供更多典型示例

## 本轮目标

响应用户"请提供更多典型示例"的请求，补充场景化的 YAML 规则示例与 Python 代码集成示例，
覆盖敏感数据检测、安全审计、代码安全、日志分析、合规审计等典型场景。

## 改动文件清单

### 新增文件

**规则示例（rules/examples/）**

- `rules/examples/sensitive-data.yaml`：敏感数据检测（身份证、手机号、银行卡、邮箱、日志文件手机号）
- `rules/examples/security-audit.yaml`：安全审计（AWS Key、私钥、JWT、GitHub Token、硬编码密码、数据库连接串）
- `rules/examples/code-security.yaml`：代码安全（eval/exec、shell=True、SQL 拼接、print 残留、TODO、硬编码 IP、assert 残留）
- `rules/examples/log-analysis.yaml`：日志分析（ERROR 级别、Java 异常、Python Traceback、慢查询、OOM、连接异常、磁盘告警、登录失败）
- `rules/examples/compliance.yaml`：合规审计（配置明文密码、日志未脱敏卡号/身份证、生产配置密码、凭证文件、数据库导出、编辑器临时文件）
- `rules/examples/README.md`：规则示例索引与编写要点说明

**代码集成示例（examples/）**

- `examples/basic_scan.py`：基础扫描（load_ruleset + Scanner.scan + 遍历报告）
- `examples/custom_extractor.py`：自定义提取器（实现 Extractor 基类 + 注册到 default_registry）
- `examples/incremental_scan.py`：增量扫描（IncrementalScanner + save_state/load_state + scan_paths）
- `examples/file_monitor.py`：文件监控（FileMonitor + MonitorConfig + 信号处理 + 增量扫描回调）
- `examples/archive_scan.py`：压缩包扫描（Scanner(scan_archives=True) + archive_password）
- `examples/README.md`：代码示例索引与核心概念说明

### 修改文件

- `README.md`：扩充 CLI 用法示例（CSV/深度/忽略目录/rules 校验/tray/版本）、添加规则示例表格、添加代码集成示例表格、添加最小代码示例

## 关键决策与依据

1. **场景化分类而非堆砌**：将示例按使用场景（敏感数据/安全审计/代码安全/日志分析/合规）组织，
   每个场景独立成文件，便于用户按需取用。依据：用户在 worker.py 打开时提出请求，
   暗示关注后台扫描场景，但请求本身通用，故采用场景化覆盖。

2. **规则示例与代码示例分离**：YAML 规则放 `rules/examples/`，Python 脚本放 `examples/`，
   职责清晰。依据：规则是数据（可被 CLI 直接使用），脚本是代码（需程序化集成）。

3. **每个规则含 file_extensions 限定**：示例规则尽量用 `file_extensions` 缩小扫描范围，
   既提升性能又降低误报。依据：python-standards.md 性能要点"统一校验"与规则编写最佳实践。

4. **示例脚本不纳入 ruff/mypy 检查**：`pyproject.toml` 的 ruff src 仅含 `["src", "tests"]`，
   examples/ 不在检查范围。依据：示例代码以可读性为先，不强制通过 strict 类型检查。

5. **保留 examples/ 不加 __init__.py**：示例是独立脚本，非包模块。依据：避免被误识别为包。

## 验证结果

### 规则文件加载验证

```
OK  rules/example.yaml  (规则数: 5)
OK  rules/examples/sensitive-data.yaml  (规则数: 5)
OK  rules/examples/security-audit.yaml  (规则数: 9)
OK  rules/examples/code-security.yaml  (规则数: 9)
OK  rules/examples/log-analysis.yaml  (规则数: 8)
OK  rules/examples/compliance.yaml  (规则数: 7)
```

所有 6 个规则文件均能被 `load_ruleset` 正确加载，共计 43 条规则。

### Python 脚本语法验证

```
OK  examples\archive_scan.py
OK  examples\basic_scan.py
OK  examples\custom_extractor.py
OK  examples\file_monitor.py
OK  examples\incremental_scan.py
```

所有 5 个示例脚本语法正确（py_compile 通过）。

### 未跑测试与覆盖率

本轮仅新增示例文件与文档，未修改 `src/` 代码，不影响测试与覆盖率。
项目仍保持 304 个测试通过、覆盖率 87.48%。

## 遗留事项

- 示例脚本未经端到端运行验证（仅语法检查），用户实际使用时可能需根据环境调整路径
- `file_monitor.py` 的信号处理在 Windows 上仅支持 SIGINT/SIGTERM（Linux/macOS 完整支持）
- 规则示例中的正则表达式为通用启发式，实际使用时可能需根据业务调整以降低误报
