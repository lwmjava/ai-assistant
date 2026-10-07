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
from app.rag.index_identity import (
    EmbeddingIndexIdentity,
    IndexIdentityError,
    current_backend,
    identity_matches_row,
)

logger = logging.getLogger(__name__)


class IndexUnavailableError(RuntimeError):
    """检索面不可用：没有生效索引，且存在身份未知的历史分块。

    与 :class:`IndexIdentityError` 的区别：后者是「身份明确不符」（走错索引），
    前者是「身份根本没登记」（不知道这些向量属于谁）。两者都必须显式失败，
    不能退化成「查过了没有」。
    """


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


def chunk_count_for(session: Session, index_id: str) -> int:
    """索引名下的**实际**分块数，而不是登记表里的自报数字。"""
    return len(
        session.exec(
            select(DocumentChunk).where(col(DocumentChunk.index_id) == index_id)
        ).all()
    )


def has_any_chunk(session: Session, tenant_id: str | None = None) -> bool:
    """是否存在任何分块（含身份未知的历史块）。

    用于区分「空库」与「有历史数据但没登记索引」：前者按 no_hit 处理即可，
    后者必须显式报 unavailable，否则等于把未知向量当成兼容数据检索。
    """
    stmt = select(DocumentChunk.id).limit(1)  # type: ignore[call-arg]
    if tenant_id:
        stmt = stmt.where(col(DocumentChunk.tenant_id) == tenant_id)
    return session.exec(stmt).first() is not None


def activate_index(
    session: Session,
    index_id: str,
    *,
    expected_identity: EmbeddingIndexIdentity | None = None,
    allow_empty: bool = False,
    commit: bool = True,
) -> EmbeddingIndex:
    """激活指定索引，同后端的其它 active 索引转为 ``retired``（保留，不删）。

    校验下沉到这个领域入口，脚本只是调用者，因此无法绕过：

    - 目标不存在 / 状态为 ``failed``：拒绝；
    - 给了 ``expected_identity`` 就必须**全字段**相等（不能只比 model/dim/version，
      否则 provider、部署端点、归一化或度量变了也查不出来）；
    - 实际分块数为 0 且未显式 ``allow_empty``：拒绝——空索引一旦激活，
      整个知识库会表现为正常 no_hit，是静默的数据丢失。

    ``commit=False`` 用于写入事务内部：索引记录随业务提交一起落库，
    避免中途提交半个事务。
    """
    target = session.get(EmbeddingIndex, index_id)
    if target is None:
        raise IndexIdentityError(f"索引 {index_id} 不存在，无法激活")
    if target.status == IndexStatus.FAILED.value:
        raise IndexIdentityError(
            f"索引 {target.name} 状态为 failed，不得激活；"
            f"失败证据：{target.notes or '（无）'}。请先修复或改用 rebuild。"
        )
    if expected_identity is not None and not identity_matches_row(expected_identity, target):
        raise IndexIdentityError(
            f"当前嵌入身份 {expected_identity.key()} 与目标索引 {target.name} 的身份 "
            f"{target.identity_key} 不一致，拒绝激活"
            "（只改索引名等于用新模型查旧向量，ADR-0008 明令禁止）"
        )
    actual = chunk_count_for(session, target.id)
    if actual == 0 and not allow_empty:
        raise IndexIdentityError(
            f"索引 {target.name} 名下没有任何分块，拒绝激活空索引"
            "（激活后知识库会表现为正常 no_hit，属于静默数据丢失）"
        )
    if actual != target.chunk_count:
        # 登记自报数与实际不一致时以实际为准并留痕，不让错误数字误导运维。
        logger.warning(
            "embedding_index_chunk_count_mismatch name=%s registered=%s actual=%s",
            target.name, target.chunk_count, actual,
        )
        target.chunk_count = actual
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
        # 空库首次摄取：库里还没有任何向量，不存在「与别的模型混用」的可能，
        # 因此这里唯一允许激活空索引。之后每一次激活都必须带上真实分块。
        return activate_index(session, created.id, allow_empty=True, commit=commit)
    if active.identity_key != identity.key():
        raise IndexIdentityError(
            f"当前嵌入身份 {identity.key()} 与生效索引 {active.name} 的身份 "
            f"{active.identity_key} 不一致，拒绝写入（需重建新索引后再显式切换）"
        )
    return active


