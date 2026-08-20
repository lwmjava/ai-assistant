"""向量库抽象层。

定义统一的最小接口，屏蔽「本地 SQLite + numpy」与「Milvus」等具体实现差异。
检索与管线只依赖本模块定义的抽象，便于按需切换后端。

混合检索策略：对查询同时做稠密向量检索（语义）与稀疏关键词检索（BM25），
再用倒数排名融合（RRF）合并两份排序，兼顾语义召回与精确词面匹配。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ChunkResult:
    """一次检索命中的分块。"""

    id: str
    content: str
    source: str | None
    document_id: str
    score: float


class VectorStore(ABC):
    """向量库接口。

    实现方需提供：批量写入（``add``）、混合检索（``hybrid_search``）、
    按文档删除（``delete_by_document``）与计数（``count``）。
    """

    @abstractmethod
    async def add(self, chunks: list) -> None:
        """写入若干分块（本地实现即数据库本身，Milvus 实现则同步向量）。"""

    @abstractmethod
    async def hybrid_search(
        self,
        query_embedding: list[float],
        query_tokens: list[str],
        tenant_id: str,
        top_k: int,
    ) -> list[ChunkResult]:
        """混合检索：融合稠密与稀疏结果，返回按融合分排序的前 top_k 个分块。"""

    @abstractmethod
    async def delete_by_document(self, document_id: str, tenant_id: str) -> int:
        """删除某文档下的全部分块索引，返回删除数量。"""

    @abstractmethod
    async def count(self, tenant_id: str) -> int:
        """返回某租户下的分块总数。"""
