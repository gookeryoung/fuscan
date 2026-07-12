"""扫描结果详情对话框。

双击结果树中的命中项时弹出，展示文件元信息、命中规则表与内容预览（高亮关键词）。
"""

from __future__ import annotations

import datetime
import html
import logging
import re
from typing import Sequence

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide2.QtWidgets import (
    QDialog,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QWidget,
)

from fuscan.extractors import extract_content
from fuscan.gui.detail_dialog_ui import Ui_HitDetailDialog
from fuscan.rules.model import Severity
from fuscan.scanner.result import RuleHit, ScanResult

__all__ = ["HitDetailDialog"]

logger = logging.getLogger(__name__)

# 严重等级 → 中文标签
_SEVERITY_LABELS: dict[Severity, str] = {
    Severity.CRITICAL: "严重",
    Severity.WARNING: "警告",
    Severity.INFO: "一般",
}

# 严重等级 → 前景色（QColor）
_SEVERITY_COLORS: dict[Severity, QColor] = {
    Severity.CRITICAL: QColor("#d73a49"),
    Severity.WARNING: QColor("#f0883e"),
    Severity.INFO: QColor("#0366d6"),
}

# 内容预览最大字符数，避免大文件阻塞 UI
_PREVIEW_MAX_CHARS = 100 * 1024

# 从 detail 中提取关键词的正则，匹配单引号包裹的内容
_KEYWORD_RE = re.compile(r"'([^']+)'")

# 内容预览 pre 标签样式
_PREVIEW_STYLE = (
    "font-family: Consolas, 'Courier New', monospace; font-size: 12px; white-space: pre-wrap; word-wrap: break-word;"
)

# 关键词高亮 span 样式
_HIGHLIGHT_STYLE = "background-color: yellow; color: black;"


