"""自研 RAG 后端：切分委托策略层（结构化），检索走 ``hybrid_search``。

调用韧性（RAG-038）：一次检索 = 「嵌入 + 向量检索」，两者装进**同一个**总
deadline（``bind_deadline``）。子调用通过上下文拿到同一个 deadline 实例并据
此夹自己的超时，因此总耗时有界；预算在向量检索前耗尽就不再发起查询。
故障一律向上抛，由 RAG-037 的检索状态契约收敛为 ``unavailable``。
"""

import asyncio
from collections.abc import Callable

from app.core.config import settings
from app.rag.access import ReadScope
from app.rag.backend.base import RagBackend
from app.rag.effective_date import retrieval_window
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.index_identity import EmbeddingIndexIdentity, identity_from_provider
from app.rag.resilience import (
    Deadline,
    DeadlineExceededError,
    bind_deadline,
    current_deadline,
    ensure_budget,
)
from app.rag.retrieval_guard import candidate_k, drop_injected_chunks
from app.rag.vectorstore.base import ChunkResult, VectorStore

Tokenizer = Callable[[str], list[str]]


class NativeRagBackend(RagBackend):
    """默认后端：行为与接入策略层之前的自研管线一致。"""

    name = "native"

    def __init__(
        self,
        embedding: EmbeddingProvider,
        vector_store: VectorStore,
        tokenizer: Tokenizer,
        rrf_k: int = 60,
    ) -> None:
        self._embedding = embedding
        self._store = vector_store
        self._tokenizer = tokenizer
        self._rrf_k = rrf_k
        # 当前查询 provider 的完整索引身份：惰性计算并缓存，
        # 读路径每次都拿它与生效索引核对，阻断同维异模型混用。
        self._identity: EmbeddingIndexIdentity | None = None

    def _current_identity(self) -> EmbeddingIndexIdentity:
        if self._identity is None:
            self._identity = identity_from_provider(self._embedding)
        return self._identity

    async def split(self, text: str, *, chunk_size: int, overlap: int) -> list[str]:
        from app.rag.chunking.base import ChunkParams
        from app.rag.chunking.factory import get_chunking_strategy

        chunks = await get_chunking_strategy("structured").split(
            text, params=ChunkParams(chunk_size=chunk_size, chunk_overlap=overlap)
        )
        return [c.text for c in chunks]

    async def retrieve(
        self,
        query: str,
        *,
        tenant_id: str,
        top_k: int,
        read_scope: ReadScope | None = None,
    ) -> list[ChunkResult]:
        # 一次检索一个总预算：嵌入与向量检索共享它，而不是各起一个。
        # 上层（如请求级）已经绑定过预算时直接复用，不另起一个。
        deadline = current_deadline() or Deadline(settings.RAG_RETRIEVAL_DEADLINE_SECONDS)
        if not deadline.enforced:
            return await self._retrieve(query, tenant_id, top_k, read_scope)
        with bind_deadline(deadline):
            try:
                return await asyncio.wait_for(
                    self._retrieve(query, tenant_id, top_k, read_scope),
                    timeout=deadline.remaining(),
                )
            # 总时限到期：不返回空结果，向上抛由检索状态契约收敛为 unavailable。
            # CancelledError 是 BaseException，不会被这里捕获，取消照常上抛。
            except TimeoutError as exc:
                raise DeadlineExceededError(
                    f"检索总时限 {deadline.total:.3f}s 内未完成"
                ) from exc

    async def _retrieve(
        self,
        query: str,
        tenant_id: str,
        top_k: int,
        read_scope: ReadScope | None,
    ) -> list[ChunkResult]:
        embedding = (await self._embedding.embed([query]))[0]
        tokens = self._tokenizer(query)
        as_of, schedule_at = retrieval_window(query)
        # 嵌入已经吃掉一部分预算；剩下的不够就别再打向量库，
        # 否则「两个都没超单次 timeout」累加起来仍然拖满整条链路。
        deadline = current_deadline()
        if deadline is not None:
            ensure_budget(deadline, backend="native", tenant=tenant_id)
        hits = await self._store.hybrid_search(
            embedding,
            tokens,
            tenant_id,
            candidate_k(top_k),
            self._rrf_k,
            as_of=as_of,
            schedule_at=schedule_at,
            read_scope=read_scope,
            identity=self._current_identity(),
        )
        # 双保险：store 恰好在 deadline 耗尽后才把结果交回来时，不能把这批迟到 hits
        # 当成正常命中 / 收敛成 no_hit——那等于把超时故障伪装成「查过了、没有」。
        # to_thread 已让等待有界，这里兜住「刚到点就返回」的边界，不往上送迟到结果。
        if deadline is not None and deadline.expired():
            raise DeadlineExceededError(
                f"向量检索在总时限 {deadline.total:.3f}s 耗尽后才返回结果，拒绝当命中"
            )
        return drop_injected_chunks(hits, keep=top_k)
