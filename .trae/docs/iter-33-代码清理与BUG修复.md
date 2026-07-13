# iter-33 代码清理与 BUG 修复

## 迭代目标

对 fuscan 项目做集中代码清理：移除死代码、修复 iter-31 遗留的 50MB/100MB 上限不一致、修复 docstring 引用错误、细化异常捕获、提取重复代码到共享模块、整理惰性导入注释。无新功能引入，不改变公共 API 行为。

## 改动文件清单

### 新增

- `src/fuscan/gui/preview_utils.py`：GUI 预览区共用工具模块，包含 6 常量 + 5 函数（公共命名），消除 `main_window.py` 与 `detail_dialog.py` 的 100% 重复代码。

### 修改

- `src/fuscan/gui/main_window.py`：
  - 删除本地 5 函数定义（`_format_size`/`_extract_keywords`/`_build_preview_html`/`_build_keyword_to_rule_map`/`_compile_keyword_pattern`）
  - 更新所有调用点为公共名（去 `_` 前缀）
  - `from fuscan import __version__`、`import subprocess`、`import sys` 移到顶部 import 区
  - 4 处惰性导入加注释说明原因
- `src/fuscan/gui/detail_dialog.py`：
  - 删除本地 6 常量 + 5 函数定义
  - 添加 `from fuscan.gui.preview_utils import ...`
  - 更新所有引用为公共名
  - 恢复 `Sequence`/`RuleHit` 导入（`_find_hit_positions` 方法签名需要）
- `src/fuscan/cli.py`：6 处惰性导入加注释说明原因
- `tests/test_gui.py`：
  - 顶部全局 import 区添加 `from fuscan.gui.preview_utils import build_preview_html, extract_keywords, format_size`
  - 24 处函数内 import 语句替换为公共名
  - 所有调用点去 `_` 前缀
  - docstring 中的引用同步更新
  - 修复被 replace_all 误伤的 `test_extract_keywords` 函数名
- `tests/test_scanner.py`：`test_default_extract_content_with_hash_oversize_returns_empty` 中 50MB → 100MB

### 已删除（上一会话完成）

- `src/fuscan/assets/icons/right.svg`：仅被死代码 `_icon_right` 引用

## 关键决策与依据

1. **100MB 上限统一**：iter-31 已实现流式读取（>10MB 分块解码 + `IncrementalDecoder`），100MB 配合流式读取合理；消除 scanner.py/context.py/text.py 三处不一致。
2. **preview_utils.py 提取**：main_window.py 与 detail_dialog.py 是 100% 重复（5 函数 + 6 常量共约 100 行），任何一处修复 bug 都需同步另一处，DRY 原则优先于"三处相似才提取"。
3. **惰性导入保留 + 注释**：GUI 子模块惰性加载是为加速启动（避免主窗口初始化时加载所有子对话框），属于合理设计；保留惰性但加注释说明原因，符合 rule-11 精神。
4. **`__version__`/`subprocess`/`sys` 顶部导入**：`fuscan` 包必然已加载（main_window.py 在 `fuscan.gui` 包内）；`subprocess`/`sys` 是标准库，顶部导入无副作用。
5. **测试全局 import**：test_gui.py 顶部添加全局 `from fuscan.gui.preview_utils import ...`，避免每个测试函数内部重复 import，简化测试代码。

## 验证结果

全套门禁检查通过：

```
uv run ruff check src tests          # All checks passed
uv run ruff format --check src tests # 71 files already formatted
uv run pyrefly check                 # 0 errors
uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=96
# 1032 passed, 4 deselected in 20.54s, coverage 96%
```

额外验证：
- `main_window.py` 与 `detail_dialog.py` 中无 `_format_size`/`_extract_keywords`/`_build_preview_html` 等私有定义残留
- `tests/test_gui.py` 中无 `from fuscan.gui.detail_dialog import _` 或 `from fuscan.gui.main_window import _format_size` 等旧导入

## 遗留事项

无。所有 8 个改动类别（A-H）均已完成。
