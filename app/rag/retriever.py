"""混合检索器（管线 Retriever 钩子实现）。

将用户问题与规划步骤拼接为检索语句，委托 ``RagBackend.retrieve`` 取得结构化
分块，再拼接为上下文文本，供 Agent 编排管线「行动」阶段注入外部知识。

文本拼接属于呈现策略，与用哪套后端无关，抽成 ``format_context`` 供各处复用。
"""

from app.rag.backend.base import RagBackend
from app.rag.vectorstore.base import ChunkResult


def format_context(chunks: list[ChunkResult]) -> str:
    """将检索命中拼接为管线可用的上下文字符串；无结果返回空串。"""
    if not chunks:
        return ""
    blocks: list[str] = []
    for idx, hit in enumerate(chunks, 1):
        source = f"（来源：{hit.source}）" if hit.source else ""
        blocks.append(f"[资料 {idx}]{source}\n{hit.content}")
    return "\n\n".join(blocks)


class HybridRetriever:
    """面向管线协议的混合检索器：``retrieve(query, plan) -> 上下文文本``。"""

    def __init__(self, backend: RagBackend, tenant_id: str, top_k: int = 5) -> None:
        self.backend = backend
        self.tenant_id = tenant_id
        self.top_k = top_k

    async def retrieve(self, query: str, plan: str) -> str:
        """返回与问题相关的外部上下文文本（无结果时返回空串）。"""
        search_text = f"{query}\n{plan}".strip() if plan else (query or "").strip()
        if not search_text:
            return ""
        results = await self.backend.retrieve(
            search_text, tenant_id=self.tenant_id, top_k=self.top_k
        )
        return format_context(results)
