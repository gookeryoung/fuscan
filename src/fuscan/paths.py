"""资产路径常量。

集中定义本包内静态资源（图标、用户手册 PDF、内置规则等）的路径常量，
与 :mod:`fuscan.config` 的配置持久化职责解耦。

公共 API：

- :data:`ASSETS_DIR`：包内 ``assets`` 目录绝对路径
- :data:`MANUAL_PDF_PATH`：用户手册 PDF 路径（GUI 关于页打开）
- :data:`BUILTIN_RULES_PATH`：内置规则文件路径（见 :mod:`fuscan.rules.builtin`，
  此处仅作为常量来源，规则加载逻辑归 :mod:`fuscan.rules`）
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["ASSETS_DIR", "BUILTIN_RULES_PATH", "MANUAL_PDF_PATH"]

# 包内静态资源根目录：``src/fuscan/assets/``
ASSETS_DIR: Path = Path(__file__).parent / "assets"

# 内置通用规则文件：``assets/rules/builtin.yaml``
BUILTIN_RULES_PATH: Path = ASSETS_DIR / "rules" / "builtin.yaml"

# 用户手册 PDF：``assets/docs/fuscan-用户手册.pdf``，GUI 关于页打开
MANUAL_PDF_PATH: Path = ASSETS_DIR / "docs" / "fuscan-用户手册.pdf"
