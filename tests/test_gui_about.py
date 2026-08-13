"""``AboutController`` 单元测试。

验证版本号/描述/作者/License/依赖列表等只读 ``@Property``。
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
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

    def test_dependencies_excludes_native_engine(self, about: AboutController) -> None:
        """第三方依赖列表不应包含 fuscan-core（自身引擎独立展示）。"""
        deps = about.dependencies
        assert not any("fuscan-core" in d for d in deps), "fuscan-core 应在原生引擎列表，非第三方依赖"

    def test_native_engines_includes_fuscan_core(self, about: AboutController) -> None:
        """原生引擎列表应包含 fuscan-core 状态项。"""
        engines = about.nativeEngines
        native_entries = [e for e in engines if "fuscan-core" in e]
        assert len(native_entries) == 1, "应恰好有一项 fuscan-core 状态"
        assert "原生引擎" in native_entries[0]

    def test_native_engine_status_when_available(self, about: AboutController) -> None:
        """fuscan-core 可用时显示版本与'已启用'。"""
        try:
            from importlib.metadata import version

            version("fuscan-core")
        except PackageNotFoundError:
            pytest.skip("fuscan-core 未安装，跳过可用状态测试")
        engines = about.nativeEngines
        native_entry = next(e for e in engines if "fuscan-core" in e)
        assert "已启用" in native_entry

    def test_native_engine_status_when_not_installed(
        self,
        about: AboutController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fuscan-core 不可用时显示'未安装'与'纯 Python'回退提示。"""

        def fake_version(_name: str) -> str:
            raise PackageNotFoundError("fuscan-core")

        from fuscan.gui.controllers import about_controller

        monkeypatch.setattr(about_controller, "version", fake_version)
        engines = about.nativeEngines
        native_entry = next(e for e in engines if "fuscan-core" in e)
        assert "未安装" in native_entry
        assert "纯 Python" in native_entry


