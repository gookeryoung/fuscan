"""正则表达式测试工具对话框。

作为独立工具窗口提供正则表达式验证能力，匹配语义与扫描引擎
:func:`fuscan.scanner.matchers._apply_regex` 一致（同为 ``finditer`` 非重叠匹配），
支持捕获组、命名组显示，并附带常用语法速查手册。可由主窗口「工具」菜单
直接调用，也可由规则编辑器在编辑正则规则时调用并通过 ``initial_pattern``
预填待测表达式。
"""

from __future__ import annotations

import html
import logging
import re

try:
    from PySide2.QtCore import Slot
    from PySide2.QtWidgets import QDialog, QWidget
except ImportError:  # pragma: no cover
    from PySide6.QtCore import Slot  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import QDialog, QWidget  # pyrefly: ignore [missing-import]

from fuscan import theme
from fuscan.gui.regex_tester_ui import Ui_RegexTesterDialog

__all__ = ["RegexTesterDialog"]

logger = logging.getLogger(__name__)

# 测试文本字符上限：超出静默截断，防止超大文本 + 复杂正则导致 UI 卡顿
_MAX_TEXT_LEN = 100_000
# 命中展示条数上限：超出仅展示前 N 处并提示总数，避免命中爆炸拖垮结果视图
_MAX_DISPLAY_MATCHES = 1000

# 速查手册数据：(节标题, [(语法, 说明), ...])
_CHEATSHEET_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "字符类",
        [
            (".", "任意字符（不含换行，flags=re.S 可让其匹配换行）"),
            (r"\d  \D", "数字 / 非数字"),
            (r"\w  \W", "单词字符 [A-Za-z0-9_] / 非单词字符"),
            (r"\s  \S", "空白 / 非空白"),
            ("[abc]", "任一字符"),
            ("[a-z]", "范围"),
            ("[^abc]", "排除指定字符"),
        ],
    ),
    (
        "量词",
        [
            ("*", "0 或多次（贪婪）"),
            ("+", "1 或多次（贪婪）"),
            ("?", "0 或 1 次"),
            ("{n}", "恰好 n 次"),
            ("{n,}", "至少 n 次"),
            ("{n,m}", "n 到 m 次"),
            ("*?  +?  ??", "非贪婪（最小匹配）"),
        ],
    ),
    (
        "锚点",
        [
            ("^", "行首"),
            ("$", "行尾"),
            (r"\b  \B", "单词边界 / 非单词边界"),
        ],
    ),
    (
        "分组与捕获",
        [
            ("(...)", "捕获组（可用 \\1 反向引用）"),
            ("(?:...)", "非捕获组"),
            ("(?P<name>...)", "命名捕获组"),
            ("(?P=name)", "引用命名组"),
        ],
    ),
    (
        "零宽断言",
        [
            ("(?=...)", "正向先行断言"),
            ("(?!...)", "负向先行断言"),
            ("(?<=...)", "正向后行断言"),
            ("(?<!...)", "负向后行断言"),
        ],
    ),
    (
        "内联修饰符",
        [
            ("(?i)", "忽略大小写"),
            ("(?m)", "多行模式（^/$ 匹配每行）"),
            ("(?s)", ". 匹配换行"),
        ],
    ),
    (
        "常用示例",
        [
            (r"\d{4}-\d{2}-\d{2}", "日期 YYYY-MM-DD"),
            (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "邮箱"),
            (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "IPv4 地址"),
            (r"1[3-9]\d{9}", "中国手机号"),
            (r"\d{17}[\dXx]", "中国身份证号（18 位）"),
            (r"https?://[\w./?=&-]+", "URL"),
            (r"^-?\d+(?:\.\d+)?$", "整数或小数"),
            (r"[\u4e00-\u9fa5]+", "中文字符"),
            (r"#[0-9a-fA-F]{6}", "十六进制颜色码（如 #1A2B3C）"),
            (r"\d{1,3}(?:,\d{3})*", "千分位数字（如 1,234,567）"),
        ],
    ),
    (
        "进阶示例",
        [
            (r"AB(?!C)", "包含 AB 但其后不跟 C"),
            (r"(?<![A-Z])ABC", "ABC 且前面不是大写字母"),
            (r"\b\w{4,6}\b", "4-6 个字符的单词"),
            (r"(\d)\1+", "连续重复数字（如 111、222）"),
            (r"\b(\w+)\s+\1\b", "连续重复单词"),
            (r"(?<=\$)\d+(?:\.\d+)?", "$ 后的金额（如 $12.50）"),
            (r"<!--.*?-->", "HTML 注释"),
            (r"\b(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b", "0-255 的数字"),
            (r"(?m)^\s*#.*$", "行首注释（多行模式）"),
            (r"(?<!\d)\d{4}(?!\d)", "独立的 4 位数字（前后非数字）"),
            (r"[A-Z][a-zA-Z]*", "大写开头的单词"),
            (r"\b\w+\.\w+\b", "含点的标识符（如 file.txt）"),
            (r"(?<=\s)\d+(?=\s)", "空白分隔的数字"),
            (r"^(?:[a-z]+:)?//[^/]+", "协议相对 URL（如 //host）"),
        ],
    ),
]


