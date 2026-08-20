"""RAG 文档与分块模型（多租户隔离）。

Document 表示一次摄取得到的文档（一篇文本 / 一个上传文件），
其下挂若干 DocumentChunk（按字符窗口切分后的片段）。
每个分块保存归一化后的向量（JSON）与 BM25 词项（JSON），
供本地向量库的稠密检索与稀疏检索复用，避免重复计算。
"""

import uuid

from sqlmodel import Field, Relationship, SQLModel

from app.models.base import TimestampMixin


def _uuid() -> str:
    """生成短 UUID 主键（十六进制字符串）。"""
    return uuid.uuid4().hex


class Document(SQLModel, TimestampMixin, table=True):
    """文档：一次摄取产生的知识单元，归属租户与用户。"""

    __tablename__ = "rag_documents"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    title: str
    source: str | None = Field(default=None)  # 来源文件名 / 标识
    chunk_count: int = Field(default=0)

    chunks: list["DocumentChunk"] = Relationship(
        back_populates="document", cascade_delete=True
    )


class DocumentChunk(SQLModel, TimestampMixin, table=True):
    """文档分块：检索的最小单元。"""

    __tablename__ = "rag_document_chunks"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    document_id: str = Field(foreign_key="rag_documents.id", index=True)
    chunk_index: int = Field(default=0)
    content: str
    source: str | None = Field(default=None)
    # 归一化后的稠密向量（JSON 数组）；为空表示尚未生成。
    embedding: str | None = Field(default=None)
    # BM25 词项（JSON 数组）；为空表示尚未分词。
    tokens: str | None = Field(default=None)

    document: Document | None = Relationship(back_populates="chunks")
