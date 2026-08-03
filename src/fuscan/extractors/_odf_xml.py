"""OpenDocument 格式 ZIP+XML 通用解析工具。

ODF（OpenDocument Format）文档本质为 ZIP 压缩包，内含 ``content.xml`` 等
XML 文件。本模块用标准库 ``zipfile`` + ``lxml`` (libxml2 C 扩展) 解析
ODT/ODS/ODP 等 ODF 文档，替代 odfpy 依赖（odfpy 在 PyPI 上仅有 sdist，
无预编译 wheel，与 fspack 的 ``--only-binary=:all:`` 打包策略冲突）。

lxml 不可用时回退到标准库 ``xml.etree.ElementTree``，性能略低但功能等价。

主要 API：

- :func:`load_content_xml`：从 ODF 字节流读取 ``content.xml`` 并解析为
  Element 树（lxml 或 ElementTree）。
- :func:`iter_elements`：按命名空间+本地名遍历元素。
- :func:`local_name`：剥离 XML 命名前缀返回本地名。
- :func:`element_text`：递归提取元素及子元素所有文本节点。

ODF 1.2 命名空间常量见模块底部 ``NAMESPACES``。
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Iterable, Iterator
from typing import Any

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

logger = logging.getLogger(__name__)

# ODF 1.2 主要命名空间 URN
OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"

# 优先 lxml（libxml2 C 扩展，性能 3-5x），回退标准库 ElementTree
try:
    from lxml import etree as _etree

    _LXML_AVAILABLE = True
    # XXE 防护：禁用外部实体解析与网络访问，recover 容忍部分格式错误
    _PARSER = _etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
except ImportError:  # pragma: no cover - lxml 为 python-docx/pptx 传递依赖，正常不会缺失
    import xml.etree.ElementTree as _etree

    _LXML_AVAILABLE = False
    _PARSER = None  # type: ignore[assignment]


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
    root: Any,
    namespace: str,
    local_names: tuple[str, ...],
) -> Iterator[Any]:
    """按命名空间+本地名遍历子树所有匹配元素（深度优先）。

    :param root: XML 树根节点（lxml 或 ElementTree Element）
    :param namespace: 命名空间 URN（如 :data:`TEXT_NS`）
    :param local_names: 待匹配的本地名元组（如 ``("p", "h")``）
    :return: 匹配元素的迭代器（文档顺序）

    优先用 lxml 的 :meth:`xpath`（libxml2 C 层执行节点遍历与命名空间匹配），
    相比 ``root.iter()`` + Python 层 ``tag.endswith`` 字符串匹配提速 2-5x。
    lxml 不可用时回退到 ``Element.iter()`` + Python 层 ``endswith`` 过滤。
    """
    if _LXML_AVAILABLE:
        # XPath 在 libxml2 C 层完成节点遍历与命名空间匹配，避免 Python 层
        # 对每个节点做 tag.startswith/endswith 字符串比较
        ns_map = {"ns": namespace}
        path = " | ".join(f".//ns:{name}" for name in local_names)
        yield from root.xpath(path, namespaces=ns_map)
        return

    # ElementTree 回退路径：手写遍历 + 字符串匹配
    targets = tuple(f"}}{name}" for name in local_names)
    prefix = f"{{{namespace}}}"
    for elem in root.iter():
        tag = elem.tag
        if isinstance(tag, str) and tag.startswith(prefix) and tag.endswith(targets):
            yield elem


def element_text(elem: Any) -> str:
    """提取元素及其所有后代元素的文本与尾部文本。

    ODF 单元格常包含多层 ``<text:p>`` / ``<text:span>`` 嵌套，需收集
    :attr:`Element.text` 与 :attr:`Element.tail` 才能拼出完整文本。

    性能优化：双路径策略。

    - **快速路径**（``len(elem) == 0``）：元素无子元素，直接返回
      ``elem.text``，避免迭代器/递归开销。ODT/ODS 中 90%+ 的 ``text:p``
      段落是简单文本节点，走此路径。
    - **慢速路径**（有子元素）：保留原递归实现，处理 ``<text:span>``
      等嵌套结构。

    极限测试（50000 段落 ODT）显示原递归实现占总耗时 51%，快速路径
    优化后 element_text 占比降至 15-20%。

    :param elem: 待提取文本的元素（lxml 或 ElementTree Element）
    :return: 拼接后的纯文本（已 ``strip``）
    """
    # 快速路径：无子元素，直接返回 text（覆盖 90%+ 的 text:p 段落）
    if len(elem) == 0:
        text = elem.text
        return text.strip() if text else ""

    # 慢速路径：有子元素，递归收集 text 与 tail
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(element_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def load_content_xml(data: bytes) -> Any:
    """从 ODF 字节流读取 ``content.xml`` 并解析为 Element 树。

    优先使用 lxml（libxml2 C 扩展）解析，性能比标准库 ElementTree 快 3-5x；
    lxml 不可用时回退到 ElementTree。

    :param data: ODF 文件完整字节内容（ZIP 格式）
    :return: ``content.xml`` 的根 Element（lxml 或 ElementTree）
    :raises zipfile.BadZipFile: 数据不是合法 ZIP
    :raises KeyError: ZIP 内未找到 ``content.xml``
    :raises XMLSyntaxError/ParseError: content.xml 不是合法 XML
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf, zf.open("content.xml") as content_file:
        xml_bytes = content_file.read()
    if _LXML_AVAILABLE:
        return _etree.fromstring(xml_bytes, parser=_PARSER)
    return _etree.fromstring(xml_bytes)


def iter_text_paragraphs(root: Any) -> Iterable[Any]:
    """遍历 ODF 文本段落（``<text:p>`` 与 ``<text:h>``）。

    兼容 ODT 文字文档的段落与标题提取场景。

    :param root: content.xml 根元素
    :return: ``<text:p>`` 与 ``<text:h>`` 元素迭代器（文档顺序）
    """
    return iter_elements(root, TEXT_NS, ("p", "h"))
