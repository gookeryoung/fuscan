# iter-01 规则引擎与 CLI 骨架

迭代日期：2026-07-09
阶段：P0（项目骨架 + 规则引擎 + CLI）

## 本轮目标

搭建 pyfilescan 项目骨架，实现核心规则引擎（YAML 配置、AND/OR/NOT 逻辑组合、
文件名/内容/路径匹配）与 CLI 入口，为后续多格式解析、压缩文件、GUI、托盘驻守
阶段奠定基础。

## 验收标准（P0 范围）

- [x] 项目可安装（pyproject.toml + requirements.txt）
- [x] YAML 规则文件可解析为不可变数据结构
- [x] 支持文件名、内容、路径三种叶子匹配，五种模式（contains/equals/startswith/endswith/regex）
- [x] 支持 AND、OR、NOT 逻辑组合，可任意嵌套
- [x] 文件遍历器支持忽略目录、忽略扩展名、最大深度
- [x] CLI 支持 scan / rules / version / gui 四个子命令
- [x] 支持 text / json / csv 三种输出格式
- [x] 单元测试覆盖率 ≥ 80%（实际 93.08%）
- [x] ruff lint + format 全部通过

## 改动文件清单

### 配置与文档
- `pyproject.toml`：项目元数据、依赖、ruff/pytest/coverage/mypy 配置
- `requirements.txt` / `requirements-dev.txt`：运行时与开发依赖
- `.gitignore`：Python 项目通用忽略 + 项目产物
- `README.md`：项目简介与快速开始
- `rules/example.yaml`：示例规则文件（5 条规则，覆盖所有匹配类型）

### 规则子包（src/pyfilescan/rules/）
- `errors.py`：RuleError / RuleParseError / RuleLoadError 异常层次
- `model.py`：MatchTarget / MatchMode / Severity 枚举；LeafMatch / AndMatch / OrMatch / NotMatch / Rule / RuleSet 不可变 dataclass
- `parser.py`：parse_match / parse_rule / parse_ruleset / load_ruleset 解析器
- `__init__.py`：公共 API 导出

### 扫描器子包（src/pyfilescan/scanner/）
- `context.py`：FileEntry 文件元信息、MatchContext 懒加载内容、default_content_provider
- `result.py`：MatchResult / RuleHit / ScanResult / ScanStats / ScanReport 结果结构
- `matchers.py`：Matcher 抽象基类 + FileNameMatcher / ContentMatcher / PathMatcher / AndMatcher / OrMatcher / NotMatcherImpl + build_matcher 工厂
- `walker.py`：FileWalker 递归遍历器（忽略目录/扩展名/最大深度/符号链接控制）
- `scanner.py`：Scanner 扫描协调器（规则编译、文件扫描、错误统计、报告生成）
- `__init__.py`：公共 API 导出

### CLI（src/pyfilescan/）
- `cli.py`：argparse 子命令（scan/rules/version/gui）、text/json/csv 渲染、日志配置
- `__init__.py`：版本号导出
- `__main__.py`：`python -m pyfilescan` 入口
- `py.typed`：PEP 561 类型标记

### 占位子包
- `extractors/__init__.py`：P1 阶段填充
- `gui/__init__.py`：P3 阶段填充
- `watcher/__init__.py`：P4 阶段填充

### 测试（tests/）
- `conftest.py`：tmp_scan_root / sample_text_file / chdir_tmp fixture
- `test_rules_model.py`：数据模型测试（13 用例）
- `test_rules_parser.py`：解析器测试（34 用例）
- `test_context.py`：FileEntry / MatchContext / default_content_provider 测试（12 用例）
- `test_matchers.py`：匹配器与 build_matcher 测试（29 用例）
- `test_walker.py`：FileWalker 测试（10 用例）
- `test_scanner.py`：Scanner 与结果结构测试（17 用例）
- `test_cli.py`：CLI 各子命令测试（20 用例）

## 关键决策与依据

### 1. 依赖范围：放宽规范，自由选型
用户明确选择"放宽规范,自由选型"，引入 PySide2、PyYAML、watchdog、python-docx、
python-pptx、openpyxl、odfpy、pypdf、rarfile、charset-normalizer 等运行时依赖。
pyproject.toml 中 `requires-python = ">=3.8"`，但开发环境为 Python 3.13，
PySide2 在 3.13 上无法安装，P3 阶段需切换至 3.8-3.10 环境或评估迁移 PySide6。

