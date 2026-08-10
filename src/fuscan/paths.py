"""资产路径常量。

集中定义本包内静态资源（图标、用户手册 PDF、内置规则等）的路径常量，
与 :mod:`fuscan.config` 的配置持久化职责解耦。

公共 API：

- :data:`ASSETS_DIR`：包内 ``assets`` 目录绝对路径
- :data:`MANUAL_PDF_PATH`：用户手册 PDF 路径（GUI 关于页打开）
- :data:`BUILTIN_RULES_PATH`：内置规则文件路径（过滤与扫描参数），
  见 :mod:`fuscan.rules.builtin`，此处仅作为常量来源，规则加载逻辑归 :mod:`fuscan.rules`
- :data:`BUILTIN_PATTERNS_PATH`：内置匹配规则文件路径（文件后缀白名单与匹配规则），
  与 ``BUILTIN_RULES_PATH`` 一同被 :func:`fuscan.rules.builtin.load_builtin_ruleset`
  加载并合并
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["ASSETS_DIR", "BUILTIN_PATTERNS_PATH", "BUILTIN_RULES_PATH", "MANUAL_PDF_PATH", "SPLASH_QML_URL"]

# 包内静态资源根目录：``src/fuscan/assets/``
ASSETS_DIR: Path = Path(__file__).parent / "assets"

# 内置通用规则文件：``assets/rules/builtin.yaml``（ignore_paths/ignore_dirs/scan_params/whitelist）
BUILTIN_RULES_PATH: Path = ASSETS_DIR / "rules" / "builtin.yaml"

# 内置匹配规则文件：``assets/rules/builtin-patterns.yaml``（scan_extensions/rules）
# 与 BUILTIN_RULES_PATH 分离，职责单一：前者承载过滤与扫描参数，后者承载匹配规则。
BUILTIN_PATTERNS_PATH: Path = ASSETS_DIR / "rules" / "builtin-patterns.yaml"

# 用户手册 PDF：``assets/docs/fuscan-用户手册.pdf``，GUI 关于页打开
MANUAL_PDF_PATH: Path = ASSETS_DIR / "docs" / "fuscan-用户手册.pdf"

# QML
QML_IMPORT_PATH = "qrc:/qml"
MAIN_QML_URL = "qrc:/qml/Main.qml"
# 启动画面 QML：在 QGuiApplication 构造后立即加载，主窗口 QML 加载完成后关闭
SPLASH_QML_URL = "qrc:/qml/Splash.qml"
