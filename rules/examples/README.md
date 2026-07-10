# 规则示例集

本目录提供多个场景化的 YAML 规则示例，覆盖安全审计、合规、DevOps、数据治理等典型场景。
可直接使用或作为编写自定义规则的参考。

## 示例文件

### 安全审计类

| 文件 | 场景 | 规则数 | 适用范围 |
|------|------|--------|---------|
| [sensitive-data.yaml](sensitive-data.yaml) | 敏感数据检测 | 5 | PII 扫描（身份证、手机号、银行卡、邮箱） |
| [security-audit.yaml](security-audit.yaml) | 凭证与密钥审计 | 9 | 硬编码密钥、私钥、JWT、数据库连接串 |
| [code-security.yaml](code-security.yaml) | 代码安全扫描 | 9 | 危险函数、调试残留、SQL 拼接 |
| [web-security.yaml](web-security.yaml) | Web 应用安全 | 10 | XSS、CORS、CSP、Cookie 安全 |
| [dependency-audit.yaml](dependency-audit.yaml) | 依赖安全审计 | 8 | 风险包、版本锁定、SNAPSHOT 依赖 |

### 合规与治理类

| 文件 | 场景 | 规则数 | 适用范围 |
|------|------|--------|---------|
| [compliance.yaml](compliance.yaml) | 合规审计 | 7 | 明文密码、未脱敏数据、凭证文件 |
| [privacy-gdpr.yaml](privacy-gdpr.yaml) | 隐私合规 | 10 | GDPR、个保法、PII、特殊类别数据 |
| [data-classification.yaml](data-classification.yaml) | 数据分类标记 | 8 | 公开/内部/机密/绝密分级 |
| [ip-protection.yaml](ip-protection.yaml) | 知识产权保护 | 9 | 源码泄露、机密文档、版权缺失 |

### 运维与基础设施类

| 文件 | 场景 | 规则数 | 适用范围 |
|------|------|--------|---------|
| [log-analysis.yaml](log-analysis.yaml) | 日志分析 | 8 | 错误日志、异常堆栈、慢查询、OOM |
| [devops-ci.yaml](devops-ci.yaml) | DevOps/CI 审计 | 8 | Dockerfile、GitHub Actions、K8s 配置 |
| [infrastructure-as-code.yaml](infrastructure-as-code.yaml) | IaC 安全 | 10 | Terraform、K8s、Ansible、CloudFormation |

**合计**：13 个文件，106 条规则（含 [example.yaml](../example.yaml) 的 5 条基础示例）。

## 使用方法

```bash
# 校验规则文件
uniscan rules -r rules/examples/security-audit.yaml

# 使用指定规则集扫描
uniscan scan /path/to/project -r rules/examples/security-audit.yaml

# 输出 JSON 报告
uniscan scan /path/to/project -r rules/examples/sensitive-data.yaml -o json -f report.json

# 托盘驻守模式（监控新增文件）
uniscan tray -r rules/examples/security-audit.yaml -w /path/to/watch
```

## 规则配置字段详解

### 顶层结构

```yaml
version: "1.0"           # 规则版本号
ignore_dirs:             # 全局忽略目录名（匹配路径任一部分）
  - .git
  - node_modules
ignore_extensions:       # 全局忽略扩展名（不含点）
  - pyc
  - pyo
rules:                   # 规则列表
  - name: 规则名称         # 必填，唯一标识
    description: 描述     # 可选，说明规则意图
    severity: warning    # 可选，默认 info；info/warning/critical
    file_extensions:     # 可选，限定扫描的扩展名（不设则扫描所有文件）
      - py
      - js
    match: {...}         # 必填，匹配条件
```

### 匹配条件（match）

#### 叶子匹配（单字段）

```yaml
match:
  type: filename          # filename | content | path
  mode: contains          # contains | equals | startswith | endswith | regex
  pattern: password       # 匹配模式（regex 时为正则表达式）
  case_sensitive: false   # 可选，默认 false
```

- `filename`：仅匹配文件名（如 `config.yaml`）
- `content`：匹配文件提取后的文本内容（支持 PDF/DOCX/XLSX 等多格式）
- `path`：匹配完整路径字符串（如 `/home/user/project/src/app.py`）

