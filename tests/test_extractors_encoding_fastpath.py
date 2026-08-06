"""编码检测快路径对照测试（R2）。

验证 :meth:`TextExtractor._decode` 的头部快路径（BOM / 全量 UTF-8 严格解码）
与旧 charset-normalizer 路径输出等价，且对无法快判的字节（GBK/非法字节）
仍回退 charset-normalizer，保证零误判。

快路径命中条件（保守）：

- 检测到 BOM（UTF-8-SIG / UTF-16 / UTF-32）
- 整段字节严格 UTF-8 解码成功（纯 ASCII 属于其子集）

其余情形（GBK、含非法字节等）不走快路径，回退 charset-normalizer，
避免「头部纯 ASCII 但正文 GBK 中文」被 UTF-8 误吞的历史风险。
"""

from __future__ import annotations

import pytest

from fuscan.extractors.text import TextExtractor, _normalize_newlines


@pytest.mark.parametrize(
    ("raw", "encoding"),
    [
        ("hello password world\n第二行 UTF-8 内容\n", "utf-8"),
        ("password=secret123\nplain ascii only\n", "ascii"),
        ("含 BOM 的 UTF-8 内容 AKIA password\n", "utf-8-sig"),
        ("UTF-16 LE 内容 password 密码\n", "utf-16"),
        ("UTF-16 BE 内容 password 密码\n", "utf-16-be-bom"),
    ],
)
def test_fastpath_matches_expected_text(raw: str, encoding: str) -> None:
    """快路径命中编码（BOM/UTF-8）时，解码结果与原文规范化后一致。"""
    if encoding == "ascii":
        data = raw.encode("ascii")
    elif encoding == "utf-8-sig":
        data = raw.encode("utf-8-sig")
    elif encoding == "utf-16":
        # utf-16 编码会自动加 LE BOM
        data = raw.encode("utf-16")
    elif encoding == "utf-16-be-bom":
        data = b"\xfe\xff" + raw.encode("utf-16-be")
    else:
        data = raw.encode("utf-8")

    content = TextExtractor().extract_from_bytes(data)
    assert content == _normalize_newlines(raw)


def test_fastpath_utf8_keeps_keywords() -> None:
    """UTF-8 快路径保留敏感关键词，供后续规则匹配。"""
    data = b"config: password=secret123\nAKIAIOSFODNN7EXAMPLE\n"
    content = TextExtractor().extract_from_bytes(data)
    assert "password" in content
    assert "AKIAIOSFODNN7EXAMPLE" in content


def test_gbk_falls_back_to_charset_normalizer() -> None:
    """GBK 字节不走 UTF-8 快路径，回退 charset-normalizer 正确解码中文。"""
    data = "这是包含密码字段的配置：password123，请妥善保管".encode("gbk")
    content = TextExtractor().extract_from_bytes(data)
    assert "密码" in content
    assert "password123" in content


def test_fastpath_equivalent_to_charset_normalizer_for_utf8() -> None:
    """UTF-8 内容：快路径与 charset-normalizer 路径解码结果一致。"""
    from charset_normalizer import from_bytes

    data = "行一 password\n行二 密码 AKIA\n行三 内容\n".encode()
    fast = TextExtractor().extract_from_bytes(data)
    cn_result = from_bytes(data).best()
    assert cn_result is not None
    assert fast == _normalize_newlines(str(cn_result))


def test_utf8_with_boundary_multibyte_not_misdetected() -> None:
    """跨越取样边界的多字节 UTF-8 内容不被误判（整段严格解码保证正确）。"""
    # 构造一段 UTF-8 内容，含大量中文，整体严格 UTF-8 可解
    data = ("段落 password 内容 " * 500 + "AKIA 结尾密码").encode()
    content = TextExtractor().extract_from_bytes(data)
    assert "password" in content
    assert "AKIA" in content
    assert "结尾密码" in content