def _format_size(size: int) -> str:
    """将字节数格式化为人类可读字符串。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def _extract_keywords(hits: Sequence[RuleHit]) -> list[str]:
    """从命中规则中提取高亮关键词。

    优先使用 ``RuleHit.match_text``（原始匹配文本，无 repr 转义）；
    对于组合规则 ``match_text`` 为空时，回退到从 ``detail`` 中提取单引号包裹的内容。
    """
    keywords: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        kw = hit.match_text
        if not kw:
            # 组合规则无单一匹配文本，回退到 detail 解析
            for match in _KEYWORD_RE.finditer(hit.detail):
                kw = match.group(1)
                if kw:
                    break
        if kw and kw not in seen:
            seen.add(kw)
            keywords.append(kw)
    return keywords


def _build_preview_html(content: str, keywords: Sequence[str]) -> str:
    """构建内容预览 HTML，关键词以黄色背景高亮。

    先对内容做 html.escape 转义，再用单次正则替换插入高亮 span，
    避免多次 replace 破坏已插入的 HTML 标签。
    关键词中的换行符规范化为 ``\\s+`` 以支持跨行高亮。
    """
    escaped = html.escape(content)
    if keywords:
        kw_patterns: list[str] = []
        for kw in sorted({k for k in keywords if k}, key=len, reverse=True):
            escaped_kw = html.escape(kw)
            if re.search(r"[\r\n]", escaped_kw):
                # 包含换行符：分段转义，用 \s+ 连接以支持跨行高亮
                parts = [p for p in re.split(r"[\r\n]+", escaped_kw) if p]
                kw_patterns.append(r"\s+".join(re.escape(p) for p in parts))
            else:
                kw_patterns.append(re.escape(escaped_kw))
        if kw_patterns:
            pattern = "|".join(kw_patterns)
            regex = re.compile(pattern, re.IGNORECASE)
            escaped = regex.sub(
                lambda m: f'<span style="{_HIGHLIGHT_STYLE}">{m.group(0)}</span>',
                escaped,
            )
    # 保留换行
    escaped = escaped.replace("\n", "<br>")
    return f"<pre style='{_PREVIEW_STYLE}'>{escaped}</pre>"


def _build_keyword_to_rule_map(hits: Sequence[RuleHit]) -> dict[str, int]:
    """构建关键词到规则索引的映射，同一关键词仅归属首条规则。

    优先使用 ``RuleHit.match_text`` 作为关键词；为空时回退到从 ``detail``
    中提取单引号包裹的内容。同一关键词被多条规则命中时，仅归属到首条规则，
    避免同一位置被重复计数。
    """
    keyword_to_rule: dict[str, int] = {}
    for rule_idx, hit in enumerate(hits):
        kw = hit.match_text
        if not kw:
            for match in _KEYWORD_RE.finditer(hit.detail):
                kw = match.group(1)
                if kw:
                    break
        if kw and kw not in keyword_to_rule:
            keyword_to_rule[kw] = rule_idx
    return keyword_to_rule


def _compile_keyword_pattern(kw: str) -> str:
    """将关键词编译为正则模式字符串。

    关键词中的换行符（\\r\\n/\\r/\\n）规范化为 ``\\s+`` 以支持跨行匹配；
    其他字符按字面量转义。
    """
    if re.search(r"[\r\n]", kw):
        parts = [p for p in re.split(r"[\r\n]+", kw) if p]
        return r"\s+".join(re.escape(p) for p in parts)
    return re.escape(kw)


class HitDetailDialog(QDialog):
    """命中详情对话框。

    展示：

    - 文件路径、大小、修改时间
    - 命中规则表（规则名、严重等级、详情）
    - 文件内容预览，命中关键词高亮显示

    内容预览限制在 _PREVIEW_MAX_CHARS 以内，避免大文件阻塞 UI。
    """

    def __init__(self, result: ScanResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result = result
        self._ui = Ui_HitDetailDialog()
        self._ui.setupUi(self)
        self._hit_positions: list[tuple[int, int, int]] = []
        self._current_hit_index: int = -1
        self._bind_widgets()
        self._configure_ui()
        self._populate_file_info()
        # 先填充预览以计算高亮位置，再填充命中表（位置数列依赖位置数据）
        self._populate_preview()
        self._populate_hits_table()

    def _bind_widgets(self) -> None:
        """将 Ui_HitDetailDialog 的部件绑定到本类私有属性，保持业务逻辑兼容。"""
        ui = self._ui
        self._info_label = ui.info_label
        self._hits_table = ui.hits_table
        self._preview = ui.preview
        self._prev_btn = ui.prev_btn
        self._next_btn = ui.next_btn
        self._nav_label = ui.nav_label

    def _configure_ui(self) -> None:
        """配置 .ui 无法静态表达的动态属性与信号槽连接。"""
        # 命中规则表：列头拉伸模式、只读、整行选择
        self._hits_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._hits_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._hits_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._hits_table.cellClicked.connect(self._on_hits_row_clicked)

        # 命中导航按钮信号槽
        self._prev_btn.clicked.connect(self._on_prev_hit)
        self._next_btn.clicked.connect(self._on_next_hit)

        # 内容预览伸缩比例（预览区占更大空间）
        self._ui.main_layout.setStretch(0, 0)
        self._ui.main_layout.setStretch(1, 0)
        self._ui.main_layout.setStretch(2, 1)
        self._ui.main_layout.setStretch(3, 0)
        self._ui.main_layout.setStretch(4, 2)
        self._ui.main_layout.setStretch(5, 0)
        self._ui.main_layout.setStretch(6, 0)

    def _populate_file_info(self) -> None:
        """填充文件元信息。"""
        path = self._result.path
        size = self._result.size
        try:
            mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            mtime_str = mtime.strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            mtime_str = "无法获取"

        info_html = (
            f"<b>文件路径:</b> {html.escape(str(path))}<br>"
            f"<b>文件大小:</b> {_format_size(size)} ({size} 字节)<br>"
            f"<b>修改时间:</b> {html.escape(mtime_str)}<br>"
            f"<b>命中规则数:</b> {len(self._result.hits)} | <b>匹配条数:</b> {self._result.total_match_count}"
        )
        self._info_label.setText(info_html)

    def _populate_hits_table(self) -> None:
        """填充命中规则表。"""
        hits = self._result.hits
        self._hits_table.setRowCount(len(hits))
        # 统计每条规则在预览中的高亮位置数
        position_counts: dict[int, int] = {}
        for _, _, rule_idx in self._hit_positions:
            position_counts[rule_idx] = position_counts.get(rule_idx, 0) + 1
        for row, hit in enumerate(hits):
            self._hits_table.setItem(row, 0, QTableWidgetItem(hit.rule_name))
            sev_item = QTableWidgetItem("")
            sev_text = _SEVERITY_LABELS.get(hit.severity, hit.severity.value)
            sev_item.setText(sev_text)
            sev_item.setForeground(_SEVERITY_COLORS[hit.severity])
            self._hits_table.setItem(row, 1, sev_item)
            count_item = QTableWidgetItem(str(hit.match_count))
            count_item.setTextAlignment(Qt.AlignCenter)
            self._hits_table.setItem(row, 2, count_item)
            pos_item = QTableWidgetItem(str(position_counts.get(row, 0)))
            pos_item.setTextAlignment(Qt.AlignCenter)
            pos_item.setToolTip("该规则在预览中可高亮跳转的位置数")
            self._hits_table.setItem(row, 3, pos_item)
            self._hits_table.setItem(row, 4, QTableWidgetItem(hit.detail))

    def _populate_preview(self) -> None:
        """填充内容预览，命中关键词高亮并定位到首个命中。"""
        path = self._result.path
        truncated = False

        # 优先使用提取器（支持 PDF/DOCX 等格式），失败回退到纯文本
        try:
            content = extract_content(path)
        except Exception:
            logger.debug("提取器提取失败，回退到纯文本: %s", path, exc_info=True)
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                logger.warning("读取内容预览失败 %s", path, exc_info=True)
                self._preview.setPlainText(f"无法读取文件内容: {exc}")
                self._update_nav_label()
                return

        if not content:
            self._preview.setPlainText("(文件内容为空或为二进制)")
            self._update_nav_label()
            return

        # 截断过长内容
        if len(content) > _PREVIEW_MAX_CHARS:
            content = content[:_PREVIEW_MAX_CHARS]
            truncated = True

        keywords = _extract_keywords(self._result.hits)
        html_content = _build_preview_html(content, keywords)
        if truncated:
            html_content += "<p style='color: #888; font-size: 11px;'>(内容已截断，仅显示前 100KB)</p>"
        self._preview.setHtml(html_content)

        # 查找所有关键词位置并定位到首个命中
        self._find_hit_positions(self._result.hits)
        if self._hit_positions:
            self._current_hit_index = 0
            self._highlight_current_hit()
            self._scroll_to_current_hit()
        self._update_nav_label()

    def _find_hit_positions(self, hits: Sequence[RuleHit]) -> None:
        """在文档中查找所有关键词出现位置，按位置排序后存储。

        使用 Python :func:`re.finditer` 在 :meth:`toPlainText` 返回的纯文本上查找，
        避免 :meth:`QTextDocument.find` 无法跨越段落边界的限制。
        关键词中的换行符（\\r\\n/\\r/\\n）规范化为 ``\\s+`` 正则，支持跨行命中的定位。

        每个位置记录为 ``(start, end, rule_index)`` 三元组，``rule_index`` 为命中
        规则在 ``hits`` 中的索引，用于点击规则表行时跳转到对应高亮位置。
        同一关键词若被多条规则命中，仅归属到首条规则（避免位置重复计数）。
        """
        self._hit_positions = []
        if not hits:
            return
        plain = self._preview.toPlainText()
        if not plain:
            return
        keyword_to_rule = _build_keyword_to_rule_map(hits)
        seen: set[tuple[int, int]] = set()
        for kw, rule_idx in sorted(keyword_to_rule.items(), key=lambda x: len(x[0]), reverse=True):
            pattern = _compile_keyword_pattern(kw)
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error:
                continue
            for m in regex.finditer(plain):
                pos = (m.start(), m.end())
                if pos not in seen:
                    seen.add(pos)
                    self._hit_positions.append((m.start(), m.end(), rule_idx))
        self._hit_positions.sort()

    def _highlight_current_hit(self) -> None:
        """用橙色背景高亮当前命中位置，区别于其他命中的黄色高亮。"""
        if self._current_hit_index < 0 or self._current_hit_index >= len(self._hit_positions):
            self._preview.setExtraSelections([])
            return
        start, end, _ = self._hit_positions[self._current_hit_index]
        sel = QTextEdit.ExtraSelection()
        cursor = self._preview.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        sel.cursor = cursor
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 165, 0))
        sel.format = fmt
        self._preview.setExtraSelections([sel])

    def _scroll_to_current_hit(self) -> None:
        """滚动预览区域使当前命中位置可见。"""
        if self._current_hit_index < 0 or self._current_hit_index >= len(self._hit_positions):
            return
        start, _, _ = self._hit_positions[self._current_hit_index]
        cursor = self._preview.textCursor()
        cursor.setPosition(start)
        self._preview.setTextCursor(cursor)
        self._preview.ensureCursorVisible()

    def _on_prev_hit(self) -> None:
        """跳转到上一个命中位置。"""
        if not self._hit_positions:
            return
        self._current_hit_index = (self._current_hit_index - 1) % len(self._hit_positions)
        self._highlight_current_hit()
        self._scroll_to_current_hit()
        self._update_nav_label()

    def _on_next_hit(self) -> None:
        """跳转到下一个命中位置。"""
        if not self._hit_positions:
            return
        self._current_hit_index = (self._current_hit_index + 1) % len(self._hit_positions)
        self._highlight_current_hit()
        self._scroll_to_current_hit()
        self._update_nav_label()

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
        """更新导航标签与按钮状态。"""
        total = len(self._hit_positions)
        if total == 0:
            self._nav_label.setText("无命中")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
        else:
            self._nav_label.setText(f"{self._current_hit_index + 1} / {total}")
            self._prev_btn.setEnabled(True)
            self._next_btn.setEnabled(True)
