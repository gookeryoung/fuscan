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
fuscan rules -r rules/examples/security-audit.yaml

# 使用指定规则集扫描
fuscan scan /path/to/project -r rules/examples/security-audit.yaml

# 输出 JSON 报告
fuscan scan /path/to/project -r rules/examples/sensitive-data.yaml -o json -f report.json
```

## 规则配置字段详解

### 顶层结构

```yaml
version: "1.0"           # 规则版本号
ignore_paths:            # 可选，路径级 glob 过滤（相对扫描根目录，大小写不敏感）
  - "*/vendor/*"
ignore_dirs:             # 可选，目录名级忽略（任意层级，大小写不敏感）
  - "testdata"
scan_extensions:         # 可选，文件后缀白名单（小写、无前导点）
  - "py"
  - "log"
scan_params:             # 可选，扫描参数（线程/深度/大文件阈值等）
  max_workers: 8
  max_file_size: 52428800
  cache_enabled: true
whitelist:               # 可选，误报白名单（path_glob + rule_name + 备注）
  - path_glob: "*/tests/**"
    rule_name: "AWS Access Key 泄露"
    note: "测试用例中的示例密钥"
    source: "rules"
rules:                   # 规则列表
  - name: 规则名称         # 必填，唯一标识
    description: 描述     # 可选，说明规则意图
    severity: warning    # 可选，默认 info；info/warning/critical
    match: {...}         # 必填，匹配条件
