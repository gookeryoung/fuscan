# 清理整理项目代码与 BUG 修复（续）

## Context

fuscan 项目最近完成 iter-30~32（UI 同步、性能优化、图标主题化）。本迭代针对死代码、50MB/100MB 上限不一致、docstring 错误、异常捕获过宽、惰性导入未注释、`main_window.py` 与 `detail_dialog.py` 重复代码等问题做集中清理。

**上一会话已部分实施但中途丢失上下文**，当前仓库处于**损坏的中间状态**：`main_window.py` 顶部已 import 不存在的 `preview_utils` 模块，本地函数定义仍存在但引用了已删除的本地常量。本计划续作剩余工作并修复损坏状态。

## Current State Analysis

### 已完成（已验证存在于代码中）

- **Task A（死代码）**：`_ICON_RIGHT` 常量、`self._icon_right` 赋值、`assets/icons/right.svg` 文件均已删除（Grep 确认无残留）。
- **Task B（100MB 统一）**：
  - `scanner/scanner.py` L56：`entry.size > 100 * 1024 * 1024`
  - `scanner/context.py` L55：`max_size: int = 100 * 1024 * 1024`
- **Task C（docstring 修复）**：`cache/hashes.py` L60 已改为 `与 :func:default_extract_content_with_hash 的 100MB 上限对齐`。
- **Task D（异常细化）**：`main_window.py` L1615 `_on_open_file_location` 已改为 `except (OSError, FileNotFoundError) as exc`。

### 损坏状态（必须优先修复）

- **`main_window.py` 损坏**：
  - L97-109 已添加 `from fuscan.gui.preview_utils import (HIGHLIGHT_STYLE, KEYWORD_RE, PREVIEW_MAX_CHARS, PREVIEW_STYLE, SEVERITY_COLORS, SEVERITY_LABELS, build_keyword_to_rule_map, build_preview_html, compile_keyword_pattern, extract_keywords, format_size)`，但 `preview_utils.py` **不存在**（Glob 确认）。
  - 本地常量 `_KEYWORD_RE`/`_HIGHLIGHT_STYLE`/`_PREVIEW_STYLE`/`_PREVIEW_MAX_CHARS`/`_SEVERITY_LABELS`/`_SEVERITY_COLORS` 已删除。
  - 本地函数 `_format_size`（L218-226）/`_extract_keywords`（L229-248）/`_build_preview_html`（L251-278）/`_build_keyword_to_rule_map`（L281-302）/`_compile_keyword_pattern`（L305-314）**仍然存在**，且函数体内部引用了已删除的 `_KEYWORD_RE`/`_HIGHLIGHT_STYLE`/`_PREVIEW_STYLE`。
  - 调用点（L1379/1440/1441/1444/1455/1485/1488）仍使用 `_` 前缀名。
  - **结果**：`import fuscan.gui.main_window` 会 `ImportError`，GUI 完全不可用。

### 未完成

- **Task E（提取 preview_utils）**：`preview_utils.py` 未创建；`detail_dialog.py` 完全未修改（仍有 6 常量 + 5 函数本地定义）。
- **Task F（导入整理）**：
  - `main_window.py` L1297 `from fuscan import __version__` 仍在 `_on_about` 函数内。
  - `main_window.py` L1606-1607 `import subprocess`/`import sys` 仍在 `_on_open_file_location` 函数内。
  - `main_window.py` 惰性导入 4 处未加注释：L1093 `from fuscan.cache import CacheStore, default_cache_path`、L1097 `from fuscan.cache import compute_source_files`、L1307 `from fuscan.gui.settings_dialog import SettingsDialog`、L1680 `from fuscan.gui.rule_editor import RuleEditorDialog`。
  - `cli.py` 惰性导入 6 处未加注释：L207/269/284/302/330/337。
