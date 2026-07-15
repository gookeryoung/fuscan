"""生成 fuscan 用户手册 PDF（iter-40）。

从 ``docs/manual.md`` 读取 Markdown 源，用 reportlab 解析为 PDF，
输出到 ``src/fuscan/assets/docs/fuscan-用户手册.pdf``。

中文字体使用 reportlab 内置 CID 字体 ``STSong-Light``（Adobe 亚洲字体包），
无需字体文件，跨平台一致。

使用::

    uv run python scripts/generate_manual_pdf.py

版本升级后须重新运行本脚本，确保随包分发的 PDF 与代码版本同步
（见 ``.trae/rules/rule-12-文档与版本发布.md``）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

# 项目根目录（脚本位于 scripts/ 下）
_ROOT = Path(__file__).resolve().parent.parent
_MANUAL_MD = _ROOT / "docs" / "manual.md"
_OUTPUT_DIR = _ROOT / "src" / "fuscan" / "assets" / "docs"
_OUTPUT_PDF = _OUTPUT_DIR / "fuscan-用户手册.pdf"

# 中文字体名（reportlab 内置 CID 字体，跨平台一致）
_FONT_CN = "STSong-Light"
_FONT_CN_BOLD = "STSong-Light"  # CID 字体无独立粗体，用同字体加粗渲染

# 颜色
_COLOR_PRIMARY = colors.HexColor("#2c5282")
_COLOR_MUTED = colors.HexColor("#666666")
_COLOR_BORDER = colors.HexColor("#e2e8f0")
_COLOR_CODE_BG = colors.HexColor("#f7fafc")
_COLOR_TABLE_HEADER = colors.HexColor("#edf2f7")


def _register_fonts() -> None:
    """注册中文字体。"""
    pdfmetrics.registerFont(UnicodeCIDFont(_FONT_CN))


def _extract_version(md_text: str) -> str:
    """从 Markdown 顶部 ``> 版本：x.y.z`` 行提取版本号。"""
    match = re.search(r"版本：\s*(\d+\.\d+\.\d+)", md_text)
    return match.group(1) if match else "unknown"


def _build_styles() -> dict[str, ParagraphStyle]:
    """构建段落样式集。"""
    base = getSampleStyleSheet()
    styles: dict[str, ParagraphStyle] = {}

    styles["title"] = ParagraphStyle(
        "title",
        parent=base["Title"],
        fontName=_FONT_CN_BOLD,
        fontSize=26,
        leading=32,
        alignment=TA_CENTER,
        textColor=_COLOR_PRIMARY,
        spaceAfter=8 * mm,
    )
    styles["subtitle"] = ParagraphStyle(
        "subtitle",
        parent=base["Normal"],
        fontName=_FONT_CN,
        fontSize=11,
        leading=16,
        alignment=TA_CENTER,
        textColor=_COLOR_MUTED,
        spaceAfter=6 * mm,
    )
    styles["h1"] = ParagraphStyle(
        "h1",
        parent=base["Heading1"],
        fontName=_FONT_CN_BOLD,
        fontSize=18,
        leading=24,
        textColor=_COLOR_PRIMARY,
        spaceBefore=10 * mm,
        spaceAfter=4 * mm,
        keepWithNext=True,
    )
    styles["h2"] = ParagraphStyle(
        "h2",
        parent=base["Heading2"],
        fontName=_FONT_CN_BOLD,
        fontSize=14,
        leading=20,
        textColor=_COLOR_PRIMARY,
        spaceBefore=6 * mm,
        spaceAfter=3 * mm,
        keepWithNext=True,
    )
    styles["h3"] = ParagraphStyle(
        "h3",
        parent=base["Heading3"],
        fontName=_FONT_CN_BOLD,
        fontSize=12,
        leading=18,
        textColor=colors.HexColor("#2d3748"),
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
        keepWithNext=True,
    )
    styles["body"] = ParagraphStyle(
        "body",
        parent=base["Normal"],
        fontName=_FONT_CN,
        fontSize=10.5,
        leading=17,
        textColor=colors.HexColor("#1a202c"),
        spaceAfter=2 * mm,
        alignment=TA_LEFT,
    )
    styles["bullet"] = ParagraphStyle(
        "bullet",
        parent=styles["body"],
        leftIndent=8 * mm,
        bulletIndent=3 * mm,
        spaceAfter=1 * mm,
    )
    styles["quote"] = ParagraphStyle(
        "quote",
        parent=styles["body"],
        leftIndent=6 * mm,
        textColor=_COLOR_MUTED,
        fontSize=10,
        leading=15,
        spaceAfter=3 * mm,
    )
    styles["code"] = ParagraphStyle(
        "code",
        parent=base["Code"],
        fontName="Courier",
        fontSize=9,
        leading=13,
        backColor=_COLOR_CODE_BG,
        borderColor=_COLOR_BORDER,
        borderWidth=0.5,
        borderPadding=4,
        spaceAfter=3 * mm,
        textColor=colors.HexColor("#1a202c"),
    )
    styles["table_header"] = ParagraphStyle(
        "table_header",
        parent=styles["body"],
        fontName=_FONT_CN_BOLD,
        fontSize=10,
        leading=14,
        textColor=colors.white,
        alignment=TA_CENTER,
    )
    styles["table_cell"] = ParagraphStyle(
        "table_cell",
        parent=styles["body"],
        fontSize=10,
        leading=14,
        spaceAfter=0,
    )
    return styles


def _escape_html(text: str) -> str:
    """转义 Markdown 内联代码中可能存在的 HTML 特殊字符。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_inline(text: str) -> str:
    """渲染行内 Markdown 标记（粗体、行内代码、链接）为 reportlab 支持的 HTML 子集。

    链接仅保留 http(s) 外部链接，锚点链接（``#xxx``）转为纯文本，
    避免 reportlab 将其当作未解析的内部目标抛错。
    """
    # 行内代码 `xxx` -> <font face="Courier">xxx</font>
    text = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', text)
    # 粗体 **xxx** -> <b>xxx</b>
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)

    # 链接 [text](url)：仅 http(s) 链接生成 <a>，其余转为纯文本
    def _link_repl(match: re.Match[str]) -> str:
        text_part, url = match.group(1), match.group(2)
        if url.startswith("http://") or url.startswith("https://"):
            return f'<a href="{url}">{text_part}</a>'
        return text_part

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link_repl, text)
    return text


