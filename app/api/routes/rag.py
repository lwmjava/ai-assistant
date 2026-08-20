"""RAG 接口：文档摄取、管理与检索。

所有接口均需认证（依赖 get_current_user）。归属校验在 RAG 服务内完成：
普通用户仅能操作自己创建的文档，系统管理员可见同租户全部。
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, status, UploadFile
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_current_user, get_db
from app.models.rag import Document
from app.models.user import User
from app.rag.service import RAGService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])

_service = RAGService  # 仅作类型占位，实际每个请求新建实例以绑定会话


class IngestRequest(BaseModel):
    """文本摄取请求体。"""

    text: str
    title: str
    source: str | None = None


class SearchRequest(BaseModel):
    """检索请求体。"""

    query: str
    top_k: int | None = None


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
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> DocumentOut:
    """摄取一段文本为知识文档（自动分块与嵌入）。"""
    if not req.text.strip() or not req.title.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="text 与 title 不能为空"
        )
    rag = RAGService(session, current_user.tenant_id)
    try:
        doc = await rag.ingest_text(req.text, req.title, req.source, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _doc_out(doc)


@router.post("/documents/upload", response_model=DocumentOut)
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> DocumentOut:
    """上传文本文件（.txt / .md）并摄取为知识文档。"""
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if file.filename else ""
    if ext not in ("txt", "md"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持 .txt / .md 文件"
        )
    try:
        raw = await file.read()
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="文件不是有效的 UTF-8 文本"
        )
    title = (file.filename or "未命名文档").rsplit(".", 1)[0]
    rag = RAGService(session, current_user.tenant_id)
    try:
        doc = await rag.ingest_text(text, title, file.filename, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _doc_out(doc)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[DocumentOut]:
    """列出当前用户可见的文档。"""
    rag = RAGService(session, current_user.tenant_id)
    return [_doc_out(d) for d in rag.list_documents(current_user)]


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
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
def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    """删除文档及其分块。"""
    rag = RAGService(session, current_user.tenant_id)
    ok = rag.delete_document(document_id, current_user)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问"
        )
    return {"deleted": True}


@router.post("/search", response_model=list[SearchResultOut])
async def search(
    req: SearchRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[SearchResultOut]:
    """对知识库做混合检索，返回融合排序后的分块。"""
    if not req.query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="query 不能为空"
        )
    rag = RAGService(session, current_user.tenant_id)
    results = await rag.search(req.query, req.top_k)
    return [
        SearchResultOut(
            document_id=r.document_id,
            content=r.content,
            source=r.source,
            score=round(r.score, 6),
        )
        for r in results
    ]
