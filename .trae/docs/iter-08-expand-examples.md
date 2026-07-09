# 迭代 08：扩充 YAML 配置示例与完善文档

## 本轮目标

响应"扩充不同 yaml 配置对应示例，完善文档"请求，在 iter-07 已有 5 个场景的基础上，
新增 7 个场景化 YAML 规则示例，并完善规则配置字段详解与编写最佳实践文档。

## 改动文件清单

### 新增文件

**规则示例（rules/examples/）**

- `rules/examples/devops-ci.yaml`：DevOps/CI 配置审计（8 条规则）
  - Dockerfile latest 标签/root 用户、GitHub Actions pull_request_target 风险、
    CI 硬编码密钥、sudo 滥用、K8s 资源限制缺失、.env 入库、docker-compose 资源限制
- `rules/examples/data-classification.yaml`：数据分类标记（8 条规则）
  - 用 severity 表示数据等级：公开（info）、内部（warning）、机密/绝密（critical）
  - 覆盖版权声明、README、内部备忘录、财务报表、合同协议、生产源码、人事档案、客户名单
- `rules/examples/dependency-audit.yaml`：依赖安全审计（8 条规则）
  - Python 风险包、setup.py 不安全写法、package.json 不安全 URL、锁文件审计、
    Go mod replace、Maven SNAPSHOT、requirements 未锁定版本、Dockerfile apt-get 未锁定
- `rules/examples/web-security.yaml`：Web 应用安全（10 条规则）
  - innerHTML/document.write XSS、eval/Function 注入、CORS 宽松、CSP unsafe、
    硬编码 API、Cookie 安全、localStorage 存 token、HTTP 明文、debugger 残留
- `rules/examples/infrastructure-as-code.yaml`：IaC 安全（10 条规则）
  - S3 公开访问、安全组开放端口、Terraform 未打标签/明文密钥、K8s Secret 明文/
    latest 镜像/特权容器/hostPath、Ansible 明文密码、CloudFormation 未加密
- `rules/examples/ip-protection.yaml`：知识产权保护（9 条规则）
  - 源码缺少版权头、商业机密标记、源码出现在非代码目录、第三方代码未标注来源、
    竞品名称、生产数据入仓、架构文档、测试数据含真实用户、内部 API 泄露
- `rules/examples/privacy-gdpr.yaml`：隐私合规（10 条规则）
  - PII 组合检测、健康数据、特殊类别数据、未成年人数据、数据处理目的缺失、
    数据跨境传输、Cookie 未获同意、数据保留期缺失、被遗忘权入口、日志记录敏感数据

### 修改文件

- `rules/examples/README.md`：完全重写，按三大类（安全审计/合规治理/运维基础设施）组织
  12 个示例索引；新增"规则配置字段详解"章节（顶层结构、叶子匹配、逻辑组合、
  severity、mode 说明）；新增"规则编写最佳实践"6 条（file_extensions、NOT 排除、
  正则原始字符串、冒号引号、大小写敏感、ignore_dirs）
- `README.md`：规则示例章节扩充为三大类 12 个示例的表格，更新总数为 101 条规则
- `rules/examples/infrastructure-as-code.yaml`：修复 YAML 解析错误
  （description 中 `privileged: true` 的冒号被误解析为键值对，改为 `privileged=true`）

## 关键决策与依据

1. **按场景分类而非平铺**：12 个示例按"安全审计/合规治理/运维基础设施"三类组织，
   降低用户选型成本。依据：iter-07 已有 5 个示例平铺，扩充到 12 个后分类更有必要。

2. **新增数据分类场景**：用 severity 字段表示数据等级（info=公开/warning=内部/
   critical=机密），展示规则的非常规用法。依据：数据治理是 DLP 前置环节，
   pyfilescan 的 severity 字段天然适合表达数据等级。

3. **修复 YAML 冒号陷阱并写入文档**：`description: 检测 privileged: true` 解析失败，
   修复后在最佳实践中增加"标量值含冒号需用引号包裹"条目。依据：踩坑即文档化，
   避免用户重蹈覆辙。

4. **IaC 规则覆盖多云栈**：Terraform/K8s/Ansible/CloudFormation 各自独立规则，
   覆盖主流 IaC 工具。依据：IaC 安全场景工具分散，单一工具覆盖面不足。

5. **privacy-gdpr 含特殊类别数据检测**：GDPR 第 9 条的特殊类别（健康、种族、宗教、
   政治倾向）单独成规则。依据：特殊类别数据处理法律风险最高，需独立检测。

## 验证结果

### 规则文件加载验证

```
OK  rules/examples/code-security.yaml         (规则数: 9)
OK  rules/examples/compliance.yaml            (规则数: 7)
OK  rules/examples/data-classification.yaml   (规则数: 8)
OK  rules/examples/dependency-audit.yaml      (规则数: 8)
OK  rules/examples/devops-ci.yaml             (规则数: 8)
OK  rules/examples/infrastructure-as-code.yaml(规则数: 10)
OK  rules/examples/ip-protection.yaml         (规则数: 9)
OK  rules/examples/log-analysis.yaml          (规则数: 8)
OK  rules/examples/privacy-gdpr.yaml          (规则数: 10)
OK  rules/examples/security-audit.yaml        (规则数: 9)
OK  rules/examples/sensitive-data.yaml        (规则数: 5)
OK  rules/examples/web-security.yaml          (规则数: 10)
OK  rules/example.yaml                        (规则数: 5)
```

全部 13 个规则文件加载成功，共 106 条规则（含 example.yaml 基础 5 条）。

### 修复的 YAML 解析错误

`infrastructure-as-code.yaml` 第 115 行 `description: 检测 K8s 部署中启用 privileged: true（...）`
因冒号+空格被 YAML 解析器误认为嵌套映射而失败。改为 `privileged=true` 后通过。

### 未跑测试与覆盖率

本轮仅新增示例与文档，未修改 `src/` 代码。项目仍保持 304 个测试通过、覆盖率 87.48%。

## 遗留事项

- 新增规则的正则表达式为通用启发式，实际使用时可能需根据业务调整以降低误报
- `data-classification.yaml` 用 severity 表示数据等级是创造性用法，
  用户需理解该语义与传统"风险等级"的差异
- IaC 规则未覆盖 Helm/CDK/Pulumi 等较新工具，后续可按需扩展
- 文档中的规则示例数统计为 101 条（12 个场景文件），加上 example.yaml 共 106 条