```

> 注：顶层扫描配置字段（`ignore_paths`/`ignore_dirs`/`scan_extensions`/
> `scan_params`/`whitelist`）可与内置 `builtin.yaml` 合并使用，合并语义见下文
> 「全局扫描设置」章节。规则中不再支持 `file_extensions` 字段（旧规则文件中
> 保留该字段会被静默忽略）。

### 全局扫描设置

规则文件顶层支持以下全局设置字段，加载时与内置 `builtin.yaml` 按下表语义合并：

| 字段 | 类型 | 合并语义 | 默认值 |
|------|------|---------|--------|
| `ignore_paths` | `list[str]` | 取并集（用户 + 内置） | 内置 `builtin.yaml` 中预定义 |
| `ignore_dirs` | `list[str]` | 取并集（用户 + 内置） | 内置 `builtin.yaml` 中预定义（含版本控制/构建输出/IDE 缓存等） |
| `scan_extensions` | `list[str]` 或 `null` | 后者非 `null` 覆盖前者 | `null`（全选默认，所有已启用提取器支持的后缀） |
| `scan_params` | `dict` | 字段级覆盖（后者非 `null` 字段覆盖前者） | 内置 `builtin.yaml` 中预定义（max_workers=5 等） |
| `whitelist` | `list[dict]` | 取并集（按 path_glob + rule_name 去重） | `[]`（空列表） |

`scan_extensions` 取值规则：

- `null`（字段未出现）：全选默认（所有已注册且未禁用的提取器支持的后缀）
- 空 `list`（`[]`）：都不扫描
- 非空 `list`：仅扫描指定后缀（小写、无前导点）

`scan_params` 支持的字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `max_workers` | `int` | 扫描线程数（默认 5，PyO3 提取器释放 GIL） |
| `max_depth` | `int` 或 `null` | 递归深度限制（`null` = 无限深度） |
| `max_file_size` | `int` | 大文件跳过阈值（字节，`0` = 不限制） |
| `scan_archives` | `bool` | 是否扫描压缩包（ZIP/RAR） |
| `cache_enabled` | `bool` | 是否启用内容哈希缓存（二次扫描加速） |
| `perf_log_enabled` | `bool` | 是否启用性能日志 |

`whitelist` 条目字段：

| 字段 | 必填 | 说明 |
|------|------|------|
| `path_glob` | 是 | 路径 glob（如 `*/tests/**`） |
| `rule_name` | 否 | 规则名（`*` 匹配所有规则，默认 `*`） |
| `note` | 否 | 备注说明 |
| `source` | 否 | 来源（`rules` = 规则文件预定义，`runtime` = 运行时标记；默认 `rules`） |
| `created_at` | 否 | 创建时间（ISO 8601 字符串，运行时标记自动填充） |

> 注：`scan_extensions` 中列出的后缀必须由已注册且未禁用的提取器支持。
> 默认禁用的提取器：`SourceCodeExtractor`（py/js/yaml/json 等源代码与配置文件）、
> `SevenZArchiveExtractor`（7z 压缩包）。需在「设置 → 文件类型」中启用对应类别
> 才能扫描到这些后缀的文件。

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

### 1. 用 NOT 排除测试目录降低误报

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

### 2. 正则使用原始字符串避免转义问题

YAML 中正则用单引号包裹，反斜杠不需额外转义：

```yaml
# 好：单引号包裹，反斜杠原样传递
pattern: '\.(conf|ini|ya?ml)$'

# 差：双引号需转义反斜杠
pattern: "\\.(conf|ini|ya?ml)$"
```

### 3. 标量值含冒号需用引号包裹

YAML 中 `key: value: extra` 会解析失败，需引号：

```yaml
# 错误：解析失败
description: 检测 privileged: true 配置

# 正确：用引号包裹
description: "检测 privileged: true 配置"
# 或避免冒号
description: 检测 privileged=true 配置
```

### 4. 大小写敏感按需设置

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

### 5. 全局扫描设置

规则文件顶层可声明 `ignore_paths`/`ignore_dirs`/`scan_extensions`/
`scan_params`/`whitelist` 五个全局字段，加载时与内置 `builtin.yaml` 合并：

- `ignore_paths` / `ignore_dirs` / `whitelist`：取并集，用户规则叠加到内置默认之上
- `scan_extensions`：后者非 `null` 覆盖前者（用户提供的列表替换内置全选默认）
- `scan_params`：字段级覆盖（用户规则中非 `null` 字段覆盖内置对应字段）

GUI「设置 → 扫描 → 忽略目录」中维护的目录会同步到 `~/.fuscan/rules/user-scan.yaml`
的 `ignore_dirs` 字段；「设置 → 文件类型」中勾选的提取器决定 `scan_extensions`
实际生效范围（未启用提取器支持的后缀即使列出也不会被扫描）。

详见上方「全局扫描设置」章节的字段语义与合并规则表。

### 6. 场景化设置示例

每个示例文件均按场景特点配置了全局设置，可作为编写自定义规则的参考：

| 示例文件 | scan_extensions 范围 | scan_params 特点 |
|---------|---------------------|-----------------|
| `sensitive-data.yaml` | 文档 + 表格 + 日志 + 邮件 | 大文件阈值 20MB |
| `security-audit.yaml` | 源代码 + 配置 + 日志 | max_workers=8 |
| `code-security.yaml` | 源代码 + SQL | max_workers=8, max_depth=20 |
| `web-security.yaml` | 前端代码 + 模板 + 样式 | max_workers=6, max_depth=15 |
| `dependency-audit.yaml` | 依赖清单 + 构建配置 | max_workers=4, max_depth=5 |
| `compliance.yaml` | 配置 + 日志 + 文档 | max_workers=6 |
| `privacy-gdpr.yaml` | 文档 + 配置 + 代码 | max_workers=6 |
| `data-classification.yaml` | 文档 + 源代码 | max_workers=6 |
| `ip-protection.yaml` | 源代码 + 文档 | max_workers=6 |
| `log-analysis.yaml` | 日志 + 文本 | max_file_size=500MB, cache_enabled=false |
| `devops-ci.yaml` | CI/CD 配置 + 脚本 | max_workers=4, max_depth=10 |
| `infrastructure-as-code.yaml` | IaC 配置（yaml/json/tf） | max_workers=4, max_depth=15 |

## 更多资源

- 基础示例：[example.yaml](../example.yaml)
- 完整 API 文档：见 [README.md](../../README.md)