- **Task G（测试同步）**：`tests/test_gui.py` 有 **84 处**引用 `_format_size`/`_extract_keywords`/`_build_preview_html` 等私有函数，分别从 `fuscan.gui.detail_dialog` 和 `fuscan.gui.main_window` 导入。提取到 preview_utils 后需全部更新为公共名（去 `_` 前缀）并统一从 `fuscan.gui.preview_utils` 导入。
- **Task H（iter-33 文档）**：`.trae/docs/iter-33-代码清理与BUG修复.md` 未创建。

## Proposed Changes

### 1. 创建 `src/fuscan/gui/preview_utils.py`（Task E 核心）

新建共享模块，包含 6 常量 + 5 函数，全部使用**公共命名**（去 `_` 前缀）：

**常量**：
- `PREVIEW_MAX_CHARS = 100 * 1024`
- `KEYWORD_RE = re.compile(r"'([^']+)'")`
- `PREVIEW_STYLE`（pre 标签样式字符串）
- `HIGHLIGHT_STYLE`（高亮 span 样式字符串）
- `SEVERITY_LABELS: dict[Severity, str]`（CRITICAL→严重 / WARNING→警告 / INFO→一般）
- `SEVERITY_COLORS: dict[Severity, QColor]`（引用 `theme.COLOR_DANGER`/`COLOR_WARNING`/`COLOR_INFO`）

**函数**（从 main_window.py L218-314 复制，内部引用改为公共常量名）：
- `format_size(size: int) -> str`
- `extract_keywords(hits: Sequence[RuleHit]) -> list[str]`
- `build_preview_html(content: str, keywords: Sequence[str]) -> str`
- `build_keyword_to_rule_map(hits: Sequence[RuleHit]) -> dict[str, int]`
- `compile_keyword_pattern(kw: str) -> str`

模块结构：`from __future__ import annotations` → 标准库 → PySide（try/except 双兼容）→ `fuscan` 本地 → `__all__` → 常量 → 函数。中文 docstring 完整。

### 2. 修复 `src/fuscan/gui/main_window.py`（Task E + F）

**Task E 修复**：
- 删除本地函数定义 L218-314（`_format_size`/`_extract_keywords`/`_build_preview_html`/`_build_keyword_to_rule_map`/`_compile_keyword_pattern`）。
- 更新调用点（去 `_` 前缀）：
  - L1379 `_format_size(size)` → `format_size(size)`
  - L1440/1441 `_PREVIEW_MAX_CHARS` → `PREVIEW_MAX_CHARS`
  - L1444 `_extract_keywords` → `extract_keywords`
  - L1455 `_build_preview_html` → `build_preview_html`
  - L1485 `_build_keyword_to_rule_map` → `build_keyword_to_rule_map`
  - L1488 `_compile_keyword_pattern` → `compile_keyword_pattern`
- L97-109 的 import 保持不变（已正确）。
- 保留 `_SEVERITY_BACKGROUNDS`（L124-128）和 `_SEVERITY_RANK`（L131-135），detail_dialog.py 不需要这两个。

**Task F 顶部导入**：
- L1297 `from fuscan import __version__` 移到顶部 import 区（L91 `from fuscan import theme` 之后）。
- L1606-1607 `import subprocess`/`import sys` 移到顶部标准库 import 区（L21-32 之间）。

**Task F 惰性导入注释**（保留惰性，加 `# ` 注释说明原因）：
- L1093 `from fuscan.cache import CacheStore, default_cache_path` → `# 延迟加载 cache 模块，避免主窗口启动时初始化 SQLite`
- L1097 `from fuscan.cache import compute_source_files` → `# 同上，cache 模块按需加载`
- L1307 `from fuscan.gui.settings_dialog import SettingsDialog` → `# 延迟加载 GUI 子对话框，加速主窗口启动`
- L1680 `from fuscan.gui.rule_editor import RuleEditorDialog` → `# 延迟加载 GUI 子对话框，加速主窗口启动`

### 3. 修改 `src/fuscan/gui/detail_dialog.py`（Task E）

