# iter-37 扫描性能优化

## 需求清单

- [x] 提高扫描速度，针对扫描热路径进行性能优化

## 迭代目标

针对扫描核心热路径（walker 遍历、matchers 匹配、archive 提取）进行性能优化，
在不改变公共 API 与测试断言阈值的前提下提升扫描吞吐量。

## 改动文件清单

- `src/fuscan/scanner/context.py`：新增 `FileEntry.from_direntry`，`from_path` 合并 stat/is_dir
- `src/fuscan/scanner/walker.py`：文件条目改用 `from_direntry`
- `src/fuscan/scanner/matchers.py`：预编译 CONTAINS 不区分大小写正则，`_apply_regex` 改迭代器
- `src/fuscan/archive/scanner.py`：`_extract_via_temp` 替换为内存版 `extract_content_from_bytes`
- `tests/test_context.py`：新增 `from_direntry` 三个测试（文件/目录/OSError）
- `tests/test_walker.py`：FakeEntry 补充 `stat()` 方法适配新接口
- `tests/test_matchers.py`：`_apply_contains` 调用更新 `compiled_ci` 参数
- `tests/test_archive.py`：`test_extract_via_temp_failure` 改 mock `extract_content_from_bytes`；
  删除已不适用的 `test_safe_unlink_permission_error`
- `benchmarks/baseline.md`：更新优化后吞吐量数据与优化记录

## 关键决策与依据

1. **DirEntry.stat() 替代 Path.stat()**：`os.scandir` 的 `DirEntry` 在 Windows 平台缓存
   stat 结果，比 `Path(entry.path).stat()` 高效。同时用 `stat.S_ISDIR(st.st_mode)` 判断目录，
   合并原 `stat()` + `is_dir()` 两次系统调用为一次。
2. **预编译 CONTAINS 正则**：原 `_apply_contains` 不区分大小写分支每次调用
   `re.finditer(re.escape(pattern), text, re.IGNORECASE)` 重复编译正则。改为在
   `LeafMatcher.__init__` 预编译并缓存。
3. **`_apply_regex` 迭代器**：原 `list(compiled.finditer(text))` 对大文本创建大列表，
   改为 `next(iterator)` 取首个 + 剩余迭代计数。
4. **archive 内存版提取**：原 `_extract_via_temp` 写临时文件再读回，而主扫描器已用
   `extract_content_from_bytes` 内存版 API。压缩包每个二进制条目产生 2 次冗余磁盘 I/O，
   直接调用内存版消除。同时收窄异常捕获为 `(ExtractorError, OSError, ValueError)` 并提升
   日志级别到 warning（顺带修复 rule-11 违规）。

## 代码实现情况

- `FileEntry.from_direntry(cls, entry: os.DirEntry[str])` 新增类方法，复用 DirEntry 缓存
- `FileEntry.from_path` 的 `is_dir=path.is_dir()` 改为 `stat_mod.S_ISDIR(st.st_mode)`
- `LeafMatcher.__init__` 新增 `_compiled_contains_ci` 字段，CONTAINS + 不区分大小写时预编译
- `_apply_contains` / `_apply_leaf` 签名增加 `compiled_ci` 参数
- `archive/scanner.py` 删除 `import os`、`import tempfile`、`_safe_unlink`、`_extract_via_temp`
- `import extract_content` 改为 `import ExtractorError, extract_content_from_bytes`

## 整合优化情况

- archive scanner 优化同时修复审查报告中的 `except Exception` + `logger.debug` 违规
  （原 `_extract_via_temp` 第 313 行），改为窄类型 + warning 级别

## 测试验证结果

- ruff check / format / pyrefly 全部通过
- 全部 1137 测试通过（含新增 3 个 from_direntry 测试）
- 覆盖率 96.28%（context.py 从 95% 提升到 100%）
- 16 个 slow benchmark 测试全部通过（性能回归断言满足）
- benchmark 对比：单线程 99.2 → 106.5 files/s（+7.4%），多线程持平

## 遗留事项

- 多线程场景提升不明显（GIL 与 I/O 竞争是主要瓶颈，syscall 减少收益被稀释）
- 审查报告中的其他性能问题（详情预览阻塞 UI 线程、`_last_events` 无界增长）未在本轮处理

## 下一轮计划

- 视用户需求处理审查报告剩余问题（如 PySide6 导入缺失、跨线程 Qt 操作等 critical 问题）
