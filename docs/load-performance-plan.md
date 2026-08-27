# fuscan 加载性能优化开发计划

> 状态：历史规划存档。文中 `register_qml_types`/`Main.qml` 等 QML 相关描述对应 Widgets 迁移前的实现，现 GUI 已改为 PySide2 + QtWidgets，相关条目仅作历史参考。

> 目标：缩短 CLI / GUI 的**进程启动到可用**时间（import 与初始化阶段），与既有"扫描热路径"优化（benchmarks/baseline.md）区分——本计划聚焦**加载期**，不涉及扫描吞吐。
> 当前基线参考：GUI 设计目标"启动到主窗口可见 < 500ms"（benchmarks/gui-baseline.md）；CLI 冷启动 `fuscan --help` / `fuscan version` 应在数百毫秒内完成。

---

## 一、现状梳理（基于代码走读）

### 1.1 CLI 启动链路（`src/fuscan/cli.py`）

`cli.py` 顶层导入（第 34-39 行）即触发重型模块级联：

```
cli.py
 └─ from fuscan.scanner import Scanner、ScanReport
     └─ scanner/__init__.py
         ├─ _helpers.py
         │    └─ from fuscan.extractors import (...)
         │        └─ extractors/__init__.py
         │             ├─ 顶层导入全部提取器类
         │             └─ register_all()  ← 实例化全部提取器并注册
         ├─ manifest.py   ← 顶层 import orjson
         ├─ matchers.py   ← 顶层从 _content_buckets import；原生引擎延迟导入 ✅
         ├─ context.py / result.py / walker.py / scanner.py
         └─ rules.model    ← via matchers
```

**问题 1（核心）**：`extractors/__init__.py` 导入 `registry.py`，`registry.py` 导入 `spreadsheet.py`，而 `spreadsheet.py` **在模块级** `from fuscan.extractors._odf_xml import ...`，`_odf_xml.py` 又**在模块级** `from lxml import etree`。

→ **lxml（libxml2 C 扩展，进程首次加载常需 100ms+）在 CLI 一启动就被强制导入**，与其余提取器"依赖在 `extract()` 方法内懒加载"的设计不一致。`_ooxml_xml.py`（DOCX/PPTX 用）已做方法内懒加载，唯独 `_odf_xml.py` 是顶层导入，属于疏漏。

**问题 2**：`scanner/manifest.py` 与 `rules/whitelist.py` 顶层 `import orjson`（带 try/except 回退）。orjson 是 C 扩展，仅用于增量清单 / 误报白名单序列化，属于低频功能，不应在启动时强制加载。回退逻辑本身没问题，但导入时机可以后置。

**问题 3**：`cli.py` 顶层 `from fuscan.benchmark import ...`、`from fuscan.export.cli_output import ...`、`from fuscan.rules import ...`。这些对 `scan` 命令必需，但对 `version` / `gui` / `cache stats` 等子命令是多余的。`_cmd_gui` 已做延迟导入（✅），但 `version` 仍需加载整条 scanner 链。

### 1.2 GUI 启动链路（`src/fuscan/app.py`）

`app.py` 顶层导入：

```
app.py
 ├─ PySide2/PySide6（回退）✅
 ├─ QtSvg（contextlib.suppress）✅
 ├─ from fuscan.gui import resources_rc   ← 664KB 编译资源模块（.pyc 反序列化）
 ├─ from fuscan.gui.controllers import AppController, SplashController, register_qml_types
 │     └─ controllers/__init__.py 顶层导入全部 9 个 controller
 └─ from fuscan.gui.theme import detect_font_families
```

**问题 4**：`controllers/__init__.py` 顶层导入全部 controller（App/Scan/Config/Rules/Workspace/Whitelist/About/FileMonitor/Splash）。`FileMonitorController` 顶层依赖 `watchdog`，`ScanController` 顶层依赖 scanner 链（进而触发问题 1 的 lxml）。GUI 启动时即便用户只用"扫描"也会加载文件监控（watchdog）依赖。

**问题 5**：`resources_rc.py` 是 664KB 的编译资源，导入即解码全部 qrc 资源（图标/字体/SVG），是 GUI 启动的固定开销。当前无法消除（qrc 机制决定），但可评估：是否全部资源都需要在启动时注册，或拆分为"启动必需"与"按需"。

### 1.3 规则加载（`rules/`）

- `builtin.py::load_builtin_ruleset` 有 `lru_cache`（✅），但 YAML 解析（`rules/parser.py`，依赖 PyYAML）在首次加载时进行，无法避免。
- PyYAML（`yaml`）本身为纯 Python，import 开销中等；可在 `_cmd_scan` 等真正需要时再导入（当前 `rules/__init__.py` 顶层即触发）。

---

## 二、优化目标（可量化）