def _parse_table(lines: list[str], styles: dict[str, ParagraphStyle]) -> Table:
    """解析 Markdown 表格行为 reportlab Table。"""
    rows: list[list[str]] = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)

    # 第二行是分隔符（|---|---|），跳过
    if len(rows) >= 2 and all(re.match(r"^[-:\s]+$", c) for c in rows[1]):
        del rows[1]

    # 转为 Paragraph 以支持中文换行
    data: list[list[Paragraph]] = []
    for row_idx, row in enumerate(rows):
        style = styles["table_header"] if row_idx == 0 else styles["table_cell"]
        data.append([Paragraph(_render_inline(c), style) for c in row])

    table = Table(data, hAlign="LEFT", colWidths=None)
    table.setStyle(
        TableStyle(
            [
                # 表头背景
                ("BACKGROUND", (0, 0), (-1, 0), _COLOR_PRIMARY),
                # 斑马纹
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _COLOR_TABLE_HEADER]),
                ("GRID", (0, 0), (-1, -1), 0.5, _COLOR_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _parse_markdown_block(
    lines: list[str],
    styles: dict[str, ParagraphStyle],
) -> tuple[list[Any], int]:
    """解析从当前位置开始的一个块（标题/代码/引用/表格/列表/段落）。

    :returns: (flowables, next_index)，next_index 指向下一个待解析行
    """
    # 单一职责：仅处理一种块类型，由 _parse_markdown 分发
    line = lines[0]
    stripped = line.strip()

    # 标题
    if stripped.startswith("### "):
        return [Paragraph(_render_inline(stripped[4:]), styles["h3"])], 1
    if stripped.startswith("## "):
        return [Paragraph(_render_inline(stripped[3:]), styles["h2"])], 1
    if stripped.startswith("# "):
        return [Paragraph(_render_inline(stripped[2:]), styles["h1"])], 1

    # 代码块
    if stripped.startswith("```"):
        code_lines: list[str] = []
        i = 1
        while i < len(lines) and not lines[i].strip().startswith("```"):
            code_lines.append(lines[i])
            i += 1
        return [Preformatted("\n".join(code_lines), styles["code"])], i + 1

    # 引用
    if stripped.startswith("> "):
        return [Paragraph(_render_inline(stripped[2:]), styles["quote"])], 1

    # 表格
    if "|" in stripped and stripped.startswith("|"):
        table_lines: list[str] = []
        i = 0
        while i < len(lines) and lines[i].strip().startswith("|"):
            table_lines.append(lines[i])
            i += 1
        return [_parse_table(table_lines, styles), Spacer(1, 2 * mm)], i

    # 无序列表
    if re.match(r"^[-*] ", stripped):
        content = _render_inline(stripped[2:])
        return [Paragraph(f"• {content}", styles["bullet"])], 1

    # 有序列表
    order_match = re.match(r"^(\d+)\.\s+(.*)", stripped)
    if order_match:
        content = _render_inline(order_match.group(2))
        return [Paragraph(f"{order_match.group(1)}. {content}", styles["bullet"])], 1

    # 普通段落
    return [Paragraph(_render_inline(stripped), styles["body"])], 1


def _parse_markdown(md_text: str, styles: dict[str, ParagraphStyle]) -> list[Any]:
    """解析 Markdown 文本为 reportlab story（flowables 列表）。

    首个分隔线之前是封面信息，由独立封面页代替，正文从首条 ``---`` 之后开始。
    """
    story: list[Any] = []
    lines = md_text.splitlines()
    i = 0
    started = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 首个分隔线之前是封面信息，跳过
        if not started:
            if stripped == "---":
                started = True
            i += 1
            continue

        # 分隔线渲染为水平分隔
        if stripped == "---":
            story.append(Spacer(1, 3 * mm))
            story.append(HRFlowable(width="100%", thickness=0.5, color=_COLOR_BORDER))
            story.append(Spacer(1, 3 * mm))
            i += 1
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        flowables, consumed = _parse_markdown_block(lines[i:], styles)
        story.extend(flowables)
        i += consumed

    return story


def _build_cover(version: str, styles: dict[str, ParagraphStyle]) -> list[Any]:
    """构建封面页 flowables。"""
    flowables: list[Any] = [
        Spacer(1, 50 * mm),
        Paragraph("fuscan", styles["title"]),
        Paragraph("极速通用文件扫描器", styles["subtitle"]),
        Spacer(1, 8 * mm),
        Paragraph(f"用户手册 · 版本 {version}", styles["subtitle"]),
        Spacer(1, 4 * mm),
        Paragraph("适用对象：初级用户 · GUI 图形界面", styles["subtitle"]),
        Spacer(1, 30 * mm),
        HRFlowable(width="60%", thickness=1, color=_COLOR_PRIMARY, hAlign="CENTER"),
    ]
    return flowables


def _on_page(canvas, doc) -> None:  # type: ignore[no-untyped-def]
    """页脚回调：绘制页码与版本号。"""
    canvas.saveState()
    canvas.setFont(_FONT_CN, 8)
    canvas.setFillColor(_COLOR_MUTED)
    # 左下：版本
    canvas.drawString(20 * mm, 10 * mm, f"fuscan 用户手册 · v{doc.version}")
    # 右下：页码
    page_text = f"第 {canvas.getPageNumber()} 页"
    canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, page_text)
    # 顶部细线
    canvas.setStrokeColor(_COLOR_BORDER)
    canvas.setLineWidth(0.3)
    canvas.line(20 * mm, A4[1] - 15 * mm, A4[0] - 20 * mm, A4[1] - 15 * mm)
    canvas.restoreState()


def _on_first_page(canvas, doc) -> None:  # type: ignore[no-untyped-def]
    """封面页不绘制页脚。"""
    pass


def main() -> int:
    """生成 PDF 主入口。

    :returns: 退出码，0 成功，1 源文件缺失
    """
    if not _MANUAL_MD.exists():
        print(f"错误：手册源文件不存在: {_MANUAL_MD}", file=sys.stderr)
        return 1

    md_text = _MANUAL_MD.read_text(encoding="utf-8")
    version = _extract_version(md_text)

    _register_fonts()
    styles = _build_styles()

    # 确保输出目录存在
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 构建 PDF
    doc = BaseDocTemplate(
        str(_OUTPUT_PDF),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"fuscan 用户手册 v{version}",
        author="fuscan",
        subject="GUI 用户手册",
    )
    doc.version = version  # type: ignore[attr-defined]

    # 封面模板（无页脚）+ 正文模板（有页脚）
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="normal",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    cover_template = PageTemplate(id="cover", frames=[frame], onPage=_on_first_page)
    body_template = PageTemplate(id="body", frames=[frame], onPage=_on_page)
    doc.addPageTemplates([cover_template, body_template])

    story: list[Any] = []
    # 封面
    story.extend(_build_cover(version, styles))
    story.append(PageBreak())
    # 切换到正文模板
    from reportlab.platypus import NextPageTemplate

    story.append(NextPageTemplate("body"))
    # 正文
    story.extend(_parse_markdown(md_text, styles))

    doc.build(story)
    print(f"已生成: {_OUTPUT_PDF} (版本 {version})")
    print(f"输出目录: {_OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
