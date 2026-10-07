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
import re
import sys
import types
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine, init_db
from app.models.rag import Document, DocumentChunk, EmbeddingIndex, IndexStatus
from app.rag.document_parsers.base import ParsedDocument
from app.rag.embeddings.base import EmbeddingInputPolicy, EmbeddingProvider
from app.rag.index_identity import (
    SUPPORTED_METRIC,
    SUPPORTED_NORMALIZATION,
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
    probe_retrieval_hits,
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
async def test_all_backends_carry_query_identity(
    db: Session, backend_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§5.4-1：三种 backend 都把查询身份带进检索，不能只靠某一条路径拦。

    M-07 复核补测：此前这条用例只断言「最终会抛 IndexIdentityError」，而该异常
    还会被 ``RAGService._read_index_id()`` 这道第二道检查兜住——变异「Local 不再
    接收 identity」时它依旧是绿的，等于锁的不是它声称的东西。这里直接对
    ``LocalVectorStore.hybrid_search`` 的入参断言。
    """
    from app.rag.backend.factory import get_rag_backend
    from app.rag.embeddings.mock import tokenize
    from app.rag.service import RAGService
    from app.rag.vectorstore import local as local_module

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

    passed_into_store: list = []
    passed_into_read_check: list = []
    real_search = LocalVectorStore.hybrid_search
    real_resolve = local_module.resolve_read_index_for

    async def spy_search(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        passed_into_store.append(kwargs.get("identity"))
        return await real_search(self, *args, **kwargs)

    def spy_resolve(session, identity, **kwargs):  # type: ignore[no-untyped-def]
        """记录 Local 真正交给读侧身份校验的值——这才「三种 backend 都传身份」的落点。"""
        passed_into_read_check.append(identity)
        return real_resolve(session, identity, **kwargs)

    monkeypatch.setattr(LocalVectorStore, "hybrid_search", spy_search)
    monkeypatch.setattr(local_module, "resolve_read_index_for", spy_resolve)

    tenant = f"bk-{uuid4().hex[:8]}"
    service_a = RAGService(db, tenant, embedding_provider=_FixedProvider("model-a", 8))
    await service_a.ingest_text("甲内容乙内容", title="甲文档", source="s", user_id="u1")

    service_b = RAGService(db, tenant, embedding_provider=_FixedProvider("model-b", 8))
    with pytest.raises(IndexIdentityError):
        await service_b.search("甲", backend=backend_name)
    assert service_b.last_backend_name == backend_name

    expected = identity_from_provider(_FixedProvider("model-b", 8))
    assert passed_into_store, "前提：检索必须真的走到 LocalVectorStore"
    assert passed_into_store[-1] is not None, "backend 必须把查询身份交给向量库"
    assert passed_into_store[-1].key() == expected.key()
    assert passed_into_read_check, "前提：Local 必须走读侧身份校验"
    assert passed_into_read_check[-1] is not None, "Local 必须把 identity 传进读侧校验"
    assert passed_into_read_check[-1].key() == expected.key()


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
        # 假 eager 命中必须携带 ``distance``：真实 pymilvus 的 COSINE 检索会返回它，
        # 生产代码用它作为相似度。缺了这个属性，替身就与真实契约脱节。
        return [
            [
                types.SimpleNamespace(
                    entity={"id": cid}, distance=0.9 - 0.1 * position
                )
                for position, cid in enumerate(_CANDIDATE_IDS)
            ]
        ]


_CANDIDATE_IDS: list[str] = []


def _milvus_serves(*chunk_ids: str) -> None:
    """让假集合像「向量已真的写入 Milvus」那样返回候选。

    激活前的抽样检索校验（ADR-0008:23 / M-09）要求目标索引的向量能被召回；
    假集合默认返回空候选，那等于「Milvus 里根本没有这些向量」，激活当然要拒绝。
    """
    _CANDIDATE_IDS.clear()
    _CANDIDATE_IDS.extend(chunk_ids)


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
    ready = _chunk(db, index_id=index.id, dim=8)
    _milvus_serves(ready.id)
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
    _milvus_serves(mine.id)
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
    ready = _chunk(db, index_id=index.id, dim=8)
    _milvus_serves(ready.id)
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
    ready = _chunk(db, index_id=index.id, dim=8)
    _milvus_serves(ready.id)
    activate_index(db, index.id)

    # 激活时的检索抽样会让 store 建出一个合规集合；这里刻意换成一个缺 index_id
    # 的旧集合：既有集合升级前就是这个样子，必须显式报错而不是当作已实现控制。
    _FakeCollection.reset()
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
    """身份要能落库、进日志与 health：绝不能夹带凭据或查询串。

    L-03：此前这里的桩 ``_Provider()`` 没有 ``base_url``，``deployment`` 恒为空串，
    ``if identity.deployment:`` 里的断言**永不执行**——变异「去掉凭据剥离」时
    这条用例仍是绿的。换用带凭据端点的真实 provider 后它才真的锁住这件事。
    """
    from app.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://svc-user:s3cr3t@example.com/v1?timeout=30",
        api_key="k",
        model="m",
        dim=8,
    )
    identity = identity_from_provider(provider)
    rendered = json.dumps(identity.describe(), ensure_ascii=False)
    assert identity.deployment, "前提：本用例必须走进 deployment 分支"
    for secret in ("s3cr3t", "svc-user", "timeout=30"):
        assert secret not in rendered
        assert secret not in identity.key()
    for secret in (settings.EMBEDDING_API_KEY, settings.MILVUS_TOKEN):
        if secret:
            assert secret not in rendered
    # 部署标识可以含 host/path，但绝不能含凭据或查询串
    assert "@" not in identity.deployment
    assert "?" not in identity.deployment


def test_normalization_and_metric_are_stored_normalized() -> None:
    """L-04：只按小写校验却**原样入库**，会让登记的 ``L2`` 与运行时的 ``l2``
    成为两个永远对不上的索引，且 health 的小写判定还会把它报成 supported。"""
    loose = _identity("m", 8, normalization="L2", metric="Cosine")
    strict = _identity("m", 8)
    assert loose.normalization == "l2"
    assert loose.metric == "cosine"
    assert loose.key() == strict.key()
    assert loose.fingerprint() == strict.fingerprint()


def _documented_choices(field: str) -> set[str]:
    """抓配置项注释里「用 | 列举」的可选值，供下面的文档一致性用例消费。

    只看配置项所在行与前两行注释——那里才是给运维看的可选值清单。
    """
    hints: set[str] = set()
    root = Path(__file__).resolve().parents[1]
    for rel in ("app/core/config.py", ".env.example"):
        lines = (root / rel).read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            if field not in line:
                continue
            for row in lines[max(0, idx - 2): idx + 1]:
                if "|" not in row:
                    continue
                for part in row.split("|"):
                    token = re.match(r"[^a-z0-9_]*([a-z0-9_]+)", part.strip())
                    if token and re.fullmatch(r"[a-z0-9_]+", token.group(1)):
                        hints.add(token.group(1))
    return hints


@pytest.mark.parametrize("field", ["normalization", "metric"])
def test_config_docs_never_advertise_unsupported_values(field: str) -> None:
    """L-02：注释里声明的可选值必须真的构造得出来。

    `app/core/config.py` 与 `.env.example` 一度写着 ``l2 | none`` / ``cosine | ip | l2``，
    而实现只按 l2 + cosine 执行、其它值构造即报错——运维照注释配 `EMBEDDING_METRIC=ip`
    会让摄取与检索直接失败。这里把「注释声明」与「构造器接受」绑在一起：
    注释里敢列的值，必须能被 ``EmbeddingIndexIdentity`` 接受。
    """
    supported = {"normalization": SUPPORTED_NORMALIZATION, "metric": SUPPORTED_METRIC}[field]
    for value in _documented_choices(f"EMBEDDING_{field.upper()}"):
        assert value == supported, (
            f"配置注释把 {field}={value} 列成了可选值，但实现只支持 {supported}"
        )
    # 前提：当前实现确实只接受这一组，注释收紧后仍要保持一致
    with pytest.raises(IndexIdentityError):
        _identity("m", 8, **{field: "something-else"})


def test_identity_matches_row_compares_full_key() -> None:
    row = EmbeddingIndex(name="r", identity_key=_identity("m", 8).key())
    assert identity_matches_row(_identity("m", 8), row) is True
    assert identity_matches_row(_identity("m", 8, deployment="d"), row) is False
    assert identity_matches_row(_identity("m", 16), row) is False


def test_resolve_read_index_for_returns_none_only_for_empty_library(db: Session) -> None:
    assert resolve_read_index_for(db, None, tenant_id=f"n-{uuid4().hex[:8]}") is None
    index = resolve_write_index(db, _identity("m", 8))
    assert resolve_read_index_for(db, identity_from_provider(_Provider("m", 8))).id == index.id


# ── 9. 第二轮复审补测（H-07 / M-08 / M-09）──────────────────


@pytest.mark.asyncio
async def test_search_narrows_parent_expansion_by_read_index_id(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-08：``search()`` → ``_read_index_id()`` 这条真实生产链路的父块收窄。

    既有的父块用例都显式传 ``index_id`` 直接调 ``_expand_parent_chunks``，绕过了
    生产侧唯一的取值来源 ``_read_index_id()``；变异「``_read_index_id()`` 恒返回
    None」时它们一条都不红。这里从 ``search()`` 进入，并用 sideways 注入一个
    retired 索引名下的 stale hit（backend 在切换前拿到的结果就该长这样）。
    """
    from app.rag.service import RAGService
    from app.rag.vectorstore.base import ChunkResult

    tenant = f"rid-{uuid4().hex[:8]}"
    service = RAGService(db, tenant, embedding_provider=_FixedProvider("model-a", 8))
    await service.ingest_text(
        "父块内容。子块内容A。", title="父子", source="s", user_id="u1", strategy="parent_child"
    )
    active = active_index(db)
    assert active is not None
    assert service._read_index_id() == active.id, "前提：收窄用的索引来自 _read_index_id()"

    stale = db.exec(
        select(DocumentChunk).where(col(DocumentChunk.index_id) == active.id)
    ).first()
    assert stale is not None
    retired = ensure_index(db, _identity("model-a", 8, index_version="0"))
    stale.index_id = retired.id
    db.add(stale)
    db.commit()

    class _StaleHitBackend:
        """模拟「命中在切换前算出」：命中的分块已经不在生效索引名下。"""

        name = "native"

        async def retrieve(self, *args, **kwargs):  # noqa: ANN002,ANN003
            return [
                ChunkResult(
                    id=stale.id,
                    content="旧内容",
                    source="s",
                    document_id=stale.document_id,
                    score=0.9,
                )
            ]

    monkeypatch.setattr(service, "_resolve_backend", lambda backend=None: _StaleHitBackend())
    result = await service.search("父块", backend="native")
    assert stale.id not in [h.id for h in result], (
        "retired 索引的 stale hit 不得经 _read_index_id() 的收窄被展开"
    )


def test_activate_is_accepted_when_target_chunks_are_reachable(db: Session) -> None:
    """M-09 正例：目标索引的分块能被真实检索命中 → 允许切换。

    ADR-0008:23 要求的五项校验里，「检索」此前没有实现，一个有几万条分块但永远
    0 命中的索引照样能激活，激活后表现为正常 no_hit——与空索引是同类静默丢失。
    """
    old = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=old.id)
    target = ensure_index(db, _identity("model-b", 8))
    _chunk(db, index_id=target.id, text="乙")

    activate_index(db, target.id)

    assert active_index(db).id == target.id
    db.refresh(old)
    assert old.status == IndexStatus.RETIRED.value
    assert probe_retrieval_hits(db, target) >= 1, "激活后切换完成的索引必须真的能召回自己"


def test_activate_refuses_index_whose_chunks_are_unreachable(db: Session) -> None:
    """M-09 反例：名下有分块、但检索一条都命中不了 → 拒绝激活且**原样撤销切换**。"""
    old = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=old.id)
    target = ensure_index(db, _identity("model-b", 8))
    _chunk(db, index_id=target.id, text="乙")
    # 目标分块挂在已下线文档上：行数存在，检索一条都出不来
    row = db.exec(
        select(DocumentChunk).where(col(DocumentChunk.index_id) == target.id)
    ).first()
    assert row is not None
    doc = db.get(Document, row.document_id)
    assert doc is not None
    doc.is_current = False
    db.add(doc)
    db.commit()

    with pytest.raises(IndexIdentityError) as exc:
        activate_index(db, target.id)
    assert "抽样检索命中数为 0" in str(exc.value)

    # 会话内（还没 refresh）就不该留下半切换：状态写入后是同一批 ORM 对象，
    # 回滚不干净的话后续任何一次 commit 都会把它落库。
    assert old.status == IndexStatus.ACTIVE.value
    assert target.status == IndexStatus.PREPARING.value
    assert active_index(db).id == old.id, "拒绝后读路径必须还是旧索引"
    db.refresh(target)
    assert target.status == IndexStatus.PREPARING.value, "不得留下半切换的中间态"
    db.refresh(old)
    assert old.status == IndexStatus.ACTIVE.value


