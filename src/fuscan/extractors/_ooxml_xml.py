"""OOXML (DOCX/PPTX) 直接 XML 解析工具。

使用 lxml (libxml2 C 扩展) 直接解析 OOXML ZIP 包内的 XML，
绕开 python-docx/python-pptx 的对象封装，提升 5-10x 解析速度。

性能对比（同硬件下解析 100MB XML）：

- python-docx/pptx：构造完整对象树，Python 层属性/方法/引用开销大
- lxml：直接 C 层遍历 XML 树，``iter()`` 按文档顺序访问节点

命名空间：

- DOCX: ``http://schemas.openxmlformats.org/wordprocessingml/2006/main`` (``w:``)
- PPTX: ``http://schemas.openxmlformats.org/drawingml/2006/main`` (``a:``)

XXE 防护：``XMLParser(resolve_entities=False, no_network=True)`` 禁用外部实体解析。
"""

from __future__ import annotations

import io
import logging
import zipfile

__all__ = ["extract_docx_text", "extract_pptx_text"]

logger = logging.getLogger(__name__)

# OOXML 命名空间
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

# 常用标签（带命名空间的 Clark 表示法）
_W_P = f"{{{_W_NS}}}p"
_W_T = f"{{{_W_NS}}}t"
_W_TAB = f"{{{_W_NS}}}tab"
_W_BR = f"{{{_W_NS}}}br"
_W_CR = f"{{{_W_NS}}}cr"
_A_T = f"{{{_A_NS}}}t"


def _make_parser() -> object:
    """构造 lxml XMLParser，禁用外部实体解析（XXE 防护）。

    :return: ``lxml.etree.XMLParser`` 实例
    """
    from lxml import etree

    return etree.XMLParser(resolve_entities=False, no_network=True, recover=True)


def extract_docx_text(data: bytes) -> str:
    """从 DOCX 字节流提取文本，使用 lxml 直接解析 XML。

    遍历 ``word/document.xml`` 中的段落 (``w:p``)，
    同时提取页眉 (``word/header*.xml``) 与页脚 (``word/footer*.xml``)。
    段落内所有 ``w:t`` 文本片段按文档顺序连接，段落间以换行分隔。

    :param data: DOCX 文件字节
    :return: 提取的文本，段落以换行分隔
    :raises zipfile.BadZipFile: 当 data 不是有效 ZIP
    :raises lxml.etree.XMLSyntaxError: 当 XML 严重损坏无法恢复
    """
    from lxml import etree

    parser = _make_parser()
    parts: list[str] = []

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        # document.xml 优先，其余 word/*.xml 按名称排序
        ordered: list[str] = []
        if "word/document.xml" in names:
            ordered.append("word/document.xml")
        for name in sorted(names):
            if name != "word/document.xml" and name.startswith("word/") and name.endswith(".xml"):
                ordered.append(name)

        for name in ordered:
            try:
                root = etree.fromstring(zf.read(name), parser=parser)
            except etree.XMLSyntaxError as exc:
                logger.debug("DOCX XML 解析跳过 %s: %s", name, exc)
                continue
            parts.extend(_extract_docx_root_paragraphs(root))

    return "\n".join(parts)