def resolve_read_index(session: Session, backend: str | None = None) -> EmbeddingIndex | None:
    """读取侧身份：返回当前 active 索引；None 表示没有任何已激活索引。

    只做登记查询，**不做核对**。真正的读路径一律走 :func:`resolve_read_index_for`。
    """
    return active_index(session, backend)


def resolve_read_index_for(
    session: Session,
    identity: EmbeddingIndexIdentity | None = None,
    *,
    tenant_id: str | None = None,
    backend: str | None = None,
) -> EmbeddingIndex | None:
    """读取侧身份核对：返回可安全检索的 active 索引。

    这是 Local / Milvus / 三种 backend 共用的唯一读侧校验点：

    - 没有 active 索引且库里**没有任何分块**：返回 ``None``，调用方按 no_hit 处理
      （空库就是没有，不该报 unavailable）；
    - 没有 active 索引但存在分块（历史数据身份未知）：抛
      :class:`IndexUnavailableError`。ADR-0008 禁止自动认定兼容，
      这些向量只能先 adopt（有证据）或 rebuild；
    - 给了 ``identity`` 且与 active 登记不符：抛 :class:`IndexIdentityError`，
      这就是「同维异模型查询」的拒绝点。
    """
    active = active_index(session, backend)
    if active is None:
        if not has_any_chunk(session, tenant_id):
            return None
        raise IndexUnavailableError(
            "没有生效的 embedding 索引，但库中存在身份未知的历史分块；"
            "不能按当前模型检索未知向量（ADR-0008 禁止自动认定兼容）。"
            "请先执行 adopt（有证据地登记）或 rebuild（重建）后再检索。"
        )
    if identity is not None and not identity_matches_row(identity, active):
        raise IndexIdentityError(
            f"当前嵌入身份 {identity.key()} 与生效索引 {active.name} 的身份 "
            f"{active.identity_key} 不一致，拒绝检索"
            "（同维度不同模型的向量不可比，需用匹配模型重建后再查）"
        )
    return active


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
    evidence: str = "",
    declared_model: str | None = None,
) -> int:
    """把身份未知的历史分块登记到指定索引。

    这是 ADR-0008 要求的「有证据的登记路径」：

    - 必须提供 ``evidence``（声明人 / 依据 / 原模型与版本），并持久化到
      ``notes``；没有证据就拒绝——维度相同不能证明向量空间相同；
    - ``declared_model`` 一旦给出，必须与目标索引登记的模型一致，
      否则就是「把模型 A 的向量标成模型 B」；
    - 必须**先抽样校验维度**与索引登记一致，有任何一条对不上就整体拒绝，不写一半。
    """
    evidence = (evidence or "").strip()
    if not evidence:
        raise IndexIdentityError(
            "登记历史分块必须提供证据（声明人 / 依据 / 原模型与版本）；"
            "维度相同不足以证明向量空间相同。无法举证请改用 rebuild。"
        )
    if declared_model is not None and declared_model.strip() != index.model:
        raise IndexIdentityError(
            f"声明的原模型 {declared_model.strip()!r} 与目标索引 {index.name} 登记的 "
            f"model={index.model!r} 不一致，拒绝登记：这等于把 A 的向量标成 B。"
            "同维度不同模型的向量不可比，请改用 rebuild。"
        )
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
    stamp = _now().isoformat(timespec="seconds")
    index.notes = (
        f"[{stamp}] adopt legacy chunks: count={len(rows)} "
        f"declared_model={declared_model or '（未声明）'} tenant={tenant_id or '*'} "
        f"evidence={evidence}"
    )
    session.add(index)
    session.commit()
    logger.info(
        "embedding_index_adopted_legacy index=%s count=%s tenant=%s",
        index.name, len(rows), tenant_id or "*",
    )
    return len(rows)
