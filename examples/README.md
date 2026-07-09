# pyfilescan 代码集成示例

本目录提供程序化使用 pyfilescan 的示例脚本，覆盖常见使用场景。

## 示例列表

| 脚本 | 场景 | 关键 API |
|------|------|---------|
| [basic_scan.py](basic_scan.py) | 基础扫描 | `load_ruleset` / `Scanner.scan` |
| [custom_extractor.py](custom_extractor.py) | 自定义提取器 | `Extractor` / `default_registry.register` |
| [incremental_scan.py](incremental_scan.py) | 增量扫描 | `IncrementalScanner` / `save_state` |
| [file_monitor.py](file_monitor.py) | 文件监控 | `FileMonitor` / `MonitorConfig` |
| [archive_scan.py](archive_scan.py) | 压缩包扫描 | `Scanner(scan_archives=True)` |

## 运行方式

所有脚本均可独立运行，需先安装 pyfilescan：

```bash
pip install -e ".[dev]"
```

运行示例：

```bash
# 基础扫描
python examples/basic_scan.py /path/to/scan rules/example.yaml

# 自定义提取器
python examples/custom_extractor.py /path/to/scan rules/example.yaml

# 增量扫描
python examples/incremental_scan.py /path/to/scan rules/example.yaml

# 文件监控（Ctrl+C 停止）
python examples/file_monitor.py /path/to/watch rules/example.yaml

# 压缩包扫描
python examples/archive_scan.py /path/to/scan rules/example.yaml
```

## 核心概念

### 规则集（RuleSet）

YAML 规则文件通过 `load_ruleset` 加载为不可变 `RuleSet` 对象，包含：

- `version`：规则版本
- `ignore_dirs`：全局忽略目录
- `ignore_extensions`：全局忽略扩展名
- `rules`：规则列表（每条含 name/match/severity/file_extensions）

### 扫描器（Scanner）

`Scanner` 是核心扫描入口：

```python
from pyfilescan.rules import load_ruleset
from pyfilescan.scanner import Scanner

ruleset = load_ruleset("rules/example.yaml")
scanner = Scanner(ruleset, max_depth=None, scan_archives=False)
report = scanner.scan(Path("/path/to/scan"))
```

### 提取器注册表（ExtractorRegistry）

`default_registry` 按扩展名分发到对应提取器。注册自定义提取器：

```python
from pyfilescan.extractors import Extractor, default_registry

class MyExtractor(Extractor):
    @property
    def supported_extensions(self):
        return ("myext",)
    def extract(self, path):
        return path.read_text(encoding="utf-8")

default_registry.register(MyExtractor())
```

### 增量扫描（IncrementalScanner）

跳过 mtime 未变化的文件，适合持续监控：

```python
from pyfilescan.watcher import IncrementalScanner

scanner = IncrementalScanner(ruleset)
scanner.load_state(Path("state.json"))  # 加载历史状态
report = scanner.scan(root)             # 增量扫描
scanner.save_state(Path("state.json"))  # 持久化状态
```

### 文件监控（FileMonitor）

基于 watchdog 的实时监控：

```python
from pyfilescan.watcher import FileMonitor, MonitorConfig

config = MonitorConfig(watch_paths=[Path("/watch")], ignore_dirs=[".git"])
monitor = FileMonitor(config)
monitor.start(callback=lambda event: print(event.path))
```

## 更多示例

- YAML 规则示例：见 [rules/examples/](../rules/examples/)
- CLI 用法：见 [README.md](../README.md)
