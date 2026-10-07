"""OpenAI 兼容嵌入模型提供商。

OpenAI 官方嵌入接口与 Ollama 暴露的 ``/v1/embeddings`` 接口格式一致，
因此同一实现即可覆盖两者，通过 ``EMBEDDING_BASE_URL`` / ``EMBEDDING_API_KEY`` /
``EMBEDDING_MODEL`` 切换目标服务。

调用韧性（RAG-038）：每批请求都走 ``retry_async``，并复用当前检索的**共享**
deadline（``current_or_new_deadline()``）——嵌入只是检索链路的一环，不能自己
另起一个完整的预算。可重试故障（连接错误 / 读超时 / 429 / 5xx）才重试，
4xx 与业务错误只打一次；失败一律向上抛，绝不返回空向量冒充「查过了」。
"""

import logging
from collections.abc import Sequence

import httpx

from app.rag.embeddings.base import EmbeddingDimensionError, EmbeddingInputPolicy, EmbeddingProvider
from app.rag.embeddings.input_limits import resolve_input_policy
from app.rag.resilience import (
    Deadline,
    current_or_new_deadline,
    retry_async,
    retry_policy_from_settings,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """基于 OpenAI Embeddings 接口的提供商（兼容 Ollama）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dim: int = 1024,
        timeout: float = 15.0,
        batch_size: int = 10,
        input_policy: EmbeddingInputPolicy | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self.batch_size = max(1, batch_size)
        self.input_policy = input_policy or resolve_input_policy(self.base_url, model)
        if self.input_policy is not None and self.input_policy.max_batch_size is not None:
            self.batch_size = min(self.batch_size, self.input_policy.max_batch_size)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key or ''}",
            "Content-Type": "application/json",
        }

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        payload_texts = list(texts)
        if not payload_texts:
            return []
        if self.input_policy is None:
            raise ValueError("embedding_input_limit_unverified")
        for text in payload_texts:
            reason = self.input_policy.check(text)
            if reason:
                raise ValueError(f"embedding_{reason}")
        vectors: list[list[float]] = []
        # 共享 deadline：被后端组合点包裹时复用总预算，独立调用时按配置新起一个。
        deadline = current_or_new_deadline()
        policy = retry_policy_from_settings()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for start in range(0, len(payload_texts), self.batch_size):
                batch = payload_texts[start : start + self.batch_size]
                vectors.extend(
                    await retry_async(
                        lambda: self._post_batch(client, batch, deadline),
                        deadline=deadline,
                        policy=policy,
                        backend="embedding",
                    )
                )
        self._validate_dimensions(vectors)
        return vectors

    async def _post_batch(
        self, client: httpx.AsyncClient, batch: list[str], deadline: Deadline
    ) -> list[list[float]]:
        """发一批嵌入请求。

        单次超时夹到剩余预算内：这样「嵌入批 1 慢」会压缩批 2 与后续向量检索
        的时间，而不是各自再拿一个完整的 ``self.timeout``。
        """
        resp = await client.post(
            f"{self.base_url}/embeddings",
            headers=self._headers(),
            json={"model": self.model, "input": batch},
            timeout=deadline.subcall_timeout(self.timeout),
        )
        if resp.is_error:
            # 只记状态码与批大小：正文可能含密钥回显或用户输入，一律不落盘。
            logger.error(
                "embedding_request_failed status=%s batch_size=%s",
                resp.status_code,
                len(batch),
            )
        resp.raise_for_status()
        data = resp.json()["data"]
        # 接口可能乱序返回，按 batch 内 index 排序以保证与输入对齐。
        data_sorted = sorted(data, key=lambda d: d.get("index", 0))
        return [item["embedding"] for item in data_sorted]

    def _validate_dimensions(self, vectors: list[list[float]]) -> None:
        """校验返回向量维度与配置一致，避免静默的检索错位。"""
        for vector in vectors:
            actual = len(vector)
            if actual != self.dim:
                raise EmbeddingDimensionError(
                    f"嵌入模型返回维度 {actual} 与配置 EMBEDDING_DIM={self.dim} 不一致，"
                    f"请检查模型 {self.model} 的实际输出维度并修正配置"
                )
