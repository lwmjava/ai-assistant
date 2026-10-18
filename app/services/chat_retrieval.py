"""对话请求内的惰性检索钩子；读取来源元数据不会创建外部依赖。"""

import logging
from collections.abc import Callable

from app.rag.retrieval_status import RetrievalOutcome, RetrievalStatus
from app.rag.retriever import HybridRetriever
from app.rag.vectorstore.base import ChunkResult

logger = logging.getLogger(__name__)


class ChatRetriever:
    """保留 Retriever 及选块协议，直到真正检索才创建请求内依赖。"""

    def __init__(self, factory: Callable[[], HybridRetriever]) -> None:
        self._factory = factory
        self._delegate: HybridRetriever | None = None
        self._initialization_error = ""
        self.last_hits: list[ChunkResult] = []
        self.last_selected: list[ChunkResult] | None = None
        self.last_status = RetrievalStatus.NO_HIT
        self.last_outcome = RetrievalOutcome(status=RetrievalStatus.NO_HIT)

    def _unavailable(self, error_type: str) -> str:
        self.last_hits = []
        self.last_selected = []
        self.last_status = RetrievalStatus.UNAVAILABLE
        self.last_outcome = RetrievalOutcome(status=self.last_status, error_type=error_type)
        return ""

    async def retrieve(self, query: str, plan: str) -> str:
        # 任何失败/取消都不能把上一轮已选来源带到本轮。
        self.last_hits = []
        self.last_selected = None
        self.last_status = RetrievalStatus.NO_HIT
        self.last_outcome = RetrievalOutcome(status=self.last_status)
        if self._initialization_error:
            return self._unavailable(self._initialization_error)
        if self._delegate is None:
            try:
                self._delegate = self._factory()
            except Exception as exc:  # noqa: BLE001 — 依赖构造失败沿原检索不可用契约收敛
                self._initialization_error = type(exc).__name__
                logger.warning("chat_retrieval_initialization_failed exception_type=%s", self._initialization_error)
                return self._unavailable(self._initialization_error)
        try:
            text = await self._delegate.retrieve(query, plan)
        except Exception as exc:  # noqa: BLE001 — 不记录可能含凭据的异常原文或堆栈
            error_type = type(exc).__name__
            logger.warning("chat_retrieval_failed exception_type=%s", error_type)
            return self._unavailable(error_type)
        self.last_outcome = self._delegate.last_outcome
        self.last_status = self._delegate.last_status
        self.last_hits = list(self._delegate.last_hits)
        self.last_selected = self._delegate.last_selected
        return text
