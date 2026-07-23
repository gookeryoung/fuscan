# req-18：解析器勾选区

## 需求来源

用户反馈：文件后缀需和扫描解析器挂钩，每个解析器对应配置其适用的文件类型，默认全部勾选。用户可以取消以提高扫描速度。设置整合到扫描文件夹选择下方。

## 需求清单

- [x] Extractor 基类新增 `display_name` 属性，14 个提取器实现中文名称
- [x] ExtractorRegistry 新增 `list_extractors()` 方法返回 (class_name, display_name, extensions) 列表
- [x] Config 字段 `scan_extensions` 替换为 `disabled_extractors: list[str]`
- [x] GUI 主窗口扫描路径选择下方新增"文件类型"勾选区
- [x] 勾选区从 `default_registry.list_extractors()` 动态生成，2 列 GridLayout 布局
- [x] 默认全部勾选，取消时对应文件类型不扫描
- [x] 勾选状态即时保存到 `Config.disabled_extractors` 并持久化
- [x] MainWindow `_compute_scan_extensions()` 根据勾选状态计算 scan_extensions 传给 Scanner
- [x] settings_dialog 移除 iter-71 的 scan_extensions 配置项（改为主界面勾选）
- [x] 全门禁通过（ruff/pyrefly/pytest 1450 passed/coverage 95.88%）

## 验收标准

1. 主界面扫描路径下方显示文件类型勾选区，列出全部 14 个提取器
2. 默认全部勾选，取消某提取器后对应扩展名不扫描
3. 勾选状态持久化，重启后恢复
4. 全部取消时不扫描任何文件（scan_extensions 为空元组）
5. 全部勾选时 scan_extensions 为 None（扫描所有文件，走快速路径）
