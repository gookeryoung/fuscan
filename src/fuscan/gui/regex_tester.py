"""正则表达式测试工具对话框。

作为独立工具窗口提供正则表达式验证能力，匹配语义与扫描引擎
:func:`fuscan.scanner.matchers._apply_regex` 一致（同为 ``finditer`` 非重叠匹配），
支持捕获组、命名组显示，并附带常用语法速查手册。可由主窗口「工具」菜单
直接调用，也可由规则编辑器在编辑正则规则时调用并通过 ``initial_pattern``
预填待测表达式。
"""

from __future__ import annotations

import logging
import re

try:
    from PySide2.QtCore import QTimer, Slot
    from PySide2.QtWidgets import QDialog, QWidget
except ImportError:  # pragma: no cover
    from PySide6.QtCore import QTimer, Slot  # pyrefly: ignore [missing-import]
    from PySide6.QtWidgets import QDialog, QWidget  # pyrefly: ignore [missing-import]

from fuscan.gui.regex_tester_ui import Ui_RegexTesterDialog

__all__ = ["RegexTesterDialog"]

logger = logging.getLogger(__name__)

# 输入触发的刷新防抖间隔（毫秒）：避免大文本+复杂正则时输入卡顿（rule-12 性能规则）
_DEBOUNCE_MS = 300
# 测试文本字符上限：超出截断，防止超大文本 + 复杂正则导致 UI 卡顿或内存膨胀
_MAX_TEXT_LEN = 100_000
# 命中展示条数上限：超出仅展示前 N 处并提示剩余数量，避免命中爆炸时占用 UI 资源
_MAX_DISPLAY_MATCHES = 1000

# 正则速查手册内容（Python re 模块常用语法）
_REGEX_CHEATSHEET = """\
== 字符类 ==
.          任意字符（不含换行，flags=re.S 可让其匹配换行）
\\d  \\D     数字 / 非数字
\\w  \\W     单词字符 [A-Za-z0-9_] / 非单词字符
\\s  \\S     空白 / 非空白
[abc]      任一字符
[a-z]      范围
[^abc]     排除指定字符

== 量词 ==
*          0 或多次（贪婪）
+          1 或多次（贪婪）
?          0 或 1 次
{n}        恰好 n 次
{n,}       至少 n 次
{n,m}      n 到 m 次
*? +? ??   非贪婪（最小匹配）

== 锚点 ==
^          行首
$          行尾
\\b  \\B     单词边界 / 非单词边界

== 分组与捕获 ==
(...)              捕获组（可用 \\1 反向引用）
(?:...)            非捕获组
(?P<name>...)      命名捕获组
(?P=name)          引用命名组

== 零宽断言 ==
(?=...)    正向先行断言
(?!...)    负向先行断言
(?<=...)   正向后行断言
(?<!...)   负向后行断言

== 内联修饰符 ==
(?i)       忽略大小写
(?m)       多行模式（^/$ 匹配每行）
(?s)       . 匹配换行

== 常用示例 ==
\\d{4}-\\d{2}-\\d{2}                                    日期 YYYY-MM-DD
\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b   邮箱
\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b                        IPv4 地址
1[3-9]\\d{9}                                          中国手机号
""".strip()


class RegexTesterDialog(QDialog, Ui_RegexTesterDialog):  # pyrefly: ignore [invalid-inheritance]
    """正则表达式测试工具对话框。

    独立工具窗口，提供正则表达式验证能力。匹配语义与扫描引擎
    :func:`fuscan.scanner.matchers._apply_regex` 一致：使用
    ``re.compile(...).finditer(text)`` 收集所有非重叠匹配，显示每个命中的
    位置、文本与捕获组。本工具为展示全部命中使用 ``list(finditer)`` 物化，
    与引擎的迭代器+计数实现在内存行为上不同；为防止超大文本或命中爆炸
    拖垮 UI，对测试文本长度与展示条数设上限。

    参数：
        parent：父窗口，可为 ``None`` 表示独立顶层窗口。
        initial_pattern：初始正则表达式，便于规则编辑器预填待测内容。
    """

    def __init__(self, parent: QWidget | None = None, initial_pattern: str = "") -> None:
        super().__init__(parent)
        self.setupUi(self)

        # 防抖定时器：textChanged/stateChanged 高频触发，间隔内仅末次生效
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._on_test_regex)

        if initial_pattern:
            self.regex_pattern_edit.setText(initial_pattern)

        self._connect_signals()

        # 预填 pattern 时立即同步执行一次，无需等待防抖
        if initial_pattern:
            self._on_test_regex()

    def _connect_signals(self) -> None:
        """配置 .ui 无法静态表达的动态属性、初始内容与信号槽连接。"""
        # 输入信号经防抖触发，避免每次按键都重算正则
        self.regex_pattern_edit.textChanged.connect(self._schedule_refresh)
        self.regex_test_text_edit.textChanged.connect(self._schedule_refresh)
        # case_sensitive 状态变化需重新编译（影响 flags），同样走防抖
        self.regex_case_sensitive_check.stateChanged.connect(self._schedule_refresh)
        # returnPressed 为用户显式触发（Enter 键），直接同步执行
        self.regex_pattern_edit.returnPressed.connect(self._on_test_regex)
        self.regex_cheatsheet_view.setPlainText(_REGEX_CHEATSHEET)

    def _schedule_refresh(self) -> None:
        """防抖触发：间隔内仅末次输入生效，避免大文本+复杂正则时卡顿。"""
        self._debounce_timer.start()  # pyrefly: ignore [missing-argument]

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

        case_sensitive = self.regex_case_sensitive_check.isChecked()
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            self.regex_result_view.setPlainText(f"正则编译失败:\n{exc}")
            return

        text = self.regex_test_text_edit.toPlainText()
        # 文本长度上限：防止超大文本 + 复杂正则导致 UI 卡顿
        truncated = len(text) > _MAX_TEXT_LEN
        if truncated:
            text = text[:_MAX_TEXT_LEN]

        matches = list(compiled.finditer(text))
        total = len(matches)

        lines: list[str] = []
        if truncated:
            lines.append(f"（测试文本已截断至 {_MAX_TEXT_LEN} 字符）")

        if not matches:
            lines.append(f"未命中（扫描 {len(text)} 字符）")
            self.regex_result_view.setPlainText("\n".join(lines))
            return

        lines.append(f"共命中 {total} 处：")
        lines.append("")
        # 命中展示上限：防止命中爆炸时占用过多内存与 UI 资源
        if total > _MAX_DISPLAY_MATCHES:
            lines.append(f"（仅展示前 {_MAX_DISPLAY_MATCHES} 处）")
            lines.append("")

        shown = matches[:_MAX_DISPLAY_MATCHES]
        for i, m in enumerate(shown, 1):
            start, end = m.span()
            lines.append(f"[{i}] {start}-{end}: {m.group(0)!r}")
            groups = m.groups()
            if groups:
                lines.append(f"    捕获组: {groups}")
            named = m.groupdict()
            if named:
                lines.append(f"    命名组: {named}")

        if total > _MAX_DISPLAY_MATCHES:
            lines.append("")
            lines.append(f"...还有 {total - _MAX_DISPLAY_MATCHES} 处未显示")

        self.regex_result_view.setPlainText("\n".join(lines))
