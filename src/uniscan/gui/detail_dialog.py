"""扫描结果详情对话框。

双击结果树中的命中项时弹出，展示文件元信息、命中规则表与内容预览（高亮关键词）。
"""

from __future__ import annotations

import datetime
import html
import logging
import re
from typing import List, Optional, Sequence, Set

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from uniscan.extractors import extract_content
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


def _extract_keywords(hits: Sequence[RuleHit]) -> List[str]:
    """从命中规则的 detail 字段中提取关键词。

    detail 形如 "包含 'password'" / "正则命中: 'AKIA...'"，
    提取单引号内的模式用于内容高亮。
    """
    keywords: List[str] = []
    seen: Set[str] = set()
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

    def __init__(self, result: ScanResult, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._result = result
        self.setWindowTitle("命中详情")
        self.resize(800, 600)
        self._init_ui()
        self._populate_file_info()
        self._populate_hits_table()
        self._populate_preview()

    def _init_ui(self) -> None:
        """初始化对话框布局。"""
        layout = QVBoxLayout(self)

        # 文件信息区
        self._info_label = QLabel()
        self._info_label.setTextFormat(Qt.RichText)
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("padding: 8px; background: #f5f5f5; border: 1px solid #ddd;")
        layout.addWidget(self._info_label)

        # 命中规则表
        layout.addWidget(QLabel("命中规则:"))
        self._hits_table = QTableWidget()
        self._hits_table.setColumnCount(3)
        self._hits_table.setHorizontalHeaderLabels(["规则名", "严重等级", "详情"])
        self._hits_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._hits_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._hits_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self._hits_table, stretch=1)

        # 内容预览
        layout.addWidget(QLabel("内容预览 (关键词高亮):"))
        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        layout.addWidget(self._preview, stretch=2)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

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
        """填充内容预览，命中关键词高亮。"""
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
                return

        if not content:
            self._preview.setPlainText("(文件内容为空或为二进制)")
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
