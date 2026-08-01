"""简历文件文本抽取单元测试（AL-M3-04）。

覆盖 txt/docx 抽取、PDF 无文本（扫描件）抛错、文件缺失/类型不支持抛错。
"""

import pytest

from app.services.resume.file_parser import ResumeParseError, extract_text


class TestExtractText:
    def test_txt(self, tmp_path):
        path = tmp_path / "resume.txt"
        path.write_text("姓名：张三\n技能：Python、MySQL\n", encoding="utf-8")
        assert extract_text(path) == "姓名：张三\n技能：Python、MySQL"

    def test_docx_paragraphs_and_tables(self, tmp_path):
        import docx

        doc = docx.Document()
        doc.add_paragraph("姓名：李四")
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "技能"
        table.rows[0].cells[1].text = "Go、Kubernetes"
        path = tmp_path / "resume.docx"
        doc.save(str(path))

        text = extract_text(path)
        assert "姓名：李四" in text
        assert "Go、Kubernetes" in text  # 表格内容也被抽取

    def test_pdf_without_text_raises(self, tmp_path):
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        path = tmp_path / "scan.pdf"
        with open(path, "wb") as f:
            writer.write(f)

        with pytest.raises(ResumeParseError, match="扫描件"):
            extract_text(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ResumeParseError, match="文件不存在"):
            extract_text(tmp_path / "nope.pdf")

    def test_unsupported_extension_raises(self, tmp_path):
        path = tmp_path / "resume.doc"  # 旧格式 .doc 明确不支持
        path.write_text("内容", encoding="utf-8")
        with pytest.raises(ResumeParseError, match="不支持的文件类型"):
            extract_text(path)
