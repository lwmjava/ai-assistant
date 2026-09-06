"""OCR provider 工厂。"""

from app.core.config import settings
from app.rag.ocr.base import OcrProvider, OcrProviderError
from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider
from app.rag.ocr.tesseract import TesseractOcrProvider


def get_ocr_provider() -> OcrProvider:
    """根据配置返回 OCR provider。"""
    if not settings.RAG_OCR_ENABLED:
        raise OcrProviderError("该 PDF 需要 OCR 才能提取文本，但当前系统未启用 OCR")
    if settings.RAG_OCR_PROVIDER == "tesseract":
        return TesseractOcrProvider(
            languages=settings.RAG_OCR_LANGUAGES,
            timeout_seconds=settings.RAG_OCR_TIMEOUT_SECONDS,
        )
    if settings.RAG_OCR_PROVIDER == "cloud":
        return OpenAiVisionOcrProvider.from_settings()
    raise OcrProviderError(f"不支持的 OCR provider: {settings.RAG_OCR_PROVIDER}")
