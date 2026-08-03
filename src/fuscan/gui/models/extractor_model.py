"""提取器勾选列表模型（QAbstractListModel）。

扁平化展示所有提取器（按 ``(category_order, display_name)`` 排序，同类相邻），
每行包含类名、显示名、扩展名、速度档次（文本/色值）、格式标签、类别与勾选状态，
供 QML ``ListView`` 直接绑定；QML 侧通过 ``section.property: "category"``
实现按类别分组渲染，类别头部仅展示类别名（勾选状态由各行 CheckBox 体现）。

公共 API：

- :class:`ExtractorListModel`：``QAbstractListModel`` 子类
- :meth:`ExtractorListModel.load_from_registry`：从默认注册表加载所有提取器
- :meth:`ExtractorListModel.set_disabled_extractors`：按 Config.disabled_extractors 配置勾选
- :meth:`ExtractorListModel.disabled_extractors`：返回当前未勾选的提取器类名列表
- :meth:`ExtractorListModel.enabled_extensions`：返回勾选提取器的扩展名集合
- :meth:`ExtractorListModel.set_extractor_enabled`：QML 勾选回调
- :meth:`ExtractorListModel.select_all` / :meth:`unselect_all`：全选/全不选
"""

from __future__ import annotations

import re

try:
    from PySide2.QtCore import QAbstractListModel, QModelIndex, Qt
except ImportError:  # pragma: no cover
    from PySide6.QtCore import (  # pyrefly: ignore [missing-import]
        QAbstractListModel,
        QModelIndex,
        Qt,
    )

from fuscan.extractors.base import SpeedTier, default_registry

__all__ = ["ExtractorListModel"]

# QML role 名称
_ROLE_CLASS_NAME = b"className"
_ROLE_DISPLAY_NAME = b"displayName"
_ROLE_EXTENSIONS = b"extensions"
_ROLE_SPEED_TIER_TEXT = b"speedTierText"
_ROLE_SPEED_TIER_COLOR = b"speedTierColor"
_ROLE_ENABLED = b"enabled"
_ROLE_FORMAT_LABEL = b"formatLabel"
_ROLE_CATEGORY = b"category"
_ROLE_FORMAT_TAGS = b"formatTags"
_ROLE_ENGINE_INFO = b"engineInfo"

_ROLES: dict[int, bytes] = {
    Qt.UserRole + 1: _ROLE_CLASS_NAME,
    Qt.UserRole + 2: _ROLE_DISPLAY_NAME,
    Qt.UserRole + 3: _ROLE_EXTENSIONS,
    Qt.UserRole + 4: _ROLE_SPEED_TIER_TEXT,
    Qt.UserRole + 5: _ROLE_SPEED_TIER_COLOR,
    Qt.UserRole + 6: _ROLE_ENABLED,
    Qt.UserRole + 7: _ROLE_FORMAT_LABEL,
    Qt.UserRole + 8: _ROLE_CATEGORY,
    Qt.UserRole + 9: _ROLE_FORMAT_TAGS,
    Qt.UserRole + 10: _ROLE_ENGINE_INFO,
}

# 提取 display_name 中全角括号内的格式标签（如 "Word（DOCX）" → "DOCX"）
_PAREN_RE = re.compile(r"（([^）]*)）")

# 类别显示顺序（按用户勾选习惯排列：文档优先 → PDF/RTF → 文本 → 邮件 → 压缩包 → 其他）
_CATEGORY_ORDER: tuple[str, ...] = ("Office 文档", "PDF/RTF", "文本", "邮件", "压缩包", "其他")

# 按提取器 class_name 映射到类别（避免 display_name 字符串匹配的脆弱性）
_CATEGORY_BY_CLASS: dict[str, str] = {
    "DocxExtractor": "Office 文档",
    "DocExtractor": "Office 文档",
    "XlsxExtractor": "Office 文档",
    "XlsExtractor": "Office 文档",
    "PptxExtractor": "Office 文档",
    "PptExtractor": "Office 文档",
    "WpsExtractor": "Office 文档",
    "OdtExtractor": "Office 文档",
    "OdsExtractor": "Office 文档",
    "PdfExtractor": "PDF/RTF",
    "RtfExtractor": "PDF/RTF",
    "PlainTextExtractor": "文本",
    "SourceCodeExtractor": "文本",
    "EmlExtractor": "邮件",
    "MsgExtractor": "邮件",
    "ZipArchiveExtractor": "压缩包",
    "RarArchiveExtractor": "压缩包",
    "SevenZArchiveExtractor": "压缩包",
}

