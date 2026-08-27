"""统计页（Widgets 版）：状态摘要 + 双阶段进度 + 分类计数 + 图表区。

对照 QML 版 :file:`StatsPage.qml` 等价迁移：

- 状态摘要：状态文字按语义着色（扫描中=警告色，已完成=有命中危险色）
- 收集（walk）/解析（scan）双进度条与统计网格
- 安全/命中/错误三卡片
- 命中分布：严重度/扩展名环形图 + Top 规则条形图（Widgets 自绘，对应原 QML 组件）
- 性能剖析条形图（仅当前会话且有 perf_summary 时显示）

刷新模型：ScanController 的状态/进度/结果信号统一汇聚到 :meth:`refresh_all`
全量重读控制器属性（页面为纯只读视图，无增量优化必要）。
"""

# pyrefly: ignore-errors
# PySide2 存根重载合并缺陷导致 QWidget/QLayout/QPainter 调用与 Signal.connect 误报，
# 详见 sidebar.py 头部说明；本文件为纯 GUI 布局代码，采用文件级压制。

from __future__ import annotations

import contextlib

from PySide2.QtCore import QRectF, Qt, Signal
from PySide2.QtGui import QColor, QFontMetrics, QPainter, QPaintEvent
from PySide2.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fuscan.gui.controllers import AppController
from fuscan.gui.widgets.about_page import CardGroupBox
from fuscan.gui.widgets.qss import palette_tokens

__all__ = ["BarChart", "PieChart", "StatsPage"]

# 安全/命中/错误三卡片的大数字与边框语义色（深浅主题共用）
_CARD_TINT = {"success": "#279E69", "danger": "#E84D3D"}


def _fmt_int(value: object) -> str:
    """整型安全转字符串（后端属性始终为 int）。"""
    return str(value)


