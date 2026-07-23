# req-17：扫描架构两阶段重构

## 需求来源

用户反馈：扫描解析速度需进一步优化，要求将扫描与解析拆分，文件后缀调整为全局规则而非特定规则，先搜索目录内符合条件的文件清单再执行扫描，使用并发技术提高效率。

## 需求清单

- [x] 将文件后缀过滤从规则级 `Rule.file_extensions` 提升为全局 `Config.scan_extensions`
- [x] 重构扫描架构为两阶段：阶段1单线程遍历收集文件清单，阶段2 ThreadPoolExecutor 并发扫描
- [x] GUI 设置对话框新增"扫描后缀"配置项，支持用户手动配置
- [x] ScanWorker/MainWindow 传递 `scan_extensions` 给底层 Scanner
- [x] IncrementalScanner 支持 `scan_extensions` 参数
- [x] ArchiveScanner 移除 `rule.file_extensions` 检查，压缩包内条目统一扫描
- [x] `scan_archives=True` 时 archive 文件即使不在 `scan_extensions` 中也收集
- [x] `Rule.file_extensions` 字段标记废弃（保留向后兼容，Scanner 不再读取）
- [x] 全门禁通过（ruff/pyrefly/pytest 1445 passed/coverage 96.02%）

## 验收标准

1. 全局后缀过滤生效：只扫描指定后缀的文件
2. 两阶段架构：先收集再扫描，walk 与 scan 不再争抢 I/O
3. archive 文件在 scan_archives=True 时不受 scan_extensions 限制
4. 旧规则文件中的 file_extensions 字段仍可解析（向后兼容）
5. 全部测试通过，覆盖率不低于 95%
