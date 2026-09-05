"""RAG 接口：文档摄取、管理与检索。

所有接口均需认证，并按角色校验 ``knowledge_bases`` 资源权限（依赖 require_permission）。
知识库是租户共享资产，删除权限仅授予管理员，写入与读取对成员开放。
归属校验在 RAG 服务内完成：普通用户仅能操作自己创建的文档，
系统管理员可见同租户全部。
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import audit_event, get_db, require_permission
from app.audit.models import AuditAction
from app.models.rag import Document
from app.models.user import User
from app.rag.document_parsers import (
    DocumentOcrRequiredError,
    DocumentParseError,
    DocumentTextEmptyError,
    UnsupportedDocumentTypeError,
    parse_uploaded_document,
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
    backend: str | None = None  # 可选：native | langchain | llamaindex，覆盖 RAG_BACKEND

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


class DocumentOut(BaseModel):
    """文档概要。"""

    id: str
    tenant_id: str
    user_id: str
    title: str
    source: str | None
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


def _doc_out(doc: Document) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        tenant_id=doc.tenant_id,
        user_id=doc.user_id,
        title=doc.title,
        source=doc.source,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at.isoformat(),
        updated_at=doc.updated_at.isoformat(),
    )


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
    # 按当前用户租户创建 RAG 服务，保证多租户数据隔离
    rag = RAGService(session, current_user.tenant_id)
    try:
        # 分块、嵌入并落库；backend 可覆盖默认 RAG 后端
        doc = await rag.ingest_text(
            req.text, req.title, req.source, current_user.id, backend=req.backend
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
        doc = await rag.ingest_text(
            parsed.text, parsed.title, parsed.source, current_user.id
        )
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
        },
    )
    return _doc_out(doc)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    current_user: User = Depends(require_permission("knowledge_bases", "read")),
    session: Session = Depends(get_db),
) -> list[DocumentOut]:
    """列出当前用户可见的文档。"""
    rag = RAGService(session, current_user.tenant_id)
    return [_doc_out(d) for d in rag.list_documents(current_user)]


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


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    request: Request,
    current_user: User = Depends(require_permission("knowledge_bases", "delete")),
    session: Session = Depends(get_db),
) -> dict:
    """删除文档及其分块。"""
    rag = RAGService(session, current_user.tenant_id)
    ok = await rag.delete_document(document_id, current_user)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问"
        )
    await audit_event(
        request,
        AuditAction.KNOWLEDGE_BASE_DELETE,
        user=current_user,
        resource_type="document",
        resource_id=document_id,
    )
    return {"deleted": True}


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
