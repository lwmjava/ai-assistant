"""Resource permissions across retrieval and derived read paths."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.models.rag import Document, DocumentACL, DocumentACLGrant, DocumentChunk
from app.models.user import Tenant, User
from app.rag.access import read_scope_for
from app.rag.evidence import EvidenceDeniedError, load_chunk_evidence
from app.rag.resource_acl import scoped_resource_allowed
from app.rag.vectorstore.local import LocalVectorStore
from tests.test_rag_019_acceptance import local_llm as _local_llm

local_llm = _local_llm


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RESOURCE_ACL_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_KB_SCOPE", "tenant")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="t", name="tenant"))
        for uid in ("owner", "peer"):
            session.add(User(id=uid, tenant_id="t", username=uid, hashed_password="unused"))
        session.add(Document(id="doc", tenant_id="t", user_id="owner", title="private"))
        session.add(DocumentACL(document_id="doc", tenant_id="t", visibility="restricted", updated_by="owner"))
        session.add(DocumentChunk(id="chunk", document_id="doc", tenant_id="t", content="restricted marker",
                                  embedding=json.dumps([1.0, 0.0]), tokens=json.dumps(["restricted"]), index_id="idx"))
        session.commit()
        yield session
    engine.dispose()


def grant(db):
    db.add(DocumentACLGrant(document_id="doc", user_id="peer", tenant_id="t"))
    db.commit()


def test_scope_snapshot_rejects_revoked_subject(db):
    grant(db)
    user = db.get(User, "peer")
    scope = read_scope_for(user)
    assert scoped_resource_allowed(db, "doc", scope)
    user.token_version += 1
    db.add(user)
    db.commit()
    assert not scoped_resource_allowed(db, "doc", scope)


@pytest.mark.parametrize("subject", [None, "peer", "owner"])
async def test_local_candidates_enforce_acl(db, monkeypatch, subject):
    monkeypatch.setattr("app.rag.vectorstore.local.resolve_read_index_for",
                        lambda *a, **kw: SimpleNamespace(id="idx", dim=2))
    scope = read_scope_for(db.get(User, subject)) if subject else None
    store = LocalVectorStore(db)
    hits = await store.hybrid_search([1.0, 0.0], ["restricted"], "t", 5, read_scope=scope)
    assert bool(hits) is (subject == "owner")
    if subject == "peer":
        grant(db)
        assert await store.hybrid_search([1.0, 0.0], ["restricted"], "t", 5, read_scope=scope)
        db.delete(db.get(DocumentACLGrant, ("doc", "peer")))
        db.commit()
        assert not await store.hybrid_search([1.0, 0.0], ["restricted"], "t", 5, read_scope=scope)


def test_evidence_rechecks_grant(db):
    user = db.get(User, "peer")
    with pytest.raises(EvidenceDeniedError):
        load_chunk_evidence(db, "chunk", user)
    grant(db)
    assert load_chunk_evidence(db, "chunk", user).content == "restricted marker"
    db.delete(db.get(DocumentACLGrant, ("doc", "peer")))
    db.commit()
    with pytest.raises(EvidenceDeniedError):
        load_chunk_evidence(db, "chunk", user)


def test_cache_snapshot_changes_on_acl_revocation(db):
    from app.services.cache_scope import AuthorizedCacheScope

    grant(db)
    scope = AuthorizedCacheScope(db, db.get(User, "peer"), "t")
    before = scope.snapshot()
    assert before is not None
    db.delete(db.get(DocumentACLGrant, ("doc", "peer")))
    db.commit()
    assert scope.snapshot() != before


@pytest.mark.parametrize("backend", ["native", "langchain", "llamaindex"])
async def test_backends_never_return_unauthorized_text(db, monkeypatch, backend):
    from app.rag.backend.factory import get_rag_backend
    from app.rag.embeddings.mock import MockEmbeddingProvider, tokenize

    monkeypatch.setattr("app.rag.vectorstore.local.resolve_read_index_for",
                        lambda *a, **kw: SimpleNamespace(id="idx", dim=2))
    impl = get_rag_backend(MockEmbeddingProvider(dim=2), LocalVectorStore(db), backend=backend, tokenizer=tokenize)
    assert impl.name == backend
    scope = read_scope_for(db.get(User, "peer"))
    assert not await impl.retrieve("restricted", tenant_id="t", top_k=5, read_scope=scope)
    grant(db)
    assert await impl.retrieve("restricted", tenant_id="t", top_k=5, read_scope=scope)


async def test_parent_expansion_rechecks_acl(db):
    from app.rag.service import RAGService
    from app.rag.vectorstore.base import ChunkResult

    service = RAGService.__new__(RAGService)
    service.session, service.tenant_id = db, "t"
    service._read_scope = read_scope_for(db.get(User, "peer"))
    hits = [ChunkResult("chunk", "forged cached content", None, "doc", 1.0)]
    assert not await service._expand_parent_chunks(hits, index_id="idx")
    grant(db)
    assert (await service._expand_parent_chunks(hits, index_id="idx"))[0].content == "restricted marker"


def test_summary_read_revocation_and_generation_fail_closed(db):
    from app.models.rag import SectionSummary
    from app.rag.section_summaries import create_summary_job, get_readable_summary

    summary = SectionSummary(id="summary", tenant_id="t", document_id="doc", chunk_id="chunk",
                             source_version_hash="", chunk_plan_version="legacy", model="synthetic",
                             prompt_version="test", protocol_version="test", summary_text="restricted")
    db.add(summary)
    db.commit()
    user = db.get(User, "peer")
    with pytest.raises(PermissionError):
        get_readable_summary(db, "summary", user)
    grant(db)
    assert get_readable_summary(db, "summary", user).summary_text == "restricted"
    with pytest.raises(PermissionError):
        create_summary_job(db, db.get(User, "owner"), "doc")


def test_cache_hit_validation_denies_revoked_acl_and_anonymous(db):
    from app.rag.vectorstore.base import ChunkResult
    from app.services.cache_scope import AuthorizedCacheScope

    grant(db)
    scope = AuthorizedCacheScope(db, db.get(User, "peer"), "t")
    hits = [ChunkResult("chunk", "restricted marker", None, "doc", 1.0)]
    assert scope.validate_hits(hits, "", "idx")
    db.delete(db.get(DocumentACLGrant, ("doc", "peer")))
    db.commit()
    assert not scope.validate_hits(hits, "", "idx")
    assert not scoped_resource_allowed(db, "doc", None)


async def test_real_retrieval_cache_cannot_replay_after_revocation(tmp_path, monkeypatch):
    from app.rag.embeddings.mock import MockEmbeddingProvider
    from app.rag.service import RAGService

    monkeypatch.setattr(settings, "RAG_RESOURCE_ACL_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_KB_SCOPE", "tenant")
    monkeypatch.setattr(settings, "RETRIEVAL_CACHE_ENABLED", True)
    engine = create_engine(f"sqlite:///{(tmp_path / 'acl-cache.db').as_posix()}",
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="t", name="tenant"))
        owner = User(id="owner", tenant_id="t", username="owner", hashed_password="unused")
        peer = User(id="peer", tenant_id="t", username="peer", hashed_password="unused")
        session.add(owner)
        session.add(peer)
        session.commit()
        rag = RAGService(session, "t", embedding_provider=MockEmbeddingProvider(dim=8),
                         vector_store=LocalVectorStore(session), reader=peer)
        doc = await rag.ingest_text("restricted keyword", title="restricted", source="synthetic", user_id=owner.id)
        session.add(DocumentACL(document_id=doc.id, tenant_id="t", visibility="restricted", updated_by=owner.id))
        session.add(DocumentACLGrant(document_id=doc.id, tenant_id="t", user_id=peer.id))
        session.commit()
        assert await rag.search("keyword")
        assert await rag.search("keyword")
        session.delete(session.get(DocumentACLGrant, (doc.id, peer.id)))
        session.commit()
        assert not await rag.search("keyword")
        assert not await rag.make_retriever().retrieve("keyword", "")
    engine.dispose()


async def test_milvus_candidate_sql_authorization(db, monkeypatch):
    from app.rag.vectorstore.milvus import MilvusVectorStore

    store = MilvusVectorStore(db)
    monkeypatch.setattr(store, "_resolve_index", lambda *a, **kw: SimpleNamespace(id="idx", name="synthetic"))
    monkeypatch.setattr(store, "_remote_search", lambda *a: [SimpleNamespace(entity={"id": "chunk"}, distance=1.0)])
    scope = read_scope_for(db.get(User, "peer"))
    assert not await store.hybrid_search([1.0, 0.0], ["restricted"], "t", 5, read_scope=scope)
    grant(db)
    assert await store.hybrid_search([1.0, 0.0], ["restricted"], "t", 5, read_scope=scope)


async def test_unbound_summary_execution_makes_no_provider_calls(db):
    from app.models.rag import SectionSummaryJob
    from app.rag.section_summaries import run_summary_job
    from tests.test_rag_040_section_summaries import ScriptedProvider

    provider = ScriptedProvider()
    with pytest.raises(PermissionError):
        await run_summary_job(db, SectionSummaryJob(tenant_id="t", document_id="doc"), provider)
    assert provider.calls == 0


async def test_llm_response_cache_does_not_replay_after_acl_revocation(db, local_llm, monkeypatch):
    from app.core.response_cache import bind_cache_scope
    from app.llm.base import ChatMessage, ChatRole, LLMOptions
    from app.llm.capabilities import GenerationCapability
    from app.llm.counters import UTF8_METHOD, estimate_messages_tokens
    from app.llm.openai_compatible import OpenAICompatibleProvider
    from app.services.cache_scope import AuthorizedCacheScope

    monkeypatch.setattr(settings, "LLM_RESPONSE_CACHE_ENABLED", True)
    grant(db)
    url, requests, _ = local_llm
    capability = GenerationCapability(
        deployment=url, model="acceptance-model", context_window=8192, max_output_tokens=2048,
        counter=estimate_messages_tokens, counting_method=UTF8_METHOD,
        safety_margin=512, source="local test server", verified_on="2026-11-02", version="1",
    )
    provider = OpenAICompatibleProvider(url, "synthetic-local-test", "acceptance-model", capability=capability)
    scope = AuthorizedCacheScope(db, db.get(User, "peer"), "t")
    messages = [ChatMessage(role=ChatRole.USER, content="acl cache synthetic response")]
    with bind_cache_scope(scope.snapshot):
        await provider.chat(messages, LLMOptions(temperature=0))
        await provider.chat(messages, LLMOptions(temperature=0))
    assert len(requests) == 1
    db.delete(db.get(DocumentACLGrant, ("doc", "peer")))
    db.commit()
    with bind_cache_scope(scope.snapshot):
        await provider.chat(messages, LLMOptions(temperature=0))
    assert len(requests) == 2
