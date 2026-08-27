"""文件扫描页：工作区列表 + 扫描进度面板。

- 工作区卡片：任务名/状态徽标/元数据/规则标签/摘要/分类计数 +
  启动暂停/重新扫描/配置规则/预览规则/查看结果/统计/展开更多操作
- 扫描进度面板：扫描中（含暂停态）以 GitHub Actions 风格阶段节点
  （收集→筛选→解析，竖直串联、进行中转圈动画）替换工作区列表
- 整页拖拽接收：拖入文件夹创建扫描任务，顶部 Toast 反馈
- 共享对话框组（:mod:`~fuscan.gui.widgets.home_dialogs`）：切换目标/
  配置规则/预览规则/扫描历史；导出与清空用原生对话框完成

刷新模型：工作区列表经 :class:`WorkspaceListModel` 信号定向刷新对应卡片；
扫描进度由活动 ScanController 的信号汇聚到 :meth:`_refresh_progress`。
"""

# pyrefly: ignore-errors
# PySide2 存根重载合并缺陷导致 QWidget/QLayout/QTimer 调用与 Signal.connect 误报，
# 详见 sidebar.py 头部说明；本文件为纯 GUI 布局代码，采用文件级压制。

from __future__ import annotations

import contextlib
import json

from PySide2.QtCore import Qt, QTimer, Signal
from PySide2.QtGui import QCloseEvent, QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide2.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from fuscan.gui.widgets.home_dialogs import (
    EditTargetDialog,
    HistoryDialog,
    PreviewRulesDialog,
    RulesDialog,
)
from fuscan.gui.widgets.qss import palette_tokens

__all__ = ["HomePage"]

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def _tokens(dark: bool) -> dict[str, str]:
    return palette_tokens(dark)


def _status_color(status_text: str, matched_count: int, t: dict[str, str]) -> str:
    """状态徽标语义色（与 StatsPage 判断逻辑一致）。"""
    s = str(status_text or "")
    if s == "扫描中":
        return t["warning"]
    if s == "已暂停":
        return t["text_secondary"]
    if s == "已完成":
        return t["danger"] if matched_count > 0 else t["success"]
    if "取消" in s or s == "失败":
        return t["warning"]
    return t["primary"]


def _is_completed(status_text: str) -> bool:
    """是否处于已完成态（含用户取消）：控制查看结果/重新扫描按钮。"""
    s = str(status_text or "")
    return s == "已完成" or "取消" in s


class _Badge(QLabel):
    """实底圆角状态徽标。"""

    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignCenter)

    def apply(self, text: str, bg: str) -> None:
        """设置文本与背景色。"""
        self.setText(text)
        self.setStyleSheet(
            f"background-color: {bg}; color: #FFFFFF; font-size: 11px;"
            " font-weight: bold; padding: 1px 9px; border-radius: 10px;"
        )


class _PhaseNode(QFrame):
    """GitHub Actions 风格阶段节点：状态图标 | 标题+明细+进度条。

    节点竖直串联时通过 :meth:`set_lines` 控制上下连接线显隐；
    进行中图标显示 braille 转圈动画（QTimer 120ms 帧轮换），完成显示对勾。
    """

    def __init__(self, dark: bool, accent_key: str = "primary") -> None:
        super().__init__()
        self._dark = dark
        self._accent_key = accent_key
        self._state = "pending"
        self._frame_idx = 0

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        # 左列：上连接线 / 状态图标 / 下连接线
        icon_col = QVBoxLayout()
        icon_col.setContentsMargins(6, 0, 0, 0)
        icon_col.setSpacing(0)
        icon_col.setAlignment(Qt.AlignHCenter)
        self._top_line = QFrame()
        self._top_line.setFixedWidth(2)
        self._bottom_line = QFrame()
        self._bottom_line.setFixedWidth(2)
        self._icon_label = QLabel("○")
        self._icon_label.setAlignment(Qt.AlignCenter)
        icon_col.addWidget(self._top_line)
        icon_col.addWidget(self._icon_label)
        icon_col.addWidget(self._bottom_line)
        row.addLayout(icon_col)

        right = QVBoxLayout()
        right.setContentsMargins(0, 2, 0, 6)
        right.setSpacing(4)
        self._title_label = QLabel()
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(self._title_label)
        title_row.addStretch()
        right.addLayout(title_row)
        self._detail_label = QLabel()
        self._detail_label.setWordWrap(True)
        right.addWidget(self._detail_label)
        self._progress = QProgressBar()
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        right.addWidget(self._progress)
        row.addLayout(right, stretch=1)

        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick_spinner)
        self.set_dark(dark)

    # ----------------------------- 公共 API -----------------------------

    def set_state(
        self,
        state: str,
        detail: str = "",
        progress_value: float = -1.0,
        indeterminate: bool = False,
    ) -> None:
        """更新节点状态。

        :param state: ``"pending"``/``"running"``/``"done"``
        :param detail: 明细文本（pending 时通常为空）
        :param progress_value: 进度值 0..1，-1 表示隐藏进度条或不确定态
        :param indeterminate: 不确定进度（busy 滚动条）
        """
        if state not in ("pending", "running", "done"):
            state = "pending"
        self._state = state
        self._detail_label.setText(detail)
        show_bar = state == "running" and (indeterminate or progress_value >= 0)
        self._progress.setVisible(show_bar)
        if show_bar:
            if indeterminate:
                self._progress.setRange(0, 0)
            else:
                self._progress.setRange(0, 100)
                self._progress.setValue(int(progress_value * 100))
        self._apply_icon()
        if state == "running":
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def set_lines(self, top: bool, bottom: bool) -> None:
        """控制上下连接线显隐（串联节点首尾分别隐藏）。"""
        self._top_line.setVisible(top)
        self._bottom_line.setVisible(bottom)

    def set_dark(self, dark: bool) -> None:
        """主题切换：刷新连接线/文字配色并重绘图标。"""
        self._dark = dark
        t = _tokens(dark)
        line_color = t["border_muted"]
        for w in (self._top_line, self._bottom_line):
            w.setStyleSheet(f"background-color: {line_color}; border: none;")
        self._title_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {t['text_primary']}; background: transparent;"
        )
        self._detail_label.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']}; background: transparent;")
        self.setStyleSheet("QFrame { border: none; }")
        self._apply_icon()

    # ----------------------------- 内部 -----------------------------

    def _tick_spinner(self) -> None:
        """转圈帧轮换。"""
        self._frame_idx = (self._frame_idx + 1) % len(_SPINNER_FRAMES)
        self._apply_icon()

    def _apply_icon(self) -> None:
        """按状态刷新图标字符与颜色。"""
        t = _tokens(self._dark)
        if self._state == "done":
            self._icon_label.setText("✓")
            color = t["success"]
        elif self._state == "running":
            self._icon_label.setText(_SPINNER_FRAMES[self._frame_idx])
            color = t.get(self._accent_key, t["primary"])
        else:
            self._icon_label.setText("○")
            color = t["text_secondary"]
        self._icon_label.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {color}; background: transparent;")
        self._icon_label.setFixedSize(20, 20)