def _extract_docx_root_paragraphs(root: object) -> list[str]:
    """从 DOCX XML 根元素提取段落文本列表。

    遍历所有 ``w:p`` 段落元素，将段落内的 ``w:t``/``w:tab``/``w:br``/``w:cr``
    子元素按文档顺序拼接为段落文本。

    .. note::
       曾尝试过两种优化方向均未达预期，已撤销：

       - ``etree.iterparse`` 流式解析：在 36KB-140KB document.xml 上反慢 0.5x
         （事件分发开销超过收益，DOCX 通常 < 1MB，无大文档内存优势）
       - ``para.iter(_W_T, _W_TAB, _W_BR, _W_CR)`` 多标签 C 层过滤：在 140KB
         document.xml 上反慢 0.68x（lxml ``iter(*tags)`` 多标签 API 有额外开销，
         不比 Python 层 ``tag ==`` 比较快）

       当前 ``root.iter(_W_P)`` + ``para.iter()`` 全树遍历已是 C 层节点遍历，
       Python 层仅做 tag 字符串比较，对小段落（1-3 个子元素）开销本就很低。
       进一步优化需另寻方向（如 Cython/Rust 重写段落遍历）。

    :param root: ``lxml.etree._Element`` 根元素
    :return: 段落文本列表（已去除空白段落）
    """
    paragraphs: list[str] = []
    if root is None:
        # recover 模式下严重损坏的 XML 可能解析为 None，视为无内容
        return paragraphs
    for para in root.iter(_W_P):  # type: ignore[attr-defined]
        para_parts: list[str] = []
        for child in para.iter():  # type: ignore[attr-defined]
            tag = child.tag
            if tag == _W_T and child.text:
                para_parts.append(child.text)
            elif tag == _W_TAB:
                para_parts.append("\t")
            elif tag in (_W_BR, _W_CR):
                para_parts.append("\n")
        text = "".join(para_parts).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def extract_pptx_text(data: bytes) -> str:
    """从 PPTX 字节流提取文本，使用 lxml 直接解析 XML。

    按幻灯片编号顺序遍历 ``ppt/slides/slideN.xml``，
    提取所有 ``a:t`` 文本节点；若存在对应 ``ppt/notesSlides/notesSlideN.xml``
    则追加备注文本。空幻灯片不输出分隔符。

    :param data: PPTX 文件字节
    :return: 提取的文本，每张幻灯片以 ``--- 幻灯片 N ---`` 分隔
    :raises zipfile.BadZipFile: 当 data 不是有效 ZIP
    :raises lxml.etree.XMLSyntaxError: 当 XML 严重损坏无法恢复
    """
    from lxml import etree

    parser = _make_parser()
    parts: list[str] = []

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())

        # 收集幻灯片编号并按数字排序
        slide_numbers: list[int] = []
        for name in names:
            if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
                num_str = name[len("ppt/slides/slide") : -len(".xml")]
                if num_str.isdigit():
                    slide_numbers.append(int(num_str))
        slide_numbers.sort()

        for slide_num in slide_numbers:
            slide_name = f"ppt/slides/slide{slide_num}.xml"
            try:
                root = etree.fromstring(zf.read(slide_name), parser=parser)
            except etree.XMLSyntaxError as exc:
                logger.debug("PPTX 幻灯片解析跳过 %s: %s", slide_name, exc)
                continue

            slide_texts = _extract_pptx_root_texts(root)
            if slide_texts:
                parts.append(f"--- 幻灯片 {slide_num} ---")
                parts.extend(slide_texts)

            # 备注幻灯片
            notes_name = f"ppt/notesSlides/notesSlide{slide_num}.xml"
            if notes_name in names:
                try:
                    notes_root = etree.fromstring(zf.read(notes_name), parser=parser)
                except etree.XMLSyntaxError as exc:
                    logger.debug("PPTX 备注解析跳过 %s: %s", notes_name, exc)
                    continue
                notes_texts = _extract_pptx_root_texts(notes_root)
                if notes_texts:
                    parts.append(f"[备注] {' '.join(notes_texts)}")

    return "\n".join(parts)


def _extract_pptx_root_texts(root: object) -> list[str]:
    """从 PPTX XML 根元素提取所有 ``a:t`` 文本节点内容。

    :param root: ``lxml.etree._Element`` 根元素
    :return: 非空文本列表
    """
    texts: list[str] = []
    if root is None:
        # recover 模式下严重损坏的 XML 可能解析为 None，视为无内容
        return texts
    for t in root.iter(_A_T):  # type: ignore[attr-defined]
        if t.text and t.text.strip():
            texts.append(t.text)
    return texts