- 删除本地常量定义 L48-73（`_SEVERITY_LABELS`/`_SEVERITY_COLORS`/`_PREVIEW_MAX_CHARS`/`_KEYWORD_RE`/`_PREVIEW_STYLE`/`_HIGHLIGHT_STYLE`）。
- 删除本地函数定义 L76-172（`_format_size`/`_extract_keywords`/`_build_preview_html`/`_build_keyword_to_rule_map`/`_compile_keyword_pattern`）。
- 添加顶部 import：`from fuscan.gui.preview_utils import (SEVERITY_COLORS, SEVERITY_LABELS, PREVIEW_MAX_CHARS, build_keyword_to_rule_map, build_preview_html, compile_keyword_pattern, extract_keywords, format_size)`。
- 更新所有引用（去 `_` 前缀）：
  - L184 docstring `_PREVIEW_MAX_CHARS` → `PREVIEW_MAX_CHARS`
  - L232 `_format_size` → `format_size`
  - L250 `_SEVERITY_LABELS` → `SEVERITY_LABELS`
  - L252 `_SEVERITY_COLORS` → `SEVERITY_COLORS`
  - L294-295 `_PREVIEW_MAX_CHARS` → `PREVIEW_MAX_CHARS`
  - L298 `_extract_keywords` → `extract_keywords`
  - L299 `_build_preview_html` → `build_preview_html`
  - L329 `_build_keyword_to_rule_map` → `build_keyword_to_rule_map`
  - L332 `_compile_keyword_pattern` → `compile_keyword_pattern`
- 移除不再使用的顶部 import：`html`/`re`（如确认无其他引用）、`Sequence`（如确认无其他引用）。
  - **注意**：`html.escape` 在 L231/233 仍在使用，`html` 保留。
  - **注意**：`re.compile`/`re.finditer` 在删除本地函数后无其他引用，`re` 可移除。
  - **注意**：`Sequence` 在删除本地函数后无其他引用，可移除。

### 4. 修改 `src/fuscan/cli.py`（Task F 惰性导入注释）

6 处惰性导入加 `# ` 注释：
- L207 `from fuscan.cache import CacheStore, compute_source_files` → `# 仅在启用缓存时加载 SQLite 依赖`
- L269 `from fuscan.gui import launch` → `# 仅在 gui 子命令时加载 PySide`
- L284 `from fuscan.watcher.tray import TrayApp` → `# 仅在 tray 子命令时加载 PySide 与 watchdog`
- L302 `from fuscan.cache import CacheStore` → `# 仅在启用缓存时加载 SQLite 依赖`
- L330 `from fuscan.cache import default_cache_path` → `# 延迟加载避免无缓存场景的 SQLite 依赖`
- L337 `from fuscan.cache import CacheStore, default_cache_path` → `# 仅在 cache 子命令时加载 SQLite 依赖`

### 5. 同步测试 `tests/test_gui.py`（Task G）

**84 处引用**需更新，分两类：

**类 1：从 `detail_dialog` 导入的测试**（L1908-3051 区域，约 60 处）：
```python
# 旧
from fuscan.gui.detail_dialog import _format_size
# 新
from fuscan.gui.preview_utils import format_size
```

**类 2：从 `main_window` 导入的测试**（L3873-3930 区域，约 24 处）：
```python
# 旧
from fuscan.gui.main_window import _format_size
# 新
from fuscan.gui.preview_utils import format_size
```

更新策略：用 `replace_all` 批量替换导入语句，函数调用去 `_` 前缀。注意 `test_gui.py` 227KB 巨大，逐个 Edit 不现实，用 `replace_all` 按模式批量替换。