class TestOcrEngine:
    """``ocrEngine`` 与 ``ocrDependencies`` Property：OCR 引擎状态展示。"""

    @staticmethod
    def _make_status(
        available: bool, reason: str = "", version: str = "", deps: tuple[object, ...] | None = None
    ) -> object:
        """构造 OcrStatus（含 5 项依赖明细），避免每个测试重复构建。"""
        from fuscan.extractors.ocr import OcrDepStatus, OcrStatus

        if deps is None:
            v = version if available else ""
            deps = (
                OcrDepStatus("rapidocr", available, v),
                OcrDepStatus("onnxruntime", available, "1.23.2" if available else ""),
                OcrDepStatus("Pillow", available, "10.0.0" if available else ""),
                OcrDepStatus("numpy", available, "2.2.6" if available else ""),
                OcrDepStatus("模型文件", available, "4/4" if available else "0/4"),
            )
        return OcrStatus(available, reason, version, deps)  # type: ignore[arg-type]

    def test_ocr_engine_available_with_version(self, about: AboutController, monkeypatch: pytest.MonkeyPatch) -> None:
        """OCR 可用且版本元数据可读时显示版本号与'已启用'。"""
        from fuscan.gui.controllers import about_controller

        monkeypatch.setattr(about_controller, "get_ocr_status", lambda: self._make_status(True, "", "3.4.0"))
        text = about.ocrEngine
        assert "已启用" in text
        assert "3.4.0" in text
        assert "RapidOCR" in text

    def test_ocr_engine_unavailable(self, about: AboutController, monkeypatch: pytest.MonkeyPatch) -> None:
        """OCR 不可用时显示'未启用'与具体原因。"""
        from fuscan.gui.controllers import about_controller

        monkeypatch.setattr(
            about_controller,
            "get_ocr_status",
            lambda: self._make_status(False, "rapidocr 未就位", ""),
        )
        text = about.ocrEngine
        assert "未启用" in text
        assert "rapidocr 未就位" in text

    def test_ocr_engine_available_without_version(
        self, about: AboutController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OCR 可用但版本元数据缺失时不显示版本号，仍标'已启用'。"""
        from fuscan.gui.controllers import about_controller

        monkeypatch.setattr(about_controller, "get_ocr_status", lambda: self._make_status(True, "", ""))
        text = about.ocrEngine
        assert "已启用" in text
        assert "RapidOCR" in text
        # 版本号缺失时不应在 RapidOCR 后出现多余空格
        assert "RapidOCR  -" not in text

    def test_ocr_dependencies_all_installed(self, about: AboutController, monkeypatch: pytest.MonkeyPatch) -> None:
        """全部依赖就位时 ocrDependencies 各项 installed=True + 版本号。"""
        from fuscan.gui.controllers import about_controller

        monkeypatch.setattr(about_controller, "get_ocr_status", lambda: self._make_status(True, "", "3.4.0"))
        deps = about.ocrDependencies
        assert len(deps) == 5
        assert all(d["installed"] for d in deps)
        assert deps[0]["name"] == "rapidocr"
        assert deps[0]["version"] == "3.4.0"
        assert deps[4]["name"] == "模型文件"
        assert deps[4]["version"] == "4/4"

    def test_ocr_dependencies_partial_missing(self, about: AboutController, monkeypatch: pytest.MonkeyPatch) -> None:
        """部分依赖缺失时 ocrDependencies 对应项 installed=False + 版本留空。"""
        from fuscan.extractors.ocr import OcrDepStatus
        from fuscan.gui.controllers import about_controller

        deps = (
            OcrDepStatus("rapidocr", True, "3.4.0"),
            OcrDepStatus("onnxruntime", False, ""),
            OcrDepStatus("Pillow", True, "10.0.0"),
            OcrDepStatus("numpy", True, "2.2.6"),
            OcrDepStatus("模型文件", True, "4/4"),
        )
        monkeypatch.setattr(
            about_controller,
            "get_ocr_status",
            lambda: self._make_status(False, "onnxruntime 未就位", "3.4.0", deps),
        )
        result = about.ocrDependencies
        assert len(result) == 5
        assert result[1]["name"] == "onnxruntime"
        assert result[1]["installed"] is False
        assert result[1]["version"] == ""
        # 其余依赖就位
        assert result[0]["installed"] is True
        assert result[4]["installed"] is True


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
        """openUrl 返回 False 时仅记录 warning，不抛异常。

        注意：mock sys.platform 为 linux 跳过 Windows ``os.startfile`` 兜底，
        避免在 Windows 运行测试时真实调用 ``os.startfile`` 弹出 PDF 阅读器/浏览器。
        Windows 兜底路径由 ``test_open_manual_emits_signal_when_open_fails`` 覆盖。
        """
        from fuscan.gui.controllers import about_controller

        pdf = tmp_path / "manual.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        monkeypatch.setattr(about_controller, "MANUAL_PDF_PATH", pdf)
        monkeypatch.setattr(about_controller.QDesktopServices, "openUrl", lambda _url: False)
        monkeypatch.setattr(about_controller.sys, "platform", "linux")
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
        """openUrl 返回 False 时仅记录 warning，不抛异常。

        注意：mock sys.platform 为 linux 跳过 Windows ``os.startfile`` 兜底，
        避免在 Windows 运行测试时真实调用 ``os.startfile`` 弹出资源管理器。
        Windows 兜底路径由 ``test_open_config_dir_emits_signal_when_open_fails`` 覆盖。
        """
        from fuscan.gui.controllers import about_controller

        config_dir = tmp_path / "fuscan_config"
        config_dir.mkdir()
        monkeypatch.setattr(about_controller, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(about_controller.QDesktopServices, "openUrl", lambda _url: False)
        monkeypatch.setattr(about_controller.sys, "platform", "linux")
        # 不应抛异常
        about.openConfigDir()
