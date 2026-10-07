"""RAG-032：Embedding 索引身份与受控切换（ADR-0008）。

重点是「同维度异模型不得混用」——光看维度发现不了，必须核对完整身份。
证据要求是故障注入后入口的实际表现，不是测试条数。

本轮（审查后）把「无 active 就放开历史块」的兼容分支改为严格隔离，并补齐
§5.4 的最低补测：查询侧拒绝、激活门、preparing 目标写入、回退可读、adopt 证据、
Milvus 身份、health 可观察性、部署端点区分。
"""

from __future__ import annotations

import hashlib
import json
import sys
import types
from collections.abc import Sequence
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine, init_db
from app.models.rag import Document, DocumentChunk, EmbeddingIndex, IndexStatus
from app.rag.document_parsers.base import ParsedDocument
from app.rag.embeddings.base import EmbeddingInputPolicy, EmbeddingProvider
from app.rag.index_identity import (
    EmbeddingIndexIdentity,
    IndexIdentityError,
    current_backend,
    deployment_from_base_url,
    identity_from_provider,
    identity_matches_row,
)
from app.rag.index_registry import (
    IndexUnavailableError,
    activate_index,
    active_index,
    adopt_legacy_chunks,
    chunk_count_for,
    ensure_index,
    legacy_chunk_count,
    resolve_read_index,
    resolve_read_index_for,
    resolve_write_index,
)
from app.rag.vectorstore.local import LocalVectorStore


class _Provider:
    """最小嵌入 provider 桩：只提供身份需要的 model / dim / deployment。"""

    def __init__(self, model: str = "text-embedding-v3", dim: int = 8, deployment: str = ""):
        self.model = model
        self.dim = dim
        self.deployment = deployment


class _FixedProvider(EmbeddingProvider):
    """可用于真实摄取/检索的定长 provider：身份只由 model/dim 决定。

    必须显式声明离线无限输入策略，否则 ``RAGService`` 会因为「输入上限未核实」
    把所有分块标成 not_vectorized，检索里就没有向量可比。
    """

    def __init__(self, model: str, dim: int) -> None:
        self.model = model
        self.dim = dim
        self.input_policy = EmbeddingInputPolicy(
            max_input_tokens=None,
            counting_method="offline-unlimited",
            source="test-fixture",
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1] * self.dim for _ in texts]


def _identity(model: str = "text-embedding-v3", dim: int = 8, **kwargs) -> EmbeddingIndexIdentity:
    # provider 默认取配置标签，与 identity_from_provider 的来源保持一致：
    # 否则测试里的身份与运行时登记的身份会因为 provider 标签不同而假性不符。
    return EmbeddingIndexIdentity(
        backend=kwargs.get("backend", current_backend()),
        provider=kwargs.get("provider", settings.EMBEDDING_PROVIDER.strip().lower() or "stub"),
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


def test_deployment_is_not_guessed_from_model_name() -> None:
    """部署标识缺失时留空，不拿模型名顶替——否则不同部署会被判为同一索引。"""
    provider = _Provider(model="m", dim=8)
    identity = identity_from_provider(provider)
    assert identity.deployment == ""
    provider.deployment = "rev-2"
    assert identity_from_provider(provider).deployment == "rev-2"


def test_deployment_from_base_url_strips_credentials_and_trailing_slash() -> None:
    """部署标识要能区分端点，同时绝不能夹带凭据。"""
    assert deployment_from_base_url("") == ""
    assert deployment_from_base_url("   ") == ""
    assert (
        deployment_from_base_url("https://user:secret@api.example.com/v1/")
        == "https://api.example.com/v1"
    )
    assert "secret" not in deployment_from_base_url("https://user:secret@api.example.com/v1")
    assert "user:" not in deployment_from_base_url("https://user:secret@api.example.com/v1")
    assert deployment_from_base_url("HTTPS://DashScope.aliyuncs.COM") == "https://dashscope.aliyuncs.com"


def test_two_openai_compatible_endpoints_share_no_identity() -> None:
    """两个 OpenAI 兼容端点、相同 model/dim 必须得到不同身份键。

    反例（审查 §5.2）：此前 provider 取实现类名，两个端点落到同一个
    ``OpenAICompatibleEmbeddingProvider``，身份键完全相同——
    等于把两个互不兼容的向量空间当成同一个索引。
    """
    from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

    a = OpenAICompatibleEmbeddingProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="k",
        model="text-embedding-v3",
        dim=1024,
    )
    b = OpenAICompatibleEmbeddingProvider(
        base_url="http://localhost:11434/v1",
        api_key="k",
        model="text-embedding-v3",
        dim=1024,
    )
    id_a = identity_from_provider(a)
    id_b = identity_from_provider(b)
    assert type(a).__name__ == type(b).__name__, "前提：两个端点共用同一个实现类"
    assert id_a.model == id_b.model and id_a.dim == id_b.dim
    assert id_a.key() != id_b.key()
    assert identity_matches_row(id_a, _row_like(id_a)) is True
    assert identity_matches_row(id_a, _row_like(id_b)) is False


