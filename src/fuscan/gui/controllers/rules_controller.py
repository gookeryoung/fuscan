"""规则控制器：QML ↔ RuleSet/规则文件管理桥接。

管理规则文件路径列表、内置规则勾选、规则集加载与合并。规则列表通过
:class:`RuleListModel` 暴露给 QML ``ListView`` 绑定，规则文件列表通过
``@Property`` 暴露简单字符串列表（条目数少，无需 Model）。

支持两种模式：

- **全局模式**（默认）：直接读写 :class:`Config` 的 ``rules_paths``/``use_builtin``，
  影响全局默认规则。用于无工作区上下文的场景（保留兼容）。
- **工作区绑定模式**：通过 :meth:`bind_workspace` 绑定到某个工作区后，
  所有编辑操作仅作用于该工作区的规则副本，编辑后通过
  :meth:`WorkspaceController.update_workspace_rules` 写回 :class:`WorkspaceItem`
  并刷新对应 :class:`ScanController` 的规则集，不影响全局配置与其他工作区。

公共 API：

- :class:`RulesController`：``QObject`` 子类
- :meth:`RulesController.bind_workspace`：绑定到工作区（编辑该工作区规则）
- :meth:`RulesController.unbind_workspace`：解除绑定（恢复全局模式）
- :meth:`RulesController.load_file_from_path`：加载规则文件（QML FileDialog 选定后调用）
- :meth:`RulesController.move_up` / :meth:`move_down` / :meth:`remove_selected`：顺序管理
- :meth:`RulesController.set_use_builtin`：勾选内置规则
- :meth:`RulesController.export_ruleset`：导出当前规则集到 YAML/JSON（iter-122）
- :meth:`RulesController.import_ruleset`：从 YAML/JSON 文件导入规则（iter-122）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    pass

__all__ = ["RulesController"]

logger = logging.getLogger(__name__)


class RulesController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """规则控制器。

    :param config_controller: 配置控制器（共享 :class:`Config` 实例）
    :param parent: 父 QObject
    """

    rulesetChanged = Signal()
    rulesFileListChanged = Signal()
    selectionChanged = Signal()
    useBuiltinChanged = Signal()
    # 绑定工作区变化：QML 据此切换标题/提示
    boundWorkspaceChanged = Signal()
    # 规则导入/导出操作结果通知（QML 据此显示 toast/对话框）
    # 参数：成功 True/False，消息文本
    rulesIoCompleted = Signal(bool, str)

    def __init__(self, config_controller: object, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config_controller = config_controller
        self._config: Config = config_controller.config  # pyrefly: ignore [missing-attribute]
        self._ruleset: RuleSet | None = None
        self._rule_model: RuleListModel = RuleListModel(self)
        self._selected_file_index: int = -1
        # 工作区绑定状态：空串=全局模式，非空=绑定到该工作区 ID
        self._bound_ws_id: str = ""
        # 绑定模式下的本地副本（编辑期间不直接影响 WorkspaceItem，写回时统一同步）
        self._local_rules_paths: list[str] = []
        self._local_use_builtin: bool = True
        # 工作区控制器引用（绑定时由 WorkspaceController 注入，避免循环依赖）
        self._workspace_controller: object | None = None
        # 初始加载规则集（全局模式）
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)

    @property
    def ruleset(self) -> RuleSet | None:
        """当前规则集（供 ScanController 读取，全局模式下生效）。"""
        return self._ruleset

    @property
    def rules_paths(self) -> list[Path]:
        """规则文件路径列表（供 ScanController 构造缓存上下文，全局模式下生效）。"""
        return [Path(p) for p in self._config.rules_paths if Path(p).exists()]

    @property
    def use_builtin(self) -> bool:
        """是否启用内置规则（全局模式下生效）。"""
        return self._config.use_builtin

    def set_workspace_controller(self, workspace_controller: object) -> None:
        """注入工作区控制器引用（避免构造时循环依赖）。

        :param workspace_controller: :class:`WorkspaceController` 实例
        """
        self._workspace_controller = workspace_controller

    # ----------------------------- 工作区绑定 -----------------------------

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def bindWorkspace(self, ws_id: str) -> bool:
        """绑定到指定工作区，进入工作区规则编辑模式。

        从 :class:`WorkspaceController` 读取该工作区的 ``rules_paths``/``use_builtin``
        复制到本地副本，后续编辑操作仅作用于本地副本，调用
        :meth:`_persist_to_bound_workspace` 写回工作区。

        :param ws_id: 工作区 ID
        :return: 是否绑定成功（工作区不存在或 WorkspaceController 未注入返回 False）
        """
        if not ws_id or self._workspace_controller is None:
            return False
        # pyrefly: ignore [missing-attribute]
        item = self._workspace_controller.get_workspace(ws_id)  # type: ignore[attr-defined]
        if item is None:
            logger.warning("绑定工作区失败：工作区 %s 不存在", ws_id)
            return False
        self._bound_ws_id = ws_id
        self._local_rules_paths = list(item.rules_paths)
        self._local_use_builtin = item.use_builtin
        self._selected_file_index = -1
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.boundWorkspaceChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.useBuiltinChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]
        return True

    @Slot()  # pyrefly: ignore [not-callable]
    def unbindWorkspace(self) -> None:
        """解除工作区绑定，恢复全局模式。"""
        if not self._bound_ws_id:
            return
        self._bound_ws_id = ""
        self._local_rules_paths = []
        self._local_use_builtin = self._config.use_builtin
        self._selected_file_index = -1
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.boundWorkspaceChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.useBuiltinChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property(str, notify=boundWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def boundWorkspaceId(self) -> str:
        """当前绑定的工作区 ID（空串=全局模式）。"""
        return self._bound_ws_id

    @Property(str, notify=boundWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def boundWorkspaceName(self) -> str:
        """当前绑定的工作区名称（供 RulesPage 标题展示）。"""
        if not self._bound_ws_id or self._workspace_controller is None:
            return ""
        # pyrefly: ignore [missing-attribute]
        item = self._workspace_controller.get_workspace(self._bound_ws_id)  # type: ignore[attr-defined]
        return item.name if item is not None else ""

    @Property(bool, notify=boundWorkspaceChanged)  # pyrefly: ignore [not-callable]
    def isBound(self) -> bool:
        """是否处于工作区绑定模式。"""
        return bool(self._bound_ws_id)

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
        return self._local_use_builtin if self._bound_ws_id else self._config.use_builtin

    @Slot(bool)  # pyrefly: ignore [not-callable]
    def setUseBuiltin(self, value: bool) -> None:
        """设置是否启用内置规则。

        绑定模式下修改本地副本并写回工作区；全局模式直接修改 ``Config``。
        """
        if self._bound_ws_id:
            if value == self._local_use_builtin:
                return
            self._local_use_builtin = value
            self._persist_to_bound_workspace()
            self._reload_ruleset()
            self._rule_model.set_ruleset(self._ruleset)
            self.useBuiltinChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]
            return
        if value != self._config.use_builtin:
            self._config.use_builtin = value
            self._config_controller.save()  # pyrefly: ignore [missing-attribute]
            self.useBuiltinChanged.emit()  # pyrefly: ignore [missing-attribute]
            self._reload_ruleset()
            self._rule_model.set_ruleset(self._ruleset)
            self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Property("QVariantList", notify=rulesFileListChanged)  # pyrefly: ignore [not-callable, bad-argument-type]
    def rulesFileModel(self) -> list[dict[str, str]]:
        """规则文件列表（QML 直接 ListView 绑定）。"""
        paths = self._local_rules_paths if self._bound_ws_id else self._config.rules_paths
        return [{"fileName": Path(p).name, "path": p} for p in paths]

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

    @Property(bool, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def canMoveUp(self) -> bool:
        """是否可上移选中规则文件。"""
        return self._selected_file_index > 0

    @Property(bool, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def canMoveDown(self) -> bool:
        """是否可下移选中规则文件。"""
        paths = self._local_rules_paths if self._bound_ws_id else self._config.rules_paths
        return 0 <= self._selected_file_index < len(paths) - 1

    @Property(bool, notify=selectionChanged)  # pyrefly: ignore [not-callable]
    def canRemove(self) -> bool:
        """是否可移除选中规则文件。"""
        paths = self._local_rules_paths if self._bound_ws_id else self._config.rules_paths
        return 0 <= self._selected_file_index < len(paths)

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
        """从路径加载规则文件（QML ``FileDialog`` 选定后调用）。

        绑定模式下加入本地副本并写回工作区；全局模式直接加入 ``Config``。
        """
        path = Path(path_str)
        if not path.exists():
            logger.warning("规则文件不存在: %s", path_str)
            return False
        if self._bound_ws_id:
            if str(path) in self._local_rules_paths:
                logger.info("规则文件已加载，跳过: %s", path_str)
                return False
            self._local_rules_paths.append(str(path))
            try:
                self._persist_to_bound_workspace()
                self._reload_ruleset()
                self._rule_model.set_ruleset(self._ruleset)
                self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
                self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]
                return True
            except RuleError as exc:
                # 加载失败：回滚刚加入的路径
                if str(path) in self._local_rules_paths:
                    self._local_rules_paths.remove(str(path))
                logger.warning("加载规则失败: %s", exc)
                return False
        # 全局模式
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

    @Slot()  # pyrefly: ignore [not-callable]
    def moveUp(self) -> None:
        """上移选中规则文件。"""
        if not self.canMoveUp:
            return
        idx = self._selected_file_index
        if self._bound_ws_id:
            paths = self._local_rules_paths
            paths[idx - 1], paths[idx] = paths[idx], paths[idx - 1]
            self._selected_file_index = idx - 1
            self._persist_to_bound_workspace()
            self._reload_ruleset()
            self._rule_model.set_ruleset(self._ruleset)
            self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]
            return
        paths = self._config.rules_paths
        paths[idx - 1], paths[idx] = paths[idx], paths[idx - 1]
        self._selected_file_index = idx - 1
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def moveDown(self) -> None:
        """下移选中规则文件。"""
        if not self.canMoveDown:
            return
        idx = self._selected_file_index
        if self._bound_ws_id:
            paths = self._local_rules_paths
            paths[idx + 1], paths[idx] = paths[idx], paths[idx + 1]
            self._selected_file_index = idx + 1
            self._persist_to_bound_workspace()
            self._reload_ruleset()
            self._rule_model.set_ruleset(self._ruleset)
            self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]
            return
        paths = self._config.rules_paths
        paths[idx + 1], paths[idx] = paths[idx], paths[idx + 1]
        self._selected_file_index = idx + 1
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    @Slot()  # pyrefly: ignore [not-callable]
    def removeSelected(self) -> None:
        """移除选中规则文件。"""
        if not self.canRemove:
            return
        idx = self._selected_file_index
        if self._bound_ws_id:
            self._local_rules_paths.pop(idx)
            self._selected_file_index = -1
            self._persist_to_bound_workspace()
            self._reload_ruleset()
            self._rule_model.set_ruleset(self._ruleset)
            self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
            self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]
            return
        self._config.rules_paths.pop(idx)
        self._selected_file_index = -1
        self._config_controller.save()  # pyrefly: ignore [missing-attribute]
        self._reload_ruleset()
        self._rule_model.set_ruleset(self._ruleset)
        self.rulesFileListChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.selectionChanged.emit()  # pyrefly: ignore [missing-attribute]
        self.rulesetChanged.emit()  # pyrefly: ignore [missing-attribute]

    # ------------------- iter-122：导入/导出 -------------------

    @Slot(str, result=bool)  # pyrefly: ignore [not-callable]
    def exportRuleset(self, path_str: str) -> bool:
        """导出当前规则集到 YAML/JSON 文件（iter-122）。

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
        """从 YAML/JSON 文件导入规则集（iter-122）。

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
            if str(path) in (self._local_rules_paths if self._bound_ws_id else self._config.rules_paths):
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

    def _persist_to_bound_workspace(self) -> None:
        """将本地副本写回绑定的 :class:`WorkspaceItem`，并通知对应 ScanController 刷新。

        委托 :meth:`WorkspaceController.updateWorkspaceRules` 完成 WorkspaceItem 更新、
        ScanController ruleset 注入与持久化。
        """
        if not self._bound_ws_id or self._workspace_controller is None:
            return
        # @Slot 装饰的方法在 Python 端可直接调用
        self._workspace_controller.updateWorkspaceRules(  # pyrefly: ignore [missing-attribute]
            self._bound_ws_id,
            list(self._local_rules_paths),
            self._local_use_builtin,
        )

    def _reload_ruleset(self) -> None:
        """重新加载规则集（按 use_builtin + rules_paths 合并）。

        绑定模式从本地副本加载；全局模式从 ``Config`` 加载。
        """
        from fuscan.config import load_with_builtin

        if self._bound_ws_id:
            paths = [Path(p) for p in self._local_rules_paths if Path(p).exists()]
            use_builtin = self._local_use_builtin
        else:
            paths = [Path(p) for p in self._config.rules_paths if Path(p).exists()]
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
