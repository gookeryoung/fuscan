# iter-60 需求清单

## 需求1：默认规则列清单+取消持久化

- [x] 内置通用规则作为一条目列在规则文件列表顶部（row 0）
- [x] 用户可勾选/取消勾选整个内置规则集（整体勾选粒度）
- [x] 取消勾选后下次启动不再自动加载内置规则（持久化到 use_builtin 配置）
- [x] 内置规则条目不可移动、不可移除（右键菜单与 Delete 快捷键跳过 row 0）

## 需求2：命中详情对话框多次打开卡滞修复

- [x] 分析卡滞根因：extract_content_with_fallback 无缓存，每次打开对话框重复提取
- [x] 新增 extractors/cache.py 提供 LRU 内容缓存（键为 path+mtime+size，最大 32 项）
- [x] DetailPanel 与 HitDetailDialog 改用 extract_content_cached
- [x] 测试 autouse fixture 清空缓存确保隔离