具体替换映射（`replace_all=true`）：
1. `from fuscan.gui.detail_dialog import _format_size` → `from fuscan.gui.preview_utils import format_size`
2. `from fuscan.gui.detail_dialog import _extract_keywords` → `from fuscan.gui.preview_utils import extract_keywords`
3. `from fuscan.gui.detail_dialog import _build_preview_html` → `from fuscan.gui.preview_utils import build_preview_html`
4. `from fuscan.gui.main_window import _format_size` → `from fuscan.gui.preview_utils import format_size`
5. `from fuscan.gui.main_window import _extract_keywords` → `from fuscan.gui.preview_utils import extract_keywords`
6. `from fuscan.gui.main_window import _build_preview_html` → `from fuscan.gui.preview_utils import build_preview_html`

调用点更新（`replace_all=true`，按上下文确保唯一性）：
- `_format_size(` → `format_size(`
- `_extract_keywords(` → `extract_keywords(`
- `_build_preview_html(` → `build_preview_html(`

**注意**：需先扫描 test_gui.py 是否有引用 `_build_keyword_to_rule_map`/`_compile_keyword_pattern`/`_PREVIEW_MAX_CHARS`/`_KEYWORD_RE`/`_PREVIEW_STYLE`/`_HIGHLIGHT_STYLE`/`_SEVERITY_LABELS`/`_SEVERITY_COLORS`，若有一并更新。

### 6. 创建 `.trae/docs/iter-33-代码清理与BUG修复.md`（Task H）

记录本轮清理改动清单、关键决策、验证结果。遵循 rule-01 迭代文档规范。

## Assumptions & Decisions

1. **100MB 上限统一**：iter-31 已实现流式读取（>10MB 分块解码 + `IncrementalDecoder`），100MB 配合流式读取合理。
2. **preview_utils.py 提取**：main_window.py 与 detail_dialog.py 是 100% 重复（5 函数 + 6 常量共约 100 行），任何一处修复 bug 都需同步另一处，DRY 原则优先于"三处相似才提取"。
3. **惰性导入保留 + 注释**：GUI 子模块惰性加载是为加速启动（避免主窗口初始化时加载所有子对话框），属于合理设计；保留惰性但加注释说明原因，符合 rule-11 精神。
4. **`__version__`/`subprocess`/`sys` 顶部导入**：`fuscan` 包必然已加载（main_window.py 在 `fuscan.gui` 包内）；`subprocess`/`sys` 是标准库，顶部导入无副作用。
5. **`_on_open_file_location` 异常已细化**：Task D 已完成，无需重复。
6. **测试更新用 `replace_all`**：84 处引用规模大，逐个 Edit 不现实；`replace_all` 按精确字符串匹配，安全可控。

## Verification

执行全套门禁检查（rule-11）：

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyrefly check
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96
```

预期：
- ruff：All checks passed
- ruff format：所有文件已格式化
- pyrefly：0 errors
- pytest：所有现有测试通过，覆盖率不低于 96%

额外验证：
- `python -c "from fuscan.gui.main_window import MainWindow"` 无 ImportError（确认损坏状态已修复）
- `python -c "from fuscan.gui.detail_dialog import HitDetailDialog"` 无 ImportError
- `python -c "from fuscan.gui.preview_utils import format_size, extract_keywords, build_preview_html, build_keyword_to_rule_map, compile_keyword_pattern"` 无 ImportError
- Grep 确认 `main_window.py` 与 `detail_dialog.py` 中无 `_format_size`/`_extract_keywords`/`_build_preview_html` 等私有定义残留
- Grep 确认 `tests/test_gui.py` 中无 `from fuscan.gui.detail_dialog import _` 或 `from fuscan.gui.main_window import _format_size` 等旧导入

## 实施顺序

1. 创建 `preview_utils.py`（解除损坏状态的前提）
2. 修复 `main_window.py`（删除本地函数 + 更新调用点 + 顶部导入 + 惰性导入注释）
3. 修改 `detail_dialog.py`（删除本地定义 + 添加 import + 更新引用）
4. 修改 `cli.py`（惰性导入注释）
5. 同步 `tests/test_gui.py`（批量替换导入与调用）
6. 运行门禁检查
7. 创建 iter-33 文档
8. git commit + push
