"""OCR 模块导出。"""

from app.rag.ocr.base import OcrPageBlock, OcrProvider, OcrProviderError, OcrResult
from app.rag.ocr.factory import get_ocr_provider
from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider
from app.rag.ocr.tesseract import TesseractOcrProvider

__all__ = [
    "OcrProvider",
    "OcrProviderError",
    "OcrPageBlock",
    "OcrResult",
    "OpenAiVisionOcrProvider",
    "TesseractOcrProvider",
    "get_ocr_provider",
]
