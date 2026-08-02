"""简历文件文本抽取服务（AL-M3-04）。

支持 pdf / docx / txt 三种格式；.doc 旧格式与图像型 PDF（扫描件）暂不支持，
明确抛 `ResumeParseError` 由调用方标记任务失败，不做假成功返回。

为 M4 简历解析（resume_parse）提供文本抽取前置能力，不含 LLM 抽取与 PII 脱敏。
"""

from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}


class ResumeParseError(Exception):
    """简历文本抽取失败。"""


def extract_text(file_path: str | Path) -> str:
    """按扩展名抽取简历文本。

    Raises:
        ResumeParseError: 文件不存在 / 类型不支持 / 无可提取文本
    """
    path = Path(file_path)
    if not path.exists():
        raise ResumeParseError(f"文件不存在: {path}")

    ext = path.suffix.lower()
    if ext == ".txt":
        return path.read_text(encoding="utf-8", errors="replace").strip()
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext == ".docx":
        return _extract_docx(path)
    raise ResumeParseError(
        f"不支持的文件类型: {ext}（仅支持 {'/'.join(sorted(SUPPORTED_EXTENSIONS))}，.doc 请转存为 .docx）"
    )


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(parts).strip()
    if not text:
        raise ResumeParseError(f"PDF 无可提取文本（可能是扫描件，OCR 暂未接入）: {path.name}")
    return text


def _extract_docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    # 表格单元格也是简历正文（如技能清单/经历表）
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    text = "\n".join(parts).strip()
    if not text:
        raise ResumeParseError(f"DOCX 无可提取文本: {path.name}")
    return text
