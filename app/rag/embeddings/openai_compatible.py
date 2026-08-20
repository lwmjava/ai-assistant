"""OpenAI 兼容嵌入模型提供商。

OpenAI 官方嵌入接口与 Ollama 暴露的 ``/v1/embeddings`` 接口格式一致，
因此同一实现即可覆盖两者，通过 ``EMBEDDING_BASE_URL`` / ``EMBEDDING_API_KEY`` /
``EMBEDDING_MODEL`` 切换目标服务。
"""

import logging

import httpx

from app.rag.embeddings.base import EmbeddingProvider

logger = logging.getLogger(__name__)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """基于 OpenAI Embeddings 接口的提供商（兼容 Ollama）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dim: int = 1536,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key or ''}",
            "Content-Type": "application/json",
        }

    async def embed(self, texts: list[str]) -> list[list[float]]:
        payload_texts = list(texts)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers=self._headers(),
                json={"model": self.model, "input": payload_texts},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
        # 接口可能乱序返回，按 index 排序以保证与输入对齐。
        data_sorted = sorted(data, key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data_sorted]
