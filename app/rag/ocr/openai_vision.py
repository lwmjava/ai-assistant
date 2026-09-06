"""OpenAI 兼容视觉云 OCR provider。"""

from __future__ import annotations

import base64

import httpx

from app.core.config import settings
from app.rag.ocr.base import OcrPageBlock, OcrProvider, OcrProviderError, OcrResult
from app.rag.ocr.tesseract import RenderedPdfPage, _render_pdf_pages


def _build_payload(model: str, image_bytes: bytes) -> dict:
    """构造 OpenAI 兼容视觉请求体。"""
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "请提取这页图片中的全部可读文本，不要总结，只输出文本内容。",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                ],
            }
        ],
    }


def _extract_content(data: dict) -> str:
    """从 OpenAI 兼容响应里提取文本。"""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [
            str(item.get("text") or "").strip()
            for item in content
            if isinstance(item, dict)
        ]
        return "\n".join(part for part in texts if part).strip()
    return ""


async def _post_vision_request(
    *,
    base_url: str,
    api_key: str,
    model: str,
    image_bytes: bytes,
    timeout_seconds: float,
) -> dict:
    """异步调用 OpenAI 兼容视觉接口。"""
    payload = _build_payload(model, image_bytes)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    response.raise_for_status()
    return response.json()


def _post_vision_request_sync(
    *,
    base_url: str,
    api_key: str,
    model: str,
    image_bytes: bytes,
    timeout_seconds: float,
) -> dict:
    """同步调用 OpenAI 兼容视觉接口。"""
    payload = _build_payload(model, image_bytes)
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    response.raise_for_status()
    return response.json()


class OpenAiVisionOcrProvider(OcrProvider):
    """基于 OpenAI 兼容视觉接口的云 OCR 实现。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> OpenAiVisionOcrProvider:
        """按 OCR 专用配置优先、LLM 配置回退的规则构建 provider。"""
        base_url = settings.RAG_OCR_BASE_URL or settings.LLM_BASE_URL
        api_key = settings.RAG_OCR_API_KEY or settings.LLM_API_KEY
        model = settings.RAG_OCR_MODEL or settings.LLM_DEFAULT_MODEL
        if not base_url or not api_key or not model:
            raise OcrProviderError(
                "OCR 配置缺失，请检查 RAG_OCR_BASE_URL / RAG_OCR_API_KEY / "
                "RAG_OCR_MODEL 或现有 LLM 配置",
                error_code="ocr_cloud_config_missing",
            )
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=settings.RAG_OCR_TIMEOUT_SECONDS,
        )

    async def aextract_pdf_text(self, file_bytes: bytes) -> OcrResult:
        """异步逐页调用视觉模型提取 PDF 文本。"""
        texts: list[str] = []
        blocks: list[OcrPageBlock] = []
        try:
            for index, rendered_page in enumerate(_render_pdf_pages(file_bytes), start=1):
                if isinstance(rendered_page, RenderedPdfPage):
                    image_bytes = rendered_page.image_bytes
                    page_number = rendered_page.page
                    bbox = rendered_page.bbox
                else:  # 兼容测试中直接 patch 为 bytes 的旧写法
                    image_bytes = rendered_page
                    page_number = index
                    bbox = None
                data = await _post_vision_request(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    model=self.model,
                    image_bytes=image_bytes,
                    timeout_seconds=self.timeout_seconds,
                )
                content = _extract_content(data)
                if content:
                    texts.append(content)
                    blocks.append(
                        OcrPageBlock(
                            page=page_number,
                            text=content,
                            bbox=bbox,
                            reading_order=1,
                            layout_role="body",
                        )
                    )
        except httpx.HTTPError as exc:
            raise OcrProviderError(
                "云 OCR 调用失败，请检查网络、鉴权或服务配置",
                error_code="ocr_cloud_http_error",
                command=f"POST {self.base_url.rstrip('/')}/chat/completions",
                stderr=str(exc),
            ) from exc

        text = "\n".join(texts).strip()
        if not text:
            raise OcrProviderError("OCR 已执行，但未提取到可用文本", error_code="ocr_empty_result")
        return OcrResult(
            text=text, provider="cloud_openai_vision", used_ocr=True, blocks=blocks
        )

    def extract_pdf_text(self, file_bytes: bytes) -> OcrResult:
        """同步逐页调用视觉模型提取 PDF 文本。"""
        texts: list[str] = []
        blocks: list[OcrPageBlock] = []
        try:
            for index, rendered_page in enumerate(_render_pdf_pages(file_bytes), start=1):
                if isinstance(rendered_page, RenderedPdfPage):
                    image_bytes = rendered_page.image_bytes
                    page_number = rendered_page.page
                    bbox = rendered_page.bbox
                else:  # 兼容测试中直接 patch 为 bytes 的旧写法
                    image_bytes = rendered_page
                    page_number = index
                    bbox = None
                data = _post_vision_request_sync(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    model=self.model,
                    image_bytes=image_bytes,
                    timeout_seconds=self.timeout_seconds,
                )
                content = _extract_content(data)
                if content:
                    texts.append(content)
                    blocks.append(
                        OcrPageBlock(
                            page=page_number,
                            text=content,
                            bbox=bbox,
                            reading_order=1,
                            layout_role="body",
                        )
                    )
        except httpx.HTTPError as exc:
            raise OcrProviderError(
                "云 OCR 调用失败，请检查网络、鉴权或服务配置",
                error_code="ocr_cloud_http_error",
                command=f"POST {self.base_url.rstrip('/')}/chat/completions",
                stderr=str(exc),
            ) from exc

        text = "\n".join(texts).strip()
        if not text:
            raise OcrProviderError("OCR 已执行，但未提取到可用文本", error_code="ocr_empty_result")
        return OcrResult(
            text=text, provider="cloud_openai_vision", used_ocr=True, blocks=blocks
        )
