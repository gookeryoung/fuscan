"""启动画面：无边框圆角卡片 + Logo + 阶段文本 + 确定性进度条。

阶段与进度数据由
:class:`~fuscan.gui.controllers.splash_controller.SplashController` 提供，
本视图连接其 ``stageChanged``/``progressChanged`` 信号刷新显示。
"""

# pyrefly: ignore-errors
# PySide2 存根重载合并缺陷导致 QWidget/QPainter 调用与 Signal.connect 误报，详见 sidebar.py 头部说明。

from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor, QPainter, QPaintEvent
from PySide2.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from fuscan.gui.controllers import SplashController
from fuscan.gui.widgets.qss import palette_tokens

__all__ = ["SplashWindow"]

_SPLASH_WIDTH = 320
_SPLASH_HEIGHT = 160


class SplashWindow(QWidget):
    """启动画面窗口（frameless + 半透明背景 + 圆角卡片自绘）。"""

    def __init__(self, controller: SplashController, parent: QWidget | None = None) -> None:
        """初始化并连接阶段/进度信号。

        :param controller: :class:`SplashController` 实例（提供 stage/progress）
        :param parent: 父部件（通常为 None，独立顶层窗口）
        """
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(_SPLASH_WIDTH, _SPLASH_HEIGHT)
        self._dark = False

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(12)

        header = QHBoxLayout()
        logo = QLabel("F", alignment=Qt.AlignCenter)
        logo.setFixedSize(32, 32)
        title = QLabel("fuscan")
        self._logo = logo
        self._title = title
        header.addWidget(logo)
        header.addSpacing(10)
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        stage_label = QLabel()
        self._stage_label = stage_label
        root.addWidget(stage_label)
        root.addStretch()

        progress = QProgressBar()
        progress.setTextVisible(False)
        progress.setRange(0, 1000)
        progress.setValue(0)
        self._progress = progress
        root.addWidget(progress)

        controller.stageChanged.connect(self._refresh_from_controller)
        controller.progressChanged.connect(self._refresh_from_controller)
        # 初始同步一次控制器状态
        self._controller = controller
        self._refresh_from_controller()

    # ----------------------------- 信号槽 -----------------------------

    def _refresh_from_controller(self) -> None:
        """从控制器读取当前阶段文本与进度并刷新显示。"""
        self._stage_label.setText(str(self._controller.stage))
        self._progress.setValue(int(float(self._controller.progress) * 1000))

    # ----------------------------- 绘制 -----------------------------

    def set_dark(self, dark: bool) -> None:
        """切换深浅色（主窗口构造前即可设置；默认浅色）。"""
        self._dark = dark
        t = palette_tokens(dark)
        self._logo.setStyleSheet(
            f"background-color: {t['primary']}; color: {t['text_on_primary']};"
            " border-radius: 7px; font-weight: bold; font-size: 15px;"
        )
        self._title.setStyleSheet(f"color: {t['text_primary']}; font-size: 17px; font-weight: bold;")
        self._stage_label.setStyleSheet(f"color: {t['text_secondary']}; font-size: 13px;")
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        """绘制圆角卡片底与进度条内圆角配色。"""
        t = palette_tokens(self._dark)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(t["bg_card"]))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)
        painter.end()