def _row_like(identity: EmbeddingIndexIdentity) -> EmbeddingIndex:
    """按身份造一个登记行，只用于 ``identity_matches_row`` 的比对测试。"""
    return EmbeddingIndex(
        name="row",
        identity_key=identity.key(),
    )


def test_identity_rejects_non_positive_dim() -> None:
    with pytest.raises(ValueError):
        _identity("m", 0)


def test_collection_name_differs_when_identity_differs() -> None:
    a = _identity("model-a", 1024, backend="milvus")
    b = _identity("model-b", 1024, backend="milvus")
    assert a.collection_name("base") != b.collection_name("base")


def test_unsupported_normalization_or_metric_is_rejected() -> None:
    """M-02：登记了却不生效的配置必须报错，不允许「声称按配置执行」。

    当前实现固定按 l2/cosine 执行；声明 none/ip/l2(metric) 只会得到一个
    看起来可信、实际错误的身份。
    """
    with pytest.raises(IndexIdentityError):
        _identity("m", 8, normalization="none")
    with pytest.raises(IndexIdentityError):
        _identity("m", 8, metric="ip")
    with pytest.raises(IndexIdentityError):
        _identity("m", 8, metric="l2")
    # 真实生效的那一组必须可构造
    assert _identity("m", 8, normalization="l2", metric="cosine").key()


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


# ── 3. 读取侧身份核对（严格隔离）─────────────────────────


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
    hits = await store.hybrid_search(
        [0.1] * 8,
        ["甲"],
        old_chunk.tenant_id,
        5,
        identity=identity_from_provider(_Provider("model-a", 8)),
    )
    assert [h.id for h in hits] == [old_chunk.id]

    # 切到新索引（模拟换模型后重建）
    new = ensure_index(db, _identity("model-b", 8))
    _chunk(db, index_id=new.id, text="甲", tenant=old_chunk.tenant_id)
    activate_index(db, new.id)
    hits = await store.hybrid_search(
        [0.1] * 8,
        ["甲"],
        old_chunk.tenant_id,
        5,
        identity=identity_from_provider(_Provider("model-b", 8)),
    )
    assert old_chunk.id not in [h.id for h in hits]


@pytest.mark.asyncio
async def test_query_dim_mismatch_against_index_is_refused(db: Session) -> None:
    """查询向量维度与索引登记不符必须报错，不能退化成「查过了没有」。"""
    index = resolve_write_index(db, _identity("m", 8))
    row = _chunk(db, index_id=index.id)
    store = LocalVectorStore(db)
    with pytest.raises(IndexIdentityError):
        await store.hybrid_search([0.1] * 16, ["甲"], row.tenant_id, 5)


@pytest.mark.asyncio
async def test_no_active_index_with_legacy_chunks_is_unavailable(db: Session) -> None:
    """H-05：没有生效索引且存在历史分块 → 明确 unavailable，不得当兼容数据读。

    此前这里会放开 ``index_id IS NULL`` 的过滤，把身份未知的向量直接召回；
    ADR-0008:22 明令禁止自动认定兼容。
    """
    row = _chunk(db, index_id=None)
    assert resolve_read_index(db) is None
    store = LocalVectorStore(db)
    with pytest.raises(IndexUnavailableError) as exc:
        await store.hybrid_search([0.1] * 8, ["甲"], row.tenant_id, 5)
    assert "adopt" in str(exc.value) and "rebuild" in str(exc.value)


@pytest.mark.asyncio
async def test_empty_library_without_index_is_no_hit(db: Session) -> None:
    """空库没有生效索引：就是没有数据，按 no_hit 处理，不必报 unavailable。"""
    store = LocalVectorStore(db)
    hits = await store.hybrid_search([0.1] * 8, ["甲"], f"empty-{uuid4().hex[:8]}", 5)
    assert hits == []


@pytest.mark.asyncio
async def test_same_dim_different_model_query_is_refused_by_domain_api(db: Session) -> None:
    """读取侧身份不符必须与写入侧一样被拒绝（维度相同也拦得住）。"""
    active = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=active.id, text="甲")
    store = LocalVectorStore(db)
    with pytest.raises(IndexIdentityError) as exc:
        await store.hybrid_search(
            [0.1] * 8,
            ["甲"],
            active.id,  # 任意 tenant：身份比对先于租户过滤
            5,
            identity=identity_from_provider(_Provider("model-b", 8)),
        )
    assert "不一致" in str(exc.value)


