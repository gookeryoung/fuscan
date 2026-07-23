# req-25 文本分类细化

## 需求

- [x] 将"纯文本"分类（原 TextExtractor 含 57 个扩展名）细分为更具体的子分类：
  - 纯文本（txt/log）
  - 源代码（py/js/java/c/go/rs/...）
  - 配置文件（ini/yaml/toml/env/...）
  - 标记与数据（md/json/xml/html/csv/...）
  - 样式表（css/scss/sass/less）

## 验收标准

- 每个子提取器独立注册到默认注册表，独立管理扩展名子集
- GUI 勾选树按 5 个独立分类展示，各自独立勾选/取消
- `TextExtractor` 保留为基类提供提取逻辑，不再直接注册
- `TEXT_EXTENSIONS` 保留为 5 组并集，向后兼容现有引用
- 全套门禁通过：ruff/format/pyrefly/pytest（覆盖率不低于 95%）
