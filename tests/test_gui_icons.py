"""``fuscan.gui.icons`` 单元测试。

SVG 文本读取、``.qrc`` 资源路径与磁盘路径读取、常量定义测试。

注：``load_themed_icon`` 的渲染行为已通过 ``MainWindow._setup_icons`` 在
``tests/test_gui.py`` 集成测试中覆盖（构造主窗口时为每个图标调用一次），
本文件不重复直接调用渲染，避免在隔离测试中触发 QSvgRenderer 原生崩溃。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    try:
        from PySide2.QtWidgets import QApplication
    except ImportError:  # pragma: no cover
        from PySide6.QtWidgets import QApplication  # pyrefly: ignore [missing-import]

    from fuscan.gui import resources_rc  # noqa: F401 注册 .qrc 资源
    from fuscan.gui.icons import (
        ICON_ABOUT,
        ICON_DISK,
        ICON_MANUAL,
        ICON_RENDER_SIZE,
        MANUAL_PDF,
        read_svg_text,
    )

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过 GUI 测试", allow_module_level=True)


@pytest.fixture(scope="module")
def qapp() -> QApplication:  # type: ignore[misc]
    """模块级 QApplication fixture。"""
    app = QApplication.instance() or QApplication([])
    yield app


class TestIconConstants:
    """图标资源路径常量定义测试。"""

    def test_icon_paths_use_qrc_prefix(self) -> None:
        """``ICON_*`` 常量应以 ``:/`` 前缀引用 .qrc 资源系统。"""
        for icon_path in (ICON_ABOUT, ICON_DISK, ICON_MANUAL):
            assert icon_path.startswith(":/icons/")
            assert icon_path.endswith(".svg")

    def test_icon_render_size_is_positive_integer(self) -> None:
        """``ICON_RENDER_SIZE`` 应为正整数（高分辨率渲染）。"""
        assert isinstance(ICON_RENDER_SIZE, int)
        assert ICON_RENDER_SIZE > 0

    def test_manual_pdf_path_points_to_assets(self) -> None:
        """``MANUAL_PDF`` 应指向 ``assets/docs/fuscan-用户手册.pdf`` 随包分发文件。"""
        assert MANUAL_PDF.name == "fuscan-用户手册.pdf"
        assert "assets" in MANUAL_PDF.parent.parts
        assert "docs" in MANUAL_PDF.parent.parts


class TestReadSvgText:
    """``read_svg_text`` SVG 文本读取测试。"""

    def test_read_disk_svg_file(self, qapp: QApplication, tmp_path: Path) -> None:
        """从磁盘路径读取 SVG 应返回完整文本。"""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#000"/></svg>'
        svg_path = tmp_path / "test.svg"
        svg_path.write_text(svg_content, encoding="utf-8")

        result = read_svg_text(str(svg_path))

        assert result == svg_content

    def test_read_qrc_resource_svg(self, qapp: QApplication) -> None:
        """从 ``.qrc`` 资源路径读取 SVG 应返回非空文本。"""
        # ICON_ABOUT 已在 .qrc 中注册，应可成功读取
        text = read_svg_text(ICON_ABOUT)
        # SVG 可能以 <?xml 声明或 <!DOCTYPE 开头，但必定包含 <svg 根标签
        assert "<svg" in text
        assert "</svg>" in text

    def test_read_missing_disk_file_raises_oserror(self, qapp: QApplication, tmp_path: Path) -> None:
        """磁盘路径不存在时应抛出 ``OSError``。"""
        missing_path = tmp_path / "nonexistent.svg"
        with pytest.raises(OSError, match="No such file"):
            read_svg_text(str(missing_path))
