"""文档解析器注册表。

按注册顺序调度解析器，根据文件名与内容类型匹配到第一个可处理的解析器；
如无命中则抛出 ``UnsupportedDocumentTypeError``，将“不支持”从业务逻辑中解耦。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.rag.document_parsers.base import (
    DocumentParser,
    UnsupportedDocumentTypeError,
)


class DocumentParserRegistry:
    """按顺序解析上传文件类型。

    解析器以列表方式组织，注册顺序即匹配优先级；后续新增格式只需 ``register``
    即可接入，无需改动路由或服务层。
    """

    def __init__(self, parsers: Sequence[DocumentParser] | None = None) -> None:
        self._parsers = list(parsers or [])

    def register(self, parser: DocumentParser) -> None:
        """追加一个解析器到匹配列表末尾。"""
        self._parsers.append(parser)

    def resolve(self, filename: str, content_type: str | None) -> DocumentParser:
        """按注册顺序返回首个可处理该文件的解析器。

        无命中时抛出 ``UnsupportedDocumentTypeError``，并给出支持的格式提示。
        """
        for parser in self._parsers:
            if parser.supports(filename, content_type):
                return parser
        raise UnsupportedDocumentTypeError(
            "当前仅支持 txt、md、json、xml、csv、docx、xlsx、pptx、pdf 文件"
        )
