"""``SplashController`` 单元测试。

验证初始阶段文本、``setStage`` 更新行为与 ``stageChanged`` 信号触发条件。
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