def _build_cheatsheet_html() -> str:
    """构建速查手册 HTML，使用主题令牌着色。

    返回适用于 ``QTextEdit.setHtml`` 的 HTML 字符串：节标题用主色背景条，
    语法用等宽字体着色，说明用次级文字色。

    Qt 富文本引擎对 CSS 支持有限：``<span style>`` 混用 ``font-family`` 与
    ``color`` 时会丢弃样式，故用 ``<font color face>`` + ``<b>`` 替代；
    背景色用 ``<table bgcolor>`` 属性。
    """
    sections: list[str] = []

    for i, (title, entries) in enumerate(_CHEATSHEET_SECTIONS):
        if i > 0:
            sections.append("<br>")
        # 节标题：主色背景条（bgcolor 是 Qt 富文本支持的属性）
        sections.append(
            f'<table width="100%" bgcolor="{theme.COLOR_PRIMARY}" cellspacing="0" cellpadding="4">'
            "<tr><td><b>"
            f'<font color="{theme.COLOR_TEXT_ON_PRIMARY}">{html.escape(title)}</font>'
            "</b></td></tr></table>"
        )
        # 条目表：<font color face> 是 Qt 富文本最可靠的着色方式
        rows: list[str] = []
        for syntax, desc in entries:
            rows.append(
                "<tr>"
                f'<td style="padding: 2px 10px 2px 8px;">'
                f'<b><font color="{theme.COLOR_INFO}" face="Consolas">{html.escape(syntax)}</font></b>'
                "</td>"
                f'<td style="padding: 2px 4px;">'
                f'<font color="{theme.COLOR_TEXT_SECONDARY}">{html.escape(desc)}</font>'
                "</td>"
                "</tr>"
            )
        sections.append(f'<table cellspacing="0" cellpadding="0">{"".join(rows)}</table>')
    return (
        f'<div style="font-family: {theme.FONT_FAMILY}; font-size: {theme.FONT_SIZE_BODY};">{"".join(sections)}</div>'
    )


class RegexTesterDialog(QDialog, Ui_RegexTesterDialog):  # pyrefly: ignore [invalid-inheritance]
    """正则表达式测试工具对话框。

    独立工具窗口，提供正则表达式验证能力。匹配语义与扫描引擎
    :func:`fuscan.scanner.matchers._apply_regex` 一致：使用
    ``re.compile(...).finditer(text)`` 收集所有非重叠匹配，显示每个命中的
    位置、文本与捕获组。为防止超大文本或命中爆炸拖垮 UI，对测试文本长度
    与展示条数设上限。

    参数：
        parent：父窗口，可为 ``None`` 表示独立顶层窗口。
        initial_pattern：初始正则表达式，便于规则编辑器预填待测内容。
    """

    def __init__(self, parent: QWidget | None = None, initial_pattern: str = "") -> None:
        super().__init__(parent)
        self.setupUi(self)
        self.regex_cheatsheet_view.setHtml(_build_cheatsheet_html())

        self.regex_pattern_edit.textChanged.connect(self._on_test_regex)
        self.regex_test_text_edit.textChanged.connect(self._on_test_regex)
        self.regex_case_sensitive_check.stateChanged.connect(self._on_test_regex)

        if initial_pattern:
            self.regex_pattern_edit.setText(initial_pattern)

    @Slot()  # pyrefly: ignore [not-callable]
    def _on_test_regex(self) -> None:
        """重新编译正则并对测试文本执行匹配，显示命中结果。

        匹配语义与扫描引擎 :func:`fuscan.scanner.matchers._apply_regex` 一致：
        同为 ``re.compile(...).finditer(text)`` 非重叠匹配。本工具为展示全部
        命中使用 ``list(finditer)`` 物化，与引擎的迭代器+计数实现在内存行为
        上不同。
        """
        pattern = self.regex_pattern_edit.text().strip()
        if not pattern:
            self.regex_result_view.setPlainText("（请输入正则表达式）")
            return

        flags = 0 if self.regex_case_sensitive_check.isChecked() else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            self.regex_result_view.setPlainText(f"正则编译失败:\n{exc}")
            return

        text = self.regex_test_text_edit.toPlainText()
        if len(text) > _MAX_TEXT_LEN:
            text = text[:_MAX_TEXT_LEN]

        matches = list(compiled.finditer(text))
        if not matches:
            self.regex_result_view.setPlainText(f"未命中（扫描 {len(text)} 字符）")
            return

        lines = [f"共命中 {len(matches)} 处：", ""]
        for i, m in enumerate(matches[:_MAX_DISPLAY_MATCHES], 1):
            lines.append(f"[{i}] {m.start()}-{m.end()}: {m.group(0)!r}")
            if m.groups():
                lines.append(f"    捕获组: {m.groups()}")
            if m.groupdict():
                lines.append(f"    命名组: {m.groupdict()}")
        if len(matches) > _MAX_DISPLAY_MATCHES:
            lines.append("")
            lines.append(f"...仅展示前 {_MAX_DISPLAY_MATCHES} 处，共 {len(matches)} 处")

        self.regex_result_view.setPlainText("\n".join(lines))
