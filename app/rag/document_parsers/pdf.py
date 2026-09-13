"""PDF 解析器。

基于 pypdf 提取文本层的 PDF 内容；可提取但无文本时判定为需要 OCR，
抛出 ``DocumentOcrRequiredError``。OCR 能力作为后续扩展点，本次不启用。
"""

from __future__ import annotations

import io

from app.rag.document_parsers.base import (
    DocumentOcrRequiredError,
    DocumentParseError,
    DocumentParser,
    ParsedBlock,
    ParsedDocument,
)
from app.rag.document_parsers.text import file_extension, title_from_filename
from app.rag.import_trace import copy_import_trace
from app.rag.ocr.base import OcrProvider, OcrProviderError
from app.rag.ocr.factory import get_ocr_provider

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - 依赖缺失时由运行时错误提示
    PdfReader = None

try:
    import fitz
except ImportError:  # pragma: no cover - 依赖缺失时由运行时错误提示
    fitz = None


def _extract_pdf_text_blocks(file_bytes: bytes) -> list[ParsedBlock] | None:
    """用 PyMuPDF 提取带 bbox 的原生文本块；不可用或失败时返回 None。"""
    if fitz is None:
        return None
    try:
        document = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return None
    blocks: list[ParsedBlock] = []
    order = 0
    for page_index, page in enumerate(document, start=1):
        reading_order = 0
        for raw_block in page.get_text("blocks"):
            if len(raw_block) < 7:
                continue
            x0, y0, x1, y1, text, _block_no, block_type = raw_block[:7]
            if block_type != 0:  # 0 为文本块，1 为图片块
                continue
            text = (text or "").strip()
            if not text:
                continue
            reading_order += 1
            blocks.append(
                ParsedBlock(
                    type="text_block",
                    text=text,
                    order=order,
                    page=page_index,
                    bbox=(float(x0), float(y0), float(x1), float(y1)),
                    metadata={
                        "parser_name": "pdf",
                        "used_ocr": False,
                        "reading_order": reading_order,
                        "layout_role": "body",
                    },
                )
            )
            order += 1
    return blocks


class PdfDocumentParser(DocumentParser):
    """解析可提取文本的 PDF；扫描件返回需 OCR 的错误。"""

    def __init__(self, ocr_provider: OcrProvider | None = None) -> None:
        self._ocr_provider = ocr_provider

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名为 pdf 时返回 True。"""
        return file_extension(filename) == "pdf"

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """提取各页文本；加密/损坏转为解析失败，无文本转为需 OCR。"""
        try:
            if PdfReader is None:
                raise ImportError("pypdf is not installed")
            reader = PdfReader(io.BytesIO(file_bytes))
            if reader.is_encrypted:
                raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密")
            texts = [(page.extract_text() or "").strip() for page in reader.pages]
        except DocumentParseError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密") from exc

        text = "\n".join(part for part in texts if part)
        if not text:
            try:
                provider = self._ocr_provider or get_ocr_provider()
                result = provider.extract_pdf_text(file_bytes)
            except OcrProviderError as exc:
                message = str(exc)
                if "未启用 OCR" in message:
                    raise copy_import_trace(DocumentOcrRequiredError(message), exc) from exc
                raise copy_import_trace(DocumentParseError(message), exc) from exc
            return ParsedDocument(
                text=result.text,
                title=title_from_filename(filename),
                source=filename,
                extension="pdf",
                content_type=content_type,
                metadata={
                    "parser_name": "pdf_ocr",
                    "used_ocr": True,
                    "ocr_provider": result.provider,
                },
                blocks=[
                    ParsedBlock(
                        type="ocr_page",
                        text=block.text,
                        order=index,
                        page=block.page,
                        bbox=block.bbox,
                        metadata={
                            "parser_name": "pdf_ocr",
                            "used_ocr": True,
                            "ocr_provider": result.provider,
                            "reading_order": block.reading_order,
                            "layout_role": block.layout_role,
                        },
                    )
                    for index, block in enumerate(result.blocks)
                ]
                or [
                    ParsedBlock(
                        type="ocr_page",
                        text=result.text,
                        order=0,
                        page=1,
                        metadata={
                            "parser_name": "pdf_ocr",
                            "used_ocr": True,
                            "ocr_provider": result.provider,
                            "reading_order": 1,
                            "layout_role": "body",
                        },
                    )
                ],
            )

        blocks = _extract_pdf_text_blocks(file_bytes)
        if not blocks:
            blocks = [
                ParsedBlock(
                    type="page_text",
                    text=part,
                    order=index,
                    page=index + 1,
                    metadata={
                        "parser_name": "pdf",
                        "used_ocr": False,
                        "reading_order": 1,
                        "layout_role": "body",
                    },
                )
                for index, part in enumerate(texts)
                if part
            ]
        return ParsedDocument(
            text=text,
            title=title_from_filename(filename),
            source=filename,
            extension="pdf",
            content_type=content_type,
            # used_ocr 字段为后续 OCR 能力预留，当前恒为 False。
            metadata={"parser_name": "pdf", "used_ocr": False},
            blocks=blocks,
        )
