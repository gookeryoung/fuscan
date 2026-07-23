"""规则文件列表面板控制器。

封装规则文件列表 ``QListWidget`` 的全部交互：列表刷新、上移/下移/移除操作、
内置规则勾选、右键菜单。主窗口通过公共 API 驱动，不直接操作底层控件，
提高功能内聚（iter-79 续解耦）。

设计要点：

- 持有 ``_use_builtin`` 与 ``_rules_paths`` 状态（与 ScanModePanel 风格一致）
- 主窗口通过 ``use_builtin`` / ``rules_paths`` property 访问，保持向后兼容
- 用户勾选内置规则 / 上移 / 下移 / 移除后 emit ``rules_changed``，主窗口据此
  重新加载规则集 + 保存配置
- ``set_use_builtin`` 仅赋值不 emit 信号，供主窗口 ``_set_use_builtin`` 外部
  主动设置后统一调 ``_apply_ruleset_loaded`` 刷新
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QObject, Qt, Signal
    from PySide2.QtWidgets import QAction, QListWidget, QListWidgetItem, QMenu
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QObject, Qt, Signal  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QAction  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import (  # pyrefly: ignore [missing-import]
        QListWidget,
        QListWidgetItem,
        QMenu,
    )

if TYPE_CHECKING:
    from PySide2.QtWidgets import QWidget

    from fuscan.config import Config

__all__ = ["RulesFilePanel"]

logger = logging.getLogger(__name__)


class RulesFilePanel(QObject):  # pyrefly: ignore [invalid-inheritance]
    """规则文件列表面板控制器：封装列表显示 + 顺序操作 + 内置勾选 + 右键菜单。

    职责内聚：

    - 管理 ``rules_file_list`` 列表项（内置规则条目 row 0 + 用户规则条目 row 1+）
    - 持有 ``_use_builtin`` 与 ``_rules_paths`` 状态
    - :meth:`refresh` 按当前状态重建列表项（blockSignals 防循环）
    - :meth:`move_up` / :meth:`move_down` / :meth:`remove_selected` 操作选中项
    - 内置规则勾选变化触发 ``rules_changed`` 信号
    - 右键菜单（上移/下移/移除）内聚，内置规则条目固定不可操作
    - :meth:`apply_config` / :meth:`save_config` 配置持久化

    主窗口通过 ``rules_changed`` 信号感知用户操作（勾选/上移/下移/移除），
    据此重新加载规则集并保存配置。
    """

    rules_changed = Signal()

    def __init__(self, rules_file_list: QListWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._list = rules_file_list
        # 内置通用规则开关（随软件分发，勾选时随启动自动加载）
        self._use_builtin: bool = True
        # 用户规则文件路径列表（顺序即加载优先级，后加载覆盖先加载）
        self._rules_paths: list[Path] = []

        # 内置规则勾选变化（itemChanged）与右键菜单（customContextMenuRequested）
        # 由 panel 内部连接，主窗口不介入
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)

    # ----------------------------- 内部槽 -----------------------------

    def _on_item_changed(self, item: QListWidgetItem) -> None:  # type: ignore[unknown-name]
        """列表项变化处理：仅内置规则条目（row 0）的勾选状态变化触发持久化。

        用户勾选/取消勾选内置规则后，立即更新 ``_use_builtin`` 开关并 emit
        ``rules_changed``，主窗口据此重新加载规则集并保存配置。
        """
        if self._list.row(item) != 0:
            return
        enabled = item.checkState() == Qt.Checked
        if enabled == self._use_builtin:
            return
        self._use_builtin = enabled
        self.rules_changed.emit()  # pyrefly: ignore [missing-attribute]

    def _on_context_menu(self, pos) -> None:  # type: ignore[no-untyped-def]
        """右键菜单：上移 / 下移 / 移除。

        内置规则条目（row 0）固定不可移动、不可移除，菜单禁用所有操作。
        """
        row = self._list.currentRow()
        if row < 0:
            return
        menu = QMenu(self._list)
        action_up = QAction("上移", menu)
        action_down = QAction("下移", menu)
        action_remove = QAction("移除", menu)
        # row 0 为内置规则条目，所有操作禁用
        is_builtin_row = row == 0
        action_up.setEnabled(not is_builtin_row and row > 1)
        action_down.setEnabled(not is_builtin_row and row < len(self._rules_paths))
        action_remove.setEnabled(not is_builtin_row)
        action_up.triggered.connect(self.move_up)
        action_down.triggered.connect(self.move_down)
        action_remove.triggered.connect(self.remove_selected)
        menu.addAction(action_up)  # pyrefly: ignore [missing-argument]
        menu.addAction(action_down)  # pyrefly: ignore [missing-argument]
        menu.addSeparator()
        menu.addAction(action_remove)  # pyrefly: ignore [missing-argument]
        menu.exec_(self._list.viewport().mapToGlobal(pos))  # pyrefly: ignore [missing-argument]

    # ----------------------------- 公共 API -----------------------------

    def refresh(self) -> None:
        """刷新规则文件列表展示。

        顶部固定显示内置通用规则条目（带复选框，反映 ``_use_builtin`` 状态），
        其后依次显示用户规则文件路径。``blockSignals`` 包裹避免 ``clear`` /
        ``addItem`` 触发 ``itemChanged`` 引发勾选状态回写。
        """
        self._list.blockSignals(True)
        try:
            self._list.clear()
            # 内置规则条目（row 0）：固定不可移动、不可移除
            builtin_item = QListWidgetItem("内置通用规则（随软件分发）")
            builtin_item.setToolTip("勾选时随软件启动自动加载；取消勾选后下次不再自动加载")
            builtin_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            builtin_item.setCheckState(Qt.Checked if self._use_builtin else Qt.Unchecked)
            self._list.addItem(builtin_item)  # pyrefly: ignore [missing-argument]
            # 用户规则条目（row 1+）
            for path in self._rules_paths:
                item = QListWidgetItem(str(path))
                item.setToolTip(str(path))
                self._list.addItem(item)  # pyrefly: ignore [missing-argument]
        finally:
            self._list.blockSignals(False)

    def move_up(self) -> None:
        """将选中的规则文件上移一位。

        列表 row 0 为内置规则条目（固定不可移动），用户规则实际索引 = row - 1。
        操作后 emit ``rules_changed`` 通知主窗口重新加载规则集。
        """
        row = self._list.currentRow()
        if row <= 1:  # row 0=内置规则不可移动；row 1=首个用户规则已居顶
            return
        idx = row - 1
        self._rules_paths[idx - 1], self._rules_paths[idx] = (
            self._rules_paths[idx],
            self._rules_paths[idx - 1],
        )
        self.refresh()
        self._list.setCurrentRow(row - 1)  # pyrefly: ignore [missing-argument]
        self.rules_changed.emit()  # pyrefly: ignore [missing-attribute]

    def move_down(self) -> None:
        """将选中的规则文件下移一位。

        列表 row 0 为内置规则条目（固定不可移动），用户规则实际索引 = row - 1。
        操作后 emit ``rules_changed`` 通知主窗口重新加载规则集。
        """
        row = self._list.currentRow()
        if row <= 0 or row >= len(self._rules_paths):  # row 0=内置规则不可移动
            return
        idx = row - 1
        self._rules_paths[idx + 1], self._rules_paths[idx] = (
            self._rules_paths[idx],
            self._rules_paths[idx + 1],
        )
        self.refresh()
        self._list.setCurrentRow(row + 1)  # pyrefly: ignore [missing-argument]
        self.rules_changed.emit()  # pyrefly: ignore [missing-attribute]

    def remove_selected(self) -> None:
        """移除选中的规则文件。

        列表 row 0 为内置规则条目（固定不可移除），用户规则实际索引 = row - 1。
        操作后 emit ``rules_changed`` 通知主窗口重新加载规则集。
        """
        row = self._list.currentRow()
        if row <= 0:  # row 0=内置规则不可移除
            return
        idx = row - 1
        del self._rules_paths[idx]
        self.refresh()
        self.rules_changed.emit()  # pyrefly: ignore [missing-attribute]

    def set_use_builtin(self, enabled: bool) -> None:
        """设置内置规则开关（仅赋值，不 emit 信号，不刷新列表）。

        供主窗口 ``_set_use_builtin`` 外部主动设置后统一调
        ``_apply_ruleset_loaded`` 刷新 UI。与用户勾选触发的 ``_on_item_changed``
        不同：后者会 emit ``rules_changed`` 通知主窗口重载。
        """
        self._use_builtin = enabled

    def apply_config(self, config: Config) -> None:
        """从配置恢复内置规则开关与用户规则路径列表。

        :param config: 配置对象，读取 ``use_builtin`` 与 ``rules_paths``
        """
        self._use_builtin = config.use_builtin
        self._rules_paths = [Path(p) for p in config.rules_paths if Path(p).exists()]

    def save_config(self, config: Config) -> None:
        """保存内置规则开关与用户规则路径到配置。

        :param config: 配置对象，写入 ``use_builtin`` 与 ``rules_paths``
        """
        config.use_builtin = self._use_builtin
        config.rules_paths = [str(p) for p in self._rules_paths]

    # ----------------------------- 属性 -----------------------------

    @property
    def use_builtin(self) -> bool:
        """内置规则开关状态。"""
        return self._use_builtin

    @use_builtin.setter
    def use_builtin(self, value: bool) -> None:
        self._use_builtin = value

    @property
    def rules_paths(self) -> list[Path]:
        """用户规则文件路径列表（返回内部引用，主窗口可 append/remove 后调 refresh）。

        返回内部 list 引用而非副本，与 ScanModePanel 持有状态的封装风格一致：
        主窗口通过 ``_on_load_rules`` 直接 append/remove 后调
        ``_apply_ruleset_loaded`` 刷新，无需通过 panel API 中转。
        """
        return self._rules_paths

    @rules_paths.setter
    def rules_paths(self, value: list[Path]) -> None:
        self._rules_paths = value

    @property
    def last_rule_dir(self) -> Path:
        """最近一个规则文件所在目录（供主窗口 QFileDialog 定位初始目录）。

        无已加载规则时返回用户主目录。
        """
        if self._rules_paths:
            return self._rules_paths[-1].parent
        return Path.home()
