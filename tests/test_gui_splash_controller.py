"""``SplashController`` 单元测试。

验证初始阶段文本、``setStage`` 更新行为与 ``stageChanged`` 信号触发条件，
以及确定性进度（``progress``）的单调递增与 ``progressChanged`` 信号触发。
"""

from __future__ import annotations

import os

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from fuscan.gui.controllers.splash_controller import SplashController

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过 Splash 控制器测试", allow_module_level=True)


@pytest.fixture()
def splash() -> SplashController:
    return SplashController()


class TestInitialStage:
    def test_initial_stage_is_default(self, splash: SplashController) -> None:
        """构造后默认阶段文本为「正在启动...」。"""
        assert splash.stage == "正在启动..."


class TestSetStage:
    def test_set_stage_updates_property(self, splash: SplashController) -> None:
        """setStage 应更新 stage 属性。"""
        splash.setStage("迁移配置...")
        assert splash.stage == "迁移配置..."

    def test_set_stage_emits_signal_when_changed(self, splash: SplashController) -> None:
        """文本变化时应触发 stageChanged 信号。"""
        received: list[str] = []
        splash.stageChanged.connect(lambda: received.append(splash.stage))  # pyrefly: ignore [missing-attribute]
        splash.setStage("加载规则与工作区...")
        assert received == ["加载规则与工作区..."]

    def test_set_stage_does_not_emit_when_unchanged(self, splash: SplashController) -> None:
        """文本未变时不应触发 stageChanged 信号。"""
        received: list[str] = []
        splash.stageChanged.connect(lambda: received.append(splash.stage))  # pyrefly: ignore [missing-attribute]
        # 初始文本为「正在启动...」，再次设置同一值不应触发信号
        splash.setStage("正在启动...")
        assert received == []

    def test_set_stage_empty_string_is_valid(self, splash: SplashController) -> None:
        """空字符串也是合法值，应触发信号。"""
        received: list[str] = []
        splash.stageChanged.connect(lambda: received.append(splash.stage))  # pyrefly: ignore [missing-attribute]
        splash.setStage("")
        assert splash.stage == ""
        assert received == [""]


class TestStageTransition:
    def test_multiple_transitions(self, splash: SplashController) -> None:
        """多次切换阶段，每次文本变化都应触发信号。"""
        received: list[str] = []
        splash.stageChanged.connect(lambda: received.append(splash.stage))  # pyrefly: ignore [missing-attribute]

        splash.setStage("迁移配置...")
        splash.setStage("加载规则与工作区...")
        splash.setStage("加载主界面...")
        splash.setStage("就绪")

        assert received == [
            "迁移配置...",
            "加载规则与工作区...",
            "加载主界面...",
            "就绪",
        ]
        assert splash.stage == "就绪"

    def test_repeated_same_value_only_emits_once(self, splash: SplashController) -> None:
        """同一值连续设置：首次触发（与初始值不同），后续不触发。"""
        received: list[str] = []
        splash.stageChanged.connect(lambda: received.append(splash.stage))  # pyrefly: ignore [missing-attribute]

        splash.setStage("加载主界面...")
        splash.setStage("加载主界面...")
        splash.setStage("加载主界面...")

        assert received == ["加载主界面..."]


class TestProgress:
    """确定性进度（progress）单调递增与信号触发测试。"""

    def test_initial_progress_is_zero(self, splash: SplashController) -> None:
        """构造后默认进度为 0.0。"""
        assert splash.progress == 0.0

    def test_set_stage_with_progress_updates_property(self, splash: SplashController) -> None:
        """setStage 传入进度应更新 progress 属性。"""
        splash.setStage("迁移配置...", 0.15)
        assert splash.progress == 0.15

    def test_progress_emits_signal_when_increased(self, splash: SplashController) -> None:
        """进度递增时应触发 progressChanged 信号。"""
        received: list[float] = []
        splash.progressChanged.connect(lambda: received.append(splash.progress))  # pyrefly: ignore [missing-attribute]
        splash.setStage("迁移配置...", 0.15)
        splash.setStage("加载主界面...", 0.65)
        assert received == [0.15, 0.65]

    def test_progress_monotonic_never_decreases(self, splash: SplashController) -> None:
        """进度单调递增：传入更小值不应回退进度条。"""
        splash.setStage("加载主界面...", 0.65)
        # 传入更小进度值，progress 不应回退
        splash.setStage("回退阶段...", 0.2)
        assert splash.progress == 0.65
        assert splash.stage == "回退阶段..."

    def test_progress_does_not_emit_when_decreasing(self, splash: SplashController) -> None:
        """进度回退值不应触发 progressChanged 信号。"""
        received: list[float] = []
        splash.progressChanged.connect(lambda: received.append(splash.progress))  # pyrefly: ignore [missing-attribute]
        splash.setStage("阶段一", 0.5)
        splash.setStage("阶段二", 0.3)  # 回退，不应触发
        assert received == [0.5]

    def test_set_stage_without_progress_keeps_progress(self, splash: SplashController) -> None:
        """setStage 不传进度（默认 -1.0）时 progress 保持不变。"""
        splash.setStage("迁移配置...", 0.15)
        splash.setStage("仅文本更新...")
        assert splash.progress == 0.15
        assert splash.stage == "仅文本更新..."

    def test_progress_full_sequence_monotonic(self, splash: SplashController) -> None:
        """模拟完整启动序列，进度应单调递增到 1.0。"""
        received: list[float] = []
        splash.progressChanged.connect(lambda: received.append(splash.progress))  # pyrefly: ignore [missing-attribute]
        splash.setStage("迁移配置...", 0.15)
        splash.setStage("加载规则与工作区...", 0.35)
        splash.setStage("加载主界面...", 0.65)
        splash.setStage("就绪", 1.0)
        assert received == [0.15, 0.35, 0.65, 1.0]
        assert splash.progress == 1.0