### 2. 数据模型：frozen dataclass + tuple 字段
所有规则模型使用 `@dataclass(frozen=True)`，可变集合字段用 `tuple` 而非 `list`，
保证不可变、可哈希、线程安全。MatchSpec 使用 `Union[LeafMatch, AndMatch, OrMatch, NotMatch]`
类型别名，通过 isinstance 判别，避免 discriminated union 复杂性。

### 3. 匹配引擎：叶子匹配器 + 组合匹配器
Matcher 抽象基类定义 `matches(context)` 接口。LeafMatcher 基类封装通用模式应用逻辑，
FileNameMatcher / ContentMatcher / PathMatcher 仅覆写 `_extract_text`。
组合匹配器（AndMatcher / OrMatcher / NotMatcherImpl）通过 build_matcher 递归构造。
正则在 matcher 构造时编译，避免每次匹配重复编译。

### 4. 内容懒加载
MatchContext.content 属性首次访问时调用 content_provider，之后缓存。
ContentMatcher 触发内容读取，FileNameMatcher / PathMatcher 不触发，
避免不必要的 I/O。content_provider 可注入，为 P1 阶段格式解析预留扩展点。

### 5. 错误处理：规则级容错
_scan_entry 对每条规则单独 try/except，单条规则失败不影响其他规则评估，
失败次数累加到 ScanResult.errors。scan() 汇总到 ScanStats.errors。
OSError（文件不可访问）在 FileEntry.from_path 中静默处理，返回空元信息。

### 6. CLI 输出格式
text 格式：人类可读，含统计摘要与命中明细。
json 格式：结构化，便于程序处理，含完整统计与命中列表。
csv 格式：表格，每行一条命中规则，便于导入电子表格。
输出到文件时自动创建父目录。

### 7. ruff 规则适配
忽略 RUF001/002/003（中文全角字符）、UP006/UP045（保留 typing.List/Optional 兼容 3.8）、
PLC0415（测试中局部 import）、PLR0911（返回语句数）。target-version=py38。

## 验证结果

```
测试：135 passed in 0.72s
覆盖率：93.08%（branch coverage，阈值 80%）
ruff check：All checks passed!
ruff format：All files formatted
```

手动验证：
- `pyfilescan rules -r rules/example.yaml` 正确列出 5 条规则
- `pyfilescan scan <test_dir> -r rules/example.yaml` 正确识别敏感文件名、密钥泄露、配置文件敏感词
- `pyfilescan scan ... -o json` 输出合法 JSON
- `pyfilescan scan ... -o csv -f report.csv` 正确写入文件
- 忽略目录（.git、node_modules）与忽略扩展名（pyc）生效

## 遗留事项

1. **mypy 类型检查未运行**：开发环境未安装 mypy，且 mypy strict 模式可能对
   dataclass + Union 类型别名有较多告警，留待 P5 阶段统一处理。
2. **PySide2 兼容性**：Python 3.13 无法安装 PySide2，P3 阶段需评估：
   - 切换至 Python 3.8-3.10 开发环境
   - 或与用户协商迁移至 PySide6
3. **pre-commit 未配置**：pyproject.toml 声明了 pre-commit 依赖，但未创建
   .pre-commit-config.yaml，留待 P5 阶段。
4. **覆盖率未达 95%**：当前 93.08%，cli.py 的 gui 占位分支、scanner.py 的
   异常分支未完全覆盖。P0 阶段满足 80% 验收标准，后续阶段补全。
5. **并发扫描未实现**：Scanner 当前单线程，大目录扫描性能有限。
   P5 阶段可引入 ThreadPoolExecutor。
6. **package-data 配置**：pyproject.toml 中声明了 assets/*.png 等，但 P3 阶段
   才会创建实际资源文件。

## 下一阶段（P1）重点

- 设计 Extractor 抽象与注册机制
- 实现 PDF（pypdf）、DOCX（python-docx）、PPTX（python-pptx）、XLSX（openpyxl）、
  ODT（odfpy）、WPS、纯文本 7 种格式的内容提取器
- 集成到 Scanner 的 content_provider 链
- 补充各格式的单元测试与 fixture 文件
