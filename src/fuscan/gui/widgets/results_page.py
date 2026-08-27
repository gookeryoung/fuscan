"""结果页：扫描结果清单 + 命中详情面板。

- 工具栏：路径搜索框（300ms 防抖）/ 严重度过滤 / 排序字段与方向 /
  重置排序 / 过滤计数；「待处理/已替换/全部」维度 Tab
- 左侧清单：:class:`ResultListModel` 经 :class:`_ResultDelegate` 自绘
  （严重度色条+徽标+路径中省略+规则名·命中数），滚动时上报可视行范围
  启用模型虚拟化（大结果集降内存占用）
- 右侧详情：文件信息卡（路径定位/大小/规则数/解析引擎）+ 命中规则卡片列表
  （目标/描述/匹配文本/>>> 高亮上下文，可折叠）+ 底部操作栏
  （上一条/下一条、替换、移至暂存、标记误报、批量替换与撤销）

刷新模型：当前工作区切换时重绑 ScanController 并回放本页过滤条件；
选中变化信号汇聚到 :meth:`ResultsPage._refresh_detail` 与操作按钮可用态。
"""

# pyrefly: ignore-errors
# PySide2 存根重载合并缺陷导致 QWidget/QLayout/QTimer 调用与 Signal.connect 误报，
# 详见 sidebar.py 头部说明；本文件为纯 GUI 布局代码，采用文件级压制。

from __future__ import annotations

import contextlib
from typing import Any

from PySide2.QtCore import QAbstractItemModel, QItemSelectionModel, QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide2.QtGui import QColor, QPainter
from PySide2.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from fuscan.gui.widgets.qss import palette_tokens

__all__ = ["ResultsPage"]

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

# ResultListModel 角色号（与 models/result_model.py 的 _ROLES 对齐）
_ROLE_FILE_PATH = int(Qt.UserRole) + 1
_ROLE_RULE_NAME = int(Qt.UserRole) + 2
_ROLE_SEVERITY_TEXT = int(Qt.UserRole) + 3
_ROLE_SEVERITY_COLOR = int(Qt.UserRole) + 4
_ROLE_HITS_COUNT = int(Qt.UserRole) + 5


def _tokens(dark: bool) -> dict[str, str]:
    return palette_tokens(dark)


def format_context_html(context: str, severity_color: str) -> str:
    """将上下文文本转为 HTML：``>>>`` 匹配行按严重度颜色加粗高亮整行。

    :param context: ScanController 生成的上下文文本（匹配行以 ``>>>`` 开头）
    :param severity_color: 匹配行高亮色值
    """
    parts: list[str] = []
    for raw in context.split("\n"):
        is_match = raw.startswith(">>>")
        # HTML 转义（& 必须先转义，避免后续实体被二次转义）
        escaped = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        stripped = escaped.lstrip(" ")
        html = "&nbsp;" * (len(escaped) - len(stripped)) + stripped
        if is_match:
            html = f'<span style="color: {severity_color}; font-weight: bold;">{html}</span>'
        parts.append(html)
    return "<br>".join(parts)


def _style_label(lbl: QLabel, dark: bool, size: int, color_key: str = "text_primary") -> None:
    """应用标签字号颜色样式。"""
    lbl.setStyleSheet(f"font-size: {size}px; color: {_tokens(dark)[color_key]}; background: transparent;")


