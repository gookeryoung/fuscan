# iter-15：coopie 模板残留清理与 pyfilescan→uniscan 命名统一

## 本轮目标

项目使用 coopie 模板初始化并更名为 uniscan，但 commit `d299aa6` 仅改了包目录结构，代码内容、文档、配置中的旧名称与模板残留未清理。本轮完成全面清理。

## 改动文件清单

### 源代码（10 文件）

- `src/uniscan/__init__.py`：docstring `pyfilescan` → `uniscan`
- `src/uniscan/__main__.py`：docstring `python -m pyfilescan` → `python -m uniscan`
- `src/uniscan/cli.py`：logger 名、prog、版本输出、用法示例
- `src/uniscan/config.py`：`CONFIG_DIR` 从 `~/.pyfilescan` 改为 `~/.uniscan`，docstring 同步
- `src/uniscan/rules/parser.py`：docstring 中 `:mod:` 引用
- `src/uniscan/scanner/matchers.py`：docstring 中 `:mod:` 引用
- `src/uniscan/gui/main_window.py`：窗口标题、关于对话框、导出报告默认文件名
- `src/uniscan/gui/app.py`：`setApplicationName`
- `src/uniscan/watcher/tray.py`：`setApplicationName`、tooltip、托盘通知文案
- `src/uniscan/builtin/rules.yaml`：注释

### 测试（2 文件）

- `tests/test_gui.py`：9 处 `pyfilescan.gui.main_window.*` mock 路径 → `uniscan.gui.main_window.*`；配置路径注释、窗口标题断言
- `tests/test_cli.py`：`pyfilescan.gui` 模块注入路径、`python -m pyfilescan` 入口测试注释

### 模板残留清理（2 文件）

- `README.md`：从模板特性介绍重写为 uniscan 项目说明（特性/安装/CLI+GUI 用法/规则配置/示例/开发）
- `pyproject.toml`：移除 `extend-exclude = ["template"]`、`project-excludes` 中 `"template/**"`、`exclude = ["template/*"]`

### 文档与示例（9 文件）

- `.trae/skills/pyfilescan-development.md` → `uniscan-development.md`：重命名并更新全部 pyfilescan 引用
- `.trae/docs/iter-11~14`：改动清单中 `src/pyfilescan/` → `src/uniscan/`，配置路径与 skills 引用同步
- `examples/README.md`、`examples/basic_scan.py`：标题、说明、代码示例中的引用
- `rules/example.yaml`、`rules/examples/README.md`：注释与命令示例

### 旧规则文件清理

- 删除 `.trae/rules/` 下 4 个旧规则文件（`dev-workflow.md`、`git-commit-message.md`、`python-standards.md`、`self-driven.md`），这些已重命名为 `rule-NN-*.md` 但删除操作未提交

## 关键决策与依据

1. **`.copier-answers.yml` 保留**：用户确认保留以便后续 `copier update` 同步模板上游修复。
2. **配置目录 `~/.pyfilescan` → `~/.uniscan`**：项目版本 0.1.0 无实际用户，可安全更改，与包名一致避免混淆。
3. **skills 文件重命名**：`pyfilescan-development.md` → `uniscan-development.md`，文件名与内容同步更新。
4. **README 重写而非微调**：原内容是模板特性介绍（构建工具链/CI/CD/多版本测试），非项目说明。重写为基于 `src/uniscan` 实际功能的项目文档。
5. **iter 历史文档更新路径引用**：虽为历史记录，但 `src/pyfilescan/` 路径已不存在，更新为 `src/uniscan/` 避免读者困惑。

## 验证结果

- **pyfilescan 残留检查**：`grep -ri pyfilescan` 无匹配（0 处残留）
- **ruff check**：209 个预存错误（UP006/UP007 类型注解升级建议），与原始状态完全相同，本次清理未引入新问题
- **ruff format --check**：3 个文件需格式化，与原始状态相同，预存问题
- **pytest**：456 passed, 1 failed, 1 skipped
  - 失败项 `test_window_geometry_restored`：PySide2 offscreen 环境下窗口高度断言（548 vs 500，差 48 > 2），环境相关问题，与改名无关
  - **关键修复**：原始状态下测试因 `ModuleNotFoundError: No module named 'pyfilescan'` 完全无法运行（mock 路径与包名不匹配），本次清理修复后 456 个测试得以通过
- **覆盖率**：88.26%，预存水平（iter-14 记录 88.79%），低于 95% 门槛但非本次引入

## 遗留事项

- ruff 的 209 个类型注解升级错误（UP006/UP007）为预存问题，需单独迭代处理（将 `typing.List`/`Optional` 升级为内置泛型，因项目已用 `from __future__ import annotations`）
- 覆盖率 88.26% 低于 95% 门槛，预存问题
- `test_window_geometry_restored` 在 offscreen 环境下的断言需调整（可放宽至 ±50 或跳过 offscreen 模式）
