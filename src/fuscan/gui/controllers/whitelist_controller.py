"""白名单控制器：QML ↔ WhitelistStore/RuleSet 白名单桥接。

历史职责：管理 :class:`WhitelistStore`（``~/.fuscan/whitelist.json``）的增删查改
与导入导出，扫描时通过 :meth:`snapshot` 返回不可变 :class:`Whitelist` 供
:class:`Scanner` 注入命中聚合阶段过滤误报。

规则系统重构后，白名单统一纳入 RuleSet 顶层（``whitelist`` 段）。新增白名单条目
委托 :meth:`RulesController.appendWhitelistEntry` 写入 ``user-scan.yaml``，
与规则文件共享同一持久化路径。本控制器保留 :class:`WhitelistStore` 用于：

1. 加载历史 ``whitelist.json``（兼容老用户数据，``source="runtime"`` 条目）
2. 导入/导出 JSON（与外部系统交换白名单）
3. 按索引/路径移除条目（仅作用于 JSON store 中的历史条目）

:meth:`snapshot` 与 :attr:`whitelistEntries` 合并 JSON store 与 effective
ruleset 的白名单条目，确保扫描过滤与 UI 展示覆盖全部来源。

公共 API：

- :class:`WhitelistController`：``QObject`` 子类
- :meth:`WhitelistController.addEntry`：添加白名单条目（委托 rules_controller 写入 user-scan.yaml）
- :meth:`WhitelistController.removeEntry`：按索引移除条目（仅 JSON store 历史条目）
- :meth:`WhitelistController.clearAll`：清空 JSON store 全部条目
- :meth:`WhitelistController.importJson` / :meth:`exportJson`：导入导出 JSON 文件
- :meth:`WhitelistController.snapshot`：返回合并 JSON store + ruleset 的不可变快照
- :meth:`WhitelistController.set_rules_controller`：延迟注入 RulesController
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
    from fuscan.gui.controllers.rules_controller import RulesController

__all__ = ["WhitelistController"]

logger = logging.getLogger(__name__)


class WhitelistController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """白名单控制器。

    持有 :class:`WhitelistStore` 实例（兼容历史 JSON 数据），并通过延迟注入的
    :class:`RulesController` 把新增条目写入 ``user-scan.yaml`` 的 ``whitelist``
    段。QML 通过 ``@Property`` 读取合并后的条目列表，``@Slot`` 接收增删/导入导出
    操作。

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
        # 延迟注入的 RulesController 引用（app_controller 构造后注入）。
        # 为 None 时（独立测试/旧路径）addEntry 回退到 WhitelistStore JSON 持久化。
        self._rules_controller: RulesController | None = None

    def set_rules_controller(self, rc: RulesController) -> None:
        """延迟注入 :class:`RulesController`（避免构造期循环依赖）。

        注入后 :meth:`addEntry` 委托 :meth:`RulesController.appendWhitelistEntry`
        写入 ``user-scan.yaml``，并监听 ``rulesetChanged`` 触发
        ``whitelistChanged`` 让 QML 列表刷新。

        :param rc: 规则控制器实例
        """
        self._rules_controller = rc
        rc.rulesetChanged.connect(self._on_ruleset_changed)  # pyrefly: ignore [missing-attribute]

    def _on_ruleset_changed(self) -> None:
        """规则集变更（含 user-scan.yaml 白名单追加）触发白名单列表刷新。"""
        self.whitelistChanged.emit()  # pyrefly: ignore [missing-attribute]

    @property
    def store(self) -> WhitelistStore:
        """底层 :class:`WhitelistStore` 实例（供 ScanController 取 JSON store 快照）。"""
        return self._store

    def _combined_entries(self) -> tuple[WhitelistEntry, ...]:
        """合并 JSON store 历史条目与 effective ruleset 白名单条目。

        :return: 合并后的 :class:`WhitelistEntry` 元组（JSON store 在前，
            ruleset 在后）。ruleset 为 None 或未注入时仅返回 JSON store 条目。
        """
        store_entries = self._store.entries()
        if self._rules_controller is None:
            return store_entries
        ruleset = self._rules_controller.ruleset
        if ruleset is None:
            return store_entries
        # 去重：相同 (path_glob, rule_name) 的条目只保留一处（JSON store 优先）
        seen = {(e.path_glob, e.rule_name) for e in store_entries}
        combined = list(store_entries)
        for entry in ruleset.whitelist:
            key = (entry.path_glob, entry.rule_name)
            if key not in seen:
                seen.add(key)
                combined.append(entry)
        return tuple(combined)

    @Property("QVariantList", notify=whitelistChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def whitelistEntries(self) -> list[dict[str, str]]:
        """白名单条目列表（QML ListView 绑定）。

        合并 JSON store 与 effective ruleset 的白名单条目。每项格式：
        ``{"pathGlob": str, "ruleName": str, "createdAt": str, "note": str,
        "source": str}``。``ruleName`` 为 ``*`` 表示匹配任意规则（UI 显示为「全部规则」）。
        ``source`` 为 ``"rules"``（规则文件预定义）或 ``"runtime"``（运行时写入）。
        """
        return [
            {
                "pathGlob": entry.path_glob,
                "ruleName": entry.rule_name,
                "createdAt": entry.created_at,
                "note": entry.note,
                "source": entry.source,
            }
            for entry in self._combined_entries()
        ]

    @Property(int, notify=whitelistChanged)  # pyrefly: ignore [not-callable]
    def whitelistCount(self) -> int:
        """白名单条目总数（JSON store + ruleset 合并去重后）。"""
        return len(self._combined_entries())

    @Slot(str, str, str, result=str)  # pyrefly: ignore [not-callable]
    def addEntry(self, path_glob: str, rule_name: str, note: str) -> str:
        """添加白名单条目并持久化。

        已注入 :class:`RulesController` 时委托 :meth:`RulesController.appendWhitelistEntry`
        写入 ``user-scan.yaml``；未注入时回退到 :class:`WhitelistStore` JSON 持久化
        （独立测试/旧路径兼容）。

        :param path_glob: 路径 glob 模式（如 ``/a/vendor/*.txt``）
        :param rule_name: 规则名；空字符串视为 ``*``（匹配任意规则）
        :param note: 用户备注（可空）
        :return: 操作消息（成功/失败原因）

        添加后发射 :pyattr:`whitelistChanged` 信号，QML ListView 自动刷新。
        """
        if self._rules_controller is not None:
            msg = self._rules_controller.appendWhitelistEntry(path_glob, rule_name, note)
            # appendWhitelistEntry 内部 emit rulesetChanged → _on_ruleset_changed
            # 已触发 whitelistChanged，此处无需重复 emit
            return msg

        # 回退路径：写入 JSON store（独立测试或未注入 rules_controller 时）
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
                source="runtime",
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
        logger.info("已添加白名单（JSON store 回退）: %s (%s)", path_glob, rule_name)
        return f"已添加: {path_glob} ({rule_name})"

    @Slot(int, result=bool)  # pyrefly: ignore [not-callable]
    def removeEntry(self, index: int) -> bool:
        """按索引移除白名单条目。

        仅作用于 :class:`WhitelistStore` JSON store 中的历史条目。
        ``user-scan.yaml`` 中的白名单条目需用户手工编辑该文件移除。

        :param index: 条目索引（与 ``whitelistEntries`` 顺序一致）
        :return: 移除成功返回 ``True``，索引越界或属于 ruleset 条目返回 ``False``
        """
        if self._store.remove_at(index):
            self.whitelistChanged.emit()  # pyrefly: ignore [missing-attribute]
            return True
        return False

    @Slot(str, str, result=bool)  # pyrefly: ignore [not-callable]
    def removeByGlobAndRule(self, path_glob: str, rule_name: str) -> bool:
        """按 (路径 glob, 规则名) 移除条目。

        仅作用于 :class:`WhitelistStore` JSON store 中的历史条目。

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
        """清空 :class:`WhitelistStore` JSON store 全部条目。

        不影响 ``user-scan.yaml`` 中的白名单条目（需用户手工编辑该文件清空）。
        """
        self._store.clear()
        self.whitelistChanged.emit()  # pyrefly: ignore [missing-attribute]
        logger.info("白名单 JSON store 已清空")

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def importJson(self, path_str: str) -> str:
        """从 JSON 文件导入白名单到 :class:`WhitelistStore`（合并去重）。

        导入的条目写入 JSON store（``source="runtime"``），不影响 ``user-scan.yaml``。
        与 :meth:`addEntry` 委托 rules_controller 的路径不同——导入保留 JSON store
        作为批量交换格式，便于与外部系统互通。

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
        """导出当前合并白名单（JSON store + ruleset）到 JSON 文件。

        :param path_str: 目标 JSON 文件路径（由 QML FileDialog 传入）
        :return: 操作消息
        """
        if not path_str:
            return "未选择文件"
        try:
            combined = Whitelist(entries=self._combined_entries())
            Path(path_str).write_text(combined.to_json(), encoding="utf-8")
        except OSError as exc:
            logger.warning("白名单导出失败: %s", path_str, exc_info=True)
            return f"导出失败: {exc}"
        return f"已导出到: {path_str}"

    def snapshot(self) -> Whitelist:
        """返回当前合并白名单的不可变快照（供 ScanController 注入 Scanner）。

        合并 :class:`WhitelistStore` JSON store 历史条目与 effective ruleset
        白名单条目，去重后返回 :class:`Whitelist`。
        """
        return Whitelist(entries=self._combined_entries())