| 指标 | 当前（估计） | 目标 |
|------|------------|------|
| CLI `fuscan version` 冷启动 | 加载整条 scanner+extractor 链 | 仅加载 `__init__`（<50ms） |
| CLI `fuscan scan` 冷启动 | 含 lxml 顶层导入 | 启动不加载 lxml/calamine/pdfium（延迟到首个对应格式文件） |
| GUI 启动到主窗口可见 | 含 lxml + watchdog 顶层加载 | 保持 < 500ms 设计目标，消除非必需重型依赖加载 |

**原则**：
1. 任何可后置到"首次使用时"的第三方重型依赖，一律延迟导入。
2. 不改变任何公共 API 与既有惰性回退语义（`fuscan_core` / `rich` / `orjson` 缺失时回退行为不变）。
3. 纯 Python 轻量导入（`typing`、`dataclasses`、标准库）不追求极致，避免过度拆分牺牲可读性。

---

## 三、开发计划（按优先级排序）

### P1（高）修复 `_odf_xml.py` 顶层 lxml 导入 → 改为方法内懒加载

- **文件**：`src/fuscan/extractors/_odf_xml.py`
- **现状**：`from lxml import etree as _etree` 在模块顶层（第 29 行）；`load_content_xml` / `iter_elements` / `element_text` 等函数被 `spreadsheet.py` 模块级调用。
- **方案**：
  1. 移除模块级 `from lxml import etree`。
  2. 新增模块级惰性占位：把 `_etree` 改为 `None`，并在各使用处通过一个小工具函数 `_get_etree()`（内部 `from lxml import etree; return etree`）获取；或将需要 `_etree` 的常量（`OfficeNS`、`TABLE_NS`、`TEXT_NS`）改为纯字符串常量（命名空间本就是字符串），仅在解析函数内延迟导入 etree。
  3. 同步确认 `spreadsheet.py` 不再因 `_odf_xml` 的顶层符号依赖而必须在 import 期触发 lxml。
- **验收**：CLI 启动时 `sys.modules` 中无 `lxml`（`import lxml` 未执行）；首次扫描到 ODS/XLSX 文件时才加载。
- **风险**：低。`_ooxml_xml.py` 已是同样模式，可对照实现。

### P2（高）`scanner` 顶层仅暴露轻量符号，重型子模块延迟加载

- **文件**：`src/fuscan/scanner/__init__.py`、`src/fuscan/scanner/_helpers.py`
- **现状**：`scanner/__init__.py` 顶层 `from ._helpers import default_extract_content...`，而 `_helpers.py` 顶层 `from fuscan.extractors import ...` 触发整条 extractor 注册链。
- **方案**：将 `_helpers.py` 中对 `extractors` 的顶层引用改为**函数内延迟 import**：
  - `default_extract_content` / `default_extract_content_with_hash` 内部再 `from fuscan.extractors import ...`。
  - `engine_for_extension` / `is_native_engine` 内部 `from fuscan.extractors import get_extractor`。
  - 这样 `scanner` 子包被 `cli.py` 顶层导入时**不再**触发 `extractors/__init__.py` 的 `register_all()` 与 lxml。
- **验收**：`import fuscan.scanner` 后 `sys.modules` 无 `fuscan.extractors`。
- **风险**：中。`extractors` 注册链从"导入期"变为"首次使用期"，需确认 `register_all()` 幂等且扫描入口（`scanner.py::Scanner`）在使用内容提供器前已触发注册。

### P3（中）`orjson` 顶层导入后置为函数内延迟导入

- **文件**：`src/fuscan/scanner/manifest.py`、`src/fuscan/rules/whitelist.py`
- **现状**：模块顶层 `try: import orjson ... except ImportError:` 定义 `_json_dumps/_json_loads`。
- **方案**：将 orjson 的导入与函数定义移到 `IncrementalManifest.save/load` 与 `Whitelist.save/load` 内部，标准库 `json` 作为纯 Python 兜底保持。可保留模块级 `_JSON_ORJSON_AVAILABLE` 标志以兼容现有调用点，但**不**在 import 期执行 `import orjson`。
- **验收**：CLI 启动不加载 orjson（除非确有需要）。
- **风险**：低。

### P4（中）CLI 顶层导入拆分：子命令级延迟

- **文件**：`src/fuscan/cli.py`
- **现状**：顶层 `from fuscan.benchmark import ...`、`from fuscan.export.cli_output import ...`、`from fuscan.scanner import ...`、`from fuscan.rules import ...`。
- **方案**：对**非通用**依赖改为在对应 handler 内延迟导入：
  - `version` / `rules` 子命令不应加载 scanner 链。
  - `scan` / `benchmark` 才需要 `Scanner`。
  - `rules` 子命令只需 `rules` 包。
  - 保留 `__version__`、`argparse` 等轻量顶层导入。
- **验收**：`fuscan version` 冷启动显著快于 `fuscan scan`；`python -c "import fuscan.cli"` 不再触发 extractors/orjson。
- **风险**：中。需保证 `build_parser()` 不依赖被后置的模块（当前 `build_parser` 只用 argparse + Path，✅ 可安全拆分）。

