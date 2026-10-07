"""混合检索器（管线 Retriever 钩子实现）。

将用户问题与规划步骤拼接为检索语句，委托 ``RagBackend.retrieve`` 取得结构化
分块，再拼接为上下文文本，供 Agent 编排管线「行动」阶段注入外部知识。

文本拼接属于呈现策略，与用哪套后端无关，抽成 ``format_context`` 供各处复用。
"""

import logging

from app.core.config import settings
from app.rag.access import ReadScope
from app.rag.backend.base import RagBackend
from app.rag.context_builder import (
    FENCE_CLOSE,
    UNTRUSTED_PREAMBLE,
    render_block,
)
from app.rag.retrieval_status import RetrievalOutcome, RetrievalStatus, classify
from app.rag.vectorstore.base import ChunkResult

logger = logging.getLogger(__name__)

# 前导说明的唯一定义在 app.rag.context_builder，避免两处围栏文案漂移。
_UNTRUSTED_PREAMBLE = UNTRUSTED_PREAMBLE


def format_context(chunks: list[ChunkResult]) -> str:
    """将检索命中拼接为管线可用的上下文字符串；无结果返回空串。

    命中一律包在不可信围栏内。注入块应在检索后处理中剔除，不依赖模型自觉。
    渲染与围栏拼接统一走 ``context_builder``，保证与按块预算组装出的文本同源。
    """
    if not chunks:
        return ""
    blocks = [render_block(idx, hit) for idx, hit in enumerate(chunks, 1)]
    return _UNTRUSTED_PREAMBLE + "\n\n".join(blocks) + "\n" + FENCE_CLOSE


class HybridRetriever:
    """面向管线协议的混合检索器：``retrieve(query, plan) -> 上下文文本``。"""

    def __init__(
        self,
        backend: RagBackend,
        tenant_id: str,
        top_k: int = 5,
        read_scope: ReadScope | None = None,
    ) -> None:
        self.backend = backend
        self.tenant_id = tenant_id
        self.top_k = top_k
        # 鉴权主体的有效读范围；None 等同同租户全可读，不构成 uploader 隔离。
        self.read_scope = read_scope
        self.last_hits: list[ChunkResult] = []
        # 上一次检索中**实际进入模型 payload** 的命中。由管线在按块预算组装后
        # 回写（见 app.agents.pipeline._fill_retrieval）。``last_hits`` 是阈值过滤
        # 后的全部命中，两者不等：预算挤掉的块只在 last_hits 里。
        # None 表示这次检索还没有组装过 payload，读来源时回落 last_hits。
        self.last_selected: list[ChunkResult] | None = None
        # 上一次检索的终态。入口据此决定「是否必须向用户披露」，
        # 不能只靠解析返回文本来猜。
        self.last_status: RetrievalStatus = RetrievalStatus.NO_HIT
        self.last_outcome: RetrievalOutcome = RetrievalOutcome(
            status=RetrievalStatus.NO_HIT, backend=getattr(backend, "name", "")
        )

    async def retrieve(self, query: str, plan: str) -> str:
        """返回与问题相关的外部上下文文本。

        命中经稠密相似度阈值（``RAG_MIN_SIMILARITY``）过滤；全部被过滤且原本
        有命中时返回明确拒答提示，不硬凑回答。无结果返回空串。

        检索没跑完（嵌入 / 向量库 / 后端异常）时**不向上抛出**：异常只会让
        请求整体失败，用户得不到任何说明。改为收敛为 ``unavailable`` 状态并
        由入口强制披露，日志保留完整堆栈。
        """
        search_text = f"{query}\n{plan}".strip() if plan else (query or "").strip()
        if not search_text:
            self._settle(RetrievalOutcome(status=RetrievalStatus.NO_HIT, hits=[]))
            return ""
        error_type = ""
        results: list[ChunkResult] = []
        try:
            raw = await self.backend.retrieve(
                search_text,
                tenant_id=self.tenant_id,
                top_k=self.top_k,
                read_scope=self.read_scope,
            )
            # 返回值校验也在收敛边界内：后端违约返回 None / 不可迭代时
            # list() 抛 TypeError，被同一处收敛为 unavailable。若在边界外迭代，
            # 状态会停留在一个误导性的旧值上。
            results = list(raw)
        except Exception as exc:  # noqa: BLE001 — 任何检索故障都收敛为 unavailable
            error_type = type(exc).__name__
            logger.exception(
                "rag_retrieval_unavailable exception_type=%s tenant=%s",
                error_type,
                self.tenant_id,
            )
        threshold = settings.RAG_MIN_SIMILARITY
        kept = [hit for hit in results if hit.similarity >= threshold]
        dropped = len(results) - len(kept)
        status = classify(error_type=error_type, candidates=len(results), kept=len(kept))
        if dropped > 0:
            logger.info(
                "rag_low_similarity_filtered kept=%s/%s dropped=%s threshold=%s",
                len(kept),
                len(results),
                dropped,
                threshold,
            )
        outcome = RetrievalOutcome(
            status=status,
            hits=list(kept),
            dropped_by_threshold=max(0, dropped),
            backend=getattr(self.backend, "name", ""),
            error_type=error_type,
        )
        self._settle(outcome)
        if error_type:
            # 检索未完成：不注入任何资料，也不冒充「没有相关资料」。
            return ""
        if kept:
            return format_context(kept)
        # 全部被低分过滤：拒答，给出明确提示语，不硬凑。
        if results:
            return settings.RAG_REFUSE_MESSAGE
        return ""

    def _settle(self, outcome: RetrievalOutcome) -> None:
        """写入上一次检索的终态与命中，供入口读取。"""
        self.last_outcome = outcome
        self.last_status = outcome.status
        self.last_hits = list(outcome.hits)
        # 每次检索都先把「已选入」清空，避免上一轮的 selected 残留到这一轮。
        self.last_selected = None
