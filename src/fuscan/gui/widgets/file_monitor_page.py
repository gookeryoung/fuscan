"""文件监控页：目录管理 + 事件日志 + 命中列表。

- 拖拽/对话框添加监控目录，首次添加自动启用监控
- 最近变更紧凑日志（最近 3 条）与过滤统计提示（悬浮显示明细）
- 命中列表复用 :class:`~fuscan.gui.models.file_monitor_model.FileMonitorModel`，
  经自绘 delegate 呈现五列信息
"""

# pyrefly: ignore-errors
# PySide2 存根重载合并缺陷导致 QWidget/QLayout/QPainter 调用与 Signal.connect 误报，
# 详见 sidebar.py 头部说明；本文件为纯 GUI 布局代码，采用文件级压制。

from __future__ import annotations

from pathlib import Path

from PySide2.QtCore import Qt, QUrl, Signal
from PySide2.QtGui import QColor, QDragEnterEvent, QDragLeaveEvent, QDropEvent, QPainter, QPaintEvent
from PySide2.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from fuscan.gui.controllers import AppController
from fuscan.gui.widgets.icons import tinted_svg_icon
from fuscan.gui.widgets.qss import palette_tokens
from fuscan.gui.widgets.sidebar import _ToggleSwitch

__all__ = ["FileMonitorPage"]

# FileMonitorModel 角色（Qt.UserRole+n，与 models/file_monitor_model.py 保持一致）
_ROLE_TIME = Qt.UserRole + 1
_ROLE_FILE_PATH = Qt.UserRole + 2
_ROLE_RULE_NAME = Qt.UserRole + 3
_ROLE_SEVERITY_COLOR = Qt.UserRole + 5
_ROLE_MATCH_TEXT = Qt.UserRole + 6


class _MonitorToggle(_ToggleSwitch):
    """监控启停开关：深色提示取自页面状态而非父级链。"""

    def __init__(self, initial: bool, dark_provider: object) -> None:
        super().__init__(initial)
        self._provider = dark_provider

    def _dark_hint(self) -> bool:
        return bool(self._provider())


def _accessible_dirs(urls: list[QUrl]) -> list[str]:
    """从 URL 列表提取可用的本地目录路径。"""
    result: list[str] = []
    for url in urls:
        if url.scheme() not in ("file", ""):
            continue
        path = url.toLocalFile()
        if path and Path(path).is_dir():
            result.append(str(Path(path).resolve()))
    return result


class _DropHint(QFrame):
    """拖拽接收区：无监控目录时占满剩余空间，接受文件夹拖入。"""

    pathsDropped = Signal(list)  # 本地目录路径列表

    def __init__(self, dark_provider: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dark_provider = dark_provider
        self._hovered = False
        self.setAcceptDrops(True)
        self.setMinimumHeight(220)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)
        icon = QLabel()
        icon.setFixedSize(48, 48)
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(tinted_svg_icon(":/icons/folder.svg", "#888888", 48).pixmap(48, 48))
        text = QLabel("拖拽文件夹到此处")
        sub = QLabel("或点击「添加监控文件夹」")
        self._icon = icon
        self._text = text
        self._sub = sub
        for w in (icon, text, sub):
            layout.addWidget(w, alignment=Qt.AlignHCenter)

    # ----------------------------- 拖放事件 -----------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """含本地目录时接受拖入并高亮边框。"""
        if event.mimeData().hasUrls() and _accessible_dirs(event.mimeData().urls()):
            event.acceptProposedAction()
            if not self._hovered:
                self._hovered = True
                self.update()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        """拖出取消高亮。"""
        super().dragLeaveEvent(event)
        if self._hovered:
            self._hovered = False
            self.update()

    def dropEvent(self, event: QDropEvent) -> None:
        """落地后发出目录路径信号。"""
        super().dropEvent(event)
        self._hovered = False
        self.update()
        dirs = _accessible_dirs(event.mimeData().urls())
        if dirs:
            event.acceptProposedAction()
            self.pathsDropped.emit(dirs)

    # ----------------------------- 绘制 -----------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        """绘制圆角虚线感边框：悬停主色加粗，常规灰细线。"""
        t = palette_tokens(bool(self._dark_provider()))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(t["primary"]) if self._hovered else QColor(t["border"])
        pen = painter.pen()
        pen.setColor(color)
        pen.setWidth(2 if self._hovered else 1)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)
        painter.end()


