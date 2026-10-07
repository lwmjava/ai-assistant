"""RAG 接口：文档摄取、管理与检索。

所有接口均需认证，并按角色校验 ``knowledge_bases`` 资源权限（依赖 require_permission）。
检索面默认同租户当前版本。控制面：成员仅自己的当前版；租户管理员看本租户全部版本；系统管理员可跨租户。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.api.deps import audit_event, get_db, require_permission
from app.audit.models import AuditAction
from app.core.config import settings
from app.core.security import Role
from app.models.rag import Document, DocumentChunk, ImportBatch, ImportJob
from app.models.user import User
from app.rag.access import can_control_document, can_read_import, can_write_document, restrict_list_to_uploader
from app.rag.document_parsers import (
    DocumentOcrRequiredError,
    DocumentParseError,
    DocumentTextEmptyError,
    UnsupportedDocumentTypeError,
    parse_uploaded_document,
)
from app.rag.document_storage import (
    delete_source_file,
    resolve_source_file_path,
    save_source_file,
)
from app.rag.import_jobs import (
    create_import_batch,
    create_reparse_job,
    create_upload_import_job,
    create_url_import_job,
    retry_import_job,
)
from app.rag.service import DocumentVectorizationSummary, RAGService, chunk_vectorization_state
from app.rag.upload_limits import UploadLimitError, check_upload_limits, parse_allowed_extensions
from app.services.quota import QuotaExceededError, SourceFileUnreadableError, begin_source_quota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])

_service = RAGService  # 仅作类型占位，实际每个请求新建实例以绑定会话


def _enforce_upload_limit(filename: str, size: int) -> None:
    """写盘前拒绝不合规文件。同一文件先看扩展名，再看字节长度。"""
    try:
        check_upload_limits(
            filename,
            size,
            allowed_extensions=parse_allowed_extensions(settings.RAG_UPLOAD_ALLOWED_EXTENSIONS),
            max_bytes=settings.RAG_UPLOAD_MAX_BYTES,
        )
    except UploadLimitError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _discard_new_source(relative_path: str) -> None:
    """删掉这一次新写的源文件。文件已经不在则返回。删除失败则请求失败。"""
    try:
        delete_source_file(relative_path)
    except OSError:
        logger.exception("未能删除本次新写的源文件")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="源文件未能删除，请稍后重试或联系管理员",
        ) from None


class IngestRequest(BaseModel):
    """文本摄取请求体。"""

    text: str
    title: str
    source: str | None = None
    backend: str | None = None
    version_state: str | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": (
                    "人工智能（Artificial Intelligence，简称 AI）是计算机科学的一个分支，"
                    "旨在创建能够模拟人类智能的系统。这些系统可以执行通常需要人类智能的任务，"
                    "如视觉感知、语音识别、决策制定和语言翻译。"
                ),
                "title": "人工智能概述",
                "source": "内部知识库",
                "backend": "native",
            }
        }
    }


class SearchRequest(BaseModel):
    """检索请求体。"""

    query: str
    top_k: int | None = None
    backend: str | None = None  # 可选：native | langchain | llamaindex，覆盖 RAG_BACKEND

    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "什么是人工智能",
                "top_k": 5,
                "backend": "native",
            }
        }
    }


class UrlImportRequest(BaseModel):
    """URL 导入请求体。"""

    url: str
    title: str | None = None
    backend: str | None = None


class DocumentOut(BaseModel):
    """文档概要。"""

    id: str
    tenant_id: str
    user_id: str
    title: str
    source: str | None
    is_current: bool
    version_state: str
    deleted_at: str | None
    chunk_count: int
    created_at: str
    updated_at: str
    vectorization_status: Literal["vectorized", "partial", "not_vectorized", "unknown"] = "unknown"
    vectorized_chunk_count: int = 0
    not_vectorized_chunk_count: int = 0
    unknown_chunk_count: int = 0
    embedding_skip_reason_counts: dict[str, int] = Field(default_factory=dict)


class DocumentDetail(DocumentOut):
    """文档详情（与概要一致，预留扩展字段）。"""


class DocumentChunkOut(BaseModel):
    """文档分块详情。"""

    id: str
    chunk_index: int
    content: str
    source: str | None
    strategy: str | None
    page: int | None
    section: str | None
    created_at: str
    embedding_status: Literal["vectorized", "not_vectorized", "unknown"] = "unknown"
    embedding_skip_reason: str | None = None
    oversized: bool = False


class SearchResultOut(BaseModel):
    """检索命中结果。"""

    document_id: str
    content: str
    source: str | None
    score: float
    chunk_id: str
    parent_id: str | None = None
    chunk_kind: Literal["parent", "child", "unknown"] = "unknown"
    retrieval_origin: Literal["hit", "parent_expansion"] = "hit"
    expanded_from_chunk_id: str | None = None
    score_inherited_from_chunk_id: str | None = None


class ImportJobOut(BaseModel):
    """导入任务概要。"""

    id: str
    tenant_id: str
    user_id: str
    batch_id: str | None
    status: str
    source_type: str
    source_name: str | None
    source_uri: str | None
    document_id: str | None
    attempt_count: int
    error: str | None
    created_at: str
    updated_at: str


class ImportBatchOut(BaseModel):
    """导入批次概要。"""

    id: str
    tenant_id: str
    user_id: str
    status: str
    total_jobs: int
    completed_jobs: int
    successful_jobs: int
    failed_jobs: int
    created_at: str
    updated_at: str
    jobs: list[ImportJobOut]


def _chunk_out(chunk: DocumentChunk) -> DocumentChunkOut:
    metadata: dict = {}
    if chunk.chunk_metadata:
        try:
            parsed_metadata = json.loads(chunk.chunk_metadata)
            metadata = parsed_metadata if isinstance(parsed_metadata, dict) else {}
        except json.JSONDecodeError:
            metadata = {}
    page_raw = metadata.get("page")
    page = page_raw if isinstance(page_raw, int) and not isinstance(page_raw, bool) and page_raw >= 1 else None
    section_raw = metadata.get("section_path")
    section = section_raw if isinstance(section_raw, str) and section_raw else None
    return DocumentChunkOut(
        id=chunk.id,
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        source=chunk.source,
        strategy=chunk.strategy,
        page=page,
        section=section,
        created_at=chunk.created_at.isoformat() if chunk.created_at else "",
        **chunk_vectorization_state(bool(chunk.embedding), chunk.chunk_metadata),
    )


def _doc_out(doc: Document, summary: DocumentVectorizationSummary | None = None) -> DocumentOut:
    result = DocumentOut(
        id=doc.id,
        tenant_id=doc.tenant_id,
        user_id=doc.user_id,
        title=doc.title,
        source=doc.source,
        is_current=doc.is_current,
        version_state=doc.version_state,
        deleted_at=doc.deleted_at.isoformat() if doc.deleted_at else None,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at.isoformat(),
        updated_at=doc.updated_at.isoformat(),
    )
    if summary is not None:
        result.vectorization_status = summary["vectorization_status"]
        result.vectorized_chunk_count = summary["vectorized_chunk_count"]
        result.not_vectorized_chunk_count = summary["not_vectorized_chunk_count"]
        result.unknown_chunk_count = summary["unknown_chunk_count"]
        result.embedding_skip_reason_counts = summary["embedding_skip_reason_counts"]
    return result


def _doc_with_vectorization(doc: Document, rag: RAGService, user: User) -> DocumentOut:
    return _doc_out(doc, rag.summarize_vectorization([doc], user).get(doc.id))


def _job_out(job: ImportJob) -> ImportJobOut:
    return ImportJobOut(
        id=job.id,
        tenant_id=job.tenant_id,
        user_id=job.user_id,
        batch_id=job.batch_id,
        status=job.status,
        source_type=job.source_type,
        source_name=job.source_name,
        source_uri=job.source_uri,
        document_id=job.document_id,
        attempt_count=job.attempt_count,
        error=job.error,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


def _batch_out(batch: ImportBatch, jobs: list[ImportJob]) -> ImportBatchOut:
    return ImportBatchOut(
        id=batch.id,
        tenant_id=batch.tenant_id,
        user_id=batch.user_id,
        status=batch.status,
        total_jobs=batch.total_jobs,
        completed_jobs=batch.completed_jobs,
        successful_jobs=batch.successful_jobs,
        failed_jobs=batch.failed_jobs,
        created_at=batch.created_at.isoformat(),
        updated_at=batch.updated_at.isoformat(),
        jobs=[_job_out(job) for job in jobs],
    )


def _can_access_import_resource(owner_user_id: str, owner_tenant_id: str, user: User) -> bool:
    return can_read_import(owner_user_id, owner_tenant_id, user)


@router.post("/documents/ingest", response_model=DocumentOut)
async def ingest_document(
    req: IngestRequest,
    request: Request,
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> DocumentOut:
    """将一段纯文本摄取为当前租户的知识文档。

    调用方提交标题与正文后，本接口会按所选 RAG 后端自动分块、生成嵌入向量并落库。
    文档归属当前登录用户及其租户；需具备 ``knowledge_bases`` 的 write 权限。
    摄取成功后写入审计日志（动作：知识库上传）。

    请求参数:
        req: 文本摄取请求体（``IngestRequest``）
            text: 待摄取正文，去空白后不能为空
            title: 文档标题，去空白后不能为空
            source: 可选来源标识（如「内部知识库」）
            backend: 可选 RAG 后端，取值 ``native`` / ``langchain`` / ``llamaindex``；
                未传时沿用配置项 ``RAG_BACKEND``
        request: FastAPI 请求对象，供审计记录客户端信息
        current_user: 已认证且具备写入权限的当前用户（依赖注入）
        session: 数据库会话（依赖注入）

    返回:
        DocumentOut: 新文档概要；chunk_count 是已保存分块数量，不保证全部已向量化。
        超出输入限制的结构保留原文；vectorization_status 和各状态计数说明向量化结果，
        embedding_skip_reason_counts 给出允许公开的跳过原因计数。

    异常:
        400: text/title 为空，或文本无法切分为任何分块等业务校验失败
    """
    # 校验必填字段：正文与标题去空白后均不能为空
    if not req.text.strip() or not req.title.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="text 与 title 不能为空")
    if req.version_state not in {None, "draft", "published"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="version_state 只能是 draft 或 published")
    # 按当前用户租户创建 RAG 服务，保证多租户数据隔离
    rag = RAGService(session, current_user.tenant_id)
    try:
        # 分块、嵌入并落库；backend 可覆盖默认 RAG 后端
        doc = await rag.ingest_text(
            req.text,
            req.title,
            req.source,
            current_user.id,
            backend=req.backend,
            is_current=req.version_state != "draft",
            version_state="draft" if req.version_state == "draft" else None,
        )
    except ValueError as exc:
        # 服务层业务错误（如无法切块）统一映射为 400
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    # 记录知识库上传审计：文档 id、标题、分块数及来源类型
    await audit_event(
        request,
        AuditAction.KNOWLEDGE_BASE_UPLOAD,
        user=current_user,
        resource_type="document",
        resource_id=doc.id,
        details={"title": doc.title, "chunk_count": doc.chunk_count, "source": "text"},
    )
    # 将 ORM 文档转为接口响应模型（时间戳格式化为 ISO 字符串）
    return _doc_with_vectorization(doc, rag, current_user)


@router.post("/documents/upload", response_model=DocumentOut)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> DocumentOut | JSONResponse:
    """上传文档并按文件类型自动提取文本后摄取为知识文档。

    成功响应中的 chunk_count 是保存数量，不保证所有分块均已向量化。
    超出输入限制的结构保留原文，以 vectorization_status、各状态数量和
    embedding_skip_reason_counts 呈现向量化结果及允许公开的跳过原因。
    """
    filename = file.filename or "未命名文档"
    raw = await file.read()
    _enforce_upload_limit(filename, len(raw))
    try:
        held = begin_source_quota(session, current_user.tenant_id, len(raw))
    except QuotaExceededError as exc:
        return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, content=exc.as_dict())
    except SourceFileUnreadableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="无法读取已有源文件，已拒绝本次上传",
        )
    storage_path: str | None = None
    try:
        storage_path = save_source_file(current_user.tenant_id, raw, filename)
    except OSError:
        logger.exception("保存源文件失败: filename=%s", filename)
        if held:
            session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="保存源文件失败，请稍后重试或联系管理员",
        )
    try:
        parsed = parse_uploaded_document(raw, filename, file.content_type)
    except (
        UnsupportedDocumentTypeError,
        DocumentParseError,
        DocumentTextEmptyError,
        DocumentOcrRequiredError,
    ) as exc:
        assert storage_path is not None
        _discard_new_source(storage_path)
        if held:
            session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    rag = RAGService(session, current_user.tenant_id)
    try:
        doc = await rag.ingest_parsed_document(
            parsed,
            user_id=current_user.id,
            storage_path=storage_path,
        )
    except ValueError as exc:
        assert storage_path is not None
        _discard_new_source(storage_path)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:  # noqa: BLE001 - 上传文件已解析成功，后续失败视为系统问题
        logger.exception("上传文档解析成功，但知识库摄取失败: filename=%s", filename)
        assert storage_path is not None
        _discard_new_source(storage_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="文档已解析，但知识库摄取失败，请稍后重试或联系管理员",
        )
    await audit_event(
        request,
        AuditAction.KNOWLEDGE_BASE_UPLOAD,
        user=current_user,
        resource_type="document",
        resource_id=doc.id,
        details={
            "title": doc.title,
            "chunk_count": doc.chunk_count,
            "source": "upload",
            "filename": filename,
            "extension": parsed.extension,
            "content_type": parsed.content_type,
            "parser_name": parsed.metadata.get("parser_name"),
            "used_ocr": parsed.metadata.get("used_ocr"),
            "storage_path": storage_path,
        },
    )
    return _doc_with_vectorization(doc, rag, current_user)


@router.post(
    "/import-jobs/upload",
    response_model=ImportBatchOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_upload_jobs(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> ImportBatchOut | JSONResponse:
    """批量上传文件并创建异步导入任务。"""
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少上传一个文件")
    prepared: list[tuple[str, bytes, str | None]] = []
    for file in files:
        filename = file.filename or "未命名文档"
        raw = await file.read()
        _enforce_upload_limit(filename, len(raw))
        prepared.append((filename, raw, file.content_type))
    additional = sum(len(raw) for _, raw, _ in prepared)
    try:
        held = begin_source_quota(session, current_user.tenant_id, additional)
    except QuotaExceededError as exc:
        return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, content=exc.as_dict())
    except SourceFileUnreadableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="无法读取已有源文件，已拒绝本次上传",
        )
    saved: list[str] = []
    try:
        batch = create_import_batch(
            session,
            current_user,
            total_jobs=len(prepared),
            source_type="file",
            commit=not held,
        )
        jobs: list[ImportJob] = []
        for filename, raw, content_type in prepared:
            storage_path = save_source_file(current_user.tenant_id, raw, filename)
            saved.append(storage_path)
            jobs.append(
                create_upload_import_job(
                    session,
                    current_user,
                    storage_path=storage_path,
                    filename=filename,
                    content_type=content_type,
                    batch_id=batch.id,
                    commit=not held,
                )
            )
        if held:
            session.commit()
    except OSError:
        logger.exception("创建上传导入任务时保存源文件失败")
        for relative_path in saved:
            _discard_new_source(relative_path)
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="保存源文件失败，请稍后重试或联系管理员",
        )
    session.refresh(batch)
    return _batch_out(batch, jobs)


@router.post(
    "/import-jobs/url",
    response_model=ImportJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_url_job(
    req: UrlImportRequest,
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> ImportJobOut:
    """创建网页 URL 异步导入任务。"""
    try:
        job = create_url_import_job(
            session,
            current_user,
            url=req.url,
            title=req.title,
            backend=req.backend,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _job_out(job)


@router.get("/import-jobs", response_model=list[ImportJobOut])
def list_import_jobs(
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> list[ImportJobOut]:
    """列出当前用户可见的导入任务。"""
    stmt = select(ImportJob).where(ImportJob.tenant_id == current_user.tenant_id)
    if restrict_list_to_uploader(current_user):
        stmt = stmt.where(ImportJob.user_id == current_user.id)
    stmt = stmt.order_by(col(ImportJob.updated_at).desc())
    jobs = list(session.exec(stmt).all())
    return [_job_out(job) for job in jobs]


@router.get("/import-jobs/{job_id}", response_model=ImportJobOut)
def get_import_job(
    job_id: str,
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> ImportJobOut:
    """获取导入任务详情。"""
    job = session.get(ImportJob, job_id)
    if job is None or not _can_access_import_resource(job.user_id, job.tenant_id, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导入任务不存在或无权访问")
    return _job_out(job)


@router.get("/import-batches/{batch_id}", response_model=ImportBatchOut)
def get_import_batch(
    batch_id: str,
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> ImportBatchOut:
    """获取批量导入批次详情。"""
    batch = session.get(ImportBatch, batch_id)
    if batch is None or not _can_access_import_resource(batch.user_id, batch.tenant_id, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导入批次不存在或无权访问")
    jobs = list(
        session.exec(
            select(ImportJob).where(ImportJob.batch_id == batch.id).order_by(col(ImportJob.created_at).asc())
        ).all()
    )
    return _batch_out(batch, jobs)


@router.post(
    "/import-jobs/{job_id}/retry",
    response_model=ImportJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_job(
    job_id: str,
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> ImportJobOut:
    """重试失败的导入任务。"""
    try:
        job = retry_import_job(session, current_user, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _job_out(job)


@router.get("/import-jobs/{job_id}/download")
def download_import_job_source(
    job_id: str,
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> FileResponse:
    """下载导入任务关联的源文件或 URL 快照。"""
    job = session.get(ImportJob, job_id)
    if job is None or not _can_access_import_resource(job.user_id, job.tenant_id, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导入任务不存在或无权访问")
    if not job.storage_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源文件不存在")
    try:
        file_path = resolve_source_file_path(job.storage_path)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源文件不存在")
    return FileResponse(file_path, filename=job.source_name or Path(file_path).name)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    include_deleted: bool = False,
    version_state: str | None = None,
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> list[DocumentOut]:
    """列出当前用户可见的文档。成员忽略已删除和状态筛选。"""
    rag = RAGService(session, current_user.tenant_id)
    documents = rag.list_documents(
        current_user,
        include_deleted=include_deleted,
        version_state=version_state,
    )
    summaries = rag.summarize_vectorization(documents, current_user)
    return [_doc_out(doc, summaries.get(doc.id)) for doc in documents]


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: str,
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> DocumentDetail:
    """获取文档详情。"""
    rag = RAGService(session, current_user.tenant_id)
    doc = rag.get_document(document_id, current_user)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")
    return DocumentDetail(**_doc_with_vectorization(doc, rag, current_user).model_dump())


@router.get("/documents/{document_id}/chunks", response_model=list[DocumentChunkOut])
def list_document_chunks(
    document_id: str,
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> list[DocumentChunkOut]:
    """列出文档的全部分块（按块序），供前端逐块展示。"""
    rag = RAGService(session, current_user.tenant_id)
    doc = rag.get_document(document_id, current_user)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")
    return [_chunk_out(c) for c in rag.list_chunks(document_id, current_user)]


@router.post(
    "/documents/{document_id}/reparse",
    response_model=ImportJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reparse_document(
    document_id: str,
    request: Request,
    confirmation_id: str | None = None,
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> ImportJobOut:
    """为既有文档创建重解析任务。跨租户须带一次性确认。"""
    from app.rag.confirmations import consume_confirmation

    doc = session.get(Document, document_id)
    if doc is not None:
        try:
            consume_confirmation(session, current_user, doc, "reparse", confirmation_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    try:
        job = create_reparse_job(session, current_user, document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if doc is not None and current_user.role_enum == Role.SYSTEM_ADMIN and doc.tenant_id != current_user.tenant_id:
        await audit_event(
            request,
            AuditAction.KNOWLEDGE_BASE_REINDEX,
            user=current_user,
            resource_type="document",
            resource_id=document_id,
            details={
                "tenant_id": doc.tenant_id,
                "document_id": document_id,
                "action": "reparse",
            },
        )
    return _job_out(job)


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: str,
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> FileResponse:
    """下载已保存的源文件。"""
    rag = RAGService(session, current_user.tenant_id)
    doc = rag.get_document(document_id, current_user)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")
    if not doc.storage_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源文件不存在")
    try:
        file_path = resolve_source_file_path(doc.storage_path)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源文件不存在")
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="源文件不存在")
    return FileResponse(file_path, filename=doc.source or Path(file_path).name)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    request: Request,
    confirmation_id: str | None = None,
    current_user: User = Depends(require_permission("knowledge_bases", "delete")),
    session: Session = Depends(get_db),
) -> dict:
    """软删除文档。跨租户须带一次性确认。"""
    from app.rag.confirmations import consume_confirmation

    doc = session.get(Document, document_id)
    if doc is None or not can_write_document(doc, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")
    try:
        consume_confirmation(session, current_user, doc, "delete", confirmation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    owner_tenant_id = doc.tenant_id
    rag = RAGService(session, owner_tenant_id)
    ok = await rag.delete_document(document_id, current_user)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")
    details = {
        "tenant_id": owner_tenant_id,
        "document_id": document_id,
        "action": "delete",
    }
    await audit_event(
        request,
        AuditAction.KNOWLEDGE_BASE_DELETE,
        user=current_user,
        resource_type="document",
        resource_id=document_id,
        details=details,
    )
    return {"deleted": True}


@router.post("/documents/{document_id}/publish", response_model=DocumentOut)
async def publish_document(
    document_id: str,
    confirmation_id: str | None = None,
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> DocumentOut:
    """把历史版本发布为当前版。跨租户须带一次性确认。"""
    from app.rag.confirmations import consume_confirmation

    doc = session.get(Document, document_id)
    if doc is None or not can_write_document(doc, current_user) or doc.version_state == "archived":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")
    try:
        consume_confirmation(session, current_user, doc, "publish", confirmation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    rag = RAGService(session, doc.tenant_id)
    published = rag.publish_document(document_id, current_user)
    if published is None:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")
    return _doc_with_vectorization(published, rag, current_user)


class ScheduleRequest(BaseModel):
    """把文档标为待生效。"""

    effective_at: datetime


@router.post("/documents/{document_id}/schedule", response_model=DocumentOut)
def schedule_document(
    document_id: str,
    req: ScheduleRequest,
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> DocumentOut:
    """标为待生效，不改变检索用的当前发布指针以外的已发布版本之外。"""
    rag = RAGService(session, current_user.tenant_id)
    try:
        doc = rag.schedule_document(document_id, current_user, req.effective_at)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")
    return _doc_with_vectorization(doc, rag, current_user)


@router.post("/documents/{document_id}/archive", response_model=DocumentOut)
def archive_document(
    document_id: str,
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> DocumentOut:
    """归档文档并退出当前版。"""
    rag = RAGService(session, current_user.tenant_id)
    doc = rag.archive_document(document_id, current_user)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")
    return _doc_with_vectorization(doc, rag, current_user)


class ConfirmationIn(BaseModel):
    """创建跨租户确认。"""

    document_id: str
    action: str


class ConfirmationOut(BaseModel):
    """确认记录。"""

    id: str
    document_id: str
    tenant_id: str
    action: str
    consumed_at: str | None


@router.post("/operation-confirmations", response_model=ConfirmationOut)
def create_operation_confirmation(
    req: ConfirmationIn,
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> ConfirmationOut:
    """为跨租户删除、重解析或发布创建一次性确认。"""
    from app.rag.confirmations import create_confirmation

    doc = session.get(Document, req.document_id)
    if doc is None or not can_control_document(doc, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")
    try:
        row = create_confirmation(session, current_user, doc, req.action)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ConfirmationOut(
        id=row.id,
        document_id=row.document_id,
        tenant_id=row.tenant_id,
        action=row.action,
        consumed_at=None,
    )


@router.get("/operation-confirmations", response_model=list[ConfirmationOut])
def list_operation_confirmations(
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> list[ConfirmationOut]:
    """系统管理员查看自己的确认记录。"""
    from sqlmodel import select

    from app.core.security import Role
    from app.models.rag import OperationConfirmation

    if current_user.role_enum != Role.SYSTEM_ADMIN:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="确认记录不存在")
    rows = session.exec(
        select(OperationConfirmation)
        .where(OperationConfirmation.actor_user_id == current_user.id)
        .order_by(col(OperationConfirmation.created_at).desc())
    ).all()
    return [
        ConfirmationOut(
            id=row.id,
            document_id=row.document_id,
            tenant_id=row.tenant_id,
            action=row.action,
            consumed_at=row.consumed_at.isoformat() if row.consumed_at else None,
        )
        for row in rows
    ]


@router.post("/search", response_model=list[SearchResultOut])
async def search(
    req: SearchRequest,
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> list[SearchResultOut]:
    """按融合顺序返回命中并追加父块；top_k 为初始命中上限，展开后可增加。"""
    if not req.query.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query 不能为空")
    # 带上鉴权主体，使 uploader 模式的有效读范围同样贯穿 HTTP 检索面，
    # 与对话检索保持一致（ADR-0001 §10）。
    rag = RAGService(session, current_user.tenant_id, reader=current_user)
    results = await rag.search(req.query, req.top_k, backend=req.backend)
    return [
        SearchResultOut(
            document_id=r.document_id,
            content=r.content,
            source=r.source,
            score=round(r.score, 6),
            chunk_id=r.id,
            parent_id=r.parent_id,
            chunk_kind=r.chunk_kind,
            retrieval_origin=r.retrieval_origin,
            expanded_from_chunk_id=r.expanded_from_chunk_id,
            score_inherited_from_chunk_id=r.score_inherited_from_chunk_id,
        )
        for r in results
    ]
