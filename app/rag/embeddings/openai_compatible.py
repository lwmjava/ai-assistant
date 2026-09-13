"""OpenAI 兼容嵌入模型提供商。

OpenAI 官方嵌入接口与 Ollama 暴露的 ``/v1/embeddings`` 接口格式一致，
因此同一实现即可覆盖两者，通过 ``EMBEDDING_BASE_URL`` / ``EMBEDDING_API_KEY`` /
``EMBEDDING_MODEL`` 切换目标服务。
"""

import logging
from collections.abc import Sequence

import httpx

from app.rag.embeddings.base import EmbeddingDimensionError, EmbeddingProvider

logger = logging.getLogger(__name__)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """基于 OpenAI Embeddings 接口的提供商（兼容 Ollama）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dim: int = 1024,
        timeout: float = 60.0,
        batch_size: int = 10,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self.batch_size = max(1, batch_size)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key or ''}",
            "Content-Type": "application/json",
        }

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        payload_texts = list(texts)
        if not payload_texts:
            return []
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for start in range(0, len(payload_texts), self.batch_size):
                batch = payload_texts[start : start + self.batch_size]
                resp = await client.post(
                    f"{self.base_url}/embeddings",
                    headers=self._headers(),
                    json={"model": self.model, "input": batch},
                )
                if resp.is_error:
                    logger.error(
                        "嵌入接口失败: status=%s model=%s batch_size=%s body=%s",
                        resp.status_code,
                        self.model,
                        len(batch),
                        resp.text[:500],
                    )
                resp.raise_for_status()
                data = resp.json()["data"]
                # 接口可能乱序返回，按 batch 内 index 排序以保证与输入对齐。
                data_sorted = sorted(data, key=lambda d: d.get("index", 0))
                vectors.extend(item["embedding"] for item in data_sorted)
        self._validate_dimensions(vectors)
        return vectors

    def _validate_dimensions(self, vectors: list[list[float]]) -> None:
        """校验返回向量维度与配置一致，避免静默的检索错位。"""
        for vector in vectors:
            actual = len(vector)
            if actual != self.dim:
                raise EmbeddingDimensionError(
                    f"嵌入模型返回维度 {actual} 与配置 EMBEDDING_DIM={self.dim} 不一致，"
                    f"请检查模型 {self.model} 的实际输出维度并修正配置"
                )
