"""云 OCR provider 测试。"""

from __future__ import annotations

import pytest


def test_cloud_ocr_prefers_dedicated_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    monkeypatch.setattr(settings, "RAG_OCR_BASE_URL", "https://ocr.example.com/v1")
    monkeypatch.setattr(settings, "RAG_OCR_API_KEY", "ocr-key")
    monkeypatch.setattr(settings, "RAG_OCR_MODEL", "ocr-model")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "llm-key")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "llm-model")

    provider = OpenAiVisionOcrProvider.from_settings()
    assert provider.base_url == "https://ocr.example.com/v1"
    assert provider.api_key == "ocr-key"
    assert provider.model == "ocr-model"


def test_cloud_ocr_falls_back_to_llm_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    monkeypatch.setattr(settings, "RAG_OCR_BASE_URL", "")
    monkeypatch.setattr(settings, "RAG_OCR_API_KEY", "")
    monkeypatch.setattr(settings, "RAG_OCR_MODEL", "")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "llm-key")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "llm-model")

    provider = OpenAiVisionOcrProvider.from_settings()
    assert provider.base_url == "https://llm.example.com/v1"
    assert provider.api_key == "llm-key"
    assert provider.model == "llm-model"


def test_cloud_ocr_reports_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.rag.ocr.base import OcrProviderError
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    monkeypatch.setattr(settings, "RAG_OCR_BASE_URL", "")
    monkeypatch.setattr(settings, "RAG_OCR_API_KEY", "")
    monkeypatch.setattr(settings, "RAG_OCR_MODEL", "")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_DEFAULT_MODEL", "")

    with pytest.raises(OcrProviderError, match="OCR 配置缺失"):
        OpenAiVisionOcrProvider.from_settings()


@pytest.mark.asyncio
async def test_cloud_ocr_reports_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from app.rag.ocr.base import OcrProviderError
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    provider = OpenAiVisionOcrProvider(
        base_url="https://ocr.example.com/v1",
        api_key="ocr-key",
        model="ocr-model",
        timeout_seconds=30.0,
    )
    monkeypatch.setattr(
        "app.rag.ocr.openai_vision._render_pdf_pages", lambda file_bytes: [b"page-1"]
    )

    async def fake_post(*args, **kwargs):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr("app.rag.ocr.openai_vision._post_vision_request", fake_post)
    with pytest.raises(OcrProviderError, match="云 OCR 调用失败"):
        await provider.aextract_pdf_text(b"%PDF")


@pytest.mark.asyncio
async def test_cloud_ocr_reports_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag.ocr.base import OcrProviderError
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider

    provider = OpenAiVisionOcrProvider(
        base_url="https://ocr.example.com/v1",
        api_key="ocr-key",
        model="ocr-model",
        timeout_seconds=30.0,
    )
    monkeypatch.setattr(
        "app.rag.ocr.openai_vision._render_pdf_pages", lambda file_bytes: [b"page-1"]
    )

    async def fake_post(*args, **kwargs):
        return {"choices": [{"message": {"content": ""}}]}

    monkeypatch.setattr("app.rag.ocr.openai_vision._post_vision_request", fake_post)
    with pytest.raises(OcrProviderError, match="OCR 已执行，但未提取到可用文本"):
        await provider.aextract_pdf_text(b"%PDF")


@pytest.mark.asyncio
async def test_cloud_ocr_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.ocr.openai_vision import OpenAiVisionOcrProvider
    from app.rag.ocr.tesseract import RenderedPdfPage

    provider = OpenAiVisionOcrProvider(
        base_url="https://ocr.example.com/v1",
        api_key="ocr-key",
        model="ocr-model",
        timeout_seconds=30.0,
    )
    monkeypatch.setattr(
        "app.rag.ocr.openai_vision._render_pdf_pages",
        lambda file_bytes: [
            RenderedPdfPage(image_bytes=b"page-1", page=1, bbox=(0.0, 0.0, 1080.0, 1440.0)),
            RenderedPdfPage(image_bytes=b"page-2", page=2, bbox=(0.0, 0.0, 1080.0, 1440.0)),
        ],
    )

    responses = iter(
        [
            {"choices": [{"message": {"content": "第一页文本"}}]},
            {"choices": [{"message": {"content": "第二页文本"}}]},
        ]
    )

    async def fake_post(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr("app.rag.ocr.openai_vision._post_vision_request", fake_post)
    result = await provider.aextract_pdf_text(b"%PDF")
    assert result.text == "第一页文本\n第二页文本"
    assert result.provider == "cloud_openai_vision"
    assert [block.page for block in result.blocks] == [1, 2]
    assert result.blocks[0].bbox == (0.0, 0.0, 1080.0, 1440.0)
