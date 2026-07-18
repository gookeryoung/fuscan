"""``fuscan.gui.explorer`` 单元测试。

跨平台文件管理器集成的命令分派逻辑，无需 QApplication 环境，
通过 monkeypatch ``sys.platform`` 与 ``subprocess.Popen`` 验证命令构造。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fuscan.gui.explorer import open_path_in_explorer


class TestOpenPathInExplorer:
    """``open_path_in_explorer`` 跨平台命令分派测试。"""

    def test_win32_uses_explorer_select(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Windows 平台应调用 ``explorer /select, <path>``。"""
        target = tmp_path / "secret.txt"
        target.write_text("x", encoding="utf-8")

        captured: list[Any] = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(subprocess, "Popen", captured.append)

        open_path_in_explorer(target)

        assert len(captured) == 1
        assert captured[0] == ["explorer", "/select,", str(target)]

    def test_darwin_uses_open_r(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """macOS 平台应调用 ``open -R <path>``。"""
        target = tmp_path / "secret.txt"
        target.write_text("x", encoding="utf-8")

        captured: list[Any] = []
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(subprocess, "Popen", captured.append)

        open_path_in_explorer(target)

        assert len(captured) == 1
        assert captured[0] == ["open", "-R", str(target)]

    def test_linux_uses_xdg_open_parent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Linux/其他平台应调用 ``xdg-open <parent>``（仅打开父目录）。"""
        target = tmp_path / "secret.txt"
        target.write_text("x", encoding="utf-8")

        captured: list[Any] = []
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(subprocess, "Popen", captured.append)

        open_path_in_explorer(target)

        assert len(captured) == 1
        assert captured[0] == ["xdg-open", str(target.parent)]

    def test_popen_oserror_propagates(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """``subprocess.Popen`` 抛出 ``OSError`` 时应向上传播，由调用方处理用户提示。"""
        target = tmp_path / "missing.txt"
        monkeypatch.setattr(sys, "platform", "win32")

        def raise_os(*args: Any, **kwargs: Any) -> None:
            raise OSError("mocked failure")

        monkeypatch.setattr(subprocess, "Popen", raise_os)

        with pytest.raises(OSError, match="mocked failure"):
            open_path_in_explorer(target)

    def test_path_string_conversion(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """路径对象应被转换为字符串拼接到命令中（避免 Path 直接传入 subprocess）。"""
        target = tmp_path / "file.txt"
        target.write_text("x", encoding="utf-8")

        captured: list[Any] = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(subprocess, "Popen", captured.append)

        open_path_in_explorer(target)

        # 命令中所有路径元素均为 str 类型（非 Path 对象）
        assert all(isinstance(arg, str) for arg in captured[0])
