# pyfilescan

通用文件扫描器：基于 YAML 规则的多格式内容扫描工具，支持 CLI/GUI 与托盘驻守。

## 特性

- **规则引擎**：YAML 配置，支持文件名、文件内容、正则表达式三种匹配模式，AND/OR/NOT 逻辑组合
- **多格式支持**：PDF、DOCX、PPTX、XLSX、ODT、ODS、WPS、纯文本
- **压缩文件**：ZIP、RAR 递归解压扫描
- **双界面**：CLI 批量扫描、PySide2 GUI 交互
- **托盘驻守**：watchdog 文件监控、忽略目录配置、增量扫描
- **跨平台**：Windows / Linux / macOS

## 安装

```bash
pip install -e ".[dev]"
```

## 快速开始

### CLI

```bash
# 使用规则文件扫描指定目录（文本格式输出到 stdout）
pyfilescan scan /path/to/scan -r rules/example.yaml

# 输出 JSON 报告到文件
pyfilescan scan /path/to/scan -r rules/example.yaml -o json -f report.json

# 输出 CSV 格式（便于 Excel 导入）
pyfilescan scan /path/to/scan -r rules/example.yaml -o csv -f report.csv

# 限制递归深度（仅扫描当前目录一层）
pyfilescan scan /path/to/scan -r rules/example.yaml --max-depth 1

# 额外忽略目录（叠加到规则文件的 ignore_dirs）
pyfilescan scan /path/to/scan -r rules/example.yaml --ignore-dir build --ignore-dir dist

# 校验规则文件格式（不执行扫描）
pyfilescan rules -r rules/example.yaml

# 启动 GUI
pyfilescan gui

# 启动托盘驻守（监控目录变更并增量扫描）
pyfilescan tray -r rules/example.yaml -w /path/to/watch --state state.json

# 显示版本
pyfilescan version
```

### GUI

```bash
pyfilescan gui
```

GUI 提供：

- 选择扫描目录与规则文件
- 实时显示扫描进度
- 命中结果表格展示（路径、规则、严重等级、详情）
- 一键导出 JSON/CSV 报告

## 规则示例

基础示例见 [rules/example.yaml](rules/example.yaml)。场景化示例见 [rules/examples/](rules/examples/)，共 12 个场景、101 条规则。

### 安全审计类

| 文件 | 场景 | 适用范围 |
|------|------|---------|
| [sensitive-data.yaml](rules/examples/sensitive-data.yaml) | 敏感数据检测 | PII 扫描（身份证、手机号、银行卡、邮箱） |
| [security-audit.yaml](rules/examples/security-audit.yaml) | 凭证与密钥审计 | 硬编码密钥、私钥、JWT、数据库连接串 |
| [code-security.yaml](rules/examples/code-security.yaml) | 代码安全扫描 | 危险函数、调试残留、SQL 拼接 |
| [web-security.yaml](rules/examples/web-security.yaml) | Web 应用安全 | XSS、CORS、CSP、Cookie 安全 |
| [dependency-audit.yaml](rules/examples/dependency-audit.yaml) | 依赖安全审计 | 风险包、版本锁定、SNAPSHOT 依赖 |

### 合规与治理类

| 文件 | 场景 | 适用范围 |
|------|------|---------|
| [compliance.yaml](rules/examples/compliance.yaml) | 合规审计 | 明文密码、未脱敏数据、凭证文件 |
| [privacy-gdpr.yaml](rules/examples/privacy-gdpr.yaml) | 隐私合规 | GDPR、个保法、PII、特殊类别数据 |
| [data-classification.yaml](rules/examples/data-classification.yaml) | 数据分类标记 | 公开/内部/机密/绝密分级 |
| [ip-protection.yaml](rules/examples/ip-protection.yaml) | 知识产权保护 | 源码泄露、机密文档、版权缺失 |

### 运维与基础设施类

| 文件 | 场景 | 适用范围 |
|------|------|---------|
| [log-analysis.yaml](rules/examples/log-analysis.yaml) | 日志分析 | 错误日志、异常堆栈、慢查询、OOM |
| [devops-ci.yaml](rules/examples/devops-ci.yaml) | DevOps/CI 审计 | Dockerfile、GitHub Actions、K8s 配置 |
| [infrastructure-as-code.yaml](rules/examples/infrastructure-as-code.yaml) | IaC 安全 | Terraform、K8s、Ansible、CloudFormation |

规则配置字段详解与编写最佳实践见 [rules/examples/README.md](rules/examples/README.md)。

## 代码集成示例

程序化使用 pyfilescan 的示例见 [examples/](examples/)：

| 脚本 | 场景 | 关键 API |
|------|------|---------|
| [basic_scan.py](examples/basic_scan.py) | 基础扫描 | `load_ruleset` / `Scanner.scan` |
| [custom_extractor.py](examples/custom_extractor.py) | 自定义提取器 | `Extractor` / `default_registry.register` |
| [incremental_scan.py](examples/incremental_scan.py) | 增量扫描 | `IncrementalScanner` / `save_state` |
| [file_monitor.py](examples/file_monitor.py) | 文件监控 | `FileMonitor` / `MonitorConfig` |
| [archive_scan.py](examples/archive_scan.py) | 压缩包扫描 | `Scanner(scan_archives=True)` |

### 最小示例

```python
from pathlib import Path
from pyfilescan.rules import load_ruleset
from pyfilescan.scanner import Scanner

ruleset = load_ruleset(Path("rules/example.yaml"))
scanner = Scanner(ruleset)
report = scanner.scan(Path("/path/to/scan"))

for result in report.hits:
    print(result.path)
    for hit in result.hits:
        print(f"  [{hit.severity.value}] {hit.rule_name}: {hit.detail}")
```

## 开发

```bash
# 运行测试
pytest

# 代码检查
ruff check src tests
ruff format src tests
mypy src/pyfilescan

# 覆盖率报告
pytest --cov=pyfilescan --cov-report=html
```

## 许可证

MIT
