"""混合检索器（管线 Retriever 钩子实现）。

将用户问题与规划步骤拼接为检索语句，先经嵌入模型取得稠密向量，再做混合检索，
最后拼接为上下文文本，供 Agent 编排管线「行动」阶段注入外部知识。
"""

from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.mock import tokenize
from app.rag.vectorstore.base import VectorStore


class HybridRetriever:
    """面向管线协议的混合检索器：``retrieve(query, plan) -> 上下文文本``。"""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        tenant_id: str,
        top_k: int = 5,
        rrf_k: int = 60,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.tenant_id = tenant_id
        self.top_k = top_k
        self.rrf_k = rrf_k

    async def retrieve(self, query: str, plan: str) -> str:
        """返回与问题相关的外部上下文文本（无结果时返回空串）。"""
        search_text = f"{query}\n{plan}".strip() if plan else (query or "").strip()
        if not search_text:
            return ""

        embedding = (await self.embedding_provider.embed([search_text]))[0]
        tokens = tokenize(search_text)
        results = await self.vector_store.hybrid_search(
            embedding, tokens, self.tenant_id, self.top_k, self.rrf_k
        )
        if not results:
            return ""

        blocks: list[str] = []
        for idx, hit in enumerate(results, 1):
            source = f"（来源：{hit.source}）" if hit.source else ""
            blocks.append(f"[资料 {idx}]{source}\n{hit.content}")
        return "\n\n".join(blocks)
