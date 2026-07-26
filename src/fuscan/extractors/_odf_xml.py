"""OpenDocument 格式 ZIP+XML 通用解析工具。

ODF（OpenDocument Format）文档本质为 ZIP 压缩包，内含 ``content.xml`` 等
XML 文件。本模块用标准库 ``zipfile`` + ``xml.etree.ElementTree`` 解析
ODT/ODS/ODP 等 ODF 文档，替代 odfpy 依赖（odfpy 在 PyPI 上仅有 sdist，
无预编译 wheel，与 fspack 的 ``--only-binary=:all:`` 打包策略冲突）。

主要 API：

- :func:`load_content_xml`：从 ODF 字节流读取 ``content.xml`` 并解析为
  :class:`~xml.etree.ElementTree.Element` 树。
- :func:`iter_elements`：按命名空间+本地名遍历元素。
- :func:`local_name`：剥离 XML 命名前缀返回本地名。
- :func:`element_text`：递归提取元素及子元素所有文本节点。

ODF 1.2 命名空间常量见模块底部 ``NAMESPACES``。
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Iterator

__all__ = [
    "NAMESPACES",
    "OfficeNS",
    "TableNS",
    "TextNS",
    "element_text",
    "iter_elements",
    "iter_text_paragraphs",
    "load_content_xml",
]

# ODF 1.2 主要命名空间 URN
OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


class Namespaces:
    """ODF 命名空间常量集合。

    用 ``OfficeNS`` / ``TableNS`` / ``TextNS`` 作为 ``Element.find`` 的
    namespace 参数，避免在调用点拼接 ``{urn:...}local-name`` 的冗长写法。
    """

    OFFICE = OFFICE_NS
    TABLE = TABLE_NS
    TEXT = TEXT_NS


# 模块级便捷别名（与 odfpy 习惯对齐：office/text/table）
NAMESPACES = Namespaces()
OfficeNS = OFFICE_NS
TableNS = TABLE_NS
TextNS = TEXT_NS


def iter_elements(
    root: ET.Element,
    namespace: str,
    local_names: tuple[str, ...],
) -> Iterator[ET.Element]:
    """按命名空间+本地名遍历子树所有匹配元素（深度优先）。

    :param root: XML 树根节点
    :param namespace: 命名空间 URN（如 :data:`TEXT_NS`）
    :param local_names: 待匹配的本地名元组（如 ``("p", "h")``）
    :return: 匹配元素的迭代器（文档顺序）

    用 ``ET.iter`` 全树遍历后用 ``tag.endswith(local_name)`` 匹配，
    避免为每个本地名单独构造 ``{ns}name`` 字符串。
    """
    targets = tuple(f"}}{name}" for name in local_names)
    prefix = f"{{{namespace}}}"
    for elem in root.iter():
        tag = elem.tag
        if isinstance(tag, str) and tag.startswith(prefix) and tag.endswith(targets):
            yield elem


def element_text(elem: ET.Element) -> str:
    """递归提取元素及其所有后代元素的文本与尾部文本。

    ODF 单元格常包含多层 ``<text:p>`` / ``<text:span>`` 嵌套，需递归
    :attr:`Element.text` 与 :attr:`Element.tail` 才能拼出完整文本。

    :param elem: 待提取文本的元素
    :return: 拼接后的纯文本（已 ``strip``）
    """
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(element_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def load_content_xml(data: bytes) -> ET.Element:
    """从 ODF 字节流读取 ``content.xml`` 并解析为 Element 树。

    :param data: ODF 文件完整字节内容（ZIP 格式）
    :return: ``content.xml`` 的根 :class:`~xml.etree.ElementTree.Element`
    :raises zipfile.BadZipFile: 数据不是合法 ZIP
    :raises KeyError: ZIP 内未找到 ``content.xml``
    :raises ET.ParseError: content.xml 不是合法 XML
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf, zf.open("content.xml") as content_file:
        xml_bytes = content_file.read()
    return ET.fromstring(xml_bytes)


def iter_text_paragraphs(root: ET.Element) -> Iterable[ET.Element]:
    """遍历 ODF 文本段落（``<text:p>`` 与 ``<text:h>``）。

    兼容 ODT 文字文档的段落与标题提取场景。

    :param root: content.xml 根元素
    :return: ``<text:p>`` 与 ``<text:h>`` 元素迭代器（文档顺序）
    """
    return iter_elements(root, TEXT_NS, ("p", "h"))
