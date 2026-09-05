"""文档解析器协议与公共模型。

定义解析器统一接口（``DocumentParser``）、统一解析结果（``ParsedDocument``）
以及解析层专用异常。所有具体格式解析器都实现该接口，
由注册表按扩展名/内容类型调度，路由层据此得到可摄取纯文本。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class DocumentParserError(ValueError):
    """文档解析基类异常。

    所有解析阶段的可识别错误都继承自本异常，便于路由层统一映射为 HTTP 响应。
    """


class UnsupportedDocumentTypeError(DocumentParserError):
    """文件类型不受支持（注册表未命中任何解析器）。"""


class DocumentParseError(DocumentParserError):
    """文件内容无法解析（损坏、加密或结构异常等）。"""


class DocumentTextEmptyError(DocumentParserError):
    """文件解析成功但未提取出可用文本。"""


class DocumentOcrRequiredError(DocumentParserError):
    """文件需要 OCR 才能继续解析（如扫描件 PDF）。"""


@dataclass(slots=True)
class ParsedDocument:
    """统一的文档解析结果。

    各字段在解析完成后统一归一化，供路由层直接消费：
    ``text`` 为最终可摄取文本，``title``/``source`` 由文件名派生，
    ``metadata`` 保留解析器名等扩展信息以便写入审计或后续扩展。
    """

    text: str
    title: str
    source: str
    extension: str
    content_type: str | None
    metadata: dict[str, str | int | bool | None] = field(default_factory=dict)


class DocumentParser(ABC):
    """文档解析器统一接口。

    每个具体格式解析器实现两个职责：
    - ``supports``：判断是否能处理给定文件（通常按扩展名/内容类型）；
    - ``extract``：从原始字节中提取文本并返回 ``ParsedDocument``。
    解析失败应抛出 ``DocumentParserError`` 体系中的对应异常。
    """

    @abstractmethod
    def supports(self, filename: str, content_type: str | None) -> bool:
        """判断是否能处理该文件，命中返回 True。"""
        raise NotImplementedError

    @abstractmethod
    def extract(
        self, file_bytes: bytes, filename: str, content_type: str | None
    ) -> ParsedDocument:
        """解析原始字节并返回归一化后的解析结果。"""
        raise NotImplementedError