@pytest.mark.asyncio
async def test_same_dim_different_model_service_search_is_refused(db: Session) -> None:
    """§5.4-1：``RAGService.search()`` 层面拒绝同维异模型查询。"""
    from app.rag.service import RAGService

    tenant = f"svc-{uuid4().hex[:8]}"
    service_a = RAGService(db, tenant, embedding_provider=_FixedProvider("model-a", 8))
    doc = await service_a.ingest_text(
        "甲内容乙内容", title="甲文档", source="s", user_id="u1"
    )
    assert doc.id

    service_b = RAGService(db, tenant, embedding_provider=_FixedProvider("model-b", 8))
    with pytest.raises(IndexIdentityError):
        await service_b.search("甲", backend="native")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_name", ["native", "langchain", "llamaindex"])
async def test_all_backends_carry_query_identity(db: Session, backend_name: str) -> None:
    """§5.4-1：三种 backend 都把查询身份带进检索，不能只靠某一条路径拦。"""
    from app.rag.backend.factory import get_rag_backend
    from app.rag.embeddings.mock import tokenize
    from app.rag.service import RAGService

    # 工厂在依赖缺失时会静默降级为 native；先确认请求的后端真的生效，
    # 否则「覆盖了三种后端」是假的，不如显式跳过。
    probe = get_rag_backend(
        _FixedProvider("probe", 8),
        LocalVectorStore(db),
        backend=backend_name,
        tokenizer=tokenize,
        rrf_k=60,
    )
    if probe.name != backend_name:
        pytest.skip(f"{backend_name} 依赖缺失，实际生效后端为 {probe.name}")

    tenant = f"bk-{uuid4().hex[:8]}"
    service_a = RAGService(db, tenant, embedding_provider=_FixedProvider("model-a", 8))
    await service_a.ingest_text("甲内容乙内容", title="甲文档", source="s", user_id="u1")

    service_b = RAGService(db, tenant, embedding_provider=_FixedProvider("model-b", 8))
    with pytest.raises(IndexIdentityError):
        await service_b.search("甲", backend=backend_name)
    assert service_b.last_backend_name == backend_name


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


@pytest.mark.asyncio
async def test_quarantined_legacy_chunks_emit_warning_log(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """M-03：过滤后为空且存在被隔离历史块时必须是 warning，不是静默 debug。"""
    index = resolve_write_index(db, _identity("m", 8))
    tenant = f"t-{uuid4().hex[:8]}"
    _chunk(db, index_id=None, text="丙", tenant=tenant)
    _chunk(db, index_id=index.id, text="乙", tenant="other-tenant")
    store = LocalVectorStore(db)
    with caplog.at_level("WARNING", logger="app.rag.vectorstore.local"):
        hits = await store.hybrid_search([0.1] * 8, ["丙"], tenant, 5)
    assert hits == []
    warnings = [
        r for r in caplog.records if r.levelname == "WARNING"
        and "rag_no_candidate_in_active_index" in r.getMessage()
    ]
    assert warnings, "被隔离的历史数据必须留下 warning 痕迹"


# ── 4. 切换、激活门与回退 ──────────────────────────────────


def test_activate_retires_previous_index_without_deleting_it(db: Session) -> None:
    """旧索引必须保留用于回退，不随切换自动清空。"""
    old = resolve_write_index(db, _identity("model-a", 8))
    new = ensure_index(db, _identity("model-b", 8))
    _chunk(db, index_id=new.id)
    activate_index(db, new.id)
    db.refresh(old)
    assert old.status == IndexStatus.RETIRED.value
    assert old.retired_at is not None
    assert db.get(EmbeddingIndex, old.id) is not None, "旧索引记录不得被删除"


def test_activate_unknown_index_raises(db: Session) -> None:
    with pytest.raises(IndexIdentityError):
        activate_index(db, "nonexistent")


def test_activate_empty_index_is_refused(db: Session) -> None:
    """空索引不得激活：激活后知识库表现为正常 no_hit，是静默数据丢失。"""
    old = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=old.id)
    new = ensure_index(db, _identity("model-b", 8))
    assert chunk_count_for(db, new.id) == 0
    with pytest.raises(IndexIdentityError) as exc:
        activate_index(db, new.id)
    assert "空索引" in str(exc.value)
    assert active_index(db).id == old.id


def test_activate_failed_index_is_refused(db: Session) -> None:
    old = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=old.id)
    new = ensure_index(db, _identity("model-b", 8))
    _chunk(db, index_id=new.id)
    new.status = IndexStatus.FAILED.value
    new.notes = "重建失败：2 篇文档无快照"
    db.add(new)
    db.commit()
    with pytest.raises(IndexIdentityError) as exc:
        activate_index(db, new.id)
    assert "failed" in str(exc.value)
    assert active_index(db).id == old.id