# 压缩包虚拟行定义：class_name、显示名、扩展名。
# 压缩包扫描由 :class:`fuscan.archive.ArchiveScanner` 处理，不通过 Extractor 接口，
# 因此在文件类型树中以虚拟行展示，扩展名走白名单过滤。
_ARCHIVE_VIRTUAL_ROWS: tuple[tuple[str, str, str], ...] = (
    ("ZipArchiveExtractor", "ZIP 压缩包", "zip"),
    ("RarArchiveExtractor", "RAR 压缩包", "rar"),
    ("SevenZArchiveExtractor", "7z 压缩包", "7z"),
)

# 扩展名较多的提取器代表性格式标签：
# 当提取器支持的扩展名超过 4 个时，formatLabel（如 "CODE"）不足以体现覆盖范围，
# 因此在文件类型树中以多个代表性 tag 展示（如 HTML/XML/JSON/JS/C/CPP/PY/SH）。
# 未在此映射中的提取器默认使用 ``(format_label,)`` 单标签。
_FORMAT_TAGS_BY_CLASS: dict[str, tuple[str, ...]] = {
    "SourceCodeExtractor": ("HTML", "XML", "JSON", "JS", "C", "CPP", "PY", "SH"),
}


def _classify(class_name: str) -> str:
    """按 class_name 返回类别名，未映射的归到「其他」。"""
    return _CATEGORY_BY_CLASS.get(class_name, "其他")


# 类别 → 排序序号映射（预构建，避免 list.index 的 O(C) 查找与 ValueError 分支）
_CATEGORY_ORDER_INDEX: dict[str, int] = {cat: i for i, cat in enumerate(_CATEGORY_ORDER)}


def _category_sort_key(category: str) -> int:
    """返回类别在 ``_CATEGORY_ORDER`` 中的序号，未列出类别排在末尾。"""
    return _CATEGORY_ORDER_INDEX.get(category, len(_CATEGORY_ORDER))


_SPEED_TIER_TEXT: dict[SpeedTier, str] = {
    SpeedTier.VERY_FAST: "T1 极速",
    SpeedTier.FAST: "T2 快速",
    SpeedTier.MEDIUM: "T3 中速",
    SpeedTier.SLOW: "T4 慢速",
    SpeedTier.VERY_SLOW: "T5 极慢",
}

_SPEED_TIER_COLOR: dict[SpeedTier, str] = {
    SpeedTier.VERY_FAST: "#28A745",
    SpeedTier.FAST: "#17A2B8",
    SpeedTier.MEDIUM: "#FFC107",
    SpeedTier.SLOW: "#FD7E14",
    SpeedTier.VERY_SLOW: "#DC3545",
}


class _ExtractorRow:
    """提取器行数据（内部可变容器）。"""

    __slots__ = (
        "category",
        "class_name",
        "display_name",
        "enabled",
        "engine_info",
        "extensions",
        "format_label",
        "format_tags",
        "speed_tier",
    )

    def __init__(
        self,
        class_name: str,
        display_name: str,
        extensions: tuple[str, ...],
        speed_tier: SpeedTier,
        enabled: bool,
        engine_info: str = "",
    ) -> None:
        self.class_name = class_name
        # 提取全角括号内的格式标签（如 "Word（DOCX）" → "DOCX"）；
        # 无括号时回退到首扩展名大写（如 "PDF" → "PDF"、"纯文本" → "TXT"）
        paren_match = _PAREN_RE.search(display_name)
        if paren_match is not None:
            self.format_label = paren_match.group(1).strip().upper()
        elif extensions:
            self.format_label = extensions[0].upper()
        else:  # pragma: no cover - 提取器注册时必带扩展名，防御性兜底
            self.format_label = class_name.upper()
        # 去掉全角括号后缀得到纯显示名（如 "Word（DOCX）" → "Word"）
        self.display_name = _PAREN_RE.sub("", display_name).strip()
        self.extensions = extensions
        self.speed_tier = speed_tier
        self.enabled = enabled
        self.engine_info = engine_info
        self.category = _classify(class_name)
        # 代表性格式标签列表：扩展名较多的提取器使用预设多标签（如 HTML/C/CPP/PY），
        # 其余默认为 ``(format_label,)`` 单标签
        self.format_tags: tuple[str, ...] = _FORMAT_TAGS_BY_CLASS.get(class_name, (self.format_label,))