class _ResultDelegate(QStyledItemDelegate):
    """结果清单委托：严重度色条 | 徽标 | 路径（中省略）+ 规则名·命中数。"""

    def __init__(self, dark: bool) -> None:
        super().__init__()
        self._dark = dark

    def set_dark(self, dark: bool) -> None:
        """主题切换后重绘可见行。"""
        self._dark = dark

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """固定行高 56px。"""
        del option, index
        return QSize(0, 56)

    def paint(self, painter: QPainter, option: Any, index: QModelIndex) -> None:
        """绘制单行结果条目。"""
        t = _tokens(self._dark)
        path = str(index.data(_ROLE_FILE_PATH) or "")
        rule_name = str(index.data(_ROLE_RULE_NAME) or "")
        sev_text = str(index.data(_ROLE_SEVERITY_TEXT) or "")
        sev_color = str(index.data(_ROLE_SEVERITY_COLOR) or "") or t["primary"]
        hits = int(index.data(_ROLE_HITS_COUNT) or 0)

        painter.save()
        rect = option.rect.adjusted(12, 8, -12, -8)
        height = rect.height()

        # 背景：选中 / 悬浮高亮
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if selected or hovered:
            painter.fillRect(option.rect, QColor(t["bg_selected" if selected else "bg_hover"]))
            if selected:
                painter.setPen(QColor(t["border"]))
                painter.drawRect(option.rect.adjusted(0, 0, -1, -1))

        # 严重度色条
        stripe_h = int(height * 0.6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(sev_color))
        painter.drawRoundedRect(QRect(rect.left(), rect.top() + (height - stripe_h) // 2, 3, stripe_h), 2, 2)
        x = rect.left() + 13

        # 严重度徽标（实底圆角白字）
        fm = painter.fontMetrics()
        badge_w = fm.horizontalAdvance(sev_text) + 14
        badge_h = 20
        badge_y = rect.top() + (height - badge_h) // 2
        painter.drawRoundedRect(QRect(x, badge_y, badge_w, badge_h), 10, 10)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(QRect(x, badge_y, badge_w, badge_h), Qt.AlignCenter, sev_text)

        # 文案列：文件路径 + 规则名·命中数
        x_text = x + badge_w + 10
        width = max(rect.right() - x_text, 40)
        painter.setPen(QColor(t["text_primary"]))
        elided = fm.elidedText(path, Qt.ElideMiddle, width)
        painter.drawText(QRect(x_text, rect.top(), width, height // 2), Qt.AlignVCenter | Qt.AlignLeft, elided)
        painter.setPen(QColor(t["text_secondary"]))
        painter.drawText(
            QRect(x_text, rect.top() + height // 2, width, height // 2),
            Qt.AlignVCenter | Qt.AlignLeft,
            f"{rule_name} · 命中 {hits} 处",
        )
        painter.restore()


class _HitCard(QFrame):
    """单条命中规则卡片：规则名/严重度徽标/目标/描述/匹配文本/高亮上下文。"""

    def __init__(self, dark: bool, hit: dict[str, object]) -> None:
        super().__init__()
        self.setObjectName("hitCard")
        self._sev_color = str(hit.get("severityColor") or "")
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        self._name_label = QLabel(str(hit.get("ruleName") or ""))
        self._name_label.setWordWrap(False)
        head.addWidget(self._name_label, stretch=1)
        self._sev_badge = QLabel(str(hit.get("severityText") or ""))
        self._sev_badge.setAlignment(Qt.AlignCenter)
        head.addWidget(self._sev_badge)
        col.addLayout(head)

        target = str(hit.get("target") or "")
        match_count = int(hit.get("matchCount") or 0)  # type: ignore[call-overload]
        meta_parts = []
        if target:
            meta_parts.append(f"目标: {target}")
        if match_count > 0:
            meta_parts.append(f"匹配 {match_count} 处")
        self._meta_label: QLabel | None = None
        if meta_parts:
            self._meta_label = QLabel(" · ".join(meta_parts))
            col.addWidget(self._meta_label)

        desc = str(hit.get("description") or "")
        self._desc_label: QLabel | None = None
        if desc:
            self._desc_label = QLabel(desc)
            self._desc_label.setWordWrap(True)
            col.addWidget(self._desc_label)

        match_text = str(hit.get("matchText") or "")
        self._match_label: QLabel | None = None
        if match_text:
            self._match_label = QLabel(f"匹配文本: {match_text}")
            self._match_label.setWordWrap(True)
            col.addWidget(self._match_label)

        ctx = str(hit.get("context") or "")
        self._ctx_label: QLabel | None = None
        if ctx:
            self._ctx_label = QLabel(format_context_html(ctx, str(hit.get("severityColor") or "")))
            self._ctx_label.setTextFormat(Qt.RichText)
            self._ctx_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            col.addWidget(self._ctx_label)

        self.set_dark(dark)

    def set_dark(self, dark: bool) -> None:
        """主题切换：重刷卡片边框与各标签配色。"""
        t = _tokens(dark)
        self.setStyleSheet(
            f"#hitCard {{ background-color: {t['bg_app']}; border: 1px solid {t['border']}; border-radius: 6px; }}"
        )
        self._name_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {t['text_primary']}; background: transparent;"
        )
        self._sev_badge.setStyleSheet(
            f"background-color: {self._sev_color or t['primary']}; color: #FFFFFF;"
            "font-size: 10px; padding: 1px 8px; border-radius: 9px; border: none;"
        )
        for lbl in (self._meta_label, self._desc_label):
            if lbl is not None:
                _style_label(lbl, dark, 10, "text_secondary")
        if self._match_label is not None:
            _style_label(self._match_label, dark, 11, "danger")
        if self._ctx_label is not None:
            _style_label(self._ctx_label, dark, 11)


class _DetailPanel(QFrame):
    """命中详情面板：文件信息卡 + 命中规则卡片列表 + 底部操作栏。

    操作按钮不直接持有 ScanController——所有动作经 :class:`ResultsPage`
    的回调完成，由页面统一汇聚消息提示与可用态刷新。
    """

    # 未选择结果时的空态文案
    _DEFAULT_EMPTY_TEXT = "在左侧清单选择一个结果，查看命中详情"

    def __init__(self, page: ResultsPage, dark: bool) -> None:
        super().__init__()
        self.setObjectName("detailPanel")
        self._page = page
        self._dark = dark
        self._details_expanded = True
        self._hit_cards: list[_HitCard] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # 空态 / 恢复中占位（二者互斥显示）
        self._empty_label = QLabel(self._DEFAULT_EMPTY_TEXT)
        self._empty_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self._empty_label, stretch=1)
        self._restore_label = QLabel()
        self._restore_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self._restore_label, stretch=1)
        self._restore_label.hide()

        # ---------- 顶部固定区：文件信息卡 ----------
        self._content = QWidget()
        ccol = QVBoxLayout(self._content)
        ccol.setContentsMargins(0, 0, 0, 0)
        ccol.setSpacing(10)
        info_card = QFrame(objectName="infoCard")
        icol = QVBoxLayout(info_card)
        icol.setContentsMargins(10, 10, 10, 10)
        icol.setSpacing(6)

        head_row = QHBoxLayout()
        head_row.setSpacing(6)
        info_title = QLabel("文件信息")
        self._info_title = info_title
        head_row.addWidget(info_title)
        head_row.addStretch()
        self._archive_badge = QLabel("压缩包条目")
        self._archive_badge.setAlignment(Qt.AlignCenter)
        head_row.addWidget(self._archive_badge)
        icol.addLayout(head_row)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        self._path_label = QLabel()
        self._path_label.setWordWrap(True)
        path_row.addWidget(self._path_label, stretch=1)
        self._locate_btn = QPushButton(" 定位")
        self._locate_btn.setProperty("variant", "secondary")
        self._locate_btn.clicked.connect(page.open_location)
        path_row.addWidget(self._locate_btn)
        icol.addLayout(path_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self._grid_pairs: list[tuple[QLabel, QLabel, str]] = []
        for caption in ("大小", "命中规则", "解析引擎"):
            cap = QLabel(caption)
            val = QLabel("—")
            grid.addWidget(cap, len(self._grid_pairs), 0)
            grid.addWidget(val, len(self._grid_pairs), 1)
            self._grid_pairs.append((cap, val, caption))
        grid.setColumnStretch(1, 1)
        self._engine_pair = self._grid_pairs[-1]
        icol.addLayout(grid)
        ccol.addWidget(info_card)

        # ---------- 命中详情标题 + 折叠按钮 ----------
        hits_head = QHBoxLayout()
        hits_head.setSpacing(8)
        hits_title = QLabel("命中详情")
        self._hits_title = hits_title
        hits_head.addWidget(hits_title)
        hits_head.addStretch()
        self._expand_btn = QPushButton("收起")
        self._expand_btn.setProperty("variant", "ghost")
        self._expand_btn.clicked.connect(self._toggle_expand)
        hits_head.addWidget(self._expand_btn)
        ccol.addLayout(hits_head)

        # ---------- 命中列表滚动区（仅此区域滚动） ----------
        self._hits_scroll = QScrollArea()
        self._hits_scroll.setWidgetResizable(True)
        self._hits_scroll.setFrameShape(QFrame.NoFrame)
        self._hits_container = QWidget()
        self._hits_layout = QVBoxLayout(self._hits_container)
        self._hits_layout.setContentsMargins(0, 0, 4, 0)
        self._hits_layout.setSpacing(8)
        self._hits_layout.addStretch()
        self._hits_scroll.setWidget(self._hits_container)
        ccol.addWidget(self._hits_scroll, stretch=1)
        root.addWidget(self._content, stretch=1)

        # ---------- 底部操作栏 ----------
        bottom = QWidget()
        bcol = QVBoxLayout(bottom)
        bcol.setContentsMargins(0, 0, 0, 0)
        bcol.setSpacing(6)
        self._op_msg = QLabel()
        self._op_msg.setWordWrap(True)
        bcol.addWidget(self._op_msg)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)
        self._prev_btn = QPushButton(" 上一条")
        self._next_btn = QPushButton(" 下一条")
        for b in (self._prev_btn, self._next_btn):
            b.setProperty("variant", "secondary")
        self._prev_btn.clicked.connect(page.select_prev)
        self._next_btn.clicked.connect(page.select_next)
        nav_row.addWidget(self._prev_btn)
        nav_row.addWidget(self._next_btn)
        nav_row.addStretch()
        bcol.addLayout(nav_row)

        replace_row = QHBoxLayout()
        replace_row.setSpacing(8)
        replace_cap = QLabel("替换为:")
        self._replace_cap = replace_cap
        replace_row.addWidget(replace_cap)
        self._replace_input = QLineEdit("...")
        self._replace_input.setPlaceholderText("输入替换文本（默认 ...）")
        replace_row.addWidget(self._replace_input, stretch=1)
        self._replace_btn = QPushButton(" 替换内容")
        self._stage_btn = QPushButton(" 移至暂存")
        self._fp_btn = QPushButton(" 标记误报")
        for b in (self._replace_btn, self._stage_btn, self._fp_btn):
            b.setProperty("variant", "secondary")
        self._replace_btn.clicked.connect(page.replace_selected)
        self._stage_btn.clicked.connect(page.move_to_staging)
        self._fp_btn.clicked.connect(page.mark_false_positive)
        replace_row.addWidget(self._replace_btn)
        replace_row.addWidget(self._stage_btn)
        replace_row.addWidget(self._fp_btn)
        bcol.addLayout(replace_row)

        batch_row = QHBoxLayout()
        batch_row.setSpacing(8)
        self._batch_cap = QLabel("批量操作")
        batch_row.addWidget(self._batch_cap)
        batch_row.addStretch()
        self._replace_all_btn = QPushButton(" 全部替换")
        self._undo_batch_btn = QPushButton(" 撤销批量")
        self._undo_current_btn = QPushButton(" 撤销当前")
        for b in (self._replace_all_btn, self._undo_batch_btn, self._undo_current_btn):
            b.setProperty("variant", "secondary")
        self._replace_all_btn.clicked.connect(page.replace_all_filtered)
        self._undo_batch_btn.clicked.connect(page.undo_batch)
        self._undo_current_btn.clicked.connect(page.undo_current)
        batch_row.addWidget(self._replace_all_btn)
        batch_row.addWidget(self._undo_batch_btn)
        batch_row.addWidget(self._undo_current_btn)
        bcol.addLayout(batch_row)
        root.addWidget(bottom)
        self._bottom = bottom

        self.refresh(None)
        self.set_dark(dark)

    # ----------------------------- 公共 API -----------------------------

    def refresh(self, sc: object | None) -> None:
        """从 ScanController 重读选中结果详情并重建命中卡列表。

        :param sc: 当前 ScanController；None 表示未选择任务
        """
        has_detail = sc is not None and int(getattr(sc, "selectedResultIndex", -1)) >= 0
        self._empty_label.setVisible(not has_detail)
        self._content.setVisible(has_detail)
        self._bottom.setVisible(has_detail)
        self._archive_badge.setVisible(bool(sc is not None and getattr(sc, "detailIsArchiveEntry", False)))
        self.update_actions(sc)
        if not has_detail or sc is None:
            return

        self._path_label.setText(str(sc.detailFilePath) or "—")
        engine = str(sc.detailEngine or "")
        for cap, val, caption in self._grid_pairs:
            is_engine = caption == "解析引擎"
            cap.setVisible(not is_engine or engine != "")
            val.setVisible(not is_engine or engine != "")
            if caption == "大小":
                val.setText(str(sc.detailFileSize) or "—")
            elif caption == "命中规则":
                val.setText(f"{int(sc.detailHitsCount)} 条")
            else:
                val.setText(engine)

        self._rebuild_hits(list(sc.detailHitsModel or []))

    def update_actions(self, sc: object | None) -> None:
        """按控制器能力标志刷新底部操作按钮可用态。"""
        enabled = {
            self._prev_btn: bool(sc is not None and sc.canSelectPrev),
            self._next_btn: bool(sc is not None and sc.canSelectNext),
            self._replace_btn: bool(sc is not None and sc.canReplaceSelected),
            self._stage_btn: bool(sc is not None),
            self._fp_btn: bool(sc is not None and not bool(sc.detailIsArchiveEntry)),
            self._replace_all_btn: bool(sc is not None and sc.canReplaceAllFiltered),
            self._undo_batch_btn: bool(sc is not None and sc.canUndoLastBatchReplace),
            self._undo_current_btn: bool(sc is not None and sc.canReplaceSelected),
        }
        for btn, on in enabled.items():
            btn.setEnabled(on)

    def show_msg(self, message: str) -> None:
        """显示操作结果消息（语义配色）。"""
        self._op_msg.setText(message)
        ok = "成功" in message or "已移至暂存" in message
        failed = "失败" in message
        key = "success" if ok and not failed else ("danger" if failed else "text_secondary")
        _style_label(self._op_msg, self._dark, 11, key)

    def clear_msg(self) -> None:
        """清除操作消息。"""
        self._op_msg.clear()

    def show_restore_hint(self, spinner_frame: str) -> None:
        """显示恢复中占位（转圈帧 + 提示文案）。"""
        self._empty_label.setVisible(False)
        self._restore_label.setText(f"{spinner_frame}  正在恢复上次扫描结果…")
        self._restore_label.setVisible(True)

    def hide_restore_hint(self) -> None:
        """隐藏恢复中占位。"""
        self._restore_label.setVisible(False)

    def replace_text(self) -> str:
        """读取「替换为」输入框内容。"""
        return self._replace_input.text()

    def set_dark(self, dark: bool) -> None:
        """主题切换：整面板重刷样式并重绘命中卡。"""
        self._dark = dark
        t = _tokens(dark)
        self.setStyleSheet(
            f"#detailPanel {{ background-color: {t['bg_card']}; border: 1px solid {t['border']}; border-radius: 8px; }}"
        )
        self._empty_label.setStyleSheet(f"font-size: 12px; color: {t['text_secondary']}; background: transparent;")
        self._restore_label.setStyleSheet(self._empty_label.styleSheet())
        self._info_title.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {t['text_primary']}; background: transparent;"
        )
        self._hits_title.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {t['text_primary']}; background: transparent;"
        )
        self._info_frame_style(t)
        self._archive_badge.setStyleSheet(
            f"background-color: {t['warning']}; color: #FFFFFF; font-size: 10px;"
            "padding: 1px 8px; border-radius: 9px; border: none;"
        )
        mono_font = 'font-family: "Consolas, Monaco, monospace";'
        self._path_label.setStyleSheet(
            f"font-size: 11px; {mono_font} color: {t['text_primary']}; background: transparent;"
        )
        for cap, val, _caption in self._grid_pairs:
            _style_label(cap, dark, 10, "text_secondary")
            _style_label(val, dark, 11)
        self._replace_cap.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']};")
        self._batch_cap.setStyleSheet(f"font-size: 10px; color: {t['text_secondary']};")
        self._style_op_msg()
        for card in self._hit_cards:
            card.set_dark(dark)

    # ----------------------------- 内部 -----------------------------

    def _info_frame_style(self, t: dict[str, str]) -> None:
        """文件信息卡底色边框样式。"""
        card = self.findChild(QFrame, "infoCard")
        if card is not None:
            card.setStyleSheet(
                f"#infoCard {{ background-color: {t['bg_app']}; border: 1px solid {t['border']}; border-radius: 6px; }}"
            )

    def _style_op_msg(self) -> None:
        """按现有文本语义重刷消息颜色（主题切换用）。"""
        if self._op_msg.text():
            self.show_msg(self._op_msg.text())
        else:
            _style_label(self._op_msg, self._dark, 11, "text_secondary")

    def _rebuild_hits(self, hits: list[dict[str, object]]) -> None:
        """重建命中规则卡片列表（折叠时仅首卡保留完整明细）。"""
        while self._hits_layout.count():
            entry = self._hits_layout.takeAt(0)
            w = entry.widget()
            if w is not None:
                w.deleteLater()
        self._hit_cards.clear()
        for i, hit in enumerate(hits):
            card = _HitCard(self._dark, hit)
            card.setVisible(self._details_expanded or i == 0)
            self._hits_layout.insertWidget(i, card)
            self._hit_cards.append(card)

    def _toggle_expand(self) -> None:
        """展开/收起命中明细（收起时仅第一条显示完整内容）。"""
        showing = not self._details_expanded
        self._details_expanded = showing
        self._expand_btn.setText("收起" if showing else "展开")
        for i, card in enumerate(self._hit_cards):
            card.setVisible(showing or i == 0)