def test_activate_with_mismatched_identity_is_refused(db: Session) -> None:
    """§5.4-4：直接调用领域 API 也不能绕过身份校验。"""
    old = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=old.id)
    new = ensure_index(db, _identity("model-b", 8))
    _chunk(db, index_id=new.id)
    # model/dim/version 相同，只有 deployment 不同：只比 model/dim/version 会漏掉
    same_but_deployment = _identity("model-b", 8, deployment="rev-9")
    with pytest.raises(IndexIdentityError):
        activate_index(db, new.id, expected_identity=same_but_deployment)
    assert active_index(db).id == old.id


@pytest.mark.parametrize(
    "field",
    ["provider", "deployment", "index_version", "dim"],
)
def test_activate_rejects_any_identity_field_drift(db: Session, field: str) -> None:
    """§5.4-6：回退/激活的身份核对是全字段的，任一字段漂移都拒绝。"""
    target = ensure_index(db, _identity("model-a", 8))
    _chunk(db, index_id=target.id)
    expected = _identity("model-a", 8)
    drifted = replace(expected, **{field: ("drifted" if field != "dim" else 16)})
    assert expected.key() != drifted.key()
    activate_index(db, target.id, expected_identity=expected)
    with pytest.raises(IndexIdentityError):
        activate_index(db, target.id, expected_identity=drifted)


def test_rollback_restores_the_previous_index(db: Session) -> None:
    old = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=old.id)
    new = ensure_index(db, _identity("model-b", 8))
    _chunk(db, index_id=new.id)
    activate_index(db, new.id)
    activate_index(db, old.id)
    assert active_index(db).id == old.id
    db.refresh(new)
    assert new.status == IndexStatus.RETIRED.value


@pytest.mark.asyncio
async def test_rollback_makes_old_chunks_searchable_again(db: Session) -> None:
    """§5.4-5：回退不是只改登记行——旧分块必须真的还能被检索命中。"""
    from app.rag.service import RAGService

    tenant = f"rb-{uuid4().hex[:8]}"
    service_a = RAGService(db, tenant, embedding_provider=_FixedProvider("model-a", 8))
    await service_a.ingest_text("旧模型的内容", title="旧", source="s", user_id="u1")
    old_index = active_index(db)
    assert old_index is not None
    old_chunk_ids = [
        r.id
        for r in db.exec(
            select(DocumentChunk).where(col(DocumentChunk.index_id) == old_index.id)
        ).all()
    ]
    assert old_chunk_ids, "前提：旧索引名下有分块"

    # 换模型：登记新索引并写入新的一套分块（旧分块保留）
    new_identity = identity_from_provider(_FixedProvider("model-b", 8))
    target = ensure_index(db, new_identity)
    service_b = RAGService(
        db, tenant, embedding_provider=_FixedProvider("model-b", 8), write_index_id=target.id
    )
    doc = db.exec(select(Document).where(col(Document.tenant_id) == tenant)).first()
    assert doc is not None
    await service_b.reindex_document_in_place(
        doc,
        ParsedDocument(
            text="旧模型的内容", title="旧", source="s", extension=".txt", content_type=None
        ),
        content_hash="rebuilt",
    )
    activate_index(db, target.id)
    assert active_index(db).id == target.id

    # 旧索引的分块仍在（未被就地替换删掉）
    assert chunk_count_for(db, old_index.id) == len(old_chunk_ids)

    # 回退后旧分块必须真实可检索
    activate_index(db, old_index.id, expected_identity=identity_from_provider(_FixedProvider("model-a", 8)))
    store = LocalVectorStore(db)
    hits = await store.hybrid_search(
        [0.1] * 8,
        ["旧模型"],
        tenant,
        5,
        identity=identity_from_provider(_FixedProvider("model-a", 8)),
    )
    assert any(h.id in old_chunk_ids for h in hits), "回退后旧分块必须能命中"


