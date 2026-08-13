"""OCR 识别测试报告生成脚本。

生成含中英文文字的测试图片与扫描版 PDF，调用 fuscan 的 ImageExtractor
与 PdfExtractor 执行 OCR 识别，输出测试报告。

用法：uv run python scripts/ocr_test_report.py
"""

from __future__ import annotations

import io
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from fuscan.extractors.image import ImageExtractor
from fuscan.extractors.ocr import get_ocr_status
from fuscan.extractors.pdf import PdfExtractor

# 测试输出目录
_OUTPUT_DIR = Path("scripts/ocr_test_output")
_FONT_CJK = "C:/Windows/Fonts/msyh.ttc"
_FONT_LATIN = "C:/Windows/Fonts/arial.ttf"


def _make_font(size: int) -> ImageFont.FreeTypeFont:
    """加载中英文字体（微软雅黑优先，兼容中英文）."""
    try:
        return ImageFont.truetype(_FONT_CJK, size)
    except OSError:
        return ImageFont.truetype(_FONT_LATIN, size)


def gen_test_image() -> bytes:
    """生成含中英文文字的测试图片（PNG）."""
    img = Image.new("RGB", (800, 400), "white")
    draw = ImageDraw.Draw(img)
    font_large = _make_font(36)
    font_small = _make_font(24)
    lines = [
        (font_large, "fuscan OCR 测试"),
        (font_small, "RapidOCR + ONNX Runtime"),
        (font_small, "中英文混合识别 Hello World 2026"),
        (font_small, "密钥检测 AKIAIOSFODNN7EXAMPLE"),
        (font_small, "邮箱 test@example.com"),
    ]
    y = 30
    for font, text in lines:
        draw.text((40, y), text, fill="black", font=font)
        y += 60
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def gen_scanned_pdf() -> bytes:
    """生成扫描版 PDF（图片转 PDF，无文本层，触发 OCR 回退）."""
    img = Image.new("RGB", (800, 500), "white")
    draw = ImageDraw.Draw(img)
    font_large = _make_font(32)
    font_small = _make_font(22)
    lines = [
        (font_large, "扫描版 PDF 测试"),
        (font_small, "此 PDF 无文本层，需 OCR 识别"),
        (font_small, "GitHub Token ghp_1234567890abcdef"),
        (font_small, "端口号 8080 / 443 / 22"),
        (font_small, "路径 C:\\Users\\admin\\.ssh\\id_rsa"),
    ]
    y = 30
    for font, text in lines:
        draw.text((40, y), text, fill="black", font=font)
        y += 55
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def gen_text_pdf() -> bytes:
    """生成有文本层的正常 PDF（reportlab 写入，pypdfium2 直接提取，不走 OCR）."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 18)
    lines = [
        "Text-layer PDF Test",
        "This PDF has a text layer, pypdfium2 extracts directly",
        "API Key sk-1234567890abcdefghijklmnopqrstuvwxyz",
        "DB connection postgres://user:pass@db:5432/myapp",
    ]
    y = 700
    for line in lines:
        c.drawString(72, y, line)
        y -= 30
    c.save()
    return buf.getvalue()


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    status = get_ocr_status()

    print("=" * 70)
    print("fuscan OCR 识别测试报告")
    print("=" * 70)
    print("\n## 测试环境")
    print(f"  OCR 可用: {status.available}")
    print(f"  rapidocr 版本: {status.version}")
    for dep in status.dependencies:
        flag = "✓" if dep.installed else "✗"
        print(f"  {flag} {dep.name}: {dep.version or '未就位'}")

    img_ext = ImageExtractor()
    pdf_ext = PdfExtractor()

    # --- 测试 1：图片 OCR ---
    print("\n## 测试 1：图片 OCR 识别")
    img_data = gen_test_image()
    img_path = _OUTPUT_DIR / "test_image.png"
    img_path.write_bytes(img_data)
    print(f"  测试文件: {img_path}（{len(img_data)} bytes）")
    print(f"  引擎: {img_ext.engine_info}")
    t0 = time.perf_counter()
    img_text = img_ext.extract_from_bytes(img_data)
    img_elapsed = time.perf_counter() - t0
    print(f"  耗时: {img_elapsed:.3f}s")
    print("  识别结果:")
    for line in img_text.split("\n"):
        print(f"    | {line}")
    print(f"  识别行数: {len([x for x in img_text.split(chr(10)) if x.strip()])}")

    # --- 测试 2：扫描版 PDF OCR 回退 ---
    print("\n## 测试 2：扫描版 PDF OCR 回退")
    scanned_data = gen_scanned_pdf()
    scanned_path = _OUTPUT_DIR / "test_scanned.pdf"
    scanned_path.write_bytes(scanned_data)
    print(f"  测试文件: {scanned_path}（{len(scanned_data)} bytes）")
    print(f"  静态引擎: {pdf_ext.engine_info}")
    t0 = time.perf_counter()
    scanned_text = pdf_ext.extract_from_bytes(scanned_data)
    scanned_elapsed = time.perf_counter() - t0
    print(f"  实际引擎: {pdf_ext.last_engine_info}")
    print(f"  耗时: {scanned_elapsed:.3f}s")
    print("  识别结果:")
    for line in scanned_text.split("\n"):
        print(f"    | {line}")
    print(f"  识别行数: {len([x for x in scanned_text.split(chr(10)) if x.strip()])}")

    # --- 测试 3：文本层 PDF 直接提取 ---
    print("\n## 测试 3：文本层 PDF 直接提取（对照）")
    try:
        text_data = gen_text_pdf()
        text_path = _OUTPUT_DIR / "test_text.pdf"
        text_path.write_bytes(text_data)
        print(f"  测试文件: {text_path}（{len(text_data)} bytes）")
        t0 = time.perf_counter()
        text_result = pdf_ext.extract_from_bytes(text_data)
        text_elapsed = time.perf_counter() - t0
        print(f"  实际引擎: {pdf_ext.last_engine_info}")
        print(f"  耗时: {text_elapsed:.3f}s")
        print("  识别结果:")
        for line in text_result.split("\n"):
            print(f"    | {line}")
    except Exception as exc:
        print(f"  跳过: {exc}")

    # --- 汇总 ---
    print("\n## 汇总")
    print(f"  图片 OCR: {'通过' if img_text.strip() else '失败'}（{img_elapsed:.3f}s）")
    print(f"  扫描版 PDF OCR: {'通过' if scanned_text.strip() else '失败'}（{scanned_elapsed:.3f}s）")
    print(f"  OCR 引擎链: {img_ext.engine_info} + onnxruntime {status.dependencies[1].version}")
    print("=" * 70)


if __name__ == "__main__":
    main()
