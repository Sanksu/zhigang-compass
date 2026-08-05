"""双栏简历 PDF 重组测试（设计文档 §8.1）。

用 PyMuPDF 在指定坐标写入文本生成测试 PDF（fitz 已在依赖中，
且可精确控制 x/y 坐标构造双栏布局）。
"""

import fitz

from app.services.resume.reflow import reflow_pdf


def _write_pdf(path, spans):
    """spans: [(x, y, text)]，生成一页 A4 PDF。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    for x, y, text in spans:
        page.insert_text((x, y), text)
    doc.save(path)
    doc.close()


def test_single_column_pdf_returns_empty(tmp_path):
    """单栏 PDF 无双栏空白带 → reflow_pdf 返回空串（调用方回退原提取）。"""
    p = tmp_path / "single.pdf"
    _write_pdf(p, [(50, 60 + i * 20, f"line {i}") for i in range(10)])
    assert reflow_pdf(p) == ""


def test_two_column_pdf_reflowed_in_reading_order(tmp_path):
    """双栏 PDF 按"左栏 → 右栏"阅读顺序重组。"""
    p = tmp_path / "two.pdf"
    left = [f"L{i}" for i in range(5)]
    right = [f"R{i}" for i in range(5)]
    spans = [(50, 60 + i * 20, t) for i, t in enumerate(left)]
    spans += [(320, 60 + i * 20, t) for i, t in enumerate(right)]
    _write_pdf(p, spans)

    text = reflow_pdf(p)
    assert text != ""
    # 左栏内容在右栏内容之前（阅读顺序）
    assert text.index("L0") < text.index("R0")
    # 两栏文本均完整保留
    assert all(f"L{i}" in text for i in range(5))
    assert all(f"R{i}" in text for i in range(5))


def test_mixed_layout_preserves_all_pages(tmp_path):
    """混合布局（第一页双栏 + 第二页单栏）时所有页文本都保留。"""
    p = tmp_path / "mixed.pdf"
    doc = fitz.open()
    page1 = doc.new_page(width=595, height=842)
    for i in range(4):
        page1.insert_text((50, 60 + i * 20), f"A{i}")   # 左栏
        page1.insert_text((320, 60 + i * 20), f"B{i}")  # 右栏
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((50, 60), "single page")
    doc.save(p)
    doc.close()

    text = reflow_pdf(p)
    assert "A0" in text and "B0" in text
    assert "single page" in text