class _ScanProgressCard(QFrame):
    """扫描进度面板：任务名/元数据/阶段时间线/分类计数/控制按钮。"""

    pause_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, dark: bool) -> None:
        super().__init__()
        self._dark = dark
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 16, 20, 16)
        col.setSpacing(14)

        # 第一行：任务名 + 状态徽标
        head = QHBoxLayout()
        head.setSpacing(10)
        self._name_label = QLabel()
        head.addWidget(self._name_label, stretch=1)
        self._badge = _Badge()
        head.addWidget(self._badge)
        col.addLayout(head)

        # 第二行：元数据网格（模式/目标/配置/当前文件）
        grid = QHBoxLayout()
        grid.setSpacing(16)
        self._meta_labels: list[tuple[QLabel, QLabel]] = []
        for caption in ("模式", "目标", "配置", "当前文件"):
            box = QVBoxLayout()
            cap = QLabel(caption)
            val = QLabel("—")
            box.setSpacing(2)
            box.addWidget(cap)
            box.addWidget(val)
            holder = QWidget()
            holder.setLayout(box)
            grid.addWidget(holder, stretch=1)
            self._meta_labels.append((cap, val))
        self._meta_widget = QWidget()
        self._meta_widget.setLayout(grid)
        col.addWidget(self._meta_widget)

        # 第三行：阶段时间线（收集 → 筛选 → 解析）
        self._node_walk = _PhaseNode(dark)
        self._node_filter = _PhaseNode(dark, accent_key="warning")
        self._node_scan = _PhaseNode(dark, accent_key="warning")
        col.addWidget(self._node_walk)
        col.addWidget(self._node_filter)
        col.addWidget(self._node_scan)

        # 展开明细：最近解析文件列表
        self._detail_toggle = QPushButton("展开解析明细")
        self._detail_toggle.setProperty("variant", "ghost")
        self._detail_list = QListWidget()
        self._detail_list.setVisible(False)
        self._detail_list.setAlternatingRowColors(True)
        self._detail_list.setMaximumHeight(180)
        col.addWidget(self._detail_toggle)
        col.addWidget(self._detail_list)

        # 第四行：分类计数 + 控制按钮
        foot = QHBoxLayout()
        foot.setSpacing(12)
        self._count_labels: dict[str, QLabel] = {}
        for key in ("passed", "matched", "error", "reused", "changed"):
            lbl = QLabel()
            foot.addWidget(lbl)
            self._count_labels[key] = lbl
        foot.addStretch()
        self._pause_btn = QPushButton("暂停")
        self._pause_btn.setProperty("variant", "secondary")
        self._cancel_btn = QPushButton(" 取消")
        self._cancel_btn.setProperty("variant", "danger")
        foot.addWidget(self._pause_btn)
        foot.addWidget(self._cancel_btn)
        col.addLayout(foot)

        self._pause_btn.clicked.connect(self.pause_clicked.emit)
        self._cancel_btn.clicked.connect(self.cancel_clicked.emit)
        self._detail_toggle.clicked.connect(lambda: self._detail_list.setVisible(not self._detail_list.isVisible()))
        self.set_dark(dark)

    # ----------------------------- 公共 API -----------------------------

    def set_dark(self, dark: bool) -> None:
        """主题切换：整卡重刷样式。"""
        self._dark = dark
        t = _tokens(dark)
        self.setStyleSheet(
            f"#scanProgressCard {{ background-color: {t['bg_card']};"
            f" border: 1px solid {t['border']}; border-radius: 8px; }}"
        )
        self.setObjectName("scanProgressCard")
        self._name_label.setStyleSheet(
            f"font-size: 17px; font-weight: bold; color: {t['text_primary']}; background: transparent;"
        )
        for cap, val in self._meta_labels:
            cap.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']}; background: transparent;")
            val.setStyleSheet(f"font-size: 12px; color: {t['text_primary']}; background: transparent;")
        counts = (
            ("passed", "安全 ", t["success"]),
            ("matched", "命中 ", t["danger"]),
            ("error", "错误 ", t["danger"]),
            ("reused", "复用 ", t["primary"]),
            ("changed", "重扫 ", t["text_secondary"]),
        )
        for key, prefix, color in counts:
            lbl = self._count_labels[key]
            lbl.setProperty("prefix", prefix)
            lbl.setProperty("color", color)
            lbl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {color}; background: transparent;")
        for node in (self._node_walk, self._node_filter, self._node_scan):
            node.set_dark(dark)
        self._detail_toggle.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']}; background: transparent;")

    def refresh(self, sc: object, ws_name: str, mode_text: str, target: str) -> None:
        """从活动 ScanController 全量重读进度信息。"""
        t = _tokens(self._dark)
        paused = bool(sc.isPaused)
        status = "已暂停" if paused else "扫描中"
        self._name_label.setText(ws_name)
        self._badge.apply(status, t["text_secondary"] if paused else t["warning"])

        cur_file = str(sc.currentFile or "") or "—"
        meta_values = (
            mode_text,
            target or "—",
            f"最多 {sc.effectiveMaxWorkers} 线程 / 最大 {sc.effectiveMaxFileSizeMB} MB / 深度 {sc.effectiveMaxDepth}",
            cur_file,
        )
        for (_cap, val), text in zip(self._meta_labels, meta_values, strict=False):
            val.setText(text)
        # 当前文件单文件徽标（大文件 [大小·扩展名·耗时]，小文件 [扩展名·大小]）
        size = int(sc.currentFileSize or 0)
        ext = str(sc.currentFileExt or "")
        elapsed_ms = float(sc.currentFileElapsedMs or 0.0)
        phase_scan = str(sc.scanPhase or "") == "scan"
        meta_tip = ""
        if phase_scan and size > 0:
            if size > 1048576:
                meta_tip = f"[{size / 1048576:.1f} MB · {ext or '?'} · {elapsed_ms / 1000.0:.1f}s]"
            else:
                meta_tip = f"[{ext or '?'} · {size / 1024:.1f} KB]"
        self._meta_labels[3][1].setText((cur_file + " " + meta_tip).strip())

        self._refresh_nodes(sc)
        self._refresh_counts(sc)
        self._pause_btn.setText("继续" if paused else "暂停")

    def set_recent_files(self, rows: list[dict[str, object]]) -> None:
        """刷新「最近解析」展开列表（最新在前）。"""
        self._detail_toggle.setVisible(bool(rows))
        self._detail_list.clear()
        for r in rows:
            engine = r.get("engine") or ""
            meta = f"{r.get('sizeText', '')} · {r.get('elapsedText', '')}"
            if engine:
                meta += f" · {engine}"
            mark = "… " if r.get("status") == "scanning" else "✓ "
            self._detail_list.addItem(f"{mark}{r.get('name', '')}   {meta}")

    # ----------------------------- 内部 -----------------------------

    def _refresh_nodes(self, sc: object) -> None:
        """按 ScanController 阶段字段求值三个节点状态。"""
        self._refresh_walk_node(sc)
        self._refresh_filter_node(sc)
        self._refresh_scan_node(sc)

    def _refresh_walk_node(self, sc: object) -> None:
        """节点 1：收集文件（walk）。"""
        if bool(sc.walkDone):
            walk_state = "done"
        elif str(sc.scanPhase or "") == "walk":
            walk_state = "running"
        else:
            walk_state = "pending"
        walk_detail = ""
        if walk_state != "pending":
            if bool(sc.walkIndeterminate):
                walk_detail = "统计中..."
            else:
                walk_detail = f"{sc.walkClassified} / {sc.walkDiscovered}"
                skipped = int(sc.walkSkipped) + int(sc.walkUserSkipped)
                if skipped > 0:
                    walk_detail += f" · 跳过 {skipped}"
            el = str(sc.walkElapsedText or "")
            if el:
                walk_detail += f" · 用时 {el}"
        self._node_walk.set_state(
            walk_state,
            walk_detail,
            -1.0 if bool(sc.walkIndeterminate) else float(sc.walkProgress or 0.0),
            bool(sc.walkIndeterminate),
        )
        self._node_walk.set_lines(False, True)

    def _refresh_filter_node(self, sc: object) -> None:
        """节点 2：筛选文件（filter，剔除空/超限/不可读/符号链接）。"""
        counts = (
            int(sc.filterRemovedEmpty),
            int(sc.filterRemovedOversize),
            int(sc.filterRemovedUnreadable),
            int(sc.filterRemovedSymlink),
        )
        removed_total = sum(counts)
        phase = str(sc.scanPhase or "")
        if bool(sc.filterActive):
            filter_state = "running"
        elif bool(sc.walkDone) and phase in ("scan", "archive", "done"):
            filter_state = "done"
        else:
            filter_state = "pending"
        filter_detail = "" if filter_state == "pending" else f"剔除 {removed_total}"
        if filter_state != "pending" and removed_total > 0:
            filter_detail = (
                f"剔除 {removed_total}（空 {counts[0]} · 超限 {counts[1]} · 不可读 {counts[2]} · 链接 {counts[3]}）"
            )
        self._node_filter.set_state(filter_state, filter_detail, -1.0, True)
        self._node_filter.set_lines(True, True)

    def _refresh_scan_node(self, sc: object) -> None:
        """节点 3：解析文件（scan/archive）。"""
        phase = str(sc.scanPhase or "")
        if bool(sc.scanDone):
            scan_state = "done"
        elif phase in ("scan", "archive"):
            scan_state = "running"
        else:
            scan_state = "pending"
        scan_detail = ""
        if scan_state != "pending":
            indet = bool(sc.progressIndeterminate) and not bool(sc.filterActive)
            if indet and not bool(sc.scanDone):
                scan_detail = "等待中..."
            else:
                scan_detail = f"{sc.progressScanned} / {sc.progressTotal}"
                if int(sc.archiveEntryCount) > 0:
                    scan_detail += f"（含压缩包 {sc.archiveEntryCount}）"
                speed = float(sc.scanSpeed or 0.0)
                if speed > 0:
                    scan_detail += f" · 平均 {speed:.0f} 文件/s"
                el = str(sc.scanElapsedText or "")
                if el:
                    scan_detail += f" · 用时 {el}"
        self._node_scan.set_state(
            scan_state,
            scan_detail,
            -1.0 if bool(sc.progressIndeterminate) else float(sc.progress or 0.0),
            bool(sc.progressIndeterminate) and not bool(sc.filterActive),
        )
        self._node_scan.set_lines(True, False)

    def _refresh_counts(self, sc: object) -> None:
        """刷新底部分类计数标签。"""
        values = {
            "passed": int(sc.passedCount),
            "matched": int(sc.matchedCount),
            "error": int(sc.errorCount),
            "reused": int(sc.reusedFiles),
            "changed": int(sc.changedFiles),
        }
        for key, value in values.items():
            lbl = self._count_labels[key]
            prefix = lbl.property("prefix") or ""
            lbl.setText(f"{prefix}{value}")
            visible = True
            if key == "reused":
                visible = value > 0
            if key == "changed":
                visible = values["reused"] > 0
            lbl.setVisible(visible)


