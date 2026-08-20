"""RAG 检索增强模块。

包含：嵌入模型层、向量库抽象（本地 / Milvus）、文档摄取、混合检索器与 RAG 服务。
对外暴露常用入口，便于在业务层与测试中引用。
"""

from app.models.rag import Document, DocumentChunk  # noqa: F401
from app.rag.embeddings.factory import (  # noqa: F401
    get_embedding_provider,
    set_embedding_override,
)
from app.rag.ingestion import split_text  # noqa: F401
from app.rag.retriever import HybridRetriever  # noqa: F401
from app.rag.service import RAGService  # noqa: F401
from app.rag.vectorstore.factory import get_vector_store  # noqa: F401
