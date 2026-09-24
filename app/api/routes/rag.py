"""RAG 接口：文档摄取、管理与检索。

所有接口均需认证，并按角色校验 ``knowledge_bases`` 资源权限（依赖 require_permission）。
检索面默认同租户当前版本。控制面：成员仅自己的当前版；租户管理员看本租户全部版本；系统管理员可跨租户。
"""

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import audit_event, get_db, require_permission
from app.audit.models import AuditAction
from app.core.security import Role
from app.models.rag import Document, ImportBatch, ImportJob
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
from app.rag.service import RAGService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])

_service = RAGService  # 仅作类型占位，实际每个请求新建实例以绑定会话


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


class DocumentDetail(DocumentOut):
    """文档详情（与概要一致，预留扩展字段）。"""


class SearchResultOut(BaseModel):
    """检索命中结果。"""

    document_id: str
    content: str
    source: str | None
    score: float


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


def _doc_out(doc: Document) -> DocumentOut:
    return DocumentOut(
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
        DocumentOut: 新文档概要，包含 id、租户/用户、标题、来源、分块数及时间戳

    异常:
        400: text/title 为空，或文本无法切分为任何分块等业务校验失败
    """
    # 校验必填字段：正文与标题去空白后均不能为空
    if not req.text.strip() or not req.title.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="text 与 title 不能为空"
        )
    if req.version_state not in {None, "draft", "published"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="version_state 只能是 draft 或 published"
        )
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
    return _doc_out(doc)


@router.post("/documents/upload", response_model=DocumentOut)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> DocumentOut:
    """上传文档并按文件类型自动提取文本后摄取为知识文档。"""
    filename = file.filename or "未命名文档"
    try:
        raw = await file.read()
        storage_path = save_source_file(current_user.tenant_id, raw, filename)
    except OSError:
        logger.exception("保存源文件失败: filename=%s", filename)
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    rag = RAGService(session, current_user.tenant_id)
    try:
        doc = await rag.ingest_parsed_document(
            parsed,
            user_id=current_user.id,
            storage_path=storage_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:  # noqa: BLE001 - 上传文件已解析成功，后续失败视为系统问题
        logger.exception("上传文档解析成功，但知识库摄取失败: filename=%s", filename)
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
    return _doc_out(doc)


@router.post(
    "/import-jobs/upload",
    response_model=ImportBatchOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_upload_jobs(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(require_permission("knowledge_bases", "write")),
    session: Session = Depends(get_db),
) -> ImportBatchOut:
    """批量上传文件并创建异步导入任务。"""
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="至少上传一个文件"
        )
    batch = create_import_batch(
        session,
        current_user,
        total_jobs=len(files),
        source_type="file",
    )
    jobs: list[ImportJob] = []
    try:
        for file in files:
            filename = file.filename or "未命名文档"
            raw = await file.read()
            storage_path = save_source_file(current_user.tenant_id, raw, filename)
            jobs.append(
                create_upload_import_job(
                    session,
                    current_user,
                    storage_path=storage_path,
                    filename=filename,
                    content_type=file.content_type,
                    batch_id=batch.id,
                )
            )
    except OSError:
        logger.exception("创建上传导入任务时保存源文件失败")
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
    stmt = stmt.order_by(ImportJob.updated_at.desc())
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="导入任务不存在或无权访问"
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="导入批次不存在或无权访问"
        )
    jobs = list(
        session.exec(
            select(ImportJob)
            .where(ImportJob.batch_id == batch.id)
            .order_by(ImportJob.created_at.asc())
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="导入任务不存在或无权访问"
        )
    if not job.storage_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="源文件不存在"
        )
    try:
        file_path = resolve_source_file_path(job.storage_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="源文件不存在"
        )
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
    return [
        _doc_out(d)
        for d in rag.list_documents(
            current_user,
            include_deleted=include_deleted,
            version_state=version_state,
        )
    ]


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问"
        )
    return DocumentDetail(**_doc_out(doc).model_dump())


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
    if (
        doc is not None
        and current_user.role_enum == Role.SYSTEM_ADMIN
        and doc.tenant_id != current_user.tenant_id
    ):
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问"
        )
    if not doc.storage_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="源文件不存在"
        )
    try:
        file_path = resolve_source_file_path(doc.storage_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="源文件不存在"
        )
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="源文件不存在"
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问"
        )
    try:
        consume_confirmation(session, current_user, doc, "delete", confirmation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    owner_tenant_id = doc.tenant_id
    rag = RAGService(session, owner_tenant_id)
    ok = await rag.delete_document(document_id, current_user)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问"
        )
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
    return _doc_out(published)


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
    return _doc_out(doc)


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
    return _doc_out(doc)


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
        .order_by(OperationConfirmation.created_at.desc())
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
    """对知识库做混合检索，返回融合排序后的分块。"""
    if not req.query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="query 不能为空"
        )
    rag = RAGService(session, current_user.tenant_id)
    results = await rag.search(req.query, req.top_k, backend=req.backend)
    return [
        SearchResultOut(
            document_id=r.document_id,
            content=r.content,
            source=r.source,
            score=round(r.score, 6),
        )
        for r in results
    ]
