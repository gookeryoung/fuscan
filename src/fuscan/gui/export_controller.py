"""扫描结果导出控制器。

封装导出流程的完整交互：格式选择对话框 → 文件保存对话框 → 启动 ExportWorker
后台导出 → 完成/失败回调。主窗口通过公共 API 驱动，不直接操作导出按钮与
状态栏文本，提高功能内聚（iter-79 续解耦）。

设计要点：

- 持有 ``export_btn`` 引用用于导出期间禁用按钮，完成后通过
  ``button_restore_requested`` 信号通知主窗口重新计算按钮状态
  （主窗口 ``_update_stage_actions`` 统一管理按钮可用性）
- 持有 ``stats_label`` 引用显示"正在导出"/"已导出"/"导出失败"提示
- 通过 ``report_getter`` 回调读取 ``_last_report``，避免双状态同步
- ExportWorker 延迟导入，避免 main_window 顶部依赖 reportlab/openpyxl
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

try:
    from PySide2.QtCore import QObject, Signal, Slot
    from PySide2.QtWidgets import (
        QFileDialog,
        QInputDialog,
        QMessageBox,
        QPushButton,
    )
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QObject, Signal, Slot  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import (  # pyrefly: ignore [missing-import]
        QFileDialog,
        QInputDialog,
        QMessageBox,
        QPushButton,
    )

from fuscan.scanner import ScanReport

if TYPE_CHECKING:
    from PySide2.QtWidgets import QLabel, QWidget

__all__ = ["ExportController"]

logger = logging.getLogger(__name__)

# 导出格式定义：(显示标签, 格式标识, 文件扩展名)。顺序即菜单显示顺序。
# 同一标识可能与扩展名不同（如 excel → xlsx），通过元组显式表达映射关系，
# 避免 export 内 ``ext = "xlsx" if fmt == "excel" else fmt`` 的特判分支。
_EXPORT_FORMATS: tuple[tuple[str, str, str], ...] = (
    ("CSV 文件 (*.csv)", "csv", "csv"),
    ("JSON 文件 (*.json)", "json", "json"),
    ("PDF 文件 (*.pdf)", "pdf", "pdf"),
    ("Excel 文件 (*.xlsx)", "excel", "xlsx"),
)
# 从 _EXPORT_FORMATS 派生的查找表（模块级常量避免每次调用重建 dict）
_EXPORT_LABEL_TO_FMT: dict[str, str] = {label: fmt for label, fmt, _ in _EXPORT_FORMATS}
_EXPORT_FMT_TO_EXT: dict[str, str] = {fmt: ext for _, fmt, ext in _EXPORT_FORMATS}


class ExportController(QObject):  # pyrefly: ignore [invalid-inheritance]
    """扫描结果导出控制器：封装格式选择 + 文件保存 + 后台导出 + 结果回调。

    职责内聚：

    - :meth:`show_menu` 弹出格式选择对话框（QInputDialog.getItem）
    - :meth:`export` 按指定格式导出（弹文件保存对话框 + 启动 ExportWorker）
    - :meth:`cleanup` 清理后台导出线程（供主窗口 closeEvent 调用）
    - 导出期间禁用 ``export_btn``，完成/失败后通过
      ``button_restore_requested`` 信号通知主窗口恢复按钮状态
    - 通过 ``report_getter`` 回调读取 ``_last_report``，避免双状态同步

    导出按钮的 ``enabled`` 状态在非导出期间由主窗口
    ``_update_stage_actions`` 统一管理（基于 workflow_stage 与 has_report）。
    """

    button_restore_requested = Signal()

    def __init__(
        self,
        export_btn: QPushButton,
        stats_label: QLabel,
        report_getter: Callable[[], ScanReport | None],
        parent_widget: QWidget,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._export_btn = export_btn
        self._stats_label = stats_label
        self._report_getter = report_getter
        self._parent_widget = parent_widget
        self._export_worker = None  # type: ignore[var-annotated]

    # ----------------------------- 公共 API -----------------------------

    def show_menu(self) -> None:
        """导出按钮入口：弹出格式选择对话框。

        支持 CSV/JSON/PDF/Excel 四种格式，PDF 与 Excel 为二进制格式，
        通过 :func:`fuscan.scanner.export.save_report` 统一写入（按扩展名
        自动选择序列化方式）。
        """
        if self._report_getter() is None:
            QMessageBox.information(self._parent_widget, "提示", "无可导出的扫描结果")
            return
        labels = [label for label, _, _ in _EXPORT_FORMATS]
        choice, ok = QInputDialog.getItem(self._parent_widget, "导出扫描结果", "选择导出格式:", labels, 0, False)
        if not ok:
            return
        self.export(_EXPORT_LABEL_TO_FMT[choice])

    def export(self, fmt: str) -> None:
        """导出扫描结果到文件（异步：通过 ExportWorker 在后台执行）。

        :param fmt: 格式标识，``csv``/``json``/``pdf``/``excel``。
            文本格式（csv/json）按 UTF-8 写入；二进制格式（pdf/excel）写 bytes。
            统一委托给 :func:`fuscan.scanner.export.save_report`，由其按扩展名
            自动选择序列化方式。

        iter-59 改为异步：PDF/Excel 渲染可能耗时数秒，同步执行会让 UI 完全
        无响应。导出期间禁用导出按钮，导出完成/失败后通过信号槽回到主线程
        处理结果对话框与状态栏提示。
        """
        report = self._report_getter()
        if report is None:
            QMessageBox.information(self._parent_widget, "提示", "无可导出的扫描结果")
            return

        ext = _EXPORT_FMT_TO_EXT.get(fmt, fmt)
        filter_str = f"{fmt.upper()} 文件 (*.{ext})"
        default_name = f"fuscan_report.{ext}"
        path_str, _ = QFileDialog.getSaveFileName(
            self._parent_widget,
            "导出扫描结果",
            default_name,
            filter_str,
        )
        if not path_str:
            return
        path = Path(path_str)
        # 禁用导出按钮防止重复触发；恢复由 button_restore_requested 信号触发
        # 主窗口 _update_stage_actions 重新计算
        self._export_btn.setEnabled(False)
        self._stats_label.setText(f"正在导出 {path.name}...")
        # 延迟加载 ExportWorker，避免 main_window 顶部依赖 reportlab/openpyxl 触发导入
        from fuscan.workers import ExportWorker

        self._export_worker = ExportWorker(report, path, parent=self._parent_widget)
        self._export_worker.finished_ok.connect(self._on_export_finished)  # pyrefly: ignore [missing-attribute]
        self._export_worker.failed.connect(self._on_export_failed)  # pyrefly: ignore [missing-attribute]
        self._export_worker.start()

    def cleanup(self) -> None:
        """清理后台导出线程（供主窗口 closeEvent 调用）。"""
        if self._export_worker is None:
            return
        self._export_worker.wait(2000)
        self._export_worker = None

    # ----------------------------- 内部槽 -----------------------------

    @Slot(object)  # pyrefly: ignore [not-callable]
    def _on_export_finished(self, path: Path) -> None:
        """导出完成回调：更新状态栏并提示用户，请求主窗口恢复按钮状态。"""
        self._cleanup_export_worker()
        self._stats_label.setText(f"已导出: {path}")
        QMessageBox.information(self._parent_widget, "导出成功", f"已导出到:\n{path}")
        self.button_restore_requested.emit()  # pyrefly: ignore [missing-attribute]

    @Slot(str)  # pyrefly: ignore [not-callable]
    def _on_export_failed(self, error: str) -> None:
        """导出失败回调：更新状态栏并提示错误，请求主窗口恢复按钮状态。"""
        self._cleanup_export_worker()
        self._stats_label.setText("导出失败")
        QMessageBox.warning(self._parent_widget, "导出失败", error)
        self.button_restore_requested.emit()  # pyrefly: ignore [missing-attribute]

    def _cleanup_export_worker(self) -> None:
        """清理后台导出线程：等待退出后释放引用。"""
        worker = self._export_worker
        if worker is None:
            return
        worker.wait(2000)
        worker.deleteLater()
        self._export_worker = None