class _WorkspaceCard(QFrame):
    """工作区卡片：单任务展示与操作。

    对话框不内嵌——所有打开动作回调到 :class:`HomePage` 统一构建模态对话框。
    """

    def __init__(self, page: HomePage, item: object, dark: bool) -> None:
        super().__init__()
        self._page = page
        self._item = item
        self._dark = dark
        col = QVBoxLayout(self)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(10)

        # 第一行：任务名 + 状态徽标
        head = QHBoxLayout()
        head.setSpacing(10)
        self._name_label = QLabel()
        head.addWidget(self._name_label, stretch=1)
        self._badge = _Badge()
        head.addWidget(self._badge)
        col.addLayout(head)

        # 第二行：元数据网格（模式/目标/规则标签）
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self._mode_cap = QLabel("模式")
        self._mode_val = QLabel()
        self._target_cap = QLabel("目标")
        self._target_val = QLabel()
        self._rules_cap = QLabel("规则")
        self._tags_holder = QWidget()
        self._tags_row = QHBoxLayout(self._tags_holder)
        self._tags_row.setContentsMargins(0, 0, 0, 0)
        self._tags_row.setSpacing(4)
        self._tags_row.addStretch()
        grid.addWidget(self._mode_cap, 0, 0)
        grid.addWidget(self._mode_val, 0, 1)
        grid.addWidget(self._target_cap, 1, 0)
        grid.addWidget(self._target_val, 1, 1)
        grid.addWidget(self._rules_cap, 2, 0)
        grid.addWidget(self._tags_holder, 2, 1)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)

        # 第三行：最近摘要
        self._summary_label = QLabel()
        col.addWidget(self._summary_label)

        # 第四行：分类计数
        count_row = QHBoxLayout()
        count_row.setSpacing(12)
        self._collected_label = QLabel()
        self._passed_label = QLabel()
        self._matched_label = QLabel()
        self._error_label = QLabel()
        for lbl in (
            self._collected_label,
            self._passed_label,
            self._matched_label,
            self._error_label,
        ):
            count_row.addWidget(lbl)
        count_row.addStretch()
        col.addLayout(count_row)

        # 第五行：主操作按钮
        ops = QHBoxLayout()
        ops.setSpacing(8)
        self._start_btn = QPushButton("启动扫描")
        self._start_btn.setProperty("variant", "primary")
        self._start_btn.clicked.connect(self._on_start_pause)
        self._rescan_btn = QPushButton("重新扫描")
        self._rescan_menu = QMenu(self)
        inc_act = self._rescan_menu.addAction("增量扫描（仅变更文件）")
        full_act = self._rescan_menu.addAction("全量重新扫描")
        self._rescan_btn.setMenu(self._rescan_menu)
        inc_act.triggered.connect(lambda: self._page.ws_start_incremental(str(self._ws_id())))
        full_act.triggered.connect(lambda: self._page.ws_start_scan(str(self._ws_id())))
        rules_btn = QPushButton("配置规则")
        rules_btn.setProperty("variant", "secondary")
        rules_btn.clicked.connect(lambda: self._page.open_rules_dialog(str(self._ws_id())))
        preview_btn = QPushButton("预览规则")
        preview_btn.setProperty("variant", "ghost")
        preview_btn.clicked.connect(lambda: self._page.open_preview_dialog(str(self._ws_id())))
        view_btn = QPushButton("查看结果")
        view_btn.setProperty("variant", "primary")
        view_btn.clicked.connect(self._on_view_results)
        stats_btn = QPushButton("统计")
        stats_btn.setProperty("variant", "ghost")
        stats_btn.clicked.connect(self._on_view_stats)
        self._expand_btn = QPushButton("展开")
        self._expand_btn.setProperty("variant", "ghost")
        self._expand_btn.clicked.connect(self._toggle_expand)
        for b in (self._start_btn, self._rescan_btn, rules_btn, preview_btn, view_btn, stats_btn, self._expand_btn):
            ops.addWidget(b)
        col.addLayout(ops)
        self._view_btn = view_btn
        self._stats_btn = stats_btn

        # 展开区：更多操作（非常用功能）
        self._expand_area = QWidget()
        ex = QHBoxLayout(self._expand_area)
        ex.setContentsMargins(0, 0, 0, 0)
        ex.setSpacing(8)
        target_btn = QPushButton("切换目标")
        target_btn.setProperty("variant", "ghost")
        target_btn.clicked.connect(lambda: self._page.open_target_dialog(str(self._ws_id())))
        export_btn = QPushButton("导出")
        export_btn.setProperty("variant", "ghost")
        export_menu = QMenu(self)
        for fmt in ("csv", "json", "pdf"):
            act = export_menu.addAction(f"{fmt.upper()} (*.{fmt})")
            act.triggered.connect(lambda _checked=False, f=fmt: self._page.export_workspace(str(self._ws_id()), f))
        export_btn.setMenu(export_menu)
        history_btn = QPushButton("历史")
        history_btn.setProperty("variant", "ghost")
        history_btn.clicked.connect(lambda: self._page.open_history_dialog(str(self._ws_id())))
        delete_btn = QPushButton("删除")
        delete_btn.setProperty("variant", "danger")
        delete_btn.clicked.connect(lambda: self._page.ws_remove(str(self._ws_id())))
        for b in (target_btn, export_btn, history_btn):
            ex.addWidget(b)
        ex.addStretch()
        ex.addWidget(delete_btn)
        self._expand_area.setVisible(False)
        sep = QFrame()
        sep.setFixedHeight(1)
        self._sep = sep
        col.addWidget(sep)
        col.addWidget(self._expand_area)

        self.refresh(item)
        self.set_dark(dark)

    # ----------------------------- 公共 API -----------------------------

    @property
    def workspace_id(self) -> str:
        """当前展示的工作区 ID。"""
        return str(self._item.workspace_id)  # type: ignore[attr-defined]

    def refresh(self, item: object) -> None:
        """从 :class:`WorkspaceItem` 重读全部展示字段。"""
        self._item = item
        t = _tokens(self._dark)
        matched = int(item.matched_count)
        status = str(item.status_text)
        completed = _is_completed(status)
        active = status in ("扫描中", "已暂停")

        self._name_label.setText(str(item.name))
        self._badge.apply(status, _status_color(status, matched, t))
        self._mode_val.setText(str(item.mode_text))
        self._target_val.setText(str(item.target) or "—")
        self._summary_label.setText(f"最近：{item.last_summary}" if item.last_summary else "尚未扫描")
        collected = int(item.collected_count)
        self._collected_label.setVisible(collected > 0)
        counts = (
            (self._passed_label, "安全 ", int(item.passed_count), t["success"], True),
            (self._matched_label, "命中 ", matched, t["danger"], True),
            (self._error_label, "错误 ", int(item.error_count), t["danger"], True),
        )
        for lbl, prefix, value, color, vis in counts:
            lbl.setText(f"<b style='color:{color}'>{prefix}{value}</b>")
            lbl.setTextFormat(Qt.RichText)
            lbl.setVisible(vis)
        self._collected_label.setText(f"<b style='color:{t['primary']}'>纳入扫描 {collected}</b>")
        self._collected_label.setTextFormat(Qt.RichText)

        # 主操作行按钮语义随状态切换
        if status == "扫描中":
            self._start_btn.setText("暂停")
        elif status == "已暂停":
            self._start_btn.setText("继续")
        else:
            self._start_btn.setText("启动扫描")
        self._start_btn.setEnabled(not completed)
        self._rescan_btn.setVisible(completed)
        self._view_btn.setEnabled(completed)
        self._expand_btn.setEnabled(not active)

    def set_dark(self, dark: bool) -> None:
        """主题切换：整卡重刷样式并重建规则标签芯片。"""
        self._dark = dark
        t = _tokens(dark)
        self.setObjectName("wsCard")
        self.setStyleSheet(
            f"#wsCard {{ background-color: {t['bg_card']}; border: 1px solid {t['border']}; border-radius: 8px; }}"
        )
        self._name_label.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {t['text_primary']}; background: transparent;"
        )
        for cap in (self._mode_cap, self._target_cap, self._rules_cap):
            cap.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']}; background: transparent;")
        for val in (self._mode_val, self._target_val):
            val.setStyleSheet(f"font-size: 12px; color: {t['text_primary']}; background: transparent;")
        self._summary_label.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']}; background: transparent;")
        self._sep.setStyleSheet(f"background-color: {t['border']}; border: none;")
        self.refresh(self._item)

    # ----------------------------- 内部 -----------------------------

    def _ws_id(self) -> str:
        """返回工作区 ID（供菜单 lambda 引用）。"""
        return str(self._item.workspace_id)  # type: ignore[attr-defined]

    def _on_start_pause(self) -> None:
        """启动/暂停/继续主操作。"""
        page = self._page
        status = str(self._item.status_text)  # type: ignore[attr-defined]
        if status in ("扫描中", "已暂停"):
            page.ws_toggle_pause(self.workspace_id)
        else:
            page.ws_start_scan(self.workspace_id)

    def _on_view_results(self) -> None:
        """切当前工作区并跳结果页。"""
        self._page.view_results_requested(self.workspace_id)

    def _on_view_stats(self) -> None:
        """切当前工作区并跳统计页。"""
        self._page.view_stats_requested(self.workspace_id)

    def _toggle_expand(self) -> None:
        """展开/收起更多操作区。"""
        showing = not self._expand_area.isVisibleTo(self)
        self._expand_area.setVisible(showing)
        self._sep.setVisible(showing)
        self._expand_btn.setText("收起" if showing else "展开")


