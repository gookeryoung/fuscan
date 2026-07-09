# pyfilescan

通用文件扫描器：基于 YAML 规则的多格式内容扫描工具，支持 CLI/GUI 与托盘驻守。

## 特性

- **规则引擎**：YAML 配置，支持文件名、文件内容、正则表达式三种匹配模式，AND/OR/NOT 逻辑组合
- **多格式支持**：PDF、DOCX、PPTX、XLSX、ODT、WPS、纯文本
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
# 使用默认规则扫描指定目录
pyfilescan scan /path/to/scan

# 指定规则文件
pyfilescan scan /path/to/scan -r rules/custom.yaml

# 输出 JSON 报告
pyfilescan scan /path/to/scan -r rules/custom.yaml -o json -f report.json
```

### GUI

```bash
pyfilescan gui
```

### 规则文件示例

见 [rules/example.yaml](rules/example.yaml)。

## 开发

```bash
# 运行测试
pytest

# 代码检查
ruff check src tests
ruff format src tests
mypy src/pyfilescan
```

## 许可证

MIT
