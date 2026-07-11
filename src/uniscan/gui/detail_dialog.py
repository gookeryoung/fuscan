"""扫描结果详情对话框。

双击结果树中的命中项时弹出，展示文件元信息、命中规则表与内容预览（高亮关键词）。
"""

from __future__ import annotations

import datetime
import html
import logging
import re
from typing import Sequence

from PySide2.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide2.QtWidgets import (
    QDialog,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QWidget,
)

from uniscan.extractors import extract_content
from uniscan.gui.detail_dialog_ui import Ui_HitDetailDialog
from uniscan.scanner.result import RuleHit, ScanResult

__all__ = ["HitDetailDialog"]

logger = logging.getLogger(__name__)

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
    """从命中规则的 detail 字段中提取关键词。

    detail 形如 "包含 'password'" / "正则命中: 'AKIA...'"，
    提取单引号内的模式用于内容高亮。
    """
    keywords: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        for match in _KEYWORD_RE.finditer(hit.detail):
            kw = match.group(1)
            if kw and kw not in seen:
                seen.add(kw)
                keywords.append(kw)
    return keywords


def _build_preview_html(content: str, keywords: Sequence[str]) -> str:
    """构建内容预览 HTML，关键词以黄色背景高亮。

    先对内容做 html.escape 转义，再用单次正则替换插入高亮 span，
    避免多次 replace 破坏已插入的 HTML 标签。
    """
    escaped = html.escape(content)
    if keywords:
        # 按长度降序排列，优先匹配最长关键词
        escaped_kws = sorted({html.escape(k) for k in keywords if k}, key=len, reverse=True)
        if escaped_kws:
            pattern = "|".join(re.escape(k) for k in escaped_kws)
            regex = re.compile(pattern, re.IGNORECASE)
            escaped = regex.sub(
                lambda m: f'<span style="{_HIGHLIGHT_STYLE}">{m.group(0)}</span>',
                escaped,
            )
    # 保留换行
    escaped = escaped.replace("\n", "<br>")
    return f"<pre style='{_PREVIEW_STYLE}'>{escaped}</pre>"


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
        self._hit_positions: list[tuple[int, int]] = []
        self._current_hit_index: int = -1
        self._bind_widgets()
        self._configure_ui()
        self._populate_file_info()
        self._populate_hits_table()
        self._populate_preview()

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
            f"<b>命中规则数:</b> {len(self._result.hits)}"
        )
        self._info_label.setText(info_html)

    def _populate_hits_table(self) -> None:
        """填充命中规则表。"""
        hits = self._result.hits
        self._hits_table.setRowCount(len(hits))
        for row, hit in enumerate(hits):
            self._hits_table.setItem(row, 0, QTableWidgetItem(hit.rule_name))
            self._hits_table.setItem(row, 1, QTableWidgetItem(hit.severity.value))
            self._hits_table.setItem(row, 2, QTableWidgetItem(hit.detail))

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
        self._find_hit_positions(keywords)
        if self._hit_positions:
            self._current_hit_index = 0
            self._highlight_current_hit()
            self._scroll_to_current_hit()
        self._update_nav_label()

    def _find_hit_positions(self, keywords: Sequence[str]) -> None:
        """在文档中查找所有关键词出现位置，按位置排序后存储。"""
        self._hit_positions = []
        if not keywords:
            return
        doc = self._preview.document()
        seen: set[tuple[int, int]] = set()
        for kw in sorted(set(keywords), key=len, reverse=True):
            cursor = doc.find(kw)
            while not cursor.isNull():
                pos = (cursor.selectionStart(), cursor.selectionEnd())
                if pos not in seen:
                    seen.add(pos)
                    self._hit_positions.append(pos)
                cursor = doc.find(kw, cursor)
        self._hit_positions.sort()

    def _highlight_current_hit(self) -> None:
        """用橙色背景高亮当前命中位置，区别于其他命中的黄色高亮。"""
        if self._current_hit_index < 0 or self._current_hit_index >= len(self._hit_positions):
            self._preview.setExtraSelections([])
            return
        start, end = self._hit_positions[self._current_hit_index]
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
        start, _ = self._hit_positions[self._current_hit_index]
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
