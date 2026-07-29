"""GUI 模块入口：支持 ``python -m fuscan.gui`` 直接启动 GUI 应用。

便于独立打包为可执行文件（fspack 等），无需通过 CLI 子命令。
"""

from __future__ import annotations

import sys

# GUI 标记：供 fspack 识别应用类型为 GUI（多入口模式下按入口脚本 import 推断）
# 显式 import QtSvg：fspack 按 AST 推断 PySide2 子模块保留清单，不导入 QtSvg
# 会导致打包后 Qt5Svg.dll + QtSvg.pyd 被剥离，仅留 plugins/imageformats/qsvg.dll
# 因缺失 Qt5Svg.dll 而无法加载 → SVG 图标渲染失败（Win7 上图标显示方块）。
try:
    import PySide2  # noqa: F401, RUF100
    import PySide2.QtSvg  # noqa: F401
except ImportError:  # pragma: no cover
    import PySide6  # noqa: F401, RUF100  # pyrefly: ignore [missing-import]
    import PySide6.QtSvg  # noqa: F401, RUF100  # pyrefly: ignore [missing-import]

from fuscan.gui.app import launch

if __name__ == "__main__":  # pragma: no cover
    sys.exit(launch())
