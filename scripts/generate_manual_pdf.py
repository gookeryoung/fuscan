"""生成 fuscan 用户速查表 PDF（A3 横版单页 cheatsheet）。

与 ``docs/manual.md``（详细在线手册）互补：本脚本生成紧凑的三栏速查表，
打印张贴或随包分发供用户快速查阅 GUI 操作。

布局：A3 横版（420×297mm），三栏 + 顶部标题栏 + 底部页脚，单页放不下时
自动溢出到第 2 页（设计上应控制在一页内）。

中文字体使用 reportlab 内置 CID 字体 ``STSong-Light``，跨平台一致。

版本号从 ``src/fuscan/__init__.py`` 的 ``__version__`` 动态读取，
不依赖 fuscan 包安装，确保 PDF 与代码版本始终同步。

使用::

    uv run python scripts/generate_manual_pdf.py

``bump-my-version`` 的 ``pre_commit_hooks`` 会在版本升级时自动调用本脚本，
重新生成 PDF 并将其暂存，随后并入 bump commit。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepInFrame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# 项目根目录
_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT_DIR = _ROOT / "src" / "fuscan" / "assets" / "docs"
_OUTPUT_PDF = _OUTPUT_DIR / "fuscan-用户手册.pdf"
_INIT_FILE = _ROOT / "src" / "fuscan" / "__init__.py"


def _read_version() -> str:
    """从 ``src/fuscan/__init__.py`` 解析 ``__version__``。

    直接读取源文件而非 import，避免脚本依赖 fuscan 包安装，
    确保在 bump hook 等未安装环境也能运行。
    """
    text = _INIT_FILE.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"无法从 {_INIT_FILE} 解析 __version__")
    return match.group(1)


_VERSION = _read_version()

# 中文字体
_FONT_CN = "STSong-Light"

# 配色（与 GUI 主题一致）
_C_TEXT = colors.HexColor("#24292E")
_C_MUTED = colors.HexColor("#586069")
_C_BORDER = colors.HexColor("#D0D7DE")
_C_BG_ZEBRA = colors.HexColor("#F6F8FA")
_C_BG_SECTION = colors.HexColor("#0366D6")


def _register_fonts() -> None:
    """注册中文字体。"""
    pdfmetrics.registerFont(UnicodeCIDFont(_FONT_CN))


def _styles() -> dict[str, ParagraphStyle]:
    """构建段落样式集（紧凑字号适配单页）。"""
    base = ParagraphStyle("base", fontName=_FONT_CN, fontSize=7.5, leading=10, textColor=_C_TEXT)
    return {
        "base": base,
        "section": ParagraphStyle(
            "section",
            parent=base,
            fontName=_FONT_CN,
            fontSize=8.5,
            leading=11,
            textColor=colors.white,
            spaceBefore=0,
            spaceAfter=0,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base,
            fontSize=7.5,
            leading=10,
            spaceAfter=1.5,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base,
            fontSize=7.5,
            leading=10,
            leftIndent=8,
            bulletIndent=2,
            spaceAfter=1,
        ),
        "kv": ParagraphStyle(
            "kv",
            parent=base,
            fontSize=7,
            leading=9,
            spaceAfter=0,
        ),
        "title": ParagraphStyle(
            "title",
            parent=base,
            fontName=_FONT_CN,
            fontSize=18,
            leading=22,
            textColor=colors.white,
            alignment=TA_LEFT,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base,
            fontSize=9,
            leading=12,
            textColor=colors.white,
            alignment=TA_LEFT,
        ),
        "footer": ParagraphStyle(
            "footer",
            parent=base,
            fontSize=7,
            leading=9,
            textColor=_C_MUTED,
            alignment=TA_CENTER,
        ),
        "hint": ParagraphStyle(
            "hint",
            parent=base,
            fontSize=6.5,
            leading=8.5,
            textColor=_C_MUTED,
        ),
    }


def _section(title: str, s: dict[str, ParagraphStyle]) -> Table:
    """区块标题条（主色背景 + 白字）。"""
    t = Table([[Paragraph(title, s["section"])]], colWidths=[None], rowHeights=[12])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _C_BG_SECTION),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return t


def _bullets(items: list[str], s: dict[str, ParagraphStyle]) -> list[Paragraph]:
    """项目符号段落列表。"""
    return [Paragraph(f"• {it}", s["bullet"]) for it in items]


def _kv_table(
    rows: list[tuple[str, str]], s: dict[str, ParagraphStyle], col_widths: list[float] | None = None
) -> Table:
    """键值两列表（斑马纹）。"""
    data = [[Paragraph(k, s["kv"]), Paragraph(v, s["kv"])] for k, v in rows]
    t = Table(data, colWidths=col_widths or [38 * mm, None], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, _C_BG_ZEBRA]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, _C_BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return t


def _three_col_table(rows: list[tuple[str, str, str]], s: dict[str, ParagraphStyle]) -> Table:
    """三列表（斑马纹）。"""
    data = [[Paragraph(c, s["kv"]) for c in row] for row in rows]
    t = Table(data, colWidths=[22 * mm, 30 * mm, None], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, _C_BG_ZEBRA]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, _C_BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return t


def _col(width: float, flowables: list[Any]) -> Table:
    """单栏容器（固定宽度，垂直堆叠 flowables）。"""
    t = Table([[flowables]], colWidths=[width])
    t.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return t


def _gap(h: float = 3) -> Spacer:
    return Spacer(1, h * mm)


def _build_left_col(s: dict[str, ParagraphStyle], w: float) -> Table:
    """左栏：新建任务 / 工作区按钮 / 扫描进度。"""
    items: list[Any] = []
    items.append(_section("1. 新建扫描任务", s))
    items.append(_gap())
    items.extend(
        _bullets(
            [
                "侧边栏「添加任务」→ 输入任务名（可空，自动生成）",
                "选模式：<b>全盘</b>（所有盘符）/ <b>盘符</b>（点选 C: D:）/ <b>文件夹</b>（最常用，可从历史路径快速填入）",
                "勾选「启用内置通用规则」+「添加规则文件」加载 YAML",
                "「创建任务」回首页",
            ],
            s,
        )
    )
    items.append(_gap(2))

    items.append(_section("2. 工作区卡片按钮", s))
    items.append(_gap())
    items.append(
        _three_col_table(
            [
                ("定义规则", "任意状态", "编辑该工作区规则集"),
                ("启动扫描", "未扫描中", "开始扫描"),
                ("暂停", "扫描中", "暂停当前扫描"),
                ("更新扫描", "已完成", "重新扫描"),
                ("查看结果", "已完成", "进入结果页"),
                ("统计", "任意", "扫描指标"),
                ("展开", "任意", "切换目标/导出/设置/删除"),
            ],
            s,
        )
    )
    items.append(_gap(2))

    items.append(_section("3. 扫描进度面板", s))
    items.append(_gap())
    items.extend(
        _bullets(
            [
                "双进度条：<b>收集</b>（发现/白名单跳过/用户跳过）+ <b>解析</b>（已扫/总数/命中/错误/速度）",
                "当前文件名 + 已用时",
                "「暂停」/「继续」可切换；暂停态点「启动扫描」也可恢复",
                "<b>取消加速</b>：取消后立即取消未启动任务，百毫秒内退出",
                "状态色：就绪=蓝 扫描中=黄 已暂停=灰 完成=红(有命中)/绿(无)",
            ],
            s,
        )
    )
    return _col(w, items)


def _build_mid_col(s: dict[str, ParagraphStyle], w: float) -> Table:
    """中栏：查看结果 / 详情操作 / 任务级配置。"""
    items: list[Any] = []
    items.append(_section("4. 查看扫描结果", s))
    items.append(_gap())
    items.extend(
        _bullets(
            [
                "左清单 + 右详情；顶部「← 返回」回首页",
                "过滤：路径搜索（不区分大小写）/ 严重度 / 排序字段 / 升降序 / 清除",
                "计数显示「过滤后 / 总数」",
                "单击清单项 → 右侧详情",
                "严重度：红=严重 橙=警告 蓝=信息",
            ],
            s,
        )
    )
    items.append(_gap(2))

    items.append(_section("5. 详情区操作", s))
    items.append(_gap())
    items.append(
        _kv_table(
            [
                ("◀ 上一条 / 下一条 ▶", "命中导航"),
                ("移至暂存", "隔离并标记跳过（压缩包禁用）"),
                ("替换内容", "备份 .bak，按 replace_with 替换"),
                ("全部替换", "对过滤后全部命中批量替换"),
                ("撤销批量", "从 .bak 恢复最近一次全部替换"),
                ("撤销当前", "从 .bak 恢复当前文件最近替换"),
                ("定位", "文件管理器中打开并选中"),
            ],
            s,
            col_widths=[40 * mm, w - 40 * mm],
        )
    )
    items.append(_gap(0.5))
    items.append(Paragraph("备份目录：<font face='Courier'>~/.fuscan/backup/</font>；压缩包条目不支持替换", s["hint"]))
    items.append(_gap(2))

    items.append(_section("6. 任务级配置覆盖", s))
    items.append(_gap())
    items.append(Paragraph("入口：工作区卡片 → 展开 → 设置（仅对该工作区生效，未覆盖回退全局）", s["body"]))
    items.append(
        _kv_table(
            [
                ("扫描压缩包", "ZIP/RAR/7Z 内文件"),
                ("并发线程数", "1-16，0=单线程"),
                ("大文件跳过阈值", "1B - 500MB"),
                ("扫描深度", "空=不限"),
                ("忽略目录", "每行一个目录名"),
                ("压缩包密码", "加密包专属密码"),
            ],
            s,
            col_widths=[36 * mm, w - 36 * mm],
        )
    )
    items.append(_gap())
    items.append(Paragraph("持久化到 <font face='Courier'>~/.fuscan/workspaces.json</font>，重启自动恢复", s["hint"]))
    return _col(w, items)


def _build_right_col(s: dict[str, ParagraphStyle], w: float) -> Table:
    """右栏：规则 / 字体 / 快捷键 / FAQ / 关于。"""
    items: list[Any] = []
    items.append(_section("7. 规则管理", s))
    items.append(_gap())
    items.extend(
        _bullets(
            [
                "工作区卡片 →「定义规则」进入，<b>仅作用于当前工作区</b>",
                "「加载规则」选 YAML；右键上移/下移/移除（Delete）",
                "后加载覆盖先加载同名规则",
                "「编辑规则」打开编辑器；底部正则验证面板（速查+测试+捕获组）",
            ],
            s,
        )
    )
    items.append(_gap(2))

    items.append(_section("8. 字体设置", s))
    items.append(_gap())
    items.extend(
        _bullets(
            [
                "侧边栏「设置」→ 字体/字号/最小字号/加粗",
                "实时应用到整个界面；「重置」恢复默认",
                "字号档：caption=base-2 small=base-1 body=base heading=base+2 title=base+4",
            ],
            s,
        )
    )
    items.append(_gap(2))

    items.append(_section("9. 快捷键", s))
    items.append(_gap())
    items.append(
        _kv_table(
            [
                ("F1", "打开用户手册"),
                ("Ctrl+O", "加载规则文件"),
                ("Ctrl+S", "开始扫描"),
                ("Ctrl+,", "打开设置"),
                ("F3 / Shift+F3", "下一条 / 上一条命中"),
                ("Delete", "移除选中规则文件"),
            ],
            s,
            col_widths=[28 * mm, w - 28 * mm],
        )
    )
    items.append(_gap(2))

    items.append(_section("10. 常见问题", s))
    items.append(_gap())
    items.append(
        _kv_table(
            [
                ("扫描慢", "缩范围 / 加并发 / 忽略目录 / 降大文件阈值"),
                ("界面卡顿", "超大文件读取，降大文件阈值"),
                ("找不到文件", "查忽略目录 / 深度 / 压缩包开关 / 大文件阈值"),
                ("加密包", "任务级设置填密码"),
                ("规则不生效", "定义规则检查加载 / 正则面板测试"),
                ("撤销替换", "详情底部撤销按钮，从 .bak 恢复"),
                ("重启后", "工作区持久化，运行时状态重置为就绪"),
                ("归档结果", "展开区 CSV / JSON 导出"),
            ],
            s,
            col_widths=[24 * mm, w - 24 * mm],
        )
    )
    items.append(_gap(2))

    items.append(_section("11. 关于页", s))
    items.append(_gap())
    items.extend(
        _bullets(
            [
                "版本 / 作者 / License / 第三方依赖",
                "「用户手册」打开本 PDF，「配置目录」打开 ~/.fuscan",
            ],
            s,
        )
    )
    return _col(w, items)


def _build_title_bar(s: dict[str, ParagraphStyle], page_w: float) -> Table:
    """顶部标题栏（主色背景 + 白字）。"""
    left = [
        Paragraph("fuscan 用户速查", s["title"]),
        Paragraph("极速通用文件扫描器 · GUI 图形界面操作速查表", s["subtitle"]),
    ]
    right = [
        Paragraph(f"<font color='white' size='9'>版本 {_VERSION}</font>", s["subtitle"]),
        Paragraph("<font color='white' size='7'>详细手册见：关于 → 用户手册</font>", s["subtitle"]),
    ]
    t = Table([[left, right]], colWidths=[page_w * 0.7, page_w * 0.3], rowHeights=[18 * mm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _C_BG_SECTION),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
                ("VALIGN", (1, 0), (1, 0), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ]
        )
    )
    return t


def _on_page(canvas, _doc) -> None:  # type: ignore[no-untyped-def]
    """页脚回调。"""
    canvas.saveState()
    canvas.setFont(_FONT_CN, 7)
    canvas.setFillColor(_C_MUTED)
    canvas.drawCentredString(
        landscape(A3)[0] / 2,
        5 * mm,
        f"fuscan 用户速查表 · v{_VERSION} · 配置目录 ~/.fuscan · 关于页可打开本 PDF",
    )
    canvas.restoreState()


def main() -> int:
    """生成速查表 PDF。"""
    _register_fonts()
    s = _styles()
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    page_w, page_h = landscape(A3)  # 420 × 297 mm
    margin = 8 * mm
    usable_w = page_w - 2 * margin
    usable_h = page_h - 2 * margin

    # 三栏布局
    gap = 4 * mm
    col_w = (usable_w - 2 * gap) / 3

    # 组装主体：标题栏 + 三栏（栏间距用空列实现）
    title = _build_title_bar(s, usable_w)
    left = _build_left_col(s, col_w)
    mid = _build_mid_col(s, col_w)
    right = _build_right_col(s, col_w)

    body_with_gap = Table(
        [[left, "", mid, "", right]],
        colWidths=[col_w, gap, col_w, gap, col_w],
        style=TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        ),
    )

    story: list[Any] = [title, Spacer(1, 3 * mm), body_with_gap]

    # 用 KeepInFrame 防止溢出（按比例缩放确保单页）
    frame = Frame(
        margin,
        margin,
        usable_w,
        usable_h,
        id="main",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        showBoundary=0,
    )
    template = PageTemplate(id="cheatsheet", frames=[frame], onPage=_on_page)

    doc = BaseDocTemplate(
        str(_OUTPUT_PDF),
        pagesize=landscape(A3),
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=f"fuscan 用户速查表 v{_VERSION}",
        author="fuscan",
        subject="GUI 操作速查表",
    )
    doc.addPageTemplates([template])

    # KeepInFrame 模式=shrink：内容超出时按比例缩放，确保单页
    kif = KeepInFrame(maxWidth=usable_w, maxHeight=usable_h, content=story, mode="shrink")
    doc.build([kif])

    print(f"已生成: {_OUTPUT_PDF} (版本 {_VERSION}, A3 横版单页)")

    # 在 bump-my-version hook 中运行时，自动暂存生成的 PDF，
    # 使其并入 bump commit（hook 会设置 BVHOOK_NEW_VERSION 环境变量）。
    if os.environ.get("BVHOOK_NEW_VERSION"):
        subprocess.run(["git", "add", str(_OUTPUT_PDF)], cwd=_ROOT, check=True)
        print(f"已暂存（bump hook）: {_OUTPUT_PDF.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
