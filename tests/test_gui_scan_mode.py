"""``fuscan.gui.scan_mode`` 模块单元测试。

覆盖 ``scan_mode_index_to_str`` / ``scan_mode_str_to_index`` / ``scan_mode_text``
三向映射的所有分支，包括越界与未知模式的回退路径。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.gui

from fuscan.gui.scan_mode import (  # noqa: E402
    SCAN_MODE_DEFAULT_INDEX,
    SCAN_MODE_INDEX_TO_STR,
    SCAN_MODE_STR_TO_INDEX,
    SCAN_MODE_STR_TO_TEXT,
    scan_mode_index_to_str,
    scan_mode_str_to_index,
    scan_mode_text,
)


class TestScanModeIndexToStr:
    def test_valid_indices(self) -> None:
        """0/1 索引应映射到对应模式字符串。"""
        assert scan_mode_index_to_str(0) == "drive"
        assert scan_mode_index_to_str(1) == "folder"

    def test_negative_index_returns_none(self) -> None:
        """负索引越界返回 None。"""
        assert scan_mode_index_to_str(-1) is None

    def test_out_of_range_returns_none(self) -> None:
        """超出范围的索引返回 None。"""
        assert scan_mode_index_to_str(2) is None
        assert scan_mode_index_to_str(99) is None


class TestScanModeStrToIndex:
    def test_known_modes(self) -> None:
        """已知模式字符串返回对应索引。"""
        assert scan_mode_str_to_index("drive") == 0
        assert scan_mode_str_to_index("folder") == 1

    def test_unknown_mode_returns_default(self) -> None:
        """未知模式字符串回退到默认索引（文件夹模式）。"""
        assert scan_mode_str_to_index("nonexistent") == SCAN_MODE_DEFAULT_INDEX
        assert scan_mode_str_to_index("") == SCAN_MODE_DEFAULT_INDEX
        # full 已废弃，回退到默认索引（文件夹）
        assert scan_mode_str_to_index("full") == SCAN_MODE_DEFAULT_INDEX


class TestScanModeText:
    def test_known_modes_text(self) -> None:
        """已知模式返回中文展示文本。"""
        assert scan_mode_text("drive") == "盘符扫描"
        assert scan_mode_text("folder") == "文件夹扫描"

    def test_unknown_mode_returns_input(self) -> None:
        """未知模式回退为原字符串。"""
        assert scan_mode_text("custom") == "custom"
        assert scan_mode_text("") == ""
        # full 已废弃，回退为原字符串
        assert scan_mode_text("full") == "full"


class TestConstantsConsistency:
    """常量之间的一致性。"""

    def test_index_to_str_length(self) -> None:
        assert len(SCAN_MODE_INDEX_TO_STR) == 2

    def test_str_to_index_covers_all_modes(self) -> None:
        """SCAN_MODE_STR_TO_INDEX 应覆盖所有 SCAN_MODE_INDEX_TO_STR 中的模式。"""
        for mode in SCAN_MODE_INDEX_TO_STR:
            assert mode in SCAN_MODE_STR_TO_INDEX

    def test_str_to_text_covers_all_modes(self) -> None:
        """SCAN_MODE_STR_TO_TEXT 应覆盖所有 SCAN_MODE_INDEX_TO_STR 中的模式。"""
        for mode in SCAN_MODE_INDEX_TO_STR:
            assert mode in SCAN_MODE_STR_TO_TEXT

    def test_default_index_is_folder(self) -> None:
        """默认索引应为文件夹模式（与 Config.scan_mode 默认值对齐）。"""
        assert SCAN_MODE_DEFAULT_INDEX == 1
        assert SCAN_MODE_INDEX_TO_STR[SCAN_MODE_DEFAULT_INDEX] == "folder"
