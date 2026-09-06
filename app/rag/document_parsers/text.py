"""文本类文件解析器。

统一处理 txt / md / json / xml / csv 等基于纯文本编码的文件，
按多种常见编码顺序尝试解码，避免把“编码”这一实现细节暴露给用户。
"""

from __future__ import annotations

import os

from app.rag.document_parsers.base import (
    DocumentParseError,
    DocumentParser,
    ParsedBlock,
    ParsedDocument,
)

# 解码尝试顺序：优先带 BOM 的 UTF-8，再纯 UTF-8、UTF-16，最后中文常用 GB18030。
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "gb18030")
_TEXT_EXTENSIONS = {"txt", "md", "json", "xml", "csv"}


def file_extension(filename: str) -> str:
    """返回小写的扩展名（不含点），供解析器匹配使用。"""
    return os.path.splitext(filename)[1].lstrip(".").lower()


def title_from_filename(filename: str) -> str:
    """从文件名派生标题：去目录与扩展名，空则回到占位名。"""
    title = os.path.splitext(os.path.basename(filename))[0]
    return title or "未命名文档"


def decode_text_bytes(file_bytes: bytes) -> str:
    """按预设编码顺序解码字节，全部失败则抛 ``DocumentParseError``。"""
    for encoding in _TEXT_ENCODINGS:
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentParseError("文件解析失败，请确认文件未损坏或未加密")


class TextDocumentParser(DocumentParser):
    """解析常见文本文件（txt/md/json/xml/csv）。"""

    def supports(self, filename: str, content_type: str | None) -> bool:
        """扩展名命中文本类集合时返回 True。"""
        return file_extension(filename) in _TEXT_EXTENSIONS

    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """解码原始字节并返回文本解析结果，来源沿用原文件名。"""
        text = decode_text_bytes(file_bytes)
        blocks = [
            ParsedBlock(
                type="text",
                text=line,
                order=index,
                metadata={
                    "parser_name": "text",
                    "reading_order": index + 1,
                    "layout_role": "body",
                },
            )
            for index, line in enumerate(part.strip() for part in text.splitlines())
            if line
        ]
        return ParsedDocument(
            text=text,
            title=title_from_filename(filename),
            source=filename,
            extension=file_extension(filename),
            content_type=content_type,
            metadata={"parser_name": "text"},
            blocks=blocks,
        )