class _ClickHint(QFrame):
    """可点击的拖拽提示条。"""

    clicked = Signal()

    def __init__(self, dark: bool) -> None:
        super().__init__()
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        inner = QHBoxLayout(self)
        inner.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("拖拽文件夹到此处添加任务，或点击右上角「添加文件夹」")
        self._label.setAlignment(Qt.AlignCenter)
        inner.addWidget(self._label)
        self.set_dark(dark)

    def set_dragging(self, dragging: bool) -> None:
        """拖拽悬浮高亮。"""
        t = _tokens(self._dark)
        if dragging:
            bg = "#0366D622"
            color = t["primary"]
            border = t["primary"]
        else:
            bg = t["bg_app"]
            color = t["text_secondary"]
            border = t["border"]
        self.setStyleSheet(f"background-color: {bg}; border: 1px solid {border}; border-radius: 4px;")
        self._label.setStyleSheet(f"font-size: 12px; color: {color}; background: transparent; border: none;")
        self._label.setText(
            "松开以添加扫描任务" if dragging else "拖拽文件夹到此处添加任务，或点击右上角「添加文件夹」"
        )

    def set_dark(self, dark: bool) -> None:
        """主题切换。"""
        self._dark = dark
        self.set_dragging(False)

    def mousePressEvent(self, event: object) -> None:
        """点击提示条即打开文件夹选择。"""
        del event
        self.clicked.emit()


