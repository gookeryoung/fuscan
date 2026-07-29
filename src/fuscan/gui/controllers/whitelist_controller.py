"""白名单控制器：QML ↔ WhitelistStore 持久化桥接。

暴露 :class:`WhitelistStore` 的增删查改与导入导出为 ``@Property``/``@Slot``，
QML 控件 ``onAccepted``/``onClicked`` 直接调用 Slot 修改白名单。

公共 API：

- :class:`WhitelistController`：``QObject`` 子类
- :meth:`WhitelistController.addEntry`：添加白名单条目（路径 glob + 规则名 + 备注）
- :meth:`WhitelistController.removeEntry`：按索引移除条目
- :meth:`WhitelistController.clearAll`：清空全部条目
- :meth:`WhitelistController.importJson` / :meth:`exportJson`：导入导出 JSON 文件
- :meth:`WhitelistController.snapshot`：返回不可变快照供 ScanController 注入 Scanner
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]

from fuscan.rules.whitelist import Whitelist, WhitelistEntry, WhitelistStore

if TYPE_CHECKING:
    pass

__all__ = ["WhitelistController"]

logger = logging.getLogger(__name__)


class WhitelistController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """白名单控制器。

    持有 :class:`WhitelistStore` 实例，通过 ``@Property`` 暴露条目列表给 QML
    ``ListView`` 绑定，``@Slot`` 接收 QML 操作（增删/导入导出/清空）。

    :param parent: 父 QObject
    :param store: 白名单存储实例（测试可注入）；``None`` 使用默认路径
    """

    whitelistChanged = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        store: WhitelistStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._store: WhitelistStore = store if store is not None else WhitelistStore()

    @property
    def store(self) -> WhitelistStore:
        """底层 :class:`WhitelistStore` 实例（供 ScanController 取快照）。"""
        return self._store

    @Property("QVariantList", notify=whitelistChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def whitelistEntries(self) -> list[dict[str, str]]:
        """白名单条目列表（QML ListView 绑定）。

        每项格式：``{"pathGlob": str, "ruleName": str, "createdAt": str, "note": str}``。
        ``ruleName`` 为 ``*`` 表示匹配任意规则（UI 显示为「全部规则」）。
        """
        return [
            {
                "pathGlob": entry.path_glob,
                "ruleName": entry.rule_name,
                "createdAt": entry.created_at,
                "note": entry.note,
            }
            for entry in self._store.entries()
        ]

    @Property(int, notify=whitelistChanged)  # pyrefly: ignore [not-callable]
    def whitelistCount(self) -> int:
        """白名单条目总数。"""
        return len(self._store.entries())

    @Slot(str, str, str, result=str)  # pyrefly: ignore [not-callable]
    def addEntry(self, path_glob: str, rule_name: str, note: str) -> str:
        """添加白名单条目并持久化。

        :param path_glob: 路径 glob 模式（如 ``/a/vendor/*.txt``）
        :param rule_name: 规则名；空字符串视为 ``*``（匹配任意规则）
        :param note: 用户备注（可空）
        :return: 操作消息（成功/失败原因）

        添加后发射 :pyattr:`whitelistChanged` 信号，QML ListView 自动刷新。
        """
        path_glob = path_glob.strip()
        if not path_glob:
            return "路径模式不能为空"
        rule_name = rule_name.strip() or "*"
        created_at = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            entry = WhitelistEntry(
                path_glob=path_glob,
                rule_name=rule_name,
                created_at=created_at,
                note=note.strip(),
            )
        except ValueError as exc:
            return f"添加失败: {exc}"
        before = len(self._store.entries())
        self._store.add(entry)
        after = len(self._store.entries())
        if after <= before:
            # 重复条目：store 去重未新增，不发射信号避免无效刷新
            return f"已存在: {path_glob} ({rule_name})"
        self.whitelistChanged.emit()  # pyrefly: ignore [missing-attribute]
        logger.info("已添加白名单: %s (%s)", path_glob, rule_name)
        return f"已添加: {path_glob} ({rule_name})"

    @Slot(int, result=bool)  # pyrefly: ignore [not-callable]
    def removeEntry(self, index: int) -> bool:
        """按索引移除白名单条目。

        :param index: 条目索引（与 ``whitelistEntries`` 顺序一致）
        :return: 移除成功返回 ``True``，索引越界返回 ``False``
        """
        if self._store.remove_at(index):
            self.whitelistChanged.emit()  # pyrefly: ignore [missing-attribute]
            return True
        return False

    @Slot(str, str, result=bool)  # pyrefly: ignore [not-callable]
    def removeByGlobAndRule(self, path_glob: str, rule_name: str) -> bool:
        """按 (路径 glob, 规则名) 移除条目。

        :param path_glob: 路径 glob 模式（精确匹配）
        :param rule_name: 规则名（精确匹配，含 ``*``）
        :return: 实际移除返回 ``True``，不存在返回 ``False``
        """
        before = len(self._store.entries())
        self._store.remove(path_glob, rule_name)
        if len(self._store.entries()) != before:
            self.whitelistChanged.emit()  # pyrefly: ignore [missing-attribute]
            return True
        return False

    @Slot()  # pyrefly: ignore [not-callable]
    def clearAll(self) -> None:
        """清空全部白名单条目。"""
        self._store.clear()
        self.whitelistChanged.emit()  # pyrefly: ignore [missing-attribute]
        logger.info("白名单已清空")

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def importJson(self, path_str: str) -> str:
        """从 JSON 文件导入白名单（合并到现有条目，去重）。

        :param path_str: JSON 文件路径（由 QML FileDialog 传入）
        :return: 操作消息（含实际新增条目数）
        """
        if not path_str:
            return "未选择文件"
        try:
            data = Path(path_str).read_bytes()
            added = self._store.import_json(data)
        except (OSError, ValueError) as exc:
            logger.warning("白名单导入失败: %s", path_str, exc_info=True)
            return f"导入失败: {exc}"
        self.whitelistChanged.emit()  # pyrefly: ignore [missing-attribute]
        return f"已导入 {added} 条（去重后）"

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def exportJson(self, path_str: str) -> str:
        """导出当前白名单到 JSON 文件。

        :param path_str: 目标 JSON 文件路径（由 QML FileDialog 传入）
        :return: 操作消息
        """
        if not path_str:
            return "未选择文件"
        try:
            Path(path_str).write_text(self._store.export_json(), encoding="utf-8")
        except OSError as exc:
            logger.warning("白名单导出失败: %s", path_str, exc_info=True)
            return f"导出失败: {exc}"
        return f"已导出到: {path_str}"

    def snapshot(self) -> Whitelist:
        """返回当前白名单的不可变快照（供 ScanController 注入 Scanner）。"""
        return self._store.snapshot()
