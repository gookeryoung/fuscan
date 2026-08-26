"""关于页控制器：暴露版本/作者/License/依赖列表给 QML。

公共 API：

- :class:`AboutController`：``QObject`` 子类
- :meth:`AboutController.open_manual`：打开用户手册 PDF
"""

from __future__ import annotations

import logging
import os
import sys
from importlib.metadata import PackageNotFoundError, version

from PySide2.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide2.QtGui import QDesktopServices

from fuscan import __author__, __description__, __license__, __version__
from fuscan.config import CONFIG_DIR
from fuscan.extractors.ocr import get_ocr_status
from fuscan.paths import MANUAL_PDF_PATH

__all__ = ["AboutController"]

logger = logging.getLogger(__name__)


def _open_path_robustly(path: object) -> bool:
    """跨平台打开本地路径（文件或目录）。

    优先使用 ``QDesktopServices.openUrl``；Windows 上若失败回退到
    ``os.startfile``（对含中文路径的本地 PDF 更可靠）。其他平台仅依赖
    ``QDesktopServices``。

    :param path: 待打开路径（``Path`` 或 ``str``）
    :return: 成功打开返回 ``True``，失败返回 ``False``
    """
    path_str = str(path)
    url = QUrl.fromLocalFile(path_str)
    if QDesktopServices.openUrl(url):
        return True
    # Windows 兜底：QDesktopServices 对含非 ASCII 路径偶发失败
    if sys.platform == "win32":
        try:
            os.startfile(path_str)
            return True
        except OSError as exc:
            logger.warning("os.startfile 打开失败: %s -> %s", path_str, exc)
    return False


# 第三方依赖（与 pyproject.toml dependencies 同步，简化展示）
_DEPENDENCIES: tuple[str, ...] = (
    "PySide2 - Qt GUI 框架",
    "PyYAML - 配置与规则文件解析",
    "lxml - DOCX/PPTX/ODF 文档提取",
    "pypdfium2 - PDF 文本提取",
    "python-calamine - XLSX/XLS 表格提取",
    "rarfile - RAR 压缩包读取",
    "py7zr - 7z 压缩包读取",
    "charset-normalizer - 编码检测",
    "olefile - OLE 复合文档",
    "reportlab - 用户手册 PDF 生成",
)


def _detect_native_matcher_status() -> str:
    """检测 fuscan-core 原生引擎状态。

    fuscan-core 是可选依赖（Rust + PyO3 实现，缺失时回退纯 Python），
    在依赖列表中显示其安装状态与版本，便于用户判断是否启用原生加速。
    """
    try:
        v = version("fuscan-core")
    except PackageNotFoundError:
        return "fuscan-core - 原生引擎（未安装，使用纯 Python）"
    return f"fuscan-core {v} - 原生引擎（已启用）"


def _detect_ocr_status() -> str:
    """检测 OCR 引擎状态，返回展示字符串。

    调用 :func:`fuscan.extractors.ocr.get_ocr_status` 探测预编译 exe 与
    PP-OCRv3 模型文件是否就位（不启动子进程），在关于页展示启用情况与
    未启用原因，便于用户定位是 exe 缺失还是模型文件缺失。
    """
    status = get_ocr_status()
    if status.available:
        v = f" {status.version}" if status.version else ""
        return f"RapidOCR-json{v} - OCR 引擎（已启用）"
    return f"RapidOCR-json - OCR 引擎（未启用：{status.reason}）"


class AboutController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """关于页控制器。"""

    # 关于页内容为常量，运行时不变；QML 绑定要求 @Property 声明 NOTIFY，
    # 否则报 "depends on non-NOTIFYable properties" 警告。共用一个信号即可。
    infoChanged = Signal()
    # 打开手册/配置目录失败时通知 QML 显示 toast（参数为提示消息）
    openFailed = Signal(str)

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
    def nativeEngines(self) -> list[str]:
        """项目原生引擎列表（与第三方依赖并列展示）。

        fuscan-core 是 Rust + PyO3 实现的可选原生引擎，
        缺失时回退纯 Python，行为一致但性能较低。
        """
        return [_detect_native_matcher_status()]

    @Property(str, notify=infoChanged)  # pyrefly: ignore [not-callable]
    def ocrEngine(self) -> str:
        """OCR 引擎状态（启用情况 + 未启用原因）。

        展示 RapidOCR-json 预编译 exe 是否可用及未启用原因（exe 缺失或
        模型文件缺失），便于用户在关于页一眼判断 OCR 是否就绪。
        """
        return _detect_ocr_status()

    @Property("QVariantList", notify=infoChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def ocrDependencies(self) -> list[dict[str, object]]:
        """OCR 各依赖项状态（供关于页逐项展示绿勾/红叉）。

        返回 RapidOCR-json 引擎与 PP-OCRv3 模型文件两项的就位状态与版本，
        QML 用 Repeater 渲染：已就位绿色勾 + 版本号，未就位红色叉。
        """
        status = get_ocr_status()
        return [{"name": d.name, "installed": d.installed, "version": d.version} for d in status.dependencies]

    @Property("QVariantList", notify=infoChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def dependencies(self) -> list[str]:
        """第三方依赖列表。"""
        return list(_DEPENDENCIES)

    @Slot()  # pyrefly: ignore [not-callable]
    def openManual(self) -> None:
        """打开用户手册 PDF（系统默认阅读器）。

        失败时通过 :attr:`openFailed` 信号通知 QML 显示 toast，
        避免用户点击后无任何反馈。Windows 上 ``QDesktopServices.openUrl``
        对含中文路径的本地 PDF 偶发失败，回退到 ``os.startfile``。
        """
        if not MANUAL_PDF_PATH.exists():
            logger.warning("用户手册 PDF 不存在: %s", MANUAL_PDF_PATH)
            self.openFailed.emit(f"用户手册不存在: {MANUAL_PDF_PATH.name}")  # pyrefly: ignore [missing-attribute]
            return
        if not _open_path_robustly(MANUAL_PDF_PATH):
            logger.warning("无法打开用户手册 PDF: %s", MANUAL_PDF_PATH)
            self.openFailed.emit("无法打开用户手册，请检查 PDF 阅读器是否安装")  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def openConfigDir(self) -> None:
        """打开配置目录（系统文件管理器）。

        方便用户查看 ``config.yaml`` / 规则文件 / 缓存等。
        失败时通过 :attr:`openFailed` 信号通知 QML 显示 toast。
        """
        if not CONFIG_DIR.exists():
            logger.warning("配置目录不存在: %s", CONFIG_DIR)
            self.openFailed.emit("配置目录不存在")  # pyrefly: ignore [missing-attribute]
            return
        if not _open_path_robustly(CONFIG_DIR):
            logger.warning("无法打开配置目录: %s", CONFIG_DIR)
            self.openFailed.emit("无法打开配置目录")  # pyrefly: ignore [missing-attribute]
