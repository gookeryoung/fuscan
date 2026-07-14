# fuscan 开发技能文档

归档自 iter-01 至 iter-10 的可复用模式、踩坑总结与设计决策。

## 项目架构

```
src/fuscan/
├── rules/          # YAML 规则解析与匹配模型
│   ├── model.py    # 不可变 dataclass（Rule/RuleSet/MatchSpec）
│   ├── parser.py   # YAML → 数据结构
│   └── errors.py   # 异常家族
├── scanner/        # 扫描引擎
│   ├── walker.py   # 文件遍历（ignore_dirs/extensions 过滤）
│   ├── context.py  # 扫描上下文（FileEntry/MatchContext）
│   ├── matchers.py # 匹配器（AND/OR/NOT 组合）
│   ├── result.py   # ScanResult/ScanReport/ScanStats
│   └── scanner.py  # Scanner 主类
├── extractors/     # 多格式提取器
│   ├── base.py     # Extractor ABC + ExtractorError
│   ├── registry.py # ExtractorRegistry 注册机制
│   ├── text.py     # 纯文本（charset-normalizer）
│   ├── pdf.py      # PDF（pypdf）
│   ├── office.py   # DOCX/PPTX（python-docx/python-pptx）
│   ├── spreadsheet.py  # XLSX/ODS（openpyxl/odfpy）
│   ├── odf.py      # ODT（odfpy）
│   └── wps.py      # WPS（OOXML 检测 + 复用 Office 库）
├── archive/        # 压缩文件扫描
│   ├── base.py     # ArchiveReader ABC + ArchiveEntry
│   ├── zip_reader.py   # ZIP（zipfile）
│   ├── rar_reader.py   # RAR（rarfile，惰性导入）
│   └── scanner.py  # ArchiveScanner（临时文件提取）
├── builtin/        # 内置通用规则
│   ├── __init__.py # load_with_builtin / load_builtin_ruleset
│   └── rules.yaml  # 8 条通用安全规则 + ignore 配置
├── gui/            # PySide2 GUI
│   ├── main_window.py  # MainWindow（杀毒软件风格 UI）
│   ├── worker.py       # ScanWorker(QThread，多根路径)
│   ├── detail_dialog.py # HitDetailDialog（详情对话框）
│   └── app.py          # launch() 入口
├── watcher/        # 托盘驻守与文件监控
│   ├── monitor.py      # FileMonitor（watchdog）
│   ├── incremental.py  # IncrementalScanner（mtime 跟踪）
│   ├── ignore_dirs.py  # 平台默认忽略目录
│   ├── tray.py         # TrayApp(QSystemTrayIcon)
│   └── __init__.py     # TrayApp 惰性导入
└── cli.py          # CLI 入口（scan/rules/gui/tray/version）
```

## 可复用模式

### 1. 惰性导入打破循环依赖

scanner.py 与 archive/__init__.py 存在循环：scanner 导入 archive，archive 导入 scanner.scanner。
解法：scanner.py 用 `TYPE_CHECKING` 守卫类型导入，运行时在方法内惰性导入。

```python
if TYPE_CHECKING:
    from fuscan.archive import ArchiveScanner

class Scanner:
    def scan(self, ...):
        if self._scan_archives:
            from fuscan.archive import ArchiveScanner  # 惰性导入
            self._archive_scanner = ArchiveScanner(...)
```

### 2. 包级 __getattr__ 惰性导出

watcher/__init__.py 用 `__getattr__` 延迟导入 TrayApp，使无 PySide2 环境也能 import watcher 子包。

