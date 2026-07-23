"""扫描模式选择面板控制器。

封装扫描模式 ``QComboBox``、目标选择区 ``QStackedWidget`` 与盘符按钮组
的全部交互逻辑：模式切换、盘符按钮创建/刷新/选择、folder 路径状态管理。
主窗口通过公共 API 驱动，不直接操作底层控件，提高功能内聚（iter-79 续）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from PySide2.QtCore import QObject, QSize, Signal
    from PySide2.QtWidgets import (
        QAbstractButton,
        QButtonGroup,
        QComboBox,
        QPushButton,
        QStackedWidget,
    )
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QObject, QSize, Signal  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import (  # pyrefly: ignore [missing-import]
        QAbstractButton,
        QButtonGroup,
        QComboBox,
        QPushButton,
        QStackedWidget,
    )

from fuscan.config import Config
from fuscan.scanner import list_drives

if TYPE_CHECKING:
    from PySide2.QtGui import QIcon
    from PySide2.QtWidgets import QLayout

__all__ = ["ScanModePanel"]

logger = logging.getLogger(__name__)

# 扫描模式 ↔ combo index 双向映射（避免 _on_mode_changed 与 _update_target_visibility
# 各自维护一份字面量字典导致漂移）
_SCAN_MODE_TO_INDEX: dict[str, int] = {"full": 0, "drive": 1, "folder": 2}
_INDEX_TO_SCAN_MODE: dict[int, str] = {v: k for k, v in _SCAN_MODE_TO_INDEX.items()}


class ScanModePanel(QObject):  # pyrefly: ignore [invalid-inheritance]
    """扫描模式选择面板控制器：封装模式 combo、目标选择区与盘符按钮组。

    职责内聚：

    - 管理 ``scan_mode_combo`` 三种模式切换（full/drive/folder）
    - 管理 ``target_stack`` QStackedWidget 可见性同步
    - 管理 ``drive_buttons_layout`` 盘符按钮组的创建、刷新与选择
    - 持有 folder 模式下的 ``_folder_root`` 路径状态
    - :meth:`apply_config` 从配置恢复模式、盘符选择与 folder 路径
    - :meth:`save_config` 保存模式与盘符到配置
    - :meth:`can_start_scan` 判断当前模式是否可启动扫描
    - :meth:`build_scan_roots` 构造根路径列表（供 :class:`ScanWorker` 使用）

    主窗口通过 ``mode_changed`` 信号感知模式/盘符/folder 路径变化，调用
    ``_update_scan_button`` 更新扫描按钮启用状态。
    """

    mode_changed = Signal()

    def __init__(
        self,
        combo: QComboBox,
        target_stack: QStackedWidget,
        drive_buttons_layout: QLayout,
        hard_disk_icon: QIcon,
        config: Config,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._combo = combo
        self._target_stack = target_stack
        self._drive_buttons_layout = drive_buttons_layout
        self._hard_disk_icon = hard_disk_icon
        self._config = config

        # 扫描模式："full"（全盘）、"drive"（盘符）、"folder"（文件夹）
        self._scan_mode: str = "folder"
        # drive 模式下选中的盘符路径（如 "C:\\"），full/folder 模式下为 None
        self._selected_drive: str | None = None
        # folder 模式下的扫描根路径，full/drive 模式下为 None
        self._folder_root: Path | None = None

        # 盘符按钮组（平铺选择，替代下拉）：drive 模式下展示所有可用盘符
        self._drive_button_group = QButtonGroup(self)
        self._drive_button_group.setExclusive(True)
        self._drive_button_group.buttonClicked.connect(self._on_drive_selected)
        self._drive_buttons: list[QPushButton] = []

        # 扫描模式切换信号
        self._combo.currentIndexChanged.connect(self._on_mode_changed)

        # 初始填充盘符按钮列表（apply_config 时按需调用 refresh）
        self._refresh_drive_buttons()

    # ----------------------------- 内部槽 -----------------------------

    def _on_mode_changed(self, index: int) -> None:
        """扫描模式切换：更新目标选择器可见性并 emit mode_changed。

        由 ``scan_mode_combo.currentIndexChanged`` 触发，主窗口据此更新
        扫描按钮启用状态（full 模式无需选路径即可扫描）。
        """
        self._scan_mode = _INDEX_TO_SCAN_MODE.get(index, "folder")
        self._update_target_visibility()
        self.mode_changed.emit()  # pyrefly: ignore [missing-attribute]

    def _on_drive_selected(self, _button: QAbstractButton) -> None:
        """盘符按钮选择变更：更新 _selected_drive 并 emit mode_changed。"""
        checked = self._drive_button_group.checkedButton()
        self._selected_drive = checked.property("drive") if checked is not None else None  # pyrefly: ignore [bad-argument-type]
        self.mode_changed.emit()  # pyrefly: ignore [missing-attribute]

    def _update_target_visibility(self) -> None:
        """根据扫描模式切换目标选择区页面（QStackedWidget 保持布局稳定）。

        - full（index 0）：全盘扫描说明页
        - drive（index 1）：盘符按钮平铺页
        - folder（index 2）：路径选择行
        """
        self._target_stack.setCurrentIndex(_SCAN_MODE_TO_INDEX.get(self._scan_mode, 2))

    def _refresh_drive_buttons(self) -> None:
        """刷新盘符按钮列表（hard_disk 图标 + 盘符字母，平铺展示）。

        清除旧按钮后按 ``list_drives`` 重新创建，盘符图标尺寸来自
        ``Config.drive_icon_size``。设置对话框中切换网络驱动器选项后调用。
        """
        # 清除旧按钮
        for btn in self._drive_buttons:
            self._drive_button_group.removeButton(btn)
            self._drive_buttons_layout.removeWidget(btn)
            btn.deleteLater()
        self._drive_buttons.clear()

        for drive in list_drives(include_network=self._config.include_network_drives):
            letter = str(drive)[:1]
            btn = QPushButton(letter, self._target_stack.widget(1))
            btn.setObjectName(f"drive_btn_{letter}")
            btn.setCheckable(True)
            btn.setProperty("drive", str(drive))  # pyrefly: ignore [bad-argument-type]
            btn.setIcon(self._hard_disk_icon)
            btn.setIconSize(QSize(14, self._config.drive_icon_size))
            self._drive_buttons_layout.addWidget(btn)
            self._drive_button_group.addButton(btn)
            self._drive_buttons.append(btn)

    # ----------------------------- 公共 API -----------------------------

    def apply_config(self, config: Config) -> None:
        """从配置恢复扫描模式、盘符选择。

        ``blockSignals`` 包裹 ``setCurrentIndex`` 避免触发 ``_on_mode_changed``
        重复 emit ``mode_changed``（主窗口 ``_apply_config`` 末尾统一调用
        ``_update_scan_button``）。

        folder 路径恢复由主窗口在调用本方法后通过 :meth:`set_folder_root`
        单独设置（依赖 ``path_combo`` 已被 :class:`ScanPathHistory` 加载）。

        :param config: 配置对象，读取 ``scan_mode`` 与 ``last_drive``
        """
        self._scan_mode = config.scan_mode if config.scan_mode in ("full", "drive", "folder") else "folder"
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(_SCAN_MODE_TO_INDEX[self._scan_mode])
        self._combo.blockSignals(False)
        self._update_target_visibility()

        # 恢复上次选择的盘符
        if config.last_drive:
            target = config.last_drive
            for btn in self._drive_buttons:
                if btn.property("drive") == target:  # pyrefly: ignore [bad-argument-type]
                    btn.setChecked(True)
                    self._selected_drive = target
                    break

    def save_config(self, config: Config) -> None:
        """保存扫描模式与盘符选择到配置。

        :param config: 配置对象，写入 ``scan_mode`` 与 ``last_drive``
        """
        config.scan_mode = self._scan_mode
        config.last_drive = self._selected_drive

    def can_start_scan(self) -> bool:
        """判断当前模式是否可启动扫描。

        - full：始终可启动（全盘扫描所有盘符）
        - drive：需已选中盘符
        - folder：需已设置 folder 路径
        """
        if self._scan_mode == "full":
            return True
        if self._scan_mode == "drive":
            return self._selected_drive is not None
        return self._folder_root is not None

    def build_scan_roots(self) -> list[Path]:
        """构造根路径列表（供 :class:`ScanWorker` 使用）。

        - full：所有可用盘符（含网络驱动器，由配置决定）
        - drive：选中的单个盘符
        - folder：folder 模式根路径
        """
        if self._scan_mode == "full":
            return list_drives(include_network=self._config.include_network_drives)
        if self._scan_mode == "drive":
            return [Path(self._selected_drive)] if self._selected_drive else []
        return [self._folder_root] if self._folder_root else []

    def select_folder_mode(self) -> None:
        """切换到 folder 模式（历史项双击时调用）。

        设置 ``scan_mode_combo`` 当前索引为 2（folder），触发
        ``_on_mode_changed`` 更新内部状态与目标选择区可见性。
        """
        self._combo.setCurrentIndex(_SCAN_MODE_TO_INDEX["folder"])

    def set_folder_root(self, path: Path | None) -> None:
        """设置 folder 模式的扫描根路径。

        由主窗口在以下场景调用：

        - ``_on_select_path``：用户通过 QFileDialog 选择目录后
        - ``_on_path_selected``：用户从 path_combo 切换路径后
        - ``_apply_config``：从配置恢复首个有效路径后

        设置后 emit ``mode_changed`` 通知主窗口更新扫描按钮启用状态。
        """
        self._folder_root = path
        self.mode_changed.emit()  # pyrefly: ignore [missing-attribute]

    def refresh_drives(self) -> None:
        """重新刷新盘符按钮列表。

        设置对话框中切换 ``include_network_drives`` 选项后调用，
        确保盘符列表反映最新配置。
        """
        self._refresh_drive_buttons()

    @property
    def folder_root(self) -> Path | None:
        """folder 模式下的扫描根路径（供主窗口 QFileDialog 定位初始目录）。"""
        return self._folder_root
