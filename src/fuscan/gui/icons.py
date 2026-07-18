"""主题图标加载与图标资源常量。

将 SVG 图标着色渲染、``.qrc`` 资源路径常量、用户手册 PDF 路径等从
``main_window.py`` 拆分到本模块，使主窗口仅负责 UI 控件装配与信号路由，
资源加载逻辑内聚到本模块。

公共 API：

- :func:`read_svg_text`：读取 SVG 文本，支持 ``.qrc`` 资源路径与磁盘路径
- :func:`load_themed_icon`：加载 SVG 并以指定主题色着色后返回 ``QIcon``
- 常量：``MANUAL_PDF``、``ICON_*``、``ICON_RENDER_SIZE``
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

try:
    from PySide2.QtCore import QByteArray, QFile, Qt
    from PySide2.QtGui import QIcon, QPainter, QPixmap
    from PySide2.QtSvg import QSvgRenderer
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QByteArray, QFile, Qt  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QIcon, QPainter, QPixmap  # pyrefly: ignore [missing-import]
    from PySide6.QtSvg import QSvgRenderer  # pyrefly: ignore [missing-import]

__all__ = [
    "ICON_ABOUT",
    "ICON_ALL_DISK",
    "ICON_DISK",
    "ICON_EDIT",
    "ICON_EXPORT",
    "ICON_EXPORT_CSV",
    "ICON_EXPORT_JSON",
    "ICON_FOLDER",
    "ICON_HARD_DISK",
    "ICON_HISTORY",
    "ICON_LOAD_LIST",
    "ICON_MANUAL",
    "ICON_PAUSE",
    "ICON_RENDER_SIZE",
    "ICON_RESCAN",
    "ICON_SCAN",
    "ICON_SEARCH",
    "ICON_SETTINGS",
    "ICON_STOP",
    "MANUAL_PDF",
    "load_themed_icon",
    "read_svg_text",
]

logger = logging.getLogger(__name__)

# 用户手册 PDF 路径（assets/docs 目录下，随包分发；PDF 由外部阅读器打开，不入 .qrc）
MANUAL_PDF = Path(__file__).parent.parent / "assets" / "docs" / "fuscan-用户手册.pdf"

# 图标路径（.qrc 资源系统，:/ 前缀引用编译嵌入的资源）
ICON_ABOUT = ":/icons/about.svg"
ICON_ALL_DISK = ":/icons/all_disk.svg"
ICON_DISK = ":/icons/disk.svg"
ICON_EDIT = ":/icons/edit.svg"
ICON_EXPORT = ":/icons/export.svg"
ICON_EXPORT_CSV = ":/icons/export_csv.svg"
ICON_EXPORT_JSON = ":/icons/export_json.svg"
ICON_FOLDER = ":/icons/folder.svg"
ICON_HARD_DISK = ":/icons/hard_disk.svg"
ICON_HISTORY = ":/icons/history.svg"
ICON_LOAD_LIST = ":/icons/load_list.svg"
ICON_MANUAL = ":/icons/manual.svg"
ICON_PAUSE = ":/icons/pause.svg"
ICON_RESCAN = ":/icons/rescan.svg"
ICON_SCAN = ":/icons/scan.svg"
ICON_SETTINGS = ":/icons/settings.svg"
ICON_STOP = ":/icons/stop.svg"
ICON_SEARCH = ":/icons/search.svg"

# 主题图标渲染分辨率（高分辨率保证 DPI 缩放下清晰）
ICON_RENDER_SIZE = 128

# 移除 SVG 中所有 fill="..." 属性的正则
_SVG_FILL_RE = re.compile(r'\sfill="[^"]*"')


def read_svg_text(svg_path: str) -> str:
    """读取 SVG 文本，支持 .qrc 资源路径（``:/`` 前缀）与磁盘路径。

    :param svg_path: ``:/icons/xxx.svg`` 资源路径或磁盘绝对路径
    :returns: SVG 文件文本
    :raises OSError: 文件打开或读取失败
    """
    if svg_path.startswith(":"):
        file = QFile(svg_path)
        if not file.open(QFile.ReadOnly | QFile.Text):  # pyrefly: ignore [missing-argument]
            raise OSError(f"无法打开 Qt 资源: {svg_path}")
        try:
            return bytes(file.readAll()).decode("utf-8")
        finally:
            file.close()
    return Path(svg_path).read_text(encoding="utf-8")


def load_themed_icon(svg_path: str, color: str) -> QIcon:
    """加载 SVG 文件并以指定主题色着色后返回 QIcon。

    读取 SVG 文本后:(1) 移除所有 fill 属性消除原色;(2) 在根 <svg> 标签注入
    ``fill="<color>"`` 作为默认填充色;(3) 通过 QSvgRenderer 渲染到透明 QPixmap
    后构造 QIcon。主题色变更时需重新调用本函数重建图标。

    :param svg_path: SVG 资源路径（``:/icons/xxx.svg``）或磁盘绝对路径
    :param color: 主题色 hex 字符串（如 ``theme.COLOR_PRIMARY``）
    :returns: 已着色的 QIcon，渲染失败时回退到原始文件加载
    """
    try:
        text = read_svg_text(svg_path)
        # 移除所有 fill 属性，确保主题色统一覆盖原图标颜色
        text = _SVG_FILL_RE.sub("", text)
        # 在首个 <svg ...> 开标签内注入 fill 属性作为默认填充
        text = re.sub(
            r"(<svg\b[^>]*?)(/?>)",
            rf'\1 fill="{color}"\2',
            text,
            count=1,
        )
        renderer = QSvgRenderer(QByteArray(text.encode("utf-8")))
        if not renderer.isValid():
            return QIcon(svg_path)
        pixmap = QPixmap(ICON_RENDER_SIZE, ICON_RENDER_SIZE)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)  # pyrefly: ignore [missing-argument]
        painter.end()
        return QIcon(pixmap)
    except (OSError, ValueError):
        logger.warning("主题图标加载失败，回退原始文件: %s", svg_path, exc_info=True)
        return QIcon(svg_path)
