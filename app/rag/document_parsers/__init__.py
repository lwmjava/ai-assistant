"""文档解析层：将上传文件统一提取为可摄取纯文本。

对外导出解析服务入口、解析器协议、解析结果与各类解析异常，
供上传路由层直接引用；具体格式解析器按需在内部实现。
"""

from app.rag.document_parsers.base import (
    DocumentOcrRequiredError,
    DocumentParseError,
    DocumentParser,
    DocumentTextEmptyError,
    ParsedBlock,
    ParsedDocument,
    UnsupportedDocumentTypeError,
)
from app.rag.document_parsers.service import (
    DocumentParserService,
    build_default_parser_service,
    parse_uploaded_document,
)

__all__ = [
    "DocumentOcrRequiredError",
    "DocumentParseError",
    "DocumentParser",
    "DocumentParserService",
    "DocumentTextEmptyError",
    "ParsedBlock",
    "ParsedDocument",
    "UnsupportedDocumentTypeError",
    "build_default_parser_service",
    "parse_uploaded_document",
]
