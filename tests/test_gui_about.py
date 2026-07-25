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
    from fuscan.gui.qml.controllers.about_controller import AboutController

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
        assert "watchdog" in joined
        assert "pdf" in joined.lower()
        assert "calamine" in joined.lower()

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
        from fuscan.gui.qml.controllers import about_controller

        # 替换 MANUAL_PDF_PATH 为不存在的路径
        non_existent = tmp_path / "non_existent.pdf"
        monkeypatch.setattr(about_controller, "MANUAL_PDF_PATH", non_existent)
        # 不应抛异常
        about.openManual()


class TestOpenConfigDir:
    """``openConfigDir`` Slot。"""

    def test_open_config_dir_no_exception_when_missing(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """配置目录不存在时仅记录 warning，不抛异常。"""
        from fuscan.gui.qml.controllers import about_controller

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
        from fuscan.gui.qml.controllers import about_controller

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
