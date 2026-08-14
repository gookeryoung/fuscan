"""规则测试沙盒：对任意文本执行单条规则匹配，无需扫描真实文件。

供 GUI「规则测试」面板调用：用户输入测试文本、选择一条规则，即时查看
命中情况与命中文本，辅助规则编写与调试。本模块为纯函数模块，无 Qt 依赖，
便于在 pytest 下覆盖各 ``MatchSpec`` 类型（CONTENT/FILENAME/PATH/AND/OR/NOT）。
"""

from __future__ import annotations

from pathlib import Path

from fuscan.rules.model import Rule
from fuscan.scanner.context import FileEntry, MatchContext, _extract_extension
from fuscan.scanner.matchers import build_matcher
from fuscan.scanner.result import MatchResult

__all__ = ["match_rule_against_text"]


def match_rule_against_text(
    rule: Rule,
    text: str,
    filename: str = "test.txt",
) -> MatchResult:
    """对纯文本执行单条规则匹配，返回顶层聚合匹配结果。

    构造合成 :class:`FileEntry`（绕过 ``from_path`` 避免 stat 系统调用）与
    :class:`MatchContext`（``content_provider`` 闭包绑定入参 ``text``，忽略
    合成 entry），复用 :func:`build_matcher` + ``Matcher.matches`` 完成求值。
    FILENAME/PATH 规则对 ``filename`` 求值，CONTENT 规则对 ``text`` 求值。

    组合规则（AND/OR/NOT）的顶层 :class:`MatchResult` 已聚合所有子条件命中
    的 ``match_texts``，无需额外收集子结果。

    .. note::
       函数名避开 ``test_`` 前缀，防止 pytest 将其作为测试用例收集
      （被测试模块按名导入时会触发 collection）。

    :param rule: 待测试规则
    :param text: 测试文本（CONTENT 规则的匹配内容）
    :param filename: 合成文件名（FILENAME/PATH 规则的匹配目标，含扩展名）；
        默认 ``"test.txt"``，如需测试 dotfile 规则可传入 ``".env"`` 等
    :return: 顶层聚合 :class:`MatchResult`，``matched``/``detail``/``match_text``/
        ``match_count``/``match_texts``/``target`` 字段供上层序列化展示；
        规则正则编译失败或匹配规格类型未知时返回 ``matched=False`` 且
        ``detail`` 标注错误，绝不向调用方抛异常
    """
    path = Path(filename)
    # 复用 context._extract_extension 的 dotfile 感知逻辑（.env → "env"），
    # 保证合成 entry 的 extension 与真实扫描一致；extension 字段不被匹配器
    # 使用，仅为元信息完整性
    entry = FileEntry(
        path=path,
        name=path.name,
        size=len(text),
        mtime=0.0,
        extension=_extract_extension(path),
    )
    # content_provider 闭包绑定入参 text，忽略入参 entry（合成 entry 无真实文件）
    context = MatchContext(entry, content_provider=lambda _entry: text)
    try:
        matcher = build_matcher(rule.match)
    except (ValueError, TypeError) as exc:
        # 用户规则可能含非法正则或未知匹配规格，沙盒不应崩溃
        return MatchResult(matched=False, detail=f"规则编译失败: {exc}")
    return matcher.matches(context)
