"""Splash 启动画面状态控制器：暴露当前阶段文本与确定性进度给 QML 绑定。

启动流程中由 :mod:`fuscan.app` 调用 :meth:`setStage` 更新阶段文本与进度，
QML ``Splash.qml`` 通过 ``SplashController.stage``/``SplashController.progress``
绑定显示当前阶段文本与进度条宽度，让用户在 QGuiApplication 构造后立即看到
反馈，缓解"应用启动卡顿"的观感。

进度采用**确定性单调递增**设计：各启动阶段传入递增的进度值（0.0→1.0），
进度条宽度按比例填充且只增不减，避免 indeterminate 左右往返动画造成的
"进度条反复前进后退"观感。

公共 API：

- :class:`SplashController`：阶段文本 + 进度状态机
- :attr:`SplashController.stage`：当前阶段文本（QML 绑定）
- :attr:`SplashController.progress`：当前进度（0.0-1.0，QML 绑定）
- :meth:`SplashController.setStage`：更新阶段文本与进度（Python 端调用）
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
    通过 ``SplashController.progress`` 绑定驱动确定性进度条宽度。
    Python 端在每个启动阶段调用 :meth:`setStage` 更新文本与进度并触发重绘。

    进度单调递增：``setStage`` 仅在传入进度**大于**当前进度时更新进度值，
    保证进度条永不回退（即便某阶段耗时波动，进度条只增不减）。

    :param parent: 父 QObject
    """

    stageChanged = Signal()
    progressChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        """初始化控制器，默认阶段文本为「正在启动...」、进度 0.0。"""
        super().__init__(parent)
        self._stage: str = "正在启动..."
        self._progress: float = 0.0

    @Property(str, notify=stageChanged)  # pyrefly: ignore [not-callable]
    def stage(self) -> str:
        """当前启动阶段文本。"""
        return self._stage

    @Property(float, notify=progressChanged)  # pyrefly: ignore [not-callable]
    def progress(self) -> float:
        """当前启动进度（0.0-1.0），单调递增。"""
        return self._progress

    @Slot(str, float)  # pyrefly: ignore [not-callable]
    def setStage(self, text: str, progress: float = -1.0) -> None:
        """更新启动阶段文本与进度。

        进度采用单调递增策略：仅当 ``progress >= 0`` 且**大于**当前进度时
        才更新进度值，保证进度条永不回退。文本变化与进度变化分别 emit
        对应信号，QML 绑定各自刷新。

        :param text: 新的阶段文本（如「迁移配置...」）
        :param progress: 新的进度值（0.0-1.0）；传 -1.0（默认）表示不更新进度，
            仅更新文本（兼容仅需刷新文本的场景）
        """
        stage_changed = self._stage != text
        # 单调递增：仅当新进度大于当前进度才更新，避免回退
        progress_changed = progress >= 0.0 and progress > self._progress
        if stage_changed:
            self._stage = text
            self.stageChanged.emit()  # pyrefly: ignore [missing-attribute]
        if progress_changed:
            self._progress = progress
            self.progressChanged.emit()  # pyrefly: ignore [missing-attribute]
