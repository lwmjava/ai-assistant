"""文档解析服务：上传文件的统一解析入口。

对外仅暴露 ``parse_uploaded_document``，内部通过注册表调度具体解析器，
并对解析结果做标题/来源/文本的归一化与空文本校验，供上传路由层直接调用。
"""

from __future__ import annotations

from app.rag.document_parsers.base import DocumentTextEmptyError, ParsedDocument
from app.rag.document_parsers.office import (
    DocxDocumentParser,
    PptxDocumentParser,
    XlsxDocumentParser,
)
from app.rag.document_parsers.pdf import PdfDocumentParser
from app.rag.document_parsers.registry import DocumentParserRegistry
from app.rag.document_parsers.text import TextDocumentParser, title_from_filename


class DocumentParserService:
    """统一的上传文档解析入口。"""

    def __init__(self, registry: DocumentParserRegistry) -> None:
        self._registry = registry

    def parse_upload(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """解析上传文件并返回归一化后的结果。

        流程：注册表选解析器 → 提取文本 → 归一化标题/来源 → 校验非空文本。
        空文本抛出 ``DocumentTextEmptyError``。
        """
        parser = self._registry.resolve(filename, content_type)
        parsed = parser.extract(file_bytes, filename, content_type)
        title = parsed.title.strip() if parsed.title.strip() else title_from_filename(filename)
        source = parsed.source.strip() if parsed.source.strip() else filename
        text = parsed.text.strip()
        if not text:
            raise DocumentTextEmptyError("文件中未提取到可用文本")
        return ParsedDocument(
            text=text,
            title=title,
            source=source,
            extension=parsed.extension,
            content_type=parsed.content_type,
            metadata=dict(parsed.metadata),
        )


def build_default_parser_service() -> DocumentParserService:
    """构建注册了内置格式解析器的默认服务。"""
    registry = DocumentParserRegistry(
        [
            TextDocumentParser(),
            DocxDocumentParser(),
            XlsxDocumentParser(),
            PptxDocumentParser(),
            PdfDocumentParser(),
        ]
    )
    return DocumentParserService(registry)


# 进程级默认服务单例，避免每次上传重复构建注册表与解析器实例。
_DEFAULT_SERVICE = build_default_parser_service()


def parse_uploaded_document(
    file_bytes: bytes, filename: str, content_type: str | None
) -> ParsedDocument:
    """对上传文件做多格式文本提取（模块对外主入口）。"""
    return _DEFAULT_SERVICE.parse_upload(file_bytes, filename, content_type)