class ResultsPage(QWidget):
    """扫描结果页：左侧结果清单 + 右侧命中详情面板。

    :param controller: :class:`AppController` 主控制器
    """

    # 由 MainWindow 订阅：返回文件扫描页
    backRequested = Signal()

    # 重绑 ScanController 时需续订的信号名（选中/详情/状态/恢复态）
    _SC_SIGNALS: tuple[str, ...] = (
        "selectedResultChanged",
        "detailHitsModelChanged",
        "statusChanged",
        "scanStateChanged",
        "scanProgressChanged",
        "restoringChanged",
    )

    def __init__(self, controller: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._wc = controller.workspace
        self._dark = False
        self._connected_sc: object | None = None
        self._updating_selection = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)

        # ---------- 标题区 ----------
        header = QHBoxLayout()
        header.setSpacing(12)
        back_btn = QPushButton(" 返回")
        back_btn.setProperty("variant", "secondary")
        back_btn.clicked.connect(self.backRequested.emit)
        self._title_label = QLabel("扫描结果")
        status_lbl = QLabel()
        self._status_label = status_lbl
        header.addWidget(back_btn)
        header.addWidget(self._title_label)
        header.addWidget(status_lbl)
        header.addStretch()
        matched_lbl = QLabel()
        self._matched_label = matched_lbl
        header.addWidget(matched_lbl)
        root.addLayout(header)

        # ---------- 过滤+排序工具栏 ----------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("搜索文件路径…")
        self._filter_debounce = QTimer(self)
        self._filter_debounce.setSingleShot(True)
        self._filter_debounce.setInterval(300)
        self._filter_debounce.timeout.connect(self._on_filter_text_changed)
        self._filter_input.textEdited.connect(lambda _text: self._filter_debounce.start())
        toolbar.addWidget(self._filter_input, stretch=1)

        self._sev_combo = QComboBox()
        self._sev_combo.addItems(["全部", "严重", "警告", "信息"])
        toolbar.addWidget(self._sev_combo)

        self._sort_field_combo = QComboBox()
        self._sort_field_combo.addItems(["默认顺序", "文件路径", "命中数", "严重度"])
        # 默认按严重度排序
        self._sort_field_combo.setCurrentIndex(3)
        toolbar.addWidget(self._sort_field_combo)

        self._sort_order_combo = QComboBox()
        self._sort_order_combo.addItems(["升序", "降序"])
        # 默认降序（严重 → 轻微）
        self._sort_order_combo.setCurrentIndex(1)
        toolbar.addWidget(self._sort_order_combo)

        reset_btn = QPushButton("重置排序")
        reset_btn.setProperty("variant", "secondary")
        reset_btn.clicked.connect(self.reset_sort)
        toolbar.addWidget(reset_btn)

        count_lbl = QLabel()
        self._count_label = count_lbl
        toolbar.addWidget(count_lbl)
        for combo in (self._sev_combo, self._sort_field_combo, self._sort_order_combo):
            combo.currentIndexChanged.connect(self._apply_sort_and_filter)
        root.addLayout(toolbar)

        # ---------- 已替换维度 Tab：待处理 / 已替换 / 全部 ----------
        self._tabs = QTabBar()
        for label in ("待处理", "已替换", "全部"):
            self._tabs.addTab(label)
        # 默认「待处理」：避免自动替换项与未替换项混在一起
        self._tabs.setCurrentIndex(0)
        self._tabs.currentChanged.connect(self._on_replaced_tab_changed)
        tabs_row = QHBoxLayout()
        tabs_row.addWidget(self._tabs)
        tabs_row.addStretch()
        root.addLayout(tabs_row)

        # ---------- 主体：左右分栏 ----------
        body = QHBoxLayout()
        body.setSpacing(12)

        left_frame = QFrame(objectName="resultPanel")
        lp = QVBoxLayout(left_frame)
        lp.setContentsMargins(8, 8, 8, 8)
        self._list_view = QListView()
        self._list_view.setFrameShape(QFrame.NoFrame)
        self._list_view.setItemDelegate(_ResultDelegate(self._dark))
        self._list_view.setUniformItemSizes(True)
        self._list_view.setSelectionMode(QListView.SingleSelection)
        self._list_view.verticalScrollBar().valueChanged.connect(lambda _v: self._report_visible_range())
        self._list_view.clicked.connect(self._on_row_clicked)
        self._delegate: _ResultDelegate = self._list_view.itemDelegate()  # type: ignore[assignment]
        lp.addWidget(self._list_view)
        body.addWidget(left_frame, stretch=5)

        self._detail_panel = _DetailPanel(self, self._dark)
        body.addWidget(self._detail_panel, stretch=6)
        root.addLayout(body, stretch=1)
        self._left_frame = left_frame

        # ---------- 恢复中占位 / 空态提示 ----------
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(120)
        self._spinner_timer.timeout.connect(self._tick_spinner)
        self._spinner_frame = 0

        # ---------- 数据接线 ----------
        self._wc.currentWorkspaceChanged.connect(self._rebind)
        self._rebind()

    # ----------------------------- 主题 -----------------------------

    def set_dark(self, dark: bool) -> None:
        """主题切换：整页样式刷新。"""
        if self._dark == dark:
            return
        self._dark = dark
        t = _tokens(dark)
        self._title_label.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {t['text_primary']};")
        _style_label(self._status_label, dark, 12, "text_secondary")
        self._matched_label.setStyleSheet(f"font-size: 12px; color: {t['danger']}; background: transparent;")
        _style_label(self._count_label, dark, 12, "text_secondary")
        self._delegate.set_dark(dark)
        self._list_view.viewport().update()
        self._refresh_left_frame_style(t)
        self._detail_panel.set_dark(dark)

    def _refresh_left_frame_style(self, t: dict[str, str]) -> None:
        """结果清单面板底色边框样式。"""
        self._left_frame.setStyleSheet(
            f"#resultPanel {{ background-color: {t['bg_card']}; border: 1px solid {t['border']}; border-radius: 8px; }}"
        )

    # ----------------------------- 数据绑定 -----------------------------

    def connected_scan_controller(self) -> object | None:
        """返回当前已绑定的 ScanController（供详情面板回读）。"""
        return self._connected_sc

    def _rebind(self) -> None:
        """当前工作区切换：重绑 ScanController、模型信号并回放过滤条件。"""
        old_sc = self._connected_sc
        if old_sc is not None:
            for name in self._SC_SIGNALS:
                with contextlib.suppress(RuntimeError, TypeError):
                    getattr(old_sc, name).disconnect(self._on_scan_signal)
            with contextlib.suppress(RuntimeError, AttributeError):
                old_sc.resultModel.dataChanged.disconnect(self._on_model_changed)
                old_sc.resultModel.modelReset.disconnect(self._on_model_changed)
            self._connected_sc = None

        has_ws = bool(self._wc.hasCurrentWorkspace)
        for w in (
            self._filter_input,
            self._sev_combo,
            self._sort_field_combo,
            self._sort_order_combo,
            self._tabs,
        ):
            w.setVisible(has_ws)
        self._left_frame.setVisible(has_ws)
        self._title_label.setVisible(True)
        sc = self._wc.currentScanController if has_ws else None
        if not has_ws or sc is None:
            self._status_label.setText("未选择任务，请从文件扫描页工作区卡片点击「查看结果」")
            self._matched_label.hide()
            self._count_label.hide()
            self._detail_panel.refresh(None)
            return

        self._connected_sc = sc
        model = sc.resultModel
        model.dataChanged.connect(self._on_model_changed)
        model.modelReset.connect(self._on_model_changed)
        for name in self._SC_SIGNALS:
            with contextlib.suppress(RuntimeError, TypeError):
                getattr(sc, name).connect(self._on_scan_signal)

        # 回放本页过滤条件到新控制器（重置已替换 Tab 为「待处理」）
        self._tabs.blockSignals(True)
        self._tabs.setCurrentIndex(0)
        self._tabs.blockSignals(False)
        self._push_filters_to_sc(sc)

        self._refresh_header(sc)
        self._refresh_left_list(model)
        self._detail_panel.refresh(sc)

    def _push_filters_to_sc(self, sc: object) -> None:
        """把工具栏控件当前值应用到 ScanController。"""
        severities: list[str] = []
        if self._sev_combo.currentIndex() == 1:
            severities = ["严重"]
        elif self._sev_combo.currentIndex() == 2:
            severities = ["警告"]
        elif self._sev_combo.currentIndex() == 3:
            severities = ["信息"]
        field = ("default", "filePath", "hitsCount", "severity")[self._sort_field_combo.currentIndex()]
        ascending = self._sort_order_combo.currentIndex() == 0
        sc.setResultFilterText(self._filter_input.text())
        sc.setResultFilterSeverities(severities)
        sc.setResultSort(field, ascending)
        # Tab 索引 → 已 replaced 过滤值：0 待处理→1 / 1 已替换→2 / 2 全部→0
        replaced_value = (1, 2, 0)[self._tabs.currentIndex()]
        sc.setResultFilterReplaced(replaced_value)

    # ----------------------------- 控件回调 -----------------------------

    def _on_filter_text_changed(self) -> None:
        """搜索框防抖到期：应用文本过滤。"""
        if self._connected_sc is not None:
            self._connected_sc.setResultFilterText(self._filter_input.text())

    def _apply_sort_and_filter(self) -> None:
        """严重度/排序组合变化：统一下发。"""
        if self._connected_sc is not None:
            self._push_filters_to_sc(self._connected_sc)

    def reset_sort(self) -> None:
        """重置排序字段为严重度降序（不影响过滤条件）。"""
        self._sort_field_combo.blockSignals(True)
        self._sort_order_combo.blockSignals(True)
        self._sort_field_combo.setCurrentIndex(3)
        self._sort_order_combo.setCurrentIndex(1)
        self._sort_field_combo.blockSignals(False)
        self._sort_order_combo.blockSignals(False)
        if self._connected_sc is not None:
            self._connected_sc.setResultSort("severity", False)

    def _on_replaced_tab_changed(self, index: int) -> None:
        """切换「待处理/已替换/全部」Tab。"""
        if self._connected_sc is not None:
            self._connected_sc.setResultFilterReplaced((1, 2, 0)[index])

    # ----------------------------- 清单交互 -----------------------------

    def _on_model_changed(self, *args: object) -> None:
        """结果模型变更：刷新计数标签与可视范围上报。"""
        del args
        sc = self._connected_sc
        if sc is None:
            return
        self._count_label.setText(f"{sc.resultFilteredCount} / {sc.resultTotalCount}")
        QTimer.singleShot(0, self._report_visible_range)

    def _on_scan_signal(self) -> None:
        """ScanController 信号汇聚：页头/清单/详情全量重读。"""
        sc = self._connected_sc
        if sc is None:
            return
        try:
            self._refresh_header(sc)
            self._sync_selection(sc)
            self._detail_panel.refresh(sc)
            self._update_restore_hint(bool(sc.restoring))
        except RuntimeError:
            # 控制器销毁瞬间的竞态：下一轮信号会再触发
            pass

    def _refresh_header(self, sc: object) -> None:
        """刷新标题行状态文案与命中计数。"""
        self._status_label.setText(f"（{sc.statusText}）")
        self._status_label.show()
        self._matched_label.setText(f"命中 {sc.matchedCount} 项")
        self._matched_label.show()
        self._count_label.setText(f"{sc.resultFilteredCount} / {sc.resultTotalCount}")
        self._count_label.show()

    def _refresh_left_list(self, model: QAbstractItemModel) -> None:
        """把结果模型挂到清单视图并按当前选中高亮。"""
        if self._list_view.model() is not model:
            self._list_view.setModel(model)
        self._on_scan_signal()

    def _on_row_clicked(self, index: QModelIndex) -> None:
        """点击行 → 选中对应结果。"""
        sc = self._connected_sc
        if sc is not None and index.isValid():
            sc.setSelectedResultIndex(index.row())

    def _sync_selection(self, sc: object) -> None:
        """控制器选中索引变化 → 清单高亮同步（防回环触发）。"""
        row = int(sc.selectedResultIndex)
        sm = self._list_view.selectionModel()
        if sm is None:
            return

        self._updating_selection = True
        try:
            if row >= 0:
                idx = self._list_view.model().index(row, 0)
                sm.setCurrentIndex(idx, QItemSelectionModel.ClearAndSelect)
                self._list_view.scrollTo(idx)
            else:
                sm.clearSelection()
        finally:
            self._updating_selection = False
        self._report_visible_range()

    def _report_visible_range(self) -> None:
        """向模型上报可视行范围启用虚拟化（大结果集才生效）。"""
        sc = self._connected_sc
        model = self._list_view.model()
        if sc is None or model is None or model.rowCount() == 0:
            return
        viewport = self._list_view.viewport()
        first = self._list_view.indexAt(viewport.rect().topLeft()).row()
        last = self._list_view.indexAt(viewport.rect().bottomLeft()).row()
        first = max(first, 0)
        if last < 0:
            last = min(first + 15, model.rowCount() - 1)
        with contextlib.suppress(RuntimeError, AttributeError):
            model.setVisibleRange(first, last)

    # ----------------------------- 恢复中占位 -----------------------------

    def _update_restore_hint(self, restoring: bool) -> None:
        """恢复中提示显隐与转圈动画开关（占位承载于详情面板空态）。"""
        show = bool(restoring) and self._connected_sc is not None
        if show:
            if not self._spinner_timer.isActive():
                self._spinner_timer.start()
            self._detail_panel.show_restore_hint(_SPINNER_FRAMES[self._spinner_frame])
        else:
            self._spinner_timer.stop()
            self._detail_panel.hide_restore_hint()

    def _tick_spinner(self) -> None:
        """恢复中转圈帧轮换并同步占位文案。"""
        self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
        if self._spinner_timer.isActive():
            self._detail_panel.show_restore_hint(_SPINNER_FRAMES[self._spinner_frame])

    # ----------------------------- 操作回调（供详情面板） -----------------------------

    def select_prev(self) -> None:
        """上一条结果。"""
        if self._connected_sc is not None:
            self._detail_panel.clear_msg()
            self._connected_sc.selectPrevResult()

    def select_next(self) -> None:
        """下一条结果。"""
        if self._connected_sc is not None:
            self._detail_panel.clear_msg()
            self._connected_sc.selectNextResult()

    def replace_selected(self) -> None:
        """替换当前选中结果内容（备份源文件后写入替换文本）。"""
        if self._connected_sc is not None:
            msg = self._connected_sc.replaceSelectedResult(self._detail_panel.replace_text())
            self._detail_panel.show_msg(msg)

    def move_to_staging(self) -> None:
        """移至暂存隔离目录。"""
        if self._connected_sc is not None:
            msg = self._connected_sc.moveSelectedToStaging()
            self._detail_panel.show_msg(msg)

    def mark_false_positive(self) -> None:
        """标记误报加入白名单。"""
        if self._connected_sc is not None:
            msg = self._connected_sc.markAsFalsePositive("")
            self._detail_panel.show_msg(msg)

    def replace_all_filtered(self) -> None:
        """对过滤后全部结果执行批量替换。"""
        if self._connected_sc is not None:
            msg = self._connected_sc.replaceAllFilteredResults(self._detail_panel.replace_text())
            self._detail_panel.show_msg(msg)

    def undo_batch(self) -> None:
        """撤销最近一次批量替换。"""
        if self._connected_sc is not None:
            msg = self._connected_sc.undoLastBatchReplace()
            self._detail_panel.show_msg(msg)

    def undo_current(self) -> None:
        """撤销当前选中结果的最近一次替换。"""
        if self._connected_sc is not None:
            msg = self._connected_sc.undoSelectedReplace()
            self._detail_panel.show_msg(msg)

    def open_location(self) -> None:
        """在文件管理器中打开/定位当前文件。"""
        if self._connected_sc is not None:
            self._connected_sc.openLocation()

    # ----------------------------- 资源回收 -----------------------------

    def closeEvent(self, event: object) -> None:
        """停止转圈与防抖定时器。"""
        self._spinner_timer.stop()
        self._filter_debounce.stop()
        super().closeEvent(event)  # type: ignore[arg-type]
