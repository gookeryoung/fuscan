"""规则控制器：QML ↔ RuleSet/规则文件管理桥接。

管理全局规则与任务级临时规则两类规则文件：

- **全局规则**：持久化到 :class:`Config`，所有工作区共享。
  内置规则归入全局规则列表（默认启用、不可移除、可禁用）；
  用户加载的全局规则文件可勾选启用/禁用、可移除。
- **临时规则**：任务级覆盖，仅对当前选中工作区生效，叠加在全局规则之上。
  通过 ``task_overrides["temp_rules_paths"]`` 持久化到工作区配置。

规则列表通过 :class:`RuleListModel` 暴露给 QML ``ListView`` 绑定，
规则文件列表（全局 + 临时合并）通过 ``@Property`` 暴露 ``QVariantList``。

公共 API：

- :class:`RulesController`：``QObject`` 子类
- :meth:`RulesController.loadFileFromPath`：加载规则文件到全局
- :meth:`RulesController.loadFileToTemp`：加载规则文件到当前工作区临时规则
- :meth:`RulesController.moveUp` / :meth:`moveDown`：全局规则文件顺序管理
- :meth:`RulesController.removeSelected`：移除选中规则文件（按作用域分派）
- :meth:`RulesController.setRuleEnabled`：勾选启用/禁用全局规则
- :meth:`RulesController.setUseBuiltin`：勾选内置规则（等价于 setRuleEnabled("__builtin__"))
- :meth:`RulesController.promoteToGlobal`：把当前工作区临时规则提升为全局规则
- :meth:`RulesController.demoteToTemp`：把全局规则降级为当前工作区临时规则
- :meth:`RulesController.exportRuleset`：导出当前规则集到 YAML/JSON
- :meth:`RulesController.importRuleset`：从 YAML/JSON 文件导入规则
- :meth:`RulesController.set_workspace_controller`：延迟注入工作区控制器
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
    from PySide2.QtWidgets import QFileDialog
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import QFileDialog  # pyrefly: ignore [missing-import]

from fuscan.config import Config
from fuscan.gui.models.rule_model import RuleListModel
from fuscan.rules import (
    RuleError,
    load_ruleset,
    merge_multiple_rulesets,
    save_ruleset,
)
from fuscan.rules.model import RuleSet

__all__ = ["RulesController"]

logger = logging.getLogger(__name__)

# 内置规则在 rulesFileModel 中的虚拟路径标识
BUILTIN_PATH_MARKER = "__builtin__"


class RulesController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """规则控制器（全局 + 临时模式）。

    :param config_controller: 配置控制器（共享 :class:`Config` 实例）
    :param parent: 父 QObject
    """

    rulesetChanged = Signal()
    rulesFileListChanged = Signal()
    selectionChanged = Signal()
    useBuiltinChanged = Signal()
    # 规则导入/导出操作结果通知（QML 据此显示 toast/对话框）
    # 参数：成功 True/False，消息文本
    rulesIoCompleted = Signal(bool, str)
    # 当前工作区变更（切换或其临时规则变更）时触发，QML 据此刷新临时规则区
    currentWorkspaceChanged = Signal()

    def __init__(self, config_controller: object, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config_controller = config_controller
        self._config: Config = config_controller.config  # pyrefly: ignore [missing-attribute]
        self._ruleset: RuleSet | None = None
        self._rule_model: RuleListModel = RuleListModel(self)
        self._selected_file_index: int = -1
        # 延迟注入的 WorkspaceController 引用（app_controller 构造后注入）
        self._workspace_controller: object | None = None
        # 初始加载规则集
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)

    # ----------------------------- 延迟注入 -----------------------------

    def set_workspace_controller(self, wc: object) -> None:
        """延迟注入 :class:`WorkspaceController`（避免构造期循环依赖）。

        注入后连接 ``currentWorkspaceChanged`` 信号，使 RulesController
        在当前工作区切换时自动刷新临时规则列表。
        """
        self._workspace_controller = wc
        wc.currentWorkspaceChanged.connect(self._on_current_workspace_changed)  # pyrefly: ignore [missing-attribute]

    def _on_current_workspace_changed(self) -> None:
        """当前工作区切换时刷新临时规则列表（触发 rulesFileListChanged）。"""
        self._selected_file_index = -1
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.currentWorkspaceChanged.emit()  # pyrefly: ignore [missing-attribute]

    # ----------------------------- 内部属性 -----------------------------

    @property
    def ruleset(self) -> RuleSet | None:
        """当前全局规则集（供 ScanController 读取，已过滤禁用的全局规则文件）。"""
        return self._ruleset

    @property
    def rules_paths(self) -> list[Path]:
        """启用的全局规则文件路径列表（供 ScanController 构造缓存上下文）。

        已过滤禁用的文件（``disabled_rules_paths``）和不存在的文件。
        """
        return [
            Path(p) for p in self._config.rules_paths if Path(p).exists() and p not in self._config.disabled_rules_paths
        ]

    @property
    def use_builtin(self) -> bool:
        """是否启用内置规则。"""
        return self._config.use_builtin

    def _current_ws_id(self) -> str:
        """当前选中工作区 ID（空串表示未选中）。"""
        if self._workspace_controller is None:
            return ""
        return self._workspace_controller.currentWorkspaceId  # pyrefly: ignore [missing-attribute]

    def _current_temp_paths(self) -> tuple[str, ...]:
        """当前工作区的临时规则文件路径元组。"""
        ws_id = self._current_ws_id()
        if not ws_id or self._workspace_controller is None:
            return ()
        item = self._workspace_controller.get_workspace(ws_id)  # pyrefly: ignore [missing-attribute]
        if item is None:
            return ()
        value = item.task_overrides.get("temp_rules_paths")
        if isinstance(value, tuple):
            return value
        return ()

    # ----------------------------- QML 属性 -----------------------------

    @Property(QObject, notify=rulesetChanged)  # pyrefly: ignore [not-callable]
    def ruleModel(self) -> RuleListModel:
        """规则列表模型。

        用 ``QObject`` 作为 Property 类型，避免 PySide2 元类型系统对
        ``QAbstractListModel*`` 未注册导致的 ``QMetaObjectBuilder`` 警告。
        """
        return self._rule_model

    @Property(int, notify=rulesetChanged)  # pyrefly: ignore [not-callable]
    def ruleCount(self) -> int:
        """当前规则数。"""
        return len(self._ruleset.rules) if self._ruleset is not None else 0

    @Property(bool, notify=useBuiltinChanged)  # pyrefly: ignore [not-callable]
    def useBuiltin(self) -> bool:
        """是否启用内置规则。"""
        return self._config.use_builtin

    @Slot(bool)  # pyrefly: ignore [not-callable]
    def setUseBuiltin(self, value: bool) -> None:
        """设置是否启用内置规则（直接修改 ``Config``）。"""
        if value != self._config.use_builtin:
            self._config.use_builtin = value
            self._config_controller.save()  # pyrefly: ignore [missing-attribute]
            self.useBuiltinChanged.emit()  # pyrefly: ignore [missing-attribute]
            self._reload_ruleset()
            self._rule_model.set_ruleset(self._ruleset)
            self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property(bool, notify=currentWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def hasCurrentWorkspace(self) -> bool:
        """是否有当前选中工作区（决定临时规则区是否可操作）。"""
        return bool(self._current_ws_id())

    @Property(str, notify=currentWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def currentWorkspaceName(self) -> str:
        """当前工作区名称（供 QML 临时规则区标题显示）。"""
        ws_id = self._current_ws_id()
        if not ws_id or self._workspace_controller is None:
            return ""
        return self._workspace_controller.workspaceName(ws_id)  # pyrefly: ignore [missing-attribute]

    @Property("QVariantList", notify=rulesFileListChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def rulesFileModel(self) -> list[dict[str, object]]:
        """规则文件列表（全局 + 临时合并，QML 直接 ListView 绑定）。

        列表顺序：内置规则（固定第一项） → 全局规则文件 → 临时规则文件。

        每项字段：
        - ``fileName``：显示名（内置规则为"内置通用规则"）
        - ``path``：文件路径（内置规则为 ``"__builtin__"`` 标识）
        - ``exists``：文件是否存在（内置规则恒为 True）
        - ``scope``：作用域，``"global"`` 或 ``"temp"``
        - ``isBuiltin``：是否内置规则
        - ``enabled``：是否启用（临时规则恒为 True，仅全局规则可禁用）
        - ``canRemove``：是否可移除（内置规则为 False，其余为 True）
        """
        items: list[dict[str, object]] = []
        # 内置规则（固定第一项）
        items.append(
            {
                "fileName": "内置通用规则",
                "path": BUILTIN_PATH_MARKER,
                "exists": True,
                "scope": "global",
                "isBuiltin": True,
                "enabled": self._config.use_builtin,
                "canRemove": False,
            }
        )
        # 全局规则文件
        for p in self._config.rules_paths:
            items.append(
                {
                    "fileName": Path(p).name,
                    "path": p,
                    "exists": Path(p).exists(),
                    "scope": "global",
                    "isBuiltin": False,
                    "enabled": p not in self._config.disabled_rules_paths,
                    "canRemove": True,
                }
            )
        # 临时规则文件（当前工作区）
        for p in self._current_temp_paths():
            items.append(
                {
                    "fileName": Path(p).name,
                    "path": p,
                    "exists": Path(p).exists(),
                    "scope": "temp",
                    "isBuiltin": False,
                    "enabled": True,
                    "canRemove": True,
                }
            )
        return items

    @Property(int, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def selectedFileIndex(self) -> int:
        """选中规则文件行号。"""
        return self._selected_file_index

    @Slot(int)  # pyrefly: ignore [not-callable]
    def setSelectedFileIndex(self, index: int) -> None:
        """设置选中规则文件行号。"""
        if index != self._selected_file_index:
            self._selected_file_index = index
            self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _selected_item(self) -> dict[str, object] | None:
        """当前选中项的 dict（无选中返回 None）。"""
        model = self.rulesFileModel
        if 0 <= self._selected_file_index < len(model):
            return model[self._selected_file_index]
        return None

    @Property(bool, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def canMoveUp(self) -> bool:
        """是否可上移选中规则文件（仅全局非内置规则可移动）。"""
        item = self._selected_item()
        if item is None or item["isBuiltin"] or item["scope"] != "global":
            return False
        # 内置规则固定索引 0，全局规则文件从索引 1 开始
        # 可上移条件：当前索引 > 1（即不是第一个全局规则文件）
        return self._selected_file_index > 1

    @Property(bool, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def canMoveDown(self) -> bool:
        """是否可下移选中规则文件（仅全局非内置规则可移动）。"""
        item = self._selected_item()
        if item is None or item["isBuiltin"] or item["scope"] != "global":
            return False
        # 全局规则文件在列表中的范围：[1, 1+len(rules_paths))
        global_end = 1 + len(self._config.rules_paths)
        return 1 <= self._selected_file_index < global_end - 1

    @Property(bool, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def canRemove(self) -> bool:
        """是否可移除选中规则文件（内置规则不可移除）。"""
        item = self._selected_item()
        if item is None:
            return False
        return bool(item["canRemove"])

    # ----------------------------- QML 调用槽 -----------------------------

    @Slot()  # pyrefly: ignore [not-callable]
    def loadFile(self) -> None:
        """弹出 QFileDialog 选择规则文件并加载（QWidget 版，QGuiApplication 下不可用）。

        .. deprecated::
            QML 应使用 :meth:`loadFileFromPath`，由 QML ``FileDialog`` 选定路径后传入。
            本方法保留供 CLI/测试场景，GUI 中调用会因 ``QGuiApplication`` 不支持
            ``QWidget`` 而无反应。
        """
        last_dir = str(Path(self._config.rules_paths[-1]).parent) if self._config.rules_paths else str(Path.home())
        path_str, _ = QFileDialog.getOpenFileName(
            None,
            "选择规则文件",
            last_dir,
            "YAML 文件 (*.yaml *.yml);;所有文件 (*.*)",
        )
        if not path_str:
            return
        self.loadFileFromPath(path_str)

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def loadFileFromPath(self, path_str: str) -> bool:
        """从路径加载规则文件到全局规则列表。

        加入 ``Config.rules_paths``，重新加载规则集并持久化。
        """
        path = Path(path_str)
        if not path.exists():
            logger.warning("规则文件不存在: %s", path_str)
            return False
        if str(path) in self._config.rules_paths:
            logger.info("规则文件已加载，跳过: %s", path_str)
            return False
        self._config.rules_paths.append(str(path))
        try:
            self._reload_ruleset()
            self._rule_model.set_ruleset(self._ruleset)
            self._config_controller.save()  # pyrefly: ignore [missing-attribute]
            self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]
            return True
        except RuleError as exc:
            # 加载失败：回滚刚加入的路径
            if str(path) in self._config.rules_paths:
                self._config.rules_paths.remove(str(path))
            logger.warning("加载规则失败: %s", exc)
            return False

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def loadFileToTemp(self, path_str: str) -> bool:
        """从路径加载规则文件到当前工作区的临时规则列表。

        :param path_str: 规则文件路径
        :return: 是否加载成功。无当前工作区或文件不存在返回 False。
        """
        ws_id = self._current_ws_id()
        if not ws_id or self._workspace_controller is None:
            logger.warning("无当前工作区，无法加载临时规则")
            self.rulesIoCompleted.emit(False, "请先在首页选择工作区")  # pyrefly: ignore [missing-attribute]
            return False

        path = Path(path_str)
        if not path.exists():
            logger.warning("规则文件不存在: %s", path_str)
            self.rulesIoCompleted.emit(False, "规则文件不存在")  # pyrefly: ignore [missing-attribute]
            return False

        current = list(self._current_temp_paths())
        if str(path) in current:
            logger.info("临时规则文件已加载，跳过: %s", path_str)
            self.rulesIoCompleted.emit(False, f"{path.name} 已在临时规则中")  # pyrefly: ignore [missing-attribute]
            return False

        # 预校验能否成功加载
        try:
            load_ruleset(path)
        except RuleError as exc:
            logger.warning("临时规则文件解析失败: %s", exc)
            self.rulesIoCompleted.emit(False, f"加载失败：{exc}")  # pyrefly: ignore [missing-attribute]
            return False

        current.append(str(path))
        # 通过 WorkspaceController.setTaskOverride 设置（自动同步到 ScanController）
        self._workspace_controller.setTaskOverride(  # pyrefly: ignore [missing-attribute]
            ws_id, "temp_rules_paths", json.dumps(current)
        )
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        msg = f"已加载临时规则 {path.name}"
        self.rulesIoCompleted.emit(True, msg)  # pyrefly: ignore [missing-attribute]
        return True

    @Slot(str, bool)  # pyrefly: ignore [not-callable]
    def setRuleEnabled(self, path: str, enabled: bool) -> None:
        """勾选启用/禁用全局规则文件。

        :param path: 规则文件路径（``"__builtin__"`` 表示内置规则）
        :param enabled: 是否启用
        """
        if path == BUILTIN_PATH_MARKER:
            self.setUseBuiltin(enabled)
            return
        # 全局规则文件：加入/移出 disabled_rules_paths
        disabled = self._config.disabled_rules_paths
        if enabled:
            if path in disabled:
                disabled.remove(path)
            else:
                return  # 无变化
        else:
            if path in disabled:
                return  # 无变化
            if path not in self._config.rules_paths:
                logger.warning("规则文件不在全局列表中: %s", path)
                return
            disabled.append(path)
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def moveUp(self) -> None:
        """上移选中全局规则文件。"""
        if not self.canMoveUp:
            return
        # 全局规则文件在 rules_paths 中的索引 = selected_file_index - 1
        idx_in_paths = self._selected_file_index - 1
        paths = self._config.rules_paths
        paths[idx_in_paths - 1], paths[idx_in_paths] = paths[idx_in_paths], paths[idx_in_paths - 1]
        self._selected_file_index = self._selected_file_index - 1
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def moveDown(self) -> None:
        """下移选中全局规则文件。"""
        if not self.canMoveDown:
            return
        idx_in_paths = self._selected_file_index - 1
        paths = self._config.rules_paths
        paths[idx_in_paths + 1], paths[idx_in_paths] = paths[idx_in_paths], paths[idx_in_paths + 1]
        self._selected_file_index = self._selected_file_index + 1
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def removeSelected(self) -> None:
        """移除选中规则文件（按作用域分派）。

        - 全局规则文件：从 ``Config.rules_paths`` 移除，同步清理禁用列表
        - 临时规则文件：从当前工作区的 ``temp_rules_paths`` 移除
        """
        if not self.canRemove:
            return
        item = self._selected_item()
        if item is None:
            return
        path = str(item["path"])
        scope = str(item["scope"])

        if scope == "global":
            self._remove_global_path(path)
        elif scope == "temp":
            # 临时规则文件
            ws_id = self._current_ws_id()
            if not ws_id or self._workspace_controller is None:
                return
            current = list(self._current_temp_paths())
            if path in current:
                current.remove(path)
            self._workspace_controller.setTaskOverride(  # pyrefly: ignore [missing-attribute]
                ws_id, "temp_rules_paths", json.dumps(current)
            )

        self._selected_file_index = -1
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        if scope == "global":
            self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot(str)  # pyrefly: ignore [not-callable]
    def removeGlobalPath(self, path: str) -> None:
        """按路径直接移除全局规则文件（无需先选中）。

        供任务级「配置规则」对话框的全局规则区调用——该区 ListView
        不与 RulesPanel 共享 selectedFileIndex，需要按路径直接操作。

        :param path: 规则文件路径（内置规则标识 ``__builtin__`` 忽略）
        """
        if path == BUILTIN_PATH_MARKER:
            return
        self._remove_global_path(path)
        self._selected_file_index = -1
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    def _remove_global_path(self, path: str) -> None:
        """移除全局规则文件并持久化（内部复用）。"""
        if path in self._config.rules_paths:
            self._config.rules_paths.remove(path)
        if path in self._config.disabled_rules_paths:
            self._config.disabled_rules_paths.remove(path)
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)

    # ------------------- 作用域迁移 -------------------

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def promoteToGlobal(self, path_str: str) -> bool:
        """把当前工作区的临时规则文件提升为全局规则。

        将 ``path`` 从当前工作区 ``temp_rules_paths`` 移除，加入
        ``Config.rules_paths``（若已存在则跳过加入，仅移除临时侧以避免冗余）。
        持久化两端配置，刷新规则集与文件列表。

        :param path_str: 规则文件路径（必须是当前工作区已加载的临时规则）
        :return: 是否迁移成功。无当前工作区、路径不在临时规则中或加载失败返回 False，
            并通过 ``rulesIoCompleted`` 信号通知 QML。
        """
        ws_id = self._current_ws_id()
        if not ws_id or self._workspace_controller is None:
            logger.warning("无当前工作区，无法提升临时规则")
            self.rulesIoCompleted.emit(False, "请先在首页选择工作区")  # pyrefly: ignore [missing-attribute]
            return False

        current_temp = list(self._current_temp_paths())
        if path_str not in current_temp:
            logger.warning("路径不在当前工作区临时规则中: %s", path_str)
            self.rulesIoCompleted.emit(False, "该文件不是当前工作区的临时规则")  # pyrefly: ignore [missing-attribute]
            return False

        path = Path(path_str)
        if not path.exists():
            logger.warning("规则文件不存在: %s", path_str)
            self.rulesIoCompleted.emit(False, "规则文件不存在")  # pyrefly: ignore [missing-attribute]
            return False

        # 预校验能否成功加载（避免把损坏文件加入全局）
        try:
            load_ruleset(path)
        except RuleError as exc:
            logger.warning("规则文件解析失败: %s", exc)
            self.rulesIoCompleted.emit(False, f"加载失败：{exc}")  # pyrefly: ignore [missing-attribute]
            return False

        # 从临时规则移除
        current_temp.remove(path_str)
        self._workspace_controller.setTaskOverride(  # pyrefly: ignore [missing-attribute]
            ws_id, "temp_rules_paths", json.dumps(current_temp)
        )

        # 加入全局规则（去重：已在全局列表则不重复加入）
        added_to_global = False
        if path_str not in self._config.rules_paths:
            self._config.rules_paths.append(path_str)
            added_to_global = True
        # 同步清理禁用列表（若该路径曾被禁用，提升后默认启用）
        if path_str in self._config.disabled_rules_paths:
            self._config.disabled_rules_paths.remove(path_str)
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]

        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self._selected_file_index = -1
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

        msg = f"已提升 {path.name} 为全局规则" + ("（全局已存在，仅移除临时侧）" if not added_to_global else "")
        logger.info(msg)
        self.rulesIoCompleted.emit(True, msg)  # pyrefly: ignore [missing-attribute]
        return True

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def demoteToTemp(self, path_str: str) -> bool:
        """把全局规则文件降级为当前工作区临时规则。

        将 ``path`` 从 ``Config.rules_paths`` 移除（同步清理禁用列表），
        加入当前工作区 ``temp_rules_paths``（若已存在则跳过加入，仅移除全局侧）。
        持久化两端配置，刷新规则集与文件列表。

        :param path_str: 规则文件路径（必须是已加载的全局规则文件）
        :return: 是否迁移成功。无当前工作区、路径不在全局规则中或加载失败返回 False，
            并通过 ``rulesIoCompleted`` 信号通知 QML。
        """
        ws_id = self._current_ws_id()
        if not ws_id or self._workspace_controller is None:
            logger.warning("无当前工作区，无法降级全局规则")
            self.rulesIoCompleted.emit(False, "请先在首页选择工作区")  # pyrefly: ignore [missing-attribute]
            return False

        if path_str not in self._config.rules_paths:
            logger.warning("路径不在全局规则中: %s", path_str)
            self.rulesIoCompleted.emit(False, "该文件不是全局规则")  # pyrefly: ignore [missing-attribute]
            return False

        path = Path(path_str)
        if not path.exists():
            logger.warning("规则文件不存在: %s", path_str)
            self.rulesIoCompleted.emit(False, "规则文件不存在")  # pyrefly: ignore [missing-attribute]
            return False

        # 预校验能否成功加载
        try:
            load_ruleset(path)
        except RuleError as exc:
            logger.warning("规则文件解析失败: %s", exc)
            self.rulesIoCompleted.emit(False, f"加载失败：{exc}")  # pyrefly: ignore [missing-attribute]
            return False

        # 从全局规则移除（同步清理禁用列表）
        self._config.rules_paths.remove(path_str)
        if path_str in self._config.disabled_rules_paths:
            self._config.disabled_rules_paths.remove(path_str)
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]

        # 加入当前工作区临时规则（去重）
        current_temp = list(self._current_temp_paths())
        added_to_temp = False
        if path_str not in current_temp:
            current_temp.append(path_str)
            added_to_temp = True
        self._workspace_controller.setTaskOverride(  # pyrefly: ignore [missing-attribute]
            ws_id, "temp_rules_paths", json.dumps(current_temp)
        )

        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self._selected_file_index = -1
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

        msg = f"已降级 {path.name} 为临时规则" + ("（临时已存在，仅移除全局侧）" if not added_to_temp else "")
        logger.info(msg)
        self.rulesIoCompleted.emit(True, msg)  # pyrefly: ignore [missing-attribute]
        return True

    # ------------------- 导入/导出 -------------------

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def exportRuleset(self, path_str: str) -> bool:
        """导出当前规则集到 YAML/JSON 文件。

        将当前合并后的 :class:`RuleSet`（内置 + 用户规则）序列化到目标路径。
        格式根据扩展名推断（.yaml/.yml → YAML，.json → JSON）。

        :param path_str: 目标文件路径（QML FileDialog 选定后传入）
        :return: 是否导出成功；失败时通过 ``rulesIoCompleted`` 信号通知 QML
        """
        if not path_str:
            logger.warning("导出规则集失败：路径为空")
            self.rulesIoCompleted.emit(False, "导出路径为空")  # pyrefly: ignore [missing-attribute]
            return False

        if self._ruleset is None:
            msg = "当前无规则集可导出（请先加载规则文件或勾选内置规则）"
            logger.warning(msg)
            self.rulesIoCompleted.emit(False, msg)  # pyrefly: ignore [missing-attribute]
            return False

        path = Path(path_str)
        try:
            save_ruleset(self._ruleset, path)
            msg = f"规则集已导出到 {path.name}（{len(self._ruleset.rules)} 条规则）"
            logger.info(msg)
            self.rulesIoCompleted.emit(True, msg)  # pyrefly: ignore [missing-attribute]
            return True
        except (ValueError, OSError) as exc:
            logger.warning("导出规则集失败: %s", exc)
            self.rulesIoCompleted.emit(False, f"导出失败：{exc}")  # pyrefly: ignore [missing-attribute]
            return False

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def importRuleset(self, path_str: str) -> bool:
        """从 YAML/JSON 文件导入规则集到全局规则列表。

        导入即将该文件加入规则文件列表（等价于 :meth:`loadFileFromPath`），
        但带有版本兼容性校验（不兼容版本会在加载阶段抛 ``RuleParseError``）。
        导入后规则立即生效，QML 列表自动刷新。

        :param path_str: 规则文件路径（QML FileDialog 选定后传入）
        :return: 是否导入成功；失败时通过 ``rulesIoCompleted`` 信号通知 QML
        """
        if not path_str:
            logger.warning("导入规则集失败：路径为空")
            self.rulesIoCompleted.emit(False, "导入路径为空")  # pyrefly: ignore [missing-attribute]
            return False

        path = Path(path_str)
        if not path.exists():
            logger.warning("规则文件不存在: %s", path_str)
            self.rulesIoCompleted.emit(False, "规则文件不存在")  # pyrefly: ignore [missing-attribute]
            return False

        # 预校验：能否成功加载（含版本兼容性检查）
        try:
            imported = load_ruleset(path)
        except RuleError as exc:
            logger.warning("导入规则集失败（解析错误）: %s", exc)
            self.rulesIoCompleted.emit(False, f"导入失败：{exc}")  # pyrefly: ignore [missing-attribute]
            return False

        # 复用 loadFileFromPath 加入规则文件列表
        if not self.loadFileFromPath(path_str):
            # loadFileFromPath 返回 False 可能是已加载或加载失败，这里给出明确提示
            if str(path) in self._config.rules_paths:
                msg = f"规则文件 {path.name} 已加载，无需重复导入"
                logger.info(msg)
                self.rulesIoCompleted.emit(False, msg)  # pyrefly: ignore [missing-attribute]
            else:
                self.rulesIoCompleted.emit(False, f"导入失败：{path.name} 加载失败")  # pyrefly: ignore [missing-attribute]
            return False

        msg = f"已导入规则集 {path.name}（{len(imported.rules)} 条规则）"
        logger.info(msg)
        self.rulesIoCompleted.emit(True, msg)  # pyrefly: ignore [missing-attribute]
        return True

    # ----------------------------- 内部方法 -----------------------------

    def _reload_ruleset(self) -> None:
        """重新加载全局规则集（按 use_builtin + 启用的 rules_paths 合并）。

        已过滤 ``disabled_rules_paths`` 中的禁用文件。
        临时规则不在此处合并——由 :meth:`ScanController._compute_effective_ruleset`
        在扫描时叠加。
        """
        from fuscan.rules import load_with_builtin

        paths = [
            Path(p) for p in self._config.rules_paths if Path(p).exists() and p not in self._config.disabled_rules_paths
        ]
        use_builtin = self._config.use_builtin

        try:
            if use_builtin:
                self._ruleset = load_with_builtin(paths)
            elif paths:
                rulesets = [load_ruleset(p) for p in paths]
                self._ruleset = merge_multiple_rulesets(*rulesets)
            else:
                self._ruleset = None
        except RuleError as exc:
            logger.warning("规则集加载失败: %s", exc)
            self._ruleset = None
