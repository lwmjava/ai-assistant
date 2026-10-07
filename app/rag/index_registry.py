"""向量索引登记与受控切换（ADR-0008）。

索引不是「一个向量库」而是「一套同模型、同维度、同归一化与度量的向量集合」。
换模型时不原地改写，而是登记新索引、重建、校验后显式激活；旧索引保留用于回退，
不随切换自动清空。

历史分块（``index_id IS NULL``）**默认不参与检索**——ADR-0008 明确禁止自动认定
历史索引与当前配置兼容。它们不是被删掉，而是被隔离，数量在 health 里可见；
要纳入当前索引必须走 :func:`adopt_legacy_chunks`，且必须通过维度抽样校验。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlmodel import Session, col, select

from app.models.rag import DocumentChunk, EmbeddingIndex, IndexStatus
from app.rag.index_identity import EmbeddingIndexIdentity, IndexIdentityError, current_backend

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def index_name(identity: EmbeddingIndexIdentity) -> str:
    """索引（或 Milvus 集合）名：带上版本与身份指纹，杜绝原地混写。"""
    base = "local" if identity.backend == "local" else None
    return identity.collection_name(base)


def find_by_identity(
    session: Session, identity: EmbeddingIndexIdentity
) -> EmbeddingIndex | None:
    stmt = select(EmbeddingIndex).where(
        col(EmbeddingIndex.backend) == identity.backend,
        col(EmbeddingIndex.identity_key) == identity.key(),
    )
    return session.exec(stmt).first()


def active_index(session: Session, backend: str | None = None) -> EmbeddingIndex | None:
    """当前生效的索引；同一后端理论上只有一个 active，取最新的以防并发登记。"""
    name = backend or current_backend()
    stmt = (
        select(EmbeddingIndex)
        .where(
            col(EmbeddingIndex.backend) == name,
            col(EmbeddingIndex.status) == IndexStatus.ACTIVE.value,
        )
        .order_by(col(EmbeddingIndex.created_at).desc())
    )
    return session.exec(stmt).first()


def ensure_index(session: Session, identity: EmbeddingIndexIdentity) -> EmbeddingIndex:
    """按身份取索引，不存在则登记为 ``preparing``。不自动激活。"""
    found = find_by_identity(session, identity)
    if found is not None:
        return found
    row = EmbeddingIndex(
        name=index_name(identity),
        backend=identity.backend,
        provider=identity.provider,
        model=identity.model,
        deployment=identity.deployment,
        dim=identity.dim,
        index_version=identity.index_version,
        normalization=identity.normalization,
        metric=identity.metric,
        identity_key=identity.key(),
        status=IndexStatus.PREPARING.value,
    )
    session.add(row)
    session.flush()
    logger.info(
        "embedding_index_registered name=%s backend=%s model=%s dim=%s version=%s",
        row.name, row.backend, row.model, row.dim, row.index_version,
    )
    return row


def activate_index(session: Session, index_id: str, *, commit: bool = True) -> EmbeddingIndex:
    """激活指定索引，同后端的其它 active 索引转为 ``retired``（保留，不删）。

    ``commit=False`` 用于写入事务内部：索引记录随业务提交一起落库，
    避免中途提交半个事务。
    """
    target = session.get(EmbeddingIndex, index_id)
    if target is None:
        raise IndexIdentityError(f"索引 {index_id} 不存在，无法激活")
    now = _now()
    for row in session.exec(
        select(EmbeddingIndex).where(
            col(EmbeddingIndex.backend) == target.backend,
            col(EmbeddingIndex.status) == IndexStatus.ACTIVE.value,
        )
    ).all():
        if row.id == target.id:
            continue
        row.status = IndexStatus.RETIRED.value
        row.retired_at = now
        session.add(row)
    target.status = IndexStatus.ACTIVE.value
    target.activated_at = now
    target.retired_at = None
    session.add(target)
    if commit:
        session.commit()
    else:
        session.flush()
    logger.info(
        "embedding_index_activated name=%s backend=%s model=%s dim=%s",
        target.name, target.backend, target.model, target.dim,
    )
    return target


def resolve_write_index(
    session: Session, identity: EmbeddingIndexIdentity, *, commit: bool = True
) -> EmbeddingIndex:
    """写入侧身份核对：返回与当前模型身份一致的 active 索引。

    - 无 active 索引（首次使用）：登记并激活。空索引不存在混用问题。
    - active 索引身份与当前模型不一致：**拒绝写入**，而不是静默混进同一批向量。
    """
    active = active_index(session, identity.backend)
    if active is None:
        created = ensure_index(session, identity)
        return activate_index(session, created.id, commit=commit)
    if active.identity_key != identity.key():
        raise IndexIdentityError(
            f"当前嵌入身份 {identity.key()} 与生效索引 {active.name} 的身份 "
            f"{active.identity_key} 不一致，拒绝写入（需重建新索引后再显式切换）"
        )
    return active


def resolve_read_index(session: Session, backend: str | None = None) -> EmbeddingIndex | None:
    """读取侧身份：返回当前 active 索引；None 表示没有任何已激活索引。"""
    return active_index(session, backend)


def legacy_chunk_count(session: Session, tenant_id: str | None = None) -> int:
    """身份未知的历史分块数。用于暴露「被隔离的数据有多少」，不静默。"""
    stmt = select(DocumentChunk).where(col(DocumentChunk.index_id).is_(None))
    if tenant_id:
        stmt = stmt.where(col(DocumentChunk.tenant_id) == tenant_id)
    return len(session.exec(stmt).all())


def adopt_legacy_chunks(
    session: Session,
    index: EmbeddingIndex,
    *,
    tenant_id: str | None = None,
    require_dim_match: bool = True,
) -> int:
    """把身份未知的历史分块登记到指定索引。

    这是 ADR-0008 要求的「有证据的登记路径」：必须**先抽样校验维度**与索引登记一致，
    有任何一条对不上就整体拒绝，不写一半。调用方（脚本）需先取得人工确认。
    """
    stmt = select(DocumentChunk).where(col(DocumentChunk.index_id).is_(None))
    if tenant_id:
        stmt = stmt.where(col(DocumentChunk.tenant_id) == tenant_id)
    rows = session.exec(stmt).all()
    mismatched: list[str] = []
    for row in rows:
        if not row.embedding:
            continue
        try:
            vector = json.loads(row.embedding)
        except (TypeError, ValueError):
            mismatched.append(row.id)
            continue
        if not isinstance(vector, list) or (require_dim_match and len(vector) != index.dim):
            mismatched.append(row.id)
    if mismatched:
        raise IndexIdentityError(
            f"{len(mismatched)} 条历史分块维度与索引 {index.name} 登记的 dim={index.dim} 不一致，"
            f"拒绝登记（样例：{mismatched[:5]}）。请改用重建，不要强行认定兼容。"
        )
    for row in rows:
        row.index_id = index.id
        session.add(row)
    session.commit()
    logger.info(
        "embedding_index_adopted_legacy index=%s count=%s tenant=%s",
        index.name, len(rows), tenant_id or "*",
    )
    return len(rows)
