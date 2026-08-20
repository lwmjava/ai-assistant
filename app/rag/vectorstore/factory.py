"""向量库工厂。

按配置选择后端：默认本地（SQLite + numpy），可选 Milvus。
"""

from sqlmodel import Session

from app.core.config import settings
from app.rag.vectorstore.base import VectorStore
from app.rag.vectorstore.local import LocalVectorStore


def get_vector_store(session: Session) -> VectorStore:
    """返回当前生效的向量库实现。"""
    if settings.RAG_VECTOR_STORE.strip().lower() == "milvus":
        from app.rag.vectorstore.milvus import MilvusVectorStore

        return MilvusVectorStore(session)
    return LocalVectorStore(session)
