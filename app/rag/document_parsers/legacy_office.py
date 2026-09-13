"""老 Office 格式（doc / xls / ppt）解析器。

``.xls`` 由纯 Python 的 ``xlrd`` 直接读取；
``.doc`` / ``.ppt`` 由 headless LibreOffice（``soffice``）转换为现代格式后再复用
现代解析器提取文本。依赖缺失或转换失败统一映射为 ``DocumentParseError``，
避免内部错误外泄给调用方。
"""

from __future__ import annotations

from app.rag.document_parsers import libreoffice as libreoffice_utils
from app.rag.document_parsers.base import (
    DocumentParseError,
    DocumentParser,
    ParsedBlock,
    ParsedDocument,
)
from app.rag.document_parsers.office import DocxDocumentParser, PptxDocumentParser
from app.rag.document_parsers.text import file_extension, title_from_filename

# 保留旧名字，避免现有测试和调用方 patch 路径失效。
LegacyOfficeConversionError = libreoffice_utils.LegacyOfficeConversionError
_libreoffice_binary = libreoffice_utils._libreoffice_binary


def _convert_with_libreoffice(
    file_bytes: bytes, source_ext: str, target_ext: str
) -> bytes:
    """兼容旧调用路径，内部复用公共 LibreOffice 转换工具。"""
    if _libreoffice_binary() is None:
        raise LegacyOfficeConversionError(
            message="老 Office 格式依赖缺失：未检测到 LibreOffice（soffice）",
            error_code="libreoffice_missing",
        )
    return libreoffice_utils.convert_office_bytes_with_libreoffice(
        file_bytes, source_ext, target_ext
    )


class XlsDocumentParser(DocumentParser):
    """解析 xls 工作簿，按 sheet / 行聚合非空单元格文本。"""

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名为 xls 时返回 True。"""
        return file_extension(filename) == "xls"

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """用 xlrd 读取 workbook，逐 sheet、逐行拼接文本。"""
        try:
            import xlrd

            workbook = xlrd.open_workbook(file_contents=file_bytes)
            rows: list[str] = []
            blocks: list[ParsedBlock] = []
            order = 0
            for sheet in workbook.sheets():
                rows.append(f"# Sheet: {sheet.name}")
                blocks.append(
                    ParsedBlock(
                        type="sheet_heading",
                        text=f"# Sheet: {sheet.name}",
                        order=order,
                        section_path=[sheet.name],
                        metadata={
                            "parser_name": "xls",
                            "reading_order": order + 1,
                            "layout_role": "title",
                        },
                    )
                )
                order += 1
                for row_index in range(sheet.nrows):
                    cells = [
                        str(value).strip()
                        for value in sheet.row_values(row_index)
                        if value is not None and str(value).strip()
                    ]
                    if cells:
                        line = ", ".join(cells)
                        rows.append(line)
                        blocks.append(
                            ParsedBlock(
                                type="sheet_row",
                                text=line,
                                order=order,
                                section_path=[sheet.name],
                                metadata={
                                    "parser_name": "xls",
                                    "reading_order": order + 1,
                                    "layout_role": "table_row",
                                },
                            )
                        )
                        order += 1
            text = "\n".join(rows)
        except Exception as exc:  # noqa: BLE001
            raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密") from exc

        return ParsedDocument(
            text=text,
            title=title_from_filename(filename),
            source=filename,
            extension="xls",
            content_type=content_type,
            metadata={"parser_name": "xls"},
            blocks=blocks,
        )


class DocDocumentParser(DocumentParser):
    """解析 doc：先转为 docx，再复用 DocxDocumentParser 提取文本。"""

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名为 doc 时返回 True。"""
        return file_extension(filename) == "doc"

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """转换后复用现代解析器，最终结果仍标记为原 doc 格式。"""
        docx_bytes = _convert_with_libreoffice(file_bytes, "doc", "docx")
        parsed = DocxDocumentParser().extract(docx_bytes, filename, content_type)
        return ParsedDocument(
            text=parsed.text,
            title=title_from_filename(filename),
            source=filename,
            extension="doc",
            content_type=content_type,
            metadata={"parser_name": "doc"},
            blocks=list(parsed.blocks),
        )


class PptDocumentParser(DocumentParser):
    """解析 ppt：先转为 pptx，再复用 PptxDocumentParser 提取文本。"""

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名为 ppt 时返回 True。"""
        return file_extension(filename) == "ppt"

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """转换后复用现代解析器，最终结果仍标记为原 ppt 格式。"""
        pptx_bytes = _convert_with_libreoffice(file_bytes, "ppt", "pptx")
        parsed = PptxDocumentParser().extract(pptx_bytes, filename, content_type)
        metadata = dict(parsed.metadata)
        metadata["parser_name"] = "ppt_ocr" if metadata.get("used_ocr") else "ppt"
        return ParsedDocument(
            text=parsed.text,
            title=title_from_filename(filename),
            source=filename,
            extension="ppt",
            content_type=content_type,
            metadata=metadata,
            blocks=list(parsed.blocks),
        )