#### 逻辑组合

```yaml
# AND：所有子条件均命中
match:
  type: and
  children:
    - { type: filename, mode: regex, pattern: '\.py$' }
    - { type: content, mode: contains, pattern: password }

# OR：任一子条件命中
match:
  type: or
  children:
    - { type: content, mode: contains, pattern: token }
    - { type: content, mode: contains, pattern: api_key }

# NOT：子条件不命中
match:
  type: not
  child:
    { type: path, mode: contains, pattern: test }
```

组合可嵌套，例如 `AND(filename + NOT(path contains test))`：

```yaml
match:
  type: and
  children:
    - type: filename
      mode: contains
      pattern: password
    - type: not
      child:
        type: path
        mode: contains
        pattern: test
```

### 严重等级（severity）

| 等级 | 含义 | 典型场景 |
|------|------|---------|
| `info` | 提示信息 | TODO 标记、版权缺失、公开数据 |
| `warning` | 警告 | 硬编码密码、配置风险、内部数据 |
| `critical` | 严重 | 密钥泄露、PII、特权容器、商业机密 |

### 匹配模式（mode）说明

| mode | 行为 | 示例 |
|------|------|------|
| `contains` | 包含子串 | `pattern: password` 匹配 `my_password_123` |
| `equals` | 完全相等 | `pattern: Dockerfile` 仅匹配名为 `Dockerfile` 的文件 |
| `startswith` | 以指定字符串开头 | `pattern: test` 匹配 `test_user.py` |
| `endswith` | 以指定字符串结尾 | `pattern: _spec.rb` 匹配 `user_spec.rb` |
| `regex` | 正则表达式 | `pattern: 'AKIA[0-9A-Z]{16}'` 匹配 AWS Key |

## 规则编写最佳实践

### 1. 限定 file_extensions 提升性能

```yaml
# 好：仅扫描代码文件
file_extensions: [py, js, ts, java]
match:
  type: content
  mode: regex
  pattern: '\beval\s*\('

# 差：扫描所有文件（含二进制、媒体文件）
match:
  type: content
  mode: regex
  pattern: '\beval\s*\('
```

### 2. 用 NOT 排除测试目录降低误报

```yaml
match:
  type: and
  children:
    - type: content
      mode: contains
      pattern: password
    - type: not
      child:
        type: path
        mode: regex
        pattern: '(test|tests|__tests__|spec)/'
```

### 3. 正则使用原始字符串避免转义问题

YAML 中正则用单引号包裹，反斜杠不需额外转义：

```yaml
# 好：单引号包裹，反斜杠原样传递
pattern: '\.(conf|ini|ya?ml)$'

# 差：双引号需转义反斜杠
pattern: "\\.(conf|ini|ya?ml)$"
```

### 4. 标量值含冒号需用引号包裹

YAML 中 `key: value: extra` 会解析失败，需引号：

```yaml
# 错误：解析失败
description: 检测 privileged: true 配置

# 正确：用引号包裹
description: "检测 privileged: true 配置"
# 或避免冒号
description: 检测 privileged=true 配置
```

### 5. 大小写敏感按需设置

```yaml
# 密钥类规则建议大小写敏感（AWS Key 固定大写）
match:
  type: content
  mode: regex
  pattern: 'AKIA[0-9A-Z]{16}'
  case_sensitive: true

# 通用关键字建议大小写不敏感
match:
  type: content
  mode: contains
  pattern: password
  case_sensitive: false  # 同时匹配 Password/PASSWORD
```

### 6. 合理使用 ignore_dirs

全局忽略 VCS、构建产物、依赖目录，避免无效扫描：

```yaml
ignore_dirs:
  - .git
  - .svn
  - __pycache__
  - node_modules
  - .venv
  - venv
  - dist
  - build
  - target
  - vendor
```

## 更多资源

- 基础示例：[example.yaml](../example.yaml)
- 代码集成示例：见 [examples/](../../examples/)
- 完整 API 文档：见 [README.md](../../README.md)
