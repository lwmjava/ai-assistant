"""Actual import-job failures must preserve the previous readable document."""

import json

import pytest
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.models.rag import Document, DocumentChunk, DocumentIngestionSnapshot, ImportJob
from app.rag.embeddings.mock import MockEmbeddingProvider
from app.rag.service import RAGService


class FailingEmbedding(MockEmbeddingProvider):
    async def embed(self, texts):
        raise RuntimeError("synthetic_embedding_failure")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["embedding", "bad_params", "missing_params", "unknown_version", "database", "external_delete"]
)
async def test_failed_reparse_job_preserves_previous_document(monkeypatch, failure):
    import app.rag.import_jobs as jobs

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    text = "abcdefghij" * 12
    with Session(engine) as session:
        service = RAGService(session, "audit", embedding_provider=MockEmbeddingProvider(dim=4))
        document = await service.ingest_text(
            text, "synthetic", "audit.txt", "audit", strategy="fixed_chars",
            chunk_params={"chunk_size": 20, "chunk_overlap": 0},
        )
        document_id = document.id
        plan = json.loads(document.chunk_plan)
        if failure == "bad_params":
            plan["chunk_params"] = 7
        elif failure == "missing_params":
            plan.pop("chunk_params")
        elif failure == "unknown_version":
            plan["version"] = "unsupported-v999"
        document.chunk_plan = json.dumps(plan)
        session.add(document)
        job = ImportJob(
            tenant_id="audit", user_id="audit", source_type="reparse",
            source_name="audit.txt", storage_path="virtual-fixture.txt",
            reparse_document_id=document_id,
        )
        session.add(job)
        session.commit()
        job_id = job.id
        previous_plan = document.chunk_plan
        previous_chunks = [row.model_dump() for row in session.exec(
            select(DocumentChunk).where(col(DocumentChunk.document_id) == document_id)
            .order_by(col(DocumentChunk.chunk_index))
        ).all()]
        previous_snapshot = session.get(DocumentIngestionSnapshot, document_id).model_dump()

    monkeypatch.setattr(jobs, "read_source_file", lambda path: text.encode())
    provider = FailingEmbedding(dim=4) if failure == "embedding" else MockEmbeddingProvider(dim=4)
    def make_service(session, tenant):
        service = RAGService(session, tenant, provider)
        if failure == "external_delete":
            class UnavailableExternalStore:
                async def delete_by_document(self, document_id, tenant_id):
                    raise RuntimeError("synthetic_external_cleanup_failure")

            service._vector_store = UnavailableExternalStore()
        return service

    monkeypatch.setattr(jobs, "RAGService", make_service)
    with Session(engine) as session:
        if failure == "database":
            from sqlalchemy import event

            def reject_candidate(mapper, connection, target):
                if target.document_id == document_id:
                    raise RuntimeError("synthetic_insert_failure")

            event.listen(DocumentChunk, "before_insert", reject_candidate)
            try:
                await jobs._process_job(session, session.get(ImportJob, job_id))
            finally:
                event.remove(DocumentChunk, "before_insert", reject_candidate)
        else:
            await jobs._process_job(session, session.get(ImportJob, job_id))

    with Session(engine) as session:
        assert session.get(ImportJob, job_id).status == "failed"
        document = session.get(Document, document_id)
        assert document.chunk_count == len(previous_chunks) == 6
        assert document.chunk_plan == previous_plan
        assert session.get(DocumentIngestionSnapshot, document_id).model_dump() == previous_snapshot
        current = [row.model_dump() for row in session.exec(
            select(DocumentChunk).where(col(DocumentChunk.document_id) == document_id)
            .order_by(col(DocumentChunk.chunk_index))
        ).all()]
        assert current == previous_chunks
    engine.dispose()
