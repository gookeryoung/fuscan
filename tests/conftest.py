"""pytest 共享 fixture。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture()
def tmp_scan_root(tmp_path: Path) -> Path:
    """创建一个临时扫描根目录，含若干示例文件。"""
    root = tmp_path / "scan_root"
    root.mkdir()
    return root


@pytest.fixture()
def sample_text_file(tmp_scan_root: Path) -> Path:
    """创建一个含示例文本的 .txt 文件。"""
    path = tmp_scan_root / "sample.txt"
    path.write_text("这是一份测试文档，包含敏感词: SECRET-12345。\n", encoding="utf-8")
    return path


@pytest.fixture()
def chdir_tmp(tmp_path: Path) -> Iterator[Path]:
    """临时切换工作目录，避免影响真实文件系统。"""
    original = Path.cwd()
    try:
        sys.path.insert(0, str(tmp_path))
        yield tmp_path
    finally:
        sys.path.remove(str(tmp_path))
        sys.chdir(original)
