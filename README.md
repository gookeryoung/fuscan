# fuscan

> 极速通用文件扫描器.

[![PyPI](https://img.shields.io/pypi/v/fuscan)](https://pypi.org/project/fuscan/)
[![CI](https://github.com/gookeryoung/fuscan/actions/workflows/ci.yml/badge.svg)](https://github.com/gookeryoung/fuscan/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Coverage](https://img.shields.io/badge/coverage-%E2%89%A596%25-brightgreen.svg)

基于 YAML 规则的多格式文件内容扫描工具，支持 CLI、GUI 与系统托盘驻守。可扫描 PDF、Office 文档、压缩包等多种格式，按文件名/内容/路径匹配并支持 AND/OR/NOT 逻辑组合。

## 特性

- **规则引擎**：YAML 配置规则，支持文件名、文件内容、路径三类匹配目标，contains/equals/regex 等多种模式，AND/OR/NOT 逻辑组合
- **多格式支持**：PDF、DOCX、PPTX、XLSX、ODT/ODS、WPS、纯文本，及 ZIP/RAR 压缩包内扫描
- **三种使用形态**：
  - CLI：`scan`/`rules`/`gui`/`tray`/`version` 子命令，支持 text/json/csv 输出
  - GUI：PySide2 GitHub Desktop 风格界面，实时进度、结果分类、详情预览、关键词高亮
  - 托盘驻守：watchdog 监控新增文件，增量扫描（mtime 跟踪），命中通知
- **内置通用规则**：随包分发 8 条安全规则，用户规则可覆盖
- **多规则合并**：支持加载多个规则文件，按顺序链式合并，后者覆盖前者同名规则

## 安装

```bash
pip install fuscan
```

或使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv add fuscan
```

GUI 与托盘功能需要 PySide2（仅支持 Python 3.8~3.10）。

## 快速上手

### CLI

```bash
# 扫描指定路径（默认使用内置通用规则）
fuscan scan /path/to/scan

# 使用自定义规则文件，输出 JSON 报告
fuscan scan /path/to/scan -r rules/custom.yaml -o json -f report.json

# 加载多个规则文件（后者覆盖前者同名规则）
fuscan scan /path/to/scan -r base.yaml -r override.yaml

# 校验规则文件格式
fuscan rules -r rules/custom.yaml

# 启动 GUI
fuscan gui

# 启动托盘驻守（监控指定目录的新增文件）
fuscan tray -w /path/to/watch -r rules/custom.yaml
```

### GUI

```bash
fuscan gui
```

GUI 提供 GitHub Desktop 风格界面：模式卡片选择扫描范围（全盘/盘符/文件夹）、规则文件列表管理、实时进度反馈（当前文件/已扫描/命中/错误/已用时）、结果分类展示与详情预览（关键词高亮）、扫描结果导出（CSV/JSON）。

### 规则配置

规则文件为 YAML 格式，详见 [rules/example.yaml](rules/example.yaml)：

```yaml
version: "1.0"

ignore_dirs:
  - .git
  - node_modules

rules:
  # 文件名匹配
  - name: 敏感文件名检测
    severity: warning
    match:
      type: filename
      mode: contains
      pattern: password

  # 内容正则匹配
  - name: AWS 密钥泄露检测
    severity: critical
    match:
      type: content
      mode: regex
      pattern: 'AKIA[0-9A-Z]{16}'

  # AND 逻辑组合：配置文件含敏感词
  - name: 配置文件敏感词
    severity: warning
    match:
      type: and
      children:
        - type: filename
          mode: regex
          pattern: '\.(conf|ini|ya?ml)$'
        - type: content
          mode: contains
          pattern: password
```

更多场景化规则示例见 [rules/examples/](rules/examples/)。

## 开发

```bash
# 安装开发依赖
uv sync --extra dev

# 全套门禁（lint + typecheck + coverage）
make check

# 运行测试
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96

# 构建 Sphinx 文档
make doc

# 多版本测试（tox）
make tox
```

更多 Make 快捷命令运行 `make help` 查看。

## 许可证

MIT