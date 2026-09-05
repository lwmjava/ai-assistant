"""Office 文档解析器（docx / xlsx / pptx）。

分别基于 python-docx / openpyxl / python-pptx 按需导入并提取文本；
依赖缺失或文件异常统一转换为 ``DocumentParseError``，避免内部错误外泄。
"""

from __future__ import annotations

import io

from app.rag.document_parsers.base import (
    DocumentParseError,
    DocumentParser,
    ParsedDocument,
)
from app.rag.document_parsers.text import file_extension, title_from_filename


class DocxDocumentParser(DocumentParser):
    """解析 docx 文档正文段落文本。"""

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名为 docx 时返回 True。"""
        return file_extension(filename) == "docx"

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """读取 docx 段落并拼接为纯文本。"""
        try:
            from docx import Document

            document = Document(io.BytesIO(file_bytes))
            parts = [paragraph.text.strip() for paragraph in document.paragraphs]
            text = "\n".join(part for part in parts if part)
        except Exception as exc:  # noqa: BLE001
            raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密") from exc

        return ParsedDocument(
            text=text,
            title=title_from_filename(filename),
            source=filename,
            extension="docx",
            content_type=content_type,
            metadata={"parser_name": "docx"},
        )


class XlsxDocumentParser(DocumentParser):
    """解析 xlsx 工作簿，汇总各 sheet 的文本单元格。"""

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名为 xlsx 时返回 True。"""
        return file_extension(filename) == "xlsx"

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """遍历所有工作表，按行聚合非空单元格为文本。"""
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(io.BytesIO(file_bytes), data_only=True)
            rows: list[str] = []
            for sheet in workbook.worksheets:
                rows.append(f"# Sheet: {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
                    if cells:
                        rows.append(", ".join(cells))
            text = "\n".join(rows)
        except Exception as exc:  # noqa: BLE001
            raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密") from exc

        return ParsedDocument(
            text=text,
            title=title_from_filename(filename),
            source=filename,
            extension="xlsx",
            content_type=content_type,
            metadata={"parser_name": "xlsx"},
        )


class PptxDocumentParser(DocumentParser):
    """解析 pptx 幻灯片，按页汇总形状中的文本。"""

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名为 pptx 时返回 True。"""
        return file_extension(filename) == "pptx"

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """遍历幻灯片与形状，提取非空文本并按页分隔。"""
        try:
            from pptx import Presentation

            presentation = Presentation(io.BytesIO(file_bytes))
            slides: list[str] = []
            for index, slide in enumerate(presentation.slides, start=1):
                slides.append(f"# Slide {index}")
                for shape in slide.shapes:
                    text = getattr(shape, "text", "")
                    if text and text.strip():
                        slides.append(text.strip())
            text = "\n".join(slides)
        except Exception as exc:  # noqa: BLE001
            raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密") from exc

        return ParsedDocument(
            text=text,
            title=title_from_filename(filename),
            source=filename,
            extension="pptx",
            content_type=content_type,
            metadata={"parser_name": "pptx"},
        )