class PieChart(QWidget):
    """自绘环形饼图：右侧图例逐项列出 标签·数量·占比。

    对应 QML 版 :file:`components/PieChart.qml`；``chartData`` 为
    ``[{label, value, color}, ...]``。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: list[dict[str, object]] = []
        self._center_title = ""
        self._dark = False
        self.setMinimumHeight(220)
        self._legend_labels: list[QLabel] = []

    # ----------------------------- 公共 API -----------------------------

    @property
    def chart_data(self) -> list[dict[str, object]]:
        """当前图表数据。"""
        return self._data

    def set_data(self, data: list[dict[str, object]], center_title: str) -> None:
        """更新数据与中心标题并重绘。"""
        total = sum(int(d["value"]) for d in data)
        if data and total <= 0:
            data = []
        if data == self._data and center_title == self._center_title:
            return
        self._data = data
        self._center_title = center_title
        self._rebuild_legend()
        self.update()

    def set_dark(self, dark: bool) -> None:
        """主题切换：刷新图例文字颜色。"""
        if self._dark == dark:
            return
        self._dark = dark
        self._rebuild_legend()
        self.update()

    # ----------------------------- 内部 -----------------------------

    def _rebuild_legend(self) -> None:
        """重建父布局中的图例行（父布局由页面注入 :attr:`_legend_layout`）。"""
        layout = getattr(self, "_legend_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        t = palette_tokens(self._dark)
        for d in self._data:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            swatch = QFrame()
            swatch.setFixedSize(10, 10)
            swatch.setStyleSheet(f"background-color: {d['color']}; border-radius: 5px; border: none;")
            label = QLabel(f"{d['label']}  {int(d['value'])}")
            label.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']}; background: transparent;")
            row.addWidget(swatch)
            row.addWidget(label)
            row.addStretch()
            layout.addWidget(row_widget)
            self._legend_labels.append(label)

    def paintEvent(self, event: QPaintEvent) -> None:
        """绘制环形图与中心标题（数据为空时绘制占位圆环）。"""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width() - 120, self.height())
        if side < 40:
            side = max(40.0, float(min(self.width(), self.height())))
        rect = QRectF(0.0, 0.0, side, side)
        rect.moveTopLeft(_pt(self.height() // 2 - int(side) // 2, 8))
        ring_width = 26

        base = QColor(palette_tokens(self._dark)["border"])
        pen = painter.pen()
        pen.setColor(base)
        pen.setWidth(ring_width)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)

        total = sum(int(d["value"]) for d in self._data)
        start = 90 * 16  # 从顶部起顺时针
        for d in self._data:
            span = int(360 * 16 * int(d["value"]) / total) if total > 0 else 0
            if span <= 0:
                continue
            pen.setColor(QColor(str(d["color"])))
            painter.setPen(pen)
            painter.drawArc(rect, -start - span, span)  # Qt 角度逆时针为正
            start += span

        painter.setPen(QColor(palette_tokens(self._dark)["text_primary"]))
        f = painter.font()
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(rect, Qt.AlignCenter, str(total))
        sub_rect = rect.adjusted(0, 18, 0, 18)
        f2 = painter.font()
        f2.setBold(False)
        painter.setFont(f2)
        painter.drawText(sub_rect, Qt.AlignHCenter | Qt.AlignBottom, self._center_title)
        painter.end()


def _pt(x: int, y: int) -> object:
    """构造 QPointF 兼容的最简写法（避免重复导入）。"""
    from PySide2.QtCore import QPointF

    return QPointF(float(x), float(y))


class BarChart(QWidget):
    """自绘水平条形图：标签 | 比例条 | 数值文本。

    对应 QML 版 :file:`components/BarChart.qml`；
    ``chartData`` 为 ``[{label, value, color}, ...]``，
    ``percent>0`` 或 ``suffix`` 支持数值文案定制。
    """

    def __init__(
        self,
        label_width: int = 140,
        suffix: str = "",
        decimals: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data: list[dict[str, object]] = []
        self._label_width = label_width
        self._suffix = suffix
        self._decimals = decimals
        self._dark = False
        self.setMinimumHeight(60)

    @property
    def chart_data(self) -> list[dict[str, object]]:
        """当前图表数据。"""
        return self._data

    def set_data(self, data: list[dict[str, object]]) -> None:
        """更新数据并重绘。"""
        if data == self._data:
            return
        self._data = data
        self.setMinimumHeight(max(60, len(data) * 30 + 12))
        self.update()

    def set_dark(self, dark: bool) -> None:
        """主题切换标记（配色在 paint 时读取最新色板）。"""
        self._dark = dark
        self.update()

    def sizeHint(self) -> object:  # type: ignore[override]
        from PySide2.QtCore import QSize

        return QSize(200, max(60, len(self._data) * 30 + 12))

    def paintEvent(self, event: QPaintEvent) -> None:
        """逐行绘制标签、比例条与右端数值。"""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        t = palette_tokens(self._dark)
        fm = QFontMetrics(painter.font())
        row_h = 30
        x_label = 4
        x_track = self._label_width + 12
        track_w = self.width() - x_track - 70
        y = 6
        max_value = max((float(d["value"]) for d in self._data), default=0.0)

        for d in self._data:
            label = fm.elidedText(str(d["label"]), Qt.ElideRight, self._label_width)
            painter.setPen(QColor(t["text_secondary"]))
            painter.drawText(x_label, y, self._label_width, row_h, Qt.AlignVCenter, label)
            if track_w > 20:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(t["border"]))
                painter.drawRoundedRect(x_track, y + row_h // 2 - 6, track_w, 12, 6, 6)
                ratio = float(d["value"]) / max_value if max_value > 0 else 0.0
                fill_w = max(8, int(track_w * ratio)) if self._data else 0
                fill_w = min(fill_w, track_w)
                painter.setBrush(QColor(str(d["color"])))
                painter.drawRoundedRect(x_track, y + row_h // 2 - 6, fill_w, 12, 6, 6)
                value = float(d["value"])
                text = f"{value:.{self._decimals}f}{self._suffix}"
                painter.setPen(QColor(t["text_primary"]))
                painter.drawText(
                    x_track + track_w + 8,
                    y,
                    62,
                    row_h,
                    Qt.AlignVCenter,
                    text,
                )
            y += row_h
        painter.end()


class StatsPage(QWidget):
    """统计页视图：依赖 WorkspaceController 当前任务所属的 ScanController。"""

    homeRequested = Signal()

    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        """初始化统计页并连接工作区/扫描信号。

        :param controller: 主控制器（使用其 :attr:`workspace` 子控制器）
        :param parent: 父部件
        """
        super().__init__(parent)
        self._workspace = controller.workspace
        self._connected_controller: object | None = None
        self._dark = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)

        # ---------- 标题区 ----------
        header = QHBoxLayout()
        header.setSpacing(12)
        back_btn = QPushButton("返回")
        back_btn.setProperty("variant", "ghost")
        title = QLabel("统计信息")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        header.addWidget(back_btn)
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)
        back_btn.clicked.connect(self.homeRequested)

        self._empty_label = QLabel("未选择任务\n请从文件扫描页工作区卡片点击「统计」")
        root.addWidget(self._empty_label)
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setWordWrap(True)

        # ---------- 内容滚动区 ----------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVisible(False)
        root.addWidget(scroll, stretch=1)
        body = QWidget()
        scroll.setWidget(body)
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(14)

        # 状态摘要
        summary_group = CardGroupBox("状态摘要")
        content.addWidget(summary_group)
        state_row = QHBoxLayout()
        state_row.setSpacing(6)
        self._state_dot = QFrame()
        self._state_dot.setFixedSize(8, 8)
        self._state_text_label = QLabel("当前状态：")
        self._state_value = QLabel()
        state_row.addWidget(self._state_dot)
        state_row.addWidget(self._state_text_label)
        state_row.addWidget(self._state_value)
        state_row.addStretch()
        summary_group.content.addLayout(state_row)
        self._status_summary = QLabel()
        self._status_summary.setWordWrap(True)
        summary_group.content.addWidget(self._status_summary)

        # 收集文件（walk）进度
        walk_group = CardGroupBox("收集文件")
        content.addWidget(walk_group)
        walk_row = QHBoxLayout()
        walk_row.setSpacing(6)
        self._walk_dot = QFrame()
        self._walk_dot.setFixedSize(8, 8)
        self._walk_state = QLabel()
        self._walk_elapsed = QLabel()
        self._walk_percent = QLabel()
        for w in (self._walk_dot, self._walk_state, self._walk_elapsed):
            walk_row.addWidget(w)
        walk_row.addStretch()
        walk_row.addWidget(self._walk_percent)
        walk_group.content.addLayout(walk_row)
        self._walk_bar = QProgressBar()
        walk_group.content.addWidget(self._walk_bar)
        walk_grid = QHBoxLayout()
        self._walk_grid_labels: dict[str, tuple[QLabel, QLabel]] = {}
        for key, caption in (
            ("discovered", "已发现"),
            ("classified", "纳入扫描"),
            ("skipped", "类型不符跳过"),
            ("user", "用户标记跳过"),
        ):
            col = QVBoxLayout()
            cap = QLabel(caption)
            val = QLabel("0")
            self._walk_grid_labels[key] = (cap, val)
            col.addWidget(cap)
            col.addWidget(val)
            wrap = QWidget()
            wrap.setLayout(col)
            walk_grid.addWidget(wrap, stretch=1)
        walk_group.content.addLayout(walk_grid)

        # 解析文件（scan）进度
        scan_group = CardGroupBox("解析文件")
        content.addWidget(scan_group)
        scan_row = QHBoxLayout()
        scan_row.setSpacing(6)
        self._scan_dot = QFrame()
        self._scan_dot.setFixedSize(8, 8)
        self._scan_state = QLabel()
        self._scan_elapsed = QLabel()
        self._scan_percent = QLabel()
        for w in (self._scan_dot, self._scan_state, self._scan_elapsed):
            scan_row.addWidget(w)
        scan_row.addStretch()
        scan_row.addWidget(self._scan_percent)
        scan_group.content.addLayout(scan_row)
        self._scan_bar = QProgressBar()
        scan_group.content.addWidget(self._scan_bar)
        self._scanned_line = QLabel()
        scan_group.content.addWidget(self._scanned_line)

        # 分类计数三卡片
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self._count_cards: dict[str, QLabel] = {}
        for key, caption, tint in (
            ("passed", "安全", "success"),
            ("matched", "命中", "danger"),
            ("error", "错误", "danger"),
        ):
            card = QFrame()
            card.setFixedHeight(80)
            card.setStyleSheet(
                f"background-color: {_CARD_TINT[tint]}1f; border: 1px solid"
                f" {palette_tokens(False)['success' if tint == 'success' else 'danger']};"
                " border-radius: 6px;"
            )
            col = QVBoxLayout(card)
            big = QLabel("0")
            big.setAlignment(Qt.AlignCenter)
            big.setStyleSheet(
                f"font-size: 28px; font-weight: bold; color: {_CARD_TINT[tint]}; background: transparent;"
            )
            small = QLabel(caption)
            small.setAlignment(Qt.AlignCenter)
            col.addWidget(big)
            col.addWidget(small)
            self._count_cards[key] = big
            cards_row.addWidget(card, stretch=1)
        content.addLayout(cards_row)

        # 命中分布
        dist_group = CardGroupBox("命中分布")
        content.addWidget(dist_group)
        top_row = QHBoxLayout()
        top_row.setSpacing(16)
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("严重度分布"))
        severity_col_widget = QWidget()
        severity_col_widget.setLayout(left_col)
        self._severity_chart = PieChart()
        severity_legend = QVBoxLayout()
        self._severity_chart._legend_layout = severity_legend
        severity_chart_holder = QWidget()
        sv_layout = QHBoxLayout(severity_chart_holder)
        sv_layout.addWidget(self._severity_chart, stretch=3)
        sv_legend_holder = QWidget()
        sv_legend_holder.setLayout(severity_legend)
        sv_layout.addWidget(sv_legend_holder, stretch=2)
        left_col.addWidget(severity_chart_holder)
        top_row.addWidget(severity_col_widget, stretch=1)
        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("扩展名分布"))
        ext_col_widget = QWidget()
        ext_col_widget.setLayout(right_col)
        self._extension_chart = PieChart()
        ext_legend = QVBoxLayout()
        self._extension_chart._legend_layout = ext_legend
        ext_chart_holder = QWidget()
        ex_layout = QHBoxLayout(ext_chart_holder)
        ex_layout.addWidget(self._extension_chart, stretch=3)
        ex_legend_holder = QWidget()
        ex_legend_holder.setLayout(ext_legend)
        ex_layout.addWidget(ex_legend_holder, stretch=2)
        right_col.addWidget(ext_chart_holder)
        top_row.addWidget(ext_col_widget, stretch=1)
        dist_group.content.addLayout(top_row)
        bottom_col = QVBoxLayout()
        bottom_col.addWidget(QLabel("命中数 Top 10 规则"))
        self._rules_chart = BarChart(label_width=160)
        bottom_col.addWidget(self._rules_chart)
        bottom_holder = QWidget()
        bottom_holder.setLayout(bottom_col)
        dist_group.content.addWidget(bottom_holder)

        # 性能剖析
        perf_group = CardGroupBox("性能剖析")
        content.addWidget(perf_group)
        perf_col = QVBoxLayout()
        perf_hint = QLabel("各阶段总耗时（毫秒）")
        perf_hint.setStyleSheet("font-size: 11px;")
        perf_col.addWidget(perf_hint)
        self._perf_chart = BarChart(label_width=120, suffix="ms", decimals=1)
        perf_col.addWidget(self._perf_chart)
        self._perf_summary_line = QLabel()
        perf_col.addWidget(self._perf_summary_line)
        perf_holder = QWidget()
        perf_holder.setLayout(perf_col)
        perf_group.content.addWidget(perf_holder)

        content.addStretch()

        # 控件句柄汇总
        self._scroll = scroll
        self._walk_group = walk_group
        self._scan_group = scan_group
        self._dist_group = dist_group
        self._perf_group = perf_group

        # ---------- 信号联动 ----------
        self._workspace.currentWorkspaceChanged.connect(self._on_workspace_changed)
        ws_scan = self._current_scan_controller()
        if ws_scan is not None:
            self._connect_scan_controller(ws_scan)
        self.refresh_all()

    # ----------------------------- 公共 API -----------------------------

    def set_dark(self, dark: bool) -> None:
        """主题切换：刷新图表与语义色元素。

        :param dark: 是否启用深色主题
        """
        if self._dark == dark:
            return
        self._dark = dark
        for chart in (self._severity_chart, self._extension_chart, self._rules_chart, self._perf_chart):
            chart.set_dark(dark)
        self.refresh_all()

    # ----------------------------- 刷新逻辑 -----------------------------

    def _current_scan_controller(self) -> object | None:
        """返回当前任务的 ScanController（无任务时为 None）。"""
        if not self._workspace.hasCurrentWorkspace:
            return None
        try:
            sc = self._workspace.currentScanController
        except Exception:
            return None
        return sc

    # 扫描控制器的状态/进度信号名（全部汇聚到 refresh_all）
    _SCAN_SIGNALS: tuple[str, ...] = (
        "progressChanged",
        "scanStateChanged",
        "statusChanged",
        "phaseChanged",
        "walkProgressChanged",
        "scanProgressChanged",
    )

    def _connect_scan_controller(self, sc: object) -> None:
        """绑定指定 ScanController 的全部状态/进度信号。"""
        for name in self._SCAN_SIGNALS:
            getattr(sc, name).connect(self.refresh_all)

    def _disconnect_scan_controller(self, sc: object) -> None:
        """解除指定 ScanController 的信号绑定（未绑定的信号静默跳过）。"""
        for name in self._SCAN_SIGNALS:
            with contextlib.suppress(RuntimeError, TypeError):
                getattr(sc, name).disconnect(self.refresh_all)

    def _on_workspace_changed(self) -> None:
        """切换当前任务后重新绑定 ScanController 信号。"""
        old = self._connected_controller
        if old is not None:
            self._disconnect_scan_controller(old)
        new = self._current_scan_controller()
        self._connected_controller = new
        if new is not None:
            self._connect_scan_controller(new)
        self.refresh_all()

    def refresh_all(self) -> None:
        """从当前 ScanController 全量重读并刷新全部展示控件。"""
        t = palette_tokens(self._dark)
        sc = self._connected_controller or self._current_scan_controller()
        has_ws = self._workspace.hasCurrentWorkspace
        self._empty_label.setVisible(not has_ws)
        self._scroll.setVisible(has_ws)
        if sc is None:
            return

        status = str(sc.statusText or "")
        matched = int(sc.matchedCount or 0)
        color = t["primary"]
        if status == "扫描中":
            color = t["warning"]
        elif status == "已暂停":
            color = t["text_secondary"]
        elif status == "已完成":
            color = t["danger"] if matched > 0 else t["success"]
        elif "取消" in status or status == "失败":
            color = t["warning"]
        self._state_value.setText(status)
        self._state_value.setStyleSheet(f"font-weight: bold; color: {color}; background: transparent;")
        self._state_dot.setStyleSheet(f"background-color: {color}; border-radius: 4px; border: none;")
        self._status_summary.setText(str(sc.statusSummary or "暂无摘要"))

        show_phases = str(sc.scanPhase) != "setup"
        self._walk_group.setVisible(show_phases)
        self._scan_group.setVisible(show_phases)

        # walk 组
        walk_color = t["success"] if sc.walkDone else (t["primary"] if sc.scanPhase == "walk" else t["border"])
        self._walk_dot.setStyleSheet(f"background-color: {walk_color}; border-radius: 4px; border: none;")
        self._walk_state.setText("已完成" if sc.walkDone else ("统计中..." if sc.walkIndeterminate else "进行中"))
        self._walk_state.setStyleSheet(f"font-weight: bold; color: {walk_color}; background: transparent;")
        self._walk_elapsed.setVisible(bool(sc.walkElapsedText))
        self._walk_elapsed.setText(f"用时 {sc.walkElapsedText}")
        wp = float(sc.walkProgress)
        self._walk_percent.setText(f"{round(wp)}%")
        self._sync_progress_bar(self._walk_bar, indeterminate=sc.walkIndeterminate, value=wp, done=sc.walkDone)
        grid_values = {
            "discovered": sc.walkDiscovered,
            "classified": sc.walkClassified,
            "skipped": sc.walkSkipped,
            "user": sc.walkUserSkipped,
        }
        for key, value in grid_values.items():
            val = self._walk_grid_labels[key][1]
            val.setText(_fmt_int(value))
            val.setStyleSheet(f"font-weight: bold; font-size: 15px; color: {t['primary']};")

        # scan 组
        active = sc.scanPhase in ("scan", "archive")
        scan_color = t["success"] if sc.scanDone else (t["warning"] if active else t["border"])
        self._scan_dot.setStyleSheet(f"background-color: {scan_color}; border-radius: 4px; border: none;")
        self._scan_state.setText("已完成" if sc.scanDone else ("等待中..." if sc.progressIndeterminate else "进行中"))
        self._scan_state.setStyleSheet(f"font-weight: bold; color: {scan_color}; background: transparent;")
        self._scan_elapsed.setVisible(bool(sc.scanElapsedText))
        self._scan_elapsed.setText(f"用时 {sc.scanElapsedText}")
        sp = float(sc.progress)
        self._scan_percent.setText(f"{round(sp)}%")
        self._sync_progress_bar(self._scan_bar, indeterminate=sc.progressIndeterminate, value=sp, done=sc.scanDone)
        scanned_text = f"已扫描：{sc.progressScanned} / {sc.progressTotal} 个文件"
        if int(sc.archiveEntryCount) > 0:
            scanned_text += f"（含压缩包内条目 {sc.archiveEntryCount}）"
        self._scanned_line.setText(scanned_text)

        # 三卡片
        self._count_cards["passed"].setText(_fmt_int(sc.passedCount))
        self._count_cards["matched"].setText(_fmt_int(sc.matchedCount))
        self._count_cards["error"].setText(_fmt_int(sc.errorCount))

        # 图表区
        show_dist = bool(sc.scanDone) and matched > 0
        self._dist_group.setVisible(show_dist)
        if show_dist:
            self._severity_chart.set_data(list(sc.severityChartData), "命中文件")
            self._extension_chart.set_data(list(sc.extensionChartData), "命中文件")
            self._rules_chart.set_data(list(sc.topRulesChartData))

        # 性能剖析
        perf = list(sc.perfSummary)
        self._perf_group.setVisible(bool(sc.scanDone) and len(perf) > 0)
        if perf:
            self._perf_chart.set_data(perf)
            top = perf[0]
            self._perf_summary_line.setText(
                f"最耗时阶段：{top['label']} {top['value']}ms（{top.get('percent', '')}%）· 共 {len(perf)} 个阶段"
            )

    @staticmethod
    def _sync_progress_bar(bar: QProgressBar, *, indeterminate: bool, value: float, done: bool) -> None:
        """同步单个进度条的忙态与数值（done 固定 100）。"""
        bar.setMaximum(100 if not indeterminate else 0)
        bar.setValue(100 if done else int(value))