@pytest.mark.asyncio
async def test_rebuild_into_preparing_index_keeps_old_active_readable(db: Session) -> None:
    """§5.4-2：写 preparing 目标期间，旧 active 必须继续可读。"""
    from app.rag.service import RAGService

    tenant = f"rb2-{uuid4().hex[:8]}"
    service_a = RAGService(db, tenant, embedding_provider=_FixedProvider("model-a", 8))
    await service_a.ingest_text("甲内容乙内容", title="甲", source="s", user_id="u1")
    old_index = active_index(db)
    assert old_index is not None

    new_identity = identity_from_provider(_FixedProvider("model-b", 8))
    target = ensure_index(db, new_identity)
    target.status = IndexStatus.PREPARING.value
    db.add(target)
    db.commit()

    service_b = RAGService(
        db, tenant, embedding_provider=_FixedProvider("model-b", 8), write_index_id=target.id
    )
    doc = db.exec(select(Document).where(col(Document.tenant_id) == tenant)).first()
    assert doc is not None
    await service_b.reindex_document_in_place(
        doc,
        ParsedDocument(
            text="甲内容乙内容", title="甲", source="s", extension=".txt", content_type=None
        ),
        content_hash="rebuilt",
    )

    # 旧 active 未变、旧分块仍在，且新建的一套分块挂在新索引名下
    assert active_index(db).id == old_index.id
    assert chunk_count_for(db, old_index.id) > 0
    assert chunk_count_for(db, target.id) > 0

    store = LocalVectorStore(db)
    hits = await store.hybrid_search(
        [0.1] * 8, ["甲"], tenant, 5, identity=identity_from_provider(_FixedProvider("model-a", 8))
    )
    assert hits, "重建期间旧索引必须继续可读"


@pytest.mark.asyncio
async def test_reindex_into_active_index_replaces_only_its_own_chunks(db: Session) -> None:
    """目标 == active 时保持就地替换，但不得顺手删掉别的索引名下的分块。"""
    from app.rag.service import RAGService

    tenant = f"rp-{uuid4().hex[:8]}"
    service = RAGService(db, tenant, embedding_provider=_FixedProvider("model-a", 8))
    doc = await service.ingest_text("甲内容乙内容", title="甲", source="s", user_id="u1")
    active = active_index(db)
    assert active is not None
    before = chunk_count_for(db, active.id)

    await service.reindex_document_in_place(
        doc,
        ParsedDocument(
            text="甲内容乙内容丙内容", title="甲", source="s", extension=".txt", content_type=None
        ),
        content_hash="again",
    )
    after = chunk_count_for(db, active.id)
    assert after > 0
    assert before > 0


@pytest.mark.asyncio
async def test_parent_expansion_drops_stale_hits_of_retired_index(db: Session) -> None:
    """H-04：父块复核必须限定同一 index_id，retired 索引的 stale hit 不得展开。"""
    from app.rag.service import RAGService
    from app.rag.vectorstore.base import ChunkResult

    tenant = f"pe-{uuid4().hex[:8]}"
    service = RAGService(db, tenant, embedding_provider=_FixedProvider("model-a", 8))
    doc = await service.ingest_text(
        "父块内容。子块内容A。", title="父子", source="s", user_id="u1", strategy="parent_child"
    )
    active = active_index(db)
    assert active is not None
    chunks = db.exec(
        select(DocumentChunk).where(col(DocumentChunk.index_id) == active.id)
    ).all()
    assert chunks

    # 把其中一块改成「已退役索引名下的 stale 数据」
    retired = ensure_index(db, _identity("model-a", 8, index_version="0"))
    stale, *rest = chunks
    stale.index_id = retired.id
    db.add(stale)
    db.commit()

    stale_hit = ChunkResult(
        id=stale.id, content="旧内容", source="s", document_id=doc.id, score=0.9
    )
    expanded = await service._expand_parent_chunks(
        [stale_hit], query="", index_id=active.id
    )
    assert expanded == []

    # 对照：仍在生效索引名下的分块必须能正常返回
    live_hit = ChunkResult(
        id=rest[0].id, content="x", source="s", document_id=doc.id, score=0.9
    )
    assert await service._expand_parent_chunks(
        [live_hit], query="", index_id=active.id
    ) != []


def test_explicit_write_target_is_validated_against_provider(db: Session) -> None:
    """H-02：显式写目标仍要校验身份，不能把新模型的向量塞进旧索引。"""
    from app.rag.service import RAGService

    active = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=active.id)
    target = ensure_index(db, _identity("model-b", 8))
    service = RAGService(
        db, "t", embedding_provider=_FixedProvider("model-a", 8), write_index_id=target.id
    )
    with pytest.raises(IndexIdentityError):
        service._write_index()


def test_explicit_write_target_wins_over_active_index(db: Session) -> None:
    """H-02 反例 1：active=A、目标=B 时，写目标必须能返回 B（不是被 A 拦下）。"""
    from app.rag.service import RAGService

    active = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=active.id)
    target = ensure_index(db, _identity("model-b", 8))
    service = RAGService(
        db, "t", embedding_provider=_FixedProvider("model-b", 8), write_index_id=target.id
    )
    assert service._write_index().id == target.id
    assert active_index(db).id == active.id, "写 preparing 目标不得顺手切换 active"


