from pathlib import Path

import pytest


def test_registry_rejects_unsupported_extension() -> None:
    from app.rag.document_parsers.base import UnsupportedDocumentTypeError
    from app.rag.document_parsers.service import parse_uploaded_document

    with pytest.raises(UnsupportedDocumentTypeError):
        parse_uploaded_document(b"binary", "data.bin", "application/octet-stream")


def test_service_rejects_empty_text() -> None:
    from app.rag.document_parsers.base import (
        DocumentParser,
        DocumentTextEmptyError,
        ParsedDocument,
    )
    from app.rag.document_parsers.registry import DocumentParserRegistry
    from app.rag.document_parsers.service import DocumentParserService

    class EmptyParser(DocumentParser):
        def supports(self, filename: str, content_type: str | None) -> bool:
            return filename.endswith(".txt")

        def extract(
            self, file_bytes: bytes, filename: str, content_type: str | None
        ) -> ParsedDocument:
            return ParsedDocument(
                text="   ",
                title="a",
                source=filename,
                extension="txt",
                content_type=content_type,
                metadata={},
            )

    service = DocumentParserService(DocumentParserRegistry([EmptyParser()]))
    with pytest.raises(DocumentTextEmptyError):
        service.parse_upload(b"abc", "a.txt", "text/plain")


def test_parse_json_file() -> None:
    from app.rag.document_parsers.service import parse_uploaded_document

    parsed = parse_uploaded_document(
        b'{"k": "v"}', "doc.json", "application/json"
    )
    assert parsed.extension == "json"
    assert '"k": "v"' in parsed.text


def test_parse_csv_with_gbk_fallback() -> None:
    from app.rag.document_parsers.service import parse_uploaded_document

    parsed = parse_uploaded_document(
        "姓名,部门".encode("gb18030"), "staff.csv", "text/csv"
    )
    assert "姓名" in parsed.text


def test_docx_parser_extracts_text(tmp_path: Path) -> None:
    from docx import Document

    from app.rag.document_parsers.service import parse_uploaded_document

    path = tmp_path / "sample.docx"
    document = Document()
    document.add_paragraph("企业知识库文档")
    document.save(path)

    parsed = parse_uploaded_document(
        path.read_bytes(),
        path.name,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "企业知识库文档" in parsed.text


def test_xlsx_parser_extracts_text(tmp_path: Path) -> None:
    from openpyxl import Workbook

    from app.rag.document_parsers.service import parse_uploaded_document

    path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "员工"
    sheet["A1"] = "姓名"
    sheet["B1"] = "部门"
    sheet["A2"] = "Alice"
    sheet["B2"] = "销售"
    workbook.save(path)

    parsed = parse_uploaded_document(
        path.read_bytes(),
        path.name,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert "Alice" in parsed.text
    assert "销售" in parsed.text


def test_pptx_parser_extracts_text(tmp_path: Path) -> None:
    from pptx import Presentation

    from app.rag.document_parsers.service import parse_uploaded_document

    path = tmp_path / "sample.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "季度汇报"
    slide.placeholders[1].text = "销售额增长 20%"
    presentation.save(path)

    parsed = parse_uploaded_document(
        path.read_bytes(),
        path.name,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    assert "季度汇报" in parsed.text
    assert "销售额增长 20%" in parsed.text


def test_pdf_parser_reports_ocr_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.document_parsers.base import DocumentOcrRequiredError
    from app.rag.document_parsers.pdf import PdfDocumentParser

    class FakePage:
        def extract_text(self) -> str:
            return ""

    class FakeReader:
        is_encrypted = False
        pages = [FakePage()]

    monkeypatch.setattr("app.rag.document_parsers.pdf.PdfReader", lambda stream: FakeReader())
    with pytest.raises(DocumentOcrRequiredError):
        PdfDocumentParser().extract(b"%PDF", "scan.pdf", "application/pdf")
