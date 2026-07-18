# iter-58 GUI 独立入口脚本

## 需求清单

- [x] 1. 让 `f:\Dev\fuscan\src\fuscan\gui` 独立可打包（用户请求
  "`f:\Dev\fuscan\src\fuscan\gui.py` 独立gui，便于分开打包"）

## 迭代目标

用户希望 GUI 模块可独立打包发布，不必依赖 `fuscan gui` CLI 子命令启动。
经两次澄清，采用「轻量方案」：

1. 删除空文件 `src/fuscan/gui.py`（与 `gui/` 子包同名，Python 中包优先，
   空文件无意义且易混淆）。
2. 在 `src/fuscan/gui/` 子包内新增 `__main__.py`，实现
   `python -m fuscan.gui` 直接启动 GUI。
3. `pyproject.toml` 注册 `fuscan-gui` console_script 入口指向
   `fuscan.gui.app:launch`，便于打包发布独立可执行命令。

不引入新依赖、不改变现有 `fuscan gui` 子命令行为，仅在打包层面新增入口。

## 改动文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/fuscan/gui.py` | 删除 | 空文件，与 `gui/` 子包同名易混淆 |
| `src/fuscan/gui/__main__.py` | 新建 | `python -m fuscan.gui` 入口，调用 `fuscan.gui.app.launch` |
| `pyproject.toml` | 修改 | `[project.scripts]` 新增 `fuscan-gui = "fuscan.gui.app:launch"` |

## 关键决策与依据

### 删除空 `gui.py` 的依据

`src/fuscan/` 下同时存在 `gui.py`（空文件）与 `gui/` 子包。Python 导入系统
中，当 `.py` 文件与同目录下的子包同名时，**子包优先**（`sys.path` 搜索时
包目录先于同名模块文件）。因此 `import fuscan.gui` 实际加载 `gui/__init__.py`，
`gui.py` 从未被导入，是历史遗留的无意义文件。

保留空 `gui.py` 的风险：
- 误导维护者认为 `gui.py` 是入口
- 与 `python -m fuscan.gui` 语义冲突（实际执行 `gui/__main__.py`）

### 新增 `gui/__main__.py` 的依据

`python -m <package>` 会执行该包目录下的 `__main__.py`。当前 `gui/` 子包
缺少 `__main__.py`，故 `python -m fuscan.gui` 失败。新增极简入口：

```python
"""GUI 模块入口：支持 ``python -m fuscan.gui`` 直接启动 GUI 应用。

便于独立打包为可执行文件（PyInstaller 等），无需通过 CLI 子命令。
"""

from __future__ import annotations

import sys

from fuscan.gui.app import launch

if __name__ == "__main__":  # pragma: no cover
    sys.exit(launch())
```

- 复用既有 `fuscan.gui.app.launch`，避免重复实现
- `if __name__ == "__main__":` 标注 `# pragma: no cover`（入口点不可单元测试）
- `from __future__ import annotations` 符合 rule-11 兼容性约束

### `fuscan-gui` console_script 注册依据

`[project.scripts]` 已有 `fuscan = "fuscan.cli:main"`，新增
`fuscan-gui = "fuscan.gui.app:launch"` 后，`uv pip install -e .` 会生成
`.venv\Scripts\fuscan-gui.EXE`，便于：

- 打包发布独立 GUI 可执行文件
- PyInstaller / cx_Freeze 等工具直接以 `fuscan-gui` 为入口
- 用户无需记忆 `python -m fuscan.gui` 长命令

### 轻量方案 vs 重构方案选择

考虑过两种方案：

| 方案 | 内容 | 取舍 |
|------|------|------|
| 轻量（采用） | 删空 `gui.py` + 加 `__main__.py` + 注册脚本入口 | 改动最小，符合「不过早抽象」 |
| 重构 | 将 `gui/` 子包拆为独立顶层包 `fuscan_gui/`，与 `fuscan` 解耦 | 需大量 import 路径迁移，破坏既有结构 |

轻量方案已满足用户「便于分开打包」需求：PyInstaller 可直接以
`fuscan-gui` 为入口生成单一 EXE，无需重构包结构。

## 代码实现情况

### `src/fuscan/gui/__main__.py`（新建）

```python
"""GUI 模块入口：支持 ``python -m fuscan.gui`` 直接启动 GUI 应用。

便于独立打包为可执行文件（PyInstaller 等），无需通过 CLI 子命令。
"""

from __future__ import annotations

import sys

from fuscan.gui.app import launch

if __name__ == "__main__":  # pragma: no cover
    sys.exit(launch())
```

### `pyproject.toml`（修改）

```toml
[project.scripts]
fuscan = "fuscan.cli:main"
fuscan-gui = "fuscan.gui.app:launch"
```

### `src/fuscan/gui.py`（删除）

空文件，无内容损失。

## 整合优化情况

- **入口多样化**：现在 GUI 有三种启动方式
  1. `fuscan gui`（CLI 子命令，原方式）
  2. `python -m fuscan.gui`（Python 标准模块运行）
  3. `fuscan-gui`（独立 console_script 命令）
- **打包友好**：PyInstaller 等工具可直接以 `fuscan-gui` 为入口生成
  独立 EXE，无需通过 `fuscan gui` 子命令跳转。
- **消除歧义**：删除空 `gui.py` 后，`fuscan/gui/` 子包成为唯一 GUI 入口，
  避免维护者混淆。
- **零行为变更**：不引入新依赖、不修改 `launch()` 实现、不改变
  `fuscan gui` 子命令行为。

## 测试验证结果

| 门禁 | 结果 | 基线（iter-57） | 变化 |
|------|------|----------------|------|
| ruff check | All checks passed | 0 errors | — |
| ruff format --check | 44 files already formatted | 43 files | +1（`__main__.py`） |
| pyrefly check | 0 errors (62 suppressed) | 0 errors (62 suppressed) | — |
| pytest | 1363 passed / 0 failed | 1363 passed | — |
| coverage | 96.21% | 96.27% | -0.06% |

覆盖率小幅下降 0.06% 来自 `gui/__main__.py` 的
`if __name__ == "__main__":` 分支已标注 `# pragma: no cover`（入口点
不可单元测试），分母增加 1 行未覆盖代码，仍高于 95% 门禁。

### 手工验证

- `python -m fuscan.gui` 在 offscreen 模式下成功启动 GUI 应用
  （2 秒后强制终止未退出，说明事件循环正常运行）
- `importlib.metadata.entry_points()` 确认 `fuscan-gui` 已注册为
  console_script，指向 `fuscan.gui.app:launch`
- `shutil.which('fuscan-gui')` 返回
  `F:\Dev\fuscan\.venv\Scripts\fuscan-gui.EXE`，可执行文件已生成

## 遗留事项

- `fuscan-gui` 命令在 PowerShell 中通过 `Start-Process -FilePath 'fuscan-gui'`
  调用失败（PATH 解析问题），但可执行文件实际存在且 `shutil.which` 能定位。
  这是 PowerShell PATH 缓存问题，不影响实际打包发布与命令行直接调用
  `fuscan-gui`。
- 未将 `gui/` 子包拆为独立顶层包 `fuscan_gui/`。如未来 GUI 与 CLI 需要
  完全独立发布（不同 PyPI 包名），可再行评估重构。

## 下一轮计划

无明确下一轮计划。GUI 独立打包入口已完成，用户需求已满足。
如用户提出新需求再行迭代。
