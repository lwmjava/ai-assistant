"""RAG-032：Embedding 索引身份与受控切换（ADR-0008）。

重点是「同维度异模型不得混用」——光看维度发现不了，必须核对完整身份。
证据要求是故障注入后入口的实际表现，不是测试条数。
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import engine, init_db
from app.models.rag import Document, DocumentChunk, EmbeddingIndex, IndexStatus
from app.rag.index_identity import (
    EmbeddingIndexIdentity,
    IndexIdentityError,
    current_backend,
    identity_from_provider,
)
from app.rag.index_registry import (
    activate_index,
    active_index,
    adopt_legacy_chunks,
    ensure_index,
    legacy_chunk_count,
    resolve_read_index,
    resolve_write_index,
)
from app.rag.vectorstore.local import LocalVectorStore


class _Provider:
    """最小嵌入 provider 桩：只提供身份需要的 model / dim / deployment。"""

    def __init__(self, model: str = "text-embedding-v3", dim: int = 8, deployment: str = ""):
        self.model = model
        self.dim = dim
        self.deployment = deployment


def _identity(model: str = "text-embedding-v3", dim: int = 8, **kwargs) -> EmbeddingIndexIdentity:
    return EmbeddingIndexIdentity(
        backend=kwargs.get("backend", current_backend()),
        provider=kwargs.get("provider", "stub"),
        model=model,
        dim=dim,
        index_version=kwargs.get("index_version", "1"),
        deployment=kwargs.get("deployment", ""),
        normalization=kwargs.get("normalization", "l2"),
        metric=kwargs.get("metric", "cosine"),
    )


# 本文件里的用例自建文档与分块，而 legacy_chunk_count / adopt 是**全局**计数，
# 不清理会让跨用例残留改变断言结果（单独跑通过、整文件跑失败）。
_CREATED: list[tuple[str, str]] = []


@pytest.fixture
def db() -> Session:
    init_db()
    session = Session(engine)
    _CREATED.clear()
    for row in session.exec(select(EmbeddingIndex)).all():
        session.delete(row)
    session.commit()
    yield session
    for chunk_id, doc_id in _CREATED:
        row = session.get(DocumentChunk, chunk_id)
        if row is not None:
            session.delete(row)
        doc = session.get(Document, doc_id)
        if doc is not None:
            session.delete(doc)
    for row in session.exec(select(EmbeddingIndex)).all():
        session.delete(row)
    session.commit()
    session.close()


# ── 1. 身份构造 ────────────────────────────────────────────


def test_same_dim_different_model_has_different_identity() -> None:
    """本卡核心：同维度异模型必须判为不同身份，否则维度校验发现不了混用。"""
    a = _identity("model-a", 1024)
    b = _identity("model-b", 1024)
    assert a.dim == b.dim
    assert a.key() != b.key()
    assert a.fingerprint() != b.fingerprint()


def test_normalization_and_metric_are_part_of_identity() -> None:
    """归一化/度量变了向量数值就不可比，必须进身份。"""
    base = _identity("m", 8)
    assert base.key() != _identity("m", 8, normalization="none").key()
    assert base.key() != _identity("m", 8, metric="ip").key()


def test_deployment_is_not_guessed_from_model_name() -> None:
    """部署标识缺失时留空，不拿模型名顶替——否则不同部署会被判为同一索引。"""
    provider = _Provider(model="m", dim=8)
    identity = identity_from_provider(provider)
    assert identity.deployment == ""
    provider.deployment = "rev-2"
    assert identity_from_provider(provider).deployment == "rev-2"


def test_identity_rejects_non_positive_dim() -> None:
    with pytest.raises(ValueError):
        _identity("m", 0)


def test_collection_name_differs_when_identity_differs() -> None:
    a = _identity("model-a", 1024, backend="milvus")
    b = _identity("model-b", 1024, backend="milvus")
    assert a.collection_name("base") != b.collection_name("base")


# ── 2. 写入侧身份核对 ──────────────────────────────────────


def test_first_write_registers_and_activates_an_index(db: Session) -> None:
    index = resolve_write_index(db, _identity("m", 8))
    assert index.status == IndexStatus.ACTIVE.value
    assert active_index(db) is not None
    assert active_index(db).id == index.id


def test_writing_a_different_model_into_active_index_is_refused(db: Session) -> None:
    """同维度异模型写进同一索引必须拒绝，而不是静默混进同一批向量。"""
    resolve_write_index(db, _identity("model-a", 1024))
    with pytest.raises(IndexIdentityError) as exc:
        resolve_write_index(db, _identity("model-b", 1024))
    assert "不一致" in str(exc.value)


def test_same_identity_write_is_idempotent(db: Session) -> None:
    first = resolve_write_index(db, _identity("m", 8))
    second = resolve_write_index(db, _identity("m", 8))
    assert first.id == second.id


# ── 3. 读取侧身份过滤 ──────────────────────────────────────


def _chunk(
    session: Session,
    *,
    index_id: str | None,
    dim: int = 8,
    text: str = "甲",
    tenant: str | None = None,
) -> DocumentChunk:
    tenant = tenant or f"t-{uuid4().hex[:8]}"
    doc = Document(
        id=f"d-{uuid4().hex[:8]}",
        tenant_id=tenant,
        user_id=f"u-{uuid4().hex[:8]}",
        title="t",
        source="s",
        content_hash=f"h-{uuid4().hex[:8]}",
        is_current=True,
        chunk_count=1,
    )
    session.add(doc)
    session.flush()
    row = DocumentChunk(
        id=f"c-{uuid4().hex[:8]}",
        tenant_id=tenant,
        document_id=doc.id,
        content=text,
        source="s",
        embedding=json.dumps([0.1] * dim),
        tokens=json.dumps([text]),
        index_id=index_id,
    )
    session.add(row)
    session.commit()
    _CREATED.append((row.id, doc.id))
    return row


@pytest.mark.asyncio
async def test_search_only_returns_chunks_of_the_active_index(db: Session) -> None:
    """切到新索引后，旧索引名下的分块必须退出检索——这正是「阻断混用」。"""
    old = resolve_write_index(db, _identity("model-a", 8))
    old_chunk = _chunk(db, index_id=old.id, text="甲")
    store = LocalVectorStore(db)
    hits = await store.hybrid_search([0.1] * 8, ["甲"], old_chunk.tenant_id, 5)
    assert [h.id for h in hits] == [old_chunk.id]

    # 切到新索引（模拟换模型后重建）
    new = ensure_index(db, _identity("model-b", 8))
    activate_index(db, new.id)
    hits = await store.hybrid_search([0.1] * 8, ["甲"], old_chunk.tenant_id, 5)
    assert hits == [], "旧索引的分块不应再出现在新索引的检索结果里"


@pytest.mark.asyncio
async def test_query_dim_mismatch_against_index_is_refused(db: Session) -> None:
    """查询向量维度与索引登记不符必须报错，不能退化成「查过了没有」。"""
    index = resolve_write_index(db, _identity("m", 8))
    row = _chunk(db, index_id=index.id)
    store = LocalVectorStore(db)
    with pytest.raises(IndexIdentityError):
        await store.hybrid_search([0.1] * 16, ["甲"], row.tenant_id, 5)


@pytest.mark.asyncio
async def test_no_registered_index_keeps_legacy_read_behaviour(db: Session) -> None:
    """未启用索引治理时（无索引登记）不应让存量库整体失检索。"""
    row = _chunk(db, index_id=None)
    assert resolve_read_index(db) is None
    store = LocalVectorStore(db)
    hits = await store.hybrid_search([0.1] * 8, ["甲"], row.tenant_id, 5)
    assert [h.id for h in hits] == [row.id]


@pytest.mark.asyncio
async def test_legacy_chunks_are_isolated_once_an_index_exists(db: Session) -> None:
    """ADR-0008：身份未知的历史分块不得自动认定兼容，默认不参与检索。

    它们不是被删掉，而是被隔离——数量可被 health 读到，需人工登记或重建后才纳入。
    """
    index = resolve_write_index(db, _identity("m", 8))
    known = _chunk(db, index_id=index.id, text="乙")
    legacy = _chunk(db, index_id=None, text="丙")
    store = LocalVectorStore(db)
    hits = await store.hybrid_search([0.1] * 8, ["乙"], known.tenant_id, 5)
    assert [h.id for h in hits] == [known.id]
    assert legacy.id not in [h.id for h in hits]
    assert legacy_chunk_count(db) >= 1


# ── 4. 切换与回退 ──────────────────────────────────────────


def test_activate_retires_previous_index_without_deleting_it(db: Session) -> None:
    """旧索引必须保留用于回退，不随切换自动清空。"""
    old = resolve_write_index(db, _identity("model-a", 8))
    new = ensure_index(db, _identity("model-b", 8))
    activate_index(db, new.id)
    db.refresh(old)
    assert old.status == IndexStatus.RETIRED.value
    assert old.retired_at is not None
    assert db.get(EmbeddingIndex, old.id) is not None, "旧索引记录不得被删除"


def test_rollback_restores_the_previous_index(db: Session) -> None:
    old = resolve_write_index(db, _identity("model-a", 8))
    new = ensure_index(db, _identity("model-b", 8))
    activate_index(db, new.id)
    activate_index(db, old.id)
    assert active_index(db).id == old.id
    db.refresh(new)
    assert new.status == IndexStatus.RETIRED.value


def test_activate_unknown_index_raises(db: Session) -> None:
    with pytest.raises(IndexIdentityError):
        activate_index(db, "nonexistent")


# ── 5. 历史数据登记 ────────────────────────────────────────


def test_adopt_refuses_when_any_legacy_dim_mismatches(db: Session) -> None:
    """登记必须过维度抽样；有一条对不上就整体拒绝，不写一半。"""
    index = resolve_write_index(db, _identity("m", 8))
    tenant = f"t-{uuid4().hex[:8]}"
    ok = _chunk(db, index_id=None, dim=8, tenant=tenant)
    bad = _chunk(db, index_id=None, dim=16, tenant=tenant)
    with pytest.raises(IndexIdentityError):
        adopt_legacy_chunks(db, index, tenant_id=tenant)
    db.refresh(ok)
    db.refresh(bad)
    # 拒绝时不得部分写入：两条都还是未登记状态
    assert ok.index_id is None
    assert bad.index_id is None


def test_adopt_writes_only_when_all_dims_match(db: Session) -> None:
    index = resolve_write_index(db, _identity("m", 8))
    tenant = f"t-{uuid4().hex[:8]}"
    a = _chunk(db, index_id=None, dim=8, tenant=tenant)
    b = _chunk(db, index_id=None, dim=8, tenant=tenant)
    adopted = adopt_legacy_chunks(db, index, tenant_id=tenant)
    assert adopted == 2
    db.refresh(a)
    db.refresh(b)
    assert a.index_id == index.id
    assert b.index_id == index.id


def test_adopt_is_scoped_to_the_given_tenant(db: Session) -> None:
    """登记按租户界定范围，不能顺手把别家的数据也登记进来。"""
    index = resolve_write_index(db, _identity("m", 8))
    mine = _chunk(db, index_id=None, dim=8, tenant=f"t-{uuid4().hex[:8]}")
    others = _chunk(db, index_id=None, dim=8, tenant=f"t-{uuid4().hex[:8]}")
    adopt_legacy_chunks(db, index, tenant_id=mine.tenant_id)
    db.refresh(mine)
    db.refresh(others)
    assert mine.index_id == index.id
    assert others.index_id is None


# ── 6. 配置与展示 ──────────────────────────────────────────


def test_identity_exposes_no_secret_material() -> None:
    identity = identity_from_provider(_Provider())
    rendered = json.dumps(identity.describe(), ensure_ascii=False)
    for secret in (settings.EMBEDDING_API_KEY, settings.MILVUS_TOKEN):
        if secret:
            assert secret not in rendered
    assert "://" not in identity.key(), "身份里不得夹带连接串"
