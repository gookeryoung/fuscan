# iter-03 压缩文件扫描

迭代日期：2026-07-09
阶段：P2（压缩文件扫描）

## 本轮目标

实现压缩文件扫描能力，支持 ZIP/RAR 压缩包内文件的扫描，复用已有的
规则引擎与提取器链，使规则可作用于压缩包内条目，并集成到主 Scanner。

## 验收标准（P2 范围）

- [x] ArchiveReader 抽象基类与工厂注册机制
- [x] ZipReader 基于 zipfile 标准库（加密条目容错）
- [x] RarReader 基于 rarfile 库（依赖 unrar 工具）
- [x] ArchiveScanner 对压缩包内条目应用规则
- [x] 主 Scanner 集成 scan_archives 选项与 scan_archive 方法
- [x] 单元测试覆盖率 ≥ 80%（实际 81.50%）
- [x] ruff lint 全部通过

## 改动文件清单

### 压缩扫描子包（src/pyfilescan/archive/）
- `base.py`：ArchiveError、ArchiveEntry（frozen dataclass，含 display_path 属性）、
  ArchiveReader ABC、ArchiveReaderFactory、default_factory、get_reader
- `zip_reader.py`：ZipReader，基于 zipfile.ZipFile，支持加密条目密码尝试、
  损坏文件容错、上下文管理器
- `rar_reader.py`：RarReader，基于 rarfile 库，惰性导入避免无 unrar 环境
  下报错，加密条目处理
- `scanner.py`：ArchiveScanner，对压缩包内条目应用规则，通过临时文件复用
  提取器链，纯文本类条目直接解码，无提取器扩展名回退解码
- `__init__.py`：公共 API 导出，register_all() 幂等注册内置读取器

### 主扫描器集成（src/pyfilescan/scanner/）
- `scanner.py`：新增 `scan_archives`、`archive_password` 构造参数；
  `scan()` 在遇到压缩包时递归扫描内部条目；新增 `scan_archive()` 公共方法；
  archive 相关导入改为惰性以打破循环依赖

### 测试（tests/）
- `test_archive.py`：43 个测试用例，覆盖工厂注册、ArchiveEntry 属性、
  ZipReader 读写、ArchiveScanner 各规则组合、Scanner 集成、内容提取分支、
  边界情况

## 关键决策与依据

### 1. 抽象层设计
ArchiveReader ABC 定义 `list_entries()` 与 `read_entry()` 两个核心接口，
ArchiveReaderFactory 按扩展名分发，与 ExtractorRegistry 模式一致。
ArchiveEntry 为 frozen dataclass，含 `display_path` 属性展示
`archive.zip!inner/file.txt` 格式路径，便于结果定位。

### 2. 循环依赖处理
`scanner.scanner` 需要 `ArchiveScanner`，而 `archive.scanner` 需要
`scanner.context/matchers/result`。直接在 scanner.py 顶部 import
`pyfilescan.archive` 会导致循环导入（archive/__init__.py 触发
archive.scanner 加载，进而触发 scanner/__init__.py 加载，又回到 scanner.scanner）。

解决方案：
- scanner.py 中 archive 相关导入改为 TYPE_CHECKING + 函数内惰性导入
- archive/__init__.py 末尾才导入 archive.scanner，确保 base/zip_reader/rar_reader
  先注册完成

### 3. 内容提取策略
ArchiveScanner._read_entry_content 按扩展名分三种路径：
1. 纯文本类（_TEXT_EXTENSIONS 集合）：直接 `_decode_bytes` 解码
2. 有注册提取器的格式（PDF/DOCX 等）：写入临时文件复用 `extract_content`
3. 其他扩展名：回退到 `_decode_bytes` 解码

通过 `_has_extractor()` 判断是否有注册提取器，避免对无提取器的扩展名
走临时文件路径却返回空字符串的 bug。

