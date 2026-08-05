"""双栏简历 PDF 文本重组（设计文档 §8.1 双栏版式）。

检测：pdfplumber 检测页中部是否存在竖直空白带（左右栏间距），
判定双栏布局。
重组：左右栏各自 `extract_text`，按"左栏自上而下 → 右栏自上而下"的
阅读顺序拼接（双栏简历阅读顺序为整左栏再整右栏）。

单栏 PDF 返回空串（调用方保留原有 pypdf 提取结果）；混合布局
（部分页双栏）时双栏页重组、单栏页保持整页提取。
"""

from pathlib import Path
from typing import Union


def is_two_column(page, gap_ratio: float = 0.06) -> bool:
    """判定页是否为双栏布局。

    双栏充分条件（同时满足）：
    1. 页中部存在宽度 ≥ gap_ratio×页宽的垂直空白带（分栏间隙）；
    2. 左右两栏区域均含文字（单栏排版右半页空白时中部无字但不应误判）。
    """
    width = page.width
    if not width:
        return False
    band_half = width * gap_ratio / 2
    half = width / 2
    mid_band = page.within_bbox(
        (half - band_half, 0, half + band_half, page.height)
    )
    if mid_band.extract_words():
        return False
    left = page.within_bbox((0, 0, half - band_half, page.height))
    right = page.within_bbox((half + band_half, 0, width, page.height))
    return bool(left.extract_words()) and bool(right.extract_words())


def reflow_pdf(pdf_path: Union[str, Path]) -> str:
    """双栏 PDF 按阅读顺序重组；单栏 PDF 返回空串。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        重组后的全文；无双栏页时返回空串（由调用方决定是否回退原提取）
    """
    import pdfplumber

    detected_two_column = False
    out: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            if is_two_column(page):
                detected_two_column = True
                half = page.width / 2
                left = page.crop((0, 0, half, page.height))
                right = page.crop((half, 0, page.width, page.height))
                out.append(left.extract_text() or "")
                out.append(right.extract_text() or "")
            else:
                out.append(page.extract_text() or "")
    if not detected_two_column:
        return ""
    return "\n".join(t for t in out if t).strip()
