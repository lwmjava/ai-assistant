"""Cross-adapter zero-score contribution and positive-overlap contracts."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.core.config import settings
from app.core.database import engine
from app.models.rag import Document, DocumentChunk
from app.rag.index_identity import EmbeddingIndexIdentity
from app.rag.index_registry import ensure_index
from app.rag.vectorstore.local import LocalVectorStore
from app.rag.vectorstore.milvus import MilvusVectorStore


@pytest.mark.asyncio
@pytest.mark.parametrize("query,tokens", [([], [["apple"], ["pear"]]),
                                         (["apple"], [[], []]),
                                         (["absent"], [["apple"], ["pear"]])])
async def test_all_zero_bm25_has_only_dense_contribution(monkeypatch, query, tokens):
    monkeypatch.setattr(settings, "RAG_EFFECTIVE_DATE_FILTER", False)
    tenant = uuid4().hex
    with Session(engine) as session:
        for backend, store_type in (("local", LocalVectorStore), ("milvus", MilvusVectorStore)):
            identity = EmbeddingIndexIdentity(backend, "synthetic", "fixed-unit-v1", 2)
            index = ensure_index(session, identity)
            rows = []
            for i, vector in enumerate(([.8, .6], [.6, .8])):
                doc = Document(tenant_id=tenant, user_id="owner", title=str(i))
                session.add(doc)
                session.flush()
                row = DocumentChunk(tenant_id=tenant, document_id=doc.id, content=str(i),
                                    embedding=json.dumps(vector), tokens=json.dumps(tokens[i]),
                                    index_id=index.id)
                session.add(row)
                rows.append(row)
            session.flush()
            index.status = "active"
            index.chunk_count = len(rows)
            session.add(index)
            session.commit()
            store = store_type(session)
            if backend == "milvus":
                hits = [SimpleNamespace(entity={"id": row.id}, distance=.8 - i * .2)
                        for i, row in enumerate(rows)]
                monkeypatch.setattr(store, "_remote_search", lambda *a: hits)
            result = await store.hybrid_search([1., 0.], query, tenant, 2, identity=identity)
            assert [r.content for r in result] == ["0", "1"]
            assert [r.score for r in result] == pytest.approx([1 / 61, 1 / 62])


@pytest.mark.asyncio
async def test_candidate_window_keyword_omission_and_authorized_filters(monkeypatch):
    from scripts.rag_hybrid_semantics_check import compare, verify

    monkeypatch.setattr(settings, "RAG_EFFECTIVE_DATE_FILTER", False)
    with Session(engine) as session:
        report = await compare(session, fake_remote=True)
        verify(report)


def test_embedding_budget_reserves_before_http_and_retains_failed_attempts(tmp_path):
    from scripts.rag_hybrid_embedding_check import Budget

    path = tmp_path / "budget.sqlite"
    budget = Budget(path)
    for _ in range(20):
        budget.reserve(["synthetic"])
    with pytest.raises(ValueError, match="exhausted"):
        budget.reserve(["synthetic"])
    budget.db.close()
    reopened = Budget(path)
    assert len(reopened.report()["attempts"]) == 20
    with pytest.raises(ValueError, match="exhausted"):
        reopened.reserve(["synthetic"])
    reopened.db.close()


def test_embedding_budget_rejects_missing_or_excessive_usage(tmp_path):
    from scripts.rag_hybrid_embedding_check import Budget

    budget = Budget(tmp_path / "budget.sqlite")
    attempt = budget.reserve(["synthetic"])
    with pytest.raises(ValueError, match="unknown_usage"):
        budget.finish(attempt, None, "unknown_usage")
    assert budget.report()["reserved_cost_upper_bound_yuan"] > 0
    assert budget.report()["known_usage_price_estimate_yuan"] is None
    attempt = budget.reserve(["synthetic"])
    with pytest.raises(ValueError, match="exceeds_reservation"):
        budget.finish(attempt, 1000, "success")
    budget.db.close()


@pytest.mark.parametrize("usage", [True, -1, "44", [44], 1000])
def test_embedding_budget_rejects_invalid_usage_without_cost_fabrication(tmp_path, usage):
    from scripts.rag_hybrid_embedding_check import Budget

    budget = Budget(tmp_path / "budget.sqlite")
    attempt = budget.reserve(["synthetic"])
    with pytest.raises(ValueError, match="usage_invalid"):
        budget.finish(attempt, usage, "success")
    report = budget.report()
    assert report["attempts"][0]["usage_tokens"] is None
    assert report["known_usage_price_estimate_yuan"] is None
    assert report["reserved_cost_upper_bound_yuan"] > 0
    budget.db.close()


@pytest.mark.parametrize("tamper", ["vectors", "model", "dimension", "corpus", "report", "dataset"])
def test_saved_real_vectors_bind_corpus_identity_and_original_report(tmp_path, tamper):
    from scripts.rag_hybrid_embedding_check import corpus_hash, load_authorized_vectors, save_manifest

    vector_path = tmp_path / "vectors.json"
    vector_path.write_text(json.dumps([[.1] * 1024] * 4), encoding="utf-8")
    source = tmp_path / "source.json"
    report = {"result": "pass", "reused_vectors": False, "vectors": str(vector_path),
              "corpus_sha256": corpus_hash(),
              "dataset_version": "hybrid-semantics-real-embedding-synthetic-v1",
              "embedding": {"provider": "dashscope-openai-compatible",
                            "model": "text-embedding-v3", "dim": 1024},
              "budget": {"attempts": [{"status": "success", "usage_tokens": 44}]}}
    source.write_text(json.dumps(report), encoding="utf-8")
    save_manifest(vector_path, source)
    vectors, _ = load_authorized_vectors(vector_path)
    assert len(vectors[0]) == 1024
    metadata_path = vector_path.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if tamper == "vectors":
        vector_path.write_text(json.dumps([[.2] * 1024] * 4), encoding="utf-8")
    elif tamper == "report":
        source.write_text(json.dumps({**report, "result": "fail"}), encoding="utf-8")
    else:
        if tamper == "model":
            metadata["embedding"]["model"] = "foreign-model"
        elif tamper == "dimension":
            metadata["embedding"]["dim"] = 2
        elif tamper == "corpus":
            metadata["corpus_sha256"] = "foreign-corpus"
        else:
            metadata["dataset_version"] = "foreign-dataset"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError):
        load_authorized_vectors(vector_path)
