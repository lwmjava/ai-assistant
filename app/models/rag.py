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


class IndexStatus(StrEnum):
    """向量索引生命周期状态（ADR-0008）。

    ``preparing`` 的索引正在重建，此时旧索引仍是 ``active``，读路径不受影响；
    只有显式激活才切换，失败保留旧读路径。
    """

    PREPARING = "preparing"
    ACTIVE = "active"
    RETIRED = "retired"
    FAILED = "failed"


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


class DocumentVersionState(StrEnum):
    """文档在版本组中的控制面状态。"""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    REPLACED = "replaced"
    ARCHIVED = "archived"


class Document(SQLModel, TimestampMixin, table=True):
    """文档：一次摄取产生的知识单元，归属租户与用户。"""

    __tablename__ = "rag_documents"

    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    title: str
    source: str | None = Field(default=None)  # 来源文件名 / 标识
    storage_path: str | None = Field(default=None)  # 源文件相对路径（data/knowledge/...）
    source_bytes: int | None = Field(default=None)  # 文档提交成功时写下的源文件字节数
    source_kind: str = Field(default=ImportSourceType.FILE.value, index=True)
    source_uri: str | None = Field(default=None)  # URL 等逻辑来源标识
    content_hash: str | None = Field(default=None, index=True)
    version_group_id: str = Field(default_factory=_uuid, index=True)
    version_number: int = Field(default=1)
    previous_document_id: str | None = Field(default=None, index=True)
    is_current: bool = Field(default=True, index=True)
    version_state: str = Field(default=DocumentVersionState.PUBLISHED.value, index=True)
    deleted_at: datetime | None = Field(default=None, index=True)
    effective_at: datetime | None = Field(default=None, index=True)
    expires_at: datetime | None = Field(default=None, index=True)
    import_job_id: str | None = Field(default=None, index=True)
    chunk_count: int = Field(default=0)
    # 版本化切分计划（JSON）：策略名 + 有效参数 + 路由原因；重解析据此精确重放。
    chunk_plan: str | None = Field(default=None)

    chunks: list["DocumentChunk"] = Relationship(
        back_populates="document", cascade_delete=True
    )
    ingestion_snapshots: list["DocumentIngestionSnapshot"] = Relationship(
        back_populates="document", cascade_delete=True
    )


class DocumentIngestionSnapshot(SQLModel, TimestampMixin, table=True):
    """原始解析文本及结构快照；不通过公开 API 暴露。"""

    __tablename__ = "rag_ingestion_snapshots"
    document_id: str = Field(primary_key=True, foreign_key="rag_documents.id")
    tenant_id: str = Field(index=True)
    original_text: str
    original_blocks: str
    cleaning_report: str
    document: Document | None = Relationship(back_populates="ingestion_snapshots")


class VectorCleanupJob(SQLModel, TimestampMixin, table=True):
    """到期清理意图和重试证据，文档删除后仍保留。"""

    __tablename__ = "rag_vector_cleanup_jobs"
    id: str = Field(default_factory=_uuid, primary_key=True)
    tenant_id: str = Field(index=True)
    document_id: str = Field(index=True, unique=True)
    vector_backend: str
    vector_target: str = Field(default="")
    status: str = Field(default="pending", index=True)
    attempt_count: int = Field(default=0)
    max_attempts: int = Field(default=3)
    next_attempt_at: datetime | None = Field(default=None, index=True)
    last_error_code: str | None = Field(default=None)


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
    # 所属向量索引。为 None 表示身份未知的历史数据：
    # 按 ADR-0008 不得自动认定与当前配置兼容，因此默认不参与检索。
    index_id: str | None = Field(default=None, index=True)

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


class EmbeddingIndex(SQLModel, TimestampMixin, table=True):
    """向量索引登记（ADR-0008）。

    一个索引 = 一套「同一模型、同一维度、同一归一化与度量」的向量集合。
    切换模型时不原地改写，而是建新索引、校验后显式激活，旧索引保留用于回退。
    """

    __tablename__ = "rag_embedding_indexes"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = Field(index=True, unique=True)
    backend: str = Field(default="local", index=True)
    provider: str = Field(default="")
    model: str = Field(default="", index=True)
    deployment: str = Field(default="")
    dim: int = Field(default=0)
    index_version: str = Field(default="1")
    normalization: str = Field(default="l2")
    metric: str = Field(default="cosine")
    # 身份指纹：比对用，避免每次拼字符串。
    identity_key: str = Field(default="", index=True)
    status: str = Field(default=IndexStatus.PREPARING.value, index=True)
    # 重建/校验结果，失败时保留证据而不是静默回退。
    chunk_count: int = Field(default=0)
    notes: str | None = Field(default=None)
    activated_at: datetime | None = Field(default=None)
    retired_at: datetime | None = Field(default=None)


class OperationConfirmation(SQLModel, TimestampMixin, table=True):
    """跨租户敏感操作的一次性确认记录。"""

    __tablename__ = "rag_operation_confirmations"

    id: str = Field(default_factory=_uuid, primary_key=True)
    actor_user_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    document_id: str = Field(index=True)
    action: str = Field(index=True)
    consumed_at: datetime | None = Field(default=None, index=True)
