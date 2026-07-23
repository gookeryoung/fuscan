"""工作流阶段控制器。

封装主窗口的三页整页切换布局（配置页 / 扫描中页 / 结果页）的阶段管理、
页面切换与按钮/actions 可用性更新。主窗口通过公共 API 驱动，不直接操作
底层控件，提高功能内聚（iter-80 UI 控件解耦系列）。

设计要点：

- 持有 ``_stage``（WorkflowStage）状态，主窗口通过 ``current_stage`` property 访问
- 通过 4 个 callback 读取外部状态（是否暂停 / 是否有报告 / 是否有命中 /
  是否可以开始扫描），避免 panel 持有主窗口的扫描状态与规则集引用
- :meth:`update_actions` 根据当前阶段与外部状态统一更新 17 个控件的可用性
- :meth:`switch_stage` 切换页面 + 同步侧边栏 + 更新 actions
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

try:
    from PySide2.QtCore import QObject
    from PySide2.QtWidgets import (
        QAction,
        QLabel,
        QListWidget,
        QProgressBar,
        QPushButton,
        QStackedWidget,
    )
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QObject  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QAction  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import (  # pyrefly: ignore [missing-import]
        QLabel,
        QListWidget,
        QProgressBar,
        QPushButton,
        QStackedWidget,
    )

if TYPE_CHECKING:
    from PySide2.QtWidgets import QWidget

__all__ = ["StageController", "StageControls", "WorkflowStage"]

logger = logging.getLogger(__name__)


class WorkflowStage(enum.Enum):
    """工作流阶段，决定主界面 QStackedWidget 显示哪一页。"""

    SETUP = "setup"
    SCANNING = "scanning"
    RESULTS = "results"


# 工作流阶段 ↔ main_stack page index / sidebar row 双向映射
_STAGE_TO_PAGE_INDEX: dict[WorkflowStage, int] = {
    WorkflowStage.SETUP: 0,
    WorkflowStage.SCANNING: 1,
    WorkflowStage.RESULTS: 2,
}
_SIDEBAR_ROW_TO_STAGE: dict[int, WorkflowStage] = {v: k for k, v in _STAGE_TO_PAGE_INDEX.items()}


@dataclass(frozen=True)
class StageControls:
    """阶段控制器操作的控件引用集合（由主窗口 setupUi 创建后传入）。"""

    main_stack: QStackedWidget
    sidebar: QListWidget
    tab_stack: QStackedWidget
    scan_btn: QPushButton
    view_results_btn: QPushButton
    progress: QProgressBar
    current_file_label: QLabel
    pause_resume_btn: QPushButton
    cancel_btn: QPushButton
    rescan_btn: QPushButton
    export_btn: QPushButton
    scan_action: QAction
    select_path_action: QAction
    export_csv_action: QAction
    export_json_action: QAction
    load_rules_action: QAction
    edit_rules_action: QAction


class StageController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """工作流阶段控制器：管理三页切换 + 按钮/actions 可用性。

    职责内聚：

    - 持有 ``_stage``（WorkflowStage）状态
    - :meth:`switch_stage` 切换 main_stack 页面 + 同步 sidebar 选中项 + 更新 actions
    - :meth:`update_actions` 根据当前阶段与外部状态统一更新 17 个控件可用性
    - :meth:`on_header_tab_changed` / :meth:`on_sidebar_stage_changed` 处理用户切换
    - :meth:`view_results` / :meth:`rescan` 封装配置页 ↔ 结果页的跳转

    外部状态通过 4 个 callback 读取，避免 panel 持有主窗口的扫描状态与规则集：

    - ``is_paused_getter``：扫描是否处于暂停状态（决定 pause_resume_btn 文案）
    - ``has_report_getter``：是否有扫描报告（决定 view_results_btn / export actions）
    - ``has_hits_getter``：报告是否有命中（决定 export_btn）
    - ``can_start_scan_getter``：是否满足开始扫描条件（决定 scan_btn / scan_action）
    """

    def __init__(
        self,
        controls: StageControls,
        is_paused_getter: Callable[[], bool],
        has_report_getter: Callable[[], bool],
        has_hits_getter: Callable[[], bool],
        can_start_scan_getter: Callable[[], bool],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controls = controls
        self._is_paused_getter = is_paused_getter
        self._has_report_getter = has_report_getter
        self._has_hits_getter = has_hits_getter
        self._can_start_scan_getter = can_start_scan_getter
        self._stage: WorkflowStage = WorkflowStage.SETUP

    # ----------------------------- 公共 API -----------------------------

    @property
    def current_stage(self) -> WorkflowStage:
        """当前工作流阶段。"""
        return self._stage

    def switch_stage(self, stage: WorkflowStage) -> None:
        """切换工作流阶段页面并更新控件状态。

        SETUP=0 配置页、SCANNING=1 扫描中页、RESULTS=2 结果页。
        同步侧边栏选中项，避免循环触发信号。
        """
        self._stage = stage
        page_index = _STAGE_TO_PAGE_INDEX[stage]
        self._controls.main_stack.setCurrentIndex(page_index)
        self._controls.sidebar.blockSignals(True)
        self._controls.sidebar.setCurrentRow(page_index)  # pyrefly: ignore [missing-argument]
        self._controls.sidebar.blockSignals(False)
        self.update_actions()

    def update_actions(self) -> None:
        """根据当前阶段与外部状态更新按钮和菜单的可用性。"""
        is_setup = self._stage == WorkflowStage.SETUP
        is_scanning = self._stage == WorkflowStage.SCANNING
        is_results = self._stage == WorkflowStage.RESULTS
        has_report = self._has_report_getter()
        can_start = self._can_start_scan_getter()

        # 配置页：scan_btn 仅在 SETUP 可用；view_results_btn 始终可见，根据是否有结果启用
        self._controls.scan_btn.setEnabled(is_setup and can_start)
        self._controls.view_results_btn.setVisible(is_setup)
        self._controls.view_results_btn.setEnabled(has_report)

        # 状态栏进度条与当前文件标签：扫描中阶段可见，其余阶段隐藏
        self._controls.progress.setVisible(is_scanning)
        self._controls.current_file_label.setVisible(is_scanning)

        # 扫描中页：pause_resume_btn 文案随暂停状态切换
        if is_scanning:
            if self._is_paused_getter():
                self._controls.pause_resume_btn.setText("继续扫描")
            else:
                self._controls.pause_resume_btn.setText("暂停扫描")

        # 暂停/取消按钮仅在扫描中阶段可用
        self._controls.pause_resume_btn.setEnabled(is_scanning)
        self._controls.cancel_btn.setEnabled(is_scanning)

        # 结果页
        self._controls.rescan_btn.setEnabled(is_results)
        if is_results and has_report:
            self._controls.export_btn.setEnabled(self._has_hits_getter())
        else:
            self._controls.export_btn.setEnabled(False)

        # 菜单 actions
        self._controls.scan_action.setEnabled(is_setup and can_start)
        self._controls.select_path_action.setEnabled(is_setup)
        self._controls.export_csv_action.setEnabled(is_results and has_report)
        self._controls.export_json_action.setEnabled(is_results and has_report)
        self._controls.load_rules_action.setEnabled(is_setup)
        self._controls.edit_rules_action.setEnabled(is_setup)

    def on_header_tab_changed(self, tab_id: int) -> None:
        """头部 Tab 切换：切换 tab_stack 页面，非扫描 Tab 隐藏侧边栏。

        :param tab_id: 0=扫描 / 1=规则管理 / 2=扫描历史
        """
        self._controls.tab_stack.setCurrentIndex(tab_id)
        self._controls.sidebar.setVisible(tab_id == 0)

    def on_sidebar_stage_changed(self, row: int) -> None:
        """侧边栏阶段项切换：映射 row 到 WorkflowStage 并切换页面。

        :param row: 0=配置 / 1=扫描中 / 2=结果
        """
        stage = _SIDEBAR_ROW_TO_STAGE.get(row)
        if stage is not None:
            self.switch_stage(stage)

    def view_results(self) -> None:
        """配置页"查看结果"按钮：有报告时切换到结果页。"""
        if self._has_report_getter():
            self.switch_stage(WorkflowStage.RESULTS)

    def rescan(self) -> None:
        """结果页"重新扫描"按钮：返回配置页。"""
        self.switch_stage(WorkflowStage.SETUP)
