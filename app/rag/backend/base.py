"""RAG 后端策略抽象。

后端只负责「切分」与「检索」两个纯能力；嵌入与向量存储由外部注入并三套共用。
主库读写、租户隔离、审计仍由 ``RAGService`` 承担。
"""

from abc import ABC, abstractmethod

from app.rag.vectorstore.base import ChunkResult


class BackendNotAvailableError(RuntimeError):
    """请求的 RAG 后端不可用（未安装可选依赖或尚未实现）。"""


class RagBackend(ABC):
    """RAG 后端策略：只暴露切分与检索。

    嵌入与存储由三套后端共用，由外部注入，后端不得自行创建。
    """

    name: str

    @abstractmethod
    async def split(self, text: str, *, chunk_size: int, overlap: int) -> list[str]:
        """切分为块文本列表。"""

    @abstractmethod
    async def retrieve(
        self, query: str, *, tenant_id: str, top_k: int
    ) -> list[ChunkResult]:
        """返回按相关度降序的分块，无结果返回空列表。"""
