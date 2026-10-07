"""RAG-027：引用原文的只读核验（ADR-0007）。

本模块是核验的**唯一受控入口**：每次调用都重新从库里取出 Chunk 与它的
Document，按「主体 → 租户 → Document/Chunk 真实关联 → 未软删 → 当前版本与
生效规则 → uploader 归属」逐项复核，任一项不满足即失败关闭。

- 来源标识和缓存文本不能充当授权证明：``chunk_id`` 只是待核验的线索，
  不是已经通过的票据，因此不信任任何随请求传入的关联（含可选 ``document_id``）。
- 失败关闭使用同一个文案与同一个 404：不返回正文，也不泄漏「这个 ID 存在
  但没权限」这类存在性细节。
- 只返回**该命中块本身**的正文与定位信息，不提供整章或整文件的旁路读取。
  父块必须独立复核同文档关系 + 同一检索授权，默认只给定位信息。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlmodel import Session

from app.core.config import settings
from app.models.rag import Document, DocumentChunk
from app.models.user import User
from app.rag.access import can_read_document
from app.rag.effective_date import document_is_live, ensure_utc

# 越权与缺失共用同一文案：调用方无法据此区分「不存在」与「无权」。
EVIDENCE_DENIED_MESSAGE = "引用原文不可用或无权核验"


class EvidenceDeniedError(Exception):
    """核验失败关闭。携带的信息不向调用方暴露。"""


@dataclass(frozen=True)
class ChunkLocator:
    """块的定位信息：块序、页码 / 段落、源范围。没有的一律为 ``None``。"""

    chunk_id: str
    chunk_index: int
    page: int | None = None
    section: str | None = None
    source_start: int | None = None
    source_end: int | None = None
    # 仅父块在显式请求且通过独立鉴权后带正文；默认 ``None``。
    content: str | None = None


@dataclass(frozen=True)
class ChunkEvidence:
    """一次核验的结果：只有命中块正文，加必要定位信息。"""

    chunk: ChunkLocator
    content: str
    document_id: str
    document_title: str
    version_state: str
    source: str | None
    parent: ChunkLocator | None


def _metadata(chunk: DocumentChunk) -> dict:
    if not chunk.chunk_metadata:
        return {}
    try:
        parsed = json.loads(chunk.chunk_metadata)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_int(value: object) -> int | None:
    """只接受非负整数；布尔是 int 的子类，单独排除。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _as_page(value: object) -> int | None:
    page = _as_int(value)
    return page if page is not None and page >= 1 else None


def _as_section(value: object) -> str | None:
    """段落路径可能是字符串或字符串数组，统一成字符串。"""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list):
        parts = [str(item) for item in value if str(item)]
        if parts:
            return " / ".join(parts)
    return None


def _locator(chunk: DocumentChunk) -> ChunkLocator:
    metadata = _metadata(chunk)
    return ChunkLocator(
        chunk_id=chunk.id,
        chunk_index=chunk.chunk_index,
        page=_as_page(metadata.get("page")),
        section=_as_section(metadata.get("section_path")),
        source_start=_as_int(metadata.get("source_start")),
        source_end=_as_int(metadata.get("source_end")),
    )


def _document_is_verifiable(doc: Document, user: User, as_of: datetime) -> bool:
    """检索面授权 + 时效语义（ADR-0007 §1 / §5）。

    ``can_read_document`` 承担同租户、未软删、当前版本与 uploader 归属；
    本函数只补上时效开关：开关打开时还要落在生效窗口内，未发布、已替换、
    已过期的版本不通过普通引用核验读取。
    """
    if not can_read_document(doc, user):
        return False
    if not settings.RAG_EFFECTIVE_DATE_FILTER:
        return True
    return document_is_live(doc.is_current, doc.effective_at, doc.expires_at, as_of)


def _authorized_parent(
    session: Session,
    chunk: DocumentChunk,
    doc: Document,
    user: User,
    as_of: datetime,
    include_content: bool,
) -> ChunkLocator | None:
    """父块独立鉴权（ADR-0007 §2）。

    子块有权不代表父块有权：父子必须同属一个文档，父块所属文档也要独立通过
    同一套检索授权与时效校验。任一项不满足就整体不返回——既不返回父正文，
    也不返回父块的存在性线索。
    """
    if not chunk.parent_id:
        return None
    parent = session.get(DocumentChunk, chunk.parent_id)
    if parent is None:
        return None
    # 同文档关系：跨文档（含他人文档、他租户文档、旧版本文档）一律不认。
    if parent.document_id != chunk.document_id:
        return None
    parent_doc = doc if parent.document_id == doc.id else session.get(Document, parent.document_id)
    if parent_doc is None or parent.tenant_id != doc.tenant_id:
        return None
    if not _document_is_verifiable(parent_doc, user, as_of):
        return None
    locator = _locator(parent)
    if not include_content:
        return locator
    # 显式请求时才带正文：仍是单个父块，不是整章或整文件。
    return replace(locator, content=parent.content)


def load_chunk_evidence(
    session: Session,
    chunk_id: str,
    user: User,
    *,
    document_id: str | None = None,
    include_parent_content: bool = False,
    as_of: datetime | None = None,
) -> ChunkEvidence:
    """核验单个命中块，越权或缺失抛 :class:`EvidenceDeniedError`。

    Args:
        session: 数据库会话。
        chunk_id: 待核验的块 ID（只是线索，不是授权证明）。
        user: 当前鉴权主体。
        document_id: 调用方声称的所属文档；与实际关联不一致即拒绝。
        include_parent_content: 是否连同父块正文返回（默认只给父块定位信息）。
        as_of: 时效判定的基准时刻，缺省为当前时间。

    Raises:
        EvidenceDeniedError: 任一项复核不通过。
    """
    if not chunk_id:
        raise EvidenceDeniedError("missing chunk_id")
    moment = ensure_utc(as_of) if as_of is not None else datetime.now(UTC)
    chunk = session.get(DocumentChunk, chunk_id)
    if chunk is None:
        raise EvidenceDeniedError("chunk missing")
    # 真实关联：按 block 自己记录的 document_id 取文档，不信传入的 ID。
    doc = session.get(Document, chunk.document_id)
    if doc is None:
        raise EvidenceDeniedError("document missing")
    # 关联自洽：块与文档的租户不一致说明数据被拼改或伪造，失败关闭。
    if chunk.tenant_id != doc.tenant_id:
        raise EvidenceDeniedError("tenant mismatch between chunk and document")
    if document_id is not None and chunk.document_id != document_id:
        raise EvidenceDeniedError("claimed document does not own the chunk")
    if not _document_is_verifiable(doc, user, moment):
        raise EvidenceDeniedError("document not verifiable for subject")
    return ChunkEvidence(
        chunk=_locator(chunk),
        content=chunk.content,
        document_id=doc.id,
        document_title=doc.title,
        version_state=doc.version_state,
        source=chunk.source,
        parent=_authorized_parent(session, chunk, doc, user, moment, include_parent_content),
    )