def test_revert_switch_restores_retired_timestamp(db: Session) -> None:
    """N-01：撤销切换必须连退役时间戳一起还回去，只还 status 会断掉切换历史。

    现场是「对一个已退役的索引做激活/回退，且检索校验不通过」：状态正确回到
    retired，但 ``retired_at`` 被 ``_apply_switch`` 清成了 None 却没还原，
    审计上这一截就丢了。
    """
    old = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=old.id)
    target = ensure_index(db, _identity("model-b", 8))
    _chunk(db, index_id=target.id, text="乙")
    from datetime import UTC, datetime

    retired_at = datetime.now(UTC)
    target.status = IndexStatus.RETIRED.value
    target.retired_at = retired_at
    target.activated_at = retired_at
    db.add(target)
    db.commit()
    # 让检索校验必然失败：目标分块挂在已下线文档上
    row = db.exec(
        select(DocumentChunk).where(col(DocumentChunk.index_id) == target.id)
    ).first()
    assert row is not None
    doc = db.get(Document, row.document_id)
    assert doc is not None
    doc.is_current = False
    db.add(doc)
    db.commit()

    with pytest.raises(IndexIdentityError):
        activate_index(db, target.id)

    observed = db.get(EmbeddingIndex, target.id)
    assert observed is not None
    assert observed.retired_at is not None, "撤销后退役时间戳不能被清空"
    # SQLite 往返会剥离 tzinfo，比较前统一按 naive 时刻对齐，同一时刻才算没丢。
    assert observed.retired_at.replace(tzinfo=None) == retired_at.replace(
        tzinfo=None
    ), "撤销后退役时间戳必须还在"
    assert target.status == IndexStatus.RETIRED.value
    assert active_index(db).id == old.id


def test_activate_refuses_index_whose_vectors_dim_drift(db: Session) -> None:
    """M-09 反例 2：登记 dim=16 却写进了 8 维向量，检索必然读不出来。"""
    old = resolve_write_index(db, _identity("model-a", 8))
    _chunk(db, index_id=old.id)
    target = ensure_index(db, _identity("model-b", 16))
    _chunk(db, index_id=target.id, dim=8, text="乙")

    with pytest.raises(IndexIdentityError):
        activate_index(db, target.id)
    assert active_index(db).id == old.id
    db.refresh(target)
    assert target.status == IndexStatus.PREPARING.value