class ExtractorListModel(QAbstractListModel):  # pyrefly: ignore [invalid-inheritance]
    """提取器勾选列表模型（扁平化，按 category 分组排序）。

    行按 ``(category_order, display_name)`` 排序，QML ``ListView`` 可通过
    ``section.property: "category"`` 实现分组渲染。类别头部仅展示类别名，
    勾选状态由各行提取器 CheckBox 体现（移除三态 CheckBox）。
    """

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._rows: list[_ExtractorRow] = []

    def rowCount(self, parent: QModelIndex = None) -> int:  # type: ignore[assignment]
        if parent is None:
            parent = QModelIndex()
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, bytes]:
        return _ROLES

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return ""
        row = self._rows[index.row()]
        if role == Qt.UserRole + 1:
            return row.class_name
        if role == Qt.UserRole + 2:
            return row.display_name
        if role == Qt.UserRole + 3:
            return ", ".join(row.extensions)
        if role == Qt.UserRole + 4:
            return _SPEED_TIER_TEXT[row.speed_tier]
        if role == Qt.UserRole + 5:
            return _SPEED_TIER_COLOR[row.speed_tier]
        if role == Qt.UserRole + 6:
            return row.enabled
        if role == Qt.UserRole + 7:
            return row.format_label
        if role == Qt.UserRole + 8:
            return row.category
        if role == Qt.UserRole + 9:
            # 返回 list（QML 端 Repeater 可直接用 modelData 访问）
            return list(row.format_tags)
        if role == Qt.UserRole + 10:
            # 返回引擎信息字符串（如 "pypdf" / "python-calamine"）
            return row.engine_info
        return ""

    # ----------------------------- 公共 API -----------------------------

    def load_from_registry(self, disabled_extractors: list[str] | None = None) -> None:
        """从默认注册表加载所有提取器，按 disabled_extractors 配置勾选。

        行按 ``(category_order, display_name)`` 排序，使同类提取器相邻，
        便于 QML ``ListView.section`` 按 ``category`` 角色分组渲染。

        压缩包类别（ZIP/RAR/7z）为虚拟行，不对应实际提取器类（压缩包扫描
        由 :class:`fuscan.archive.ArchiveScanner` 处理），仅用于文件类型树勾选
        与扩展名白名单过滤。勾选状态通过 ``disabled_extractors`` 持久化，
        与 ``Config.scan_archives`` 总开关配合使用：扫描器仅在
        ``scan_archives=True`` 且扩展名在白名单内时才扫描对应压缩包格式。

        :param disabled_extractors: 未勾选的提取器类名列表（来自 ``Config.disabled_extractors``）
        """
        disabled_set = set(disabled_extractors or [])
        self.beginResetModel()
        rows: list[_ExtractorRow] = []
        for class_name, display_name, extensions, speed_tier, engine_info in default_registry.list_extractors():
            rows.append(
                _ExtractorRow(
                    class_name=class_name,
                    display_name=display_name,
                    extensions=extensions,
                    speed_tier=speed_tier,
                    enabled=class_name not in disabled_set,
                    engine_info=engine_info,
                )
            )
        # 压缩包虚拟行：扩展名走白名单，扫描由 ArchiveScanner 在 scan_archives=True 时执行。
        # 速度档次标注为 T3 中速（实际解压耗时取决于压缩包大小与条目数）。
        for class_name, display_name, ext in _ARCHIVE_VIRTUAL_ROWS:
            rows.append(
                _ExtractorRow(
                    class_name=class_name,
                    display_name=display_name,
                    extensions=(ext,),
                    speed_tier=SpeedTier.MEDIUM,
                    enabled=class_name not in disabled_set,
                    engine_info="zipfile/tarfile/rarfile/py7zr",
                )
            )
        # 按 (category_order, display_name) 排序，保证同类相邻且类内稳定
        rows.sort(key=lambda r: (_category_sort_key(r.category), r.display_name))
        self._rows = rows
        self.endResetModel()

    def set_disabled_extractors(self, disabled: list[str]) -> None:
        """按 disabled 列表批量更新勾选状态。"""
        disabled_set = set(disabled)
        for i, row in enumerate(self._rows):
            new_enabled = row.class_name not in disabled_set
            if row.enabled != new_enabled:
                row.enabled = new_enabled
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, [Qt.UserRole + 6])

    def disabled_extractors(self) -> list[str]:
        """返回未勾选的提取器类名列表。"""
        return [row.class_name for row in self._rows if not row.enabled]

    def enabled_extensions(self) -> tuple[str, ...] | None:
        """返回勾选提取器的扩展名集合（用于扫描时白名单过滤）。

        :return: 三种语义（与 :class:`fuscan.scanner.Scanner` ``scan_extensions`` 参数一致）：

            - ``None``：全部勾选，扫描所有文件（快速路径）
            - 空 tuple：全部取消勾选，不扫描任何文件（防御性边界）
            - 非空 tuple：仅扫描扩展名在白名单中的文件（已小写、去点、排序、去重）
        """
        # 空模型（尚未加载注册表）时 all([]) 为 True，会误判为「全部勾选」；
        # 此时无任何提取器可用，应返回空 tuple（不扫描任何文件）
        if not self._rows:
            return ()
        if all(row.enabled for row in self._rows):
            return None
        exts: list[str] = []
        for row in self._rows:
            if row.enabled:
                exts.extend(row.extensions)
        return tuple(sorted(set(exts)))

    def set_extractor_enabled(self, class_name: str, enabled: bool) -> None:
        """QML 勾选回调：按类名更新勾选状态。"""
        for i, row in enumerate(self._rows):
            if row.class_name == class_name:
                if row.enabled != enabled:
                    row.enabled = enabled
                    idx = self.index(i)
                    self.dataChanged.emit(idx, idx, [Qt.UserRole + 6])
                return

    def set_category_enabled(self, category: str, enabled: bool) -> bool:
        """批量设置某类别下所有提取器的勾选状态（父节点统一勾选）。

        :param category: 类别名（如 ``"Office 文档"``/``"文本"``）
        :param enabled: True=全部勾选，False=全部取消勾选
        :return: 是否实际修改了任意行（无变化返回 False）
        """
        changed = False
        for i, row in enumerate(self._rows):
            if row.category == category and row.enabled != enabled:
                row.enabled = enabled
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, [Qt.UserRole + 6])
                changed = True
        return changed

    def category_enabled_state(self, category: str) -> int:
        """返回类别勾选状态（父节点三态显示）。

        :param category: 类别名
        :return: 三态值

            - ``0``：全部未勾选
            - ``1``：全部勾选
            - ``2``：部分勾选
        """
        rows = [row for row in self._rows if row.category == category]
        if not rows:
            return 0
        enabled_count = sum(1 for row in rows if row.enabled)
        if enabled_count == 0:
            return 0
        if enabled_count == len(rows):
            return 1
        return 2

    def select_all(self) -> None:
        """全选。"""
        self._set_all_enabled(True)

    def unselect_all(self) -> None:
        """全不选。"""
        self._set_all_enabled(False)

    @property
    def total_count(self) -> int:
        """提取器总数。"""
        return len(self._rows)

    @property
    def enabled_count(self) -> int:
        """已勾选提取器数。"""
        return sum(1 for row in self._rows if row.enabled)

    # ----------------------------- 内部方法 -----------------------------

    def _set_all_enabled(self, enabled: bool) -> None:
        """批量设置所有提取器勾选状态并 emit dataChanged。"""
        for row in self._rows:
            row.enabled = enabled
        if self._rows:
            top = self.index(0)
            bottom = self.index(len(self._rows) - 1)
            self.dataChanged.emit(top, bottom, [Qt.UserRole + 6])
