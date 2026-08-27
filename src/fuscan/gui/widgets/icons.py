"""SVG 图标染色工具：将 qrc 内单色 SVG 渲染为指定颜色的 QIcon。

QML 版用 ``ColorOverlay`` 染色；Widgets 版等价实现：QSvgRenderer 渲染
SVG 到透明 QPixmap，再以 ``CompositionMode_SourceIn`` 整体着色。
渲染结果按 ``(qrc 路径, 颜色, 尺寸)`` 缓存，导航切换/主题切换时避免重复渲染。
"""

# pyrefly: ignore-errors
# PySide2 存根重载合并缺陷导致 QPainter/QSvgRenderer/QFile 调用误报，详见 sidebar.py 头部说明。

from __future__ import annotations

from pathlib import Path

from PySide2.QtCore import QByteArray, QFile, QIODevice, QRectF, Qt
from PySide2.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide2.QtSvg import QSvgRenderer

# 确保 qrc 资源（icons/*.svg、favicon.ico）已注册：
# 生产入口 app.py 会导入 resources_rc，但 Widgets 组件可能被独立构造
# （如单独实例化 SidebarWidget），此处兜底注册一次。
from fuscan.gui import resources_rc  # noqa: F401

__all__ = ["clear_icon_cache", "tinted_svg_icon"]

# (source, color, size) -> QIcon；窗口存活期内缓存，量级为图标数 × 2 态，可忽略
_ICON_CACHE: dict[tuple[str, str, int], QIcon] = {}


def tinted_svg_icon(source: str, color: str, size: int = 16) -> QIcon:
    """渲染单色 SVG 并整体染色。

    :param source: SVG 来源（``qrc:/icons/xxx.svg`` 或磁盘路径）
    :param color: 十六进制染色色值（如 ``"#586069"``）
    :param size: 输出图标边长（正方形，逻辑像素）
    :return: 染色后的 :class:`QIcon`（来源缺失或解析失败返回空 QIcon，不抛异常）
    """
    key = (source, color, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    icon = _render(source, color, size)
    _ICON_CACHE[key] = icon
    return icon


def clear_icon_cache() -> None:
    """清空图标缓存（仅测试使用）。"""
    _ICON_CACHE.clear()


def _render(source: str, color: str, size: int) -> QIcon:
    """执行实际的渲染与染色，失败时静默返回空图标。"""
    data = _read_bytes(source)
    if not data:
        return QIcon()
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid():
        return QIcon()

    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    pixmap = QPixmap(int(size * dpr), int(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)  # type: ignore[arg-type]
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
    # SourceIn：以染色覆盖既有像素的 alpha 形状，实现 ColorOverlay 等价效果
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(QRectF(0.0, 0.0, float(size), float(size)), QColor(color))
    painter.end()
    return QIcon(pixmap)


def _read_bytes(source: str) -> bytes:
    """读取 qrc 或磁盘来源的字节流（来源不可达返回空）。

    分流依据 ``Path.exists``：磁盘路径存在则直接读文件；
    否则视为 qrc 内部资源（``:/xxx`` 与 ``qrc:/xxx`` 均归一化处理）。
    """
    try:
        if Path(source).exists():
            return Path(source).read_bytes()
        qrc_path = source[3:] if source.startswith("qrc:") else source
        f = QFile(qrc_path)
        if not f.open(QIODevice.ReadOnly):
            return b""
        return bytes(f.readAll())
    except OSError:
        return b""