### 4. 加密压缩包容错
ZipReader 检测 `info.flag_bits & 0x1` 判断加密条目，无密码时抛 ArchiveError
并记录 info 日志；有密码时用 `zipfile.read(pwd=...)` 尝试解密。RarReader
类似，通过 `info.needs_password` 判断。

ArchiveScanner 捕获 ArchiveError，内容返回空字符串，规则不命中，
不影响其他条目扫描。

### 5. 临时文件与 Windows 锁定
`_extract_via_temp` 使用 `tempfile.mkstemp` 创建带正确扩展名的临时文件，
立即 `os.close(fd)` 释放句柄，写入字节后调用 `extract_content`。
清理时使用 `_safe_unlink` 捕获 Windows PermissionError（某些库如 openpyxl
关闭后仍短期锁定文件）。

### 6. RarReader 环境依赖
rarfile 库需要系统安装 unrar 工具。RarReader 构造时若 rarfile 导入失败
或 unrar 缺失，抛 ArchiveError。测试中真实 RAR 文件依赖 unrar，
通过 `pytest.skip` 跳过（test_rar_not_installed_skipped）。

### 7. Scanner 集成方式
主 Scanner 新增 `scan_archives: bool = False` 参数。开启后，在 walk
遇到压缩包文件时，调用 `ArchiveScanner.scan_archive()` 展开内部条目，
结果累加到同一 ScanReport。压缩包本身也作为普通文件扫描一遍（外层规则
可作用于 .zip 文件名）。

`scan_archive(path)` 公共方法支持显式扫描单个压缩包，未开启 scan_archives
时抛 RuntimeError。

## 验证结果

```
测试：214 passed, 1 skipped in 1.94s
  - P0 规则引擎与 CLI：135
  - P1 多格式提取器：36
  - P2 压缩扫描：43（1 跳过：RAR 需 unrar 工具）
覆盖率：81.50%（branch coverage，阈值 80%）
ruff check：All checks passed!
```

手动验证：
- ZipReader：正常 ZIP 列条目、读内容；加密条目未提供密码时抛错；
  损坏 ZIP 抛 ArchiveError
- ArchiveScanner：文件名规则、内容规则、AND/OR/NOT 组合规则均作用于
  压缩包内条目；file_extensions 过滤生效
- Scanner 集成：scan_archives=True 时递归扫描 ZIP 内文件；
  scan_archives=False（默认）不递归
- 边界：空 ZIP、中文文件名、二进制条目、超大条目跳过等均正常处理

## 遗留事项

1. **RAR 测试覆盖不足**：当前环境无 unrar 工具，RarReader 真实读取逻辑
   未测试。P5 阶段可在有 unrar 的环境补充真实 RAR fixture 测试。
   rar_reader.py 覆盖率仅 34%。
2. **嵌套压缩包未支持**：当前 ArchiveScanner 不会递归扫描压缩包内的
   压缩包。如需支持，可在 _scan_entry 中检测内层压缩包并递归调用。
3. **加密 ZIP 写入受限**：标准库 zipfile 不支持写入加密 ZIP，测试通过
   mock flag_bits 模拟加密条目。真实加密 ZIP 测试需 pyzipper 或预置 fixture。
4. **大压缩包内存**：read_entry 一次性读取整个条目到内存，超大条目可能
   OOM。已有 max_entry_size=50MB 限制，但未限制压缩包整体解压大小
   （zip bomb 风险）。P5 阶段可加解压总大小限制。
5. **archive/scanner.py 覆盖率 78%**：_extract_via_temp 的 extract_content
   异常分支、_close_reader 的无 close 方法分支未覆盖，可在 P5 补充。

## 下一阶段（P3）重点

- PySide2 GUI 主窗口设计
- 规则配置可视化编辑
- 扫描进度展示与结果树形展示
- 扫描结果导出（CSV/JSON/HTML）

注意：PySide2 不支持 Python 3.13，当前环境无法安装。P3 需切换到
Python 3.8-3.10 环境或评估迁移到 PySide6（需用户确认）。
