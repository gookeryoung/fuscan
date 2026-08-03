"""扫描模式常量与映射（GUI 层单一来源）。

fuscan 的扫描模式在 :class:`fuscan.config.Config.scan_mode` 中以字符串存储
（``"drive"``/``"folder"``），QML 侧用索引切换模式更直观，
故 GUI 层需要 ``索引 ↔ 字符串 ↔ 中文文本`` 三向映射。

历史上 ``_SCAN_MODE_INDEX_TO_STR`` / ``_MODE_STR_TO_INDEX`` 曾在
``workspace_model.py`` / ``scan_controller.py`` / ``workspace_controller.py``
三处重复定义（注释互指"一致"），本模块集中为唯一来源。

公共 API：

- :data:`SCAN_MODE_INDEX_TO_STR`：索引 → 模式字符串元组（顺序固定）
- :data:`SCAN_MODE_STR_TO_INDEX`：模式字符串 → 索引（反向映射）
- :data:`SCAN_MODE_STR_TO_TEXT`：模式字符串 → 中文展示文本
- :data:`SCAN_MODE_DEFAULT_INDEX`：默认索引（文件夹模式）
- :func:`scan_mode_text`：按模式字符串取中文展示文本
- :func:`scan_mode_index_to_str`：按索引取模式字符串，越界返回 None
- :func:`scan_mode_str_to_index`：按模式字符串取索引，未知返回默认索引
"""

from __future__ import annotations

__all__ = [
    "SCAN_MODE_DEFAULT_INDEX",
    "SCAN_MODE_INDEX_TO_STR",
    "SCAN_MODE_STR_TO_INDEX",
    "SCAN_MODE_STR_TO_TEXT",
    "scan_mode_index_to_str",
    "scan_mode_str_to_index",
    "scan_mode_text",
]

# 索引 → 模式字符串（顺序与 QML 切换控件一致：0=盘符 / 1=文件夹）
SCAN_MODE_INDEX_TO_STR: tuple[str, ...] = ("drive", "folder")

# 模式字符串 → 索引（反向映射，由 SCAN_MODE_INDEX_TO_STR 派生）
SCAN_MODE_STR_TO_INDEX: dict[str, int] = {s: i for i, s in enumerate(SCAN_MODE_INDEX_TO_STR)}

# 模式字符串 → 中文展示文本（用于工作区列表与 UI 标签）
SCAN_MODE_STR_TO_TEXT: dict[str, str] = {
    "drive": "盘符扫描",
    "folder": "文件夹扫描",
}

# 默认索引：文件夹模式（与 Config.scan_mode 默认值 "folder" 对齐）
SCAN_MODE_DEFAULT_INDEX: int = SCAN_MODE_STR_TO_INDEX.get("folder", 1)


def scan_mode_text(mode_str: str) -> str:
    """按模式字符串取中文展示文本，未知模式回退为原字符串。

    :param mode_str: 模式字符串（``"drive"``/``"folder"``）
    :return: 中文展示文本
    """
    return SCAN_MODE_STR_TO_TEXT.get(mode_str, mode_str)


def scan_mode_index_to_str(index: int) -> str | None:
    """按索引取模式字符串。

    :param index: 索引（0/1）
    :return: 模式字符串；越界返回 ``None``
    """
    if 0 <= index < len(SCAN_MODE_INDEX_TO_STR):
        return SCAN_MODE_INDEX_TO_STR[index]
    return None


def scan_mode_str_to_index(mode_str: str) -> int:
    """按模式字符串取索引，未知模式回退到默认索引。

    :param mode_str: 模式字符串
    :return: 索引（0/1）
    """
    return SCAN_MODE_STR_TO_INDEX.get(mode_str, SCAN_MODE_DEFAULT_INDEX)
