# 迭代 26：基于哈希的扫描结果缓存

## 迭代目标

实现基于内容哈希的扫描结果缓存机制：规则文件与被扫描文件通过 SHA-256 哈希关联，
扫描结果持久化到 SQLite 数据库，后续扫描时若文件哈希与规则哈希均未变化则复用结果，
显著提升二次扫描速度。

## 改动文件清单

### 新建文件

- `src/fuscan/cache/__init__.py`：缓存子包公共 API 导出
- `src/fuscan/cache/schema.py`：SQLite schema 定义与迁移
- `src/fuscan/cache/hashes.py`：SHA-256 哈希计算与规则稳定序列化
- `src/fuscan/cache/store.py`：CacheStore 核心 CRUD（线程安全）
- `src/fuscan/cache/sources.py`：compute_source_files 公共函数（CLI/GUI 共用）

### 修改文件

- `src/fuscan/scanner/scanner.py`：Scanner 集成缓存（_scan_entry_cached/_scan_entry_uncached 分流）
- `src/fuscan/archive/scanner.py`：ArchiveScanner 集成缓存
- `src/fuscan/watcher/incremental.py`：IncrementalScanner 委托 Scanner+cache，save_state/load_state 为空操作
- `src/fuscan/watcher/tray.py`：TrayApp 接入 cache 参数，_quit 关闭 cache
- `src/fuscan/cli.py`：CLI 接入 --no-cache/--cache-path 选项与 cache 子命令（stats/clear/prune）
- `src/fuscan/config.py`：新增 cache_enabled/cache_path 字段
- `src/fuscan/gui/worker.py`：ScanWorker 加 cache/source_files 参数
- `src/fuscan/gui/main_window.py`：MainWindow 持有 CacheStore，_build_cache_context 惰性创建，closeEvent 释放
- `src/fuscan/gui/settings_dialog.py`：SettingsDialog 加缓存设置 UI（启用开关 + 路径输入）
- `tests/test_scanner.py`：Scanner 缓存测试
- `tests/test_archive.py`：ArchiveScanner 缓存测试
- `tests/test_watcher.py`：IncrementalScanner 缓存测试与异常处理测试
- `tests/test_tray.py`：TrayApp cache 集成测试
- `tests/test_cli.py`：CLI cache 子命令测试（TestCacheCommand）
- `tests/test_gui.py`：GUI 缓存集成测试（TestGuiCache + SettingsDialog 缓存测试）

## 关键决策与依据

1. **SQLite + WAL 模式**：标准库 sqlite3，无需新依赖；WAL 提升并发读写性能
2. **单条级缓存键**：(file_hash, rule_hash) 组合键，规则变更时仅使受影响的文件失效
3. **Scanner 仍读文件算哈希**：_scan_entry_cached 路径读取文件计算 SHA-256，但跳过匹配器调用；
   原因是文件内容哈希是缓存键的一部分，必须计算才能查询缓存
4. **IncrementalScanner 委托模式**：save_state/load_state 为空操作，缓存由 SQLite 持久化，
   保持 TrayApp 接口兼容
5. **compute_source_files 公共函数**：CLI 和 GUI 共用规则文件哈希计算逻辑
6. **GUI CacheStore 生命周期**：MainWindow 持有，_build_cache_context 惰性创建，
   closeEvent/settings 变更时释放
7. **CLI cache 子命令 --cache-path 位置**：通过 argparse parents 共享给 stats/clear/prune 子操作，
   支持 `cache <action> --cache-path X` 顺序

## 验证结果

- ruff check：All checks passed
- ruff format --check：67 files already formatted
- pyrefly check：0 errors (106 suppressed)
- pytest：915 passed
- coverage：96.02%（branch），达到 96% 门槛

## 遗留事项

- tray.py start() 方法（事件循环阻塞）未覆盖，需 GUI 集成测试环境
- ignore_dirs.py Windows 平台分支未覆盖（sys.platform == "win32"）
- scanner/walker.py 部分边界条件未覆盖（85%）
