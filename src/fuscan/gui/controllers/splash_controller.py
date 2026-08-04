"""Splash 启动画面状态控制器：暴露当前阶段文本给 QML 绑定。

启动流程中由 :mod:`fuscan.app` 调用 :meth:`setStage` 更新阶段文本，
QML ``Splash.qml`` 通过 ``SplashController.stage`` 绑定显示当前进度，
让用户在 QGuiApplication 构造后立即看到反馈，缓解"应用启动卡顿"的观感。

公共 API：

- :class:`SplashController`：阶段文本状态机
- :attr:`SplashController.stage`：当前阶段文本（QML 绑定）
- :meth:`SplashController.setStage`：更新阶段文本（Python 端调用）
"""

from __future__ import annotations

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]

__all__ = ["SplashController"]


class SplashController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """Splash 启动画面阶段状态控制器。

    QML 通过 ``SplashController.stage`` 绑定显示当前启动阶段文本，
    Python 端在每个启动阶段调用 :meth:`setStage` 更新文本并触发重绘。

    :param parent: 父 QObject
    """

    stageChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        """初始化控制器，默认阶段文本为「正在启动...」。"""
        super().__init__(parent)
        self._stage: str = "正在启动..."

    @Property(str, notify=stageChanged)  # pyrefly: ignore [not-callable]
    def stage(self) -> str:
        """当前启动阶段文本。"""
        return self._stage

    @Slot(str)  # pyrefly: ignore [not-callable]
    def setStage(self, text: str) -> None:
        """更新启动阶段文本；文本未变时不触发信号。

        :param text: 新的阶段文本（如「迁移配置...」）
        """
        if self._stage != text:
            self._stage = text
            self.stageChanged.emit()  # pyrefly: ignore [missing-attribute]
