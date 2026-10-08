"""向量库抽象层。

定义统一的最小接口，屏蔽「本地 SQLite + numpy」与「Milvus」等具体实现差异。
检索与管线只依赖本模块定义的抽象，便于按需切换后端。

混合检索策略：对查询同时做稠密向量检索（语义）与稀疏关键词检索（BM25），
再用倒数排名融合（RRF）合并两份排序，兼顾语义召回与精确词面匹配。
"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.rag.access import ReadScope
from app.rag.index_identity import EmbeddingIndexIdentity


@dataclass
class ChunkResult:
    """一次检索命中的分块。"""

    id: str
    content: str
    source: str | None
    document_id: str
    score: float
    version_status: str = "current"
    similarity: float = 0.0  # 稠密余弦相似度（-1~1），供低分阈值过滤；score 为 RRF 融合分
    parent_id: str | None = None
    chunk_kind: Literal["parent", "child", "unknown"] = "unknown"
    retrieval_origin: Literal["hit", "parent_expansion"] = "hit"
    expanded_from_chunk_id: str | None = None
    score_inherited_from_chunk_id: str | None = None


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
        rrf_k: int = 60,
        as_of: datetime | None = None,
        schedule_at: datetime | None = None,
        read_scope: ReadScope | None = None,
        identity: EmbeddingIndexIdentity | None = None,
    ) -> list[ChunkResult]:
        """混合检索：融合稠密与稀疏结果，返回按融合分排序的前 top_k 个分块。

        ``rrf_k`` 为倒数排名融合常数，须与实现类签名保持一致，避免新后端漏参。
        ``read_scope`` 为鉴权主体的有效读范围，必须在候选集构造阶段生效。
        ``identity`` 是**当前查询 provider 的完整索引身份**；实现类必须在取候选
        前拿它与生效索引核对（同维异模型靠维度发现不了）。默认 None 只为兼容
        测试里的假实现——真实读路径不允许省略。
        """

    @abstractmethod
    async def delete_by_document(self, document_id: str, tenant_id: str) -> int:
        """删除某文档下的全部分块索引，返回删除数量。"""

    @abstractmethod
    async def count(self, tenant_id: str) -> int:
        """返回某租户下的分块总数。"""


def l2_normalize(vector: list[float]) -> list[float]:
    """对向量做 L2 归一化。零向量（norm=0）原样返回，避免除零。

    写入侧统一在此固化：检索侧点积=余弦成立的前提是入库向量已归一。
    不引入 numpy，保持本模块对后端实现无第三方数值库依赖。
    """
    if not vector:
        return vector
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return list(vector)
    return [value / norm for value in vector]
