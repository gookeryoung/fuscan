# 规则示例集

本目录提供多个场景化的 YAML 规则示例，可直接使用或作为编写自定义规则的参考。

## 示例文件

| 文件 | 场景 | 适用范围 |
|------|------|---------|
| [sensitive-data.yaml](sensitive-data.yaml) | 敏感数据检测 | 金融、医疗、政府等 PII 扫描 |
| [security-audit.yaml](security-audit.yaml) | 安全审计 | 上线前检查、CI/CD 集成、代码审计 |
| [code-security.yaml](code-security.yaml) | 代码安全扫描 | 代码评审、CI 门禁 |
| [log-analysis.yaml](log-analysis.yaml) | 日志分析 | 运维巡检、故障排查 |
| [compliance.yaml](compliance.yaml) | 合规审计 | GDPR、等保、PCI-DSS 自检 |

## 使用方法

```bash
# 校验规则文件
pyfilescan rules -r rules/examples/security-audit.yaml

# 使用指定规则集扫描
pyfilescan scan /path/to/project -r rules/examples/security-audit.yaml

# 输出 JSON 报告
pyfilescan scan /path/to/project -r rules/examples/sensitive-data.yaml -o json -f report.json

# 托盘驻守模式（监控新增文件）
pyfilescan tray -r rules/examples/security-audit.yaml -w /path/to/watch
```

## 规则编写要点

### 匹配模式（mode）

- `contains`：包含子串
- `equals`：完全相等
- `startswith`：以指定字符串开头
- `endswith`：以指定字符串结尾
- `regex`：正则表达式匹配

### 匹配目标（type）

- `filename`：仅匹配文件名
- `content`：匹配文件提取后的文本内容
- `path`：匹配完整路径字符串
- `and` / `or` / `not`：逻辑组合

### 严重等级（severity）

- `info`：提示信息
- `warning`：警告
- `critical`：严重（需立即处理）

### 最佳实践

1. **限定 file_extensions**：缩小扫描范围，提升性能、降低误报
2. **使用 NOT 排除测试目录**：测试数据常含假密码，避免误报
3. **正则用原始字符串**：YAML 中正则用单引号包裹，避免转义问题
4. **大小写敏感**：默认 `case_sensitive: false`，密钥类规则建议设为 `true`
5. **合理使用 ignore_dirs**：全局忽略 VCS、构建产物等无关目录

## 更多示例

完整规则字段说明见 [example.yaml](../example.yaml)。