# ── 5. 历史数据登记（adopt 证据）───────────────────────────


def test_adopt_refuses_when_any_legacy_dim_mismatches(db: Session) -> None:
    """登记必须过维度抽样；有一条对不上就整体拒绝，不写一半。"""
    index = resolve_write_index(db, _identity("m", 8))
    tenant = f"t-{uuid4().hex[:8]}"
    ok = _chunk(db, index_id=None, dim=8, tenant=tenant)
    bad = _chunk(db, index_id=None, dim=16, tenant=tenant)
    with pytest.raises(IndexIdentityError):
        adopt_legacy_chunks(db, index, tenant_id=tenant, evidence="e", declared_model="m")
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
    adopted = adopt_legacy_chunks(
        db, index, tenant_id=tenant, evidence="ops: 原模型即 m", declared_model="m"
    )
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
    adopt_legacy_chunks(
        db, index, tenant_id=mine.tenant_id, evidence="ops", declared_model="m"
    )
    db.refresh(mine)
    db.refresh(others)
    assert mine.index_id == index.id
    assert others.index_id is None


def test_adopt_requires_evidence(db: Session) -> None:
    """M-01：没有证据就不能把身份未知的向量认成当前模型。"""
    index = resolve_write_index(db, _identity("m", 8))
    tenant = f"t-{uuid4().hex[:8]}"
    row = _chunk(db, index_id=None, dim=8, tenant=tenant)
    with pytest.raises(IndexIdentityError) as exc:
        adopt_legacy_chunks(db, index, tenant_id=tenant)
    assert "证据" in str(exc.value)
    db.refresh(row)
    assert row.index_id is None


def test_adopt_same_dim_different_model_is_refused(db: Session) -> None:
    """反例（审查 §5.2）：active=B/dim=8 时，未知来源的 A/dim=8 历史块
    不能只凭维度相同就被标成 B。声明的原模型不符必须拒绝。"""
    index = resolve_write_index(db, _identity("model-b", 8))
    tenant = f"t-{uuid4().hex[:8]}"
    legacy = _chunk(db, index_id=None, dim=8, tenant=tenant)
    # 举证说是另一个模型产出的 → 拒绝
    with pytest.raises(IndexIdentityError) as exc:
        adopt_legacy_chunks(
            db,
            index,
            tenant_id=tenant,
            evidence="ops: 由 model-a 产出",
            declared_model="model-a",
        )
    assert "model-a" in str(exc.value)
    db.refresh(legacy)
    assert legacy.index_id is None, "同维异模型的历史分块不得被标成当前索引"


def test_adopt_persists_evidence_into_notes(db: Session) -> None:
    """证据必须落库可追溯，不能只停留在命令行输出里。"""
    index = resolve_write_index(db, _identity("m", 8))
    tenant = f"t-{uuid4().hex[:8]}"
    _chunk(db, index_id=None, dim=8, tenant=tenant)
    adopt_legacy_chunks(
        db,
        index,
        tenant_id=tenant,
        evidence="声明人=张三; 依据=部署记录 2026-09; 原模型=m v1",
        declared_model="m",
    )
    db.refresh(index)
    assert index.notes is not None
    assert "声明人=张三" in index.notes
    assert "原模型=m v1" in index.notes
    assert "m" in index.notes


# ── 6. Milvus 身份治理（假 pymilvus，不连真库）─────────────


class _FakeFieldSchema:
    def __init__(self, name: str, dtype: object, **kwargs: object) -> None:
        self.name = name
        self.dtype = dtype
        self.kwargs = kwargs


class _FakeSchema:
    def __init__(self, fields: list, description: str = "") -> None:
        self.fields = fields
        self.description = description


class _FakeCollection:
    """记录创建/检索调用的假集合。"""

    registry: dict[str, _FakeCollection] = {}

    def __init__(self, name: str, schema: _FakeSchema | None = None) -> None:
        if schema is None:
            if name not in _FakeCollection.registry:
                raise Exception(f"collection {name} not found")
            existing = _FakeCollection.registry[name]
            self.name = name
            self.schema = existing.schema
            self.indexes = existing.indexes
            self.search_calls = existing.search_calls
            return
        if name in _FakeCollection.registry:
            raise Exception(f"collection {name} already exists")
        self.name = name
        self.schema = schema
        self.indexes: list = []
        self.search_calls: list[dict] = []
        self.upserts: list = []
        _FakeCollection.registry[name] = self

    @classmethod
    def reset(cls) -> None:
        cls.registry = {}

    def create_index(self, field_name: str, index_params: dict) -> None:
        self.indexes.append(
            types.SimpleNamespace(field_name=field_name, params=index_params)
        )

    def load(self) -> None:
        return None

    def upsert(self, entities: list) -> None:
        self.upserts.extend(entities)

    def search(
        self, data: list, anns_field: str, param: dict, limit: int, expr: str, output_fields: list
    ) -> list:
        self.search_calls.append({"expr": expr, "param": param, "limit": limit})
        return [
            [types.SimpleNamespace(entity={"id": cid}) for cid in _CANDIDATE_IDS]
        ]