class _HitDelegate(QStyledItemDelegate):
    """命中列表行：时间 | 严重度色条 | 规则名 | 文件路径 | 命中片段。"""

    def __init__(self, view: QListView) -> None:
        super().__init__(view)
        self._view = view

    def paint(self, painter: QPainter, option: QStyle.OptionViewItem, index: object) -> None:  # type: ignore[override]
        dark_hint = getattr(option.widget, "_dark_hint", None)
        dark = bool(dark_hint()) if callable(dark_hint) else False
        t = palette_tokens(dark)
        rect = option.rect.adjusted(8, 0, -8, 0)

        painter.save()
        # 卡片底与边框
        card = QColor(t["bg_card"])
        border = QColor(t["border"])
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(border)
        painter.setBrush(card)
        painter.drawRoundedRect(option.rect.adjusted(0, 1, 0, -1), 4, 4)

        fm = painter.fontMetrics()

        def _draw(text: str, x: int, width: int, color: str, bold: bool = False) -> None:
            painter.setPen(QColor(color))
            font = painter.font()
            font.setBold(bold)
            painter.setFont(font)
            painter.drawText(
                rect.x() + x,
                rect.y(),
                width,
                rect.height(),
                Qt.AlignVCenter | Qt.AlignLeft,
                fm.elidedText(text, Qt.ElideRight, width),
            )
            font.setBold(False)
            painter.setFont(font)

        x = 0
        time_text = str(index.data(_ROLE_TIME) or "")
        _draw(time_text, x, 60, t["text_secondary"])
        x += 66

        severity = str(index.data(_ROLE_SEVERITY_COLOR) or t["border_muted"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(severity))
        bar_x = rect.x() + x
        bar_y = rect.y() + (rect.height() - 16) // 2
        painter.drawRoundedRect(bar_x, bar_y, 4, 16, 2, 2)
        x += 12

        rule_name = str(index.data(_ROLE_RULE_NAME) or "")
        _draw(rule_name, x, 120, severity, bold=True)
        x += 128

        file_path = str(index.data(_ROLE_FILE_PATH) or "")
        remaining = rect.width() - x - 228
        if remaining > 60:
            painter.setPen(QColor(t["text_secondary"]))
            painter.drawText(
                rect.x() + x,
                rect.y(),
                remaining,
                rect.height(),
                Qt.AlignVCenter | Qt.AlignLeft,
                fm.elidedText(file_path, Qt.ElideMiddle, remaining),
            )
        match_text = str(index.data(_ROLE_MATCH_TEXT) or "")
        _draw(match_text, rect.width() - 224, 224, t["text_secondary"])
        painter.restore()

    def refresh_theme(self) -> None:
        """主题切换后刷新 delegate 持有的视图（下次 paint 读取新色板）。"""
        widget = getattr(self, "_view", None)
        if widget is not None:
            widget.viewport().update()

    def sizeHint(self, _option: QStyle.OptionViewItem, _index: object) -> object:  # type: ignore[override]
        from PySide2.QtCore import QSize

        return QSize(0, 36)


class _HitListView(QListView):
    """命中列表：内置空态提示覆盖层。"""

    def set_empty_label(self, label: QLabel) -> None:
        """挂载空态标签（由页面负责文本更新）。"""
        self._empty_label = label
        label.setParent(self.viewport())
        label.setAlignment(Qt.AlignCenter)

    def resizeEvent(self, event: object) -> None:
        """视口尺寸变化时保持空态提示居中。"""
        super().resizeEvent(event)
        label = getattr(self, "_empty_label", None)
        if label is not None:
            label.setGeometry(self.viewport().rect())

    def _dark_hint(self) -> bool:
        """供 delegate 取当前主题态（由页面注入）。"""
        page = self.parent()
        while page is not None:
            if hasattr(page, "_dark"):
                return bool(page._dark)
            page = page.parent()
        return False


class FileMonitorPage(QWidget):
    """文件监控页视图：目录管理与实时命中展示。"""

    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        """初始化监控页并连接控制器信号。

        :param controller: 主控制器（使用其 :attr:`file_monitor` 子控制器）
        :param parent: 父部件
        """
        super().__init__(parent)
        self._controller = controller.file_monitor
        self._dark = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(10)

        # ---------- 标题栏 ----------
        header = QHBoxLayout()
        header.setSpacing(12)
        title = QLabel("文件监控")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        self._status_label = QLabel()
        add_btn = QPushButton(" 添加监控文件夹")
        clear_hits_btn = QPushButton(" 清空命中")
        self._add_btn = add_btn
        self._clear_hits_btn = clear_hits_btn
        header.addWidget(title)
        header.addWidget(self._status_label)
        header.addStretch()
        self._toggle = _MonitorToggle(False, lambda: self._dark)
        self._toggle.toggled_to.connect(self._controller.setMonitoringEnabled)
        header.addWidget(self._toggle)
        header.addWidget(add_btn)
        header.addWidget(clear_hits_btn)
        root.addLayout(header)

        add_btn.clicked.connect(self._choose_folder)
        clear_hits_btn.clicked.connect(self._controller.clearHits)

        # ---------- 拖拽接收区 ----------
        self._drop_hint = _DropHint(lambda: self._dark)
        self._drop_hint.pathsDropped.connect(self._on_paths_dropped)
        root.addWidget(self._drop_hint, stretch=1)

        # ---------- 监控目录 ----------
        watched_box = QWidget()
        watched_col = QVBoxLayout(watched_box)
        watched_col.setContentsMargins(0, 0, 0, 0)
        watched_col.setSpacing(4)
        self._watched_title = QLabel()
        self._watched_title.setStyleSheet("font-size: 11px; font-weight: bold;")
        watched_col.addWidget(self._watched_title)
        self._watched_rows_layout = QVBoxLayout()
        self._watched_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._watched_rows_layout.setSpacing(4)
        watched_col.addLayout(self._watched_rows_layout)
        self._watched_box = watched_box
        root.addWidget(watched_box)

        # ---------- 最近变更 ----------
        events_box = QWidget()
        events_col = QVBoxLayout(events_box)
        events_col.setContentsMargins(0, 0, 0, 0)
        events_col.setSpacing(4)
        events_header = QHBoxLayout()
        events_header.setSpacing(8)
        events_title = QLabel("最近变更")
        events_title.setStyleSheet("font-size: 11px; font-weight: bold;")
        self._event_badge = QLabel()
        self._filtered_label = QLabel()
        self._filtered_label.setStyleSheet("font-size: 10px; background: transparent;")
        clear_events_btn = QPushButton("清空事件")
        clear_events_btn.setProperty("variant", "ghost")
        self._clear_events_btn = clear_events_btn
        events_header.addWidget(events_title)
        events_header.addWidget(self._event_badge)
        events_header.addWidget(self._filtered_label)
        events_header.addStretch()
        events_header.addWidget(clear_events_btn)
        events_col.addLayout(events_header)
        self._events_rows_layout = QVBoxLayout()
        self._events_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._events_rows_layout.setSpacing(2)
        events_col.addLayout(self._events_rows_layout)
        self._events_box = events_box
        root.addWidget(events_box)

        clear_events_btn.clicked.connect(self._controller.clearEvents)

        # ---------- 命中列表 ----------
        self._hit_list = _HitListView()
        model = self._controller.model
        self._hit_list.setModel(model)
        self._hit_delegate = _HitDelegate(self._hit_list)
        self._hit_list.setItemDelegate(self._hit_delegate)
        self._hit_list.setSelectionMode(QListView.NoSelection)
        self._empty_label = QLabel()
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet("color: #888888; font-size: 12px; background: transparent;")
        self._hit_list.set_empty_label(self._empty_label)
        model.rowsInserted.connect(self._refresh_empty_state)
        model.modelReset.connect(self._refresh_empty_state)
        model.rowsRemoved.connect(self._refresh_empty_state)
        root.addWidget(self._hit_list, stretch=2)

        # ---------- 控制器信号联动 ----------
        self._controller.watchedDirectoriesChanged.connect(self._refresh_watched)
        self._controller.eventLogChanged.connect(self._refresh_events)
        self._controller.monitorStateChanged.connect(self._refresh_state)
        self._controller.directoryRemoved.connect(lambda _p: self._refresh_visibility())

        self._refresh_watched()
        self._refresh_events()
        self._refresh_state()
        self._apply_theme_texts()

    # ----------------------------- 构建/刷新 -----------------------------

    def _choose_folder(self) -> None:
        """弹出文件夹选择对话框并添加首个选中目录。"""
        chosen = QFileDialog.getExistingDirectory(self, "选择监控文件夹")
        if not chosen:
            return
        if self._controller.addWatch(chosen) and not self._controller.monitoringEnabled:
            self._controller.setMonitoringEnabled(True)

    def _on_paths_dropped(self, dirs: list[str]) -> None:
        """拖入目录批量添加；任一成功即启用监控。"""
        added = False
        for d in dirs:
            if self._controller.addWatch(d):
                added = True
        if added and not self._controller.monitoringEnabled:
            self._controller.setMonitoringEnabled(True)

    def _refresh_watched(self) -> None:
        """重建监控目录行与可见性、标题计数。"""
        while self._watched_rows_layout.count():
            item = self._watched_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        t = palette_tokens(self._dark)
        count = self._controller.watchedCount
        self._watched_title.setText(f"监控目录（{count}）")
        for path in self._controller.watchedDirectories:
            row_widget = QFrame()
            row_widget.setStyleSheet(
                f"background-color: {t['bg_card']}; border: 1px solid {t['border']}; border-radius: 4px;"
            )
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(8, 4, 8, 4)
            row.setSpacing(6)
            icon = QLabel()
            icon.setFixedSize(14, 14)
            icon.setPixmap(tinted_svg_icon(":/icons/folder.svg", t["text_secondary"], 14).pixmap(14, 14))
            icon.setStyleSheet("background: transparent;")
            name = QLabel(path)
            name.setToolTip(path)
            name.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']}; background: transparent;")
            remove_btn = QPushButton()
            remove_btn.setProperty("variant", "ghost")
            remove_btn.setObjectName("iconBtn")
            remove_btn.setIcon(tinted_svg_icon(":/icons/close.svg", t["text_secondary"], 12))
            remove_btn.setFixedSize(24, 24)
            remove_btn.setToolTip("移除监控")
            remove_btn.clicked.connect(lambda _=False, p=path: self._remove_watch(p))
            row.addWidget(icon)
            row.addWidget(name, stretch=1)
            row.addWidget(remove_btn)
            self._watched_rows_layout.addWidget(row_widget)
        self._refresh_visibility()

    def _remove_watch(self, path: str) -> None:
        """移除目录并刷新区域显隐。"""
        if self._controller.removeWatch(path):
            self._refresh_visibility()

    def _refresh_events(self) -> None:
        """重建事件徽标、过滤统计与最近 3 条事件行。"""
        c = self._controller
        t = palette_tokens(self._dark)
        filtered_total = c.ignoredDirCount + c.filteredExtCount + c.dirEventCount
        badge_text = f"{c.eventCount} 个事件"
        if c.eventCount > 0:
            self._event_badge.setText(badge_text)
            self._event_badge.setStyleSheet(
                f"font-size: 10px; padding: 2px 6px; border-radius: 8px; color: {t['primary']};"
            )
        else:
            self._event_badge.setText(badge_text)
            self._event_badge.setStyleSheet(
                f"font-size: 10px; padding: 2px 6px; border-radius: 8px; color: {t['text_secondary']};"
            )
        self._clear_events_btn.setEnabled(c.eventCount > 0)
        if filtered_total > 0:
            self._filtered_label.setText(f"已过滤 {filtered_total} 个")
            self._filtered_label.setVisible(True)
            self._filtered_label.setToolTip(
                f"目录事件 {c.dirEventCount} / 噪声目录 {c.ignoredDirCount} / 扩展名不匹配 {c.filteredExtCount}"
            )
        else:
            self._filtered_label.setVisible(False)

        while self._events_rows_layout.count():
            item = self._events_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        recent = list(reversed(c.recentEvents[-3:]))
        for ev in recent:
            row_widget = QFrame()
            row_widget.setStyleSheet(f"background-color: {t['bg_hover']}; border-radius: 3px; border: none;")
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(8, 2, 8, 2)
            row.setSpacing(8)
            time_lbl = QLabel(str(ev.get("time", "")))
            time_lbl.setStyleSheet("font-family: Consolas; font-size: 10px; background: transparent;")
            time_lbl.setFixedWidth(56)
            type_lbl = QLabel(str(ev.get("event_type", "")))
            type_lbl.setStyleSheet(f"font-size: 10px; color: {t['primary']}; background: transparent;")
            type_lbl.setFixedWidth(50)
            path_lbl = QLabel(str(ev.get("path", "")))
            path_lbl.setStyleSheet(f"font-size: 10px; color: {t['text_secondary']}; background: transparent;")
            row.addWidget(time_lbl)
            row.addWidget(type_lbl)
            row.addWidget(path_lbl, stretch=1)
            self._events_rows_layout.addWidget(row_widget)

    def _refresh_state(self) -> None:
        """同步标题旁的状态文字与开关选中态（不发回环信号）。"""
        enabled = self._controller.monitoringEnabled
        t = palette_tokens(self._dark)
        self._status_label.setText("监控中" if enabled else "已停止")
        color = t["success"] if enabled else t["text_secondary"]
        self._status_label.setStyleSheet(f"font-size: 11px; color: {color}; background: transparent;")
        self._toggle.blockSignals(True)
        self._toggle.set_on(enabled)
        self._toggle.blockSignals(False)
        self._toggle.setEnabled(self._controller.watchedCount > 0)
        self._refresh_empty_state()

    def _refresh_visibility(self) -> None:
        """按监控目录数切换拖拽区/目录区/事件区/命中列表显隐与开关禁用态。"""
        has_dirs = self._controller.watchedCount > 0
        self._drop_hint.setVisible(not has_dirs)
        self._watched_box.setVisible(has_dirs)
        self._events_box.setVisible(has_dirs)
        self._hit_list.setVisible(has_dirs)
        self._toggle.setEnabled(has_dirs)
        self._refresh_empty_state()

    def _refresh_empty_state(self) -> None:
        """按「未启动/有事件无命中/等待中」三种情形刷新空态文案。"""
        c = self._controller
        has_dirs = c.watchedCount > 0
        visible = has_dirs and c.model.rowCount() == 0
        self._empty_label.setVisible(visible)
        if visible:
            if not c.monitoringEnabled:
                self._empty_label.setText("点击开关开始监控")
            elif c.eventCount > 0:
                self._empty_label.setText(f"已接收 {c.eventCount} 个变更事件，暂无命中")
            else:
                self._empty_label.setText("等待文件变更…")
        self._clear_hits_btn.setEnabled(c.model.rowCount() > 0)

    # ----------------------------- 公共 API -----------------------------

    def set_dark(self, dark: bool) -> None:
        """主题切换：刷新自绘元素与语义色文本。

        :param dark: 是否启用深色主题
        """
        if self._dark == dark:
            return
        self._dark = dark
        self._drop_hint.update()
        self._hit_delegate.refresh_theme()
        self._hit_list.viewport().update()
        self._refresh_watched()
        self._refresh_events()
        self._refresh_state()
        self._apply_theme_texts()

    # ----------------------------- 私有 -----------------------------

    def _apply_theme_texts(self) -> None:
        """静态主题色文本集中刷新。"""
        t = palette_tokens(self._dark)
        self._add_btn.setIcon(tinted_svg_icon(":/icons/add.svg", t["text_on_primary"], 14))
        self._clear_hits_btn.setIcon(tinted_svg_icon(":/icons/delete.svg", t["text_primary"], 14))
