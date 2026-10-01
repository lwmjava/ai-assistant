"""RAG 文档与分块模型（多租户隔离）。

Document 表示一次摄取得到的文档（一篇文本 / 一个上传文件），
其下挂若干 DocumentChunk（按字符窗口切分后的片段）。
每个分块保存归一化后的向量（JSON）与 BM25 词项（JSON），
供本地向量库的稠密检索与稀疏检索复用，避免重复计算。
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, Relationship, SQLModel

from app.models.base import TimestampMixin


def _uuid() -> str:
    """生成短 UUID 主键（十六进制字符串）。"""
    return uuid.uuid4().hex


class ImportJobStatus(StrEnum):
    """导入任务状态机。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ImportBatchStatus(StrEnum):
    """批量导入聚合状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class ImportSourceType(StrEnum):
    """导入来源类型。"""

    FILE = "file"
    URL = "url"
    REPARSE = "reparse"


class Document(SQLModel, TimestampMixin, table=True):
    """文档：一次摄取产生的知识单元，归属租户与用户。"""

    __tablename__ = "rag_documents"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    title: str
    source: str | None = Field(default=None)  # 来源文件名 / 标识
    storage_path: str | None = Field(default=None)  # 源文件相对路径（data/knowledge/...）
    source_kind: str = Field(default=ImportSourceType.FILE.value, index=True)
    source_uri: str | None = Field(default=None)  # URL 等逻辑来源标识
    content_hash: str | None = Field(default=None, index=True)
    version_group_id: str = Field(default_factory=_uuid, index=True)
    version_number: int = Field(default=1)
    previous_document_id: str | None = Field(default=None, index=True)
    is_current: bool = Field(default=True, index=True)
    deleted_at: datetime | None = Field(default=None, index=True)
    effective_at: datetime | None = Field(default=None, index=True)
    expires_at: datetime | None = Field(default=None, index=True)
    import_job_id: str | None = Field(default=None, index=True)
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
    # 父子文档关联：指向父块（parent_id 为 None 表示自身是父块或普通块）。
    parent_id: str | None = Field(default=None, index=True)
    # 本次切分使用的策略名，便于审计与后续重解析策略还原。
    strategy: str | None = Field(default=None)
    # 章节 / 位置 / 溯源等扩展信息（JSON 字符串）。
    chunk_metadata: str | None = Field(default=None)

    document: Document | None = Relationship(back_populates="chunks")


class ImportBatch(SQLModel, TimestampMixin, table=True):
    """批量导入批次：用于聚合多个导入任务的状态与统计。"""

    __tablename__ = "rag_import_batches"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    status: str = Field(default=ImportBatchStatus.PENDING.value, index=True)
    total_jobs: int = Field(default=0)
    completed_jobs: int = Field(default=0)
    successful_jobs: int = Field(default=0)
    failed_jobs: int = Field(default=0)
    source_type: str = Field(default=ImportSourceType.FILE.value)

    jobs: list["ImportJob"] = Relationship(back_populates="batch")


class ImportJob(SQLModel, TimestampMixin, table=True):
    """导入任务：承载单次文件 / URL / 重解析执行与失败治理。"""

    __tablename__ = "rag_import_jobs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    batch_id: str | None = Field(default=None, foreign_key="rag_import_batches.id", index=True)
    status: str = Field(default=ImportJobStatus.PENDING.value, index=True)
    source_type: str = Field(default=ImportSourceType.FILE.value, index=True)
    source_name: str | None = Field(default=None)
    source_uri: str | None = Field(default=None)
    storage_path: str | None = Field(default=None)
    content_type: str | None = Field(default=None)
    requested_title: str | None = Field(default=None)
    backend: str | None = Field(default=None)
    parser_name: str | None = Field(default=None)
    content_hash: str | None = Field(default=None, index=True)
    document_id: str | None = Field(default=None, index=True)
    reparse_document_id: str | None = Field(default=None, index=True)
    error: str | None = Field(default=None)
    attempt_count: int = Field(default=0)
    max_attempts: int = Field(default=3)

    batch: ImportBatch | None = Relationship(back_populates="jobs")


class ImportJobTrace(SQLModel, TimestampMixin, table=True):
    """导入任务追踪记录：持久化失败证据，便于定位、审计与后续演进。"""

    __tablename__ = "rag_import_job_traces"

    id: str = Field(default_factory=_uuid, primary_key=True)
    job_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    attempt_count: int = Field(default=0)
    stage: str = Field(default="import_job", index=True)
    error_code: str | None = Field(default=None, index=True)
    exception_type: str | None = Field(default=None)
    message: str | None = Field(default=None)
    command: str | None = Field(default=None)
    exit_code: int | None = Field(default=None)
    stdout: str | None = Field(default=None)
    stderr: str | None = Field(default=None)