_CANDIDATE_IDS: list[str] = []


@pytest.fixture
def fake_pymilvus(monkeypatch: pytest.MonkeyPatch):
    """注入假 pymilvus，使 Milvus 路径可在本机无 docker 时被测试覆盖。"""
    _FakeCollection.reset()
    _CANDIDATE_IDS.clear()
    module = types.ModuleType("pymilvus")

    class _DataType:
        VARCHAR = "VARCHAR"
        FLOAT_VECTOR = "FLOAT_VECTOR"

    module.DataType = _DataType  # type: ignore[attr-defined]
    module.FieldSchema = _FakeFieldSchema  # type: ignore[attr-defined]
    module.CollectionSchema = _FakeSchema  # type: ignore[attr-defined]
    module.Collection = _FakeCollection  # type: ignore[attr-defined]
    module.connections = types.SimpleNamespace(  # type: ignore[attr-defined]
        connect=lambda **kwargs: None, disconnect=lambda alias: None
    )
    module.utility = types.SimpleNamespace(  # type: ignore[attr-defined]
        get_server_version=lambda using="default": "v2.4.0"
    )
    monkeypatch.setitem(sys.modules, "pymilvus", module)
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "milvus")
    yield _FakeCollection
    monkeypatch.setattr(settings, "RAG_VECTOR_STORE", "local")
    _FakeCollection.reset()


def test_milvus_collection_name_comes_from_active_index(
    db: Session, fake_pymilvus
) -> None:
    """H-04：Milvus 集合名必须由登记的生效索引决定，不再是固定配置名。"""
    from app.rag.vectorstore.milvus import MilvusVectorStore

    identity = _identity("model-a", 8, backend="milvus")
    index = ensure_index(db, identity)
    _chunk(db, index_id=index.id, dim=8)
    activate_index(db, index.id)

    store = MilvusVectorStore(db)
    collection = store._connect()
    assert collection.name == index.name
    assert collection.name != settings.MILVUS_COLLECTION
    assert index.name.endswith(identity.fingerprint())


def test_milvus_search_filters_by_index_id(db: Session, fake_pymilvus) -> None:
    """H-04：SQL 回查必须再按 index_id 收口，否则身份过滤形同虚设。"""
    from app.rag.vectorstore.milvus import MilvusVectorStore

    active_identity = _identity("model-a", 8, backend="milvus")
    active = ensure_index(db, active_identity)
    mine = _chunk(db, index_id=active.id, dim=8, text="甲")
    activate_index(db, active.id)

    # 另一个（已退役）索引名下的同名分块：不该被当前索引召回
    retired = ensure_index(db, _identity("model-a", 8, backend="milvus", index_version="0"))
    stale = _chunk(db, index_id=retired.id, dim=8, text="甲", tenant=mine.tenant_id)

    _CANDIDATE_IDS.clear()
    _CANDIDATE_IDS.extend([mine.id, stale.id])

    store = MilvusVectorStore(db)
    hits = [h.id for h in _run(store.hybrid_search(
        [0.1] * 8, ["甲"], mine.tenant_id, 5, identity=active_identity
    ))]
    assert hits == [mine.id], "退役索引的 stale hit 不得被当前索引召回"
    calls = store._collection.search_calls
    assert calls and f'index_id == "{active.id}"' in calls[-1]["expr"]


def _run(coro):
    """同步执行协程（本文件其余异步用例走 pytest-asyncio，这里避免依赖）。"""
    import asyncio

    return asyncio.run(coro)


def test_milvus_rejects_mismatched_query_identity(db: Session, fake_pymilvus) -> None:
    """Milvus 同样要在取候选之前核对身份。"""
    from app.rag.vectorstore.milvus import MilvusVectorStore

    identity = _identity("model-a", 8, backend="milvus")
    index = ensure_index(db, identity)
    _chunk(db, index_id=index.id, dim=8)
    activate_index(db, index.id)

    store = MilvusVectorStore(db)
    with pytest.raises(IndexIdentityError):
        _run(store.hybrid_search(
            [0.1] * 8, ["甲"], "any", 5, identity=_identity("model-b", 8, backend="milvus")
        ))


