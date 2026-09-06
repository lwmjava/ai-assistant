"""OCR 抽象与公共模型。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.rag.import_trace import ImportTraceMixin


class OcrProviderError(RuntimeError, ImportTraceMixin):
    """OCR provider 统一错误。"""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "ocr",
        error_code: str | None = None,
        command: str | None = None,
        exit_code: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self._init_import_trace(
            stage=stage,
            error_code=error_code,
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )


@dataclass(slots=True)
class OcrPageBlock:
    """OCR 页级结果块。"""

    page: int
    text: str
    bbox: tuple[float, float, float, float] | None = None
    reading_order: int = 1
    layout_role: str = "body"


@dataclass(slots=True)
class OcrResult:
    """OCR 识别结果。"""

    text: str
    provider: str
    used_ocr: bool = True
    blocks: list[OcrPageBlock] = field(default_factory=list)


class OcrProvider(ABC):
    """OCR provider 统一接口。"""

    @abstractmethod
    def extract_pdf_text(self, file_bytes: bytes) -> OcrResult:
        """从 PDF 中提取文本。"""
        raise NotImplementedError
