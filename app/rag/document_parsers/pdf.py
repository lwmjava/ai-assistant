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
    ParsedDocument,
)
from app.rag.document_parsers.text import file_extension, title_from_filename

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - 依赖缺失时由运行时错误提示
    PdfReader = None


class PdfDocumentParser(DocumentParser):
    """解析可提取文本的 PDF；扫描件返回需 OCR 的错误。"""

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
            # 可打开但无文本：近似判断为扫描件，交由 OCR（当前未启用）处理。
            raise DocumentOcrRequiredError(
                "该 PDF 需要 OCR 才能提取文本，当前系统未启用 OCR"
            )

        return ParsedDocument(
            text=text,
            title=title_from_filename(filename),
            source=filename,
            extension="pdf",
            content_type=content_type,
            # used_ocr 字段为后续 OCR 能力预留，当前恒为 False。
            metadata={"parser_name": "pdf", "used_ocr": False},
        )