```python
def __getattr__(name):
    if name == "TrayApp":
        from fuscan.watcher.tray import TrayApp

        return TrayApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

### 3. 提取器注册机制

ExtractorRegistry 按扩展名注册提取器实例，支持大小写不敏感查询。
第三方库在 extract() 方法内 try/except ImportError，未安装时抛 ExtractorError。

### 4. OOXML 格式检测与扩展名绕过

WPS 文件通过 ZIP 魔数（PK\x03\x04）判断是否为 OOXML 兼容格式。
提取时复制到临时文件并改扩展名（.wps→.docx），绕过 python-docx 的扩展名检查。
临时文件用 _safe_unlink 删除，处理 Windows 文件锁定。

### 5. QThread 信号测试模式

QThread 信号在无事件循环时无法传递到主线程。
用 QEventLoop + QTimer.singleShot 超时退出模式等待信号：

```python
loop = QEventLoop()
worker.finished_report.connect(loop.quit)
QTimer.singleShot(5000, loop.quit)  # 超时保护
worker.start()
loop.exec_()
```

### 6. 增量扫描的 mtime 比较

用 `abs(entry.mtime - stored_mtime) < 0.001` 而非 `==`，
避免文件系统 mtime 精度差异导致误判。状态以 path_str→mtime 持久化为 JSON。

### 7. 文件监控事件去抖

watchdog 短时间内可能触发多次事件。FileMonitor 用 Dict[Path, float] 记录
最后事件时间，dedup_interval 内同路径事件被丢弃。
TrayApp 用 QTimer singleShot + 2 秒间隔批量处理文件事件。

## 踩坑总结

### 1. charset-normalizer 短文本误判

GBK 编码的短文本（如"密码"）可能被 charset-normalizer 误判为韩文。
解法：测试中使用足够长的 GBK 文本（至少 20 字符）。

### 2. odfpy H 元素必填属性

odfpy 的 `H`（标题）元素需要 `outlinelevel` 属性，否则抛 AttributeError。
创建测试 ODT 时需：`H(outlinelevel="1", text="标题")`。

### 3. Windows chmod 限制

Windows 上 `chmod(0o000)` 不阻止文件所有者读取，无法用于测试权限错误。
解法：用不存在的文件路径测试 OSError 分支。

### 4. QThread 测试崩溃

TrayApp._full_scan 创建真实 ScanWorker(QThread) 在测试中导致
Windows STATUS_STACK_BUFFER_OVERRUN 崩溃。
解法：用 monkeypatch 替换 ScanWorker 为 FakeWorker（带 finished_report
信号的 connect 方法）。

### 5. PySide2 Python 版本限制

PySide2 仅支持 Python 3.8-3.10，Python 3.13 无法安装。
解法：创建 conda 环境 `fuscan`（Python 3.10.20）。

### 6. QApplication 跨测试污染

QApplication 是单例，多个测试模块创建 QApplication 会冲突。
解法：用 `QApplication.instance() or QApplication([])` 模式，
并设置 `QT_QPA_PLATFORM=offscreen` 支持无显示器环境。

### 7. ZIP 压缩包内未知扩展名处理

ArchiveScanner 对压缩包内未知扩展名的文件，原逻辑调用 extract_content
返回空字符串（不抛异常），导致回退到 _decode_bytes 的分支永远不执行。
解法：先检查 _has_extractor(extension)，未知扩展名直接走 _decode_bytes。

## 设计决策

### 1. 零运行时依赖原则 vs 实际需求

项目规范要求零运行时依赖，但文件扫描器需要 pypdf、python-docx、openpyxl 等
第三方库。用户确认"放宽规范，自由选型"后，引入按需的第三方依赖，但在
extract() 方法内惰性导入，未安装时优雅降级。

### 2. PySide2 vs PySide6

用户明确要求 PySide2（尽管 PySide6 是推荐选择）。PySide2 仅支持 Python 3.8-3.10，
需创建专用 conda 环境。

### 3. watchdog 文件监控

选择 watchdog 库进行文件系统监控，跨平台支持、API 简洁。
Observer 异步监控不阻塞主线程，适合托盘驻守场景。

### 4. 托盘应用的批量扫描策略

文件监控触发后不立即扫描，而是加入 pending_paths 队列，由 QTimer 2 秒后
批量扫描。避免解压大量文件时逐个扫描的开销。

### 5. 状态持久化位置

IncrementalScanner 的状态文件应保存在扫描目录外，避免被当作新文件扫描。
测试中用 `tmp_path.parent / f"state_{tmp_path.name}.json"`。

### 6. 多线程扫描用 ThreadPoolExecutor

文件扫描为 I/O 密集型任务，`ThreadPoolExecutor` 可有效并发读取文件。
`_scan_entry` 每个文件创建独立 `MatchContext`，无共享可变状态，线程安全。
压缩包内条目扫描始终单线程（`ArchiveScanner` 可能持有内部状态）。
GUI 默认 `max_workers=8`，CLI 保持单线程（兼容性优先）。

### 7. 关键词高亮用单次正则替换

`_build_preview_html` 先 `html.escape` 转义内容，再用单次 `re.sub` 插入高亮 span。
多次 `str.replace` 会破坏已插入的 HTML 标签；按关键词长度降序排列避免短词匹配到长词内部。
使用 `re.IGNORECASE` 高亮所有大小写变体。

### 8. ignore_paths glob 路径过滤

`ignore_paths` 使用 `fnmatch.fnmatch` 进行 glob 模式匹配，大小写不敏感。
匹配逻辑：检查目录相对路径是否匹配模式，同时检查 `路径 + "/x"` 是否匹配
（处理 `*/vendor/*` 等描述目录内文件的模式）。

### 9. 内置规则随包分发

内置规则文件放在 `src/fuscan/builtin/rules.yaml`，通过 `pyproject.toml`
的 `package-data` 打包，确保安装后即可使用。`load_with_builtin()` 合并内置与
用户规则（按名称覆盖，ignore 列表取并集）。

### 10. 规则合并链式覆盖

`merge_multiple_rulesets(*rulesets)` 按顺序链式合并，后者覆盖前者同名规则。
`ignore_dirs`/`ignore_extensions`/`ignore_paths` 取并集，去重保序（base 优先）。

## 踩坑总结（iter-06～10 补充）

### 8. YAML 标量值含冒号需引号包裹

`description: 检测 privileged: true` 因冒号+空格被 YAML 解析器误认为嵌套映射而失败。
解法：标量值含冒号时用引号包裹，如 `description: "检测 privileged: true"`。

### 9. pypdf 不支持创建带文本的 PDF

pypdf 只能写 PDF 不能提取文本。测试 PDF 提取器时用 mock PdfReader
模拟 pages 列表和 extract_text()，覆盖正常/异常/加密路径。

### 10. PDF 日志噪音抑制

pypdf 的 `MediaBox` 等重复定义会输出 WARNING 日志。
解法：在模块级别将 pypdf logger 设为 ERROR 级别：
`logging.getLogger("pypdf").setLevel(logging.ERROR)`。

## 设计决策（iter-06～10 补充）

### 6. 多线程扫描用 ThreadPoolExecutor 而非 ProcessPoolExecutor

文件扫描为 I/O 密集型，线程池即可提升吞吐量且无进程间通信开销。
`_scan_entry` 无共享可变状态，天然线程安全。

### 7. 规则合并策略：按名称合并，ignore 取并集

用户规则中同名规则覆盖内置规则；`ignore_dirs`/`ignore_extensions`/`ignore_paths`
取并集。降低使用门槛的同时保留用户覆盖能力。

### 8. GUI 默认加载内置规则

GUI 启动时默认加载内置规则，用户可通过复选框关闭。
降低使用门槛，用户无需准备规则文件即可开始扫描。

### 9. 详情对话框用模态 QDialog

双击结果项弹出模态 `QDialog`，而非内嵌面板。
模态对话框不干扰主窗口布局，用户可自由调整大小查看长内容。
内容预览优先用提取器（支持 PDF/DOCX 等），限制 100KB 避免阻塞 UI。

### 10. 示例按场景分类组织

规则示例按"安全审计/合规治理/运维基础设施"三类组织，而非平铺。
降低用户选型成本，每个场景独立成文件便于按需取用。
示例脚本不纳入 ruff/mypy 检查（`pyproject.toml` 的 ruff src 仅含 `["src", "tests"]`）。
