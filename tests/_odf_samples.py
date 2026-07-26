"""测试用 ODF 样本生成工具。

iter-109 起 fuscan 移除 odfpy 依赖，改用标准库 ``zipfile`` +
``xml.etree.ElementTree`` 解析 ODT/ODS。测试样本改用 ``zipfile`` 手工
构造最小合法 ODF 包（``mimetype`` + ``content.xml``），不再依赖 odfpy。

ODF 包结构（最小集）：

- ``mimetype``：文件类型标识（必须为第一个文件，且 ``ZIP_STORED`` 不压缩）
- ``content.xml``：实际内容 XML

ODF 1.2 命名空间：

- office: ``urn:oasis:names:tc:opendocument:xmlns:office:1.0``
- text: ``urn:oasis:names:tc:opendocument:xmlns:text:1.0``
- table: ``urn:oasis:names:tc:opendocument:xmlns:table:1.0``
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable

__all__ = ["make_ods_sample", "make_odt_sample"]

# ODF 1.2 命名空间
_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"

# ODT mimetype
_ODT_MIMETYPE = b"application/vnd.oasis.opendocument.text"
# ODS mimetype
_ODS_MIMETYPE = b"application/vnd.oasis.opendocument.spreadsheet"

# ODT content.xml 头部（office:document-content + office:body + office:text）
_ODT_HEADER = (
    f'<?xml version="1.0" encoding="UTF-8"?>\n'
    f"<office:document-content "
    f'xmlns:office="{_OFFICE_NS}" '
    f'xmlns:text="{_TEXT_NS}" '
    f'office:version="1.2">\n'
    f"<office:body><office:text>\n"
)
_ODT_FOOTER = "</office:text></office:body></office:document-content>\n"

# ODS content.xml 头部（office:document-content + office:body + office:spreadsheet）
_ODS_HEADER = (
    f'<?xml version="1.0" encoding="UTF-8"?>\n'
    f"<office:document-content "
    f'xmlns:office="{_OFFICE_NS}" '
    f'xmlns:table="{_TABLE_NS}" '
    f'xmlns:text="{_TEXT_NS}" '
    f'office:version="1.2">\n'
    f"<office:body><office:spreadsheet>\n"
)
_ODS_FOOTER = "</office:spreadsheet></office:body></office:document-content>\n"


def _escape_xml(text: str) -> str:
    """转义 XML 特殊字符。

    :param text: 原始文本
    :return: XML 安全文本（``&`` / ``<`` / ``>`` / ``"`` / ``'`` 转义）
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _build_odf_zip(mimetype: bytes, content_xml: str) -> bytes:
    """构造最小合法 ODF ZIP 包。

    :param mimetype: ODF mimetype 标识字节（如 :data:`_ODT_MIMETYPE`）
    :param content_xml: ``content.xml`` 完整字符串
    :return: ODF ZIP 包字节内容

    ``mimetype`` 必须为 ZIP 第一个文件且 ``ZIP_STORED`` 不压缩（ODF 规范
    要求，便于通过文件头快速识别类型）。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype 必须为第一个文件且不压缩
        mi = zipfile.ZipInfo("mimetype")
        mi.compress_type = zipfile.ZIP_STORED
        zf.writestr(mi, mimetype)
        # content.xml 用默认 DEFLATED 压缩
        zf.writestr("content.xml", content_xml.encode("utf-8"))
    return buf.getvalue()


def make_odt_sample(paragraphs: Iterable[str] | None = None) -> bytes:
    """生成最小合法 ODT 文档样本。

    :param paragraphs: 段落文本列表，每段生成一个 ``<text:p>`` 元素；
        默认 ``["段落含 password 内容", "标题 secret"]``
    :return: ODT ZIP 包字节内容
    """
    if paragraphs is None:
        paragraphs = ["段落含 password 内容", "标题 secret"]
    parts = [_ODT_HEADER]
    for text in paragraphs:
        parts.append(f"<text:p>{_escape_xml(text)}</text:p>\n")
    parts.append(_ODT_FOOTER)
    return _build_odf_zip(_ODT_MIMETYPE, "".join(parts))


def make_ods_sample(
    rows: Iterable[Iterable[str]] | None = None,
    table_name: str = "数据",
) -> bytes:
    """生成最小合法 ODS 表格样本。

    :param rows: 二维单元格文本，外层为行，内层为列；默认 1 行 1 列
        ``[["cell_password"]]``
    :param table_name: 工作表名称
    :return: ODS ZIP 包字节内容
    """
    if rows is None:
        rows = [["cell_password"]]
    parts = [_ODS_HEADER]
    parts.append(f'<table:table table:name="{_escape_xml(table_name)}">\n')
    for row in rows:
        parts.append("<table:table-row>\n")
        for cell_text in row:
            parts.append(f"<table:table-cell><text:p>{_escape_xml(cell_text)}</text:p></table:table-cell>\n")
        parts.append("</table:table-row>\n")
    parts.append("</table:table>\n")
    parts.append(_ODS_FOOTER)
    return _build_odf_zip(_ODS_MIMETYPE, "".join(parts))
