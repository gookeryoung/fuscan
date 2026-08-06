"""规则控制器：QML ↔ RuleSet/规则文件管理桥接。

管理全局规则与任务级临时规则两类规则文件：

- **全局规则**：持久化到 :class:`Config`，所有工作区共享。
  内置规则归入全局规则列表（默认启用、不可移除、可禁用）；
  用户加载的全局规则文件可勾选启用/禁用、可移除。
- **临时规则**：任务级覆盖，仅对当前选中工作区生效，叠加在全局规则之上。
  通过 ``task_overrides["temp_rules_paths"]`` 持久化到工作区配置；
  临时规则文件可勾选启用/禁用（禁用列表持久化到
  ``task_overrides["disabled_temp_rules_paths"]``），可移除。

规则列表通过 :class:`RuleListModel` 暴露给 QML ``ListView`` 绑定，
规则文件列表（全局 + 临时合并）通过 ``@Property`` 暴露 ``QVariantList``。

公共 API：

- :class:`RulesController`：``QObject`` 子类
- :meth:`RulesController.loadFileFromPath`：加载规则文件到全局
- :meth:`RulesController.loadFileToTemp`：加载规则文件到当前工作区临时规则
- :meth:`RulesController.moveUp` / :meth:`moveDown`：全局规则文件顺序管理
- :meth:`RulesController.removeSelected`：移除选中规则文件（按作用域分派）
- :meth:`RulesController.setRuleEnabled`：勾选启用/禁用规则文件（内置/全局/临时）
- :meth:`RulesController.setUseBuiltin`：勾选内置规则（等价于 setRuleEnabled("__builtin__"))
- :meth:`RulesController.promoteToGlobal`：把当前工作区临时规则提升为全局规则
- :meth:`RulesController.demoteToTemp`：把全局规则降级为当前工作区临时规则
- :meth:`RulesController.exportRuleset`：导出当前规则集到 YAML/JSON
- :meth:`RulesController.importRuleset`：从 YAML/JSON 文件导入规则
- :meth:`RulesController.set_workspace_controller`：延迟注入工作区控制器
- :meth:`RulesController.effectiveConfigPreview`：生效配置预览（QML 只读展示）
- :meth:`RulesController.appendWhitelistEntry`：追加白名单条目到 user-scan.yaml
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import replace
from pathlib import Path

try:
    from PySide2.QtCore import Property, QObject, Signal, Slot
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Property, QObject, Signal, Slot  # pyrefly: ignore [missing-import]

from fuscan.config import DEFAULT_MAX_FILE_SIZE, Config
from fuscan.gui.controllers._task_overrides import (
    effective_disabled_temp_rules_paths,
    effective_ignore_dirs,
    effective_max_depth,
    effective_max_file_size,
    effective_max_workers,
    effective_rules_paths,
    effective_scan_archives,
    effective_temp_rules_paths,
    effective_use_builtin,
)
from fuscan.gui.models.rule_model import RuleListModel
from fuscan.gui.severity_utils import severity_color_hex, severity_text
from fuscan.rules import (
    RuleError,
    load_builtin_ruleset,
    load_ruleset,
    load_with_builtin,
    merge_multiple_rulesets,
    save_ruleset,
)
from fuscan.rules.model import RuleSet
from fuscan.rules.whitelist import WhitelistEntry

__all__ = ["RulesController"]

logger = logging.getLogger(__name__)

# 内置规则在 rulesFileModel 中的虚拟路径标识
BUILTIN_PATH_MARKER = "__builtin__"


def _scan_extensions_state_of(rs: RuleSet | None) -> tuple[list[str], str]:
    """从 RuleSet 提取 scan_extensions 列表与状态字符串。

    :param rs: 规则集（None 视为未设置）
    :return: ``(extensions_list, state)``：

        - ``state="unset"``：``scan_extensions is None``（未设置，继承前序），
          ``extensions_list=[]``
        - ``state="none"``：``scan_extensions == ()``（空 tuple，都不扫描），
          ``extensions_list=[]``
        - ``state="list"``：非空 tuple，``extensions_list=list(scan_extensions)``
    """
    if rs is None or rs.scan_extensions is None:
        return [], "unset"
    if len(rs.scan_extensions) == 0:
        return [], "none"
    return list(rs.scan_extensions), "list"


def _builtin_scan_extensions() -> tuple[list[str], str]:
    """内置规则的 scan_extensions 列表与状态。

    内置规则文件加载失败时回退到 ``"unset"``（避免阻塞 UI 渲染）。
    """
    try:
        rs = load_builtin_ruleset()
    except RuleError as exc:  # pragma: no cover - 内置规则不应失败
        logger.warning("内置规则 scan_extensions 加载失败: %s", exc)
        return [], "unset"
    return _scan_extensions_state_of(rs)


def _scan_extensions_of(path: Path) -> tuple[list[str], str]:
    """指定规则文件的 scan_extensions 列表与状态。

    文件不存在或解析失败时回退到 ``"unset"``（避免阻塞 UI 渲染）。

    :param path: 规则文件路径
    """
    if not path.exists():
        return [], "unset"
    try:
        rs = load_ruleset(path)
    except RuleError as exc:
        logger.debug("规则文件 %s scan_extensions 加载失败: %s", path, exc)
        return [], "unset"
    return _scan_extensions_state_of(rs)


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

    def _current_disabled_temp_paths(self) -> tuple[str, ...]:
        """当前工作区禁用的临时规则文件路径元组。

        与全局 ``Config.disabled_rules_paths`` 同语义，仅作用于当前工作区
        临时规则——禁用后不参与规则集合并，但仍保留在 ``temp_rules_paths``
        中以便重新启用。
        """
        ws_id = self._current_ws_id()
        if not ws_id or self._workspace_controller is None:
            return ()
        item = self._workspace_controller.get_workspace(ws_id)  # pyrefly: ignore [missing-attribute]
        if item is None:
            return ()
        value = item.task_overrides.get("disabled_temp_rules_paths")
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
        - ``enabled``：是否启用。全局规则文件由 ``Config.disabled_rules_paths``
          控制；临时规则文件由当前工作区 ``task_overrides.disabled_temp_rules_paths``
          控制；内置规则由 ``Config.use_builtin`` 控制
        - ``canRemove``：是否可移除（内置规则为 False，其余为 True）
        - ``scanExtensions``：该规则文件自身的 ``scan_extensions`` 列表（list[str]）。
          未设置（None）或文件不存在/解析失败时为空列表
        - ``scanExtensionsState``：``"unset"``（未设置，继承前序）/
          ``"none"``（空 tuple，都不扫描）/``"list"``（非空列表）
        """
        items: list[dict[str, object]] = []
        # 内置规则（固定第一项）
        builtin_ext, builtin_state = _builtin_scan_extensions()
        items.append(
            {
                "fileName": "内置通用规则",
                "path": BUILTIN_PATH_MARKER,
                "exists": True,
                "scope": "global",
                "isBuiltin": True,
                "enabled": self._config.use_builtin,
                "canRemove": False,
                "scanExtensions": builtin_ext,
                "scanExtensionsState": builtin_state,
            }
        )
        # 全局规则文件
        for p in self._config.rules_paths:
            exts, state = _scan_extensions_of(Path(p))
            items.append(
                {
                    "fileName": Path(p).name,
                    "path": p,
                    "exists": Path(p).exists(),
                    "scope": "global",
                    "isBuiltin": False,
                    "enabled": p not in self._config.disabled_rules_paths,
                    "canRemove": True,
                    "scanExtensions": exts,
                    "scanExtensionsState": state,
                }
            )
        # 临时规则文件（当前工作区）—— enabled 由 task_overrides.disabled_temp_rules_paths 控制
        disabled_temp = self._current_disabled_temp_paths()
        for p in self._current_temp_paths():
            exts, state = _scan_extensions_of(Path(p))
            items.append(
                {
                    "fileName": Path(p).name,
                    "path": p,
                    "exists": Path(p).exists(),
                    "scope": "temp",
                    "isBuiltin": False,
                    "enabled": p not in disabled_temp,
                    "canRemove": True,
                    "scanExtensions": exts,
                    "scanExtensionsState": state,
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
        """勾选启用/禁用规则文件（按作用域分派）。

        - 内置规则（``"__builtin__"``）：等价于 :meth:`setUseBuiltin`
        - 全局规则文件：操作 ``Config.disabled_rules_paths``，立即生效并持久化
        - 临时规则文件：操作当前工作区 ``task_overrides.disabled_temp_rules_paths``
          （通过 :meth:`WorkspaceController.setTaskOverride`），立即生效并持久化

        :param path: 规则文件路径（``"__builtin__"`` 表示内置规则）
        :param enabled: 是否启用
        """
        if path == BUILTIN_PATH_MARKER:
            self.setUseBuiltin(enabled)
            return

        # 临时规则文件：操作 task_overrides.disabled_temp_rules_paths
        if path in self._current_temp_paths():
            self._set_temp_rule_enabled(path, enabled)
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

    def _set_temp_rule_enabled(self, path: str, enabled: bool) -> None:
        """启用/禁用当前工作区的临时规则文件（操作 disabled_temp_rules_paths）。

        :param path: 临时规则文件路径（必须在当前工作区 ``temp_rules_paths`` 中）
        :param enabled: 是否启用
        """
        ws_id = self._current_ws_id()
        if not ws_id or self._workspace_controller is None:
            logger.warning("无当前工作区，无法修改临时规则启用状态")
            return

        disabled = list(self._current_disabled_temp_paths())
        if enabled:
            if path in disabled:
                disabled.remove(path)
            else:
                return  # 无变化
        else:
            if path in disabled:
                return  # 无变化
            disabled.append(path)

        # 通过 WorkspaceController.setTaskOverride 持久化并同步到 ScanController
        self._workspace_controller.setTaskOverride(  # pyrefly: ignore [missing-attribute]
            ws_id, "disabled_temp_rules_paths", json.dumps(disabled)
        )
        # setTaskOverride 内部已 emit currentWorkspaceChanged（经 WorkspaceItem 更新），
        # 但 RulesController 的 rulesFileListChanged 不会自动触发，需显式 emit
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]

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
            # 同步清理 disabled_temp_rules_paths 中的悬空记录
            disabled = list(self._current_disabled_temp_paths())
            if path in disabled:
                disabled.remove(path)
                self._workspace_controller.setTaskOverride(  # pyrefly: ignore [missing-attribute]
                    ws_id, "disabled_temp_rules_paths", json.dumps(disabled)
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
        # 同步清理 disabled_temp_rules_paths 中的悬空记录
        disabled_temp = list(self._current_disabled_temp_paths())
        if path_str in disabled_temp:
            disabled_temp.remove(path_str)
            self._workspace_controller.setTaskOverride(  # pyrefly: ignore [missing-attribute]
                ws_id, "disabled_temp_rules_paths", json.dumps(disabled_temp)
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

    # ------------------- 生效配置预览与白名单 -------------------

    @property
    def userScanPath(self) -> Path:
        """用户扫描规则文件路径（``~/.fuscan/rules/user-scan.yaml``）。

        动态读取 :data:`fuscan.config.CONFIG_DIR`，支持测试 monkeypatch。
        供 :meth:`appendWhitelistEntry` 与外部测试读取/创建该文件。
        """
        # 动态读取 fuscan.config.CONFIG_DIR，避免模块级导入的固定引用
        # 无法响应测试 monkeypatch（参见 _USER_SCAN_PATH 修复记录）
        from fuscan.config import CONFIG_DIR as _config_dir

        return _config_dir / "rules" / "user-scan.yaml"

    @Property("QVariantMap", notify=rulesetChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def effectiveConfigPreview(self) -> dict[str, object]:
        """当前规则集的生效配置预览（供 QML 设置页只读展示）。

        从 :attr:`_ruleset` 读取 ``scan_params``/``ignore_dirs``/``scan_extensions``
        等字段，``None`` 字段回退到内置默认值。ruleset 为 None 时全部返回默认值
        并标记 ``hasRuleset=False``。

        返回字段：

        - ``scanArchives``/``maxWorkers``/``maxDepth``/``maxFileSizeMB``/
          ``cacheEnabled``/``perfLogEnabled``：扫描参数（回退内置默认）
        - ``ignoreDirs``：忽略目录名列表
        - ``scanExtensions``：文件扩展名白名单（空列表表示全选默认）
        - ``whitelistCount``：白名单条目数
        - ``hasRuleset``：是否已加载规则集
        """
        if self._ruleset is None:
            return {
                "scanArchives": True,
                "maxWorkers": 5,
                "maxDepth": 0,
                "maxFileSizeMB": DEFAULT_MAX_FILE_SIZE // (1024 * 1024),
                "cacheEnabled": True,
                "perfLogEnabled": False,
                "ignoreDirs": [],
                "scanExtensions": [],
                "whitelistCount": 0,
                "hasRuleset": False,
            }
        sp = self._ruleset.scan_params
        return {
            "scanArchives": sp.scan_archives if sp is not None and sp.scan_archives is not None else True,
            "maxWorkers": sp.max_workers if sp is not None and sp.max_workers is not None else 5,
            "maxDepth": sp.max_depth if sp is not None and sp.max_depth is not None else 0,
            "maxFileSizeMB": (
                sp.max_file_size if sp is not None and sp.max_file_size is not None else DEFAULT_MAX_FILE_SIZE
            )
            // (1024 * 1024),
            "cacheEnabled": sp.cache_enabled if sp is not None and sp.cache_enabled is not None else True,
            "perfLogEnabled": sp.perf_log_enabled if sp is not None and sp.perf_log_enabled is not None else False,
            "ignoreDirs": list(self._ruleset.ignore_dirs),
            "scanExtensions": list(self._ruleset.scan_extensions) if self._ruleset.scan_extensions is not None else [],
            "whitelistCount": len(self._ruleset.whitelist),
            "hasRuleset": True,
        }

    @Slot(str, str, str, result=str)  # pyrefly: ignore [not-callable]
    def appendWhitelistEntry(self, path_glob: str, rule_name: str, note: str) -> str:
        """追加白名单条目到 ``~/.fuscan/rules/user-scan.yaml``。

        将 (path_glob, rule_name, note) 作为 ``WhitelistEntry``（source="runtime"）
        追加到 user-scan.yaml 的 ``whitelist`` 段。文件不存在时创建；存在时加载
        现有 RuleSet 并 append。保存后重新加载规则集并 emit ``rulesetChanged``，
        使 QML 设置页与扫描控制器立即生效。

        :param path_glob: 路径 glob 模式（空字符串返回错误消息）
        :param rule_name: 规则名；空字符串归一化为 ``*``（匹配任意规则）
        :param note: 用户备注（可空）
        :return: 操作消息（成功/失败原因），供 QML 显示
        """
        path_glob = path_glob.strip()
        if not path_glob:
            return "路径模式不能为空"
        rule_name = rule_name.strip() or "*"
        note = note.strip()

        user_scan_path = self.userScanPath
        try:
            if user_scan_path.exists():
                existing = load_ruleset(user_scan_path)
                new_entry = WhitelistEntry(
                    path_glob=path_glob,
                    rule_name=rule_name,
                    created_at=datetime.datetime.now().isoformat(timespec="seconds"),
                    note=note,
                    source="runtime",
                )
                # 去重：已存在相同 (path_glob, rule_name) 则提示无变化
                if any(e.path_glob == path_glob and e.rule_name == rule_name for e in existing.whitelist):
                    return f"已存在: {path_glob} ({rule_name})"
                updated = replace(existing, whitelist=(*existing.whitelist, new_entry))
            else:
                new_entry = WhitelistEntry(
                    path_glob=path_glob,
                    rule_name=rule_name,
                    created_at=datetime.datetime.now().isoformat(timespec="seconds"),
                    note=note,
                    source="runtime",
                )
                updated = RuleSet(
                    version="1.0",
                    whitelist=(new_entry,),
                )
            user_scan_path.parent.mkdir(parents=True, exist_ok=True)
            save_ruleset(updated, user_scan_path)
        except (RuleError, OSError) as exc:
            logger.warning("追加白名单条目失败: %s", exc)
            return f"添加失败: {exc}"

        # 将 user-scan.yaml 加入 rules_paths（若未存在），使新条目进入 effective ruleset
        user_scan_str = str(user_scan_path)
        if user_scan_str not in self._config.rules_paths:
            self._config.rules_paths.append(user_scan_str)
            self._config_controller.save()  # pyrefly: ignore [missing-attribute]

        # 重新加载规则集，emit 信号让 QML 与 ScanController 同步
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

        msg = f"已添加: {path_glob} ({rule_name})"
        logger.info(msg)
        return msg

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

    # ------------------- 任务级规则预览 -------------------

    @Slot(str, result=str)  # pyrefly: ignore [not-callable]
    def previewRuleset(self, ws_id: str) -> str:
        """返回指定工作区 effective ruleset 预览的 JSON 字符串。

        合并任务级覆盖（``rules_paths``/``use_builtin``）与临时规则
        （``temp_rules_paths``，跳过 ``disabled_temp_rules_paths``），
        与 :meth:`ScanController._compute_effective_ruleset` 算法一致。
        供 QML「预览规则」对话框只读展示。

        :param ws_id: 工作区 ID（不存在或为空返回空对象 ``{}``）
        :return: JSON 字符串，字段：

            - ``scanArchives``/``maxWorkers``/``maxDepth``/``maxFileSizeMB``/
              ``cacheEnabled``/``perfLogEnabled``：生效扫描参数
            - ``ignoreDirs``：生效忽略目录名列表
            - ``scanExtensions``：文件扩展名白名单（空列表表示全选默认）
            - ``whitelistEntries``：白名单条目数组（``pathGlob``/``ruleName``/
              ``createdAt``/``note``/``source``）
            - ``rules``：匹配规则数组（``name``/``severityText``/
              ``severityColor``/``description``/``replace``/``replaceWith``）
            - ``ruleFiles``：规则文件数组（与 :attr:`rulesFileModel` 字段一致，
              含 ``fileName``/``path``/``exists``/``scope``/``isBuiltin``/
              ``enabled``/``canRemove``，仅展示当前 ``ws_id`` 的临时规则）
            - ``hasRuleset``：是否成功加载 effective ruleset
        """
        import json as _json

        if not ws_id or self._workspace_controller is None:
            return "{}"
        item = self._workspace_controller.get_workspace(ws_id)  # pyrefly: ignore [missing-attribute]
        if item is None:
            return "{}"
        overrides = item.task_overrides

        # 计算 effective ruleset（与 ScanController._compute_effective_ruleset 一致）
        ruleset = self._compute_effective_ruleset_for(overrides)

        # 规则文件列表（与 rulesFileModel 一致，但仅展示当前 wsId 的临时规则）
        rule_files = self._rule_files_for_preview(overrides)

        # 扫描参数（从 effective ruleset 读取，回退内置默认；不再支持任务级覆盖）
        if ruleset is None:
            preview: dict[str, object] = {
                "scanArchives": effective_scan_archives(None),
                "maxWorkers": effective_max_workers(None),
                "maxDepth": effective_max_depth(None) or 0,
                "maxFileSizeMB": effective_max_file_size(None) // (1024 * 1024),
                "cacheEnabled": True,
                "perfLogEnabled": False,
                "ignoreDirs": list(effective_ignore_dirs(None)),
                "scanExtensions": list[str](),
                "whitelistEntries": list[dict[str, object]](),
                "rules": list[dict[str, object]](),
                "ruleFiles": rule_files,
                "hasRuleset": False,
            }
        else:
            sp = ruleset.scan_params
            preview = {
                "scanArchives": effective_scan_archives(ruleset),
                "maxWorkers": effective_max_workers(ruleset),
                "maxDepth": effective_max_depth(ruleset) or 0,
                "maxFileSizeMB": effective_max_file_size(ruleset) // (1024 * 1024),
                "cacheEnabled": sp.cache_enabled if sp is not None and sp.cache_enabled is not None else True,
                "perfLogEnabled": sp.perf_log_enabled if sp is not None and sp.perf_log_enabled is not None else False,
                "ignoreDirs": list(effective_ignore_dirs(ruleset)),
                "scanExtensions": list(ruleset.scan_extensions) if ruleset.scan_extensions is not None else [],
                "whitelistEntries": [
                    {
                        "pathGlob": e.path_glob,
                        "ruleName": e.rule_name,
                        "createdAt": e.created_at,
                        "note": e.note,
                        "source": e.source,
                    }
                    for e in ruleset.whitelist
                ],
                "rules": [
                    {
                        "name": r.name,
                        "severityText": severity_text(r.severity),
                        "severityColor": severity_color_hex(r.severity),
                        "description": r.description,
                        "replace": r.replace,
                        "replaceWith": r.replace_with,
                    }
                    for r in ruleset.rules
                ],
                "ruleFiles": rule_files,
                "hasRuleset": True,
            }
        try:
            return _json.dumps(preview, ensure_ascii=False)
        except (TypeError, ValueError):
            logger.warning("工作区 %s 规则预览序列化失败", ws_id, exc_info=True)
            return "{}"

    def _compute_effective_ruleset_for(self, overrides: dict[str, object]) -> RuleSet | None:
        """计算指定任务级覆盖的 effective ruleset。

        与 :meth:`ScanController._compute_effective_ruleset` 算法一致：
        任务级 ``rules_paths``/``use_builtin`` 覆盖优先，临时规则最后叠加
        （跳过 ``disabled_temp_rules_paths`` 中禁用的路径）。

        :param overrides: 任务级覆盖字典
        :return: :class:`RuleSet`；无可用规则或加载失败返回 ``None``
        """
        has_override = "rules_paths" in overrides or "use_builtin" in overrides
        disabled_temp = effective_disabled_temp_rules_paths(overrides)
        temp_paths = [
            Path(p) for p in effective_temp_rules_paths(overrides) if Path(p).exists() and p not in disabled_temp
        ]

        if not has_override and not temp_paths:
            return self._ruleset

        if has_override:
            paths = [Path(p) for p in effective_rules_paths(overrides, self._config) if Path(p).exists()]
            use_builtin = effective_use_builtin(overrides, self._config)
            try:
                if use_builtin:
                    base: RuleSet | None = load_with_builtin(paths)
                elif paths:
                    rulesets = [load_ruleset(p) for p in paths]
                    base = merge_multiple_rulesets(*rulesets)
                else:
                    base = None
            except RuleError as exc:
                logger.warning("预览：任务级规则集加载失败: %s", exc)
                return None
        else:
            base = self._ruleset

        if not temp_paths:
            return base

        try:
            temp_rulesets = [load_ruleset(p) for p in temp_paths]
            if base is not None:
                return merge_multiple_rulesets(base, *temp_rulesets)
            return merge_multiple_rulesets(*temp_rulesets)
        except RuleError as exc:
            logger.warning("预览：临时规则集加载失败: %s", exc)
            return base

    def _rule_files_for_preview(self, overrides: dict[str, object]) -> list[dict[str, object]]:
        """构造预览用的规则文件列表（内置 + 全局 + 当前工作区临时规则）。

        与 :attr:`rulesFileModel` 字段一致，但 ``enabled`` 字段对临时规则
        反映 ``disabled_temp_rules_paths`` 的禁用状态，``scanExtensions``/
        ``scanExtensionsState`` 反映该规则文件自身的 ``scan_extensions``。

        :param overrides: 任务级覆盖字典
        :return: 规则文件描述字典列表
        """
        items: list[dict[str, object]] = []
        builtin_ext, builtin_state = _builtin_scan_extensions()
        items.append(
            {
                "fileName": "内置通用规则",
                "path": BUILTIN_PATH_MARKER,
                "exists": True,
                "scope": "global",
                "isBuiltin": True,
                "enabled": effective_use_builtin(overrides, self._config),
                "canRemove": False,
                "scanExtensions": builtin_ext,
                "scanExtensionsState": builtin_state,
            }
        )
        for p in self._config.rules_paths:
            exts, state = _scan_extensions_of(Path(p))
            items.append(
                {
                    "fileName": Path(p).name,
                    "path": p,
                    "exists": Path(p).exists(),
                    "scope": "global",
                    "isBuiltin": False,
                    "enabled": p not in self._config.disabled_rules_paths,
                    "canRemove": True,
                    "scanExtensions": exts,
                    "scanExtensionsState": state,
                }
            )
        disabled_temp = effective_disabled_temp_rules_paths(overrides)
        for p in effective_temp_rules_paths(overrides):
            exts, state = _scan_extensions_of(Path(p))
            items.append(
                {
                    "fileName": Path(p).name,
                    "path": p,
                    "exists": Path(p).exists(),
                    "scope": "temp",
                    "isBuiltin": False,
                    "enabled": p not in disabled_temp,
                    "canRemove": True,
                    "scanExtensions": exts,
                    "scanExtensionsState": state,
                }
            )
        return items

    # ----------------------------- 内部方法 -----------------------------

    def _reload_ruleset(self) -> None:
        """重新加载全局规则集（按 use_builtin + 启用的 rules_paths 合并）。

        已过滤 ``disabled_rules_paths`` 中的禁用文件。
        临时规则不在此处合并——由 :meth:`ScanController._compute_effective_ruleset`
        在扫描时叠加。
        """
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
