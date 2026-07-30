"""``AboutController`` 单元测试。

验证版本号/描述/作者/License/依赖列表等只读 ``@Property``。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from fuscan import __author__, __description__, __license__, __version__
    from fuscan.gui.controllers.about_controller import AboutController

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过关于控制器测试", allow_module_level=True)


@pytest.fixture()
def about() -> AboutController:
    return AboutController()


class TestProperties:
    def test_version_matches_module(self, about: AboutController) -> None:
        assert about.version == __version__

    def test_description_matches_module(self, about: AboutController) -> None:
        assert about.description == __description__

    def test_author_matches_module(self, about: AboutController) -> None:
        assert about.author == __author__

    def test_license_matches_module(self, about: AboutController) -> None:
        assert about.license == __license__


class TestDependencies:
    def test_dependencies_is_list(self, about: AboutController) -> None:
        deps = about.dependencies
        assert isinstance(deps, list)
        assert len(deps) > 0

    def test_dependencies_includes_key_libraries(self, about: AboutController) -> None:
        deps = about.dependencies
        # 关键依赖应在列表中（部分匹配）
        joined = " ".join(deps)
        assert "PySide" in joined
        assert "PyYAML" in joined
        assert "pdf" in joined.lower()
        assert "calamine" in joined.lower()
        assert "7z" in joined.lower()

    def test_dependencies_returns_fresh_copy(self, about: AboutController) -> None:
        """每次返回应是新列表（避免外部修改内部状态）。"""
        deps1 = about.dependencies
        deps1.append("test-lib")
        deps2 = about.dependencies
        assert "test-lib" not in deps2


class TestOpenManual:
    def test_open_manual_no_exception_when_pdf_missing(
        self, about: AboutController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """PDF 不存在时仅记录 warning，不抛异常。"""
        from fuscan.gui.controllers import about_controller

        # 替换 MANUAL_PDF_PATH 为不存在的路径
        non_existent = tmp_path / "non_existent.pdf"
        monkeypatch.setattr(about_controller, "MANUAL_PDF_PATH", non_existent)
        # 不应抛异常
        about.openManual()

    def test_open_manual_calls_desktop_services(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """PDF 存在时应调用 QDesktopServices.openUrl。"""
        from fuscan.gui.controllers import about_controller

        pdf = tmp_path / "manual.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        monkeypatch.setattr(about_controller, "MANUAL_PDF_PATH", pdf)

        called_urls: list[str] = []

        def fake_open_url(url: object) -> bool:
            called_urls.append(str(url))
            return True

        monkeypatch.setattr(about_controller.QDesktopServices, "openUrl", fake_open_url)
        about.openManual()
        assert len(called_urls) == 1
        assert "manual.pdf" in called_urls[0]

    def test_open_manual_warns_when_open_url_fails(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """openUrl 返回 False 时仅记录 warning，不抛异常。"""
        from fuscan.gui.controllers import about_controller

        pdf = tmp_path / "manual.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        monkeypatch.setattr(about_controller, "MANUAL_PDF_PATH", pdf)
        monkeypatch.setattr(about_controller.QDesktopServices, "openUrl", lambda _url: False)
        # 不应抛异常
        about.openManual()


class TestOpenFailedSignal:
    """iter-139：openFailed 信号触发场景。"""

    def test_open_manual_emits_signal_when_pdf_missing(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """PDF 不存在时应发送 openFailed 信号，消息包含文件名。"""
        from fuscan.gui.controllers import about_controller

        non_existent = tmp_path / "non_existent.pdf"
        monkeypatch.setattr(about_controller, "MANUAL_PDF_PATH", non_existent)

        messages: list[str] = []
        about.openFailed.connect(messages.append)  # pyrefly: ignore [missing-attribute]
        about.openManual()
        assert len(messages) == 1
        assert "non_existent.pdf" in messages[0]

    def test_open_manual_emits_signal_when_open_fails(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """openUrl 与 os.startfile 均失败时应发送 openFailed 信号。"""
        from fuscan.gui.controllers import about_controller

        pdf = tmp_path / "manual.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        monkeypatch.setattr(about_controller, "MANUAL_PDF_PATH", pdf)
        # openUrl 失败
        monkeypatch.setattr(about_controller.QDesktopServices, "openUrl", lambda _url: False)
        # Windows 兜底也失败（或非 Windows 平台直接跳过）
        monkeypatch.setattr(about_controller.sys, "platform", "win32")
        monkeypatch.setattr(
            about_controller.os, "startfile", lambda _p: (_ for _ in ()).throw(OSError("no association")), raising=False
        )

        messages: list[str] = []
        about.openFailed.connect(messages.append)  # pyrefly: ignore [missing-attribute]
        about.openManual()
        assert len(messages) == 1
        assert "无法打开用户手册" in messages[0]

    def test_open_manual_no_signal_when_open_succeeds(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """openUrl 成功时不应发送 openFailed 信号。"""
        from fuscan.gui.controllers import about_controller

        pdf = tmp_path / "manual.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        monkeypatch.setattr(about_controller, "MANUAL_PDF_PATH", pdf)
        monkeypatch.setattr(about_controller.QDesktopServices, "openUrl", lambda _url: True)

        messages: list[str] = []
        about.openFailed.connect(messages.append)  # pyrefly: ignore [missing-attribute]
        about.openManual()
        assert messages == []

    def test_open_config_dir_emits_signal_when_missing(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """配置目录不存在时应发送 openFailed 信号。"""
        from fuscan.gui.controllers import about_controller

        non_existent = tmp_path / "non_existent_config_dir"
        monkeypatch.setattr(about_controller, "CONFIG_DIR", non_existent)

        messages: list[str] = []
        about.openFailed.connect(messages.append)  # pyrefly: ignore [missing-attribute]
        about.openConfigDir()
        assert len(messages) == 1
        assert "配置目录不存在" in messages[0]

    def test_open_config_dir_emits_signal_when_open_fails(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """openUrl 与 os.startfile 均失败时应发送 openFailed 信号。"""
        from fuscan.gui.controllers import about_controller

        config_dir = tmp_path / "fuscan_config"
        config_dir.mkdir()
        monkeypatch.setattr(about_controller, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(about_controller.QDesktopServices, "openUrl", lambda _url: False)
        monkeypatch.setattr(about_controller.sys, "platform", "win32")
        monkeypatch.setattr(
            about_controller.os, "startfile", lambda _p: (_ for _ in ()).throw(OSError("no association")), raising=False
        )

        messages: list[str] = []
        about.openFailed.connect(messages.append)  # pyrefly: ignore [missing-attribute]
        about.openConfigDir()
        assert len(messages) == 1
        assert "无法打开配置目录" in messages[0]


class TestOpenConfigDir:
    """``openConfigDir`` Slot。"""

    def test_open_config_dir_no_exception_when_missing(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """配置目录不存在时仅记录 warning，不抛异常。"""
        from fuscan.gui.controllers import about_controller

        non_existent = tmp_path / "non_existent_config_dir"
        monkeypatch.setattr(about_controller, "CONFIG_DIR", non_existent)
        # 不应抛异常
        about.openConfigDir()

    def test_open_config_dir_calls_desktop_services(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """配置目录存在时应调用 QDesktopServices.openUrl。"""
        from fuscan.gui.controllers import about_controller

        config_dir = tmp_path / "fuscan_config"
        config_dir.mkdir()
        monkeypatch.setattr(about_controller, "CONFIG_DIR", config_dir)

        called_urls: list[str] = []

        def fake_open_url(url: object) -> bool:
            called_urls.append(str(url))
            return True

        monkeypatch.setattr(about_controller.QDesktopServices, "openUrl", fake_open_url)
        about.openConfigDir()
        assert len(called_urls) == 1
        assert "fuscan_config" in called_urls[0]

    def test_open_config_dir_warns_when_open_url_fails(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """openUrl 返回 False 时仅记录 warning，不抛异常。"""
        from fuscan.gui.controllers import about_controller

        config_dir = tmp_path / "fuscan_config"
        config_dir.mkdir()
        monkeypatch.setattr(about_controller, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(about_controller.QDesktopServices, "openUrl", lambda _url: False)
        # 不应抛异常
        about.openConfigDir()
