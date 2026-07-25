"""关于页控制器：暴露版本/作者/License/依赖列表给 QML。

公共 API：

- :class:`AboutController`：``QObject`` 子类
- :meth:`AboutController.open_manual`：打开用户手册 PDF
"""

from __future__ import annotations

import logging

try:
    from PySide2.QtCore import Property, QObject, QUrl, Signal, Slot
    from PySide2.QtGui import QDesktopServices
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QDesktopServices  # pyrefly: ignore [missing-import]

from fuscan import __author__, __description__, __license__, __version__
from fuscan.config import CONFIG_DIR, MANUAL_PDF_PATH

__all__ = ["AboutController"]

logger = logging.getLogger(__name__)

# 第三方依赖（与 pyproject.toml dependencies 同步，简化展示）
_DEPENDENCIES: tuple[str, ...] = (
    "PySide2/PySide6 - Qt GUI 框架",
    "PyYAML - 配置与规则文件解析",
    "watchdog - 文件系统监控",
    "python-docx - DOCX 文档提取",
    "python-pptx - PPTX 演示提取",
    "odfpy - ODF 文档提取",
    "pypdf / pdf_oxide - PDF 文本提取",
    "python-calamine - XLSX/XLS 表格提取",
    "rarfile - RAR 压缩包读取",
    "py7zr - 7z 压缩包读取",
    "charset-normalizer - 编码检测",
    "striprtf - RTF 文档提取",
    "olefile - OLE 复合文档",
    "extract-msg - MSG 邮件解析",
    "reportlab - 用户手册 PDF 生成",
    "pyzstd - Zstandard 解压",
)


class AboutController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """关于页控制器。"""

    # 关于页内容为常量，运行时不变；QML 绑定要求 @Property 声明 NOTIFY，
    # 否则报 "depends on non-NOTIFYable properties" 警告。共用一个信号即可。
    infoChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    @Property(str, notify=infoChanged)  # pyrefly: ignore [not-callable]
    def version(self) -> str:
        """fuscan 版本号。"""
        return __version__

    @Property(str, notify=infoChanged)  # pyrefly: ignore [not-callable]
    def description(self) -> str:
        """fuscan 描述。"""
        return __description__

    @Property(str, notify=infoChanged)  # pyrefly: ignore [not-callable]
    def author(self) -> str:
        """作者。"""
        return __author__

    @Property(str, notify=infoChanged)  # pyrefly: ignore [not-callable]
    def license(self) -> str:
        """License。"""
        return __license__

    @Property("QVariantList", notify=infoChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def dependencies(self) -> list[str]:
        """第三方依赖列表。"""
        return list(_DEPENDENCIES)

    @Slot()  # pyrefly: ignore [not-callable]
    def openManual(self) -> None:
        """打开用户手册 PDF（系统默认阅读器）。"""
        if not MANUAL_PDF_PATH.exists():
            logger.warning("用户手册 PDF 不存在: %s", MANUAL_PDF_PATH)
            return
        url = QUrl.fromLocalFile(str(MANUAL_PDF_PATH))
        if not QDesktopServices.openUrl(url):
            logger.warning("无法打开用户手册 PDF: %s", MANUAL_PDF_PATH)

    @Slot()  # pyrefly: ignore [not-callable]
    def openConfigDir(self) -> None:
        """打开配置目录（系统文件管理器）。

        方便用户查看 ``config.yaml`` / 规则文件 / 缓存等。
        """
        if not CONFIG_DIR.exists():
            logger.warning("配置目录不存在: %s", CONFIG_DIR)
            return
        url = QUrl.fromLocalFile(str(CONFIG_DIR))
        if not QDesktopServices.openUrl(url):
            logger.warning("无法打开配置目录: %s", CONFIG_DIR)
