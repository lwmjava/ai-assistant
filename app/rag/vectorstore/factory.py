"""向量库工厂。

按配置选择后端：默认本地（SQLite + numpy），可选 Milvus。
"""

from sqlmodel import Session

from app.core.config import settings
from app.rag.vectorstore.base import VectorStore
from app.rag.vectorstore.local import LocalVectorStore


def get_vector_store(session: Session) -> VectorStore:
    """返回当前生效的向量库实现。

    只接受已声明的 ``local`` 与 ``milvus``。未知值直接报配置错误，
    连接失败由 Milvus 实现向上抛出，这里不会改回本地库。
    """
    name = settings.RAG_VECTOR_STORE.strip().lower()
    if name == "milvus":
        from app.rag.vectorstore.milvus import MilvusVectorStore

        return MilvusVectorStore(session)
    if name == "local":
        return LocalVectorStore(session)
    raise ValueError(f"未知向量库 {settings.RAG_VECTOR_STORE!r}，只接受 local 或 milvus")
