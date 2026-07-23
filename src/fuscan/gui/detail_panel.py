"""扫描结果详情面板控制器。

将详情区的状态管理、内容填充、命中导航与文件操作从 ``main_window.py`` 拆分
到独立的 :class:`DetailPanel` 控制器，使主窗口仅负责创建控件、连接信号与
路由用户操作，详情区逻辑内聚到本模块。

公共 API：

- :class:`DetailControls`：详情区 UI 控件引用集合（frozen dataclass），由主窗口
  ``setupUi`` 后构造并传入 :class:`DetailPanel`
- :class:`DetailPanel`：详情面板控制器（:class:`QObject` 子类），封装所有详情区
  状态与方法，通过两个信号向外通信：
  - ``path_copy_requested(str)``：复制路径后通知主窗口更新状态栏
  - ``open_location_requested(object)``：请求主窗口定位文件（携带 ``Path``）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Sequence

try:
    from PySide2.QtCore import QObject, Qt, Signal
    from PySide2.QtGui import QColor, QTextCharFormat, QTextCursor
    from PySide2.QtWidgets import (
        QApplication,
        QHeaderView,
        QLabel,
        QPushButton,
        QStackedWidget,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
    )
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QObject, Qt, Signal  # pyrefly: ignore [missing-import]
    from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import (  # pyrefly: ignore [missing-import]
        QApplication,
        QHeaderView,
        QLabel,
        QPushButton,
        QStackedWidget,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
    )

from fuscan.extractors import extract_content_cached
from fuscan.gui.preview_utils import (
    PREVIEW_MAX_CHARS,
    SEVERITY_BACKGROUNDS,
    build_keyword_to_rule_map,
    build_preview_html,
    compile_keyword_pattern,
    extract_keywords,
    severity_text,
)
from fuscan.rules.model import Severity
from fuscan.scanner.result import RuleHit, ScanResult

__all__ = ["DetailControls", "DetailPanel"]

logger = logging.getLogger(__name__)


def _apply_severity_to_table_item(item: QTableWidgetItem, severity: Severity) -> None:
    """为 QTableWidgetItem 设置中文标签与背景色。

    仅设置背景色（浅红/浅橙/浅蓝），不设置前景色——避免 ``setForeground``
    覆盖 QSS ``::item:selected`` 的选中态白字（需求1：选中项字体统一白色）。
    """
    item.setText(severity_text(severity))
    item.setBackground(SEVERITY_BACKGROUNDS[severity])


@dataclass(frozen=True)
class DetailControls:
    """详情区 UI 控件引用集合，由主窗口 ``setupUi`` 后构造并传入 :class:`DetailPanel`。

    所有控件由主窗口的 ``Ui_MainWindow.setupUi`` 创建，本 dataclass 仅持有引用，
    不取得所有权。控件的生命周期由主窗口管理。
    """

    action_stack: QStackedWidget
    main_stack: QStackedWidget
    prev_btn: QPushButton
    next_btn: QPushButton
    nav_label: QLabel
    open_location_btn: QPushButton
    info_label: QLabel
    hits_table: QTableWidget
    preview: QTextEdit
    # iter-77：原 note_edit（备注/批注/导出说明）替换为操作按钮行
    move_to_staging_btn: QPushButton
    toggle_skip_btn: QPushButton


class DetailPanel(QObject):  # pyrefly: ignore [invalid-inheritance]
    """扫描结果详情面板控制器。

    封装详情区的状态管理（当前结果、命中位置列表、当前命中索引）、内容填充
    （文件信息、命中表、内容预览）、命中导航（上一条/下一条/行点击跳转）与
    文件操作（复制路径、打开位置、移动至暂存区、标记为跳过）。

    主窗口通过 :meth:`show_result` / :meth:`clear` 驱动详情区，通过 :attr:`current_result`
    读取当前选中结果，通过四个信号响应用户操作（复制路径/打开位置/移动至暂存区/切换跳过）。
    """

    # 复制路径后通知主窗口更新状态栏（携带路径字符串）
    path_copy_requested = Signal(str)
    # 请求主窗口在文件管理器中定位文件（携带 Path）
    open_location_requested = Signal(object)
    # 请求主窗口将当前文件移动到暂存区（携带 ScanResult，iter-77）
    move_to_staging_requested = Signal(object)
    # 请求主窗口切换当前文件的跳过标记（携带 ScanResult，iter-77）
    toggle_skip_requested = Signal(object)

    def __init__(self, controls: DetailControls, parent: QObject | None = None) -> None:
        """初始化详情面板：存储控件引用、初始化状态、配置命中表与信号连接。

        :param controls: 详情区 UI 控件引用集合
        :param parent: 父 QObject（通常为主窗口）
        """
        super().__init__(parent)
        self._c = controls
        # 详情区状态：当前结果、命中位置列表（start, end, rule_index）、当前命中索引
        self._current_result: ScanResult | None = None
        self._hit_positions: list[tuple[int, int, int]] = []
        self._current_hit_index: int = -1
        # 预览纯文本缓存：_find_hit_positions 时一次性取 toPlainText()，
        # 后续 _highlight/_scroll 导航复用，避免每次 F3 分配 100KB 字符串
        self._plain_text: str = ""
        self._setup_table()
        self._connect_signals()

    # ----------------------------- 公共 API -----------------------------

    @property
    def current_result(self) -> ScanResult | None:
        """当前详情区展示的扫描结果（无选中时为 ``None``）。"""
        return self._current_result

    def show_result(self, result: ScanResult) -> None:
        """在详情区展示选中项的详情，切换到非空态。

        :param result: 待展示的扫描结果
        """
        self._current_result = result
        self._c.action_stack.setCurrentIndex(1)
        self._c.main_stack.setCurrentIndex(1)
        # 先填充预览以计算高亮位置，再填充文件信息和命中表（均依赖位置数据）
        self._populate_preview(result)
        self._populate_file_info(result)
        self._populate_hits_table(result)
        # 强制刷新当前详情页，避免 Qt 渲染时序导致 stack 未生效
        self._c.main_stack.currentWidget().update()  # pyrefly: ignore [missing-argument]

    def clear(self) -> None:
        """清空详情区，切换到空态。"""
        self._c.action_stack.setCurrentIndex(0)
        self._c.main_stack.setCurrentIndex(0)
        self._current_result = None
        self._hit_positions = []
        self._current_hit_index = -1
        self._plain_text = ""
        self._c.preview.clear()
        self._c.hits_table.setRowCount(0)
        self._c.info_label.setText("")
        # iter-77：重置跳过按钮状态（空态下不可见，但保持一致避免下次显示残留）
        self.set_skip_state(False)

    def prev_hit(self) -> None:
        """跳转到上一个命中位置（循环）。"""
        if not self._hit_positions:
            return
        self._current_hit_index = (self._current_hit_index - 1) % len(self._hit_positions)
        self._highlight_current_hit()
        self._scroll_to_current_hit()
        self._update_nav_label()

    def next_hit(self) -> None:
        """跳转到下一个命中位置（循环）。"""
        if not self._hit_positions:
            return
        self._current_hit_index = (self._current_hit_index + 1) % len(self._hit_positions)
        self._highlight_current_hit()
        self._scroll_to_current_hit()
        self._update_nav_label()

    def copy_path(self) -> None:
        """复制当前结果路径到剪贴板，并发出 ``path_copy_requested`` 信号。

        无当前结果时直接返回，不复制也不发信号。
        """
        if self._current_result is None:
            return
        path_str = str(self._current_result.path)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(path_str)
        self.path_copy_requested.emit(path_str)  # pyrefly: ignore [missing-attribute]

    def open_location(self) -> None:
        """请求主窗口在文件管理器中定位当前结果文件。

        无当前结果时直接返回，不发信号。
        """
        if self._current_result is None:
            return
        self.open_location_requested.emit(self._current_result.path)  # pyrefly: ignore [missing-attribute]

    def set_skip_state(self, skipped: bool) -> None:
        """更新「标记为跳过」按钮的勾选状态与文案（iter-77）。

        :param skipped: True 表示当前文件已被用户标记跳过，按钮显示「取消跳过」；
            False 表示未标记，按钮显示「标记为跳过」
        """
        # blockSignals 避免setChecked 触发 toggled -> _on_toggle_skip_clicked -> emit
        # 造成主窗口 set_skip_state 调用与按钮状态同步的循环
        btn = self._c.toggle_skip_btn
        btn.blockSignals(True)
        btn.setChecked(skipped)
        btn.setText("取消跳过" if skipped else "标记为跳过")
        btn.blockSignals(False)

    def move_to_staging(self) -> None:
        """请求主窗口将当前结果文件移动到暂存区（iter-77）。

        无当前结果时直接返回，不发信号。
        """
        if self._current_result is None:
            return
        self.move_to_staging_requested.emit(self._current_result)  # pyrefly: ignore [missing-attribute]

    def toggle_skip(self) -> None:
        """请求主窗口切换当前结果文件的跳过标记（iter-77）。

        按钮的 checked 状态由主窗口在处理完成后通过 :meth:`set_skip_state` 同步，
        本方法仅发出信号，不在此处更新按钮状态——避免在主窗口处理失败时按钮状态
        与持久化存储不一致。无当前结果时直接返回，不发信号。
        """
        if self._current_result is None:
            return
        self.toggle_skip_requested.emit(self._current_result)  # pyrefly: ignore [missing-attribute]

    # ----------------------------- 内部实现 -----------------------------

    def _setup_table(self) -> None:
        """配置命中表：全列拉伸 + 行点击信号（editTriggers/selectionBehavior 已在 .ui 中声明）。"""
        self._c.hits_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)  # pyrefly: ignore [missing-argument]

    def _connect_signals(self) -> None:
        """连接详情区内部信号（按钮点击、命中表行点击）。"""
        self._c.prev_btn.clicked.connect(self.prev_hit)
        self._c.next_btn.clicked.connect(self.next_hit)
        self._c.hits_table.cellClicked.connect(self._on_hits_row_clicked)
        self._c.open_location_btn.clicked.connect(self.open_location)
        # iter-77：操作按钮行
        self._c.move_to_staging_btn.clicked.connect(self.move_to_staging)
        # toggled 信号在 setChecked 时触发（包括 blockSignals(False) 后的用户点击），
        # 用 clicked 避免与 set_skip_state 内的 setChecked 冲突；clicked 携带 checked 状态
        # 但我们以当前持久化状态为准，故连接到 toggle_skip 由主窗口决定下一步
        self._c.toggle_skip_btn.clicked.connect(self.toggle_skip)

    def _populate_file_info(self, result: ScanResult) -> None:
        """填充详情区文件元信息。"""
        # 文件信息 HTML 由 ScanResult.file_info_html 构造，本类仅追加自身状态字段
        extra = f"<b>可切换位置:</b> {len(self._hit_positions)}"
        self._c.info_label.setText(result.file_info_html(extra=extra))

    def _populate_hits_table(self, result: ScanResult) -> None:
        """填充详情区命中规则表。"""
        hits = result.hits
        logger.debug("填充命中表: %s, 命中数=%d", result.path, len(hits))
        self._c.hits_table.setRowCount(len(hits))
        # 统计每条规则在预览中的高亮位置数
        position_counts: dict[int, int] = {}
        for _, _, rule_idx in self._hit_positions:
            position_counts[rule_idx] = position_counts.get(rule_idx, 0) + 1
        for row, hit in enumerate(hits):
            self._c.hits_table.setItem(row, 0, QTableWidgetItem(hit.rule_name))
            sev_item = QTableWidgetItem("")
            _apply_severity_to_table_item(sev_item, hit.severity)
            self._c.hits_table.setItem(row, 1, sev_item)
            count_item = QTableWidgetItem(str(hit.match_count))
            count_item.setTextAlignment(Qt.AlignCenter)
            self._c.hits_table.setItem(row, 2, count_item)
            if hit.target == "filename":
                pos_item = QTableWidgetItem("-")
                pos_item.setToolTip("仅匹配文件名，无内容高亮位置")
            else:
                pos_item = QTableWidgetItem(str(position_counts.get(row, 0)))
                pos_item.setToolTip("该规则在预览中可高亮跳转的位置数")
            pos_item.setTextAlignment(Qt.AlignCenter)
            self._c.hits_table.setItem(row, 3, pos_item)
            detail_text = hit.detail
            if hit.target == "filename":
                detail_text = f"{detail_text}（仅文件名）"
            self._c.hits_table.setItem(row, 4, QTableWidgetItem(detail_text))
            # 描述列：来自 MatchSpec.description，可为空
            desc_item = QTableWidgetItem(hit.match_description)
            if hit.match_description:
                desc_item.setToolTip(hit.match_description)
            self._c.hits_table.setItem(row, 5, desc_item)

    def _populate_preview(self, result: ScanResult) -> None:
        """填充详情区内容预览，命中关键词高亮并定位到首个命中。"""
        path = result.path
        truncated = False

        # 优先使用提取器（支持 PDF/DOCX 等格式），失败回退到纯文本
        # 使用带缓存的版本：同一文件多次打开对话框/面板时不重复提取（需求2）
        try:
            content = extract_content_cached(path)
        except OSError as exc:
            logger.warning("读取内容预览失败 %s", path, exc_info=True)
            self._c.preview.setPlainText(f"无法读取文件内容: {exc}")
            self._update_nav_label()
            return

        if not content:
            self._c.preview.setPlainText("(文件内容为空或为二进制)")
            self._update_nav_label()
            return

        # 截断过长内容
        if len(content) > PREVIEW_MAX_CHARS:
            content = content[:PREVIEW_MAX_CHARS]
            truncated = True

        keywords = extract_keywords(result.hits)
        # 命中规则但无法提取关键词（如纯文件名/路径匹配），显示提示避免误判为"无命中"
        if not keywords and result.hits:
            rule_names = "、".join(h.rule_name for h in result.hits)
            self._c.preview.setPlainText(
                f"（此文件因【{rule_names}】规则命中，但无内容关键词可高亮。命中详情见上方表格。）"
            )
            self._hit_positions = []
            self._current_hit_index = -1
            self._update_nav_label()
            return
        html_content = build_preview_html(content, keywords)
        if truncated:
            html_content += "<p style='color: #888; font-size: 11px;'>(内容已截断，仅显示前 100KB)</p>"
        self._c.preview.setHtml(html_content)

        # 查找所有关键词位置并定位到首个命中
        self._find_hit_positions(result.hits)
        if self._hit_positions:
            self._current_hit_index = 0
            self._highlight_current_hit()
            self._scroll_to_current_hit()
        self._update_nav_label()

    def _find_hit_positions(self, hits: Sequence[RuleHit]) -> None:
        """在详情区预览文档中查找所有关键词出现位置，按位置排序后存储。

        使用 Python :func:`re.finditer` 在 :meth:`toPlainText` 返回的纯文本上查找，
        避免 :meth:`QTextDocument.find` 无法跨越段落边界的限制。
        关键词中的换行符（\\r\\n/\\r/\\n）规范化为 ``\\s+`` 正则，支持跨行命中的定位。

        每个位置记录为 ``(start, end, rule_index)`` 三元组，``rule_index`` 为命中
        规则在 ``hits`` 中的索引，用于点击规则表行时跳转到对应高亮位置。
        同一关键词若被多条规则命中，仅归属到首条规则（避免位置重复计数）。

        纯文本一次性缓存到 ``self._plain_text``，后续 :meth:`_highlight_current_hit`/
        :meth:`_scroll_to_current_hit` 复用，避免每次导航重复调用 ``toPlainText()``
        分配大字符串（100KB 文档每次约 0.1ms，F3 连续导航累积可感知）。
        """
        self._hit_positions = []
        if not hits:
            return
        # 缓存纯文本：_highlight/_scroll 复用，避免重复 toPlainText() 调用
        self._plain_text = self._c.preview.toPlainText()
        if not self._plain_text:
            return
        keyword_to_rule = build_keyword_to_rule_map(hits)
        seen: set[tuple[int, int]] = set()
        for kw, rule_idx in sorted(keyword_to_rule.items(), key=lambda x: len(x[0]), reverse=True):
            pattern = compile_keyword_pattern(kw)
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error:
                continue
            for m in regex.finditer(self._plain_text):
                pos = (m.start(), m.end())
                if pos not in seen:
                    seen.add(pos)
                    self._hit_positions.append((m.start(), m.end(), rule_idx))
        self._hit_positions.sort()

    def _highlight_current_hit(self) -> None:
        """用橙色背景高亮当前命中位置，区别于其他命中的黄色高亮。"""
        if self._current_hit_index < 0 or self._current_hit_index >= len(self._hit_positions):
            self._c.preview.setExtraSelections([])
            return
        start, end, _ = self._hit_positions[self._current_hit_index]
        doc_length = len(self._plain_text)
        if start >= doc_length or end > doc_length:
            self._c.preview.setExtraSelections([])
            return
        sel = QTextEdit.ExtraSelection()
        cursor = self._c.preview.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        sel.cursor = cursor
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 165, 0))
        sel.format = fmt
        self._c.preview.setExtraSelections([sel])

    def _scroll_to_current_hit(self) -> None:
        """滚动详情区预览使当前命中位置可见。"""
        if self._current_hit_index < 0 or self._current_hit_index >= len(self._hit_positions):
            return
        start, _, _ = self._hit_positions[self._current_hit_index]
        doc_length = len(self._plain_text)
        if start >= doc_length:
            return
        cursor = self._c.preview.textCursor()
        cursor.setPosition(start)
        self._c.preview.setTextCursor(cursor)
        self._c.preview.ensureCursorVisible()

    def _on_hits_row_clicked(self, row: int, _col: int) -> None:
        """点击命中规则表行，跳转到该规则对应的高亮位置。

        若当前已处于该规则的某个位置，则跳转到该规则的下一个位置（循环）；
        否则跳转到该规则的首个高亮位置。
        """
        if not self._hit_positions:
            return
        rule_indices = [i for i, (_, _, ri) in enumerate(self._hit_positions) if ri == row]
        if not rule_indices:
            return
        target = rule_indices[0]
        for i in rule_indices:
            if i > self._current_hit_index:
                target = i
                break
        self._current_hit_index = target
        self._highlight_current_hit()
        self._scroll_to_current_hit()
        self._update_nav_label()

    def _update_nav_label(self) -> None:
        """更新详情区导航标签与按钮状态。"""
        total = len(self._hit_positions)
        if total == 0:
            self._c.nav_label.setText("无命中")
            self._c.prev_btn.setEnabled(False)
            self._c.next_btn.setEnabled(False)
        else:
            self._c.nav_label.setText(f"{self._current_hit_index + 1} / {total}")
            self._c.prev_btn.setEnabled(True)
            self._c.next_btn.setEnabled(True)