def test_milvus_verify_schema_requires_index_id_field(db: Session, fake_pymilvus) -> None:
    """既有集合缺 index_id 字段时必须显式报错，不能当作已实现控制。"""
    from app.rag.vectorstore.milvus import (
        MilvusUnavailableError,
        MilvusVectorStore,
    )

    identity = _identity("model-a", 8, backend="milvus")
    index = ensure_index(db, identity)
    _chunk(db, index_id=index.id, dim=8)
    activate_index(db, index.id)

    # 手工造一个只有旧字段（无 index_id）的集合
    _FakeCollection(
        index.name,
        _FakeSchema([
            _FakeFieldSchema(name="id", dtype="VARCHAR"),
            _FakeFieldSchema(name="document_id", dtype="VARCHAR"),
        ]),
    )
    store = MilvusVectorStore(db)
    with pytest.raises(MilvusUnavailableError) as exc:
        store._connect()
    assert "index_id" in str(exc.value)


def test_milvus_without_active_index_is_unavailable(db: Session, fake_pymilvus) -> None:
    identity = _identity("model-a", 8, backend="milvus")
    index = ensure_index(db, identity)
    _chunk(db, index_id=index.id, dim=8)  # 有数据但没有 active → 不能猜集合
    from app.rag.vectorstore.milvus import MilvusVectorStore

    with pytest.raises(IndexUnavailableError):
        MilvusVectorStore(db)._connect()


# ── 7. health 可观察性 ─────────────────────────────────────


def _health_body(db: Session) -> dict:
    """取 /api/health 响应体；health 与用例共用同一个 engine，无需替换 Session。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/health")
    return resp.json()


def test_health_reports_unavailable_when_no_index_but_chunks_exist(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-03：identity unknown 且存在数据时，向量库检查项不得仍是 ok。"""
    _chunk(db, index_id=None)
    body = _health_body(db)
    check = body["checks"]["vector_store"]
    assert check["status"] != "ok"
    assert check["reason"]
    assert check["embedding_index"]["status"] == "unavailable"


def test_health_reports_legacy_quarantined(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    index = resolve_write_index(db, _identity("m", 8))
    _chunk(db, index_id=index.id, text="乙")
    _chunk(db, index_id=None, text="丙")
    body = _health_body(db)
    view = body["checks"]["vector_store"]["embedding_index"]
    assert view["status"] == "legacy_quarantined"
    assert view["legacy_chunks"] >= 1
    assert body["checks"]["vector_store"]["status"] != "ok"
    assert body["checks"]["vector_store"]["reason"]


def test_health_fingerprint_and_identity_key_are_distinct(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L-01：fingerprint 字段此前存的是完整 identity_key，两者必须各归其位。"""
    index = resolve_write_index(db, _identity("m", 8))
    _chunk(db, index_id=index.id)
    body = _health_body(db)
    view = body["checks"]["vector_store"]["embedding_index"]
    assert view["fingerprint"] == hashlib.sha256(
        index.identity_key.encode("utf-8")
    ).hexdigest()[:12]
    assert view["fingerprint"] != view["identity_key"]
    assert view["identity_key"] == index.identity_key


def test_health_reports_actual_behaviour_not_config(db: Session) -> None:
    """M-02：health 必须报实际执行的归一化/度量约定。"""
    resolve_write_index(db, _identity("m", 8))
    body = _health_body(db)
    view = body["checks"]["vector_store"]["embedding_index"]
    assert view["normalization_actual"] == "l2_at_query_time"
    assert view["metric_actual"] == "cosine"
    assert view["supported"] is True


# ── 8. 配置与展示 ──────────────────────────────────────────


def test_identity_exposes_no_secret_material() -> None:
    identity = identity_from_provider(_Provider())
    rendered = json.dumps(identity.describe(), ensure_ascii=False)
    for secret in (settings.EMBEDDING_API_KEY, settings.MILVUS_TOKEN):
        if secret:
            assert secret not in rendered
    # 部署标识可以含 host/path，但绝不能含凭据或查询串
    if identity.deployment:
        assert "@" not in identity.deployment
        assert "?" not in identity.deployment


def test_identity_matches_row_compares_full_key() -> None:
    row = EmbeddingIndex(name="r", identity_key=_identity("m", 8).key())
    assert identity_matches_row(_identity("m", 8), row) is True
    assert identity_matches_row(_identity("m", 8, deployment="d"), row) is False
    assert identity_matches_row(_identity("m", 16), row) is False


def test_resolve_read_index_for_returns_none_only_for_empty_library(db: Session) -> None:
    assert resolve_read_index_for(db, None, tenant_id=f"n-{uuid4().hex[:8]}") is None
    index = resolve_write_index(db, _identity("m", 8))
    assert resolve_read_index_for(db, identity_from_provider(_Provider("m", 8))).id == index.id