class HomePage(QWidget):
    """文件扫描页：工作区列表 / 扫描进度双视图 + 整页拖拽接收。

    :param controller: :class:`AppController` 主控制器
    """

    # 由 MainWindow 订阅：跳转到 结果 / 统计 页
    viewResultsRequested = Signal(str)
    viewStatsRequested = Signal(str)

    def __init__(self, controller: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._wc = controller.workspace
        self._config = controller.config
        self._rules = controller.rules
        self._model = self._wc.workspaceModel
        self._cards: dict[str, _WorkspaceCard] = {}
        self._connected_sc: object | None = None
        self._dark = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)

        # ---------- 标题区 ----------
        header = QHBoxLayout()
        header.setSpacing(12)
        self._title_label = QLabel("工作区")
        self._title_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        header.addWidget(self._title_label)
        header.addStretch()
        self._add_btn = QPushButton(" 添加文件夹")
        self._add_btn.setProperty("variant", "secondary")
        self._add_btn.clicked.connect(self.add_folders)
        self._clear_btn = QPushButton(" 清空")
        self._clear_btn.setProperty("variant", "ghost")
        self._clear_btn.clicked.connect(self.clear_all)
        header.addWidget(self._add_btn)
        header.addWidget(self._clear_btn)
        self._count_label = QLabel()
        header.addWidget(self._count_label)
        root.addLayout(header)

        # ---------- 双视图 ----------
        self._views = QStackedWidget()
        root.addWidget(self._views, stretch=1)

        # 视图 A：工作区列表
        list_page = QWidget()
        lp = QVBoxLayout(list_page)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(10)
        self._drop_hint = _ClickHint(self._dark)
        self._drop_hint.clicked.connect(self.add_folders)
        lp.addWidget(self._drop_hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self._list_layout = QVBoxLayout(body)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(12)
        self._list_layout.addStretch()
        scroll.setWidget(body)
        lp.addWidget(scroll, stretch=1)
        self._empty_label = QLabel("拖拽文件夹到此处创建扫描任务")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._scroll = scroll
        lp.addWidget(self._empty_label, stretch=1)
        self._views.addWidget(list_page)

        # 视图 B：扫描进度面板
        progress_page = QWidget()
        pp = QVBoxLayout(progress_page)
        pp.setContentsMargins(0, 24, 0, 0)
        self._progress_card = _ScanProgressCard(self._dark)
        self._progress_card.pause_clicked.connect(self._on_progress_pause)
        self._progress_card.cancel_clicked.connect(self._on_progress_cancel)
        pp.addWidget(self._progress_card)
        hint = QLabel("扫描结束后自动恢复工作区列表")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("font-size: 11px;")
        self._restore_hint = hint
        pp.addWidget(hint)
        pp.addStretch()
        self._views.addWidget(progress_page)

        # 盘符扫描入口（列表视图下方）
        drive_box = QWidget()
        dbox = QVBoxLayout(drive_box)
        dbox.setContentsMargins(0, 0, 0, 0)
        dbox.setSpacing(6)
        drive_title = QLabel("盘符扫描")
        drive_title.setStyleSheet("font-size: 12px; font-weight: bold;")
        dbox.addWidget(drive_title)
        drive_row = QHBoxLayout()
        drive_row.setSpacing(8)
        self._drive_buttons: list[tuple[QPushButton, str]] = []
        drives = list(getattr(self._config, "drives", []) or [])
        for letter in drives:
            btn = QPushButton(f" {letter}")
            btn.setProperty("variant", "secondary")
            btn.clicked.connect(lambda _checked=False, ld=letter: self._add_drive_workspace(ld))
            drive_row.addWidget(btn)
            self._drive_buttons.append((btn, letter))
        if not drives:
            warn = QLabel("未检测到可用盘符")
            drive_row.addWidget(warn)
            self._no_drive_label = warn
        else:
            self._no_drive_label = None
        drive_row.addStretch()
        dbox.addLayout(drive_row)
        self._drive_entry = drive_box
        root.addWidget(drive_box)

        # ---------- 拖拽遮罩与 Toast ----------
        self._overlay = QFrame(self)
        self._overlay.hide()
        self._toast = QLabel(self)
        self._toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)
        self.setAcceptDrops(True)

        # ---------- 数据接线 ----------
        self._model.rowsInserted.connect(self._sync_cards)
        self._model.rowsRemoved.connect(self._sync_cards)
        self._model.modelReset.connect(self._sync_cards)
        self._model.dataChanged.connect(self._on_data_changed)
        self._wc.activeScanChanged.connect(self._sync_views)
        self._sync_cards()
        self._sync_views()

    # ----------------------------- 主题 -----------------------------

    def set_dark(self, dark: bool) -> None:
        """主题切换：整页样式刷新。"""
        if self._dark == dark:
            return
        self._dark = dark
        t = _tokens(dark)
        self._title_label.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {t['text_primary']};")
        self._count_label.setStyleSheet(f"font-size: 12px; color: {t['text_secondary']};")
        self._empty_label.setStyleSheet(f"font-size: 13px; color: {t['text_secondary']};")
        self._restore_hint.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']};")
        self._drop_hint.set_dark(dark)
        self._progress_card.set_dark(dark)
        for card in self._cards.values():
            card.set_dark(dark)
        if self._no_drive_label is not None:
            self._no_drive_label.setStyleSheet(f"font-size: 12px; color: {t['warning']};")
        self._refresh_overlay_style()
        self._style_toast(ok=self._ok_toast)
        self._sync_views()

    # ----------------------------- 工作区操作回调 -----------------------------

    def ws_start_scan(self, ws_id: str) -> None:
        """启动指定工作区扫描。"""
        self._wc.startScan(ws_id)

    def ws_start_incremental(self, ws_id: str) -> None:
        """增量扫描指定工作区。"""
        self._wc.startIncrementalScan(ws_id)

    def ws_toggle_pause(self, ws_id: str) -> None:
        """暂停/继续指定工作区。"""
        self._wc.togglePause(ws_id)

    def ws_remove(self, ws_id: str) -> None:
        """移除指定工作区。"""
        self._wc.removeWorkspace(ws_id)

    def view_results_requested(self, ws_id: str) -> None:
        """切换当前工作区并发跳转信号。"""
        self._wc.setCurrentWorkspaceId(ws_id)
        self.viewResultsRequested.emit(ws_id)

    def view_stats_requested(self, ws_id: str) -> None:
        """切换当前工作区并发统计信号。"""
        self._wc.setCurrentWorkspaceId(ws_id)
        self.viewStatsRequested.emit(ws_id)

    # ----------------------------- 对话框 -----------------------------

    def add_folders(self) -> None:
        """多选文件夹批量创建扫描任务。"""
        dialog = QFileDialog(self, "选择扫描文件夹")
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        if dialog.exec_():
            paths = [p for p in dialog.selectedFiles() if p]
            count = self._wc.addWorkspacesFromPaths(paths)
            if count == 0:
                self.show_toast("拖拽的目标不是文件或文件夹", ok=False)
            else:
                self.show_toast(f"已添加 {count} 个扫描任务", ok=True)

    def clear_all(self) -> None:
        """确认后清空全部工作区。"""
        answer = QMessageBox.question(
            self,
            "清空工作区",
            "确定清空所有工作区？此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._wc.clearAllWorkspaces()

    def open_target_dialog(self, ws_id: str) -> None:
        """切换扫描目标对话框。"""
        item = self._model.get_workspace(ws_id)
        if item is None:
            return
        EditTargetDialog(
            self._config,
            self._wc,
            ws_id,
            str(item.mode_text),
            str(item.target),
            self._dark,
        ).exec_()

    def open_rules_dialog(self, ws_id: str) -> None:
        """配置规则对话框（先切 RulesController 当前工作区上下文）。"""
        self._wc.setCurrentWorkspaceId(ws_id)
        RulesDialog(self._rules, self._wc.workspaceName(ws_id), self._dark).exec_()

    def open_preview_dialog(self, ws_id: str) -> None:
        """预览规则对话框（只读 effective 规则集）。"""
        try:
            data = json.loads(self._rules.previewRuleset(ws_id))
        except (ValueError, TypeError):
            data = {}
        PreviewRulesDialog(data, self._wc.workspaceName(ws_id), self._dark).exec_()

    def open_history_dialog(self, ws_id: str) -> None:
        """扫描历史对话框（趋势图 + 对比摘要 + 历史列表）。"""
        HistoryDialog(
            self._wc,
            ws_id,
            self._wc.workspaceName(ws_id),
            self._dark,
            parent=None,
        ).exec_()

    def export_workspace(self, ws_id: str, fmt: str) -> None:
        """导出扫描结果到用户选择的路径。"""
        name = self._wc.workspaceName(ws_id) or "fuscan"
        filters = {
            "csv": "CSV (*.csv)",
            "json": "JSON (*.json)",
            "pdf": "PDF (*.pdf)",
        }
        path, _ = QFileDialog.getSaveFileName(self, f"导出 {fmt.upper()}", f"{name}.{fmt}", filters[fmt])
        if path:
            self._wc.exportResults(ws_id, fmt, path)

    def _add_drive_workspace(self, letter: str) -> None:
        """一键创建盘符扫描任务。"""
        self._wc.addWorkspace("", "drive", letter, "[]", True)

    def _on_progress_pause(self) -> None:
        """进度面板暂停/继续。"""
        ws_id = self._wc.activeScanWorkspaceId
        if ws_id:
            self._wc.togglePause(ws_id)

    def _on_progress_cancel(self) -> None:
        """进度面板取消扫描。"""
        ws_id = self._wc.activeScanWorkspaceId
        if ws_id:
            self._wc.cancelScan(ws_id)

    # ----------------------------- Toast / 拖拽 -----------------------------

    _ok_toast: bool = True

    def show_toast(self, message: str, ok: bool = True) -> None:
        """顶部居中 Toast 提示，3 秒后自动消失。"""
        self._ok_toast = ok
        self._toast.setText(message)
        self._style_toast(ok)
        parent_w = max(self.width(), 100)
        width = min(self._toast.sizeHint().width() + 32, parent_w - 32)
        self._toast.setGeometry((parent_w - width) // 2, 16, width, 34)
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.raise_()
        self._toast.show()
        self._toast_timer.start(3000)

    def _style_toast(self, ok: bool) -> None:
        """Toast 语义底色。"""
        t = _tokens(self._dark)
        color = t["success"] if ok else t["danger"]
        self._toast.setStyleSheet(
            f"background-color: {color}; color: #FFFFFF; font-size: 12px; border-radius: 6px; border: none;"
        )

    def _refresh_overlay_style(self) -> None:
        """拖拽悬浮遮罩高亮样式。"""
        t = _tokens(self._dark)
        primary = t["primary"]
        self._overlay.setStyleSheet(
            f"background-color: {primary}22; border: 2px solid {primary}; border-radius: 8px; border-top-width: 2px;"
        )
        lay = QVBoxLayout(self._overlay)
        lay.setContentsMargins(0, 0, 0, 0)
        if not lay.count():
            label = QLabel("松开以添加扫描任务")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {primary}; background: transparent;")
            lay.addWidget(label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """接受含 URL 的拖入并显示遮罩。"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._refresh_overlay_style()
            self._overlay.setGeometry(self.rect())
            self._overlay.raise_()
            self._overlay.show()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        """离开页面隐藏遮罩。"""
        del event
        self._overlay.hide()

    def dropEvent(self, event: QDropEvent) -> None:
        """落点提取本地路径批量建任务。"""
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        paths = [p for p in paths if p]
        event.acceptProposedAction()
        self._overlay.hide()
        if not paths:
            self.show_toast("拖拽的目标不是文件或文件夹", ok=False)
            return
        count = self._wc.addWorkspacesFromPaths(paths)
        if count == 0:
            self.show_toast("拖拽的目标不是文件或文件夹", ok=False)
        else:
            self.show_toast(f"已添加 {count} 个扫描任务", ok=True)

    # ----------------------------- 内部同步 -----------------------------

    def _sync_cards(self) -> None:
        """结构性变更后重建卡片集合并按模型顺序排列。

        完整对比模型与既有卡片字典：新建缺失卡片（插入模型对应位置）、
        移除失效卡片；随后把布局顺序对齐模型行序。
        """
        items = list(self._model.items)
        model_ids = [it.workspace_id for it in items]

        # 移除失效卡片
        for stale_id in [wid for wid in self._cards if wid not in model_ids]:
            card = self._cards.pop(stale_id)
            self._list_layout.removeWidget(card)
            card.deleteLater()

        # 新增缺失卡片
        for item in items:
            if item.workspace_id not in self._cards:
                card = _WorkspaceCard(self, item, self._dark)
                self._cards[item.workspace_id] = card

        # 拉走既有 widget 后按模型顺序重新放入，再补回 stretch
        while self._list_layout.count():
            entry = self._list_layout.takeAt(0)
            w = entry.widget()
            del w  # widget 由 self._cards 持有，无需 deleteLater
        for wid in model_ids:
            self._list_layout.addWidget(self._cards[wid])
        self._list_layout.addStretch()

        has_ws = len(model_ids) > 0
        self._scroll.setVisible(has_ws)
        self._empty_label.setVisible(not has_ws)
        self._update_header_text()

    def _on_data_changed(self, top_left: object, bottom_right: object, _roles: list[int]) -> None:
        """定向刷新数据变化的行对应卡片。"""
        for row in range(top_left.row(), bottom_right.row() + 1):
            item = self._model.get_by_index(row)
            if item is None:
                continue
            card = self._cards.get(item.workspace_id)
            if card is not None:
                card.refresh(item)
                card.updateGeometry()

    def _update_header_text(self) -> None:
        """标题/计数文案随视图状态切换。"""
        scanning = bool(self._wc.hasActiveScan)
        self._title_label.setText("扫描中" if scanning else "工作区")
        if scanning:
            self._count_label.setText("扫描进行中...")
        else:
            self._count_label.setText(f"共 {len(list(self._model.items))} 个任务")

    # 扫描控制器汇聚刷新的信号名（与 StatsPage 一致）
    _SCAN_SIGNALS: tuple[str, ...] = (
        "progressChanged",
        "scanStateChanged",
        "statusChanged",
        "phaseChanged",
        "walkProgressChanged",
        "scanProgressChanged",
        "recentParsedFilesChanged",
    )

    def _sync_views(self) -> None:
        """活动扫描变化：切换视图并重绑进度信号。"""
        scanning = bool(self._wc.hasActiveScan)
        self._views.setCurrentIndex(1 if scanning else 0)
        self._add_btn.setVisible(not scanning)
        self._clear_btn.setVisible(not scanning)
        self._drop_hint.setVisible(not scanning)
        self._drive_entry.setVisible(not scanning)
        for btn, _letter in self._drive_buttons:
            btn.setEnabled(not scanning)
        self._update_header_text()

        old = self._connected_sc
        if old is not None:
            for name in self._SCAN_SIGNALS:
                with contextlib.suppress(RuntimeError, TypeError):
                    getattr(old, name).disconnect(self._refresh_progress)
        new = self._wc.activeScanController if scanning else None
        self._connected_sc = new
        if new is not None:
            for name in self._SCAN_SIGNALS:
                getattr(new, name).connect(self._refresh_progress)
        if scanning:
            self._refresh_progress()

    def _refresh_progress(self) -> None:
        """活动扫描全量重读：卡片刷新 + 最近解析明细。"""
        sc = self._connected_sc
        if sc is None or not self._wc.hasActiveScan:
            return
        try:
            self._progress_card.refresh(
                sc,
                str(self._wc.activeScanWorkspaceName),
                str(self._wc.activeScanModeText),
                str(self._wc.activeScanTarget),
            )
            self._progress_card.set_recent_files(list(sc.recentParsedFiles))
        except RuntimeError:
            # 控制器对象销毁瞬间的竞态：下一轮信号会再触发
            pass

    def closeEvent(self, event: QCloseEvent) -> None:
        """停止转圈定时器等资源。"""
        self._toast_timer.stop()
        super().closeEvent(event)