### P5（中）GUI 控制器导入拆分：`controllers/__init__.py` 避免全量导入

- **文件**：`src/fuscan/gui/controllers/__init__.py`（及 `app_controller.py` 内延迟聚合）
- **现状**：顶层导入全部 9 个 controller，其中 `FileMonitorController`（watchdog）与 `ScanController`（scanner 链）较重。
- **方案**：让 `controllers/__init__.py` 采用惰性 `__getattr__`（与 `gui/__init__.py` 一致），仅 `register_qml_types` 需要的类型在 `app.py` 启动早期显式导入，文件监控相关在 `AppController` 构造时再按需创建。
- **验收**：`from fuscan.gui.controllers import AppController` 不加载 watchdog。
- **风险**：中。需梳理 `app_controller.py` 构造时实际创建的子控制器，避免循环依赖。

### P6（低）GUI 资源按需拆分（可选）

- **文件**：`src/fuscan/gui/resources_rc.py`（生成物，源头为 `.qrc`）
- **方案**：评估将启动必需资源（favicon、Splash）与主界面资源拆分为两个 `.qrc`，主界面资源在 `Main.qml` 加载前再注册。此项收益取决于 `resources_rc.py` 反序列化耗时占比，需先实测。
- **风险**：高（涉及构建脚本 `scripts/build_qrc.py` 与 fspack 打包配置）。**列为探索项，不阻塞主计划**。

---

## 四、度量与验证

### 4.1 新增启动耗时度量

利用既有 `perf.py` 基础设施（`PerfTimer` / `timed` / `PerfReport` / `FUSCAN_PERF=1`），补充**导入期**度量：

- CLI：`python -X importtime -c "import fuscan.cli" 2> import.log` 用 `-X importtime` 量化各模块导入耗时，建立**优化前后**基线。
- 关键断言：`sys.modules` 快照检查 `lxml` / `orjson` / `fuscan_core` / `watchdog` 是否在启动时被加载。

### 4.2 回归测试

- 新增 `tests/test_startup_imports.py`（`slow` 标记）：
  - `import fuscan.scanner` 不触发 `lxml` / `orjson` / `fuscan.extractors`。
  - `import fuscan.cli` 不触发 lxml。
  - `import fuscan.gui.controllers` 不触发 watchdog（或在 P5 完成后）。
  - 既有 `test_benchmark.py` 的提取器速度阈值保持不变（确保懒加载未破坏功能）。

### 4.3 与既有基线衔接

- 更新 `benchmarks/baseline.md` 增加"启动/导入耗时"一节；`benchmarks/gui-baseline.md` 补充真实 `FUSCAN_PERF=1` 启动阶段实测数据（当前为理论估算）。

---

## 五、交付顺序与工作量估算

| 阶段 | 内容 | 涉及文件 | 估算 |
|------|------|---------|------|
| 阶段 1 | P1 + P2（核心：消除启动期 lxml/extractors 加载） | `_odf_xml.py`、`spreadsheet.py`、`_helpers.py`、`scanner/__init__.py` | 1-2 天 |
| 阶段 2 | P3 + P4（orjson 后置、CLI 子命令级拆分） | `manifest.py`、`whitelist.py`、`cli.py` | 1 天 |
| 阶段 3 | P5（GUI 控制器拆分） | `controllers/__init__.py`、`app_controller.py` | 1 天 |
| 阶段 4 | P6 探索 + 度量与回归测试固化 | `resources*.qrc`、`scripts/build_qrc.py`、`tests/`、`benchmarks/` | 1-2 天 |

**里程碑**：
- M1：CLI 启动不再加载 lxml / orjson（阶段 1-2 完成后，`fuscan version` 冷启动进入 <100ms 量级）。
- M2：GUI 启动消除 watchdog 顶层依赖（阶段 3 完成后）。
- M3：启动/导入性能回归测试纳入 `make check` / CI（阶段 4 完成后）。

---

## 六、风险与注意事项

1. **延迟注册竞态**：P2 将 `extractors` 注册链后置后，必须保证 `Scanner` 首次使用内容提供器前已完成 `register_all()`。建议在 `Scanner.__init__` 显式调用一次 `extractors`（惰性导入 + 幂等注册）作为安全锚点。
2. **公共 API 兼容**：`scanner/__init__.py` 的 `default_extract_content` 等符号必须仍可从顶层导入（改为函数内延迟 import 不改变导出名，✅）。
3. **orjson 回退语义**：P3 保持 `except ImportError` 回退到标准库 `json`，确保缺失依赖环境行为一致（有现有测试覆盖）。
4. **GIL/`-X importtime` 环境差异**：导入耗时受磁盘缓存、杀毒软件影响较大，度量时取多次均值，避免单次抖动。
